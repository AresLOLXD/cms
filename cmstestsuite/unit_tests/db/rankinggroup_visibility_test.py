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


if __name__ == "__main__":
    unittest.main()
