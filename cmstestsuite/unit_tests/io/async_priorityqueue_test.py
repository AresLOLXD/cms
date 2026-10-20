#!/usr/bin/env python3

"""Tests for cms.io.async_priorityqueue."""

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from cms.io.async_priorityqueue import AsyncPriorityQueue
from cms.io.priorityqueue import QueueItem


class FakeItem(QueueItem):
    def __init__(self, title):
        self._title = title

    def __eq__(self, other):
        return self._title == other._title

    def __hash__(self):
        return hash(self._title)

    def __str__(self):
        return self._title


class TestAsyncPriorityQueueBasics(unittest.IsolatedAsyncioTestCase):

    async def test_push_and_pop_respects_priority(self):
        queue = AsyncPriorityQueue()
        low = FakeItem("low")
        high = FakeItem("high")
        queue.push(low, priority=AsyncPriorityQueue.PRIORITY_LOW)
        queue.push(high, priority=AsyncPriorityQueue.PRIORITY_HIGH)

        first = await queue.pop()
        self.assertEqual(first.item, high)
        second = await queue.pop()
        self.assertEqual(second.item, low)

    async def test_pop_without_wait_raises_on_empty(self):
        queue = AsyncPriorityQueue()
        with self.assertRaises(LookupError):
            await queue.pop(wait=False)


class TestAsyncPriorityQueueDefaultTimestamp(
    unittest.IsolatedAsyncioTestCase,
):
    """The default timestamp must not go backwards with the clock."""

    T0 = datetime(2026, 10, 19, 12, 0, 0)

    def set_clock(self, *times):
        patcher = patch("cms.io.async_priorityqueue.make_datetime",
                        side_effect=times)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_default_timestamps_never_go_backwards(self):
        self.set_clock(self.T0, self.T0 - timedelta(hours=1),
                       self.T0 - timedelta(hours=2))
        queue = AsyncPriorityQueue()
        items = [FakeItem(name) for name in "abc"]
        for item in items:
            queue.push(item)

        popped = [(await queue.pop()).item for _ in items]
        self.assertEqual(popped, items)

    async def test_default_timestamps_are_strictly_increasing_when_the_clock_does_not_move(
        self,
    ):
        self.set_clock(self.T0, self.T0, self.T0)
        queue = AsyncPriorityQueue()
        for name in "abc":
            queue.push(FakeItem(name))

        stamps = [(await queue.pop()).timestamp for _ in range(3)]
        self.assertTrue(stamps[0] < stamps[1] < stamps[2], stamps)

    async def test_explicit_timestamps_are_kept(self):
        self.set_clock(self.T0)
        queue = AsyncPriorityQueue()
        queue.push(FakeItem("default"))
        later = self.T0 + timedelta(days=1)
        earlier = self.T0 - timedelta(days=1)
        queue.push(FakeItem("x"), timestamp=later)
        queue.push(FakeItem("y"), timestamp=earlier)

        # The earlier submission goes first, as ES needs for fairness.
        first = await queue.pop()
        self.assertEqual((first.item, first.timestamp),
                         (FakeItem("y"), earlier))
        self.assertEqual(queue._last_default_timestamp, self.T0)


class TestAsyncPriorityQueueBlockingWait(unittest.IsolatedAsyncioTestCase):
    """Review Focus item 3: a test that only exercises the non-blocking
    path wouldn't catch a wait() that returns immediately without
    truly waiting. This test proves pop(wait=True) genuinely blocks
    until a concurrent push, not before."""

    async def test_pop_wait_true_blocks_until_concurrent_push(self):
        queue = AsyncPriorityQueue()
        item = FakeItem("delayed")

        events: list[str] = []

        async def popper():
            entry = await queue.pop(wait=True)
            events.append("popped")
            return entry

        async def pusher():
            await asyncio.sleep(0.1)
            events.append("about to push")
            queue.push(item)

        pop_task = asyncio.create_task(popper())
        push_task = asyncio.create_task(pusher())

        # Give the popper a chance to start waiting before the pusher
        # runs -- if pop(wait=True) doesn't actually block, "popped"
        # would appear before "about to push".
        await asyncio.sleep(0.02)
        self.assertFalse(pop_task.done(),
                         "pop(wait=True) returned before any item was "
                         "pushed -- it isn't actually blocking.")

        entry = await pop_task
        await push_task

        self.assertEqual(events, ["about to push", "popped"])
        self.assertEqual(entry.item, item)
