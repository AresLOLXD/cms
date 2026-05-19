# README Fork Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite README.md as a delegate-friendly entry point with a quick-start block and a navigation table, and create user-facing docs for every fork-specific feature.

**Architecture:** Five documentation files touched in dependency order — new feature docs first (they are linked from the README), then the README rewrite, then the RankingWebServer.rst addition. No code changes.

**Tech Stack:** Markdown (`.md`), reStructuredText (`.rst`), Git.

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Create | `docs/cms-loader.md` | User-facing guide for CMS-Loader feature |
| Create | `docs/rekarel.md` | User-facing guide for Rekarel bundled tools |
| Create | `docs/ranking-mexico.md` | User-facing guide for Mexican state flags and auto-teams |
| Modify | `docs/RankingWebServer.rst` | Add dedicated "Custom logo" subsection |
| Modify | `README.md` | Full rewrite from fork perspective |

---

## Task 1: Create `docs/cms-loader.md`

**Files:**
- Create: `docs/cms-loader.md`

- [ ] **Step 1: Create the file with the following exact content**

```markdown
# CMS-Loader

[CMS-Loader](https://github.com/AresLOLXD/CMS-Loader) is a browser-based tool
for bulk-importing users and contest participations via CSV. It is bundled
directly inside the CMS Docker image and managed by supervisord alongside the
other CMS services.

## Enabling CMS-Loader

CMS-Loader is **opt-in** — it only starts if the three required credentials are
set in `.env`. If any of them is missing the service is silently skipped and no
error is raised.

Open `.env` and fill in:

```env
# Random 32+ character string to sign session cookies.
# Generate one with: openssl rand -base64 32
CMS_LOADER_SESSION_SECRET=<your-secret>

# Administrator credentials for the CMS-Loader web UI.
CMS_LOADER_ADMIN_USER=<username>
CMS_LOADER_ADMIN_PASSWORD=<password>

# Port where CMS-Loader listens (default: 9995).
# CMS_LOADER_PORT=9995
```

Rebuild and restart the container after changing these values:

```bash
./restart.sh
# Answer "y" to "Rebuild image?" so the new env vars are picked up.
```

## Accessing CMS-Loader

Once running, open `http://your-server:9995` in your browser and log in with
the credentials you set above.

## Importing users

CMS-Loader expects a CSV file with **one row per user**. The required columns
are:

| Column | Description |
|--------|-------------|
| `username` | Login username (no spaces) |
| `password` | Initial password |
| `first_name` | First name |
| `last_name` | Last name |

Save your spreadsheet as CSV (UTF-8), then use the **Import Users** form in the
CMS-Loader UI to upload it.

## Importing participations

A participation links a user to a contest. The required columns are:

| Column | Description |
|--------|-------------|
| `username` | Must match an existing user |
| `contest_id` | Numeric ID shown in the Admin interface |
| `team` | Team code (e.g. `JAL`). Must match an existing team. |

Use the **Import Participations** form in the UI to upload the CSV.

## Version pinning

By default the image is built from the `main` branch of CMS-Loader. To pin to
a specific release, set `CMS_LOADER_VERSION` in `.env` before building:

```env
CMS_LOADER_VERSION=v1.0.0
```

This is a **build-time argument** — changing it requires a full image rebuild
(`./restart.sh` → answer `y` to "Rebuild image?").

## Troubleshooting

**CMS-Loader is not accessible on port 9995:**
Check that `CMS_LOADER_SESSION_SECRET`, `CMS_LOADER_ADMIN_USER`, and
`CMS_LOADER_ADMIN_PASSWORD` are all set in `.env`. If any is missing the
service does not start. Verify with:

```bash
./logs.sh | grep -i loader
```

**Port conflict:**
Change `CMS_LOADER_PORT` in `.env` and rebuild.
```

- [ ] **Step 2: Verify the file exists and has no placeholder text**

```bash
grep -iE "TBD|TODO|placeholder|fill.in" docs/cms-loader.md
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add docs/cms-loader.md
git commit -m "docs: add user-facing CMS-Loader guide"
```

---

## Task 2: Create `docs/rekarel.md`

**Files:**
- Create: `docs/rekarel.md`

- [ ] **Step 1: Create the file with the following exact content**

```markdown
# Rekarel

This fork bundles two Karel tools directly inside the Docker image — no
installation required.

| Tool | What it is | Source |
|------|-----------|--------|
| `rekarel` | Compiler for the Karel programming language (Node.js CLI) | [@rekarel/cli](https://github.com/kishtarn555/rekarel-js) |
| `karel` | Karel interpreter written in C++ (statically linked binary) | [rekarel-cpp-interpreter v2.3.1](https://github.com/kishtarn555/rekarel-cpp-interpreter) |

Both tools are in PATH inside the container and are available to CMS workers
when evaluating Karel submissions.

## Verifying the tools are available

Run a shell inside the running container and check both tools:

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env \
    exec cms bash -c "rekarel --version && karel"
```

Expected output: the rekarel version string followed by the karel interpreter
usage message.

## Using Karel as a task type

Karel tasks are evaluated using the standard **Batch** task type in CMS. The
task checker or manager calls `rekarel` (to compile the contestant's `.kp`
source) and `karel` (to run the compiled program against each test case).

Refer to the CMS documentation on
[task types](https://cms.readthedocs.io/en/latest/Task%20types.html) for the
full configuration.

## Versions bundled

| Tool | Version |
|------|---------|
| `@rekarel/cli` | latest at image build time (npm latest) |
| `rekarel-cpp-interpreter` | v2.3.1 |

To pin `@rekarel/cli` to a specific version, modify the `RUN npm install -g`
line in the `rekarel-builder` stage of `Dockerfile` and rebuild.
```

- [ ] **Step 2: Verify no placeholder text**

```bash
grep -iE "TBD|TODO|placeholder|fill.in" docs/rekarel.md
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add docs/rekarel.md
git commit -m "docs: add user-facing Rekarel guide"
```

---

## Task 3: Create `docs/ranking-mexico.md`

**Files:**
- Create: `docs/ranking-mexico.md`

- [ ] **Step 1: Create the file with the following exact content**

```markdown
# Ranking: Mexican State Flags and Auto-Team Registration

This fork ships two enhancements to the Ranking Web Server targeted at the OMI:

1. **Bundled flags** — real flag images for all 32 Mexican states, served
   automatically by the ranking server.
2. **Auto-team registration** — teams are created from the flag images found
   in `lib_dir/flags/` every time the ranking server starts.

## How auto-team registration works

On startup, `cmsRankingWebServer` calls `seed_flags_and_teams`, which:

1. Copies the 32 bundled state flag PNG files into `lib_dir/flags/` (default:
   `.venv/lib/ranking/flags/`), without overwriting files you have placed there
   manually.
2. Scans `lib_dir/flags/` for image files (`.png`, `.jpg`, `.gif`, `.bmp`) and
   creates a **team** entry for each filename stem not yet registered.

The team name is resolved from the filename stem: if it matches a known Mexican
state code (e.g. `JAL`) the full state name is used (`Jalisco`); otherwise the
stem itself becomes the team name.

Teams are only *created*, never automatically updated or deleted. Once a team
exists it can be renamed or modified through the admin interface without losing
changes on the next restart.

## State codes

| Code | State | Code | State |
|------|-------|------|-------|
| AGU | Aguascalientes | MOR | Morelos |
| BCN | Baja California | NAY | Nayarit |
| BCS | Baja California Sur | NLE | Nuevo León |
| CAM | Campeche | OAX | Oaxaca |
| CHP | Chiapas | PUE | Puebla |
| CHH | Chihuahua | QUE | Querétaro |
| CMX | Ciudad de México | ROO | Quintana Roo |
| COA | Coahuila | SLP | San Luis Potosí |
| COL | Colima | SIN | Sinaloa |
| DUR | Durango | SON | Sonora |
| GUA | Guanajuato | TAB | Tabasco |
| GRO | Guerrero | TAM | Tamaulipas |
| HID | Hidalgo | TLA | Tlaxcala |
| JAL | Jalisco | VER | Veracruz |
| MEX | Estado de México | YUC | Yucatán |
| MIC | Michoacán | ZAC | Zacatecas |

## Replacing a bundled state flag

Replace the file at `lib_dir/flags/<CODE>.png` while the server is running.
The new image is served immediately — no restart required.

To make the replacement permanent across server restarts, also replace the
source file inside the package installation:

```
<venv>/lib/python3.x/site-packages/cmsranking/flags/<CODE>.png
```

## Adding a custom team with a flag

1. Place an image file at `lib_dir/flags/<YOUR_CODE>.png`.
2. Restart `cmsRankingWebServer`.
3. A team with key `YOUR_CODE` is created automatically. To set a friendlier
   display name, edit the team from the admin interface.

## Regenerating flag images from Wikimedia Commons

If you want to refresh the bundled flags from their original Wikimedia Commons
sources, use the contrib script:

```bash
.venv/bin/python3 cmscontrib/DownloadMexicanStateFlags.py
```

The script downloads each flag at 160 px width, resizes to exactly 160×100 px,
and overwrites `cmsranking/flags/<CODE>.png`. Requires the optional `Pillow`
dependency:

```bash
pip install "cms[contrib]"
```

## Custom logo

To replace the default CMS logo shown in the ranking server, see the
[Custom logo](RankingWebServer.rst#custom-logo) section in
`docs/RankingWebServer.rst`.
```

- [ ] **Step 2: Verify no placeholder text**

```bash
grep -iE "TBD|TODO|placeholder|fill.in" docs/ranking-mexico.md
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add docs/ranking-mexico.md
git commit -m "docs: add user-facing ranking Mexico guide (flags and auto-teams)"
```

---

## Task 4: Add custom logo subsection to `docs/RankingWebServer.rst`

**Files:**
- Modify: `docs/RankingWebServer.rst` — add subsection after the "User face images" subsection

- [ ] **Step 1: Locate the insertion point**

The new subsection goes immediately **before** the `Default \`\`lib_dir\`\` location` section. Find the exact line:

```bash
grep -n "Default \`\`lib_dir\`\`" docs/RankingWebServer.rst
```

Expected output: a line number for `Default ``lib_dir`` location`.

- [ ] **Step 2: Insert the custom logo subsection**

Open `docs/RankingWebServer.rst`. Immediately before the line
`Default \`\`lib_dir\`\` location` (and its `~~~~~~~~~~~~` underline), insert:

```rst
Custom logo
~~~~~~~~~~~

To display a custom logo in the ranking server, place an image file directly
in ``lib_dir`` (see :ref:`rankingwebserver_default-lib-dir-location` for the
default path)::

    lib_dir/logo.png   ← or .jpg, .gif, .bmp

The server serves it automatically at ``/logo`` — no restart required. Remove
the file to revert to the bundled default CMS logo.

**Recommended resolution:** 200×160 px.

**Supported formats:** ``.png``, ``.jpg``, ``.gif``, ``.bmp``

```

- [ ] **Step 3: Verify the file builds without RST errors**

```bash
python3 -c "
import docutils.parsers.rst, docutils.utils, docutils.frontend
p = docutils.parsers.rst.Parser()
settings = docutils.frontend.OptionParser(
    components=(docutils.parsers.rst.Parser,)
).get_default_values()
doc = docutils.utils.new_document('test', settings)
p.parse(open('docs/RankingWebServer.rst').read(), doc)
print('RST parsed OK')
"
```

Expected: `RST parsed OK` (warnings are acceptable, errors are not).

- [ ] **Step 4: Commit**

```bash
git add docs/RankingWebServer.rst
git commit -m "docs: add custom logo subsection to RankingWebServer"
```

---

## Task 5: Rewrite `README.md`

**Files:**
- Modify: `README.md` — full rewrite

- [ ] **Step 1: Replace the full content of README.md with the following**

```markdown
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
- **Support (upstream):** [Telegram](https://t.me/contestms) · [mailing list](contestms-support@googlegroups.com)

If you used CMS for a contest and want to appear on the testimonials list,
see <http://cms-dev.github.io/testimonials.html>.
```

- [ ] **Step 2: Verify all internal links resolve to existing files**

```bash
grep -oP '\[.*?\]\(\K[^)]+' README.md | grep -v '^http' | while read f; do
  [ -e "$f" ] && echo "OK: $f" || echo "MISSING: $f"
done
```

Expected: all lines print `OK:`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README as delegate-friendly fork entry point"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Covered by |
|---|---|
| Quick-start block (4 steps) | Task 5, Quick Start section |
| "About this fork" with OMI + open-to-all + license | Task 5, About section |
| Features navigation table | Task 5, Features section |
| Full Docker guide stays in README | Task 5 (preserved verbatim) |
| Fork attribution maintained | Task 5, title + About + Upstream section |
| `docs/cms-loader.md` with upstream link | Task 1 |
| `docs/rekarel.md` with upstream links | Task 2 |
| `docs/ranking-mexico.md` | Task 3 |
| `docs/RankingWebServer.rst` custom logo section | Task 4 |

### Placeholder scan

No TBD, TODO, or vague steps — all tasks contain complete file content.

### Type consistency

Documentation only — no types or method signatures.
