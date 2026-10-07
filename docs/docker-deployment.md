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
| `CMS_RWS_PASSWORD` | Password CMS uses to send scores to the Ranking Web Server. The Ranking Web Server port is only accessible through the reverse proxy (TLS, rate limiting): never keep `CHANGE_ME`. |
| `CMS_CWS_COOKIE_DURATION` | Contestant session lifetime in seconds (default `18000` = 5 hours). Every authenticated request renews the session cookie, including background polls (notifications, submission status), so the session only expires after this long without any request. Must be a positive integer. An open contest page keeps its session alive, so on shared computers contestants must log out and close the tab. |
| `CMS_CWS_REQUEST_TIME_HEADER` | Optional, empty by default (off). The header in which your reverse proxy writes when it received each request, so that a busy server still accepts the submissions sent before the contest stop. Set it only as explained in "Submissions at the contest stop" below. |

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

By default the following ports are bound to `127.0.0.1` (loopback only). A
reverse proxy on the host (Caddy, nginx) is the only way in from outside; it
must proxy to `127.0.0.1:<port>` or `localhost:<port>`. This prevents
Docker-published ports from bypassing host firewalls (such as ufw) and reaching
plain HTTP. You can change all port numbers in `.env`.

| Port | Service |
|------|---------|
| `8888` | Contest Web Server (contestants log in here) |
| `8889` | Admin Web Server (contest administration) |
| `8890` | Ranking Web Server (public scoreboard) |
| `9995` | CMS-Loader (bulk user import; runs only when `CMS_LOADER_SESSION_SECRET`, `CMS_LOADER_ADMIN_USER` and `CMS_LOADER_ADMIN_PASSWORD` are set, see [CMS-Loader](cms-loader.md)) |

## Submissions at the contest stop

The Contest Web Server decides whether a submission is on time by the time it
starts handling the request. On a busy server a request can wait several
seconds before that, so a submission sent just before the contest stop can be
refused as late. If your reverse proxy writes in a header when it received
each request, the Contest Web Server can use that time instead. This is off by
default (`CMS_CWS_REQUEST_TIME_HEADER` empty): the Contest Web Server then uses
the time it handles the request, as before.

- The time in the header decides the contest phase of the request, and it is
  the time stored with submissions and user tests. Tokens, questions,
  notifications and cookies keep the time the Contest Web Server handled the
  request.
- The header holds seconds or milliseconds since the Unix epoch, with an
  optional `t=` prefix (`t=1696698123.456` or `t=1696698123456`). A value the
  Contest Web Server cannot read, or one later than the time it handles the
  request, is ignored.
- It never moves a request more than 60 seconds back: an older time counts as
  60 seconds before the request was handled.

Turn it on only if all of these are true. Otherwise a contestant can write
the header, or send the body late, and get a submission made after the stop
accepted:

- Every request reaches the Contest Web Server through that proxy. In this
  deployment the Contest Web Server ports listen on `127.0.0.1` only (see
  "Ports" above): keep it that way.
- The proxy overwrites the header, replacing any value the client sent.
- The proxy writes the header after it has received the whole request body.
  A proxy that writes it when the request headers arrive lets a client send
  the headers before the stop and the body after it.
- The proxy and CMS share a clock: they run on the same host, or both are
  synced with NTP.

**nginx** meets these: `proxy_set_header` replaces the client's header, and
nginx reads the whole body before it contacts the Contest Web Server
(`proxy_request_buffering on`, the default). Add this line to the `location`
block that proxies to the Contest Web Server (see "nginx configuration" in the
[Docker Scripts Guide](docker-scripts.md)), and do not set
`proxy_request_buffering off` there:

```nginx
proxy_set_header X-Request-Start "t=${msec}";
```

**Caddy** writes `header_up` values by default as soon as the request headers
arrive, before the body, so the `header_up` line alone is not safe.
`request_buffers` makes Caddy read the body first, but only up to that size: a
larger body goes on with an early time. Set `max_size` in `request_body` to the
same size, so that Caddy refuses larger requests (413) and the Contest Web
Server never handles them. Pick a size above your largest request: a user test
can carry up to 5 MB of input. In the site that proxies to the Contest Web
Server (keep your upstreams and other options):

```
request_body {
    max_size 10MB
}
reverse_proxy 127.0.0.1:8888 {
    header_up X-Request-Start "t={time.now.unix_ms}"
    request_buffers 10MB
}
```

Once the proxy writes the header, put its name in `.env` and run
`./restart.sh`:

```
CMS_CWS_REQUEST_TIME_HEADER=X-Request-Start
```

To check that it works, look at the Contest Web Server log (`./logs.sh cms`)
under load, near the stop. It shows lines like this one:

```
Request POST /tasks/sum/submit arrived 3.2 s before its handler ran, as its X-Request-Start header says.
```

They appear only for requests that waited more than one second, so a quiet
server shows none. A line that says "(clamped to 60 s)" means the header was
more than 60 seconds old: check the proxy's clock, and that nothing else can
reach the Contest Web Server.

Two harmless effects near the stop:

- A page that reached the proxy just before the stop, but was handled after
  it, shows the contest as running with no time left, and reloads once more:
  the phase uses the arrival time, the clock the time the page was handled.
- A token used just before the stop is stored with the time the Contest Web
  Server handled the request, at most 60 seconds after the stop.

## Container resource limits

The `cms` container is configured with `ulimits nofile 65536` to allow many
concurrent database connections. It also has a `stop_grace_period: 180s` to let
supervisord stop the ContestWebServer shards one after another, each waiting up
to 40 seconds to flush the participant activity log. The `ranking` container
gets `ulimits nofile 65536` as well.

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

Run from a terminal, `cmsSetupDB` may ask questions. When the database has no
admin yet and `CMS_ADMIN_USER` and `CMS_ADMIN_PASSWORD` are not set, it asks for
an admin username and a password, and asks you to confirm the password. When
the database has no contests, it asks
`No contests found. Create a sample contest? [y/N]`: press Enter to skip it.
`./up.sh` does not ask these questions, because it starts `db-init` detached,
without a terminal.

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
  running, but the Admin Web Server crashes with "Non-hexadecimal digit found"
  and `./up.sh` gives up waiting after 90 seconds — generate a real one with
  `openssl rand -hex 16`. The Contest Web Server fails the same way. With a
  numeric `CMS_CONTEST_ID` it gets that far only once its contest exists: on a
  fresh install it stops earlier, on the missing contest (see the next entry).
  With `CMS_CONTEST_ID=ALL` there is no contest lookup, so it fails with the key
  error right away.
- `CMS_DB_URL` is wrong or the database is unreachable.
- cgroups are not available on your machine — check that you are on a modern
  Linux kernel (5.10+) with `cat /sys/fs/cgroup/cgroup.controllers`.

**"There is no contest with the specified id" in the logs:**
On a fresh install `CMS_CONTEST_ID=1` points to a contest that does not exist
yet, so the Contest Web Server, ProxyService and EvaluationService stop with
this message, and so does the Telegram bot if you configured it
(`CMS_TELEGRAM_BOT_TOKEN` and `CMS_TELEGRAM_CHAT_ID`). This is expected: the
Admin interface still works. Create the contest there, set its ID with
`./contest.sh` (or set `CMS_CONTEST_ID=ALL` in `.env`) and restart with
`./restart.sh`. Do not remove `CMS_CONTEST_ID` to silence the message: without
it no CMS service starts.

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
