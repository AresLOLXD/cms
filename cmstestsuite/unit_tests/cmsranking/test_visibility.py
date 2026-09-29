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

"""Tests for the MC-2 visibility guard of RWS group namespaces."""

import json
import os
import re
import shutil
import tempfile
import threading
import unittest
from base64 import b64encode
from importlib.resources import files
from unittest.mock import patch

from gevent import socket
from gevent.pywsgi import WSGIServer
from werkzeug.test import Client, create_environ

from cmscommon.crypto import build_password, hash_password
from cmsranking.Config import Config
from cmsranking.RankingWebServer import NamespaceDispatcher, \
    build_ranking_app
from cmsranking.visibility import STAFF_COOKIE, VISIBILITY_FILE, \
    VisibilityGuard, VisibilityState, staff_cookie_value


USERNAME = "rws"
PASSWORD = "secret"
AUTH = {"Authorization": "Basic " + b64encode(
    ("%s:%s" % (USERNAME, PASSWORD)).encode()).decode()}
CONTEST = {"name": "Day 1", "begin": 0, "end": 10, "score_precision": 0}
STAFF_HASH = build_password("s3cret", "plaintext")

# Every read endpoint of a namespace, including static files.
DATA_PATHS = ["contests/", "contests/c1", "tasks/", "teams/", "users/",
              "submissions/", "subchanges/", "sublist/u1", "scores",
              "history", "events", "config", "logo", "faces/u1",
              "flags/t1", "Ranking.js", "img/favicon.ico"]


class VisibilityTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.lib_dir = os.path.join(self.tmp, "lib")
        self.config = Config(username=USERNAME, password=PASSWORD,
                             lib_dir=self.lib_dir,
                             log_dir=os.path.join(self.tmp, "log"))
        self.web_dir = str(files("cmsranking") / "static")
        self.client = self.make_client()

    def make_client(self) -> Client:
        def make_app(lib_dir):
            return build_ranking_app(self.config, lib_dir, self.web_dir)
        dispatcher = NamespaceDispatcher(
            make_app(self.lib_dir), os.path.join(self.lib_dir, "groups"),
            make_app, USERNAME, PASSWORD, "Scoreboard")
        # The tests send their cookies explicitly: with a cookie jar the
        # client would replace a Cookie header of its own choosing.
        return Client(dispatcher, use_cookies=False)

    def put_contest(self, prefix: str):
        return self.client.put(
            prefix + "/contests/", data=json.dumps({"c1": CONTEST}),
            content_type="application/json", headers=AUTH)

    def put_visibility(self, group: str, hidden: bool,
                       staff_password: str | None = STAFF_HASH,
                       auth: bool = True):
        return self.client.put(
            "/%s/visibility" % group,
            data=json.dumps({"hidden": hidden,
                             "staff_password": staff_password}),
            content_type="application/json",
            headers=AUTH if auth else {})


class TestVisibilityUpdate(VisibilityTestCase):

    def test_group_without_state_is_public(self):
        self.put_contest("/olim")
        self.assertEqual(self.client.get("/olim/contests/").json,
                         {"c1": CONTEST})

    def test_put_requires_credentials(self):
        self.put_contest("/olim")
        response = self.put_visibility("olim", True, auth=False)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(os.path.exists(os.path.join(
            self.lib_dir, "groups", "olim", VISIBILITY_FILE)))

    def test_put_rejects_bad_bodies(self):
        self.put_contest("/olim")
        for body in ["not json", json.dumps([]),
                     json.dumps({"hidden": "yes", "staff_password": None}),
                     json.dumps({"hidden": True, "staff_password": 3}),
                     json.dumps({"hidden": True,
                                 "staff_password": "no-method"})]:
            response = self.client.put(
                "/olim/visibility", data=body,
                content_type="application/json", headers=AUTH)
            self.assertEqual(response.status_code, 400, msg=body)

    def test_put_creates_namespace(self):
        self.assertEqual(self.put_visibility("omips", True).status_code,
                         204)
        self.assertTrue(os.path.isfile(os.path.join(
            self.lib_dir, "groups", "omips", VISIBILITY_FILE)))

    def test_state_persists_across_restart(self):
        self.put_contest("/olim")
        self.put_visibility("olim", True)
        self.client = self.make_client()
        self.assertEqual(self.client.get("/olim/contests/").status_code,
                         403)


class TestHiddenGroup(VisibilityTestCase):

    def setUp(self):
        super().setUp()
        self.put_contest("/olim")
        self.put_visibility("olim", True)

    def test_root_page_shows_notice(self):
        response = self.client.get("/olim/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        text = response.get_data(as_text=True)
        self.assertIn("Este ranking está oculto por ahora.", text)
        self.assertIn('action="staff-login"', text)

    def test_every_data_endpoint_is_forbidden(self):
        for path in DATA_PATHS:
            response = self.client.get("/olim/" + path)
            self.assertEqual(response.status_code, 403, msg=path)
            self.assertEqual(response.headers["Cache-Control"], "no-store",
                             msg=path)

    def test_proxy_writes_still_accepted(self):
        response = self.client.put(
            "/olim/users/", data=json.dumps({}),
            content_type="application/json", headers=AUTH)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get("/olim/users/").status_code, 403)

    def test_unhide_restores_public_access(self):
        self.put_visibility("olim", False)
        self.assertEqual(self.client.get("/olim/contests/").json,
                         {"c1": CONTEST})

    def test_root_ranking_and_other_groups_unaffected(self):
        self.put_contest("")
        self.put_contest("/omips")
        self.assertEqual(self.client.get("/contests/").status_code, 200)
        self.assertEqual(self.client.get("/omips/contests/").status_code,
                         200)

    def test_malformed_state_fails_closed(self):
        self.put_contest("/omips")
        path = os.path.join(self.lib_dir, "groups", "omips",
                            VISIBILITY_FILE)
        with open(path, "w") as f:
            f.write("{broken")
        with self.assertLogs("cmsranking.visibility", "ERROR"):
            self.client = self.make_client()
        self.assertEqual(self.client.get("/omips/contests/").status_code,
                         403)


class TestStaffLogin(VisibilityTestCase):

    def setUp(self):
        super().setUp()
        patcher = patch("cmsranking.visibility.time.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)
        self.put_contest("/olim")
        self.put_visibility("olim", True)

    def login(self, password: str, group: str = "olim", **environ):
        return self.client.post(
            "/%s/staff-login" % group, data={"password": password},
            environ_overrides=environ)

    def cookie_from(self, response) -> str:
        header = response.headers["Set-Cookie"]
        return header.split(";", 1)[0].split("=", 1)[1]

    @staticmethod
    def with_cookie(cookie: str) -> dict[str, str]:
        return {"Cookie": "%s=%s" % (STAFF_COOKIE, cookie)}

    def test_login_sets_scoped_cookie_and_relative_redirect(self):
        response = self.login("s3cret", SCRIPT_NAME="/ranking")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "./")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        header = response.headers["Set-Cookie"]
        self.assertTrue(header.startswith(STAFF_COOKIE + "="))
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Lax", header)
        self.assertNotIn("Path=", header)
        self.assertNotIn("Secure", header)

    def test_secure_cookie_over_https(self):
        response = self.client.post(
            "/olim/staff-login", data={"password": "s3cret"},
            headers={"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", response.headers["Set-Cookie"])
        response = self.login("s3cret", **{"wsgi.url_scheme": "https"})
        self.assertIn("Secure", response.headers["Set-Cookie"])

    def test_staff_cookie_grants_access_and_banner(self):
        cookie = self.cookie_from(self.login("s3cret"))
        headers = self.with_cookie(cookie)
        self.assertEqual(
            self.client.get("/olim/contests/", headers=headers).json,
            {"c1": CONTEST})
        page = self.client.get("/olim/", headers=headers)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Vista staff: este ranking está oculto al público",
                      page.get_data(as_text=True))

    def test_banner_is_inserted_right_after_the_body_tag(self):
        with open(os.path.join(self.web_dir, "Ranking.html"), "rb") as f:
            original = f.read()
        after_tag = re.search(rb"<body[^>]*>", original).end()
        cookie = self.cookie_from(self.login("s3cret"))
        response = self.client.get("/olim/", headers=self.with_cookie(cookie))
        page = response.get_data()
        # Only the banner is added, and nothing else changes.
        self.assertTrue(page.startswith(original[:after_tag]))
        self.assertTrue(page.endswith(original[after_tag:]))
        banner = page[after_tag:len(page) - len(original[after_tag:])]
        self.assertIn(b"Vista staff", banner)
        self.assertIn(b'href="staff-logout"', banner)
        self.assertEqual(response.headers["Content-Length"], str(len(page)))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("Last-Modified", response.headers)

    def test_wrong_password(self):
        response = self.login("wrong")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertIn("Contraseña incorrecta.",
                      response.get_data(as_text=True))
        self.sleep.assert_called_once_with(1.0)

    def test_login_is_a_post(self):
        response = self.client.get("/olim/staff-login")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_no_staff_password_means_nobody_logs_in(self):
        self.put_visibility("olim", True, staff_password=None)
        self.assertEqual(self.login("s3cret").status_code, 401)

    def test_empty_password_never_logs_in(self):
        self.put_visibility("olim", True, staff_password=build_password(
            "", "plaintext"))
        self.assertEqual(self.login("").status_code, 401)

    def test_password_change_invalidates_cookie(self):
        cookie = self.cookie_from(self.login("s3cret"))
        self.put_visibility("olim", True,
                            staff_password=build_password("new", "plaintext"))
        response = self.client.get(
            "/olim/contests/", headers=self.with_cookie(cookie))
        self.assertEqual(response.status_code, 403)

    def test_cookie_of_another_group_is_rejected(self):
        self.put_contest("/omips")
        self.put_visibility("omips", True)
        cookie = self.cookie_from(self.login("s3cret"))
        response = self.client.get(
            "/omips/contests/", headers=self.with_cookie(cookie))
        self.assertEqual(response.status_code, 403)

    def test_garbage_cookie_is_not_staff(self):
        # The cookie comes from the client: whatever it holds, the answer
        # is the same as for a request without it, never an error.
        for value in ["", "x" * 64, "caf\xe9", "\xc3\xa9"]:
            with self.subTest(value=value):
                response = self.client.get(
                    "/olim/contests/", headers=self.with_cookie(value))
                self.assertEqual(response.status_code, 403)

    def test_logout_clears_cookie(self):
        response = self.client.get("/olim/staff-logout")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "./")
        header = response.headers["Set-Cookie"]
        self.assertTrue(header.startswith(STAFF_COOKIE + "=;"))
        self.assertIn("Expires=", header)
        self.assertNotIn("Path=", header)

    def test_logout_is_a_get_and_works_for_visible_groups_too(self):
        self.assertEqual(
            self.client.post("/olim/staff-logout").status_code, 403)
        self.put_visibility("olim", False)
        self.assertEqual(
            self.client.get("/olim/staff-logout").status_code, 303)

    def test_non_ascii_password(self):
        self.put_visibility("olim", True, staff_password=build_password(
            "contraseña", "plaintext"))
        self.assertEqual(self.login("contraseña").status_code, 303)

    def test_password_is_read_verbatim(self):
        # AWS stores the password exactly as typed: no stripping.
        self.put_visibility("olim", True, staff_password=build_password(
            " contraseña ", "plaintext"))
        self.assertEqual(self.login("contraseña").status_code, 401)
        self.assertEqual(self.login(" contraseña ").status_code, 303)

    def test_bcrypt_password(self):
        self.put_visibility("olim", True,
                            staff_password=hash_password("s3cret"))
        self.assertEqual(self.login("s3cret").status_code, 303)

    def test_password_is_validated_off_the_hub(self):
        # bcrypt takes a while: run on the hub, it would stop every other
        # request meanwhile, so it has to run in a thread of its own.
        threads = []

        def validate(stored, password):
            threads.append(threading.get_ident())
            return True

        with patch("cmsranking.visibility.validate_password", validate):
            self.assertEqual(self.login("s3cret").status_code, 303)
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], threading.get_ident())

    def test_unusable_hash_is_a_failed_login(self):
        for staff_password in ["md5:abc", "bcrypt:not-a-hash"]:
            with self.subTest(staff_password=staff_password):
                self.put_visibility("olim", True,
                                    staff_password=staff_password)
                response = self.login("abc")
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("Set-Cookie", response.headers)

    def test_password_changed_during_validation_grants_nothing(self):
        # The check runs in a thread, so the group can change meanwhile.
        guard = self.client.application.apps["olim"]

        def validate(stored, password):
            guard.state.update(True, build_password("new", "plaintext"))
            return True

        with patch("cmsranking.visibility.validate_password", validate):
            response = self.login("s3cret")
        self.assertEqual(response.status_code, 303)
        response = self.client.get(
            "/olim/contests/",
            headers=self.with_cookie(self.cookie_from(response)))
        self.assertEqual(response.status_code, 403)

    def test_oversized_login_is_refused(self):
        response = self.login("x" * 1_000_000)
        self.assertEqual(response.status_code, 413)
        self.assertNotIn("Set-Cookie", response.headers)

    def test_visible_group_has_no_banner(self):
        self.put_visibility("olim", False)
        cookie = staff_cookie_value(
            self.client.application.apps["olim"].state.secret, "olim",
            STAFF_HASH)
        page = self.client.get("/olim/", headers=self.with_cookie(cookie))
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("Vista staff", page.get_data(as_text=True))


class TestOpenConnections(unittest.TestCase):

    def test_open_stream_is_cut_when_hidden(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        state_holder = {}

        def streaming_app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])

            def body():
                yield b"event 1\n"
                # The group gets hidden while the stream is open.
                state_holder["guard"].state.update(True, None)
                yield b"event 2\n"
            return body()

        guard = VisibilityGuard(streaming_app, tmp, "olim", USERNAME,
                                PASSWORD, "Scoreboard")
        state_holder["guard"] = guard
        response = Client(guard).get("/events")
        self.assertEqual(response.get_data(), b"event 1\n")

    def get_write_stream(self, guard_class=VisibilityGuard):
        """Get /events from an app that hides its group mid-stream.

        The app sends its data through write(), as the real /events
        handler does, and gives up when write() raises.

        guard_class: the guard to put in front of the app.

        return: the response.

        """
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        state_holder = {}

        def writing_app(environ, start_response):
            write = start_response(
                "200 OK", [("Content-Type", "text/plain")])
            write(b"event 1\n")
            # The group gets hidden while the stream is open.
            state_holder["guard"].state.update(True, None)
            try:
                write(b"event 2\n")
            except Exception:
                pass
            return []

        guard = guard_class(writing_app, tmp, "olim", USERNAME, PASSWORD,
                            "Scoreboard")
        state_holder["guard"] = guard
        return Client(guard).get("/events")

    def test_write_stream_is_cut_when_hidden(self):
        response = self.get_write_stream()
        self.assertEqual(response.get_data(), b"event 1\n")

    def test_write_stream_of_staff_is_not_cut(self):
        class StaffGuard(VisibilityGuard):
            def _is_staff(self, request):
                return True

        response = self.get_write_stream(StaffGuard)
        self.assertEqual(response.get_data(), b"event 1\nevent 2\n")

    def test_app_still_finds_the_server_handler(self):
        # The /events handler reads start_response.__self__ to spot the
        # gevent handler, so the guard must not hide it.
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        seen = []

        class Handler:
            def start_response(self, status, headers, exc_info=None):
                return lambda data: None

        def app(environ, start_response):
            seen.append(getattr(start_response, "__self__", None))
            start_response("204 No Content", [])
            return []

        handler = Handler()
        guard = VisibilityGuard(app, tmp, "olim", USERNAME, PASSWORD,
                                "Scoreboard")
        guard(create_environ("/events"), handler.start_response)
        self.assertEqual(seen, [handler])


class TestRealEventStream(VisibilityTestCase):
    """The real /events handler, behind a real gevent server."""

    def open_stream(self, cookie: str | None = None) -> socket.socket:
        server = WSGIServer(("127.0.0.1", 0), self.client.application,
                            log=None)
        server.start()
        self.addCleanup(server.stop)
        stream = socket.create_connection(("127.0.0.1", server.server_port),
                                          timeout=3)
        self.addCleanup(stream.close)
        cookie_line = b"" if cookie is None else \
            b"Cookie: %s=%s\r\n" % (STAFF_COOKIE.encode(), cookie.encode())
        stream.sendall(b"GET /olim/events HTTP/1.1\r\nHost: rws\r\n"
                       b"Accept: text/event-stream\r\n" + cookie_line
                       + b"\r\n")
        self.assertIn(b"200 OK", stream.recv(65536))
        return stream

    def staff_cookie(self) -> str:
        secret = self.client.application.apps["olim"].state.secret
        return staff_cookie_value(secret, "olim", STAFF_HASH)

    @staticmethod
    def drain(stream: socket.socket) -> tuple[bytes, bool]:
        """Read until the server closes the stream or the timeout expires.

        return: the received bytes and whether the server closed it.

        """
        received = b""
        try:
            while True:
                chunk = stream.recv(65536)
                if not chunk:
                    return received, True
                received += chunk
        except socket.timeout:
            return received, False

    def put_second_contest(self):
        return self.client.put(
            "/olim/contests/c2", data=json.dumps(CONTEST),
            content_type="application/json", headers=AUTH)

    def test_stream_is_closed_without_data_when_group_gets_hidden(self):
        self.put_contest("/olim")
        stream = self.open_stream()
        self.put_visibility("olim", True)
        self.put_second_contest()
        received, closed = self.drain(stream)
        self.assertNotIn(b"c2", received)
        self.assertTrue(closed)

    def test_stream_of_a_visible_group_keeps_delivering(self):
        self.put_contest("/olim")
        stream = self.open_stream()
        self.put_second_contest()
        self.assertIn(b"data:create c2", stream.recv(65536))

    def test_staff_stream_of_a_hidden_group_delivers(self):
        self.put_contest("/olim")
        self.put_visibility("olim", True)
        stream = self.open_stream(cookie=self.staff_cookie())
        self.put_second_contest()
        self.assertIn(b"data:create c2", stream.recv(65536))

    def test_staff_stream_survives_hiding(self):
        self.put_contest("/olim")
        self.put_visibility("olim", False)
        stream = self.open_stream(cookie=self.staff_cookie())
        self.put_visibility("olim", True)
        self.put_second_contest()
        self.assertIn(b"data:create c2", stream.recv(65536))

    def test_staff_stream_is_closed_when_the_password_changes(self):
        self.put_contest("/olim")
        self.put_visibility("olim", True)
        stream = self.open_stream(cookie=self.staff_cookie())
        self.put_visibility("olim", True,
                            staff_password=build_password("new", "plaintext"))
        self.put_second_contest()
        received, closed = self.drain(stream)
        self.assertNotIn(b"c2", received)
        self.assertTrue(closed)


class TestVisibilityState(unittest.TestCase):

    def test_update_is_persisted_with_a_stable_secret(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        state = VisibilityState(tmp)
        state.update(True, STAFF_HASH)
        secret = state.secret
        state.update(False, None)
        reloaded = VisibilityState(tmp)
        self.assertEqual((reloaded.hidden, reloaded.staff_password,
                          reloaded.secret), (False, None, secret))


if __name__ == "__main__":
    unittest.main()
