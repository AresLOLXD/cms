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

"""Tests for the fault injector of the load test (faults.py)."""

import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import faults  # noqa: E402
import faultstats  # noqa: E402


# What the commands of every test answer: the container and its pid.
ANSWERS = {
    "ps -q db": (0, "c0ffee\n", ""),
    "{{.State.Pid}}": (0, "4242\n", ""),
}


class FakeRunner:
    """Stand in for run_command: record the commands and answer them."""

    def __init__(self, answers: dict[str, tuple[int, str, str]]):
        self.commands: list[list[str]] = []
        self.answers = answers

    def __call__(self, argv: list[str],
                 check: bool = True) -> subprocess.CompletedProcess:
        self.commands.append(argv)
        joined = " ".join(argv)
        for needle, (returncode, stdout, stderr) in self.answers.items():
            if needle in joined:
                return subprocess.CompletedProcess(
                    argv, returncode, stdout, stderr)
        return subprocess.CompletedProcess(argv, 0, "", "")


class SignalingRunner(FakeRunner):
    """A FakeRunner that gets SIGTERM just after a command took effect."""

    def __init__(self, answers: dict[str, tuple[int, str, str]],
                 signal_after: list[str]):
        super().__init__(answers)
        self.signal_after = signal_after

    def __call__(self, argv: list[str],
                 check: bool = True) -> subprocess.CompletedProcess:
        result = super().__call__(argv, check)
        if argv == self.signal_after:
            # What run.sh's SIGTERM does, through main()'s handler.
            raise SystemExit(143)
        return result


class FailingRunner(FakeRunner):
    """A FakeRunner whose first command with a given text raises."""

    def __init__(self, answers: dict[str, tuple[int, str, str]],
                 fail_on: str | None):
        super().__init__(answers)
        self.fail_on = fail_on

    def __call__(self, argv: list[str],
                 check: bool = True) -> subprocess.CompletedProcess:
        result = super().__call__(argv, check)
        if self.fail_on is not None and self.fail_on in " ".join(argv):
            self.fail_on = None
            raise OSError("the command could not run")
        return result


class FakeTime:
    """A clock that moves only when the injector sleeps."""

    def __init__(self, interrupt_at: float | None = None):
        self.now = 1_800_000_000.0
        self.interrupt_at = interrupt_at

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        if seconds == self.interrupt_at:
            # What run.sh's SIGTERM does, through main()'s handler.
            raise SystemExit(143)
        self.now += seconds


class FakeStdin:
    def __init__(self):
        self.text = ""

    def write(self, text: str) -> None:
        self.text += text

    def close(self) -> None:
        pass


class FakePopen:
    """Stand in for subprocess.Popen, for the holder's psql."""

    def __init__(self, argv: list[str], **keywords):
        self.argv = argv
        self.stdin = FakeStdin()
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


class BrokenPopen(FakePopen):
    """A holder that cannot be killed."""

    def kill(self) -> None:
        raise OSError("no such process")


class InjectorTest(unittest.TestCase):

    def setUp(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        self.out = os.path.join(directory, "faults.json")
        self.popens: list[FakePopen] = []

    def _injector(self, answers: dict | None = None,
                  time_source: FakeTime | None = None) -> faults.Injector:
        self.runner = FakeRunner({**(answers or {}), **ANSWERS})
        self.time = time_source or FakeTime()

        def popen(argv: list[str], **keywords) -> FakePopen:
            process = FakePopen(argv, **keywords)
            self.popens.append(process)
            return process

        return faults.Injector(
            "cmsload-fork", "/l/compose.yml", "/l/.env.load", self.out,
            run=self.runner, popen=popen, sleep=self.time.sleep,
            clock=self.time.clock)

    def test_docker_goes_through_the_rootful_engine(self):
        injector = self._injector()
        self.assertEqual(
            injector.docker("pause", "c0ffee"),
            ["sg", "docker", "-c", "docker --context default pause c0ffee"])
        self.assertEqual(
            injector.compose("ps", "-q", "db")[3],
            "docker --context default compose -p cmsload-fork "
            "-f /l/compose.yml --env-file /l/.env.load ps -q db")

    def test_the_drop_rules_are_two_way_on_5432_and_spare_loopback(self):
        self.assertEqual(
            faults.iptables_argv(4242, "-I", faults.DROP_RULES[0]),
            ["sudo", "nsenter", "-t", "4242", "-n", "iptables", "-I",
             "INPUT", "!", "-i", "lo", "-p", "tcp", "--dport", "5432",
             "-j", "DROP"])
        self.assertEqual(
            faults.DROP_RULES[1],
            ("OUTPUT", "!", "-o", "lo", "-p", "tcp", "--sport", "5432",
             "-j", "DROP"))

    def test_f1_ends_the_cms_backends(self):
        injector = self._injector({"pg_terminate_backend": (0, "7\n", "")})
        injector.f1()
        self.assertEqual(injector.records[-1]["fault"], "F1")
        self.assertEqual(injector.records[-1]["terminated"], 7)
        self.assertEqual(shlex.split(self.runner.commands[-1][3])[-1],
                         faults.TERMINATE_SQL)
        self.assertIn("application_name LIKE 'cms%'", faults.TERMINATE_SQL)

    def test_f2_drops_both_ways_then_does_the_partition_step(self):
        injector = self._injector(
            {"idle in transaction%": (0, "2\n", "")})
        started = self.time.now
        injector.f2()
        record = injector.records[-1]
        self.assertEqual(record["start"], started)
        self.assertEqual(record["end"], started + faults.F2_DROP_S)
        self.assertEqual(record["partition_step"],
                         record["end"] + faults.F2_STEP_AFTER_S)
        self.assertEqual(record["partition_step_terminated"], 2)
        self.assertEqual(
            [command for command in self.runner.commands
             if command[0] == "sudo"],
            [faults.iptables_argv(4242, "-I", faults.DROP_RULES[0]),
             faults.iptables_argv(4242, "-I", faults.DROP_RULES[1]),
             faults.iptables_argv(4242, "-D", faults.DROP_RULES[1]),
             faults.iptables_argv(4242, "-D", faults.DROP_RULES[0])])
        self.assertEqual(shlex.split(self.runner.commands[-1][3])[-1],
                         faults.PARTITION_STEP_SQL.format(end=record["end"]))
        self.assertEqual(injector.rules, [])

    def test_cleanup_after_an_interrupted_drop_removes_both_rules(self):
        injector = self._injector(
            time_source=FakeTime(interrupt_at=faults.F2_DROP_S))
        with self.assertRaises(SystemExit):
            injector.f2()
        injector.cleanup()
        self.assertEqual(
            self.runner.commands[-2:],
            [faults.iptables_argv(4242, "-D", faults.DROP_RULES[1]),
             faults.iptables_argv(4242, "-D", faults.DROP_RULES[0])])
        self.assertEqual(injector.rules, [])

    def test_cleanup_after_an_interrupted_pause_unpauses_postgresql(self):
        injector = self._injector(
            time_source=FakeTime(interrupt_at=faults.F3_PAUSE_S))
        with self.assertRaises(SystemExit):
            injector.f3()
        injector.cleanup()
        self.assertEqual(self.runner.commands[-1],
                         injector.docker("unpause", "c0ffee"))
        self.assertIsNone(injector.paused)

    def test_cleanup_after_a_signal_as_the_rule_goes_in_deletes_it(self):
        injector = self._injector()
        injector.run = SignalingRunner(
            ANSWERS, faults.iptables_argv(4242, "-I", faults.DROP_RULES[0]))
        with self.assertRaises(SystemExit):
            injector.f2()
        injector.cleanup()
        self.assertEqual(
            injector.run.commands[-1],
            faults.iptables_argv(4242, "-D", faults.DROP_RULES[0]))
        self.assertEqual(injector.rules, [])

    def test_cleanup_after_a_signal_during_the_delete_retries_it(self):
        injector = self._injector()
        injector.run = SignalingRunner(
            ANSWERS, faults.iptables_argv(4242, "-D", faults.DROP_RULES[1]))
        with self.assertRaises(SystemExit):
            injector.f2()
        injector.run = self.runner
        injector.cleanup()
        self.assertEqual(
            [command for command in self.runner.commands
             if command[0] == "sudo"],
            [faults.iptables_argv(4242, "-D", faults.DROP_RULES[1]),
             faults.iptables_argv(4242, "-D", faults.DROP_RULES[0])])
        self.assertEqual(injector.rules, [])

    def test_cleanup_after_a_signal_as_the_pause_takes_effect_unpauses(self):
        injector = self._injector()
        injector.run = SignalingRunner(
            ANSWERS, injector.docker("pause", "c0ffee"))
        with self.assertRaises(SystemExit):
            injector.f3()
        injector.cleanup()
        self.assertEqual(injector.run.commands[-1],
                         injector.docker("unpause", "c0ffee"))
        self.assertIsNone(injector.paused)

    def test_a_rule_that_cannot_be_deleted_is_reported(self):
        injector = self._injector(
            {"iptables -D": (1, "", "iptables: Bad rule\n")})
        injector.rules.append((4242, faults.DROP_RULES[0]))
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            injector.cleanup()
        self.assertIn("iptables -D INPUT ! -i lo", errors.getvalue())
        self.assertIn("Bad rule", errors.getvalue())
        self.assertEqual(injector.rules, [])

    def test_f4_runs_cmssetupdb_behind_an_idle_reader(self):
        injector = self._injector({
            "application_name = ": (0, "1\n", ""),
            "cmsSetupDB": (1, "", "2026 - CRITICAL [x] "
                           + faults.SETUP_DB_MESSAGE + ": another\n")})
        injector.f4()
        record = injector.records[-1]
        self.assertEqual((record["fault"], record["holder_ready"],
                          record["exit_status"], record["message"]),
                         ("F4", True, 1, True))
        self.assertEqual(self.popens[0].stdin.text, faults.HOLDER_SCRIPT)
        self.assertIn("psql", self.popens[0].argv[3])
        self.assertIsNone(injector.holder)

    def test_f4_records_that_the_holder_never_became_ready(self):
        injector = self._injector({"application_name = ": (0, "0\n", "")})
        injector.f4()
        self.assertIs(injector.records[-1]["holder_ready"], False)

    def test_a_cleanup_step_that_raises_does_not_stop_the_others(self):
        for broken in ("iptables -D", "unpause", "kill"):
            with self.subTest(broken=broken):
                injector = self._injector()
                injector.run = FailingRunner(
                    ANSWERS, None if broken == "kill" else broken)
                holder = (BrokenPopen if broken == "kill" else FakePopen)(
                    ["psql"])
                injector.rules.extend(
                    [(4242, faults.DROP_RULES[0]),
                     (4242, faults.DROP_RULES[1])])
                injector.paused = "c0ffee"
                injector.holder = holder
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    injector.cleanup()
                self.assertIn("raised", errors.getvalue())
                commands = [" ".join(command)
                            for command in injector.run.commands]
                self.assertEqual(
                    sum("iptables -D" in command for command in commands),
                    2)
                self.assertTrue(any("unpause" in command
                                    for command in commands))
                self.assertEqual(injector.rules, [])
                self.assertIsNone(injector.holder)
                if broken != "kill":
                    self.assertTrue(holder.killed)
                with open(self.out) as f:
                    self.assertEqual(json.load(f), injector.records)

    def test_a_failed_unpause_is_reported(self):
        injector = self._injector(
            {"unpause": (1, "", "Error: no such container\n")})
        injector.paused = "c0ffee"
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            injector.cleanup()
        self.assertIn("docker unpause c0ffee failed", errors.getvalue())
        self.assertIn("no such container", errors.getvalue())
        self.assertIsNone(injector.paused)

    def test_the_records_are_saved_as_json(self):
        injector = self._injector({"pg_terminate_backend": (0, "3\n", "")})
        injector.f1()
        injector.save()
        with open(self.out) as f:
            self.assertEqual(json.load(f), injector.records)

    def test_the_schedule_is_in_order_and_ends_before_the_end_burst(self):
        offsets = [faults.F1_AT[0], faults.F1_AT[1], faults.F3_AT,
                   faults.F4_AT, faults.F2_AT]
        self.assertEqual(offsets, sorted(offsets))
        # The fault run's end burst is the last 120 s of the contest.
        recovered = faults.F2_AT + faults.F2_DROP_S + faults.F2_STEP_AFTER_S
        self.assertLessEqual(recovered + faultstats.SCORES_LIMIT_S,
                             faults.MIN_CONTEST_S - 120)


if __name__ == "__main__":
    unittest.main()
