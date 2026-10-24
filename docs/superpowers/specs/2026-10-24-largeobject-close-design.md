# Close LargeObject connections explicitly (issue #64) — Design

**Date:** 2026-10-24
**Status:** Decisions by a read-only panel (postgres-pro); the owner approved
the consolidated design.

## Facts

Each `LargeObject` (`cms/db/fsobject.py`) opens its own non-pooled psycopg2
connection. On the happy path CPython refcounting already frees it (psycopg2
closes in its destructor); a leak shows up while something still references
the object: an exception traceback, a stored lobject, a greenlet frame. So the
fix buys determinism and error-path safety, not the ~8 ms connect cost.

Leak sites:
1. `LargeObject.close()` (~317-338) never calls `_conn.close()`; if
   `lo_close` or `commit` raises, `_fd` stays set and GC retries the failure.
2. `LargeObject.__init__` (~100-120): if `lo_creat`/`lo_open` fails, the
   connection stays open in an aborted transaction.
3. `LargeObject.unlink(loid)` with `conn=None` opens an autocommit connection
   and never closes it (reached by `DBBackend.delete` and the IntegrityError
   path of `commit_file`).
4. `FileCacher.put_file_from_fobj` (`cms/db/filecacher.py`): if
   `copyfileobj` raises, `fobj` is never closed.

## Non-goals
Connection reuse per batch, a pool, a thread-local cache (most submissions
carry one file; #22 moves the work off the loop); an orphan large-object
cleaner; rollback-on-error semantics (a close after a failed copy still
commits the partial large object, as the GC path does today); `__del__` or
weakref nets; a wrapper context manager (`IOBase` gives `with`); an async
LargeObject; metrics.

## Decisions (about 15 lines)
- `__init__`: set `self._fd = None` and `self._conn = None` right after
  `RawIOBase.__init__`; wrap the cursor work in
  `except BaseException: self._conn.close(); raise`.
- `close()`: lo_close and commit in a `try`, a `finally` that sets
  `_fd = None` and calls `_conn.close()`; idempotent.
- `unlink`: close the connection only when the function created it
  (`own = conn is None`).
- `put_file_from_fobj`: `try/finally: fobj.close()` around `copyfileobj` and
  `commit_file` (safe for FSBackend files and LargeObject; NullBackend returns
  None there — guard it). Known limit: if that close raises it masks the
  original error (kept as `__context__`).
- `psycopg2.connection.close()` does no network wait: safe from gevent
  (Worker) and from asyncio executor threads.

## Tests
A `DatabaseMixin` module next to `filecacher_test.py`. Patch
`cms.db.fsobject.custom_psycopg2_connection` with a spy that records each
connection it returns (and `conn.info.backend_pid`); assert `conn.closed != 0`
for every recorded connection; one server-side check: poll
`pg_stat_activity WHERE pid = ANY(recorded_pids)` up to ~2 s until 0 rows (pids,
not application_name: the test DB is shared and the pool uses the same name).
Cases (each fails before the fix because the test keeps a reference alive):
create and close an `LO('wb')` keeping the reference; `LargeObject(999999999,
'rb')` raises OSError (hold the exception) → closed; `put_file_content` then
`get_file_content` through `FileCacher()` → every recorded connection closed
and its backend gone; `DBBackend.delete` closes the unlink connection.

## Tasks
1. fsobject changes + tests. 2. filecacher change + test. (No docs.)
