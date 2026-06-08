# EvaluationService & ProxyService Contest Flag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pass `-c {contest_id}` to EvaluationService and ProxyService in the generated supervisord.conf so they don't block on a full-DB scan or prompt interactively for a contest ID.

**Architecture:** `generate_supervisord_conf()` in `docker/generate_config.py` computes a `contest_flag` string (`"-c {contest_id}"` when `contest_id != "ALL"`, empty otherwise) and includes it in the EvaluationService and ProxyService command lines, matching the existing pattern used for ContestWebServer and TelegramBot.

**Tech Stack:** Python 3, pytest, docker/generate_config.py, docker/test_generate_config.py

---

### Task 1: Fix two broken existing tests and add new failing tests

**Files:**
- Modify: `docker/test_generate_config.py`

**Context:** Two existing tests assert `"cmsContestWebServer 0 1"` but the actual command is `"cmsContestWebServer 0 -c 1"` (the `-c` flag is already present). They must be fixed first so the test suite is green before adding new behavior.

- [ ] **Step 1: Fix the two broken assertions in existing tests**

In `docker/test_generate_config.py`, change:

```python
# line 103 — was: assert "cmsContestWebServer 0 1" in conf
assert "cmsContestWebServer 0 -c 1" in conf

# line 113-114 — was: assert "cmsContestWebServer 0 5" / "cmsContestWebServer 1 5"
assert "cmsContestWebServer 0 -c 5" in conf
assert "cmsContestWebServer 1 -c 5" in conf
```

- [ ] **Step 2: Run tests to confirm only those two now pass (others still green)**

```bash
python -m pytest docker/test_generate_config.py -v
```

Expected: all 18 tests PASS.

- [ ] **Step 3: Add failing tests for EvaluationService and ProxyService with `-c` flag**

Append to `docker/test_generate_config.py`:

```python
def test_supervisord_evaluation_and_proxy_get_contest_flag(monkeypatch):
    _set(monkeypatch, {"CMS_CONTEST_ID": "23"})
    conf = gc.generate_supervisord_conf()
    assert "cmsEvaluationService 0 -c 23" in conf
    assert "cmsProxyService 0 -c 23" in conf


def test_supervisord_evaluation_and_proxy_no_flag_when_all(monkeypatch):
    _set(monkeypatch, {"CMS_CONTEST_ID": "ALL"})
    conf = gc.generate_supervisord_conf()
    assert "cmsEvaluationService 0 -c" not in conf
    assert "cmsProxyService 0 -c" not in conf
    # services still present without flag
    assert "cmsEvaluationService 0" in conf
    assert "cmsProxyService 0" in conf
```

- [ ] **Step 4: Run tests to verify the new tests fail**

```bash
python -m pytest docker/test_generate_config.py::test_supervisord_evaluation_and_proxy_get_contest_flag docker/test_generate_config.py::test_supervisord_evaluation_and_proxy_no_flag_when_all -v
```

Expected: both FAIL (EvaluationService and ProxyService don't have `-c` yet, and ALL case raises SystemExit because `_require("CMS_CONTEST_ID")` rejects "ALL" — or returns it as-is and the flag check fails).

- [ ] **Step 5: Commit the test changes**

```bash
git add docker/test_generate_config.py
git commit -m "test: fix broken CWS assertions and add failing tests for ES/proxy contest flag"
```

---

### Task 2: Implement the fix in generate_supervisord_conf

**Files:**
- Modify: `docker/generate_config.py:164-222`

**Context:** `generate_supervisord_conf()` already calls `_require("CMS_CONTEST_ID")` which returns the raw string (e.g. `"23"` or `"ALL"`). We need to compute a flag string once and use it for EvaluationService and ProxyService.

- [ ] **Step 1: Add `contest_flag` variable and apply it to both services**

In `docker/generate_config.py`, inside `generate_supervisord_conf()`, replace lines 202 and 208:

```python
# Before (line 167 area — after contest_id = _require("CMS_CONTEST_ID")):
contest_flag = f" -c {contest_id}" if contest_id != "ALL" else ""

# Line 202 — was:
#     program("cmsevaluationservice", "cmsEvaluationService 0", 30),
# Change to:
        program("cmsevaluationservice", f"cmsEvaluationService 0{contest_flag}", 30),

# Line 208 — was:
#     blocks.append(program("cmsproxyservice", "cmsProxyService 0", 50))
# Change to:
    blocks.append(program("cmsproxyservice", f"cmsProxyService 0{contest_flag}", 50))
```

The full updated block (lines 164–222 after the change):

```python
def generate_supervisord_conf() -> str:
    cws_count = _get_int("CMS_CWS_COUNT", 1)
    worker_count = _get_int("CMS_WORKER_COUNT", 1)
    contest_id = _require("CMS_CONTEST_ID")
    contest_flag = f" -c {contest_id}" if contest_id != "ALL" else ""
    bot_token = os.environ.get("CMS_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("CMS_TELEGRAM_CHAT_ID", "").strip()
    telegram_configured = bool(bot_token and chat_id)

    def program(name: str, command: str, priority: int) -> str:
        return (
            f"[program:{name}]\n"
            f"priority={priority}\n"
            f"command={command}\n"
            "autostart=true\n"
            "autorestart=true\n"
            "stdout_logfile=/dev/stdout\n"
            "stdout_logfile_maxbytes=0\n"
            "stderr_logfile=/dev/stderr\n"
            "stderr_logfile_maxbytes=0\n"
        )

    blocks = [
        "[supervisord]\n"
        "nodaemon=true\n"
        "logfile=/dev/null\n"
        "logfile_maxbytes=0\n"
        "\n"
        "[unix_http_server]\n"
        "file=/tmp/supervisor.sock\n"
        "\n"
        "[supervisorctl]\n"
        "serverurl=unix:///tmp/supervisor.sock\n"
        "\n"
        "[rpcinterface:supervisor]\n"
        "supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface\n",
        program("cmslogservice", "cmsLogService 0", 10),
        program("cmsresourceservice", "cmsResourceService 0", 20),
        program("cmsscoringservice", "cmsScoringService 0", 30),
        program("cmsevaluationservice", f"cmsEvaluationService 0{contest_flag}", 30),
    ]

    for i in range(worker_count):
        blocks.append(program(f"cmsworker{i}", f"cmsWorker {i}", 40))

    blocks.append(program("cmsproxyservice", f"cmsProxyService 0{contest_flag}", 50))
    blocks.append(program("cmsrankingwebserver", "cmsRankingWebServer", 55))

    for i in range(cws_count):
        blocks.append(
            program(f"cmscontestwebserver{i}", f"cmsContestWebServer {i} -c {contest_id}", 60)
        )

    blocks.append(program("cmsadminwebserver", "cmsAdminWebServer 0", 60))

    if telegram_configured:
        blocks.append(
            program("cmstelegrambot", f"cmsTelegramBot 0 -c {contest_id}", 65)
        )

    return "\n".join(blocks)
```

- [ ] **Step 2: Run all tests and confirm they all pass**

```bash
python -m pytest docker/test_generate_config.py -v
```

Expected: all 20 tests PASS.

- [ ] **Step 3: Commit the implementation**

```bash
git add docker/generate_config.py
git commit -m "fix: pass -c contest_id to EvaluationService and ProxyService in supervisord.conf"
```
