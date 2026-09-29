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
handlers only set up what the error page needs (the URL helpers) in
prepare(). These tests send real requests, without a valid XSRF token,
through the WSGI application of a real AdminWebServer and check that
the admin gets a readable 403 error page, and that nothing is logged as
an error.

"""

import collections
import json
import unittest

try:
    collections.MutableMapping
except AttributeError:
    # Monkey-patch: Tornado 4.5.3 does not work on Python 3.11 by default
    collections.MutableMapping = collections.abc.MutableMapping

from tornado.web import create_signed_value
from werkzeug.test import Client, TestResponse

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.db import Contest
from cms.server.admin.authentication import AWSAuthMiddleware
from cms.server.admin.server import AdminWebServer
from cmscommon.datetime import make_timestamp


XSRF_TOKEN = "0123456789abcdef"


class XsrfErrorPageTest(DatabaseMixin, unittest.TestCase):
    """Serve the real AdminWebServer application."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aws = AdminWebServer(0)

    def setUp(self):
        super().setUp()
        # This is the application that the WSGI server of the service
        # runs, including all its middlewares.
        self.client = Client(self.aws)

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def post(self, path: str, arguments: dict[str, str]) -> TestResponse:
        """Send a form, with the given arguments."""
        return self.client.post(path, data=arguments)

    def assert_error_page(self, response: TestResponse):
        """Check that response is the normal 403 error page."""
        self.assertEqual(response.status_code, 403)
        body = response.text
        self.assertIn("Error 403", body)
        # The page is complete, with its static files.
        self.assertIn("aws_style.css", body)
        self.assertIn("</html>", body)

    def test_login_without_xsrf(self):
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = self.post(
                "/login", {"username": "admin", "password": "admin"})
        self.assert_error_page(response)

    def test_form_after_the_xsrf_cookie_was_cleared(self):
        # The form still carries the token, but the cookie is gone.
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = self.post(
                "/contests/add", {"name": "c", "_xsrf": XSRF_TOKEN})
        self.assert_error_page(response)

    def test_form_of_a_logged_in_admin_without_xsrf(self):
        admin = self.add_admin()
        awslogin = create_signed_value(
            bytes.fromhex(config.web_server.secret_key),
            AWSAuthMiddleware.COOKIE,
            json.dumps({"id": admin.id, "timestamp": make_timestamp()}))
        self.client.set_cookie(AWSAuthMiddleware.COOKIE, awslogin.decode())
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = self.post("/contests/add", {"name": "c"})
        self.assert_error_page(response)
        self.assertEqual(self.session.query(Contest).count(), 0)


if __name__ == "__main__":
    unittest.main()
