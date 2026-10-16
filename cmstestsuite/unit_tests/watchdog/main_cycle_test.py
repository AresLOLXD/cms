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

"""Tests for cmsWatchdog's cycle, actions, schedule and command line."""

import asyncio
import contextlib
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cms import Address, ServiceCoord
from cms.watchdog import main as watchdog
from cms.watchdog.control import DryRunActuator, Outcome, ProcessInfo, \
    SupervisorSource, Target
from cms.watchdog.main import Alerter, Files, Watchdog, parse_classes, \
    restart_line, start_line, status_line, take_snapshot
from cms.watchdog.policy import ENFORCE, HOLDS, OBSERVE, PAUSE_S, RESTART, \
    SELF_GUARD_S, START, SUMMARY_PERIOD_S, Decision, Ledger, PolicyState, \
    ProgramFacts
from cms.watchdog.probes import GateResult, ProbeResult, RankingTarget, \
    RpcProbe


WALL = 1_800_000_000.0
MONO = 10_000.0
ALL = ("web", "core", "infra", "workers")
KINDS = ("LogService", "EvaluationService", "Worker", "ContestWebServer")


def info(name: str, kind: str, shard: int, pid: int) -> ProcessInfo:
    return ProcessInfo(name, kind, shard, "RUNNING", pid, WALL - 86_400)


PROGRAMS = (info("cmslogservice", "LogService", 0, 101),
            info("cmsevaluationservice", "EvaluationService", 0, 104),
            info("cmsworker0", "Worker", 0, 105),
            info("cmscontestwebserver0", "ContestWebServer", 0, 108),
            info("cmscontestwebserver1", "ContestWebServer", 1, 109))


def http_address(kind: str, shard: int) -> tuple[str, int] | None:
    if kind != "ContestWebServer":
        return None
    return "127.0.0.1", 8000 + shard


def rpc_address(kind: str, shard: int) -> tuple[str, int]:
    return "127.0.0.1", 20_000 + 10 * KINDS.index(kind) + shard


CWS1 = {http_address("ContestWebServer", 1),
        rpc_address("ContestWebServer", 1)}
PASS = ProbeResult(True, 0.01)
FAIL = ProbeResult(False, None, "timed out")


class FakeSource:
    can_act = True

    def __init__(self):
        self.infos = list(PROGRAMS)
        self.down = False
        # Called at each read, before it answers (None: nothing).
        self.on_read = None

    async def programs(self) -> list[ProcessInfo]:
        if self.on_read is not None:
            self.on_read()
        if self.down:
            raise TimeoutError("timed out")
        return list(self.infos)

    def set(self, name: str, **changes) -> None:
        self.infos = [replace(item, **changes) if item.name == name
                      else item for item in self.infos]


class FakeProber:
    """Every probe passes, unless its address is in failing."""

    def __init__(self):
        self.failing: set = set()
        self.gate_result = GateResult(True)
        self.calls: list = []

    async def http(self, address):
        return FAIL if address in self.failing else PASS

    async def rpc(self, address, method):
        self.calls.append((address, method))
        if address in self.failing:
            return FAIL
        if method == "workers_status":
            return ProbeResult(True, 0.01, data={"0": {
                "connected": True, "disabled_by_timeout": False,
                "disabled_at": None}})
        return ProbeResult(True, 0.01, data={"uptime_s": 9_999.0,
                                             "busy": []})

    async def ranking(self, target):
        return PASS

    async def gate(self):
        return self.gate_result

    def proc_facts(self, pid):
        return {"state": "S", "wchan": "do_epoll_wait",
                "threads": {pid: "S"}, "cpu_s": 1.0}

    def thread_cpu_s(self, pid, tid):
        return 1.0

    def close(self):
        pass


class FakeLog:
    """LogService: each call passes while ok is true.

    While it is false, a call raises error if it is set, else fails.

    """

    def __init__(self):
        self.calls: list = []
        self.ok = True
        self.error: Exception | None = None

    async def call(self, method, data=None):
        self.calls.append((method, data))
        if not self.ok and self.error is not None:
            raise self.error
        return PASS if self.ok else FAIL


class FakeActuator:
    """Books, writes the evidence, and returns its outcome.

    It then waits for release, if it is set, and raises error, if it
    is set. A start returns start_result ("would" for the dry run).

    """

    def __init__(self, outcome: Outcome, start_result: str = "started"):
        self.outcome = outcome
        self.start_result = start_result
        self.calls: list = []
        self.release: asyncio.Event | None = None
        self.error: Exception | None = None

    async def finish(self) -> None:
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error

    async def restart(self, target, book, write_evidence):
        book()
        write_evidence()
        self.calls.append(target)
        await self.finish()
        return self.outcome

    async def start(self, names, book):
        book()
        self.calls.append(tuple(names))
        await self.finish()
        return Outcome(self.start_result)


class Rig:
    """A Watchdog over fakes, in temporary directories."""

    def __init__(self, test: unittest.TestCase, mode: str = ENFORCE,
                 restart: tuple = ALL):
        root = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, root)
        self.test = test
        self.files = Files(os.path.join(root, "run"),
                           os.path.join(root, "log"))
        self.out = io.StringIO()
        self.log = FakeLog()
        self.source = FakeSource()
        self.prober = FakeProber()
        self.actuator = FakeActuator(Outcome(
            "restarted", new_pid=999,
            usr1="USR1 sent: its stacks are in ./logs.sh"))
        self.dry_run = FakeActuator(Outcome(
            "would", usr1="no USR1: the process does not handle it"),
            start_result="would")
        self.rearms: list = []
        self.history = os.path.join(root, "history.jsonl")
        self.watchdog = Watchdog(
            state=PolicyState(mode=mode, restart_classes=frozenset(restart)),
            source=self.source, actuator=self.actuator,
            dry_run=self.dry_run, prober=self.prober, files=self.files,
            alerter=Alerter(self.log, self.out),
            rearm=lambda: self.rearms.append(True),
            status_history=self.history, http_address=http_address,
            rpc_address=rpc_address)
        self.mono, self.wall, self.count = MONO, WALL, 0

    async def cycle(self, lag_s: float = 0.0, settle: bool = True) -> None:
        if self.count:
            self.mono += 15.0
            self.wall += 15.0
        await self.watchdog.cycle(self.mono, self.wall, lag_s,
                                  15.0 if self.count else None)
        self.count += 1
        if settle:
            await self.watchdog.settle()
        # A hold episode's urgent holds end with its headline (Task 9):
        # the cycle never clears one without the other.
        state = self.watchdog.state
        self.test.assertLessEqual(set(state.held_announced),
                                  set(state.held_headline))

    async def trip_cws1(self, settle: bool = True) -> None:
        """Run 9 passing cycles, then 5 in which CWS 1 fails."""
        for _ in range(9):
            await self.cycle()
        self.prober.failing = set(CWS1)
        for _ in range(4):
            await self.cycle()
        await self.cycle(settle=settle)

    async def outage(self, names: list[str], settle: bool = True) -> None:
        """Leave names FATAL after a database outage, for R5.

        It runs 9 green cycles (the watchdog's grace), a red one in
        which they go FATAL, then 2 green ones: R5 starts them in the
        last.

        """
        for _ in range(9):
            await self.cycle()
        self.prober.gate_result = GateResult(False, "refused",
                                             "Connection refused")
        for name in names:
            self.source.set(name, state="FATAL", pid=None,
                            start_wall=self.wall + 15.0)
        await self.cycle()
        self.prober.gate_result = GateResult(True)
        await self.cycle()
        await self.cycle(settle=settle)

    def lines(self) -> list[str]:
        return self.out.getvalue().splitlines()

    def status(self) -> dict:
        with open(self.files.status) as f:
            return json.load(f)

    def journal(self) -> list[dict]:
        with open(self.files.journal) as f:
            return [json.loads(line) for line in f]

    def programs(self) -> dict[str, dict]:
        return {entry["name"]: entry for entry in self.status()["programs"]}


def sleeper(test: unittest.TestCase) -> int:
    """Start a process that does not handle USR1; return its pid."""
    process = subprocess.Popen(["sleep", "60"])
    test.addCleanup(process.wait)
    test.addCleanup(process.kill)
    return process.pid


class TestCycle(unittest.IsolatedAsyncioTestCase):

    async def test_one_full_cycle(self):
        rig = Rig(self)
        await rig.cycle()
        status = rig.status()
        self.assertEqual(
            (status["mode"], status["supervisord"], status["gate"],
             status["cycle"]), ("enforce", "ok", "green", 0))
        self.assertEqual([entry["name"] for entry in status["programs"]],
                         [item.name for item in PROGRAMS])
        self.assertTrue(os.path.exists(rig.files.ledger))
        self.assertEqual(len(rig.rearms), 1)
        with open(rig.history) as f:
            self.assertEqual(len(f.readlines()), 1)
        self.assertIn((rpc_address("EvaluationService", 0),
                       "workers_status"), rig.prober.calls)
        self.assertEqual(rig.lines(), [])

    async def test_supervisord_unreachable(self):
        rig = Rig(self)
        await rig.cycle()
        rig.source.down = True
        await rig.cycle()
        status = rig.status()
        self.assertEqual(status["supervisord"], "unreachable")
        self.assertEqual(len(status["programs"]), len(PROGRAMS))

    async def test_the_rate_limit_and_the_resend_to_a_new_logservice(self):
        rig = Rig(self)
        rig.prober.gate_result = GateResult(False, "refused",
                                            "Connection refused")
        await rig.cycle()
        await rig.cycle()
        red = [line for line in rig.lines() if "gate is red" in line]
        self.assertEqual(len(red), 1)
        self.assertEqual(len(rig.log.calls), 1)
        rig.source.set("cmslogservice", pid=201)
        await rig.cycle()
        self.assertEqual(
            len([line for line in rig.lines() if "gate is red" in line]), 1)
        messages = [data["msg"] for _, data in rig.log.calls]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0], messages[1])

    async def test_the_open_alerts_wait_for_a_logservice_that_answers(self):
        # Just after RUNNING, a new LogService may not listen yet: its
        # open alerts are sent again until one flush reaches it. A call
        # that raises (an answer RpcProbe does not expect) fails alike.
        for error in (None, RecursionError("maximum recursion depth")):
            with self.subTest(error=error):
                rig = Rig(self)
                rig.prober.gate_result = GateResult(False, "refused",
                                                    "Connection refused")
                await rig.cycle()
                rig.source.set("cmslogservice", pid=201)
                rig.log.ok, rig.log.error = False, error
                await rig.cycle()
                rig.log.ok = True
                await rig.cycle()
                await rig.cycle()
                messages = [data["msg"] for _, data in rig.log.calls]
                self.assertEqual(len(messages), 3)
                self.assertEqual(len(set(messages)), 1)
                self.assertIn("gate is red", messages[0])
                self.assertEqual(len(rig.rearms), 4)

    async def test_the_hourly_summary_stays_on_stdout(self):
        rig = Rig(self)
        await rig.cycle()
        rig.watchdog.state.summary_mono = rig.mono + 15.0 - SUMMARY_PERIOD_S
        await rig.cycle()
        [summary] = [line for line in rig.lines()
                     if "hourly near-miss summary" in line]
        self.assertIn(" INFO Watchdog: hourly near-miss summary: ",
                      summary)
        self.assertEqual(rig.log.calls, [])

    async def test_the_self_guard_is_rearmed_every_cycle(self):
        rig = Rig(self)
        for _ in range(3):
            await rig.cycle()
        self.assertEqual(len(rig.rearms), 3)

    async def test_the_status_carries_the_budgets_and_the_ledger_state(
            self):
        rig = Rig(self, mode=OBSERVE)
        rig.source.set("cmsworker0", state="STOPPED", pid=None)
        await rig.trip_cws1()
        status = rig.status()
        self.assertIsNone(status["exhausted_until"])
        programs = rig.programs()
        cws1 = programs["cmscontestwebserver1"]
        self.assertEqual(
            (cws1["misses"], cws1["known"], cws1["restarts_last_hour"],
             cws1["would_restarts_last_hour"], cws1["starts_last_hour"],
             cws1["incident"]["rule"]), (5, 6, 0, 1, 0, "R1"))
        self.assertEqual(
            (programs["cmsworker0"]["misses"],
             programs["cmsworker0"]["known"]), (None, None))
        # A ledger that did not parse exhausts every budget: the status
        # says until when, for --status.
        rig = Rig(self)
        rig.watchdog.state.ledger = Ledger.exhausted(WALL)
        await rig.cycle()
        self.assertEqual(rig.status()["exhausted_until"], WALL + 3600)
        self.assertIn("every budget counts as used until",
                      status_line(rig.files.status, {}, WALL))

    async def test_a_restart(self):
        rig = Rig(self)
        await rig.trip_cws1()
        self.assertEqual(rig.actuator.calls, [
            Target("cmscontestwebserver1", "ContestWebServer", 109)])
        with open(rig.files.ledger) as f:
            self.assertEqual([b["program"] for b in json.load(f)["real"]],
                             ["cmscontestwebserver1"])
        booking, outcome = rig.journal()
        self.assertEqual((booking["action"], booking["evidence_planned"]),
                         ("restart", True))
        self.assertEqual(outcome["outcome"], "restarted")
        name = os.path.basename(booking["evidence"])
        with open(os.path.join(rig.files.evidence_dir, name)) as f:
            evidence = json.load(f)
        self.assertEqual(evidence["decision"]["rule"], "R1")
        self.assertEqual(evidence["decision"]["holds_checked"], list(HOLDS))
        self.assertEqual(len(evidence["observations"]), 6)
        self.assertEqual(evidence["supervisor"]["pid"], 109)
        # Its probes failed in the trip's cycles: the evidence keeps the
        # last health() answer received, from the 9th cycle.
        self.assertEqual(evidence["health"],
                         {"uptime_s": 9_999.0, "busy": []})
        self.assertEqual(evidence["health_time"],
                         watchdog.utc(WALL + 8 * 15))
        self.assertEqual(evidence["proc"]["wchan"], "do_epoll_wait")
        self.assertIn(
            "WARNING Watchdog: restarted cmsContestWebServer 1 (pid "
            "109→999) after 5 of its last 6 favicon probes failed (R1); "
            "DB OK; evidence log/watchdog/%s; USR1 sent: its stacks are in "
            "./logs.sh." % name,
            rig.lines()[-1])

    async def test_a_would_restart_in_observe_mode(self):
        rig = Rig(self, mode=OBSERVE)
        await rig.trip_cws1()
        self.assertEqual(rig.actuator.calls, [])
        self.assertEqual(len(rig.dry_run.calls), 1)
        line = rig.lines()[-1]
        self.assertIn("Watchdog: WOULD restart cmsContestWebServer 1 (pid "
                      "109) after 5 of its last 6 favicon probes failed "
                      "(R1); DB OK; evidence log/watchdog/", line)
        self.assertTrue(line.endswith(
            "no USR1: the process does not handle it; observe mode: "
            "nothing was restarted."))

    async def test_a_would_restart_of_a_class_not_enabled(self):
        rig = Rig(self, restart=("core", "infra"))
        await rig.trip_cws1()
        self.assertIn("class web is not in CMS_WATCHDOG_RESTART (H9), so "
                      "nothing was done; shadow count 1 of 2 this hour.",
                      rig.lines()[-1])

    async def test_a_restart_that_did_not_happen_did_not_help(self):
        # Only a restart that may have signalled the program gets "answers
        # again"; every outcome is journaled.
        cases = (
            (Outcome("restarted", new_pid=999), True),
            (Outcome("refused", "it is no longer RUNNING as pid 109"),
             False),
            (Outcome("gone", "pid 109 had already exited"), False),
            (Outcome("failed", "pidfd_open failed: [Errno 24] Too many "
                     "open files"), False),
            (Outcome("failed", "TERM failed: [Errno 1] Operation not "
                     "permitted", usr1="no USR1: x"), False),
            (Outcome("failed", "TERM sent; then the group could not be "
                     "checked: x", usr1="no USR1: x"), True),
            (Outcome("failed", "KILL failed: x", usr1="no USR1: x"), True),
        )
        for outcome, signalled in cases:
            with self.subTest(detail=outcome.detail or outcome.result):
                rig = Rig(self)
                rig.actuator.outcome = outcome
                await rig.trip_cws1()
                self.assertEqual(rig.journal()[-1]["outcome"],
                                 outcome.result)
                restarted = rig.watchdog.state.programs[
                    "cmscontestwebserver1"].restarted_mono
                self.assertEqual(restarted is not None, signalled)
                # CWS 1 runs again as a new process that answers.
                rig.prober.failing = set()
                rig.source.set("cmscontestwebserver1", pid=999)
                await rig.cycle()
                self.assertEqual(
                    len([line for line in rig.lines()
                         if "answers again" in line]), int(signalled))

    async def test_an_actuator_that_raises_is_a_failed_action(self):
        rig = Rig(self)
        rig.actuator.error = RuntimeError("bad file descriptor")
        await rig.trip_cws1()
        booking, outcome = rig.journal()
        self.assertEqual((booking["action"], outcome["outcome"],
                          outcome["detail"]),
                         ("restart", "error", "bad file descriptor"))
        self.assertIn(
            "ERROR Watchdog: the restart of cmsContestWebServer 1 (pid 109) "
            "after 5 of its last 6 favicon probes failed (R1) failed "
            "unexpectedly: bad file descriptor; TERM and KILL may have "
            "been sent.", rig.lines()[-1])
        # The booking stays, and the cycle goes on.
        with open(rig.files.ledger) as f:
            self.assertEqual([b["program"] for b in json.load(f)["real"]],
                             ["cmscontestwebserver1"])
        await rig.cycle()
        self.assertEqual(len(rig.rearms), 15)

    async def test_an_error_outside_the_actuator_is_alerted(self):
        # A bug of the watchdog's own around an action: the action's
        # task alerts it, and the cycles go on.
        rig = Rig(self)

        def broken(wall, program):
            raise RuntimeError("bad name")

        rig.files.evidence_name = broken
        await rig.trip_cws1()
        self.assertEqual(rig.actuator.calls, [])
        self.assertIn("ERROR Watchdog: the restart of cmscontestwebserver1 "
                      "failed unexpectedly: bad name", rig.lines()[-1])
        await rig.cycle()
        self.assertEqual(len(rig.rearms), 15)

    async def test_a_long_action_runs_beside_the_cycles(self):
        # A restart may take 54 s: the cycles go on meanwhile, each
        # re-arming the self-guard, and H8 holds the next trip.
        rig = Rig(self)
        rig.actuator.release = asyncio.Event()
        await rig.trip_cws1(settle=False)
        async with asyncio.timeout(10):
            for _ in range(4):
                await rig.cycle(settle=False)
        self.assertEqual(len(rig.rearms), 18)
        self.assertTrue(rig.status()["action_in_flight"])
        self.assertEqual(len(rig.actuator.calls), 1)
        self.assertTrue(any("Also: H8" in line for line in rig.lines()))
        rig.actuator.release.set()
        await rig.watchdog.settle()
        self.assertIn("restarted cmsContestWebServer 1 (pid 109→999)",
                      rig.lines()[-1])
        await rig.cycle()
        self.assertFalse(rig.status()["action_in_flight"])

    async def test_an_action_interrupted_at_stop_is_journaled(self):
        # supervisord stops the watchdog (SIGTERM) during an action: the
        # journal and the alerts say so, and the cancellation goes on.
        rig = Rig(self)
        rig.actuator.release = asyncio.Event()
        await rig.trip_cws1(settle=False)
        while not rig.actuator.calls:
            await asyncio.sleep(0)
        action = rig.watchdog._action
        action.cancel()
        await asyncio.wait({action})
        self.assertTrue(action.cancelled())
        booking, outcome = rig.journal()
        self.assertEqual(
            (booking["action"], outcome["outcome"], outcome["detail"]),
            ("restart", "interrupted", "the watchdog stopped during it"))
        self.assertIn(
            "ERROR Watchdog: the restart of cmsContestWebServer 1 (pid 109) "
            "after 5 of its last 6 favicon probes failed (R1) was "
            "interrupted: the watchdog stopped during it; TERM and KILL may "
            "have been sent.", rig.lines()[-1])
        # A start batch, stopped as serve stops it.
        rig = Rig(self)
        rig.actuator.release = asyncio.Event()
        await rig.outage(["cmscontestwebserver1"], settle=False)
        while not rig.actuator.calls:
            await asyncio.sleep(0)
        await rig.watchdog.stop()
        self.assertTrue(rig.watchdog._action.cancelled())
        self.assertEqual(rig.journal()[-1]["outcome"], "interrupted")
        self.assertIn("ERROR Watchdog: the start of cmscontestwebserver1 was "
                      "interrupted: the watchdog stopped during it; some of "
                      "them may have been started.", rig.lines()[-1])
        # Nothing in flight: nothing to stop.
        await rig.watchdog.stop()

    async def test_a_real_and_a_shadow_start_batch_in_one_cycle(self):
        rig = Rig(self, restart=("web", "core", "infra"))
        await rig.outage(["cmscontestwebserver1", "cmsworker0"])
        self.assertEqual(rig.actuator.calls, [("cmscontestwebserver1",)])
        self.assertEqual(rig.dry_run.calls, [("cmsworker0",)])
        self.assertEqual(
            [(record.get("programs"), record.get("shadow"),
              record.get("outcome")) for record in rig.journal()],
            [(["cmscontestwebserver1"], False, None),
             (["cmscontestwebserver1"], None, "started"),
             (["cmsworker0"], True, None),
             (["cmsworker0"], None, "would")])
        lines = rig.lines()
        self.assertIn("WARNING Watchdog: started cmscontestwebserver1, left "
                      "FATAL by a database outage (R5). Check ./status.sh.",
                      lines[-2])
        self.assertIn("WARNING Watchdog: WOULD start cmsworker0, left FATAL "
                      "by a database outage (R5); their class is not in "
                      "CMS_WATCHDOG_RESTART (H9), so nothing was done.",
                      lines[-1])

    async def test_a_start_batch_is_in_flight_until_its_last_start_returns(
            self):
        rig = Rig(self)
        rig.actuator.release = asyncio.Event()
        await rig.outage(["cmscontestwebserver1"], settle=False)
        # The start returns while the next cycle probes: that cycle's
        # source read, taken before, still shows the program FATAL.
        rig.source.on_read = rig.actuator.release.set
        await rig.cycle(settle=False)
        await rig.watchdog.settle()
        self.assertEqual(rig.actuator.calls, [("cmscontestwebserver1",)])
        self.assertTrue(any("is held by H8" in line
                            for line in rig.lines()))

    async def test_a_start_batch_that_raises_is_a_failed_action(self):
        rig = Rig(self)
        rig.actuator.error = RuntimeError("supervisord went away")
        await rig.outage(["cmscontestwebserver1"])
        booking, outcome = rig.journal()
        self.assertEqual(
            (booking["programs"], outcome["outcome"], outcome["detail"]),
            (["cmscontestwebserver1"], "error", "supervisord went away"))
        self.assertIn("ERROR Watchdog: the start of cmscontestwebserver1 "
                      "failed unexpectedly: supervisord went away; some of "
                      "them may have been started.", rig.lines()[-1])

    async def test_a_probe_that_raises_is_a_miss(self):
        rig = Rig(self)
        rpc = rig.prober.rpc

        async def raising_rpc(address, method):
            if address == rpc_address("ContestWebServer", 1) \
                    or method == "workers_status":
                # A deeply nested answer: not one of PROBE_ERRORS.
                raise RecursionError("maximum recursion depth exceeded")
            return await rpc(address, method)

        async def raising(*args):
            raise ValueError("embedded null byte")

        rig.prober.rpc = raising_rpc
        rig.prober.http = raising
        rig.prober.ranking = raising
        rig.prober.gate = raising
        rig.watchdog.rankings = [RankingTarget("127.0.0.1", 8890, False)]
        await rig.cycle()
        status = rig.status()
        self.assertEqual(
            (status["gate"], status["gate_reason"], status["gate_detail"]),
            ("red", "other", "embedded null byte"))
        programs = rig.programs()
        self.assertEqual(
            (programs["cmscontestwebserver1"]["rpc_ok"],
             programs["cmscontestwebserver0"]["http_ok"],
             programs["cmscontestwebserver0"]["rpc_ok"]),
            (False, False, True))
        self.assertEqual(status["rankings"]["http://127.0.0.1:8890/"],
                         {"ok": False, "latency_s": None,
                          "error": "embedded null byte"})
        self.assertEqual(len(rig.rearms), 1)

    async def test_an_unwritable_status_file_alerts_and_the_cycle_goes_on(
            self):
        rig = Rig(self)
        # A directory where the file goes: the rename fails, even as root.
        os.makedirs(rig.files.status)
        await rig.cycle()
        self.assertTrue(any("status file" in line
                            and "could not be written" in line
                            for line in rig.lines()))
        self.assertEqual(len(rig.rearms), 1)
        self.assertTrue(os.path.exists(rig.files.ledger))

    async def test_an_unwritable_history_alerts_once(self):
        rig = Rig(self)
        os.makedirs(rig.history)
        for _ in range(3):
            await rig.cycle()
        self.assertEqual(len([line for line in rig.lines()
                              if "status history" in line
                              and "could not be written" in line]), 1)
        self.assertEqual((len(rig.rearms), rig.status()["cycle"]), (3, 2))

    async def test_a_full_log_volume_alerts_and_the_action_goes_on(self):
        rig = Rig(self, mode=OBSERVE)
        rig.watchdog.dry_run = DryRunActuator(rig.source, usr1_wait_s=0)
        rig.source.set("cmscontestwebserver1", pid=sleeper(self))
        # The journal cannot be appended to, the evidence not written.
        os.makedirs(rig.files.journal)

        def full(name, evidence):
            raise OSError(28, "No space left on device")

        rig.files.write_evidence = full
        await rig.trip_cws1()
        lines = rig.lines()
        self.assertEqual(len([line for line in lines
                              if "its journal" in line
                              and "actions go on" in line]), 1)
        self.assertTrue(any(
            "Watchdog: WOULD restart cmsContestWebServer 1" in line
            for line in lines))
        self.assertIn(
            "ERROR Watchdog: the evidence of cmsContestWebServer 1 could not "
            "be written ([Errno 28] No space left on device); the action "
            "went on.", lines[-1])

    async def test_an_unwritable_ledger_stops_the_action(self):
        rig = Rig(self, mode=OBSERVE)
        rig.watchdog.dry_run = DryRunActuator(rig.source, usr1_wait_s=0)
        rig.source.set("cmscontestwebserver1", pid=sleeper(self))
        os.makedirs(rig.files.ledger)
        await rig.trip_cws1()
        # No booking line: the booking stopped at the ledger.
        [outcome] = rig.journal()
        self.assertEqual(outcome["outcome"], "unbooked")
        self.assertIn("ERROR Watchdog: did not restart cmsContestWebServer "
                      "1: its ledger could not be written: ",
                      rig.lines()[-1])
        self.assertEqual(os.listdir(rig.files.evidence_dir),
                         ["actions.jsonl"])
        self.assertEqual(len([line for line in rig.lines()
                              if "a restart stops at its booking" in line]),
                         1)

    async def test_the_evidence_directory_is_created(self):
        rig = Rig(self, mode=OBSERVE)
        self.assertFalse(os.path.exists(rig.files.evidence_dir))
        await rig.trip_cws1()
        self.assertEqual(len([name for name in
                              os.listdir(rig.files.evidence_dir)
                              if name.endswith(".json")]), 1)

    async def test_the_schedule_measures_lag_and_pauses(self):
        rig = Rig(self)
        seen: list = []

        async def cycle(mono, wall, lag_s, since_previous_s):
            seen.append((lag_s, since_previous_s))

        now = [1000.0]
        sleeps: list = []

        async def sleep(delay):
            sleeps.append(delay)
            # The second sleep lasts 45 s more: a pause.
            now[0] += delay + (45.0 if len(sleeps) == 2 else 0.0)

        rig.watchdog.cycle = cycle
        await rig.watchdog.run_forever(clock=lambda: now[0],
                                       wall_clock=lambda: WALL + now[0],
                                       sleep=sleep, cycles=4)
        self.assertEqual(seen, [(0.0, None), (0.0, 15.0), (45.0, 60.0),
                                (0.0, 15.0)])

    async def test_an_unexpected_error_of_a_cycle_is_told_then_raised(self):
        # The watchdog exits and supervisord starts it again: the AWS
        # Overview gets one line about it, not only ./logs.sh a
        # traceback.
        rig = Rig(self)

        def broken(pid):
            raise RuntimeError("a bug")

        rig.prober.proc_facts = broken
        now = [MONO]

        async def sleep(delay):
            now[0] += delay

        with self.assertRaisesRegex(RuntimeError, "a bug"):
            await rig.watchdog.run_forever(clock=lambda: now[0],
                                           wall_clock=lambda: WALL + now[0],
                                           sleep=sleep, cycles=2)
        line = ("Watchdog: its cycle failed unexpectedly (RuntimeError: a "
                "bug); it exits, and supervisord starts it again. Its "
                "traceback is in ./logs.sh.")
        self.assertEqual(rig.lines(), ["%s ERROR %s" % (
            watchdog.utc(WALL + MONO), line)])
        self.assertEqual([data["msg"] for _, data in rig.log.calls],
                         [line])
        self.assertEqual(rig.rearms, [])

    async def test_a_slow_cycle_is_no_pause(self):
        # A cycle whose probes take 16 s skips one slot of the schedule:
        # the next one starts 30 s after it, not 31 s, which the policy
        # would read as a pause that clears every miss window.
        rig = Rig(self)
        now = [MONO]
        seen: list = []
        real_cycle = rig.watchdog.cycle

        async def cycle(mono, wall, lag_s, since_previous_s):
            seen.append((lag_s, since_previous_s))
            await real_cycle(mono, wall, lag_s, since_previous_s)
            if len(seen) == 2:
                now[0] += 16.0

        async def sleep(delay):
            now[0] += delay

        rig.watchdog.cycle = cycle
        await rig.watchdog.run_forever(clock=lambda: now[0],
                                       wall_clock=lambda: WALL + now[0],
                                       sleep=sleep, cycles=4)
        self.assertEqual(seen, [(0.0, None), (0.0, 15.0), (0.0, 30.0),
                                (0.0, 15.0)])
        self.assertLessEqual(seen[2][1], PAUSE_S)
        self.assertFalse(any("paused for" in line for line in rig.lines()))


class TestSnapshot(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        self.files = Files(os.path.join(root, "run"),
                           os.path.join(root, "log"))
        self.source = FakeSource()

    async def snapshot(self, program: str,
                       prober: FakeProber | None = None) -> tuple[int, str]:
        out = io.StringIO()
        code = await take_snapshot(program, self.files, source=self.source,
                                   prober=prober or FakeProber(), out=out,
                                   rpc_address=rpc_address)
        return code, out.getvalue()

    async def test_snapshot_of_an_unknown_program_exits_1(self):
        code, text = await self.snapshot("cmsworker9")
        self.assertEqual(code, 1)
        self.assertIn("no RUNNING CMS program named 'cmsworker9'", text)
        self.source.set("cmscontestwebserver1", state="STOPPED", pid=None)
        code, text = await self.snapshot("cmscontestwebserver1")
        self.assertEqual(code, 1)
        self.assertIn("it is STOPPED", text)
        self.assertFalse(os.path.exists(self.files.evidence_dir))

    async def test_a_snapshot_of_a_running_program(self):
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        self.source.set("cmscontestwebserver0", pid=sleeper.pid)
        # The supervisord name, which the alerts print after
        # "cmsWatchdog --snapshot", and the label.
        for program in ("cmscontestwebserver0", "cmsContestWebServer 0"):
            with self.subTest(program=program):
                code, text = await self.snapshot(program)
                self.assertEqual(code, 0, text)
                [name] = [name for name in os.listdir(
                    self.files.evidence_dir) if name in text]
                self.assertIn("no USR1: the process does not handle it",
                              text)
                with open(os.path.join(self.files.evidence_dir,
                                       name)) as f:
                    self.assertEqual(json.load(f)["supervisor"]["pid"],
                                     sleeper.pid)
        self.assertIsNone(sleeper.poll())
        self.assertFalse(os.path.exists(self.files.journal))

    async def test_a_snapshot_survives_a_probe_that_raises(self):
        self.source.set("cmscontestwebserver0", pid=sleeper(self))
        prober = FakeProber()

        async def raising(*args):
            raise RecursionError("maximum recursion depth exceeded")

        prober.rpc = raising
        prober.gate = raising
        code, text = await self.snapshot("cmscontestwebserver0", prober)
        self.assertEqual(code, 0, text)
        [name] = os.listdir(self.files.evidence_dir)
        with open(os.path.join(self.files.evidence_dir, name)) as f:
            evidence = json.load(f)
        self.assertEqual(
            (evidence["gate"]["green"], evidence["gate"]["reason"],
             evidence["health"], evidence["health_error"]),
            (False, "other", None, "maximum recursion depth exceeded"))

    async def test_a_snapshot_without_a_unix_socket_exits_1(self):
        out = io.StringIO()
        with patch.dict(os.environ,
                        {"SUPERVISOR_SERVER_URL": "http://127.0.0.1:9001"}):
            code = await take_snapshot("cmscontestwebserver0", self.files,
                                       prober=FakeProber(), out=out)
        self.assertEqual(code, 1)
        self.assertIn("cmsWatchdog: cannot reach supervisord: cmsWatchdog "
                      "talks to supervisord on a unix socket only",
                      out.getvalue())

    async def test_a_snapshot_closes_the_source_it_opens(self):
        socket_path = os.path.join(self.files.evidence_dir, "none.sock")
        out = io.StringIO()
        with patch.dict(os.environ,
                        {"SUPERVISOR_SERVER_URL": "unix://" + socket_path}), \
                patch.object(SupervisorSource, "close", autospec=True,
                             side_effect=SupervisorSource.close) as close:
            code = await take_snapshot("cmscontestwebserver0", self.files,
                                       prober=FakeProber(), out=out)
        self.assertEqual(code, 1)
        self.assertIn("supervisord did not answer", out.getvalue())
        close.assert_called_once()

    async def test_a_snapshot_whose_actuator_raises_exits_1(self):
        self.source.set("cmscontestwebserver0", pid=sleeper(self))
        with patch.object(DryRunActuator, "snapshot", new=AsyncMock(
                side_effect=RuntimeError("bad file descriptor"))):
            code, text = await self.snapshot("cmscontestwebserver0")
        self.assertEqual(code, 1)
        self.assertIn("cmsWatchdog: no evidence: the snapshot failed "
                      "unexpectedly: bad file descriptor.", text)


class TestServe(unittest.TestCase):
    """serve() over fakes, a supervisord socket that does not exist, a
    LogService port where nothing listens, and a fake faulthandler (a
    real self-guard would end the test run)."""

    def setUp(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        self.files = Files(os.path.join(root, "run"),
                           os.path.join(root, "log"))
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            self.log_port = unused.getsockname()[1]
        config = SimpleNamespace(
            services={ServiceCoord("LogService", 0):
                      Address("127.0.0.1", self.log_port)},
            contest_web_server=SimpleNamespace(listen_address=(),
                                               listen_port=()),
            admin_web_server=SimpleNamespace(listen_address="127.0.0.1",
                                             listen_port=self.log_port),
            proxy_service=SimpleNamespace(rankings=()),
            database=SimpleNamespace(
                url="postgresql+psycopg2://cmsuser@127.0.0.1:1/none"))
        socket_path = os.path.join(root, "supervisor.sock")
        patchers = (
            patch.object(watchdog, "config", config),
            patch.object(watchdog, "Prober", lambda env: FakeProber()),
            patch.dict(os.environ,
                       {"SUPERVISOR_SERVER_URL": "unix://" + socket_path}))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(watchdog, "faulthandler")
        self.guard = patcher.start()
        self.addCleanup(patcher.stop)
        self.out = ""

    def serve(self, terminate_after: float | None = None) -> int:
        """Run serve() in observe mode; SIGTERM it after that long."""
        async def scenario() -> int:
            def terminate() -> None:
                # Only once serve handles it: by default, SIGTERM would
                # end the test run.
                if signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL:
                    os.kill(os.getpid(), signal.SIGTERM)

            if terminate_after is not None:
                asyncio.get_running_loop().call_later(terminate_after,
                                                      terminate)
            return await asyncio.wait_for(watchdog.serve(
                OBSERVE, frozenset({"web"}), "supervisor", self.files,
                None), 10)

        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                return asyncio.run(scenario())
        finally:
            self.out = out.getvalue()

    def test_sigterm_ends_it_with_0(self):
        with patch.object(SupervisorSource, "close", autospec=True,
                          side_effect=SupervisorSource.close) as source, \
                patch.object(RpcProbe, "close", autospec=True,
                             side_effect=RpcProbe.close) as probe:
            self.assertEqual(self.serve(terminate_after=0.5), 0)
        self.assertIn("INFO Watchdog: started in observe mode", self.out)
        with open(self.files.status) as f:
            self.assertEqual(json.load(f)["supervisord"], "unreachable")
        self.guard.cancel_dump_traceback_later.assert_called_once_with()
        # What it opened is closed: the source, and its own connection
        # to LogService.
        source.assert_called_once()
        self.assertEqual([call.args[0].port for call in probe.call_args_list],
                         [self.log_port])

    def test_the_self_guard_is_armed_before_the_first_cycle(self):
        armed: list = []
        real_cycle = Watchdog.cycle

        async def cycle(watchdog_, *args):
            armed.append(self.guard.dump_traceback_later.call_count)
            await real_cycle(watchdog_, *args)

        with patch.object(Watchdog, "cycle", cycle):
            self.assertEqual(self.serve(terminate_after=0.5), 0)
        # Armed once before the first cycle; each cycle re-arms at its
        # end.
        self.assertEqual(armed[0], 1)
        self.guard.dump_traceback_later.assert_called_with(SELF_GUARD_S,
                                                           exit=True)

    def test_an_error_of_a_cycle_ends_it_with_that_error(self):
        # So that supervisord starts it again.
        async def cycle(watchdog_, *args):
            raise RuntimeError("a bug")

        with patch.object(Watchdog, "cycle", cycle), \
                self.assertRaisesRegex(RuntimeError, "a bug"):
            self.serve()
        self.assertIn("ERROR Watchdog: its cycle failed unexpectedly "
                      "(RuntimeError: a bug)", self.out)
        self.guard.cancel_dump_traceback_later.assert_called_once_with()


class TestCommandLine(unittest.TestCase):

    def setUp(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        patcher = patch.object(watchdog, "config")
        config = patcher.start()
        self.addCleanup(patcher.stop)
        config.global_.run_dir = os.path.join(root, "run")
        config.global_.log_dir = os.path.join(root, "log")

    def run_main(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = watchdog.main(list(argv))
        return code, out.getvalue()

    def test_status(self):
        with patch.dict(os.environ, {"CMS_WATCHDOG": "observe"}):
            self.assertEqual(self.run_main("--status"),
                             (0, "no status file\n"))
        with patch.dict(os.environ, {"CMS_WATCHDOG": "off"}):
            self.assertEqual(self.run_main("--status"),
                             (0, "watchdog: off (not configured)\n"))

    def test_the_proc_source_forces_observe_mode(self):
        with patch.object(watchdog, "serve",
                          new=AsyncMock(return_value=0)) as serve, \
                contextlib.redirect_stderr(io.StringIO()):
            code, _ = self.run_main("--process-source", "proc", "--mode",
                                    "enforce")
        self.assertEqual(code, 0)
        self.assertEqual(serve.await_args.args[:3],
                         (OBSERVE, frozenset({"web", "core", "infra"}),
                          "proc"))

    def test_restart_classes(self):
        self.assertEqual(parse_classes("Web, core,,web"),
                         frozenset({"web", "core"}))
        with self.assertRaises(ValueError):
            parse_classes("web,nope")
        with self.assertRaises(SystemExit), \
                contextlib.redirect_stderr(io.StringIO()):
            self.run_main("--restart", "nope")


class TestLines(unittest.TestCase):

    PROGRAM = ProgramFacts("cmscontestwebserver1", "ContestWebServer", 1,
                           "RUNNING", 109, WALL)
    DECISION = Decision(RESTART, text="5 of its last 6 favicon probes "
                        "failed", program="cmscontestwebserver1", rule="R1")
    WOULD = replace(DECISION, would=True)
    WHAT = "after 5 of its last 6 favicon probes failed (R1)"
    USR1 = "USR1 sent: its stacks are in ./logs.sh"

    def test_restart_lines(self):
        cases = (
            (Outcome("restarted", new_pid=999, killed=True, usr1=self.USR1),
             "WARNING",
             "Watchdog: restarted cmsContestWebServer 1 (pid 109→999, KILL "
             "after 10 s) after 5 of its last 6 favicon probes failed "
             "(R1); DB OK; evidence log/watchdog/e.json; USR1 sent: its "
             "stacks are in ./logs.sh."),
            (Outcome("unconfirmed", "no new pid was RUNNING within 30 s",
                     killed=True), "ERROR",
             "Watchdog: sent TERM and KILL to cmsContestWebServer 1 (pid "
             "109) after 5 of its last 6 favicon probes failed (R1), but no "
             "new pid was RUNNING within 30 s. Check ./status.sh: a program "
             "left FATAL is started again only after a database outage."),
            (Outcome("unbooked", "its ledger could not be written: disk "
                     "full"), "ERROR",
             "Watchdog: did not restart cmsContestWebServer 1: its ledger "
             "could not be written: disk full, and an unbooked restart "
             "would bypass the budget. Check the free space of the run "
             "directory."),
            (Outcome("refused", "it is no longer RUNNING as pid 109"),
             "WARNING",
             "Watchdog: did not restart cmsContestWebServer 1: it is no "
             "longer RUNNING as pid 109; nothing was signalled."),
        )
        for outcome, level, line in cases:
            with self.subTest(result=outcome.result):
                self.assertEqual(
                    restart_line(self.DECISION, self.PROGRAM, outcome,
                                 "e.json", 0), (level, line))

    def test_the_stacks_are_named_only_when_usr1_was_sent(self):
        # A Worker that never connected does not handle USR1: its
        # restart wrote no stacks.
        no_usr1 = "no USR1: the process does not handle it"
        cases = (
            (Outcome("restarted", new_pid=999, usr1=no_usr1), "WARNING",
             "Watchdog: restarted cmsContestWebServer 1 (pid 109→999) %s; "
             "DB OK; evidence log/watchdog/e.json; %s."
             % (self.WHAT, no_usr1)),
            (Outcome("unconfirmed", "no new pid was RUNNING within 30 s",
                     usr1=self.USR1), "ERROR",
             "Watchdog: sent TERM to cmsContestWebServer 1 (pid 109) %s, "
             "but no new pid was RUNNING within 30 s; evidence "
             "log/watchdog/e.json; %s. Check ./status.sh: a program left "
             "FATAL is started again only after a database outage."
             % (self.WHAT, self.USR1)),
        )
        for outcome, level, line in cases:
            with self.subTest(result=outcome.result):
                self.assertEqual(
                    restart_line(self.DECISION, self.PROGRAM, outcome,
                                 "e.json", 0), (level, line))
        self.assertNotIn("stacks", cases[0][2])

    def test_the_lines_of_a_restart_that_went_wrong(self):
        # Each says which signals were sent: none of them says "nothing
        # was signalled" unless that is so.
        tail = "Check ./status.sh, and restart it by hand if needed, " \
            "after cmsWatchdog --snapshot."
        cases = (
            (self.DECISION, Outcome("gone", "pid 109 had already exited"),
             "WARNING",
             "Watchdog: did not restart cmsContestWebServer 1 %s: pid 109 "
             "had already exited, so no TERM or KILL was sent. Check "
             "./status.sh." % self.WHAT),
            (self.DECISION, Outcome("gone", "pid 109 exited before its "
                                    "TERM", usr1=self.USR1), "WARNING",
             "Watchdog: did not restart cmsContestWebServer 1 %s: pid 109 "
             "exited before its TERM, so no TERM or KILL was sent; evidence "
             "log/watchdog/e.json; %s. Check ./status.sh."
             % (self.WHAT, self.USR1)),
            (self.DECISION, Outcome("failed", "pidfd_open failed: [Errno "
                                    "24] Too many open files"), "ERROR",
             "Watchdog: could not restart cmsContestWebServer 1 (pid 109) "
             "%s: pidfd_open failed: [Errno 24] Too many open files; no "
             "TERM or KILL was sent. %s" % (self.WHAT, tail)),
            (self.DECISION, Outcome("failed", "TERM failed: [Errno 1] "
                                    "Operation not permitted",
                                    usr1=self.USR1), "ERROR",
             "Watchdog: could not restart cmsContestWebServer 1 (pid 109) "
             "%s: TERM failed: [Errno 1] Operation not permitted; no TERM "
             "or KILL was sent; evidence log/watchdog/e.json; %s. %s"
             % (self.WHAT, self.USR1, tail)),
            (self.DECISION, Outcome("failed", "TERM sent; then the group "
                                    "could not be checked: x",
                                    usr1=self.USR1), "ERROR",
             "Watchdog: could not restart cmsContestWebServer 1 (pid 109) "
             "%s: TERM sent; then the group could not be checked: x; TERM "
             "was sent, KILL was not; evidence log/watchdog/e.json; %s. %s"
             % (self.WHAT, self.USR1, tail)),
            (self.DECISION, Outcome("failed", "KILL failed: x",
                                    usr1=self.USR1), "ERROR",
             "Watchdog: could not restart cmsContestWebServer 1 (pid 109) "
             "%s: KILL failed: x; TERM was sent before it; evidence "
             "log/watchdog/e.json; %s. %s" % (self.WHAT, self.USR1, tail)),
            (self.DECISION, Outcome("error", "bad file descriptor"),
             "ERROR",
             "Watchdog: the restart of cmsContestWebServer 1 (pid 109) %s "
             "failed unexpectedly: bad file descriptor; TERM and KILL may "
             "have been sent. %s" % (self.WHAT, tail)),
            (self.WOULD, Outcome("error", "bad file descriptor"), "ERROR",
             "Watchdog: the would-restart of cmsContestWebServer 1 (pid "
             "109) %s failed unexpectedly: bad file descriptor; no TERM or "
             "KILL was sent. %s" % (self.WHAT, tail)),
            (self.DECISION, Outcome("interrupted", "the watchdog stopped "
                                    "during it"), "ERROR",
             "Watchdog: the restart of cmsContestWebServer 1 (pid 109) %s "
             "was interrupted: the watchdog stopped during it; TERM and KILL "
             "may have been sent. %s" % (self.WHAT, tail)),
            (self.WOULD, Outcome("interrupted", "the watchdog stopped "
                                 "during it"), "ERROR",
             "Watchdog: the would-restart of cmsContestWebServer 1 (pid "
             "109) %s was interrupted: the watchdog stopped during it; no "
             "TERM or KILL was sent. %s" % (self.WHAT, tail)),
            (self.WOULD, Outcome("would", usr1=self.USR1), "WARNING",
             "Watchdog: WOULD restart cmsContestWebServer 1 (pid 109) %s; "
             "DB OK; evidence log/watchdog/e.json; %s; observe mode: "
             "nothing was restarted." % (self.WHAT, self.USR1)),
        )
        for decision, outcome, level, line in cases:
            with self.subTest(detail=outcome.detail or outcome.result):
                self.assertEqual(
                    restart_line(decision, self.PROGRAM, outcome, "e.json",
                                 0), (level, line))
                self.assertNotIn("nothing was signalled", line)

    def test_start_lines(self):
        start = Decision(START, programs=("cmslogservice",
                                          "cmscontestwebserver1"),
                         rule="R5")
        names = "cmslogservice, cmscontestwebserver1"
        cases = (
            (start, Outcome("started"), "WARNING",
             "Watchdog: started %s, left FATAL by a database outage (R5). "
             "Check ./status.sh." % names),
            (replace(start, would=True), Outcome("would"), "WARNING",
             "Watchdog: WOULD start %s, left FATAL by a database outage "
             "(R5); observe mode: nothing was started." % names),
            (replace(start, would=True, shadow=True), Outcome("would"),
             "WARNING",
             "Watchdog: WOULD start %s, left FATAL by a database outage "
             "(R5); their class is not in CMS_WATCHDOG_RESTART (H9), so "
             "nothing was done." % names),
            (start, Outcome("unbooked", "its ledger could not be written: "
                            "disk full"), "ERROR",
             "Watchdog: did not start %s: its ledger could not be written: "
             "disk full." % names),
            (start, Outcome("start failed", "cmslogservice: SPAWN_ERROR"),
             "ERROR",
             "Watchdog: could not start all of %s: cmslogservice: "
             "SPAWN_ERROR. Read ./logs.sh, then start them with "
             "supervisorctl." % names),
            (start, Outcome("error", "boom"), "ERROR",
             "Watchdog: the start of %s failed unexpectedly: boom; some of "
             "them may have been started. Read ./logs.sh and ./status.sh, "
             "then start the FATAL ones with supervisorctl." % names),
            (replace(start, would=True), Outcome("error", "boom"), "ERROR",
             "Watchdog: the would-start of %s failed unexpectedly: boom; "
             "nothing was started." % names),
            (start, Outcome("interrupted", "the watchdog stopped during "
                            "it"), "ERROR",
             "Watchdog: the start of %s was interrupted: the watchdog "
             "stopped during it; some of them may have been started. Read "
             "./logs.sh and ./status.sh, then start the FATAL ones with "
             "supervisorctl." % names),
        )
        for decision, outcome, level, line in cases:
            with self.subTest(result=outcome.result,
                              would=decision.would, shadow=decision.shadow):
                self.assertEqual(start_line(decision, outcome),
                                 (level, line))


if __name__ == "__main__":
    unittest.main()
