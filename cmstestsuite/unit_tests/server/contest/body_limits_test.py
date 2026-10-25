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

"""Tests for the limits on the form bodies that CWS parses.

Tornado parses a form body before it checks the XSRF token, and rejects
it with a 400 if it has too many arguments (urlencoded) or parts
(multipart), or if a part's header is too large. The per-file submission
form of an OutputOnly task sends one part per output, so with the
default of 100 parts a task with 99 outputs or more could not be
submitted that way. CWS raises the limit on parts, and caps the size of
a part's header.

These tests send forms of the sizes at each limit to a real
ContestWebServer, without an XSRF token. A body that was parsed gets the
403 of the XSRF check, which comes after parsing; one that was rejected
gets a 400.

"""

import unittest
from unittest.mock import patch

from tornado.httpclient import AsyncHTTPClient, HTTPResponse
from tornado.httpserver import HTTPServer
from tornado.httputil import ParseBodyConfig, set_parse_body_config
from tornado.netutil import bind_sockets

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.server.formbodies import \
    MULTIPART_CONTENT_TYPE, URLENCODED_CONTENT_TYPE, multipart_body, \
    urlencoded_body

from cms.server.contest.handlers import HANDLERS
from cms.server.contest.server import ContestWebServer


class BodyLimitsTest(DatabaseMixin, unittest.IsolatedAsyncioTestCase):
    """Serve the real ContestWebServer application, for all contests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The limits are global to the process: undo them for the tests
        # that run after this class.
        cls.addClassCleanup(set_parse_body_config, ParseBodyConfig())

    async def asyncSetUp(self):
        # ContestWebServer.__init__ appends to the module's HANDLERS
        # every time; give it a copy so the tests leave no trace.
        patcher = patch(
            "cms.server.contest.server.HANDLERS", list(HANDLERS))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Start from Tornado's defaults, so that the boundaries below
        # measure what the server sets, not what an earlier test left.
        set_parse_body_config(ParseBodyConfig())
        self.cws = ContestWebServer(0)
        sockets = bind_sockets(0, "127.0.0.1")
        self.port = sockets[0].getsockname()[1]
        self.server = HTTPServer(self.cws.application)
        self.server.add_sockets(sockets)
        self.client = AsyncHTTPClient()

    async def asyncTearDown(self):
        self.client.close()
        self.server.stop()
        await self.server.close_all_connections()

    async def post(
        self, content_type: str, body: str,
        headers: dict[str, str] | None = None, path: str = "/",
    ) -> HTTPResponse:
        # The contest list takes a POST, and checks the XSRF token
        # before it does anything else with it.
        return await self.client.fetch(
            "http://127.0.0.1:%d%s" % (self.port, path),
            method="POST", body=body,
            headers={"Content-Type": content_type,
                     "Accept-Language": "en", **(headers or {})},
            raise_error=False)

    async def test_multipart_form_at_the_limit_is_parsed(self):
        # Tornado counts the empty preamble as a part: 1000 parts are
        # rejected, and 999 are the most that it accepts. That is the
        # _xsrf field and 998 outputs in the per-file submission form.
        response = await self.post(
            MULTIPART_CONTENT_TYPE, multipart_body(999))
        self.assertEqual(response.code, 403)

    async def test_multipart_form_over_the_limit_is_rejected(self):
        response = await self.post(
            MULTIPART_CONTENT_TYPE, multipart_body(1000))
        self.assertEqual(response.code, 400)

    async def test_part_header_at_the_limit_is_parsed(self):
        response = await self.post(
            MULTIPART_CONTENT_TYPE, multipart_body(1, header_size=2048))
        self.assertEqual(response.code, 403)

    async def test_part_header_over_the_limit_is_rejected(self):
        response = await self.post(
            MULTIPART_CONTENT_TYPE, multipart_body(1, header_size=2049))
        self.assertEqual(response.code, 400)

    async def test_urlencoded_limit_stays_at_the_default(self):
        # Only the multipart form of a submission has a field per file.
        response = await self.post(
            URLENCODED_CONTENT_TYPE, urlencoded_body(1000))
        self.assertEqual(response.code, 403)
        response = await self.post(
            URLENCODED_CONTENT_TYPE, urlencoded_body(1001))
        self.assertEqual(response.code, 400)

    async def test_rejected_body_gets_its_own_message(self):
        response = await self.post(
            MULTIPART_CONTENT_TYPE, multipart_body(1000))
        self.assertEqual(response.code, 400)
        page = response.body.decode()
        self.assertIn("could not read the data of your request", page)
        # Not a suspect of tampering: nobody forged a URL.
        self.assertNotIn("tamper", page)

    async def test_xsrf_failure_keeps_the_generic_message(self):
        response = await self.post(
            MULTIPART_CONTENT_TYPE, multipart_body(1))
        self.assertEqual(response.code, 403)
        page = response.body.decode()
        self.assertIn("tamper", page)
        self.assertNotIn("could not read the data of your request", page)

    async def test_error_raised_by_a_handler_keeps_the_generic_message(self):
        # A 400 that the application raises (here, a missing argument of
        # the registration form, after a valid XSRF check) is not a body
        # Tornado could not read.
        contest = self.add_contest(allow_registration=True, active=True)
        name = contest.name
        self.session.commit()
        self.addCleanup(self.delete_data)
        token = "0123456789abcdef0123456789abcdef"
        response = await self.post(
            URLENCODED_CONTENT_TYPE, "_xsrf=" + token,
            headers={"Cookie": "_xsrf=" + token},
            path="/%s/register" % name)
        self.assertEqual(response.code, 400)
        page = response.body.decode()
        self.assertIn("tamper", page)
        self.assertNotIn("could not read the data of your request", page)

    async def test_rejected_body_is_logged_with_the_client_ip(self):
        # Behind one proxy: the client is the rightmost address of the
        # header, not the connecting socket.
        self.cws.num_proxies_used = 1
        with self.assertLogs("tornado.general", "WARNING") as logs:
            await self.post(
                MULTIPART_CONTENT_TYPE, multipart_body(1000),
                headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})
        self.assertEqual(len(logs.records), 1)
        self.assertIn("(10.0.0.1)", logs.records[0].getMessage())


if __name__ == "__main__":
    unittest.main()
