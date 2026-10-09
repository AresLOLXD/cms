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
            {"fault": "F3", "start": START + 600, "end": START + 630},
            {"fault": "F4", "start": START + 1000, "end": START + 1006,
             "holder_ready": True, "exit_status": 1, "duration": 6.0,
             "message": True},
            {"fault": "F2", "start": F2_START, "end": F2_START + 90,
             "partition_step": RECOVERED, "partition_step_terminated": 0},
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
            ("F2", "pages answer again after"): ("20.1 s", True),
            ("F2", "ES commits again after"): ("31.0 s", True),
            ("F2", "earlier submissions scored after"): ("200.0 s", True),
            ("F3", "'timeout expired' log lines"): ("1", True),
            ("F3", "longest CWS 500 answer"): ("10.0 s of 1", True),
            ("F3", "longest non-RWS request that started during the "
                   "pause"): ("10.0 s of 1", True),
            ("F3", "pages answer again after"): ("10.1 s", True),
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

    def test_an_f3_without_timeout_expired_fails_its_row(self):
        del self.logs["ContestWebServer-0"][2]
        value, passed = self._rows()[("F3", "'timeout expired' log lines")]
        self.assertFalse(passed)
        self.assertTrue(value.startswith("0 ("), value)
        self.assertIn("either none was attempted or the deadline did not "
                      "fire", value)
        self.assertEqual(self._main()[0], 1)

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

    def test_an_f3_request_that_waited_for_the_resume_fails_its_row(self):
        self.requests[0] = {"t": START + 601, "dur": 28.0, "status": 200,
                            "shard": "cms:8888"}
        self.assertEqual(
            self._rows()[("F3", "longest non-RWS request that started "
                          "during the pause")],
            ("28.0 s of 1", False))
        self.assertEqual(self._main()[0], 1)

    def test_an_f3_with_no_request_in_the_pause_passes_that_row(self):
        # Only a ranking request lasted during the pause: not counted.
        self.requests[0] = {"t": START + 601, "dur": 40.0, "status": 200,
                            "shard": "rws"}
        self.assertEqual(
            self._rows()[("F3", "longest non-RWS request that started "
                          "during the pause")],
            ("no request started during the pause", True))

    def test_pages_are_measured_when_they_are_answered(self):
        # Started before the recovery, answered 25 s after it.
        self.requests[-1] = {"t": RECOVERED - 5, "dur": 30.0,
                             "status": 200, "shard": "cms:8888"}
        self.assertEqual(self._rows()[("F2", "pages answer again after")],
                         ("25.0 s", True))

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
