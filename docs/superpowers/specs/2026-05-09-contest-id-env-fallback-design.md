# Design: CMS_CONTEST_ID environment variable fallback

**Date:** 2026-05-09
**Status:** Approved

## Problem

When CMS runs inside Docker under supervisord, the `ContestWebServer` (and any other service that resolves a contest id at startup) occasionally starts without the `-c CONTEST_ID` flag being parsed — possibly due to a race, a restart by supervisord's autorestart, or an image that predates the flag being added to `generate_config.py`. When `-c` is absent, `contest_id_from_args` falls through to `ask_for_contest()`, which calls `input()`. Because Docker stdin is not a TTY, `input()` returns an empty string immediately and the code silently defaults to the **last contest in the database** (not the intended one). The contest list is printed twice to stdout, polluting `docker logs`, and the wrong contest is served.

## Goal

- Eliminate the interactive prompt from Docker logs entirely.
- Ensure the correct contest is served even when `-c` is not passed.
- Fail loudly (not silently) when no contest can be determined in a non-interactive environment.
- Zero changes to supervisord config generation, entrypoint, or Docker Compose files.

## Solution

Add a `CMS_CONTEST_ID` environment variable fallback inside `contest_id_from_args` in `cms/util.py`.

Docker already injects every key from `.env` into the container environment. `CMS_CONTEST_ID=23` is therefore available to every process in the container regardless of how it was started (supervisord, ResourceService autorestart, or manual `docker exec`).

### Logic change

**File:** `cms/util.py`, function `contest_id_from_args` (current lines 248-257)

**Current flow:**
```
args_contest_id == "ALL"  →  return None (multi-contest mode)
args_contest_id is not None  →  parse as int
else  →  call ask_contest()  ← interactive prompt / silent default in Docker
```

**New flow:**
```
args_contest_id == "ALL"  →  return None (multi-contest mode)
args_contest_id is not None  →  parse as int
else:
    CMS_CONTEST_ID env var set and != "ALL"  →  parse as int
    stdin is not a TTY (Docker/CI)  →  log critical + sys.exit(1)
    else  →  call ask_contest()  (interactive, local dev only)
```

### Code diff (conceptual)

```python
# cms/util.py — contest_id_from_args, in the else branch

else:
    env_id = os.environ.get("CMS_CONTEST_ID")
    if env_id and env_id != "ALL":
        try:
            contest_id = int(env_id)
        except ValueError:
            logger.critical(
                "CMS_CONTEST_ID env var is not a valid integer: %r", env_id
            )
            sys.exit(1)
    elif not sys.stdin.isatty():
        logger.critical(
            "No contest id given via -c and stdin is not a TTY. "
            "Set CMS_CONTEST_ID or pass -c CONTEST_ID."
        )
        sys.exit(1)
    else:
        contest_id = ask_contest()
```

`os` is already imported in `cms/util.py`; no new imports needed.

## Affected services

All services that call `contest_id_from_args` with a non-None `ask_contest` argument:
- `cmsContestWebServer`
- `cmsTelegramBot`
- Any future service using `default_argument_parser` with `ask_contest=`

Services NOT affected (they pass `ask_contest=None`):
- `cmsAdminWebServer`, `cmsWorker`, `cmsEvaluationService`, etc.

## Backward compatibility

- Local development (terminal): unchanged — `ask_contest()` still runs when `-c` is absent and stdin is a TTY.
- Docker with `CMS_CONTEST_ID` set: uses env var, no prompt.
- Docker without `CMS_CONTEST_ID` set: fails fast with a clear error instead of silently loading the wrong contest.
- `CMS_CONTEST_ID=ALL`: treated the same as `-c ALL` (multi-contest mode, returns `None`).

## What is NOT changed

- `docker/generate_config.py`
- `docker/entrypoint.sh`
- `docker/docker-compose.prod.yml`
- Any supervisord configuration
- ResourceService behavior

## Testing

- Unit test: call `contest_id_from_args(None, mock_ask)` with `CMS_CONTEST_ID=23` in env → returns 23 without calling `mock_ask`.
- Unit test: call with no env var and `sys.stdin.isatty()` returning False → `sys.exit(1)` is called.
- Unit test: call with no env var and stdin is a TTY → `mock_ask` is called (existing behavior).
- Manual: rebuild Docker image, `docker compose up`, verify no contest list in `docker logs`.
