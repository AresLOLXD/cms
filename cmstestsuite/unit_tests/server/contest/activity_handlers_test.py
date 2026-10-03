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

"""Tests for what a real ContestWebServer records in the activity log.

"""

import json
import unittest
import uuid

import tornado.web
from tornado.httpclient import HTTPResponse

from cmstestsuite.unit_tests.server.contest.xsrf_error_page_test import \
    CwsTestBase

from cms.db import Participation, User
from cms.server.contest.handlers.contest import DEVICE_COOKIE_NAME
from cmscommon.datetime import make_datetime, make_timestamp


class ActivityHandlersTests:
    """Tests that hold in both modes."""

    def setUp(self):
        super().setUp()
        self.participation_id = self.session.query(Participation.id) \
            .join(User).filter(User.username == "myuser").scalar()
        self.session.close()

    @property
    def main_page(self) -> str:
        """The path of the contest's main page."""
        return self.contest_path or "/"

    @property
    def login_cookie_name(self) -> str:
        return self.contest_name + "_login"

    def pending(self) -> dict:
        return self.cws.activity_recorder.pending()

    def signed(self, name: str, value: str) -> str:
        return tornado.web.create_signed_value(
            self.cws.application.settings["cookie_secret"], name, value
        ).decode()

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

    def device_of(self, cookies: dict[str, str]) -> uuid.UUID:
        value = tornado.web.decode_signed_value(
            self.cws.application.settings["cookie_secret"],
            DEVICE_COOKIE_NAME, cookies[DEVICE_COOKIE_NAME],
            max_age_days=365)
        return uuid.UUID(value.decode())

    async def test_login_records_a_login_with_a_new_device(self):
        cookies = await self.login()

        device = self.device_of(cookies)
        [(key, segments)] = self.pending().items()
        self.assertEqual(key, (self.participation_id, device, "127.0.0.1"))
        self.assertEqual([s.login for s in segments], [True])

    async def test_later_requests_reuse_the_device(self):
        cookies = await self.login()

        response = await self.get_with(self.main_page, cookies)

        self.assertEqual(response.code, 200)
        self.assertNotIn(DEVICE_COOKIE_NAME, self.set_cookies(response))
        [(key, segments)] = self.pending().items()
        self.assertEqual(key[1], self.device_of(cookies))
        self.assertEqual(len(segments), 1)
        self.assertGreater(segments[0].last_seen, segments[0].first_seen)

    async def test_badly_signed_device_cookie_is_replaced(self):
        cookies = await self.login()
        cookies[DEVICE_COOKIE_NAME] = "forged"

        response = await self.get_with(self.main_page, cookies)

        new_device = self.device_of(self.set_cookies(response))
        self.assertIn((self.participation_id, new_device, "127.0.0.1"),
                      self.pending())

    async def test_logout_records_the_logout(self):
        cookies = await self.login()

        response = await self.post(
            self.contest_path + "/logout", {"_xsrf": cookies["_xsrf"]},
            cookies=cookies)

        self.assertEqual(response.code, 302)
        [segments] = self.pending().values()
        self.assertIsNotNone(segments[-1].logged_out_at)

    async def test_anonymous_requests_are_not_recorded(self):
        response = await self.get_with(self.main_page)

        self.assertEqual(response.code, 200)
        self.assertEqual(self.pending(), {})
        self.assertNotIn(DEVICE_COOKIE_NAME, self.set_cookies(response))

    async def test_impersonation_is_not_recorded(self):
        cookie = json.dumps(
            ["myuser", "", make_timestamp(make_datetime()), True])

        response = await self.get_with(
            self.main_page,
            {self.login_cookie_name: self.signed(self.login_cookie_name,
                                                 cookie)})

        self.assertEqual(response.code, 200)
        self.assertEqual(self.pending(), {})

    async def test_header_authentication_has_no_device(self):
        cookies = await self.login()
        self.pending().clear()

        response = await self.get_with(
            self.main_page,
            headers={"X-CMS-Authorization": cookies[self.login_cookie_name]})

        self.assertEqual(response.code, 200)
        self.assertEqual(list(self.pending()),
                         [(self.participation_id, None, "127.0.0.1")])
        self.assertNotIn(DEVICE_COOKIE_NAME, self.set_cookies(response))


class SingleContestActivityHandlersTest(ActivityHandlersTests, CwsTestBase):
    multi_contest = False


class MultiContestActivityHandlersTest(ActivityHandlersTests, CwsTestBase):
    multi_contest = True


del CwsTestBase


if __name__ == "__main__":
    unittest.main()
