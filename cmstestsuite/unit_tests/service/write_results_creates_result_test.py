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

"""Regression test for the SQLAlchemy 2.0 migration dropping the implicit
backref save-update cascade: Submission.get_result_or_create() and
UserTest.get_result_or_create() used to rely on the cascade to get a
freshly-constructed SubmissionResult/UserTestResult into the session, but
2.0 removed cascade_backrefs for back_populates relationships. Without an
explicit session.add(), the first compilation of every new submission (or
user test) was silently dropped at commit and never actually persisted,
breaking the grading pipeline for all new work.

These tests call EvaluationService.write_results() -- the only place
results are first created in production -- for a submission/user test that
has NO existing result, then re-fetch from a fresh session/query to prove
the result row was actually written to the database.

"""

import asyncio
import unittest
from unittest.mock import patch

from cms.conf import Address, ServiceCoord
from cms.db import Submission, UserTest
from cms.grading.Job import CompilationJob
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, Result
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


class TestWriteResultsCreatesNewResult(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    async def asyncSetUp(self):
        # EvaluationService.__init__ (via WorkerPool.__init__, which calls
        # get_service_shards("Worker") defined in cms.util) would
        # otherwise try to connect to every configured Worker shard.
        config_patcher = patch("cms.util.config.services", {})
        config_patcher.start()
        self.addCleanup(config_patcher.stop)

        address_patcher = patch(
            "cms.io.async_service.get_service_address",
            return_value=Address("127.0.0.1", 0))
        address_patcher.start()
        self.addCleanup(address_patcher.stop)

        # EvaluationService.__init__ connects to LogService (via
        # AsyncService.__init__) and to ScoringService: both go through
        # async_rpc's own imported reference to get_service_address.
        rpc_address_patcher = patch(
            "cms.io.async_rpc.get_service_address",
            return_value=Address("127.0.0.1", 0))
        rpc_address_patcher.start()
        self.addCleanup(rpc_address_patcher.stop)

        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(task=self.task, autojudge=True)
        self.task.active_dataset = self.dataset
        # write_results() opens its own DB session (SessionGen()), so the
        # fixture needs to be actually committed, not just flushed, to be
        # visible to it.
        self.session.commit()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def _build_service(self) -> EvaluationService:
        """Build an EvaluationService safe to drive from a running loop.

        Mirrors EvaluationService_test.py's _build_service: constructs
        the service while this test's own event loop is already
        running, sets self._loop explicitly so thread-safe dispatch
        helpers take their call_soon_threadsafe branch as they would in
        production, and stubs out start_sweeper so a background sweep
        can't race with the test's own operations.

        """
        with patch.object(
                EvaluationService, "start_sweeper", lambda self, timeout: None):
            service = EvaluationService(0)
        service._loop = asyncio.get_running_loop()
        self.addCleanup(service._disconnect_all)
        # EvaluationExecutor.max_operations_per_batch divides by
        # len(self.pool): with zero workers registered (the case here,
        # since config.services is patched to {}), the executor's
        # always-running background run() loop would crash with a
        # ZeroDivisionError as soon as anything is enqueued. Register
        # one placeholder worker (left unconnected).
        service.get_executor().pool.add_worker(ServiceCoord("Worker", 0))
        return service

    @staticmethod
    def _make_compilation_result(operation):
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

    async def test_submission_result_is_persisted_on_first_compilation(self):
        # No pre-existing SubmissionResult: get_result_or_create() has to
        # construct one from scratch inside write_results().
        submission = self.add_submission(self.task, self.participation)
        self.session.commit()

        operation = ESOperation(
            ESOperation.COMPILATION, submission.id, self.dataset.id)
        items = [(operation, self._make_compilation_result(operation))]

        service = self._build_service()
        await service.write_results(items)

        # Re-fetch from a fresh session/query to make sure the row was
        # actually committed to the database, not merely present on the
        # in-memory object graph.
        self.session.expire_all()
        fetched_submission = Submission.get_from_id(
            submission.id, self.session)
        result = fetched_submission.get_result(self.dataset)

        self.assertIsNotNone(result)
        self.assertTrue(result.compiled())
        self.assertTrue(result.compilation_succeeded())

    async def test_user_test_result_is_persisted_on_first_compilation(self):
        # No pre-existing UserTestResult: get_result_or_create() has to
        # construct one from scratch inside write_results().
        user_test = self.add_user_test(self.task, self.participation)
        self.session.commit()

        operation = ESOperation(
            ESOperation.USER_TEST_COMPILATION, user_test.id, self.dataset.id)
        items = [(operation, self._make_compilation_result(operation))]

        service = self._build_service()
        await service.write_results(items)

        self.session.expire_all()
        fetched_user_test = UserTest.get_from_id(user_test.id, self.session)
        result = fetched_user_test.get_result(self.dataset)

        self.assertIsNotNone(result)
        self.assertTrue(result.compiled())
        self.assertTrue(result.compilation_succeeded())


if __name__ == "__main__":
    unittest.main()
