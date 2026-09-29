#!/usr/bin/env python3

"""Tests for cms.io.async_rpc."""

import asyncio
import contextlib
import gc
import logging
import unittest
from unittest.mock import patch

from cms.io.async_rpc import AsyncRemoteServiceServer, AsyncRemoteServiceClient
from cms.io.rpc import rpc_method, RPCError
from cms.conf import Address, ServiceCoord
from cmstestsuite.unit_tests.stuckpeer import StuckPeer, connect_client


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


class TestExecuteRpcCleanup(unittest.IsolatedAsyncioTestCase):
    """execute_rpc() only ever fails with RPCError, whatever happens to
    the connection meanwhile, and never leaves its request behind.

    """

    async def asyncSetUp(self):
        self.peer = StuckPeer()
        await self.peer.start()
        self.client = await connect_client(
            ServiceCoord("FakeLocalService", 0), self.peer)

    async def asyncTearDown(self):
        self.client.disconnect()
        await self.peer.stop()

    def assert_nothing_pending(self):
        self.assertEqual(self.client.pending_outgoing_requests, {})
        self.assertEqual(self.client.pending_outgoing_requests_results, {})

    async def test_failed_write_raises_rpc_error(self):
        # The transport dies while the request is being written:
        # _write() finalizes the connection, which empties the
        # pending dicts.
        async def failing_drain():
            raise ConnectionResetError("Connection lost")

        with patch.object(self.client._writer, "drain", failing_drain):
            with self.assertRaises(RPCError):
                await self.client.execute_rpc("echo", {"string": "x"})
        self.assert_nothing_pending()

    async def test_write_failing_after_finalize_raises_rpc_error(self):
        # The read loop finalized the connection (emptying the pending
        # dicts) while the write was still waiting, and the write
        # then fails too.
        async def drain_after_finalize():
            self.client.finalize("Connection closed.")
            raise ConnectionResetError("Connection lost")

        with patch.object(self.client._writer, "drain", drain_after_finalize):
            with self.assertRaises(RPCError):
                await self.client.execute_rpc("echo", {"string": "x"})
        self.assert_nothing_pending()

    async def test_failed_write_leaves_no_unretrieved_exception(self):
        # finalize() fails the request's future with an RPCError while
        # nobody is awaiting it yet: unless execute_rpc() takes it
        # from there, asyncio reports "Future exception was never
        # retrieved" when the future is garbage-collected.
        reports = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: reports.append(context["message"]))

        async def failing_drain():
            raise ConnectionResetError("Connection lost")

        # A log record keeps the exception it formats, and so the
        # frames (and futures) in its traceback, alive: pytest holds on
        # to the records of a running test, which would delay the
        # report past the check below.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        with patch.object(self.client._writer, "drain", failing_drain):
            # How the call fails is checked by the tests above.
            with contextlib.suppress(Exception):
                await self.client.execute_rpc("echo", {"string": "x"})
        gc.collect()

        self.assertEqual(reports, [])

    async def test_cancelled_call_leaves_no_pending_entries(self):
        call = asyncio.create_task(
            self.client.execute_rpc("echo", {"string": "x"}))
        await self.peer.wait_for_requests(1)
        self.assertEqual(len(self.client.pending_outgoing_requests), 1)

        call.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await call

        self.assert_nothing_pending()

    async def test_connection_lost_while_waiting_raises_rpc_error(self):
        call = asyncio.create_task(
            self.client.execute_rpc("echo", {"string": "x"}))
        await self.peer.wait_for_requests(1)

        await self.peer.stop()

        with self.assertRaises(RPCError):
            await asyncio.wait_for(call, timeout=2)
        self.assert_nothing_pending()


if __name__ == "__main__":
    unittest.main()
