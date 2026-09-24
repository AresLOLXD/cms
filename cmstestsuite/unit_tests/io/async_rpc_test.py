#!/usr/bin/env python3

"""Tests for cms.io.async_rpc."""

import asyncio
import unittest
from unittest.mock import patch

from cms.io.async_rpc import AsyncRemoteServiceServer, AsyncRemoteServiceClient
from cms.io.rpc import rpc_method, RPCError
from cms.conf import Address, ServiceCoord


class FakeLocalService:
    """A minimal stand-in for cms.io.service.Service, for testing
    AsyncRemoteServiceServer without needing a real Service."""

    @rpc_method
    def echo(self, string: str) -> str:
        return string

    @rpc_method
    def fail(self):
        raise ValueError("deliberate failure")

    def not_rpc_callable(self):
        """Not decorated with @rpc_method -- must be rejected."""
        return "should never be called remotely"


class TestAsyncRpcRoundTrip(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.local_service = FakeLocalService()
        self.server_reader = None
        self.server_writer = None

        async def handle_client(reader, writer):
            server_side = AsyncRemoteServiceServer(
                self.local_service, Address("127.0.0.1", 0))
            server_side.initialize_streams(reader, writer, plus=None)
            await server_side.run()

        self.server = await asyncio.start_server(
            handle_client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        asyncio.create_task(self.server.serve_forever())

        # Bypass service-address lookup (no cms.toml entry for this
        # coord): AsyncRemoteServiceClient's constructor resolves the
        # coord eagerly, so the lookup itself must be patched -- the
        # test then connects directly to the loopback test server
        # instead of relying on the (unused) resolved address.
        with patch("cms.io.async_rpc.get_service_address",
                    return_value=Address("127.0.0.1", 0)):
            self.client = AsyncRemoteServiceClient(
                ServiceCoord("FakeLocalService", 0))
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.client.initialize_streams(reader, writer, plus=None)
        asyncio.create_task(self.client.run())

    async def asyncTearDown(self):
        self.client.disconnect()
        self.server.close()
        await self.server.wait_closed()

    async def test_successful_call_returns_value(self):
        result = await self.client.execute_rpc("echo", {"string": "hello"})
        self.assertEqual(result, "hello")

    async def test_failing_call_raises_rpc_error(self):
        with self.assertRaises(RPCError):
            await self.client.execute_rpc("fail", {})

    async def test_non_rpc_method_is_rejected(self):
        with self.assertRaises(RPCError):
            await self.client.execute_rpc("not_rpc_callable", {})

    async def test_nonexistent_method_is_rejected(self):
        with self.assertRaises(RPCError):
            await self.client.execute_rpc("does_not_exist", {})


class TestAsyncRpcReconnection(unittest.IsolatedAsyncioTestCase):

    async def test_client_reconnects_after_server_restart(self):
        local_service = FakeLocalService()

        async def handle_client(reader, writer):
            server_side = AsyncRemoteServiceServer(
                local_service, Address("127.0.0.1", 0))
            server_side.initialize_streams(reader, writer, plus=None)
            await server_side.run()

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        asyncio.create_task(server.serve_forever())

        # See TestAsyncRpcRoundTrip.asyncSetUp for why this lookup
        # must be patched.
        with patch("cms.io.async_rpc.get_service_address",
                    return_value=Address("127.0.0.1", 0)):
            client = AsyncRemoteServiceClient(
                ServiceCoord("FakeLocalService", 0), auto_retry=0.05)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        client.initialize_streams(reader, writer, plus=None)
        client_loop = asyncio.create_task(client.run())

        result = await client.execute_rpc("echo", {"string": "before"})
        self.assertEqual(result, "before")

        # Kill the connection from the client's side and reconnect
        # manually against a fresh server socket on the same port,
        # simulating a server restart.
        client.disconnect()
        client_loop.cancel()
        server.close()
        await server.wait_closed()

        server2 = await asyncio.start_server(handle_client, "127.0.0.1", port)
        asyncio.create_task(server2.serve_forever())

        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
        client.initialize_streams(reader2, writer2, plus=None)
        asyncio.create_task(client.run())

        result2 = await client.execute_rpc("echo", {"string": "after"})
        self.assertEqual(result2, "after")

        client.disconnect()
        server2.close()
        await server2.wait_closed()


if __name__ == "__main__":
    unittest.main()
