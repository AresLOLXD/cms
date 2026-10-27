#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 The CMS development team
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

"""Tests for ContestWebServer construction.

"""

import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from tornado.httputil import ParseBodyConfig, set_parse_body_config

from cms import config
from cms.io import WebService
from cms.server.contest.server import (
    BODY_TIMEOUT, MAX_BODY_SIZE, ContestWebServer)


class TestConstruction(unittest.TestCase):
    """ContestWebServer must be constructible without raising.

    Regression test for an AttributeError that used to be raised at
    startup (ContestWebServer.__init__ tried to wrap self.wsgi_app,
    an attribute WebService no longer has since its native-Tornado
    rewrite) -- see this sub-project's final-review fix round 1.

    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The limits are global to the process: undo them for the tests
        # that run after this class.
        cls.addClassCleanup(set_parse_body_config, ParseBodyConfig())

    def test_constructs_without_raising(self):
        server = ContestWebServer(0)
        self.assertIsNone(server.contest_id)

    def test_bounds_the_body_of_a_request(self):
        http_server = ContestWebServer(0)._http_server
        self.assertEqual(
            http_server.conn_params.max_body_size, MAX_BODY_SIZE)
        self.assertEqual(http_server.max_buffer_size, MAX_BODY_SIZE)
        self.assertEqual(http_server.conn_params.body_timeout, BODY_TIMEOUT)

    def test_warns_about_an_invalid_request_time_header_name(self):
        for name in ("X Y", "-X", "X_Y"):
            with self.subTest(name=name), \
                    patch.object(config.contest_web_server,
                                 "request_time_header", name), \
                    self.assertLogs("cms.server.contest.server",
                                    "WARNING") as logs:
                ContestWebServer(0)
            self.assertIn(repr(name), logs.output[0])

        # Off (the default, empty) and a valid name must stay silent.
        for name in ("", "X-Request-Start"):
            with self.subTest(name=name), \
                    patch.object(config.contest_web_server,
                                 "request_time_header", name), \
                    self.assertNoLogs("cms.server.contest.server", "WARNING"):
                ContestWebServer(0)


class TestActivityFlush(unittest.IsolatedAsyncioTestCase):
    """The activity log is flushed periodically and at shutdown."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The limits are global to the process: undo them for the tests
        # that run after this class.
        cls.addClassCleanup(set_parse_body_config, ParseBodyConfig())

    def test_flush_is_scheduled_every_flush_interval(self):
        with patch.object(ContestWebServer, "add_timeout") as add_timeout:
            server = ContestWebServer(0)
        add_timeout.assert_any_call(
            server.activity_recorder.flush, None,
            config.contest_web_server.activity_flush_interval)

    async def test_shutdown_flushes_what_is_left(self):
        server = ContestWebServer(0)
        server.activity_recorder.flush = AsyncMock()
        with patch.object(WebService, "_async_run",
                          AsyncMock(return_value=True)):
            self.assertTrue(await server._async_run())
        server.activity_recorder.flush.assert_awaited_once_with()

    async def test_shutdown_flush_gives_up_after_a_timeout(self):
        async def hanging_write(session, pending, inactivity_threshold):
            await asyncio.Event().wait()

        server = ContestWebServer(0)
        timestamp = datetime(2026, 10, 12, 10, 0, 0)
        server.activity_recorder.record(1, None, "10.0.0.5", timestamp)
        server.activity_recorder.record(2, None, "10.0.0.5", timestamp)
        with patch.object(WebService, "_async_run",
                          AsyncMock(return_value=True)), \
                patch("cms.server.contest.server.SHUTDOWN_FLUSH_TIMEOUT",
                      0.1), \
                patch("cms.server.contest.activity.write_pending_activity",
                      side_effect=hanging_write), \
                self.assertLogs("cms.server.contest.server",
                                "ERROR") as logs:
            self.assertTrue(await server._async_run())

        self.assertIn("2 participation(s)", logs.output[0])
        self.assertEqual(len(server.activity_recorder.pending()), 2)


if __name__ == "__main__":
    unittest.main()
