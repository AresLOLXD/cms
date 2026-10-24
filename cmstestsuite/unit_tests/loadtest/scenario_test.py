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


class OmiProfileTest(unittest.TestCase):

    def test_omi_is_a_two_phase_profile_with_one_ranked_contest(self):
        self.assertIn("omi", scenario.PROFILES)
        self.assertTrue(scenario.two_phase("omi"))
        self.assertEqual(scenario.RANKED["omi"], {"loada": "loada/scores"})

    def test_omi_has_four_tasks_in_loada_and_none_in_loadb(self):
        self.assertEqual(len(scenario.contest_tasks("loada", "omi")), 4)
        self.assertEqual(scenario.contest_tasks("loadb", "omi"), [])
        self.assertEqual(scenario.contest_tasks("loadb", "full"),
                         scenario.TASKS["loadb"])

    def test_omi_keeps_the_dependencies_of_its_tasks(self):
        chained = [spec["name"] for spec in scenario.OMI_TASKS
                   if scenario.task_spec(spec["name"], "omi")["subtasks"][1]
                   .get("depends_on")]
        self.assertEqual(len(chained), 1)

    def test_ac_cpu_scores_the_maximum_and_a_wrong_kind_nothing(self):
        for spec in scenario.OMI_TASKS:
            self.assertEqual(
                scenario.expected_score(spec["name"], "ac_cpu", "omi"), 100.0)
            self.assertEqual(
                scenario.expected_score(spec["name"], "wa_overflow", "omi"),
                0.0)

    def test_omi_weights_sum_to_one_and_every_kind_in_use_is_cpp(self):
        weights = scenario.weights("omi")
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        used = [kind for kind, weight in weights.items() if weight]
        self.assertTrue(used)
        self.assertEqual({scenario.SOLUTIONS[kind][1] for kind in used},
                         {"cpp"})
        self.assertEqual(scenario.languages("omi"), {"cpp": "C++20 / g++"})

    def test_other_profiles_keep_their_weights_and_never_send_ac_cpu(self):
        for profile in ("portable", "full"):
            weights = scenario.weights(profile)
            self.assertAlmostEqual(sum(weights.values()), 1.0)
            self.assertEqual(weights["ac_cpu"], 0.0)

    def test_omi_time_limit_is_longer_than_the_others(self):
        self.assertEqual(scenario.time_limit("omi"), 2.0)
        self.assertEqual(scenario.time_limit("full"), scenario.TIME_LIMIT)


if __name__ == "__main__":
    unittest.main()
