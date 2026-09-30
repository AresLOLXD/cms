#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2016 Stefano Maggiolo <s.maggiolo@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Tests for the flushing dict module."""

import asyncio
import unittest

from cms.service.flushingdict import FlushingDict


class TestFlushingDict(unittest.IsolatedAsyncioTestCase):

    SIZE = 3
    FLUSH_LATENCY_SECONDS = 0.2

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.received_data = []
        self._started_dicts = []
        self.d = self._make_dict(self.callback)

    def _make_dict(self, callback):
        d = FlushingDict(
            TestFlushingDict.SIZE, TestFlushingDict.FLUSH_LATENCY_SECONDS,
            callback)
        d.start()
        self._started_dicts.append(d)
        return d

    async def asyncTearDown(self):
        for d in self._started_dicts:
            d._flush_task.cancel()
        await super().asyncTearDown()

    async def test_success_latency(self):
        self.d.add(0, 0)
        await asyncio.sleep(2 * TestFlushingDict.FLUSH_LATENCY_SECONDS)
        self.assertEqual(1, len(self.received_data))
        self.assertCountEqual([(0, 0)], self.received_data[0])

    async def test_success_size(self):
        expected_data = []
        for i in range(TestFlushingDict.SIZE):
            self.d.add(i, i)
            expected_data.append((i, i))
        await asyncio.sleep(0.1)
        self.assertEqual(1, len(self.received_data))
        self.assertCountEqual(expected_data, self.received_data[0])

    async def test_success_size_latency(self):
        expected_data = []
        for i in range(TestFlushingDict.SIZE):
            self.d.add(i, i)
            expected_data.append((i, i))
        await asyncio.sleep(0.1)
        self.assertEqual(1, len(self.received_data))
        self.assertCountEqual(expected_data, self.received_data[0])
        self.d.add(TestFlushingDict.SIZE, TestFlushingDict.SIZE)
        await asyncio.sleep(TestFlushingDict.FLUSH_LATENCY_SECONDS + 0.1)
        self.assertEqual(2, len(self.received_data))
        self.assertCountEqual(
            [(TestFlushingDict.SIZE, TestFlushingDict.SIZE)],
            self.received_data[1])

    async def test_long_callback(self):
        self.d = self._make_dict(self.long_callback)
        expected_data = []
        for i in range(TestFlushingDict.SIZE):
            self.d.add(i, i)
            expected_data.append((i, i))

        # We add another element while the callback is idling, to see
        # if the new element is flushed later. We need to wait 2
        # latencies for the callback, one more for the time-based
        # second flush, and we add some tolerance.
        await asyncio.sleep(0.1)
        self.d.add(TestFlushingDict.SIZE, TestFlushingDict.SIZE)
        await asyncio.sleep((TestFlushingDict.FLUSH_LATENCY_SECONDS + 0.1) * 3)

        self.assertEqual(TestFlushingDict.SIZE + 1,
                         sum(len(data) for data in self.received_data))

    async def test_many_elements(self):
        expected_data = []
        for i in range(20):
            self.d.add(i, i)
            expected_data.append((i, i))
        await asyncio.sleep(TestFlushingDict.FLUSH_LATENCY_SECONDS + 0.1)
        self.assertCountEqual(expected_data, sum(self.received_data, []))

    # -- in-flight batches and discard ------------------------------------

    def _make_unstarted_dict(self, callback):
        """Build a dict with no background flush task.

        Only the explicit flush() calls of the test flush it, so what is
        pending and what is in flight is up to the test alone.

        callback: the callback to flush to.

        return: the dict.

        """
        return FlushingDict(
            TestFlushingDict.SIZE, TestFlushingDict.FLUSH_LATENCY_SECONDS,
            callback)

    def _held_callback(self):
        """Return a callback that keeps every flush in flight until released.

        return: the callback, the list each call's items are appended
            to (as the callback received them), and the event that
            lets every call return.

        """
        calls: list[list] = []
        release = asyncio.Event()

        async def callback(items):
            calls.append(items)
            await release.wait()

        return callback, calls, release

    async def _wait_for_calls(self, calls: list, count: int):
        """Let the loop run until the callback has been called count times.

        calls: the list the held callback appends to.
        count: how many calls to wait for.

        """
        for _ in range(100):
            if len(calls) >= count:
                return
            await asyncio.sleep(0)
        self.fail("The callback was called %d time(s), not %d."
                  % (len(calls), count))

    async def test_discard_removes_pending_entries(self):
        d = self._make_unstarted_dict(self.callback)
        kept, dropped = object(), object()
        d.add("kept", kept)
        d.add("dropped", dropped)

        removed = d.discard(lambda key: key == "dropped")

        self.assertEqual(len(removed), 1)
        self.assertIs(removed[0], dropped)
        self.assertNotIn("dropped", d)
        self.assertIn("kept", d)
        await d.flush()
        self.assertEqual(self.received_data, [[("kept", kept)]])

    async def test_discard_removes_in_flight_entries(self):
        callback, calls, release = self._held_callback()
        d = self._make_unstarted_dict(callback)
        value, other = object(), object()
        d.add("key", value)
        d.add("other", other)
        flush = asyncio.create_task(d.flush())
        await self._wait_for_calls(calls, 1)
        self.assertIn("key", d)
        self.assertEqual(d.fd, {"key": value, "other": other})

        removed = d.discard(lambda key: key == "key")

        # The very object the callback got: marking it reaches the write.
        self.assertEqual(len(removed), 1)
        self.assertIs(removed[0], value)
        self.assertIs(dict(calls[0])["key"], value)
        self.assertNotIn("key", d)
        self.assertEqual(d.fd, {"other": other})
        release.set()
        await flush
        self.assertEqual(d.fd, {})
        self.assertEqual(d._flushing, [])

    async def test_overlapping_flushes_are_both_tracked(self):
        callback, calls, release = self._held_callback()
        d = self._make_unstarted_dict(callback)
        first, second = object(), object()
        d.add("first", first)
        first_flush = asyncio.create_task(d.flush())
        await self._wait_for_calls(calls, 1)
        d.add("second", second)
        second_flush = asyncio.create_task(d.flush())
        await self._wait_for_calls(calls, 2)

        # The second flush did not hide the first one's batch.
        self.assertEqual(len(d._flushing), 2)
        self.assertIn("first", d)
        self.assertIn("second", d)
        self.assertEqual(d.fd, {"first": first, "second": second})
        removed = d.discard(lambda key: True)
        self.assertCountEqual([id(value) for value in removed],
                              [id(first), id(second)])
        self.assertNotIn("first", d)
        self.assertNotIn("second", d)

        release.set()
        await asyncio.gather(first_flush, second_flush)
        self.assertEqual(d._flushing, [])
        self.assertEqual(d.fd, {})

    async def test_failed_flush_drops_its_batch(self):
        async def failing_callback(items):
            raise RuntimeError("cannot write")

        d = self._make_unstarted_dict(failing_callback)
        d.add("key", object())

        with self.assertLogs(
                "cms.service.flushingdict", level="ERROR") as logs:
            await d.flush()

        self.assertEqual([record.getMessage() for record in logs.records],
                         ["Unexpected error while flushing."])
        self.assertNotIn("key", d)
        self.assertEqual(d._flushing, [])
        self.assertEqual(d.fd, {})

    async def callback(self, data):
        self.received_data.append(data)

    async def long_callback(self, data):
        await asyncio.sleep(TestFlushingDict.FLUSH_LATENCY_SECONDS * 2)
        await self.callback(data)


if __name__ == "__main__":
    unittest.main()
