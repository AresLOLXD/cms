#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
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

"""Tests for EvaluationService when the database connection is lost
(#39: 8b, the write path; 8a, the phase-2 window).

"""

import asyncio
import socket
import unittest
from unittest.mock import MagicMock, call, patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import IntegrityError, OperationalError, \
    PendingRollbackError
from sqlalchemy.orm import sessionmaker

from cms.conf import Address, ServiceCoord, config
from cms.db import Dataset, Submission, SubmissionResult, engine
from cms.db.async_session import async_engine
from cms.db.connect_options import with_connection_defaults
from cms.service.esoperations import ESOperation, \
    get_submission_results_to_finalize
from cms.service.EvaluationService import EvaluationService, Result
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.dbfault import terminate_backends
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


LOST = ("Lost the database connection while writing worker results "
        "(%s); the sweeper will redo them within about 2 minutes.")


class DeadlockDetected(Exception):
    """A driver error with the SQLSTATE of a deadlock (40P01)."""

    pgcode = "40P01"


class EvaluationServiceDatabaseTestCase(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):
    """An EvaluationService on the test database, with one compiled
    submission of a task with TESTCASES testcases.

    """

    TESTCASES = 3

    async def asyncSetUp(self):
        # As in write_results_creates_result_test.py: no real Worker,
        # LogService or ScoringService is reachable.
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

        contest = self.add_contest()
        task = self.add_task(contest=contest)
        self.dataset = self.add_dataset(task=task)
        task.active_dataset = self.dataset
        self.testcases = [
            self.add_testcase(self.dataset, codename="t%d" % index)
            for index in range(self.TESTCASES)]
        participation = self.add_participation(contest=contest)
        self.submission, (self.submission_result,) = \
            self.add_submission_with_results(task, participation, True)
        # The service reads and writes with its own sessions.
        self.session.commit()

        # Built as in write_results_creates_result_test.py, with the
        # sweeper's timer stubbed out: the tests sweep by themselves.
        with patch.object(EvaluationService, "start_sweeper",
                          lambda self, timeout: None):
            self.service = EvaluationService(0)
        self.service._loop = asyncio.get_running_loop()
        self.addCleanup(self.service._disconnect_all)
        self.service.get_executor().pool.add_worker(
            ServiceCoord("Worker", 0))
        # There is no ScoringService: record the notifications.
        self.service._threadsafe_notify_scoring_service = MagicMock()

    async def asyncTearDown(self):
        for task in list(self.service._background_tasks):
            task.cancel()
        await async_engine.dispose()

    def tearDown(self):
        # The sweeper looks at every submission of the database.
        self.delete_data()
        super().tearDown()

    def _evaluation_items(self) -> list[tuple[ESOperation, Result]]:
        """Return a worker result for each testcase of the submission."""
        return [(ESOperation(ESOperation.EVALUATION, self.submission.id,
                             self.dataset.id, testcase.codename),
                 Result(MagicMock(), True))
                for testcase in self.testcases]

    def _load_result(self) -> SubmissionResult:
        """Return the submission's result, read again from the DB."""
        self.session.expire_all()
        submission = Submission.get_from_id(self.submission.id, self.session)
        return submission.get_result(self.dataset)


class TestWriteResultsOnALostConnection(EvaluationServiceDatabaseTestCase):

    async def test_a_disconnect_on_the_first_row_stops_the_batch(self):
        lost = OperationalError(
            "INSERT", {},
            Exception("server closed the connection unexpectedly"),
            connection_invalidated=True)
        with patch.object(self.service, "write_results_one_row",
                          side_effect=lost) as write_row, \
                self.assertLogs("cms.service.EvaluationService",
                                "ERROR") as logs:
            await self.service.write_results(self._evaluation_items())
        # The later rows are not attempted.
        self.assertEqual(write_row.call_count, 1)
        self.assertEqual(
            [record.getMessage() for record in logs.records],
            [LOST % "server closed the connection unexpectedly"])

    async def test_an_unusable_transaction_stops_the_batch_too(self):
        with patch.object(self.service, "write_results_one_row",
                          side_effect=PendingRollbackError("rolled back")) \
                as write_row:
            # Not a lost connection: FlushingDict logs it, as before.
            with self.assertRaises(PendingRollbackError):
                await self.service.write_results(self._evaluation_items())
        self.assertEqual(write_row.call_count, 1)

    async def test_a_disconnect_at_the_first_query_is_one_error(self):
        items = self._evaluation_items()
        self.session.commit()
        # Leave one idle connection in the pool, and cut it: the flush
        # gets it, as after a server restart.
        engine.dispose()
        with engine.connect() as connection:
            pid = connection.execute(
                text("SELECT pg_backend_pid()")).scalar_one()
        self.assertEqual(terminate_backends("pid = %s", (pid,)), 1)
        for operation, result in items:
            self.service.result_cache.add(operation, result)
        with self.assertLogs("cms.service.EvaluationService",
                             "ERROR") as logs, \
                self.assertNoLogs("cms.service.flushingdict", "ERROR"):
            await self.service.result_cache.flush()
        self.assertEqual(len(logs.records), 1)
        message = logs.records[0].getMessage()
        # The driver's text varies with the libpq version.
        self.assertTrue(message.startswith(
            "Lost the database connection while writing worker results ("))
        self.assertTrue(message.endswith(
            "); the sweeper will redo them within about 2 minutes."))

    async def test_a_failed_connect_at_the_first_query_is_one_error(self):
        items = self._evaluation_items()
        self.session.commit()
        silent = socket.socket()
        self.addCleanup(silent.close)
        silent.bind(("127.0.0.1", 0))
        silent.listen(8)
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        closed_port = closed.getsockname()[1]
        closed.close()
        base = make_url(config.database.url)
        for case, port, expected in (
                ("refused", closed_port, "Connection refused"),
                ("silent", silent.getsockname()[1], "timeout expired")):
            with self.subTest(case=case):
                failing = create_engine(with_connection_defaults(base.set(
                    host="127.0.0.1", port=port,
                    query={"connect_timeout": "2"})))
                self.addCleanup(failing.dispose)
                with patch("cms.db.session.Session", sessionmaker(failing)), \
                        self.assertLogs("cms.service.EvaluationService",
                                        "ERROR") as logs:
                    await self.service.write_results(items)
                self.assertEqual(len(logs.records), 1)
                self.assertIn(expected, logs.records[0].getMessage())

    async def test_other_errors_still_go_row_by_row(self):
        errors = [IntegrityError("INSERT", {}, Exception("duplicate key")),
                  ValueError("bad row"), None]
        with patch.object(self.service, "write_results_one_row",
                          side_effect=errors) as write_row, \
                self.assertLogs("cms.service.EvaluationService",
                                "WARNING") as logs:
            await self.service.write_results(self._evaluation_items())
        self.assertEqual(write_row.call_count, 3)
        messages = [record.getMessage() for record in logs.records]
        self.assertIn("Integrity error while inserting worker result.",
                      messages)
        self.assertIn("Unexpected exception while inserting worker result.",
                      messages)
        self.assertEqual([message for message in messages
                          if message.startswith("Lost the database")], [])

    async def test_a_deadlock_is_not_a_lost_connection(self):
        # An OperationalError too, but with a SQLSTATE: the data, not the
        # connection, is at fault. A rule of "any OperationalError" would
        # drop these batches and hide the deadlock.
        deadlock = OperationalError("INSERT", {}, DeadlockDetected())
        with patch.object(self.service, "write_results_one_row",
                          side_effect=deadlock) as write_row, \
                self.assertLogs("cms.service.EvaluationService",
                                "ERROR") as logs:
            await self.service.write_results(self._evaluation_items())
        self.assertEqual(write_row.call_count, 3)
        messages = [record.getMessage() for record in logs.records]
        self.assertEqual(
            messages, ["Unexpected exception while inserting worker "
                       "result."] * 3)

    async def test_a_deadlock_at_the_first_query_propagates(self):
        deadlock = OperationalError("SELECT", {}, DeadlockDetected())
        with patch.object(Dataset, "get_from_id", side_effect=deadlock), \
                self.assertNoLogs("cms.service.EvaluationService", "ERROR"):
            # FlushingDict's generic handling must still see it.
            with self.assertRaises(OperationalError) as raised:
                await self.service.write_results(self._evaluation_items())
        self.assertIs(raised.exception, deadlock)


class TestPlainSweepFinalizesCutResults(EvaluationServiceDatabaseTestCase):
    """8a: a write cut between the two commits of _write_results_sync
    leaves every evaluation stored but no outcome.

    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # The plain path: no two-phase grading (and no dependencies).
        patcher = patch.object(config.global_, "two_phase_evaluation", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _add_evaluations(self, submission_result: SubmissionResult,
                         testcases: list) -> None:
        for testcase in testcases:
            self.add_evaluation(submission_result, testcase)
        self.session.commit()

    async def test_a_complete_result_without_outcome_is_finalized(self):
        self._add_evaluations(self.submission_result, self.testcases)
        submission_id, dataset_id = self.submission.id, self.dataset.id
        with self.assertLogs("cms.service.EvaluationService",
                             "WARNING") as logs:
            await self.service._missing_operations()
        self.assertTrue(self._load_result().evaluated())
        self.assertEqual(
            [record.getMessage() for record in logs.records],
            ["Finalized result %d(%d): all its evaluations were stored "
             "but its outcome was not (a write was cut)."
             % (submission_id, dataset_id)])
        self.service._threadsafe_notify_scoring_service \
            .assert_called_once_with(submission_id=submission_id,
                                     dataset_id=dataset_id)

    async def test_a_result_with_a_missing_evaluation_is_left_alone(self):
        self._add_evaluations(self.submission_result, self.testcases[:-1])
        with self.assertNoLogs("cms.service.EvaluationService", "WARNING"):
            await self.service._missing_operations()
        self.assertFalse(self._load_result().evaluated())
        self.service._threadsafe_notify_scoring_service.assert_not_called()

    async def test_only_unfinalized_complete_results_of_the_contest(self):
        self._add_evaluations(self.submission_result, self.testcases)
        task = self.dataset.task
        # The same, already finalized: not selected.
        _, (finished,) = self.add_submission_with_results(
            task, self.submission.participation, True)
        self._add_evaluations(finished, self.testcases)
        finished.set_evaluation_outcome()
        # Complete and without outcome, in another contest.
        other_contest = self.add_contest()
        other_task = self.add_task(contest=other_contest)
        other_dataset = self.add_dataset(task=other_task)
        other_task.active_dataset = other_dataset
        other_testcase = self.add_testcase(other_dataset)
        _, (other,) = self.add_submission_with_results(
            other_task, self.add_participation(contest=other_contest), True)
        self.add_evaluation(other, other_testcase)
        self.session.commit()

        def keys(contest_id: int | None) -> list[tuple[int, int]]:
            return sorted(
                (result.submission_id, result.dataset_id)
                for result in get_submission_results_to_finalize(
                    self.session, contest_id))

        mine = (self.submission.id, self.dataset.id)
        theirs = (other.submission_id, other.dataset_id)
        self.assertEqual(keys(task.contest_id), [mine])
        self.assertEqual(keys(other_contest.id), [theirs])
        self.assertEqual(keys(None), sorted([mine, theirs]))

    async def test_a_sibling_dataset_result_is_finalized_only_once(self):
        # evaluation_ended() of the first result finalizes the other one
        # (it ends with submission_enqueue_operations(), which goes over
        # every dataset to judge): the sweep must not do it again.
        sibling = self.add_dataset(task=self.dataset.task, autojudge=True)
        sibling_testcases = [
            self.add_testcase(sibling, codename="s%d" % index)
            for index in range(self.TESTCASES)]
        sibling_result = self.add_submission_result(self.submission, sibling)
        sibling_result.set_compilation_outcome(True)
        self._add_evaluations(self.submission_result, self.testcases)
        self._add_evaluations(sibling_result, sibling_testcases)
        submission_id = self.submission.id
        dataset_ids = [self.dataset.id, sibling.id]
        with self.assertLogs("cms.service.EvaluationService",
                             "WARNING") as logs:
            await self.service._missing_operations()
        self.assertEqual(
            sorted(record.getMessage() for record in logs.records),
            sorted("Finalized result %d(%d): all its evaluations were "
                   "stored but its outcome was not (a write was cut)."
                   % (submission_id, dataset_id)
                   for dataset_id in dataset_ids))
        notify = self.service._threadsafe_notify_scoring_service
        self.assertEqual(notify.call_count, 2)
        notify.assert_has_calls(
            [call(submission_id=submission_id, dataset_id=dataset_id)
             for dataset_id in dataset_ids], any_order=True)


class TestGatedSweepFinalizesSilently(EvaluationServiceDatabaseTestCase):
    """The gated sweep finalizes a result without outcome through
    submission_enqueue_operations(), as before, with no 8a WARNING.

    """

    async def _assert_finalized_silently(self) -> None:
        submission_id, dataset_id = self.submission.id, self.dataset.id
        self.session.commit()
        with self.assertNoLogs("cms.service.EvaluationService", "WARNING"):
            await self.service._missing_operations()
        self.assertTrue(self._load_result().evaluated())
        self.service._threadsafe_notify_scoring_service \
            .assert_called_once_with(submission_id=submission_id,
                                     dataset_id=dataset_id)

    async def test_with_two_phase_grading(self):
        for testcase in self.testcases:
            self.add_evaluation(self.submission_result, testcase)
        with patch.object(config.global_, "two_phase_evaluation", True):
            await self._assert_finalized_silently()

    async def test_with_subtask_dependencies(self):
        codenames = ["s0-00-sample", "s0-01-normal", "s1-00-normal"]
        for testcase, codename in zip(self.testcases, codenames):
            testcase.codename = codename
            self.add_evaluation(self.submission_result, testcase,
                                outcome="1.0")
        self.dataset.score_type = "GroupMin"
        self.dataset.score_type_parameters = [
            {"max_score": 50, "testcases": 2},
            {"max_score": 50, "testcases": 1, "depends_on": [0]}]
        with patch.object(config.global_, "two_phase_evaluation", False):
            await self._assert_finalized_silently()


if __name__ == "__main__":
    unittest.main()
