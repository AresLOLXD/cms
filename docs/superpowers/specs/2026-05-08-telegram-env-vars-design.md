# Telegram bot env vars for Docker

**Date:** 2026-05-08

## Goal

Expose the Telegram bot credentials (`bot_token`, `chat_id`) as Docker env vars so that
`generate_config.py` can write the `[telegram_bot]` section into `cms.toml` at container
startup, without requiring a manually-edited config file.

## Context

- `cms/conf.py` already defines `TelegramBotConfig` with `bot_token: str` and `chat_id: str`.
- `Config.telegram_bot` is `TelegramBotConfig | None = None`, so the bot is fully optional.
- `docker/generate_config.py` generates `cms.toml` from env vars but currently omits the
  `[telegram_bot]` section entirely.
- `.env.example` has no Telegram vars.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Bot optional? | Yes | Container must start without Telegram credentials |
| Env var names | `CMS_TELEGRAM_BOT_TOKEN`, `CMS_TELEGRAM_CHAT_ID` | Consistent with `CMS_` prefix convention |

## Changes

### `.env.example`
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

### `docker/generate_config.py` — `generate_cms_toml()`
After building the base TOML string, conditionally append the `[telegram_bot]` block:

```python
bot_token = os.environ.get("CMS_TELEGRAM_BOT_TOKEN", "").strip()
chat_id   = os.environ.get("CMS_TELEGRAM_CHAT_ID", "").strip()
if bot_token and chat_id:
    toml += f'\n[telegram_bot]\nbot_token = "{_toml_str(bot_token)}"\nchat_id = "{_toml_str(chat_id)}"\n'
```

If either var is absent or empty, the block is omitted and the bot remains `None`.

### `docker/test_generate_config.py`
Two new tests:

- `test_cms_toml_no_telegram_by_default` — without the vars, output must NOT contain `[telegram_bot]`.
- `test_cms_toml_telegram_section` — with both vars set, output must contain correct `bot_token` and `chat_id` lines.

## Out of scope

- Running `cmsTelegramBot` via supervisord (separate task if needed).
- Validation that the token format is correct (Telegram rejects bad tokens at runtime).
