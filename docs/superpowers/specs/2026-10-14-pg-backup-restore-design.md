# Design: full-database backup, verify and restore scripts

**Date:** 2026-10-14
**Status:** Scope approved by the user on 2026-10-20 (no `restore.sh`); ready for the plan
**Branch:** `feat/pg-backup` (rebased onto main 14e169ef)

## Problem

The documented backup is `./export.sh` (`cmsDumpExporter`). It is slow, uses
one core, and it is not a full backup: it leaves out the ranking groups, the
participant activity log (`activity_intervals`) and the admin accounts. For a
full copy the docs only say "keep a `pg_dump` too" and "restoring a `pg_dump`
is not covered".

All file content in CMS (statements, testcases, managers, submissions,
executables) is stored as PostgreSQL large objects referenced by
`fsobjects.loid`, so the database is the whole state.

## Decisions taken with the user

- `export.sh` keeps its current scope: moving contests between deployments.
  It gains nothing. Making it faster is a separate later project, measured on
  a real copy of the cms-live database.
- The full backup is a new `backup.sh` built on `pg_dump`, plus
  `verify-backup.sh`. **Restoring is a documented procedure, not a script**
  (decided 2026-10-20, see "Restoring a backup").
- Approach A: `pg_dump` 17+ in directory format with `-j N` and zstd, so
  large objects use several cores.
- **No `restore.sh` (user, 2026-10-20).** It would be the only script that stops
  CMS and renames databases in production; restores are rare and supervised
  by a person, and the manual restore of 2026-10-14 worked. The commands, with
  the lessons of that day, go in the runbook.
- Out of scope for this version: cron, retention, encryption, off-site copy,
  backing up `.env` or the Docker volumes, making `export.sh` faster.

## Facts the design relies on

- Production (cms-live) uses an external PostgreSQL **16.15** on the Docker
  host. Containers reach it at the compose network gateway, user `cmsuser`,
  database `cmsdb`. The `localdb` profile uses `postgres:17-alpine`.
- The `cms` image (ubuntu:noble) ships `postgresql-client` 16, which refuses
  to dump a 17 server.
- `pg_dump` 16 and older write every large object under one TOC entry, so one
  worker dumps all of them and `-j` does not help. `pg_dump` 17+ batches them
  (about 1000 per entry), spreads them across workers, and can dump older
  servers. So the client is always 17+, even against a 16 server. This must be
  confirmed by measurement (see Testing).
- `fsobjects.digest` is the SHA1 of the content (`cmscommon/digest.py`).
- `import.sh` lists everything in `dumps/`, so the new backups do not live
  there.
- On cms-live, `down` recreates the compose network and the gateway can
  change, so the restore uses `compose stop`, never `down`.
- Restoring with `--clean --single-transaction` into the live database is
  rejected: with many large objects one transaction runs out of lock slots,
  and `--clean` leaves objects that are not in the dump.
- Observed in the manual cms-live restore of 2026-10-14 (18 GB, 34876 large
  objects, `pg_dump`/`pg_restore` 17.11 against the 16.15 server):
  - the dump with `-j 16` on 16 vCPU took at most ~83 s; the restore with
    `-j 16` on 6 vCPU took ~6 min;
  - `pg_restore` 17 sends `SET transaction_timeout = 0` on every connection
    and a 16 server rejects it (`unrecognized configuration parameter`):
    once per worker and twice for the leader. The error is harmless, but
    `--exit-on-error` aborts on it, and the final "errors ignored" count only
    includes the leader's errors (2 of the 18 printed);
  - restoring as `postgres` with `--role=<cms user> --no-owner --no-acl`
    produced no other error, and every table and large object ended up owned
    by the CMS user.

## Components

### `docker/_backup_lib.sh` (sourced, no side effects)

Pure helpers shared by the two scripts, unit tested:

- `parse_db_url URL` → sets `PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE`.
  Handles `postgresql+psycopg2://`, `%`-encoded passwords, an optional port
  and a `?query` suffix. Fails clearly when the URL is empty or malformed.
- `db_connection_from_env` → for `CMS_USE_LOCALDB=true`, uses
  `POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB` with host `db`; otherwise
  `parse_db_url "$CMS_DB_URL"`.
- `pick_pg_major SERVER_VERSION_NUM` → `max(17, server major)`.
- `backup_name [label]` → `cms-YYYYmmdd-HHMMSS[-label]`. The label is
  restricted to `[A-Za-z0-9_-]`.
- `compose_network` → the network labelled with the compose project and
  `default`. Fails with "run ./up.sh first" when it does not exist.
- `pg_tool ...` → runs a command in `docker run --rm --init
  --network <compose network> postgres:<M>` with `-e PGHOST -e PGPORT -e PGUSER
  -e PGPASSWORD -e PGDATABASE` (names only, values come from the environment,
  so they never appear in argv or shell history). Mounts the backup directory
  when needed. The container is named `cms-pgtool-$$` so a trap can remove it.
- `manifest_compare A B` → compares two count sections and prints mismatches.

### `backup.sh`

Usage: `sudo ./backup.sh [label] [-j N]`. Default `-j 4`, which is safe
during a live contest.

1. Read the connection (`db_connection_from_env`). Find the compose network.
2. Ask the server for `server_version_num` with `psql` from `postgres:17`.
   Choose `postgres:M`. If the image is missing, pull it before anything else.
   If the pull fails, stop and say so.
3. Check that free space on `backups/` is at least `pg_database_size`.
4. Print a summary (database, size, free space, workers, image) and ask for
   one confirmation.
5. Open a `REPEATABLE READ` transaction and export its snapshot with
   `pg_export_snapshot()`. Keep the session open in the background. In that
   same snapshot, record in `MANIFEST`:
   - exact `count(*)` of every table in `public`, from one generated query, so
     new tables are covered with no hard-coded list;
   - the `pg_largeobject_metadata` count;
   - the date in UTC, the git commit, the fork version, the server version,
     the `pg_dump` version, the database name, encoding, `LC_COLLATE`,
     `LC_CTYPE` and the database-level settings (`pg_db_role_setting`).
6. Run `nice -n 10 pg_dump --snapshot=<id> -Fd -j N --compress=zstd:3
   -f backups/<name>.partial`. zstd is always available in the official
   `postgres:17+` image, so there is no fallback. The tool runs as root with
   `umask 077`. `backups/` is mode 700.
7. Close the snapshot session. Verify:
   - `pg_restore -l` parses the TOC;
   - one `TABLE DATA` entry per table in the manifest;
   - the number of large objects in the TOC equals the manifest count.
8. Write `SHA256SUMS` for every file, computed in parallel. Rename
   `<name>.partial` to `<name>`.
9. Print the path, size, duration, an `rsync -a` line to copy it off the
   server, and a reminder that `.env` is not inside the backup.

A trap on EXIT, INT and TERM removes the tool container, closes the snapshot
session and deletes the `.partial` directory, so Ctrl+C leaves nothing behind.

### `verify-backup.sh`

Usage: `./verify-backup.sh <backup dir>`. Needs only Docker: no `.env`, no
CMS. It is meant for the operator's machine after the `rsync`, or the server
outside contest hours.

1. `sha256sum -c SHA256SUMS`.
2. Start a throwaway `postgres:<pg_dump major from MANIFEST>` container with
   an anonymous volume. A trap removes both, including on Ctrl+C. Using the
   dump's major (17+) rather than the source server's avoids the
   `transaction_timeout` error, so `--exit-on-error` stays safe here.
3. `pg_restore -j <nproc> --no-owner --no-acl --exit-on-error`.
4. Check, and fail on any mismatch:
   - per-table counts and the large object count equal the manifest exactly;
   - every `fsobjects.loid` exists in `pg_largeobject_metadata`;
   - zero rows where `encode(digest(lo_get(loid), 'sha1'), 'hex') <> digest`
     (`pgcrypto` is created in the throwaway database).
   Orphan large objects are reported as information only.
5. Exit 0 when everything passes. Otherwise exit non-zero and name the failed
   check.

### Restoring a backup (a documented procedure, not a script)

It lives in `docs/docker-scripts.md` as a numbered procedure with the exact
commands, to be run by a person who decides to accept the downtime. It
contains:

Preflight, with CMS still running:

- `sha256sum -c SHA256SUMS` and `pg_restore -l` on the backup (or run
  `verify-backup.sh` first).
- The server you restore onto is not older than the source server that made
  the backup (the `server_version` in `MANIFEST`). A server older than the
  dump's `pg_dump` is fine (cms-live: server 16, `pg_dump` 17); it is why
  `--exit-on-error` is not used there.
- The CMS database user can create databases; if not, the exact
  `ALTER ROLE <user> CREATEDB;` to run with `sudo -u postgres psql`.
- Free space for a second copy of the database.

Restore into a new database, without touching the live one:

1. `CREATE DATABASE <db>_restore OWNER <cms user> TEMPLATE template0` with the
   manifest's encoding and locale.
2. `pg_restore -Fd -j N --no-owner --no-acl` **as the CMS user**, so the large
   objects are owned by that user (owned by `postgres`, CMS gets "permission
   denied for large object" on every file). Use
   `PGOPTIONS='-c synchronous_commit=off'`. With a server of major 17 or newer
   add `--exit-on-error`; with an older one do not (the `transaction_timeout`
   fact above): read stderr and treat any `pg_restore: error:` other than the
   `transaction_timeout` one as a failure, and do not trust the "errors
   ignored" count, which leaves out the workers.
3. Check that the table counts and the large object count equal the manifest
   and that no large object has an owner other than the CMS user; run
   `vacuumdb --analyze-only`.

Switch (the only downtime, about 1-3 minutes):

1. `docker compose stop cms` (never `down`: it recreates the network).
2. Terminate the remaining sessions on `<db>`.
3. `ALTER DATABASE <db> RENAME TO <db>_before_restore_<timestamp>`, then
   `ALTER DATABASE <db>_restore RENAME TO <db>`.
4. Re-apply the database-level settings recorded in the manifest.
5. `docker compose up -d --no-recreate --no-build --wait cms`.
6. If CMS does not come up, roll back by renaming the two databases back.

After: `./status.sh`; Workers connected equals configured; **Regenerate** on
each ranking group; check the **Active** flags; and, once satisfied,
`DROP DATABASE <db>_before_restore_<timestamp>`. Not covered: restoring `.env`
and restoring onto an older PostgreSQL major.

### Repo changes

- `.gitignore` and `.dockerignore`: add `backups/`.
- `docker/_cms-test-internal.sh`: add the new pytest file to the list it
  runs.

## Documentation

English sources, with the Spanish `.po` files updated in the same change,
following `docs/locale/es/GLOSSARY.md`. The docs CI checks them.

- `docs/docker-scripts.md`:
  - new sections for `backup.sh` and `verify-backup.sh`, and the section
    "Restoring a backup" with the procedure above;
  - update the script count in the introduction;
  - in `export.sh`, point to `backup.sh` for a full backup;
  - remove "Restoring a `pg_dump` is not covered in this manual".
- `docs/docker-deployment.md`: rows for the new scripts in the table.
- `docs/contest-day.md`:
  - before and after the contest, `backup.sh` is the backup;
  - `export.sh` is only for moving contests;
  - a restore means downtime and is the lead's decision; it points to
    "Restoring a backup".
- `docs/migrating-to-multi-contest.md`: step 1 uses `backup.sh`.

## Testing

1. **Unit.** `docker/test_backup_lib.py` (pytest, calls bash, same style as
   `docker/test_generate_config.py`) covers:
   - URL parsing (psycopg2 scheme, `%` encoding, port, query, empty);
   - the localdb branch;
   - `pick_pg_major`;
   - the label filter and names;
   - `manifest_compare`.

   CI runs it through `_cms-test-internal.sh`.
2. **End to end, local, rootful Docker.** Two setups: the compose `localdb`
   (17), and an external `postgres:16` that mimics cms-live. Both are seeded
   with real CMS data that includes large objects. In each:
   - `backup.sh` → `verify-backup.sh`;
   - then the documented restore procedure, followed by hand once, copying its
     commands as they are written (this tests the documentation): CMS starts
     and the counts match;
   - rolling back with the two `RENAME` commands works.
3. **Speed.** Compare `-j 1` and `-j 8` against the 16 server. This confirms
   that `pg_dump` 17 really spreads large objects across workers. Record the
   numbers in the report.
4. **Failures:**
   - a corrupted blob file → `verify-backup.sh` fails on the SHA1 check;
   - Ctrl+C during the dump → no `.partial` and no tool container are left;
   - a user without CREATEDB → the procedure's preflight shows the
     `ALTER ROLE` to run (followed by hand);
   - CMS not starting after the switch → the rollback commands work.

## Risks

- **No large-object parallelism.** If `pg_dump` 17 did not spread large
  objects across workers against a 16 server, approach A would lose its
  advantage. The speed test detects this before merging.
- **Restoring as a non-superuser.** `pg_restore` may stop on objects that
  only a superuser can create (for example a comment on `plpgsql`). The
  2026-10-14 manual restore ran every statement as the CMS user (`--role`)
  with no such error, so the cms-live dump does not hit this. The end to end
  test with the 16 server still checks it. The fix would be a `-L` list that
  leaves those entries out.
- **Disk.** The restore needs room for two copies of the database until the
  operator drops the old one.
