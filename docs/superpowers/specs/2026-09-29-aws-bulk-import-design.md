# Bulk Import of Users and Participations in AWS — Design Spec

**Date:** 2026-09-29
**Status:** Approved (design, sections 1-4); pending written-spec review
**When:**
- **Phase A** (the import) is done now, on the branch `carga-masiva`. It is merged into `beta` only if it passes everything before the 2026-10-02 freeze; otherwise the 2026-10-10 contest uses CMS-Loader.
- **Phase B** (removing CMS-Loader) comes after 2026-10-10.

## Problem

Contestants are loaded in bulk with CMS-Loader, a separate Node service
bundled in the image. It takes two CSVs (users, then participations), and
it needs its own credentials, port and build stage. Organizers want to do
the same from the Admin Web Server (AWS) alone, then retire CMS-Loader.

In OMI contests, each contest day has its own password per contestant. In
CMS that is the participation password, which overrides the account
password for that contest.

## Goals

- **Upload.** On a contest's page in AWS, an admin uploads one CSV that
  creates the missing users and registers every row's participation in
  that contest. The participation carries the day's password, the team and
  the group.
- **Mapping.** A column-mapping step, as in CMS-Loader, lets any
  spreadsheet's headers be used.
- **Safety.** Everything is validated before anything is written. Any
  error means nothing is applied.
- **Re-upload.** Uploading a corrected file again updates what already
  exists.
- **Progress.** A progress bar is shown while the passwords are hashed.
- **Retirement.** After 2026-10-10, CMS-Loader is removed from the image
  (Phase B).

## Non-Goals

- Any download or export. Passwords are stored hashed (bcrypt) and
  cannot be recovered, so there is nothing to export.
- Creating teams or groups from the CSV. They must exist beforehand.
- Deleting users or participations that are missing from the file.
- A CLI tool (`cmscontrib`). The import lives in AWS.
- CWS self-registration (a separate backlog item).

## Decisions Taken in Brainstorming

| Question | Decision |
|---|---|
| Where | In AWS, per contest (not in CMS-Loader) |
| File shape | One CSV per contest: user and participation in the same row |
| Day password | The CSV's password becomes the participation password of that contest |
| Account password of new users | Random and hashed; only the day's password logs in |
| Password storage | bcrypt, for both |
| Existing user or participation | Update (names; password, team, group) |
| Unknown team code | Error; nothing is applied |
| Column mapping | Yes, as in CMS-Loader |
| Progress | A progress bar (CMS-Loader style); AWS polls a status URL |
| Downloads | None |
| Timing | Phase A now on its own branch; Phase B after 2026-10-10 |

Rejected alternatives:
- A two-step server-side preview, which needs a pending-file store. "Only
  validate" gives the same preview with no server state.
- A CLI.

## Design (Phase A)

### 1. The file and what it does

**Fields**, mapped from CSV columns in the mapping step:

| Field | Required | Meaning |
|---|---|---|
| username | yes | The login; unique across CMS |
| first_name | yes | |
| last_name | yes | |
| password | yes | The day's password: the participation password in this contest |
| team | no | The team code (for example `JAL`); the team must exist |
| group | no | The name of a group of this contest; empty means the contest's main group |

**Format.**
- UTF-8. A byte-order mark is ignored.
- The first row is the header.
- The delimiter is `,` or `;`, detected from the header line (Excel in
  Spanish saves with `;`).
- Cells are stripped of surrounding whitespace, `password` included,
  because CWS strips what the contestant types (changed on 2026-09-29 by
  the user's decision, after the final review).

**Validation**, done for the whole file before anything is written. Every
error is listed as "fila N: …", with N counting the header as row 1, and
if there is any error nothing is applied:
- a required field that is not mapped;
- a required cell that is empty;
- a username that appears twice in the file;
- a team code that is not in the database;
- a group name that is not a group of this contest;
- a password longer than 72 bytes (bcrypt's limit, as for the staff
  password);
- more than 5000 data rows, or a file over 2 MB.

**Application**, in one database transaction:
- **New user:** created with the row's names and a random account password
  (`hash_password(generate_random_password(), "bcrypt")`).
- **Existing user**, matched by username: `first_name` and `last_name` are
  updated, and nothing else about the user changes.
- **New participation** of that user in this contest: created with the
  row's password (bcrypt), the team (or none) and the group (or the main
  group).
- **Existing participation:** its password, team and group are replaced by
  the row's.
- Nothing else is touched. Other participations, users not in the file,
  and fields such as hidden, unrestricted, IP, delay and extra time keep
  their values.

**Summary** at the end: users created and updated, participations created
and updated. Passwords never appear in any message, page or log line.

### 2. Progress (background job)

- **Validation** runs in the request. If the file is invalid, the page
  shows the errors and nothing starts.
- **The job.** For a valid file (and not "Only validate"), AWS creates an
  in-memory job, following CMS-Loader's `JobStore`:
  - a random id;
  - an owner: the admin who started it;
  - a contest;
  - a status: `running`, `done` or `error`;
  - counters: `processed` and `total`;
  - the summary or an error message;
  - the creation time, and eviction after 1 hour.
- **Running the job.** It hashes every password first. Those are two bcrypt
  hashes per new user and one per existing user. The hashing runs in a pool
  of 4 threads, since bcrypt releases the GIL, and each row that finishes
  advances `processed`. Then the whole application runs in one
  transaction. If any step fails, the transaction is rolled back and the
  job ends in `error` with a generic message. The traceback goes to the
  AWS log, without row data.
- **Polling.** The page polls `GET /contest/<id>/users/import/<job>/status`
  every 500 ms. It shows "Procesando X de N" with a `<progress>` bar, then
  the summary or the error. Closing the tab doesn't stop the job; the same
  URL shows its state again.
- **One job at a time per contest.** Starting a second job while one is
  running for the same contest is refused, with a message.
- **One AWS process.** The job store lives in the AWS process. The Docker
  setup runs one AWS, and a restart loses running jobs. Nothing is
  written until the final transaction, so a lost job has no effect.

### 3. The page, security and limits

**Page:** "Importar CSV" on the contest's Users page (`/contest/<id>/users`),
at `/contest/<id>/users/import`.
1. The admin picks the file. The browser reads only the header line
   (FileReader) and shows one selector per field.
   - A selector is pre-assigned when a header matches the field name or
     a Spanish alias (`username`/`usuario`, `first_name`/`nombre`,
     `last_name`/`apellido(s)`, `password`/`contraseña`, `team`/`equipo`/
     `estado`, `group`/`grupo`), matched without case or accents.
2. "Solo validar" or "Importar" sends the file and the mapping in one
   multipart POST, with XSRF like the rest of AWS. The server keeps no
   file between requests.
3. Errors are shown as a list. A valid "Solo validar" shows the summary of
   what it would do: users to create and to update, participations to
   create and to update. A valid "Importar" starts the job and shows the
   progress bar.

**Security:**
- Only an admin with full permission (`permission_all`) can use it, the
  same as creating users today.
- A job's status is visible only to the admin who started it.
- The day passwords are never echoed back, and never logged.
- An error on a row names the row and the column, never the value of a
  password.
- When a job finishes, AWS logs one INFO line: the admin, the contest and
  the counts, with no personal data.

**Limits:** 2 MB and 5000 rows, checked before parsing the rows.

### 4. Testing

- **CSV reader.** Delimiters `,` and `;`, the BOM, mapping, a missing
  required mapping, empty cells, and the row numbers in errors.
- **Validation.** A username repeated in the file, an empty password, an
  unknown team or group, a password over 72 bytes, and the size and row
  limits. On any error, nothing is written.
- **Application.**
  - A new user gets a random, hashed account password, and their
    participation gets the row's password, hashed.
  - An existing user gets updated names and keeps their account password.
  - An existing participation gets its password, team and group replaced,
    and keeps its other fields.
  - A database error mid-apply leaves everything unchanged (all or
    nothing).
- **Job.**
  - Progress advances to `total`.
  - The owner check holds.
  - A second job on the same contest is refused.
  - Jobs are evicted after 1 hour.
  - bcrypt is replaced by a fast fake in unit tests.
- **Page.**
  - Permission and XSRF.
  - The render has the file input, the selectors, the aliases and both
    buttons.
  - No password appears in any response or log line.
- **End to end.** In Chromium, on a local stack, import a CSV of about 300
  rows.
  - The bar advances, and the summary is right.
  - A contestant logs in to CWS with the day's password.
  - Re-uploading a corrected file updates it.

## Phase B: retiring CMS-Loader (after 2026-10-10)

Remove, in one change with its own plan:
- the `loader-builder` stage and the CMS-Loader copies in `Dockerfile`;
- the `CMS_LOADER_*` build arguments in the compose files;
- the supervisord block and its tests in `docker/generate_config.py` and
  `docker/test_generate_config.py`;
- the `CMS_LOADER_*` variables and the port in `.env.example`;
- the CMS-Loader port in `docker-compose.prod.yml`;
- `docs/cms-loader.md` and its README mentions.

Replace them with a short "Importing users" section in the docs that points
to the AWS page.

## Error Handling

| Situation | Behaviour |
|---|---|
| Invalid file (format, mapping, row errors) | Errors listed per row; nothing written; no job |
| A job already running for the contest | Refused with a message |
| DB error while applying | Transaction rolled back; job `error`; nothing written; traceback in the AWS log |
| AWS restarts during a job | The job is lost; nothing was written (the transaction was not committed) |
| The status URL of an unknown, expired or foreign job | 404 |
