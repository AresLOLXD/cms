#!/usr/bin/env python3

"""Asyncio-based base classes for services relying on notifications and
sweeper loops, mirroring cms.io.triggeredservice.

"""

from abc import ABCMeta, abstractmethod
from datetime import datetime
import asyncio
import logging
import time
import typing

from cms.io.rpc import rpc_method
from cms.io.priorityqueue import QueueEntry, QueueEntryDict, QueueItemT
from .async_priorityqueue import AsyncPriorityQueue
from .async_service import AsyncService


logger = logging.getLogger(__name__)


class AsyncExecutor(typing.Generic[QueueItemT], metaclass=ABCMeta):
    """A class taking care of executing operations.

    See cms.io.triggeredservice.Executor for the full contract this
    mirrors (batch vs. one-at-a-time execution).

    """

    def __init__(self, batch_executions: bool = False):
        super().__init__()

        self._batch_executions = batch_executions
        self._operation_queue: AsyncPriorityQueue[QueueItemT] = AsyncPriorityQueue()

    def __contains__(self, item: QueueItemT) -> bool:
        return item in self._operation_queue

    def get_status(self) -> list[QueueEntryDict]:
        return self._operation_queue.get_status()

    def enqueue(
        self,
        item: QueueItemT,
        priority: int | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        return self._operation_queue.push(item, priority, timestamp)

    def dequeue(self, item: QueueItemT) -> QueueEntry[QueueItemT]:
        return self._operation_queue.remove(item)

    async def _pop(self, wait: bool = False) -> QueueEntry[QueueItemT]:
        return await self._operation_queue.pop(wait=wait)

    async def run(self):
        """Monitor the queue, and dispatch operations when available.

        See cms.io.triggeredservice.Executor.run for the full
        contract: an infinite loop, blocking until an element is
        present, then dispatching it (or a batch) to execute().

        """
        while True:
            to_execute = [await self._pop(wait=True)]
            if self._batch_executions:
                max_operations = self.max_operations_per_batch()
                while not self._operation_queue.empty() and (
                        max_operations == 0 or
                        len(to_execute) < max_operations):
                    to_execute.append(await self._pop())

            assert len(to_execute) > 0, "Expected at least one element."
            if self._batch_executions:
                try:
                    logger.info("Executing operations `%s' and %d more.",
                                to_execute[0].item, len(to_execute) - 1)
                    await self.execute(to_execute)
                    logger.info("Operations `%s' and %d more concluded "
                                "successfully.", to_execute[0].item,
                                len(to_execute) - 1)
                except Exception:
                    logger.error(
                        "Unexpected error when executing operation "
                        "`%s' (and %d more operations).", to_execute[0].item,
                        len(to_execute) - 1, exc_info=True)

            else:
                try:
                    logger.info("Executing operation `%s'.",
                                to_execute[0].item)
                    await self.execute(to_execute[0])
                    logger.info("Operation `%s' concluded successfully",
                                to_execute[0].item)
                except Exception:
                    logger.error(
                        "Unexpected error when executing operation `%s'.",
                        to_execute[0].item, exc_info=True)

    def max_operations_per_batch(self) -> int:
        """See cms.io.triggeredservice.Executor.max_operations_per_batch.

        Base implementation returns 0 (no limit) -- subclasses using
        batch_executions=True override this the same way Executor
        subclasses do today; not abstract, since a non-batch executor
        never calls it.

        """
        return 0

    @abstractmethod
    async def execute(self, entry: QueueEntry[QueueItemT] | list[QueueEntry[QueueItemT]]):
        """Perform a single operation (or a batch).

        async def, not sync, unlike Executor.execute: a real 2.4
        migration's implementation will need to await RPCs to other
        services and run_in_executor-wrapped DB calls -- see the
        spec's DB bridge pattern.

        """
        pass


ExecutorT = typing.TypeVar("ExecutorT", bound=AsyncExecutor)


class AsyncTriggeredService(AsyncService, typing.Generic[QueueItemT, ExecutorT]):
    """A service receiving notifications to perform an operation.

    See cms.io.triggeredservice.TriggeredService for the full
    contract this mirrors: a list of executors (each running its own
    task), plus a sweeper task that periodically searches for missed
    operations.

    """

    def __init__(self, shard: int):
        AsyncService.__init__(self, shard)

        self._executors: list[ExecutorT] = []

        self._sweeper_start: float | None = None
        self._sweeper_event = asyncio.Event()
        self._sweeper_started = False
        self._sweeper_timeout: float | None = None

    def add_executor(self, executor: ExecutorT):
        """Add an executor for the service, and start its run loop."""
        self._executors.append(executor)
        self._call_when_running(lambda: self._spawn(executor.run()))

    def get_executor(self) -> ExecutorT:
        return self._executors[0]

    def enqueue(
        self,
        operation: QueueItemT,
        priority: int | None = None,
        timestamp: datetime | None = None,
    ) -> int:
        """See cms.io.triggeredservice.TriggeredService.enqueue.

        return: the number of executors that successfully added
            the operation to their queue.

        """
        ret = 0
        for executor in self._executors:
            if executor.enqueue(operation, priority, timestamp):
                ret += 1
        return ret

    def dequeue(self, operation: QueueItemT):
        for executor in self._executors:
            executor.dequeue(operation)

    def start_sweeper(self, timeout: float):
        """Start sweeper loop with given timeout."""
        if not self._sweeper_started:
            self._sweeper_started = True
            self._sweeper_timeout = timeout

            self._call_when_running(lambda: self._spawn(self._sweeper_loop()))
        else:
            logger.warning("Service tried to start the sweeper loop twice.")

    async def _sweeper_loop(self):
        """Regularly check for missed operations.

        See cms.io.triggeredservice.TriggeredService._sweeper_loop for
        the full contract: run the sweep once every _sweeper_timeout
        seconds, but a new sweep can be triggered early by
        search_operations_not_done() setting _sweeper_event.

        """
        while True:
            self._sweeper_start = time.monotonic()
            self._sweeper_event.clear()

            try:
                self._sweep()
            except Exception:
                logger.error("Unexpected error when searching for missed "
                             "operations.", exc_info=True)

            remaining = max(self._sweeper_start + self._sweeper_timeout -
                            time.monotonic(), 0)
            try:
                await asyncio.wait_for(self._sweeper_event.wait(), remaining)
            except asyncio.TimeoutError:
                # Expected: gevent.event.Event.wait(timeout) returns
                # False (rather than raising) on timeout, and the loop
                # continues identically either way -- this except
                # clause is that same "continue regardless" behavior,
                # just via asyncio.wait_for's raise-on-timeout API
                # instead of a bool return value.
                pass

    def _sweep(self):
        """Check for missed operations."""
        logger.info("Start looking for missing operations.")
        start_time = time.time()
        counter = self._missing_operations()
        logger.info("Found %d missed operation(s) in %d ms.",
                    counter, (time.time() - start_time) * 1000)

    def _missing_operations(self) -> int:
        """See cms.io.triggeredservice.TriggeredService._missing_operations.

        Base implementation returns 0 -- subclasses override.

        """
        return 0

    @rpc_method
    def search_operations_not_done(self):
        """Make the sweeper loop fire the sweeper as soon as possible."""
        self._sweeper_event.set()

    @rpc_method
    def queue_status(self) -> list[list[QueueEntryDict]]:
        return [executor.get_status() for executor in self._executors]
