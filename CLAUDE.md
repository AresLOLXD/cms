# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is CMS

Contest Management System (CMS) is a distributed Python system for running IOI-style programming contests. It evaluates contestant submissions by compiling and running them in sandboxed environments against judge testcases.

## Commands

### Installation
```bash
pip install -e ".[devel]"   # editable install with dev dependencies
cmsInitDB                   # initialize the PostgreSQL database
```

### Running services
Each service is a standalone process. The primary ones:
```bash
cmsLogService 0             # start log aggregator (start this first)
cmsResourceService 0        # resource manager
cmsEvaluationService 0      # submission queue and worker dispatch
cmsWorker 0                 # compiles and runs submissions in sandbox
cmsAdminWebServer 0         # admin UI
cmsContestWebServer 0       # contestant-facing UI
cmsScoringService 0         # computes scores from evaluation results
cmsRankingWebServer 0       # public scoreboard
```

### Testing
```bash
pytest                                          # run all unit tests
pytest cmstestsuite/unit_tests/path/test_x.py  # run a single test file
pytest -k "test_name"                           # run tests matching a name
cmsRunFunctionalTests -v                        # run functional tests (needs a running DB)
```

### Linting
```bash
pyflakes cms cmscommon cmscontrib cmsranking cmstaskenv cmstestsuite   # Python linting
eslint cms/server/contest/static cms/server/admin/static               # JS linting
```

### Database
```bash
cmsDropDB && cmsInitDB      # reset the database
```

## Architecture

CMS is a **service-oriented system** where each component is a long-running process communicating via **JSON-RPC over TCP** (gevent-based, defined in `cms/io/rpc.py`). Services discover each other through `cms.conf` (TOML, default location `~/.config/cms/cms.conf` or `config/cms.sample.toml` for reference).

### Core packages

- **`cms/`** — main package
  - `conf.py` — config loading; `config` singleton is imported everywhere
  - `log.py` — logging setup with per-service log files
  - `db/` — SQLAlchemy models (PostgreSQL only). All models inherit from `Base` in `cms/db/base.py`. Sessions are managed via `cms/db/session.py`.
  - `io/` — gevent-based service runtime: `Service` base class, RPC client/server, priority queue
  - `service/` — concrete services: `EvaluationService`, `ScoringService`, `Worker`, `Checker`, etc.
  - `server/` — Tornado-based web servers: `admin/` (AdminWebServer) and `contest/` (ContestWebServer)
  - `grading/` — pluggable evaluation engine:
    - `tasktypes/` — how submissions are compiled and run (Batch, Communication, Interactive, OutputOnly, TwoSteps)
    - `scoretypes/` — how scores are computed from test results (Sum, GroupMin, GroupMul, GroupThreshold)
    - `languages/` — compiler/runner definitions per programming language
    - `Sandbox.py` — isolation layer for running contestant code

- **`cmscommon/`** — shared utilities (crypto, archive, config parser, MIME types)

- **`cmscontrib/`** — CLI tools for contest management: importers, exporters, task loaders (`loaders/`), and administrative scripts

- **`cmsranking/`** — standalone ranking web server (independent of main DB, uses its own in-memory store)

- **`cmstasksuite/`** — task format definitions used during import (`cmstaskenv/`)

- **`cmstestsuite/`** — test infrastructure
  - `unit_tests/` — pytest tests, mirroring the structure of the main packages
  - `functionaltestframework.py`, `RunFunctionalTests.py` — end-to-end tests that submit real solutions

### Submission lifecycle

1. Contestant submits via `ContestWebServer` → stored in DB
2. `EvaluationService` picks it up, dispatches jobs to `Worker`(s) via RPC
3. `Worker` compiles the submission (using the language plugin), then runs it on each testcase in a `Sandbox`
4. Results flow back to `EvaluationService`, then to `ScoringService`
5. `ScoringService` applies the task's score type to produce a final score and notifies `RankingWebServer`

### Plugin system

Task types, score types, and languages are all plugins discovered via `cms/plugin.py`. New ones are added by creating a class in the appropriate `cms/grading/{tasktypes,scoretypes,languages}/` directory and registering it in the package's `__init__.py`.

## Code style

- Follow PEP 8; no pyflakes warnings per commit.
- Use PEP 484 type annotations.
- Docstrings use the project's custom format: imperative first line, then args/return/raise sections (see `CONTRIBUTING.md`).
- Python 3.11+ only.
- JS follows ESLint config (4-space indent, double quotes).
