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

"""Tests for cmsWatchdog's actuators, against real child processes."""

import asyncio
import inspect
import os
import signal
import subprocess
import sys
import time
import unittest

from cms.io.async_service import AsyncService
from cms.io.service import Service
from cms.server.admin.server import AdminWebServer
from cms.server.contest.server import ContestWebServer
from cms.service.EvaluationService import EvaluationService
from cms.service.LogService import LogService
from cms.service.ProxyService import ProxyService
from cms.service.ResourceService import ResourceService
from cms.service.ScoringService import ScoringService
from cms.service.Worker import Worker
from cms.watchdog.control import USR1_KINDS, DryRunActuator, \
    ProcessInfo, SignalActuator, Target, process_state
from cms.watchdog.policy import KIND_CLASS


# A program to restart: it prints the pid of its child (0 for none) and
# sleeps. --usr1 registers faulthandler like a CMS service,
# --ignore-term ignores SIGTERM, --grandchild starts a sleep in its
# process group. --keeper, after both, gives TERM back to the leader
# only: the sleep keeps it ignored through exec and outlives the
# leader's TERM, as a Worker's isolate keeper may.
LEADER = """\
import faulthandler, signal, subprocess, sys, time
if "--usr1" in sys.argv:
    faulthandler.register(signal.SIGUSR1, file=sys.__stderr__,
                          all_threads=True)
if "--ignore-term" in sys.argv:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen(["sleep", "60"]) if "--grandchild" in sys.argv \\
    else None
if "--keeper" in sys.argv:
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
print(child.pid if child else 0, flush=True)
time.sleep(60)
"""
NEW_PID = 4_000_000


def alive(pid: int) -> bool:
    """Tell whether pid runs (a zombie does not)."""
    return process_state(pid) not in (None, "Z", "X")


class Child:
    """A leader in a process group of its own, as supervisord starts."""

    def __init__(self, *flags: str):
        self.process = subprocess.Popen(
            [sys.executable, "-c", LEADER, *flags], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, process_group=0)
        self.pid = self.process.pid
        self.grandchild = int(self.process.stdout.readline())

    def finish(self) -> str:
        """Kill what is left of the group; return the leader's stderr."""
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return self.process.communicate(timeout=10)[1]


class FakeSource:
    """A process source over one child, as supervisord would show it.

    Once the child has exited, it shows the program RUNNING again as
    NEW_PID, as supervisord's autorestart would. Each programs() call
    takes programs_delay seconds, and raises the next item of
    programs_errors while there is one (None: it answers).

    """

    def __init__(self, child: Child, kind: str = "ContestWebServer"):
        self.child = child
        self.kind = kind
        self.pid_override: int | None = None
        self.started: list[str] = []
        self.start_error: Exception | None = None
        self.programs_delay = 0.0
        self.programs_errors: list[Exception | None] = []

    async def programs(self) -> list[ProcessInfo]:
        if self.programs_delay:
            await asyncio.sleep(self.programs_delay)
        if self.programs_errors:
            error = self.programs_errors.pop(0)
            if error is not None:
                raise error
        pid = (self.child.pid if self.child.process.poll() is None
               else NEW_PID)
        if self.pid_override is not None:
            pid = self.pid_override
        return [ProcessInfo("cws", self.kind, 0, "RUNNING", pid, 1.0)]

    async def start(self, name: str) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started.append(name)


class TestSignalActuator(unittest.IsolatedAsyncioTestCase):

    def child(self, *flags: str) -> Child:
        child = Child(*flags)
        self.addCleanup(child.finish)
        return child

    def actuator(self, source: FakeSource, log: list,
                 **kwargs) -> SignalActuator:
        def killpg(pid: int, signum: int) -> None:
            log.append(("signal", signum))
            os.killpg(pid, signum)

        options = {"term_wait_s": 5, "confirm_s": 5, "poll_s": 0.05,
                   "usr1_wait_s": 0, "killpg": killpg}
        options.update(kwargs)
        return SignalActuator(source, **options)

    async def test_the_booking_comes_before_any_signal(self):
        child = self.child()
        log: list = []
        outcome = await self.actuator(FakeSource(child), log).restart(
            Target("cws", "ContestWebServer", child.pid),
            lambda: log.append(("book", child.process.poll())),
            lambda: log.append(("evidence", None)))
        self.assertEqual(log[:3], [("book", None), ("evidence", None),
                                   ("signal", signal.SIGTERM)])
        self.assertEqual((outcome.result, outcome.new_pid, outcome.killed),
                         ("restarted", NEW_PID, False))
        self.assertEqual(child.process.returncode, -signal.SIGTERM)

    async def test_a_child_that_ignores_term_is_killed_after_the_wait(self):
        child = self.child("--ignore-term")
        log: list = []
        start = time.monotonic()
        outcome = await self.actuator(
            FakeSource(child), log, term_wait_s=0.3).restart(
            Target("cws", "ContestWebServer", child.pid), lambda: None,
            lambda: None)
        self.assertGreaterEqual(time.monotonic() - start, 0.3)
        self.assertEqual([entry for entry in log if entry[0] == "signal"],
                         [("signal", signal.SIGTERM),
                          ("signal", signal.SIGKILL)])
        self.assertTrue(outcome.killed)
        self.assertEqual(child.process.returncode, -signal.SIGKILL)

    async def test_an_exited_pid_is_left_alone(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        log: list = []
        child = self.child()
        outcome = await self.actuator(FakeSource(child), log).restart(
            Target("cws", "ContestWebServer", gone.pid), lambda: None,
            lambda: None)
        self.assertEqual(outcome.result, "gone")
        self.assertEqual(log, [])

    async def test_a_leader_that_exits_before_its_term_is_gone(self):
        child = self.child()
        log: list = []

        def write_evidence() -> None:
            # It dies meanwhile (a crash, or an operator's restart); a
            # zombie, it still holds its pid.
            os.kill(child.pid, signal.SIGKILL)
            deadline = time.monotonic() + 5
            while process_state(child.pid) != "Z" \
                    and time.monotonic() < deadline:
                time.sleep(0.01)

        outcome = await self.actuator(FakeSource(child), log).restart(
            Target("cws", "ContestWebServer", child.pid), lambda: None,
            write_evidence)
        self.assertEqual((outcome.result, outcome.usr1),
                         ("gone", "no USR1: the process does not handle it"))
        self.assertEqual(log, [])

    async def test_a_changed_pid_is_refused_with_nothing_signalled(self):
        child = self.child()
        source = FakeSource(child)
        source.pid_override = 1
        log: list = []
        booked: list = []
        outcome = await self.actuator(source, log).restart(
            Target("cws", "ContestWebServer", child.pid),
            lambda: booked.append(True), lambda: None)
        self.assertEqual(outcome.result, "refused")
        self.assertEqual(log, [])
        self.assertEqual(booked, [True])
        self.assertTrue(alive(child.pid))

    async def test_a_source_that_fails_the_check_is_refused(self):
        child = self.child()
        source = FakeSource(child)
        source.programs_errors = [OSError("supervisord did not answer")]
        log: list = []
        booked: list = []
        outcome = await self.actuator(source, log).restart(
            Target("cws", "ContestWebServer", child.pid),
            lambda: booked.append(True), lambda: log.append("evidence"))
        self.assertEqual((outcome.result, outcome.detail, booked),
                         ("refused", "the process source did not answer: "
                          "supervisord did not answer", [True]))
        self.assertEqual(log, [])
        self.assertTrue(alive(child.pid))

    async def test_a_source_that_fails_meanwhile_still_confirms(self):
        child = self.child()
        source = FakeSource(child)
        # It answers the check, then fails twice during the confirmation.
        source.programs_errors = [None, OSError("supervisord did not "
                                                "answer"), TimeoutError()]
        outcome = await self.actuator(source, []).restart(
            Target("cws", "ContestWebServer", child.pid), lambda: None,
            lambda: None)
        self.assertEqual((outcome.result, outcome.new_pid),
                         ("restarted", NEW_PID))
        self.assertEqual(source.programs_errors, [])

    async def test_the_group_signal_reaches_a_grandchild(self):
        child = self.child("--grandchild")
        self.assertTrue(alive(child.grandchild))
        outcome = await self.actuator(FakeSource(child), []).restart(
            Target("cws", "ContestWebServer", child.pid), lambda: None,
            lambda: None)
        self.assertEqual(outcome.result, "restarted")
        deadline = time.monotonic() + 5
        while alive(child.grandchild) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        self.assertFalse(alive(child.grandchild))

    async def test_a_member_that_outlives_the_leader_is_killed(self):
        child = self.child("--ignore-term", "--grandchild", "--keeper")
        log: list = []
        outcome = await self.actuator(FakeSource(child, "Worker"),
                                      log).restart(
            Target("cws", "Worker", child.pid), lambda: None, lambda: None)
        self.assertEqual([entry for entry in log if entry[0] == "signal"],
                         [("signal", signal.SIGTERM),
                          ("signal", signal.SIGKILL)])
        self.assertEqual((outcome.result, outcome.killed),
                         ("restarted", True))
        self.assertEqual(child.process.returncode, -signal.SIGTERM)
        deadline = time.monotonic() + 5
        while alive(child.grandchild) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        self.assertFalse(alive(child.grandchild))

    async def test_a_failed_evidence_write_still_restarts(self):
        child = self.child()

        def write_evidence() -> None:
            raise OSError(28, "No space left on device")

        outcome = await self.actuator(FakeSource(child), []).restart(
            Target("cws", "ContestWebServer", child.pid), lambda: None,
            write_evidence)
        self.assertEqual(outcome.result, "restarted")
        self.assertIn("No space left on device", outcome.evidence_error)

    async def test_a_failed_booking_stops_everything(self):
        child = self.child()
        log: list = []

        def book() -> None:
            raise OSError(28, "No space left on device")

        outcome = await self.actuator(FakeSource(child), log).restart(
            Target("cws", "ContestWebServer", child.pid), book,
            lambda: log.append("evidence"))
        self.assertEqual(outcome.result, "unbooked")
        self.assertEqual(log, [])
        self.assertTrue(alive(child.pid))

    async def test_a_term_error_is_a_failed_action(self):
        child = self.child("--usr1")
        log: list = []
        booked: list = []

        def killpg(pid: int, signum: int) -> None:
            log.append(("signal", signum))
            raise PermissionError(1, "Operation not permitted")

        outcome = await self.actuator(
            FakeSource(child), log, killpg=killpg).restart(
            Target("cws", "ContestWebServer", child.pid),
            lambda: booked.append(True), lambda: None)
        self.assertEqual((outcome.result, outcome.detail, outcome.killed),
                         ("failed",
                          "TERM failed: [Errno 1] Operation not permitted",
                          False))
        self.assertIn("USR1 sent", outcome.usr1)
        self.assertEqual((log, booked), ([("signal", signal.SIGTERM)],
                                         [True]))
        self.assertTrue(alive(child.pid))

    async def test_a_kill_error_is_a_failed_action(self):
        child = self.child("--ignore-term")
        log: list = []

        def killpg(pid: int, signum: int) -> None:
            log.append(("signal", signum))
            if signum == signal.SIGKILL:
                raise PermissionError(1, "Operation not permitted")
            os.killpg(pid, signum)

        outcome = await self.actuator(
            FakeSource(child), log, killpg=killpg, term_wait_s=0.1).restart(
            Target("cws", "ContestWebServer", child.pid), lambda: None,
            lambda: None)
        self.assertEqual((outcome.result, outcome.detail, outcome.killed),
                         ("failed",
                          "KILL failed: [Errno 1] Operation not permitted",
                          False))
        self.assertEqual(log, [("signal", signal.SIGTERM),
                               ("signal", signal.SIGKILL)])
        self.assertTrue(alive(child.pid))

    async def test_an_error_after_term_is_a_failed_action(self):
        child = self.child()
        log: list = []
        # Without /proc, the group cannot be checked once TERM is sent.
        outcome = await self.actuator(
            FakeSource(child), log, proc="/nonexistent").restart(
            Target("cws", "ContestWebServer", child.pid), lambda: None,
            lambda: None)
        self.assertEqual((outcome.result, outcome.killed), ("failed", False))
        self.assertTrue(outcome.detail.startswith("TERM sent; then "),
                        outcome.detail)
        self.assertIn("No such file or directory", outcome.detail)
        self.assertEqual(log, [("signal", signal.SIGTERM)])
        self.assertEqual(child.process.wait(timeout=5), -signal.SIGTERM)

    async def test_a_pidfd_error_is_a_failed_action(self):
        child = self.child()
        log: list = []
        booked: list = []

        def pidfd_open(pid: int) -> int:
            raise PermissionError(1, "Operation not permitted")

        outcome = await self.actuator(
            FakeSource(child), log, pidfd_open=pidfd_open).restart(
            Target("cws", "ContestWebServer", child.pid),
            lambda: booked.append(True), lambda: log.append("evidence"))
        self.assertEqual((outcome.result, outcome.detail, booked),
                         ("failed", "pidfd_open failed: [Errno 1] "
                          "Operation not permitted", [True]))
        self.assertEqual(log, [])
        self.assertTrue(alive(child.pid))

    async def test_a_usr1_error_does_not_stop_the_restart(self):
        child = self.child("--usr1")

        def send_signal(pidfd: int, signum: int) -> None:
            raise PermissionError(1, "Operation not permitted")

        outcome = await self.actuator(
            FakeSource(child), [], send_signal=send_signal).restart(
            Target("cws", "ContestWebServer", child.pid), lambda: None,
            lambda: None)
        self.assertEqual((outcome.result, outcome.usr1),
                         ("restarted",
                          "no USR1: [Errno 1] Operation not permitted"))
        self.assertEqual(child.process.returncode, -signal.SIGTERM)

    async def test_no_new_pid_is_unconfirmed(self):
        child = self.child()
        source = FakeSource(child)
        source.pid_override = child.pid
        outcome = await self.actuator(source, [], confirm_s=0.3).restart(
            Target("cws", "ContestWebServer", child.pid), lambda: None,
            lambda: None)
        self.assertEqual(outcome.result, "unconfirmed")

    async def test_the_confirmation_ends_at_its_deadline(self):
        child = self.child()
        target = Target("cws", "ContestWebServer", child.pid)
        # The child keeps running: no new pid ever shows.
        for delay, poll_s in ((2.0, 0.05), (0.0, 5.0), (2.0, 5.0)):
            with self.subTest(delay=delay, poll_s=poll_s):
                source = FakeSource(child)
                source.programs_delay = delay
                actuator = self.actuator(source, [], confirm_s=0.3,
                                         poll_s=poll_s)
                start = time.monotonic()
                self.assertIsNone(await actuator._confirm(target))
                # confirm_s + min(poll_s, 1 s), plus a margin.
                self.assertLess(time.monotonic() - start,
                                0.3 + min(poll_s, 1.0) + 0.3)

    async def test_a_pid_that_shows_at_the_deadline_is_confirmed(self):
        child = self.child()
        source = FakeSource(child)
        source.pid_override = child.pid
        # Reads at 0, 0.4 and 0.8 s miss it; only the read at the 1 s
        # deadline, after the last pause, can see it.
        asyncio.get_running_loop().call_later(
            0.9, setattr, source, "pid_override", NEW_PID)
        actuator = self.actuator(source, [], confirm_s=1.0, poll_s=0.4)
        self.assertEqual(await actuator._confirm(
            Target("cws", "ContestWebServer", child.pid)), NEW_PID)

    async def test_start(self):
        child = self.child()
        source = FakeSource(child)
        booked: list = []
        outcome = await self.actuator(source, []).start(
            ["a", "b"], lambda: booked.append(True))
        self.assertEqual((outcome.result, source.started, booked),
                         ("started", ["a", "b"], [True]))
        source.start_error = RuntimeError("SPAWN_ERROR")
        outcome = await self.actuator(source, []).start(["c"], lambda: None)
        self.assertEqual(outcome.result, "start failed")
        self.assertIn("c: SPAWN_ERROR", outcome.detail)


class TestEvidenceSignal(unittest.IsolatedAsyncioTestCase):

    def child(self, *flags: str) -> Child:
        child = Child(*flags)
        self.addCleanup(child.finish)
        return child

    async def test_usr1_reaches_the_leader_only(self):
        child = self.child("--usr1", "--grandchild")
        outcome = await DryRunActuator(
            FakeSource(child), usr1_wait_s=0.5).restart(
            Target("cws", "ContestWebServer", child.pid), lambda: None,
            lambda: None)
        self.assertEqual(outcome.result, "would")
        self.assertIn("USR1 sent", outcome.usr1)
        self.assertTrue(alive(child.pid))
        # A group USR1 would have killed the sleep (default action).
        self.assertTrue(alive(child.grandchild))
        self.assertIn("Current thread", child.finish())

    async def test_a_process_that_does_not_handle_usr1_is_not_sent_it(self):
        child = self.child()
        outcome = await DryRunActuator(FakeSource(child)).restart(
            Target("cws", "ContestWebServer", child.pid), lambda: None,
            lambda: None)
        self.assertEqual(outcome.usr1,
                         "no USR1: the process does not handle it")
        await asyncio.sleep(0.2)
        self.assertTrue(alive(child.pid))

    async def test_a_stopped_process_is_not_sent_usr1(self):
        child = self.child("--usr1")
        os.kill(child.pid, signal.SIGSTOP)
        self.addCleanup(os.kill, child.pid, signal.SIGCONT)
        deadline = time.monotonic() + 5
        while process_state(child.pid) != "T" \
                and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        outcome = await DryRunActuator(FakeSource(child)).snapshot(
            Target("cws", "ContestWebServer", child.pid), lambda: None)
        self.assertEqual((outcome.result, outcome.usr1),
                         ("evidence",
                          "no USR1: the process is stopped (state T)"))

    async def test_a_kind_off_the_allow_list_is_not_sent_usr1(self):
        child = self.child("--usr1")
        outcome = await DryRunActuator(
            FakeSource(child, "TelegramBot")).snapshot(
            Target("cws", "TelegramBot", child.pid), lambda: None)
        self.assertEqual(outcome.usr1,
                         "no USR1: TelegramBot is not a CMS service")

    async def test_the_dry_run_sends_nothing_else(self):
        child = self.child("--usr1")
        booked: list = []
        outcome = await DryRunActuator(
            FakeSource(child), usr1_wait_s=0).restart(
            Target("cws", "ContestWebServer", child.pid),
            lambda: booked.append(True), lambda: None)
        self.assertEqual((outcome.result, booked), ("would", [True]))
        await asyncio.sleep(0.3)
        self.assertTrue(alive(child.pid))
        source = FakeSource(child)
        outcome = await DryRunActuator(source).start(["a"], lambda: None)
        self.assertEqual((outcome.result, source.started), ("would", []))


class TestAllowList(unittest.TestCase):

    def test_the_usr1_kinds_are_the_services_that_register_it(self):
        classes = {
            "LogService": LogService, "ResourceService": ResourceService,
            "EvaluationService": EvaluationService,
            "ScoringService": ScoringService, "ProxyService": ProxyService,
            "ContestWebServer": ContestWebServer,
            "AdminWebServer": AdminWebServer, "Worker": Worker}
        self.assertEqual(USR1_KINDS, frozenset(classes))
        self.assertEqual(USR1_KINDS, frozenset(KIND_CLASS))
        for kind, cls in classes.items():
            with self.subTest(kind=kind):
                self.assertTrue(issubclass(cls, (AsyncService, Service)))
        # Both base classes register it when they run (Task 2).
        self.assertIn("register_stack_dump()",
                      inspect.getsource(AsyncService._async_run))
        self.assertIn("register_stack_dump()",
                      inspect.getsource(Service.run))


if __name__ == "__main__":
    unittest.main()
