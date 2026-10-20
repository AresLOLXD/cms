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

"""Tests for cmsWatchdog's command parser and process sources."""

import os
import shutil
import socket
import socketserver
import tempfile
import threading
import time
import unittest
import xmlrpc.client
import xmlrpc.server
from unittest.mock import patch

from cms.watchdog.control import ProcessInfo, ProcSource, \
    SupervisorSource, parse_command, split_command
from cms.watchdog.policy import KIND_CLASS
from cms.watchdog.procfs import CLOCK_TICKS


class TestParseCommand(unittest.TestCase):

    def test_the_cms_programs(self):
        cases = (
            (["cmsContestWebServer", "2", "-c", "5"],
             ("ContestWebServer", 2)),
            (["cmsWorker", "0"], ("Worker", 0)),
            (["cmsEvaluationService", "0"], ("EvaluationService", 0)),
            (["/opt/venv/bin/python3", "/opt/venv/bin/cmsContestWebServer",
              "0", "-c", "1"], ("ContestWebServer", 0)),
            (["cmsWatchdog", "--mode", "observe"], ("Watchdog", None)),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(parse_command(argv), expected)

    def test_unmanaged_and_foreign_commands(self):
        self.assertEqual(parse_command(["cmsTelegramBot", "0", "-c", "3"]),
                         ("TelegramBot", 0))
        self.assertNotIn("TelegramBot", KIND_CLASS)
        for argv in ([], ["cmsuser"],
                     ["/home/cmsuser/cms-loader/node_modules/.bin/tsx",
                      "src/index.ts"],
                     ["bash", "-c",
                      "cmsProxyService 0 -c ALL >>/loadtest/run/start.log"]):
            with self.subTest(argv=argv):
                self.assertIsNone(parse_command(argv))

    def test_an_unparsable_command_is_unmanaged(self):
        self.assertEqual(split_command('cmsWorker "1'), [])
        self.assertIsNone(parse_command(split_command('cmsWorker "1')))
        self.assertEqual(split_command("cmsWorker 1"), ["cmsWorker", "1"])


class _Handler(xmlrpc.server.SimpleXMLRPCRequestHandler):
    # A unix socket refuses TCP_NODELAY, which the base class sets.
    disable_nagle_algorithm = False


class FakeSupervisor(socketserver.ThreadingMixIn,
                     socketserver.UnixStreamServer,
                     xmlrpc.server.SimpleXMLRPCDispatcher):
    """supervisord's XML-RPC interface on a unix socket, scripted."""

    daemon_threads = True

    def __init__(self, path: str, processes: list, configs: list):
        socketserver.UnixStreamServer.__init__(self, path, _Handler)
        xmlrpc.server.SimpleXMLRPCDispatcher.__init__(
            self, allow_none=True, encoding=None)
        self.logRequests = False
        self.processes = processes
        self.configs = configs
        self.calls: list[str] = []
        self.started: list[tuple[str, bool]] = []
        self.register_function(self._info, "supervisor.getAllProcessInfo")
        self.register_function(self._config,
                               "supervisor.getAllConfigInfo")
        self.register_function(self._start, "supervisor.startProcess")
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def _info(self):
        self.calls.append("getAllProcessInfo")
        return self.processes

    def _config(self):
        self.calls.append("getAllConfigInfo")
        return self.configs

    def _start(self, name, wait):
        if name.endswith("cmsscoringservice"):
            raise xmlrpc.client.Fault(60, "ALREADY_STARTED: " + name)
        if name.endswith("cmsbroken"):
            raise xmlrpc.client.Fault(10, "BAD_NAME: " + name)
        self.started.append((name, wait))
        return True


def process(name: str, state: str = "RUNNING", pid: int = 10,
            start: int = 1_800_000_000) -> dict:
    return {"name": name, "group": name, "statename": state, "pid": pid,
            "start": start, "stop": 0, "now": start + 100,
            "description": "", "spawnerr": "", "exitstatus": 0}


def config(name: str, command: str) -> dict:
    return {"name": name, "group": name, "command": command}


class TestSupervisorSource(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        self.path = os.path.join(directory, "supervisor.sock")
        self.server = FakeSupervisor(
            self.path,
            [process("cmsworker0"), process("cmscontestwebserver2", pid=11),
             process("cmsloader", pid=12),
             process("cmstelegrambot", pid=13),
             process("cmsadded", pid=14), process("cmsquote", pid=15),
             process("cmsscoringservice", "FATAL", 0, 0)],
            [config("cmsworker0", "cmsWorker 0"),
             config("cmscontestwebserver2", "cmsContestWebServer 2 -c 5"),
             config("cmsloader", "/home/cmsuser/cms-loader/node_modules"
                                 "/.bin/tsx src/index.ts"),
             config("cmstelegrambot", "cmsTelegramBot 0 -c 3"),
             config("cmsquote", 'cmsWorker "1'),
             config("cmsscoringservice", "cmsScoringService 0")])
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.source = SupervisorSource("unix://" + self.path, timeout=2)
        self.addCleanup(self.source.close)

    async def test_programs(self):
        programs = {info.name: info for info in await self.source.programs()}
        self.assertEqual(
            programs["cmsworker0"],
            ProcessInfo("cmsworker0", "Worker", 0, "RUNNING", 10,
                        1_800_000_000.0))
        self.assertEqual(
            (programs["cmscontestwebserver2"].kind,
             programs["cmscontestwebserver2"].shard),
            ("ContestWebServer", 2))
        self.assertIsNone(programs["cmsloader"].kind)
        self.assertEqual(programs["cmstelegrambot"].kind, "TelegramBot")
        fatal = programs["cmsscoringservice"]
        self.assertEqual((fatal.state, fatal.pid, fatal.start_wall),
                         ("FATAL", None, None))

    async def test_a_program_without_config_info_is_unmanaged(self):
        programs = {info.name: info for info in await self.source.programs()}
        self.assertEqual((programs["cmsadded"].kind,
                          programs["cmsadded"].shard), (None, None))
        self.assertIsNone(programs["cmsquote"].kind)

    async def test_the_commands_are_read_once_per_new_program(self):
        await self.source.programs()
        await self.source.programs()
        self.assertEqual(self.server.calls.count("getAllConfigInfo"), 1)
        self.server.processes.append(process("cmsworker1", pid=16))
        self.server.configs.append(config("cmsworker1", "cmsWorker 1"))
        programs = {info.name: info for info in await self.source.programs()}
        self.assertEqual(programs["cmsworker1"].kind, "Worker")
        self.assertEqual(self.server.calls.count("getAllConfigInfo"), 2)

    async def test_start(self):
        await self.source.programs()
        await self.source.start("cmsworker0")
        self.assertEqual(self.server.started,
                         [("cmsworker0:cmsworker0", False)])
        # ALREADY_STARTED: an operator started it first.
        await self.source.start("cmsscoringservice")
        with self.assertRaises(xmlrpc.client.Fault):
            await self.source.start("cmsbroken")

    async def test_a_supervisord_that_does_not_answer(self):
        hung = os.path.join(os.path.dirname(self.path), "hung.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(hung)
        listener.listen(1)
        self.addCleanup(listener.close)
        source = SupervisorSource("unix://" + hung, timeout=0.3)
        self.addCleanup(source.close)
        start = time.monotonic()
        with self.assertRaises(OSError):
            await source.programs()
        self.assertLess(time.monotonic() - start, 2)

    def test_the_url(self):
        with patch.dict(os.environ,
                        {"SUPERVISOR_SERVER_URL": "unix://" + self.path}):
            SupervisorSource().close()
        with self.assertRaises(ValueError):
            SupervisorSource("http://127.0.0.1:9001")


class TestProcSource(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.proc = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.proc)
        self.write("stat", "cpu 1 2 3 4\nbtime 1800000000\n")
        os.makedirs(os.path.join(self.proc, "self"))

    def write(self, path: str, data: str | bytes) -> None:
        full = os.path.join(self.proc, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data.encode() if isinstance(data, str) else data)

    def add(self, pid: int, argv: list[str], starttime: int = 1000) -> None:
        self.write("%d/cmdline" % pid, "\0".join(argv) + "\0")
        self.write("%d/stat" % pid,
                   "%d (x) S 1 %d %d 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 %d 0\n"
                   % (pid, pid, pid, starttime))

    async def test_interpreter_command_lines(self):
        self.add(100, ["/usr/bin/python3", "/usr/local/bin/cmsLogService",
                       "0"])
        self.add(101, ["bash", "-c",
                       "cmsProxyService 0 -c ALL >>/loadtest/run/start.log"])
        self.add(102, ["cmsWatchdog", "--mode", "observe"])
        os.makedirs(os.path.join(self.proc, "103"))
        [log_service] = await ProcSource(self.proc).programs()
        self.assertEqual(
            log_service,
            ProcessInfo("cmsLogService 0", "LogService", 0, "RUNNING", 100,
                        1_800_000_000 + 1000 / CLOCK_TICKS))

    async def test_a_program_that_is_gone_is_absent(self):
        self.add(100, ["cmsWorker", "0"])
        source = ProcSource(self.proc)
        self.assertEqual([info.state for info in await source.programs()],
                         ["RUNNING"])
        shutil.rmtree(os.path.join(self.proc, "100"))
        [absent] = await source.programs()
        self.assertEqual((absent.name, absent.state, absent.pid),
                         ("cmsWorker 0", "ABSENT", None))

    async def test_the_oldest_process_of_a_label_wins(self):
        self.add(200, ["cmsWorker", "1"], starttime=5000)
        self.add(150, ["cmsWorker", "1"], starttime=900)
        [worker] = await ProcSource(self.proc).programs()
        self.assertEqual(worker.pid, 150)
        self.assertFalse(ProcSource.can_act)


if __name__ == "__main__":
    unittest.main()
