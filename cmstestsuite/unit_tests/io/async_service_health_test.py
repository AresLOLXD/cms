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

"""Tests for AsyncService.health() and the USR1 stack dump (#38)."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from cms.conf import Address
from cms.io.async_service import AsyncService
from cms.io.service import Service
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


class QueueService(AsyncService):
    """An AsyncService with a field of its own in health()."""

    def _health_extra(self) -> dict:
        return {"queue": 7}


def _read_line(stream, timeout: float) -> str:
    """Return the next line of stream, or "" after timeout seconds."""
    lines: list[str] = []
    reader = threading.Thread(
        target=lambda: lines.append(stream.readline()), daemon=True)
    reader.start()
    reader.join(timeout)
    return lines[0] if lines else ""


@patch("cms.io.async_service.get_service_address",
       return_value=Address("127.0.0.1", 0))
class TestHealth(
    ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase,
):

    async def test_health_reports_the_pid_the_uptime_and_the_sections(
            self, _address):
        service = QueueService(0)
        service._busy.start("score")
        self.addCleanup(service._busy.stop)

        answer = service.health()

        self.assertEqual(answer["pid"], os.getpid())
        self.assertGreaterEqual(answer["uptime_s"], 0)
        self.assertEqual([entry["what"] for entry in answer["busy"]],
                         ["score"])
        self.assertEqual(answer["queue"], 7)

    async def test_the_default_extra_fields_are_none(self, _address):
        answer = AsyncService(0).health()
        self.assertEqual(set(answer), {"pid", "uptime_s", "busy"})
        self.assertEqual(answer["busy"], [])

    async def test_health_is_an_rpc_method_answered_by_the_loop(
            self, _address):
        service = QueueService(0)
        run_task = asyncio.create_task(service._async_run())
        self.addAsyncCleanup(self._stop, service, run_task)
        await asyncio.sleep(0.05)
        port = service._server.sockets[0].getsockname()[1]

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            request = {"__id": "1", "__method": "health", "__data": {}}
            writer.write(json.dumps(request).encode() + b"\r\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readuntil(b"\r\n"), 5)
        finally:
            writer.close()

        response = json.loads(line)
        self.assertIsNone(response["__error"])
        self.assertEqual(response["__data"]["pid"], os.getpid())
        self.assertEqual(response["__data"]["queue"], 7)

    async def test_building_a_service_registers_no_stack_dump(
            self, _address):
        with patch("cms.io.async_service.register_stack_dump") as register:
            AsyncService(0)
        register.assert_not_called()

    async def test_running_a_service_registers_the_stack_dump(
            self, _address):
        service = AsyncService(0)
        with patch("cms.io.async_service.register_stack_dump") as register:
            run_task = asyncio.create_task(service._async_run())
            await asyncio.sleep(0.05)
            await self._stop(service, run_task)
        register.assert_called_once_with()

    @staticmethod
    async def _stop(service: AsyncService, run_task: asyncio.Task):
        if not run_task.done():
            service.exit()
            await asyncio.wait_for(run_task, timeout=5)


class TestGeventServiceStackDump(unittest.TestCase):

    def test_run_registers_the_stack_dump(self):
        # Not a real Service: building one installs signal handlers in
        # the test process. run() only needs these attributes.
        fake = MagicMock()
        fake._my_coord = ("Worker", 0)
        with patch("cms.io.service.register_stack_dump") as register, \
                patch("cms.io.service.config") as config:
            config.global_.backdoor = False
            self.assertTrue(Service.run(fake))
        register.assert_called_once_with()
        fake.rpc_server.serve_forever.assert_called_once_with()


class TestWebServiceStackDump(unittest.TestCase):
    """A real WebService, run in its own process, survives USR1."""

    def test_usr1_dumps_the_stacks_of_a_running_web_service(self):
        script = textwrap.dedent("""\
            from unittest.mock import patch
            from cms.conf import Address
            from cms.io.web_service import WebService

            with patch("cms.io.async_service.get_service_address",
                       return_value=Address("127.0.0.1", 0)):
                service = WebService(0, [], {}, shard=0,
                                     listen_address="127.0.0.1")
            service._call_when_running(
                lambda: print("running", flush=True))
            service.run()
            """)
        process = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(_read_line(process.stdout, 30).strip(),
                             "running")
            process.send_signal(signal.SIGUSR1)
            time.sleep(1)
            self.assertIsNone(process.poll())
            process.send_signal(signal.SIGTERM)
            _, stderr = process.communicate(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        self.assertIn("Current thread", stderr)


if __name__ == "__main__":
    unittest.main()
