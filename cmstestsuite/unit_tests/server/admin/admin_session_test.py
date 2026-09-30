#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""Tests for the AWS admin session cookie (awslogin).

Covers decode_admin_session() directly, the page path
(BaseHandler.prepare() -> get_current_user()) and the /rpc path
(AdminWebServer.is_rpc_authorized via the real RPCHandler), all through
real requests against real Tornado servers, with cookies signed by
the same secret the AdminWebServer application uses.

"""

import json
import unittest

import tornado.web
from tornado.httpclient import AsyncHTTPClient
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_sockets

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.server.admin.handlers.base import BaseHandler, decode_admin_session
from cms.server.admin.server import AdminWebServer
from cmscommon.binary import hex_to_bin
from cmscommon.datetime import make_timestamp


# Same derivation as AdminWebServer.__init__'s "cookie_secret".
SECRET = hex_to_bin(config.web_server.secret_key)
DURATION = config.admin_web_server.cookie_duration
XSRF_TOKEN = "0123456789abcdef"


def sign(value: str | bytes) -> str:
    """Return value signed as the awslogin cookie by the AWS secret."""
    return tornado.web.create_signed_value(
        SECRET, BaseHandler.COOKIE_NAME, value).decode()


def session_payload(admin_id: object, timestamp: object = None) -> str:
    if timestamp is None:
        timestamp = make_timestamp()
    return json.dumps({"id": admin_id, "timestamp": timestamp})


def tamper(signed: str) -> str:
    """Flip the last character of the signature."""
    return signed[:-1] + ("0" if signed[-1] != "0" else "1")


def bad_cookies(admin_id: int = 1) -> dict[str, tuple[str | None, bool]]:
    """Every invalid awslogin cookie, with whether the page path
    clears it.

    admin_id: the admin id to put in the payloads that have one.

    return: name -> (cookie value or None for no cookie, whether
        BaseHandler._get_session_admin_id clears the cookie). A bad
        signature or no cookie at all isn't cleared (it reads as no
        cookie); a validly signed but unusable payload is.

    """
    now = make_timestamp()
    return {
        "no cookie": (None, False),
        "tampered signature": (
            tamper(sign(session_payload(admin_id))), False),
        "unsigned": (session_payload(admin_id), False),
        "expired": (
            sign(session_payload(admin_id, now - DURATION - 10.0)), True),
        "id not int": (sign(session_payload(str(admin_id))), True),
        "timestamp int": (sign(session_payload(admin_id, int(now))), True),
        "bad json": (sign("not json"), True),
        "not utf-8": (sign(b"\xff\xfe\xfd"), True),
        "missing id": (sign(json.dumps({"timestamp": now})), True),
        "missing timestamp": (sign(json.dumps({"id": admin_id})), True),
    }


class _DecodeHandler(tornado.web.RequestHandler):
    """A plain Tornado handler (like RPCHandler, not a BaseHandler)."""

    def get(self):
        self.write(json.dumps({"id": decode_admin_session(self)}))


class TestDecodeAdminSession(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        app = tornado.web.Application(
            [(r"/", _DecodeHandler)], cookie_secret=SECRET)
        sockets = bind_sockets(0, "127.0.0.1")
        self.port = sockets[0].getsockname()[1]
        self.server = HTTPServer(app)
        self.server.add_sockets(sockets)
        self.client = AsyncHTTPClient()

    async def asyncTearDown(self):
        self.client.close()
        self.server.stop()
        await self.server.close_all_connections()

    async def _decode(self, cookie: str | None):
        headers = {}
        if cookie is not None:
            headers["Cookie"] = "%s=%s" % (BaseHandler.COOKIE_NAME, cookie)
        response = await self.client.fetch(
            "http://127.0.0.1:%d/" % self.port, headers=headers,
            raise_error=False)
        self.assertEqual(response.code, 200)
        self.assertNotIn("Set-Cookie", response.headers)
        return json.loads(response.body)["id"]

    async def test_valid(self):
        self.assertEqual(await self._decode(sign(session_payload(42))), 42)

    async def test_nearly_expired_is_still_valid(self):
        cookie = sign(session_payload(42, make_timestamp() - DURATION + 60.0))
        self.assertEqual(await self._decode(cookie), 42)

    async def test_invalid(self):
        for name, (cookie, _) in bad_cookies().items():
            with self.subTest(name):
                self.assertIsNone(await self._decode(cookie))


class _WhoAmIHandler(BaseHandler):
    """Runs the REAL BaseHandler.prepare(); never touches current_user
    itself, so whatever is cached afterwards was set by prepare()."""

    async def get(self):
        self.application.last_handler = self
        self.write("ok")


class _AWSTestCase(DatabaseMixin, unittest.IsolatedAsyncioTestCase):
    """Serve the real AdminWebServer application (plus a test route)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aws = AdminWebServer(0)
        cls.aws.application.add_handlers(r".*", [(r"/whoami", _WhoAmIHandler)])

    async def asyncSetUp(self):
        self.aws.application.last_handler = None
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

    def _cookie_header(self, cookie: str | None) -> dict[str, str]:
        cookies = ["_xsrf=%s" % XSRF_TOKEN]
        if cookie is not None:
            cookies.append("%s=%s" % (BaseHandler.COOKIE_NAME, cookie))
        return {"Cookie": "; ".join(cookies)}

    async def _whoami(self, cookie: str | None):
        response = await self.client.fetch(
            "http://127.0.0.1:%d/whoami" % self.port,
            headers=self._cookie_header(cookie), raise_error=False)
        self.assertEqual(response.code, 200)
        handler = self.aws.application.last_handler
        self.assertIsNotNone(handler)
        return response, handler

    async def _rpc(self, cookie: str | None,
                   service="AdminWebServer", method="submissions_status"):
        headers = self._cookie_header(cookie)
        headers["X-XSRFToken"] = XSRF_TOKEN
        # Deliberately not application/json: an authorized call gets
        # past the auth check and stops at the 415 content-type check
        # instead of issuing a real RPC.
        headers["Content-Type"] = "text/plain"
        response = await self.client.fetch(
            "http://127.0.0.1:%d/rpc/%s/0/%s" % (self.port, service, method),
            method="POST", body="{}", headers=headers, raise_error=False)
        self.assertIn(response.code, (403, 415))
        return response.code == 415

    @staticmethod
    def _awslogin_set_cookies(response) -> list[str]:
        return [value for value in response.headers.get_list("Set-Cookie")
                if value.startswith(BaseHandler.COOKIE_NAME + "=")]


class TestPagePath(_AWSTestCase):

    async def test_prepare_sets_current_user_and_loop(self):
        admin = self.add_admin()
        _, handler = await self._whoami(sign(session_payload(admin.id)))
        # Read the attribute prepare() caches, not the current_user
        # property (which would lazily compute it if missing).
        self.assertIn("_current_user", handler.__dict__)
        self.assertEqual(handler._current_user.id, admin.id)
        self.assertIsNotNone(handler._loop)

    async def test_valid_enabled_admin_refreshes_cookie_with_max_age(self):
        admin = self.add_admin()
        response, handler = await self._whoami(sign(session_payload(admin.id)))
        self.assertEqual(handler._current_user.id, admin.id)
        [set_cookie] = self._awslogin_set_cookies(response)
        self.assertIn("Max-Age=%d" % DURATION, set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        # The refreshed cookie is itself a valid session.
        refreshed = set_cookie.split(";")[0].split("=", 1)[1]
        _, handler = await self._whoami(refreshed)
        self.assertEqual(handler._current_user.id, admin.id)

    async def test_disabled_admin(self):
        admin = self.add_admin(enabled=False)
        response, handler = await self._whoami(sign(session_payload(admin.id)))
        self.assertIsNone(handler._current_user)
        [set_cookie] = self._awslogin_set_cookies(response)
        self.assertTrue(set_cookie.startswith(BaseHandler.COOKIE_NAME + "=;")
                        or set_cookie.startswith(
                            BaseHandler.COOKIE_NAME + '="";'))

    async def test_deleted_admin(self):
        admin = self.add_admin()
        admin_id = admin.id
        self.session.delete(admin)
        self.session.commit()
        response, handler = await self._whoami(sign(session_payload(admin_id)))
        self.assertIsNone(handler._current_user)
        self.assertEqual(len(self._awslogin_set_cookies(response)), 1)

    async def test_invalid_cookies(self):
        # Point the (bad) payloads at a real enabled admin, so
        # rejection can only come from the cookie checks.
        admin = self.add_admin()
        for name, (cookie, cleared) in bad_cookies(admin.id).items():
            with self.subTest(name):
                response, handler = await self._whoami(cookie)
                self.assertIsNone(handler._current_user)
                self.assertEqual(
                    len(self._awslogin_set_cookies(response)),
                    1 if cleared else 0)


class TestRpcPath(_AWSTestCase):

    async def test_valid_admin_is_authorized(self):
        admin = self.add_admin()
        self.assertTrue(await self._rpc(sign(session_payload(admin.id))))

    async def test_disallowed_rpc_is_not_authorized(self):
        admin = self.add_admin(permission_all=False)
        self.assertFalse(await self._rpc(
            sign(session_payload(admin.id)),
            service="EvaluationService", method="enable_worker"))

    async def test_disabled_admin_is_not_authorized(self):
        admin = self.add_admin(enabled=False)
        self.assertFalse(await self._rpc(sign(session_payload(admin.id))))

    async def test_deleted_admin_is_not_authorized(self):
        admin = self.add_admin()
        admin_id = admin.id
        self.session.delete(admin)
        self.session.commit()
        self.assertFalse(await self._rpc(sign(session_payload(admin_id))))

    async def test_forged_cookie_is_not_authorized(self):
        admin = self.add_admin()
        # Signed with a different secret.
        forged = tornado.web.create_signed_value(
            b"not the secret", BaseHandler.COOKIE_NAME,
            session_payload(admin.id)).decode()
        self.assertFalse(await self._rpc(forged))

    async def test_invalid_cookies_are_not_authorized(self):
        admin = self.add_admin()
        for name, (cookie, _) in bad_cookies(admin.id).items():
            with self.subTest(name):
                self.assertFalse(await self._rpc(cookie))


if __name__ == "__main__":
    unittest.main()
