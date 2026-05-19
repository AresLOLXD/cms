Contest Management System — OMI Fork
=====================================

[![Build Status](https://github.com/cms-dev/cms/actions/workflows/main.yml/badge.svg)](https://github.com/cms-dev/cms/actions)
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
| `CMS_CONTEST_ID` | The numeric ID of the contest to serve. You get this from the Admin interface after importing a contest — set it then and restart. |

Everything else has a sensible default and can be left as-is on the first try.

### Step 3 — Start CMS

```bash
./up.sh
```

The script asks two questions:
- **Use local database (Docker)?** — answer `y` if you want Docker to manage PostgreSQL for you (recommended for a single server). Answer `n` if you have an existing PostgreSQL server and already set `CMS_DB_URL` accordingly.
- **Rebuild image?** — answer `n` on the first run (or when nothing has changed).

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
many other programming contests worldwide. This fork does not modify the core
evaluation engine, scoring system, or database schema.

- **Upstream repository:** <https://github.com/cms-dev/cms>
- **Upstream documentation:** <https://cms.readthedocs.org/>
- **Support (upstream):** [Telegram](https://t.me/contestms) · <contestms-support@googlegroups.com>

If you used CMS for a contest and want to appear on the testimonials list,
see <http://cms-dev.github.io/testimonials.html>.
