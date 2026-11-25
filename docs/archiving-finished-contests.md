# Archiving finished contests

Removing a contest does not free disk space, and it is not undone. This page
is the procedure to free space after contests ended: measure, take a verified
backup and keep it cold, remove the contests, then delete the files nobody
uses any more.

Use it only for contests that are over and whose results are published. Do it
outside the contest hours, never on a contest day.

> **Warning:** This procedure needs the version `omi.18` of the fork or newer
> (`version` in `pyproject.toml` of the deployed commit). That release is the
> first where `cmsCleanFiles` keeps the files of the tasks that belong to no
> contest and the sandbox archives of the results. On an older version,
> `cmsCleanFiles` deletes them: do **not** run it there.

> **Warning:** Removing a contest also deletes **its tasks**, with their
> statements, attachments, datasets, testcases and managers, as well as its
> announcements, messages, questions, groups and participations. Users, teams
> and the other contests stay. To keep a task, remove it from the contest
> first (Admin Web Server, the contest, **Tasks**): it becomes a task without
> a contest and `cmsCleanFiles` keeps its files. Its submissions are still
> deleted with the participations of the contest. Do this **before** step 0,
> so that part C of the measurement counts its files as kept.

Run the commands in the project root. `PROJECT` is `CMS_PROJECT_NAME` from
`.env` (`cms-prod` if it is unset):

```bash
PROJECT=cms-prod
```

## How the space is used

- Every file of CMS (statements, testcases, submission sources, executables,
  user test files, and the archives of the sandboxes) is a PostgreSQL large
  object, in the database.
- `cmsRemoveContest` deletes the rows of the contest: its tasks (with their
  datasets and testcases), submissions, results, evaluations, participations
  and the participant activity log. It does not delete any file: the large
  objects stay, nobody uses them.
- `cmsCleanFiles` deletes the files that no row uses. A file shared with
  another contest stays.
- The space returns to the operating system only after `VACUUM FULL` (step 4).
  Before that, the logical size and the size of a `pg_dump` backup already
  go down, which is what makes backups, restores and `verify-backup.sh`
  faster.

## What this procedure keeps

- The ranking group of the contest, if you archive it first (step 2): the
  public ranking keeps the final scoreboard.
- Everything else, in the cold backup of step 1: the contest with its tasks,
  its participations, the submissions, the ranking groups, the participant
  activity log and the admin accounts. The users and the teams are not lost:
  they stay in the live database, and the backup has them too.

## 0. Measure first

Find out what weighs before you remove anything. The script only reads
(it creates two temporary tables of its own session and nothing else). It is
in the repository as `docs/sql/archive-measure.sql`:

```{literalinclude} sql/archive-measure.sql
:language: text
```

The script has two parts:

- **Part A** (`A1` to `A3`) needs only the database role of CMS.
- **Parts B and C** read the large objects, so they need a superuser, or a
  role that is a member of `pg_read_all_data` (PostgreSQL 14 or newer). With
  the role of CMS, the script prints part A and then stops with `permission
  denied for table pg_largeobject`.

> **Warning:** The script is not light. Part A3 reads `evaluations` and
> `submission_results` once per contest, and parts B and C read the whole
> `pg_largeobject` twice (the size table and B2). On a large database this
> takes minutes and loads the disk. Run all of it off-peak, never on a contest
> day.

Run it from the project root. To run only part A, with the role of CMS (the
`cms` container reads `CMS_DB_URL`, without `+psycopg2`):

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env \
    -p "$PROJECT" exec -T cms bash -c 'psql "${CMS_DB_URL/+psycopg2/}" -X -f -' \
    < docs/sql/archive-measure.sql
```

To run all of it, use a superuser. If Docker manages your database
(`CMS_USE_LOCALDB=true`), `POSTGRES_USER` is one:

```bash
source .env
docker compose -f docker/docker-compose.prod.yml --env-file .env --profile localdb \
    -p "$PROJECT" exec -T db psql -U "$POSTGRES_USER" "$POSTGRES_DB" -X -f - \
    < docs/sql/archive-measure.sql
```

Otherwise run `psql "<url of a superuser>" -X -f docs/sql/archive-measure.sql`
from a machine that reaches the database.

It writes nothing to the database. What to look at:

- **A2** (`large_objects` against `database_total`): how much of the database
  is files. If it is small, this procedure will not free much.
- **B** (by kind): which kind of file weighs most.
- **C** (per contest): `freed_if_removed` is what removing that contest lets
  `cmsCleanFiles` delete: the files that no other contest and no task without
  a contest uses. Choose the contests to remove from this column.
- **C2**: how much of it is executables and user test outputs.
- **B2**, first line: files that no row uses now. This is what
  `cmsCleanFiles` would delete today, before you remove anything.

The numbers say how big your contests and your problem are. Keep them
private: do not paste the output in a public issue.

## 1. Take a full backup and keep it cold

Nothing in the next steps can be undone from the database. The backup is the
only way back.

1. Download the participant activity log of each contest you will remove (see
   [Participant activity](participant-activity.md), "After the contest"). The
   backup has it too, but the CSV is easier to read.
2. Make the backup, with a label, and check it. Both scripts are in the
   [Docker Scripts Guide](docker-scripts.md) (`backup.sh` and
   `verify-backup.sh`):
   ```bash
   sudo ./backup.sh before-archive
   sudo ./verify-backup.sh backups/<backup dir>
   ```
   Go on only if `verify-backup.sh` ends with `OK`.
3. Copy it off the server with the `rsync` line that `backup.sh` prints, and
   keep it cold: store it, do not use it.

> **Warning:** The backup has the password hashes of every user and every
> admin. Store it encrypted and off the server. For example, on your machine,
> after the copy and the check, encrypt it and delete the plain copy on your
> machine (the plain copy on the server stays, readable only by root, until
> you delete it with `sudo`):
>
> ```bash
> tar -cf - <backup dir> | gpg --symmetric --cipher-algo AES256 -o <backup dir>.tar.gpg
> ```

One backup covers every contest you remove in the same maintenance window.
Keep `.env` somewhere safe too: it is not in the backup.

## 2. Archive the ranking group first

Skip this step if the contest does not belong to a ranking group.

If it does, archive the group in the Admin Web Server **before** you remove
the contest, so that the public ranking keeps it (see "Archiving a ranking
group" in [Running several contests at once](multi-contest.md)):

1. Open **Ranking groups** and the group.
2. Check that every contest of the group is over: an archived group stops
   receiving anything. If the group is hidden or frozen now, archiving shows
   its ranking in full to the public (the page asks you to confirm).
3. Press **Archivar ahora** (or wait for the **Archive at** time).

An archived group gets nothing more from ProxyService, so removing its
contests later does not change its public ranking.

> **Warning:** If you remove the contest from a group that is not archived,
> a later **Regenerate** of that group empties the public ranking and sends it
> again from the database, and the contest is no longer on it. Do not press
> **Regenerate** on a group that is not archived after you removed a contest
> of it (**Regenerate** does nothing on an archived group).

## 3. Remove the contest and delete the files

Do this when no contest is running, nobody submits, and nobody uploads files
in the Admin Web Server (for example, a task or a dataset that is being
imported). A file that was uploaded but is not yet saved in a row looks
unused, and `cmsCleanFiles` deletes it. In **Overview** of the Admin Web
Server, check that the queue of the Evaluation Service is empty: an
evaluation in progress can still add files.

If the system serves a single contest (`CMS_CONTEST_ID=<id>` in `.env`), do
not remove the contest that `CMS_CONTEST_ID` names.

1. Remove each contest. The command shows the name of the contest and asks
   you to confirm with `y`, so run it in an interactive terminal (do not add
   `-T`, which takes away the terminal that the prompt needs). It deletes the
   contest and all its related data, including its tasks, submissions and the
   activity log:
   ```bash
   docker compose -f docker/docker-compose.prod.yml --env-file .env \
       -p "$PROJECT" exec cms cmsRemoveContest -c <contest id>
   ```
   It frees nothing by itself: the rows are gone, the files are still in the
   database.
2. Do a dry run of `cmsCleanFiles`. `-n` (`--dry-run`) deletes nothing and
   logs how many files are in the store, how many are orphans and how many
   bytes the orphans take:
   ```bash
   docker compose -f docker/docker-compose.prod.yml --env-file .env \
       -p "$PROJECT" exec cms cmsCleanFiles -n
   ```
   The orphan bytes must equal the sum of `freed_if_removed` (part C) of the
   contests you removed, plus the first line of B2 (both measured before step
   3). A file that only the removed contests share is in no
   `freed_if_removed`, so the dry run can be a little larger when you remove
   several contests that share files. If it is larger by more than you can
   explain, **stop**: do not run step 3 and look for what the extra files
   are.
3. Delete them (only on `omi.18` or newer, see the warning at the top):
   ```bash
   docker compose -f docker/docker-compose.prod.yml --env-file .env \
       -p "$PROJECT" exec cms cmsCleanFiles
   ```
   It scans the whole database, not one contest, and it deletes every file
   that no row uses: the files of the removed contests, and also any other
   orphan that was already there. A file used by a remaining contest, a task
   without a contest, or a result (the archives of the sandboxes) stays.

> **Warning:** Do not use `cmsCleanFiles -t` (`--tombstone`) here. It replaces
> the executables of **every** contest, the contests that are running too,
> and then deletes the real files. The executables of a running contest are
> compiled again at the next evaluation, which loads the Workers.

Run the dry run again: it must report no orphans.

## 4. Return the disk space (only if you need it)

Deleting the files makes backups and restores smaller, but the table
`pg_largeobject` keeps its size on disk. A plain `VACUUM` does not return it.
`VACUUM FULL` does, but it locks the table that every file read needs and
writes a full new copy of it.

Do it only if the **disk** is the problem, not the size of the backups, and
only with the system stopped. Check first that the disk has free space for a
copy of `pg_largeobject` plus about as much for the WAL that the copy writes
(A1 and A2 of the measurement show its size). Never
use `down`: it recreates the Docker network.

`VACUUM FULL` of `pg_largeobject` needs a superuser. The commands below are
for a database that Docker manages (`CMS_USE_LOCALDB=true`), where
`POSTGRES_USER` is one; otherwise run the two `psql` commands as a superuser
from a machine that reaches the database.

```bash
source .env
docker compose -f docker/docker-compose.prod.yml --env-file .env \
    -p "$PROJECT" stop cms
docker compose -f docker/docker-compose.prod.yml --env-file .env --profile localdb \
    -p "$PROJECT" exec -T db psql -U "$POSTGRES_USER" "$POSTGRES_DB" -X -c "VACUUM FULL pg_largeobject"
docker compose -f docker/docker-compose.prod.yml --env-file .env --profile localdb \
    -p "$PROJECT" exec -T db psql -U "$POSTGRES_USER" "$POSTGRES_DB" -X -At -c "SELECT pg_size_pretty(pg_total_relation_size('pg_largeobject'))"
docker compose -f docker/docker-compose.prod.yml --env-file .env \
    -p "$PROJECT" up -d --no-recreate --no-build --wait cms
```

Its duration was not measured on a large database: start it when you can
leave the system stopped for a while.

## 5. Restoring an archived contest

Restore the cold backup into a **scratch** database, and look at the contest
there. Do not import a single-contest dump into the live database (see
"Why not `cmsDumpExporter`").

1. Copy the backup back to the server (`backups/` belongs to root, so copy
   it with `sudo`), decrypt it if it is encrypted, and run
   `sudo ./verify-backup.sh` on it again.
2. Follow "Preflight" and "Restore into a new database" of "Restoring a
   backup" in the [Docker Scripts Guide](docker-scripts.md), and **stop before
   "Switch"**. That creates `${DB}_restore` next to the live database and
   leaves the live one as it is. The server must not be older than the one
   that made the backup.
3. Read it with `psql` on `${DB}_restore` (for example, with the queries of
   the measurement). If you need the Admin Web Server, run **only** it, on
   your own machine and not in the live `cms` container, with its own
   `cms.toml`: a copy of `config/cms.sample.toml` where the database `url`
   points to the scratch database, every address of `[services]` is a port
   where nothing listens (for example `["127.0.0.1", 1]`) and `rankings = []`
   in `[proxy_service]`. Do not copy the configuration of the live system:
   an Admin Web Server connects to the Evaluation Service, the Scoring
   Service and ProxyService at the addresses of its configuration, and an
   action such as re-evaluate or **Regenerate** would reach the live services
   with the ids of the scratch database. In Docker, use a separate project
   (another `-p` name, so another network and other volumes), a copy of
   `.env` with other ports and without the address of the live ranking
   (`CMS_RWS_HOST`), and start only `cmsAdminWebServer`, never the whole `cms`
   container: it starts every service.
4. When you finish, drop the scratch database:
   ```bash
   pgc psql -X -d postgres -c "DROP DATABASE ${DB}_restore"
   ```
   (`pgc` and `DB` are defined in "Restoring a backup".)

The scratch database has the contest as it was at the backup: the ranking
group link and the **Active** flag too. Never run a ProxyService on it: it
would send the contest to the public ranking.

## Why not `cmsDumpExporter` as the archive

It looks like an archive per contest, but today it is not one:

- **It exports more than the contest.** With `-c`, it also exports the other
  contests of the users that took part in the contest. A dump of one contest
  holds all the contests that share its users.
- **It did not import back.** In an experiment on a scratch database with a
  small synthetic contest, `cmsDumpImporter` of that dump into the same
  database failed with a duplicate contest name (and a user that
  already exists fails the same way). It rolled back and nothing changed, but
  the dump is no way back.
- **It leaves out data.** It has neither the ranking groups nor the
  participant activity log (see `export.sh` in the
  [Docker Scripts Guide](docker-scripts.md)), and an imported contest is
  inactive.
- The export jobs of the Admin Web Server run the same exporter. They are for
  moving a contest to another system: the file is deleted an hour later. They
  are not a place to keep an archive, and they do not free space.

## What this procedure does not do

- It does not archive one contest alone, with a way to put just that contest
  back into the live database. A restore is a scratch database.
- There is no per-contest tombstone: dropping only the executables of a
  contest and keeping everything else. `cmsCleanFiles -t` works on every
  contest.
- There is no exporter limited to the contest and its users.
- Nothing archives or removes contests by itself.
- It does not keep a static copy of a public ranking. An archived group keeps
  the data that the ranking already has.

The per-contest tombstone and the scoped exporter are not built until the
measurement shows they are needed.
