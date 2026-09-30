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

"""Tests for the public score details of a partially private subtask.

A subtask that contains at least one non-public testcase is stored in the
public details without any score (its result must not be revealed); the
filtering and the rendering of such details must cope with that.

"""

import unittest

from cms import (
    FEEDBACK_LEVEL_FULL,
    FEEDBACK_LEVEL_OI_RESTRICTED,
    FEEDBACK_LEVEL_RESTRICTED,
)
from cms.grading.scoretypes.GroupMin import GroupMin
from cms.grading.scoretypes.GroupMul import GroupMul
from cms.grading.scoretypes.GroupThreshold import GroupThreshold
from cmstestsuite.unit_tests.grading.scoretypes.scoretypetestutils import (
    ScoreTypeTestMixin,
)


UNAVAILABLE = "Score details temporarily unavailable."

FEEDBACK_LEVELS = [
    FEEDBACK_LEVEL_FULL,
    FEEDBACK_LEVEL_RESTRICTED,
    FEEDBACK_LEVEL_OI_RESTRICTED,
]


class PublicDetailsTests:
    """Tests shared by the group score types, run once per score type.

    Not collected by itself: the concrete test cases below combine it with
    unittest.TestCase.

    """

    score_type_class = None
    # Values appended to the parameters of every subtask (e.g. a threshold).
    extra_parameters = ()

    def setUp(self):
        super().setUp()
        # Subtask 0 is fully public; subtask 1 mixes public and non-public
        # testcases (the non-public one sits between the two public ones).
        self.public_testcases = {
            "0_0": True,
            "0_1": True,
            "1_0": True,
            "1_1": False,
            "1_2": True,
        }
        self.score_type = self.score_type_class(
            [
                [10.0, "0_*", *self.extra_parameters],
                [20.0, "1_*", *self.extra_parameters],
            ],
            self.public_testcases,
            2,
        )

    @staticmethod
    def get_submission_result(testcases):
        # The template renders each evaluation's text as a list of messages.
        sr = ScoreTypeTestMixin.get_submission_result(testcases)
        for evaluation in sr.evaluations:
            evaluation.text = ["Output is correct"]
        return sr

    def get_public_details(self, failing=()):
        """Return the public details of a submission that fails the given
        testcases and is correct everywhere else.

        """
        sr = self.get_submission_result(self.public_testcases)
        for codename in failing:
            self.set_outcome(sr, codename, 0.0)
        return self.score_type.compute_score(sr)[3]

    def assert_fully_public_subtask(self, subtask):
        """The fully public subtask keeps its score, as it always did."""
        self.assertEqual(
            {key: value for key, value in subtask.items() if key != "testcases"},
            {"idx": 0, "score_fraction": 1.0, "score": 10.0, "max_score": 10.0},
        )
        self.assertEqual(
            [(tc["idx"], tc.get("outcome")) for tc in subtask["testcases"]],
            [("0_0", "Correct"), ("0_1", "Correct")],
        )

    def test_json_details_when_the_public_testcase_fails(self):
        # Restricted feedback shows up to the first lowest public testcase
        # (1_2), OI restricted feedback only that one.
        details = self.get_public_details(failing=["1_2"])
        expected = {
            FEEDBACK_LEVEL_FULL: [
                ("1_0", "Correct"),
                ("1_1", None),
                ("1_2", "Not correct"),
            ],
            FEEDBACK_LEVEL_RESTRICTED: [
                ("1_0", "Correct"),
                ("1_1", None),
                ("1_2", "Not correct"),
            ],
            FEEDBACK_LEVEL_OI_RESTRICTED: [("1_2", "Not correct")],
        }
        for level in FEEDBACK_LEVELS:
            with self.subTest(level=level):
                filtered = self.score_type.get_json_details(details, level)
                self.assertEqual(len(filtered), 2)
                self.assert_fully_public_subtask(filtered[0])
                self.assertEqual(set(filtered[1]), {"idx", "testcases"})
                self.assertEqual(filtered[1]["idx"], 1)
                self.assertEqual(
                    [(tc["idx"], tc.get("outcome")) for tc in filtered[1]["testcases"]],
                    expected[level],
                )

    def test_json_details_when_everything_is_correct(self):
        details = self.get_public_details()
        expected = {
            FEEDBACK_LEVEL_FULL: [
                ("1_0", "Correct"),
                ("1_1", None),
                ("1_2", "Correct"),
            ],
            FEEDBACK_LEVEL_RESTRICTED: [
                ("1_0", "Correct"),
                ("1_1", None),
                ("1_2", "Correct"),
            ],
            FEEDBACK_LEVEL_OI_RESTRICTED: [("1_0", "Correct"), ("1_2", "Correct")],
        }
        for level in FEEDBACK_LEVELS:
            with self.subTest(level=level):
                filtered = self.score_type.get_json_details(details, level)
                self.assertEqual(len(filtered), 2)
                self.assert_fully_public_subtask(filtered[0])
                self.assertEqual(set(filtered[1]), {"idx", "testcases"})
                self.assertEqual(
                    [(tc["idx"], tc.get("outcome")) for tc in filtered[1]["testcases"]],
                    expected[level],
                )

    def test_json_details_public_testcases_follow_the_feedback_level(self):
        details = self.get_public_details(failing=["1_2"])
        full = self.score_type.get_json_details(details, FEEDBACK_LEVEL_FULL)
        restricted = self.score_type.get_json_details(
            details, FEEDBACK_LEVEL_RESTRICTED
        )
        # Times and memory only at the full level, never for a placeholder.
        self.assertIn("time", full[1]["testcases"][0])
        self.assertIn("memory", full[1]["testcases"][0])
        self.assertEqual(full[1]["testcases"][1], {"idx": "1_1"})
        self.assertNotIn("time", restricted[1]["testcases"][0])
        self.assertNotIn("memory", restricted[1]["testcases"][0])
        self.assertEqual(restricted[1]["testcases"][1], {"idx": "1_1"})

    def test_html_details_render_the_partially_private_subtask(self):
        for failing in [[], ["1_2"]]:
            details = self.get_public_details(failing=failing)
            for level in FEEDBACK_LEVELS:
                with self.subTest(failing=failing, level=level):
                    html = self.score_type.get_html_details(details, level)
                    self.assertNotIn(UNAVAILABLE, html)
                    self.assertIn("Subtask 0", html)
                    self.assertIn("Subtask 1", html)
                    # Subtask 0 is scored, subtask 1 is rendered without
                    # any score.
                    self.assertEqual(html.count('class="subtask correct"'), 1)
                    self.assertEqual(html.count('class="subtask undefined"'), 1)


class TestGroupMinPublicDetails(
    PublicDetailsTests, ScoreTypeTestMixin, unittest.TestCase
):
    score_type_class = GroupMin


class TestGroupMulPublicDetails(
    PublicDetailsTests, ScoreTypeTestMixin, unittest.TestCase
):
    score_type_class = GroupMul


class TestGroupThresholdPublicDetails(
    PublicDetailsTests, ScoreTypeTestMixin, unittest.TestCase
):
    score_type_class = GroupThreshold
    extra_parameters = (1.0,)


if __name__ == "__main__":
    unittest.main()
