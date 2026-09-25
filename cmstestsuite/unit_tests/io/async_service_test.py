#!/usr/bin/env python3

"""Tests for cms.io.async_service."""

import asyncio
import os
import signal
import time
import unittest
from unittest.mock import patch

from cms.conf import Address
from cms.io.async_service import AsyncService
from cms.io.rpc import rpc_method
from cms.log import root_logger


class EchoingAsyncService(AsyncService):

    @rpc_method
    def double(self, value: int) -> int:
        return value * 2


class TestAsyncServiceLifecycle(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_service_starts_and_stops(self, mock_get_address):
        mock_get_address.return_value = Address("127.0.0.1", 0)
        service = EchoingAsyncService(shard=0)
        run_task = asyncio.create_task(service._async_run())
        # Give the server a moment to actually bind before checking.
        await asyncio.sleep(0.05)
        self.assertTrue(service._server.is_serving())
        service.exit()
        await asyncio.wait_for(run_task, timeout=2)
        self.assertFalse(service._server.is_serving())


class TestShutdownWithConnectedPeer(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_exit_closes_connections_still_open(self, mock_get_address):
        # Regression test: asyncio.Server.wait_closed() (3.12.1+) waits
        # for every accepted connection, so a peer that never hangs up
        # (like every service's permanent connection to LogService)
        # used to make shutdown hang forever.
        mock_get_address.return_value = Address("127.0.0.1", 0)
        service = EchoingAsyncService(shard=0)
        run_task = asyncio.create_task(service._async_run())
        await asyncio.sleep(0.05)
        port = service._server.sockets[0].getsockname()[1]

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await asyncio.sleep(0.05)
            self.assertEqual(len(service._connections), 1)

            service.exit()
            result = await asyncio.wait_for(run_task, timeout=2)
            self.assertTrue(result)
            self.assertEqual(service._connections, set())
            # The server actively closed our connection.
            self.assertEqual(
                await asyncio.wait_for(reader.read(), timeout=1), b"")
        finally:
            writer.close()


class TestAsyncServiceLogsToLogService(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_rpc.get_service_address")
    @patch("cms.io.async_service.get_service_address")
    async def test_log_record_reaches_real_log_service(
        self, mock_async_address, mock_async_rpc_address
    ):
        import logging
        import socket
        from cms.conf import ServiceCoord
        from cms.service.LogService import LogService

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            log_service_port = probe.getsockname()[1]

        mock_async_address.side_effect = lambda coord: (
            Address("127.0.0.1", log_service_port)
            if coord.name == "LogService" else Address("127.0.0.1", 0))
        mock_async_rpc_address.return_value = \
            Address("127.0.0.1", log_service_port)

        # LogService installs a DEBUG FileHandler with a gevent lock on
        # the root logger; left there, it can deadlock later tests that
        # log from both a gevent thread and the asyncio thread.
        original_handlers = list(root_logger.handlers)

        def restore_handlers():
            for handler in root_logger.handlers:
                if handler not in original_handlers:
                    handler.close()
            root_logger.handlers = original_handlers

        self.addCleanup(restore_handlers)

        log_service = LogService(0)
        log_task = asyncio.create_task(log_service._async_run())
        service = EchoingAsyncService(shard=0)
        service_task = asyncio.create_task(service._async_run())
        try:
            log_client = service.remote_services[ServiceCoord("LogService", 0)]
            self.assertTrue(await log_client.wait_for_connection(timeout=2))

            marker = "async service log record %f" % time.monotonic()
            logging.getLogger("async_service_test").warning(marker)

            deadline = time.monotonic() + 2
            received = []
            while time.monotonic() < deadline and not received:
                await asyncio.sleep(0.02)
                received = [m for m in log_service.last_messages()
                            if m["message"] == marker]
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["coord"], "EchoingAsyncService,0")
        finally:
            service.exit()
            await asyncio.wait_for(service_task, timeout=2)
            log_service.exit()
            await asyncio.wait_for(log_task, timeout=2)


class TestAddTimeout(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_repeated_calls_happen_at_interval(self, mock_get_address):
        mock_get_address.return_value = Address("127.0.0.1", 0)
        service = EchoingAsyncService(shard=0)
        service._loop = asyncio.get_running_loop()
        service._exit_event = asyncio.Event()

        calls = []
        service.add_timeout(lambda: calls.append(time.monotonic()),
                            plus=None, seconds=0.05, immediately=True)

        await asyncio.sleep(0.22)

        # Expect at least 4 calls in ~0.22s at a 0.05s period
        # (immediately=True means the first call has no initial delay).
        self.assertGreaterEqual(len(calls), 4)


class TestSignalHandling(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_sigterm_triggers_clean_shutdown(self, mock_get_address):
        mock_get_address.return_value = Address("127.0.0.1", 0)
        service = EchoingAsyncService(shard=0)
        run_task = asyncio.create_task(service._async_run())
        await asyncio.sleep(0.05)

        os.kill(os.getpid(), signal.SIGTERM)

        result = await asyncio.wait_for(run_task, timeout=2)
        self.assertTrue(result)
        self.assertFalse(service._server.is_serving())


class TestGeventAsyncioInterop(unittest.IsolatedAsyncioTestCase):
    """Proves an AsyncService and an old, unmigrated gevent Service can
    exchange real RPCs in both directions -- the premise the entire
    2.2->2.4 incremental migration strategy depends on."""

    @patch("cms.io.async_rpc.get_service_address")
    @patch("cms.io.async_service.get_service_address")
    @patch("cms.io.service.get_service_address")
    async def test_async_client_calls_gevent_server(
        self, mock_gevent_address, mock_async_address, mock_async_rpc_address
    ):
        import threading
        from cms.io.service import Service
        from cms.io.rpc import rpc_method as gevent_rpc_method

        class GeventEchoService(Service):
            @gevent_rpc_method
            def triple(self, value: int) -> int:
                return value * 3

        mock_gevent_address.return_value = Address("127.0.0.1", 24680)
        mock_async_address.return_value = Address("127.0.0.1", 24681)
        # AsyncRemoteServiceClient resolves its remote coord's address via
        # cms.io.async_rpc's own imported get_service_address reference
        # (not cms.io.async_service's), so that lookup must be patched
        # separately -- this is what actually lets the client below find
        # "GeventEchoService" without a real cms.toml entry.
        mock_async_rpc_address.return_value = Address("127.0.0.1", 24680)

        # GeventEchoService must be constructed *inside* the thread that
        # runs it: gevent's Event/hub objects bind to the thread they
        # were created in, so constructing the Service here (in the
        # test's own thread) and running it in server_thread causes the
        # gevent hub driving serve_forever() to have no registered
        # watchers, raising "gevent.exceptions.LoopExit: This operation
        # would block forever" as soon as the thread starts -- a real
        # defect in this test's original construct-then-thread ordering,
        # found by reproducing it in isolation.
        gevent_service_box: dict = {}
        gevent_service_ready = threading.Event()

        def run_gevent_service():
            # Service.__init__ registers OS signal handlers, which only
            # works in the interpreter's main thread; this test doesn't
            # need real signal handling for its background gevent
            # service, so that registration is stubbed out here.
            with patch("signal.signal"):
                service = GeventEchoService(shard=0)
            gevent_service_box["service"] = service
            gevent_service_ready.set()
            service.run()

        server_thread = threading.Thread(
            target=run_gevent_service, daemon=True)
        server_thread.start()
        gevent_service_ready.wait(timeout=2)
        gevent_service = gevent_service_box["service"]

        from cms.io.async_rpc import AsyncRemoteServiceClient
        from cms.conf import ServiceCoord

        client = AsyncRemoteServiceClient(ServiceCoord("GeventEchoService", 0))

        # The gevent StreamServer's bind happens asynchronously in its
        # own thread; a single fixed sleep before connecting is racy
        # under CI/Docker timing, so retry connect()/wait_for_connection
        # for up to 2 seconds instead.
        deadline = time.monotonic() + 2
        connected = False
        while time.monotonic() < deadline and not connected:
            client.connect()
            connected = await client.wait_for_connection(timeout=0.1)
            if not connected:
                client.disconnect()

        self.assertTrue(connected, "Never connected to the gevent server.")

        result = await client.execute_rpc("triple", {"value": 7})
        self.assertEqual(result, 21)

        client.disconnect()
        gevent_service.exit()

    @patch("cms.io.rpc.get_service_address")
    @patch("cms.io.async_service.get_service_address")
    async def test_gevent_client_calls_async_server(
        self, mock_async_address, mock_gevent_client_address
    ):
        import threading
        from cms.conf import ServiceCoord
        from cms.io.rpc import RemoteServiceClient

        mock_async_address.return_value = Address("127.0.0.1", 24682)
        # RemoteServiceClient resolves its remote coord's address via
        # cms.io.rpc's own imported get_service_address reference (not
        # cms.io.async_service's), so that lookup must be patched
        # separately -- mirrors the reverse-direction test above.
        mock_gevent_client_address.return_value = Address("127.0.0.1", 24682)

        service = EchoingAsyncService(shard=0)
        run_task = asyncio.create_task(service._async_run())
        # Give the server a moment to actually bind before connecting.
        await asyncio.sleep(0.05)
        self.assertTrue(service._server.is_serving())

        # RemoteServiceClient spawns greenlets (gevent.spawn) and blocks
        # on a gevent.event.AsyncResult.get(), both of which need an
        # active gevent hub to drive them; that hub is thread-affine, so
        # (mirroring the gevent-Service-in-a-thread pattern above) the
        # client is driven from its own thread rather than the test's
        # asyncio event loop thread.
        result_box: dict = {}
        client_done = threading.Event()

        def run_gevent_client():
            client = RemoteServiceClient(ServiceCoord("EchoingAsyncService", 0))
            try:
                client.connect()
                connected = client.wait_for_connection(timeout=2)
                if not connected:
                    result_box["error"] = "Never connected to the async server."
                    return
                result_box["value"] = client.execute_rpc(
                    "echo", {"string": "hello"}).get(timeout=2)
            except BaseException as error:  # noqa: BLE001 - surfaced in main thread
                # gevent.Timeout subclasses BaseException, not Exception,
                # so a plain "except Exception" would silently swallow a
                # stuck RPC instead of reporting it below.
                result_box["error"] = error
            finally:
                client.disconnect()
                client_done.set()

        client_thread = threading.Thread(target=run_gevent_client, daemon=True)
        client_thread.start()
        # A plain client_thread.join() here would block this coroutine's
        # OS thread -- which is also the thread driving the asyncio event
        # loop that EchoingAsyncService's server needs in order to accept
        # the connection and answer the RPC -- deadlocking both sides.
        # Waiting via run_in_executor keeps the event loop running while
        # the gevent client (in its own thread, with its own gevent hub)
        # does its blocking work.
        await asyncio.get_running_loop().run_in_executor(
            None, client_thread.join, 5)

        self.assertTrue(client_done.is_set(), "gevent client thread did not finish.")
        self.assertNotIn("error", result_box, str(result_box.get("error")))
        self.assertEqual(result_box.get("value"), "hello")

        service.exit()
        await asyncio.wait_for(run_task, timeout=2)


class TestRunInExecutorBridgePattern(unittest.IsolatedAsyncioTestCase):
    """Documents and proves the pattern future service migrations (2.4)
    will use to call blocking cms/db/ code from an AsyncService without
    blocking the event loop, until sub-project 2.3 makes cms/db/ itself
    async. Not tied to any real DB call -- a dummy blocking function
    stands in for "some synchronous, blocking call."""

    async def test_blocking_call_does_not_block_the_event_loop(self):
        import time as time_module

        def blocking_call():
            time_module.sleep(0.2)
            return "done"

        loop = asyncio.get_running_loop()

        # Prove the event loop stays responsive to other tasks while
        # the blocking call is running in a thread: a concurrently
        # scheduled task must be able to complete before the blocking
        # call returns.
        marker = []

        async def concurrent_task():
            await asyncio.sleep(0.05)
            marker.append("concurrent task ran")

        task = asyncio.create_task(concurrent_task())
        result = await loop.run_in_executor(None, blocking_call)

        self.assertEqual(result, "done")
        self.assertEqual(marker, ["concurrent task ran"])
        await task
