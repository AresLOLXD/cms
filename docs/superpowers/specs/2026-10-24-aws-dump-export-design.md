# Contest dump download from AWS (issue #34) — Design

**Date:** 2026-10-24
**Status:** Decisions taken by read-only specialist panels (python-pro,
postgres-pro, backend-developer); approved by the owner as a consolidated
design on 2026-10-24.

## Intent

Today the only full export of a contest is `cmsDumpExporter` from a shell on
the server. Let an admin download, from AWS, an archive with the same
content and layout `cmsDumpExporter` produces, re-importable with
`cmsDumpImporter` unchanged.

## Facts verified in the code

- `cmscontrib/DumpExporter.py` calls `gevent.monkey.patch_all()` at import
  time. **AWS must never import it** (it would patch `threading` and `socket`
  for the whole asyncio server). It runs only as a child process.
- It opens one `SessionGen()` for the whole run (READ COMMITTED, no
  snapshot, one connection and one transaction held for minutes), loads
  everything through the ORM with lazy relationships (N+1 queries, all in
  memory, one `json.dump`), and fetches every file with the FileCacher.
  During the files phase the outer session is idle in transaction.
- It writes `.tar`, `.tar.gz`, `.tar.bz2`; **`.zip` has an empty
  `write_mode`**, so a `.zip` target would be created as a directory. A
  target without an archive extension is a supported plain directory
  (`contest.json`, `files/`, `descriptions/`; `os.mkdir` on the target, which
  must not exist yet). `DumpImporter.do_import` unpacks through `Archive` and
  requires exactly **one entry at the root** of the unpacked tree. A zip made
  with a single `dump_<...>/` root was verified to be accepted.
- `-c` also exports the users of those contests and every contest they
  joined (existing FIXME in the exporter): the dump can be wider than the
  selection; say so on the page.
- Request slots: `prepare()` takes a slot with a 30 s timeout;
  `FileHandler.fetch` (`cms/server/admin/handlers/base.py` ~L932) closes the
  SQL session and releases the slot before streaming. Copy that for the
  download. `DownloadTestcasesHandler` (BytesIO in memory) is NOT a model.
- `cms/server/admin/importjobs.py` has the job-registry pattern to copy
  (random id, owner check, `JOB_TTL`, one running job).

## Goals

1. An admin with `PERMISSION_ALL` selects one or more contests and the
   exporter switches, starts an export, sees its progress state, and
   downloads a **zip**.
2. The export does not run in the AWS process and does not block other
   admins.
3. Temporary files never accumulate.

## Non-goals (decided)

- **Whole-database mode**: a full-database dump with files is several GB and
  holds a transaction open for a long time; the right tool is `pg_dump`
  (`backup.sh`, #60). The page says so, naming `backup.sh`. (It also
  includes things DumpExporter omits.)
- tar.gz as a selectable format, a synchronous mode for small exports,
  progress percentages, a size cap, a REPEATABLE READ snapshot, a database
  audit table, a generalised job framework, and fixing `DumpExporter`'s own
  `.zip` branch (touches the CLI and its tests).

## Decisions

### 1. Background job, subprocess
`asyncio.create_subprocess_exec(sys.executable, "-m",
"cmscontrib.DumpExporter", "-c", ids..., flags..., target)` with the flags
`-f/-F/-G/-S/-U/-X` mapped from the form. No thread; the event loop awaits
the process. The POST creates the job and returns at once; the page polls the
job state like the CSV import. Run it with `nice -n 19` and `TMPDIR` set to
the job directory. A small `ExportJobStore` copied from the pattern (about 40
lines); do not generalise `ImportJobStore`. **One running export in the whole
process** (disk and memory are the scarce resources).

### 2. Zip packaging
The child exports to a plain directory (a fresh subdirectory
`<job dir>/dump_<contests|db>_<date>`), then the job packs it with stdlib
`zipfile` (or `shutil.make_archive(base, "zip", root_dir=tmp,
base_dir=name)`) so the zip has exactly one root directory, and removes the
staging directory right after. Filename:
`dump_<ids>_<date>.zip`, `Content-Type: application/zip`,
`Content-Disposition: attachment`. A flat zip makes the importer fail with
"Cannot find a root directory": test it. Condition: check that the Docker
image can read zips (`Archive.is_supported` relies on `patoolib`; the
Dockerfile installs `zip`, no `unzip`/`7z` seen; patool probably falls back
to Python's zipfile): run the round-trip test inside the image if feasible,
otherwise report it as not verified.

### 3. Files and cleanup
A dedicated directory, `<cms data dir>/aws-exports` (not `/tmp`, which is
often tmpfs). Wipe it on AWS startup (one AWS process owns it). On non-zero
exit, cancel or exception, `rmtree` the job directory. A finished job keeps
its archive for 1 hour (`JOB_TTL` pattern); TTL eviction deletes the file,
not only the registry entry; re-downloads are allowed within the TTL.
`shutil.disk_usage(dir).free` must be at least 2x the estimate (sum of the
scope's blob sizes if a cheap SQL sum exists, otherwise a fixed floor of
5 GB with a `shortcut:` comment); fail before starting with a clear message.
Do not poll the disk during the export.

### 4. Contests that are running
The export has no snapshot and loads the primary (long transaction, N+1
queries, file reads), in the same database the contest uses. Refuse the
export of any selected contest that is running or in its analysis window
unless the admin ticks an explicit confirmation checkbox that states the
risk (inconsistent dump, database load); default `-G` on and recommended.
No size cap in v1.

### 5. Permissions and audit
Page, start, poll and download require
`require_permission(BaseHandler.PERMISSION_ALL)` (the dump holds password
hashes or plaintexts and all submissions); say so on the page. Poll and
download apply the owner check. `logger.info` at start (admin id and name,
contest ids, flags), finish (size, seconds), failure (exit code only) and
each download (admin id, job id), as `importjobs.py` and `contestuser.py` do.

## Interaction to record on issue #39

#39 stage 2 plans `idle_in_transaction_session_timeout` (~120 s): it would
kill the exporter during the files phase. Add this to the follow-up notes in
the final report (and a comment on #39 is for the coordinator to post).

## Tests

- Job store with a fake argv (`sys.executable -c ...`) and a fake clock:
  success; non-zero exit cleans up; TTL deletes the file; a second
  concurrent start is refused; flags-to-argv mapping; low-disk refusal. **These
  tests must not import `cmscontrib.DumpExporter`.**
- Zip layout test: one root directory.
- Round trip: runs the real child `python -m cmscontrib.DumpExporter` against
  the test database (`CMS_CONFIG` must reach the child through the
  environment), packs the zip, then runs `DumpImporter` on it and compares
  the contest, as `DumpExporterTest` does. It belongs to the **gevent group**
  of the split pytest runs: add it to `GEVENT_SERVICE_FILES` or the
  equivalent list in `docker/_cms-test-internal.sh` and to the CLAUDE.md
  list if that file mirrors it.
- Handler test: POST needs `PERMISSION_ALL` (a plain admin gets 403); the
  download streams and releases its request slot; the running-contest
  refusal and the confirmation.

## Docs

A short note in the admin docs (en + Spanish `.po` by hand), a menu entry
"Export" in the AWS main menu, the warning about wider-than-selected scope
and about `backup.sh` for the whole database.

## Tasks

1. `ExportJobStore` + disk check + cleanup + tests.
2. Subprocess runner + zip packaging + round-trip test.
3. Handlers, page, permissions, audit, running-contest confirmation + tests.
4. Docs, menu entry; docs check.
