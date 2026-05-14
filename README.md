Contest Management System
=========================

Homepage: <http://cms-dev.github.io/>

[![Build Status](https://github.com/cms-dev/cms/actions/workflows/main.yml/badge.svg)](https://github.com/cms-dev/cms/actions)
[![Codecov](https://codecov.io/gh/cms-dev/cms/branch/main/graph/badge.svg)](https://codecov.io/gh/cms-dev/cms)
[![Get support on Telegram](https://img.shields.io/badge/Questions%3F-Join%20the%20Telegram%20group!-%2326A5E4?style=flat&logo=telegram)](https://t.me/contestms)
[![Translation status](https://hosted.weblate.org/widget/cms/svg-badge.svg)](https://hosted.weblate.org/engage/cms/)

[🌍 Help translate CMS in your language using Weblate!](https://hosted.weblate.org/engage/cms/)

Introduction
------------

CMS, or Contest Management System, is a distributed system for running
and (to some extent) organizing a programming contest.

CMS has been designed to be general and to handle many different types
of contests, tasks, scorings, etc. Nonetheless, CMS has been
explicitly build to be used in the 2012 International Olympiad in
Informatics, held in September 2012 in Italy.


Download
--------

**For end-users it's best to download the latest stable version of CMS,
which can be found already packaged at <http://cms-dev.github.io/>.**

This git repository, which contains the development version in its
main branch, is intended for developers and everyone interested in
contributing or just curious to see how the code works and wanting to
hack on it.


Support
-------

To learn how to install and use CMS, please read the **documentation**,
available at <https://cms.readthedocs.org/>.

If you have questions or need help troubleshooting some problem, contact us in
the **chat** on [Telegram](https://t.me/contestms), or write on the **support
mailing list** <contestms-support@googlegroups.com>, where no registration is
required (you can see the archives on [Google
Groups](https://groups.google.com/forum/#!forum/contestms-support)).

To help with the troubleshooting, you can upload on some online pastebin the
relevant **log files**, that you can find in `/var/local/log/cms/`.

If you encountered a bug, please file an
[issue](https://github.com/cms-dev/cms/issues) on **GitHub** following the
instructions in the issue template.

**Please don't file issues to ask for help**, we are happy to help on the
mailing list or on Telegram, and it is more likely somebody will answer your
query sooner.

You can subscribe to <contestms-announce@googlegroups.com> to receive
**announcements** of new releases and other important news. Register on
[Google Groups](https://groups.google.com/forum/#!forum/contestms-announce).

For **development** queries, you can write to
<contestms-discuss@googlegroups.com> and as before subscribe or see the
archives on
[Google Groups](https://groups.google.com/forum/#!forum/contestms-discuss).



Deploy with Docker
------------------

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

**Option A — Let Docker run the database for you (recommended for a single
server):**

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env --profile localdb up -d
```

This starts PostgreSQL, initializes the database, and then starts all CMS
services automatically.

**Option B — Use an existing PostgreSQL server:**

Set `CMS_DB_URL` in your `.env` to point to your server, then run:

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env up -d
```

### Step 4 — Import a contest and get its ID

Open the Admin interface in your browser at `http://your-server:8889`.
Use `cmscontrib` tools (e.g. `cmsImportContest`) to import your contest.
The contest ID appears in the Admin interface — update `CMS_CONTEST_ID` in
your `.env` with that value.

To apply the change, restart CMS:

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env restart cms
```

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
docker compose -f docker/docker-compose.prod.yml --env-file .env logs -f cms
```

**Stop everything:**
```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env down
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
Check the logs with `docker compose ... logs cms`. The most common causes are:
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

### Helper scripts

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

Testimonials
------------

CMS has been used in several official and unofficial contests. Please
find an updated list at <http://cms-dev.github.io/testimonials.html>.

If you used CMS for a contest, selection, or a similar event, and want
to publicize this information, we would be more than happy to hear
from you and add it to that list.
