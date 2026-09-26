#!/usr/bin/env python3

"""Tests for cms.io.web_rpc.RPCHandler."""

import asyncio
import json
import socket
import time
import unittest
from unittest.mock import patch

import tornado.httpserver
import tornado.netutil
import tornado.web

from cms.conf import Address, ServiceCoord
from cms.io.async_service import AsyncService
from cms.io.rpc import rpc_method
from cms.io.web_rpc import RPCHandler
from cmstestsuite.unit_tests.servicelogmixin import ServiceLoggingIsolationMixin


class EchoingRemoteService(AsyncService):
    @rpc_method
    def echo(self, value: str) -> str:
        return value

    @rpc_method
    def boom(self):
        raise ValueError("deliberate failure")

    @rpc_method
    async def sleep_forever(self):
        await asyncio.sleep(10)


class RPCHandlerTest(ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    @patch("cms.io.async_rpc.get_service_address")
    async def asyncSetUp(self, mock_rpc_address, mock_service_address):
        # Reserve a port up front (rather than reading it back off the
        # server socket after the fact): EchoingRemoteService's own
        # __init__ already needs to resolve its own address via the
        # mock, before the server socket exists.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            remote_address = Address("127.0.0.1", probe.getsockname()[1])

        def address_of(coord):
            if coord.name == "EchoingRemoteService":
                return remote_address
            return Address("127.0.0.1", 0)

        mock_service_address.side_effect = address_of
        mock_rpc_address.side_effect = address_of

        self.remote = EchoingRemoteService(shard=0)
        self.remote_task = asyncio.create_task(self.remote._async_run())
        await asyncio.sleep(0.05)

        self.frontend = AsyncService(shard=0)
        self.frontend_task = asyncio.create_task(self.frontend._async_run())
        await asyncio.sleep(0.05)
        self.frontend.connect_to(ServiceCoord("EchoingRemoteService", 0))
        await asyncio.sleep(0.1)  # let the connection establish

        handler_spec = RPCHandler.make_route(r"/rpc/(.*)/(.*)/(.*)", None)
        application = tornado.web.Application([handler_spec])
        application.service = self.frontend
        self.server = tornado.httpserver.HTTPServer(application)
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        self.server.add_sockets(sockets)
        self.port = sockets[0].getsockname()[1]

        self.addAsyncCleanup(self._teardown)

    async def _teardown(self):
        self.server.stop()
        await self.server.close_all_connections()
        self.remote.exit()
        self.frontend.exit()
        await asyncio.wait_for(self.remote_task, timeout=5)
        await asyncio.wait_for(self.frontend_task, timeout=5)

    async def _raw_post(self, path, headers, payload):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            header_lines = "".join(
                "%s: %s\r\n" % (key, value) for key, value in headers.items())
            request = (
                "POST %s HTTP/1.1\r\nHost: localhost\r\n%s"
                "Content-Length: %d\r\n\r\n" % (path, header_lines, len(payload))
            ).encode() + payload
            writer.write(request)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(8192), timeout=15)
        finally:
            writer.close()
        return self._parse_response(response)

    @staticmethod
    def _parse_response(response):
        header_block, _, rest = response.partition(b"\r\n\r\n")
        lines = header_block.split(b"\r\n")
        status_line = lines[0]
        headers = {}
        for line in lines[1:]:
            if b":" in line:
                key, _, value = line.partition(b":")
                headers[key.strip().decode().lower()] = value.strip().decode()
        body = json.loads(rest) if rest else None
        return status_line, headers, body

    async def _post(self, path, body, accept="application/json"):
        payload = json.dumps(body).encode()
        headers = {"Content-Type": "application/json", "Accept": accept}
        return await self._raw_post(path, headers, payload)

    def _assert_json_error_response(self, status, headers, body, status_code):
        self.assertIn(status_code, status)
        self.assertEqual(headers.get("content-type"), "application/json")
        self.assertEqual(set(body.keys()), {"data", "error"})
        self.assertIsNone(body["data"])
        self.assertIsNotNone(body["error"])
        return body["error"]

    async def test_successful_call(self):
        status, _headers, body = await self._post(
            "/rpc/EchoingRemoteService/0/echo", {"value": "hi"})
        self.assertIn(b"200", status)
        self.assertEqual(body, {"data": "hi", "error": None})

    async def test_rpc_error_propagates_as_json_error(self):
        status, headers, body = await self._post(
            "/rpc/EchoingRemoteService/0/boom", {})
        self.assertIn(b"200", status)
        self.assertEqual(headers.get("content-type"), "application/json")
        self.assertIsNone(body["data"])
        self.assertIn("deliberate failure", body["error"])

    async def test_unknown_service_is_404(self):
        status, headers, body = await self._post("/rpc/NoSuchService/0/echo", {})
        self._assert_json_error_response(status, headers, body, b"404")

    async def test_non_numeric_shard_is_404(self):
        status, headers, body = await self._post(
            "/rpc/EchoingRemoteService/not-a-number/echo", {})
        self._assert_json_error_response(status, headers, body, b"404")

    async def test_malformed_json_is_400(self):
        status, headers, body = await self._raw_post(
            "/rpc/EchoingRemoteService/0/echo",
            {"Content-Type": "application/json", "Accept": "application/json"},
            b"not json")
        self._assert_json_error_response(status, headers, body, b"400")

    async def test_wrong_content_type_is_415(self):
        status, headers, body = await self._raw_post(
            "/rpc/EchoingRemoteService/0/echo",
            {"Content-Type": "text/plain", "Accept": "application/json"},
            b"{}")
        self._assert_json_error_response(status, headers, body, b"415")

    async def test_wrong_accept_is_406(self):
        status, headers, body = await self._post(
            "/rpc/EchoingRemoteService/0/echo", {"value": "hi"},
            accept="application/xml")
        self._assert_json_error_response(status, headers, body, b"406")

    async def test_disconnected_service_is_503(self):
        # A coord that isn't in the (real, unmocked here) configuration
        # makes connect_to() fall back to a fake client that never
        # connects, so remote_services knows about it but its
        # "connected" property stays False.
        disconnected_coord = ServiceCoord("EchoingRemoteService", 1)
        self.frontend.connect_to(disconnected_coord, must_be_present=False)
        self.assertFalse(
            self.frontend.remote_services[disconnected_coord].connected)

        status, headers, body = await self._post(
            "/rpc/EchoingRemoteService/1/echo", {"value": "hi"})
        self._assert_json_error_response(status, headers, body, b"503")

    async def test_timeout_reports_error(self):
        with patch("cms.io.web_rpc.RPC_TIMEOUT_SECONDS", 0.2):
            status, headers, body = await self._post(
                "/rpc/EchoingRemoteService/0/sleep_forever", {})
        self.assertIn(b"200", status)
        self.assertEqual(headers.get("content-type"), "application/json")
        self.assertEqual(
            body, {"data": None, "error": "Timed out waiting for a reply."})

    async def test_auth_rejection_is_403(self):
        self.server.stop()
        await self.server.close_all_connections()
        handler_spec = RPCHandler.make_route(
            r"/rpc/(.*)/(.*)/(.*)", lambda service, shard, method: False)
        application = tornado.web.Application([handler_spec])
        application.service = self.frontend
        self.server = tornado.httpserver.HTTPServer(application)
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        self.server.add_sockets(sockets)
        self.port = sockets[0].getsockname()[1]

        status, headers, body = await self._post(
            "/rpc/EchoingRemoteService/0/echo", {"value": "hi"})
        self._assert_json_error_response(status, headers, body, b"403")

    async def test_slow_sync_auth_does_not_block_other_requests(self):
        # A synchronous, slow rpc_auth callback must be offloaded via
        # run_in_executor, so it doesn't block the single event-loop
        # thread from serving a concurrent request on another route.
        self.server.stop()
        await self.server.close_all_connections()

        def slow_auth(service_name, shard, method):
            time.sleep(0.3)
            return True

        handler_specs = [
            RPCHandler.make_route(r"/slow-rpc/(.*)/(.*)/(.*)", slow_auth),
            RPCHandler.make_route(r"/rpc/(.*)/(.*)/(.*)", None),
        ]
        application = tornado.web.Application(handler_specs)
        application.service = self.frontend
        self.server = tornado.httpserver.HTTPServer(application)
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        self.server.add_sockets(sockets)
        self.port = sockets[0].getsockname()[1]

        order = []

        async def slow_call():
            await self._post("/slow-rpc/EchoingRemoteService/0/echo",
                              {"value": "slow"})
            order.append("slow")

        async def fast_call():
            await asyncio.sleep(0.05)  # let the slow call start first
            await self._post("/rpc/EchoingRemoteService/0/echo",
                              {"value": "fast"})
            order.append("fast")

        await asyncio.gather(slow_call(), fast_call())
        self.assertEqual(order, ["fast", "slow"])


if __name__ == "__main__":
    unittest.main()
