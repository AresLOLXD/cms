# Telegram bot env vars for Docker

**Date:** 2026-05-08

## Goal

Expose the Telegram bot credentials (`bot_token`, `chat_id`) as Docker env vars so that
`generate_config.py` writes the `[telegram_bot]` section into `cms.toml` at container
startup — and registers the bot under ResourceService and supervisord — without requiring
a manually-edited config file.

## Context

- `cms/conf.py` already defines `TelegramBotConfig` with `bot_token: str` and `chat_id: str`.
- `Config.telegram_bot` is `TelegramBotConfig | None = None`, so the bot is fully optional.
- `docker/generate_config.py` generates `cms.toml` and `supervisord.conf` from env vars but
  currently omits the `[telegram_bot]` section and does not start `cmsTelegramBot`.
- `cmscontrib/TelegramBot.py` accepts a shard positional arg ("unused, but passed by
  ResourceService") and a `-c/--contest-id` flag, confirming it is designed to be managed
  by ResourceService.
- `.env.example` has no Telegram vars.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Bot optional? | Yes | Container must start without Telegram credentials |
| Env var names | `CMS_TELEGRAM_BOT_TOKEN`, `CMS_TELEGRAM_CHAT_ID` | Consistent with `CMS_` prefix convention |
| RPC port for TelegramBot | `27000` (hardcoded) | Only free slot in the current port map; not user-configurable |
| Contest ID for bot | Reuse `CMS_CONTEST_ID` | Consistent with the rest of the Docker stack |
| Prometheus / sandbox gaps | Out of scope | Not used in current Docker setup |

## Port map (reference)

| Port | Service |
|---|---|
| 21000+ | ContestWebServer shards |
| 21100 | AdminWebServer |
| 22000 | Checker |
| 25000 | EvaluationService |
| 26000+ | Worker shards |
| **27000** | **TelegramBot (new)** |
| 28000 | ResourceService |
| 28500 | ScoringService |
| 28600 | ProxyService |
| 29000 | LogService |

## Changes

### 1. `.env.example`

Add a new optional section at the end:

```ini
# -----------------------------------------------------------
# TELEGRAM BOT (optional)
# -----------------------------------------------------------

# Bot token issued by @BotFather.
CMS_TELEGRAM_BOT_TOKEN=

# Numeric ID of the Telegram chat/group where the bot posts.
CMS_TELEGRAM_CHAT_ID=
```

### 2. `docker/generate_config.py` — `generate_cms_toml()`

Read both vars near the top of the function:

```python
bot_token = os.environ.get("CMS_TELEGRAM_BOT_TOKEN", "").strip()
chat_id   = os.environ.get("CMS_TELEGRAM_CHAT_ID", "").strip()
telegram_configured = bool(bot_token and chat_id)
```

In `[services]`, conditionally append:

```toml
TelegramBot = [["localhost", 27000]]
```

At the end of the generated string, conditionally append:

```toml
[telegram_bot]
bot_token = "<bot_token>"
chat_id = "<chat_id>"
```

If either var is absent or empty, both additions are skipped.

### 3. `docker/generate_config.py` — `generate_supervisord_conf()`

At the end, if `telegram_configured`:

```ini
[program:cmstelegrambot]
priority=65
command=cmsTelegramBot 0 -c {contest_id}
autostart=true
autorestart=true
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
```

The `telegram_configured` flag must be derived from env vars inside this function too
(same logic: both vars non-empty).

### 4. `docker/test_generate_config.py`

New tests for `generate_cms_toml()`:
- `test_cms_toml_no_telegram_by_default` — output must NOT contain `[telegram_bot]` or `TelegramBot =`
- `test_cms_toml_telegram_section` — with both vars, output must contain `bot_token`, `chat_id`, and `TelegramBot = [["localhost", 27000]]`
- `test_cms_toml_telegram_partial` — only one var set → no telegram block

New tests for `generate_supervisord_conf()`:
- `test_supervisord_no_telegram_by_default` — output must NOT contain `cmstelegrambot`
- `test_supervisord_telegram_program` — with both vars, output must contain `cmsTelegramBot 0 -c 1`

## Agent assignments

| Step | File | Agent |
|---|---|---|
| 1 | `.env.example` | Direct edit (trivial) |
| 2 | `docker/generate_config.py` | `voltagent-lang:python-pro` |
| 3 | `docker/test_generate_config.py` | `voltagent-qa-sec:test-automator` |
