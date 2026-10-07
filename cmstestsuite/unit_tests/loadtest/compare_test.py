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

"""Tests for the load-test run comparison."""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import analyze  # noqa: E402
import compare  # noqa: E402


class CompareTest(unittest.TestCase):

    RUNS = [
        {"run": "f1", "target": "fork", "login_p95": 0.2, "http_errors": 0},
        {"run": "f2", "target": "fork", "login_p95": 0.4, "http_errors": 2},
        {"run": "u1", "target": "upstream", "login_p95": 1.5,
         "http_errors": 0},
    ]

    def test_table_has_one_column_per_run(self):
        text = compare.table(self.RUNS, ["login_p95"])
        self.assertIn("| f1 | f2 | u1 |", text)
        self.assertIn("| login_p95 | 0.2 | 0.4 | 1.5 |", text)

    def test_compare_tolerates_missing_metrics(self):
        runs = self.RUNS + [{"run": "crashed", "target": "upstream"}]
        text = compare.table(runs, ["login_p95"])
        self.assertIn("| 1.5 | - |", text)

    def test_summarize_takes_the_median_per_target(self):
        rows = compare.summarize(self.RUNS)
        fork = next(r for r in rows if r["target"] == "fork")
        self.assertAlmostEqual(fork["login_p95"], 0.3)
        self.assertEqual(fork["runs"], 2)

    def test_table_formats_numbers_compactly(self):
        runs = [{"run": "a", "users": 120, "login_p95": 0.123456,
                 "scored_max": 1234.5, "scored_p50": float("nan"),
                 "drain_after_stop_s": None}]
        text = compare.table(runs, ["users", "login_p95", "scored_max",
                                    "scored_p50", "drain_after_stop_s"])
        self.assertIn("| users | 120 |", text)
        self.assertIn("| login_p95 | 0.123 |", text)
        self.assertIn("| scored_max | 1234 |", text)  # no exponent
        self.assertIn("| scored_p50 | - |", text)
        self.assertIn("| drain_after_stop_s | - |", text)

    def test_summarize_skips_missing_values_but_counts_the_run(self):
        runs = self.RUNS + [{"run": "crashed", "target": "fork"}]
        fork = next(r for r in compare.summarize(runs)
                    if r["target"] == "fork")
        self.assertAlmostEqual(fork["login_p95"], 0.3)
        self.assertEqual(fork["runs"], 3)

    def test_summarize_groups_by_the_given_key(self):
        runs = [{"run": "a", "profile": "portable", "users": 10},
                {"run": "b", "profile": "full", "users": 30}]
        rows = compare.summarize(runs, group_by="profile")
        self.assertEqual({r["profile"]: r["users"] for r in rows},
                         {"portable": 10, "full": 30})

    def test_default_keys_are_the_analyzer_metrics_in_order(self):
        skipped = {"run", "target", "profile", "cpu_mean_by_container",
                   "mem_last_by_container"}
        self.assertEqual(
            list(compare.DEFAULT_KEYS),
            [k for k in analyze.METRIC_KEYS if k not in skipped])


class CompareFilesTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def make_run(self, name, metrics):
        run_dir = os.path.join(self.root, name)
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, "metrics.json"), "w") as f:
            json.dump(metrics, f)
        return run_dir

    def test_load_reads_metrics_json_in_the_given_order(self):
        b = self.make_run("b", {"run": "b", "users": 2})
        a = self.make_run("a", {"run": "a", "users": 1})
        self.assertEqual([r["run"] for r in compare.load([b, a])],
                         ["b", "a"])

    def test_load_without_metrics_json_says_to_run_the_analyzer(self):
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        with self.assertRaisesRegex(FileNotFoundError, "analyze.py"):
            compare.load([empty])

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            compare.main(list(args))
        return out.getvalue()

    def test_cli_prints_one_column_per_run(self):
        a = self.make_run("a", {"run": "a", "target": "fork", "users": 5})
        b = self.make_run("b", {"run": "b", "target": "upstream",
                                "users": 7})
        text = self.run_cli(a, b)
        self.assertIn("| metric | a | b |", text)
        self.assertIn("| users | 5 | 7 |", text)

    def test_cli_median_prints_one_column_per_target(self):
        a = self.make_run("a", {"run": "a", "target": "fork",
                                "login_p95": 0.2})
        b = self.make_run("b", {"run": "b", "target": "fork",
                                "login_p95": 0.4})
        c = self.make_run("c", {"run": "c", "target": "upstream",
                                "login_p95": 1.5})
        text = self.run_cli(a, b, c, "--median")
        self.assertIn("| metric | fork | upstream |", text)
        self.assertIn("| runs | 2 | 1 |", text)
        self.assertIn("| login_p95 | 0.3 | 1.5 |", text)


if __name__ == "__main__":
    unittest.main()
