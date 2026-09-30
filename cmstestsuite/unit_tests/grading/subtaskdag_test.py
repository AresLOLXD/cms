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

"""Tests for cms.grading.subtaskdag."""

import unittest

from cms.grading import subtaskdag


class TestDeclaresDependencies(unittest.TestCase):

    def test_list_form_and_plain_dicts_declare_none(self):
        self.assertFalse(subtaskdag.declares_dependencies([[20, 3]]))
        self.assertFalse(subtaskdag.declares_dependencies(
            [{"max_score": 20, "testcases": 3}]))
        self.assertFalse(subtaskdag.declares_dependencies(
            [{"max_score": 20, "testcases": 3, "depends_on": []}]))
        self.assertFalse(subtaskdag.declares_dependencies(100))
        self.assertFalse(subtaskdag.declares_dependencies({}))

    def test_non_empty_depends_on_declares(self):
        self.assertTrue(subtaskdag.declares_dependencies([
            {"max_score": 20, "testcases": 3},
            {"max_score": 80, "testcases": 3, "depends_on": [0]}]))

    def test_null_depends_on_declares_none(self):
        self.assertFalse(subtaskdag.declares_dependencies([
            {"max_score": 20, "testcases": 3, "depends_on": None}]))


class TestParseDependencies(unittest.TestCase):

    def test_valid(self):
        self.assertEqual(
            subtaskdag.parse_dependencies([
                [20, 3],
                {"max_score": 30, "testcases": 3},
                {"max_score": 50, "testcases": 3, "depends_on": [1, 0]}]),
            [[], [], [0, 1]])

    def test_not_a_list_of_integers(self):
        for bad in ["0", [0.0], [False], [None], {"0": 1}]:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "must be a list"):
                    subtaskdag.parse_dependencies([
                        [20, 3],
                        {"max_score": 80, "testcases": 3,
                         "depends_on": bad}])

    def test_out_of_range(self):
        for bad in [-1, 2]:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "does not exist"):
                    subtaskdag.parse_dependencies([
                        [20, 3],
                        {"max_score": 80, "testcases": 3,
                         "depends_on": [bad]}])

    def test_self_dependency(self):
        with self.assertRaisesRegex(ValueError, "itself"):
            subtaskdag.parse_dependencies([
                {"max_score": 100, "testcases": 3, "depends_on": [0]}])

    def test_repeated(self):
        with self.assertRaisesRegex(ValueError, "repeats"):
            subtaskdag.parse_dependencies([
                [20, 3],
                {"max_score": 80, "testcases": 3, "depends_on": [0, 0]}])

    def test_cycle(self):
        with self.assertRaisesRegex(ValueError, "cycle.*0, 1"):
            subtaskdag.parse_dependencies([
                {"max_score": 50, "testcases": 3, "depends_on": [1]},
                {"max_score": 50, "testcases": 3, "depends_on": [0]}])

    def test_null_depends_on_raises(self):
        with self.assertRaisesRegex(ValueError, "must be a list"):
            subtaskdag.parse_dependencies([
                {"max_score": 100, "testcases": 3,
                 "depends_on": None}])

    def test_cycle_with_downstream(self):
        with self.assertRaisesRegex(ValueError, r": 2, 3, 4\."):
            subtaskdag.parse_dependencies([
                {},
                {"max_score": 10},
                {"depends_on": [3]},
                {"depends_on": [2]},
                {"depends_on": [3]}
            ])


class TestTopologicalOrder(unittest.TestCase):

    def test_dependencies_come_first_lowest_number_first(self):
        self.assertEqual(
            subtaskdag.topological_order([[2], [], [], [0, 1]]),
            [1, 2, 0, 3])

    def test_no_dependencies_keeps_order(self):
        self.assertEqual(subtaskdag.topological_order([[], [], []]),
                         [0, 1, 2])

    def test_fifo_with_sort(self):
        self.assertEqual(
            subtaskdag.topological_order([[1], [], []]),
            [1, 0, 2])

    def test_diamond(self):
        self.assertEqual(
            subtaskdag.topological_order([[], [0], [0], [1, 2]]),
            [0, 1, 2, 3])


class TestZeroedBy(unittest.TestCase):

    def test_independent_subtasks_keep_their_score(self):
        self.assertEqual(
            subtaskdag.zeroed_by([0.0, 1.0, 1.0], [[], [], []]),
            [None, None, None])

    def test_failed_dependency_zeroes_the_dependent(self):
        self.assertEqual(
            subtaskdag.zeroed_by([0.0, 1.0, 1.0], [[], [0], []]),
            [None, 0, None])

    def test_transitive(self):
        self.assertEqual(
            subtaskdag.zeroed_by([0.0, 1.0, 1.0], [[], [0], [1]]),
            [None, 0, 1])

    def test_partial_score_passes(self):
        self.assertEqual(
            subtaskdag.zeroed_by([0.5, 1.0], [[], [0]]),
            [None, None])

    def test_lowest_numbered_failed_dependency_is_named(self):
        self.assertEqual(
            subtaskdag.zeroed_by([1.0, 0.0, 0.0, 1.0],
                                 [[], [], [], [2, 1, 0]]),
            [None, None, None, 1])

    def test_uses_topological_order(self):
        # A chain numbered backwards: 0 depends on 1, which depends on 2.
        # Visiting subtasks by index would zero 1 but miss 0.
        self.assertEqual(
            subtaskdag.zeroed_by([1.0, 1.0, 0.0], [[1], [2], []]),
            [1, 2, None])


if __name__ == "__main__":
    unittest.main()
