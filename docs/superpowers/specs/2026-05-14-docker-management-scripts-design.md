# Docker Management Scripts — Design Spec

**Date:** 2026-05-14  
**Status:** Approved

## Problem

The production Docker Compose command is too long to type manually:

```bash
sudo docker compose -f docker/docker-compose.prod.yml --env-file .env -p cms-test --profile localdb up -d --build
```

Users need simple scripts to start, stop, inspect, and switch contests without memorizing flags.

## Goals

- One-command start/stop/status/logs/restart for the production stack.
- Interactive prompt to choose local DB (Docker) vs external DB.
- Interactive prompt to rebuild the image or use the existing one.
- Project name configurable via `.env` (no flags needed).
- Script to switch the active contest ID with optional DB lookup.
- Documentation for all scripts and `.env` variables.

## File Structure

```
cms/
├── up.sh          # start services
├── down.sh        # stop services
├── status.sh      # show container state
├── logs.sh        # follow logs in real time
├── restart.sh     # down + up
├── contest.sh     # change active contest ID
├── .env           # add CMS_PROJECT_NAME and CMS_CONTEST_ID here
└── docker/
    ├── _lib.sh    # shared variables and helper functions (not executable directly)
    └── ...        # existing files unchanged
```

## Shared Library: `docker/_lib.sh`

Not executable directly. Sourced by every script at startup.

Provides:

- **`PROJECT_NAME`** — read from `CMS_PROJECT_NAME` in `.env`; default `cms-prod`.
- **`COMPOSE_FILE`** — `docker/docker-compose.prod.yml`
- **`COMPOSE_CMD`** — full base command: `docker compose -f $COMPOSE_FILE --env-file .env -p $PROJECT_NAME`
- **`ask_yes_no "question" default`** — prints a `[y/N]` or `[Y/n]` prompt and returns 0 (yes) or 1 (no).

## Scripts

### `up.sh`

1. Sources `docker/_lib.sh`.
2. Asks: `Use local database (Docker)? [y/N]` — if yes, appends `--profile localdb`.
3. Asks: `Rebuild image? [y/N]` — if yes, appends `--build`.
4. Runs: `$COMPOSE_CMD up -d [--profile localdb] [--build]`

### `down.sh`

1. Sources `docker/_lib.sh`.
2. Runs: `$COMPOSE_CMD down`

No interactive prompts.

### `status.sh`

1. Sources `docker/_lib.sh`.
2. Runs: `$COMPOSE_CMD ps`

### `logs.sh`

1. Sources `docker/_lib.sh`.
2. Runs: `$COMPOSE_CMD logs -f`

User exits with Ctrl+C.

### `restart.sh`

1. Sources `docker/_lib.sh`.
2. Calls `down.sh` then `up.sh` (reuses their logic — no duplication).

### `contest.sh`

1. Sources `docker/_lib.sh`.
2. Shows current value of `CMS_CONTEST_ID` from `.env`.
3. Queries the PostgreSQL container for available contests:
   ```sql
   SELECT id, name, start, stop FROM contests ORDER BY id;
   ```
4. If no contests found: prints a warning and the Admin UI URL, then allows manual input.
5. Asks: `Enter new contest ID:` — validates it is a positive integer.
6. Updates `CMS_CONTEST_ID` in `.env` using `sed`.
7. Asks: `Restart services to apply change? [y/N]` — if yes, calls `restart.sh`.

## `.env` Variables Added

| Variable | Default | Description |
|---|---|---|
| `CMS_PROJECT_NAME` | `cms-prod` | Docker Compose project name (`-p` flag) |
| `CMS_CONTEST_ID` | `1` | ID of the active contest used by CMS services |

## Documentation

A `docs/docker-scripts.md` user-facing guide covering:
- Prerequisites (Docker, `.env` setup)
- Quick start example
- Description of each script
- All `.env` variables with examples
- Troubleshooting (DB not reachable, no contests found)

**Audience:** users with basic Linux knowledge (cd, ls, running scripts) but no Docker experience.  
This means:
- No Docker jargon without a one-line plain-language explanation (e.g., "image — the packaged application, like a zip file").
- Explain what each script *does* in plain terms before showing the command.
- Troubleshooting section uses symptom-first language ("If the page doesn't load…") not error-code-first.
- No assumptions about knowing `docker compose`, containers, volumes, or profiles.

## Implementation Agents

| Task | Agent | Reason |
|---|---|---|
| `docker/_lib.sh` | `voltagent-dev-exp:cli-developer` | Shell scripting, CLI interface design |
| `up.sh`, `down.sh`, `status.sh`, `logs.sh`, `restart.sh` | `voltagent-dev-exp:cli-developer` | Same profile; parallelizable once `_lib.sh` exists |
| `contest.sh` | `voltagent-dev-exp:cli-developer` | CLI + DB query logic via `docker exec` |
| Update `.env.example` | `claude` (main agent) | Simple file edit, no specialist needed |
| `docs/docker-scripts.md` | `voltagent-dev-exp:documentation-engineer` | User-facing technical documentation |

**Task dependency order:**

```
1. docker/_lib.sh         ← must go first (all scripts depend on it)
         ↓
2. up.sh / down.sh /      ← parallel, all depend on _lib.sh
   status.sh / logs.sh /
   restart.sh / contest.sh
         ↓
3. .env.example update    ← simple edit, after scripts are stable
         ↓
4. docs/docker-scripts.md ← last, documents the finished scripts
```

## Out of Scope

- Dev or test compose files (existing `cms-dev.sh`, `cms-test.sh` remain unchanged).
- Creating contests (done via Admin Web UI at port 8889).
- Multi-environment switching beyond local/external DB.
