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

"""Tests for the limits on the form bodies that AWS parses.

Tornado parses a form body before it checks the XSRF token, and
rejects it with a 400 if it has too many arguments (urlencoded) or
parts (multipart), or if a part's header is too large. AWS raises the
limits so that its big forms work: the users list posts one user_id per
checked user, and the task page one field per public testcase.

These tests send forms of the sizes at each limit to a real
AdminWebServer, without an XSRF token. A body that was parsed gets the
403 of the XSRF check, which comes after parsing; one that was rejected
gets a 400.

"""

import unittest

from tornado.httpclient import AsyncHTTPClient, HTTPResponse
from tornado.httpserver import HTTPServer
from tornado.httputil import ParseBodyConfig, set_parse_body_config
from tornado.netutil import bind_sockets

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.server.formbodies import \
    MULTIPART_CONTENT_TYPE, URLENCODED_CONTENT_TYPE, multipart_body, \
    urlencoded_body

from cms.server.admin.bulkremove import MAX_PLAN_USERS
from cms.server.admin.server import AdminWebServer


class BodyLimitsTest(DatabaseMixin, unittest.IsolatedAsyncioTestCase):
    """Serve the real AdminWebServer application."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The limits are global to the process: undo them for the tests
        # that run after this class.
        cls.addClassCleanup(set_parse_body_config, ParseBodyConfig())
        # Start from Tornado's defaults, so that the boundaries below
        # measure what the server sets, not what an earlier test left.
        set_parse_body_config(ParseBodyConfig())
        cls.aws = AdminWebServer(0)

    async def asyncSetUp(self):
        sockets = bind_sockets(0, "127.0.0.1")
        self.port = sockets[0].getsockname()[1]
        self.server = HTTPServer(self.aws.application)
        self.server.add_sockets(sockets)
        self.client = AsyncHTTPClient()

    async def asyncTearDown(self):
        self.client.close()
        self.server.stop()
        await self.server.close_all_connections()

    async def post(self, content_type: str, body: str) -> HTTPResponse:
        return await self.client.fetch(
            "http://127.0.0.1:%d/login" % self.port,
            method="POST", body=body,
            headers={"Content-Type": content_type}, raise_error=False)

    async def test_urlencoded_form_at_the_limit_is_parsed(self):
        response = await self.post(
            URLENCODED_CONTENT_TYPE, urlencoded_body(10_000))
        self.assertEqual(response.code, 403)

    async def test_urlencoded_form_over_the_limit_is_rejected(self):
        response = await self.post(
            URLENCODED_CONTENT_TYPE, urlencoded_body(10_001))
        self.assertEqual(response.code, 400)

    async def test_confirmation_form_of_the_largest_plan_is_parsed(self):
        # One user_id per planned user, and the form's own 3 fields.
        response = await self.post(
            URLENCODED_CONTENT_TYPE, urlencoded_body(MAX_PLAN_USERS + 3))
        self.assertEqual(response.code, 403)

    async def test_multipart_form_at_the_limit_is_parsed(self):
        # Tornado counts the empty preamble as a part: 10,000 parts are
        # rejected, and 9,999 are the most that it accepts.
        response = await self.post(
            MULTIPART_CONTENT_TYPE, multipart_body(9_999))
        self.assertEqual(response.code, 403)

    async def test_multipart_form_over_the_limit_is_rejected(self):
        response = await self.post(
            MULTIPART_CONTENT_TYPE, multipart_body(10_000))
        self.assertEqual(response.code, 400)

    async def test_part_header_at_the_limit_is_parsed(self):
        response = await self.post(
            MULTIPART_CONTENT_TYPE, multipart_body(1, header_size=2048))
        self.assertEqual(response.code, 403)

    async def test_part_header_over_the_limit_is_rejected(self):
        response = await self.post(
            MULTIPART_CONTENT_TYPE, multipart_body(1, header_size=2049))
        self.assertEqual(response.code, 400)


if __name__ == "__main__":
    unittest.main()
