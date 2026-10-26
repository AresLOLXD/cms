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

"""Tests for the time zone lookups of the datetime module."""

import unittest
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cmscommon import datetime as cms_datetime
from cmscommon.datetime import get_system_timezone, get_timezone, local_tz

LOGGER = "cmscommon.datetime"
MEXICO_CITY = ZoneInfo("America/Mexico_City")


def _contest(name: str | None) -> SimpleNamespace:
    return SimpleNamespace(timezone=name)


class TestGetTimezone(unittest.TestCase):

    def setUp(self):
        cms_datetime._find_timezone.cache_clear()
        self.addCleanup(cms_datetime._find_timezone.cache_clear)

    def test_a_name_that_cannot_be_resolved_gives_the_server_zone(self):
        for name in ("America/Mexico_City/", "../x", "Foo/Bar", "America",
                     "a/" * 3000):
            with self.subTest(name=name[:30]), self.assertLogs(LOGGER):
                self.assertIs(get_timezone(None, _contest(name)), local_tz)

    def test_an_empty_name_gives_the_server_zone_without_a_warning(self):
        for name in ("", None):
            with self.subTest(name=name), \
                    self.assertNoLogs(LOGGER, "WARNING"):
                self.assertIs(get_timezone(None, _contest(name)), local_tz)

    def test_a_bad_name_is_warned_once_per_process(self):
        with self.assertLogs(LOGGER, "WARNING") as logs:
            for _ in range(3):
                get_timezone(None, _contest("Foo/Bar"))

        self.assertEqual(len(logs.records), 1)
        self.assertIn("Foo/Bar", logs.output[0])

    def test_a_valid_name_gives_its_zone_without_a_warning(self):
        with self.assertNoLogs(LOGGER, "WARNING"):
            zone = get_timezone(None, _contest("America/Mexico_City"))

        self.assertEqual(zone.key, "America/Mexico_City")

    def test_a_bad_user_zone_falls_back_to_the_contest_zone(self):
        user = SimpleNamespace(timezone="Foo/Bar")

        with self.assertLogs(LOGGER, "WARNING"):
            zone = get_timezone(user, _contest("America/Mexico_City"))

        self.assertEqual(zone.key, "America/Mexico_City")


class TestGetSystemTimezone(unittest.TestCase):

    def test_a_zoneinfo_zone_gives_its_iana_name(self):
        with patch.object(cms_datetime, "local_tz", MEXICO_CITY):
            self.assertEqual(get_system_timezone(), "America/Mexico_City")

    def test_a_keyless_zone_gives_its_abbreviation(self):
        zone = timezone(timedelta(hours=-6), "XYZ")

        with patch.object(cms_datetime, "local_tz", zone):
            self.assertEqual(get_system_timezone(), "XYZ")


if __name__ == "__main__":
    unittest.main()
