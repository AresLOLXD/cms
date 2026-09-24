#!/usr/bin/env python3

"""Tests for cms.io.async_priorityqueue."""

import asyncio
import unittest

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
