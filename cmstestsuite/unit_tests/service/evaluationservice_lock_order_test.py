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

"""Regression test for a lock-order deadlock in EvaluationService.

EvaluationService.post_finish_lock (a threading.RLock) and the
SQLAlchemy connection pool (5 + 10 overflow connections, 60 s timeout)
were taken in opposite orders: _write_results_sync, _missing_operations_sync
and _invalidate_submission_sync took the lock and then a connection,
while _new_submission_sync and _new_user_test_sync opened a session
(a connection) and then reached _enqueue_sync, which takes the lock. A
burst of new_submission/new_user_test RPCs could therefore hold every
pooled connection while blocked on the lock, with write_results holding
the lock while waiting for a connection: everything stalled until the
pool timeout fired.

The rule that prevents it is: take post_finish_lock before opening a
session/connection, never the reverse. This test pins the rule without
any timing or concurrency: it counts, per thread, the pooled
connections currently checked out, and records a violation whenever a
thread that does not already own post_finish_lock acquires it while
holding at least one connection. Each ES path that touches both the
database and the lock is then exercised sequentially.

The same checking lock also pins the rule that the event loop never
takes post_finish_lock (it would freeze every RPC while a _sync method
works), and a checking _pending_lock pins that FlushingDict.d_lock is
never taken while _pending_lock is held.

"""

import asyncio
import threading
import traceback
import unittest
from unittest.mock import patch

from sqlalchemy import event

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


class LockOrderCheckingRLock:
    """A threading.RLock recording acquisitions that invert the lock order.

    An acquisition by a thread that does not own the lock yet and
    currently holds at least one pooled connection is a violation of
    the "lock before connection" rule; reentrant acquisitions by the
    owner are fine, since the lock was already taken first.

    """

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

    def release(self):
        self._lock.release()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc_info):
        self.release()


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


class TestPostFinishLockOrder(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    async def asyncSetUp(self):
        # Same service scaffolding as write_results_creates_result_test.py.
        config_patcher = patch("cms.util.config.services", {})
        config_patcher.start()
        self.addCleanup(config_patcher.stop)

        address_patcher = patch(
            "cms.io.async_service.get_service_address",
            return_value=Address("127.0.0.1", 0))
        address_patcher.start()
        self.addCleanup(address_patcher.stop)

        rpc_address_patcher = patch(
            "cms.io.async_rpc.get_service_address",
            return_value=Address("127.0.0.1", 0))
        rpc_address_patcher.start()
        self.addCleanup(rpc_address_patcher.stop)

        # Count, per thread, the pooled connections currently checked out.
        self.connections_held = threading.local()

        def on_checkout(*args):
            self.connections_held.count = getattr(
                self.connections_held, "count", 0) + 1

        def on_checkin(*args):
            self.connections_held.count = getattr(
                self.connections_held, "count", 0) - 1

        pool = cms.db.engine.pool
        event.listen(pool, "checkout", on_checkout)
        event.listen(pool, "checkin", on_checkin)
        self.addCleanup(event.remove, pool, "checkout", on_checkout)
        self.addCleanup(event.remove, pool, "checkin", on_checkin)

        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(task=self.task, autojudge=True)
        self.task.active_dataset = self.dataset
        self.new_submission = self.add_submission(
            self.task, self.participation)
        self.old_submission = self.add_submission(
            self.task, self.participation)
        self.user_test = self.add_user_test(self.task, self.participation)
        self.session.commit()
        self.new_submission_id = self.new_submission.id
        self.new_submission_timestamp = self.new_submission.timestamp
        self.old_submission_id = self.old_submission.id
        self.user_test_id = self.user_test.id
        self.dataset_id = self.dataset.id
        # The fixture session must not keep a connection checked out on
        # the loop thread: it would make every acquisition there look
        # like a violation.
        self.session.close()
        self.assertEqual(getattr(self.connections_held, "count", 0), 0)

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def _build_service(self) -> EvaluationService:
        """Build an EvaluationService with a lock-order-checking lock."""
        with patch.object(
                EvaluationService, "start_sweeper", lambda self, timeout: None):
            service = EvaluationService(0)
        service._loop = asyncio.get_running_loop()
        self.addCleanup(service._disconnect_all)
        # Keep the executor's run() loop from dividing by zero (see
        # write_results_creates_result_test.py).
        service.get_executor().pool.add_worker(ServiceCoord("Worker", 0))
        service.post_finish_lock = LockOrderCheckingRLock(
            self.connections_held, threading.get_ident())
        return service

    def _make_compilation_result(self, operation: ESOperation) -> Result:
        job = CompilationJob(
            operation=operation,
            success=True,
            compilation_success=True,
            executables={},
            text=["Compilation succeeded"],
            sandboxes=[],
            sandbox_digests={},
            plus={
                "execution_time": 0.0,
                "execution_wall_clock_time": 0.0,
                "execution_memory": 0,
            })
        return Result(job, True)

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


if __name__ == "__main__":
    unittest.main()
