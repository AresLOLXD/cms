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

"""Tests for the pass criteria of a watchdog run (watchdogstats.py)."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

from cms.watchdog import policy

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import watchdogstats  # noqa: E402


START = 1_800_000_000.0
LABELS = ["cmsLogService 0", "cmsEvaluationService 0",
          "cmsScoringService 0", "cmsProxyService 0",
          "cmsContestWebServer 0", "cmsContestWebServer 1",
          "cmsAdminWebServer 0", "cmsWorker 0"]
WEB = ["cmsContestWebServer 0", "cmsContestWebServer 1", "cmsAdminWebServer 0"]
LAST = "last cycle at most 45 s before the driver's last request"
GAP = "no gap over 45 s between cycles"
WINDOW = "a cycle inside every F2 and F3 window"
SAMPLES = "probe latencies for every probed program"
FAVICON_P99 = "favicon p99 under 1/3 of 5 s"
HEALTH_P99 = "health() p99 under 1/3 of 3 s"


def entry(label: str, **changes) -> dict:
    kind = label.split()[0][3:]
    probed = kind != "Worker"
    http = kind in ("ContestWebServer", "AdminWebServer")
    data = {"name": label, "label": label, "kind": kind,
            "state": "RUNNING", "pid": 100, "misses": 0, "known": 6,
            "http_ok": True if http else None,
            "http_latency_s": 0.02 if http else None,
            "rpc_ok": True if probed else None,
            "rpc_latency_s": 0.01 if probed else None, "sections": []}
    data.update(changes)
    return data


def status(index: int, labels=LABELS, gate: str = "green",
           changes: dict | None = None) -> dict:
    programs = [entry(label, **(changes or {}).get(label, {}))
                for label in labels]
    return {"time": START + 15 * index, "gate": gate, "programs": programs}


def log_line(when: float, text: str) -> str:
    return "%s WARNING Watchdog: %s" % (
        time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(when)), text)


class WatchdogStatsTest(unittest.TestCase):

    def setUp(self):
        self.run_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.run_dir)
        self.statuses = [status(index) for index in range(40)]
        self.log: list[str] = []
        self.faults: list[dict] | None = None
        with open(os.path.join(self.run_dir, "requests.jsonl"), "w") as f:
            f.write(json.dumps({"t": START + 30, "dur": 0.1}) + "\n")

    def rows(self) -> dict[str, bool]:
        with open(os.path.join(self.run_dir, "watchdog_status.jsonl"),
                  "w") as f:
            for item in self.statuses:
                f.write(json.dumps(item) + "\n")
        with open(os.path.join(self.run_dir, "watchdog.log"), "w") as f:
            f.write("\n".join(self.log) + "\n")
        if self.faults is not None:
            with open(os.path.join(self.run_dir, "faults.json"), "w") as f:
                json.dump(self.faults, f)
        rows, _ = watchdogstats.check(self.run_dir, cws=2, workers=1)
        return {criterion: passed for criterion, _, _, passed in rows}

    def value(self, criterion: str) -> str:
        """Return the value of a row, from the files rows() wrote."""
        rows, _ = watchdogstats.check(self.run_dir, cws=2, workers=1)
        return next(value for name, value, _, _ in rows if name == criterion)

    def add_request(self, when: float) -> None:
        with open(os.path.join(self.run_dir, "requests.jsonl"), "a") as f:
            f.write(json.dumps({"t": when, "dur": 0.1}) + "\n")

    def test_a_quiet_run_passes(self):
        rows = self.rows()
        self.assertTrue(all(rows.values()), rows)

    def test_a_would_line_fails(self):
        self.log.append(log_line(START + 100, "WOULD restart "
                                 "cmsContestWebServer 1 (pid 100) after"))
        self.assertFalse(self.rows()["no WOULD line"])

    def test_an_early_warning_is_neither_a_hold_nor_a_would_line(self):
        # The policy's warnings name the hold that applies now and the
        # restart it would do, in other words than the held and WOULD
        # lines.
        self.log.append(log_line(
            START + 100, "early warning: 3 of the last 6 favicon probes "
            "of cmsContestWebServer 1 failed. At 5 misses, the latest "
            "included, the watchdog would restart it. But H2 applies now: "
            "the database gate is red (refused). Check ./logs.sh for its "
            "log; cmsWatchdog --snapshot cmscontestwebserver1 writes its "
            "stacks."))
        rows = self.rows()
        self.assertTrue(rows["no WOULD line"])
        self.assertTrue(rows["holds only inside F2 and F3"])

    def test_a_hold_fails_outside_a_fault_window_only(self):
        self.log.append(log_line(START + 300, "cmsContestWebServer 1 fails "
                                 "R1 (5 of 6) but is held by H2: x."))
        self.assertFalse(self.rows()["holds only inside F2 and F3"])
        self.faults = [{"fault": "F2", "start": START + 250,
                        "end": START + 340}]
        self.assertTrue(self.rows()["holds only inside F2 and F3"])

    def test_a_client_drop_is_not_a_fault_window(self):
        # faults.py --drop-client-ports (the drills D7 and D15) records
        # it in faults.json too; only F2 and F3 open a window.
        self.log.append(log_line(START + 300, "cmsContestWebServer 1 fails "
                                 "R1 (5 of 6) but is held by H2: x."))
        self.faults = [{"fault": "client-drop", "ports": [40000],
                        "start": START + 250, "end": START + 340,
                        "rules_removed": 2}]
        self.assertFalse(self.rows()["holds only inside F2 and F3"])

    def test_a_red_gate_fails_outside_a_fault_window_only(self):
        self.statuses[20] = status(20, gate="red")
        self.assertFalse(self.rows()["gate red only inside F2 and F3"])
        self.faults = [{"fault": "F3", "start": START + 300,
                        "end": START + 330},
                       {"fault": "F1", "start": START + 100,
                        "end": START + 101}]
        self.assertTrue(self.rows()["gate red only inside F2 and F3"])

    def test_programs_must_stay_from_the_driver_start(self):
        without_proxy = [label for label in LABELS
                         if label != "cmsProxyService 0"]
        # ProxyService starts late, after the driver's start: fine.
        for index in range(4):
            self.statuses[index] = status(index, labels=without_proxy)
        self.assertTrue(self.rows()["every program in every cycle"])
        # It disappears after it appeared: not fine.
        self.statuses[30] = status(30, labels=without_proxy)
        self.assertFalse(self.rows()["every program in every cycle"])

    def test_a_program_that_never_appears_fails(self):
        self.rows()
        rows, _ = watchdogstats.check(self.run_dir, cws=3, workers=1)
        passed = {criterion: ok for criterion, _, _, ok in rows}
        self.assertFalse(passed["every program in every cycle"])

    def test_an_absent_program_fails(self):
        self.statuses[25] = status(25, changes={
            "cmsWorker 0": {"state": "ABSENT", "pid": None}})
        self.assertFalse(self.rows()["every program in every cycle"])

    def test_slow_probes_fail(self):
        for index in range(0, 40, 5):
            self.statuses[index] = status(index, changes={
                "cmsContestWebServer 0": {"http_latency_s": 2.0}})
        self.assertFalse(self.rows()["favicon p99 under 1/3 of 5 s"])

    def test_near_misses(self):
        self.statuses[10] = status(10, changes={
            "cmsContestWebServer 1": {"misses": 3}})
        self.assertFalse(self.rows()["at most 2 misses in 6"])

    def test_sections_past_half_their_trip(self):
        es = "cmsEvaluationService 0"
        held_lock = {"what": "post_finish_lock:_write_results_sync",
                     "age_s": 95.0, "idle_s": 31.0, "tid": 1,
                     "statements": 3}
        self.statuses[10] = status(10, changes={es: {"sections": [
            held_lock]}})
        self.assertFalse(self.rows()["ES sections under half their trip"])
        self.statuses[10] = status(10, changes={es: {"sections": [
            dict(held_lock, what=watchdogstats.INVALIDATION)]}})
        self.assertTrue(self.rows()["ES sections under half their trip"])

    def test_a_fault_run_records_the_latencies_and_near_misses(self):
        # L3: F2 and F3 may push them past half their trips by design.
        self.statuses[10] = status(10, changes={
            "cmsContestWebServer 1": {"misses": 3, "http_latency_s": 4.0}})
        self.assertFalse(self.rows()["at most 2 misses in 6"])
        self.faults = [{"fault": "F2", "start": START + 100,
                        "end": START + 190}]
        rows = self.rows()
        self.assertTrue(rows["at most 2 misses in 6"])
        self.assertTrue(rows["favicon p99 under 1/3 of 5 s"])
        self.assertTrue(all(rows.values()), rows)
        # The L3 criteria still apply.
        self.log.append(log_line(START + 100, "WOULD restart "
                                 "cmsContestWebServer 1 (pid 100) after"))
        self.assertFalse(self.rows()["no WOULD line"])

    def test_a_watchdog_that_stops_early_fails(self):
        # Its self-guard exits after a 60 s stall, and the harness has
        # no supervisord to start it again: the cycles it left pass.
        self.statuses = self.statuses[:20]
        self.add_request(START + 15 * 39)
        rows = self.rows()
        self.assertTrue(rows["every program in every cycle"])
        self.assertFalse(rows[LAST])
        self.assertEqual(self.value(LAST), "300 s")

    def test_the_last_cycle_may_come_45_s_before_the_last_request(self):
        self.add_request(START + 15 * 39 + 45)
        self.assertTrue(self.rows()[LAST])
        self.add_request(START + 15 * 39 + 46)
        self.assertFalse(self.rows()[LAST])

    def test_a_gap_between_cycles_fails(self):
        del self.statuses[20:22]  # From 285 s to 330 s: 45 s, fine.
        self.assertTrue(self.rows()[GAP])
        del self.statuses[20]  # From 285 s to 345 s: 60 s.
        self.assertFalse(self.rows()[GAP])
        self.assertIn("60 s after %d" % (START + 285), self.value(GAP))

    def test_a_gap_across_the_driver_start_fails(self):
        # The driver starts at 30 s.
        del self.statuses[1:5]  # From 0 s to 75 s.
        self.assertFalse(self.rows()[GAP])
        # A watchdog with no cycle before the driver's start: its first
        # cycle counts from that start.
        del self.statuses[0]  # The first cycle at 75 s: 45 s, fine.
        self.assertTrue(self.rows()[GAP])
        del self.statuses[0]  # The first cycle at 90 s: 60 s.
        self.assertFalse(self.rows()[GAP])

    def test_a_fault_window_without_a_cycle_fails(self):
        # No cycle falls inside F3's 13 s, but the gate's slack widens
        # its window over the cycles at 300 s and 315 s.
        self.faults = [{"fault": "F3", "start": START + 301,
                        "end": START + 314}]
        self.assertTrue(self.rows()[WINDOW])
        self.faults.append({"fault": "F2", "start": START + 1000,
                            "end": START + 1090})
        self.assertFalse(self.rows()[WINDOW])
        self.assertEqual(self.value(WINDOW), "%d-%d" % (
            START + 1000 - watchdogstats.FAULT_BEFORE_S,
            START + 1090 + watchdogstats.FAULT_AFTER_S))

    def test_a_run_without_favicon_samples_fails(self):
        failed = {"http_ok": False, "http_latency_s": None}
        self.statuses = [status(index, changes=dict.fromkeys(WEB, failed))
                         for index in range(40)]
        rows = self.rows()
        self.assertFalse(rows[FAVICON_P99])
        self.assertEqual(self.value(FAVICON_P99), "no samples")
        self.assertFalse(rows[SAMPLES])

    def test_a_run_without_health_samples_fails(self):
        failed = {"rpc_ok": False, "rpc_latency_s": None}
        probed = [label for label in LABELS if label != "cmsWorker 0"]
        self.statuses = [status(index, changes=dict.fromkeys(probed, failed))
                         for index in range(40)]
        rows = self.rows()
        self.assertFalse(rows[HEALTH_P99])
        self.assertEqual(self.value(HEALTH_P99), "no samples")
        self.assertFalse(rows[SAMPLES])

    def test_a_web_server_needs_favicon_samples_of_its_own(self):
        # Its health() answers and its favicon never does. In a fault
        # run too, where the p99 and miss rows are only recorded.
        self.statuses = [status(index, changes={"cmsContestWebServer 1": {
            "http_ok": False, "http_latency_s": None}})
            for index in range(40)]
        self.assertFalse(self.rows()[SAMPLES])
        self.assertEqual(self.value(SAMPLES), "cmsContestWebServer 1 favicon")
        self.faults = [{"fault": "F2", "start": START + 100,
                        "end": START + 190}]
        self.assertFalse(self.rows()[SAMPLES])

    def test_an_empty_or_missing_status_history_fails(self):
        self.statuses = []
        failing = [SAMPLES, FAVICON_P99, HEALTH_P99, LAST,
                   "every program in every cycle"]
        rows = self.rows()
        self.assertEqual([name for name in failing if rows[name]], [])
        os.remove(os.path.join(self.run_dir, "watchdog_status.jsonl"))
        rows, _ = watchdogstats.check(self.run_dir, cws=2, workers=1)
        passed = {criterion: ok for criterion, _, _, ok in rows}
        self.assertEqual([name for name in failing if passed[name]], [])

    def test_the_constants_match_the_policy(self):
        self.assertEqual(watchdogstats.HTTP_TIMEOUT_S,
                         policy.HTTP_TIMEOUT_S)
        self.assertEqual(watchdogstats.RPC_TIMEOUT_S, policy.RPC_TIMEOUT_S)
        self.assertEqual(watchdogstats.HALF_MISSES,
                         policy.MISSES_TO_WARN - 1)
        self.assertEqual(watchdogstats.HALF_SECTION_S,
                         policy.SECTION_WARN_S)
        self.assertEqual(watchdogstats.HALF_ES_IDLE_S,
                         policy.ES_IDLE_WARN_S)
        self.assertEqual(watchdogstats.INVALIDATION,
                         policy.INVALIDATION_SECTION)
        self.assertEqual(watchdogstats.MAX_CYCLE_GAP_S, 3 * policy.CYCLE_S)
        self.assertEqual(watchdogstats.FAVICON_KINDS, policy.HTTP_KINDS)
        self.assertEqual(watchdogstats.HEALTH_KINDS, policy.HEALTH_KINDS)


if __name__ == "__main__":
    unittest.main()
