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

"""Tests for the load-test analyzer."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import analyze  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "run_small")

METRIC_KEYS = {
    "run", "target", "profile", "users", "poll_cap", "request_time_header",
    "submissions_sent", "submissions_rejected", "submissions_rejected_in_time",
    "login_failures", "http_errors", "score_mismatches", "rws_pairs", "rws_mismatches",
    "login_p50", "login_p95", "submit_p50", "submit_p95", "submit_end_p95",
    "scored_p50", "scored_p95", "scored_max", "perceived_p50",
    "perceived_p95", "perceived_max", "status_polls", "drain_after_stop_s",
    "peak_pg_connections", "cpu_mean_by_container", "mem_last_by_container"}


def runs(times, phase="steady", kind="ac_cpu", testcase="s1-03-t", first=1,
         expected=100.0, text=("Output is correct",)):
    """Return (evaluations, db submissions by id, driver submissions) of
    one submission per time, each with one evaluation of the testcase."""
    evaluations, db, driver = [], {}, []
    for sub_id, t in enumerate(times, first):
        evaluations.append({
            "submission_id": sub_id, "testcase": testcase, "outcome": "1.0",
            "text": list(text), "execution_time": t,
            "execution_wall_clock_time": t * 2})
        db[sub_id] = {"id": sub_id, "opaque_id": 100 + sub_id, "task": "omia"}
        driver.append({"opaque_id": 100 + sub_id, "kind": kind,
                       "task": "omia", "phase": phase, "expected": expected})
    return evaluations, db, driver


def merged(*parts):
    evaluations, db, driver = [], {}, []
    for ev, sub, drv in parts:
        evaluations += ev
        db.update(sub)
        driver += drv
    return evaluations, db, driver


class RuntimeSpreadTest(unittest.TestCase):

    GROUP = ("ac_cpu", "omia", "s1-03-t")

    def test_spread_is_range_over_mean_per_group_and_phase(self):
        result = analyze.runtime_spread(*runs([1.0, 1.1, 1.2, 1.3, 1.4]))
        stats = result["groups"][self.GROUP]["steady"]
        self.assertEqual(stats["n"], 5)
        self.assertEqual((stats["min"], stats["max"]), (1.0, 1.4))
        self.assertAlmostEqual(stats["p50"], 1.2)
        self.assertAlmostEqual(stats["spread"], 0.4 / 1.2)
        self.assertAlmostEqual(stats["p95_p50"], 1.38 / 1.2)
        self.assertEqual(result["skipped"], 0)

    def test_burst_over_steady_ratio_needs_both_phases(self):
        result = analyze.runtime_spread(*merged(
            runs([1.0] * 5, "steady"),
            runs([1.5] * 5, "end_burst", first=6)))
        self.assertAlmostEqual(result["groups"][self.GROUP]["ratio"], 1.5)
        alone = analyze.runtime_spread(*runs([1.0] * 5, "end_burst"))
        self.assertNotIn("ratio", alone["groups"][self.GROUP])

    def test_small_or_short_groups_are_skipped_and_counted(self):
        result = analyze.runtime_spread(*merged(
            runs([1.0] * 4, "steady"),
            runs([0.01] * 6, "steady", testcase="s1-00-sample", first=5)))
        self.assertEqual(result["groups"], {})
        self.assertEqual(result["skipped"], 2)

    def test_only_fully_correct_submissions_in_the_two_phases_count(self):
        result = analyze.runtime_spread(*merged(
            runs([1.0] * 5, "steady", kind="wa_small", expected=0.0),
            runs([1.0] * 5, "drain", first=6),
            runs([1.0] * 5, "steady", first=11)))
        self.assertEqual(list(result["groups"]), [self.GROUP])
        self.assertEqual(result["groups"][self.GROUP]["steady"]["n"], 5)

    def test_wall_clock_time_and_timeouts(self):
        evaluations, db, driver = runs([1.0] * 5)
        evaluations[0]["text"] = ["Execution timed out"]
        result = analyze.runtime_spread(
            evaluations, db, driver, field="execution_wall_clock_time")
        self.assertEqual(result["tle"], 1)
        self.assertEqual(result["groups"][self.GROUP]["steady"]["p50"], 2.0)

    def test_false_timeouts_count_in_any_phase_of_a_correct_submission(self):
        timed_out = ("Execution timed out (wall clock limit exceeded)",)
        result = analyze.runtime_spread(*merged(
            runs([1.0], "start_burst", text=timed_out),
            runs([1.0], "drain", first=2, text=timed_out),
            runs([1.0], "steady", first=3, kind="tle", expected=0.0,
                 text=timed_out)))
        self.assertEqual(result["tle"], 2)


class AnalyzeTest(unittest.TestCase):

    def setUp(self):
        self.run_dir = os.path.join(tempfile.mkdtemp(), "run_small")
        shutil.copytree(FIXTURE, self.run_dir)
        self.addCleanup(shutil.rmtree, os.path.dirname(self.run_dir))

    def metrics(self):
        with open(os.path.join(self.run_dir, "metrics.json")) as f:
            return json.load(f)

    def test_rws_check_only_covers_ranked_contests(self):
        with open(os.path.join(self.run_dir, "db_export.json")) as f:
            db = json.load(f)
        ranking = analyze.load_jsonl(
            os.path.join(self.run_dir, "ranking.jsonl"))
        pairs, diff = analyze.rws_mismatches(
            db["task_scores"], ranking, {"loada"})
        self.assertEqual(diff, [])
        self.assertEqual(pairs, sum(1 for t in db["task_scores"]
                                    if t["contest"] == "loada"))

    def test_rws_check_reports_a_ranked_contest_that_differs(self):
        with open(os.path.join(self.run_dir, "db_export.json")) as f:
            db = json.load(f)
        ranking = analyze.load_jsonl(
            os.path.join(self.run_dir, "ranking.jsonl"))
        pairs, diff = analyze.rws_mismatches(
            db["task_scores"], ranking, {"loada", "loadb"})
        self.assertEqual(pairs, 3)
        self.assertEqual([(ts["user"], rws) for ts, rws in diff],
                         [("b001", 0.0)])

    def test_main_writes_summary_and_metrics(self):
        analyze.main(self.run_dir)
        self.assertTrue(os.path.exists(
            os.path.join(self.run_dir, "summary.md")))
        metrics = self.metrics()
        self.assertEqual(set(metrics), METRIC_KEYS)
        self.assertEqual(metrics["run"], "run_small")
        self.assertEqual(metrics["target"], "fork")
        self.assertEqual(metrics["profile"], "portable")
        self.assertEqual(metrics["users"], 3)
        self.assertEqual(metrics["poll_cap"], 10.0)
        self.assertEqual(metrics["request_time_header"], "X-Request-Start")
        self.assertEqual(metrics["rws_pairs"], 2)
        self.assertEqual(metrics["rws_mismatches"], 0)
        self.assertEqual(metrics["http_errors"], 1)
        self.assertEqual(metrics["submissions_sent"], 3)
        self.assertEqual(metrics["submissions_rejected"], 1)
        self.assertEqual(metrics["submissions_rejected_in_time"], 1)
        self.assertEqual(metrics["login_failures"], 1)
        self.assertEqual(metrics["score_mismatches"], 0)
        self.assertIn("login_p95", metrics)

    def test_rejections_after_the_stop_are_not_counted_as_in_time(self):
        # The run stops at 1600: the fixture rejection at 1570 is in time,
        # the two appended ones (one exactly at the stop) are late by design.
        with open(os.path.join(self.run_dir, "submissions.jsonl"), "a") as f:
            for t_submit in (1600.0, 1603.5):
                f.write(json.dumps(
                    {"user": "a001", "contest": "loada", "task": "suma",
                     "kind": "ac", "t_submit": t_submit, "phase": "drain",
                     "accepted": False, "expected": 100.0,
                     "location": "/loada/tasks/suma/submissions",
                     "status": 302}) + "\n")
        analyze.main(self.run_dir)
        metrics = self.metrics()
        self.assertEqual(metrics["submissions_sent"], 5)
        self.assertEqual(metrics["submissions_rejected"], 3)
        self.assertEqual(metrics["submissions_rejected_in_time"], 1)

    def test_metrics_values_come_from_the_inputs(self):
        analyze.main(self.run_dir)
        metrics = self.metrics()
        self.assertEqual(metrics["login_p50"], 0.7)
        self.assertEqual(metrics["submit_end_p95"], 0.39)
        self.assertEqual(metrics["scored_p50"], 37.5)
        self.assertEqual(metrics["scored_max"], 69.5)
        self.assertEqual(metrics["drain_after_stop_s"], 30.0)
        self.assertEqual(metrics["peak_pg_connections"], 7)

    def test_poll_cap_is_null_without_a_cap_and_in_older_runs(self):
        users_path = os.path.join(self.run_dir, "users.json")
        with open(users_path) as f:
            users = json.load(f)
        for case in ("null", "missing"):
            with self.subTest(case=case):
                if case == "null":
                    users["poll_cap"] = None
                else:
                    del users["poll_cap"]
                with open(users_path, "w") as f:
                    json.dump(users, f)
                analyze.main(self.run_dir)
                self.assertIsNone(self.metrics()["poll_cap"])

    def test_request_time_header_is_empty_when_off_and_null_in_older_runs(self):
        users_path = os.path.join(self.run_dir, "users.json")
        with open(users_path) as f:
            users = json.load(f)
        for case, expected in (("off", ""), ("missing", None)):
            with self.subTest(case=case):
                if case == "off":
                    users["request_time_header"] = ""
                else:
                    del users["request_time_header"]
                with open(users_path, "w") as f:
                    json.dump(users, f)
                analyze.main(self.run_dir)
                self.assertEqual(self.metrics()["request_time_header"],
                                 expected)

    def test_perceived_latency_is_first_poll_seen_final_minus_submit(self):
        # The fixture: a001 submits at 1550 and sees it final at 1561,
        # a002 submits at 1560 and sees it at 1635.
        analyze.main(self.run_dir)
        metrics = self.metrics()
        self.assertEqual(metrics["perceived_p50"], 43.0)
        self.assertEqual(metrics["perceived_p95"], 71.8)
        self.assertEqual(metrics["perceived_max"], 75.0)
        with open(os.path.join(self.run_dir, "summary.md")) as f:
            self.assertIn("All: perceived (browser backoff) p50 43.0 s, "
                          "p95 71.8 s, max 75.0 s; 3 status polls.", f.read())

    def test_perceived_latency_counts_only_submissions_seen_final(self):
        # One more submission seen final in another phase, and one that
        # never was (stuck): only the first one moves the percentiles.
        with open(os.path.join(self.run_dir, "submissions.jsonl"), "a") as f:
            for opaque_id, t_terminal_seen, final_status in (
                    (13, 1004.0, 5), (14, None, "stuck")):
                entry = {"user": "a001", "contest": "loada", "task": "suma",
                         "kind": "ac", "t_submit": 1000.0, "phase": "steady",
                         "accepted": True, "expected": 100.0,
                         "opaque_id": opaque_id, "final_status": final_status,
                         "polls": 3}
                if t_terminal_seen is not None:
                    entry["t_terminal_seen"] = t_terminal_seen
                f.write(json.dumps(entry) + "\n")
        analyze.main(self.run_dir)
        metrics = self.metrics()
        self.assertEqual(metrics["perceived_p50"], 11.0)
        self.assertEqual(metrics["perceived_max"], 75.0)

    def test_perceived_latency_needs_no_database_export(self):
        os.remove(os.path.join(self.run_dir, "db_export.json"))
        analyze.main(self.run_dir)
        metrics = self.metrics()
        self.assertEqual(metrics["perceived_p50"], 43.0)
        self.assertEqual(metrics["perceived_max"], 75.0)

    def test_perceived_latency_is_null_when_nothing_was_seen_final(self):
        with open(os.path.join(self.run_dir, "submissions.jsonl"), "w") as f:
            f.write(json.dumps(
                {"user": "a002", "contest": "loada", "task": "suma",
                 "kind": "ac", "t_submit": 1570.0, "phase": "end_burst",
                 "accepted": False, "expected": 100.0, "status": 302}) + "\n")
        analyze.main(self.run_dir)
        metrics = self.metrics()
        for key in ("perceived_p50", "perceived_p95", "perceived_max"):
            self.assertIsNone(metrics[key], key)

    def test_status_polls_are_the_status_poll_requests_of_every_phase(self):
        # Counted from requests.jsonl, like the n column of the HTTP table
        # in summary.md. A failed poll is still a poll.
        with open(os.path.join(self.run_dir, "requests.jsonl"), "a") as f:
            f.write(json.dumps(
                {"t": 1640.0, "kind": "status_poll", "dur": 3.0,
                 "status": 500, "ok": False, "user": "a002",
                 "shard": "cms:8888", "phase": "drain"}) + "\n")
        analyze.main(self.run_dir)
        metrics = self.metrics()
        self.assertEqual(metrics["status_polls"], 4)
        self.assertEqual(metrics["http_errors"], 2)

    def test_docker_stats_follow_the_project_prefix(self):
        analyze.main(self.run_dir, project_prefix="cmsload-")
        metrics = self.metrics()
        self.assertEqual(metrics["cpu_mean_by_container"],
                         {"cmsload-fork-cms-1": 60.0})
        self.assertEqual(metrics["mem_last_by_container"],
                         {"cmsload-fork-cms-1": "530MiB"})
        analyze.main(self.run_dir, project_prefix="other-")
        self.assertEqual(self.metrics()["cpu_mean_by_container"],
                         {"other-container": 99.0})

    def test_summary_has_the_runtime_spread_only_with_evaluations(self):
        analyze.main(self.run_dir)
        with open(os.path.join(self.run_dir, "summary.md")) as f:
            self.assertNotIn("Runtime spread", f.read())
        path = os.path.join(self.run_dir, "db_export.json")
        with open(path) as f:
            db = json.load(f)
        db["evaluations"] = [
            {"submission_id": 1, "testcase": "s1-03-t", "outcome": "1.0",
             "text": ["Output is correct"], "execution_time": 1.0 + i / 10,
             "execution_wall_clock_time": 1.5} for i in range(5)]
        with open(path, "w") as f:
            json.dump(db, f)
        analyze.main(self.run_dir)
        with open(os.path.join(self.run_dir, "summary.md")) as f:
            summary = f.read()
        self.assertIn("Runtime spread (execution_time)", summary)
        self.assertIn("Runtime spread (execution_wall_clock_time)", summary)
        self.assertEqual(set(self.metrics()), METRIC_KEYS)

    def test_a_run_without_the_optional_inputs_still_gets_metrics(self):
        for name in ("docker_stats.log", "db.log", "monitor.jsonl",
                     "ranking.jsonl"):
            path = os.path.join(self.run_dir, name)
            if os.path.exists(path):
                os.remove(path)
        shutil.rmtree(os.path.join(self.run_dir, "cmslog"))
        analyze.main(self.run_dir)
        self.assertTrue(os.path.exists(
            os.path.join(self.run_dir, "summary.md")))
        metrics = self.metrics()
        self.assertEqual(set(metrics), METRIC_KEYS)
        self.assertIsNone(metrics["peak_pg_connections"])
        self.assertIsNone(metrics["cpu_mean_by_container"])
        self.assertIsNone(metrics["mem_last_by_container"])

    def test_a_run_that_crashed_before_the_db_export_gets_nulls(self):
        os.remove(os.path.join(self.run_dir, "db_export.json"))
        analyze.main(self.run_dir)
        metrics = self.metrics()
        self.assertEqual(set(metrics), METRIC_KEYS)
        for key in ("score_mismatches", "rws_pairs", "rws_mismatches",
                    "scored_p50", "scored_p95", "scored_max",
                    "drain_after_stop_s"):
            self.assertIsNone(metrics[key], key)
        self.assertEqual(metrics["http_errors"], 1)
