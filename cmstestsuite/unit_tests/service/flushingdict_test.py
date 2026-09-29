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

import unittest

import gevent

from cms.service.flushingdict import FlushingDict


class TestFlushingDict(unittest.TestCase):

    SIZE = 3
    FLUSH_LATENCY_SECONDS = 0.2

    def setUp(self):
        super().setUp()
        self.received_data = []
        self.d = FlushingDict(
            TestFlushingDict.SIZE, TestFlushingDict.FLUSH_LATENCY_SECONDS,
            self.callback)

    def test_success_latency(self):
        self.d.add(0, 0)
        gevent.sleep(2 * TestFlushingDict.FLUSH_LATENCY_SECONDS)
        self.assertEqual(1, len(self.received_data))
        self.assertCountEqual([(0, 0)], self.received_data[0])

    def test_success_size(self):
        expected_data = []
        for i in range(TestFlushingDict.SIZE):
            self.d.add(i, i)
            expected_data.append((i, i))
        gevent.sleep(0)
        self.assertEqual(1, len(self.received_data))
        self.assertCountEqual(expected_data, self.received_data[0])

    def test_success_size_latency(self):
        expected_data = []
        for i in range(TestFlushingDict.SIZE):
            self.d.add(i, i)
            expected_data.append((i, i))
        gevent.sleep(0)
        self.assertEqual(1, len(self.received_data))
        self.assertCountEqual(expected_data, self.received_data[0])
        self.d.add(TestFlushingDict.SIZE, TestFlushingDict.SIZE)
        gevent.sleep(TestFlushingDict.FLUSH_LATENCY_SECONDS + 0.1)
        self.assertEqual(2, len(self.received_data))
        self.assertCountEqual(
            [(TestFlushingDict.SIZE, TestFlushingDict.SIZE)],
            self.received_data[1])

    def test_long_callback(self):
        self.d = FlushingDict(
            TestFlushingDict.SIZE, TestFlushingDict.FLUSH_LATENCY_SECONDS,
            self.long_callback)
        expected_data = []
        for i in range(TestFlushingDict.SIZE):
            self.d.add(i, i)
            expected_data.append((i, i))

        # We add another element while the callback is idling, to see
        # if the new element is flushed later. We need to wait 2
        # latencies for the callback, one more for the time-based
        # second flush, and we add some tolerance.
        gevent.sleep(0)
        self.d.add(TestFlushingDict.SIZE, TestFlushingDict.SIZE)
        gevent.sleep((TestFlushingDict.FLUSH_LATENCY_SECONDS + 0.1) * 3)

        self.assertEqual(TestFlushingDict.SIZE + 1,
                         sum(len(data) for data in self.received_data))

    def test_many_elements(self):
        expected_data = []
        for i in range(20):
            self.d.add(i, i)
            expected_data.append((i, i))
        gevent.sleep(TestFlushingDict.FLUSH_LATENCY_SECONDS + 0.1)
        self.assertCountEqual(expected_data, sum(self.received_data, []))

    def test_callback_error_is_logged_and_drops_its_batch(self):
        # The batch that the callback raised on is not kept as being
        # flushed: its keys are not in the dict any more.
        self.d = FlushingDict(
            TestFlushingDict.SIZE, TestFlushingDict.FLUSH_LATENCY_SECONDS,
            self.failing_callback)
        with self.assertLogs("cms.service.flushingdict", level="ERROR"):
            self.d.add(0, 0)
            gevent.sleep(2 * TestFlushingDict.FLUSH_LATENCY_SECONDS)

        self.assertEqual(1, len(self.received_data))
        self.assertEqual({}, self.d.fd)
        self.assertNotIn(0, self.d)

    def test_flushing_goes_on_after_callback_error(self):
        self.d = FlushingDict(
            TestFlushingDict.SIZE, TestFlushingDict.FLUSH_LATENCY_SECONDS,
            self.failing_callback)
        self.d.add(0, 0)
        gevent.sleep(2 * TestFlushingDict.FLUSH_LATENCY_SECONDS)
        self.d.add(1, 1)
        gevent.sleep(2 * TestFlushingDict.FLUSH_LATENCY_SECONDS)

        # The second batch got to the callback, which raised on the
        # first one only.
        self.assertEqual([[(0, 0)], [(1, 1)]], self.received_data)
        self.assertEqual({}, self.d.fd)

    def callback(self, data):
        self.received_data.append(data)

    def failing_callback(self, data):
        """Raise on the first batch, and go on normally after that."""
        self.received_data.append(data)
        if len(self.received_data) == 1:
            raise RuntimeError("Simulated failure of the callback.")

    def long_callback(self, data):
        gevent.sleep(TestFlushingDict.FLUSH_LATENCY_SECONDS * 2)
        self.callback(data)


if __name__ == "__main__":
    unittest.main()
