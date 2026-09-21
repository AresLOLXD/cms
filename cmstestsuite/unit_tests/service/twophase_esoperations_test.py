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

"""Tests for the two-phase screening gate in submission_get_operations()."""

import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.service.esoperations import ESOperation, submission_get_operations


class TestSubmissionGetOperationsTwoPhase(DatabaseMixin, unittest.TestCase):

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
                "s1-00-sample", "s1-01-scr-wa", "s1-02-normal",
                "s2-00-sample", "s2-01-normal",
            ]
        }
        self.session.flush()

    def _evaluation_codenames(self, submission_result, submission):
        return set(
            op.testcase_codename
            for op, _, _ in submission_get_operations(
                submission_result, submission, self.dataset)
            if op.type_ == ESOperation.EVALUATION)

    def test_disabled_yields_all_unevaluated_testcases(self):
        submission, results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.session.flush()

        self.assertEqual(
            self._evaluation_codenames(results[0], submission),
            set(self.testcases.keys()))

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_enabled_withholds_non_screening_until_screening_passes(self):
        submission, results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.session.flush()

        self.assertEqual(
            self._evaluation_codenames(results[0], submission),
            {"s1-00-sample", "s1-01-scr-wa", "s2-00-sample"})

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_enabled_releases_group_once_its_screening_passes(self):
        submission, results = self.add_submission_with_results(
            self.task, self.participation, True)
        result = results[0]
        self.add_evaluation(
            result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            result, self.testcases["s1-01-scr-wa"], outcome="1.0")
        self.session.flush()

        self.assertEqual(
            self._evaluation_codenames(result, submission),
            {"s1-02-normal", "s2-00-sample"})

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_enabled_withholds_group_whose_screening_failed(self):
        submission, results = self.add_submission_with_results(
            self.task, self.participation, True)
        result = results[0]
        self.add_evaluation(
            result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            result, self.testcases["s1-01-scr-wa"], outcome="0.0")
        self.session.flush()

        self.assertEqual(
            self._evaluation_codenames(result, submission),
            {"s2-00-sample"})


if __name__ == "__main__":
    unittest.main()
