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

"""Tests for Dataset.validate_score_type."""

import unittest
from unittest.mock import PropertyMock, patch

from cms.db import Dataset, Task, Testcase


def make_dataset(score_type_parameters, codenames=("a", "b")):
    """Return an in-memory GroupMin dataset with the given testcases."""
    task = Task(name="task", title="Task")
    dataset = Dataset(
        task=task, description="dataset", task_type="Batch",
        task_type_parameters=[], score_type="GroupMin",
        score_type_parameters=score_type_parameters)
    for codename in codenames:
        Testcase(dataset=dataset, codename=codename, public=True,
                 input="input", output="output")
    return dataset


class TestValidateScoreType(unittest.TestCase):

    def test_a_valid_dag_passes(self):
        dataset = make_dataset([
            {"max_score": 20, "testcases": 1},
            {"max_score": 80, "testcases": 1, "depends_on": [0]}])
        self.assertIsNone(dataset.validate_score_type())

    def test_a_cycle_is_refused(self):
        dataset = make_dataset([
            {"max_score": 50, "testcases": 1, "depends_on": [1]},
            {"max_score": 50, "testcases": 1, "depends_on": [0]}])
        with self.assertRaisesRegex(ValueError, "form a cycle"):
            dataset.validate_score_type()

    def test_a_non_boolean_hide_dependency_is_refused(self):
        dataset = make_dataset([
            {"max_score": 100, "testcases": 1, "hide_dependency": "yes"}])
        with self.assertRaisesRegex(
                ValueError, "hide_dependency must be true or false"):
            dataset.validate_score_type()

    def test_regexes_that_match_testcases_pass(self):
        dataset = make_dataset(
            [{"max_score": 30, "testcases": "^s1-"},
             {"max_score": 70, "testcases": "^s2-", "depends_on": [0]}],
            codenames=("s1-00", "s2-00", "s2-01"))
        self.assertIsNone(dataset.validate_score_type())

    def test_a_regex_without_testcases_is_refused(self):
        dataset = make_dataset([{"max_score": 100, "testcases": "^z"}])
        with self.assertRaisesRegex(ValueError, "No testcase matches"):
            dataset.validate_score_type()

    def test_an_unknown_score_type_is_refused(self):
        dataset = make_dataset([{"max_score": 100, "testcases": 2}])
        dataset.score_type = "NoSuchType"
        with self.assertRaisesRegex(
                ValueError, r'^Unknown score type "NoSuchType"\.$'):
            dataset.validate_score_type()

    def test_assertion_errors_become_value_errors(self):
        dataset = make_dataset([{"max_score": 100, "testcases": 2}])
        with patch.object(
                Dataset, "score_type_object", new_callable=PropertyMock,
                side_effect=AssertionError("too precise")):
            with self.assertRaisesRegex(ValueError, "^too precise$"):
                dataset.validate_score_type()


if __name__ == "__main__":
    unittest.main()
