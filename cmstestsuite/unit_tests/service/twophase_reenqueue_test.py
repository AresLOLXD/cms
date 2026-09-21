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

"""Tests that a group's phase-2 operations are released once its screening
passes, via EvaluationService.submission_enqueue_operations (the same call
write_results() makes for two-phase submissions).

"""

import gevent.monkey

gevent.monkey.patch_all()  # noqa

import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService


class TestTwoPhaseReEnqueue(DatabaseMixin, unittest.TestCase):

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
        self.session.flush()

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_phase_two_operation_released_after_screening_passes(self):
        self.add_evaluation(
            self.result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            self.result, self.testcases["s1-01-scr-wa"], outcome="1.0")
        self.session.flush()

        service = EvaluationService(0)
        service.submission_enqueue_operations(self.submission)

        expected = ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            "s1-02-normal")
        self.assertIn(expected, service.get_executor())


if __name__ == "__main__":
    unittest.main()
