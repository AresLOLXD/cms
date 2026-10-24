#!/usr/bin/env python3

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

"""Tests for the time formatting helpers of AWS."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cms.server.admin.formatting import clock_zones, format_utc_time


NOW = datetime(2026, 10, 10, 20, 30, 15)


class TestFormatUtcTime(unittest.TestCase):

    def test_drops_microseconds_and_names_the_zone(self):
        self.assertEqual(
            format_utc_time(datetime(2026, 10, 10, 20, 30, 15, 987654)),
            "2026-10-10 20:30:15 UTC")

    def test_none_is_empty(self):
        self.assertEqual(format_utc_time(None), "")


class TestClockZones(unittest.TestCase):

    def zones(self, contest_timezone, has_contest=True):
        """Return the rows for a server in a fixed -06:00 zone."""
        contest = SimpleNamespace(timezone=contest_timezone) \
            if has_contest else None
        server = timezone(timedelta(hours=-6), "XYZ")
        with patch("cms.server.admin.formatting.local_tz", server), \
                patch("cms.server.admin.formatting.get_system_timezone",
                      return_value="Test/Zone"):
            return clock_zones(NOW, contest)

    def test_contest_zone_follows_utc_and_the_server_zone(self):
        self.assertEqual(self.zones("Asia/Kolkata"), [
            {"label": "UTC", "offset_seconds": 0},
            {"label": "Test/Zone (XYZ, UTC-06:00)",
             "offset_seconds": -6 * 3600},
            {"label": "Asia/Kolkata (IST, UTC+05:30)",
             "offset_seconds": 5 * 3600 + 1800},
        ])

    def test_no_contest_zone_is_utc_and_the_server_zone(self):
        self.assertEqual(
            [row["label"] for row in self.zones(None)],
            ["UTC", "Test/Zone (XYZ, UTC-06:00)"])

    def test_unresolvable_contest_zone_is_left_out(self):
        # An unknown name, and a malformed key (a ValueError in zoneinfo).
        for name in ("Not/AZone", "../etc/passwd"):
            with self.subTest(name=name):
                self.assertEqual(
                    [row["label"] for row in self.zones(name)],
                    ["UTC", "Test/Zone (XYZ, UTC-06:00)"])

    def test_a_zoneinfo_server_zone_is_named_by_its_key(self):
        with patch("cms.server.admin.formatting.local_tz",
                   ZoneInfo("America/Mexico_City")):
            rows = clock_zones(NOW, None)
        self.assertEqual(
            [row["label"] for row in rows],
            ["UTC", "America/Mexico_City (CST, UTC-06:00)"])

    def test_contest_zone_equal_to_the_server_zone_is_not_repeated(self):
        with patch("cms.server.admin.formatting.local_tz",
                   ZoneInfo("America/Mexico_City")):
            rows = clock_zones(
                NOW, SimpleNamespace(timezone="America/Mexico_City"))
        self.assertEqual(len(rows), 2)

    def test_a_utc_server_and_contest_add_no_rows(self):
        with patch("cms.server.admin.formatting.local_tz",
                   ZoneInfo("Etc/UTC")):
            rows = clock_zones(NOW, SimpleNamespace(timezone="UTC"))
        self.assertEqual(rows, [{"label": "UTC", "offset_seconds": 0}])

    def test_no_contest_is_utc_and_the_server_zone(self):
        self.assertEqual(
            [row["label"] for row in self.zones(None, has_contest=False)],
            ["UTC", "Test/Zone (XYZ, UTC-06:00)"])


if __name__ == "__main__":
    unittest.main()
