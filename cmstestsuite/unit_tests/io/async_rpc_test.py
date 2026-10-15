#!/usr/bin/env python3

"""Tests for cms.io.async_rpc."""

import asyncio
import contextlib
import gc
import logging
import socket
import unittest
from unittest.mock import patch

from cms.io.async_rpc import AsyncFakeRemoteServiceClient, \
    AsyncRemoteServiceServer, AsyncRemoteServiceClient, \
    NotificationFailureLatch
from cms.io.rpc import rpc_method, RPCError, ServiceNotConfiguredError
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


class TestAsyncRpcConnectLoop(unittest.IsolatedAsyncioTestCase):
    """The connect loop of a client with auto_retry keeps trying
    whatever goes wrong with the peer, without hammering it.

    """

    async def _start_peer(self, handle_connection) -> int:
        """Start a loopback server, stopped when the test ends.

        handle_connection: the asyncio.start_server callback.

        return: the port it listens on.

        """
        server = await asyncio.start_server(
            handle_connection, "127.0.0.1", 0)

        async def stop():
            server.close()
            await server.wait_closed()

        self.addAsyncCleanup(stop)
        return server.sockets[0].getsockname()[1]

    def _client(
        self, port: int, auto_retry: float | None = 0.05
    ) -> AsyncRemoteServiceClient:
        """Return a client of the peer at port, stopped when the test
        ends (cleanups run in reverse, so before the peer stops).

        """
        with patch("cms.io.async_rpc.get_service_address",
                   return_value=Address("127.0.0.1", port)):
            client = AsyncRemoteServiceClient(
                ServiceCoord("FakeLocalService", 0), auto_retry=auto_retry)

        async def stop():
            client.disconnect()
            if client._loop_task is not None:
                client._loop_task.cancel()
                await asyncio.wait([client._loop_task])

        self.addAsyncCleanup(stop)
        return client

    @staticmethod
    async def _serve(reader, writer):
        server_side = AsyncRemoteServiceServer(
            FakeLocalService(), Address("127.0.0.1", 0))
        server_side.initialize_streams(reader, writer, plus=None)
        await server_side.run()

    @staticmethod
    async def _wait_until(predicate, timeout: float = 2.0):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.005)

    @staticmethod
    def _flaky_getaddrinfo(failures: int):
        """Return a getaddrinfo that fails failures times, then works."""
        real_getaddrinfo = asyncio.get_running_loop().getaddrinfo
        calls = 0

        async def getaddrinfo(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls <= failures:
                raise socket.gaierror(
                    socket.EAI_NONAME, "Name or service not known")
            return await real_getaddrinfo(*args, **kwargs)

        return getaddrinfo

    async def test_name_resolution_failures_do_not_end_the_loop(self):
        port = await self._start_peer(self._serve)
        client = self._client(port)
        loop = asyncio.get_running_loop()

        with patch.object(loop, "getaddrinfo", self._flaky_getaddrinfo(2)):
            with self.assertLogs("cms.io.async_rpc", "DEBUG") as logs:
                client.connect()
                self.assertTrue(await client.wait_for_connection(timeout=2))

        self.assertFalse(client._loop_task.done())
        self.assertEqual(
            await client.execute_rpc("echo", {"string": "x"}), "x")
        # One WARNING for the outage, DEBUG for each retry, one INFO
        # when it ends.
        self.assertEqual(
            [record.levelname for record in logs.records
             if "resolve" in record.getMessage()],
            ["WARNING", "DEBUG", "INFO"])

    async def test_one_shot_client_that_cannot_resolve_ends_quietly(self):
        # Without auto_retry, it ends up not connected, as when the
        # connection is refused.
        port = await self._start_peer(self._serve)
        client = self._client(port, auto_retry=None)
        loop = asyncio.get_running_loop()

        with patch.object(loop, "getaddrinfo", self._flaky_getaddrinfo(1)):
            with self.assertLogs("cms.io.async_rpc", "WARNING"):
                client.connect()
                await asyncio.wait_for(asyncio.wait([client._loop_task]), 2)

        self.assertIsNone(client._loop_task.exception())
        self.assertFalse(client.connected)

    async def test_malformed_answers_make_the_client_reconnect(self):
        # Connection n gets bad_answers[n], unasked; once they are over,
        # a real server answers.
        bad_answers = [b"not json", b"null", b"[]", b'"x"', b'{"__id": 1}']
        connections = 0

        async def answer_badly(reader, writer):
            nonlocal connections
            connections += 1
            if connections > len(bad_answers):
                await self._serve(reader, writer)
                return
            writer.write(bad_answers[connections - 1] + b"\r\n")
            await writer.drain()
            await reader.read()
            writer.close()

        port = await self._start_peer(answer_badly)
        client = self._client(port)

        with self.assertLogs("cms.io.async_rpc", "DEBUG") as logs:
            client.connect()
            await self._wait_until(
                lambda: connections > len(bad_answers) and client.connected)
            self.assertEqual(
                await client.execute_rpc("echo", {"string": "x"}), "x")

        self.assertFalse(client._loop_task.done())
        # Only the first one is a WARNING, so that a peer that keeps
        # answering garbage can't flood the logs.
        self.assertEqual(
            [record.levelname for record in logs.records
             if "Cannot parse" in record.getMessage()
             or "missing some fields" in record.getMessage()
             or "well-formed" in record.getMessage()],
            ["WARNING"] + ["DEBUG"] * (len(bad_answers) - 1) + ["INFO"])

    async def test_reconnects_no_faster_than_auto_retry(self):
        # A peer that accepts and closes at once must not be
        # reconnected to as fast as the loop can go.
        auto_retry = 0.1
        connections = 0

        async def close_at_once(_reader, writer):
            nonlocal connections
            connections += 1
            writer.close()

        port = await self._start_peer(close_at_once)
        client = self._client(port, auto_retry=auto_retry)

        client.connect()
        await asyncio.sleep(1)

        self.assertGreaterEqual(connections, 2)
        self.assertLessEqual(connections, 1 / auto_retry + 2)

    async def test_server_drops_a_peer_sending_a_non_object(self):
        port = await self._start_peer(self._serve)

        for payload in (b"null", b"[]", b'"x"'):
            with self.subTest(payload=payload):
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", port)
                # Before the peer stops, which waits for its connections.
                self.addCleanup(writer.close)
                writer.write(payload + b"\r\n")
                # The server closes the connection.
                self.assertEqual(
                    await asyncio.wait_for(reader.read(), 2), b"")


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


class TestNotificationFailureLatch(unittest.TestCase):

    def setUp(self):
        self.now = 1000.0
        patcher = patch("cms.io.async_rpc.monotonic", lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.logger = logging.getLogger("cmstestsuite.notificationlatch")
        self.latch = NotificationFailureLatch(
            self.logger, "EvaluationService",
            ServiceCoord("ScoringService", 0),
            "nothing is lost, its sweeper will score these submissions")

    def drop(self, submission_id: int, error: Exception | None = None):
        """Make the notification about submission_id fail.

        return: the (level name, message) pairs it logged.

        """
        if error is None:
            error = RPCError("Write failed.")
        with self.assertLogs(self.logger, "DEBUG") as logs:
            self.latch.failed("submission %d" % submission_id, error)
        return [(record.levelname, record.getMessage())
                for record in logs.records]

    def warnings(self, logged: list[tuple[str, str]]) -> list[str]:
        return [message for level, message in logged if level == "WARNING"]

    def test_first_failure_is_a_warning_naming_both_ends(self):
        logged = self.drop(1)

        self.assertEqual(
            self.warnings(logged),
            ["EvaluationService cannot notify ScoringService,0 (Write "
             "failed.); nothing is lost, its sweeper will score these "
             "submissions."])
        # What the notification was about is only in the DEBUG line.
        self.assertIn(("DEBUG", "EvaluationService did not notify "
                       "ScoringService,0 of submission 1 (Write failed.)."),
                      logged)

    def test_failures_within_a_minute_are_debug_only(self):
        self.drop(1)
        self.now += 59

        logged = self.drop(2)

        self.assertEqual([level for level, _message in logged], ["DEBUG"])
        self.assertIn("submission 2", logged[0][1])

    def test_failures_after_a_minute_give_a_reminder_with_the_count(self):
        self.drop(1)
        self.now += 30
        self.drop(2)
        self.now += 31

        reminder = self.warnings(self.drop(3))

        self.assertEqual(len(reminder), 1)
        self.assertRegex(
            reminder[0],
            r"^EvaluationService still cannot notify ScoringService,0 "
            r"\(Write failed\.\): 3 notification\(s\) dropped since "
            r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d; nothing is lost, its "
            r"sweeper will score these submissions\.$")
        # The next reminder is a minute after this one.
        self.now += 59
        self.assertEqual(self.warnings(self.drop(4)), [])
        self.now += 1
        reminder = self.warnings(self.drop(5))
        self.assertEqual(len(reminder), 1)
        self.assertIn("5 notification(s) dropped", reminder[0])

    def test_success_after_failures_is_an_info_with_the_count(self):
        self.drop(1)
        self.drop(2)

        with self.assertLogs(self.logger, "DEBUG") as logs:
            self.latch.succeeded()

        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].levelname, "INFO")
        self.assertRegex(
            logs.records[0].getMessage(),
            r"^EvaluationService can notify ScoringService,0 again: 2 "
            r"notification\(s\) dropped since \d{4}-\d\d-\d\d "
            r"\d\d:\d\d:\d\d\.$")
        # The next failure starts a new outage.
        self.assertEqual(len(self.warnings(self.drop(3))), 1)

    def test_success_without_failures_logs_nothing(self):
        with self.assertNoLogs(self.logger, "DEBUG"):
            self.latch.succeeded()

    def test_peer_not_configured_is_debug_and_not_counted(self):
        logged = self.drop(1, ServiceNotConfiguredError("Not configured."))

        self.assertEqual(
            logged, [("DEBUG", "EvaluationService did not notify "
                      "ScoringService,0 of submission 1: it is not "
                      "configured.")])
        with self.assertNoLogs(self.logger, "DEBUG"):
            self.latch.succeeded()

    def test_no_answer_in_time_is_a_failure(self):
        warnings = self.warnings(self.drop(1, TimeoutError()))

        self.assertEqual(len(warnings), 1)
        self.assertIn("(no answer in time)", warnings[0])


class TestFakeRemoteServiceClient(unittest.IsolatedAsyncioTestCase):

    async def test_call_fails_with_service_not_configured_error(self):
        client = AsyncFakeRemoteServiceClient(ServiceCoord("ProxyService", 0))

        with self.assertRaises(ServiceNotConfiguredError) as context:
            await client.reinitialize()

        self.assertEqual(str(context.exception),
                         "Called a method of a non-configured service.")

    async def test_error_is_still_an_rpc_error(self):
        # Callers that catch RPCError keep catching it.
        client = AsyncFakeRemoteServiceClient(ServiceCoord("ProxyService", 0))

        with self.assertRaises(RPCError):
            await client.execute_rpc("reinitialize", {})

        self.assertTrue(issubclass(ServiceNotConfiguredError, RPCError))


if __name__ == "__main__":
    unittest.main()
