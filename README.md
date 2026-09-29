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

**`main` stays closely aligned with [upstream](#upstream-project)** and is
what real contests should run. Larger, in-progress experiments — new
features and deeper modernization work (e.g. updating long-pinned
dependencies) that we don't want to wait on upstream for — happen on the
**`beta`** branch instead, before they're considered stable enough to land
here.

### Beta-line modernization

The main effort on `beta` right now is retiring gevent in favor of Python's
native `asyncio`, one layer of the system at a time:

| Stage | Status | What it does |
|-------|--------|---------------|
| SQLAlchemy 2.0 migration | Done | Move the whole codebase off the legacy `Query` API to SQLAlchemy 2.0's `select()` style |
| `cms/io/` gevent → asyncio | Done | New `AsyncService`/`AsyncTriggeredService` runtime, RPC client/server ported to native asyncio |
| Async DB access | Done | Async-safe session/query layer (`AsyncSessionGen`) for code running on the asyncio loop |
| Service migration | Done | `EvaluationService`, `ScoringService`, `ProxyService`, `Worker`, etc. ported to `AsyncTriggeredService` |
| `WebService`/Tornado-native | Done | `WebService` and the admin/contest RPC handlers now run on native Tornado instead of gevent-patched WSGI |
| AdminWebServer handlers | Done | `cms/server/admin/handlers/` run their blocking DB work off the event loop, and admin login/session uses native Tornado secure cookies |
| ContestWebServer handlers | Planned | Same migration for the contestant-facing server |

Each stage has a written design spec under
[`docs/superpowers/specs/`](docs/superpowers/specs/) and lands on `beta`
once implemented, reviewed, and its tests pass. `docker/_cms-test-internal.sh`
also gained a gevent/asyncio-aware test split so both the legacy and
migrated code can be tested in CI without the two colliding.

---

## Features

| Feature | Description | Doc |
|---------|-------------|-----|
| Docker deployment | Stand up the full system with `./up.sh` | [Deploy with Docker](#deploy-with-docker) |
| Helper scripts | `up`, `down`, `logs`, `restart`, `contest`, `sync-upstream` | [docs/docker-scripts.md](docs/docker-scripts.md) |
| CMS-Loader | Bulk-import users and participations via CSV from the browser | [docs/cms-loader.md](docs/cms-loader.md) |
| Rekarel | Karel compiler and interpreter bundled in the Docker image | [docs/rekarel.md](docs/rekarel.md) |
| Ranking: flags and teams | Real Mexican state flags + automatic team registration on startup | [docs/ranking-mexico.md](docs/ranking-mexico.md) |
| Ranking: custom logo | Replace the ranking server logo without touching source code | [docs/RankingWebServer.rst](docs/RankingWebServer.rst) |
| External judge/bridge integration | Configurable `EvaluationService` bind host (`CMS_ES_BIND_HOST`) so a sibling container can reach it, plus optional two-phase fail-fast grading (`CMS_TWO_PHASE_EVALUATION`) that screens a few testcases per subtask before running the rest | [.env.example](.env.example) |
| Several contests at once | `CMS_CONTEST_ID=ALL` serves every active contest; each ranking group gets its own scoreboard at `/<group>/`, managed and regenerated from the Admin Web Server | [docs/multi-contest.md](docs/multi-contest.md), [migration guide](docs/migrating-to-multi-contest.md) |

---

## Deploy with Docker

This guide explains how to run CMS using Docker. No prior Docker experience is
required — just follow the steps below.

### What you need first

- [Docker](https://docs.docker.com/get-docker/) (version 24 or newer)
- [Docker Compose](https://docs.docker.com/compose/install/) (included with
  Docker Desktop; on Linux install the `docker-compose-plugin` package)
- A machine with Linux and cgroups v2 enabled (required by the sandbox).
  Most modern Linux distros (Ubuntu 22.04+, Debian 12+, Fedora 36+) have
  this enabled by default.

### Step 1 — Get the code

```bash
git clone https://github.com/AresLOLXD/cms.git
cd cms
```

### Step 2 — Create your configuration file

Copy the example file and open it in any text editor:

```bash
cp .env.example .env
```

The file has comments explaining every option. At a minimum you **must** fill
in the values marked `CHANGE_ME`:

| Variable | What it is |
|----------|-----------|
| `CMS_SECRET_KEY` | A random 16-byte key used to protect cookies. Generate one with the command shown in the file. |
| `CMS_DB_URL` | The connection string to the PostgreSQL database. |
| `POSTGRES_PASSWORD` | Password for the Docker-managed PostgreSQL database (required for Option A). Must match the password in `CMS_DB_URL`. |
| `CMS_ADMIN_USER` | Username for the initial admin account created on first run. Can be removed after the first deploy. |
| `CMS_ADMIN_PASSWORD` | Password for the initial admin account created on first run. Can be removed after the first deploy. |
| `CMS_CONTEST_ID` | The numeric ID of the contest to serve, or `ALL` to serve every active contest at once (see [docs/multi-contest.md](docs/multi-contest.md)). You get the ID from the Admin interface after importing a contest — set it then and restart. |

Everything else has a sensible default and can be left as-is on the first try.

### Step 3 — Start CMS

```bash
./up.sh
```

The script asks two questions:
- **Use local database (Docker)?** — answer `y` if you want Docker to manage PostgreSQL for you (recommended for a single server). Answer `n` if you have an existing PostgreSQL server and already set `CMS_DB_URL` accordingly.
- **Rebuild?** — a menu from 1 to 7. Choose `1) No` on the first run (Docker builds the images that do not exist yet) or when nothing has changed. After updating the code of a single-contest deployment, choose `2) All services`, which rebuilds both images (`4) CMS only` leaves the ranking on its old image). A multi-contest deployment follows the two steps in [docs/multi-contest.md](docs/multi-contest.md) instead.

### Step 4 — Import a contest and set it as active

Open the Admin interface in your browser at `http://your-server:8889`.
Use `cmscontrib` tools (e.g. `cmsImportContest`) to import your contest.

Then run:

```bash
./contest.sh
```

This lists all contests in the database, prompts you to pick one, updates
`CMS_CONTEST_ID` in `.env`, and optionally restarts services to apply the change.

### Ports

By default the following ports are exposed. You can change all of them in
`.env`.

| Port | Service |
|------|---------|
| `8888` | Contest Web Server (contestants log in here) |
| `8889` | Admin Web Server (contest administration) |
| `8890` | Ranking Web Server (public scoreboard) |

### Common operations

**View live logs:**
```bash
./logs.sh
```

**Stop everything:**
```bash
./down.sh
```

**Stop and delete all data (including the database — be careful):**
```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env down -v
```

**Run multiple Contest Web Server instances (for load balancing):**

Set `CMS_CWS_COUNT=2` in your `.env`. CMS will start two contest web servers
on consecutive ports (e.g. 8888 and 8889 if `CMS_CWS_HTTP_PORT=8888`).
Point your load balancer (e.g. nginx) at those two ports.

**Reset the database (first boot only, dangerous on a running contest):**

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env run --rm db-init
```

**Upgrading a deployment that was started before the `cms-data` volume moved:**

The `cms-data` volume used to be mounted on `/home/cmsuser/cms/lib`, which is
also where the CMS code is installed. Docker filled the volume from the first
image and never refreshed it, so rebuilding the image did not change the code
the `cms` container ran. The volume is now mounted on `/home/cmsuser/cms/data`,
which holds only runtime data (submission and user-test copies, Telegram bot
state). It keeps its name and its contents, so nothing is lost.

After updating to this version you **must rebuild the CMS image**. For a
single-contest deployment run `./up.sh` and choose `4) CMS only` (or `2) All
services`). For a multi-contest deployment follow the two steps of "Deployment"
in [docs/multi-contest.md](docs/multi-contest.md): first rebuild only the
ranking container with the command given there, then `./up.sh` and `4) CMS
only`. (`./up.sh` with `3) Ranking only` is not enough: it builds only the
ranking image but then starts every service, so `cms` is recreated with the old
image.) Never start this version with an old image, which is also what `1) No`
does (the default in `./up.sh`, `./restart.sh` and `./contest.sh`): the old
image does not know the new `data_dir`, so submission and user-test copies go to
the container's own filesystem and are lost the next time it is recreated, and
the Telegram bot re-sends every question and announcement. Once rebuilt, the
`cms` container runs the code of the image for real, possibly newer than what
was running until now.

The old copy of the code left in the volume, the `python3.12` directory, is
inert. To free the space, find the volume name with
`docker volume ls | grep cms-data` (with the default project name it is
`cms-prod_cms-data`) and delete that directory:

```bash
docker run --rm -v cms-prod_cms-data:/data busybox rm -rf /data/python3.12
```

This removes only the stale code copy; `submissions/`, `tests/` and `telegram/`
in the volume are not touched. Do not roll back to a checkout from before this
change: its compose file mounts the volume over the code again, so the `cms`
container runs that stale copy instead of the checkout's code, and if the
directory was deleted it cannot start at all.

### Troubleshooting

**The container exits immediately:**
Check the logs with `./logs.sh`. The most common causes are:
- `CMS_SECRET_KEY` is still set to the example value — generate a real one.
- `CMS_DB_URL` is wrong or the database is unreachable.
- cgroups are not available on your machine — check that you are on a modern
  Linux kernel (5.10+) with `cat /sys/fs/cgroup/cgroup.controllers`.

**"No contests in the database" on startup:**
Set `CMS_CONTEST_ID` in `.env` only after you have imported a contest through
the Admin interface.

**Port already in use:**
Change the corresponding `CMS_*_HTTP_PORT` variable in `.env` and restart.
Make sure to always pass `--env-file .env` — without it, Docker Compose cannot
read the file because it lives in the repo root while the compose file is in
`docker/`, so the port variables fall back to their hardcoded defaults
(8888/8889/8890) regardless of what you set in `.env`.

---

## Helper scripts

The repo root contains convenience wrappers around the `docker compose` commands.
Run them from the repo root — they read `.env` automatically.

| Script | What it does |
|--------|-------------|
| `./up.sh` | Start services. Asks whether to use a local Docker-managed database (`--profile localdb`) and whether to rebuild the image. |
| `./down.sh` | Stop and remove all containers. |
| `./restart.sh` | `down` followed by `up` (prompts again for local DB / rebuild). |
| `./logs.sh` | Follow live logs for all services. |
| `./status.sh` | Show the running status of all containers. |
| `./contest.sh` | Switch the active contest: lists available contests from the database, prompts for a new ID, and updates `CMS_CONTEST_ID` in `.env`. Optionally restarts services to apply the change. |
| `./sync-upstream.sh` | Merge the latest changes from the upstream `cms-dev/cms` repository into this fork and push to `origin`. Requires an `upstream` remote: `git remote add upstream https://github.com/cms-dev/cms.git`. |

For more detail on each script see [docs/docker-scripts.md](docs/docker-scripts.md).

---

## Upstream project

CMS was originally created by the cms-dev community and is used in IOI and
many other programming contests worldwide. This fork does not modify the
database schema, and its one change to the evaluation engine — two-phase
fail-fast grading — is opt-in and off by default, so grading behaves
identically to upstream unless `CMS_TWO_PHASE_EVALUATION` is explicitly set.

- **Upstream repository:** <https://github.com/cms-dev/cms>
- **Upstream documentation:** <https://cms.readthedocs.org/>
- **Support (upstream):** [Telegram](https://t.me/contestms) · <contestms-support@googlegroups.com>

If you used CMS for a contest and want to appear on the testimonials list,
see <http://cms-dev.github.io/testimonials.html>.
