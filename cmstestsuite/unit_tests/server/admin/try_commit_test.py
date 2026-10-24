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

"""Tests for the commit of the AWS handlers when the database fails."""

import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, InterfaceError, \
    OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from cms.db import User
from cms.server.admin.handlers import base
from cms.server.admin.handlers.base import BaseHandler
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

SQL = "UPDATE admins SET authentication = :secret_hash"
PARAMETERS = {"secret_hash": "s3cr3t-hash"}


class _DriverError(Exception):
    """A driver error, which may carry a SQLSTATE."""

    def __init__(self, message: str, pgcode: str | None = None):
        super().__init__(message)
        self.pgcode = pgcode


def _lost_connection() -> OperationalError:
    return OperationalError(
        SQL, PARAMETERS,
        _DriverError("server closed the connection unexpectedly"),
        connection_invalidated=True)


def _lock_timeout() -> OperationalError:
    return OperationalError(
        SQL, PARAMETERS,
        _DriverError("canceling statement due to lock timeout", "55P03"))


def _lost_interface() -> InterfaceError:
    return InterfaceError(
        SQL, PARAMETERS, _DriverError("connection already closed"),
        connection_invalidated=True)


def _pool_timeout() -> PoolTimeoutError:
    return PoolTimeoutError(
        "QueuePool limit of size 1 overflow 0 reached, connection timed out")


def _integrity_error() -> IntegrityError:
    return IntegrityError(
        SQL, PARAMETERS, _DriverError("duplicate key value", "23505"))


class TestTryCommitFailure(unittest.TestCase):

    def setUp(self):
        self.handler = BaseHandler.__new__(BaseHandler)
        # "service" is a read-only property of the request handler that
        # returns self.application.service.
        self.handler.application = MagicMock()
        self.handler.sql_session = MagicMock()
        self.handler.request = MagicMock(method="POST", path="/tasks/add")
        self.notification = self.handler.application.service.add_notification
        # The error log line is not noise in the output, and is checked.
        self.logger = patch.object(base, "logger").start()
        self.addCleanup(patch.stopall)

    def commit_failing_with(self, error: Exception) -> bool:
        self.handler.sql_session.commit.side_effect = error
        return self.handler.try_commit()

    def test_a_failed_commit_is_rolled_back_and_notified(self):
        for make_error in (_lost_connection, _lock_timeout, _lost_interface,
                           _pool_timeout, _integrity_error):
            with self.subTest(error=make_error.__name__):
                self.handler.sql_session.rollback.reset_mock()
                self.notification.reset_mock()

                committed = self.commit_failing_with(make_error())

                self.assertFalse(committed)
                self.handler.sql_session.rollback.assert_called_once_with()
                self.notification.assert_called_once()
                self.assertEqual(self.notification.call_args.args[1],
                                 "Operation failed.")

    def test_the_admin_is_told_to_try_again_when_the_database_is_lost(self):
        self.commit_failing_with(_lost_connection())

        self.assertEqual(self.notification.call_args.args[2],
                         "The database did not answer, try again.")

    def test_the_log_line_has_no_sql_text(self):
        self.commit_failing_with(_lost_connection())

        self.logger.error.assert_called_once()
        template, *arguments = self.logger.error.call_args.args
        message = template % tuple(arguments)
        self.assertIn("POST /tasks/add", message)
        self.assertIn("server closed the connection unexpectedly", message)
        self.assertNotIn("secret_hash", message)
        self.assertNotIn("s3cr3t", message)

    def test_an_integrity_error_keeps_its_text_in_the_notification(self):
        error = _integrity_error()
        self.commit_failing_with(error)

        self.assertEqual(self.notification.call_args.args[2], "%s" % error)

    def test_another_database_error_is_a_bug_and_propagates(self):
        error = ProgrammingError(SQL, PARAMETERS, _DriverError("syntax"))

        with self.assertRaises(ProgrammingError):
            self.commit_failing_with(error)

        self.notification.assert_not_called()

    def test_a_failing_rollback_does_not_mask_the_failure(self):
        self.handler.sql_session.rollback.side_effect = _lost_connection()

        committed = self.commit_failing_with(_lost_connection())

        self.assertFalse(committed)
        self.notification.assert_called_once()
        self.assertEqual(self.notification.call_args.args[1],
                         "Operation failed.")


class TestTryCommitRealSession(DatabaseMixin, unittest.TestCase):

    def test_a_failed_commit_leaves_the_session_usable(self):
        self.add_user(username="taken")
        self.session.commit()
        self.add_user(username="taken")
        handler = BaseHandler.__new__(BaseHandler)
        handler.application = MagicMock()
        handler.sql_session = self.session

        committed = handler.try_commit()

        self.assertFalse(committed)
        # Without the rollback, this raises PendingRollbackError.
        self.assertEqual(self.session.scalar(
            select(func.count()).select_from(User)), 1)
        notification = handler.application.service.add_notification
        notification.assert_called_once()
        self.assertEqual(notification.call_args.args[1], "Operation failed.")


if __name__ == "__main__":
    unittest.main()
