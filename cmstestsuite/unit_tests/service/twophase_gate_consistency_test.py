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

import gevent.monkey

gevent.monkey.patch_all()  # noqa

import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.db import Submission
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService


class TestSweeperRespectsTwoPhaseGate(DatabaseMixin, unittest.TestCase):
    """C1: the periodic sweeper must not bypass the two-phase gate."""

    def setUp(self):
        super().setUp()
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

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_sweeper_withholds_failed_groups_non_screening_testcases(self):
        service = EvaluationService(0)
        service._missing_operations()

        withheld = ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            "s1-02-normal")
        self.assertNotIn(withheld, service.get_executor())

    def test_sweeper_enqueues_it_when_two_phase_is_disabled(self):
        # Sanity check: with the flag off, the same testcase is real
        # (ungated) work and must still be enqueued by the sweeper, same
        # as before this feature existed.
        service = EvaluationService(0)
        service._missing_operations()

        expected = ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            "s1-02-normal")
        self.assertIn(expected, service.get_executor())


class TestInvalidateDoesNotPrematurelyFinalize(DatabaseMixin, unittest.TestCase):
    """C2: submission_enqueue_operations() must not finalize a result that
    is still missing evaluations for a failed group's testcases.

    """

    def setUp(self):
        super().setUp()
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

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_invalidating_one_skipped_testcase_does_not_lose_it(self):
        # Admin invalidates just the s1-02-normal evaluation (e.g. to
        # force a re-check). It still belongs to a failed group, so the
        # gate withholds it again; the fix must re-synthesize its skip
        # instead of finalizing the result without an evaluation for it.
        testcase_id = self.testcases["s1-02-normal"].id

        service = EvaluationService(0)
        service.invalidate_submission(
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

        # The result must only be marked evaluated once it genuinely has
        # an evaluation for every testcase again.
        self.assertTrue(result.evaluated())


if __name__ == "__main__":
    unittest.main()
