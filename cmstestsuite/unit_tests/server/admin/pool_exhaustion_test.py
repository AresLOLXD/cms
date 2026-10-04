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

"""Tests for AWS when the database connection pool runs short.

Every AWS request checks a connection out of the pool in prepare() and
keeps it until the request is finished. A burst of requests used to
exhaust the pool; prepare() then failed after the pool timeout, and the
error page of that failure queried the database on the event loop
thread, waiting for the pool another timeout and stalling the whole
server, which then could not finish the requests that would have freed
the connections. These tests use a real AdminWebServer with a tiny pool
to check that:

- the error page never queries the database, so it can't block the loop;
- AWS caps the requests it serves at once below the pool size, and
  every request gives its slot back, however it ends.

"""

import asyncio
import contextlib
import io
import re
import socket
import time
import unittest
from collections.abc import Iterator
from unittest import mock
from urllib.parse import urlencode

import tornado.web
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from tornado.httpclient import AsyncHTTPClient, HTTPClientError, \
    HTTPResponse
from tornado.httpserver import HTTPServer
from tornado.iostream import StreamClosedError
from tornado.netutil import bind_sockets

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.server.admin.admin_session_test import \
    session_payload, sign

from cms import config
from cms.db import engine
from cms.db.session import Session
from cms.server.admin.handlers import base
from cms.server.admin.handlers.base import BaseHandler, require_permission
from cms.server.admin.server import AdminWebServer


# How long a request waits for a connection of the tiny test pool.
POOL_TIMEOUT = 1.0

# A download that can't fit in the buffers of a client that doesn't read.
DOWNLOAD_SIZE = 32 * 1024 * 1024

# An XSRF cookie Tornado accepts. Without one, Tornado makes a token
# when the error page asks for the form, and then looks at the current
# user, which is another way to query the database.
XSRF_COOKIE = "0123456789abcdef0123456789abcdef"


class _ProbeHandler(BaseHandler):
    """Hold the requests for a while, counting the ones inside."""

    body_time = 0.3
    inside = 0
    max_inside = 0
    entered: asyncio.Event | None = None

    @classmethod
    def reset(cls):
        cls.body_time = 0.3
        cls.inside = 0
        cls.max_inside = 0
        cls.entered = asyncio.Event()

    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self):
        cls = type(self)
        cls.inside += 1
        cls.max_inside = max(cls.max_inside, cls.inside)
        cls.entered.set()
        try:
            await asyncio.sleep(cls.body_time)
        finally:
            cls.inside -= 1
        self.write("ok")


class _BoomHandler(BaseHandler):
    """Fail with an exception that is not an HTTPError."""

    async def get(self):
        raise RuntimeError("boom")


class _CommittedHandler(BaseHandler):
    """Commit, which expires the current admin, and then fail."""

    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self):
        self.sql_session.commit()
        raise tornado.web.HTTPError(404)


async def longest_loop_stall(stop: asyncio.Event) -> float:
    """Measure the longest time the event loop went without running.

    stop: set it to end the measure.

    return: the longest gap, in seconds, between two ticks of 20 ms.

    """
    longest = 0.0
    last = time.monotonic()
    while not stop.is_set():
        await asyncio.sleep(0.02)
        now = time.monotonic()
        longest = max(longest, now - last)
        last = now
    return longest


class _AwsServerMixin(DatabaseMixin):
    """Serve the real AdminWebServer application to a logged-in admin."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aws = AdminWebServer(0)
        cls.aws.application.add_handlers(r".*$", [
            (r"/test/probe", _ProbeHandler),
            (r"/test/boom", _BoomHandler),
            (r"/test/committed", _CommittedHandler),
        ])

    async def asyncSetUp(self):
        await super().asyncSetUp()
        _ProbeHandler.reset()
        # Each test gets its own semaphore, which binds to the event
        # loop of the test as soon as a request has to wait for it.
        slots_patch = mock.patch.object(base, "_request_slots", None)
        slots_patch.start()
        self.addCleanup(slots_patch.stop)
        admin = self.add_admin()
        self.admin_name = admin.name
        self.awslogin = sign(session_payload(admin.id))
        # Reading the admin started a transaction: free its connection.
        self.session.rollback()

        sockets = bind_sockets(0, "127.0.0.1")
        self.port = sockets[0].getsockname()[1]
        self.server = HTTPServer(self.aws.application)
        self.server.add_sockets(sockets)
        self.client = AsyncHTTPClient(force_instance=True, max_clients=64)

    async def asyncTearDown(self):
        self.client.close()
        self.server.stop()
        await self.server.close_all_connections()
        await super().asyncTearDown()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    @property
    def cookie_header(self) -> str:
        return "%s=%s" % (BaseHandler.COOKIE_NAME, self.awslogin)

    async def get(
        self, path: str, authenticated: bool = True, xsrf_cookie: bool = False,
    ) -> HTTPResponse:
        cookies = []
        if authenticated:
            cookies.append(self.cookie_header)
        if xsrf_cookie:
            cookies.append("_xsrf=" + XSRF_COOKIE)
        headers = {"Cookie": "; ".join(cookies)}
        return await self.client.fetch(
            "http://127.0.0.1:%d%s" % (self.port, path), headers=headers,
            follow_redirects=False, raise_error=False)

    @contextlib.contextmanager
    def request_limit(self, limit: int) -> Iterator[None]:
        """Cap the requests served at once, with a fresh semaphore."""
        with mock.patch.object(base, "MAX_IN_FLIGHT_REQUESTS", limit), \
                mock.patch.object(base, "_request_slots", None):
            self.limit = limit
            yield

    @staticmethod
    async def free_slots() -> int:
        """Count the free slots, without any request in flight."""
        slots = base._get_request_slots()
        taken = 0
        while not slots.locked():
            await slots.acquire()
            taken += 1
        for _ in range(taken):
            slots.release()
        return taken

    async def assert_all_slots_free(self, when: str):
        """Check that every slot is free, and no more than the limit."""
        for _ in range(60):
            free = await self.free_slots()
            if free == self.limit:
                return
            await asyncio.sleep(0.05)
        self.fail("%d of %d slots are free %s" % (free, self.limit, when))

    @contextlib.contextmanager
    def small_pool(self, size: int, timeout: float) -> Iterator[Engine]:
        """Make the sessions of the handlers use a tiny pool.

        size: the number of connections of the pool.
        timeout: how long to wait for a connection before giving up.

        return: the engine of the tiny pool.

        """
        small_engine = create_engine(
            config.database.url,
            pool_size=size, max_overflow=0, pool_timeout=timeout)
        Session.configure(bind=small_engine)
        try:
            yield small_engine
        finally:
            Session.configure(bind=engine)
            small_engine.dispose()


class ErrorPageTest(_AwsServerMixin, unittest.IsolatedAsyncioTestCase):

    async def assert_pool_timeout_leaves_the_loop_running(
        self, xsrf_cookie: bool,
    ):
        # Compile the error templates now, so rendering them later is
        # quick and doesn't look like a stall.
        warm_up = await self.get("/contest/999999")
        self.assertEqual(warm_up.code, 404)

        with self.small_pool(size=1, timeout=POOL_TIMEOUT) as small_engine:
            # The only connection is busy: prepare() times out.
            busy = small_engine.connect()
            try:
                stop = asyncio.Event()
                ticker = asyncio.create_task(longest_loop_stall(stop))
                with self.assertLogs(base.logger, "ERROR"):
                    response = await self.get(
                        "/notifications", xsrf_cookie=xsrf_cookie)
                stop.set()
                stall = await ticker
            finally:
                busy.close()

        # The error page waited for no connection: the loop kept running
        # (it used to stall for one more POOL_TIMEOUT).
        self.assertLess(stall, POOL_TIMEOUT / 2)
        self.assertEqual(response.code, 500)
        self.assertIn("Error 500", response.body.decode())

    async def test_pool_timeout_does_not_block_the_event_loop(self):
        await self.assert_pool_timeout_leaves_the_loop_running(
            xsrf_cookie=True)

    async def test_pool_timeout_without_xsrf_cookie_does_not_block_loop(self):
        await self.assert_pool_timeout_leaves_the_loop_running(
            xsrf_cookie=False)

    async def test_error_page_does_not_load_the_sidebar_lists(self):
        statements = []

        def record(conn, cursor, statement, *unused):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            response = await self.get("/contest/999999")
        finally:
            event.remove(engine, "before_cursor_execute", record)

        self.assertEqual(response.code, 404)
        for table in ("tasks", "users", "teams", "ranking_groups"):
            self.assertFalse(
                any(re.search(r"FROM %s\b" % table, s) for s in statements),
                "the error page queried %s" % table)
        # Nor does it claim the lists are empty.
        self.assertNotIn("available)", response.body.decode())

    async def test_error_page_greets_the_admin_prepare_resolved(self):
        response = await self.get("/contest/999999")
        self.assertEqual(response.code, 404)
        body = response.body.decode()
        self.assertIn("Error 404", body)
        self.assertIn("Hello", body)
        self.assertIn(self.admin_name, body)

    async def test_error_page_skips_an_admin_that_needs_a_query(self):
        # The commit expired the admin: reading its name would query.
        response = await self.get("/test/committed")
        self.assertEqual(response.code, 404)
        body = response.body.decode()
        self.assertIn("Error 404", body)
        self.assertNotIn("Hello", body)


class RequestLimitTest(_AwsServerMixin, unittest.IsolatedAsyncioTestCase):

    async def test_requests_in_flight_never_exceed_the_limit(self):
        with self.request_limit(3):
            responses = await asyncio.gather(
                *(self.get("/test/probe") for _ in range(20)))
            self.assertEqual({r.code for r in responses}, {200})
            self.assertEqual(_ProbeHandler.max_inside, 3)
            await self.assert_all_slots_free("after the burst")

    async def test_slots_are_given_back_whatever_the_outcome(self):
        with self.request_limit(2):
            response = await self.get("/notifications")
            self.assertEqual(response.code, 200)
            await self.assert_all_slots_free("after a 200")

            response = await self.get("/contest/999999")
            self.assertEqual(response.code, 404)
            await self.assert_all_slots_free("after a 404")

            with self.assertLogs(base.logger, "ERROR"):
                response = await self.get("/test/boom")
            self.assertEqual(response.code, 500)
            await self.assert_all_slots_free("after a 500")

            response = await self.get("/test/probe", authenticated=False)
            self.assertIn(response.code, (302, 403))
            await self.assert_all_slots_free("after a login redirect")

            # prepare() fails, after taking the slot.
            with self.small_pool(size=1, timeout=0.3) as small_engine:
                busy = small_engine.connect()
                try:
                    with self.assertLogs(base.logger, "ERROR"):
                        response = await self.get("/notifications")
                finally:
                    busy.close()
            self.assertEqual(response.code, 500)
            await self.assert_all_slots_free("after a pool timeout")

            # Fails the XSRF check, before prepare(): it takes no slot,
            # so it must not give one back either.
            response = await self.client.fetch(
                "http://127.0.0.1:%d/contests/add" % self.port,
                method="POST", body=urlencode({"name": "c"}),
                headers={"Cookie": self.cookie_header}, raise_error=False)
            self.assertEqual(response.code, 403)
            await self.assert_all_slots_free("after an XSRF failure")

    async def test_slot_is_given_back_when_the_client_disconnects(self):
        with self.request_limit(2):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", self.port)
            writer.write((
                "GET /test/probe HTTP/1.1\r\nHost: localhost\r\n"
                "Cookie: %s\r\n\r\n" % self.cookie_header).encode())
            await writer.drain()
            await asyncio.wait_for(_ProbeHandler.entered.wait(), 5)
            writer.close()
            await writer.wait_closed()
            await self.assert_all_slots_free("after a disconnection")

    async def test_slot_is_given_back_when_finishing_fails(self):
        def finish_on_a_closed_connection(handler, chunk=None):
            handler.request.connection.stream.close()
            raise StreamClosedError()

        with self.request_limit(2):
            # CommonRequestHandler.finish() swallows the error, and
            # Tornado never gets to call on_finish().
            with mock.patch.object(tornado.web.RequestHandler, "finish",
                                   finish_on_a_closed_connection):
                with self.assertRaises(HTTPClientError):
                    await self.get("/notifications")
            await self.assert_all_slots_free("after a failed finish")

    async def test_client_that_stops_reading_a_download_holds_no_slot(self):
        file_cacher = self.aws.file_cacher
        content = io.BytesIO(b"x" * DOWNLOAD_SIZE)
        with self.request_limit(1), mock.patch.object(
                file_cacher, "get_file", return_value=content):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", self.port)
            try:
                # Keep the kernel buffers small, so that the file doesn't
                # fit in them and the server has to wait for the client.
                writer.get_extra_info("socket").setsockopt(
                    socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                writer.write((
                    "GET /file/abc123/big.txt HTTP/1.1\r\n"
                    "Host: localhost\r\nCookie: %s\r\n\r\n"
                    % self.cookie_header).encode())
                await writer.drain()
                # Once the headers are here the server is streaming the
                # file, and the client stops reading it.
                await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)

                try:
                    response = await asyncio.wait_for(
                        self.get("/notifications"), 3)
                except asyncio.TimeoutError:
                    self.fail("the stalled download holds the only slot")
                self.assertEqual(response.code, 200)
            finally:
                writer.transport.abort()

    async def test_request_that_gets_no_slot_in_time_gets_a_503(self):
        _ProbeHandler.body_time = 1.5
        with self.request_limit(1), mock.patch.object(
                base, "REQUEST_SLOT_TIMEOUT", 0.3):
            # This one takes the only slot for a while.
            holder = asyncio.create_task(self.get("/test/probe"))
            await asyncio.wait_for(_ProbeHandler.entered.wait(), 5)

            started = time.monotonic()
            response = await self.get("/notifications")
            elapsed = time.monotonic() - started

            self.assertEqual(response.code, 503)
            self.assertLess(elapsed, 1.0)
            # The usual error page: no admin yet, and no queries.
            body = response.body.decode()
            self.assertIn("Error 503", body)
            self.assertIn("</html>", body)
            self.assertNotIn("Hello", body)
            self.assertEqual((await holder).code, 200)
            await self.assert_all_slots_free("after a 503")

    async def test_burst_over_a_small_pool_gets_no_server_error(self):
        _ProbeHandler.body_time = 0.25
        # With the limit under the pool size, a request never waits for
        # a connection, and the ones over the limit wait for a slot.
        with self.small_pool(size=3, timeout=0.5), self.request_limit(2):
            responses = await asyncio.gather(
                *(self.get("/test/probe") for _ in range(12)))
        self.assertEqual([r.code for r in responses], [200] * 12)


if __name__ == "__main__":
    unittest.main()
