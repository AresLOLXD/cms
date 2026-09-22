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

"""Tests for the two-phase (fail-fast screening) grading gate."""

import unittest
from unittest.mock import patch

from cms import config
from cms.grading import twophase


class FakeDataset:
    """Minimal stand-in for cms.db.Dataset: only needs .testcases."""

    def __init__(self, codenames):
        self.testcases = {codename: object() for codename in codenames}


class TestEnabled(unittest.TestCase):

    def test_default_is_false(self):
        self.assertFalse(twophase.enabled())

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_true_when_configured(self):
        self.assertTrue(twophase.enabled())


class TestGroupOf(unittest.TestCase):

    def test_group_before_first_dash(self):
        self.assertEqual(twophase.group_of("s1-00-sample"), "s1")
        self.assertEqual(twophase.group_of("s1-01-scr-wa"), "s1")

    def test_no_dash_falls_in_global_group(self):
        self.assertEqual(twophase.group_of("000"), "")


class TestIsScreening(unittest.TestCase):

    def test_sample_is_screening(self):
        self.assertTrue(twophase.is_screening("s1-00-sample"))

    def test_scr_tag_is_screening(self):
        self.assertTrue(twophase.is_screening("s1-01-scr-wa"))
        self.assertTrue(twophase.is_screening("s1-02-scr-tle"))

    def test_normal_testcase_is_not_screening(self):
        self.assertFalse(twophase.is_screening("s1-03-normal"))
        self.assertFalse(twophase.is_screening("000"))

    def test_group_name_containing_scr_is_not_screening(self):
        # The group name itself contains "scr", but the tag ("normal")
        # does not: this must NOT be classified as screening.
        self.assertFalse(twophase.is_screening("scr1-00-normal"))

    def test_group_name_containing_sample_is_not_screening(self):
        # Same as above, but with "sample" in the group name instead of
        # the tag.
        self.assertFalse(twophase.is_screening("sample-00-normal"))

    def test_tag_containing_scr_is_screening_even_with_unrelated_group(self):
        # The tag contains "scr" while the group name does not: this
        # must be classified as screening.
        self.assertTrue(twophase.is_screening("s1-04-scr-extra"))


class TestGroupScreeningStatus(unittest.TestCase):

    def setUp(self):
        self.dataset = FakeDataset([
            "s1-00-sample", "s1-01-scr-wa", "s1-02-normal",
            "s2-00-sample", "s2-01-normal",
        ])

    def test_group_with_no_evaluations_yet_is_pending(self):
        status = twophase.group_screening_status(self.dataset, {})
        self.assertEqual(status, {"s1": "pending", "s2": "pending"})

    def test_group_pending_until_all_screening_done(self):
        status = twophase.group_screening_status(
            self.dataset, {"s1-00-sample": "1.0"})
        self.assertEqual(status["s1"], "pending")

    def test_group_passed_when_all_screening_positive(self):
        status = twophase.group_screening_status(
            self.dataset,
            {"s1-00-sample": "1.0", "s1-01-scr-wa": "1.0",
             "s2-00-sample": "1.0"})
        self.assertEqual(status, {"s1": "passed", "s2": "passed"})

    def test_group_failed_when_any_screening_non_positive(self):
        status = twophase.group_screening_status(
            self.dataset,
            {"s1-00-sample": "1.0", "s1-01-scr-wa": "0.0",
             "s2-00-sample": "1.0"})
        self.assertEqual(status, {"s1": "failed", "s2": "passed"})

    def test_group_without_screening_testcases_is_absent(self):
        dataset = FakeDataset(["000", "001"])
        status = twophase.group_screening_status(dataset, {})
        self.assertEqual(status, {})

    def test_non_numeric_outcome_counts_as_not_passed(self):
        status = twophase.group_screening_status(
            self.dataset,
            {"s1-00-sample": None, "s1-01-scr-wa": "1.0",
             "s2-00-sample": "1.0"})
        self.assertEqual(status["s1"], "failed")


if __name__ == "__main__":
    unittest.main()
