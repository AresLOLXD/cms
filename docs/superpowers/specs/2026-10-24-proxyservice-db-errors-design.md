# ProxyService: transient database errors and a safe Regenerate (issues #61, #62) — Design

**Date:** 2026-10-24
**Status:** Decisions by a read-only panel (python-pro, sre-engineer) and a
tie-breaker (architect-reviewer); the owner approved the consolidated design.
Issue #49 (bisect the split of a refused batch) is deferred on purpose (comment
on the issue).

## Intent

ProxyService treats a transient database error as a broken contest (#61: a
misleading "Cannot build the ranking data" traceback, and after a disconnect
every remaining contest hits `PendingRollbackError`), and `regenerate_ranking`
enqueues the RESET of a ranking namespace before it reads the data (#62: if
the read fails the public scoreboard stays empty, and AWS has already said
"Ranking regeneration requested"). Current locations on main: `_send_type`
~726, `_missing_operations_sync` ~1248, `initialize` ~1300,
`_enqueue_contest_data` ~1345, `_reinitialize_sync` ~1520,
`_regenerate_ranking_sync` ~1605.

## Non-goals

A separate "pending" set (`_broken_contests` already means "data not queued,
scores held back, the sweeper retries and resends all scores"); in-process
retry/backoff loops (they would hold executor threads and DB connections
during an outage, the 2026-10-03 pattern); surfacing errors in the AWS UI
(`schedule_rpc` is fire-and-forget); a two-phase RESET; moving the lost-group
RESET of `_reinitialize_sync` (it has a recovery path); per-contest sessions;
#49.

## Decisions

### #61 — classify database errors
- One helper in ProxyService.py: a database error is
  `isinstance(e, (DBAPIError, PendingRollbackError, sqlalchemy.exc.TimeoutError))`
  (DBAPIError covers OperationalError — lock/statement timeout, deadlock,
  failed connect — InterfaceError and the `is_disconnect` cases; the pool
  TimeoutError is not a DBAPIError). Data errors (ValueError, AttributeError
  from score types, a missing dataset) keep today's "broken" path.
- In `_enqueue_contest_data`, on a database error: add the contest to
  `_broken_contests` (scores stay held back, or RWS refuses them), log ONE
  WARNING line built with `describe_error` from `cms.db.connect_options` (no
  traceback, wording like "database error while reading contest X; its scores
  are held back and it is retried at the next sweep"), then **re-raise**:
  continuing on the poisoned session is what causes the cascade.
- `initialize()`: capture the contest ids first and loop with `enumerate`; on
  a database error add the not-yet-read ids (`ids[i:]`) to
  `_broken_contests` and re-raise.
- `__init__`: do NOT swallow a database error of `initialize()` (tie-breaker):
  let the service start fail like the unguarded `SessionGen` just above it;
  supervisord restarts it (`startretries=30`) and a restart starts clean.
  Keep `test_broken_contest_does_not_stop_startup` true for DATA errors.
- `reinitialize` then fails with an RPC error (AWS logs "RPC reinitialize
  failed" as a warning); the sweep path already logs through `_sweeper_loop`.
- Rewrite `proxyservice_groups_test.py::test_contests_broken_by_a_database_error_all_heal`
  (it pins today's behaviour).

### #62 — build first, then RESET
- Give `_enqueue_visibility`, `_enqueue_contest_data` and
  `_enqueue_submissions` an `enqueue=None` parameter defaulting to
  `self._threadsafe_enqueue`; regenerate passes a list's `append`. After the
  `SessionGen` closes, enqueue `[RESET, *operations]` in a single
  `call_soon_threadsafe` callback (a small `_threadsafe_enqueue_all`). Order
  stays RESET, visibility, contest data, scores. Data errors keep today's
  behaviour (that contest held, the rest sent, the RESET still goes); only a
  database error aborts before the RESET.
- On failure: log ERROR ("Regenerate of group X failed: the scoreboard was
  left as it was and may be stale; press Regenerate again once the database
  answers") and re-raise. In `except BaseException` undo the build's side
  effects: restore `_broken_contests` from a copy taken at the start, and
  discard from the sent sets (submissions and tokens) the ids the build added
  (`after − before`), or a scored-but-never-sent submission looks sent.
- **The race read-then-RESET opens (fix it, tie-breaker):** a score queued or
  sent while the read runs (seconds at ~2000 submissions) is wiped by the
  RESET and missing from the snapshot, yet stays in `scores_sent_to_rankings`,
  so the sweeper never resends it. Copy both sent sets before the read;
  schedule the single RESET+data callback FIRST, then take the "after" copy;
  discard `(after − before) − built_ids` so the sweeper resends them within
  ~6 min (a harmless duplicate PUT at worst). Handle tokens the same way.
  `# shortcut: relies on CPython's atomic set.copy() under the GIL; revisit
  under free-threaded Python.`
- Wrap the sync body of regenerate in a lock (`self._regenerate_lock`): #19's
  bulk removal fires one `regenerate_ranking` per group at once, and one
  group's fix-up must not discard another group's built ids.
- Known consequence to document: after a bulk removal, if the read fails the
  scoreboard now keeps the removed users (before: it went empty). The ERROR
  text and the runbook say so.

## Docs (en + es .po by hand)
`docs/contest-day.md` (~171-174, 244-245, 543): AWS says "requested" even when
it fails, so after pressing Regenerate check `./logs.sh cms` for the result; a
row for the new "database error while reading contest" WARNING (do nothing,
fix PostgreSQL first, recovery within ~6 min or at once after restarting
`cmsproxyservice`); relax the "can leave the scoreboard empty" wording;
`docs/multi-contest.md` (~190-200).

## Tests
- #61: reuse the `SELECT 1/0` injection (it aborts the transaction and
  reproduces the cascade): start healthy, inject, call reinitialize; assert
  the RPC error, the contests held, one WARNING without exc_info, no "Cannot
  build" message; after recovery `_missing_operations()` sends the contests
  then all their scores. Parametrized test of the helper (OperationalError,
  PendingRollbackError, pool TimeoutError → db; ValueError → not). A DB error
  in `__init__`'s initialize propagates.
- #62: fail the read of the last contest → no DELETE, no PUT,
  `_broken_contests` and sent sets unchanged, ERROR logged; the order of
  enqueued operations (spy on the enqueue, as
  `test_healing_contest_is_held_back_until_its_data_is_queued`); the race:
  call `_submission_scored_sync(new)` from inside a patched
  `_operations_for_contest` → the id leaves the sent set and the next sweep
  sends it; two concurrent regenerates serialize.

## Tasks
1. #61 helper + `_enqueue_contest_data` + `initialize` + tests (rewrite the pinned test).
2. #62 build-then-RESET + undo on failure + tests.
3. #62 race fix + lock + tests.
4. Docs + docs check.
