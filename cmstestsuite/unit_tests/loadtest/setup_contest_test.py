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
import random
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


class OmiSetupTest(unittest.TestCase):

    def test_every_omi_task_has_twenty_testcases_in_four_groups(self):
        for spec in scenario.OMI_TASKS:
            self.assertEqual(len(spec["subtasks"]), 4)
            self.assertEqual(
                sum(len(setup_contest.group_cases(sub["category"]))
                    for sub in spec["subtasks"]), 20)

    def test_omi_scores_are_group_min_of_four_by_25(self):
        for spec in scenario.OMI_TASKS:
            params = setup_contest.score_parameters(
                scenario.task_spec(spec["name"], "omi"))
            self.assertEqual(spec["score_type"], "GroupMin")
            self.assertEqual([p["max_score"] for p in params], [25] * 4)

    def test_the_reps_line_follows_the_input_only_when_asked(self):
        data_in, data_out = setup_contest.make_input(
            random.Random(1), 3, 1000, reps=700)
        lines = data_in.decode().split("\n")
        self.assertEqual(lines[0], "3")
        self.assertEqual(lines[2:], ["700", ""])
        self.assertEqual(int(data_out), sum(map(int, lines[1].split())))
        plain, _ = setup_contest.make_input(random.Random(1), 3, 1000)
        self.assertEqual(plain.decode().count("\n"), 2)

    def test_omi_contest_is_published_through_its_ranking_group(self):
        groups = {"loada": object()}
        self.assertEqual(
            setup_contest.contest_extra_kwargs(
                "loada", "omi", groups, has_active=True),
            {"active": True, "ranking_group": groups["loada"]})


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
