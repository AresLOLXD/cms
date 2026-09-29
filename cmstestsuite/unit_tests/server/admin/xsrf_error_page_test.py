#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <www.luisrodolfo@gmail.com>
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

"""Tests for the error page of a POST that fails the XSRF check in AWS.

Tornado checks the XSRF token before it calls prepare(), and the
handlers only set up what the error page needs (the URL helpers, the
current admin) in prepare(). These tests send real requests, without a
valid XSRF token, through a real AdminWebServer and check that the
admin gets a readable 403 error page, and nothing is logged as an
error.

"""

import unittest
from urllib.parse import urlencode

from tornado.httpclient import AsyncHTTPClient, HTTPResponse
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_sockets

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.server.admin.admin_session_test import \
    session_payload, sign

from cms.db import Contest
from cms.server.admin.handlers.base import BaseHandler
from cms.server.admin.server import AdminWebServer


XSRF_TOKEN = "0123456789abcdef"


class XsrfErrorPageTest(DatabaseMixin, unittest.IsolatedAsyncioTestCase):
    """Serve the real AdminWebServer application."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
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

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    async def post(
        self, path: str, arguments: dict[str, str],
        cookies: dict[str, str] | None = None,
    ) -> HTTPResponse:
        headers = {}
        if cookies:
            headers["Cookie"] = "; ".join(
                "%s=%s" % item for item in cookies.items())
        return await self.client.fetch(
            "http://127.0.0.1:%d%s" % (self.port, path),
            method="POST", body=urlencode(arguments), headers=headers,
            raise_error=False)

    def assert_error_page(self, response: HTTPResponse):
        """Check that response is the normal 403 error page."""
        self.assertEqual(response.code, 403)
        body = response.body.decode()
        self.assertIn("Error 403", body)
        # The page is complete, with its static files.
        self.assertIn("aws_style.css", body)
        self.assertIn("</html>", body)

    async def test_login_without_xsrf(self):
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = await self.post(
                "/login", {"username": "admin", "password": "admin"})
        self.assert_error_page(response)

    async def test_form_after_the_xsrf_cookie_was_cleared(self):
        # The form still carries the token, but the cookie is gone.
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = await self.post(
                "/contests/add", {"name": "c", "_xsrf": XSRF_TOKEN})
        self.assert_error_page(response)

    async def test_form_of_a_logged_in_admin_without_xsrf(self):
        admin = self.add_admin()
        self.session.commit()
        awslogin = sign(session_payload(admin.id))
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = await self.post(
                "/contests/add", {"name": "c"},
                cookies={BaseHandler.COOKIE_NAME: awslogin})
        self.assert_error_page(response)
        self.assertEqual(self.session.query(Contest).count(), 0)


if __name__ == "__main__":
    unittest.main()
