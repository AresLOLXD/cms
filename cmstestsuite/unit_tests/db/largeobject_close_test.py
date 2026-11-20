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

"""Tests that LargeObject returns the connection it checks out (issue #64).

Every LargeObject owns a connection of the SQLAlchemy pool. CPython
frees it when the object dies, but not while something still references
the object (an exception traceback, a stored lobject, a greenlet frame),
and the pool would then run dry. A spy records each connection fsobject
checks out and keeps it alive, so these tests only pass if the code
returns the connection explicitly.

"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cms.db import LargeObject, engine
from cms.db.filecacher import FileCacher
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin


class LargeObjectCloseTest(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        # Every pooled connection fsobject checked out.
        self.opened = []
        raw_connection = engine.raw_connection

        def spy():
            conn = raw_connection()
            self.opened.append(conn)
            return conn

        # Patch the name fsobject uses: patching the engine itself would
        # also record the connections of every session.
        patcher = patch("cms.db.fsobject.engine",
                        SimpleNamespace(raw_connection=spy))
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_all_closed(self):
        """Check the recorded connections went back to the pool.

        """
        self.assertTrue(self.opened, "No connection was recorded.")
        for conn in self.opened:
            # A checked-in pool proxy has let go of its DB-API connection.
            self.assertIsNone(conn.dbapi_connection)

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
        # Each test stores its own content: the database outlives the test.
        content = self.id().encode()
        file_cacher = FileCacher()
        digest = file_cacher.put_file_content(content, "Test #064")
        os.unlink(os.path.join(file_cacher.file_dir, digest))
        self.assertEqual(file_cacher.get_file_content(digest), content)
        self.assertEqual(len(self.opened), 2)
        self.assert_all_closed()

    def test_delete_releases_unlink_connection(self):
        file_cacher = FileCacher()
        digest = file_cacher.put_file_content(self.id().encode(), "Test #064")
        self.opened.clear()
        file_cacher.backend.delete(digest)
        self.assertEqual(len(self.opened), 1)
        self.assert_all_closed()

    def test_failed_copy_closes_the_destination(self):
        file_cacher = FileCacher()
        with patch("cms.db.filecacher.copyfileobj",
                   side_effect=OSError("copy failed")):
            # Keep the exception, hence the frame holding fobj, alive.
            try:
                file_cacher.put_file_content(self.id().encode(), "Test #064")
            except OSError as error:
                held = error
            else:
                self.fail("The copy did not fail.")
        self.assertIsNotNone(held)
        self.assertEqual(len(self.opened), 1)
        self.assert_all_closed()

    def test_failed_copy_keeps_the_original_error(self):
        def interrupt(src, dst, size):
            # What green psycopg2 does to the connection when a wait is
            # interrupted: close it, then let the interruption propagate.
            dst._conn.close()
            raise KeyboardInterrupt()

        file_cacher = FileCacher()
        with patch("cms.db.filecacher.copyfileobj", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                file_cacher.put_file_content(self.id().encode(), "Test #064")


if __name__ == "__main__":
    unittest.main()
