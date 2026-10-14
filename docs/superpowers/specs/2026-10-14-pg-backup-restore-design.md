# Design: full-database backup, verify and restore scripts

**Date:** 2026-10-14
**Status:** Draft, awaiting user review
**Branch:** `feat/pg-backup` (from beta 6b777b79)

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
- The full backup is a new `backup.sh` built on `pg_dump`, plus `restore.sh`
  and `verify-backup.sh`.
- Approach A: `pg_dump` 17+ in directory format with `-j N` and zstd, so
  large objects use several cores.
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

## Components

### `docker/_backup_lib.sh` (sourced, no side effects)

Pure helpers shared by the three scripts, unit tested:

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
   -f backups/<name>.partial`. Fall back to `gzip:6` only if zstd is not
   supported. The tool runs as root with `umask 077`. `backups/` is mode 700.
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
2. Start a throwaway `postgres:<major from MANIFEST>` container with an
   anonymous volume. A trap removes both, including on Ctrl+C.
3. `pg_restore -j <nproc> --no-owner --no-acl --exit-on-error`.
4. Check, and fail on any mismatch:
   - per-table counts and the large object count equal the manifest exactly;
   - every `fsobjects.loid` exists in `pg_largeobject_metadata`;
   - zero rows where `encode(digest(lo_get(loid), 'sha1'), 'hex') <> digest`
     (`pgcrypto` is created in the throwaway database).
   Orphan large objects are reported as information only.
5. Exit 0 when everything passes. Otherwise exit non-zero and name the failed
   check.

### `restore.sh`

Usage: `sudo ./restore.sh [backup dir]`. Without an argument it lists the
complete (not `.partial`) directories in `backups/`.

Preflight, with CMS still running:

- `sha256sum -c` and `pg_restore -l`.
- The server major is not lower than the dump's `pg_dump` major.
- The tool image is present.
- The CMS database user can create databases (`rolcreatedb` or superuser). If
  not, print the exact `ALTER ROLE <user> CREATEDB;` to run with
  `sudo -u postgres psql` and stop. With `localdb`, `POSTGRES_USER` is a
  superuser.
- Free space for a second copy of the database.
- Print the manifest counts next to the current database counts.

The operator confirms by typing the database name.

Restore:

1. Drop a leftover `<db>_restore` if one exists. Create it with
   `OWNER <cms user> TEMPLATE template0` and the manifest's encoding and
   locale.
2. `pg_restore -Fd -j N --no-owner --no-acl --exit-on-error` **as the CMS
   user**, so the large objects are owned by that user. If they were owned by
   `postgres`, CMS would get "permission denied for large object" on every
   file. Use `PGOPTIONS='-c synchronous_commit=off'`. On failure, drop
   `<db>_restore` and stop. The live database is untouched.
3. Check that the counts equal the manifest exactly, and that no large object
   has an owner other than the CMS user. Run `vacuumdb --analyze-only`.
4. Switch, the only downtime (about 1-3 minutes):
   1. `compose stop cms`.
   2. Terminate any remaining sessions on `<db>`.
   3. `ALTER DATABASE <db> RENAME TO <db>_before_restore_<timestamp>`.
   4. `ALTER DATABASE <db>_restore RENAME TO <db>`.
   5. Re-apply the database-level settings from the manifest.
   6. `compose up -d --no-recreate --no-build --wait cms`.
5. If CMS does not come up, leave everything as it is and print the two
   `RENAME` commands that roll back.
6. Print the next steps:
   - `./status.sh`;
   - Workers connected equals configured;
   - **Regenerate** on each ranking group;
   - check the **Active** flags;
   - the `DROP DATABASE <db>_before_restore_<timestamp>` to run once
     satisfied.

Not automated: dropping the old database, restoring `.env`, regenerating the
scoreboards, restoring onto an older PostgreSQL major.

### Repo changes

- `.gitignore` and `.dockerignore`: add `backups/`.
- `docker/_cms-test-internal.sh`: add the new pytest file to the list it
  runs.

## Documentation

English sources, with the Spanish `.po` files updated in the same change,
following `docs/locale/es/GLOSSARY.md`. The docs CI checks them.

- `docs/docker-scripts.md`:
  - new sections for `backup.sh`, `verify-backup.sh` and `restore.sh`;
  - update the script count in the introduction;
  - in `export.sh`, point to `backup.sh` for a full backup;
  - remove "Restoring a `pg_dump` is not covered in this manual".
- `docs/docker-deployment.md`: rows for the new scripts in the table.
- `docs/contest-day.md`:
  - before and after the contest, `backup.sh` is the backup;
  - `export.sh` is only for moving contests;
  - a restore means downtime and is the lead's decision.
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
   - `backup.sh` → `verify-backup.sh` → `restore.sh`;
   - CMS starts and the counts match;
   - rolling back with the printed `RENAME` commands works.
3. **Speed.** Compare `-j 1` and `-j 8` against the 16 server. This confirms
   that `pg_dump` 17 really spreads large objects across workers. Record the
   numbers in the report.
4. **Failures:**
   - a corrupted blob file → `verify-backup.sh` fails on the SHA1 check;
   - Ctrl+C during the dump → no `.partial` and no tool container are left;
   - a user without CREATEDB → `restore.sh` prints the `ALTER ROLE` and
     changes nothing;
   - CMS not starting after the switch → the rollback commands are printed
     and work.

## Risks

- **No large-object parallelism.** If `pg_dump` 17 did not spread large
  objects across workers against a 16 server, approach A would lose its
  advantage. The speed test detects this before merging.
- **Restoring as a non-superuser.** `pg_restore` may stop on objects that
  only a superuser can create (for example a comment on `plpgsql`). The end
  to end test with the 16 server catches it. The fix is a `-L` list that
  leaves those entries out.
- **Disk.** The restore needs room for two copies of the database until the
  operator drops the old one.
