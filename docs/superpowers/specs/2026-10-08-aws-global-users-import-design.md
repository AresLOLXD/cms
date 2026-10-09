# Global Users Import and Optional Contest Password in AWS — Design Spec

**Date:** 2026-10-08
**Status:** Approved (design, sections 1-3); pending written-spec review
**When:** before the 2026-10-10 contest. Built on `main`, then merged into
`main` and `beta`. The version bump (omi.7), the tag and the deploy are the
user's call.

## Problem

The AWS bulk import (spec `2026-09-29-aws-bulk-import-design.md`) works per
contest: each row creates the user if missing, with a **random** account
password, and gives the participation the row's password (the day's
password). There is no way to create accounts with their own password in
bulk: CMS-Loader's "Import Users" did that, and CMS-Loader is being retired
(#18).

Organizers want to create the contestants' accounts once, with the account
password, and then register them in one or more contests (for example D1
and D2) with no per-contest password. Today a contest can only be filled
with the CSV import, which requires a password column and stores it as the
participation password, or one user at a time with "Add user".

In CMS, a participation without a password logs in with the account
password (`get_password` in `cms/server/contest/authentication.py`), and the
CWS cookie holds the stored hash, so replacing a hash with a new hash of the
same password logs the contestant out.

## Goals

- **Global import.** On the global Users list, an admin uploads a CSV that
  creates or updates accounts with their account password.
- **Optional contest password.** The contest import accepts no password
  column: new participations then log in with the account password.
- **Same guarantees as the contest import.** Column mapping (a column may
  feed several fields, except the password one), validation of the whole
  file before writing, all or nothing, a progress bar, passwords never
  echoed or logged.

## Non-Goals

- Registering users in a contest from the global import. The contest
  import does that.
- Optional fields on the global import (email, timezone, preferred
  languages).
- Telling apart an account password set by an admin from the random one a
  contest import gives to the users it creates. The manual says to import
  the accounts first.
- Removing CMS-Loader (Phase B of the 2026-09-29 spec, after 2026-10-10).
- Deleting users missing from the file.

## Decisions Taken in Brainstorming

| Question | Decision |
|---|---|
| Purpose | Create accounts with their password once, then register them in contests without a per-contest password |
| Timing | Before 2026-10-10, on `main` |
| Global import fields | username, first_name, last_name, password; all required; no optional fields |
| Existing user in the global import | Update first name, last name and account password; keep the stored hash when the password still matches it |
| Registering the accounts in contests | The contest import's password becomes optional |
| Contest import without a password column, existing participation | Keeps its password |
| Contest import without a password column, user that does not exist | Error on the row; nothing is applied |
| Approach | Reuse the contest import's modules (parametrized), not a copy |

Rejected alternative: a separate module and template for the global import.
It would leave the contest import untouched before 2026-10-10, but it would
duplicate the password-safety rules (never echoing the password column, NUL,
control characters, 72 bytes, keeping a matching hash), and every later fix
would have to be made twice.

Related change, on its own branch and merged first: a CSV column may feed
several fields of the contest import, except the password column, which
stays exclusive. The global import gets the same rule because it reuses
`read_rows`.

## Design

### 1. Behaviour

#### Global import ("Importar CSV" on Users)

**Page:** `/users/import`, linked as "Importar CSV" from the global Users
list (`/users`), like the link on a contest's Users page.

**Fields**, mapped from CSV columns exactly as in the contest import:

| Field | Required | Meaning |
|---|---|---|
| username | yes | The login; unique across CMS |
| first_name | yes | |
| last_name | yes | |
| password | yes | The account password |

The file format, limits (2 MB, 5000 rows), header guessing and Spanish
aliases are the contest import's. The password selector is labelled
"Contraseña (password)".

**Validation**, for the whole file before anything is written, with the
contest import's messages: a required field not mapped, a required cell
empty, a username repeated in the file, a username with characters the
database refuses, a NUL in a cell, a password over 72 bytes or with a
control character. The password column cannot be used by any other field.

**Application**, in one transaction:
- **New user:** created with the row's names and the row's password
  (bcrypt) as the account password.
- **Existing user**, matched by username: first name, last name and account
  password are replaced. When the stored account password is a bcrypt hash
  that the row's password matches, the stored hash is kept, so a contestant
  logged in with it stays logged in.
- Nothing else is touched: participations, teams, groups, email, timezone
  and languages keep their values.

**Summary:** users created and users updated. The page also says that the
account password is only used in contests where the participation has no
password of its own.

#### Contest import: optional password

The password field of the contest import stops being required. When it is
mapped, everything works as today (an empty cell is still an error). When
it is left "(sin asignar)":
- **New participation** of an existing user: created with no password, so
  it logs in with the account password.
- **Existing participation:** keeps its password, whatever it is.
- **User that does not exist:** an error on the row, "fila N: el usuario X
  no existe; sin columna de contraseña solo se pueden inscribir usuarios
  que ya existen". Nothing is applied. A new user would get a random
  account password and could never log in.
- Names, team and group behave as today.

The selector keeps the label "Contraseña del día (password)" and loses the
`*`. When the page is shown again after an error, the password selector is
guessed again from the headers, as today, so an admin who left it
unassigned must unassign it again; the existing notice ("revísala") already
says so.

**Resulting workflow:** import the accounts once on Users with their
password; then, in each contest, import the same CSV with the password left
"(sin asignar)".

### 2. Code

**`cms/server/admin/bulkimport.py`**
- `read_rows(data, mapping, fields, required)` takes the list of fields and
  the required ones. The contest import passes its six fields with
  `username`, `first_name` and `last_name` required; the global import
  passes `username`, `first_name`, `last_name` and `password`, all
  required. Labels and empty-cell messages stay keyed by field.
- `ImportRow.password` becomes `str | None`: `None` when the password field
  is not mapped. A mapped password with an empty cell is still the error
  "la contraseña está vacía".
- `plan_import` (contest): when the rows carry no password, every username
  that does not exist is an error on its row, and the plan records that
  participation passwords are not touched.
- `apply_import` (contest): with no password, a new participation gets
  `password = None` and an existing one keeps its password.
- New `plan_user_import(session, rows)`: the new and the existing usernames,
  and username -> stored account password, for the comparison.
- New `apply_user_import(session, rows, hashes)`: creates or updates the
  users.
- The "keep the stored hash when the password matches it" step moves out of
  `hash_passwords` into a helper that both imports use: the contest import
  for the participation password, the global one for the account password.
  The hashing still runs in the pool of `HASH_THREADS` threads at a lower
  priority, with one progress tick per row.

**`cms/server/admin/importjobs.py`**
- `ImportJob.contest_id` becomes `int | None`; `None` is the global Users
  import. `_run` picks the plan, hash and apply steps from it.
- One running job per contest and one for the global import. All writes
  still go through `_APPLY_LOCK`, and each apply step reads the users again
  before writing, so a user created by another import between a plan and
  its apply is updated, not duplicated.
- The log lines say "global users import" instead of a contest id, and
  still carry no row data.

**Handlers and routes**
- The contest `ImportUsersHandler` and a new handler for the global import
  share the form, validation and job-start logic through a common base.
  What differs is the field list, the required fields, the plan step and
  the URLs.
- New routes: `/users/import` and `/users/import/<job>/status`, both
  requiring `PERMISSION_ALL`, as the contest ones.
- After the commit, the global import calls `proxy_service.reinitialize`,
  as "Add user" does.

**Template**
- `contest_users_import.html` becomes one template for both pages. The
  handler passes the title, the introduction, the fields, the required
  fields, the labels and the URLs (import, status, back to the list).
- The summary, both in Jinja and in the progress JavaScript, shows only the
  keys the plan or the job returns, so it serves both pages.
- `users.html` gets the "Importar CSV" link.

**Docs**
- `docs/importing-users.md`: a section on the global import, the optional
  contest password and the workflow above. The Spanish
  `docs/locale/es/LC_MESSAGES/importing-users.po` follows every English
  edit.

### 3. Error Handling

| Situation | Behaviour |
|---|---|
| Invalid file (format, mapping, row errors) | Errors listed per row; nothing written; no job |
| Contest import without a password column and a user that does not exist | Error on the row; nothing written; no job |
| A job already running for the same contest, or a global import already running | Refused with a message; the admin's own running job is shown instead |
| DB error while applying | Transaction rolled back; job `error` with "no se pudo aplicar la importación; no se guardó nada"; the AWS log gets the class of the exception and the traceback frames only |
| AWS restarts during a job | The job is lost; nothing was written |
| The status URL of an unknown, expired or foreign job, or of a job of the other kind | 404 |

## Testing

- **Unit** (`bulkimport_test.py`):
  - `read_rows` with the global fields: the four are required, the mapping
    rules hold, a shared column feeds several fields, the password column
    is exclusive and never echoed.
  - The contest fields with the password unmapped give rows with
    `password = None` and no error; a mapped password with an empty cell is
    still an error.
- **DB:**
  - `plan_user_import` / `apply_user_import`: a new user gets the row's
    password hashed; an existing one gets names and password replaced; a
    matching bcrypt hash is kept; nothing else of the user changes; all or
    nothing on a DB error.
  - Contest import without a password: a user that does not exist is an
    error on its row; a new participation has no password; an existing
    participation keeps its password; names, team and group are updated.
- **Jobs:** `ImportJobStore` with `contest_id = None`: one running at a
  time, independent of contest jobs, progress, owner check, 404 for a
  foreign job or a job of the other kind, eviction.
- **Handlers:** the new routes, permission and XSRF, the render of the
  shared template on both pages, the "Importar CSV" link on Users, and no
  password in any response or log line.
- **End to end**, in Chromium on a local stack: import accounts on Users;
  import the same CSV in a contest with the password unassigned; log in to
  CWS with the account password; import the accounts again with the same
  passwords and stay logged in.
- **Locally, not on GitHub** (the fork's workflows are disabled and runs
  must be frugal): the admin unit tests, the docs translation check and
  Spanish build, and the functional tests on the rootful Docker harness.
  The work is pushed once, after the final review.
