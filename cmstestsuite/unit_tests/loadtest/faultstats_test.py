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

"""Tests for the pass criteria of a fault run (faultstats.py)."""

import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import faultstats  # noqa: E402


START = 1_800_000_000.0  # the contest start
F2_START = START + 1080
RECOVERED = START + 1350  # F2's partition step
ES = "EvaluationService::_write_results_sync"
LOST = ("Lost the database connection while writing worker results "
        "(server closed the connection unexpectedly); the sweeper will "
        "redo them within about 2 minutes.")


def stamp(moment: float) -> str:
    """Return the time of a CMS log line (UTC)."""
    return datetime.datetime.fromtimestamp(
        moment, datetime.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S,%f")[:23]


# The gating row of F3: the probe of faults.py.
PROBE_ROW = "a new connection during the pause fails with 'timeout expired'"


def record_line(moment: float, where: str, message: str) -> str:
    """Return a CMS log record, as the log files hold it."""
    return "%s - INFO [%s] %s\n" % (stamp(moment), where, message)


class FaultStatsTest(unittest.TestCase):
    """A small run where every criterion passes; tests break one each."""

    def setUp(self):
        self.run_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.run_dir)
        self.faults = [
            {"fault": "F1", "start": START + 120, "end": START + 121,
             "terminated": 9},
            {"fault": "F1", "start": START + 180, "end": START + 181,
             "terminated": 8},
            {"fault": "F3", "start": START + 600, "end": START + 630,
             "probe": {"elapsed_s": 10.1, "outcome": "error",
                       "error": "OperationalError: timeout expired",
                       "connect_started_at": START + 603, "green": True}},
            {"fault": "F4", "start": START + 1000, "end": START + 1006,
             "holder_ready": True, "exit_status": 1, "duration": 6.0,
             "message": True},
            {"fault": "F2", "start": F2_START, "end": F2_START + 90,
             "rules_removed": True, "partition_step": RECOVERED,
             "partition_step_terminated": 0},
        ]
        self.logs = {
            "EvaluationService-0": [
                (START + 125, ES, LOST),
                (START + 185, ES, LOST),
                (F2_START + 5, ES, "Starting commit process..."),
                (F2_START + 65, ES, LOST),
                (RECOVERED + 30, ES, "Starting commit process..."),
                (RECOVERED + 31, ES, "Done"),
            ],
            "ContestWebServer-0": [
                (START + 612, "web::log_request",
                 "500 GET /loada (172.18.0.4) 10012.00ms"),
                (F2_START + 70, "web::log_request",
                 "500 GET /loada (172.18.0.4) 61000.00ms"),
                # The error is in the traceback lines, which have no time
                # of their own.
                (START + 611.9, "web::log_exception",
                 "Uncaught exception GET /loada (172.18.0.4)\n"
                 "Traceback (most recent call last):\n"
                 "psycopg2.OperationalError: timeout expired"),
            ],
            "ScoringService-0": [
                (F2_START + 1, "async_triggeredservice::run",
                 "Executing operation `scoring submission 1 on dataset 1'."),
                (F2_START + 62, "async_triggeredservice::run",
                 "Unexpected error when executing operation `scoring "
                 "submission 1 on dataset 1'."),
            ],
        }
        self.requests = [
            {"t": START + 602, "dur": 10.0, "status": 500,
             "shard": "cms:8888"},
            {"t": START + 640, "dur": 0.1, "status": 200,
             "shard": "cms:8888"},
            {"t": START + 1001, "dur": 4.0, "status": 200,
             "shard": "cms:8888"},
            {"t": RECOVERED + 5, "dur": 0.1, "status": 200, "shard": "rws"},
            {"t": RECOVERED + 20, "dur": 0.1, "status": 200,
             "shard": "cms:8888"},
            # A second shard, which answers again later than the first.
            {"t": START + 645, "dur": 0.2, "status": 200,
             "shard": "cms:8889"},
            {"t": RECOVERED + 30, "dur": 0.2, "status": 200,
             "shard": "cms:8889"},
        ]
        self.submissions: list[dict] | None = [
            {"timestamp": F2_START - 10, "scored_at": RECOVERED + 200},
            {"timestamp": START + 10, "scored_at": START + 40},
        ]
        self.census = "cmsEvaluationService expected=1 found=1\n"
        # LogService's merged copy of every record (cmslog/cms).
        self.merged = False

    def _write(self) -> None:
        with open(os.path.join(self.run_dir, "faults.json"), "w") as f:
            json.dump(self.faults, f)
        for service, lines in self.logs.items():
            directory = os.path.join(self.run_dir, "cmslog", service)
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, "2026-10-08-00-00-00.log")
            with open(path, "w") as f:
                for moment, where, message in lines:
                    f.write(record_line(moment, where, message))
        if self.merged:
            directory = os.path.join(self.run_dir, "cmslog", "cms")
            os.makedirs(directory)
            with open(os.path.join(directory, "1800000000.log"), "w") as f:
                for lines in self.logs.values():
                    for moment, where, message in lines:
                        f.write(record_line(moment, where, message))
        with open(os.path.join(self.run_dir, "requests.jsonl"), "w") as f:
            for request in self.requests:
                f.write(json.dumps(request) + "\n")
        if self.submissions is not None:
            path = os.path.join(self.run_dir, "db_export.json")
            with open(path, "w") as f:
                json.dump({"submissions": self.submissions}, f)
        with open(os.path.join(self.run_dir, "processes_end.txt"),
                  "w") as f:
            f.write(self.census)

    def _rows(self) -> dict[tuple[str, str], tuple[str, bool]]:
        self._write()
        return {(fault, criterion): (value, passed)
                for fault, criterion, value, _, passed
                in faultstats.check(self.run_dir)}

    def _main(self) -> tuple[int, str]:
        self._write()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = faultstats.main(["faultstats.py", self.run_dir])
        return status, output.getvalue()

    def test_a_good_run_passes_every_criterion(self):
        status, output = self._main()
        self.assertEqual(status, 0, output)
        self.assertNotIn("FAIL", output)
        rows = self._rows()
        expected = {
            ("F1", "lost-connection log lines within 120 s (#1)"):
                ("1", True),
            ("F1", "lost-connection log lines within 120 s (#2)"):
                ("1", True),
            ("F2", "longest ES flush that lost the connection"):
                ("60.0 s of 1", True),
            ("F2", "longest CWS 500 answer"): ("61.0 s of 1", True),
            ("F2", "longest SS operation that failed"):
                ("61.0 s of 1", True),
            ("F2", "CWS 500 answers during the drop"): ("1", True),
            ("F2", "DROP rules removed after the drop"): ("yes", True),
            ("F2", "shard cms:8888 pages answer again after"):
                ("20.1 s", True),
            ("F2", "shard cms:8889 pages answer again after"):
                ("30.2 s", True),
            ("F2", "ES commits again after"): ("31.0 s", True),
            ("F2", "earlier submissions scored after"): ("200.0 s", True),
            ("F3", PROBE_ROW): ("timeout expired after 10.1 s", True),
            ("F3", "'timeout expired' log lines"): ("1", True),
            ("F3", "longest CWS 500 answer"): ("10.0 s of 1", True),
            ("F3", "longest non-RWS request that started during the "
                   "pause"): ("10.0 s of 1", True),
            ("F3", "shard cms:8888 pages answer again after"):
                ("10.1 s", True),
            ("F3", "shard cms:8889 pages answer again after"):
                ("15.2 s", True),
            ("F4", "holder idle in its transaction before cmsSetupDB"):
                ("yes", True),
            ("F4", "CWS p99 from its start to 5 s after its exit"):
                ("4.0 s", True),
            ("all", "submissions never scored"): ("0", True),
        }
        for key, value in expected.items():
            with self.subTest(row=key):
                self.assertEqual(rows[key], value)

    def test_a_call_that_failed_too_late_fails_its_row(self):
        self.logs["EvaluationService-0"][3] = (F2_START + 85, ES, LOST)
        rows = self._rows()
        self.assertEqual(
            rows[("F2", "longest ES flush that lost the connection")],
            ("80.0 s of 1", False))
        self.assertEqual(self._main()[0], 1)

    def test_an_f3_without_timeout_expired_lines_does_not_fail(self):
        # Informational: the probe decides, a connect may not be needed.
        del self.logs["ContestWebServer-0"][2]
        rows = self._rows()
        self.assertEqual(rows[("F3", "'timeout expired' log lines")],
                         ("0", True))
        self.assertEqual(self._main()[0], 0)

    def test_an_f3_with_no_500_answers_passes_that_row(self):
        del self.logs["ContestWebServer-0"][0]
        rows = self._rows()
        self.assertEqual(rows[("F3", "longest CWS 500 answer")],
                         ("no 500 answers", True))
        self.assertEqual(rows[("F3", "'timeout expired' log lines")],
                         ("1", True))
        self.assertEqual(self._main()[0], 0)

    def test_an_f3_500_answer_over_the_deadline_fails_its_row(self):
        self.logs["ContestWebServer-0"][0] = (
            START + 625, "web::log_request",
            "500 GET /loada (172.18.0.4) 20000.00ms")
        self.assertEqual(self._rows()[("F3", "longest CWS 500 answer")],
                         ("20.0 s of 1", False))

    def test_an_f3_request_that_waited_for_the_resume_does_not_fail(self):
        # A pooled connection waits for the resume: informational.
        self.requests[0] = {"t": START + 601, "dur": 28.0, "status": 200,
                            "shard": "cms:8888"}
        self.assertEqual(
            self._rows()[("F3", "longest non-RWS request that started "
                          "during the pause")],
            ("28.0 s of 1", True))
        self.assertEqual(self._main()[0], 0)

    def test_an_f3_with_no_request_in_the_pause_passes_that_row(self):
        # Only a ranking request lasted during the pause: not counted.
        self.requests[0] = {"t": START + 601, "dur": 40.0, "status": 200,
                            "shard": "rws"}
        self.assertEqual(
            self._rows()[("F3", "longest non-RWS request that started "
                          "during the pause")],
            ("no request started during the pause", True))

    def _probe_row(self, probe, connect_started_at: float | None = None
                   ) -> tuple[str, bool]:
        """Return the probe row for a probe record.

        connect_started_at: when its connect began; by default 3 s into
            the pause. The record is completed with it.

        """
        if probe is None:
            del self.faults[2]["probe"]
        else:
            self.faults[2]["probe"] = {
                "connect_started_at": (START + 603
                                       if connect_started_at is None
                                       else connect_started_at),
                "green": True, **probe}
        return self._rows()[("F3", PROBE_ROW)]

    def test_a_probe_that_timed_out_at_ten_seconds_passes(self):
        for elapsed in (9.0, 10.0, 13.0):
            with self.subTest(elapsed=elapsed):
                self.assertEqual(
                    self._probe_row({"elapsed_s": elapsed,
                                     "outcome": "error", "error":
                                     "OperationalError: timeout expired"}),
                    ("timeout expired after %.1f s" % elapsed, True))

    def test_a_probe_that_connected_at_once_fails_the_run(self):
        for elapsed in (0.1, 8.9):
            with self.subTest(elapsed=elapsed):
                value, passed = self._probe_row(
                    {"elapsed_s": elapsed, "outcome": "ok", "error": ""})
                self.assertFalse(passed)
                self.assertEqual(
                    value, "connected at once: the pause did not stall "
                    "connects (after %.1f s)" % elapsed)
                self.assertEqual(self._main()[0], 1)

    def test_a_probe_that_connected_after_the_resume_fails_the_run(self):
        # The connect waited for the unpause: no deadline cut it.
        for elapsed in (9.0, 26.0):
            with self.subTest(elapsed=elapsed):
                value, passed = self._probe_row(
                    {"elapsed_s": elapsed, "outcome": "ok", "error": ""})
                self.assertFalse(passed)
                self.assertEqual(
                    value, "connected only after the resume (the deadline "
                    "did not fire in time), after %.1f s" % elapsed)
                self.assertEqual(self._main()[0], 1)

    def test_a_probe_that_hung_in_its_connect_fails_the_run(self):
        value, passed = self._probe_row(
            {"elapsed_s": None, "outcome": "hung", "error": ""})
        self.assertFalse(passed)
        self.assertEqual(
            value, "hung, no answer in time: the deadline did not fire")
        self.assertEqual(self._main()[0], 1)

    def test_a_probe_that_hung_before_its_connect_is_inconclusive(self):
        # No line before the connect: it never got that far.
        self.faults[2]["probe"] = {"elapsed_s": None, "outcome": "hung",
                                   "error": ""}
        self.assertEqual(
            self._rows()[("F3", PROBE_ROW)],
            ("inconclusive: the probe never reached the connect", False))
        self.assertEqual(self._main()[0], 1)

    def test_a_probe_that_hung_after_the_pause_is_inconclusive(self):
        value, passed = self._probe_row(
            {"elapsed_s": None, "outcome": "hung", "error": ""},
            START + 640)
        self.assertFalse(passed)
        self.assertIn("inconclusive: the probe connected outside the pause",
                      value)

    def test_a_probe_that_was_not_green_fails_the_run(self):
        # A blocking libpq applies connect_timeout itself: the timing
        # would pass without the green deadline.
        timed_out = {"elapsed_s": 10.1, "outcome": "error",
                     "error": "OperationalError: timeout expired"}
        for green in (False, None):
            with self.subTest(green=green):
                value, passed = self._probe_row({**timed_out,
                                                 "green": green})
                self.assertFalse(passed)
                self.assertTrue(value.startswith("not green"), value)
                self.assertEqual(self._main()[0], 1)
        # A record without the flag fails the same way.
        self.faults[2]["probe"] = {**timed_out,
                                   "connect_started_at": START + 603}
        value, passed = self._rows()[("F3", PROBE_ROW)]
        self.assertFalse(passed)
        self.assertTrue(value.startswith("not green"), value)

    def test_a_probe_that_expired_too_late_or_too_soon_fails(self):
        for elapsed, why in ((13.1, "did not fire at about 10 s"),
                             (25.0, "did not fire at about 10 s"),
                             (8.9, "earlier than the 10 s deadline")):
            with self.subTest(elapsed=elapsed):
                value, passed = self._probe_row(
                    {"elapsed_s": elapsed, "outcome": "error",
                     "error": "OperationalError: timeout expired"})
                self.assertFalse(passed)
                self.assertIn(why, value)

    def test_a_probe_with_another_error_fails_the_run(self):
        value, passed = self._probe_row(
            {"elapsed_s": 0.2, "outcome": "error", "error":
             "OperationalError: connection refused"})
        self.assertFalse(passed)
        self.assertIn("another error: OperationalError: connection refused",
                      value)
        self.assertEqual(self._main()[0], 1)

    def test_a_probe_that_connected_inside_the_pause_counts(self):
        timed_out = {"elapsed_s": 10.0, "outcome": "error",
                     "error": "OperationalError: timeout expired"}
        # The pause is [START + 600, START + 630): the start counts, the
        # end does not.
        self.assertEqual(self._probe_row(timed_out, START + 600),
                         ("timeout expired after 10.0 s", True))
        self.assertEqual(self._probe_row(timed_out, START + 629.9),
                         ("timeout expired after 10.0 s", True))

    def test_a_probe_that_connected_after_the_pause_is_inconclusive(self):
        # It would have connected: the unpause came first.
        value, passed = self._probe_row(
            {"elapsed_s": 0.1, "outcome": "ok", "error": ""},
            START + 631.2)
        self.assertFalse(passed)
        self.assertEqual(
            value, "inconclusive: the probe connected outside the pause "
            "(31.2 s after its start)")
        self.assertEqual(self._main()[0], 1)
        # Also when it timed out: the bounds do not matter then.
        value, passed = self._probe_row(
            {"elapsed_s": 10.0, "outcome": "error",
             "error": "OperationalError: timeout expired"}, START + 630)
        self.assertFalse(passed)
        self.assertEqual(
            value, "inconclusive: the probe connected outside the pause "
            "(30.0 s after its start)")

    def test_a_probe_that_connected_before_the_pause_is_inconclusive(self):
        value, passed = self._probe_row(
            {"elapsed_s": 0.1, "outcome": "ok", "error": ""}, START + 599.5)
        self.assertFalse(passed)
        self.assertIn("(-0.5 s after its start)", value)

    def test_a_probe_without_a_connect_time_fails_the_run(self):
        probe = {"elapsed_s": 10.1, "outcome": "error", "green": True,
                 "error": "OperationalError: timeout expired"}
        self.faults[2]["probe"] = probe
        self.assertEqual(self._rows()[("F3", PROBE_ROW)],
                         ("probe missing connect time", False))
        self.assertEqual(self._main()[0], 1)

    def test_a_missing_probe_fails_the_run(self):
        self.assertEqual(self._probe_row(None),
                         ("no probe was recorded", False))
        self.assertEqual(self._main()[0], 1)

    def test_pages_are_measured_when_they_are_answered(self):
        # Started before the recovery, answered 25 s after it.
        self.requests[4] = {"t": RECOVERED - 5, "dur": 30.0,
                            "status": 200, "shard": "cms:8888"}
        self.assertEqual(
            self._rows()[("F2", "shard cms:8888 pages answer again after")],
            ("25.0 s", True))

    def test_a_shard_that_never_answers_again_fails_the_run(self):
        # cms:8888 answers, cms:8889 stays frozen: its requests end with
        # the driver's timeout.
        self.requests[-1] = {"t": RECOVERED + 30, "dur": 120.0,
                             "status": -1, "shard": "cms:8889"}
        rows = self._rows()
        self.assertEqual(
            rows[("F2", "shard cms:8888 pages answer again after")],
            ("20.1 s", True))
        self.assertEqual(
            rows[("F2", "shard cms:8889 pages answer again after")],
            ("no answer", False))
        self.assertEqual(self._main()[0], 1)
        # Its requests a minute after the limit fail too.
        self.requests.append({"t": RECOVERED + 70, "dur": 120.0,
                              "status": -1, "shard": "cms:8889"})
        self.assertEqual(
            self._rows()[("F2", "shard cms:8889 pages answer again after")],
            ("no answer, then 1 of 1 requests started 60-120 s after it "
             "ended with status -1", False))

    def test_a_shard_that_answers_late_fails_its_row(self):
        self.requests[-1] = {"t": RECOVERED + 61, "dur": 0.2,
                             "status": 200, "shard": "cms:8889"}
        self.assertEqual(
            self._rows()[("F2", "shard cms:8889 pages answer again after")],
            ("61.2 s", False))

    def test_a_shard_whose_requests_still_fail_later_fails_its_row(self):
        # It answered in time, but a minute after the limit its requests
        # still end with a connection error.
        self.requests.append({"t": RECOVERED + 90, "dur": 0.5,
                              "status": -1, "shard": "cms:8889"})
        self.requests.append({"t": RECOVERED + 100, "dur": 0.1,
                              "status": 200, "shard": "cms:8889"})
        self.assertEqual(
            self._rows()[("F2", "shard cms:8889 pages answer again after")],
            ("30.2 s, then 1 of 2 requests started 60-120 s after it "
             "ended with status -1", False))
        # Outside that minute, a -1 is not counted.
        self.requests[-2]["t"] = RECOVERED + 130
        self.assertEqual(
            self._rows()[("F2", "shard cms:8889 pages answer again after")],
            ("30.2 s", True))

    def test_a_run_without_cws_requests_fails_the_pages_rows(self):
        self.requests = [request for request in self.requests
                         if request["shard"] == "rws"]
        rows = self._rows()
        for fault in ("F2", "F3"):
            with self.subTest(fault=fault):
                self.assertEqual(rows[(fault, "pages answer again after")],
                                 ("no CWS request", False))

    def test_an_f2_whose_rules_were_not_removed_fails_its_row(self):
        for removed, value in ((False, "no"), (None, "not recorded")):
            with self.subTest(removed=removed):
                if removed is None:
                    del self.faults[4]["rules_removed"]
                else:
                    self.faults[4]["rules_removed"] = removed
                self.assertEqual(
                    self._rows()[("F2", "DROP rules removed after the "
                                  "drop")], (value, False))
                self.assertEqual(self._main()[0], 1)

    def test_an_f4_whose_cmssetupdb_hung_fails_its_rows(self):
        self.faults[3].update(exit_status=None, duration=120.0,
                              message=False)
        rows = self._rows()
        self.assertEqual(rows[("F4", "cmsSetupDB exit status")],
                         ("hung", False))
        self.assertEqual(rows[("F4", "cmsSetupDB duration")],
                         ("120.0 s", False))
        self.assertEqual(self._main()[0], 1)

    def test_a_fault_that_failed_shows_its_error(self):
        error = "CalledProcessError: Command 'pause' returned 1. (gone)"
        for index, fault, criterion in ((0, "F1", "ran (#1)"),
                                        (2, "F3", "ran"), (3, "F4", "ran"),
                                        (4, "F2", "ran")):
            with self.subTest(fault=fault):
                self.setUp()
                # As run_schedule records it: the start and the error.
                self.faults[index] = {"fault": fault,
                                      "start": self.faults[index]["start"],
                                      "error": error}
                self.assertEqual(self._rows()[(fault, criterion)],
                                 ("failed: " + error, False))
                self.assertEqual(self._main()[0], 1)

    def test_an_f4_whose_holder_was_not_ready_fails_its_row(self):
        self.faults[3]["holder_ready"] = False
        value, passed = self._rows()[
            ("F4", "holder idle in its transaction before cmsSetupDB")]
        self.assertFalse(passed)
        self.assertTrue(value.startswith("no ("), value)
        self.assertEqual(self._main()[0], 1)

    def test_an_f1_window_ends_at_the_next_injection(self):
        # The only line is the effect of the second injection (at 180 s).
        del self.logs["EvaluationService-0"][0]
        rows = self._rows()
        self.assertEqual(
            rows[("F1", "lost-connection log lines within 120 s (#1)")],
            ("0", False))
        self.assertEqual(
            rows[("F1", "lost-connection log lines within 120 s (#2)")],
            ("1", True))

    def test_a_500_before_the_drop_is_not_one_during_it(self):
        # Logged 10 s before the drop, for a request that started 15 s
        # before it: inside the window of the calls, but not during.
        self.logs["ContestWebServer-0"][1] = (
            F2_START - 10, "web::log_request",
            "500 GET /loada (172.18.0.4) 5000.00ms")
        rows = self._rows()
        self.assertEqual(rows[("F2", "CWS 500 answers during the drop")],
                         ("0", False))
        self.assertEqual(rows[("F2", "longest CWS 500 answer")],
                         ("5.0 s of 1", True))

    def test_the_merged_copy_of_the_logs_is_not_counted_twice(self):
        self.merged = True
        rows = self._rows()
        self.assertEqual(
            rows[("F1", "lost-connection log lines within 120 s (#1)")],
            ("1", True))
        self.assertEqual(rows[("F3", "'timeout expired' log lines")],
                         ("1", True))

    def test_a_fault_that_did_not_run_fails_its_row(self):
        self.faults = [record for record in self.faults
                       if record["fault"] != "F2"]
        self.assertEqual(self._rows()[("F2", "ran")], ("no", False))
        self.assertEqual(self._main()[0], 1)

    def test_a_missing_export_fails_instead_of_crashing(self):
        self.submissions = None
        self.assertEqual(self._rows()[("all", "submissions never scored")],
                         ("no db_export.json", False))
        self.assertEqual(self._main()[0], 1)

    def test_a_dead_process_fails(self):
        self.census = "cmsWorker expected=8 found=7 SHORT\n"
        self.assertEqual(
            self._rows()[("all", "CMS processes still running at the end")],
            ("no", False))


class ReadLogsTest(unittest.TestCase):

    def setUp(self):
        self.run_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.run_dir)

    def _log(self, directory: str, name: str, text: str) -> None:
        path = os.path.join(self.run_dir, "cmslog", directory)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, name), "w") as f:
            f.write(text)

    def test_untimed_lines_join_their_record_and_never_cross_files(self):
        a = record_line(START + 1, "x", "A")
        b = record_line(START + 2, "x", "B")
        self._log("ContestWebServer-0", "2026-10-08-00-00-00.log",
                  "leading one\nleading two\n" + a
                  + "  File \"f.py\", line 1\nOperationalError: timeout\n")
        self._log("ContestWebServer-0", "2026-10-08-00-00-01.log",
                  "orphan\n" + b)
        self._log("ContestWebServer-0", "last.log", "not a log of a run\n")
        self.assertEqual(
            faultstats.read_logs(self.run_dir, "ContestWebServer"),
            [(START + 1, a.rstrip("\n") + "\n  File \"f.py\", line 1"
              "\nOperationalError: timeout"),
             (START + 2, b.rstrip("\n"))])

    def test_every_service_skips_the_merged_copy(self):
        a = record_line(START + 1, "x", "A")
        self._log("ContestWebServer-0", "2026-10-08-00-00-00.log", a)
        self._log("cms", "1800000000.log", a)
        self.assertEqual(faultstats.read_logs(self.run_dir, ""),
                         [(START + 1, a.rstrip("\n"))])


if __name__ == "__main__":
    unittest.main()
