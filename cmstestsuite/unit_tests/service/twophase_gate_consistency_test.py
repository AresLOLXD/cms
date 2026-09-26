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

"""Regression tests for two consistency bugs found in the final review of
two-phase fail-fast grading:

- the periodic sweeper (EvaluationService._missing_operations(), backed by
  get_submissions_operations()) used to bypass the two-phase gate entirely
  and force-enqueue withheld phase-2 testcases;
- submission_enqueue_operations()'s "0 operations left, finalize" branch
  used to wrongly finalize a submission result that still had unsynthesized
  skipped evaluations for a failed group (reachable e.g. via
  invalidate_submission() re-opening one previously-skipped testcase).

"""

import asyncio
import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin

from cms import config
from cms.conf import Address, ServiceCoord
from cms.db import Submission
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService


class TestSweeperRespectsTwoPhaseGate(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):
    """C1: the periodic sweeper must not bypass the two-phase gate."""

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
        self.add_evaluation(
            self.result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            self.result, self.testcases["s1-01-scr-wa"], outcome="0.0")
        # _missing_operations() opens its own DB session, so the fixture
        # needs to be committed, not just flushed.
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
            service = EvaluationService(shard=0)
        service._loop = asyncio.get_running_loop()
        self.addCleanup(service._disconnect_all)
        # EvaluationExecutor.max_operations_per_batch divides by
        # len(self.pool): with zero workers registered (the case here,
        # since config.services is patched to {}), the executor's
        # always-running background run() loop would crash with a
        # ZeroDivisionError as soon as anything is enqueued. Register
        # one placeholder worker (left unconnected, so acquire_worker
        # never actually hands operations to it).
        service.get_executor().pool.add_worker(ServiceCoord("Worker", 0))
        return service

    @patch.object(config.global_, "two_phase_evaluation", True)
    async def test_sweeper_withholds_failed_groups_non_screening_testcases(self):
        service = self._build_service()
        await service._missing_operations()

        withheld = ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            "s1-02-normal")
        self.assertNotIn(withheld, service.get_executor())

    async def test_sweeper_enqueues_it_when_two_phase_is_disabled(self):
        # Sanity check: with the flag off, the same testcase is real
        # (ungated) work and must still be enqueued by the sweeper, same
        # as before this feature existed.
        service = self._build_service()
        await service._missing_operations()

        expected = ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            "s1-02-normal")
        self.assertIn(expected, service.get_executor())


class TestInvalidateDoesNotPrematurelyFinalize(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):
    """C2: submission_enqueue_operations() must not finalize a result that
    is still missing evaluations for a failed group's testcases.

    """

    async def asyncSetUp(self):
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

        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(task=self.task, autojudge=True)
        self.task.active_dataset = self.dataset
        self.testcases = {
            codename: self.add_testcase(self.dataset, codename=codename)
            for codename in [
                "s1-00-sample", "s1-01-scr-wa", "s1-02-normal", "s1-03-normal",
            ]
        }
        self.submission, self.results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.result = self.results[0]
        # Screening already failed, and both non-screening testcases were
        # already synthesized as skipped (mirroring what _advance_two_phase
        # would have produced).
        self.add_evaluation(
            self.result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            self.result, self.testcases["s1-01-scr-wa"], outcome="0.0")
        self.add_evaluation(
            self.result, self.testcases["s1-02-normal"], outcome="0.0",
            text=["Skipped after screening phase failure"])
        self.add_evaluation(
            self.result, self.testcases["s1-03-normal"], outcome="0.0",
            text=["Skipped after screening phase failure"])
        self.result.set_evaluation_outcome()
        # invalidate_submission() opens its own DB session, so the
        # fixture needs to be committed, not just flushed.
        self.session.commit()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def _build_service(self) -> EvaluationService:
        """Build an EvaluationService safe to drive from a running loop.

        See TestSweeperRespectsTwoPhaseGate._build_service for details.

        """
        with patch.object(
                EvaluationService, "start_sweeper", lambda self, timeout: None):
            service = EvaluationService(shard=0)
        service._loop = asyncio.get_running_loop()
        self.addCleanup(service._disconnect_all)
        service.get_executor().pool.add_worker(ServiceCoord("Worker", 0))
        return service

    @patch.object(config.global_, "two_phase_evaluation", True)
    async def test_invalidating_one_skipped_testcase_does_not_lose_it(self):
        # Admin invalidates just the s1-02-normal evaluation (e.g. to
        # force a re-check). It still belongs to a failed group, so the
        # gate withholds it again; the fix must re-synthesize its skip
        # instead of finalizing the result without an evaluation for it.
        testcase_id = self.testcases["s1-02-normal"].id
        # The fixture already built the "correctly resynthesized" end
        # state (mirroring what _advance_two_phase would produce), so
        # the assertions below can't tell a genuine
        # delete-then-resynthesize from a no-op by codename/outcome
        # alone: capture the pre-invalidation row's id to confirm the
        # evaluation was actually replaced.
        old_evaluation_id = next(
            e.id for e in self.result.evaluations
            if e.codename == "s1-02-normal")

        service = self._build_service()
        await service.invalidate_submission(
            submission_id=self.submission.id,
            dataset_id=self.dataset.id,
            testcase_id=testcase_id,
            level="evaluation")

        self.session.expire_all()
        submission = Submission.get_from_id(self.submission.id, self.session)
        result = submission.get_result(self.dataset)

        codenames = {e.codename for e in result.evaluations}
        self.assertEqual(
            codenames,
            {"s1-00-sample", "s1-01-scr-wa", "s1-02-normal", "s1-03-normal"})

        resynthesized = next(
            e for e in result.evaluations if e.codename == "s1-02-normal")
        self.assertEqual(resynthesized.outcome, "0.0")
        self.assertNotEqual(resynthesized.id, old_evaluation_id)

        # The result must only be marked evaluated once it genuinely has
        # an evaluation for every testcase again.
        self.assertTrue(result.evaluated())


if __name__ == "__main__":
    unittest.main()
