# ES Lock-Free Loop Actions and Airtight Invalidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The EvaluationService (ES) event loop never waits on `post_finish_lock`, and a late result of an invalidated operation is never written, whatever the timing.

**Architecture:** The `_sync` methods keep running on executor threads under `post_finish_lock`, but they no longer make the loop take that lock: every queue push or dequeue becomes a "loop action" that is buffered and handed to the loop with one `call_soon_threadsafe` when the outermost decorated call (a "section") ends. `invalidate_submission` ignores the invalidated operations in the worker pool on its own thread (B), also drops their `archive_sandbox=True` twins, and purges and marks their results in the result cache, including the batches a flush has already taken (G1).

**Tech Stack:** Python 3.12, asyncio, `threading`, SQLAlchemy on PostgreSQL, `unittest.IsolatedAsyncioTestCase` run by pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-es-loop-actions-and-invalidation-design.md` (binding; read it first). Its line numbers refer to a1f5e242; the code is unchanged at c82952f4, where this plan starts.

## Global Constraints

- Work in `/var/home/areslolxd/Documentos/cms/.worktrees/es-loop-stall` (branch `es-loop-stall`). Never `cd` to the main checkout. Don't push.
- Python 3.12 venv of the worktree only: always `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/pyflakes`, never the system ones. If `.venv` is missing: `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[devel]" pyflakes`.
- Private test database, created once before Task 1 and dropped after the last task (no credentials in commands or logs). From the worktree root:

  ```bash
  DB_NAME="esloopactions$(date +%s)fortesting"
  CMS_CONFIG_COPY="/tmp/${DB_NAME}-cms.toml"
  cp /var/home/areslolxd/Documentos/cms/.superpowers/beta-worktree-archive/scratch/ctrl-cms.toml "$CMS_CONFIG_COPY"
  chmod 600 "$CMS_CONFIG_COPY"
  sed -i "s#/ctrlfortesting\"#/${DB_NAME}\"#" "$CMS_CONFIG_COPY"
  grep -c "/${DB_NAME}\"" "$CMS_CONFIG_COPY"   # must print 1
  export CMS_CONFIG="$CMS_CONFIG_COPY"
  .venv/bin/python - <<'EOF'
  import os, tomllib
  from sqlalchemy import create_engine, text
  from sqlalchemy.engine import make_url
  with open(os.environ["CMS_CONFIG"], "rb") as f:
      url = make_url(tomllib.load(f)["database"]["url"])
  engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
  with engine.connect() as connection:
      connection.execute(text('CREATE DATABASE "%s"' % url.database))
  print("created", url.database)
  EOF
  ```

  Every pytest command below needs `CMS_CONFIG` exported in the same shell. If your shell does not keep state between commands, prefix each command with `CMS_CONFIG=/tmp/<DB_NAME>-cms.toml`. When all tasks are done, drop the database with the same script, using `'DROP DATABASE "%s"'`, then `rm "$CMS_CONFIG_COPY"`.
- Run every pytest as `timeout --foreground --signal=ABRT 900 .venv/bin/pytest ...`. Never run a bare `pytest`, nor the gevent files in the same process as the asyncio ones. The two groups, each in its own process:
  - asyncio group: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/service --ignore=cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py --ignore=cmstestsuite/unit_tests/service/twophase_reenqueue_test.py -q`
  - gevent group: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py cmstestsuite/unit_tests/service/twophase_reenqueue_test.py -q` (expected: `5 passed`).
- Baseline before Task 1: the asyncio group gives `265 passed` (plus `19 subtests passed`). `ProxyServiceTest.py::TestProxyService::test_startup` is a known, unrelated timing flake (about 1 run in 20, on the untouched code too): if it alone fails, rerun; nothing in this plan touches ProxyService.
- Lock order (I7, verbatim from the spec): "`post_finish_lock` first; then a DB connection, `_operation_lock`, `d_lock` or `_pending_lock`. `_current_execution_lock` before `_operation_lock`. `_pending_lock` is a leaf. Never take `d_lock` while holding `_pending_lock`. Never take `post_finish_lock` (except reentrantly) while holding a DB connection or another lock."
- The event loop never takes `post_finish_lock` in production code (I6). Test code may, but no new test may rely on it.
- Parity (I11): with no invalidation, nothing changes beyond the loop not blocking: same pushes, same batching into job groups, same log lines in the same order.
- All code, names and comments in English. Docstrings follow `.github/CONTRIBUTING.md`: imperative first line, blank line, description, blank line, `arg: ...` lines, blank line, `return: ...`, blank line, `raise (Error): ...`, blank line before the closing quotes.
- `.venv/bin/pyflakes` must print nothing for every file a task touches (project-wide pyflakes has unrelated pre-existing warnings: only check the touched files). Lines stay under 80 characters.
- Commits: Conventional Commits, one per task, ending with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` after a blank line. Never amend, rebase or reset; fix mistakes with a new commit.
- Line numbers below are those of c82952f4, before any task. Earlier edits in the same file shift later lines: locate each change by the quoted code, not by the number.

## Review Focus

1. **An exception inside a section after it scheduled pushes** (e.g. a commit failing in `_new_submission_sync`): the pushes still land, the exception reaches the awaiting coroutine, `_pending_operations` ends `{}` and `_post_finish_depth` back at 0. Test: `test_actions_of_a_section_that_raises_still_land` (Task 2).
2. **Reentrant nested decorated calls** (`_write_results_sync` → `_enqueue_sync`): the inner call must not hand anything over; the outermost one hands everything over in one `call_soon_threadsafe`. Test: `test_nested_sections_hand_their_actions_over_once` (Task 2).
3. **No loop yet, or a loop already closed at shutdown:** with `_loop is None` (construction time, the gevent-group tests) actions run inline as today; with a closed loop the section returns its own result and a WARNING says how many actions were dropped. Tests: `test_actions_run_inline_without_a_loop` and `test_closed_loop_does_not_mask_the_section_result` (Task 2).
4. **A `write_results` batch where some Results of an object are discarded and others are not:** the live ones are written, the discarded ones are not, and an object left incomplete is neither finalized nor notified. Test: `test_write_results_skips_the_discarded_results` (Task 4).
5. **An invalidation limited to one testcase (`testcase_id`) while results of other testcases of the same submission are cached:** the purge covers every testcase of the submission (the operations come from `get_relevant_operations`, which ignores `testcase_id`), so the purged ones must be computed again and the submission must still end with exactly one evaluation per testcase and one notification. Test: `test_testcase_invalidation_recomputes_the_purged_results` (Task 4).

(An invalidation of a submission with no result in the DB yet is spec test 11, `test_cached_compilation_of_an_invalidated_operation_is_dropped`, Task 4.)

---

## File Structure

- `cms/service/flushingdict.py` (Task 1): in-flight batches become a list, `fd` becomes a read-only merged view, new `discard(predicate)`.
- `cms/service/EvaluationService.py`:
  - Task 2: the module note (lines 70-93), `with_post_finish_lock` (243-254), the new state in `__init__` (327-333), `_enqueue_sync` through `_threadsafe_dequeue_and_ignore` (573-714), plus the new `_schedule_loop_action`, `_flush_loop_actions`, `_run_loop_actions`.
  - Task 3: `_threadsafe_dequeue_and_ignore` (synchronous ignore) and the dequeue loop of `_invalidate_submission_sync` (1520-1523, twins).
  - Task 4: `Result.__init__` (263-265), `_enqueue_sync` (cache rule), new `_discard_cached_results`, the first lines of `_write_results_sync` (843-844), and one call in `_invalidate_submission_sync`.
- Tests, all in the asyncio group:
  - `cmstestsuite/unit_tests/service/flushingdict_test.py` (Task 1): spec test 13.
  - `cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py` (new, Task 2; one test added in Task 4): no database. Spec tests 1, 5 (one section each way) and 8, Review Focus 1-3, and the rule of spec 3.4.
  - `cmstestsuite/unit_tests/service/evaluationservice_lock_order_test.py` (Task 2): spec test 2, stale comment.
  - `cmstestsuite/unit_tests/service/EvaluationService_test.py` (Task 2): the adjusted `test_enqueue_pending_when_dequeue_scheduled_right_after`, spec test 5 (double invalidation), stale comment.
  - `cmstestsuite/unit_tests/service/evaluationservice_failures_test.py` (Tasks 2-4): its harness (real service, `ControllableWorker`, `_wait_until_idle`, notification counting) is the one every DB race test needs. Spec tests 6 and 7 plus the loop gate (Task 2); 3, 4, 16 and `RunLabellingWorker` (Task 3); 9, 10, 11, 12, 14, 15 and Review Focus 4-5 (Task 4).

## Where this plan refines the spec

1. **`_dequeued_in_section` (Task 2).** The dequeue is handed to the loop right away (`flush_now=True`, spec 4), so the loop usually applies it, and clears its pending entry, before the invalidation re-enqueues. The operation is then still in the worker pool (ignored, its worker has not answered), and `operation in self.get_executor()` made `_enqueue_sync` skip the push: the invalidation silently lost the operation. Without this set, `evaluationservice_failures_test.py::test_result_of_an_operation_invalidated_in_flight_is_ignored` and `EvaluationService_test.py::test_execute_does_not_deadlock_under_executor_pressure` fail. The set holds the operations the current section dequeued; `_enqueue_sync` treats them as it treats a pending `"dequeue"`; it is emptied when the section ends. It is the pool counterpart of the result-cache case of spec 1.2 (last bullet).
2. **I5' holds when a section's dequeues come before its pushes**, as in `_invalidate_submission_sync`. `flush_now` hands over the whole buffer (FIFO, I1), so a push recorded before a dequeue in the same section is applied early. No production section records a push before a dequeue.
3. **A closed loop (Task 2).** `_flush_loop_actions` catches the `RuntimeError` of `call_soon_threadsafe` on a closed loop and logs a WARNING, so that a section finishing during shutdown does not replace its own result or exception with that error.
4. **Spec test 12** passes as soon as the purge exists; the "always check `result_cache`" rule of spec 3.4 gets its own unit test (`test_cached_result_stops_the_push_even_with_a_dequeue_pending`, Task 4).
5. **Spec test 2** runs the six existing paths on one service and the round trip plus invalidation on a second one: the paths' fixture dataset has no task type, so batching its operations with the round trip's would lose the round trip's job group.

---

### Task 1: FlushingDict tracks every in-flight batch and can discard entries

**Files:**
- Modify: `cms/service/flushingdict.py:65-66` (the `fd` attribute), `cms/service/flushingdict.py:97-113` (`flush`, `__contains__`)
- Test: `cmstestsuite/unit_tests/service/flushingdict_test.py` (insert before `async def callback` at line 107)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `FlushingDict._flushing: list[dict[KeyT, ValueT]]`: the batches being flushed, in the order the flushes took them.
  - `FlushingDict.fd` (read-only property) `-> dict[KeyT, ValueT]`: a merged copy of every in-flight batch, taken under `d_lock`. Existing tests read `cache.fd`; they keep working.
  - `FlushingDict.discard(predicate: Callable[[KeyT], bool]) -> list[ValueT]`: under `d_lock`, pops every matching key from `d` and from every in-flight batch; returns the popped values (the same objects the callback's list holds).
  - `key in flushing_dict` is true for keys in `d` or in any in-flight batch.

Existing tests that must stay green: `flushingdict_test.py`, and (they read `cache.fd`) `evaluationservice_failures_test.py`, `twophase_e2e_test.py`, `EvaluationService_test.py`; then the whole asyncio and gevent groups.

- [ ] **Step 1: Write the failing tests**

In `cmstestsuite/unit_tests/service/flushingdict_test.py`, insert this block right before `    async def callback(self, data):` (line 107):

```python
    # -- in-flight batches and discard ------------------------------------

    def _make_unstarted_dict(self, callback):
        """Build a dict with no background flush task.

        Only the explicit flush() calls of the test flush it, so what is
        pending and what is in flight is up to the test alone.

        callback: the callback to flush to.

        return: the dict.

        """
        return FlushingDict(
            TestFlushingDict.SIZE, TestFlushingDict.FLUSH_LATENCY_SECONDS,
            callback)

    def _held_callback(self):
        """Return a callback that keeps every flush in flight until released.

        return: the callback, the list each call's items are appended
            to (as the callback received them), and the event that
            lets every call return.

        """
        calls: list[list] = []
        release = asyncio.Event()

        async def callback(items):
            calls.append(items)
            await release.wait()

        return callback, calls, release

    async def _wait_for_calls(self, calls: list, count: int):
        """Let the loop run until the callback has been called count times.

        calls: the list the held callback appends to.
        count: how many calls to wait for.

        """
        for _ in range(100):
            if len(calls) >= count:
                return
            await asyncio.sleep(0)
        self.fail("The callback was called %d time(s), not %d."
                  % (len(calls), count))

    async def test_discard_removes_pending_entries(self):
        d = self._make_unstarted_dict(self.callback)
        kept, dropped = object(), object()
        d.add("kept", kept)
        d.add("dropped", dropped)

        removed = d.discard(lambda key: key == "dropped")

        self.assertEqual(len(removed), 1)
        self.assertIs(removed[0], dropped)
        self.assertNotIn("dropped", d)
        self.assertIn("kept", d)
        await d.flush()
        self.assertEqual(self.received_data, [[("kept", kept)]])

    async def test_discard_removes_in_flight_entries(self):
        callback, calls, release = self._held_callback()
        d = self._make_unstarted_dict(callback)
        value, other = object(), object()
        d.add("key", value)
        d.add("other", other)
        flush = asyncio.create_task(d.flush())
        await self._wait_for_calls(calls, 1)
        self.assertIn("key", d)
        self.assertEqual(d.fd, {"key": value, "other": other})

        removed = d.discard(lambda key: key == "key")

        # The very object the callback got: marking it reaches the write.
        self.assertEqual(len(removed), 1)
        self.assertIs(removed[0], value)
        self.assertIs(dict(calls[0])["key"], value)
        self.assertNotIn("key", d)
        self.assertEqual(d.fd, {"other": other})
        release.set()
        await flush
        self.assertEqual(d.fd, {})
        self.assertEqual(d._flushing, [])

    async def test_overlapping_flushes_are_both_tracked(self):
        callback, calls, release = self._held_callback()
        d = self._make_unstarted_dict(callback)
        first, second = object(), object()
        d.add("first", first)
        first_flush = asyncio.create_task(d.flush())
        await self._wait_for_calls(calls, 1)
        d.add("second", second)
        second_flush = asyncio.create_task(d.flush())
        await self._wait_for_calls(calls, 2)

        # The second flush did not hide the first one's batch.
        self.assertEqual(len(d._flushing), 2)
        self.assertIn("first", d)
        self.assertIn("second", d)
        self.assertEqual(d.fd, {"first": first, "second": second})
        removed = d.discard(lambda key: True)
        self.assertCountEqual([id(value) for value in removed],
                              [id(first), id(second)])
        self.assertNotIn("first", d)
        self.assertNotIn("second", d)

        release.set()
        await asyncio.gather(first_flush, second_flush)
        self.assertEqual(d._flushing, [])
        self.assertEqual(d.fd, {})

    async def test_failed_flush_drops_its_batch(self):
        async def failing_callback(items):
            raise RuntimeError("cannot write")

        d = self._make_unstarted_dict(failing_callback)
        d.add("key", object())

        with self.assertLogs(
                "cms.service.flushingdict", level="ERROR") as logs:
            await d.flush()

        self.assertEqual([record.getMessage() for record in logs.records],
                         ["Unexpected error while flushing."])
        self.assertNotIn("key", d)
        self.assertEqual(d._flushing, [])
        self.assertEqual(d.fd, {})

```

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/service/flushingdict_test.py -q`
Expected: `4 failed, 5 passed`; the failures are `AttributeError: 'FlushingDict' object has no attribute 'discard'` (or `'_flushing'`).

- [ ] **Step 3: Implement**

In `cms/service/flushingdict.py`, replace lines 65-66:

```python
        # This contains all the key-values that are currently being flushed
        self.fd: dict[KeyT, ValueT] = dict()
```

with:

```python
        # The batches of key-values currently being flushed, in the
        # order the flushes took them. A list, since two flushes can
        # overlap (e.g. an explicit flush() while the background one is
        # still writing): each one must stay visible to __contains__,
        # fd and discard() until its own callback returns.
        self._flushing: list[dict[KeyT, ValueT]] = []
```

Then replace `flush` and `__contains__` (lines 97-113, from `    async def flush(self):` to `            return key in self.d or key in self.fd`) with:

```python
    async def flush(self):
        logger.debug("Flushing items")
        with self.d_lock:
            batch = self.d
            self.d = dict()
            self._flushing.append(batch)
            items = list(batch.items())
        try:
            await self.callback(items)
        except Exception:
            # Otherwise the background flush task would die silently,
            # leaving the batch in self._flushing forever.
            logger.error("Unexpected error while flushing.", exc_info=True)
        finally:
            with self.d_lock:
                # By identity: two batches may compare equal (e.g. both
                # emptied by discard()).
                self._flushing = [
                    flushing for flushing in self._flushing
                    if flushing is not batch]

    @property
    def fd(self) -> dict[KeyT, ValueT]:
        """Return the key-values currently being flushed.

        A merged copy of every batch in flight, for callers that only
        look at it (e.g. tests checking that nothing is left in flight).

        return: the key-values of every flush in progress.

        """
        with self.d_lock:
            merged: dict[KeyT, ValueT] = {}
            for batch in self._flushing:
                merged.update(batch)
            return merged

    def discard(self, predicate: Callable[[KeyT], bool]) -> list[ValueT]:
        """Remove the key-values whose key matches a predicate.

        Both the key-values not flushed yet and the ones in a batch
        being flushed are removed. The values removed from a batch
        being flushed were already handed to the callback (its list of
        items is built when the flush starts), so the callback may
        still process them: the caller must neutralize them, e.g. by
        marking them so that the callback skips them.

        predicate: called with each key; True means remove it.

        return: the values removed, pending ones first.

        """
        removed: list[ValueT] = []
        with self.d_lock:
            for mapping in [self.d, *self._flushing]:
                for key in [key for key in mapping if predicate(key)]:
                    removed.append(mapping.pop(key))
        return removed

    def __contains__(self, key):
        with self.d_lock:
            return key in self.d or any(
                key in batch for batch in self._flushing)
```

`Callable` is already imported (`from collections.abc import Awaitable, Callable`, line 20).

- [ ] **Step 4: Run the tests to see them pass**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/service/flushingdict_test.py -q`
Expected: `9 passed`.

Run: `.venv/bin/pyflakes cms/service/flushingdict.py cmstestsuite/unit_tests/service/flushingdict_test.py`
Expected: no output.

Run the asyncio group (see Global Constraints). Expected: `269 passed`.
Run the gevent group. Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add cms/service/flushingdict.py cmstestsuite/unit_tests/service/flushingdict_test.py
git commit -m "feat(flushingdict): track overlapping flushes and add discard()

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Loop actions applied on the loop without post_finish_lock (L1)

**Files:**
- Modify: `cms/service/EvaluationService.py:33-38` (imports), `:70-93` (module note), `:243-254` (`with_post_finish_lock`), `:329-333` (state in `__init__`), `:573-714` (`_enqueue_sync` through `_threadsafe_dequeue_and_ignore`)
- Create: `cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py`
- Modify: `cmstestsuite/unit_tests/service/evaluationservice_lock_order_test.py` (docstring 36-39, imports 49-56, 59-91, 94, 170-171, 190-227)
- Modify: `cmstestsuite/unit_tests/service/EvaluationService_test.py` (5-6, 269-275, 607, 628-644, 663-674)
- Modify: `cmstestsuite/unit_tests/service/evaluationservice_failures_test.py` (885-890; new tests before line 943)

**Interfaces:**
- Consumes: nothing from Task 1 (the `fd` property keeps `_is_idle` helpers working).
- Produces (all on `EvaluationService`, all used by Tasks 3 and 4):
  - `with_post_finish_lock(func)`: the outermost decorated call is a section; at its end (normally or by exception, still holding the lock) it empties `_dequeued_in_section` and calls `_flush_loop_actions()`.
  - `_pending_lock: threading.Lock` (leaf), guarding `_pending_operations`.
  - `_loop_actions: list[Callable[[], None]]`, `_post_finish_depth: int`, `_dequeued_in_section: set[ESOperation]`: guarded by `post_finish_lock`.
  - `_schedule_loop_action(action: Callable[[], None], flush_now: bool = False) -> None`: runs `action()` inline if `self._loop is None`; else buffers it, and flushes if `flush_now`.
  - `_flush_loop_actions() -> None`: one `self._loop.call_soon_threadsafe(self._run_loop_actions, actions)` per non-empty buffer; logs `"Event loop closed, dropping %d queue action(s)."` at WARNING on a closed loop.
  - `_run_loop_actions(actions: list[Callable[[], None]]) -> None`: runs each action; an exception is logged as `"Unexpected error in a queue action."` at ERROR with `exc_info`, and the next action still runs.
  - `_threadsafe_dequeue_and_ignore(operation)`: records `"dequeue"`, adds the operation to `_dequeued_in_section`, schedules its loop action with `flush_now=True`.
  - Test helper `_loop_held()` (context manager, used on an executor thread): `loop.call_soon_threadsafe(release.wait, 10)` on entry, `release.set()` on exit. Defined in the new test file (method) and in `EvaluationService_test.py` (static method taking the loop).

Existing tests that must stay green: the whole asyncio group, including the eight that failed with the first attempt (spec 7): `evaluationservice_failures_test.py::test_result_of_an_operation_invalidated_in_flight_is_ignored`, `::test_sweep_finds_what_es_was_never_told_about`, `::test_sweeper_recovers_a_compilation_lost_to_an_rpc_error`, `::test_sweeper_recovers_evaluations_lost_to_an_rpc_error`, `twophase_e2e_test.py::test_sweeper_enqueues_the_compilation_of_an_uncompiled_one`, `::test_sweeper_of_a_contest_ignores_the_other_contests`, `twophase_gate_consistency_test.py::test_sweeper_enqueues_it_when_two_phase_is_disabled` (all unchanged), and `EvaluationService_test.py::test_enqueue_pending_when_dequeue_scheduled_right_after` (adjusted in Step 4). The gevent group too: `twophase_reenqueue_test.py` builds ES with no loop and relies on the inline path.

- [ ] **Step 1: Create the loop actions test file**

Create `cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py`:

```python
#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Tests of the loop actions of EvaluationService.

The queue of EvaluationService may only be touched on the event loop,
so the _sync methods (running on executor threads, holding
post_finish_lock) record each push or dequeue in _pending_operations
and hand it to the loop as a "loop action". The actions of a section
(the outermost @with_post_finish_lock call) are handed over together
when it ends, and the loop applies them without ever taking
post_finish_lock.

No database is needed: only operations with made-up ids are queued,
and the only worker is an unconnected placeholder, so the executor
keeps the first operation it pops and never dispatches anything.

This file is asyncio-only: it must never share a process with the
gevent-based twophase files (see docker/_cms-test-internal.sh).

"""

import asyncio
import contextlib
import threading
import time
import unittest
from unittest.mock import patch

from cms.conf import Address, ServiceCoord
from cms.io.async_triggeredservice import AsyncTriggeredService
from cms.io.priorityqueue import PriorityQueue
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, \
    with_post_finish_lock
from cmscommon.datetime import make_datetime
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


# How long the long section of the responsiveness test keeps the lock.
LOCK_HOLD_SECONDS = 1.0
# The longest the loop may go without running the heartbeat meanwhile.
MAX_LOOP_GAP_SECONDS = 0.1

HIGH = PriorityQueue.PRIORITY_HIGH


class TestEvaluationServiceLoopActions(
    ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase,
):

    async def asyncSetUp(self):
        # See EvaluationService_test.py's asyncSetUp for why these are
        # needed: no real Worker, LogService or ScoringService is
        # reachable.
        local_address = Address("127.0.0.1", 0)
        for patcher in (
            patch("cms.util.config.services", {}),
            patch("cms.io.async_service.get_service_address",
                  return_value=local_address),
            patch("cms.io.async_rpc.get_service_address",
                  return_value=local_address),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        with patch.object(
                EvaluationService, "start_sweeper",
                lambda self, timeout: None):
            self.service = EvaluationService(0)
        self.loop = asyncio.get_running_loop()
        self.service._loop = self.loop
        self.addCleanup(self.service._disconnect_all)
        # An unconnected placeholder: the executor pops the first
        # operation and keeps it in _currently_executing forever, so the
        # later ones stay in the queue.
        self.service.get_executor().pool.add_worker(ServiceCoord("Worker", 0))
        self.executor = self.service.get_executor()
        self.timestamp = make_datetime()

    async def asyncTearDown(self):
        for task in list(self.service._background_tasks):
            task.cancel()

    @staticmethod
    def _operation(object_id: int) -> ESOperation:
        """Return a compilation operation with a made-up submission id."""
        return ESOperation(ESOperation.COMPILATION, object_id, 1)

    async def _wait_until(self, predicate, timeout: float = 5.0):
        """Let the loop run until predicate() is true, failing on timeout.

        predicate: a zero-argument callable to poll.
        timeout: how many seconds to poll for.

        """
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("Timed out waiting for %s." % predicate)
            await asyncio.sleep(0.01)

    async def _run_in_thread(self, func, *args):
        """Run func on an executor thread, like the service's _sync methods.

        func: the callable to run.
        args: its arguments.

        return: what func returned.

        """
        return await asyncio.wait_for(
            self.loop.run_in_executor(None, func, *args), timeout=10)

    @contextlib.contextmanager
    def _loop_held(self):
        """Keep the event loop from running anything new, from a thread.

        Everything handed to the loop while inside (loop actions
        included) waits behind the gate, in order, until the block is
        left. Only for use on an executor thread: the loop thread would
        block itself.

        """
        release = threading.Event()
        self.loop.call_soon_threadsafe(release.wait, 10)
        try:
            yield
        finally:
            release.set()

    async def _stall_queue(self):
        """Keep the executor busy, so later operations stay in the queue."""
        busy_operation = self._operation(1000)
        self.assertTrue(await self.service.enqueue(
            busy_operation, HIGH, self.timestamp))
        await self._wait_until(
            lambda: busy_operation in self.executor._currently_executing)

    # -- the loop never waits for a section ------------------------------

    async def test_loop_stays_responsive_during_a_long_section(self):
        await self._stall_queue()
        queued = self._operation(1)
        pushed = self._operation(2)
        self.assertTrue(await self.service.enqueue(
            queued, HIGH, self.timestamp))
        self.assertIn(queued, self.executor._operation_queue)
        in_section = threading.Event()
        leave_section = threading.Event()

        @with_post_finish_lock
        def long_section(service):
            # Like an invalidation: dequeue something, queue something,
            # then keep working (committing, say).
            service._threadsafe_dequeue_and_ignore(queued)
            self.assertTrue(service._enqueue_sync(
                pushed, HIGH, self.timestamp))
            in_section.set()
            leave_section.wait(timeout=5)

        max_gap = 0.0
        stop = asyncio.Event()

        async def heartbeat():
            nonlocal max_gap
            last = time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.monotonic()
                max_gap = max(max_gap, now - last - 0.01)
                last = now

        heartbeat_task = asyncio.create_task(heartbeat())
        section = asyncio.ensure_future(
            self.loop.run_in_executor(None, long_section, self.service))
        try:
            await self._wait_until(in_section.is_set)
            await asyncio.sleep(LOCK_HOLD_SECONDS)
            # The lock is still held: the loop answers RPCs, the dequeue
            # has been applied, the push waits for the end of the section.
            status_during = self.service.queue_status()
            queued_during = queued in self.executor
            pushed_during = pushed in self.executor
        finally:
            leave_section.set()
            await asyncio.wait_for(section, timeout=10)
            stop.set()
            await heartbeat_task

        self.assertEqual(status_during, [])
        self.assertFalse(queued_during)
        self.assertFalse(pushed_during)
        # Landed as soon as the await returned, without waiting.
        self.assertIn(pushed, self.executor._operation_queue)
        self.assertEqual(self.service._pending_operations, {})
        self.assertLess(max_gap, MAX_LOOP_GAP_SECONDS)

    # -- order and pending bookkeeping -----------------------------------

    async def test_push_then_dequeue_in_one_section(self):
        await self._stall_queue()
        operation = self._operation(1)

        @with_post_finish_lock
        def push_then_dequeue(service):
            self.assertTrue(service._enqueue_sync(
                operation, HIGH, self.timestamp))
            after_push = dict(service._pending_operations)
            service._threadsafe_dequeue_and_ignore(operation)
            after_dequeue = dict(service._pending_operations)
            return after_push, after_dequeue

        def body():
            with self._loop_held():
                after_push, after_dequeue = push_then_dequeue(self.service)
                # The section is over, the loop has not run its actions.
                after_section = dict(self.service._pending_operations)
                queued_after_section = \
                    operation in self.executor._operation_queue
            return after_push, after_dequeue, after_section, \
                queued_after_section

        after_push, after_dequeue, after_section, queued_after_section = \
            await self._run_in_thread(body)

        self.assertEqual(after_push, {operation: ("push", 1)})
        self.assertEqual(after_dequeue, {operation: ("dequeue", 2)})
        self.assertEqual(after_section, {operation: ("dequeue", 2)})
        self.assertFalse(queued_after_section)
        self.assertNotIn(operation, self.executor)
        self.assertEqual(self.service._pending_operations, {})

    async def test_dequeue_then_push_in_one_section(self):
        await self._stall_queue()
        operation = self._operation(1)
        self.assertTrue(await self.service.enqueue(
            operation, HIGH, self.timestamp))
        self.assertIn(operation, self.executor._operation_queue)

        @with_post_finish_lock
        def dequeue_then_push(service):
            service._threadsafe_dequeue_and_ignore(operation)
            after_dequeue = dict(service._pending_operations)
            queued_after_dequeue = operation in self.executor._operation_queue
            self.assertTrue(service._enqueue_sync(
                operation, HIGH, self.timestamp))
            after_push = dict(service._pending_operations)
            return after_dequeue, queued_after_dequeue, after_push

        def body():
            with self._loop_held():
                return dequeue_then_push(self.service)

        after_dequeue, queued_after_dequeue, after_push = \
            await self._run_in_thread(body)

        self.assertEqual(after_dequeue, {operation: ("dequeue", 1)})
        # The loop is held: the dequeue has not been applied yet.
        self.assertTrue(queued_after_dequeue)
        self.assertEqual(after_push, {operation: ("push", 2)})
        self.assertIn(operation, self.executor._operation_queue)
        self.assertEqual(self.service._pending_operations, {})

    async def test_push_after_a_dequeue_applied_during_the_section(self):
        # An invalidation's dequeue is handed to the loop right away, so
        # the loop may apply it before the invalidation re-enqueues. The
        # worker still holding the operation (ignored from then on) must
        # not stop the push.
        operation = self._operation(1)
        pool = self.executor.pool
        pool._add_operations(0, [operation])
        dequeue_applied = threading.Event()
        real_clear_pending_one = self.service._clear_pending_one

        def clear_pending_one_and_signal(cleared: ESOperation):
            real_clear_pending_one(cleared)
            dequeue_applied.set()

        self.service._clear_pending_one = clear_pending_one_and_signal

        @with_post_finish_lock
        def invalidate(service):
            service._threadsafe_dequeue_and_ignore(operation)
            self.assertTrue(dequeue_applied.wait(timeout=10))
            pending = dict(service._pending_operations)
            return pending, service._enqueue_sync(
                operation, HIGH, self.timestamp)

        pending, pushed = await self._run_in_thread(invalidate, self.service)

        self.assertEqual(pending, {})
        self.assertTrue(pushed)
        self.assertIn(operation, pool._operations_to_ignore[0])
        self.assertTrue(operation in self.executor._operation_queue
                        or operation in self.executor._currently_executing)
        self.assertEqual(self.service._pending_operations, {})
        self.assertEqual(self.service._dequeued_in_section, set())

    # -- failures ---------------------------------------------------------

    async def test_failing_action_is_logged_and_the_next_one_runs(self):
        failing = self._operation(1)
        later = self._operation(2)
        real_enqueue = AsyncTriggeredService.enqueue

        def failing_enqueue(service, operation, priority, timestamp):
            if operation == failing:
                raise RuntimeError("enqueue failed")
            return real_enqueue(service, operation, priority, timestamp)

        @with_post_finish_lock
        def enqueue_both(service):
            for operation in (failing, later):
                self.assertTrue(service._enqueue_sync(
                    operation, HIGH, self.timestamp))

        with patch.object(AsyncTriggeredService, "enqueue", failing_enqueue), \
                self.assertLogs("cms.service.EvaluationService",
                                level="ERROR") as logs:
            await self._run_in_thread(enqueue_both, self.service)

        self.assertEqual(len(logs.records), 1)
        self.assertIsNotNone(logs.records[0].exc_info)
        self.assertEqual(str(logs.records[0].exc_info[1]), "enqueue failed")
        self.assertIn(later, self.executor)
        self.assertNotIn(failing, self.executor)
        self.assertEqual(self.service._pending_operations, {})

    async def test_actions_of_a_section_that_raises_still_land(self):
        operation = self._operation(1)

        @with_post_finish_lock
        def push_then_fail(service):
            self.assertTrue(service._enqueue_sync(
                operation, HIGH, self.timestamp))
            raise RuntimeError("commit failed")

        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            await self._run_in_thread(push_then_fail, self.service)

        self.assertIn(operation, self.executor)
        self.assertEqual(self.service._pending_operations, {})
        self.assertEqual(self.service._loop_actions, [])
        self.assertEqual(self.service._post_finish_depth, 0)

    # -- sections --------------------------------------------------------

    async def test_nested_sections_hand_their_actions_over_once(self):
        first = self._operation(1)
        second = self._operation(2)
        handovers: list[int] = []
        real_run_loop_actions = self.service._run_loop_actions

        def recording_run_loop_actions(actions):
            handovers.append(len(actions))
            real_run_loop_actions(actions)

        self.service._run_loop_actions = recording_run_loop_actions

        @with_post_finish_lock
        def outer(service):
            # _enqueue_sync is itself decorated: a nested section.
            self.assertTrue(service._enqueue_sync(
                first, HIGH, self.timestamp))
            buffered = len(service._loop_actions)
            self.assertTrue(service._enqueue_sync(
                second, HIGH, self.timestamp))
            return buffered

        buffered = await self._run_in_thread(outer, self.service)

        # The inner section did not hand its push over on its own.
        self.assertEqual(buffered, 1)
        self.assertEqual(handovers, [2])
        self.assertIn(first, self.executor)
        self.assertIn(second, self.executor)
        self.assertEqual(self.service._post_finish_depth, 0)

    async def test_actions_run_inline_without_a_loop(self):
        # As at __init__ time, before run() has set the loop.
        self.service._loop = None
        operation = self._operation(1)

        @with_post_finish_lock
        def push_then_dequeue(service):
            self.assertTrue(service._enqueue_sync(
                operation, HIGH, self.timestamp))
            queued_after_push = operation in self.executor._operation_queue
            service._threadsafe_dequeue_and_ignore(operation)
            queued_after_dequeue = \
                operation in self.executor._operation_queue
            return queued_after_push, queued_after_dequeue

        # Called on the loop's own thread, synchronously, as __init__ is.
        queued_after_push, queued_after_dequeue = \
            push_then_dequeue(self.service)

        self.assertTrue(queued_after_push)
        self.assertFalse(queued_after_dequeue)
        self.assertEqual(self.service._loop_actions, [])
        self.assertEqual(self.service._pending_operations, {})

    async def test_closed_loop_does_not_mask_the_section_result(self):
        # A section ending while ES shuts down, after the loop is closed.
        closed_loop = asyncio.new_event_loop()
        closed_loop.close()
        self.service._loop = closed_loop
        operation = self._operation(1)

        with self.assertLogs("cms.service.EvaluationService",
                             level="WARNING") as logs:
            self.assertTrue(self.service._enqueue_sync(
                operation, HIGH, self.timestamp))

        self.assertEqual(
            [record.getMessage() for record in logs.records],
            ["Event loop closed, dropping 1 queue action(s)."])
        self.assertEqual(self.service._loop_actions, [])
        self.assertEqual(self.service._post_finish_depth, 0)
        self.assertNotIn(operation, self.executor)


if __name__ == "__main__":
    unittest.main()
```

Maps to the spec: test 1 is `test_loop_stays_responsive_during_a_long_section`; test 5 (one section each way) is `test_push_then_dequeue_in_one_section` and `test_dequeue_then_push_in_one_section`; test 8 is `test_failing_action_is_logged_and_the_next_one_runs`; Review Focus 1-3 are the last four tests; `test_push_after_a_dequeue_applied_during_the_section` pins `_dequeued_in_section`.

- [ ] **Step 2: Extend the lock order test (spec test 2) and fix its stale comment**

In `cmstestsuite/unit_tests/service/evaluationservice_lock_order_test.py`:

1. In the module docstring, replace

```python
holding at least one connection. Each ES path that touches both the
database and the lock is then exercised sequentially.

"""
```

with

```python
holding at least one connection. Each ES path that touches both the
database and the lock is then exercised sequentially.

The same checking lock also pins the rule that the event loop never
takes post_finish_lock (it would freeze every RPC while a _sync method
works), and a checking _pending_lock pins that FlushingDict.d_lock is
never taken while _pending_lock is held.

"""
```

2. Replace the imports block (lines 49-56)

```python
import cms.db
from cms.conf import Address, ServiceCoord
from cms.grading.Job import CompilationJob
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, Result
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin
```

with

```python
import cms.db
from cms.conf import Address, ServiceCoord
from cms.grading.Job import CompilationJob
from cms.io.async_rpc import AsyncRemoteServiceClient
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, Result
from cms.service.workerpool import WorkerPool
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin
from cmstestsuite.unit_tests.service.EvaluationService_test import \
    FakeScoringService, FakeWorker, _start_server
```

3. In `LockOrderCheckingRLock`, replace `__init__` and `acquire` (lines 69-82) with:

```python
    def __init__(
        self, connections_held: threading.local, loop_thread_id: int
    ):
        self._lock = threading.RLock()
        self._connections_held = connections_held
        self._loop_thread_id = loop_thread_id
        self.violations: list[list[str]] = []
        self.acquisitions = 0
        # The stack of every acquisition made on the event loop thread.
        self.loop_acquisitions: list[list[str]] = []

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if threading.get_ident() == self._loop_thread_id:
            self.loop_acquisitions.append(
                [frame.name for frame in traceback.extract_stack()])
        if not self._lock._is_owned():
            self.acquisitions += 1
            if getattr(self._connections_held, "count", 0) > 0:
                self.violations.append([
                    frame.name for frame in traceback.extract_stack()
                    if frame.filename.endswith("EvaluationService.py")])
        return self._lock.acquire(blocking, timeout)
```

4. Insert right before `class TestPostFinishLockOrder(` (line 94):

```python
class PendingLockTracker:
    """A stand-in for _pending_lock that knows which thread holds it."""

    def __init__(self):
        self._lock = threading.Lock()
        self._holder: int | None = None

    def held_by_current_thread(self) -> bool:
        return self._holder == threading.get_ident()

    def __enter__(self) -> bool:
        self._lock.acquire()
        self._holder = threading.get_ident()
        return True

    def __exit__(self, *exc_info):
        self._holder = None
        self._lock.release()


class DLockUnderPendingLockDetector:
    """A stand-in for FlushingDict.d_lock recording forbidden acquisitions.

    Taking d_lock while holding _pending_lock is a violation: d_lock
    may be held while post_finish_lock holders wait for _pending_lock.

    """

    def __init__(self, pending_lock: PendingLockTracker):
        self._lock = threading.RLock()
        self._pending_lock = pending_lock
        self.violations: list[list[str]] = []

    def __enter__(self) -> bool:
        if self._pending_lock.held_by_current_thread():
            self.violations.append(
                [frame.name for frame in traceback.extract_stack()])
        return self._lock.acquire()

    def __exit__(self, *exc_info):
        self._lock.release()


```

5. In `_build_service`, replace

```python
        service.post_finish_lock = LockOrderCheckingRLock(
            self.connections_held)
```

with

```python
        service.post_finish_lock = LockOrderCheckingRLock(
            self.connections_held, threading.get_ident())
```

6. Replace the whole `test_lock_is_taken_before_a_connection` (lines 190-227, up to the two blank lines before `if __name__`) with:

```python
    def _paths(self, service: EvaluationService) -> dict:
        """Return every ES path that takes post_finish_lock, by name.

        service: the service to call.

        return: a dict from the name of each path to a zero-argument
            callable returning the coroutine that runs it.

        """
        compilation = ESOperation(
            ESOperation.COMPILATION, self.old_submission_id, self.dataset_id)
        return {
            "new_submission": lambda: service.new_submission(
                self.new_submission_id),
            "new_user_test": lambda: service.new_user_test(
                self.user_test_id),
            "write_results": lambda: service.write_results(
                [(compilation, self._make_compilation_result(compilation))]),
            "invalidate_submission": lambda: service.invalidate_submission(
                submission_id=self.old_submission_id, level="compilation"),
            "_missing_operations": service._missing_operations,
            "enqueue": lambda: service.enqueue(
                ESOperation(ESOperation.COMPILATION, self.new_submission_id,
                            self.dataset_id),
                1, self.new_submission_timestamp),
        }

    async def test_lock_is_taken_before_a_connection(self):
        service = self._build_service()
        lock = service.post_finish_lock

        violations_by_path = {}
        for name, start in self._paths(service).items():
            acquisitions_before = lock.acquisitions
            violations_before = len(lock.violations)
            # The loop actions of the path have run once this returns.
            await start()
            self.assertGreater(
                lock.acquisitions, acquisitions_before,
                "%s never acquired post_finish_lock" % name)
            violations_by_path[name] = lock.violations[violations_before:]

        # Only paths that inverted the order have a non-empty entry.
        self.assertEqual(
            {name: v for name, v in violations_by_path.items() if v}, {})

    async def _connect_peer(
        self, service: EvaluationService, coord: ServiceCoord,
        local_service: object,
    ) -> AsyncRemoteServiceClient:
        """Wire a real, connected peer for coord.

        service: the service to register the peer in.
        coord: the coord to register the peer under.
        local_service: object exposing the RPC methods to serve.

        return: the connected client.

        """
        server, port = await _start_server(local_service)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        client = AsyncRemoteServiceClient(coord)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        client.initialize_streams(reader, writer, plus=None)
        run_task = asyncio.create_task(client.run())
        self.addCleanup(run_task.cancel)
        self.addCleanup(client.disconnect)
        service.remote_services[coord] = client
        return client

    async def _wait_for(self, predicate, what: str, timeout: float = 15.0):
        """Poll predicate() until it is true, failing the test on timeout.

        predicate: a zero-argument callable to poll.
        what: what is being waited for, for the failure message.
        timeout: how long to wait, in seconds.

        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() > deadline:
                self.fail("Timed out after %gs waiting for %s." %
                          (timeout, what))
            await asyncio.sleep(0.02)

    def _build_instrumented_service(
        self,
    ) -> tuple[EvaluationService, DLockUnderPendingLockDetector]:
        """Build a service whose three locks record forbidden acquisitions.

        return: the service (its post_finish_lock is a
            LockOrderCheckingRLock) and the detector standing in for its
            result cache's d_lock.

        """
        service = self._build_service()
        self.addCleanup(
            lambda: [task.cancel() for task in service._background_tasks])
        pending_lock = PendingLockTracker()
        service._pending_lock = pending_lock
        d_lock = DLockUnderPendingLockDetector(pending_lock)
        service.result_cache.d_lock = d_lock
        return service, d_lock

    async def test_loop_never_takes_post_finish_lock(self):
        # Every path of the other test, on a service of its own: the
        # fixture's dataset has no task type, so a worker could not
        # compile what they queue.
        paths_service, paths_d_lock = self._build_instrumented_service()
        for start in self._paths(paths_service).values():
            await start()

        # A full round trip on another service, with a real worker and
        # ScoringService: new_submission, the dispatch, the answer, the
        # write and the notification. Then an invalidation of the same
        # submission, and its own round trip.
        service, d_lock = self._build_instrumented_service()
        worker = FakeWorker(compilation_success=False)
        pool: WorkerPool = service.get_executor().pool
        pool._worker[0] = await self._connect_peer(
            service, ServiceCoord("Worker", 0), worker)
        scoring = FakeScoringService()
        service.scoring_service = await self._connect_peer(
            service, ServiceCoord("ScoringService", 0), scoring)
        # The fixture's objects are detached by now: build new ones.
        contest = self.add_contest()
        participation = self.add_participation(contest=contest)
        task = self.add_task(contest=contest)
        dataset = self.add_dataset(
            task=task, task_type="Batch",
            task_type_parameters=["alone", ["", ""], "diff"])
        task.active_dataset = dataset
        submission = self.add_submission(task, participation)
        self.session.commit()
        key = (submission.id, dataset.id)
        compilation = ESOperation(ESOperation.COMPILATION, *key)
        self.session.close()

        await service.new_submission(key[0])
        await self._wait_for(lambda: compilation in service.result_cache,
                             "the first result to reach the cache")
        await service.result_cache.flush()
        await service.invalidate_submission(
            submission_id=key[0], level="compilation")
        await self._wait_for(lambda: compilation in service.result_cache,
                             "the second result to reach the cache")
        await service.result_cache.flush()
        await self._wait_for(lambda: len(scoring.new_evaluation_calls) == 2,
                             "the two notifications")

        self.assertEqual(len(worker.received_job_groups), 2)
        for checked in (paths_service, service):
            self.assertEqual(checked.post_finish_lock.loop_acquisitions, [])
        self.assertEqual(paths_d_lock.violations, [])
        self.assertEqual(d_lock.violations, [])
```

(The `await asyncio.sleep(0.05)` with its stale comment at lines 217-219 is gone: the loop actions no longer take the lock, and they have run once the await returns.)

- [ ] **Step 3: Adjust `EvaluationService_test.py` (the loop gate, spec test 5's double invalidation, stale comment)**

1. After `import asyncio` (line 5) add `import contextlib`.

2. Replace lines 271-275

```python
        # _push_to_queue's enqueue is dispatched via call_soon_threadsafe,
        # so the operation may not be visible until the next loop
        # iteration after new_submission()'s await returns.
        await self._wait_until(lambda: operation in self.service.get_executor())
        self.assertIn(operation, self.service.get_executor())
```

with

```python
        # The push is handed to the loop before new_submission()'s body
        # returns, so it has landed once the await returns.
        self.assertIn(operation, self.service.get_executor())
```

3. Insert right before `    def _invalidate_compilation_sync(self, submission_id: int):` (line 607):

```python
    @staticmethod
    @contextlib.contextmanager
    def _loop_held(loop: asyncio.AbstractEventLoop):
        """Keep the event loop from running anything new, from a thread.

        Everything handed to the loop while inside (loop actions
        included) waits behind the gate, in order, until the block is
        left. Only for use on an executor thread.

        loop: the event loop to hold.

        """
        release = threading.Event()
        loop.call_soon_threadsafe(release.wait, 10)
        try:
            yield
        finally:
            release.set()

```

4. In `test_enqueue_pending_when_dequeue_scheduled_right_after`, replace lines 628-636

```python
        # Holding post_finish_lock on the executor thread for the whole
        # sequence keeps every scheduled callback (each one takes that
        # lock) from running before the sequence is complete; they then
        # run in the order they were scheduled.
        def push_then_invalidate():
            with self.service.post_finish_lock:
                self.service._new_submission_sync(submission.id)
                self.assertNotIn(operation, executor._operation_queue)
                self._invalidate_compilation_sync(submission.id)
```

with

```python
        # Holding the loop for the whole sequence keeps every loop action
        # handed over meanwhile from running before the sequence is
        # complete; they then run in the order they were recorded.
        def push_then_invalidate():
            with self._loop_held(loop):
                self.service._new_submission_sync(submission.id)
                self.assertNotIn(operation, executor._operation_queue)
                self._invalidate_compilation_sync(submission.id)
```

5. In `test_double_invalidation_keeps_operation_queued`, replace lines 663-674

```python
        # See test_enqueue_pending_when_dequeue_scheduled_right_after
        # for why post_finish_lock is held across both invalidations.
        def invalidate_twice():
            with self.service.post_finish_lock:
                self._invalidate_compilation_sync(submission.id)
                self._invalidate_compilation_sync(submission.id)

        await asyncio.wait_for(
            loop.run_in_executor(None, invalidate_twice), timeout=10)

        self.assertIn(operation, executor._operation_queue)
        self.assertEqual(self.service._pending_operations, {})
```

with

```python
        # See test_enqueue_pending_when_dequeue_scheduled_right_after
        # for why the loop is held across both invalidations.
        def invalidate_twice():
            with self._loop_held(loop):
                self._invalidate_compilation_sync(submission.id)
                after_first = self.service._pending_operations.get(operation)
                self._invalidate_compilation_sync(submission.id)
                after_second = self.service._pending_operations.get(
                    operation)
            return after_first, after_second

        after_first, after_second = await asyncio.wait_for(
            loop.run_in_executor(None, invalidate_twice), timeout=10)

        # Each invalidation recorded a dequeue and then a push; none of
        # them was applied while the loop was held.
        self.assertEqual(after_first, ("push", 2))
        self.assertEqual(after_second, ("push", 4))
        self.assertIn(operation, executor._operation_queue)
        self.assertEqual(self.service._pending_operations, {})
```

(It reads `.get(operation)` on purpose: from Task 3 on, the snapshot also holds the `archive_sandbox=True` twin.)

Leave `test_invalidate_submission_keeps_queued_operation` (lines 541-555) as it is: its test-only callback that blocks the loop on `post_finish_lock` still works, since the invalidation's actions queue up behind it.

- [ ] **Step 4: Adjust `evaluationservice_failures_test.py` (loop gate) and add spec tests 6 and 7**

1. In `test_sweep_does_not_queue_what_a_new_submission_just_queued`, replace lines 885-890

```python
        def new_submission_then_sweep():
            # Holding the lock keeps the push, which takes it too, from
            # landing before the sweep: it is still in flight for it.
            with self.service.post_finish_lock:
                self.service._new_submission_sync(fixture.submission.id)
                return self.service._missing_operations_sync()
```

with

```python
        def new_submission_then_sweep():
            # Holding the loop keeps the push from landing before the
            # sweep: it is still in flight for it.
            release = threading.Event()
            loop.call_soon_threadsafe(release.wait, 10)
            try:
                self.service._new_submission_sync(fixture.submission.id)
                return self.service._missing_operations_sync()
            finally:
                release.set()
```

2. Insert right before `    # -- results written from an executor thread -------------------------` (line 943):

```python
    # -- what the coroutines queue, and how --------------------------------

    async def test_what_a_coroutine_queues_has_landed_when_it_returns(self):
        # The loop actions of a _sync method are handed to the loop
        # before the method returns, and run_in_executor delivers its
        # result after them: once the await returns, every push and
        # dequeue it decided has been applied, with no waiting.
        executor = self.service.get_executor()

        def assert_landed(*operations: ESOperation):
            self.assertEqual(self.service._pending_operations, {})
            for operation in operations:
                self.assertIn(operation, executor)

        queued = self._add_fixture()
        self.assertTrue(await self.service.enqueue(
            queued.compilation(), PriorityQueue.PRIORITY_HIGH,
            queued.submission.timestamp))
        assert_landed(queued.compilation())

        submitted = self._add_fixture()
        await self.service.new_submission(submitted.submission.id)
        assert_landed(submitted.compilation())

        participation = self.add_participation(contest=queued.contest)
        user_test = self.add_user_test(
            task=queued.task, participation=participation)
        self.session.commit()
        await self.service.new_user_test(user_test.id)
        assert_landed(ESOperation(
            ESOperation.USER_TEST_COMPILATION, user_test.id,
            queued.dataset.id))

        invalidated = self._add_fixture(compiled=True)
        await self.service.invalidate_submission(
            submission_id=invalidated.submission.id, level="compilation")
        assert_landed(invalidated.compilation())

        written = self._add_fixture(testcases=2)
        job = CompilationJob(
            operation=written.compilation(), task_type="Batch",
            task_type_parameters={}, language=None, files={}, managers={},
            success=True, compilation_success=True,
            text=["Compiled successfully."], plus={})
        await self.service.write_results(
            [(written.compilation(), Result(job, True))])
        assert_landed(written.evaluation("t0"), written.evaluation("t1"))

        swept = self._add_fixture()
        with self.assertLogs(
                "cms.io.async_triggeredservice", level="INFO") as logs:
            await self.service._sweep()
        assert_landed(swept.compilation())
        self.assertIn("Found 1 missed operation(s)", logs.output[-1])

    async def test_a_sweep_sends_what_it_finds_in_one_job_group(self):
        # The pushes of a section reach the queue together, so the
        # executor batches them as it always did.
        fixture = self._add_fixture(testcases=3, compiled=True)
        worker = ControllableWorker()
        await self._start_worker(worker)

        await self.service._sweep()
        await self._wait_until_idle()

        self.assertEqual(len(worker.received_job_groups), 1)
        self.assertCountEqual(
            worker.jobs,
            [("evaluation", fixture.submission.id, "t%d" % index)
             for index in range(3)])
        self.assertTrue(self._load_result(fixture).evaluated())

```

Both guard behavior that already holds today (they pass before Step 6); they must keep passing after it.

- [ ] **Step 5: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py cmstestsuite/unit_tests/service/evaluationservice_lock_order_test.py cmstestsuite/unit_tests/service/EvaluationService_test.py cmstestsuite/unit_tests/service/evaluationservice_failures_test.py -q`
Expected: `8 failed, 47 passed` (it takes about 20 s). The failures are `test_loop_never_takes_post_finish_lock` (`loop_acquisitions` is not empty) and seven loop actions tests: `test_loop_stays_responsive_during_a_long_section` (the loop blocks for the whole section), `test_push_after_a_dequeue_applied_during_the_section` (after its 10 s wait), `test_failing_action_is_logged_and_the_next_one_runs`, `test_actions_of_a_section_that_raises_still_land`, `test_nested_sections_hand_their_actions_over_once`, `test_actions_run_inline_without_a_loop`, `test_closed_loop_does_not_mask_the_section_result` (missing attributes or the old blocking behavior). `test_push_then_dequeue_in_one_section` and `test_dequeue_then_push_in_one_section` pass already: they pin ordering that must not change.

- [ ] **Step 6: Implement L1 in `cms/service/EvaluationService.py`**

1. Imports (lines 33-38): after `from collections import defaultdict` add `from collections.abc import Callable`.

2. In the module note, replace the last paragraph (lines 88-93)

```python
# Lock ordering: always take post_finish_lock before opening a DB
# session (a pooled connection), never the reverse. A thread holding a
# connection while it waits for the lock can deadlock with the lock
# holder waiting for a connection once the pool (5 + 10 overflow) is
# exhausted; every entry point that touches both is decorated with
# @with_post_finish_lock so that the lock comes first.
```

with

```python
# The event loop never takes post_finish_lock: while a _sync method
# holds it, the loop keeps serving RPCs. The queue (the executor's
# AsyncPriorityQueue, queue_status_cumulative, _currently_executing)
# is mutated only on the loop, so a _sync method never pushes or
# dequeues by itself: it records the action in _pending_operations and
# hands a "loop action" to _schedule_loop_action. The loop actions of
# a section (the outermost @with_post_finish_lock call) are buffered
# and handed to the loop with one call_soon_threadsafe when the section
# ends, normally or with an exception, still under the lock: the loop
# applies them in the order they were recorded, and before the
# coroutine awaiting the section resumes. Dequeues are handed over
# right away, pushes only at the end of the section, after its commits.
#
# Lock order: post_finish_lock first; then a DB connection,
# `_operation_lock`, `d_lock` or `_pending_lock`.
# `_current_execution_lock` before `_operation_lock`. `_pending_lock`
# is a leaf. Never take `d_lock` while holding `_pending_lock`. Never
# take `post_finish_lock` (except reentrantly) while holding a DB
# connection or another lock. A thread holding a pooled connection
# while it waits for post_finish_lock can deadlock with the lock holder
# waiting for a connection once the pool (5 + 10 overflow) is
# exhausted: every entry point that touches both is decorated with
# @with_post_finish_lock so that the lock comes first.
```

3. Replace `with_post_finish_lock` (lines 243-254) with:

```python
def with_post_finish_lock(func):
    """Decorator for locking on self.post_finish_lock.

    Ensures that no more than one decorated function is executing at
    the same time. The outermost decorated call is a "section": when it
    ends (normally or with an exception), the loop actions it scheduled
    are handed to the event loop, still under the lock. Nested calls
    leave them to the outermost one.

    """
    @wraps(func)
    def wrapped(self, *args, **kwargs):
        with self.post_finish_lock:
            self._post_finish_depth += 1
            try:
                return func(self, *args, **kwargs)
            finally:
                self._post_finish_depth -= 1
                if self._post_finish_depth == 0:
                    self._dequeued_in_section.clear()
                    self._flush_loop_actions()
    return wrapped
```

4. In `EvaluationService.__init__`, replace lines 329-333

```python
        # For each operation with push/dequeue callbacks scheduled on the
        # event loop but not run yet: the last scheduled action ("push"
        # or "dequeue") and how many of those callbacks are still
        # pending. Guarded by post_finish_lock.
        self._pending_operations: dict[ESOperation, tuple[str, int]] = {}
```

with

```python
        # The loop actions (pushes and dequeues to apply on the event
        # loop) scheduled by the current section and not handed to the
        # loop yet, and how many decorated calls are running on the
        # thread holding the lock (0 outside of a section). Both guarded
        # by post_finish_lock; see with_post_finish_lock.
        self._loop_actions: list[Callable[[], None]] = []
        self._post_finish_depth = 0

        # The operations the current section has dequeued. Their
        # dequeue is handed to the loop right away, so it may have been
        # applied (and its pending entry cleared) before the section
        # re-enqueues them, while a worker still holds them, ignored:
        # _enqueue_sync must not take that for a live copy. Guarded by
        # post_finish_lock; emptied when the section ends.
        self._dequeued_in_section: set[ESOperation] = set()

        # Guards _pending_operations. A leaf lock: nothing is acquired
        # while holding it.
        self._pending_lock = threading.Lock()

        # For each operation with push/dequeue loop actions recorded but
        # not applied yet: the last recorded action ("push" or
        # "dequeue") and how many of those actions are still to apply.
        # Only post_finish_lock holders add to it; the loop removes.
        self._pending_operations: dict[ESOperation, tuple[str, int]] = {}
```

These must stay before any decorated call; nothing in `__init__` calls one.

5. Replace everything from `    @with_post_finish_lock` / `    def _enqueue_sync(` (line 573) down to the end of `_threadsafe_dequeue_and_ignore` (line 714, the line before `    def _threadsafe_notify_scoring_service(`) with:

```python
    @with_post_finish_lock
    def _enqueue_sync(
        self, operation: ESOperation, priority: int, timestamp: datetime
    ) -> bool:
        """Decide whether to enqueue, and push if so, synchronously.

        Runs inside loop.run_in_executor (or is called directly, as a
        plain nested function call, from another _sync method already
        holding post_finish_lock on the same background thread -- see
        submission_enqueue_operations/user_test_enqueue_operations/
        write_results_sync/etc, which all call this directly rather than
        the async enqueue()). The push itself is a loop action, applied
        once the current section ends.

        operation: the operation to push.
        priority: the priority of the operation.
        timestamp: the time of the submission.

        return: True if pushed, False if not.

        """
        # Read the pending action first and let go of _pending_lock
        # before the membership checks: `in self.result_cache` takes
        # d_lock, never to be taken while holding _pending_lock.
        with self._pending_lock:
            pending_action, _ = self._pending_operations.get(
                operation, (None, 0))
        if pending_action == "push":
            return False
        dequeued = pending_action == "dequeue" \
            or operation in self._dequeued_in_section
        if not dequeued and (
                operation in self.get_executor()
                or operation in self.result_cache):
            return False
        self._record_pending(operation, "push")
        self._push_to_queue(operation, priority, timestamp)
        return True

    def _record_pending(self, operation: ESOperation, action: str):
        """Record that action has just been scheduled for operation.

        Tracks a (last_action, pending_count) pair per operation so that
        _enqueue_sync()'s synchronous membership check can predict the
        *eventual* state correctly even when multiple push/dequeue
        loop actions are in flight for the same operation at once (e.g.
        a push already scheduled when an invalidation's dequeue comes in
        right after, or two invalidations of the same operation
        back-to-back). The loop actions of _push_to_queue and
        _threadsafe_dequeue_and_ignore each call _clear_pending_one()
        once their real action has run, in the same FIFO order they
        were recorded in, so the last recorded action is the one that
        determines the final state. Must be called while holding
        post_finish_lock.

        operation: the operation the action was scheduled for.
        action: "push" or "dequeue".

        """
        with self._pending_lock:
            _, count = self._pending_operations.get(operation, (None, 0))
            self._pending_operations[operation] = (action, count + 1)

    def _clear_pending_one(self, operation: ESOperation):
        """Record that one loop action for operation has been applied.

        Decrements the pending count; once it reaches zero, removes the
        entry entirely (no action left in flight, so _enqueue_sync()'s
        real membership check alone is accurate again). Called on the
        event loop (or inline, when there is no loop yet).

        operation: the operation whose action has just been applied.

        """
        with self._pending_lock:
            action, count = self._pending_operations.get(
                operation, (None, 0))
            if count <= 1:
                self._pending_operations.pop(operation, None)
            else:
                self._pending_operations[operation] = (action, count - 1)

    def _schedule_loop_action(
        self, action: Callable[[], None], flush_now: bool = False
    ):
        """Have action applied on the event loop.

        The action waits in a buffer until the current section ends
        (see with_post_finish_lock), or until now if flush_now. At
        __init__ time (self._loop is still None) nothing can race with
        the caller, so the action runs right away instead. Must be
        called while holding post_finish_lock.

        action: the loop action; it must not take post_finish_lock.
        flush_now: whether to hand the buffer to the loop right away.

        """
        if self._loop is None:
            action()
            return
        self._loop_actions.append(action)
        if flush_now:
            self._flush_loop_actions()

    def _flush_loop_actions(self):
        """Hand the buffered loop actions to the event loop, in order.

        One call_soon_threadsafe for the whole buffer. Must be called
        while holding post_finish_lock.

        """
        if not self._loop_actions:
            return
        actions, self._loop_actions = self._loop_actions, []
        try:
            self._loop.call_soon_threadsafe(self._run_loop_actions, actions)
        except RuntimeError:
            # The loop is closed: ES is shutting down, and the queue is
            # gone with it. Don't hide the section's own outcome.
            logger.warning("Event loop closed, dropping %d queue action(s).",
                           len(actions))

    def _run_loop_actions(self, actions: list[Callable[[], None]]):
        """Apply loop actions in order, on the event loop.

        An action that raises is logged and does not stop the next ones.

        actions: the loop actions to apply.

        """
        for action in actions:
            try:
                action()
            except Exception:
                logger.error("Unexpected error in a queue action.",
                             exc_info=True)

    def _push_to_queue(
        self, operation: ESOperation, priority: int, timestamp: datetime
    ):
        """Push into the executor's queue at the end of the section.

        AsyncTriggeredService.enqueue ultimately touches an asyncio.Event
        (inside AsyncPriorityQueue.push()), unsafe to call directly from
        a thread other than the event loop's: the push is a loop action
        (see _schedule_loop_action). Once it has been applied, it also
        clears one pending action from the operation's
        _pending_operations entry (recorded by _enqueue_sync). Must be
        called while holding post_finish_lock.

        operation: the operation to push.
        priority: the priority of the operation.
        timestamp: the time of the submission.

        """
        def _do():
            try:
                AsyncTriggeredService.enqueue(
                    self, operation, priority, timestamp)
            finally:
                # Always clear the pending marker, even if enqueue()
                # raised -- otherwise a stuck "push" entry would
                # block every future re-enqueue of this operation.
                self._clear_pending_one(operation)
        self._schedule_loop_action(_do)

    def _threadsafe_dequeue_and_ignore(self, operation: ESOperation):
        """Dequeue and ignore an operation, safely from any thread.

        Records "dequeue" as the operation's last scheduled action in
        _pending_operations immediately (this method is only ever
        called while post_finish_lock is already held by the caller --
        _invalidate_submission_sync) so a later _enqueue_sync() for the
        same operation neither trusts its stale "still in the queue"
        membership nor an earlier still-pending push, and schedules a
        new push after this dequeue. The actual dequeue is a loop action
        (dequeue() eventually touches AsyncPriorityQueue.remove(),
        unsafe off the event loop thread), handed to the loop right
        away; once it has run, it clears one pending action from the
        operation's entry.

        operation: the operation to dequeue and ignore.

        """
        self._record_pending(operation, "dequeue")
        self._dequeued_in_section.add(operation)

        def _do():
            try:
                try:
                    self.dequeue(operation)
                except KeyError:
                    pass  # Ok, the operation wasn't in the queue.
                try:
                    self.get_executor().pool.ignore_operation(operation)
                except LookupError:
                    pass  # Ok, the operation wasn't in the pool.
            finally:
                # Always clear the pending marker, even on an
                # unexpected exception -- otherwise a stuck
                # "dequeue" entry would block every future
                # re-enqueue of this operation.
                self._clear_pending_one(operation)
        self._schedule_loop_action(_do, flush_now=True)

```

- [ ] **Step 7: Run the tests to see them pass**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py cmstestsuite/unit_tests/service/evaluationservice_lock_order_test.py cmstestsuite/unit_tests/service/EvaluationService_test.py cmstestsuite/unit_tests/service/evaluationservice_failures_test.py -q`
Expected: `55 passed`.

Run: `.venv/bin/pyflakes cms/service/EvaluationService.py cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py cmstestsuite/unit_tests/service/evaluationservice_lock_order_test.py cmstestsuite/unit_tests/service/EvaluationService_test.py cmstestsuite/unit_tests/service/evaluationservice_failures_test.py`
Expected: no output.

Run the asyncio group. Expected: `281 passed`, including the eight tests of spec section 7 listed above.
Run the gevent group. Expected: `5 passed`.

- [ ] **Step 8: Commit**

```bash
git add cms/service/EvaluationService.py \
    cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py \
    cmstestsuite/unit_tests/service/evaluationservice_lock_order_test.py \
    cmstestsuite/unit_tests/service/EvaluationService_test.py \
    cmstestsuite/unit_tests/service/evaluationservice_failures_test.py
git commit -m "fix(es): apply queue actions on the loop without post_finish_lock

The event loop no longer blocks while a _sync method holds the lock:
the pushes and dequeues of a section are handed to the loop in one
call_soon_threadsafe when the section ends, and applied lock-free.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Ignore invalidated operations on the invalidating thread, twins included (B)

**Files:**
- Modify: `cms/service/EvaluationService.py`: `_threadsafe_dequeue_and_ignore` (as rewritten by Task 2), and the dequeue loop of `_invalidate_submission_sync` (lines 1520-1523 at c82952f4)
- Test: `cmstestsuite/unit_tests/service/evaluationservice_failures_test.py` (imports 42-49; new class before `@dataclass` at line 129; new tests before `    # -- the sweeper, driven through _sweep() ----------------------------` at line 771)

**Interfaces:**
- Consumes (Task 2): `_threadsafe_dequeue_and_ignore` scheduling its `_do` with `_schedule_loop_action(_do, flush_now=True)`; `_dequeued_in_section`.
- Produces:
  - `_threadsafe_dequeue_and_ignore(operation)` also calls `self.get_executor().pool.ignore_operation(operation)` on the calling thread, after scheduling (a `LookupError` is swallowed).
  - `_invalidate_submission_sync` dequeues and ignores each operation and its twin `ESOperation(type_, object_id, dataset_id, testcase_codename, archive_sandbox=True)`.
  - Test class `RunLabellingWorker(ControllableWorker)` in `evaluationservice_failures_test.py`: the n-th job group it answers carries the text `["run n"]` in every job; attribute `answered: int`. Task 4 uses it.
  - Test helper `EvaluationServiceFailurePathsTest._ignored_lines(logs) -> list[str]`.

Existing tests that must stay green: the whole asyncio group (in particular `evaluationservice_failures_test.py::test_result_of_an_operation_invalidated_in_flight_is_ignored`, and `EvaluationService_test.py::test_double_invalidation_keeps_operation_queued`, whose snapshot now also holds the twin) and the gevent group.

- [ ] **Step 1: Write the tests (spec tests 3, 4, 16)**

1. In `cmstestsuite/unit_tests/service/evaluationservice_failures_test.py`, after `from unittest.mock import MagicMock, patch` (line 46) and its blank line, add as the first project import:

```python
import cms.service.EvaluationService as EvaluationServiceModule
```

so that the block reads `import cms.service.EvaluationService as EvaluationServiceModule` followed by `from cms.conf import Address, ServiceCoord`.

2. Insert right before `@dataclass` / `class Fixture:` (line 129):

```python
class RunLabellingWorker(ControllableWorker):
    """A ControllableWorker whose answers tell which run produced them.

    Every job of the n-th job group it answers (counting from 1) gets
    the text ["run n"]: it ends up as the compilation text or the
    evaluation text in the DB, so a test can tell a stale result from a
    fresh one.

    """

    def __init__(self, compilation_success: bool = False):
        super().__init__(compilation_success)
        self.answered = 0

    @rpc_method
    async def execute_job_group(self, job_group_dict: dict) -> dict:
        answer = await super().execute_job_group(job_group_dict)
        self.answered += 1
        for job in answer["jobs"]:
            job["text"] = ["run %d" % self.answered]
        return answer


```

3. Insert right before `    # -- the sweeper, driven through _sweep() ----------------------------` (line 771):

```python
    def _ignored_lines(self, logs) -> list[str]:
        """Return the log lines of the results dropped as requested."""
        return [line for line in logs.output
                if "result ignored as requested" in line]

    async def test_answer_arriving_during_the_invalidation_is_ignored(self):
        # The worker answers while the invalidation holds the lock, and
        # the loop runs the invalidation's dequeue (and its ignore) only
        # after that answer was handled: the ignore the invalidation does
        # on its own thread is what drops the stale answer.
        fixture = self._add_fixture()
        worker = RunLabellingWorker()
        worker.answers_released.clear()
        await self._start_worker(worker)
        await self.service.new_submission(fixture.submission.id)
        await self._wait_for(lambda: len(worker.jobs) == 1,
                             "the job to reach the worker")
        loop = asyncio.get_running_loop()
        cached: list[tuple[ESOperation, list[str]]] = []
        real_add = self.service.result_cache.add

        def recording_add(operation, result):
            cached.append((operation, result.job.text))
            real_add(operation, result)

        self.service.result_cache.add = recording_add

        # The answer reaches ES once the invalidation holds the lock.
        answer_arrived = threading.Event()
        self._gates.append(answer_arrived)
        real_action_finished = self.service.action_finished

        async def signalling_action_finished(data, shard, error=None):
            answer_arrived.set()
            await real_action_finished(data, shard, error)

        self.service.action_finished = signalling_action_finished
        real_get_relevant_operations = \
            EvaluationServiceModule.get_relevant_operations

        def answering_get_relevant_operations(*args, **kwargs):
            loop.call_soon_threadsafe(worker.answers_released.set)
            answer_arrived.wait(timeout=10)
            return real_get_relevant_operations(*args, **kwargs)

        # The loop applies the dequeue only once the answer was handled.
        answer_handled = threading.Event()
        self._gates.append(answer_handled)
        real_action_finished_sync = self.service._action_finished_sync

        def signalling_action_finished_sync(*args, **kwargs):
            try:
                return real_action_finished_sync(*args, **kwargs)
            finally:
                answer_handled.set()

        self.service._action_finished_sync = signalling_action_finished_sync
        real_dequeue = self.service.dequeue

        def late_dequeue(operation):
            answer_handled.wait(timeout=10)
            return real_dequeue(operation)

        self.service.dequeue = late_dequeue

        with patch.object(EvaluationServiceModule, "get_relevant_operations",
                          answering_get_relevant_operations), \
                self.assertLogs("cms.service.EvaluationService",
                                level="INFO") as logs:
            await self.service.invalidate_submission(
                submission_id=fixture.submission.id, level="compilation")
            await self._wait_until_idle()

        self.assertEqual(len(self._ignored_lines(logs)), 1)
        self.assertEqual(cached, [(fixture.compilation(), ["run 2"])])
        self.assertEqual(len(worker.jobs), 2)
        result = self._load_result(fixture)
        self.assertTrue(result.compilation_failed())
        self.assertEqual(result.compilation_text, ["run 2"])
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [fixture.key])

    async def test_operation_held_for_a_worker_is_not_sent_after_invalidation(
        self
    ):
        # The executor has popped the operation and waits for a worker
        # when the invalidation comes: the dequeue takes it out of
        # _currently_executing, so only the copy queued again is sent.
        busy = self._add_fixture()
        waiting = self._add_submission_of(busy)
        waiting_operation = ESOperation(
            ESOperation.COMPILATION, waiting.id, busy.dataset.id)
        worker = RunLabellingWorker()
        worker.answers_released.clear()
        await self._start_worker(worker)
        executor = self.service.get_executor()
        self.assertTrue(await self.service.enqueue(
            busy.compilation(), PriorityQueue.PRIORITY_HIGH,
            busy.submission.timestamp))
        await self._wait_for(lambda: len(worker.jobs) == 1,
                             "the first job to reach the worker")
        self.assertTrue(await self.service.enqueue(
            waiting_operation, PriorityQueue.PRIORITY_HIGH,
            waiting.timestamp))
        await self._wait_for(
            lambda: executor._currently_executing == [waiting_operation],
            "the operation to wait for a worker")

        await self.service.invalidate_submission(
            submission_id=waiting.id, level="compilation")

        self.assertEqual(executor._currently_executing, [])
        self.assertIn(waiting_operation, executor._operation_queue)
        self.assertEqual(self.service._pending_operations, {})
        worker.answers_released.set()
        await self._wait_until_idle()
        self.assertCountEqual(
            [job[1] for job in worker.jobs],
            [busy.submission.id, waiting.id])
        self.assertEqual(self.notifications.call_count, 2)

    async def test_invalidation_also_drops_the_archiving_twin(self):
        # An invalidation asking to archive the sandbox queues operations
        # with archive_sandbox=True, which are not equal to the ones
        # get_relevant_operations() builds. A later invalidation must
        # drop them all the same.
        fixture = self._add_fixture()
        twin = ESOperation(
            ESOperation.COMPILATION, fixture.submission.id,
            fixture.dataset.id, archive_sandbox=True)
        worker = RunLabellingWorker()
        worker.answers_released.clear()
        await self._start_worker(worker)
        await self.service.invalidate_submission(
            submission_id=fixture.submission.id, level="compilation",
            archive_sandbox=True)
        await self._wait_for(lambda: twin in self.pool,
                             "the twin to reach the worker")

        with self.assertLogs(
                "cms.service.EvaluationService", level="INFO") as logs:
            await self.service.invalidate_submission(
                submission_id=fixture.submission.id, level="compilation")
            self.assertIn(twin, self.pool._operations_to_ignore[0])
            worker.answers_released.set()
            await self._wait_until_idle()

        self.assertEqual(len(self._ignored_lines(logs)), 1)
        self.assertEqual(len(worker.jobs), 2)
        self.assertEqual(self._load_result(fixture).compilation_text,
                         ["run 2"])
        self.assertEqual(self.notifications.call_count, 1)

```

Maps to the spec: test 3 is `test_answer_arriving_during_the_invalidation_is_ignored`, test 4 is `test_operation_held_for_a_worker_is_not_sent_after_invalidation` (it pins existing behavior of the `_currently_executing` fallback, so it passes before Step 3 too), test 16 is `test_invalidation_also_drops_the_archiving_twin`.

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/service/evaluationservice_failures_test.py -k "during_the_invalidation or held_for_a_worker or archiving_twin" -q`
Expected: `2 failed, 1 passed`. `test_answer_arriving_during_the_invalidation_is_ignored` fails with `AssertionError: 0 != 1` (no answer ignored: the stale one was cached and written); `test_invalidation_also_drops_the_archiving_twin` fails with `("compile", ..., True) not found in []` (the twin is not ignored).

- [ ] **Step 3: Implement B and the twins**

1. In `_threadsafe_dequeue_and_ignore`, add a paragraph to the docstring, right before `        operation: the operation to dequeue and ignore.`:

```python
        The operation is also ignored in the worker pool right here, on
        the calling thread, after the loop action is scheduled: a
        worker's answer that takes post_finish_lock before the loop has
        run the action must already find it ignored. The loop action
        ignores it again, for an operation the executor hands to a
        worker in between; a duplicate in the ignore list is harmless.

```

and, after its last line `        self._schedule_loop_action(_do, flush_now=True)`, add:

```python
        try:
            self.get_executor().pool.ignore_operation(operation)
        except LookupError:
            pass  # Ok, the operation wasn't in the pool.
```

(Schedule first, then ignore: that is the order spec 4 requires. `ignore_operation` takes `WorkerPool._operation_lock`, an edge after `post_finish_lock` that already exists.)

2. In `_invalidate_submission_sync`, replace

```python
            for operation in operations:
                self._threadsafe_dequeue_and_ignore(operation)
```

with

```python
            for operation in operations:
                self._threadsafe_dequeue_and_ignore(operation)
                # Its twin, queued by an invalidation that asked to
                # archive the sandbox: not equal to it (archive_sandbox
                # is part of ESOperation.__eq__), stale all the same.
                self._threadsafe_dequeue_and_ignore(ESOperation(
                    operation.type_, operation.object_id,
                    operation.dataset_id, operation.testcase_codename,
                    archive_sandbox=True))
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/service/evaluationservice_failures_test.py -k "during_the_invalidation or held_for_a_worker or archiving_twin" -q`
Expected: `3 passed`.

Run: `.venv/bin/pyflakes cms/service/EvaluationService.py cmstestsuite/unit_tests/service/evaluationservice_failures_test.py`
Expected: no output.

Run the asyncio group. Expected: `284 passed`.
Run the gevent group. Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add cms/service/EvaluationService.py \
    cmstestsuite/unit_tests/service/evaluationservice_failures_test.py
git commit -m "fix(es): ignore invalidated operations on the invalidating thread

A worker's answer taking post_finish_lock right after an invalidation
is now dropped even if the loop has not run the dequeue yet, and the
archive_sandbox=True twins of the operations are dropped too.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Purge and mark the cached results of invalidated operations (G1)

**Files:**
- Modify: `cms/service/EvaluationService.py`: `Result.__init__` (263-265), `_enqueue_sync` (as rewritten by Task 2), new `_discard_cached_results` (right before `_threadsafe_notify_scoring_service`), `_write_results_sync` (first lines, 843-844), `_invalidate_submission_sync` (after the dequeue loop of Task 3)
- Test: `cmstestsuite/unit_tests/service/evaluationservice_failures_test.py` (new tests before `    # -- the sweeper, driven through _sweep() ----------------------------`, i.e. after Task 3's tests)
- Test: `cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py` (imports, one test)

**Interfaces:**
- Consumes: `FlushingDict.discard(predicate) -> list[ValueT]` (Task 1); `_dequeued_in_section`, `_loop_held()` (Task 2); `RunLabellingWorker` (Task 3).
- Produces:
  - `Result.discarded: bool`, `False` at creation.
  - `EvaluationService._discard_cached_results(operations: list[ESOperation]) -> None`, called with `post_finish_lock` held: discards the cache entries whose `(type_, object_id, dataset_id, testcase_codename)` matches one of `operations` (any `archive_sandbox`), sets `discarded = True` on each, logs `"Discarded %d cached result(s) of invalidated operations."` at INFO.
  - `_write_results_sync` first drops the discarded items, logging ``"Not writing the result of `%s': its operation was invalidated."`` at INFO for each.
  - `_enqueue_sync` checks `operation in self.result_cache` even for a dequeued operation.

Existing tests that must stay green: the whole asyncio group and the gevent group. Then the regression of spec 7: the asyncio group three times.

- [ ] **Step 1: Write the tests (spec tests 9, 10, 11, 12, 14, 15; Review Focus 4 and 5; the rule of spec 3.4)**

1. In `cmstestsuite/unit_tests/service/evaluationservice_failures_test.py`, insert right before `    # -- the sweeper, driven through _sweep() ----------------------------` (after the tests of Task 3):

```python
    # -- results of invalidated operations, cached or being flushed -----

    async def _cache_first_result(
        self, fixture: Fixture, operation: ESOperation
    ) -> RunLabellingWorker:
        """Get a first result of operation into the cache, and keep it there.

        The fixture's submission is sent to a worker, whose answer for
        operation is then held in the cache (the flush latency is
        raised). The worker holds its next answers.

        fixture: the fixture whose submission to send.
        operation: the operation whose result to wait for.

        return: the worker.

        """
        worker = RunLabellingWorker()
        await self._start_worker(worker)
        self.service.result_cache.flush_latency_seconds = 3600
        await self.service.new_submission(fixture.submission.id)
        await self._wait_for(lambda: operation in self.service.result_cache,
                             "the first result to reach the cache")
        worker.answers_released.clear()
        return worker

    async def _release_and_finish(self, worker: RunLabellingWorker):
        """Let the worker answer, flush right away, and wait for ES."""
        self.service.result_cache.flush_latency_seconds = 0
        worker.answers_released.set()
        await self._wait_until_idle()

    def _stale_line(self, operation: ESOperation) -> str:
        """Return what write_results logs for a discarded result."""
        return ("Not writing the result of `%s': its operation was "
                "invalidated." % operation)

    async def test_cached_evaluation_of_an_invalidated_operation_is_dropped(
        self
    ):
        fixture = self._add_fixture(testcases=1, compiled=True)
        operation = fixture.evaluation("t0")
        worker = await self._cache_first_result(fixture, operation)
        stale = self.service.result_cache.d[operation]

        with self.assertLogs(
                "cms.service.EvaluationService", level="INFO") as logs:
            await self.service.invalidate_submission(
                submission_id=fixture.submission.id, level="evaluation")
            self.assertNotIn(operation, self.service.result_cache)
            self.assertTrue(stale.discarded)
            # What the cache held before the invalidation is not written.
            await self.service.result_cache.flush()
            self.assertEqual(self._load_result(fixture).evaluations, [])
            await self._release_and_finish(worker)

        self.assertEqual(len(worker.jobs), 2)
        result = self._load_result(fixture)
        self.assertEqual([(e.codename, e.text) for e in result.evaluations],
                         [("t0", ["run 2"])])
        self.assertFalse(any("Integrity error" in line
                             for line in logs.output))
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [fixture.key])

    async def test_evaluation_being_flushed_when_invalidated_is_dropped(self):
        # A flush has taken the result, and its write waits for the lock
        # the invalidation holds: the write runs after the invalidation,
        # and must skip it.
        fixture = self._add_fixture(testcases=1, compiled=True)
        operation = fixture.evaluation("t0")
        worker = await self._cache_first_result(fixture, operation)
        cache = self.service.result_cache
        holding_lock = threading.Event()
        batch_taken = threading.Event()
        self._gates.append(batch_taken)
        real_get_relevant_operations = \
            EvaluationServiceModule.get_relevant_operations

        def gated_get_relevant_operations(*args, **kwargs):
            holding_lock.set()
            batch_taken.wait(timeout=10)
            return real_get_relevant_operations(*args, **kwargs)

        batches: list[list] = []
        real_callback = cache.callback

        async def signalling_callback(items):
            batches.append(items)
            batch_taken.set()
            await real_callback(items)

        cache.callback = signalling_callback

        with patch.object(EvaluationServiceModule, "get_relevant_operations",
                          gated_get_relevant_operations), \
                self.assertLogs("cms.service.EvaluationService",
                                level="INFO") as logs:
            invalidation = asyncio.create_task(
                self.service.invalidate_submission(
                    submission_id=fixture.submission.id,
                    level="evaluation"))
            await self._wait_for(holding_lock.is_set,
                                 "the invalidation to hold the lock")
            await cache.flush()
            await invalidation
            self.assertEqual(len(batches), 1)
            [(flushed_operation, stale)] = batches[0]
            self.assertEqual(flushed_operation, operation)
            self.assertTrue(stale.discarded)
            self.assertNotIn(operation, cache)
            self.assertEqual(self._load_result(fixture).evaluations, [])
            await self._release_and_finish(worker)

        self.assertIn(self._stale_line(operation),
                      [record.getMessage() for record in logs.records])
        self.assertEqual(len(worker.jobs), 2)
        result = self._load_result(fixture)
        self.assertEqual([(e.codename, e.text) for e in result.evaluations],
                         [("t0", ["run 2"])])
        self.assertFalse(any("Integrity error" in line
                             for line in logs.output))
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [fixture.key])

    async def test_cached_compilation_of_an_invalidated_operation_is_dropped(
        self
    ):
        # The submission has no result in the DB yet: only the cache
        # holds the stale compilation.
        fixture = self._add_fixture()
        operation = fixture.compilation()
        worker = await self._cache_first_result(fixture, operation)

        with self.assertLogs(
                "cms.service.EvaluationService", level="INFO") as logs:
            await self.service.invalidate_submission(
                submission_id=fixture.submission.id, level="compilation")
            self.assertNotIn(operation, self.service.result_cache)
            await self.service.result_cache.flush()
            self.assertIsNone(self._load_result(fixture))
            await self._release_and_finish(worker)

        self.assertEqual(len(worker.jobs), 2)
        result = self._load_result(fixture)
        self.assertTrue(result.compilation_failed())
        self.assertEqual(result.compilation_text, ["run 2"])
        self.assertEqual(result.compilation_tries, 0)
        self.assertFalse(any("Integrity error" in line
                             for line in logs.output))
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [fixture.key])

    async def test_reenqueue_after_the_dequeue_landed_drops_the_stale_result(
        self
    ):
        # The loop applies the invalidation's dequeues before the
        # invalidation queues the operations again: nothing pending any
        # more, and the stale result still cached would make it skip the
        # push without the purge.
        fixture = self._add_fixture()
        operation = fixture.compilation()
        worker = await self._cache_first_result(fixture, operation)
        dequeues_applied = threading.Event()
        self._gates.append(dequeues_applied)
        real_clear_pending_one = self.service._clear_pending_one

        def clear_pending_one_and_signal(cleared: ESOperation):
            real_clear_pending_one(cleared)
            if not self.service._pending_operations:
                dequeues_applied.set()

        self.service._clear_pending_one = clear_pending_one_and_signal
        real_submission_enqueue_operations = \
            self.service.submission_enqueue_operations

        def late_submission_enqueue_operations(*args, **kwargs):
            dequeues_applied.wait(timeout=10)
            return real_submission_enqueue_operations(*args, **kwargs)

        self.service.submission_enqueue_operations = \
            late_submission_enqueue_operations

        await self.service.invalidate_submission(
            submission_id=fixture.submission.id, level="compilation")

        self.assertTrue(dequeues_applied.is_set())
        self.assertIn(operation, self.service.get_executor())
        self.assertNotIn(operation, self.service.result_cache)
        await self.service.result_cache.flush()
        self.assertIsNone(self._load_result(fixture))
        await self._release_and_finish(worker)
        self.assertEqual(len(worker.jobs), 2)
        self.assertEqual(self._load_result(fixture).compilation_text,
                         ["run 2"])
        self.assertEqual(self.notifications.call_count, 1)

    async def test_invalidation_purges_only_the_results_it_invalidates(self):
        # Keys are compared without archive_sandbox (twins are stale
        # too) but with the type (a user test may share the id).
        fixture = self._add_fixture(testcases=1, compiled=True)
        submission_id, dataset_id = fixture.key
        cache = self.service.result_cache
        cache.flush_latency_seconds = 3600
        twin_evaluation = ESOperation(
            ESOperation.EVALUATION, submission_id, dataset_id, "t0",
            archive_sandbox=True)
        user_test_evaluation = ESOperation(
            ESOperation.USER_TEST_EVALUATION, submission_id, dataset_id)
        compilation = fixture.compilation()
        results = {operation: Result(MagicMock(), True) for operation in (
            twin_evaluation, user_test_evaluation, compilation)}
        for operation, result in results.items():
            cache.add(operation, result)

        with self.assertLogs(
                "cms.service.EvaluationService", level="INFO") as logs:
            await self.service.invalidate_submission(
                submission_id=submission_id, level="evaluation")

        self.assertNotIn(twin_evaluation, cache)
        self.assertTrue(results[twin_evaluation].discarded)
        for operation in (user_test_evaluation, compilation):
            self.assertIn(operation, cache)
            self.assertFalse(results[operation].discarded)
        self.assertIn(
            "Discarded 1 cached result(s) of invalidated operations.",
            [record.getMessage() for record in logs.records])
        # Nothing of this is meant to be written.
        cache.discard(lambda key: True)

    async def test_write_results_skips_the_discarded_results(self):
        # Of two evaluations of the same submission, the discarded one is
        # not written: the other one is, and the submission, still
        # missing an evaluation, is neither finalized nor notified.
        fixture = self._add_fixture(testcases=2, compiled=True)
        stale_operation = fixture.evaluation("t0")
        stale = Result(self._evaluation_job(stale_operation), True)
        stale.discarded = True
        live_operation = fixture.evaluation("t1")
        live = Result(self._evaluation_job(live_operation), True)

        with self.assertLogs(
                "cms.service.EvaluationService", level="INFO") as logs:
            await self.service.write_results(
                [(stale_operation, stale), (live_operation, live)])

        self.assertIn(self._stale_line(stale_operation),
                      [record.getMessage() for record in logs.records])
        result = self._load_result(fixture)
        self.assertEqual([e.codename for e in result.evaluations], ["t1"])
        self.assertIsNone(result.evaluation_outcome)

        # A batch of discarded results only: nothing at all is written.
        fresh = self._add_fixture()
        job = CompilationJob(
            operation=fresh.compilation(), success=True,
            compilation_success=False, text=["Compilation failed."],
            plus={})
        discarded = Result(job, True)
        discarded.discarded = True
        await self.service.write_results([(fresh.compilation(), discarded)])

        self.assertIsNone(self._load_result(fresh))
        self.assertEqual(self.notifications.call_count, 0)

    async def test_testcase_invalidation_recomputes_the_purged_results(self):
        # Invalidating one testcase dequeues, ignores and purges the
        # operations of every testcase of the submission: the results
        # that were only cached are computed again, and the submission
        # still ends with one evaluation per testcase.
        fixture = self._add_fixture(testcases=2, compiled=True)
        cache = self.service.result_cache
        worker = await self._cache_first_result(
            fixture, fixture.evaluation("t1"))
        await self._wait_for(lambda: fixture.evaluation("t0") in cache,
                             "the other first result to reach the cache")
        testcase_id = fixture.dataset.testcases["t0"].id

        await self.service.invalidate_submission(
            submission_id=fixture.submission.id, testcase_id=testcase_id,
            level="evaluation")

        self.assertNotIn(fixture.evaluation("t0"), cache)
        self.assertNotIn(fixture.evaluation("t1"), cache)
        await self._release_and_finish(worker)
        self.assertEqual(len(worker.jobs), 4)
        result = self._load_result(fixture)
        self.assertEqual(
            sorted((e.codename, e.text) for e in result.evaluations),
            [("t0", ["run 2"]), ("t1", ["run 2"])])
        self.assertTrue(result.evaluated())
        self.assertEqual(self.notifications.call_count, 1)

```

Maps to the spec: 9 is `test_cached_evaluation_of_an_invalidated_operation_is_dropped`, 10 `test_evaluation_being_flushed_when_invalidated_is_dropped`, 11 `test_cached_compilation_of_an_invalidated_operation_is_dropped` (also the "no result yet" case), 12 `test_reenqueue_after_the_dequeue_landed_drops_the_stale_result`, 14 `test_invalidation_purges_only_the_results_it_invalidates`, 15 `test_write_results_skips_the_discarded_results` (Review Focus 4), Review Focus 5 `test_testcase_invalidation_recomputes_the_purged_results`.

2. In `cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py`, change `from unittest.mock import patch` to `from unittest.mock import MagicMock, patch`, and

```python
from cms.service.EvaluationService import EvaluationService, \
    with_post_finish_lock
```

to

```python
from cms.service.EvaluationService import EvaluationService, Result, \
    with_post_finish_lock
```

Then insert right before `    # -- failures ---------------------------------------------------------`:

```python
    async def test_cached_result_stops_the_push_even_with_a_dequeue_pending(
        self
    ):
        # After the purge of an invalidation, a result still in the cache
        # is a live one: _enqueue_sync must not push its operation again,
        # even while a dequeue of it is pending.
        operation = self._operation(1)
        live = Result(MagicMock(), True)

        @with_post_finish_lock
        def dequeue_then_push(service):
            service._threadsafe_dequeue_and_ignore(operation)
            service.result_cache.add(operation, live)
            pending = dict(service._pending_operations)
            return pending, service._enqueue_sync(
                operation, HIGH, self.timestamp)

        def body():
            with self._loop_held():
                return dequeue_then_push(self.service)

        try:
            pending, pushed = await self._run_in_thread(body)
        finally:
            # Nothing of this is meant to be written.
            self.service.result_cache.discard(lambda key: True)

        self.assertEqual(pending, {operation: ("dequeue", 1)})
        self.assertFalse(pushed)
        self.assertNotIn(operation, self.executor)
        self.assertEqual(self.service._pending_operations, {})

```

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/service/evaluationservice_failures_test.py cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py -q`
Expected: `8 failed, 36 passed`: the seven new tests of the failures file and `test_cached_result_stops_the_push_even_with_a_dequeue_pending` (the stale results stay cached, get written, or the push happens).

- [ ] **Step 3: Implement G1**

1. `Result.__init__` (lines 263-265) becomes:

```python
    def __init__(self, job: Job, job_success: bool):
        self.job = job
        self.job_success = job_success
        # Set by an invalidation that purged this result from the cache
        # (see EvaluationService._discard_cached_results): it must not
        # be written, even by a flush that had already taken it.
        self.discarded = False
```

2. In `_enqueue_sync`, replace

```python
        dequeued = pending_action == "dequeue" \
            or operation in self._dequeued_in_section
        if not dequeued and (
                operation in self.get_executor()
                or operation in self.result_cache):
            return False
```

with

```python
        dequeued = pending_action == "dequeue" \
            or operation in self._dequeued_in_section
        if not dequeued and operation in self.get_executor():
            return False
        # Even for a dequeued operation: an invalidation purges the
        # results it makes stale before queueing again, so a result
        # still cached is a live one.
        if operation in self.result_cache:
            return False
```

3. Insert right before `    def _threadsafe_notify_scoring_service(`:

```python
    def _discard_cached_results(self, operations: list[ESOperation]):
        """Drop the cached worker results of operations, for good.

        Removes them from the result cache, both the ones waiting for a
        flush and the ones a flush has already taken, and marks every
        removed Result as discarded so that _write_results_sync skips
        it. Results match by type, object, dataset and testcase, whatever
        their archive_sandbox. Must be called while holding
        post_finish_lock, which _write_results_sync and
        _action_finished_sync also hold.

        operations: the operations whose results are stale.

        """
        keys = {(operation.type_, operation.object_id,
                 operation.dataset_id, operation.testcase_codename)
                for operation in operations}
        discarded = self.result_cache.discard(
            lambda cached: (cached.type_, cached.object_id,
                            cached.dataset_id,
                            cached.testcase_codename) in keys)
        for result in discarded:
            result.discarded = True
        logger.info("Discarded %d cached result(s) of invalidated "
                    "operations.", len(discarded))

```

4. In `_write_results_sync`, replace its first statement (line 844)

```python
        logger.info("Starting commit process...")
```

with

```python
        # Results purged by an invalidation after a flush took them.
        for operation, result in items:
            if result.discarded:
                logger.info("Not writing the result of `%s': its "
                            "operation was invalidated.", operation)
        items = [(operation, result) for operation, result in items
                 if not result.discarded]

        logger.info("Starting commit process...")
```

With no invalidation no line is added and `items` is unchanged (I11). Filtering before the grouping means no `compilation_ended`, `evaluation_ended` or notification runs for a discarded result.

5. In `_invalidate_submission_sync`, right after the dequeue loop as rewritten by Task 3 (the line `                    archive_sandbox=True))`) and before `            # Then we find all existing results in the database, and`, insert:

```python

            # Then we drop their results that a worker has already sent
            # but that are not written yet. If this invalidation fails
            # before committing, those results are lost, and the sweeper
            # computes them again.
            self._discard_cached_results(operations)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/service/evaluationservice_failures_test.py cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py -q`
Expected: `44 passed`.

Run: `.venv/bin/pyflakes cms/service/EvaluationService.py cms/service/flushingdict.py cmstestsuite/unit_tests/service/evaluationservice_failures_test.py cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py cmstestsuite/unit_tests/service/evaluationservice_lock_order_test.py cmstestsuite/unit_tests/service/EvaluationService_test.py cmstestsuite/unit_tests/service/flushingdict_test.py`
Expected: no output.

- [ ] **Step 5: Regression (spec 7)**

Run the asyncio group three times, each in its own process. Expected each time: `292 passed` (plus `19 subtests passed`). If `ProxyServiceTest.py::TestProxyService::test_startup` alone fails once, rerun that run (see Global Constraints); any other failure is a real one.
Run the gevent group. Expected: `5 passed`.

The two-image CI (`ubuntu:noble`, then `debian:bookworm`, one at a time) belongs to the rollout (spec 8), not to this task.

- [ ] **Step 6: Commit**

```bash
git add cms/service/EvaluationService.py \
    cmstestsuite/unit_tests/service/evaluationservice_failures_test.py \
    cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py
git commit -m "fix(es): never write a cached result of an invalidated operation

invalidate_submission now purges the results of the operations it
invalidates from the result cache, in-flight flush batches included,
and marks them so that a write already under way skips them.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Drop the private database**

Drop the database created in Global Constraints (same script, with `'DROP DATABASE "%s"'` instead of `'CREATE DATABASE "%s"'`), then `rm "$CMS_CONFIG_COPY"`. Then `ls "/tmp/${DB_NAME}-cms.toml"` must report that the file does not exist. Nothing is committed in this step.
