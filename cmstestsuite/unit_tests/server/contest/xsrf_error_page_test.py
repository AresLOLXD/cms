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
requests, without a valid XSRF token, through a real ContestWebServer
(single-contest and multi-contest mode) and check that the contestant
gets a readable 403 error page, and nothing is logged as an error.

"""

import unittest
from http.cookies import SimpleCookie
from unittest.mock import patch
from urllib.parse import urlencode

from tornado.httpclient import AsyncHTTPClient, HTTPResponse
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_sockets

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.server.contest.handlers import HANDLERS
from cms.server.contest.server import ContestWebServer
from cmscommon.crypto import build_password


class CwsTestBase(DatabaseMixin, unittest.IsolatedAsyncioTestCase):
    """A real ContestWebServer serving one contest with one contestant.

    Subclasses say whether the server serves a single contest or all
    of them, with multi_contest.

    """

    multi_contest: bool

    def setUp(self):
        super().setUp()
        self.delete_data()
        contest = self.add_contest(
            allow_password_authentication=True, active=True)
        user = self.add_user(
            username="myuser", password=build_password("mypass"))
        self.add_participation(contest=contest, user=user)
        # Read what the tests need before the commit expires it.
        self.contest_name = contest.name
        self.session.commit()
        self.contest_id = contest.id
        self.session.close()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    async def asyncSetUp(self):
        # ContestWebServer.__init__ appends to the module's HANDLERS
        # every time; give it a copy so the tests leave no trace.
        patcher = patch(
            "cms.server.contest.server.HANDLERS", list(HANDLERS))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cws = ContestWebServer(
            0, contest_id=None if self.multi_contest else self.contest_id)
        sockets = bind_sockets(0, "127.0.0.1")
        self.port = sockets[0].getsockname()[1]
        self.server = HTTPServer(self.cws.application)
        self.server.add_sockets(sockets)
        self.client = AsyncHTTPClient()

    async def asyncTearDown(self):
        self.client.close()
        self.server.stop()
        await self.server.close_all_connections()

    @property
    def contest_path(self) -> str:
        """The path prefix of the contest's pages."""
        return "/" + self.contest_name if self.multi_contest else ""

    async def get(self, path: str) -> HTTPResponse:
        return await self.client.fetch(
            "http://127.0.0.1:%d%s" % (self.port, path), raise_error=False)

    async def post(
        self, path: str, arguments: dict[str, str],
        cookies: dict[str, str] | None = None,
    ) -> HTTPResponse:
        headers = {"Accept-Language": "en"}
        if cookies:
            headers["Cookie"] = "; ".join(
                "%s=%s" % item for item in cookies.items())
        return await self.client.fetch(
            "http://127.0.0.1:%d%s" % (self.port, path),
            method="POST", body=urlencode(arguments), headers=headers,
            follow_redirects=False, raise_error=False)

    @staticmethod
    def set_cookies(response: HTTPResponse) -> dict[str, str]:
        """The cookies the response sets, by name."""
        cookies = SimpleCookie()
        for header in response.headers.get_list("Set-Cookie"):
            cookies.load(header)
        return {name: morsel.value for name, morsel in cookies.items()}

    async def fetch_xsrf_token(self) -> str:
        """Load the contest's page, as a browser would, for its token."""
        response = await self.get(self.contest_path or "/")
        self.assertEqual(response.code, 200)
        return self.set_cookies(response)["_xsrf"]

    def assert_error_page(self, response: HTTPResponse):
        """Check that response is the normal 403 error page."""
        self.assertEqual(response.code, 403)
        body = response.body.decode()
        self.assertRegex(body, r"<title>\s*Error 403")
        # The page is styled, so the static URLs were computed.
        self.assertIn("cws_style.css", body)


class XsrfErrorPageTests:
    """Tests that hold in both modes."""

    async def test_login_without_xsrf(self):
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = await self.post(
                self.contest_path + "/login",
                {"username": "myuser", "password": "mypass"})
        self.assert_error_page(response)
        self.assertNotIn(
            self.contest_name + "_login", self.set_cookies(response))

    async def test_login_after_the_xsrf_cookie_was_cleared(self):
        # The form still carries the token, but the cookie is gone.
        token = await self.fetch_xsrf_token()
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = await self.post(
                self.contest_path + "/login",
                {"username": "myuser", "password": "mypass", "_xsrf": token})
        self.assert_error_page(response)

    async def test_submit_after_the_xsrf_cookie_was_cleared(self):
        token = await self.fetch_xsrf_token()
        login = await self.post(
            self.contest_path + "/login",
            {"username": "myuser", "password": "mypass", "_xsrf": token},
            cookies={"_xsrf": token})
        self.assertEqual(login.code, 302)
        login_cookie = self.set_cookies(login)[self.contest_name + "_login"]

        # Logged in, but with no XSRF cookie any more.
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = await self.post(
                self.contest_path + "/tasks/sum/submit", {"_xsrf": token},
                cookies={self.contest_name + "_login": login_cookie})
        self.assert_error_page(response)

    async def test_login_with_a_valid_xsrf_still_works(self):
        # Control: the same request with the token and its cookie.
        token = await self.fetch_xsrf_token()
        response = await self.post(
            self.contest_path + "/login",
            {"username": "myuser", "password": "mypass", "_xsrf": token},
            cookies={"_xsrf": token})
        self.assertEqual(response.code, 302)
        self.assertIn(self.contest_name + "_login", self.set_cookies(response))


class SingleContestXsrfErrorPageTest(XsrfErrorPageTests, CwsTestBase):
    multi_contest = False


class MultiContestXsrfErrorPageTest(XsrfErrorPageTests, CwsTestBase):
    multi_contest = True

    async def test_contest_list_post_without_xsrf(self):
        # The contest list is not a contest's page: its handler is a
        # BaseHandler, not a ContestHandler.
        with self.assertNoLogs("tornado.application", "ERROR"):
            response = await self.post("/", {})
        self.assert_error_page(response)


if __name__ == "__main__":
    unittest.main()
