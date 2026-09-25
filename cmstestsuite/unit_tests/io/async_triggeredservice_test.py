#!/usr/bin/env python3

"""Tests for cms.io.async_triggeredservice."""

import asyncio
import unittest
from unittest.mock import patch

from cms.io.async_triggeredservice import AsyncExecutor, AsyncTriggeredService
from cms.io.priorityqueue import QueueItem


class FakeOperation(QueueItem):
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return self.name == other.name

    def __hash__(self):
        return hash(self.name)

    def to_dict(self):
        return {"name": self.name}


class RecordingExecutor(AsyncExecutor):
    def __init__(self):
        super().__init__(batch_executions=False)
        self.executed: list = []

    async def execute(self, entry):
        self.executed.append(entry.item)


class RecordingTriggeredService(AsyncTriggeredService):
    pass


class TestEnqueueDequeue(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_enqueue_then_dequeue(self, mock_get_address):
        mock_get_address.return_value = ("127.0.0.1", 0)
        service = RecordingTriggeredService(shard=0)
        executor = RecordingExecutor()
        service.add_executor(executor)
        op = FakeOperation("compile")

        self.assertEqual(service.enqueue(op), 1)  # 1 executor accepted it
        self.assertEqual(service.enqueue(op), 0)   # duplicate, rejected by push()

        service.dequeue(op)
        self.assertNotIn(op, executor)


class TestSweeper(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_search_operations_not_done_wakes_sweeper_early(
        self, mock_get_address
    ):
        mock_get_address.return_value = ("127.0.0.1", 0)

        sweeps: list[int] = []

        class SweepingService(AsyncTriggeredService):
            def _missing_operations(self):
                sweeps.append(1)
                return 0

        service = SweepingService(shard=0)
        # A long timeout: only search_operations_not_done() should wake
        # this loop within the test's timeframe, not the timeout itself.
        service.start_sweeper(timeout=60)
        await asyncio.sleep(0.05)
        self.assertEqual(len(sweeps), 1, "expected exactly the initial sweep")

        service.search_operations_not_done()
        await asyncio.sleep(0.05)
        self.assertEqual(len(sweeps), 2,
                         "search_operations_not_done() should trigger an "
                         "immediate second sweep, not wait for the 60s timeout")


class TestRunLoop(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_run_dispatches_enqueued_operation(self, mock_get_address):
        mock_get_address.return_value = ("127.0.0.1", 0)
        service = RecordingTriggeredService(shard=0)
        executor = RecordingExecutor()
        service.add_executor(executor)

        op = FakeOperation("evaluate")
        service.enqueue(op)

        await asyncio.sleep(0.1)

        self.assertEqual(executor.executed, [op])


class TestConstructionOutsideEventLoop(unittest.TestCase):
    """The scripts/cms* launchers build the service before run() starts
    the event loop, so __init__-time connect_to/add_timeout/
    add_executor/start_sweeper must not need a running loop."""

    def test_init_time_scheduling_starts_once_running(self):
        import socket
        import threading
        from cms.conf import Address, ServiceCoord

        def free_port():
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                return probe.getsockname()[1]

        service_port = free_port()
        # Nothing listens here: the LogService client just keeps retrying.
        log_service_port = free_port()

        def address_of(coord):
            if coord.name == "LogService":
                return Address("127.0.0.1", log_service_port)
            return Address("127.0.0.1", service_port)

        happened: set[str] = set()
        op = FakeOperation("compile")

        class LauncherBuiltService(AsyncTriggeredService):
            def __init__(self, shard):
                super().__init__(shard)
                # The service connects to itself, so the connection is
                # real without needing a second service.
                self.connect_to(
                    ServiceCoord("LauncherBuiltService", 0),
                    on_connect=lambda _: self._mark("connected"))
                self.executor = MarkingExecutor(self)
                self.add_executor(self.executor)
                self.enqueue(op)
                self.add_timeout(lambda: self._mark("timeout"), None,
                                 seconds=0.01)
                self.start_sweeper(timeout=60)

            def _missing_operations(self):
                self._mark("swept")
                return 0

            def _mark(self, what):
                happened.add(what)
                if happened >= {"connected", "executed", "timeout", "swept"}:
                    self.exit()

        class MarkingExecutor(RecordingExecutor):
            def __init__(self, service):
                super().__init__()
                self.service = service

            async def execute(self, entry):
                await super().execute(entry)
                self.service._mark("executed")

        with patch("cms.io.async_service.get_service_address",
                   side_effect=address_of), \
                patch("cms.io.async_rpc.get_service_address",
                      side_effect=address_of):
            service = LauncherBuiltService(0)
            # Safety net so a regression fails instead of hanging.
            watchdog = threading.Timer(5, service.exit)
            watchdog.start()
            try:
                self.assertTrue(service.run())
            finally:
                watchdog.cancel()

        self.assertEqual(
            happened, {"connected", "executed", "timeout", "swept"})
        self.assertEqual(service.executor.executed, [op])
