#!/usr/bin/env python3

"""Tests for cms.service.Checker."""

import asyncio
import time
import unittest
from unittest.mock import ANY, patch

from cms.conf import Address, ServiceCoord
from cms.io.async_rpc import AsyncRemoteServiceClient, AsyncRemoteServiceServer
from cms.io.rpc import rpc_method
from cms.service.Checker import Checker


class EchoingLocalService:
    """A minimal stand-in for the remote side of a successful echo RPC."""

    @rpc_method
    def echo(self, string: str) -> str:
        return string


class FailingLocalService:
    """A stand-in whose echo RPC always fails, to trigger RPCError."""

    @rpc_method
    def echo(self, string: str) -> str:
        raise ValueError("deliberate failure")


class HangingLocalService:
    """A stand-in whose echo RPC never responds, to simulate a service
    that is connected but stuck (e.g. deadlocked or overloaded)."""

    @rpc_method
    async def echo(self, string: str) -> str:
        await asyncio.Future()  # never completes
        return string  # pragma: no cover


class SlowLocalService:
    """A stand-in whose echo RPC only responds once release_event is
    set, to simulate a service that is connected but slow to reply."""

    def __init__(self, release_event: asyncio.Event):
        self._release_event = release_event

    @rpc_method
    async def echo(self, string: str) -> str:
        await self._release_event.wait()
        return string


async def _start_server(local_service: object) -> tuple[asyncio.Server, int]:
    """Start a loopback server exposing local_service's RPC methods.

    local_service: object exposing the RPC methods to serve.

    return: the started server (must be closed by the caller) and the
        port it is listening on.

    """
    async def handle_client(reader, writer):
        server_side = AsyncRemoteServiceServer(
            local_service, Address("127.0.0.1", 0))
        server_side.initialize_streams(reader, writer, plus=None)
        await server_side.run()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    asyncio.create_task(server.serve_forever())
    return server, server.sockets[0].getsockname()[1]


class CheckerTest(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._servers: list[asyncio.Server] = []
        self._clients: list[AsyncRemoteServiceClient] = []

        # Checker.__init__ would otherwise connect to every service in
        # the real (sample) config; tests wire their own peers instead.
        config_patcher = patch("cms.service.Checker.config.services", {})
        config_patcher.start()
        self.addCleanup(config_patcher.stop)

        address_patcher = patch(
            "cms.io.async_service.get_service_address",
            return_value=Address("127.0.0.1", 0))
        address_patcher.start()
        self.addCleanup(address_patcher.stop)

        # The patch above targets cms.service.Checker.config.services, but
        # config is a global singleton, so it also empties the LogService
        # entry that cms.util.get_service_address would otherwise return.
        # AsyncRemoteServiceClient (used by AsyncService.__init__ to connect
        # to LogService) resolves addresses via async_rpc's own imported
        # reference to that function, so it must be patched too, or
        # connecting raises ConfigError: Missing address and port for
        # LogService.
        rpc_address_patcher = patch(
            "cms.io.async_rpc.get_service_address",
            return_value=Address("127.0.0.1", 0))
        rpc_address_patcher.start()
        self.addCleanup(rpc_address_patcher.stop)

        self.checker = Checker(shard=0)

    async def asyncTearDown(self):
        for client in self._clients:
            client.disconnect()
        for server in self._servers:
            server.close()
            await server.wait_closed()

    async def _add_connected_service(
        self, coord: ServiceCoord, local_service: object
    ) -> AsyncRemoteServiceClient:
        """Wire a real, connected peer for coord into self.checker.

        coord: the coord to register the peer under.
        local_service: object exposing the RPC methods to serve.

        return: the connected client, already stored in
            self.checker.remote_services[coord].

        """
        server, port = await _start_server(local_service)
        self._servers.append(server)

        with patch("cms.io.async_rpc.get_service_address",
                   return_value=Address("127.0.0.1", 0)):
            client = AsyncRemoteServiceClient(coord)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        client.initialize_streams(reader, writer, plus=None)
        asyncio.create_task(client.run())
        self._clients.append(client)

        self.checker.remote_services[coord] = client
        return client

    def _spy_on_spawn(self) -> list[asyncio.Task]:
        """Record every task self.checker spawns from now on.

        return: the (initially empty) list that will be appended to.

        """
        spawned_tasks: list[asyncio.Task] = []
        original_spawn = self.checker._spawn

        def spy_spawn(coro):
            task = original_spawn(coro)
            spawned_tasks.append(task)
            return task

        self.checker._spawn = spy_spawn
        return spawned_tasks

    async def test_check_success_removes_waiting_for_and_does_not_warn(self):
        coord = ServiceCoord("EchoService", 0)
        await self._add_connected_service(coord, EchoingLocalService())
        spawned_tasks = self._spy_on_spawn()

        with patch("cms.service.Checker.logger") as mock_logger:
            await self.checker.check()
            await asyncio.wait_for(asyncio.gather(*spawned_tasks), timeout=2)

        self.assertEqual(len(spawned_tasks), 1)
        self.assertNotIn(coord, self.checker.waiting_for)
        mock_logger.warning.assert_not_called()
        mock_logger.info.assert_any_call(
            "Got reply (%5.3lf s) from %s.", ANY, coord)

    async def test_check_rpc_error_does_not_crash_or_leave_task_dangling(self):
        coord = ServiceCoord("FailingService", 0)
        await self._add_connected_service(coord, FailingLocalService())
        spawned_tasks = self._spy_on_spawn()

        result = await self.checker.check()
        await asyncio.wait_for(asyncio.gather(*spawned_tasks), timeout=2)

        self.assertTrue(result)
        self.assertEqual(len(spawned_tasks), 1)
        self.assertTrue(spawned_tasks[0].done())
        self.assertIsNone(spawned_tasks[0].exception())
        # The waiting_for entry is left in place after an RPCError, so the
        # next check() call detects it as a timeout and retries.
        self.assertIn(coord, self.checker.waiting_for)

    async def test_check_skips_unconnected_service(self):
        coord = ServiceCoord("DisconnectedService", 0)
        with patch("cms.io.async_rpc.get_service_address",
                   return_value=Address("127.0.0.1", 0)):
            client = AsyncRemoteServiceClient(coord)
        self.checker.remote_services[coord] = client
        spawned_tasks = self._spy_on_spawn()

        await self.checker.check()

        self.assertEqual(spawned_tasks, [])
        self.assertNotIn(coord, self.checker.waiting_for)

    async def test_late_reply_is_logged_as_warning(self):
        coord = ServiceCoord("LateService", 0)
        self.checker.waiting_for[coord] = time.time()
        old_time = time.time() - 20
        data = "%s %5.3lf" % (coord, old_time)

        with patch("cms.service.Checker.logger") as mock_logger:
            self.checker.echo_callback(data)

        mock_logger.warning.assert_called_once()
        # The late-reply branch leaves the waiting_for entry untouched.
        self.assertIn(coord, self.checker.waiting_for)

    async def test_late_reply_for_unknown_service_logs_warning(self):
        # A reply for a coordinate that was never (or is no longer) in
        # waiting_for hits the same "late reply" branch as a stale
        # timestamp does.
        coord = ServiceCoord("UnknownService", 0)
        data = "%s %5.3lf" % (coord, time.time())

        with patch("cms.service.Checker.logger") as mock_logger:
            self.checker.echo_callback(data)

        mock_logger.warning.assert_called_once()
        self.assertNotIn(coord, self.checker.waiting_for)

    async def test_cheated_timestamp_still_logs_reply_as_success(self):
        # A reply that isn't late, but whose embedded timestamp doesn't
        # match what Checker recorded in waiting_for, triggers the
        # "cheated on the timestamp" warning while still being processed
        # as a successful (non-late) reply.
        coord = ServiceCoord("CheatingService", 0)
        self.checker.waiting_for[coord] = time.time() - 5
        now = time.time()
        data = "%s %5.3lf" % (coord, now)

        with patch("cms.service.Checker.logger") as mock_logger:
            self.checker.echo_callback(data)

        mock_logger.warning.assert_called_once_with(
            "Someone cheated on the timestamp?!")
        mock_logger.info.assert_called_once_with(
            "Got reply (%5.3lf s) from %s.", ANY, coord)
        self.assertNotIn(coord, self.checker.waiting_for)

    async def test_check_timeout_then_retry_resets_waiting_for_timestamp(
        self,
    ):
        coord = ServiceCoord("HangingService", 0)
        await self._add_connected_service(coord, HangingLocalService())
        self._spy_on_spawn()

        with patch("cms.service.Checker.time.time", side_effect=[100.0]):
            await self.checker.check()

        self.assertIn(coord, self.checker.waiting_for)
        self.assertEqual(self.checker.waiting_for[coord], 100.0)

        with patch("cms.service.Checker.logger") as mock_logger, \
                patch("cms.service.Checker.time.time", side_effect=[200.0]):
            await self.checker.check()

        mock_logger.info.assert_any_call(
            "Service %s timeout, retrying.", coord)
        # The retry resets waiting_for to a new timestamp, not the old one.
        self.assertIn(coord, self.checker.waiting_for)
        self.assertEqual(self.checker.waiting_for[coord], 200.0)

    async def test_check_does_not_block_on_a_slow_service(self):
        release_event = asyncio.Event()
        slow_coord = ServiceCoord("SlowService", 0)
        await self._add_connected_service(
            slow_coord, SlowLocalService(release_event))
        fast_coord = ServiceCoord("FastService", 0)
        await self._add_connected_service(fast_coord, EchoingLocalService())
        spawned_tasks = self._spy_on_spawn()

        # check() must not wait for the slow service's echo to come back.
        await asyncio.wait_for(self.checker.check(), timeout=1)

        self.assertEqual(len(spawned_tasks), 2)
        slow_task, fast_task = spawned_tasks

        # The fast reply is processed even though the slow one is still
        # pending.
        await asyncio.wait_for(fast_task, timeout=2)
        self.assertNotIn(fast_coord, self.checker.waiting_for)
        self.assertIn(slow_coord, self.checker.waiting_for)
        self.assertFalse(slow_task.done())

        # Unblock the slow service so its task can be cleaned up.
        release_event.set()
        await asyncio.wait_for(slow_task, timeout=2)
        self.assertNotIn(slow_coord, self.checker.waiting_for)


if __name__ == "__main__":
    unittest.main()
