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

"""Tests for cms.db.connect_options, the connection defaults (#39)."""

import asyncio
import os
import socket
import sys
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import IntegrityError, InternalError, \
    OperationalError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from cms.conf import config
from cms.db import custom_psycopg2_connection, engine
from cms.db.async_session import _async_database_url, async_engine
from cms.db.connect_options import LIBPQ_DEFAULTS, PROGRAM_NAME, \
    describe_error, is_disconnect, program_name, sqlstate, \
    with_connection_defaults


# The suffix of the runbook (docs/contest-day.md, "Switching the
# timeouts off"). Keep the two identical.
KILL_SWITCH = ("?connect_timeout=0&keepalives_idle=7200"
               "&keepalives_interval=75&keepalives_count=9"
               "&tcp_user_timeout=0")

# SO_KEEPALIVE, TCP_KEEPIDLE, TCP_KEEPINTVL, TCP_KEEPCNT and
# TCP_USER_TIMEOUT of a connection's socket.
DEFAULT_SOCKET = (1, 30, 10, 3, 60000)
KILL_SWITCH_SOCKET = (1, 7200, 75, 9, 0)

EXPECTED_DEFAULTS = {
    "connect_timeout": "10",
    "keepalives": "1",
    "keepalives_idle": "30",
    "keepalives_interval": "10",
    "keepalives_count": "3",
    "tcp_user_timeout": "60000",
    "fallback_application_name": PROGRAM_NAME,
}


def with_kill_switch(url: str) -> str:
    """Return url with KILL_SWITCH added, as the runbook says to.

    url: a database URL, with or without a query.

    return: the URL with the suffix, joined with "&" when url already
        has a query.

    """
    if "?" in url:
        return url + "&" + KILL_SWITCH[1:]
    return url + KILL_SWITCH


def _database_over_tcp() -> bool:
    """Tell whether CMS_CONFIG reaches the test database over TCP."""
    url = make_url(config.database.url)
    host = url.query.get("host", url.host)
    return isinstance(host, str) and not host.startswith("/")


def _socket_options(fileno: int) -> tuple[int, int, int, int, int]:
    """Return the keepalive and user-timeout options of a socket.

    fileno: the socket's file descriptor; it stays open.

    return: SO_KEEPALIVE, TCP_KEEPIDLE, TCP_KEEPINTVL, TCP_KEEPCNT and
        TCP_USER_TIMEOUT.

    """
    sock = socket.socket(fileno=os.dup(fileno))
    try:
        return (sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE),
                sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE),
                sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL),
                sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT),
                sock.getsockopt(socket.IPPROTO_TCP,
                                socket.TCP_USER_TIMEOUT))
    finally:
        sock.close()


def _custom_connection_state() -> tuple[tuple[int, ...], str]:
    """Return the socket options and the application_name of a new
    custom_psycopg2_connection().

    """
    connection = custom_psycopg2_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SHOW application_name")
        return _socket_options(connection.fileno()), cursor.fetchone()[0]
    finally:
        connection.close()


async def _async_engine_state(
    some_engine: AsyncEngine
) -> tuple[tuple[int, ...], str]:
    """Return the socket options and the application_name of a
    connection of an async engine.

    """
    async with some_engine.connect() as connection:
        raw = await connection.get_raw_connection()
        fileno = raw.dbapi_connection.driver_connection.fileno()
        name = (await connection.execute(
            text("SHOW application_name"))).scalar_one()
        return _socket_options(fileno), name


class _DriverError(Exception):
    """A stand-in for a driver error, with or without a SQLSTATE."""

    def __init__(self, message: str = "", pgcode: str | None = None,
                 sqlstate: str | None = None):
        super().__init__(message)
        if pgcode is not None:
            self.pgcode = pgcode
        if sqlstate is not None:
            self.sqlstate = sqlstate


def _sqlalchemy_error(error_class: type, orig: Exception,
                      invalidated: bool = False) -> Exception:
    """Return a SQLAlchemy DBAPIError of error_class wrapping orig."""
    return error_class("SELECT 1", {}, orig,
                       connection_invalidated=invalidated)


class TestWithConnectionDefaults(unittest.TestCase):

    @staticmethod
    def _query(url: str) -> dict:
        return dict(with_connection_defaults(make_url(url)).query)

    def test_the_defaults_are_added(self):
        self.assertEqual(self._query("postgresql+psycopg2://u:p@h:5432/db"),
                         EXPECTED_DEFAULTS)
        # Milliseconds: "60" would abort connections after 60 ms.
        self.assertEqual(LIBPQ_DEFAULTS["tcp_user_timeout"], "60000")

    def test_a_user_keyword_wins(self):
        query = self._query("postgresql+psycopg2://u@h/db"
                            "?keepalives_idle=5&tcp_user_timeout=0")
        self.assertEqual(query, {**EXPECTED_DEFAULTS,
                                 "keepalives_idle": "5",
                                 "tcp_user_timeout": "0"})

    def test_the_rest_of_the_query_passes_through(self):
        for url, extra in (
                ("postgresql+psycopg2://u@/db?host=/var/run/postgresql",
                 {"host": "/var/run/postgresql"}),
                ("postgresql+psycopg2://u@h/db?sslmode=require",
                 {"sslmode": "require"}),
                ("postgresql+psycopg2://u@/db?host=a&host=b",
                 {"host": ("a", "b")})):
            with self.subTest(url=url):
                self.assertEqual(self._query(url),
                                 {**EXPECTED_DEFAULTS, **extra})

    def test_a_password_with_special_characters_survives(self):
        url = with_connection_defaults(make_url(
            "postgresql+psycopg2://us%40r:p%40ss%3Aw%2Fd%25@h/db"))
        self.assertEqual(url.username, "us@r")
        self.assertEqual(url.password, "p@ss:w/d%")

    def test_options_is_never_added_and_a_users_options_is_kept(self):
        self.assertNotIn("options",
                         self._query("postgresql+psycopg2://u@h/db"))
        query = self._query("postgresql+psycopg2://u@h/db"
                            "?options=-c%20lock_timeout%3D5s")
        self.assertEqual(query["options"], "-c lock_timeout=5s")

    def test_a_users_application_name_is_kept(self):
        # libpq uses fallback_application_name only when no
        # application_name is given.
        query = self._query(
            "postgresql+psycopg2://u@h/db?application_name=mine")
        self.assertEqual(query["application_name"], "mine")

    def test_the_kill_switch_merges_into_a_url_with_a_query(self):
        query = self._query(with_kill_switch(
            "postgresql+psycopg2://u@h/db?sslmode=require"))
        self.assertEqual(query, {
            **EXPECTED_DEFAULTS, "sslmode": "require",
            "connect_timeout": "0", "keepalives_idle": "7200",
            "keepalives_interval": "75", "keepalives_count": "9",
            "tcp_user_timeout": "0"})


class TestProgramName(unittest.TestCase):

    def test_a_shard_number_is_kept(self):
        self.assertEqual(program_name(["/x/cmsContestWebServer", "2"]),
                         "cmsContestWebServer 2")

    def test_other_arguments_are_dropped(self):
        self.assertEqual(program_name(["/x/cmsDumpExporter", "-h"]),
                         "cmsDumpExporter")
        self.assertEqual(program_name(["/x/cmsWorker"]), "cmsWorker")

    def test_an_empty_command_line_gives_an_empty_name(self):
        self.assertEqual(program_name([]), "")
        self.assertEqual(program_name([""]), "")
        self.assertEqual(program_name(["-c"]), "-c")

    def test_the_module_value_names_this_process(self):
        self.assertEqual(PROGRAM_NAME, program_name(sys.argv))


class TestDisconnectRule(unittest.TestCase):

    def test_sqlstate_reads_both_drivers(self):
        self.assertEqual(sqlstate(_DriverError(pgcode="40P01")), "40P01")
        self.assertEqual(sqlstate(_DriverError(sqlstate="25P03")), "25P03")
        self.assertIsNone(sqlstate(_DriverError("Connection refused")))
        self.assertIsNone(sqlstate(None))

    def test_an_invalidated_connection_is_a_disconnect(self):
        error = _sqlalchemy_error(
            OperationalError,
            _DriverError("server closed the connection unexpectedly"),
            invalidated=True)
        self.assertTrue(is_disconnect(error))

    def test_a_connection_exception_sqlstate_is_a_disconnect(self):
        for orig in (_DriverError(pgcode="08006"),
                     _DriverError(sqlstate="08001")):
            with self.subTest(code=sqlstate(orig)):
                self.assertTrue(is_disconnect(
                    _sqlalchemy_error(OperationalError, orig)))

    def test_lock_and_transaction_errors_are_not_disconnects(self):
        for code in ("40P01", "40001", "55P03"):
            with self.subTest(code=code):
                self.assertFalse(is_disconnect(_sqlalchemy_error(
                    OperationalError, _DriverError(pgcode=code))))
        self.assertFalse(is_disconnect(_sqlalchemy_error(
            IntegrityError, _DriverError(pgcode="23505"))))

    def test_errors_that_do_not_come_from_the_driver_are_not(self):
        self.assertFalse(is_disconnect(PendingRollbackError("rolled back")))
        self.assertFalse(is_disconnect(ValueError("bad row")))

    def test_describe_error_gives_the_driver_message_on_one_line(self):
        error = _sqlalchemy_error(
            OperationalError,
            _DriverError("server closed the connection unexpectedly\n"
                         "\tThis probably means\n"),
            invalidated=True)
        self.assertEqual(describe_error(error),
                         "server closed the connection unexpectedly "
                         "This probably means")
        self.assertEqual(describe_error(ValueError("bad row")), "bad row")
        self.assertEqual(describe_error(ValueError()), "ValueError")


class TestPsycopg3IdleKill(unittest.IsolatedAsyncioTestCase):
    """psycopg 3 reports an idle kill as InternalError (8g)."""

    async def test_an_idle_kill_is_a_disconnect(self):
        url = with_connection_defaults(_async_database_url()) \
            .update_query_dict(
                {"options": "-c idle_in_transaction_session_timeout=500"})
        idle_engine = create_async_engine(url)
        self.addAsyncCleanup(idle_engine.dispose)
        async with idle_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            await asyncio.sleep(1.5)
            with self.assertRaises(InternalError) as caught:
                await connection.execute(text("SELECT 1"))
        self.assertEqual(sqlstate(caught.exception.orig), "25P03")
        self.assertTrue(is_disconnect(caught.exception))


class TestProgramNameOnTheServer(unittest.TestCase):

    def test_an_empty_program_name_still_connects(self):
        connection = custom_psycopg2_connection(fallback_application_name="")
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone(), (1,))
        finally:
            connection.close()


@unittest.skipUnless(_database_over_tcp(),
                     "the test database is reached over a Unix socket")
class TestConnectionPaths(unittest.TestCase):
    """The defaults reach the sockets of the sync paths."""

    def test_the_sync_engine(self):
        with engine.connect() as connection:
            fileno = connection.connection.dbapi_connection.fileno()
            self.assertEqual(_socket_options(fileno), DEFAULT_SOCKET)
            name = connection.execute(
                text("SHOW application_name")).scalar_one()
        self.assertEqual(name, PROGRAM_NAME)

    def test_custom_psycopg2_connection(self):
        self.assertEqual(_custom_connection_state(),
                         (DEFAULT_SOCKET, PROGRAM_NAME))

    def test_the_kill_switch(self):
        switched_url = with_kill_switch(config.database.url)
        with patch.object(config.database, "url", switched_url):
            switched = create_engine(
                with_connection_defaults(make_url(config.database.url)))
            custom_state = _custom_connection_state()
        self.addCleanup(switched.dispose)
        with switched.connect() as connection:
            fileno = connection.connection.dbapi_connection.fileno()
            self.assertEqual(_socket_options(fileno), KILL_SWITCH_SOCKET)
        self.assertEqual(custom_state[0], KILL_SWITCH_SOCKET)


@unittest.skipUnless(_database_over_tcp(),
                     "the test database is reached over a Unix socket")
class TestAsyncConnectionPath(unittest.IsolatedAsyncioTestCase):
    """The defaults reach the socket of the async engine."""

    async def asyncTearDown(self):
        await async_engine.dispose()

    async def test_the_async_engine(self):
        self.assertEqual(await _async_engine_state(async_engine),
                         (DEFAULT_SOCKET, PROGRAM_NAME))

    async def test_the_kill_switch(self):
        switched_url = with_kill_switch(config.database.url)
        with patch.object(config.database, "url", switched_url):
            switched = create_async_engine(
                with_connection_defaults(_async_database_url()))
        self.addAsyncCleanup(switched.dispose)
        socket_options, _ = await _async_engine_state(switched)
        self.assertEqual(socket_options, KILL_SWITCH_SOCKET)


if __name__ == "__main__":
    unittest.main()
