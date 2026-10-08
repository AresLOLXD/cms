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

"""Tests for the connect deadline of cms.io.PsycoGevent (#39)."""

import gc
import os
import socket
import time
import unittest
from types import SimpleNamespace

import gevent
import psycopg2
from psycopg2 import extensions
from sqlalchemy.engine.url import make_url

from cms.db import custom_psycopg2_connection
from cms.db.connect_options import with_connection_defaults
from cms.io.PsycoGevent import connect_deadline, is_psycopg_green, \
    make_psycopg_green
from cmstestsuite.unit_tests.db.connect_options_test import \
    with_kill_switch


class TestConnectDeadline(unittest.TestCase):
    """Which waits get a deadline, and how long it is."""

    @staticmethod
    def _seconds_left(status: int, dsn: str) -> float | None:
        deadline = connect_deadline(SimpleNamespace(status=status, dsn=dsn))
        return None if deadline is None else deadline - time.monotonic()

    def test_a_connect_gets_its_connect_timeout(self):
        self.assertAlmostEqual(
            self._seconds_left(extensions.STATUS_SETUP,
                               "host=h connect_timeout=10"), 10, delta=0.5)

    def test_a_connect_timeout_under_2_seconds_counts_as_2(self):
        self.assertAlmostEqual(
            self._seconds_left(extensions.STATUS_SETUP,
                               "host=h connect_timeout=1"), 2, delta=0.5)

    def test_zero_or_no_connect_timeout_means_no_deadline(self):
        for dsn in ("host=h connect_timeout=0", "host=h"):
            with self.subTest(dsn=dsn):
                self.assertIsNone(
                    self._seconds_left(extensions.STATUS_SETUP, dsn))

    def test_a_query_never_gets_a_deadline(self):
        for status in (extensions.STATUS_READY, extensions.STATUS_BEGIN):
            with self.subTest(status=status):
                self.assertIsNone(self._seconds_left(
                    status, "host=h connect_timeout=10"))


class TestGreenConnect(unittest.TestCase):
    """A green connect to a server that accepts and never answers."""

    def setUp(self):
        make_psycopg_green()
        self.assertTrue(is_psycopg_green())
        # The kernel completes the handshake; nobody ever answers.
        self.listener = socket.socket()
        self.addCleanup(self.listener.close)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(64)
        self.port = self.listener.getsockname()[1]

    def _connect(self, **keywords: str) -> None:
        psycopg2.connect(host="127.0.0.1", port=self.port, user="cms",
                         dbname="cms", **keywords)

    def _seconds_to_fail(self, **keywords: str) -> float:
        started = time.monotonic()
        with self.assertRaises(psycopg2.OperationalError) as caught:
            self._connect(**keywords)
        self.assertEqual(str(caught.exception), "timeout expired")
        return time.monotonic() - started

    def test_a_silent_server_fails_at_the_connect_timeout(self):
        elapsed = self._seconds_to_fail(connect_timeout="2")
        self.assertGreaterEqual(elapsed, 2)
        self.assertLess(elapsed, 3.5)

    def test_a_connect_timeout_of_1_waits_2_seconds(self):
        elapsed = self._seconds_to_fail(connect_timeout="1")
        self.assertGreaterEqual(elapsed, 2)
        self.assertLess(elapsed, 3.5)

    def test_a_zero_connect_timeout_has_no_deadline(self):
        started = time.monotonic()
        with self.assertRaises(gevent.Timeout):
            with gevent.Timeout(3.5):
                self._connect(connect_timeout="0")
        self.assertGreaterEqual(time.monotonic() - started, 3.5)

    def test_the_kill_switch_removes_the_deadline(self):
        url = with_connection_defaults(make_url(with_kill_switch(
            "postgresql+psycopg2://cms@127.0.0.1:%d/cms" % self.port)))
        with self.assertRaises(gevent.Timeout):
            with gevent.Timeout(3.5):
                self._connect(**url.query)

    def test_timed_out_connects_leave_no_file_descriptor(self):
        # The first timed wait of a thread creates its gevent hub's own
        # descriptors (eventpoll, eventfd): count after it.
        with self.assertRaises(psycopg2.OperationalError):
            self._connect(connect_timeout="2")
        gc.collect()
        before = len(os.listdir("/proc/self/fd"))
        for _ in range(5):
            try:
                self._connect(connect_timeout="2")
            except psycopg2.OperationalError:
                pass
        # The collector frees a connection that a reference cycle still
        # holds; a leaked one would stay.
        gc.collect()
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)


class TestQueriesHaveNoDeadline(unittest.TestCase):

    def test_a_long_query_is_not_cut(self):
        make_psycopg_green()
        connection = custom_psycopg2_connection(connect_timeout="2")
        try:
            cursor = connection.cursor()
            started = time.monotonic()
            cursor.execute("SELECT pg_sleep(3)")
            cursor.fetchall()
            self.assertGreaterEqual(time.monotonic() - started, 3)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
