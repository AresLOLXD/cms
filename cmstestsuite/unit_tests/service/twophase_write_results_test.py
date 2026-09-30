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

"""Tests that exercise EvaluationService.write_results() directly (rather
than calling submission_enqueue_operations() or _advance_two_phase() in
isolation), to cover the two new two-phase call sites inside it: the
_advance_two_phase() skip-synthesis loop, and the phase-2 release
(submission_enqueue_operations()) in the "ending operations" loop.

"""

import asyncio
import unittest
from unittest.mock import patch

from cms import config
from cms.conf import Address, ServiceCoord
from cms.db import Submission
from cms.grading.Job import EvaluationJob
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, Result
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


class TestTwoPhaseWriteResults(
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
        self.testcases = {
            codename: self.add_testcase(self.dataset, codename=codename)
            for codename in ["s1-00-sample", "s1-01-scr-wa", "s1-02-normal"]
        }
        self.submission, self.results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.result = self.results[0]
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
    def _make_evaluation_result(operation, outcome, text):
        job = EvaluationJob(
            operation=operation,
            outcome=outcome,
            text=text,
            success=True,
            shard=0,
            sandboxes=[],
            sandbox_digests={},
            plus={
                "execution_time": 0.0,
                "execution_wall_clock_time": 0.0,
                "execution_memory": 0,
            })
        return Result(job, True)

    @patch.object(config.global_, "two_phase_evaluation", True)
    async def test_write_results_synthesizes_skips_and_completes_evaluation(
        self,
    ):
        sample_op = ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            "s1-00-sample")
        scr_op = ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            "s1-01-scr-wa")

        items = [
            (sample_op, self._make_evaluation_result(
                sample_op, "1.0", ["Output is correct"])),
            (scr_op, self._make_evaluation_result(
                scr_op, "0.0", ["Output isn't correct"])),
        ]

        service = self._build_service()
        await service.write_results(items)

        self.session.expire_all()
        submission = Submission.get_from_id(self.submission.id, self.session)
        result = submission.get_result(self.dataset)

        codenames = {e.codename for e in result.evaluations}
        self.assertEqual(
            codenames, {"s1-00-sample", "s1-01-scr-wa", "s1-02-normal"})

        skipped = next(
            e for e in result.evaluations if e.codename == "s1-02-normal")
        self.assertEqual(skipped.outcome, "0.0")
        self.assertEqual(
            skipped.text, ["Skipped after screening phase failure"])

        # All testcases now have an evaluation (two real, one
        # synthesized), so the result should be fully (and correctly)
        # evaluated, not stuck waiting forever.
        self.assertTrue(result.evaluated())


if __name__ == "__main__":
    unittest.main()
