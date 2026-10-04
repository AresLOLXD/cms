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

"""Tests for the renewal of the login cookie in a real CWS.

The session of a contestant lasts cookie_duration seconds after their
last request, whatever that request is: also the ones the pages make in
the background (to poll the notifications, or the status of a
submission) renew the login cookie, so a contestant who keeps a page
open is not logged out. A session with no requests at all still ends
after cookie_duration.

"""

import json
import time
import unittest
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import tornado.web
from tornado.httpclient import HTTPResponse

from cmstestsuite.unit_tests.server.contest.xsrf_error_page_test import \
    CwsTestBase

from cms import config
from cmscommon.datetime import make_datetime, make_timestamp


COOKIE_DURATION = 5 * 60 * 60

# The paths (relative to the contest) of the requests the pages make in
# the background. The task and the submissions do not need to exist: the
# login cookie is renewed before the handler looks for them.
POLLING_PATHS = [
    "/notifications",
    "/tasks/sum/submissions/1",
    "/tasks/sum/submissions/1/details",
    "/tasks/sum/tests/1",
    "/tasks/sum/tests/1/details",
]


class SessionRenewalTests:
    """Tests that hold in both modes."""

    def setUp(self):
        super().setUp()
        duration = patch.object(
            config.contest_web_server, "cookie_duration", COOKIE_DURATION)
        duration.start()
        self.addCleanup(duration.stop)

    @property
    def login_cookie_name(self) -> str:
        return self.contest_name + "_login"

    @property
    def notifications(self) -> str:
        """The path of the notifications the pages poll."""
        return self.contest_path + "/notifications"

    @property
    def cookie_secret(self) -> bytes:
        return self.cws.application.settings["cookie_secret"]

    @contextmanager
    def clock_shifted_by(self, offset: timedelta):
        """Make the handlers see the time as offset later than it is."""
        real_make_datetime = make_datetime

        def shifted_make_datetime(timestamp=None):
            if timestamp is None:
                return real_make_datetime() + offset
            return real_make_datetime(timestamp)

        with patch("cms.server.util.make_datetime", shifted_make_datetime):
            yield

    def login_data(self, cookie: str) -> list:
        """Return what a login cookie holds.

        cookie: the (signed) value of the login cookie.

        return: the username, the password, the timestamp of the last
            update and whether it is an impersonation.

        """
        return json.loads(tornado.web.decode_signed_value(
            self.cookie_secret, self.login_cookie_name, cookie,
            max_age_days=31).decode("utf-8"))

    def idle_since(
        self, cookies: dict[str, str], idle: timedelta
    ) -> dict[str, str]:
        """Return cookies whose login cookie was last renewed idle ago.

        cookies: the cookies of a browser logged in.
        idle: how long ago the login cookie was last renewed.

        return: the same cookies, with the login one re-signed.

        """
        data = self.login_data(cookies[self.login_cookie_name])
        data[2] = make_timestamp() - idle.total_seconds()
        aged = dict(cookies)
        aged[self.login_cookie_name] = tornado.web.create_signed_value(
            self.cookie_secret, self.login_cookie_name,
            json.dumps(data)).decode()
        return aged

    def renewed_login_cookie(self, response: HTTPResponse) -> str | None:
        """Return the login cookie that response sets, if it sets one."""
        return self.set_cookies(response).get(self.login_cookie_name) or None

    def assert_renewed(self, response: HTTPResponse):
        """Check that response sets a login cookie of just now."""
        renewed = self.renewed_login_cookie(response)
        self.assertIsNotNone(renewed, "the login cookie was not renewed")
        self.assertAlmostEqual(
            self.login_data(renewed)[2], time.time(), delta=60)

    async def get_with(self, path: str, cookies: dict[str, str] | None = None,
                       headers: dict[str, str] | None = None) -> HTTPResponse:
        headers = dict(headers or {})
        if cookies:
            headers["Cookie"] = "; ".join(
                "%s=%s" % item for item in cookies.items())
        return await self.client.fetch(
            "http://127.0.0.1:%d%s" % (self.port, path), headers=headers,
            follow_redirects=False, raise_error=False)

    async def login(self) -> dict[str, str]:
        """Log in as a browser would; return the cookies it then holds."""
        token = await self.fetch_xsrf_token()
        response = await self.post(
            self.contest_path + "/login",
            {"username": "myuser", "password": "mypass", "_xsrf": token},
            cookies={"_xsrf": token})
        self.assertEqual(response.code, 302)
        cookies = self.set_cookies(response)
        cookies["_xsrf"] = token
        return cookies

    async def test_a_poll_renews_the_login_cookie(self):
        cookies = await self.login()
        stale = self.idle_since(cookies, timedelta(hours=1))

        response = await self.get_with(self.notifications, stale)

        self.assertEqual(response.code, 200)
        self.assert_renewed(response)
        [header] = [
            header for header in response.headers.get_list("Set-Cookie")
            if header.startswith(self.login_cookie_name + "=")]
        self.assertIn("Max-Age=%d" % COOKIE_DURATION, header)

    async def test_every_polling_handler_renews_the_login_cookie(self):
        cookies = await self.login()
        stale = self.idle_since(cookies, timedelta(hours=1))

        for path in POLLING_PATHS:
            with self.subTest(path=path):
                response = await self.get_with(
                    self.contest_path + path, stale)
                self.assert_renewed(response)

        with self.subTest(path="/tasks/sum/test"):
            response = await self.post(
                self.contest_path + "/tasks/sum/test",
                {"_xsrf": cookies["_xsrf"]}, cookies=stale)
            self.assert_renewed(response)

    async def test_the_session_lasts_while_requests_keep_coming(self):
        cookies = await self.login()

        # Inside the window of the login cookie, a poll renews it.
        with self.clock_shifted_by(timedelta(hours=4)):
            response = await self.get_with(self.notifications, cookies)
        self.assertEqual(response.code, 200)
        renewed = self.renewed_login_cookie(response)
        self.assertIsNotNone(renewed, "the poll did not renew the cookie")
        keeps_going = {**cookies, self.login_cookie_name: renewed}

        # Eight hours after the login, but four after the last request.
        with self.clock_shifted_by(timedelta(hours=8)):
            response = await self.get_with(self.notifications, keeps_going)
            self.assertEqual(response.code, 200)
            # The login cookie that was not renewed has lapsed.
            response = await self.get_with(self.notifications, cookies)
            self.assertEqual(response.code, 403)

        # With no request for longer than the duration, it ends too.
        gap = timedelta(seconds=COOKIE_DURATION + 60)
        with self.clock_shifted_by(timedelta(hours=4) + gap):
            response = await self.get_with(self.notifications, keeps_going)
        self.assertEqual(response.code, 403)

    async def test_a_cookie_idle_for_less_than_the_duration_is_valid(self):
        cookies = await self.login()
        almost = self.idle_since(
            cookies, timedelta(seconds=COOKIE_DURATION - 60))

        response = await self.get_with(self.notifications, almost)

        self.assertEqual(response.code, 200)

    async def test_a_cookie_idle_for_longer_than_the_duration_is_rejected(
        self,
    ):
        cookies = await self.login()
        expired = self.idle_since(
            cookies, timedelta(seconds=COOKIE_DURATION + 60))

        # The polls get an error they can understand...
        response = await self.get_with(self.notifications, expired)
        self.assertEqual(response.code, 403)
        self.assertIsNone(self.renewed_login_cookie(response))

        # ... and the pages send the contestant to the login.
        response = await self.get_with(
            self.contest_path + "/documentation", expired)
        self.assertEqual(response.code, 302)
        self.assertIsNone(self.renewed_login_cookie(response))

    async def test_a_poll_keeps_an_impersonation_cookie_impersonating(self):
        data = json.dumps(["myuser", "", make_timestamp() - 3600, True])
        impersonation = {
            self.login_cookie_name: tornado.web.create_signed_value(
                self.cookie_secret, self.login_cookie_name, data).decode()}

        response = await self.get_with(self.notifications, impersonation)

        self.assertEqual(response.code, 200)
        self.assert_renewed(response)
        username, password, _, impersonated = self.login_data(
            self.renewed_login_cookie(response))
        self.assertEqual((username, password, impersonated),
                         ("myuser", "", True))

    async def test_a_poll_authenticated_by_header_needs_no_cookie(self):
        cookies = await self.login()

        response = await self.get_with(
            self.notifications,
            headers={"X-CMS-Authorization": cookies[self.login_cookie_name]})

        self.assertEqual(response.code, 200)


class SingleContestSessionRenewalTest(SessionRenewalTests, CwsTestBase):
    multi_contest = False


class MultiContestSessionRenewalTest(SessionRenewalTests, CwsTestBase):
    multi_contest = True


del CwsTestBase


if __name__ == "__main__":
    unittest.main()
