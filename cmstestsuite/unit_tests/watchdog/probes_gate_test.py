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

"""Tests for cmsWatchdog's database gate and /proc facts."""

import asyncio
import json
import os
import shutil
import signal
import tempfile
import threading
import unittest

from sqlalchemy.engine import make_url

from cms import config
from cms.watchdog.policy import GATE_ANSWERED_REASONS, Backend
from cms.watchdog.probes import BACKEND_FIELDS, classify, db_gate, \
    parse_backends, proc_facts, psql_environment, thread_cpu_s
from cms.watchdog.procfs import CLOCK_TICKS


def environment(url: str) -> dict[str, str]:
    return psql_environment(url, "/usr/bin:/bin", 3, 3000)


class FakeProcess:
    """What asyncio.create_subprocess_exec returns, scripted."""

    def __init__(self, returncode: int = 0, stdout: bytes = b"",
                 stderr: bytes = b"", hang: bool = False,
                 kill_error: OSError | None = None,
                 wait_hang: bool = False):
        self.returncode = returncode
        self._output = (stdout, stderr)
        self._hang = hang
        self._kill_error = kill_error
        self._wait_hang = wait_hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(60)
        return self._output

    def kill(self):
        if self._kill_error is not None:
            raise self._kill_error
        self.killed = True
        self.returncode = -9

    async def wait(self):
        if self._wait_hang:
            await asyncio.sleep(60)
        return self.returncode


class FakeRun:
    """Stand in for create_subprocess_exec; record argv and env."""

    def __init__(self, process: FakeProcess | None = None,
                 error: OSError | None = None):
        self.process = process
        self.error = error
        self.argv: tuple = ()
        self.env: dict = {}

    async def __call__(self, *argv, **kwargs):
        self.argv = argv
        self.env = kwargs["env"]
        if self.error is not None:
            raise self.error
        return self.process


ROW = {"pid": 77, "application_name": "cmsEvaluationService 0",
       "client_port": 51234, "state": "active", "wait_event_type": "Lock",
       "wait_event": "transactionid", "backend_age_s": 3000.5,
       "xact_age_s": 40.0, "query_age_s": 39.0, "state_age_s": 39.0,
       "blocked_by": [88], "query": "UPDATE submission_results ..."}
# Another role's session, seen by a CMS role that is not a superuser.
HIDDEN = {"pid": 88, "application_name": None, "client_port": None,
          "state": None, "wait_event_type": None, "wait_event": None,
          "backend_age_s": None, "xact_age_s": None, "query_age_s": None,
          "state_age_s": None, "blocked_by": None, "query": None}


class TestEnvironment(unittest.TestCase):

    def test_from_a_url(self):
        env = environment("postgresql+psycopg2://cms%40x:p%40ss%3Aw@db:6543"
                          "/cmsdb?sslmode=require&connect_timeout=30")
        self.assertEqual(
            {key: env[key] for key in ("PGHOST", "PGPORT", "PGUSER",
                                       "PGPASSWORD", "PGDATABASE",
                                       "PGSSLMODE")},
            {"PGHOST": "db", "PGPORT": "6543", "PGUSER": "cms@x",
             "PGPASSWORD": "p@ss:w", "PGDATABASE": "cmsdb",
             "PGSSLMODE": "require"})
        self.assertEqual(env["PGAPPNAME"], "cmsWatchdog")
        self.assertEqual(env["PGCONNECT_TIMEOUT"], "3")
        self.assertEqual(env["PGOPTIONS"], "-c statement_timeout=3000")
        self.assertEqual(env["LC_ALL"], "C")
        self.assertEqual(env["PATH"], "/usr/bin:/bin")

    def test_a_socket_directory_and_no_password(self):
        env = environment("postgresql+psycopg2://cms@/cmsdb"
                          "?host=/var/run/postgresql")
        self.assertEqual(env["PGHOST"], "/var/run/postgresql")
        self.assertNotIn("PGPASSWORD", env)
        self.assertNotIn("PGPORT", env)

    def test_the_url_is_read_as_sqlalchemy_reads_it(self):
        # CMS opens the database with SQLAlchemy 2.0, whose password may
        # hold "/", "#", "?", ":", "+" and "=" unencoded (it ends at the
        # first "@"), and which decodes only the user and the password:
        # the gate must connect with what CMS connects with.
        urls = (
            "postgresql+psycopg2://cms:Ab/cd+ef=@db:5432/cmsdb",
            "postgresql+psycopg2://cms:Ab#cd@db:5432/cmsdb",
            "postgresql+psycopg2://cms:Ab?cd=ef@db:5432/cmsdb",
            "postgresql+psycopg2://cms:a:b:c@db/cmsdb",
            "postgresql+psycopg2://cms:a%40b%2Fc%23d@db/cmsdb",
            "postgresql+psycopg2://cms%3Ax:pw+=%@db/cmsdb",
            "postgresql://cms:pw@db",
            "postgresql://cms:pw@db:5433/",
            "postgresql://cms@db/cmsdb?sslmode=require",
            "postgresql://cms:pw@/cmsdb?host=/var/run/postgresql",
            "postgresql://cms:pw@[::1]:5433/cmsdb",
            "postgresql://cms:pw@[::1]/cmsdb?sslmode=disable&x=1",
            "postgresql://cms:@db/cmsdb",
            "postgresql://:pw@db/cmsdb",
            "postgresql://Db.Example:5432/cm%40sdb",
            "postgresql://",
        )
        for url in urls:
            with self.subTest(url=url):
                parsed = make_url(url)
                expected = {
                    name: str(value) for name, value in (
                        ("PGHOST", parsed.host), ("PGPORT", parsed.port),
                        ("PGUSER", parsed.username),
                        ("PGPASSWORD", parsed.password),
                        ("PGDATABASE", parsed.database),
                        ("PGSSLMODE", parsed.query.get("sslmode")))
                    if value}
                if "host" in parsed.query:
                    expected["PGHOST"] = parsed.query["host"]
                env = environment(url)
                self.assertEqual(
                    {name: value for name, value in env.items()
                     if name in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD",
                                 "PGDATABASE", "PGSSLMODE")},
                    expected)


class TestGate(unittest.IsolatedAsyncioTestCase):

    async def test_green_with_a_snapshot(self):
        run = FakeRun(FakeProcess(stdout=json.dumps([ROW]).encode()))
        result = await db_gate(environment("postgresql://u:pw@db/x"), 7,
                               run=run)
        self.assertTrue(result.green)
        self.assertEqual(result.backends[0]["blocked_by"], (88,))
        # -w: never ask for a password (a terminal prompt would hang the
        # gate until its kill).
        self.assertEqual(run.argv[:7],
                         ("psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                          "-w"))

    async def test_the_password_never_reaches_argv(self):
        run = FakeRun(FakeProcess(stdout=b"[]"))
        await db_gate(environment("postgresql://u:s%23cret@db/x"), 7,
                      run=run)
        self.assertFalse(any("cret" in argument for argument in run.argv))
        self.assertEqual(run.env["PGPASSWORD"], "s#cret")

    async def test_red_reasons(self):
        cases = (
            (b'psql: error: connection to server at "db" (127.0.0.1), port '
             b'5432 failed: Connection refused\n\tIs the server running?\n',
             "refused"),
            (b"psql: error: connection to server at \"db\", port 5432 "
             b"failed: timeout expired\n", "timeout"),
            (b"psql: error: ... failed: FATAL:  sorry, too many clients "
             b"already\n", "too many clients"),
            (b"psql: error: ... failed: FATAL:  remaining connection slots "
             b"are reserved for roles with the SUPERUSER attribute\n",
             "too many clients"),
            (b"psql: error: ... failed: FATAL:  password authentication "
             b"failed for user \"cms\"\n", "authentication"),
            (b"psql: error: connection to server at \"db\", port 5432 "
             b"failed: fe_sendauth: no password supplied\n",
             "authentication"),
            (b"ERROR:  canceling statement due to statement timeout\n",
             "timeout"),
            (b"psql: error: could not translate host name \"db\" to "
             b"address: Name or service not known\n", "other"),
        )
        for stderr, reason in cases:
            with self.subTest(reason=reason):
                result = await db_gate(
                    environment("postgresql://u:pw@db/x"), 7,
                    run=FakeRun(FakeProcess(2, b"", stderr)))
                self.assertFalse(result.green)
                self.assertEqual(result.reason, reason)
                self.assertEqual(result.detail,
                                 stderr.decode().splitlines()[0].strip())

    async def test_a_psql_that_hangs_is_killed(self):
        process = FakeProcess(hang=True)
        result = await db_gate(environment("postgresql://u:pw@db/x"), 0.1,
                               run=FakeRun(process))
        self.assertFalse(result.green)
        self.assertEqual(result.reason, "timeout")
        self.assertTrue(process.killed)

    async def test_a_psql_that_is_gone_when_killed_is_still_a_timeout(self):
        # It exited in the instant between the timeout and the kill: the
        # gate must not raise (the cycle gathers it with every probe).
        process = FakeProcess(hang=True, kill_error=ProcessLookupError())
        result = await db_gate(environment("postgresql://u:pw@db/x"), 0.1,
                               run=FakeRun(process))
        self.assertFalse(result.green)
        self.assertEqual(result.reason, "timeout")

    async def test_a_psql_that_does_not_die_is_given_up(self):
        process = FakeProcess(hang=True, wait_hang=True)
        result = await asyncio.wait_for(
            db_gate(environment("postgresql://u:pw@db/x"), 0.1,
                    run=FakeRun(process)), 5)
        self.assertFalse(result.green)
        self.assertEqual(result.reason, "timeout")
        self.assertTrue(process.killed)

    async def test_an_answer_too_deep_to_read_is_red(self):
        result = await db_gate(
            environment("postgresql://u:pw@db/x"), 7,
            run=FakeRun(FakeProcess(stdout=b"[" * 100000)))
        self.assertFalse(result.green)
        self.assertEqual(result.reason, "other")
        self.assertIn("did not parse", result.detail)

    async def test_an_answer_that_does_not_parse_is_red(self):
        result = await db_gate(environment("postgresql://u:pw@db/x"), 7,
                               run=FakeRun(FakeProcess(stdout=b"oops")))
        self.assertFalse(result.green)
        self.assertEqual(result.reason, "other")
        self.assertIn("did not parse", result.detail)

    async def test_a_missing_psql_is_red(self):
        result = await db_gate(
            environment("postgresql://u:pw@db/x"), 7,
            run=FakeRun(error=FileNotFoundError(2, "No such file", "psql")))
        self.assertFalse(result.green)
        self.assertIn("cannot run psql", result.detail)

    @unittest.skipIf(shutil.which("psql") is None, "psql is not installed")
    async def test_against_the_test_database(self):
        env = psql_environment(config.database.url, os.environ["PATH"], 3,
                               3000)
        result = await db_gate(env, 7)
        self.assertTrue(result.green, result.detail)
        # Its own backend (application_name cmsWatchdog) is left out.
        self.assertNotIn("cmsWatchdog",
                         [row["application_name"]
                          for row in result.backends])


class TestGateProcess(unittest.IsolatedAsyncioTestCase):
    """db_gate with a real child: a fake psql script that sleeps."""

    def setUp(self) -> None:
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        script = os.path.join(self.directory, "psql")
        with open(script, "w") as f:
            f.write('#!/bin/sh\necho $$ > "$FAKE_PSQL_PID"\nexec sleep 30\n')
        os.chmod(script, 0o755)
        self.pid_file = os.path.join(self.directory, "pid")
        self.env = {"PATH": self.directory + ":/usr/bin:/bin",
                    "FAKE_PSQL_PID": self.pid_file}

    async def started(self) -> int:
        """Wait for the script to run and return its pid."""
        for _ in range(100):
            if os.path.exists(self.pid_file):
                with open(self.pid_file) as f:
                    text = f.read().strip()
                if text:
                    self.addCleanup(self.stop, int(text))
                    return int(text)
            await asyncio.sleep(0.05)
        self.fail("the fake psql did not start")

    @staticmethod
    def stop(pid: int) -> None:
        """Make sure that a test that failed leaves no child behind."""
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    async def assert_gone(self, pid: int) -> None:
        """Wait for the process to be killed and reaped."""
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(0.05)
        self.fail("psql (pid %d) is still there" % pid)

    async def test_a_psql_that_hangs_is_gone_after_the_kill(self):
        task = asyncio.create_task(db_gate(self.env, 0.5))
        pid = await self.started()
        result = await task
        self.assertEqual(result.reason, "timeout")
        await self.assert_gone(pid)

    async def test_cancelling_the_gate_kills_psql(self):
        task = asyncio.create_task(db_gate(self.env, 30))
        pid = await self.started()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await self.assert_gone(pid)


class TestSnapshot(unittest.TestCase):

    def test_rows_and_hidden_fields(self):
        rows = parse_backends(json.dumps([ROW, HIDDEN]))
        self.assertEqual(rows[0]["backend_age_s"], 3000.5)
        self.assertEqual(rows[1], dict(HIDDEN, blocked_by=()))
        # The names match the policy's Backend, which main.py builds.
        self.assertEqual(Backend(**rows[1]).application_name, None)
        self.assertEqual(Backend(**rows[0]).blocked_by, (88,))
        self.assertEqual(list(BACKEND_FIELDS), list(ROW))

    def test_a_row_with_a_blocked_by_that_is_not_pids_is_dropped(self):
        for value in (5, "88", {"88": 1}, [1, "x"], [True], [[88]]):
            with self.subTest(value=value):
                text = json.dumps([dict(ROW, blocked_by=value), HIDDEN])
                self.assertEqual(
                    [row["pid"] for row in parse_backends(text)], [88])

    def test_what_is_not_a_snapshot(self):
        for text in ("", "{}", "[1]", '[{"application_name": "x"}]'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_backends(text)

    def test_a_row_with_an_age_that_is_not_finite_is_dropped(self):
        for value in ("NaN", "Infinity", "-Infinity", "1e999", '"39"',
                      "true"):
            with self.subTest(value=value):
                text = json.dumps([ROW, HIDDEN]).replace(
                    '"query_age_s": 39.0', '"query_age_s": %s' % value)
                self.assertEqual(
                    [row["pid"] for row in parse_backends(text)], [88])

    def test_classify(self):
        self.assertEqual(classify("Connection refused"), "refused")
        self.assertEqual(classify("weird"), "other")
        # The server answered by asking for a password (psql runs with
        # -w, so it does not prompt): an answered red, as a wrong one.
        self.assertEqual(
            classify('connection to server at "db" (127.0.0.1), port 5432 '
                     'failed: fe_sendauth: no password supplied'),
            "authentication")

    def test_the_reasons_postgresql_answers_are_the_policys(self):
        # A starved cycle still tracks a red gate with one of these
        # reasons (policy.GATE_ANSWERED_REASONS): they must be the ones
        # classify() gives for PostgreSQL's own answers, and only those.
        answered = {classify(text) for text in (
            "failed: Connection refused",
            "failed: FATAL:  sorry, too many clients already",
            "failed: FATAL:  password authentication failed for user",
            "failed: fe_sendauth: no password supplied")}
        self.assertEqual(answered, set(GATE_ANSWERED_REASONS))
        for text in ("failed: timeout expired", "could not translate"):
            with self.subTest(text=text):
                self.assertNotIn(classify(text), GATE_ANSWERED_REASONS)


class TestProcFacts(unittest.TestCase):

    def setUp(self):
        self.proc = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.proc)

    def write(self, path: str, text: str) -> None:
        full = os.path.join(self.proc, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)

    @staticmethod
    def stat(pid: int, state: str, utime: int, stime: int) -> str:
        # The comm field may hold spaces and parentheses.
        return ("%d (python3 (x)) %s 1 %d %d 0 -1 4194560 100 0 0 0 %d %d "
                "0 0 20 0 3 0 1000 0\n" % (pid, state, pid, pid, utime,
                                           stime))

    def test_a_fake_process(self):
        self.write("1234/stat", self.stat(1234, "S", 250, 50))
        self.write("1234/wchan", "do_epoll_wait")
        self.write("1234/task/1234/stat", self.stat(1234, "S", 200, 40))
        self.write("1234/task/1240/stat", self.stat(1240, "R", 50, 10))
        facts = proc_facts(1234, self.proc)
        self.assertEqual(facts["state"], "S")
        self.assertEqual(facts["wchan"], "do_epoll_wait")
        self.assertEqual(facts["threads"], {1234: "S", 1240: "R"})
        self.assertAlmostEqual(facts["cpu_s"], 300 / CLOCK_TICKS)
        self.assertAlmostEqual(thread_cpu_s(1234, 1240, self.proc),
                               60 / CLOCK_TICKS)

    def test_a_process_that_is_gone(self):
        self.assertIsNone(proc_facts(999, self.proc))
        self.assertIsNone(thread_cpu_s(999, 999, self.proc))

    def test_this_process(self):
        facts = proc_facts(os.getpid())
        self.assertIn(threading.get_native_id(), facts["threads"])
        self.assertIsNotNone(
            thread_cpu_s(os.getpid(), threading.get_native_id()))


if __name__ == "__main__":
    unittest.main()
