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

"""Tests that LargeObject closes the connection it opens (issue #64).

Every LargeObject owns a non-pooled psycopg2 connection. CPython frees
it when the object dies, but not while something still references the
object (an exception traceback, a stored lobject, a greenlet frame). A
spy records each connection fsobject opens and keeps it alive, so these
tests only pass if the code closes the connection explicitly.

"""

import os
import time
import unittest
from unittest.mock import patch

from sqlalchemy import text

from cms.db import LargeObject, custom_psycopg2_connection
from cms.db.filecacher import FileCacher
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin


class LargeObjectCloseTest(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        # (connection, backend pid) of every connection fsobject opened.
        self.opened = []

        def spy(**kwargs):
            conn = custom_psycopg2_connection(**kwargs)
            self.opened.append((conn, conn.info.backend_pid))
            return conn

        patcher = patch("cms.db.fsobject.custom_psycopg2_connection", spy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_all_closed(self):
        """Check the recorded connections are closed, client and server side.

        """
        self.assertTrue(self.opened, "No connection was recorded.")
        for conn, _ in self.opened:
            self.assertNotEqual(conn.closed, 0)

        # The test database server is shared, so look only at our pids.
        pids = [pid for _, pid in self.opened]
        deadline = time.monotonic() + 2
        while True:
            alive = self.session.execute(
                text("SELECT pid FROM pg_stat_activity WHERE pid = ANY(:pids)"),
                {"pids": pids}).all()
            # pg_stat_activity is a snapshot per transaction.
            self.session.rollback()
            if not alive or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        self.assertEqual(alive, [])

    def test_close_releases_connection(self):
        lobject = LargeObject(0, "wb")
        lobject.write(b"content")
        lobject.close()
        self.assert_all_closed()
        # Closing twice does nothing.
        lobject.close()

    def test_failed_open_releases_connection(self):
        # Keep the exception, hence the half-built object, alive.
        try:
            LargeObject(999999999, "rb")
        except OSError as error:
            held = error
        else:
            self.fail("Opening a missing large object did not fail.")
        self.assertIsNotNone(held)
        self.assert_all_closed()

    def test_file_cacher_round_trip(self):
        file_cacher = FileCacher()
        digest = file_cacher.put_file_content(b"content", "Test #064")
        os.unlink(os.path.join(file_cacher.file_dir, digest))
        self.assertEqual(file_cacher.get_file_content(digest), b"content")
        self.assertEqual(len(self.opened), 2)
        self.assert_all_closed()

    def test_delete_releases_unlink_connection(self):
        file_cacher = FileCacher()
        digest = file_cacher.put_file_content(b"content", "Test #064")
        self.opened.clear()
        file_cacher.backend.delete(digest)
        self.assertEqual(len(self.opened), 1)
        self.assert_all_closed()


if __name__ == "__main__":
    unittest.main()
