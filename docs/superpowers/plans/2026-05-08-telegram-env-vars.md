# Telegram Bot Env Vars for Docker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose `CMS_TELEGRAM_BOT_TOKEN` and `CMS_TELEGRAM_CHAT_ID` as Docker env vars so the container auto-configures the Telegram bot in `cms.toml`, registers it in `[services]`, and starts it via supervisord — all conditionally (no-op when vars are absent).

**Architecture:** `docker/generate_config.py` already generates `cms.toml` and `supervisord.conf` from env vars at startup. We extend `generate_cms_toml()` to conditionally emit a `TelegramBot` entry in `[services]` and a `[telegram_bot]` section, and extend `generate_supervisord_conf()` to conditionally add a `cmstelegrambot` supervisord program. The bot uses RPC port 27000 (unused in the current stack). All changes are guarded by checking whether both env vars are non-empty.

**Tech Stack:** Python 3.11+, pytest, TOML string generation (no TOML library — the file builds strings directly), supervisord

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `.env.example` | Modify | Document the two new optional env vars |
| `docker/generate_config.py` | Modify | Emit Telegram config and supervisord program conditionally |
| `docker/test_generate_config.py` | Modify | Verify Telegram blocks appear/absent under the right conditions |

---

### Task 1: Add Telegram vars to `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Append the Telegram section**

Open `.env.example` and add at the very end:

```ini
# -----------------------------------------------------------
# TELEGRAM BOT (optional)
# -----------------------------------------------------------

# Bot token issued by @BotFather.
CMS_TELEGRAM_BOT_TOKEN=

# Numeric ID of the Telegram chat/group where the bot posts.
CMS_TELEGRAM_CHAT_ID=
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "feat(docker): add CMS_TELEGRAM_BOT_TOKEN and CMS_TELEGRAM_CHAT_ID to .env.example"
```

---

### Task 2: Extend `generate_cms_toml()` for Telegram

**Agent:** `voltagent-lang:python-pro`

**Files:**
- Modify: `docker/generate_config.py`

The function currently returns a multi-line f-string. We need to:
1. Read both Telegram env vars near the top of `generate_cms_toml()`.
2. Conditionally add `TelegramBot = [["localhost", 27000]]` inside `[services]`.
3. Conditionally append a `[telegram_bot]` block at the end.

- [ ] **Step 1: Write the failing tests first** (see Task 3 — write tests before touching the implementation)

  Skip to Task 3, then come back here.

- [ ] **Step 2: Read Telegram vars at the top of `generate_cms_toml()`**

  In `docker/generate_config.py`, inside `generate_cms_toml()`, add these two lines right after the existing variable reads (after `rws_password = ...`):

  ```python
  bot_token = os.environ.get("CMS_TELEGRAM_BOT_TOKEN", "").strip()
  chat_id = os.environ.get("CMS_TELEGRAM_CHAT_ID", "").strip()
  telegram_configured = bool(bot_token and chat_id)
  ```

- [ ] **Step 3: Add `TelegramBot` to `[services]` conditionally**

  The `[services]` block is part of the f-string. Replace the current `[services]` block (lines that start with `[services]` through `ProxyService = ...`) with a version that appends the TelegramBot line when configured.

  Change the return value so the services block reads:

  ```python
  telegram_service_line = (
      '\nTelegramBot = [["localhost", 27000]]' if telegram_configured else ""
  )
  ```

  And inside the f-string, after the `ProxyService` line in `[services]`:

  ```
  ProxyService = [["localhost", 28600]]{telegram_service_line}
  ```

  The full updated `[services]` block in the f-string will look like:

  ```python
  f"""
  [services]
  LogService = [["localhost", 29000]]
  ResourceService = [["localhost", 28000]]
  ScoringService = [["localhost", 28500]]
  Checker = [["localhost", 22000]]
  EvaluationService = [["localhost", 25000]]
  Worker = [{worker_entries}]
  ContestWebServer = [{cws_entries}]
  AdminWebServer = [["localhost", {aws_rpc_port}]]
  ProxyService = [["localhost", 28600]]{telegram_service_line}
  """
  ```

- [ ] **Step 4: Append `[telegram_bot]` block at the end**

  After the f-string is built into a local variable (e.g., `toml`), append conditionally:

  ```python
  if telegram_configured:
      toml += (
          f'\n[telegram_bot]\n'
          f'bot_token = "{_toml_str(bot_token)}"\n'
          f'chat_id = "{_toml_str(chat_id)}"\n'
      )
  return toml
  ```

  Refactor the current function to assign the f-string to `toml` before returning, then append and return:

  ```python
  def generate_cms_toml() -> str:
      # ... existing var reads ...
      bot_token = os.environ.get("CMS_TELEGRAM_BOT_TOKEN", "").strip()
      chat_id = os.environ.get("CMS_TELEGRAM_CHAT_ID", "").strip()
      telegram_configured = bool(bot_token and chat_id)
      telegram_service_line = (
          '\nTelegramBot = [["localhost", 27000]]' if telegram_configured else ""
      )

      toml = f"""\
  [global]
  file_log_debug = {log_debug}
  stream_log_detailed = false

  [services]
  LogService = [["localhost", 29000]]
  ResourceService = [["localhost", 28000]]
  ScoringService = [["localhost", 28500]]
  Checker = [["localhost", 22000]]
  EvaluationService = [["localhost", 25000]]
  Worker = [{worker_entries}]
  ContestWebServer = [{cws_entries}]
  AdminWebServer = [["localhost", {aws_rpc_port}]]
  ProxyService = [["localhost", 28600]]{telegram_service_line}

  [database]
  url = "{_toml_str(db_url)}"
  debug = false

  [worker]
  keep_sandbox = false

  [web_server]
  secret_key = "{_toml_str(secret_key)}"
  tornado_debug = false

  [contest_web_server]
  listen_address = {cws_addrs}
  listen_port = {cws_ports}
  num_proxies_used = {num_proxies}

  [admin_web_server]
  listen_address = "{listen_addr}"
  listen_port = {aws_http_port}
  num_proxies_used = {num_proxies}

  [proxy_service]
  rankings = ["http://{_url_quote(rws_username, safe="")}:{_url_quote(rws_password, safe="")}@localhost:{rws_http_port}/"]
  """

      if telegram_configured:
          toml += (
              f'\n[telegram_bot]\n'
              f'bot_token = "{_toml_str(bot_token)}"\n'
              f'chat_id = "{_toml_str(chat_id)}"\n'
          )

      return toml
  ```

- [ ] **Step 5: Run the tests**

  ```bash
  pytest docker/test_generate_config.py -v
  ```

  Expected: all tests pass including the new Telegram ones.

- [ ] **Step 6: Commit**

  ```bash
  git add docker/generate_config.py
  git commit -m "feat(docker): emit [telegram_bot] and TelegramBot service entry when env vars are set"
  ```

---

### Task 3: Write tests for `generate_cms_toml()` Telegram behavior

**Agent:** `voltagent-qa-sec:test-automator`

**Files:**
- Modify: `docker/test_generate_config.py`

These tests must be written **before** Task 2 Step 2 so they fail first (TDD).

- [ ] **Step 1: Add three new test functions**

  Append to `docker/test_generate_config.py`:

  ```python
  def test_cms_toml_no_telegram_by_default(monkeypatch):
      _set(monkeypatch)
      toml = gc.generate_cms_toml()
      assert "[telegram_bot]" not in toml
      assert "TelegramBot" not in toml


  def test_cms_toml_telegram_section(monkeypatch):
      _set(monkeypatch, {
          "CMS_TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
          "CMS_TELEGRAM_CHAT_ID": "-1001234567890",
      })
      toml = gc.generate_cms_toml()
      assert '[telegram_bot]' in toml
      assert 'bot_token = "123456:ABC-DEF"' in toml
      assert 'chat_id = "-1001234567890"' in toml
      assert 'TelegramBot = [["localhost", 27000]]' in toml


  def test_cms_toml_telegram_partial(monkeypatch):
      # Only one var set — no telegram block should appear
      _set(monkeypatch, {"CMS_TELEGRAM_BOT_TOKEN": "123456:ABC-DEF"})
      toml = gc.generate_cms_toml()
      assert "[telegram_bot]" not in toml
      assert "TelegramBot" not in toml
  ```

- [ ] **Step 2: Run the new tests to confirm they fail**

  ```bash
  pytest docker/test_generate_config.py::test_cms_toml_no_telegram_by_default \
         docker/test_generate_config.py::test_cms_toml_telegram_section \
         docker/test_generate_config.py::test_cms_toml_telegram_partial -v
  ```

  Expected: `test_cms_toml_no_telegram_by_default` may pass (block not yet emitted),
  `test_cms_toml_telegram_section` FAILS with assertion error.

- [ ] **Step 3: Return to Task 2 and implement**

  Implement Task 2 Steps 2–4, then run all tests again:

  ```bash
  pytest docker/test_generate_config.py -v
  ```

  Expected: all pass.

- [ ] **Step 4: Commit the tests**

  ```bash
  git add docker/test_generate_config.py
  git commit -m "test(docker): add Telegram cms.toml generation tests"
  ```

---

### Task 4: Extend `generate_supervisord_conf()` for Telegram

**Agent:** `voltagent-lang:python-pro`

**Files:**
- Modify: `docker/generate_config.py`

- [ ] **Step 1: Write failing tests first** (see Task 5)

  Skip to Task 5, then come back.

- [ ] **Step 2: Read Telegram vars inside `generate_supervisord_conf()`**

  At the top of `generate_supervisord_conf()`, add:

  ```python
  bot_token = os.environ.get("CMS_TELEGRAM_BOT_TOKEN", "").strip()
  chat_id = os.environ.get("CMS_TELEGRAM_CHAT_ID", "").strip()
  telegram_configured = bool(bot_token and chat_id)
  ```

- [ ] **Step 3: Append the bot program when configured**

  After the existing `blocks.append(program("cmsadminwebserver", ...))` line, add:

  ```python
  if telegram_configured:
      blocks.append(
          program("cmstelegrambot", f"cmsTelegramBot 0 -c {contest_id}", 65)
      )
  ```

- [ ] **Step 4: Run all tests**

  ```bash
  pytest docker/test_generate_config.py -v
  ```

  Expected: all pass including the new supervisord Telegram tests.

- [ ] **Step 5: Commit**

  ```bash
  git add docker/generate_config.py
  git commit -m "feat(docker): start cmsTelegramBot via supervisord when env vars are set"
  ```

---

### Task 5: Write tests for `generate_supervisord_conf()` Telegram behavior

**Agent:** `voltagent-qa-sec:test-automator`

**Files:**
- Modify: `docker/test_generate_config.py`

Write these before Task 4 Step 2 (TDD).

- [ ] **Step 1: Add two new test functions**

  Append to `docker/test_generate_config.py`:

  ```python
  def test_supervisord_no_telegram_by_default(monkeypatch):
      _set(monkeypatch)
      conf = gc.generate_supervisord_conf()
      assert "cmstelegrambot" not in conf
      assert "cmsTelegramBot" not in conf


  def test_supervisord_telegram_program(monkeypatch):
      _set(monkeypatch, {
          "CMS_TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
          "CMS_TELEGRAM_CHAT_ID": "-1001234567890",
          "CMS_CONTEST_ID": "3",
      })
      conf = gc.generate_supervisord_conf()
      assert "cmstelegrambot" in conf
      assert "cmsTelegramBot 0 -c 3" in conf
  ```

- [ ] **Step 2: Run new tests to confirm they fail**

  ```bash
  pytest docker/test_generate_config.py::test_supervisord_no_telegram_by_default \
         docker/test_generate_config.py::test_supervisord_telegram_program -v
  ```

  Expected: `test_supervisord_telegram_program` FAILS.

- [ ] **Step 3: Return to Task 4 and implement, then verify**

  ```bash
  pytest docker/test_generate_config.py -v
  ```

  Expected: all pass.

- [ ] **Step 4: Commit**

  ```bash
  git add docker/test_generate_config.py
  git commit -m "test(docker): add Telegram supervisord generation tests"
  ```

---

### Task 6: Final verification

- [ ] **Step 1: Run the full test suite**

  ```bash
  pytest docker/test_generate_config.py -v
  ```

  Expected output (all green):
  ```
  test_validate_missing_db_url PASSED
  test_validate_insecure_secret_key PASSED
  test_validate_passes_with_valid_env PASSED
  test_cms_toml_defaults PASSED
  test_cms_toml_multiple_cws PASSED
  test_cms_toml_multiple_workers PASSED
  test_cms_toml_custom_aws_port PASSED
  test_cms_toml_proxy_url_contains_rws_creds PASSED
  test_cms_toml_no_telegram_by_default PASSED
  test_cms_toml_telegram_section PASSED
  test_cms_toml_telegram_partial PASSED
  test_ranking_toml_defaults PASSED
  test_ranking_toml_custom PASSED
  test_supervisord_single_cws_worker PASSED
  test_supervisord_multiple_cws PASSED
  test_supervisord_multiple_workers PASSED
  test_supervisord_no_telegram_by_default PASSED
  test_supervisord_telegram_program PASSED
  ```

- [ ] **Step 2: Smoke-test manually**

  ```bash
  cd /var/home/areslolxd/Documentos/cms
  CMS_DB_URL="postgresql+psycopg2://cms:secret@db:5432/cmsdb" \
  CMS_SECRET_KEY="abcdef0123456789abcdef0123456789" \
  CMS_CONTEST_ID="1" \
  CMS_TELEGRAM_BOT_TOKEN="123456:ABC-DEF" \
  CMS_TELEGRAM_CHAT_ID="-100999" \
  python3 -c "
  import sys; sys.path.insert(0, 'docker')
  import generate_config as gc
  print(gc.generate_cms_toml())
  print('--- supervisord ---')
  print(gc.generate_supervisord_conf())
  "
  ```

  Expected: output contains `[telegram_bot]`, `bot_token = "123456:ABC-DEF"`,
  `TelegramBot = [["localhost", 27000]]`, and `cmsTelegramBot 0 -c 1`.

- [ ] **Step 3: Final commit if any stray changes remain**

  ```bash
  git status
  # If clean, nothing to do.
  ```
