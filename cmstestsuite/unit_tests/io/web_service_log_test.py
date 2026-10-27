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

"""Tests for the request log of WebService: probes at DEBUG (#38)."""

import asyncio
import logging
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import tornado.httputil
import tornado.web

from cms.conf import Address
from cms.io.web_service import WebService, log_request, resolve_remote_ip
from cms.server.util import CommonRequestHandler
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


class HelloHandler(tornado.web.RequestHandler):
    def get(self):
        self.write("hello")


class QuietHandler(tornado.web.RequestHandler):
    quiet_access_log = True

    def get(self):
        self.write("polled")


class TestRequestLog(
    ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase,
):

    @patch("cms.io.async_service.get_service_address",
           return_value=Address("127.0.0.1", 0))
    async def asyncSetUp(self, _address):
        self.service = WebService(
            listen_port=0,
            handlers=[(r"/", HelloHandler), (r"/quiet", QuietHandler)],
            parameters={"static_files": [("cms.server", "static")]},
            shard=0, listen_address="127.0.0.1")
        self.run_task = asyncio.create_task(self.service._async_run())
        await asyncio.sleep(0.05)
        self.addAsyncCleanup(self._stop)
        self.port = self.service._http_server_sockets[0].getsockname()[1]

    async def _stop(self):
        self.service.exit()
        await asyncio.wait_for(self.run_task, timeout=5)

    async def _request(
        self, method: str, path: str, forwarded_for: str | None = None,
    ) -> logging.LogRecord:
        """Send one request; return the access log record it produced."""
        header = ("X-Forwarded-For: %s\r\n" % forwarded_for
                  if forwarded_for else "")
        with self.assertLogs("tornado.access", level="DEBUG") as logs:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", self.port)
            try:
                writer.write(("%s %s HTTP/1.0\r\n%s\r\n"
                              % (method, path, header)).encode())
                await writer.drain()
                await asyncio.wait_for(reader.read(), timeout=5)
            finally:
                writer.close()
            # The log line is written once the response is finished.
            await asyncio.sleep(0.05)
        self.assertEqual(len(logs.records), 1)
        return logs.records[0]

    async def test_a_successful_favicon_probe_is_logged_at_debug(self):
        record = await self._request("GET", "/static/favicon.ico")
        self.assertEqual(record.levelno, logging.DEBUG)
        self.assertRegex(
            record.getMessage(),
            r"^200 GET /static/favicon\.ico \(127\.0\.0\.1\) "
            r"[0-9]+\.[0-9]{2}ms$")

    async def test_another_path_is_logged_at_info(self):
        record = await self._request("GET", "/")
        self.assertEqual(record.levelno, logging.INFO)
        self.assertTrue(re.match(r"^200 GET / ", record.getMessage()))

    async def test_a_favicon_head_is_logged_at_info(self):
        record = await self._request("HEAD", "/static/favicon.ico")
        self.assertEqual(record.levelno, logging.INFO)

    async def test_a_failed_favicon_is_logged_as_today(self):
        with patch("cms.io.static_handler.MultiLocationStaticFileHandler"
                   ".get", side_effect=tornado.web.HTTPError(404)):
            record = await self._request("GET", "/static/favicon.ico")
        self.assertEqual(record.levelno, logging.WARNING)
        self.assertTrue(record.getMessage().startswith("404 GET "))

    async def test_a_quiet_handler_is_logged_at_debug_when_it_succeeds(self):
        record = await self._request("GET", "/quiet")
        self.assertEqual(record.levelno, logging.DEBUG)
        self.assertTrue(re.match(r"^200 GET /quiet ", record.getMessage()))

    async def test_a_failed_quiet_handler_is_logged_as_today(self):
        with patch.object(QuietHandler, "get",
                          side_effect=tornado.web.HTTPError(404)):
            record = await self._request("GET", "/quiet")
        self.assertEqual(record.levelno, logging.WARNING)

    async def test_an_unrouted_request_logs_the_resolved_address(self):
        # Tornado's own 404 handler is not a CommonRequestHandler, so it
        # never resolves the address; the log does. The client forged
        # 10.9.9.9 and the one trusted proxy appended the real address.
        self.service.num_proxies_used = 1
        record = await self._request(
            "GET", "/no/such/route", "10.9.9.9, 203.0.113.7")
        self.assertEqual(record.levelno, logging.WARNING)
        self.assertRegex(record.getMessage(),
                         r"^404 GET /no/such/route \(203\.0\.113\.7\) ")

    async def test_a_static_file_request_logs_the_resolved_address(self):
        self.service.num_proxies_used = 1
        record = await self._request(
            "GET", "/static/favicon.ico", "10.9.9.9, 203.0.113.7")
        self.assertRegex(record.getMessage(), r"\(203\.0\.113\.7\) ")

    async def test_an_unrouted_request_keeps_the_peer_without_a_proxy(self):
        # With no trusted proxy the header is not believed at all.
        record = await self._request(
            "GET", "/no/such/route", "10.9.9.9, 203.0.113.7")
        self.assertRegex(record.getMessage(), r"\(127\.0\.0\.1\) ")

    async def test_an_unrouted_request_with_a_short_header_keeps_the_peer(
            self):
        self.service.num_proxies_used = 2
        record = await self._request("GET", "/no/such/route", "10.9.9.9")
        self.assertRegex(record.getMessage(), r"\(127\.0\.0\.1\) ")


def make_finished_handler(handler_class, remote_ip, forwarded_for, application):
    """Build a handler of handler_class that answered a 404, without a socket.

    remote_ip: the address of the request when the log is written.
    forwarded_for: the X-Forwarded-For header of the request.
    application: what the handler says its application is.

    """
    request = tornado.httputil.HTTPServerRequest(
        method="GET", uri="/x",
        headers=tornado.httputil.HTTPHeaders({"X-Forwarded-For": forwarded_for}))
    request.remote_ip = remote_ip
    handler = handler_class.__new__(handler_class)
    handler.request = request
    handler.application = application
    handler._status_code = 404
    return handler


class TestLogRequestResolution(
    ServiceLoggingIsolationMixin, unittest.TestCase,
):

    def logged_address(self, handler) -> str:
        with self.assertLogs("tornado.access", level="DEBUG") as logs:
            log_request(handler)
        return re.search(r"\(([^)]*)\)", logs.records[0].getMessage())[1]

    def test_a_common_handler_is_not_resolved_again(self):
        # CommonRequestHandler.__init__ already resolved its address, so
        # whatever it left in request.remote_ip is what the log prints,
        # even where resolving the header again would say otherwise.
        application = SimpleNamespace(
            service=SimpleNamespace(num_proxies_used=1))
        for remote_ip, header, num_proxies_used in (
                ("1.1.1.1", "9.9.9.9, 5.6.7.8", 1),
                ("172.19.0.1", "9.9.9.9", 2)):
            with self.subTest(header=header):
                application.service.num_proxies_used = num_proxies_used
                handler = make_finished_handler(
                    CommonRequestHandler, remote_ip, header, application)
                self.assertEqual(self.logged_address(handler), remote_ip)

    def test_a_handler_without_a_service_keeps_the_peer(self):
        for application in (SimpleNamespace(),
                            SimpleNamespace(service=SimpleNamespace())):
            handler = make_finished_handler(
                tornado.web.ErrorHandler, "172.19.0.1",
                "10.9.9.9, 203.0.113.7", application)
            self.assertEqual(self.logged_address(handler), "172.19.0.1")

    def test_resolving_twice_gives_the_same_address(self):
        # The RPC endpoint resolves the address itself, in post(), and the
        # log resolves it again; that is harmless because both read the
        # header, not the address the other left.
        for header, num_proxies_used in (
                ("9.9.9.9, 5.6.7.8", 1), ("9.9.9.9", 2), ("", 1)):
            once = resolve_remote_ip(header, "172.19.0.1", num_proxies_used)
            twice = resolve_remote_ip(header, once, num_proxies_used)
            self.assertEqual(once, twice)


class TestCustomLogFunction(
    ServiceLoggingIsolationMixin, unittest.TestCase,
):

    @patch("cms.io.async_service.get_service_address",
           return_value=Address("127.0.0.1", 0))
    def test_a_subclass_may_give_its_own(self, _address):
        def custom(handler):
            pass

        service = WebService(
            listen_port=0, handlers=[], parameters={"log_function": custom},
            shard=0, listen_address="127.0.0.1")
        self.assertIs(service.application.settings["log_function"], custom)


if __name__ == "__main__":
    unittest.main()
