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

"""Tests for the subtask dependency gate in submission_get_operations()."""

import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.grading.steps import EVALUATION_MESSAGES
from cms.service.esoperations import ESOperation, \
    any_dataset_declares_dependencies, submission_get_operations


DAG_PARAMETERS = [
    {"max_score": 20, "testcases": 3},
    {"max_score": 30, "testcases": 2, "depends_on": [0]},
    {"max_score": 50, "testcases": 1},
]
CODENAMES = [
    "s0-00-sample", "s0-01-scr-wa", "s0-02-normal",
    "s1-00-sample", "s1-01-normal",
    "s2-00-sample",
]


class TestSubtaskDependencyGate(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(
            task=self.task, autojudge=True, score_type="GroupMin",
            score_type_parameters=DAG_PARAMETERS)
        self.task.active_dataset = self.dataset
        self.testcases = {
            codename: self.add_testcase(self.dataset, codename=codename)
            for codename in CODENAMES}
        self.submission, results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.result = results[0]
        self.session.flush()

    def _released(self):
        return set(
            op.testcase_codename
            for op, _, _ in submission_get_operations(
                self.result, self.submission, self.dataset)
            if op.type_ == ESOperation.EVALUATION)

    def _evaluate(self, **outcomes):
        for codename, outcome in outcomes.items():
            self.add_evaluation(
                self.result, self.testcases[codename], outcome=outcome)
        self.session.flush()

    def test_message_is_registered(self):
        message = EVALUATION_MESSAGES.get("skipped_dependency").message
        self.assertIn("%s", message)

    def test_dependents_wait_for_their_dependencies(self):
        self.assertEqual(self._released(), {
            "s0-00-sample", "s0-01-scr-wa", "s0-02-normal", "s2-00-sample"})

    def test_passed_dependency_releases_the_dependent(self):
        self._evaluate(**{"s0-00-sample": "1.0", "s0-01-scr-wa": "1.0",
                          "s0-02-normal": "1.0"})
        self.assertEqual(self._released(),
                         {"s1-00-sample", "s1-01-normal", "s2-00-sample"})

    def test_failed_dependency_never_releases_the_dependent(self):
        self._evaluate(**{"s0-00-sample": "1.0", "s0-01-scr-wa": "0.0"})
        self.assertEqual(self._released(), {"s0-02-normal", "s2-00-sample"})

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_with_two_phase_the_released_subtask_screens_first(self):
        self.assertEqual(self._released(), {
            "s0-00-sample", "s0-01-scr-wa", "s2-00-sample"})
        self._evaluate(**{"s0-00-sample": "1.0", "s0-01-scr-wa": "1.0",
                          "s0-02-normal": "1.0"})
        self.assertEqual(self._released(), {"s1-00-sample", "s2-00-sample"})


class TestParityWithoutDependencies(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(
            task=self.task, autojudge=True, score_type="GroupMin",
            score_type_parameters=[[20, 3], [30, 2], [50, 1]])
        self.task.active_dataset = self.dataset
        for codename in CODENAMES:
            self.add_testcase(self.dataset, codename=codename)
        self.submission, results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.result = results[0]
        self.session.flush()

    def _released(self):
        return set(
            op.testcase_codename
            for op, _, _ in submission_get_operations(
                self.result, self.submission, self.dataset)
            if op.type_ == ESOperation.EVALUATION)

    def test_two_phase_off_releases_everything(self):
        self.assertEqual(self._released(), set(CODENAMES))

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_two_phase_on_releases_the_screening(self):
        self.assertEqual(self._released(), {
            "s0-00-sample", "s0-01-scr-wa", "s1-00-sample", "s2-00-sample"})


class TestSharedAndOrphanTestcases(DatabaseMixin, unittest.TestCase):

    def test_shared_and_orphan_testcases(self):
        contest = self.add_contest()
        participation = self.add_participation(contest=contest)
        task = self.add_task(contest=contest)
        dataset = self.add_dataset(
            task=task, autojudge=True, score_type="GroupMin",
            score_type_parameters=[
                {"max_score": 50, "testcases": "^(a|x)"},
                {"max_score": 50, "testcases": "^(b|x)", "depends_on": [0]}])
        task.active_dataset = dataset
        for codename in ["a", "b", "x", "z"]:
            self.add_testcase(dataset, codename=codename)
        submission, results = self.add_submission_with_results(
            task, participation, True)
        self.session.flush()
        released = set(
            op.testcase_codename
            for op, _, _ in submission_get_operations(
                results[0], submission, dataset)
            if op.type_ == ESOperation.EVALUATION)
        self.assertEqual(released, {"a", "x", "z"})


class TestAnyDatasetDeclaresDependencies(DatabaseMixin, unittest.TestCase):

    def test_detects_per_contest(self):
        with_deps = self.add_contest()
        without = self.add_contest()
        task = self.add_task(contest=with_deps)
        self.add_dataset(task=task, score_type="GroupMin",
                         score_type_parameters=DAG_PARAMETERS)
        other = self.add_task(contest=without)
        self.add_dataset(task=other, score_type="GroupMin",
                         score_type_parameters=[[100, 1]])
        self.session.flush()
        self.assertTrue(
            any_dataset_declares_dependencies(self.session, with_deps.id))
        self.assertFalse(
            any_dataset_declares_dependencies(self.session, without.id))
        self.assertTrue(any_dataset_declares_dependencies(self.session, None))


if __name__ == "__main__":
    unittest.main()
