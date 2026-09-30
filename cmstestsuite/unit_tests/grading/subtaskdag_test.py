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
from unittest.mock import Mock, PropertyMock

from cms.grading import subtaskdag
from cms.grading.scoretypes.GroupMin import GroupMin
from cms.grading.scoretypes.GroupThreshold import GroupThreshold


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


def _gate(parameters, codenames):
    public = {codename: True for codename in codenames}
    return subtaskdag.SubtaskGate(GroupMin(parameters, public, 2))


class TestSubtaskGate(unittest.TestCase):

    def setUp(self):
        # Count-based: subtask 0 = a0, a1; subtask 1 = b0 (depends on 0);
        # subtask 2 = c0.
        self.gate = _gate(
            [{"max_score": 20, "testcases": 2},
             {"max_score": 30, "testcases": 1, "depends_on": [0]},
             {"max_score": 50, "testcases": 1}],
            ["a0", "a1", "b0", "c0"])

    def test_nothing_evaluated(self):
        status, blocked_by = self.gate.statuses({})
        self.assertEqual(status, ["pending", "pending", "pending"])
        self.assertEqual(blocked_by, [None, None, None])
        self.assertTrue(self.gate.releasable("a0", status))
        self.assertFalse(self.gate.releasable("b0", status))
        self.assertTrue(self.gate.releasable("c0", status))

    def test_dependency_passed_releases_dependent(self):
        status, _ = self.gate.statuses({"a0": "1.0", "a1": "1.0"})
        self.assertEqual(status[0], "passed")
        self.assertTrue(self.gate.releasable("b0", status))

    def test_statuses_use_existing_outcomes(self):
        status, blocked_by = self.gate.statuses(
            {"a0": "0.0", "b0": "1.0", "c0": "1.0"})
        self.assertEqual(status, ["failed", "failed", "passed"])
        self.assertEqual(blocked_by, [None, 0, None])

    def test_failure_is_known_before_all_testcases_run(self):
        status, blocked_by = self.gate.statuses({"a0": "0.0"})
        self.assertEqual(status[:2], ["failed", "failed"])
        self.assertEqual(blocked_by[1], 0)
        self.assertEqual(
            self.gate.skippable(["a1", "b0", "c0"], status, blocked_by),
            {"b0": 0})

    def test_partial_score_passes(self):
        status, _ = self.gate.statuses({"a0": "0.5", "a1": "1.0"})
        self.assertEqual(status[0], "passed")

    def test_zero_point_subtask_passes(self):
        gate = _gate([{"max_score": 0, "testcases": 1},
                      {"max_score": 100, "testcases": 1, "depends_on": [0]}],
                     ["a0", "b0"])
        status, _ = gate.statuses({"a0": "1.0"})
        self.assertEqual(status[0], "passed")

    def test_transitive_skip(self):
        gate = _gate([{"max_score": 20, "testcases": 1},
                      {"max_score": 30, "testcases": 1, "depends_on": [0]},
                      {"max_score": 50, "testcases": 1, "depends_on": [1]}],
                     ["a0", "b0", "c0"])
        status, blocked_by = gate.statuses({"a0": "0.0"})
        self.assertEqual(blocked_by, [None, 0, 1])
        self.assertEqual(gate.skippable(["b0", "c0"], status, blocked_by),
                         {"b0": 0, "c0": 1})

    def test_shared_testcase_released_if_any_subtask_needs_it(self):
        # Regex params: "x" is in subtask 0 and in subtask 1.
        gate = _gate([{"max_score": 50, "testcases": "^(a|x)"},
                      {"max_score": 50, "testcases": "^(b|x)",
                       "depends_on": [0]}],
                     ["a", "b", "x"])
        status, blocked_by = gate.statuses({"a": "0.0"})
        self.assertTrue(gate.releasable("x", status))
        self.assertEqual(gate.skippable(["b", "x"], status, blocked_by),
                         {"b": 0})

    def test_testcase_in_no_subtask_is_never_held(self):
        gate = _gate([{"max_score": 50, "testcases": "^a"},
                      {"max_score": 50, "testcases": "^b",
                       "depends_on": [0]}],
                     ["a", "b", "z"])
        status, blocked_by = gate.statuses({"a": "0.0"})
        self.assertTrue(gate.releasable("z", status))
        self.assertNotIn("z", gate.skippable(["b", "z"], status, blocked_by))

    def test_passing_needs_all_dependencies_passed(self):
        # b0 is fully evaluated, but its dependency (subtask 0) is still
        # pending: subtask 1 must not pass, nor fail.
        self.assertEqual(
            self.gate.statuses({"b0": "1.0"}),
            (["pending", "pending", "pending"], [None, None, None]))

    def test_walk_follows_dependencies_not_numbers(self):
        # 0 depends on 1, and 1 depends on 2: the failure of subtask 2 has
        # to reach subtask 0 through subtask 1.
        gate = _gate([{"max_score": 30, "testcases": 1, "depends_on": [1]},
                      {"max_score": 30, "testcases": 1, "depends_on": [2]},
                      {"max_score": 40, "testcases": 1}],
                     ["a", "b", "c"])
        self.assertEqual(
            gate.statuses({"c": "0.0"}),
            (["failed", "failed", "failed"], [1, 2, None]))

    def test_lowest_failed_dependency_is_the_blocker(self):
        gate = _gate([{"max_score": 30, "testcases": 1},
                      {"max_score": 30, "testcases": 1},
                      {"max_score": 40, "testcases": 1,
                       "depends_on": [0, 1]}],
                     ["a", "b", "c"])
        status, blocked_by = gate.statuses({"a": "0.0", "b": "0.0"})
        self.assertEqual(status, ["failed", "failed", "failed"])
        self.assertEqual(blocked_by, [None, None, 0])

    def test_shared_testcase_names_the_blocker_of_lowest_subtask(self):
        # "x" is in subtasks 2 and 3, blocked by subtasks 1 and 0.
        gate = _gate([{"max_score": 25, "testcases": "^a"},
                      {"max_score": 25, "testcases": "^b"},
                      {"max_score": 25, "testcases": "^(c|x)",
                       "depends_on": [1]},
                      {"max_score": 25, "testcases": "^(d|x)",
                       "depends_on": [0]}],
                     ["a", "b", "c", "d", "x"])
        status, blocked_by = gate.statuses({"a": "0.0", "b": "0.0"})
        self.assertEqual(blocked_by, [None, None, 1, 0])
        self.assertEqual(
            gate.skippable(["c", "d", "x"], status, blocked_by),
            {"c": 1, "d": 0, "x": 1})

    def test_missing_or_bad_outcome_counts_as_zero(self):
        for outcome in ("oops", None):
            with self.subTest(outcome=outcome):
                status, _ = self.gate.statuses({"a0": outcome})
                self.assertEqual(status[0], "failed")

    def test_empty_subtask_passes_when_its_dependencies_pass(self):
        gate = subtaskdag.SubtaskGate(GroupThreshold(
            [{"max_score": 10, "testcases": 1, "threshold": 1.0},
             {"max_score": 0, "testcases": 0, "threshold": 1.0,
              "depends_on": [0]},
             {"max_score": 90, "testcases": 1, "threshold": 1.0,
              "depends_on": [1]}],
            {"a": True, "b": True}, 2))
        status, _ = gate.statuses({})
        self.assertEqual(status, ["pending", "pending", "pending"])
        self.assertFalse(gate.releasable("b", status))
        status, _ = gate.statuses({"a": "1.0"})
        self.assertEqual(status, ["passed", "passed", "pending"])
        self.assertTrue(gate.releasable("b", status))

    def test_empty_subtask_without_dependencies_passes_at_once(self):
        gate = subtaskdag.SubtaskGate(GroupThreshold(
            [{"max_score": 0, "testcases": 0, "threshold": 1.0},
             {"max_score": 100, "testcases": 1, "threshold": 1.0,
              "depends_on": [0]}],
            {"a": True}, 2))
        status, _ = gate.statuses({})
        self.assertEqual(status, ["passed", "pending"])
        self.assertTrue(gate.releasable("a", status))


class TestGateForDataset(unittest.TestCase):

    def test_no_dependencies_builds_nothing(self):
        dataset = Mock()
        dataset.score_type_parameters = [[20, 1], [80, 1]]
        score_type_object = PropertyMock()
        type(dataset).score_type_object = score_type_object
        with self.assertNoLogs("cms.grading.subtaskdag"):
            self.assertIsNone(subtaskdag.gate_for_dataset(dataset))
        score_type_object.assert_not_called()

    def test_dependencies_build_a_gate(self):
        dataset = Mock()
        dataset.score_type_parameters = [
            {"max_score": 20, "testcases": 1},
            {"max_score": 80, "testcases": 1, "depends_on": [0]}]
        dataset.score_type_object = GroupMin(
            dataset.score_type_parameters, {"a": True, "b": True}, 2)
        gate = subtaskdag.gate_for_dataset(dataset)
        self.assertIsInstance(gate, subtaskdag.SubtaskGate)

    def test_gate_for_invalid_dataset_is_none_and_warns_once(self):
        dataset = Mock()
        dataset.id = 424242
        dataset.score_type_parameters = [
            {"max_score": 50, "testcases": 1, "depends_on": [1]},
            {"max_score": 50, "testcases": 1, "depends_on": [0]}]
        type(dataset).score_type_object = PropertyMock(
            side_effect=ValueError("cycle"))
        self.addCleanup(subtaskdag._warned_datasets.discard, 424242)
        with self.assertLogs("cms.grading.subtaskdag", "WARNING") as logs:
            self.assertIsNone(subtaskdag.gate_for_dataset(dataset))
            self.assertIsNone(subtaskdag.gate_for_dataset(dataset))
        self.assertEqual(len(logs.records), 1)
        self.assertIn("424242", logs.records[0].getMessage())


if __name__ == "__main__":
    unittest.main()
