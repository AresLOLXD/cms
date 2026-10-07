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
import time
import unittest

from cms.service.flushingdict import FlushingDict
from cmstestsuite.unit_tests.asyncwait import wait_until


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

    # -- max age ----------------------------------------------------------

    # With these, neither the size nor the quiet time can flush a dict
    # within a test: only its max age can.
    AGED_SIZE = 1000
    AGED_FLUSH_LATENCY_SECONDS = 60

    def _make_unstarted_aged_dict(self, callback, max_age_seconds):
        """Build a dict that only its max age can flush, with no task.

        callback: the callback to flush to.
        max_age_seconds: the max age of the dict (None for no limit).

        return: the dict, not started.

        """
        return FlushingDict(
            TestFlushingDict.AGED_SIZE,
            TestFlushingDict.AGED_FLUSH_LATENCY_SECONDS,
            callback, max_age_seconds=max_age_seconds)

    def _start_dict(self, d):
        """Start the background flush loop of a dict, cancelled on teardown.

        d: the dict to start.

        """
        d.start()
        self._started_dicts.append(d)

    def _make_aged_dict(self, callback, max_age_seconds):
        """Build and start a dict that only its max age can flush.

        callback: the callback to flush to.
        max_age_seconds: the max age of the dict (None for no limit).

        return: the dict.

        """
        d = self._make_unstarted_aged_dict(callback, max_age_seconds)
        self._start_dict(d)
        return d

    async def test_max_age_flushes_a_single_entry(self):
        d = self._make_aged_dict(self.callback, 0.3)

        added_at = time.monotonic()
        d.add("key", "value")
        await wait_until(lambda: self.received_data,
                         describe=lambda: repr(self.received_data))
        waited = time.monotonic() - added_at

        self.assertEqual(self.received_data, [[("key", "value")]])
        # The flush came from the age, not before it.
        self.assertGreaterEqual(waited, 0.3)

    async def test_max_age_flushes_a_steady_stream(self):
        max_age = 0.3
        count = 30
        inserted_at: dict[int, float] = {}
        flushed_at: dict[int, float] = {}

        async def callback(items):
            now = time.monotonic()
            for key, _ in items:
                flushed_at[key] = now
            self.received_data.append(items)

        d = self._make_aged_dict(callback, max_age)

        # An entry every 0.05 s: there is never a quiet period.
        for i in range(count):
            inserted_at[i] = time.monotonic()
            d.add(i, i)
            await asyncio.sleep(0.05)
        flushes_during_stream = len(self.received_data)
        await wait_until(
            lambda: len(flushed_at) == count,
            describe=lambda: "%d of %d entries flushed"
            % (len(flushed_at), count))

        self.assertGreaterEqual(flushes_during_stream, 1)
        self.assertGreaterEqual(len(self.received_data), 2)
        # Every entry reached the callback exactly once.
        self.assertEqual(
            sorted(key for items in self.received_data for key, _ in items),
            list(range(count)))
        # The background check runs every 0.05 s. The tolerance is
        # generous so that a loaded machine does not fail the test, yet
        # far from the 60 s the quiet time and the size would take.
        longest_wait = max(flushed_at[i] - inserted_at[i]
                           for i in range(count))
        self.assertLess(longest_wait, max_age + 2.0)

    async def test_no_max_age_keeps_the_old_behavior(self):
        d = self._make_aged_dict(self.callback, None)

        d.add("key", "value")
        # Nothing to wait for: we check that nothing happens.
        await asyncio.sleep(0.5)

        self.assertEqual(self.received_data, [])
        self.assertIn("key", d)
        self.assertEqual(d.d, {"key": "value"})

    async def test_max_age_restarts_after_a_flush(self):
        d = self._make_aged_dict(self.callback, 0.3)
        d.add("first", 1)
        await wait_until(lambda: len(self.received_data) == 1)
        self.assertIsNone(d.oldest_insert)

        added_at = time.monotonic()
        d.add("second", 2)
        await wait_until(lambda: len(self.received_data) == 2,
                         describe=lambda: repr(self.received_data))
        waited = time.monotonic() - added_at

        self.assertEqual(self.received_data, [[("first", 1)], [("second", 2)]])
        # The age of the first entry did not flush the second at once.
        self.assertGreaterEqual(waited, 0.3)

    async def test_discard_emptying_the_dict_resets_the_age(self):
        max_age = 0.5
        d = self._make_unstarted_aged_dict(self.callback, max_age)
        d.add("old", 1)
        # The entry looks as if it had been waiting for a long time,
        # so that a leftover age could not pass for a fresh one. What
        # is pinned here is the invariant that oldest_insert is None
        # exactly when the dict is empty.
        d.oldest_insert = time.monotonic() - 10
        d.discard(lambda key: True)
        self.assertEqual(d.d, {})
        self.assertIsNone(d.oldest_insert)

        added_at = time.monotonic()
        d.add("new", 2)
        self._start_dict(d)
        await wait_until(lambda: self.received_data,
                         describe=lambda: repr(self.received_data))
        waited = time.monotonic() - added_at

        self.assertEqual(self.received_data, [[("new", 2)]])
        self.assertGreaterEqual(waited, max_age)

    async def test_entry_added_during_a_flush_gets_its_own_age(self):
        max_age = 0.3
        callback, calls, release = self._held_callback()
        d = self._make_aged_dict(callback, max_age)
        d.add("first", 1)
        await wait_until(lambda: len(calls) == 1,
                         describe=lambda: repr(calls))
        # The first entry is in flight, held by the callback.
        self.assertIn("first", d)
        self.assertIsNone(d.oldest_insert)

        added_at = time.monotonic()
        d.add("second", 2)
        self.assertGreaterEqual(d.oldest_insert, added_at)
        release.set()
        await wait_until(lambda: len(calls) == 2,
                         describe=lambda: repr(calls))
        waited = time.monotonic() - added_at

        self.assertEqual(calls, [[("first", 1)], [("second", 2)]])
        # The second entry waited for its own age, not for the age of
        # the first one, which was already over when it was added.
        self.assertGreaterEqual(waited, max_age)

    async def test_oldest_insert_tracking(self):
        d = self._make_unstarted_aged_dict(self.callback, 1)
        self.assertIsNone(d.oldest_insert)

        before = time.monotonic()
        d.add("a", 1)
        after = time.monotonic()
        first_oldest = d.oldest_insert
        self.assertIsNotNone(first_oldest)
        self.assertTrue(before <= first_oldest <= after)

        # Neither a new key nor an overwrite changes the oldest time.
        d.add("b", 2)
        self.assertEqual(d.oldest_insert, first_oldest)
        d.add("a", 3)
        self.assertEqual(d.oldest_insert, first_oldest)

        # Removing only some entries keeps it (it can only flush early).
        d.discard(lambda key: key == "a")
        self.assertEqual(d.oldest_insert, first_oldest)

        # Emptying the dict, by discard or by flush, resets it.
        d.discard(lambda key: key == "b")
        self.assertIsNone(d.oldest_insert)
        d.add("c", 4)
        self.assertGreaterEqual(d.oldest_insert, first_oldest)
        await d.flush()
        self.assertIsNone(d.oldest_insert)

    async def callback(self, data):
        self.received_data.append(data)

    async def long_callback(self, data):
        await asyncio.sleep(TestFlushingDict.FLUSH_LATENCY_SECONDS * 2)
        await self.callback(data)


if __name__ == "__main__":
    unittest.main()
