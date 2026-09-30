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

"""Tests that exercise the subtask dependency hooks of EvaluationService:
the skip in cascade of write_results() (through _advance_dependencies()),
and the sweeper choosing the dependency-aware path.

"""

import asyncio
import unittest
from unittest.mock import patch

from cms import config
from cms.conf import Address, ServiceCoord
from cms.db import Submission
from cms.grading.Job import EvaluationJob
from cms.grading.steps import EVALUATION_MESSAGES
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, Result
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


DAG_PARAMETERS = [
    {"max_score": 20, "testcases": 2},
    {"max_score": 30, "testcases": 1, "depends_on": [0]},
    {"max_score": 50, "testcases": 1, "depends_on": [1]},
]
CODENAMES = ["s0-00-sample", "s0-01-scr-wa", "s1-00-normal", "s2-00-normal"]


class WriteResultsFixtureMixin:
    """A GroupMin dataset with four testcases and one compiled submission.

    Subclasses set SCORE_TYPE_PARAMETERS. Must come before
    IsolatedAsyncioTestCase in the bases.

    """

    SCORE_TYPE_PARAMETERS: list

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
        self.dataset = self.add_dataset(
            task=self.task, autojudge=True, score_type="GroupMin",
            score_type_parameters=self.SCORE_TYPE_PARAMETERS)
        self.task.active_dataset = self.dataset
        self.testcases = {
            codename: self.add_testcase(self.dataset, codename=codename)
            for codename in CODENAMES
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

        Mirrors twophase_write_results_test.py's _build_service:
        constructs the service while this test's own event loop is
        already running, sets self._loop explicitly so thread-safe
        dispatch helpers take their call_soon_threadsafe branch as they
        would in production, and stubs out start_sweeper so a
        background sweep can't race with the test's own operations.

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

    def _op(self, codename):
        return ESOperation(ESOperation.EVALUATION, self.submission.id,
                           self.dataset.id, codename)

    def _current_result(self):
        self.session.expire_all()
        return Submission.get_from_id(
            self.submission.id, self.session).get_result(self.dataset)

    async def _write_with(self, service, outcomes):
        items = [(self._op(c), self._make_evaluation_result(
                    self._op(c), o, ["Output is correct"]))
                 for c, o in outcomes.items()]
        await service.write_results(items)
        return self._current_result()

    async def _write(self, **outcomes):
        return await self._write_with(self._build_service(), outcomes)

    @staticmethod
    def _enqueued_evaluation_codenames(enqueue_mock):
        return {call.args[0].testcase_codename
                for call in enqueue_mock.call_args_list
                if call.args[0].type_ == ESOperation.EVALUATION}


class TestSubtaskDependencyWriteResults(
    WriteResultsFixtureMixin, ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    SCORE_TYPE_PARAMETERS = DAG_PARAMETERS

    async def test_failed_root_skips_dependents_in_cascade(self):
        result = await self._write(
            **{"s0-00-sample": "1.0", "s0-01-scr-wa": "0.0"})
        texts = {e.codename: e.text for e in result.evaluations}
        message = EVALUATION_MESSAGES.get("skipped_dependency").message
        self.assertEqual(texts["s1-00-normal"], [message, "0"])
        self.assertEqual(texts["s2-00-normal"], [message, "1"])
        self.assertTrue(result.evaluated())

    async def test_passed_root_skips_nothing(self):
        result = await self._write(
            **{"s0-00-sample": "1.0", "s0-01-scr-wa": "1.0"})
        self.assertEqual({e.codename for e in result.evaluations},
                         {"s0-00-sample", "s0-01-scr-wa"})
        self.assertFalse(result.evaluated())

    @patch.object(config.global_, "two_phase_evaluation", True)
    async def test_screening_failure_of_root_skips_dependents_same_batch(
        self,
    ):
        # With two-phase on, the root's screening fails. Its own
        # non-screening testcase gets the two-phase skip, and its
        # dependents get the dependency skip, all in the same call.
        self.add_testcase(self.dataset, codename="s0-02-normal")
        self.dataset.score_type_parameters = [
            {"max_score": 20, "testcases": 3},
            {"max_score": 30, "testcases": 1, "depends_on": [0]},
            {"max_score": 50, "testcases": 1, "depends_on": [1]}]
        self.session.commit()
        result = await self._write(
            **{"s0-00-sample": "0.0", "s0-01-scr-wa": "1.0"})
        texts = {e.codename: e.text for e in result.evaluations}
        self.assertEqual(set(texts), set(CODENAMES) | {"s0-02-normal"})
        self.assertEqual(
            texts["s0-02-normal"], [EVALUATION_MESSAGES.get("skipped").message])
        message = EVALUATION_MESSAGES.get("skipped_dependency").message
        self.assertEqual(texts["s1-00-normal"], [message, "0"])
        self.assertEqual(texts["s2-00-normal"], [message, "1"])
        self.assertTrue(result.evaluated())

    async def test_passed_root_releases_its_dependent(self):
        service = self._build_service()
        with patch.object(service, "_enqueue_sync",
                          return_value=True) as enqueue:
            await self._write_with(
                service, {"s0-00-sample": "1.0", "s0-01-scr-wa": "1.0"})
        self.assertEqual(
            self._enqueued_evaluation_codenames(enqueue), {"s1-00-normal"})

    async def test_failed_root_skips_while_a_root_testcase_is_pending(self):
        result = await self._write(**{"s0-01-scr-wa": "0.0"})
        texts = {e.codename: e.text for e in result.evaluations}
        message = EVALUATION_MESSAGES.get("skipped_dependency").message
        self.assertEqual(texts["s1-00-normal"], [message, "0"])
        self.assertEqual(texts["s2-00-normal"], [message, "1"])
        self.assertFalse(result.evaluated())

    async def test_sweeper_finishes_a_half_done_result(self):
        self.add_evaluation(
            self.result, self.testcases["s0-00-sample"], outcome="1.0")
        self.add_evaluation(
            self.result, self.testcases["s0-01-scr-wa"], outcome="0.0")
        self.session.commit()
        service = self._build_service()
        with patch.object(service, "_enqueue_sync", return_value=True):
            service._missing_operations_sync()
        result = self._current_result()
        self.assertEqual({e.codename for e in result.evaluations},
                         set(CODENAMES))
        self.assertTrue(result.evaluated())

    async def test_invalid_dependencies_do_not_block_grading(self):
        # A cycle: the gate is off, so nothing is held nor skipped.
        self.dataset.score_type_parameters = [
            {"max_score": 20, "testcases": 2, "depends_on": [1]},
            {"max_score": 30, "testcases": 1, "depends_on": [0]},
            {"max_score": 50, "testcases": 1}]
        self.session.commit()
        result = await self._write(**{c: "1.0" for c in CODENAMES})
        self.assertTrue(result.evaluated())

    async def test_sweeper_takes_the_gated_path_when_dependencies_exist(
        self,
    ):
        service = self._build_service()
        with patch("cms.service.EvaluationService.get_submissions_operations"
                   ) as plain, \
                patch.object(service, "submission_enqueue_operations",
                             return_value=0) as gated:
            service._missing_operations_sync()
        plain.assert_not_called()
        gated.assert_called()


class TestWriteResultsParityWithoutDependencies(
    WriteResultsFixtureMixin, ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    SCORE_TYPE_PARAMETERS = [[20, 2], [30, 1], [50, 1]]

    async def test_failed_testcase_synthesizes_nothing(self):
        result = await self._write(
            **{"s0-00-sample": "1.0", "s0-01-scr-wa": "0.0"})
        self.assertEqual({e.codename for e in result.evaluations},
                         {"s0-00-sample", "s0-01-scr-wa"})

    async def test_writing_a_testcase_does_not_reenqueue(self):
        service = self._build_service()
        with patch.object(service, "submission_enqueue_operations",
                          return_value=0) as gated:
            await self._write_with(service, {"s0-00-sample": "1.0"})
        gated.assert_not_called()

    async def test_sweeper_takes_the_plain_path_without_dependencies(self):
        service = self._build_service()
        with patch("cms.service.EvaluationService.get_submissions_operations",
                   return_value=[]) as plain, \
                patch.object(service, "submission_enqueue_operations",
                             return_value=0) as gated:
            service._missing_operations_sync()
        plain.assert_called()
        gated.assert_not_called()


class TestUnusableDependenciesDoNotStallOthers(
    WriteResultsFixtureMixin, ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):
    """A dataset whose dependencies cannot be used is graded without them.

    The bad dataset is a GroupThreshold in dict form with "depends_on" but
    no "threshold": its gate builds, but reduce() raises on the first
    evaluated testcase. Whatever it does must not affect the healthy
    dataset of self.dataset (list form, no dependencies).

    """

    SCORE_TYPE_PARAMETERS = [[20, 2], [30, 1], [50, 1]]

    def _add_bad_submission(self):
        task = self.add_task(contest=self.contest)
        dataset = self.add_dataset(
            task=task, autojudge=True, score_type="GroupThreshold",
            score_type_parameters=[
                {"max_score": 50, "testcases": 1},
                {"max_score": 50, "testcases": 1, "depends_on": [0]}])
        task.active_dataset = dataset
        testcases = {
            codename: self.add_testcase(dataset, codename=codename)
            for codename in ["b-00", "b-01"]}
        submission, results = self.add_submission_with_results(
            task, self.participation, True)
        self.session.commit()
        return submission, results[0], testcases

    async def test_bad_result_does_not_stall_a_healthy_batchmate(self):
        bad_submission, bad_result, _ = self._add_bad_submission()
        bad_operation = ESOperation(
            ESOperation.EVALUATION, bad_submission.id,
            bad_result.dataset_id, "b-00")
        items = [(bad_operation, self._make_evaluation_result(
            bad_operation, "1.0", ["Output is correct"]))]
        items += [(self._op(c), self._make_evaluation_result(
                    self._op(c), "1.0", ["Output is correct"]))
                  for c in CODENAMES]
        service = self._build_service()
        await service.write_results(items)
        self.assertTrue(self._current_result().evaluated())

    async def test_sweeper_survives_the_bad_result(self):
        _, bad_result, testcases = self._add_bad_submission()
        self.add_evaluation(bad_result, testcases["b-00"], outcome="1.0")
        self.session.commit()
        service = self._build_service()
        with patch.object(service, "_enqueue_sync", return_value=False):
            service._missing_operations_sync()


class TestFinalizeOnlyCompleteResults(
    WriteResultsFixtureMixin, ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    SCORE_TYPE_PARAMETERS = [[20, 2], [30, 1], [50, 1]]

    async def test_zero_operations_with_missing_evaluations_is_not_finalized(
        self,
    ):
        service = self._build_service()
        with patch("cms.service.EvaluationService.submission_get_operations",
                   return_value=[]), \
                patch.object(service, "evaluation_ended") as ended, \
                self.assertLogs(
                    "cms.service.EvaluationService", "ERROR") as logs:
            service.submission_enqueue_operations(self.submission)
        ended.assert_not_called()
        self.assertFalse(self._current_result().evaluated())
        self.assertIn(f"{self.submission.id}({self.dataset.id})",
                      logs.records[0].getMessage())

    async def test_zero_operations_with_every_evaluation_is_finalized(self):
        for codename, testcase in self.testcases.items():
            self.add_evaluation(self.result, testcase, outcome="1.0")
        self.session.commit()
        service = self._build_service()
        with patch("cms.service.EvaluationService.submission_get_operations",
                   return_value=[]), \
                patch.object(service, "evaluation_ended") as ended:
            service.submission_enqueue_operations(self.submission)
        ended.assert_called_once()
        self.assertTrue(self._current_result().evaluated())


if __name__ == "__main__":
    unittest.main()
