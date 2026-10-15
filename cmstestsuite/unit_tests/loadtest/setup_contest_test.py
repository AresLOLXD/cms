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

"""Tests for the pure helpers of the load-test contest setup."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import scenario  # noqa: E402
import setup_contest  # noqa: E402


class ScoreParametersTest(unittest.TestCase):

    def test_full_keeps_dependencies(self):
        params = setup_contest.score_parameters(
            scenario.task_spec("cadena", "full"))
        self.assertEqual(params[1]["depends_on"], [0])
        self.assertEqual(params[0]["testcases"], "^s1-")

    def test_portable_has_no_dependencies(self):
        params = setup_contest.score_parameters(
            scenario.task_spec("cadena", "portable"))
        self.assertTrue(all("depends_on" not in p for p in params))

    def test_threshold_only_for_group_threshold(self):
        params = setup_contest.score_parameters(
            scenario.task_spec("umbral", "portable"))
        self.assertTrue(all(p["threshold"] == 1.0 for p in params))
        params = setup_contest.score_parameters(
            scenario.task_spec("suma", "portable"))
        self.assertTrue(all("threshold" not in p for p in params))


class ContestExtraKwargsTest(unittest.TestCase):

    def test_fork_full_gets_active_and_group(self):
        groups = {"loada": object(), "loadb": object()}
        kwargs = setup_contest.contest_extra_kwargs(
            "loadb", "full", groups, has_active=True)
        self.assertEqual(kwargs, {"active": True,
                                  "ranking_group": groups["loadb"]})

    def test_fork_portable_is_active_without_group(self):
        kwargs = setup_contest.contest_extra_kwargs(
            "loada", "portable", {}, has_active=True)
        self.assertEqual(kwargs, {"active": True})

    def test_upstream_gets_nothing(self):
        self.assertEqual(setup_contest.contest_extra_kwargs(
            "loada", "portable", {}, has_active=False), {})


class ContestWindowTest(unittest.TestCase):

    def test_counts_from_the_call(self):
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        start, stop = setup_contest.contest_window(195, 1500)
        after = datetime.now(timezone.utc).replace(tzinfo=None)
        self.assertLessEqual(before + timedelta(seconds=195), start)
        self.assertLessEqual(start, after + timedelta(seconds=195))
        self.assertEqual(stop - start, timedelta(seconds=1500))
