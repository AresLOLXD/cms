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

"""Tests for the error page of a POST that fails the XSRF check in CWS.

Tornado checks the XSRF token before it calls prepare(), and the
handlers only set up what the error page needs (the contest, the URL
helpers, the render parameters) in prepare(). These tests send real
requests, without a valid XSRF token, through the WSGI application of a
real ContestWebServer (single-contest and multi-contest mode) and check
that the contestant gets a readable 403 error page, and that nothing is
logged as an error.

"""

import unittest
from unittest.mock import patch

from werkzeug.test import Client, TestResponse

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.server.contest.handlers import HANDLERS
from cms.server.contest.server import ContestWebServer
from cmscommon.crypto import build_password


class CwsTestBase(DatabaseMixin, unittest.TestCase):
    """A real ContestWebServer serving one contest with one contestant.

    Subclasses say whether the server serves a single contest or all
    of them, with multi_contest.

    """

    multi_contest: bool

    def setUp(self):
        super().setUp()
        self.delete_data()
        contest = self.add_contest(allow_password_authentication=True)
        user = self.add_user(
            username="myuser", password=build_password("mypass"))
        self.add_participation(contest=contest, user=user)
        self.session.commit()
        self.contest_name = contest.name
        self.contest_id = contest.id
        self.session.close()

        # ContestWebServer.__init__ appends to the module's HANDLERS
        # every time; give it a copy so the tests leave no trace.
        patcher = patch(
            "cms.server.contest.server.HANDLERS", list(HANDLERS))
        patcher.start()
        self.addCleanup(patcher.stop)
        cws = ContestWebServer(
            0, contest_id=None if self.multi_contest else self.contest_id)
        # This is the application that the WSGI server of the service
        # runs, including all its middlewares.
        self.client = Client(cws)

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    @property
    def contest_path(self) -> str:
        """The path prefix of the contest's pages."""
        return "/" + self.contest_name if self.multi_contest else ""

    def get(self, path: str) -> TestResponse:
        """Send a GET request, as a browser at localhost would."""
        return self.client.get(
            path, headers={"Accept-Language": "en"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"})

    def post(self, path: str, arguments: dict[str, str]) -> TestResponse:
        """Send a form, as a browser at localhost would."""
        return self.client.post(
            path, data=arguments, headers={"Accept-Language": "en"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"})

    def fetch_xsrf_token(self) -> str:
        """Load the contest's page, as a browser would, for its token.

        The client also keeps the XSRF cookie that the response sets.

        return: the token to put in the forms.

        """
        response = self.get(self.contest_path or "/")
        self.assertEqual(response.status_code, 200)
        return self.client.get_cookie("_xsrf").value

    def post_login(self, arguments: dict[str, str]) -> TestResponse:
        """Send the login form, with the given arguments."""
        return self.post(self.contest_path + "/login", arguments)

    def assert_error_page(self, response: TestResponse):
        """Check that response is the normal 403 error page."""
        self.assertEqual(response.status_code, 403)
        body = response.text
        self.assertRegex(body, r"<title>\s*Error 403")
        # The page is styled, so the static URLs were computed.
        self.assertIn("cws_style.css", body)


class XsrfErrorPageTests:
    """Tests that hold in both modes."""

    def test_login_without_xsrf(self):
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = self.post_login(
                {"username": "myuser", "password": "mypass"})
        self.assert_error_page(response)
        self.assertIsNone(
            self.client.get_cookie(self.contest_name + "_login"))

    def test_login_after_the_xsrf_cookie_was_cleared(self):
        # The form still carries the token, but the cookie is gone.
        token = self.fetch_xsrf_token()
        self.client.delete_cookie("_xsrf")
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = self.post_login(
                {"username": "myuser", "password": "mypass", "_xsrf": token})
        self.assert_error_page(response)

    def test_submit_after_the_xsrf_cookie_was_cleared(self):
        token = self.fetch_xsrf_token()
        login = self.post_login(
            {"username": "myuser", "password": "mypass", "_xsrf": token})
        self.assertEqual(login.status_code, 302)
        self.assertIsNotNone(
            self.client.get_cookie(self.contest_name + "_login"))

        # Logged in, but with no XSRF cookie any more.
        self.client.delete_cookie("_xsrf")
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = self.post(
                self.contest_path + "/tasks/sum/submit", {"_xsrf": token})
        self.assert_error_page(response)

    def test_login_with_a_valid_xsrf_still_works(self):
        # Control: the same request with the token and its cookie.
        token = self.fetch_xsrf_token()
        response = self.post_login(
            {"username": "myuser", "password": "mypass", "_xsrf": token})
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(
            self.client.get_cookie(self.contest_name + "_login"))


class SingleContestXsrfErrorPageTest(XsrfErrorPageTests, CwsTestBase):
    multi_contest = False


class MultiContestXsrfErrorPageTest(XsrfErrorPageTests, CwsTestBase):
    multi_contest = True

    def test_contest_list_post_without_xsrf(self):
        # The contest list is not a contest's page: its handler is a
        # BaseHandler, not a ContestHandler.
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = self.post("/", {})
        self.assert_error_page(response)


if __name__ == "__main__":
    unittest.main()
