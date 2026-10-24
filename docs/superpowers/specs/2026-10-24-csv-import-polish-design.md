# Polish of the CSV bulk import (issue #17) — Design

**Date:** 2026-10-24
**Status:** Decisions taken by a panel of read-only specialist subagents
(python-pro, security-auditor, code-reviewer), approved by the owner as a
consolidated design on 2026-10-24. Written task list at the end.

## Intent

The AWS bulk import of users and participations (guide
`docs/importing-users.md`, original spec
`2026-09-29-aws-bulk-import-design.md`) is on main. Its final review parked a
list of small defects and UX gaps. This change closes the ones that are worth
their cost and says plainly which are left out.

## Goals

1. A job whose thread cannot start must not block its contest for an hour.
2. The 500 ms status poll must not flood the INFO log.
3. The account password generated for imported users, `cmsAddUser`,
   `cmsAddAdmin` and the `User` default must come from a CSPRNG.
4. A hung database commit must not freeze every contest's import.
5. The admin sees the first parsed row before confirming, to catch a wrong
   column mapping.
6. The import page and guide no longer suggest a "unchanged" count that does
   not exist.

## Non-goals (decided, with the reason)

- Lowering the 100 MB Tornado body limit **per handler**: Tornado reads the
  body before `prepare()` for non-streaming handlers, so a per-handler cap
  needs `@stream_request_body` and hand-parsed multipart. The limit is shared
  by about ten AWS upload handlers and the task/testcase uploads need it.
  Decision: document it, and document a proxy cap (see Docs).
- A real "unchanged" count (the checklist's "sin cambios"): no such counter
  exists in the code. `usuarios_actualizados` means "already exists", even
  when identical. Comparing every field plus a bcrypt check per row is a lot
  of code for a cosmetic number. Decision: rename the labels and document.
- Restoring the password column by position after "Solo validar", and the
  alias fallback for fields left unassigned because the previous file did not
  have them (the page cannot tell "previous file lacked it" from "admin chose
  to unassign", and unassigning team/group clears them on purpose).
- Changing the 500 ms poll interval, a mixed or longer password alphabet, a
  new config option, a fix of the stale "nuevos" count when two contests share
  a new user (cosmetic, rare).

## Decisions

### 1. Thread start (cms/server/admin/importjobs.py)
`ImportJobs.start` registers the job and then calls `Thread(...).start()`
outside any `try`. Wrap the `.start()` in `try/except BaseException`; on
failure take `self._lock`, delete the job from `self._jobs`, re-raise. Do not
hold the lock around `.start()`. Removing the job is smaller than marking it
`error`: nobody can poll a job whose id was never returned.

### 2. Registry hardening
- `_evict` currently drops jobs by `created_at` after `JOB_TTL` even if still
  `running`; evict only jobs that are not running.
- In the worker thread, `finally: if job.status == "running": job.status =
  "error"` so a `BaseException` does not leave a job running forever.
- `_APPLY_LOCK` (global) has no timeout: use
  `_APPLY_LOCK.acquire(timeout=...)` and set `lock_timeout` and
  `statement_timeout` on the import session with `SET LOCAL`, so a commit
  stuck on a database lock turns into `status="error"` instead of stalling
  every contest. Pick conservative values (tens of seconds for the lock,
  minutes for the statement) as named module constants; note them in a
  `shortcut:` comment if they are guesses.

### 3. Poll logging
`cms/io/web_service.py:log_request` already sends the probe path to DEBUG.
Add a class attribute `quiet_access_log = True` on
`ImportJobStatusHandler` and `GlobalImportJobStatusHandler` and make
`log_request` use DEBUG when `status < 400` and (probe path or
`getattr(handler, "quiet_access_log", False)`). Errors keep their levels.

### 4. Password generator (cmscommon/crypto.py)
`generate_random_password`: `secrets.choice` over `string.ascii_lowercase`,
12 characters (about 56 bits). Keeping lowercase letters keeps the
existing `^[a-z]*$` test and keeps CLI-printed passwords easy to type;
bcrypt's 72-byte limit is irrelevant. Callers verified: `cms/db/user.py`
(User default, stored plaintext), `bulkimport.py` (never shown),
`AddAdmin.py`, `AddUser.py`; nothing depends on the length. Replace
`import random` with `import secrets` if `random` is unused elsewhere in the
module. Add a length assertion to `crypto_test.py`.

### 5. First-row preview
In the import handler's validate action (`_upload`), pass the first parsed
row (username, first name, last name, team, group; **never the password**)
to `_render_page` as a new `preview` argument. `_render_page` renders with
`StrictUndefined`, so update every call site. Add a few lines to the
template. Server side, not JavaScript.

### 6. Labels ("actualizados" means "already exists")
Rename the labels in `contest_users_import.html` (the Spanish text at the
summary table and the JS strings that render it) to something like "ya
existentes (se sobrescriben)", update the assertions in
`cmstestsuite/unit_tests/server/admin/contest_users_import_test.py`, update
`docs/importing-users.md` and its Spanish `.po`. Keep the summary keys
unchanged.

## Docs

- `docs/importing-users.md`: the body limit sentence next to the 2 MB line:
  AWS accepts up to 100 MB of body and only then refuses with "pasa de 2 MB";
  a reverse proxy can cap it earlier. Add a Caddy example,
  `request_body { max_size 4MB }`, scoped to the import paths only
  (`/admin/users/import` and `/admin/contest/*/users/import`; a 5000-row CSV is
  about 0.5 MB). Say this is the only protection that runs before
  authentication. The runbook is a doc change, not a repo-side fix.
- A `shortcut:` comment in the `_upload` handler pointing at the same limit.
- The English text is the source; edit the Spanish `.po` entries by hand
  (never `sphinx-intl update`).

## Tests

Unit tests in the existing style: start failure frees the contest
(`threading.Thread.start` patched to raise, `running_job(contest)` is None);
eviction keeps a running job; the thread `finally` marks an error; lock
timeout path; `log_request` level for the status handlers; password length
and alphabet; the preview appears on validate and never contains the
password; label assertions.

## Out of scope for this branch

Everything under Non-goals. Another lane (issue #19) edits
`cms/server/admin/handlers/contestuser.py` (it removes the old POST
handlers) and `docs/importing-users.md`; keep edits to those two files small
and local so the merge is trivial.

## Tasks

1. Thread start + registry hardening + tests.
2. Poll logging + test.
3. Password generator + tests.
4. First-row preview + labels + tests.
5. Docs (en + es `.po`) and docs check.
