# backup.sh, verify-backup.sh and the restore procedure: end-to-end test

**Date:** 2026-10-20 and 2026-10-21 (the machine rebooted in between; the
Docker volumes survived and the runs went on from where they stopped).
**Issue:** #60
**Code under test:** branch `feat/pg-backup` from `80bbcd29`, plus the fixes
listed below. Everything ran against real PostgreSQL servers in throwaway
containers on the local rootful Docker. Nothing touched cms-live.

## Setup

**Host.** Docker Engine 29.9 on Bazzite (Fedora 44), 16 CPUs, 41 GB RAM. The
worktree is on btrfs over LUKS. The host has SELinux enforcing, but the engine
runs without the `selinux` security option, so containers run as `spc_t`.

**Root.** `backup.sh` needs root and there is no passwordless `sudo` here. The
scripts therefore ran as root in a helper container, `cms-e2e-runner`: Ubuntu
24.04 like cms-live (bash 5.2, GNU coreutils and findutils, mawk), with the
docker CLI and compose plugin copied from `docker:cli`, plus git, procps and
zstd. It mounts `/var/run/docker.sock`, and the worktree and the repository's
`.git` at their host paths, so the bind-mount sources that the scripts pass to
`docker run` are real host paths:

```bash
docker run --rm -i --name cms-e2e-runner-<x> -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$WT:$WT" -v "$GITDIR:$GITDIR:ro" -w "$WT" cms-e2e-runner bash -c './backup.sh <label>'
```

**Setup A, the `localdb` shape.** This is the real compose file with the real
`db` service, as `./up.sh` starts it:
`docker compose -f docker/docker-compose.prod.yml --env-file .env -p cms-e2e-a
--profile localdb up -d --wait db`. That is `postgres:17-alpine` 17.11, whose
user `cms` is a superuser. The `.env` has `CMS_USE_LOCALDB=true` and the
`POSTGRES_*` values.

**Setup B, like cms-live.** It is a plain `postgres:16` (16.15, the same minor
as cms-live) on the network `cms-e2e-b_default`. That network was created with
the labels `com.docker.compose.project=cms-e2e-b` and
`com.docker.compose.network=default`. The role `cmsuser` has LOGIN and no
CREATEDB, and owns `cmsdb`. Its password is `Pa@ss:w/rd%7Ez9`, and the `.env`
has `CMS_DB_URL=postgresql+psycopg2://cmsuser:Pa%40ss%3Aw%2Frd%257Ez9@172.21.0.2:5432/cmsdb`.
SQLAlchemy and `parse_db_url` both connected with it. The database also has
two settings: `idle_in_transaction_session_timeout=5s` on the database, and
`statement_timeout=30s` for `cmsuser` in it.

**Data.** `cmsInitDB` ran from the worktree venv, with a `CMS_CONFIG` that
points at the container's IP, and created 29 tables. A seed script then wrote
the large objects through `cms.db.filecacher.DBBackend`, the way CMS writes
them, so `fsobjects.digest` is `cmscommon.digest.bytes_digest` of the
content. The content is hex text, which compresses to about 53 %, and the
objects are written in shuffled order, so big and small ones share pg_dump
batches. The script also wrote, through the CMS models: a contest, 4 tasks
and their datasets, testcases, users and participations, submissions with
their files, messages, questions, an announcement and an admin.

| | A | B |
|---|---|---|
| Large objects | 3000 (2500 sources of 0.5-6 KB, 500 testcase files of 100 KB on average) | 8000 (6000 sources, 2000 testcase files of 750 KB on average) |
| Content of the large objects | ~60 MB | 1515 MB |
| `pg_database_size` | 90 863 283 B (87M) | 2 141 297 687 B (2.0G) |
| Rows | 30 users, 2500 submissions, 250 testcases | 100 users, 6000 submissions, 1000 testcases |

## Results

### backup.sh

| | A (`-j 4`) | B (`-j 4`) |
|---|---|---|
| Duration (the script's own) | 31 s the first time, 9 s on a traced re-run | 37 s |
| Backup size | 37M | 803M |
| `blobs_<id>.toc` files | 3, of 1000 lines | 8, of 1000 lines |

- The layout is as the script expects. Each large object is a line `OID
  blob_OID.dat` of a `blobs_<id>.toc` file, in batches of 1000. The TOC check
  of step 7 passes on a good dump.
- The TOC check also rejects real broken dumps. A `docker` shim in the runner
  broke the dump on purpose. With `--exclude-table-data=public.users` added
  to `pg_dump`, it printed `< users` and `ERROR: the TABLE DATA entries of the
  dump (>) differ from the tables of the manifest (<)`. With one line removed
  from a real `blobs_3937.toc`, it printed `ERROR: the dump holds 2999 large
  objects, the manifest 3000`. Both exited 1, with no `.partial` and no
  container left.
- The snapshot session (`docker run -i`, psql in a coprocess) works:
  - psql answers each query as it comes;
  - the generated count query with `\gexec` and `:'VERSION_NAME'` give the
    MANIFEST;
  - closing the coprocess's input ends psql, whose container is gone about
    0.8 s later.
- On B, the snapshot session outlived the database's 5 s
  `idle_in_transaction_session_timeout` during a dump of more than 30 s, so
  the `SET ... = 0` lines work. `pg_dump` 17 against the 16 server printed no
  `transaction_timeout` error.
- MANIFEST:
  - `pg_dump_version` is psql's `VERSION_NAME`, `17.11 (Debian
    17.11-1.pgdg13+2)`;
  - `git_commit` resolves as root in a repository of uid 1000, thanks to
    `safe.directory`;
  - on B, the two settings are recorded as `database_setting	*
    idle_in_transaction_session_timeout=5s` and `database_setting	cmsuser
    statement_timeout=30s`.
- After `chmod -R go=`, every file of a backup is 0600 root and every
  directory 0700 root, `backups/` included.
- The bind mount works on this host as `-v`, with `:z`, with `:ro`, with
  `:ro,z` and as `--mount ...,readonly`. The engine applies no SELinux
  labels, so this does not show whether an SELinux-enforcing engine needs
  `:z` (cms-live is Ubuntu, with AppArmor).
- **Free space, real `df`** (A, a tmpfs on `backups/` in the runner):
  - With 60m, it printed `ERROR: not enough free space in .../backups: 60M
    free, the database takes 87M` and exited 1. Nothing was written, and no
    dump or session container started.
  - With 88m (92 274 688 B, more than 90 863 283 B), it passed to the
    summary (`Free space: 88M`). The answer `n` gave `Cancelled.` and
    nothing was written.
  - Both sides of the comparison are in bytes.
- The label filter refuses `cc0.2`, before anything runs. My first Ctrl+C
  harness used that label by mistake.

### Speed: `-j 1` against `-j 8` (B, a 16 server, `pg_dump` 17.11)

| Output | `-j 1` | `-j 4` | `-j 8` |
|---|---|---|---|
| `backup.sh`, dump phase, to `backups/` | 34.6 s, 31.2 s | 37 s total | 37.5 s, 33.3 s |
| `pg_dump` alone, to a tmpfs in the dump container | 23.1, 24.6, 24.9 s | 10.4, 9.3 s | 6.9, 8.9, 7.9 s |
| `pg_dump` alone, to `backups/`, with or without `--no-sync` | 31.8 / 40.4 s | | 40.4 / 38.2 s |

`pg_dump` 17 does spread the large objects across the workers against a 16
server: with the disk out of the way, `-j 8` is about 3 times faster than
`-j 1`. With 8 batches, `-j 8` gains little over `-j 4`. On this host the
backup itself shows **no gain**. The disk of `backups/` is the bottleneck: it
wrote 30.6 MB/s in `dd ... conv=fsync`, and needed 22 s for 8000 files of
100 KB, while the IO pressure was high after the reboot (`full avg60` about
30 %). So the spec's first risk does not hold for `pg_dump`, but the real
gain on cms-live depends on its disk. The 2026-10-14 dump there took about
83 s for 18 GB with `-j 16`, which is much faster than this disk.

### verify-backup.sh

- A passes in 25 s and B in 5 min 2 s, the latter a 16 dump restored into a
  `postgres:17` container.
- This exercises, all on the real thing:
  - `docker exec -u root` reading the 0600 root files through the `:ro`
    mount;
  - `-c fsync=off` as image arguments;
  - the `pg_isready -h 127.0.0.1` wait;
  - the `query_to_xml` count;
  - the `lo_get`/`digest` query.
- A corrupted byte in a blob file gives `blob_17044.dat.zst: FAILED` and
  `ERROR: the checksums do not match ...`.
- A blob whose content was changed, re-compressed with zstd and given a new
  `SHA256SUMS` line restores fine. The SHA1 check then gives `FAILED: SHA1 of
  the large objects: 1 rows whose content does not match fsobjects.digest`,
  and the script exits 1.
- Run as a normal user on the server, it said `<dir> is not a directory`:
  `backups/` is root's with mode 700. Fixed, see below.

### The restore procedure, followed as written

The ```` ```bash ```` blocks of "Restoring a backup" were extracted from
`docs/docker-scripts.md` by a script and run verbatim, in order, in one bash
shell of the runner. The only edit was the `BACKUP=` line, which the doc says
to change. My own checks are separate commands around the blocks. Some
commands could not be run as written:

- `docker compose stop cms` and `up -d --no-recreate --no-build --wait cms`
  were no-ops, because the CMS image could not be built: a no-cache build
  takes 55 minutes.
- Instead, a fake CMS session (`psql -c 'SELECT pg_sleep(600)'` as the CMS
  user) stayed open on the live database across switch step 1.
- On B, `sudo -u postgres psql` (preflight steps 3 and 4) became `docker exec
  cms-e2e-b-db psql -U postgres`, because the server is a container.
- The live database got an extra `users` row after the backup, so the swap
  can be seen.

| Step | A (17, superuser) | B (16, `cmsuser`) |
|---|---|---|
| Variables | `cms-e2e-a_default cmsdb cms db:5432 170011 postgres:17 ...` | `cms-e2e-b_default cmsdb cmsuser 172.21.0.2:5432 160015 postgres:17 ...` |
| Preflight 1, 2 | `checksums OK`, `table of contents OK`, MANIFEST lines, `server major` | same |
| Preflight 3 (CREATEDB) | `t` | `f`, then `ALTER ROLE` printed, then `t` |
| Preflight 4 | `87 MB` and `df` through `compose exec -T db` | `2042 MB` and `df` of the data directory |
| Restore 1 | `CREATE DATABASE` | same |
| Restore 2 | `pg_restore exit status: 0`, error file empty, 2 s | `pg_restore exit status: 1`, 167 s |
| Restore 2, errors | none | 6 lines `pg_restore: error: could not execute query: ERROR:  unrecognized configuration parameter "transaction_timeout"`, each followed by `Command was: SET transaction_timeout = 0;` (4 workers + 2 for the leader), then `errors ignored on restore: 2`. The `grep` prints nothing, so there is no other error as a non-superuser. |
| Restore 3 | `counts match`, `0` | `counts match`, `0`, all 29 tables owned by `cmsuser` |
| `vacuumdb` | one progress line | one progress line and 11 `WARNING:  permission denied to analyze "pg_..."` lines (fixed, see below) |
| Switch 2 | `t`: the fake CMS session ended | same |
| Switch 3 | `ALTER DATABASE` twice. The live name holds the copy, without the extra row; the old name keeps it | same |
| Switch 4 | nothing | the two `database_setting` lines. `cmsuser` applied both with the documented `ALTER DATABASE ... SET` and `ALTER ROLE cmsuser IN DATABASE ... SET`, and `SHOW` gives `5s` and `30s` |
| Rollback (switch 6) | `ALTER DATABASE` twice. The extra row is back, and the copy is `cmsdb_restore` | same |
| After | `DROP DATABASE cmsdb_restore` | switch again, list `cmsdb_before_restore_...`, `DROP DATABASE` |
| Total | 42 s | 249 s |

`PGOPTIONS='-c synchronous_commit=off' pgc ...` reaches the client: `SHOW
synchronous_commit` printed `off`. With the image's default `pg_hba.conf`,
the CMS user can connect to the `postgres` maintenance database.

After the fixes, a second run on B of the variables block and of restore
steps 1-3, with the blocks extracted again from the edited doc, printed the
same as above. The `vacuumdb` line printed nothing, and the restore took
184 s.

### Failures and Ctrl+C

- A Ctrl+C was simulated as a terminal sends it: SIGINT to the whole process
  group of `backup.sh` (`setsid`, then `kill -INT -- -PGID`). The script was
  started with SIGINT reset to its default, as a terminal job has it. Without
  that reset, a command started with `&` from a script inherits SIGINT
  ignored, and the INT trap cannot run.
- Ctrl+C 8 s, 1 s and 0.2 s into the dump of B:
  - exit 130, 0.2 to 0.7 s after the signal; one run took 25 s, during the
    IO pressure after the reboot, and was not reproduced;
  - `pg_dump: terminated by user`;
  - no `.partial`, no `cms-pgtool-*` container, and no session left on
    `cmsdb` in `pg_stat_activity` (before the signal there were six `idle in
    transaction` sessions: the snapshot session and the dump's).
- Ctrl+C right at the start of the dump, while docker creates its container
  (the "M4 wait" risk): the cleanup did not wait for a whole dump. The script
  exited in 0.2-0.4 s, but in 1 run of 3 the container `cms-pgtool-<pid>`
  then appeared in the `Created` state, and stayed (fixed, see below).
- The same with a bare `docker run --rm`, interrupted while the daemon
  creates the container: the client exits 130, and the daemon still creates
  the container 0.03-0.63 s later, in 12 of 15 tries. `--rm` never removes a
  container that never ran.
- Each interrupted backup also left up to two empty anonymous volumes. The
  `postgres` image declares one, `--rm` removes it only when the container
  ends by itself, and the cleanup used `docker rm -f` without `-v`. The
  volume count rose by 15 over the backup.sh Ctrl+C runs (fixed). Five more
  real Ctrl+C runs after the fix, at 1 s and 0 s, on a 435 MB database in
  setup A, left the count unchanged, with no container, no `.partial` and
  no session.
- `verify-backup.sh` with Ctrl+C 0.1-0.3 s after "Starting a throwaway ...":
  5 runs of 6 left a `Created` `cms-verify-<pid>` container with its
  anonymous volume (fixed).
- A user without CREATEDB gets the `ALTER ROLE` hint at preflight step 3, as
  shown above.

## Fixes

| Commit | What and why |
|---|---|
| `6319838a` `fix(docker): remove a tool container the daemon creates after a Ctrl+C` | On an exit by a signal (status > 128), the cleanup of both scripts waits 2 s before its `docker rm`, so that a container the daemon finishes creating after the client exits is removed too. Both fake dockers gained a late create, and each test file gained a test (RED before the fix). Real re-test: 6 of 6 `verify-backup.sh` runs and 4 of 4 `backup.sh` runs at 0 s left nothing, and exited 2.1-2.2 s after the signal. Side effect: a dump container created late is removed after those 2 s, so the cleanup no longer waits for a whole dump. |
| `1de10579` `fix(docker): tell a user without sudo why verify-backup.sh sees no directory` | The message is now `<dir> is not a directory you can open: check the path, or run it with sudo`, with a test. |
| `1cfc4eee` `docs(docker): make the vacuumdb step of the restore print nothing` | `vacuumdb -q --analyze-only -n public` prints nothing, as the doc says, and still analyzes the 29 tables. The code blocks are not in the translation catalog, so no `.po` changed. |
| `d2a83875` `docs(docker): say what to do when pg_hba.conf stops the restore` | Found by an extra check. `.env.example` suggests a `pg_hba.conf` line that lets the CMS user reach only its own database. With that line on B, `backup.sh` worked, but the procedure failed at its first command (`no pg_hba.conf entry for host "172.21.0.3", user "cmsuser", database "postgres"`), and every later `pgc` failed with `invalid reference format`. With `host cmsdb,postgres,cmsdb_restore cmsuser ...` the procedure ran. The doc now says so, with its `.po` entry. |
| `1e5e25a2` `docs(docker): say what backup.sh does about free disk space` | The user decided on no margin. The doc now says that a backup is usually a fraction of the database, and how to watch the disk with `./status.sh` and `df -h backups`, with its `.po` entry. |
| `db202c0e` `fix(docker): remove the volumes of the tool containers that backup.sh stops` | The cleanup uses `docker rm -f -v`, like verify-backup.sh, so a stopped snapshot or dump container no longer leaves its anonymous volume. The shared test assertion now requires `-v` (6 tests were RED before the fix). |

After each fix, the five docker test files passed (176 tests at the end),
`shellcheck -x` and `bash -n` printed nothing, and the docs check gave
`docs check rc=0`.

## Not tested here

- **The real cms-live:**
  - its disk speed and the real `-j` gain;
  - its `pg_hba.conf`: whether the CMS user can reach `postgres` and
    `cmsdb_restore`;
  - its PostgreSQL at the Docker network gateway (here the server was a
    container on the network);
  - its 34876 large objects and 18 GB.
- `sudo`: the scripts ran as root in a container, so `SUDO_USER` was unset
  and the `rsync` line printed `root@<container id>`. `rsync` itself was not
  run.
- The two compose commands of the switch (`stop cms`, `up -d --no-recreate
  --no-build --wait cms`), and CMS running on a restored database. That
  needs the CMS image, which was not built.
- A Docker engine with SELinux labels enabled (`:z`).

## To try once on the server

1. Outside contest hours, run `sudo ./backup.sh e2e-check`. Check the
   `Duration`, and the size against `df -h backups`.
2. Run `sudo ./verify-backup.sh backups/cms-...-e2e-check`. It needs about
   one copy of the database in Docker's storage.
3. Run the variables block of "Restoring a backup". If it prints `no
   pg_hba.conf entry`, follow the new paragraph. Then run preflight steps 1-4
   only. They change nothing, except the `ALTER ROLE ... CREATEDB` if step 3
   prints `f`.
4. Optionally, time `-j 4` against `-j 8` once, to choose the default for a
   contest day.
5. Delete the test backup: `sudo rm -rf backups/cms-...-e2e-check`.
