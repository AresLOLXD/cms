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

"""Tests for RankingGroup.archive_at (ranking-group-archiving spec)."""

import unittest
from datetime import datetime, timedelta

from cms.db import RankingGroup
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin


NOW = datetime(2026, 10, 28, 12, 0, 0)
MICROSECOND = timedelta(microseconds=1)


def make_group(archive_at: datetime | None) -> RankingGroup:
    return RankingGroup(name="olim", description="OLIM", archive_at=archive_at)


class TestRankingGroupArchivedAt(unittest.TestCase):

    def test_before_archive_at(self):
        # Scenario "Before archive_at".
        group = make_group(NOW + timedelta(hours=1))
        self.assertFalse(group.is_archived_at(NOW))

    def test_at_and_after_archive_at(self):
        # Scenario "At and after archive_at".
        self.assertTrue(make_group(NOW).is_archived_at(NOW))
        self.assertTrue(make_group(NOW - MICROSECOND).is_archived_at(NOW))
        self.assertFalse(make_group(NOW + MICROSECOND).is_archived_at(NOW))

        # Stays archived only while archive_at is not moved to the future.
        group = make_group(NOW - timedelta(days=1))
        self.assertTrue(group.is_archived_at(NOW))
        group.archive_at = NOW + timedelta(hours=1)
        self.assertFalse(group.is_archived_at(NOW))

    def test_no_archive_at(self):
        # Scenario "No archive_at".
        self.assertFalse(make_group(None).is_archived_at(datetime(2100, 1, 1)))


class TestRankingGroupArchiveColumn(DatabaseMixin, unittest.TestCase):

    def test_the_column_defaults_to_empty(self):
        # Supports "Updating an existing database": a new group is empty.
        group = RankingGroup(name="olim", description="OLIM")
        self.session.add(group)
        self.session.commit()
        self.session.refresh(group)
        self.assertIsNone(group.archive_at)


if __name__ == "__main__":
    unittest.main()
