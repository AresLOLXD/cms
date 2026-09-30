# Bulk Removal of Users and Participations in AWS — Design Spec

**Date:** 2026-09-30
**Status:** Approved (design, sections 1-3); pending written-spec review
**When:** built now on the branch `borrado-masivo`. It is merged into
`main` only if it passes everything (task reviews, final review, CI on
noble and bookworm, the Chromium E2E) before the 2026-10-02 freeze, and
only after the user confirms the merge. Otherwise the branch waits until
after 2026-10-10.

## Problem

AWS removes one user, or one participation, at a time: the lists use
radio buttons and every removal needs its own confirmation page. Cleaning
up after a rehearsal or a wrong CSV import means dozens or hundreds of
round trips. The existing removal also deletes through the ORM
(`session.delete(user)`), which loads every dependent row into Python.

## Goals

- **Two scopes, two pages.**
  - The "Users list" (`/users`) removes accounts from the platform.
  - A contest's "Users" page removes participations from that contest
    only; it never removes an account.
- **Two ways to select, on both pages.** Checkboxes with "select all",
  and a list of usernames (pasted in a textarea or uploaded as a file).
- **One confirmation page** that shows exactly what will be lost and
  requires typing the number of users to confirm.
- **All or nothing.** One transaction; any error leaves the database
  untouched.

## Non-Goals

- A background job or a progress bar (removal is one SQL statement with
  database cascades; it takes seconds for a few hundred users).
- Removing the stored files (`fsobjects`) of the deleted submissions.
  Single-user removal leaves them too.
- Filters (by group, team or username prefix).
- A CLI tool.
- Blocking removal while a contest is running (a warning is shown
  instead).
- Changing the single-user routes (`/users/<id>/remove`,
  `/contest/<id>/user/<id>/remove`); they stay as they are.
- Fixing `FunctionalTestFramework.get_users`, whose regex no longer
  matches the contest users page (pre-existing, out of scope).

## Decisions Taken in Brainstorming

| Question | Decision |
|---|---|
| Timing | As soon as possible; merge only if everything passes before 2026-10-02 |
| Selection | Checkboxes and a list of usernames (textarea or file) |
| Protection | Summary page; confirm by typing the number of users; unknown usernames are reported and do not block |
| Approach | A: synchronous, in the request; one bulk `DELETE`; one ranking reinitialization |
| Scopes | Platform removal on `/users`; participation removal on the contest's users page only |
| UI language | Spanish, like the CSV import page |

## Section 1: Interface and Flow

**`/users` ("Users list"):**
- The radio buttons become checkboxes named `user_id` (several values),
  with a "select all" checkbox in the header.
- The "Remove" button becomes **"Borrar seleccionados"**. It also works
  for a single user.
- Below the table, a second form **"Borrar por lista"** has a textarea
  and a file input.

**`/contest/<id>/users`:**
- The same checkboxes and "select all".
- The "Remove from contest" button becomes **"Quitar seleccionados del
  concurso"**.
- The same "por lista" form, in the contest scope.

**Username list format** (textarea and file alike):
- One username per line, or a CSV whose first column is the username.
- UTF-8 with or without BOM; LF or CRLF.
- Leading and trailing whitespace is stripped; empty lines are skipped.
- A first line whose first column is `username` (any case) is a header
  and is skipped.
- Duplicates are dropped, keeping the first occurrence's order.
- If both the textarea and the file are given, their usernames are
  joined (textarea first).
- More than 1 MB of input is rejected with a notification, and nothing
  is shown or removed.

**Confirmation page** (one template for both scopes):
- A table of the users to remove: username, first and last name, and
  submission count. In the platform scope, also the names of the contests
  the user participates in.
- Totals: users, submissions and user tests to be removed.
- An "ignored" list, which does not block: usernames that do not exist,
  and, in the contest scope, users who exist but do not participate in
  the contest. Each carries its reason.
- A red warning when any affected contest is running now: submissions
  made during the contest will be lost and the ranking is rebuilt.
- A field "Escribe N para confirmar" and the confirm button.
- If nothing is left to remove, the page says so and shows no button.
- On success AWS redirects to the list with a notification: "Se borraron
  N usuarios" or "Se quitaron N participaciones".

"Select all" uses a short inline script, as `contest_users_import.html`
already does.

## Section 2: Backend, Data and Errors

### Module `cms/server/admin/bulkremove.py`

Pure functions, no Tornado, testable against the test database.

- `parse_usernames(data: bytes) -> list[str]`
  - Implements the list format above.
  - Raises `ValueError` on more than 1 MB or on bytes that are not UTF-8.
- `plan_removal(session, *, contest_id: int | None, user_ids:
  Sequence[int] = (), usernames: Sequence[str] = ()) -> RemovalPlan`
  - `contest_id is None` is the platform scope.
  - Resolves user ids and usernames into the users to remove.
  - Each ignored entry gets a reason: `"no existe"` (unknown username or
    id) or `"no participa en el concurso"` (contest scope only).
  - Counts submissions and user tests per user. In the contest scope they
    are counted in that contest only; in the platform scope, over every
    participation.
  - Lists the affected contests. In the contest scope it is that contest;
    in the platform scope, every contest of those users.
  - Sets `running` when any affected participation's group has
    `start <= now < stop`.
- `RemovalPlan` is a dataclass:
  - `users`: a list of entries, each with the id, username, names,
    submission count, user test count and contest names;
  - `ignored`: a list of `(value, reason)` pairs;
  - the totals, `running` and `user_ids`, sorted.
- `remove_users(session, user_ids) -> int` runs
  `DELETE FROM users WHERE id IN (...)`.
- `remove_participations(session, contest_id, user_ids) -> int` runs
  `DELETE FROM participations WHERE contest_id = ? AND user_id IN (...)`.
- Both use `synchronize_session=False` and return the row count. The
  dependent rows go through the database's `ON DELETE CASCADE`, which
  every foreign key under users and participations has:
  - submissions, and their files, results, evaluations and executables;
  - user tests, and their files, managers and results;
  - questions and messages.

### Handlers

- **Routes.** `POST /users/remove` handles the platform scope, and
  `POST /contest/<id>/users/remove` the contest scope.
- **Permissions.** Both require `PERMISSION_ALL` and run their work in
  the executor, like every other AWS handler.
- **Form fields.** `action` is `preview` or `confirm`. The other fields
  are the `user_id` values, `usernames` (the textarea), the `file` upload
  and `expected_count` (the typed number).
- **`preview`:**
  1. Builds the plan from the checkboxes, or from the list.
  2. Renders the confirmation page, with the resolved ids as hidden
     `user_id` fields.
  3. If nothing was given, it redirects back with a notification.
- **`confirm`:**
  1. Builds the plan again from the posted ids.
  2. Removes nothing when `expected_count` differs from the number of
     posted ids or from the number of users in the new plan. Either
     mismatch covers the case where the selection changed since the
     preview, for instance because another admin removed someone. The
     confirmation page is rendered again, with the fresh plan and an
     error line.
  3. Otherwise it removes everything in one transaction through
     `try_commit`.
  4. After a successful commit, it schedules one
     `proxy_service.reinitialize` and adds the success notification.
  5. On failure it adds the usual error notification and schedules no
     RPC.
- **Existing list handlers.** `UserListHandler.post` and
  `ContestUsersHandler.post` keep their single-user behaviour for any old
  form. The new buttons post straight to the new routes.

### Known limits

- The stored files of the removed submissions stay in `fsobjects`, as
  with single removal.
- If the Evaluation Service still has queued work for a removed
  submission, it logs errors when it cannot find it, as with single
  removal. This is why the confirmation page shows the running-contest
  warning.

## Section 3: Testing and Delivery

**Unit tests.** They follow the patterns of `bulkimport_test.py` and
`importjobs_db_test.py`.

`cmstestsuite/unit_tests/server/admin/bulkremove_test.py`:
- `parse_usernames`: BOM, CRLF, a multi-column CSV, the header, empty
  lines, duplicates, more than 1 MB, invalid UTF-8.
- `plan_removal`:
  - unknown usernames;
  - in the contest scope, users who exist but do not participate;
  - the counts;
  - the affected contests;
  - `running` true and false.
- `remove_users`: after the removal, no submission, result, evaluation,
  user test, question or message of those users is left, and the other
  users are untouched.
- `remove_participations`: only that contest's participation goes; the
  user and their other participations stay.

`cmstestsuite/unit_tests/server/admin/bulkremove_handlers_test.py`:
- a preview from checkboxes and a preview from a list;
- a confirm with the right count and with a wrong one;
- a plan that changed between the preview and the confirm, where
  nothing is removed;
- the permission check (403);
- exactly one `reinitialize` after a successful commit, and none when
  the commit fails.

**Browser E2E.** Chromium, against a local Docker stack:
- the checkboxes and "select all";
- removing 3 users, and removing 2 participations from a contest;
- a CSV file with an unknown username;
- the red warning on a running contest;
- a mistyped count being rejected.

Screenshots go to the user.

**Delivery:**
1. The branch `borrado-masivo`, in its own worktree from `main`.
2. Subagent-driven development, with a task reviewer per task and a
   final whole-branch review.
3. The CI on noble and bookworm, never concurrently. It includes the
   functional tests, which use `users/add` and
   `contest/<id>/users/add`; those routes are unchanged.
4. The merge into `main` only if everything passes before 2026-10-02,
   and only after the user confirms it.
