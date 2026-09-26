#!/usr/bin/env python3

"""Tests for cms.io.web_rpc.RPCHandler."""

import asyncio
import json
import socket
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

    async def _post(self, path, body):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            payload = json.dumps(body).encode()
            request = (
                "POST %s HTTP/1.1\r\nHost: localhost\r\n"
                "Content-Type: application/json\r\n"
                "Accept: application/json\r\n"
                "Content-Length: %d\r\n\r\n" % (path, len(payload))
            ).encode() + payload
            writer.write(request)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(8192), timeout=5)
        finally:
            writer.close()
        status_line = response.split(b"\r\n", 1)[0]
        body_start = response.index(b"\r\n\r\n") + 4
        return status_line, json.loads(response[body_start:] or b"null")

    async def test_successful_call(self):
        status, body = await self._post(
            "/rpc/EchoingRemoteService/0/echo", {"value": "hi"})
        self.assertIn(b"200", status)
        self.assertEqual(body, {"data": "hi", "error": None})

    async def test_rpc_error_propagates_as_json_error(self):
        status, body = await self._post(
            "/rpc/EchoingRemoteService/0/boom", {})
        self.assertIn(b"200", status)
        self.assertIsNone(body["data"])
        self.assertIsNotNone(body["error"])

    async def test_unknown_service_is_404(self):
        status, _ = await self._post("/rpc/NoSuchService/0/echo", {})
        self.assertIn(b"404", status)

    async def test_malformed_json_is_400(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            payload = b"not json"
            request = (
                "POST /rpc/EchoingRemoteService/0/echo HTTP/1.1\r\n"
                "Host: localhost\r\nContent-Type: application/json\r\n"
                "Accept: application/json\r\n"
                "Content-Length: %d\r\n\r\n" % len(payload)
            ).encode() + payload
            writer.write(request)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"400", response.split(b"\r\n", 1)[0])

    async def test_wrong_content_type_is_415(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            payload = b"{}"
            request = (
                "POST /rpc/EchoingRemoteService/0/echo HTTP/1.1\r\n"
                "Host: localhost\r\nContent-Type: text/plain\r\n"
                "Accept: application/json\r\n"
                "Content-Length: %d\r\n\r\n" % len(payload)
            ).encode() + payload
            writer.write(request)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"415", response.split(b"\r\n", 1)[0])

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

        status, _ = await self._post(
            "/rpc/EchoingRemoteService/0/echo", {"value": "hi"})
        self.assertIn(b"403", status)


if __name__ == "__main__":
    unittest.main()
