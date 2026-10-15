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

"""Cut database connections from a test, as a failing network would."""

import time

from cms.db import custom_psycopg2_connection


def terminate_backends(condition: str, params: tuple = ()) -> int:
    """End the backends that match a condition, and wait until they are gone.

    Only client backends of the current database match, and never the
    killer's own, so a test cannot cut the connections of another
    database or the server's own processes on a shared PostgreSQL.

    condition: an SQL condition on the columns of pg_stat_activity,
        with %s placeholders. The driver reads it for placeholders even
        when params is empty, so a literal % must be written %%.
    params: the values of the placeholders.

    return: how many backends were ended.

    raise (AssertionError): if one is still there after 10 seconds.

    """
    killer = custom_psycopg2_connection()
    try:
        killer.autocommit = True
        cursor = killer.cursor()
        cursor.execute(
            "SELECT pid FROM pg_stat_activity "
            "WHERE pid <> pg_backend_pid() "
            "AND datname = current_database() "
            "AND backend_type = 'client backend' "
            "AND (" + condition + ")", params)
        pids = [pid for (pid,) in cursor.fetchall()]
        for pid in pids:
            cursor.execute("SELECT pg_terminate_backend(%s)", (pid,))
        deadline = time.monotonic() + 10
        while pids:
            cursor.execute("SELECT count(*) FROM pg_stat_activity "
                           "WHERE pid = ANY(%s)", (pids,))
            if cursor.fetchone()[0] == 0:
                break
            if time.monotonic() > deadline:
                raise AssertionError(
                    "backends %s still there after 10 s" % pids)
            time.sleep(0.1)
        return len(pids)
    finally:
        killer.close()
