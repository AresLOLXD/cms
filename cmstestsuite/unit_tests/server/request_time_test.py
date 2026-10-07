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
        for value in (None, "", "   ", "abc", "t=", "-5", "1e12", "t=12,5",
                      "99999999999999999999999"):
            with self.subTest(value=value):
                self.assertIsNone(parse_request_time_header(value))


class ArrivalTimeTest(unittest.TestCase):

    def test_handler_time_alone(self):
        self.assertEqual(request_arrival_time(T, None, None), (T, "handler"))

    def test_tornado_elapsed_moves_it_back(self):
        self.assertEqual(request_arrival_time(T, 2.5, None),
                         (T - timedelta(seconds=2.5), "tornado"))

    def test_header_wins_when_earliest(self):
        header = T - timedelta(seconds=9)
        self.assertEqual(request_arrival_time(T, 2.5, header),
                         (header, "header"))

    def test_tornado_wins_over_later_header(self):
        header = T - timedelta(seconds=1)
        self.assertEqual(request_arrival_time(T, 2.5, header),
                         (T - timedelta(seconds=2.5), "tornado"))

    def test_future_header_ignored(self):
        self.assertEqual(
            request_arrival_time(T, None, T + timedelta(seconds=30)),
            (T, "handler"))

    def test_clamped_to_max_skew(self):
        self.assertEqual(MAX_SKEW, timedelta(seconds=60))
        header = T - timedelta(hours=1)
        self.assertEqual(request_arrival_time(T, None, header),
                         (T - MAX_SKEW, "header"))

    def test_negative_or_zero_elapsed_ignored(self):
        self.assertEqual(request_arrival_time(T, 0.0, None), (T, "handler"))
        self.assertEqual(request_arrival_time(T, -1.0, None), (T, "handler"))


if __name__ == "__main__":
    unittest.main()
