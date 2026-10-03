# Docker deployment

This guide explains how to run CMS using Docker. No prior Docker experience is
required — just follow the steps below.

## What you need first

- [Docker](https://docs.docker.com/get-docker/) (version 24 or newer)
- [Docker Compose](https://docs.docker.com/compose/install/) v2.24 or newer
  (included with Docker Desktop; on Linux install the `docker-compose-plugin`
  package)
- A machine with Linux and cgroups v2 enabled (required by the sandbox).
  Most modern Linux distros (Ubuntu 22.04+, Debian 12+, Fedora 36+) have
  this enabled by default.

## Step 1 — Get the code

```bash
git clone https://github.com/AresLOLXD/cms.git
cd cms
```

## Step 2 — Create your configuration file

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
| `POSTGRES_PASSWORD` | Password for the Docker-managed PostgreSQL database (required for Option A in `.env.example`: PostgreSQL run by Docker, the `y` answer in Step 3). Must match the password in `CMS_DB_URL`. |
| `CMS_ADMIN_USER` | Username for the initial admin account created on first run. Can be removed after the first deploy. |
| `CMS_ADMIN_PASSWORD` | Password for the initial admin account created on first run. Can be removed after the first deploy. |
| `CMS_RWS_PASSWORD` | Password CMS uses to send scores to the Ranking Web Server. Its port is published, so anyone who knows this password can change the public scoreboard: never keep `CHANGE_ME`. |

Everything else has a sensible default and can be left as-is on the first try.

`CMS_CONTEST_ID` is the numeric ID of the contest to serve, or `ALL` to serve
every active contest at once (see
[Running several contests at once](multi-contest.md)). It ships as `1`, the ID
your first contest will get: keep it on a fresh install. Do not remove it —
without it no CMS service starts, not even the Admin interface.

## Step 3 — Start CMS

```bash
./up.sh
```

The script asks two questions:
- **Use local PostgreSQL container?** — answer `y` if you want Docker to manage PostgreSQL for you (recommended for a single server). Answer `n` if you have an existing PostgreSQL server and already set `CMS_DB_URL` accordingly. The answer is saved in `.env` as `CMS_USE_LOCALDB` and becomes the default the next time a script asks.
- **Rebuild?** — a menu from 1 to 7. Choose `1) No` on the first run (Docker builds the images that do not exist yet) or when nothing has changed. After updating the code of a single-contest deployment, choose `2) All services`, which rebuilds both images (`4) CMS only` leaves the ranking on its old image). A multi-contest deployment follows the two steps in [Running several contests at once](multi-contest.md) instead.

## Step 4 — Import a contest and set it as active

Open the Admin interface in your browser at `http://your-server:8889` and
create your contest there. To restore a backup made with `./export.sh`, use
`./import.sh` instead: it reads the backup from the `dumps/` folder (see
[Docker Scripts Guide](docker-scripts.md)).

Then run:

```bash
./contest.sh
```

This lists all contests in the database, prompts you to pick one, updates
`CMS_CONTEST_ID` in `.env`, and optionally restarts services to apply the change.
It accepts only a numeric ID and replaces whatever was set before, including
`ALL`; to serve every active contest, set `CMS_CONTEST_ID=ALL` in `.env` by hand.

## Ports

By default the following ports are exposed. You can change all of them in
`.env`.

| Port | Service |
|------|---------|
| `8888` | Contest Web Server (contestants log in here) |
| `8889` | Admin Web Server (contest administration) |
| `8890` | Ranking Web Server (public scoreboard) |
| `9995` | CMS-Loader (bulk user import; runs only when `CMS_LOADER_SESSION_SECRET`, `CMS_LOADER_ADMIN_USER` and `CMS_LOADER_ADMIN_PASSWORD` are set, see [CMS-Loader](cms-loader.md)) |

## Common operations

**Follow live logs:**
```bash
./logs.sh -f
```

**Stop everything:**
```bash
./down.sh
```

**Update to a newer version of this fork:**
```bash
git pull
./up.sh
```
In `./up.sh`, choose a rebuild option as described in Step 3 (a multi-contest
deployment follows the two steps in
[Running several contests at once](multi-contest.md) instead).
`./sync-upstream.sh` is not for this: it is a tool for the fork's maintainers
(see "Helper scripts" below).

**Run multiple Contest Web Server instances (for load balancing):**

Set `CMS_CWS_COUNT=2` in your `.env`. CMS will start two contest web servers
on consecutive ports (8888 and 8889 with the default `CMS_CWS_HTTP_PORT=8888`).
The Admin and Ranking Web Server ports must not fall in that range: set
`CMS_AWS_HTTP_PORT` and `CMS_RWS_HTTP_PORT` to `CMS_CWS_HTTP_PORT + CMS_CWS_COUNT`
or higher (for example 8890 and 8891). Point your load balancer (e.g. nginx) at
the contest web server ports with `ip_hash`, as shown in "Scaling Contest Web
Server" in the [Docker Scripts Guide](docker-scripts.md).

**Running `docker compose` directly:**

The next two commands call Docker Compose without a helper script, so they
must pass what the scripts add for you. Run them from the repo root and:
- replace `cms-prod` after `-p` with your `CMS_PROJECT_NAME` (`cms-prod` is the
  default). Without `-p`, Docker Compose works on a project named `docker`,
  not on your deployment;
- keep `--profile localdb` only if Docker manages your database (you answered
  `y` to "Use local PostgreSQL container?"); remove it if you use an external
  PostgreSQL server;
- if you changed `CMS_CWS_HTTP_PORT`, put
  `CMS_CWS_HTTP_PORT_END=<last Contest Web Server port>` in front of
  `docker compose` (that is `CMS_CWS_HTTP_PORT + CMS_CWS_COUNT - 1`; the
  scripts compute it), or Docker Compose may stop with `invalid containerPort`.

**Stop and delete all data (including a Docker-managed database — be careful):**
```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env -p cms-prod --profile localdb down -v
```
This deletes the Docker volumes of the deployment. With an external PostgreSQL
server it does not delete the database, which lives on that server.

**Re-run the database setup:**

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env -p cms-prod --profile localdb run --rm db-init
```

The `db-init` service runs `cmsSetupDB`, which creates missing tables, applies
the fork's schema updates and creates the first admin if there is none. It
deletes nothing, so it is safe to re-run, and `./up.sh` already runs it on
every start. It does not reset the database: to start over with an empty
Docker-managed database, use the `down -v` command above, then `./up.sh`.

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
in [Running several contests at once](multi-contest.md): `./up.sh` and `3) Ranking
only` first (it starts only the ranking container, so `cms` is not recreated
from its old image), then `./up.sh` and `4) CMS only`. Never start this version
with an old image, which is also what `1) No` does (the default in `./up.sh`,
`./restart.sh` and `./contest.sh`): the old
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

## Troubleshooting

**A container exits or `./up.sh` times out:**
Check the logs with `./logs.sh`. The most common causes are:
- `CMS_SECRET_KEY` is still set to the example value. The container keeps
  running, but the Admin and Contest Web Servers crash with "Non-hexadecimal digit found" and `./up.sh`
  gives up waiting after 90 seconds — generate a real one with
  `openssl rand -hex 16`.
- `CMS_DB_URL` is wrong or the database is unreachable.
- cgroups are not available on your machine — check that you are on a modern
  Linux kernel (5.10+) with `cat /sys/fs/cgroup/cgroup.controllers`.

**"There is no contest with the specified id" in the logs:**
On a fresh install `CMS_CONTEST_ID=1` points to a contest that does not exist
yet, so the Contest Web Server, ProxyService and EvaluationService stop with
this message. This is expected: the Admin interface
still works. Create the contest there, set its ID with `./contest.sh` (or set
`CMS_CONTEST_ID=ALL` in `.env`) and restart with `./restart.sh`. Do not remove
`CMS_CONTEST_ID` to silence the message: without it no CMS service starts.

**Port already in use:**
Change the corresponding `CMS_*_HTTP_PORT` variable in `.env` and restart.
If you run `docker compose` yourself, always pass `--env-file .env` and the
other options listed in "Running `docker compose` directly" above — without
`--env-file`, Docker Compose cannot read the file because it lives in the repo
root while the compose file is in `docker/`, so the port variables fall back to
their hardcoded defaults (8888/8889/8890) regardless of what you set in `.env`.

## Helper scripts

The repo root contains convenience wrappers around the `docker compose` commands.
Run them from the repo root — they read `.env` automatically.

| Script | What it does |
|--------|-------------|
| `./up.sh` | Start services. Asks whether to use a local Docker-managed database (`--profile localdb`) and whether to rebuild the image. |
| `./down.sh` | Stop and remove all containers. Asks for confirmation first. |
| `./restart.sh` | `down` followed by `up` (asks for confirmation, then prompts again for local DB / rebuild). |
| `./logs.sh` | Show the last 100 log lines of all services; `./logs.sh -f` follows them live. |
| `./status.sh` | Show the running status of all containers. |
| `./contest.sh` | Switch the active contest: lists available contests from the database, prompts for a new ID, and updates `CMS_CONTEST_ID` in `.env`. Optionally restarts services to apply the change. |
| `./export.sh` | Back up contest data to a `.tar.gz` file in `dumps/`. |
| `./import.sh` | Restore contest data from a backup in `dumps/` made with `./export.sh`. |
| `./sync-upstream.sh` | For the fork's maintainers, not for updating a deployment: merges the latest changes from the upstream `cms-dev/cms` repository into this fork's `main` and pushes it to `origin`. Requires an `upstream` remote: `git remote add upstream https://github.com/cms-dev/cms.git`. |

For more detail on each script see [Docker Scripts Guide](docker-scripts.md).
