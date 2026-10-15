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
import time
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
    """Stand in for run_command: record the commands and answer them.

    An answer is (returncode, stdout, stderr), or an exception to raise.

    """

    def __init__(self, answers: dict[str, tuple[int, str, str]
                                     | Exception]):
        self.commands: list[list[str]] = []
        self.answers = answers

    def __call__(self, argv: list[str],
                 check: bool = True) -> subprocess.CompletedProcess:
        self.commands.append(argv)
        joined = " ".join(argv)
        for needle, answer in self.answers.items():
            if needle in joined:
                if isinstance(answer, Exception):
                    raise answer
                returncode, stdout, stderr = answer
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


# What the probe prints before its connect, and then when the connect
# deadline ends it.
PROBE_CONNECTING = '{"connect_started_at": 1800000003.0, "green": true}\n'
PROBE_TIMED_OUT = PROBE_CONNECTING + (
    '{"connect_started_at": 1800000003.0, "green": true, '
    '"outcome": "error", "error": "OperationalError: timeout expired", '
    '"elapsed_s": 10.1}\n')


class FakePopen:
    """Stand in for subprocess.Popen, for the holder and the probe."""

    def __init__(self, argv: list[str], **keywords):
        self.argv = argv
        self.stdin = FakeStdin()
        self.killed = False
        self.waited_after_kill = False
        self.started_at: float | None = None
        # What each communicate() answers, in order (the last one
        # repeats): (returncode, stdout, stderr), or an exception to
        # raise.
        self.answers: list[tuple[int, str, str] | Exception] = [
            (0, PROBE_TIMED_OUT, "")]
        self.returncode: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        self.waited_after_kill = self.killed
        return 0

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        answer = (self.answers.pop(0) if len(self.answers) > 1
                  else self.answers[0])
        if isinstance(answer, Exception):
            raise answer
        self.returncode, stdout, stderr = answer
        return stdout, stderr

    def kill(self) -> None:
        self.killed = True


class BrokenPopen(FakePopen):
    """A process that cannot be killed."""

    def kill(self) -> None:
        raise OSError("no such process")


class InjectorTest(unittest.TestCase):

    def setUp(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        self.out = os.path.join(directory, "faults.json")
        self.popens: list[FakePopen] = []
        # What the processes the injector starts answer, by the text
        # their command holds: one answer, or one per communicate().
        self.popen_answers: dict[str, object] = {}

    def _injector(self, answers: dict | None = None,
                  time_source: FakeTime | None = None) -> faults.Injector:
        self.runner = FakeRunner({**(answers or {}), **ANSWERS})
        self.time = time_source or FakeTime()

        def popen(argv: list[str], **keywords) -> FakePopen:
            process = FakePopen(argv, **keywords)
            process.started_at = self.time.now
            for needle, answer in self.popen_answers.items():
                if needle in " ".join(argv):
                    process.answers = (list(answer)
                                       if isinstance(answer, list)
                                       else [answer])
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
        self.assertIs(record["rules_removed"], True)
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

    def test_f2_records_a_rule_it_could_not_remove(self):
        for answer in ((1, "", "iptables: Bad rule\n"),
                       OSError("sudo is gone")):
            with self.subTest(answer=answer):
                injector = self._injector(
                    {"iptables -D OUTPUT": answer,
                     "idle in transaction%": (0, "0\n", "")})
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    injector.f2()
                self.assertIs(injector.records[-1]["rules_removed"], False)
                self.assertIn("iptables -D OUTPUT", errors.getvalue())
                # The other rule is still deleted.
                self.assertIn(
                    faults.iptables_argv(4242, "-D", faults.DROP_RULES[0]),
                    self.runner.commands)
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
            time_source=FakeTime(
                interrupt_at=faults.F3_PAUSE_S - faults.F3_PROBE_AFTER_S))
        with self.assertRaises(SystemExit):
            injector.f3()
        injector.cleanup()
        self.assertEqual(self.runner.commands[-1],
                         injector.docker("unpause", "c0ffee"))
        self.assertIsNone(injector.paused)

    def test_cleanup_kills_a_probe_that_is_still_running(self):
        # The signal arrives while the probe waits for its connect.
        injector = self._injector(
            time_source=FakeTime(
                interrupt_at=faults.F3_PAUSE_S - faults.F3_PROBE_AFTER_S))
        with self.assertRaises(SystemExit):
            injector.f3()
        probe = self.popens[0]
        self.assertIs(injector.probe, probe)
        self.assertFalse(probe.killed)
        injector.cleanup()
        self.assertTrue(probe.killed)
        self.assertTrue(probe.waited_after_kill)
        self.assertIsNone(injector.probe)

    def test_f3_probes_a_fresh_connection_two_seconds_into_the_pause(self):
        injector = self._injector()
        started = self.time.now
        injector.f3()
        record = injector.records[-1]
        self.assertEqual(record["end"], started + faults.F3_PAUSE_S)
        self.assertEqual(
            record["probe"],
            {"outcome": "error", "error": "OperationalError: timeout "
             "expired", "connect_started_at": 1800000003.0,
             "green": True, "elapsed_s": 10.1})
        probe = self.popens[0]
        self.assertEqual(probe.started_at, started + faults.F3_PROBE_AFTER_S)
        # Through the cms container, cut by timeout(1) before the unpause.
        words = shlex.split(probe.argv[3])
        self.assertEqual(
            words[-8:-2], ["exec", "-T", "cms", "timeout",
                           str(faults.F3_PROBE_TIMEOUT_S), "python3"])
        self.assertEqual(words[-2:], ["-c", faults.PROBE_SCRIPT])
        self.assertLess(faults.F3_PROBE_AFTER_S + faults.F3_PROBE_TIMEOUT_S,
                        faults.F3_PAUSE_S)
        pause, unpause = [
            command for command in self.runner.commands
            if command[:3] == ["sg", "docker", "-c"]
            and ("pause" in command[3])]
        self.assertEqual(pause, injector.docker("pause", "c0ffee"))
        self.assertEqual(unpause, injector.docker("unpause", "c0ffee"))
        self.assertIsNone(injector.probe)

    def test_f3_records_a_probe_that_connected(self):
        self.popen_answers["python3 -c"] = (
            0, '{"outcome": "ok", "error": "", "elapsed_s": 0.1}\n', "")
        injector = self._injector()
        injector.f3()
        self.assertEqual(injector.records[-1]["probe"]["outcome"], "ok")

    def test_f3_records_a_probe_cut_by_timeout_as_hung(self):
        injector = self._injector()
        self.popen_answers["python3 -c"] = (124, "", "")
        injector.f3()
        self.assertEqual(injector.records[-1]["probe"],
                         {"elapsed_s": None, "outcome": "hung",
                          "error": ""})

    def test_f3_records_where_a_probe_cut_by_timeout_got_to(self):
        # It printed the line before its connect, then hung in it.
        injector = self._injector()
        self.popen_answers["python3 -c"] = (124, PROBE_CONNECTING, "")
        injector.f3()
        self.assertEqual(injector.records[-1]["probe"],
                         {"connect_started_at": 1800000003.0,
                          "green": True, "elapsed_s": None,
                          "outcome": "hung", "error": ""})

    def test_f3_records_a_probe_that_does_not_answer_as_hung(self):
        # After the kill, communicate() reaps it and returns what it
        # printed before.
        self.popen_answers["python3 -c"] = [
            subprocess.TimeoutExpired("probe", faults.F3_PROBE_COLLECT_S),
            (-9, PROBE_CONNECTING, "")]
        injector = self._injector()
        injector.f3()
        self.assertEqual(injector.records[-1]["probe"],
                         {"connect_started_at": 1800000003.0,
                          "green": True, "elapsed_s": None,
                          "outcome": "hung", "error": ""})
        self.assertTrue(self.popens[0].killed)
        self.assertIsNone(injector.probe)

    def test_f3_reads_a_killed_probe_whose_pipe_stays_open(self):
        # A child of docker outlives the kill and holds the pipe: the
        # second wait times out too, with what was printed so far.
        self.popen_answers["python3 -c"] = [
            subprocess.TimeoutExpired("probe", faults.F3_PROBE_COLLECT_S),
            subprocess.TimeoutExpired("probe", faults.F3_PROBE_COLLECT_S,
                                      output=PROBE_CONNECTING.encode())]
        injector = self._injector()
        injector.f3()
        probe = injector.records[-1]["probe"]
        self.assertEqual((probe["outcome"], probe["connect_started_at"]),
                         ("hung", 1800000003.0))
        # And with nothing printed, it has no connect time.
        self.popen_answers["python3 -c"] = subprocess.TimeoutExpired(
            "probe", faults.F3_PROBE_COLLECT_S)
        injector = self._injector()
        injector.f3()
        self.assertEqual(injector.records[-1]["probe"],
                         {"elapsed_s": None, "outcome": "hung",
                          "error": ""})

    def test_f3_records_a_probe_that_printed_nothing_as_an_error(self):
        self.popen_answers["python3 -c"] = (
            1, "", "ModuleNotFoundError: No module named 'cms'\n")
        injector = self._injector()
        injector.f3()
        probe = injector.records[-1]["probe"]
        self.assertEqual(probe["outcome"], "error")
        self.assertIn("no answer (exit 1)", probe["error"])
        self.assertIn("No module named 'cms'", probe["error"])

    def test_the_probe_script_prints_a_line_before_and_after_it(self):
        # Run it against stand-ins for cms.io and the connection function.
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        for name in ("cms", "cms/io", "cms/db"):
            os.makedirs(os.path.join(directory, name))
            with open(os.path.join(directory, name, "__init__.py"), "w"):
                pass
        # cms.io makes psycopg2 green with a wait callback, as the real
        # one does.
        green_io = ("import psycopg2.extensions\n"
                    "psycopg2.extensions.set_wait_callback(\n"
                    "    lambda conn: None)\n")
        connects = ("def custom_psycopg2_connection():\n"
                    "    class Connection:\n"
                    "        def close(self):\n"
                    "            pass\n"
                    "    return Connection()\n")
        cases = {
            "ok": (green_io, connects,
                   {"outcome": "ok", "error": "", "green": True}),
            "error": (green_io,
                      "class OperationalError(Exception):\n"
                      "    pass\n"
                      "def custom_psycopg2_connection():\n"
                      "    raise OperationalError('timeout\\nexpired')\n",
                      {"outcome": "error", "green": True,
                       "error": "OperationalError: timeout expired"}),
            "not green": ("", connects,
                          {"outcome": "ok", "error": "", "green": False}),
        }
        for name, (io_source, source, expected) in cases.items():
            with self.subTest(case=name):
                with open(os.path.join(directory, "cms", "io",
                                       "__init__.py"), "w") as f:
                    f.write(io_source)
                with open(os.path.join(directory, "cms", "db",
                                       "session.py"), "w") as f:
                    f.write(source)
                before = time.time()
                done = subprocess.run(
                    [sys.executable, "-c", faults.PROBE_SCRIPT],
                    capture_output=True, text=True, timeout=60,
                    cwd=directory, env={"PYTHONPATH": directory})
                self.assertEqual(done.returncode, 0, done.stderr)
                lines = done.stdout.splitlines()
                self.assertEqual(len(lines), 2, done.stdout)
                connecting, result = map(json.loads, lines)
                # The line before the connect has what a hung probe
                # needs.
                self.assertEqual(
                    connecting,
                    {"connect_started_at": result["connect_started_at"],
                     "green": expected["green"]})
                elapsed = result.pop("elapsed_s")
                connect_started_at = result.pop("connect_started_at")
                self.assertEqual(result, expected)
                self.assertLess(elapsed, 5)
                # The time of the connect, in epoch seconds.
                self.assertLessEqual(before, connect_started_at)
                self.assertLessEqual(connect_started_at, time.time())

    def test_the_probe_flushes_its_first_line_before_the_connect(self):
        # A connect that never ends: the first line is already out when
        # timeout(1) would cut it.
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        for name in ("cms", "cms/io", "cms/db"):
            os.makedirs(os.path.join(directory, name))
            with open(os.path.join(directory, name, "__init__.py"), "w"):
                pass
        with open(os.path.join(directory, "cms", "db", "session.py"),
                  "w") as f:
            f.write("import time\n"
                    "def custom_psycopg2_connection():\n"
                    "    time.sleep(30)\n")
        probe = subprocess.Popen(
            [sys.executable, "-c", faults.PROBE_SCRIPT],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            cwd=directory, env={"PYTHONPATH": directory})
        self.addCleanup(probe.stdout.close)
        self.addCleanup(probe.wait)
        self.addCleanup(probe.kill)
        started = time.monotonic()
        line = probe.stdout.readline()
        # Still in its 30 s connect: the line was flushed before it, not
        # at the exit.
        self.assertLess(time.monotonic() - started, 20)
        self.assertIsNone(probe.poll())
        self.assertEqual(sorted(json.loads(line)),
                         ["connect_started_at", "green"])

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

    def test_f4_records_a_cmssetupdb_that_hung(self):
        # run_command cuts it after 120 s.
        injector = self._injector({
            "application_name = ": (0, "1\n", ""),
            "cmsSetupDB": subprocess.TimeoutExpired("cmsSetupDB", 120)})
        injector.f4()
        record = injector.records[-1]
        self.assertEqual((record["exit_status"], record["message"]),
                         (None, False))
        self.assertIn("duration", record)
        self.assertIsNone(injector.holder)

    def test_f4_records_that_the_holder_never_became_ready(self):
        injector = self._injector({"application_name = ": (0, "0\n", "")})
        injector.f4()
        self.assertIs(injector.records[-1]["holder_ready"], False)

    def test_a_cleanup_step_that_raises_does_not_stop_the_others(self):
        for broken in ("iptables -D", "unpause", "kill", "probe"):
            with self.subTest(broken=broken):
                injector = self._injector()
                injector.run = FailingRunner(
                    ANSWERS, broken if broken in ("iptables -D", "unpause")
                    else None)
                holder = (BrokenPopen if broken == "kill" else FakePopen)(
                    ["psql"])
                probe = (BrokenPopen if broken == "probe" else FakePopen)(
                    ["python3"])
                injector.probe = probe
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
                self.assertIsNone(injector.probe)
                if broken != "kill":
                    self.assertTrue(holder.killed)
                    self.assertTrue(holder.waited_after_kill)
                if broken != "probe":
                    self.assertTrue(probe.killed)
                    self.assertTrue(probe.waited_after_kill)
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

    def _schedule(self, fail_on: str) -> tuple[faults.Injector, str]:
        """Run the whole schedule with one command that raises.

        return: the injector, and what it reported to stderr.

        """
        answers = {"pg_terminate_backend": (0, "7\n", ""),
                   "idle in transaction%": (0, "2\n", ""),
                   "application_name = ": (0, "1\n", "")}
        injector = self._injector(answers)
        injector.run = self.runner = FailingRunner(
            {**answers, **ANSWERS}, fail_on)
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), \
                contextlib.redirect_stdout(io.StringIO()):
            injector.run_schedule(self.time.now)
        return injector, errors.getvalue()

    def test_a_fault_that_fails_does_not_stop_the_schedule(self):
        # The pause takes effect, then its command raises.
        injector, errors = self._schedule("default pause c0ffee")
        self.assertEqual([record["fault"] for record in injector.records],
                         ["F1", "F1", "F3", "F4", "F2"])
        f3 = injector.records[2]
        self.assertEqual(f3, {"fault": "F3", "start": f3["start"],
                              "error": "OSError: the command could not "
                              "run"})
        self.assertEqual(
            [record for record in injector.records if "error" in record],
            [f3])
        self.assertIn("F3 failed, the next fault still runs: OSError",
                      errors)
        # What it left in place is undone before the next fault.
        commands = [" ".join(command) for command in self.runner.commands]
        unpause = commands.index(" ".join(injector.docker("unpause",
                                                          "c0ffee")))
        setup_db = [index for index, command in enumerate(commands)
                    if "cmsSetupDB" in command]
        self.assertLess(unpause, setup_db[0])
        self.assertIsNone(injector.paused)
        # The probe never started; F4's holder is the only process.
        self.assertEqual(len(self.popens), 1)
        self.assertIn("psql", self.popens[0].argv[3])
        with open(self.out) as f:
            self.assertEqual(json.load(f), injector.records)

    def test_a_fault_that_fails_after_its_record_keeps_it(self):
        # F2's partition step fails after the drop was recorded.
        injector, errors = self._schedule("idle in transaction%")
        f2 = injector.records[-1]
        self.assertEqual((f2["fault"], f2["rules_removed"]), ("F2", True))
        self.assertIn("end", f2)
        self.assertEqual(f2["error"], "OSError: the command could not run")
        self.assertEqual(len(injector.records), 5)
        self.assertIn("F2 failed", errors)

    def test_a_failed_command_puts_its_stderr_in_the_record(self):
        injector = self._injector({
            "pg_terminate_backend": subprocess.CalledProcessError(
                1, ["psql"], "", "psql: error: no such role\n"),
            "idle in transaction%": (0, "2\n", ""),
            "application_name = ": (0, "1\n", "")})
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            injector.run_schedule(self.time.now)
        self.assertEqual(
            injector.records[0]["error"],
            "CalledProcessError: Command '['psql']' returned non-zero exit "
            "status 1. (psql: error: no such role)")

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
