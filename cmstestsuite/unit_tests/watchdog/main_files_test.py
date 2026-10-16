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

"""Tests for cmsWatchdog's files, alert channels and --status line."""

import io
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from cms import config
from cms.service.LogService import LogService
from cms.watchdog import policy
from cms.watchdog.main import Alerter, Files, log_record, status_line
from cms.watchdog.probes import ProbeResult
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


WALL = 1_800_000_000.0


class FakeLog:
    """Stands in for the RpcProbe to LogService: records each call."""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[tuple[str, dict]] = []

    async def call(self, method: str, data: dict | None = None):
        self.calls.append((method, data))
        return ProbeResult(self.ok, 0.01 if self.ok else None,
                           "" if self.ok else "timed out")


class TestFiles(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.run_dir = os.path.join(self.root, "run")
        self.files = Files(self.run_dir, os.path.join(self.root, "log"))

    def test_a_missing_ledger_starts_empty(self):
        self.assertEqual(self.files.load_ledger(WALL), (policy.Ledger(), ""))

    def test_the_ledger_survives_a_restart(self):
        ledger = policy.Ledger()
        ledger.book(policy.Booking(
            WALL, "cmscontestwebserver1", "cmsContestWebServer 1",
            policy.SERVICES_POOL, policy.RESTART, "R1", False, 109), False)
        self.files.save_ledger(ledger)
        again = Files(self.run_dir, os.path.join(self.root, "log"))
        self.assertEqual(again.load_ledger(WALL + 60), (ledger, ""))
        self.assertEqual(os.listdir(self.run_dir), ["watchdog-ledger.json"])

    def test_a_ledger_that_does_not_parse_exhausts_every_budget(self):
        os.makedirs(self.run_dir)
        for content in (b"{broken", b'{"real": "\xff\xfe"}'):
            with self.subTest(content=content):
                with open(self.files.ledger, "wb") as f:
                    f.write(content)
                ledger, problem = self.files.load_ledger(WALL)
                self.assertEqual(ledger.exhausted_until, WALL + 3600)
                self.assertIn("did not parse", problem)
                # Every budget: the restarts' and the FATAL starts'.
                self.assertIn("every budget counts as used until %s"
                              % policy.hhmm_utc(WALL + 3600), problem)

    def test_a_future_ledger_is_brought_back_to_now(self):
        # A hand edit with times far ahead must not freeze the budgets
        # for years: at most an hour, as for a ledger that does not
        # parse (spec 3.10).
        ledger = policy.Ledger(exhausted_until=WALL + 10 * 365 * 86400)
        ledger.book(policy.Booking(
            WALL + 86400, "cmscontestwebserver1", "cmsContestWebServer 1",
            policy.SERVICES_POOL, policy.RESTART, "R1", False, 109), False)
        self.files.save_ledger(ledger)
        loaded, problem = self.files.load_ledger(WALL)
        self.assertEqual(problem, "")
        self.assertEqual(loaded.exhausted_until,
                         WALL + policy.CORRUPT_LEDGER_S)
        self.assertEqual([booking.wall for booking in loaded.real], [WALL])

    def test_a_future_shadow_booking_is_brought_back_to_now(self):
        # The would-actions of H9 have budgets of their own.
        ledger = policy.Ledger()
        ledger.book(policy.Booking(
            WALL + 86400, "cmsworker3", "cmsWorker 3", policy.WORKERS_POOL,
            policy.RESTART, "R4", True, 4242), True)
        self.files.save_ledger(ledger)
        loaded, problem = self.files.load_ledger(WALL)
        self.assertEqual(problem, "")
        self.assertIsNone(loaded.exhausted_until)
        self.assertEqual([booking.wall for booking in loaded.shadow], [WALL])

    def test_a_future_red_interval_and_incident_are_brought_back_to_now(
            self):
        # An outage that ends ahead of now would make R5 see one after
        # every later start of a program.
        ledger = policy.Ledger(
            red=[[WALL - 600, WALL + 3600]],
            incidents={"cmscontestwebserver1": {
                "rule": "R1", "opened": WALL + 3600, "passes": 0}})
        self.files.save_ledger(ledger)
        loaded, problem = self.files.load_ledger(WALL)
        self.assertEqual(problem, "")
        self.assertEqual(loaded.red, [[WALL - 600, WALL]])
        self.assertFalse(loaded.red_since(WALL))
        self.assertEqual(loaded.incidents, {"cmscontestwebserver1": {
            "rule": "R1", "opened": WALL, "passes": 0}})

    def test_every_red_interval_is_brought_back_to_now(self):
        ledger = policy.Ledger(red=[
            [WALL - 600, WALL - 300], [WALL + 60, WALL + 120],
            [WALL + 60, None]])
        self.files.save_ledger(ledger)
        loaded, _problem = self.files.load_ledger(WALL)
        # A past interval is kept, a future one ends now, and an open
        # one (the gate was red when it was saved) stays open.
        self.assertEqual(loaded.red, [
            [WALL - 600, WALL - 300], [WALL, WALL], [WALL, None]])

    def test_a_ledger_json_cannot_hold_is_a_failed_write(self):
        ledger = policy.Ledger()
        ledger.book(policy.Booking(
            float("nan"), "cmscontestwebserver1", "cmsContestWebServer 1",
            policy.SERVICES_POOL, policy.RESTART, "R1", False, 109), False)
        with self.assertRaises(OSError):
            self.files.save_ledger(ledger)
        self.assertFalse(os.path.exists(self.files.ledger))

    def test_a_failed_write_keeps_the_previous_ledger(self):
        # The callers handle the failed write as one: the ledger on disk
        # is the last one written, whole.
        self.files.save_ledger(policy.Ledger())
        with open(self.files.ledger, encoding="utf-8") as f:
            before = f.read()
        with self.assertRaises(OSError):
            self.files.save_ledger(
                policy.Ledger(exhausted_until=float("inf")))
        with open(self.files.ledger, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(os.listdir(self.run_dir), ["watchdog-ledger.json"])

    def test_the_status_is_written_atomically(self):
        self.files.save_status({"time": WALL, "mode": "observe"})
        with open(self.files.status) as f:
            self.assertEqual(json.load(f)["mode"], "observe")
        self.assertEqual(os.listdir(self.run_dir), ["watchdog.json"])

    def test_the_journal_appends_lines(self):
        self.files.append_journal({"a": 1})
        self.files.append_journal({"b": 2})
        with open(self.files.journal) as f:
            self.assertEqual([json.loads(line) for line in f],
                             [{"a": 1}, {"b": 2}])

    def test_evidence(self):
        self.assertEqual(
            self.files.evidence_name(WALL, "cmscontestwebserver1"),
            "20270115T080000Z-cmscontestwebserver1.json")
        self.assertEqual(self.files.evidence_name(WALL, "cmsLogService 0"),
                         "20270115T080000Z-cmsLogService_0.json")
        self.files.write_evidence("x.json", {"rule": "R1"})
        with open(os.path.join(self.root, "log", "watchdog",
                               "x.json")) as f:
            self.assertEqual(json.load(f), {"rule": "R1"})


class TestAlerter(unittest.IsolatedAsyncioTestCase):

    async def test_stdout_gets_everything_and_logservice_warnings(self):
        out, log = io.StringIO(), FakeLog()
        alerter = Alerter(log, out)
        alerter.emit("INFO", "Watchdog: summary", WALL)
        alerter.emit("WARNING", "Watchdog: held", WALL)
        await alerter.flush()
        self.assertEqual(out.getvalue().splitlines(), [
            "2027-01-15 08:00:00 UTC INFO Watchdog: summary",
            "2027-01-15 08:00:00 UTC WARNING Watchdog: held"])
        self.assertEqual(log.calls, [
            ("Log", log_record("WARNING", "Watchdog: held", WALL))])
        await alerter.flush()
        self.assertEqual(len(log.calls), 1)

    async def test_the_hourly_summary_stays_on_stdout(self):
        out, log = io.StringIO(), FakeLog()
        alerter = Alerter(log, out)
        summary = policy.summary_text(policy.NearMiss())
        alerter.emit("INFO", summary, WALL)
        await alerter.flush()
        self.assertIn(summary, out.getvalue())
        self.assertEqual(log.calls, [])

    async def test_a_logservice_that_does_not_answer_costs_one_call(self):
        log = FakeLog(ok=False)
        alerter = Alerter(log, io.StringIO())
        for index in range(3):
            alerter.emit("ERROR", "Watchdog: %d" % index, WALL)
        await alerter.flush()
        self.assertEqual(len(log.calls), 1)

    async def test_open_alerts_are_sent_again(self):
        log = FakeLog()
        alerter = Alerter(log, io.StringIO())
        alerter.resend([
            policy.Decision(policy.ALERT, "Watchdog: red", "ERROR"),
            policy.Decision(policy.ALERT, "Watchdog: hourly", "INFO")],
            WALL)
        await alerter.flush()
        self.assertEqual([data["msg"] for _, data in log.calls],
                         ["Watchdog: red"])

    async def test_the_group_alerts_are_sent_again(self):
        # The open lines of the Workers and of R5 are one per group.
        log = FakeLog()
        alerter = Alerter(log, io.StringIO())
        alerter.resend([
            policy.Decision(policy.ALERT, "Watchdog: 2 Workers held",
                            "WARNING", key=policy.WORKERS_KEY),
            policy.Decision(policy.ALERT, "Watchdog: 3 starts held",
                            "WARNING", key=policy.FATAL_KEY),
            policy.Decision(policy.ALERT, "Watchdog: 2 left to you",
                            "ERROR", key=policy.FATAL_LEFT_KEY)], WALL)
        await alerter.flush()
        self.assertEqual(log.calls, [
            ("Log", log_record("WARNING", "Watchdog: 2 Workers held",
                               WALL)),
            ("Log", log_record("WARNING", "Watchdog: 3 starts held", WALL)),
            ("Log", log_record("ERROR", "Watchdog: 2 left to you", WALL))])

    async def test_without_logservice(self):
        out = io.StringIO()
        alerter = Alerter(None, out)
        alerter.emit("WARNING", "Watchdog: held", WALL)
        alerter.resend([policy.Decision(policy.ALERT, "x")], WALL)
        await alerter.flush()
        self.assertIn("Watchdog: held", out.getvalue())


class TestLogRecord(ServiceLoggingIsolationMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        # LogService writes its log file in log_dir.
        log_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, log_dir)
        patcher = patch.object(config.global_, "log_dir", log_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_logservice_rebuilds_it_for_the_overview(self):
        service = LogService(0)
        service.Log(**log_record(
            "WARNING", "Watchdog: restarted cmsContestWebServer 1", WALL))
        self.assertEqual(service.last_messages()[-1], {
            "message": "Watchdog: restarted cmsContestWebServer 1",
            "coord": "Watchdog,0", "operation": "", "severity": "WARNING",
            "timestamp": WALL, "exc_text": None})


class TestStatusLine(unittest.TestCase):

    def setUp(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        self.path = os.path.join(directory, "watchdog.json")
        self.files = Files(directory, os.path.join(directory, "log"))

    def write(self, text: str) -> None:
        with open(self.path, "w") as f:
            f.write(text)

    def test_off(self):
        self.write(json.dumps({"time": WALL, "mode": "observe",
                               "open_alerts": []}))
        self.assertEqual(status_line(self.path, {"CMS_WATCHDOG": "Off"},
                                     WALL),
                         "watchdog: off (not configured)")

    def test_no_status_file(self):
        self.assertEqual(status_line(self.path, {}, WALL), "no status file")

    def test_a_status(self):
        self.write(json.dumps({"time": WALL - 4, "mode": "observe",
                               "open_alerts": [], "supervisord": "ok"}))
        self.assertEqual(status_line(self.path, {}, WALL),
                         "watchdog: observe, last cycle 4 s ago, "
                         "0 open alerts")

    def test_stale_and_unreachable(self):
        self.write(json.dumps({"time": WALL - 300, "mode": "enforce",
                               "open_alerts": ["a", "b"],
                               "supervisord": "unreachable"}))
        self.assertEqual(
            status_line(self.path, {}, WALL),
            "watchdog: enforce, last cycle 300 s ago, 2 open alerts, "
            "supervisord unreachable (stale: is cmswatchdog running? "
            "See ./status.sh)")

    def test_a_status_that_does_not_parse(self):
        self.write("{")
        self.assertTrue(status_line(self.path, {}, WALL).startswith(
            "watchdog: the status file"))

    def test_programs_that_do_not_parse(self):
        for programs in ([1], [{"name": "x", "restarts_last_hour": "a"}]):
            with self.subTest(programs=programs):
                self.write(json.dumps({"time": WALL, "mode": "observe",
                                       "open_alerts": [],
                                       "programs": programs}))
                self.assertTrue(status_line(self.path, {}, WALL).startswith(
                    "watchdog: the status file"))

    def test_a_ledger_that_did_not_parse(self):
        # Its budgets are exhausted with no booking: the counts say 0.
        with open(self.files.ledger, "w") as f:
            f.write("{broken")
        ledger, _problem = self.files.load_ledger(WALL - 1800)
        self.files.save_status({
            "time": WALL - 4, "mode": "observe", "open_alerts": [],
            "programs": [], "exhausted_until": ledger.exhausted_until})
        self.assertEqual(
            status_line(self.files.status, {}, WALL),
            "watchdog: observe, last cycle 4 s ago, 0 open alerts, every "
            "budget counts as used until 08:30 UTC (its ledger did not "
            "parse)")

    def test_an_exhausted_state_that_ended_is_not_shown(self):
        for exhausted_until in (None, WALL - 1, WALL):
            with self.subTest(exhausted_until=exhausted_until):
                self.write(json.dumps({
                    "time": WALL - 4, "mode": "observe", "open_alerts": [],
                    "exhausted_until": exhausted_until}))
                self.assertEqual(status_line(self.path, {}, WALL),
                                 "watchdog: observe, last cycle 4 s ago, "
                                 "0 open alerts")

    def test_the_status_of_a_cycle(self):
        # What program_status gives reaches the file, and the line
        # counts the budget each program used in the last hour.
        cws = policy.ProgramFacts("cmscontestwebserver1", "ContestWebServer",
                                  1, "RUNNING", 1301, WALL - 600)
        worker = policy.ProgramFacts("cmsworker3", "Worker", 3, "FATAL",
                                     None, WALL - 900)
        state = policy.PolicyState(
            mode=policy.ENFORCE,
            restart_classes=frozenset({"web", "core", "infra"}))
        state.ledger.book(policy.Booking(
            WALL - 400, "cmscontestwebserver1", "cmsContestWebServer 1",
            policy.SERVICES_POOL, policy.RESTART, "R1", False, 1234), False)
        state.ledger.book(policy.Booking(
            WALL - 300, "cmsworker3", "cmsWorker 3", policy.WORKERS_POOL,
            policy.RESTART, "R4", True, 4242), True)
        state.ledger.book(policy.Booking(
            WALL - 120, "cmsworker3", "cmsWorker 3", policy.WORKERS_POOL,
            policy.START, "R5", True), True)
        state.ledger.incidents["cmscontestwebserver1"] = {
            "rule": "R1", "opened": WALL - 400, "passes": 1}
        state.programs["cmscontestwebserver1"] = policy.ProgramState(
            pid=1301)
        state.programs["cmscontestwebserver1"].window.extend(
            [True, False, False])
        state.programs["cmsworker3"] = policy.ProgramState(pid=4242)
        state.programs["cmsworker3"].window.extend([True] * 6)
        observation = policy.Observation(
            lag_s=0.0, since_previous_s=policy.CYCLE_S, supervisor_ok=True,
            programs=(cws, worker), gate=policy.Gate(True))
        self.files.save_status({
            "time": WALL - 4, "mode": state.mode, "open_alerts": [],
            "supervisord": "ok",
            "programs": policy.program_status(state, observation, WALL),
            "exhausted_until": state.ledger.exhausted_until})
        with open(self.files.status, encoding="utf-8") as f:
            status = json.load(f)
        self.assertIsNone(status["exhausted_until"])
        entries = {entry["name"]: entry for entry in status["programs"]}
        self.assertEqual(
            {name: (entry["misses"], entry["known"], entry["incident"],
                    entry["restarts_last_hour"],
                    entry["would_restarts_last_hour"],
                    entry["shadow_restarts_last_hour"],
                    entry["starts_last_hour"])
             for name, entry in entries.items()},
            {"cmscontestwebserver1": (
                1, 3, {"rule": "R1", "opened": WALL - 400, "passes": 1},
                1, 0, 0, 0),
             "cmsworker3": (None, None, None, 0, 0, 1, 1)})
        self.assertEqual(
            status_line(self.files.status, {}, WALL),
            "watchdog: enforce, last cycle 4 s ago, 0 open alerts, in the "
            "last hour: 1 restart (cmsContestWebServer 1), 1 would-restart "
            "(cmsWorker 3), 1 FATAL start or would-start (cmsWorker 3)")

    def test_the_counts_add_up(self):
        self.write(json.dumps({
            "time": WALL - 4, "mode": "observe", "open_alerts": [],
            "programs": [
                {"name": "cmscontestwebserver1",
                 "label": "cmsContestWebServer 1",
                 "would_restarts_last_hour": 2},
                {"name": "cmscontestwebserver2",
                 "label": "cmsContestWebServer 2",
                 "would_restarts_last_hour": 1},
                {"name": "loader", "label": None,
                 "would_restarts_last_hour": 0}]}))
        self.assertEqual(
            status_line(self.path, {}, WALL),
            "watchdog: observe, last cycle 4 s ago, 0 open alerts, in the "
            "last hour: 3 would-restarts (cmsContestWebServer 1, "
            "cmsContestWebServer 2)")


if __name__ == "__main__":
    unittest.main()
