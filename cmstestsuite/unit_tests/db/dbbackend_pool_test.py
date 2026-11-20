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

"""Tests that DBBackend never holds two pool connections at once.

A LargeObject takes its connection from the SQLAlchemy pool. If
DBBackend opened one while its own session still held a connection,
enough concurrent operations would each hold one connection and wait for
a second one, and the pool would deadlock until pool_timeout.

"""

import threading
import unittest
from unittest.mock import patch

from cms.db import FSObject, LargeObject, engine
from cms.db.filecacher import DBBackend
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin


class DBBackendPoolTest(DatabaseMixin, unittest.TestCase):

    def test_concurrent_operations_do_not_exhaust_the_pool(self):
        digest = "a" * 40
        self.add_fsobject(digest, b"content")

        # As many threads as pool connections, so that all of them can
        # hold a session at once.
        threads_count = engine.pool.size() + engine.pool._max_overflow
        barrier = threading.Barrier(threads_count, timeout=10)
        original = FSObject.get_from_digest

        def get_from_digest_at_barrier(digest, session):
            # Every thread has its session's connection here: wait until
            # all of them do, which forces the bad interleaving.
            fso = original(digest, session)
            barrier.wait()
            return fso

        errors = []

        def work():
            try:
                with DBBackend().get_file(digest) as fobj:
                    self.assertEqual(fobj.read(), b"content")
                fobj = DBBackend().create_file("b" * 40)
                loid = fobj.loid
                fobj.close()
                LargeObject.unlink(loid)
                self.assertEqual(DBBackend().get_size(digest), 7)
            except Exception as error:
                errors.append(error)

        with patch.object(engine.pool, "_timeout", 3), \
                patch.object(FSObject, "get_from_digest",
                             staticmethod(get_from_digest_at_barrier)):
            threads = [threading.Thread(target=work)
                       for _ in range(threads_count)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
