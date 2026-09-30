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
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, Result
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


class LockOrderCheckingRLock:
    """A threading.RLock recording acquisitions that invert the lock order.

    An acquisition by a thread that does not own the lock yet and
    currently holds at least one pooled connection is a violation of
    the "lock before connection" rule; reentrant acquisitions by the
    owner are fine, since the lock was already taken first.

    """

    def __init__(self, connections_held: threading.local):
        self._lock = threading.RLock()
        self._connections_held = connections_held
        self.violations: list[list[str]] = []
        self.acquisitions = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
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
            self.connections_held)
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

    async def test_lock_is_taken_before_a_connection(self):
        service = self._build_service()
        lock = service.post_finish_lock
        compilation = ESOperation(
            ESOperation.COMPILATION, self.old_submission_id, self.dataset_id)

        paths = {
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

        violations_by_path = {}
        for name, start in paths.items():
            acquisitions_before = lock.acquisitions
            violations_before = len(lock.violations)
            await start()
            # Let the callbacks scheduled on the loop (which take the
            # lock themselves) run before looking at the counters.
            await asyncio.sleep(0.05)
            self.assertGreater(
                lock.acquisitions, acquisitions_before,
                "%s never acquired post_finish_lock" % name)
            violations_by_path[name] = lock.violations[violations_before:]

        # Only paths that inverted the order have a non-empty entry.
        self.assertEqual(
            {name: v for name, v in violations_by_path.items() if v}, {})


if __name__ == "__main__":
    unittest.main()
