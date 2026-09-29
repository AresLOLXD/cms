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

"""Tests for EvaluationService.write_results() when some of the objects
of its batch are gone.

A dataset, a submission or a user test can be deleted while a worker is
judging it. write_results() skips (and logs) the result of such an
object in its first loop, and must then skip it in the loops that follow
too, instead of crashing on the None that SubmissionResult.get_from_id()
or UserTestResult.get_from_id() returns. A crash would either roll back
the evaluation outcomes of the healthy results of the same batch, or
leave the healthy keys after the gone one without their notification to
ScoringService and their next operations.

"""

import gevent.monkey

gevent.monkey.patch_all()  # noqa

import unittest
from collections.abc import Sequence
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.db import Submission
from cms.grading.Job import CompilationJob, EvaluationJob
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, Result


# An id that no row of the DB has.
GONE_ID = 987654321


@dataclass
class Fixture:
    """A contest with one submission, and the objects it refers to."""

    contest: object
    task: object
    dataset: object
    submission: object

    def compilation(self) -> ESOperation:
        """Return the compilation operation of the submission."""
        return ESOperation(
            ESOperation.COMPILATION, self.submission.id, self.dataset.id)

    def evaluation(self, codename: str) -> ESOperation:
        """Return the evaluation operation of the submission on codename."""
        return ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            codename)

    @property
    def key(self) -> tuple[int, int]:
        """Return the (submission id, dataset id) ScoringService gets."""
        return (self.submission.id, self.dataset.id)


class TestWriteResultsOfGoneObjects(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.service = EvaluationService(0)
        # No ScoringService is reachable: record what ES tells it.
        self.service.scoring_service = MagicMock()

    def tearDown(self):
        # write_results() uses its own sessions, so the fixtures are
        # committed: clean them up, or later tests would see them.
        self.delete_data()
        super().tearDown()

    # -- helpers ---------------------------------------------------------

    def _add_fixture(
        self, testcases: Sequence[str] = (), compiled: bool = False
    ) -> Fixture:
        """Add a contest with the task and one submission of it.

        The dataset is the active one. The fixture is committed, since
        write_results() opens its own DB session.

        testcases: the codenames of the testcases of the dataset.
        compiled: whether the submission already has a result with a
            successful compilation.

        return: the fixture.

        """
        contest = self.add_contest()
        task = self.add_task(contest=contest)
        dataset = self.add_dataset(task=task, autojudge=True)
        task.active_dataset = dataset
        for codename in testcases:
            self.add_testcase(dataset, codename=codename)
        participation = self.add_participation(contest=contest)
        if compiled:
            submission, _ = self.add_submission_with_results(
                task, participation, True)
        else:
            submission = self.add_submission(
                task=task, participation=participation)
        self.session.commit()
        return Fixture(contest, task, dataset, submission)

    def _add_submission_of(self, fixture: Fixture) -> Submission:
        """Add another, brand new submission of the fixture's task."""
        participation = self.add_participation(contest=fixture.contest)
        submission = self.add_submission(
            task=fixture.task, participation=participation)
        self.session.commit()
        return submission

    @staticmethod
    def _failed_compilation(
        operation: ESOperation
    ) -> tuple[ESOperation, Result]:
        """Return a worker's answer: a compilation error of the contestant.

        ES takes it as a normal, final outcome, and reports it to
        ScoringService. Each operation needs its own job.

        """
        job = CompilationJob(
            operation=operation, success=True, compilation_success=False,
            text=["Compilation failed."], plus={})
        return operation, Result(job, True)

    @staticmethod
    def _evaluated(
        operation: ESOperation, outcome: str = "1.0"
    ) -> tuple[ESOperation, Result]:
        """Return a worker's answer for an evaluation operation.

        operation: the evaluation operation.
        outcome: the outcome of the testcase; "0.0" fails it.

        """
        job = EvaluationJob(
            operation=operation, success=True, outcome=outcome,
            text=["Output is correct"], shard=0, sandboxes=[],
            sandbox_digests={},
            plus={"execution_time": 0.1, "execution_wall_clock_time": 0.1,
                  "execution_memory": 1024})
        return operation, Result(job, True)

    def _load_result(self, fixture: Fixture):
        """Return the fixture's submission result, fresh from the DB."""
        self.session.expire_all()
        submission = Submission.get_from_id(
            fixture.submission.id, self.session)
        return submission.get_result(fixture.dataset)

    def _notified(self) -> list[tuple[int, int]]:
        """Return the (submission id, dataset id) ScoringService was told
        about.

        """
        return [
            (call.kwargs["submission_id"], call.kwargs["dataset_id"])
            for call in
            self.service.scoring_service.new_evaluation.call_args_list]

    def _logged(self, logs, level: str) -> list[str]:
        """Return the messages of the captured log records of a level.

        logs: the context assertLogs() returned.
        level: the level name to keep.

        """
        return [record.getMessage() for record in logs.records
                if record.levelname == level]

    # -- tests -----------------------------------------------------------

    def test_evaluation_on_a_gone_dataset_does_not_stop_its_batch(self):
        # A dataset deleted while a contestant's submission was being
        # evaluated on it: that result is skipped, even though its 0
        # evaluations match the 0 testcases of a dataset that is gone.
        # The last evaluation of another contestant's submission, in the
        # same batch, still completes it: its outcome is committed (read
        # back with the test's own session) and ScoringService is told
        # once.
        sibling = self._add_fixture(testcases=["t0"], compiled=True)
        other_submission = self._add_submission_of(sibling)
        on_gone_dataset = ESOperation(
            ESOperation.EVALUATION, other_submission.id, GONE_ID, "t0")

        with self.assertLogs(
                "cms.service.EvaluationService", level="INFO") as logs:
            self.service.write_results([
                self._evaluated(sibling.evaluation("t0")),
                self._evaluated(on_gone_dataset)])

        self.assertEqual(
            self._logged(logs, "ERROR"),
            ["Could not find dataset %d in the database." % GONE_ID])
        self.assertIn(
            "Result of submission %d(%d) not found, not ending its "
            "evaluation." % (other_submission.id, GONE_ID),
            self._logged(logs, "INFO"))
        result = self._load_result(sibling)
        self.assertEqual([e.codename for e in result.evaluations], ["t0"])
        self.assertEqual(result.evaluation_outcome, "ok")
        self.assertEqual(self._notified(), [sibling.key])

    def test_evaluation_of_a_gone_submission_does_not_stop_its_batch(self):
        # A submission deleted while it was being evaluated, on a dataset
        # that still has its testcases: its result is skipped, and since
        # its evaluations don't add up to the testcases, only the loop
        # that ends the operations meets it. The last evaluation of
        # another submission, after it in the batch, still completes
        # that one, which is notified once.
        sibling = self._add_fixture(testcases=["t0"], compiled=True)
        of_gone_submission = ESOperation(
            ESOperation.EVALUATION, GONE_ID, sibling.dataset.id, "t0")

        with self.assertLogs(
                "cms.service.EvaluationService", level="INFO") as logs:
            self.service.write_results([
                self._evaluated(of_gone_submission),
                self._evaluated(sibling.evaluation("t0"))])

        self.assertEqual(
            self._logged(logs, "ERROR"),
            ["Could not find submission %d in the database." % GONE_ID])
        self.assertIn(
            "Result of submission %d(%d) not found, not ending its "
            "evaluation." % (GONE_ID, sibling.dataset.id),
            self._logged(logs, "INFO"))
        result = self._load_result(sibling)
        self.assertEqual(result.evaluation_outcome, "ok")
        self.assertEqual(self._notified(), [sibling.key])

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_gone_keys_with_two_phase_on_do_not_stop_their_batch(self):
        # The real contests run with two-phase evaluation on. Next to the
        # results of a dataset and of a submission that are gone, a
        # submission whose screening passes gets its phase 2 queued, and
        # one whose screening fails gets the rest of its group skipped,
        # is finalized and is notified once.
        passing = self._add_fixture(
            testcases=["s1-00-sample", "s1-01-normal"], compiled=True)
        failing = self._add_fixture(
            testcases=["s1-00-sample", "s1-01-normal"], compiled=True)
        other_submission = self._add_submission_of(passing)
        on_gone_dataset = ESOperation(
            ESOperation.EVALUATION, other_submission.id, GONE_ID,
            "s1-00-sample")
        of_gone_submission = ESOperation(
            ESOperation.EVALUATION, GONE_ID, passing.dataset.id,
            "s1-00-sample")

        # The executor takes what is pushed out of its queue right away,
        # so record the pushes.
        executor = self.service.get_executor()
        pushed: list[ESOperation] = []
        real_push = executor._operation_queue.push

        def recording_push(item, *args, **kwargs):
            pushed.append(item)
            return real_push(item, *args, **kwargs)

        with patch.object(executor._operation_queue, "push", recording_push), \
                self.assertLogs(
                    "cms.service.EvaluationService", level="INFO") as logs:
            self.service.write_results([
                self._evaluated(on_gone_dataset),
                self._evaluated(passing.evaluation("s1-00-sample")),
                self._evaluated(of_gone_submission),
                self._evaluated(failing.evaluation("s1-00-sample"), "0.0")])

        self.assertEqual(
            sorted(self._logged(logs, "ERROR")),
            ["Could not find dataset %d in the database." % GONE_ID,
             "Could not find submission %d in the database." % GONE_ID])
        # Screening passed: phase 2 is queued, and nothing is final yet.
        self.assertEqual(pushed, [passing.evaluation("s1-01-normal")])
        self.assertIn(passing.evaluation("s1-01-normal"), executor)
        result = self._load_result(passing)
        self.assertEqual([e.codename for e in result.evaluations],
                         ["s1-00-sample"])
        self.assertIsNone(result.evaluation_outcome)
        # Screening failed: the rest is skipped, and the result is final.
        result = self._load_result(failing)
        self.assertEqual(
            sorted((e.codename, e.outcome) for e in result.evaluations),
            [("s1-00-sample", "0.0"), ("s1-01-normal", "0.0")])
        self.assertEqual(result.evaluation_outcome, "ok")
        self.assertEqual(self._notified(), [failing.key])

    def test_compilations_of_gone_keys_do_not_stop_their_batch(self):
        # The results of a dataset, a submission or a user test that is
        # gone are skipped and logged; the healthy results before and
        # after them in the same batch are still written, and notified to
        # ScoringService once each.
        first = self._add_fixture()
        second = self._add_fixture()
        no_dataset = ESOperation(
            ESOperation.COMPILATION, first.submission.id, GONE_ID)
        no_submission = ESOperation(
            ESOperation.COMPILATION, GONE_ID, first.dataset.id)
        no_user_test = ESOperation(
            ESOperation.USER_TEST_COMPILATION, GONE_ID, first.dataset.id)
        no_user_test_evaluation = ESOperation(
            ESOperation.USER_TEST_EVALUATION, GONE_ID, first.dataset.id)

        with self.assertLogs(
                "cms.service.EvaluationService", level="INFO") as logs:
            self.service.write_results([
                self._failed_compilation(first.compilation()),
                self._failed_compilation(no_dataset),
                self._failed_compilation(no_submission),
                self._failed_compilation(no_user_test),
                self._evaluated(no_user_test_evaluation),
                self._failed_compilation(second.compilation())])

        self.assertEqual(
            sorted(self._logged(logs, "ERROR")),
            ["Could not find %s in the database." % missing
             for missing in ("dataset %d" % GONE_ID,
                             "submission %d" % GONE_ID,
                             "user test %d" % GONE_ID,
                             "user test %d" % GONE_ID)])
        # The loop that ends the operations says what it skips too, with
        # ids only.
        self.assertEqual(
            [message for message in self._logged(logs, "INFO")
             if "not found, not ending" in message],
            ["Result of %s not found, not ending its %s." % skipped
             for skipped in (
                 ("submission %d(%d)" % (first.submission.id, GONE_ID),
                  "compilation"),
                 ("submission %d(%d)" % (GONE_ID, first.dataset.id),
                  "compilation"),
                 ("user test %d(%d)" % (GONE_ID, first.dataset.id),
                  "compilation"),
                 ("user test %d(%d)" % (GONE_ID, first.dataset.id),
                  "evaluation"))])
        for fixture in (first, second):
            self.assertTrue(self._load_result(fixture).compilation_failed())
        self.assertCountEqual(self._notified(), [first.key, second.key])


if __name__ == "__main__":
    unittest.main()
