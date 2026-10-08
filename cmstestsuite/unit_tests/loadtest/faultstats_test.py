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
             "exit_status": 1, "duration": 6.0, "message": True},
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

    def _write(self) -> None:
        with open(os.path.join(self.run_dir, "faults.json"), "w") as f:
            json.dump(self.faults, f)
        for service, lines in self.logs.items():
            directory = os.path.join(self.run_dir, "cmslog", service)
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, "2026-10-08-00-00-00.log")
            with open(path, "w") as f:
                for moment, where, message in lines:
                    f.write("%s - INFO [%s] %s\n"
                            % (stamp(moment), where, message))
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
                ("2", True),
            ("F2", "longest ES flush that lost the connection"):
                ("60.0 s of 1", True),
            ("F2", "longest CWS 500 answer"): ("61.0 s of 1", True),
            ("F2", "longest SS operation that failed"):
                ("61.0 s of 1", True),
            ("F2", "pages answer again after"): ("20.0 s", True),
            ("F2", "ES commits again after"): ("31.0 s", True),
            ("F2", "earlier submissions scored after"): ("200.0 s", True),
            ("F3", "'timeout expired' log lines"): ("1", True),
            ("F3", "longest CWS 500 answer"): ("10.0 s of 1", True),
            ("F3", "pages answer again after"): ("10.0 s", True),
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
        self.assertIn("deadline was not exercised", value)
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


if __name__ == "__main__":
    unittest.main()
