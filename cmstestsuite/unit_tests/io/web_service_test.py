#!/usr/bin/env python3

"""Tests for cms.io.web_service.WebService."""

import asyncio
import unittest
from unittest.mock import patch

import tornado.web

from cms.conf import Address
from cms.io.web_service import WebService
from cmstestsuite.unit_tests.servicelogmixin import ServiceLoggingIsolationMixin


class EchoHandler(tornado.web.RequestHandler):
    def get(self):
        self.write("hello from %s" % type(self.application.service).__name__)


class WebServiceTest(ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def asyncSetUp(self, mock_get_address):
        mock_get_address.return_value = Address("127.0.0.1", 0)
        self.service = WebService(
            listen_port=0, handlers=[(r"/", EchoHandler)],
            parameters={}, shard=0, listen_address="127.0.0.1")
        self.run_task = asyncio.create_task(self.service._async_run())
        await asyncio.sleep(0.05)
        self.addAsyncCleanup(self._stop_service)

    async def _stop_service(self):
        self.service.exit()
        await asyncio.wait_for(self.run_task, timeout=5)

    async def test_serves_a_real_request(self):
        port = self.service._http_server_sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"200 OK", response)
        self.assertIn(b"hello from WebService", response)

    async def test_application_service_backreference(self):
        self.assertIs(self.service.application.service, self.service)

    async def test_exit_stops_accepting_new_connections(self):
        port = self.service._http_server_sockets[0].getsockname()[1]
        self.service.exit()
        await asyncio.wait_for(self.run_task, timeout=5)
        with self.assertRaises(ConnectionRefusedError):
            await asyncio.open_connection("127.0.0.1", port)

    async def test_exit_lets_an_in_flight_request_finish(self):
        # A request already being handled when exit() is called should
        # still get its response, not be cut off mid-flight.
        port = self.service._http_server_sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            self.service.exit()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"200 OK", response)
        await asyncio.wait_for(self.run_task, timeout=5)


if __name__ == "__main__":
    unittest.main()
