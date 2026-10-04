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

"""Tests for the requests of scanners and bots to a multi-contest CWS.

Bots probe the server asking for "contests" named like sensitive files
(with the slashes percent-encoded, so that the router keeps them in one
path segment). Such a contest does not exist, so the request has to end
in a normal 404 page, like the one of any other unknown contest, with no
uncaught exception: the name of the contest must never reach, for
example, the name of a cookie.

"""

import contextlib
import unittest
from urllib.parse import quote

from tornado.httpclient import HTTPResponse

from cmstestsuite.unit_tests.server.contest.xsrf_error_page_test import \
    CwsTestBase


# Names that no contest can have (contest names are made of letters,
# digits, underscores and dashes), as scanners ask for them, and a name
# that is valid but unknown.
UNKNOWN_CONTEST_NAMES = [
    "../../../../../../etc/passwd",
    "../.env",
    "/.aws/credentials",
    "a b",
    "café",
    "nosuchcontest",
]


class ScannerRequestsTest(CwsTestBase):
    """A multi-contest CWS asked for contests that do not exist."""

    multi_contest = True

    @contextlib.contextmanager
    def assert_no_errors_logged(self):
        """Check that nothing is logged as an error in the block."""
        with self.assertNoLogs("tornado.application", "ERROR"), \
                self.assertNoLogs("cms", "ERROR"):
            yield

    def assert_normal_404(self, response: HTTPResponse):
        """Check that response is the normal 404 error page."""
        self.assertEqual(response.code, 404)
        body = response.body.decode()
        self.assertRegex(body, r"<title>\s*Error 404")
        # The page is styled, so the static URLs were computed.
        self.assertIn("cws_style.css", body)

    async def test_pages_of_unknown_contests_are_404(self):
        for name in UNKNOWN_CONTEST_NAMES:
            for suffix in ["", "/documentation", "/notifications"]:
                path = "/" + quote(name, safe="") + suffix
                with self.subTest(path=path):
                    with self.assert_no_errors_logged():
                        response = await self.get(path)
                    self.assert_normal_404(response)

    async def test_post_to_an_unknown_contest_is_404(self):
        token = await self.fetch_xsrf_token()
        for name in UNKNOWN_CONTEST_NAMES:
            path = "/" + quote(name, safe="") + "/login"
            with self.subTest(path=path):
                with self.assert_no_errors_logged():
                    response = await self.post(
                        path,
                        {"username": "myuser", "password": "mypass",
                         "_xsrf": token},
                        cookies={"_xsrf": token})
                self.assert_normal_404(response)

    async def test_no_login_cookie_is_set_for_an_unknown_contest(self):
        for name in UNKNOWN_CONTEST_NAMES:
            path = "/" + quote(name, safe="")
            with self.subTest(path=path):
                response = await self.get(path)
                self.assertEqual(
                    [header for header in
                     response.headers.get_list("Set-Cookie")
                     if "_login" in header], [])

    async def test_a_known_contest_is_still_served(self):
        # Control: the same request for a contest that exists.
        response = await self.get(self.contest_path)
        self.assertEqual(response.code, 200)


del CwsTestBase


if __name__ == "__main__":
    unittest.main()
