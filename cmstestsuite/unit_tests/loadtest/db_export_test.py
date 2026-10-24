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

"""Tests for the database export of the load-test harness."""

import contextlib
import io
import json
import os
import sys
import unittest

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import db_export  # noqa: E402


class EvaluationExportTest(DatabaseMixin, unittest.TestCase):

    def test_evaluations_come_from_the_active_dataset_only(self):
        task = self.add_task(contest=self.add_contest())
        active = self.add_dataset(task=task)
        other = self.add_dataset(task=task)
        task.active_dataset = active
        submission = self.add_submission(task=task)
        for dataset, cpu in ((active, 1.5), (other, 9.0)):
            testcase = self.add_testcase(dataset=dataset, codename="s1-03-t")
            self.add_evaluation(
                self.add_submission_result(submission, dataset), testcase,
                outcome="1.0", text=["Output is correct"],
                execution_time=cpu, execution_wall_clock_time=cpu * 2)
        self.session.commit()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            db_export.main()
        self.assertEqual(json.loads(out.getvalue())["evaluations"], [
            {"submission_id": submission.id, "testcase": "s1-03-t",
             "outcome": "1.0", "text": ["Output is correct"],
             "execution_time": 1.5, "execution_wall_clock_time": 3.0}])


if __name__ == "__main__":
    unittest.main()
