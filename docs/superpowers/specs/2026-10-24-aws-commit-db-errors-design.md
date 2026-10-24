# AWS: "Operation failed" instead of a 500 when a commit loses the database (issue #63) — Design

**Date:** 2026-10-24
**Status:** Decisions by a read-only panel (python-pro) and a tie-breaker
(architect-reviewer); the owner approved the consolidated design.

## Facts verified on main

- `try_commit` (`cms/server/admin/handlers/base.py` ~338) catches only
  `IntegrityError` and never rolls back. After any failed commit the session
  must be rolled back before it is used again (a failed flush leaves it
  DEACTIVE, a failed COMMIT PREPARED); without it the next attribute read
  raises `PendingRollbackError`/`InvalidRequestError`. So today's
  `IntegrityError` branch has a latent bug: e.g. `dataset.py:284` calls
  `try_commit()` then `redirect(self.url("task", task.id))` → 500.
- `write_error` (~586) builds `error_render_params()` (~500) only when
  `r_params is None`; otherwise `error.html` extends `base.html`, which reads
  `contest.main_group.*`, `admin.name` and the sidebar lists; all lazy-load
  once the session committed or rolled back (default `expire_on_commit`), on
  the event loop, possibly waiting `pool_timeout` (60 s): the 2026-10-03
  incident class.
- `cms.db.connect_options` already has `is_disconnect`, `describe_error`,
  `log_rollback_error`. Reuse them.
- The bulk-removal handler commits inside its own `try` (rolls back,
  notifies): already complete; leave it.

## Non-goals
Retry loops, a new exception class, a 503 for DB errors, a decorator
converting DB errors for all handlers, catching `PendingRollbackError` /
`InvalidRequestError` (they mean a handler swallowed an error), Spanish text
or i18n, `pool_pre_ping`, changes to bulkremove. Follow-up to note (not
build): the pre-existing `IntegrityError` notification shows the full SQL and
parameters to the admin.

## Decisions

### try_commit
```python
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError  # the builtin TimeoutError is used elsewhere in the module
...
except (DBAPIError, PoolTimeoutError) as error:
    if isinstance(error, IntegrityError):
        text = "%s" % error                       # unchanged
    elif isinstance(error, (OperationalError, PoolTimeoutError)) \
            or is_disconnect(error):
        logger.error("Commit of %s %s failed: %s", self.request.method,
                     self.request.path, describe_error(error))
        text = "The database did not answer, try again."
    else:
        raise                                     # a bug: keep the 500
    try:
        self.sql_session.rollback()
    except Exception as rollback_error:
        log_rollback_error(rollback_error, True)  # DEBUG, never masks
    self.service.add_notification(make_datetime(), "Operation failed.", text)
    return False
```
Log `describe_error`, never `str(error)` (SQLAlchemy's text carries the SQL
and parameters, which can include password hashes). Subject stays
"Operation failed." (English, like the neighbours and the docs).

### write_error (tie-breaker: Option A)
Delete the `if self.r_params is None:` guard: always render the error page
with `error_render_params()` (query-free). Keep the existing `try/except`
"critical error" fallback. The page loses its sidebar/title, which an
"An error occurred" page does not need.

## Tests
1. New `cmstestsuite/unit_tests/server/admin/try_commit_test.py` (pattern of
   `clonedataset_test.py`: `BaseHandler.__new__`, MagicMock application and
   session, a request mock). subTests with `commit.side_effect`:
   OperationalError with `connection_invalidated=True` (helper as in
   `session_exit_test.py`), an OperationalError lock timeout, an
   InterfaceError with `connection_invalidated=True`, the pool TimeoutError,
   an IntegrityError → each returns False, calls rollback once, notifies
   "Operation failed."; a ProgrammingError re-raises with no notification; a
   raising rollback still returns False; no SQL text in the log line.
2. `write_error(500)` with `r_params = {"sentinel": 1}` renders with the
   error params, not the sentinel.
3. If cheap: in `pool_exhaustion_test.py`'s `ErrorPageTest`, a handler that
   sets `r_params = self.render_params()` before a failing commit and asserts
   no lazy-load SELECT from the error page.

## Docs
If `docs/contest-day.md` has a row about AWS 500s on database errors, update it
(en + es .po by hand); otherwise none.

## Tasks
1. try_commit + tests. 2. write_error + test. 3. Docs if any; docs check.
