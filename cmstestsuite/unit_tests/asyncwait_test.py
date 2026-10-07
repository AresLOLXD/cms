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

"""Tests for the wait_until test helper."""

import asyncio
import unittest

from cmstestsuite.unit_tests.asyncwait import wait_until


class WaitUntilTest(unittest.IsolatedAsyncioTestCase):

    async def test_returns_once_the_predicate_becomes_true(self):
        events: list[str] = []
        asyncio.get_running_loop().call_later(0.05, events.append, "put")

        await wait_until(lambda: events, timeout=1)

        self.assertEqual(events, ["put"])

    async def test_timeout_fails_loudly_with_the_description(self):
        with self.assertRaises(AssertionError) as raised:
            await wait_until(
                lambda: False, timeout=0.1,
                describe=lambda: "received ['contests/']")

        self.assertIn("0.1", str(raised.exception))
        self.assertIn("received ['contests/']", str(raised.exception))

    async def test_timeout_without_description_still_fails(self):
        with self.assertRaises(AssertionError):
            await wait_until(lambda: False, timeout=0.1)


if __name__ == "__main__":
    unittest.main()
