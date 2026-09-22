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

"""Tests for EvaluationService's two-phase skip synthesis."""

# We enable monkey patching to make many libraries gevent-friendly.
import gevent.monkey

gevent.monkey.patch_all()  # noqa

import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.service.EvaluationService import EvaluationService


class TestAdvanceTwoPhase(DatabaseMixin, unittest.TestCase):

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
                "s2-00-sample", "s2-01-scr-wa", "s2-02-normal",
            ]
        }
        self.submission, self.results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.result = self.results[0]
        self.session.flush()

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_synthesizes_skipped_evaluations_for_failed_group(self):
        self.add_evaluation(
            self.result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            self.result, self.testcases["s1-01-scr-wa"], outcome="0.0")
        self.session.flush()

        service = EvaluationService(0)
        service._advance_two_phase(self.session, self.result)

        codenames = {e.codename for e in self.result.evaluations}
        self.assertEqual(
            codenames,
            {"s1-00-sample", "s1-01-scr-wa", "s1-02-normal", "s1-03-normal"})
        for codename in ("s1-02-normal", "s1-03-normal"):
            evaluation = next(
                e for e in self.result.evaluations if e.codename == codename)
            self.assertEqual(evaluation.outcome, "0.0")
            self.assertEqual(evaluation.evaluation_sandbox_paths, [])
            self.assertEqual(evaluation.evaluation_sandbox_digests, [])

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_no_op_while_screening_still_pending(self):
        self.add_evaluation(
            self.result, self.testcases["s1-00-sample"], outcome="1.0")
        self.session.flush()

        service = EvaluationService(0)
        service._advance_two_phase(self.session, self.result)

        codenames = {e.codename for e in self.result.evaluations}
        self.assertEqual(codenames, {"s1-00-sample"})

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_no_op_when_screening_passed(self):
        self.add_evaluation(
            self.result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            self.result, self.testcases["s1-01-scr-wa"], outcome="1.0")
        self.session.flush()

        service = EvaluationService(0)
        service._advance_two_phase(self.session, self.result)

        codenames = {e.codename for e in self.result.evaluations}
        self.assertEqual(codenames, {"s1-00-sample", "s1-01-scr-wa"})

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_two_groups_failed_group_synthesized_pending_group_untouched(
            self):
        # s1's screening fails; s2's screening is still pending (only its
        # sample has been evaluated).
        self.add_evaluation(
            self.result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            self.result, self.testcases["s1-01-scr-wa"], outcome="0.0")
        self.add_evaluation(
            self.result, self.testcases["s2-00-sample"], outcome="1.0")
        self.session.flush()

        service = EvaluationService(0)
        service._advance_two_phase(self.session, self.result)

        codenames = {e.codename for e in self.result.evaluations}
        # s1's remaining testcases were synthesized as skipped, s2 was
        # left completely untouched (no synthesized evaluations for its
        # non-screening testcase, since its screening hasn't concluded).
        self.assertEqual(
            codenames,
            {"s1-00-sample", "s1-01-scr-wa", "s1-02-normal", "s1-03-normal",
             "s2-00-sample"})
        for codename in ("s1-02-normal", "s1-03-normal"):
            evaluation = next(
                e for e in self.result.evaluations if e.codename == codename)
            self.assertEqual(evaluation.outcome, "0.0")


if __name__ == "__main__":
    unittest.main()
