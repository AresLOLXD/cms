#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
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

"""Tests for the request arrival time helper."""

import unittest
from datetime import datetime, timedelta

from cms.server.request_time import (MAX_SKEW, parse_request_time_header,
                                     request_arrival_time)

T = datetime(2026, 10, 10, 20, 0, 0)  # the handler time in these tests
EPOCH = datetime(1970, 1, 1)


def unix(dt: datetime) -> float:
    return (dt - EPOCH).total_seconds()


class ParseHeaderTest(unittest.TestCase):

    def test_seconds_with_fraction(self):
        self.assertEqual(parse_request_time_header("%.3f" % unix(T)), T)

    def test_milliseconds_integer(self):
        self.assertEqual(
            parse_request_time_header("%d" % (unix(T) * 1000)), T)

    def test_t_prefix(self):
        self.assertEqual(
            parse_request_time_header("t=%d" % (unix(T) * 1000)), T)
        self.assertEqual(
            parse_request_time_header("t=%.3f" % unix(T)), T)

    def test_garbage_is_ignored(self):
        # The last value is in Arabic-Indic digits, which float() reads.
        for value in (None, "", "   ", "abc", "t=", "-5", "1e12", "t=12,5",
                      "99999999999999999999999", "\u0661\u0662\u0663"):
            with self.subTest(value=value):
                self.assertIsNone(parse_request_time_header(value))


class ArrivalTimeTest(unittest.TestCase):

    def test_handler_time_without_a_header(self):
        self.assertEqual(request_arrival_time(T, None), T)

    def test_header_moves_the_time_back(self):
        header = T - timedelta(seconds=9)
        self.assertEqual(request_arrival_time(T, header), header)

    def test_header_at_the_handler_time(self):
        self.assertEqual(request_arrival_time(T, T), T)

    def test_future_header_ignored(self):
        self.assertEqual(
            request_arrival_time(T, T + timedelta(seconds=30)), T)

    def test_clamped_to_max_skew(self):
        self.assertEqual(MAX_SKEW, timedelta(seconds=60))
        self.assertEqual(
            request_arrival_time(T, T - timedelta(hours=1)), T - MAX_SKEW)
        # The bound itself is allowed, and so is a custom one.
        self.assertEqual(
            request_arrival_time(T, T - MAX_SKEW), T - MAX_SKEW)
        self.assertEqual(
            request_arrival_time(T, T - timedelta(seconds=20),
                                 max_skew=timedelta(seconds=5)),
            T - timedelta(seconds=5))

    def test_zero_header_is_clamped(self):
        # "0" is the Unix epoch: far before the handler time.
        self.assertEqual(
            request_arrival_time(T, parse_request_time_header("0")),
            T - MAX_SKEW)


if __name__ == "__main__":
    unittest.main()
