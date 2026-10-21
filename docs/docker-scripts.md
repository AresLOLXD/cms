# Docker Scripts Guide

## Introduction

These ten scripts handle everything needed to start, stop, and manage the contest system. Instead of typing one very long command with confusing flags (easy to mistype and hard to remember), you run a simple script. Each script does one thing and gives you clear feedback about what's happening.

## Before you start

- **Docker must be installed.** Check by opening a terminal and running `docker --version`. It should print a version number. If you see "command not found," install Docker from [https://docs.docker.com/get-docker/](https://docs.docker.com/get-docker/).

- **Create a `.env` configuration file.** Copy the example file with this command:
  ```bash
  cp .env.example .env
  ```
  Then open `.env` in a text editor (nano, gedit, VS Code, or whatever you normally use) and change every value marked `CHANGE_ME`. At minimum: `CMS_DB_URL` password, `POSTGRES_PASSWORD`, `CMS_SECRET_KEY`, `CMS_ADMIN_USER`, `CMS_ADMIN_PASSWORD`, and `CMS_RWS_PASSWORD`.

## Quick start

Follow these steps to start the contest system for the first time:

1. **Open a terminal** in the folder where the scripts live (the project root).

2. **Run the startup script:**
   ```bash
   ./up.sh
   ```

3. **Answer the first question:** `Use local PostgreSQL container? [y/N]`
   - Answer **`y`** if you want these scripts to manage the database (most common for new setups).
   - Answer **`n`** only if you already have a PostgreSQL database running somewhere else.

4. **Answer the second question:** `Rebuild?` is a menu from 1 to 7.
   - Choose **`1`** (No, the default and the fastest) on the first run or when nothing has changed.
   - After updating the code of a single-contest deployment, choose **`2`** (all services); **`4`** (CMS only) leaves the ranking container on its old image. A multi-contest deployment uses **`3`** and then **`4`**, see below.
   - Choose **`3`** (Ranking only) to rebuild and start only the ranking container: the other services are not touched. It is the first step of a multi-contest update, see [Running several contests at once](multi-contest.md).

5. **Wait for startup.** You'll see output like:
   ```
   [+] Running 4/4
    ✓ Container cms-prod-db-1           Created
    ✓ Container cms-prod-db-init-1      Created
    ✓ Container cms-prod-ranking-1      Created
    ✓ Container cms-prod-cms-1          Created
   ```
   The system takes about 30-60 seconds to fully start. Services may not respond immediately.

6. **Check the status** once the script finishes:
   ```bash
   ./status.sh
   ```
   You should see several containers with status `Up`. If any say `Exit` or `Exited`, something went wrong — check `./logs.sh` for error messages.

7. **Open your browser** and try these URLs (assuming default ports):
   - **Contestants log in here:** [http://localhost:8888](http://localhost:8888)
   - **Contest administration:** [http://localhost:8889](http://localhost:8889)
   - **Public scoreboard:** [http://localhost:8890](http://localhost:8890)

If the pages don't load, the system might still be starting up. Wait another 30 seconds and try again, or run `./logs.sh` to see what's happening.

## Scripts reference

### up.sh

Starts the contest system. Asks two questions before starting: whether to use a local database and whether to rebuild the Docker image. Run this when the system is stopped.

```bash
./up.sh
```

### down.sh

Stops all running services. The data is preserved — you can run `./up.sh` again to start back where you left off.

```bash
./down.sh
```

### status.sh

Shows whether all services are running correctly. Lists each container (a lightweight virtual environment) with its current state, then each program that supervisord runs in the `cms` container. Every container should say `Up`, and every program `RUNNING`; a program you stopped on purpose says `STOPPED`, and the list is still shown.

If supervisord does not answer within 10 seconds, the script prints `(supervisord did not answer in 10 s)` instead of waiting forever. `(container not running)` means that the `cms` container is not running.

Next comes the watchdog's line (see "The watchdog" in [Docker deployment](docker-deployment.md)): its mode, how long ago its last cycle ran, and how many of its alerts are open. `(no answer from the cms container)` means that the `cms` container did not run the command.

The last part, `Disk`, shows how full the log volume is: the filesystem, its size, what is used and what is available (in blocks of 1 KiB), and the percentage used, as `df -P` reports them inside the `cms` container. From 80%, it adds the line `WARNING: the log disk is N% full`. It prints `(no answer from the cms container)` too when that container did not run the command.

Every program runs with `stopasgroup` and `killasgroup`: stopping or restarting one with `supervisorctl` also ends the processes it started, such as the sandboxes of a Worker.

```bash
./status.sh
```

### logs.sh

Shows the last 100 lines of what every service logged — useful for debugging problems. Arguments are passed to `docker compose logs`: add `-f` to keep following the logs (press `Ctrl+C` to stop), `--tail 2000` for more lines, or a service name (`cms`, `ranking`) to see only that one.

```bash
./logs.sh
```

### restart.sh

Stops and immediately starts the system again. Use this after editing the `.env` config file to apply changes, or if services seem stuck. Answer **`1`** (No) to the rebuild question: if you choose **`3`** (Ranking only), only the ranking container starts after the stop, and you have to run `./up.sh` and choose **`4`** (CMS only) to start the rest.

```bash
./restart.sh
```

### contest.sh

Changes which contest is currently active. It shows you the list of contests already in the system and lets you pick one by ID. After changing, it can automatically restart the services to apply the change.

```bash
./contest.sh
```

> `clear-ranking.sh` was removed: use **Ranking groups → Regenerate** in the
> Admin Web Server instead (see [Running several contests at once](multi-contest.md)).

### export.sh

Creates a backup of contest data as a `.tar.gz` file in the `dumps/` folder. Asks which contests to back up, the filename to use, and whether to leave out submissions, user accounts, or generated files.

```bash
./export.sh
```

The backup file is saved to `dumps/` at the project root and is ready to use immediately after the script finishes.

The backup does **not** contain:

- **The ranking groups**: neither the groups themselves (name, description,
  hide and freeze windows, staff password) nor which contest is assigned to
  which group.
- **The participant activity log.** Download its CSV from each contest's
  **Activity** page and keep it with the backup (see
  [Participant activity](participant-activity.md)).

For a complete copy of the database, ranking groups and activity log
included, use `backup.sh` (see below). To restore that copy, follow
"Restoring a backup".

### import.sh

Restores contest data from a backup file created by `export.sh`. Lists the available backups in `dumps/`, lets you pick one, and walks you through the options — including whether to wipe the database first (useful for a full restore from scratch).

```bash
./import.sh
```

> **Warning:** Choosing to wipe the database before importing will permanently delete all existing contest data. Only do this when you are sure you want to restore from the selected backup.

Imported contests arrive **inactive** unless you choose to wipe the
database first (`-d`), which restores each contest's active flag from the
backup. After a normal import, activate the contests from the Admin Web
Server when they are ready.

Imported contests also arrive **without a ranking group**, even with `-d`,
because the backup does not contain the groups (see `export.sh` above). With
`-d`, the ranking groups that were in the database are deleted with
everything else. After a restore, create the ranking groups again, with their
windows and staff passwords, and assign the contests: follow "Before an exam
day" in [Running several contests at once](multi-contest.md).

The activity log is not restored either, and `-d` deletes the one in the
database.

### backup.sh

Makes a full backup of the database with `pg_dump`. Unlike `export.sh`, it
has everything the database has: the ranking groups, the participant
activity log and the admin accounts. It copies the database as it was at one
moment, even while the database is in use. It needs `sudo`, and refuses to
run without it, because only root can remove what it writes.

```bash
sudo ./backup.sh [label] [-j N]
```

- `label` (optional) is added to the name of the backup. It can have only
  letters, digits, `_` and `-`.
- `-j N` is the number of workers of `pg_dump` (default `4`). More workers
  finish sooner and use more CPU of the database server.

Before it dumps anything, the script reads the connection from `.env`
(`CMS_DB_URL`, or `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB` when
`CMS_USE_LOCALDB=true`). It needs the Docker network of the project: if there
is none, it tells you to run `./up.sh` first. It pulls the `postgres:N` image
that does the dump if the server does not have it; N is the major version of
the PostgreSQL server, but never below 17, because `pg_dump` 17 or newer
spreads the large objects across the workers. It stops if `backups/` has less
free space than the database takes. A backup is usually a fraction of that:
the dump of cms-live of 2026-10-14 took 9.7 GB for an 18 GB database. But if
`backups/` is on the same filesystem as the PostgreSQL server, a backup that
fills the disk can stop PostgreSQL, so keep an eye on the free space:
`./status.sh` shows how full the disk of the log volume is, and warns from
80%; if `backups/` is on another disk, check it with `df -h backups` before a
backup. Then it prints a summary and asks for one confirmation, whose default
is no:

```
Database:   cmsdb on 172.18.0.1:5432 (PostgreSQL 16.15)
Size:       18G
Free space: 120G in /home/ops/cms/backups
Workers:    4
Image:      postgres:17
Backup:     /home/ops/cms/backups/cms-20261010-120000-before-exam
Start the backup? [y/N]
```

The backup is the directory `backups/cms-YYYYmmdd-HHMMSS` of the project
root, with `-label` at the end if you gave one. `backups/` has mode 700, git
and Docker ignore it, and it is not `dumps/`, so `./import.sh` does not list
the backups. While `backup.sh` writes a backup, the name of its directory
ends in `.partial`; it renames the directory when the backup is complete and
checked, so a directory without `.partial` is a finished backup. If you stop
the script with Ctrl+C, or it fails, it removes the `.partial` directory.

Inside the backup:

- The dump, in the directory format of `pg_dump` compressed with zstd.
- `MANIFEST`: the exact row count of every table, the number of large
  objects, the date (UTC), the Git commit, the version of the fork, the
  versions of PostgreSQL and `pg_dump`, and the name, encoding, locale and own
  settings of the database.
- `SHA256SUMS`: the SHA-256 of every other file.

Before the rename, the script reads the table of contents of the dump and
checks that it has a `TABLE DATA` entry for every table of `MANIFEST` and the
same number of large objects. At the end it prints the path, the size, how
long it took and an `rsync -a` line to copy the backup off the server, which
you run from your machine:

```
Backup complete: /home/ops/cms/backups/cms-20261010-120000-before-exam
Size:     5.1G
Duration: 142 s
Copy it off the server, from your machine:
  rsync -a --rsync-path="sudo rsync" ops@cms-live:/home/ops/cms/backups/cms-20261010-120000-before-exam ./
Reminder: .env is not inside the backup; keep a copy of it somewhere safe.
```

The files of the backup belong to root and nobody else can read them. The
backup has neither `.env` nor the Docker volumes.

### verify-backup.sh

Proves that a backup made by `backup.sh` restores, before you need it. It
needs only Docker: no `.env`, no CMS and no access to the database, so you
can run it on your own machine after copying the backup, or on the server
outside the contest hours. It restores the whole backup into a PostgreSQL
container that it removes at the end, so it needs the disk space of one copy
of the database in Docker's storage, and it takes about as long as a restore.

```bash
./verify-backup.sh <backup dir>
```

The files of a backup belong to root and nobody else can read them. On the
server, run the script with `sudo`. After an `rsync -a` to another machine,
run it as the user who ran `rsync`, who owns the files. It refuses a
directory that ends in `.partial`.

It runs these checks, in this order:

1. The checksums: every file must match `SHA256SUMS`. If not, it stops there
   (`ERROR: the checksums do not match`).
2. It starts a throwaway `postgres:N` container without network, where N is
   the major version of the `pg_dump` that made the backup (from `MANIFEST`),
   and restores the backup in it with `pg_restore --exit-on-error`, one worker
   per CPU. The container is removed at the end, and also if you press Ctrl+C.
3. The checks on the restored database. All three run, and each failure
   prints a line that starts with `FAILED:`:
   - The row count of every table, and the number of large objects, equal
     those of `MANIFEST`.
   - Every `fsobjects.loid` points to a large object that exists.
   - The SHA1 of the content of every large object equals `fsobjects.digest`.

Large objects that no `fsobjects` row uses are only reported, as a line that
starts with `Info:`; that is not an error.

The exit status is 0 when everything passes, after the line `OK: <dir>
restores; the checksums, the counts and the large objects match.` Otherwise it
is 1, and the line that starts with `ERROR:` or `FAILED:` names the check that
failed. Do not trust a backup that fails.

## Restoring a backup

A restore is a procedure, not a script: it stops CMS and renames databases in
production, so a person runs each step. It restores a backup of `backup.sh`
into a new database while CMS keeps running, and CMS is stopped only to swap
the names, for about one to three minutes. Run every command in one bash
shell, in the project root, as a user that can run `docker`. The commands
write the database name without quotes, so it must have only lowercase
letters, digits and underscores, as `cmsdb`. Restoring `.env` and restoring
onto a PostgreSQL major older than the one that made the backup are not
covered.

Set these first, in the shell you will use for all the steps. Change only
`BACKUP`, which is the absolute path of the backup, and `WORKERS`, which is the
number of workers of `pg_restore` (keep it low while CMS is serving a
contest):

```bash
source docker/_lib.sh           # COMPOSE_CMD: docker compose with the project's file and name
source docker/_backup_lib.sh
db_connection_from_env          # PGHOST, PGPORT, PGUSER, PGPASSWORD and PGDATABASE, from .env
NETWORK=$(compose_network)      # the Docker network of the project
DB=$PGDATABASE                  # the live database
CMS_USER=$PGUSER                # the database user of CMS
BACKUP=$PWD/backups/cms-20261010-120000
WORKERS=4
OLD=${DB}_before_restore_$(date +%Y%m%d_%H%M%S)

# Runs a PostgreSQL client in a container on the network of CMS, with the backup at /backup.
PGIMAGE=postgres:17
pgc() {
  docker run --rm --init --network "$NETWORK" \
    --mount "type=bind,source=$BACKUP,target=/backup,readonly" \
    -e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGOPTIONS "$PGIMAGE" "$@"
}
# The value of a key of MANIFEST.
manifest() { pgc cat /backup/MANIFEST | awk -F'\t' -v key="$1" '$1 == key { print $2 }'; }

# The client must be as new as the server, and at least 17.
VERSION_NUM=$(pgc psql -X -At -d postgres -c 'SHOW server_version_num')
PGIMAGE=postgres:$(pick_pg_major "$VERSION_NUM")
# --exit-on-error needs a server of major 17 or newer (see step 2 of "Restore into a new database").
EXIT_ON_ERROR=; if (( VERSION_NUM >= 170000 )); then EXIT_ON_ERROR=--exit-on-error; fi
echo "$NETWORK $DB $CMS_USER $PGHOST:$PGPORT $VERSION_NUM $PGIMAGE $OLD"
```

The last line prints what you set, for example `cms-prod_default cmsdb
cmsuser 172.18.0.1:5432 160015 postgres:17 cmsdb_before_restore_20261012_093000`.
Close the shell when you finish: it holds the database password.

If it prints an error with `no pg_hba.conf entry` and `database "postgres"`
instead, the server lets the CMS user connect only to its own database, as
`.env.example` suggests. The procedure also connects to the databases
`postgres` and `${DB}_restore`: add them to the line of the CMS user in
`pg_hba.conf`, for example
`host cmsdb,postgres,cmsdb_restore cmsuser 172.18.0.0/16 scram-sha-256`,
reload PostgreSQL with `sudo systemctl reload postgresql` and run this block
again.

**Preflight**, with CMS still running:

1. The backup is intact:
   ```bash
   pgc sh -c 'cd /backup && sha256sum --quiet --strict -c SHA256SUMS' && echo "checksums OK"
   pgc pg_restore -l /backup > /dev/null && echo "table of contents OK"
   ```
   It prints `checksums OK` and `table of contents OK`. For a stronger test,
   run `sudo ./verify-backup.sh "$BACKUP"` instead.
2. The server is not older than the one that made the backup:
   ```bash
   pgc cat /backup/MANIFEST | grep -E '^(created_utc|server_version|pg_dump_version|encoding|lc_collate|lc_ctype|large_objects)'
   echo "server major: $(( VERSION_NUM / 10000 ))"
   ```
   It prints, for example (a tab separates each name from its value):
   ```
   created_utc       2026-10-10T18:12:40Z
   server_version    16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
   pg_dump_version   17.6
   encoding          UTF8
   lc_collate        en_US.UTF-8
   lc_ctype          en_US.UTF-8
   large_objects     34876
   server major: 16
   ```
   The `server major` must not be lower than the major of `server_version`.
3. The CMS database user can create databases:
   ```bash
   pgc psql -X -At -d postgres -c "SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user"
   ```
   It prints `t`. If it prints `f`, run this on the PostgreSQL server, which
   prints `ALTER ROLE`, and repeat the step:
   ```bash
   sudo -u postgres psql -c "ALTER ROLE $CMS_USER CREATEDB;"
   ```
4. There is room for a second copy of the database:
   ```bash
   pgc psql -X -At -d postgres -c "SELECT pg_size_pretty(pg_database_size('$DB'))"
   df -h "$(sudo -u postgres psql -X -At -c 'SHOW data_directory')"
   ```
   The `Avail` column of `df` must be larger than the size, with room to
   spare. With the bundled database (`CMS_USE_LOCALDB=true`), use
   `"${COMPOSE_CMD[@]}" exec -T db df -h /var/lib/postgresql/data` instead of
   the second command.

**Restore into a new database**, with CMS still running and the live
database untouched:

1. Create the database `${DB}_restore`, with the encoding and the locale of
   `MANIFEST`:
   ```bash
   pgc psql -X -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${DB}_restore OWNER $CMS_USER TEMPLATE template0 ENCODING '$(manifest encoding)' LC_COLLATE '$(manifest lc_collate)' LC_CTYPE '$(manifest lc_ctype)'"
   ```
   It prints `CREATE DATABASE`. If it says that the database already exists,
   an earlier attempt left it: drop it with
   `pgc psql -X -d postgres -c "DROP DATABASE ${DB}_restore"` and repeat.
2. Restore the backup, as the CMS user, so that the CMS user owns the large
   objects. If `postgres` owns them, CMS gets `permission denied for large
   object` on every file. `synchronous_commit=off` makes it faster:
   ```bash
   PGOPTIONS='-c synchronous_commit=off' pgc pg_restore -Fd -j "$WORKERS" --no-owner --no-acl $EXIT_ON_ERROR -d "${DB}_restore" /backup 2> "/tmp/restore-$OLD.err"; echo "pg_restore exit status: $?"
   ```
   It prints nothing while it works and takes minutes (cms-live, with 18 GB
   and 34876 large objects, took about 6 minutes with 16 workers on 6 vCPUs).
   With a server of major 17 or newer it must print `pg_restore exit status:
   0`, and the error file must be empty. With an older server, `pg_restore`
   17 sends `SET transaction_timeout = 0` on every connection and the server
   rejects it. The error file then has this harmless error, once for each
   worker and twice for the leader, each followed by the line `Command was:
   SET transaction_timeout = 0;`, and the exit status is 1:
   ```
   pg_restore: error: could not execute query: ERROR:  unrecognized configuration parameter "transaction_timeout"
   ```
   That is why `--exit-on-error` is only used with a server of major 17 or
   newer. Read the error file for any other error:
   ```bash
   grep 'pg_restore: error:' "/tmp/restore-$OLD.err" | grep -v transaction_timeout
   ```
   It must print nothing. A line printed is a failed restore: drop
   `${DB}_restore` as in step 1, find the cause and start again. Do not trust
   the `errors ignored on restore` count that `pg_restore` prints at the end:
   it leaves out the errors of the workers.
3. Check the restored copy against `MANIFEST`:
   ```bash
   EXPECTED=$(pgc cat /backup/MANIFEST | awk -F'\t' '$1 == "table" { print $2 "\t" $3 } $1 == "large_objects" { print "(large objects)\t" $2 }')
   ACTUAL=$(pgc psql -X -At -F $'\t' -d "${DB}_restore" -c "SELECT tablename, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM public.%I', tablename), false, true, '')))[1]::text FROM pg_tables WHERE schemaname = 'public' UNION ALL SELECT '(large objects)', count(*)::text FROM pg_largeobject_metadata")
   manifest_compare "$EXPECTED" "$ACTUAL" && echo "counts match"
   pgc psql -X -At -d "${DB}_restore" -c "SELECT count(*) FROM pg_largeobject_metadata WHERE pg_get_userbyid(lomowner) <> current_user"
   pgc vacuumdb -q --analyze-only -n public -j "$WORKERS" -d "${DB}_restore"
   ```
   The first command prints `counts match`; if the counts differ, it prints
   one line for each table that differs, as `table: <in MANIFEST> vs
   <restored>`. The second prints `0`, the number of large objects that the
   CMS user does not own. `vacuumdb` prints nothing.

**Switch**, the only downtime:

1. Stop CMS. Never use `down`: it recreates the Docker network, and its
   gateway, the address at which an external database is reached, can change.
   ```bash
   "${COMPOSE_CMD[@]}" stop cms
   ```
   It prints the `cms` container as `Stopped`. Contestants are disconnected
   from here.
2. End the sessions that remain on the live database:
   ```bash
   pgc psql -X -At -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB' AND usename = current_user"
   ```
   It prints a `t` for each session it ended, or nothing.
3. Put the copy in place and keep the old database under another name:
   ```bash
   pgc psql -X -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE $DB RENAME TO $OLD" -c "ALTER DATABASE ${DB}_restore RENAME TO $DB"
   ```
   It prints `ALTER DATABASE` twice. If it says that the database is being
   accessed by other users, a session of another user is open (for example
   your own `psql`): close it and repeat steps 2 and 3.
4. Apply again the settings of the database, because the copy does not have
   them:
   ```bash
   pgc cat /backup/MANIFEST | grep '^database_setting'
   ```
   It prints nothing if the database has none. Each line it prints has three
   columns, separated by a tab: `database_setting`, a role or `*`, and
   `name=value`. For a line with `*`, run
   `pgc psql -X -d postgres -c "ALTER DATABASE $DB SET name TO 'value'"`. For a
   line with a role, run
   `pgc psql -X -d postgres -c "ALTER ROLE role IN DATABASE $DB SET name TO 'value'"`.
5. Start CMS:
   ```bash
   "${COMPOSE_CMD[@]}" up -d --no-recreate --no-build --wait cms
   ```
   It returns when the `cms` container is healthy, after 30 to 60 seconds.
6. If CMS does not come up, or the data is wrong, go back: repeat steps 1 and
   2, swap the names back, which leaves the failed copy as `${DB}_restore`,
   and repeat step 5:
   ```bash
   pgc psql -X -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE $DB RENAME TO ${DB}_restore" -c "ALTER DATABASE $OLD RENAME TO $DB"
   ```

**After the switch**:

- Run `./status.sh`: every container is `Up` and every program `RUNNING`.
- In the Admin Web Server, open **Overview**. The **Workers status** title
  reads "(N connected, M configured)": N must equal M.
- On **Ranking groups**, press **Regenerate** on each group, so that the
  scoreboards match the restored data.
- Check the **Active** flag of each contest: it is as it was when the backup
  was made.
- When you are satisfied, and not before, drop the old database. It cannot
  be undone. If you closed the shell, set `OLD` to the name that the first
  command prints:
  ```bash
  pgc psql -X -At -d postgres -c "SELECT datname FROM pg_database WHERE datname LIKE '${DB}\_before\_restore\_%'"
  pgc psql -X -d postgres -c "DROP DATABASE $OLD"
  ```

## Configuring the project name

The `CMS_PROJECT_NAME` variable in `.env` (default: `cms-prod`) is used to group Docker containers on your machine. If you run only one copy of CMS, leave it as is. If you need to run two separate CMS setups on the same machine (for example, testing and production), change this to a different short name for the second one — something like `cms-test` or `cms-staging`. This prevents containers from different instances from conflicting. Keep it short and use lowercase letters and hyphens only.

## Scaling Contest Web Server

By default, a single `cmsContestWebServer` instance handles all contestant traffic. If you
need more capacity — typically for contests with hundreds of simultaneous users — you can
run several instances behind a reverse proxy.

### How it works

Set `CMS_CWS_COUNT` in `.env` to the number of shards you want. Each shard listens on a
consecutive port starting from `CMS_CWS_HTTP_PORT`:

| Shard | Port |
|-------|------|
| 0 | `CMS_CWS_HTTP_PORT` (e.g. 8888) |
| 1 | `CMS_CWS_HTTP_PORT + 1` (e.g. 8889) |
| 2 | `CMS_CWS_HTTP_PORT + 2` (e.g. 8890) |

All shard ports are bound to `127.0.0.1` (loopback only) when you start with `./up.sh`. The reverse proxy must be configured to access them there.

### Port conflict warning

With the defaults (`CMS_CWS_HTTP_PORT=8888`, `CMS_AWS_HTTP_PORT=8889`, `CMS_RWS_HTTP_PORT=8890`), setting
`CMS_CWS_COUNT > 1` puts shards on ports that may collide with other servers.
When `CMS_CWS_COUNT > 1`, move both `CMS_AWS_HTTP_PORT` and `CMS_RWS_HTTP_PORT` above the CWS range:

```
CMS_CWS_COUNT=3
CMS_CWS_HTTP_PORT=8888   # shards: 8888, 8889, 8890
CMS_AWS_HTTP_PORT=8891   # must be >= CMS_CWS_HTTP_PORT + CMS_CWS_COUNT
CMS_RWS_HTTP_PORT=8892   # must also be outside the CWS shard range
```

### nginx configuration

Your reverse proxy must balance incoming contestant requests across all shards. Here is a
minimal nginx configuration for three shards (`CMS_CWS_COUNT=3`, base port 8888). Adapt
the port numbers to match your `.env`:

```nginx
upstream cws {
    ip_hash;                        # required: keeps each contestant on the same shard
    server 127.0.0.1:8888;
    server 127.0.0.1:8889;
    server 127.0.0.1:8890;
}

server {
    listen 80;
    server_name contest.example.com;

    location / {
        proxy_pass         http://cws;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   X-Request-Start "t=${msec}";
    }
}
```

The `X-Request-Start` line records when nginx received each request, so that
a busy CMS still accepts the submissions sent before the contest stop; it is
used only with `CMS_CWS_REQUEST_TIME_HEADER=X-Request-Start` in `.env` (see
"Submissions at the contest stop" in [Docker deployment](docker-deployment.md)).

> **`ip_hash` is required.** Without it, a contestant's requests may land on different
> shards and their session will be lost. CMS stores session state in-process, not in a
> shared store.

Also set `CMS_NUM_PROXIES_USED=1` in `.env` so CMS logs the real contestant IP addresses
instead of the proxy's address.

After changing `.env`, run `./restart.sh` to apply.

## Troubleshooting

### The page doesn't load in my browser

The services might still be starting up — they can take 30-60 seconds to fully initialize. Run `./status.sh` to see if all containers say `Up`. If they do, wait a bit longer and try the page again. If some containers show `Exit` or `Exited`, run `./logs.sh` to see what error caused them to fail.

### contest.sh shows no contests

No contests have been imported yet. Use the Admin interface at [http://localhost:8889](http://localhost:8889) to create a new contest or import an existing one. Once you've imported a contest, run `./contest.sh` again and you should see it in the list.

### The system fails to start with "Non-hexadecimal digit found"

`CMS_SECRET_KEY` in your `.env` file must be a 32-character hexadecimal string (16 bytes). If you generated it with a tool that produces Base64 output (characters like `/`, `+`, or `=`), the admin server will crash with this error.

Generate a valid key and update `.env` in one command:

```bash
NEW_KEY=$(openssl rand -hex 16) && sed -i "s|CMS_SECRET_KEY=.*|CMS_SECRET_KEY=${NEW_KEY}|" .env && grep CMS_SECRET_KEY .env
```

Then restart with `./restart.sh`.

### The system starts but "There is no contest with the specified id"

`CMS_CONTEST_ID` is required — without it, `supervisord.conf` is not generated and no services start at all. On a fresh install the contest does not exist in the database yet, so `cmsContestWebServer`, `cmsEvaluationService` and `cmsProxyService` (and the Telegram bot, if configured) stop with this message. This is normal.

The admin server (`cmsAdminWebServer`) still starts and is available at port 8889. Use it to create or import your first contest. The generated `supervisord.conf` sets `autorestart=true` and `startretries=30` on every service, so supervisord keeps starting a service that exits right after it starts for about 8 minutes, then gives up on it (`FATAL`). Once the contest exists, run `./restart.sh` instead of waiting for the failed services to recover.

Afterwards, run `./contest.sh` to confirm the ID is correct.

### Permission denied when running a script

The script file doesn't have execute permission. Fix it by running:
```bash
chmod +x <script-name>.sh
```
For example: `chmod +x up.sh`. After this, you can run the script normally.
