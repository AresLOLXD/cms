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

"""Tests for the pool timeout constant of both engines (#38)."""

import unittest

from cms.db import POOL_TIMEOUT_S, async_engine, engine
from cms.db import connect_options


class TestPoolTimeout(unittest.TestCase):

    def test_both_engines_use_the_constant(self):
        self.assertEqual(POOL_TIMEOUT_S, 60)
        self.assertIs(POOL_TIMEOUT_S, connect_options.POOL_TIMEOUT_S)
        self.assertEqual(engine.pool.timeout(), POOL_TIMEOUT_S)
        self.assertEqual(async_engine.sync_engine.pool.timeout(),
                         POOL_TIMEOUT_S)


if __name__ == "__main__":
    unittest.main()
