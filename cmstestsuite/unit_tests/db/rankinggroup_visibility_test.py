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

"""Tests for the MC-2 visibility columns of ranking groups."""

import unittest
from datetime import datetime, timedelta

from cms.db import RankingGroup, custom_psycopg2_connection
from cmscontrib.updaters.fork_multi_contest import FORK_MULTI_CONTEST_SQL
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin


def run_sql(sql: str) -> list[tuple]:
    """Execute sql in its own connection and return the fetched rows."""
    conn = custom_psycopg2_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall() if cursor.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


class TestRankingGroupVisibilityColumns(DatabaseMixin, unittest.TestCase):

    def test_defaults(self):
        group = RankingGroup(name="olim", description="OLIM")
        self.session.add(group)
        self.session.commit()
        self.session.refresh(group)
        self.assertIs(group.hidden, False)
        self.assertIsNone(group.staff_password)

    def test_fork_sql_upgrades_a_pre_mc2_table(self):
        # Simulate a database created before MC-2, with one group.
        run_sql("ALTER TABLE ranking_groups DROP COLUMN hidden, "
                "DROP COLUMN staff_password; "
                "INSERT INTO ranking_groups (name, description) "
                "VALUES ('old', 'Old');")
        # Idempotent: applying it twice must not fail.
        run_sql(FORK_MULTI_CONTEST_SQL)
        run_sql(FORK_MULTI_CONTEST_SQL)

        rows = run_sql("SELECT hidden, staff_password FROM ranking_groups "
                       "WHERE name = 'old';")
        self.assertEqual(rows, [(False, None)])
        defaults = run_sql(
            "SELECT column_name, column_default, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'ranking_groups' "
            "AND column_name IN ('hidden', 'staff_password') "
            "ORDER BY column_name;")
        self.assertEqual(defaults, [("hidden", None, "NO"),
                                    ("staff_password", None, "YES")])

    def test_windows(self):
        now = datetime(2026, 10, 10, 13, 0)
        group = RankingGroup(name="olim", description="O",
                             hide_at=now, show_at=now + timedelta(hours=3),
                             freeze_at=now - timedelta(hours=1))
        self.assertTrue(group.is_hidden_at(now))
        self.assertFalse(group.is_frozen_at(now))      # hidden wins
        self.assertTrue(group.is_frozen_at(now - timedelta(minutes=30)))
        self.assertTrue(group.hide_pending_at(now - timedelta(hours=1)))
        self.assertFalse(group.hide_pending_at(now + timedelta(hours=3)))

    def test_fork_sql_migrates_hidden_to_hide_at(self):
        # Rows written by MC-2 minimal: one hidden, one visible, no windows.
        run_sql("INSERT INTO ranking_groups (name, description, hidden) "
                "VALUES ('was_hidden', 'H', true), "
                "('was_visible', 'V', false);")
        # Idempotent: applying it twice must not fail or move hide_at.
        run_sql(FORK_MULTI_CONTEST_SQL)
        first = run_sql("SELECT hide_at FROM ranking_groups "
                        "WHERE name = 'was_hidden';")
        run_sql(FORK_MULTI_CONTEST_SQL)
        rows = dict(run_sql("SELECT name, hide_at IS NOT NULL "
                            "FROM ranking_groups "
                            "WHERE name IN ('was_hidden', 'was_visible');"))
        self.assertEqual(rows, {"was_hidden": True, "was_visible": False})
        self.assertEqual(run_sql("SELECT hide_at FROM ranking_groups "
                                 "WHERE name = 'was_hidden';"), first)
        late = run_sql("SELECT hide_at > (now() AT TIME ZONE 'UTC') "
                       "FROM ranking_groups WHERE name = 'was_hidden';")
        self.assertEqual(late, [(False,)])


if __name__ == "__main__":
    unittest.main()
