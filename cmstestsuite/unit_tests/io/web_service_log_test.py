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
from unittest.mock import patch

import tornado.web

from cms.conf import Address
from cms.io.web_service import WebService
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


class HelloHandler(tornado.web.RequestHandler):
    def get(self):
        self.write("hello")


class TestRequestLog(
    ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase,
):

    @patch("cms.io.async_service.get_service_address",
           return_value=Address("127.0.0.1", 0))
    async def asyncSetUp(self, _address):
        self.service = WebService(
            listen_port=0, handlers=[(r"/", HelloHandler)],
            parameters={"static_files": [("cms.server", "static")]},
            shard=0, listen_address="127.0.0.1")
        self.run_task = asyncio.create_task(self.service._async_run())
        await asyncio.sleep(0.05)
        self.addAsyncCleanup(self._stop)
        self.port = self.service._http_server_sockets[0].getsockname()[1]

    async def _stop(self):
        self.service.exit()
        await asyncio.wait_for(self.run_task, timeout=5)

    async def _request(self, method: str, path: str) -> logging.LogRecord:
        """Send one request; return the access log record it produced."""
        with self.assertLogs("tornado.access", level="DEBUG") as logs:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", self.port)
            try:
                writer.write(("%s %s HTTP/1.0\r\n\r\n"
                              % (method, path)).encode())
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
