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

"""Tests for the load-test scenario profiles."""

import unittest

from cmstestsuite.loadtest import scenario


class ExpectedScoreTest(unittest.TestCase):

    def test_full_applies_dependencies(self):
        # cadena: S(10), M(20) needs S, L(30) needs M, L(40) needs L#2.
        # wa_small passes M and L but not S, so the whole chain fails.
        self.assertEqual(
            scenario.expected_score("cadena", "wa_small", "full"), 0.0)

    def test_portable_ignores_dependencies(self):
        self.assertEqual(
            scenario.expected_score("cadena", "wa_small", "portable"),
            90.0)

    def test_full_solution_scores_the_maximum_in_both_profiles(self):
        for profile in scenario.PROFILES:
            self.assertEqual(
                scenario.expected_score("cadena", "ac", profile), 100.0)

    def test_portable_task_specs_have_no_dependencies(self):
        for tasks in scenario.TASKS.values():
            for spec in tasks:
                portable = scenario.task_spec(spec["name"], "portable")
                for sub in portable["subtasks"]:
                    self.assertNotIn("depends_on", sub)

    def test_ranked_contests_per_profile(self):
        self.assertEqual(scenario.RANKED["portable"], {"loada": "scores"})
        self.assertEqual(set(scenario.RANKED["full"]), {"loada", "loadb"})

    def test_two_phase_only_in_full(self):
        self.assertTrue(scenario.two_phase("full"))
        self.assertFalse(scenario.two_phase("portable"))

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            scenario.task_spec("cadena", "fast")


if __name__ == "__main__":
    unittest.main()
