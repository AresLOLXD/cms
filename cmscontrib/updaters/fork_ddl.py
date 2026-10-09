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

"""Apply a fork schema update without queueing CMS behind its locks.

An idempotent ALTER TABLE or CREATE INDEX still takes its lock when it
changes nothing, and while it waits for that lock every later query on
the table waits behind it: every Contest Web Server freezes. So an
update gives up once it has waited 5 s for one lock, that update then
changes nothing, and cmsSetupDB tells the operator what to do (#39).
The bound is per lock wait: an update that waits for several tables in
turn can queue CMS for up to 5 s behind each of them (with two holders,
an update gave up after 9 s). There is no retry: each try would freeze
the tables again, and running the setup again is safe.

"""

import psycopg2.errors

from cms.db import custom_psycopg2_connection


class SchemaLockTimeout(Exception):
    """A fork schema update waited 5 s for a lock on one of its tables."""

    def __init__(self, tables: str) -> None:
        """Create the error, with the message cmsSetupDB prints.

        tables: the tables the update locks, as the message names them
            (PostgreSQL's lock-timeout error names none).

        """
        super().__init__(
            "cmsSetupDB could not lock %s within 5 s: another session is "
            "using them. That update changed nothing. Find that session "
            "with the holder query in the contest-day runbook (\"When the "
            "database misbehaves\"), wait for it to finish or end it, then "
            "run the setup again." % tables)
        self.tables = tables


def apply_fork_sql(sql: str, tables: str) -> None:
    """Apply one fork schema update, in one transaction.

    sql: the update's statements.
    tables: the tables they lock, for the message of SchemaLockTimeout.

    raise (SchemaLockTimeout): if one lock was not granted within
        5 s (each wait has its own 5 s, so the whole update can take
        longer); the transaction is rolled back, so this update
        changes nothing.

    """
    conn = custom_psycopg2_connection()
    try:
        cursor = conn.cursor()
        # The same transaction as the update: psycopg2 does not
        # autocommit, and SET LOCAL ends with the transaction.
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        try:
            cursor.execute(sql)
        except psycopg2.errors.LockNotAvailable as error:
            raise SchemaLockTimeout(tables) from error
        conn.commit()
    finally:
        conn.close()
