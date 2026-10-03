Contest Management System — OMI Fork
=====================================

[![Build Status](https://github.com/AresLOLXD/cms/actions/workflows/main.yml/badge.svg)](https://github.com/AresLOLXD/cms/actions)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE.txt)
[![Get support on Telegram](https://img.shields.io/badge/Questions%3F-Join%20the%20Telegram%20group!-%2326A5E4?style=flat&logo=telegram)](https://t.me/contestms)

> **Fork of [cms-dev/cms](https://github.com/cms-dev/cms) — maintained for the
> [Olimpiada Mexicana de Informática (OMI)](https://www.olimpiadadeinformatica.org.mx/).
> Distributed under the same license as the original project (AGPL-3.0).**

---

## Quick Start

No Docker experience required. Run these four commands on a Linux machine:

```bash
# 1. Get the code
git clone https://github.com/AresLOLXD/cms.git && cd cms

# 2. Configure
cp .env.example .env
# Open .env in any editor and fill in the values marked CHANGE_ME

# 3. Start
./up.sh

# 4. Open the Admin interface
#    http://your-server:8889  ← import your contest here
#    http://your-server:8888  ← contestants log in here
#    http://your-server:8890  ← public scoreboard
```

> Need more detail? See [Deploy with Docker](#deploy-with-docker) below.

---

## About this fork

This fork was built for the OMI, but **anyone can use it**. It adds a
Docker-based deployment workflow and several OMI-specific integrations on top
of the upstream CMS project, so you can go from a fresh machine to a running
contest without manually installing dependencies.

**`main` is the only line of this fork** and is what real contests should
run. It merges [upstream](#upstream-project) regularly (`./sync-upstream.sh`)
and keeps the fork's own features and modernization work on top. The former
`beta` branch, where that work used to happen, was merged into `main` on
2026-09-30 and deleted.

### Modernization

The main modernization effort is retiring gevent in favor of Python's
native `asyncio`, one layer of the system at a time:

| Stage | Status | What it does |
|-------|--------|---------------|
| SQLAlchemy 2.0 migration | Done | Move the whole codebase off the legacy `Query` API to SQLAlchemy 2.0's `select()` style |
| `cms/io/` gevent → asyncio | Done | New `AsyncService`/`AsyncTriggeredService` runtime, RPC client/server ported to native asyncio |
| Async DB access | Infrastructure only | Async-safe session/query layer (`AsyncSessionGen`) exists and is ready, but no consumers yet |
| Service migration | Done | `EvaluationService`, `ScoringService`, `ProxyService`, `Checker`, `ResourceService`, `LogService` ported to `AsyncService`/`AsyncTriggeredService`; `Worker` remains gevent (deferred to post-October-10) |
| `WebService`/Tornado-native | Done | `WebService` and the admin/contest RPC handlers now run on native Tornado instead of gevent-patched WSGI |
| AdminWebServer handlers | Done | `cms/server/admin/handlers/` run their blocking DB work off the event loop, and admin login/session uses native Tornado secure cookies |
| ContestWebServer handlers | Planned | Same migration for the contestant-facing server |

Each stage (except Service migration, which was coordinated per-service) has a written design spec under
[`docs/superpowers/specs/`](docs/superpowers/specs/) and lands on `main`
once implemented, reviewed, and its tests pass. `docker/_cms-test-internal.sh`
also gained a gevent/asyncio-aware test split so both the legacy and
migrated code can be tested in CI without the two colliding.

---

## Features

| Feature | Description | Doc |
|---------|-------------|-----|
| Docker deployment | Stand up the full system with `./up.sh` | [docs/docker-deployment.md](docs/docker-deployment.md) |
| Helper scripts | `up`, `down`, `logs`, `restart`, `contest`, `sync-upstream` | [docs/docker-scripts.md](docs/docker-scripts.md) |
| CMS-Loader | Bulk-import users and participations via CSV from the browser | [docs/cms-loader.md](docs/cms-loader.md) |
| AWS user import | Bulk-import users and participations via CSV from the Admin Web Server, one file per contest | [docs/importing-users.md](docs/importing-users.md) |
| Subtask dependencies | A subtask whose prerequisite scored 0 is worth 0 and is not graded | [docs/subtask-dependencies.md](docs/subtask-dependencies.md) |
| Rekarel | Karel compiler and interpreter bundled in the Docker image | [docs/rekarel.md](docs/rekarel.md) |
| Ranking: flags and teams | Real Mexican state flags + automatic team registration on startup | [docs/ranking-mexico.md](docs/ranking-mexico.md) |
| Ranking: custom logo | Replace the ranking server logo without touching source code | [docs/RankingWebServer.rst](docs/RankingWebServer.rst) |
| External judge/bridge integration | Configurable `EvaluationService` bind host (`CMS_ES_BIND_HOST`) so a sibling container can reach it, plus optional two-phase fail-fast grading (`CMS_TWO_PHASE_EVALUATION`) that screens a few testcases per subtask before running the rest | [.env.example](.env.example) |
| Several contests at once | `CMS_CONTEST_ID=ALL` serves every active contest; each ranking group gets its own scoreboard at `/<group>/`, managed and regenerated from the Admin Web Server | [docs/multi-contest.md](docs/multi-contest.md), [migration guide](docs/migrating-to-multi-contest.md) |
| Contest-day checklist | What to check the week before, at the start, during and at the end of a contest, and what to do when something goes wrong | [docs/contest-day.md](docs/contest-day.md) |

---

## Deploy with Docker

The full guide — requirements, configuration, ports, upgrades, common
operations and the helper scripts — is in
[docs/docker-deployment.md](docs/docker-deployment.md).

---

## Upstream project

CMS was originally created by the cms-dev community and is used in IOI and
many other programming contests worldwide. This fork adds to the database
schema only what serving several contests at once needs (a `ranking_groups`
table and a few columns on `contests`, added by `cmsSetupDB`), and its one
change to the evaluation engine — two-phase fail-fast grading — is opt-in and
off by default, so grading behaves identically to upstream unless
`CMS_TWO_PHASE_EVALUATION` is explicitly set.

- **Upstream repository:** <https://github.com/cms-dev/cms>
- **Upstream documentation:** <https://cms.readthedocs.org/>
- **Support (upstream):** [Telegram](https://t.me/contestms) · <contestms-support@googlegroups.com>

If you used CMS for a contest and want to appear on the testimonials list,
see <http://cms-dev.github.io/testimonials.html>.
