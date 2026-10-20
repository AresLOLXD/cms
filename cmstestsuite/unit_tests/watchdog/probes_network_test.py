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

"""Tests for cmsWatchdog's HTTP, ranking and RPC probes."""

import asyncio
import http.server
import json
import socket
import ssl
import threading
import unittest
from unittest.mock import patch

from cms.conf import Address
from cms.io.async_rpc import MAX_MESSAGE_SIZE as CMS_MAX_MESSAGE_SIZE
from cms.io.async_service import AsyncService
from cms.watchdog.probes import MAX_MESSAGE_SIZE, RpcProbe, describe, \
    http_probe, probe_host, ranking_target
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


class _Handler(http.server.BaseHTTPRequestHandler):
    """200 for the favicon, 401 for /private, the code of /status/<code>
    and 404 otherwise."""

    def do_GET(self):
        if self.path.startswith("/status/"):
            code = int(self.path[len("/status/"):])
        else:
            code = {"/static/favicon.ico": 200, "/private": 401}.get(
                self.path, 404)
        self.send_response(code)
        self.end_headers()

    def log_message(self, *args):
        pass


class HttpServer:
    """A threaded HTTP server on a free port of host."""

    def __init__(self, host: str = "127.0.0.1"):
        self.server = http.server.ThreadingHTTPServer((host, 0), _Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def free_port() -> int:
    """Return a loopback port nothing listens on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestHttpProbe(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.http = HttpServer()
        self.addCleanup(self.http.stop)

    async def test_a_200_passes(self):
        result = await http_probe("127.0.0.1", self.http.port,
                                  "/static/favicon.ico", 5)
        self.assertTrue(result.ok)
        self.assertGreater(result.latency_s, 0)

    async def test_a_404_fails(self):
        result = await http_probe("127.0.0.1", self.http.port,
                                  "/static/missing.ico", 5)
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "HTTP status 404")

    async def test_2xx_and_3xx_pass_and_the_other_statuses_fail(self):
        for status, passes in ((199, False), (200, True), (299, True),
                               (300, True), (399, True), (400, False),
                               (503, False)):
            with self.subTest(status=status):
                result = await http_probe("127.0.0.1", self.http.port,
                                          "/status/%d" % status, 5)
                self.assertEqual(result.ok, passes)
                if not passes:
                    self.assertEqual(result.error,
                                     "HTTP status %d" % status)

    async def test_what_is_not_a_status_line_fails_even_a_ranking(self):
        async def answer_with(reply: bytes, reader, writer) -> None:
            await reader.readline()
            writer.write(reply)
            await writer.drain()
            writer.close()

        for reply in (b"garbage\r\n", b"HTTP/1.1 OK\r\n", b""):
            with self.subTest(reply=reply):
                server = await asyncio.start_server(
                    lambda reader, writer: answer_with(reply, reader,
                                                       writer),
                    "127.0.0.1", 0)
                async with server:
                    result = await http_probe(
                        "127.0.0.1", server.sockets[0].getsockname()[1],
                        "/", 5, any_status=True)
                self.assertFalse(result.ok)
                self.assertEqual(result.error, "no HTTP status line")

    async def test_any_status_line_passes_a_ranking(self):
        result = await http_probe("127.0.0.1", self.http.port, "/private",
                                  5, any_status=True)
        self.assertTrue(result.ok)

    async def test_a_server_that_never_answers_times_out(self):
        with socket.create_server(("127.0.0.1", 0)) as listener:
            result = await http_probe("127.0.0.1",
                                      listener.getsockname()[1], "/", 0.3)
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "timed out")

    async def test_a_refused_connection_fails(self):
        result = await http_probe("127.0.0.1", free_port(), "/", 5)
        self.assertFalse(result.ok)
        self.assertIn("refused", result.error.lower())

    async def test_a_wildcard_listen_address_is_probed_on_loopback(self):
        self.assertEqual(probe_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(probe_host(""), "127.0.0.1")
        self.assertEqual(probe_host("127.0.0.2"), "127.0.0.2")
        wildcard = HttpServer("0.0.0.0")
        self.addCleanup(wildcard.stop)
        result = await http_probe(probe_host("0.0.0.0"), wildcard.port,
                                  "/static/favicon.ico", 5)
        self.assertTrue(result.ok)

    async def test_a_refusal_says_refused_even_for_a_dual_stack_name(self):
        # "localhost" may resolve to ::1 and 127.0.0.1: asyncio then
        # joins the two refusals into one error without an errno.
        result = await http_probe("localhost", free_port(), "/", 5)
        self.assertFalse(result.ok)
        self.assertIn("refused", result.error.lower())

    async def test_a_failed_tls_handshake_keeps_the_ssl_text(self):
        result = await http_probe("127.0.0.1", self.http.port, "/", 5,
                                  tls=True)
        self.assertFalse(result.ok)
        self.assertIn("[SSL", result.error)
        self.assertNotIn("not permitted", result.error)


class TestDescribe(unittest.TestCase):

    def test_a_name_that_does_not_resolve_keeps_its_own_text(self):
        error = socket.gaierror(-2, "Name or service not known")
        self.assertEqual(describe(error),
                         "[Errno -2] Name or service not known")

    def test_a_tls_error_keeps_its_own_text(self):
        text = ("[SSL: WRONG_VERSION_NUMBER] wrong version number "
                "(_ssl.c:1000)")
        self.assertEqual(describe(ssl.SSLError(1, text)), text)

    def test_the_refusals_of_every_address_say_refused(self):
        error = OSError(
            "Multiple exceptions: [Errno 111] Connect call failed "
            "('::1', 8890, 0, 0), [Errno 111] Connect call failed "
            "('127.0.0.1', 8890)")
        self.assertTrue(describe(error).startswith(
            "Connection refused: Multiple exceptions: "))

    def test_different_failures_of_the_addresses_are_shown_as_they_are(self):
        text = ("Multiple exceptions: [Errno 101] Network is unreachable "
                "('::1', 8890, 0, 0), [Errno 111] Connect call failed "
                "('127.0.0.1', 8890)")
        self.assertEqual(describe(OSError(text)), text)

    def test_a_plain_refusal_names_its_errno(self):
        error = ConnectionRefusedError(111, "Connect call failed "
                                       "('127.0.0.1', 8890)")
        self.assertEqual(describe(error), "Connection refused: [Errno 111] "
                         "Connect call failed ('127.0.0.1', 8890)")


class TestRpcLimits(unittest.TestCase):

    def test_the_probe_reads_as_much_as_a_cms_service_sends(self):
        self.assertEqual(MAX_MESSAGE_SIZE, CMS_MAX_MESSAGE_SIZE)


class TestRankingTarget(unittest.TestCase):

    def test_the_address_and_no_credentials(self):
        target = ranking_target("http://usern4me:passw0rd@localhost:8890/")
        self.assertEqual((target.host, target.port, target.tls),
                         ("localhost", 8890, False))
        self.assertEqual(target.visible, "http://localhost:8890/")
        self.assertNotIn("passw0rd", repr(target))

    def test_default_ports_and_ipv6(self):
        https = ranking_target("https://u:p@rws.example.com/")
        self.assertEqual((https.port, https.tls, https.visible),
                         (443, True, "https://rws.example.com:443/"))
        self.assertEqual(ranking_target("http://rws.example.com").port, 80)
        self.assertEqual(ranking_target("http://[::1]:8890/").visible,
                         "http://[::1]:8890/")

    def test_what_is_not_a_ranking(self):
        for url in ("", "ftp://host/", "http://host:notaport/",
                    "http:///nohost"):
            with self.subTest(url=url):
                self.assertIsNone(ranking_target(url))


class FakeService:
    """An asyncio server that speaks CMS's RPC wire format.

    The method "fail" gets an error answer; every other method gets
    {"method": its name}, after delay seconds. The fault, when set, is
    the name of a key of FAULTS: the answer it turns, or "close" to close
    the connection without answering.

    """

    FAULTS = {
        "wrong_id": lambda answer: {**answer, "__id": "another-request"},
        "missing_fields": lambda answer: {"__id": answer["__id"]},
        "not_an_object": lambda answer: [answer],
    }

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.fault = ""
        self.connections = 0
        self.requests: list[dict] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.port: int | None = None

    async def start(self, port: int = 0):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1",
                                                 port)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        for writer in self.writers:
            writer.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer):
        self.connections += 1
        self.writers.append(writer)
        try:
            while line := await reader.readline():
                request = json.loads(line)
                self.requests.append(request)
                await asyncio.sleep(self.delay)
                answer = {"__id": request["__id"],
                          "__data": {"method": request["__method"]},
                          "__error": None}
                if request["__method"] == "fail":
                    answer.update(__data=None, __error="boom\nTraceback")
                if self.fault == "close":
                    break
                if self.fault:
                    answer = self.FAULTS[self.fault](answer)
                writer.write(json.dumps(answer).encode() + b"\r\n")
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()


class TestRpcProbe(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.service = FakeService()
        await self.service.start()
        self.probe = RpcProbe("127.0.0.1", self.service.port, 3.0)
        self.addCleanup(self.probe.close)

    async def asyncTearDown(self):
        await self.service.stop()

    async def test_one_connection_serves_every_cycle(self):
        for _ in range(3):
            result = await self.probe.call("health")
            self.assertTrue(result.ok)
            self.assertEqual(result.data, {"method": "health"})
        self.assertEqual(self.service.connections, 1)

    async def test_an_error_answer_is_a_miss_and_closes(self):
        result = await self.probe.call("fail")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "boom Traceback")
        self.assertFalse(self.probe.connected)

    async def test_an_answer_that_is_not_whole_is_a_miss_and_closes(self):
        for fault in ("missing_fields", "not_an_object"):
            with self.subTest(fault=fault):
                self.service.fault = fault
                result = await self.probe.call("health")
                self.assertFalse(result.ok)
                self.assertEqual(result.error, "a bad answer")
                self.assertFalse(self.probe.connected)

    async def test_an_answer_to_another_request_is_a_miss_and_closes(self):
        self.service.fault = "wrong_id"
        result = await self.probe.call("health")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "an answer to another request")
        self.assertFalse(self.probe.connected)

    async def test_a_fresh_connection_closed_unanswered_is_not_retried(self):
        self.service.fault = "close"
        result = await self.probe.call("health")
        self.assertFalse(result.ok)
        self.assertEqual(result.error,
                         "the service closed the connection before "
                         "answering")
        self.assertFalse(self.probe.connected)
        self.assertEqual(self.service.connections, 1)

    async def test_a_reused_connection_and_its_retry_both_closed_is_a_miss(
            self):
        self.assertTrue((await self.probe.call("health")).ok)
        self.service.fault = "close"
        result = await self.probe.call("health")
        self.assertFalse(result.ok)
        self.assertEqual(result.error,
                         "the service closed the connection before "
                         "answering")
        self.assertFalse(self.probe.connected)
        self.assertEqual(self.service.connections, 2)
        self.service.fault = ""
        self.assertTrue((await self.probe.call("health")).ok)

    async def test_after_a_timeout_a_late_answer_is_never_read(self):
        self.service.delay = 0.5
        self.probe.timeout = 0.2
        result = await self.probe.call("health")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "timed out")
        self.assertFalse(self.probe.connected)
        self.service.delay = 0.0
        self.probe.timeout = 3.0
        result = await self.probe.call("workers_status")
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"method": "workers_status"})
        self.assertEqual(self.service.connections, 2)

    async def test_concurrent_calls_are_served_one_after_the_other(self):
        self.service.delay = 0.1
        first, second = await asyncio.gather(
            self.probe.call("first"), self.probe.call("second"))
        self.assertTrue(first.ok)
        self.assertEqual(first.data, {"method": "first"})
        self.assertTrue(second.ok)
        self.assertEqual(second.data, {"method": "second"})
        self.service.delay = 0.0
        self.assertTrue((await self.probe.call("third")).ok)
        self.assertEqual(self.service.connections, 1)

    async def test_a_waiter_that_times_out_spares_the_call_it_waits_for(
            self):
        self.service.delay = 0.5
        holder = asyncio.create_task(self.probe.call("holder"))
        await asyncio.sleep(0.1)
        self.probe.timeout = 0.2
        waiter = await self.probe.call("waiter")
        self.assertFalse(waiter.ok)
        self.assertEqual(waiter.error, "timed out")
        held = await holder
        self.assertTrue(held.ok)
        self.assertEqual(held.data, {"method": "holder"})
        self.assertEqual(self.service.connections, 1)

    async def test_a_restarted_service_is_reached_at_once(self):
        self.assertTrue((await self.probe.call("health")).ok)
        port = self.service.port
        await self.service.stop()
        self.service = FakeService()
        await self.service.start(port)
        result = await self.probe.call("health")
        self.assertTrue(result.ok)
        self.assertEqual(self.service.connections, 1)

    async def test_a_failure_of_the_fresh_connection_is_a_miss(self):
        self.assertTrue((await self.probe.call("health")).ok)
        await self.service.stop()
        result = await self.probe.call("health")
        self.assertFalse(result.ok)
        # Restart it for asyncTearDown.
        await self.service.start()


@patch("cms.io.async_service.get_service_address",
       return_value=Address("127.0.0.1", 0))
class TestAgainstARealService(
    ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase,
):

    async def test_later_cycles_add_no_info_record(self, _address):
        service = AsyncService(0)
        run_task = asyncio.create_task(service._async_run())
        await asyncio.sleep(0.05)
        port = service._server.sockets[0].getsockname()[1]
        probe = RpcProbe("127.0.0.1", port, 3.0)
        try:
            first = await probe.call("health")
            self.assertTrue(first.ok)
            self.assertIn("pid", first.data)
            with self.assertNoLogs("cms.io.async_rpc", level="INFO"):
                for _ in range(3):
                    self.assertTrue((await probe.call("health")).ok)
        finally:
            probe.close()
            service.exit()
            await asyncio.wait_for(run_task, timeout=5)


if __name__ == "__main__":
    unittest.main()
