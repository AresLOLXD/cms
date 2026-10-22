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

"""Tests for subtask dependencies in the group score types."""

import unittest

from cms.grading.scoretypes.GroupMin import GroupMin
from cms.grading.scoretypes.GroupMul import GroupMul
from cms.grading.scoretypes.GroupThreshold import GroupThreshold
from cms.grading.steps.evaluation import EVALUATION_MESSAGES
from cmstestsuite.unit_tests.grading.scoretypes.scoretypetestutils import \
    ScoreTypeTestMixin


PUBLIC = {"0": True, "1": True, "2": True, "3": True}


def params(*subtasks):
    """Build dict-form parameters, one testcase per subtask by default."""
    return [dict(max_score=s[0], testcases=s[1], **s[2]) for s in subtasks]


class TestGroupDependencies(ScoreTypeTestMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        # Subtask 0 = "0", subtask 1 = "1" (depends on 0), subtask 2 = "2".
        self.parameters = params(
            (20, 1, {}), (30, 1, {"depends_on": [0]}), (50, 1, {}))
        self.public = {"0": True, "1": True, "2": True}

    @staticmethod
    def get_submission_result(testcases):
        # The template renders each evaluation's text as a list of messages.
        sr = ScoreTypeTestMixin.get_submission_result(testcases)
        for evaluation in sr.evaluations:
            evaluation.text = ["Output is correct"]
        return sr

    def test_validation_errors_raise(self):
        for bad in [[1], [5], ["0"], [0, 0]]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    GroupMin(params((20, 1, {}), (80, 1, {"depends_on": bad})),
                             {"0": True, "1": True}, 2)

    def test_dependencies_attribute(self):
        st = GroupMin(self.parameters, self.public, 2)
        self.assertEqual(st.dependencies, [[], [0], []])
        self.assertEqual(GroupMin([[20, 1], [80, 2]], PUBLIC, 2).dependencies,
                         [[], []])

    def test_hide_dependency_attribute(self):
        self.parameters[1]["hide_dependency"] = True
        st = GroupMin(self.parameters, self.public, 2)
        self.assertEqual(st.hide_dependency, [False, True, False])

    def test_non_boolean_hide_dependency_raises_like_depends_on(self):
        self.parameters[1]["hide_dependency"] = "yes"
        with self.assertRaises(ValueError):
            GroupMin(self.parameters, self.public, 2)

    def test_hide_dependency_without_depends_on_is_accepted(self):
        self.parameters[2]["hide_dependency"] = True
        st = GroupMin(self.parameters, self.public, 2)
        self.assertEqual(st.hide_dependency, [False, False, True])

    def test_failed_dependency_zeroes_the_dependent(self):
        st = GroupMin(self.parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        score, details, public_score, _, ranking = st.compute_score(sr)
        self.assertAlmostEqual(score, 50.0)
        self.assertAlmostEqual(public_score, 50.0)
        self.assertEqual(ranking, ["0", "0", "50"])
        self.assertNotIn("zeroed_by_dependency", details[0])
        self.assertEqual(details[1]["zeroed_by_dependency"], 0)
        self.assertEqual(details[1]["score"], 0.0)
        self.assertEqual(details[1]["score_fraction"], 0.0)
        self.assertNotIn("zeroed_by_dependency", details[2])

    def test_passed_dependency_keeps_scores_and_details(self):
        st = GroupMin(self.parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        score, details, _, _, ranking = st.compute_score(sr)
        self.assertAlmostEqual(score, 100.0)
        self.assertEqual(ranking, ["20", "30", "50"])
        for subtask in details:
            self.assertNotIn("zeroed_by_dependency", subtask)

    def test_rule_applies_to_evaluated_subtasks(self):
        # Subtask 1's own testcase is correct, but its dependency failed.
        st = GroupMin(self.parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        _, details, _, _, _ = st.compute_score(sr)
        self.assertEqual(details[1]["testcases"][0]["outcome"], "Correct")
        self.assertEqual(details[1]["score"], 0.0)

    def test_transitive(self):
        parameters = params((20, 1, {}), (30, 1, {"depends_on": [0]}),
                            (50, 1, {"depends_on": [1]}))
        st = GroupMin(parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        score, details, _, _, _ = st.compute_score(sr)
        self.assertAlmostEqual(score, 0.0)
        self.assertEqual(details[2]["zeroed_by_dependency"], 1)

    def test_partial_dependency_passes(self):
        parameters = params((20, 2, {}), (80, 1, {"depends_on": [0]}))
        public = {"0": True, "1": True, "2": True}
        st = GroupMin(parameters, public, 2)
        sr = self.get_submission_result(public)
        self.set_outcome(sr, "0", 0.5)
        score, _, _, _, ranking = st.compute_score(sr)
        self.assertAlmostEqual(score, 90.0)
        self.assertEqual(ranking, ["10", "80"])

    def test_zero_point_dependency_passes_on_fraction(self):
        parameters = params((0, 1, {}), (100, 1, {"depends_on": [0]}))
        public = {"0": True, "1": True}
        st = GroupMin(parameters, public, 2)
        sr = self.get_submission_result(public)
        self.assertAlmostEqual(st.compute_score(sr)[0], 100.0)
        self.set_outcome(sr, "0", 0.0)
        self.assertAlmostEqual(st.compute_score(sr)[0], 0.0)

    def test_group_mul_and_threshold(self):
        mul = GroupMul(self.parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        self.assertAlmostEqual(mul.compute_score(sr)[0], 50.0)

        # GroupThreshold: an outcome passes when 0 < outcome <= threshold.
        thr = GroupThreshold(
            params((20, 1, {"threshold": 1.0}),
                   (30, 1, {"threshold": 1.0, "depends_on": [0]}),
                   (50, 1, {"threshold": 1.0})),
            self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 2.0)
        self.assertAlmostEqual(thr.compute_score(sr)[0], 50.0)

    def test_parity_without_dependencies(self):
        list_form = GroupMin([[20, 1], [30, 1], [50, 1]], self.public, 2)
        dict_form = GroupMin(params((20, 1, {}), (30, 1, {}), (50, 1, {})),
                             self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        self.assertEqual(list_form.compute_score(sr),
                         dict_form.compute_score(sr))

    def test_json_details_and_html_note(self):
        st = GroupMin(self.parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        _, details, _, _, _ = st.compute_score(sr)
        json_details = st.get_json_details(details)
        self.assertEqual(json_details[1]["zeroed_by_dependency"], 0)
        self.assertNotIn("zeroed_by_dependency", json_details[0])
        html = st.get_html_details(details)
        self.assertIn("Worth 0 because it depends on subtask 0, which scored "
                      "no points.", html)
        self.assertEqual(html.count("Worth 0 because"), 1)

    def _partially_private_dataset(self):
        """Return a score type whose subtask 1 (depends on 0) mixes a public
        and a non-public testcase, and its public testcases.

        """
        public = {"0": True, "1": True, "2": False, "3": True}
        st = GroupMin(
            params((20, 1, {}), (30, 2, {"depends_on": [0]}), (50, 1, {})),
            public, 2)
        return st, public

    def test_partially_private_zeroed_subtask_in_public_details(self):
        st, public = self._partially_private_dataset()
        sr = self.get_submission_result(public)
        self.set_outcome(sr, "0", 0.0)
        _, _, _, public_details, _ = st.compute_score(sr)

        # No score is revealed, but the (already visible) dependency skip is.
        self.assertEqual(public_details[1]["zeroed_by_dependency"], 0)
        for key in ("score_fraction", "score", "max_score"):
            self.assertNotIn(key, public_details[1])
        json_details = st.get_json_details(public_details)
        self.assertEqual(json_details[1]["zeroed_by_dependency"], 0)
        self.assertEqual(set(json_details[1]),
                         {"idx", "zeroed_by_dependency", "testcases"})
        # The fully public subtasks are untouched.
        self.assertNotIn("zeroed_by_dependency", json_details[0])
        self.assertEqual(json_details[0]["score"], 0.0)
        self.assertEqual(json_details[2]["score"], 50.0)

        html = st.get_html_details(public_details)
        self.assertNotIn("temporarily unavailable", html)
        self.assertIn("Worth 0 because it depends on subtask 0, which scored "
                      "no points.", html)
        self.assertEqual(html.count("Worth 0 because"), 1)

    def test_partially_private_subtask_not_zeroed_has_no_note(self):
        st, public = self._partially_private_dataset()
        sr = self.get_submission_result(public)
        _, _, _, public_details, _ = st.compute_score(sr)

        self.assertNotIn("zeroed_by_dependency", public_details[1])
        for key in ("score_fraction", "score", "max_score"):
            self.assertNotIn(key, public_details[1])
        json_details = st.get_json_details(public_details)
        self.assertEqual(set(json_details[1]), {"idx", "testcases"})
        html = st.get_html_details(public_details)
        self.assertNotIn("temporarily unavailable", html)
        self.assertNotIn("Worth 0 because", html)

    def test_fully_private_zeroed_subtask_reveals_nothing(self):
        # With no public testcase there is no "Not tested" text to show, so
        # the note would reveal that a hidden subtask scored no points.
        cases = {
            # Hidden subtask 2 depends on hidden subtask 1, which failed.
            "hidden dependency": (
                params((20, 1, {}), (30, 1, {}),
                       (50, 1, {"depends_on": [1]})),
                {"0": True, "1": False, "2": False}, "1"),
            # Hidden subtask 1 depends on public subtask 0, which failed.
            "public dependency": (
                params((20, 1, {}), (80, 1, {"depends_on": [0]})),
                {"0": True, "1": False}, "0"),
        }
        for name, (parameters, public, failing) in cases.items():
            with self.subTest(name):
                st = GroupMin(parameters, public, 2)
                sr = self.get_submission_result(public)
                self.set_outcome(sr, failing, 0.0)
                _, private_details, _, public_details, _ = st.compute_score(sr)
                # The private details still record the dependency.
                self.assertEqual(private_details[-1]["zeroed_by_dependency"],
                                 int(failing))

                hidden = public_details[-1]
                self.assertEqual(set(hidden), {"idx", "testcases"})
                self.assertEqual(
                    set(st.get_json_details(public_details)[-1]),
                    {"idx", "testcases"})
                html = st.get_html_details(public_details)
                self.assertNotIn("temporarily unavailable", html)
                self.assertNotIn("Worth 0 because it depends on subtask", html)

    @staticmethod
    def skip(sr, *codenames):
        """Make the evaluations look skipped by the failed dependency 0."""
        for evaluation in sr.evaluations:
            if evaluation.codename in codenames:
                evaluation.text = [
                    EVALUATION_MESSAGES.get("skipped_dependency").message,
                    "0"]

    def test_skipped_testcase_is_marked(self):
        st = GroupMin(self.parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        self.skip(sr, "1")
        _, details, _, public_details, _ = st.compute_score(sr)
        for view in (details, public_details):
            self.assertIs(view[1]["testcases"][0]["skipped"], True)
            self.assertNotIn("skipped", view[0]["testcases"][0])
            self.assertNotIn("skipped", view[2]["testcases"][0])

    def _hidden_dataset(self, all_public):
        """Return a score type whose subtask 1 (depends on 0, hidden) has
        the testcases "1", "2" and "3", and its public testcases.

        """
        public = {"0": True, "1": True, "2": all_public, "3": True}
        self.parameters = params(
            (20, 1, {}),
            (30, 3, {"depends_on": [0], "hide_dependency": True}))
        return GroupMin(self.parameters, public, 2), public

    def test_hidden_subtask_public_details_have_no_note_and_na_rows(self):
        st, public = self._hidden_dataset(all_public=False)
        sr = self.get_submission_result(public)
        self.set_outcome(sr, "0", 0.0)
        self.skip(sr, "1", "2", "3")
        _, _, _, public_details, _ = st.compute_score(sr)
        self.assertNotIn("zeroed_by_dependency", public_details[1])
        self.assertEqual(public_details[1]["testcases"],
                         [{"idx": "1"}, {"idx": "2"}, {"idx": "3"}])
        html = st.get_html_details(public_details)
        self.assertNotIn("Worth 0 because", html)

    def test_hiding_does_not_touch_the_full_details(self):
        message = EVALUATION_MESSAGES.get("skipped_dependency").message
        for all_public in (False, True):
            with self.subTest(all_public=all_public):
                st, public = self._hidden_dataset(all_public)
                sr = self.get_submission_result(public)
                self.set_outcome(sr, "0", 0.0)
                self.skip(sr, "1", "2", "3")
                _, details, _, public_details, _ = st.compute_score(sr)
                self.assertEqual(details[1]["zeroed_by_dependency"], 0)
                for tc in details[1]["testcases"]:
                    self.assertEqual(tc["text"], [message, "0"])
                    self.assertIs(tc["skipped"], True)
                self.assertIsNot(public_details[1], details[1])
                self.assertNotIn("zeroed_by_dependency", public_details[1])
                self.assertEqual(
                    [tc["idx"] for tc in public_details[1]["testcases"]],
                    ["1", "2", "3"])
                for tc in public_details[1]["testcases"]:
                    self.assertEqual(set(tc), {"idx"})

    def test_hidden_subtask_that_graded_its_testcases_keeps_the_rows(self):
        for all_public in (False, True):
            with self.subTest(all_public=all_public):
                st, public = self._hidden_dataset(all_public)
                sr = self.get_submission_result(public)
                self.set_outcome(sr, "0", 0.0)
                _, details, _, public_details, _ = st.compute_score(sr)
                self.assertEqual(details[1]["zeroed_by_dependency"], 0)
                self.assertNotIn("zeroed_by_dependency", public_details[1])
                rows = public_details[1]["testcases"]
                self.assertEqual([tc["idx"] for tc in rows],
                                 ["1", "2", "3"])
                self.assertEqual(rows[0]["outcome"], "Correct")
                self.assertEqual("outcome" in rows[1], all_public)

    def test_not_hidden_public_details_are_unchanged(self):
        message = EVALUATION_MESSAGES.get("skipped_dependency").message
        st, public = self._partially_private_dataset()
        sr = self.get_submission_result(public)
        self.set_outcome(sr, "0", 0.0)
        self.skip(sr, "1", "2")
        _, details, _, public_details, _ = st.compute_score(sr)
        self.assertEqual(public_details[1]["zeroed_by_dependency"], 0)
        self.assertEqual(public_details[1]["testcases"][0]["text"],
                         [message, "0"])
        self.assertIs(public_details[1]["testcases"][0]["skipped"], True)
        self.assertEqual(public_details[1]["testcases"][1], {"idx": "2"})
        # A fully public subtask is still the same object as the full one.
        st = GroupMin(self.parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        self.skip(sr, "1")
        _, details, _, public_details, _ = st.compute_score(sr)
        self.assertEqual(public_details[1], details[1])
        self.assertEqual(public_details[1]["zeroed_by_dependency"], 0)


if __name__ == "__main__":
    unittest.main()
