# Evaluation Service: lock-free loop actions and airtight invalidation

Date: 2026-09-30. Branch: `es-loop-stall` (from main f6fe1d18; the CI
open-files commit a1f5e242 is already on it). Deadline: merged into main
before Friday 2026-10-02, for the contest on 2026-10-10.

Line numbers refer to commit a1f5e242. ES = `cms/service/EvaluationService.py`,
WP = `cms/service/workerpool.py`, FD = `cms/service/flushingdict.py`,
OPS = `cms/service/esoperations.py`.

## 1. Problem

### 1.1 The event loop blocks on `post_finish_lock`

`post_finish_lock` is a `threading.RLock`. It is held by `_sync` methods on
executor threads: `write_results` batches, the sweeper, `invalidate_submission`,
and (since 8cc6cfb9) `new_submission` / `new_user_test`. Two kinds of deferred
action run on the event loop thread: the `_do` of `_threadsafe_enqueue`
(ES 658) and the `_do` of `_threadsafe_dequeue_and_ignore` (ES 694). Both take
the same lock and block. While a holder works, the whole loop freezes: RPCs,
the executor, `queue_status`. A 2 s hold measured a 1.95 s freeze.

A first attempt drained the actions with a non-blocking acquire and a 5 ms
retry. It removed the freeze but broke two guarantees, and 8 tests failed:
- A late worker result of an operation invalidated just before was no longer
  ignored (`evaluationservice_failures_test.py::test_result_of_an_operation_invalidated_in_flight_is_ignored`,
  3 of 3 runs).
- When an ES coroutine returned, its deferred actions had not always been
  applied yet (7 tests).
That attempt is dropped. Its patch is kept at
`.superpowers/es-loop-stall-change1-wip.patch` in the main checkout, for
reference only.

### 1.2 Stale results written after an invalidation (G1)

`invalidate_submission` does not purge `result_cache`. Two windows let a
stale result reach the DB after the invalidation:
1. **Still cached.** A worker result R for operation O is in the cache's
   pending dict when the invalidation V runs (ES 810, FD 93-95). V deletes the
   DB rows and re-pushes O. Then the next flush writes R.
2. **Taken by a flush.** `flush` has moved the pending dict into `fd` and built
   the callback's list (FD 99-103). `write_results` is blocked on
   `post_finish_lock`, which V holds. After V, it writes the stale items.

Effects:
- **Evaluations.** The stale row wins. The fresh result then violates
  `UniqueConstraint(submission_id, dataset_id, testcase_id)`, is caught as
  "Integrity error" (ES 1014) and lost. ScoringService gets two notifications.
- **Compilations.** The stale outcome and executables are written. Evaluations
  are built from them, and the fresh compilation is probably lost to the
  executables' unique constraint.
- Under the new loop design (section 3), the dequeue can land before V's
  re-enqueue. Then `_enqueue_sync` sees R in the cache and does not push O,
  and the invalidation is silently undone.

User tests are not affected: `invalidate_submission` only builds COMPILATION
and EVALUATION operations (OPS 316-357).

### 1.3 The `archive_sandbox` twin

`get_relevant_operations` builds operations with `archive_sandbox=False`
(OPS 346-355). `invalidate_submission(archive_sandbox=True)` re-enqueues
`archive_sandbox=True` operations (ES 1570). `ESOperation.__eq__` includes
`archive_sandbox` (OPS 744-758), so a later invalidation neither dequeues nor
ignores those twins, and their late results get written.

## 2. Goals and non-goals

Goals:
- The event loop never waits on `post_finish_lock`.
- A late result of an invalidated operation is never written, whatever the
  timing: in the pool, in the queue, in the cache, or in a batch being flushed.
- No behavior change when no invalidation happens, beyond the loop no longer
  blocking.

Non-goals (after 2026-10-10):
- A thread-safe queue that removes the deferred-action machinery entirely
  (the best long-term design, but it touches the queue shared by every asyncio
  service).
- G1b: in the tombstone path (ES 1146-1160), evaluations of the same result
  still in the cache are written afterwards. The helper from section 5 can be
  reused.
- `EvaluationExecutor.__contains__` (ES 125-138) reads the queue and
  `_currently_executing` from threads without a lock.
- A free-threaded Python build. The design relies on the GIL for plain reads.

## 3. Design L1: lock-free loop actions, flushed per section

### 3.1 State (ES `__init__`, ~327-333)

- `self._pending_lock = threading.Lock()`: guards `_pending_operations`. It is
  a leaf lock: nothing is acquired while holding it.
- `self._loop_actions: list[Callable[[], None]] = []`: guarded by
  `post_finish_lock`.
- `self._post_finish_depth = 0`: guarded by `post_finish_lock`. It lives on
  the service, not in a lock wrapper, so tests that replace
  `post_finish_lock` keep working.

### 3.2 `with_post_finish_lock` (ES 243-254)

```python
def wrapped(self, *args, **kwargs):
    with self.post_finish_lock:
        self._post_finish_depth += 1
        try:
            return func(self, *args, **kwargs)
        finally:
            self._post_finish_depth -= 1
            if self._post_finish_depth == 0:
                self._flush_loop_actions()   # still holding the lock
```

The flush runs after the body's `SessionGen` has committed and closed, and
before the lock is released. Nested calls do not flush. In production the
decorator stays the only way `post_finish_lock` is acquired.

### 3.3 Helpers (called with `post_finish_lock` held)

- `_schedule_loop_action(action, flush_now=False)`: if `self._loop is None`
  (`__init__` time), run `action()` inline, as today. Otherwise append it, and
  call `_flush_loop_actions()` when `flush_now`.
- `_flush_loop_actions()`: if the buffer is non-empty, swap it for a new list
  and call `self._loop.call_soon_threadsafe(self._run_loop_actions, actions)`:
  one callback per flush.
- `_run_loop_actions(actions)`, on the loop: run each action in order; log any
  exception on the ES logger with `exc_info` and go on with the next.

### 3.4 Pending bookkeeping

- `_record_pending` and `_clear_pending_one` (ES 598-636): each body runs
  under `_pending_lock`.
- `_enqueue_sync` (ES 573-596): read the pending action under `_pending_lock`,
  release it, then check membership. Never take `d_lock` (inside
  `in self.result_cache`) while holding `_pending_lock`.
- Also in `_enqueue_sync`: always check `operation in self.result_cache`; keep
  the `"dequeue"` bypass only for `operation in self.get_executor()`. After the
  purge of section 5 a cached result of the operation is a live one, and not
  re-pushing is correct.

### 3.5 The two loop actions

- `_push_to_queue` / `_threadsafe_enqueue` (ES 638-671): `_do` no longer takes
  `post_finish_lock`. It pushes, then clears one pending mark in a `finally`.
  It is scheduled with `_schedule_loop_action(_do)`, so it waits in the buffer
  until the section ends.
- `_threadsafe_dequeue_and_ignore` (ES 673-714): see section 4.

## 4. Design B: the ignore takes effect on the calling thread

```python
self._record_pending(operation, "dequeue")
self._schedule_loop_action(_do, flush_now=True)  # dequeue + _currently_executing fallback + ignore + clear
try:
    self.get_executor().pool.ignore_operation(operation)  # synchronous, after scheduling
except LookupError:
    pass
```

- Schedule first, then ignore. The reverse order has a theoretical gap.
- `_do` still ignores on the loop. Duplicate entries in
  `_operations_to_ignore[shard]` are harmless: every consumer tests membership
  (ES 807, WP 496, WP 542), and the list is reset on release (WP 372) and on
  disable (WP 548).
- The new edge `post_finish_lock` → `_operation_lock` already exists
  (`release_worker` under the lock, ES 778 → WP 359).
- **Twins.** `_invalidate_submission_sync` dequeues and ignores each operation
  and also its `archive_sandbox=True` twin.

## 5. Design G1: purge and mark

### 5.1 `FlushingDict` (FD)

- Replace the single `fd` with `self._flushing: list[dict]`, the batches being
  flushed. Two overlapping flushes (tests call `flush()` while `_check_flush`
  also runs) no longer overwrite each other.
- `flush`: under `d_lock`, do `batch = self.d; self.d = {};
  self._flushing.append(batch); items = list(batch.items())`. Then await the
  callback. In `finally`, under `d_lock`, remove `batch` by identity. On
  callback failure: log and drop the batch, as today (FD 104-109).
- `__contains__`: `key in self.d or any(key in b for b in self._flushing)`.
- `fd`: read-only property returning the merged in-flight batches under
  `d_lock`, for the tests that read it.
- New `discard(predicate) -> list[ValueT]`: under `d_lock`, pop the matching
  keys from `d` and from every batch in `_flushing`, and return the popped
  values. The docstring says that values popped from an in-flight batch were
  already handed to the callback, so the caller must neutralize them.

### 5.2 ES

- `Result.__init__` (ES 263): `self.discarded = False`.
- New `_discard_cached_results(operations)`, called with `post_finish_lock`
  held. It builds keys `(type_, object_id, dataset_id, testcase_codename)`
  (with `type_`, so user tests with the same numeric id are safe, and without
  `archive_sandbox`, so twins are covered), calls
  `self.result_cache.discard(...)`, sets `discarded = True` on every popped
  Result, and logs at INFO how many were discarded.
- `_invalidate_submission_sync`: call it right after the dequeue loop
  (after ES 1523) and before the re-enqueue (ES 1569). If V later fails before
  committing, the purged results are lost and the sweeper recomputes them.
- `_write_results_sync` (ES 844), first line: drop the items whose Result is
  `discarded`, and log them. Filtering before grouping means no
  `compilation_ended` / `evaluation_ended` or notification runs for them.

### 5.3 Why both windows close

V, `_action_finished_sync` and `_write_results_sync` each run entirely under
`post_finish_lock`, so each is wholly before or wholly after V. For a result R
of an operation in V's scope:
1. R is in `d` when V runs: `discard` removes it under `d_lock`, atomically
   with respect to `flush`'s take. It is never written.
2. R is in an in-flight batch: `discard` finds it and marks it. The callback's
   list holds the same object. If that batch's write finished before V, V's DB
   invalidation removes the row. Otherwise the write runs after V, sees the
   mark and skips R.
3. R is reported after V: B put the operation in the ignore list, and
   `release_worker` drops the result (ES 807-808).
4. R reported during V: impossible, because of the lock.

With no invalidation, `discard` is never called and `discarded` stays False.

## 6. Invariants

- **I1 FIFO per operation.** The loop applies push and dequeue actions in the
  order `_record_pending` recorded them (lock order across threads, program
  order within a thread).
- **I2 Pending accuracy.** `_pending_operations[op]` = (last recorded action,
  number recorded but not yet applied). The entry is absent at 0. Only
  `post_finish_lock` holders add. The count goes down once per applied action,
  after its queue mutation. Every read-modify-write runs under `_pending_lock`.
- **I3 Dedup.** `_enqueue_sync` never pushes a second live copy of an
  operation.
- **I4 Late results of an invalidated operation are dropped**: in the pool
  (B), in the queue (the dequeue, which lands before any fresh dispatch), in
  the cache or in a flush batch (section 5).
- **I5 Returned ⇒ landed.** When `await` of `enqueue`, `new_submission`,
  `new_user_test`, `invalidate_submission`, `_missing_operations` or
  `write_results` completes (normally or by exception), every action recorded
  in its `_sync` body has been applied, before the awaiting coroutine resumes.
  Reason: the actions are handed over with `call_soon_threadsafe` before the
  body returns, `run_in_executor` delivers its result with a later
  `call_soon_threadsafe` (CPython 3.12 `asyncio/futures.py:396-405`), and the
  loop's ready queue is FIFO.
- **I5' Push after commit.** Pushes recorded in a section apply only after its
  body, commits included, has finished. Dequeues may apply earlier.
- **I6 No loop blocking.** Production code never acquires `post_finish_lock`
  on the loop thread. The loop takes only `_pending_lock`,
  `WorkerPool._operation_lock`, `_current_execution_lock` and `d_lock`, each
  for microseconds of bookkeeping.
- **I7 Lock order.** `post_finish_lock` first; then a DB connection,
  `_operation_lock`, `d_lock` or `_pending_lock`. `_current_execution_lock`
  before `_operation_lock`. `_pending_lock` is a leaf. Never take `d_lock`
  while holding `_pending_lock`. Never take `post_finish_lock` (except
  reentrantly) while holding a DB connection or another lock.
- **I8 Liveness.** Every recorded action is handed to the loop exactly once,
  before the recording section releases the lock (the flush is in a
  `finally`). No retry or polling loops.
- **I9 Queue on the loop.** `AsyncPriorityQueue`, `queue_status_cumulative`
  and `_currently_executing` are mutated only on the loop.
- **I10 Error isolation.** An exception in one loop action is logged on the ES
  logger; later actions still run; no pending entry stays stuck.
- **I11 Parity.** With no invalidation, batching into job groups and the order
  of log lines are the same as today.

The module note at ES 70-93 is updated with: the loop never takes
`post_finish_lock`; the per-section flush; the lock order of I7.

## 7. Tests

All deterministic: loop gates (`call_soon_threadsafe(event.wait)`) and thread
gates, no sleeps for correctness. Asyncio group.

New:
1. **Loop responsiveness.** A decorated section holds the lock for 1 s. A
   heartbeat task (10 ms sleeps) sees a maximum gap under 0.1 s;
   `queue_status()` answers during the hold; a pushed operation is not in the
   queue during the hold and is right after the await; a queued operation being
   dequeued is gone during the hold.
2. **The loop never takes `post_finish_lock`.** Extend the checking lock in
   `evaluationservice_lock_order_test.py` to record acquisitions on the loop
   thread; run every path there plus a full round trip and an invalidation;
   assert zero. Also assert `d_lock` is never taken while `_pending_lock` is
   held.
3. **In-flight race closed by B** (must fail without B). A worker holds its
   answers; `action_finished` is blocked on the lock while the invalidation
   holds it; the loop-side dequeue is gated. Exactly one "result ignored", the
   stale result is never cached, 2 jobs, and the DB result comes from the
   second run.
4. **Operation in `_currently_executing` at invalidation**: removed by the
   dequeue, never dispatched, re-pushed, pending ends empty.
5. **FIFO and pending.** Push→dequeue and dequeue→push in one section, and a
   double invalidation, with a loop gate: deterministic `_pending_operations`
   snapshots while gated; correct final membership and `{}` after.
6. **Returned ⇒ landed** for the six wrappers of I5 (pending `{}`, operations
   in the executor, and for the sweep "Found N" is the last
   `async_triggeredservice` log line).
7. **Batching unchanged.** A sweep finding 3 evaluations with one worker sends
   one job group with all 3.
8. **Error isolation.** A failing action logs an ES ERROR with `exc_info`; a
   later action still lands; pending ends `{}`.
9. **G1 window 1, evaluation.** Result cached (long flush latency), then
   invalidation: the operation is not in the cache, 2 jobs, one Evaluation
   from the second answer, no "Integrity error", one notification.
10. **G1 window 2.** A batch is taken by the flush while the lock is held;
    the invalidation runs; then the write: same assertions as 9.
11. **G1 window 1, compilation.** The compilation text is the second answer,
    no IntegrityError, one notification with the fresh outcome.
12. **Re-enqueue although the dequeue landed first.** The fresh operation is
    enqueued and the stale result is not written.
13. **`FlushingDict.discard`** removes pending and in-flight entries (the
    popped value is the same object as in the callback's list), and two
    overlapping flushes are both tracked by `__contains__`, `fd` and
    `discard`.
14. **Purge key.** With EVALUATION(s, d, t0, `archive_sandbox=True`),
    USER_TEST_EVALUATION(id == s, d) and COMPILATION(s, d) cached and
    `level="evaluation"`: only the first is purged and marked.
15. **`write_results` skips discarded Results**; with all discarded, nothing is
    written and nothing is notified.
16. **Twins.** An invalidation dequeues and ignores the `archive_sandbox=True`
    twin.

The 8 tests that failed with the first attempt:
- `failures_test::test_result_of_an_operation_invalidated_in_flight_is_ignored`,
  `twophase_e2e_test` (2 sweeper tests),
  `twophase_gate_consistency_test::test_sweeper_enqueues_it_when_two_phase_is_disabled`
  and the three `logs.output[-1]` sweep tests in `failures_test`: pass
  unchanged.
- `EvaluationService_test::test_enqueue_pending_when_dequeue_scheduled_right_after`:
  its middle `assertNotIn` used `post_finish_lock` as a stand-in for "the loop
  has not applied callbacks yet", which this design removes on purpose. Hold
  the loop with a gate instead (the technique already used at
  `EvaluationService_test.py:548-554`).

Stale comments to update: `evaluationservice_lock_order_test.py:217-219`,
`EvaluationService_test.py:271-273`. Tests that should get the same loop gate
to cover the in-flight path deterministically: `failures_test:885-890`,
`EvaluationService_test:663-668`. `_is_idle` helpers that read `cache.fd`
keep working through the property.

Regression: the whole `cmstestsuite/unit_tests/service/` suite (asyncio group;
the two gevent files in their own process), three times; then the full CI on
`ubuntu:noble` and `debian:bookworm`, one at a time.

## 8. Rollout

Subagent-driven execution on `es-loop-stall`, a review per task and a final
whole-branch review, then the two-image CI, merge into main and push before
2026-10-02, and a redeploy of cms-test for the rehearsal.
