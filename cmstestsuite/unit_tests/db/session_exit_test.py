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

"""Tests for the exit of SessionGen and AsyncSessionGen when the
connection is lost (#39, 8c).

"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from cms.db import SessionGen, engine
from cms.db.async_session import AsyncSessionGen, async_engine
from cms.db.connect_options import log_rollback_error
from cmstestsuite.unit_tests.dbfault import terminate_backends


LOST = ("Lost the database connection before the session's rollback: "
        "server closed the connection unexpectedly")


def _lost_connection_error() -> OperationalError:
    return OperationalError(
        "ROLLBACK", {},
        Exception("server closed the connection unexpectedly"),
        connection_invalidated=True)


class TestLogRollbackError(unittest.TestCase):
    """The rule, without a database."""

    def test_after_a_failed_block_any_error_is_debug(self):
        with self.assertLogs("cms.db.connect_options", "DEBUG") as logs:
            self.assertTrue(log_rollback_error(ValueError("x"), True))
        self.assertEqual([r.levelname for r in logs.records], ["DEBUG"])

    def test_a_lost_connection_after_a_good_block_is_a_warning(self):
        with self.assertLogs("cms.db.connect_options", "DEBUG") as logs:
            self.assertTrue(
                log_rollback_error(_lost_connection_error(), False))
        self.assertEqual([(r.levelname, r.getMessage())
                          for r in logs.records], [("WARNING", LOST)])

    def test_another_error_after_a_good_block_must_propagate(self):
        with self.assertNoLogs("cms.db.connect_options", "DEBUG"):
            self.assertFalse(log_rollback_error(ValueError("x"), False))


class TestSessionGenExit(unittest.TestCase):

    def test_a_lost_connection_after_a_good_block_is_not_an_error(self):
        checked_out = engine.pool.checkedout()
        with self.assertLogs("cms.db.connect_options", "WARNING") as logs:
            with SessionGen() as session:
                pid = session.execute(
                    text("SELECT pg_backend_pid()")).scalar_one()
                session.close = MagicMock(wraps=session.close)
                terminate_backends("pid = %s", (pid,))
        # The driver's text varies with the libpq version: check the
        # fixed part only.
        self.assertTrue(logs.records[0].getMessage().startswith(
            "Lost the database connection before the session's rollback: "))
        # The failed rollback already released the connection, so the
        # pool count below cannot tell whether close() ran: the spy can.
        session.close.assert_called_once_with()
        self.assertEqual(engine.pool.checkedout(), checked_out)

    def test_another_rollback_error_still_propagates(self):
        checked_out = engine.pool.checkedout()
        with self.assertRaises(ValueError):
            with SessionGen() as session:
                session.execute(text("SELECT 1"))
                session.rollback = MagicMock(
                    side_effect=ValueError("rollback failed"))
        self.assertEqual(engine.pool.checkedout(), checked_out)

    def test_the_block_error_wins_over_a_rollback_error(self):
        checked_out = engine.pool.checkedout()
        with self.assertLogs("cms.db.connect_options", "DEBUG") as logs:
            with self.assertRaises(KeyError):
                with SessionGen() as session:
                    pid = session.execute(
                        text("SELECT pg_backend_pid()")).scalar_one()
                    session.close = MagicMock(wraps=session.close)
                    terminate_backends("pid = %s", (pid,))
                    raise KeyError("the block's own error")
        self.assertEqual([r.levelname for r in logs.records], ["DEBUG"])
        session.close.assert_called_once_with()
        self.assertEqual(engine.pool.checkedout(), checked_out)

    def test_an_interrupted_rollback_after_a_failed_block_propagates(self):
        # KeyboardInterrupt is not an Exception: log_rollback_error must
        # not get the chance to swallow it, and close() must still run.
        checked_out = engine.pool.checkedout()
        with self.assertRaises(KeyboardInterrupt):
            with SessionGen() as session:
                session.execute(text("SELECT 1"))
                session.rollback = MagicMock(side_effect=KeyboardInterrupt())
                session.close = MagicMock(wraps=session.close)
                raise KeyError("the block's own error")
        session.close.assert_called_once_with()
        self.assertEqual(engine.pool.checkedout(), checked_out)


class TestAsyncSessionGenExit(unittest.IsolatedAsyncioTestCase):

    async def asyncTearDown(self):
        await async_engine.dispose()

    @staticmethod
    def _checked_out() -> int:
        return async_engine.sync_engine.pool.checkedout()

    async def test_a_lost_connection_after_a_good_block_is_not_an_error(self):
        checked_out = self._checked_out()
        with self.assertLogs("cms.db.connect_options", "WARNING") as logs:
            async with AsyncSessionGen() as session:
                pid = (await session.execute(
                    text("SELECT pg_backend_pid()"))).scalar_one()
                session.close = AsyncMock(wraps=session.close)
                terminate_backends("pid = %s", (pid,))
        self.assertTrue(logs.records[0].getMessage().startswith(
            "Lost the database connection before the session's rollback: "))
        # As in the sync test, the pool count alone does not prove it.
        session.close.assert_awaited_once_with()
        self.assertEqual(self._checked_out(), checked_out)

    async def test_another_rollback_error_still_propagates(self):
        checked_out = self._checked_out()
        with self.assertRaises(ValueError):
            async with AsyncSessionGen() as session:
                await session.execute(text("SELECT 1"))
                session.rollback = AsyncMock(
                    side_effect=ValueError("rollback failed"))
        self.assertEqual(self._checked_out(), checked_out)

    async def test_the_block_error_wins_over_a_rollback_error(self):
        checked_out = self._checked_out()
        with self.assertLogs("cms.db.connect_options", "DEBUG") as logs:
            with self.assertRaises(KeyError):
                async with AsyncSessionGen() as session:
                    pid = (await session.execute(
                        text("SELECT pg_backend_pid()"))).scalar_one()
                    session.close = AsyncMock(wraps=session.close)
                    terminate_backends("pid = %s", (pid,))
                    raise KeyError("the block's own error")
        self.assertEqual([r.levelname for r in logs.records], ["DEBUG"])
        session.close.assert_awaited_once_with()
        self.assertEqual(self._checked_out(), checked_out)

    async def test_a_cancelled_rollback_still_propagates_and_closes(self):
        checked_out = self._checked_out()
        with self.assertRaises(asyncio.CancelledError):
            async with AsyncSessionGen() as session:
                await session.execute(text("SELECT 1"))
                session.rollback = AsyncMock(
                    side_effect=asyncio.CancelledError())
        self.assertEqual(self._checked_out(), checked_out)

    async def test_a_cancelled_rollback_after_a_failed_block_propagates(self):
        # CancelledError is not an Exception: log_rollback_error must
        # not get the chance to swallow it, even when the block failed.
        checked_out = self._checked_out()
        with self.assertRaises(asyncio.CancelledError):
            async with AsyncSessionGen() as session:
                await session.execute(text("SELECT 1"))
                session.rollback = AsyncMock(
                    side_effect=asyncio.CancelledError())
                raise KeyError("the block's own error")
        self.assertEqual(self._checked_out(), checked_out)


if __name__ == "__main__":
    unittest.main()
