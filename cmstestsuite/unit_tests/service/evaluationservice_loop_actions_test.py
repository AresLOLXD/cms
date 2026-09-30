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
from unittest.mock import MagicMock, patch

from cms.conf import Address, ServiceCoord
from cms.io.async_triggeredservice import AsyncTriggeredService
from cms.io.priorityqueue import PriorityQueue
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, Result, \
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
