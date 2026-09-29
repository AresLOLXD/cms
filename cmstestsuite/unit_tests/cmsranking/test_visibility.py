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

import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from base64 import b64encode
from importlib.resources import files
from unittest.mock import patch

import gevent
from gevent import socket
from gevent.monkey import get_original
from gevent.pywsgi import WSGIServer
from werkzeug.test import Client, create_environ

from cmscommon.crypto import build_password, hash_password
from cmsranking.Config import Config
from cmsranking.RankingWebServer import NamespaceDispatcher, \
    build_ranking_app
from cmsranking.visibility import FROZEN_ROUTES, HIDDEN_SINCE_ALWAYS, \
    STAFF_COOKIE, VISIBILITY_FILE, VisibilityGuard, VisibilitySettings, \
    VisibilityState, parse_settings, public_frozen_banner, \
    staff_cookie_value


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
              "flags/t1", "Ranking.html", "Ranking.js", "img/favicon.ico"]


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

    def test_put_rejects_missing_keys(self):
        self.put_contest("/olim")
        for body in [{}, {"hidden": True}, {"staff_password": None}]:
            response = self.client.put(
                "/olim/visibility", data=json.dumps(body),
                content_type="application/json", headers=AUTH)
            self.assertEqual(response.status_code, 400, msg=body)

    def test_put_creates_namespace(self):
        self.assertEqual(self.put_visibility("omips", True).status_code,
                         204)
        self.assertTrue(os.path.isfile(os.path.join(
            self.lib_dir, "groups", "omips", VISIBILITY_FILE)))

    def test_namespace_created_hidden_is_forbidden(self):
        self.put_visibility("omips", True)
        for path in DATA_PATHS:
            response = self.client.get("/omips/" + path)
            self.assertEqual(response.status_code, 403, msg=path)
            self.assertEqual(response.headers["Cache-Control"], "no-store",
                             msg=path)

    def test_only_a_change_is_logged_at_info(self):
        # ProxyService sends the settings of every group at each sweep:
        # the same settings sent again must not fill the log.
        other_hash = build_password("0ther", "plaintext")
        with self.assertLogs("cmsranking.visibility", "DEBUG") as logs:
            self.put_visibility("olim", True)
            self.put_visibility("olim", True)
            self.put_visibility("olim", True, staff_password=other_hash)
            self.put_visibility("olim", False, staff_password=other_hash)
            self.put_visibility("olim", False, staff_password=other_hash)
        levels = [record.levelname for record in logs.records
                  if record.getMessage().startswith("Ranking group olim")]
        self.assertEqual(levels, ["INFO", "DEBUG", "INFO", "INFO", "DEBUG"])

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
        self.assertIn("<title>Ranking oculto</title>", text)
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

    def test_proxy_deletes_still_accepted(self):
        self.assertEqual(
            self.client.delete("/olim/contests/missing",
                               headers=AUTH).status_code, 404)
        self.assertEqual(
            self.client.delete("/olim/contests/c1",
                               headers=AUTH).status_code, 204)

    def test_anonymous_writes_get_a_uniform_401(self):
        # The guard answers before the store handlers, which look the key
        # up before checking the credentials: an anonymous DELETE would
        # tell which keys exist, and a PUT would reach the static files.
        wrong = {"Authorization": "Basic " + b64encode(
            (USERNAME + ":wrong").encode()).decode()}
        for method, path, headers in [
                ("PUT", "contests/c2", {}), ("PUT", "contests/", {}),
                ("PUT", "Ranking.js", {}), ("DELETE", "contests/c1", {}),
                ("DELETE", "contests/missing", {}),
                ("DELETE", "contests/", {}),
                ("DELETE", "contests/missing", wrong)]:
            with self.subTest(method=method, path=path):
                response = self.client.open(
                    "/olim/" + path, method=method, headers=headers,
                    data=json.dumps(CONTEST),
                    content_type="application/json")
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["Cache-Control"],
                                 "no-store")
                self.assertEqual(response.headers["WWW-Authenticate"],
                                 'Basic realm="Scoreboard"')
        existing = self.client.delete("/olim/contests/c1")
        missing = self.client.delete("/olim/contests/missing")
        self.assertEqual(existing.get_data(), missing.get_data())
        # None of them had any effect.
        self.put_visibility("olim", False)
        self.assertEqual(self.client.get("/olim/contests/").json,
                         {"c1": CONTEST})

    def test_anonymous_write_is_logged(self):
        with self.assertLogs("cmsranking.visibility", "WARNING"):
            self.client.delete("/olim/contests/c1")

    def test_proxy_credentials_do_not_open_reads(self):
        for path in DATA_PATHS:
            response = self.client.get("/olim/" + path, headers=AUTH)
            self.assertEqual(response.status_code, 403, msg=path)

    def test_visibility_is_only_special_for_put(self):
        response = self.client.get("/olim/visibility")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.post("/olim/visibility").status_code,
                         403)
        self.assertEqual(self.client.delete("/olim/visibility").status_code,
                         401)
        # Once visible, the app answers as for any unknown path.
        self.put_visibility("olim", False)
        self.assertEqual(self.client.get("/olim/visibility").status_code,
                         404)

    def test_odd_methods_and_paths_stay_closed(self):
        paths = ["/events", "//events", "/events/", "/events?x=1",
                 "/%65vents"]
        for method in ["HEAD", "OPTIONS", "POST", "PATCH", "PUT", "DELETE"]:
            for path in paths:
                with self.subTest(method=method, path=path):
                    response = self.client.open("/olim" + path,
                                                method=method)
                    self.assertEqual(
                        response.status_code,
                        401 if method in ["PUT", "DELETE"] else 403)
                    self.assertEqual(response.headers["Cache-Control"],
                                     "no-store")

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
        patcher = patch("cmsranking.visibility.gevent.sleep")
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

    def test_cookie_is_the_documented_hmac(self):
        # Spelled out here, not computed with staff_cookie_value, so a
        # change to what it signs (the group, the separator) is noticed.
        secret = self.client.application.apps["olim"].state.secret
        expected = hmac.new(bytes.fromhex(secret),
                            b"olim\n" + STAFF_HASH.encode(),
                            hashlib.sha256).hexdigest()
        self.assertEqual(self.cookie_from(self.login("s3cret")), expected)

    def test_staff_responses_of_a_hidden_group_are_not_cacheable(self):
        # A shared cache that ignores cookies must not keep what only the
        # staff may see, whatever the app says about caching it.
        headers = self.with_cookie(self.cookie_from(self.login("s3cret")))
        headers["Accept"] = "application/json"
        for path in DATA_PATHS:
            if path == "events":
                continue
            with self.subTest(path=path):
                response = self.client.get("/olim/" + path, headers=headers)
                self.assertNotEqual(response.status_code, 403)
                self.assertEqual(response.headers["Cache-Control"],
                                 "private, no-store")

    def test_staff_index_of_a_hidden_group_is_private(self):
        headers = self.with_cookie(self.cookie_from(self.login("s3cret")))
        for path in ("", "Ranking.html"):
            with self.subTest(path=path):
                response = self.client.get("/olim/" + path, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Cache-Control"],
                                 "private, no-store")

    def test_visible_group_keeps_the_caching_of_the_app(self):
        self.put_visibility("olim", False)
        cookie = staff_cookie_value(
            self.client.application.apps["olim"].state.secret, "olim",
            STAFF_HASH)
        for headers in [{}, self.with_cookie(cookie)]:
            response = self.client.get("/olim/Ranking.js", headers=headers)
            self.assertEqual(response.headers["Cache-Control"],
                             "max-age=43200, public")

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

    def staff_page(self):
        """Get / as staff, and cut the banner out of it.

        return: the response and the banner.

        """
        with open(os.path.join(self.web_dir, "Ranking.html"), "rb") as f:
            original = f.read()
        after_tag = re.search(rb"<body[^>]*>", original).end()
        cookie = self.cookie_from(self.login("s3cret"))
        response = self.client.get("/olim/", headers=self.with_cookie(cookie))
        page = response.get_data()
        # Only the banner is added, and nothing else changes.
        self.assertTrue(page.startswith(original[:after_tag]))
        self.assertTrue(page.endswith(original[after_tag:]))
        return response, page[after_tag:len(page) - len(original[after_tag:])]

    def test_banner_stays_above_the_scoreboard(self):
        # The upper panel of the scoreboard is positioned at the top of
        # the page, over anything that is in the flow there. The banner
        # must be a bar of its own that no panel covers and that leaves
        # the panel alone.
        with open(os.path.join(self.web_dir, "Ranking.css")) as f:
            highest = max(int(z) for z in
                          re.findall(r"z-index:\s*(\d+)", f.read()))
        banner = self.staff_page()[1].decode("utf-8")
        style = re.search(r'style="([^"]*)"', banner).group(1)
        self.assertRegex(style, r"position:\s*fixed")
        self.assertRegex(style, r"(^|;)\s*bottom:\s*0")
        self.assertNotRegex(style, r"(^|;)\s*top:")
        self.assertGreater(
            int(re.search(r"z-index:\s*(\d+)", style).group(1)), highest)

    def banner_parts(self) -> tuple[str, str]:
        """Get the style rules and the style of the bar of the banner."""
        banner = self.staff_page()[1].decode("utf-8")
        rules = re.search(r"<style>(.*?)</style>", banner, re.DOTALL)
        self.assertIsNotNone(rules, "the banner reserves no room")
        return rules.group(1), re.search(r'<div style="([^"]*)"',
                                         banner).group(1)

    def test_scrolling_areas_leave_room_for_the_banner(self):
        # The scoreboard scrolls in areas that Ranking.css anchors to the
        # bottom of the page, where the fixed banner sits: it would cover
        # the last row, the arrow of the scrollbar and the link of the
        # side panel. Their room comes from the same property as the
        # height of the banner, so that the two always match.
        with open(os.path.join(self.web_dir, "Ranking.css")) as f:
            css = f.read()
        rules, style = self.banner_parts()
        variable = re.search(r"height:\s*var\((--[\w-]+)\)", style).group(1)
        for selector in ["#InnerFrame", "#SidePanel", "#UserDetail_bg"]:
            with self.subTest(selector=selector):
                bottom = re.search(
                    r"%s\s*\{[^}]*?\bbottom:\s*([^;]+);" % selector,
                    css).group(1)
                room = "var(%s)" % variable if bottom == "0" else \
                    "calc(%s + var(%s))" % (bottom, variable)
                self.assertRegex(rules, r"%s[^{}]*\{[^}]*\bbottom:\s*%s\s*[;}]"
                                 % (selector, re.escape(room)))

    def test_banner_height_fits_its_text(self):
        # One line and its padding, or two lines on a narrow screen.
        rules, style = self.banner_parts()

        def rem(pattern: str, text: str) -> float:
            return float(re.search(pattern + r"([\d.]+)rem", text).group(1))

        line = rem(r"font:[^;]*/", style)
        padding = rem(r"padding:\s*", style)
        wide = rem(r":root\s*\{--[\w-]+:\s*", rules)
        narrow = rem(r"@media[^{]*\{\s*:root\s*\{--[\w-]+:\s*", rules)
        self.assertAlmostEqual(wide, line + 2 * padding)
        self.assertGreaterEqual(narrow, 2 * line + 2 * padding)

    def test_banner_is_inserted_right_after_the_body_tag(self):
        response, banner = self.staff_page()
        page = response.get_data()
        self.assertIn(b"Vista staff", banner)
        self.assertIn(b'href="staff-logout"', banner)
        self.assertEqual(response.headers["Content-Length"], str(len(page)))
        self.assertEqual(response.headers["Cache-Control"],
                         "private, no-store")
        self.assertNotIn("Last-Modified", response.headers)

    def test_wrong_password(self):
        response = self.login("wrong")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertIn("Contraseña incorrecta.",
                      response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
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
        self.assertEqual(response.headers["Cache-Control"], "no-store")
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

    def test_login_does_not_use_the_shared_threadpool(self):
        # Anonymous logins must not be able to fill the pool that the hub
        # shares with everything else, nor wait in a queue behind it.
        allocate_lock = get_original("_thread", "allocate_lock")
        gate = allocate_lock()
        gate.acquire()

        def blocker():
            if gate.acquire(timeout=5):
                gate.release()

        # Released in a chain: each blocker lets the next one go.
        self.addCleanup(gate.release)
        shared_pool = gevent.get_hub().threadpool
        for _ in range(shared_pool.maxsize):
            shared_pool.spawn(blocker)
        with gevent.Timeout(2):
            self.assertEqual(self.login("s3cret").status_code, 303)

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
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("Set-Cookie", response.headers)

    def test_login_body_limit_is_far_below_the_form_default(self):
        # A login form is tiny, so a body of a few kB is already too big.
        self.assertEqual(self.login("x" * 3000).status_code, 401)
        self.assertEqual(self.login("x" * 5000).status_code, 413)

    def test_visible_group_has_no_banner(self):
        self.put_visibility("olim", False)
        cookie = staff_cookie_value(
            self.client.application.apps["olim"].state.secret, "olim",
            STAFF_HASH)
        page = self.client.get("/olim/", headers=self.with_cookie(cookie))
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("Vista staff", page.get_data(as_text=True))


class TestIndexPage(VisibilityTestCase):
    """A browser must ask again for the index page of a group.

    The page has a Last-Modified and no Cache-Control, so a browser keeps
    it for a while without asking. A visitor who loaded it while the group
    was visible would then get the scoreboard, and none of the data, from
    its cache once the group is hidden, instead of the notice.

    """

    def setUp(self):
        super().setUp()
        self.put_contest("/olim")
        self.put_visibility("olim", False)

    def staff_cookie(self) -> dict[str, str]:
        secret = self.client.application.apps["olim"].state.secret
        return {"Cookie": "%s=%s" % (
            STAFF_COOKIE, staff_cookie_value(secret, "olim", STAFF_HASH))}

    def test_visible_index_is_revalidated(self):
        for method in ["GET", "HEAD"]:
            for headers in [{}, self.staff_cookie()]:
                with self.subTest(method=method, staff=bool(headers)):
                    response = self.client.open(
                        "/olim/", method=method, headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["Cache-Control"],
                                     "no-cache")

    def test_index_by_its_file_name_is_revalidated(self):
        # The same page, that a bookmark can name.
        for method in ["GET", "HEAD"]:
            for headers in [{}, self.staff_cookie()]:
                with self.subTest(method=method, staff=bool(headers)):
                    response = self.client.open(
                        "/olim/Ranking.html", method=method,
                        headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["Cache-Control"],
                                     "no-cache")

    def test_visible_index_is_the_page_as_it_is(self):
        with open(os.path.join(self.web_dir, "Ranking.html"), "rb") as f:
            original = f.read()
        for headers in [{}, self.staff_cookie()]:
            with self.subTest(staff=bool(headers)):
                response = self.client.get("/olim/", headers=headers)
                self.assertEqual(response.get_data(), original)

    def test_returning_visitor_gets_the_notice(self):
        response = self.client.get("/olim/")
        self.assertEqual(response.headers["Cache-Control"], "no-cache")
        self.assertNotIn("staff-login", response.get_data(as_text=True))
        self.put_visibility("olim", True)
        response = self.client.get("/olim/")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn('action="staff-login"', response.get_data(as_text=True))

    def test_other_pages_keep_their_caching(self):
        response = self.client.get("/olim/Ranking.js")
        self.assertEqual(response.headers["Cache-Control"],
                         "max-age=43200, public")

    def test_index_of_the_root_ranking_is_unchanged(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Cache-Control", response.headers)
        self.assertIn("Last-Modified", response.headers)


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

    def test_open_stream_is_cut_by_the_clock_alone(self):
        # The group gets hidden by its schedule, with no PUT involved.
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)

        def streaming_app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])

            def body():
                yield b"event 1\n"
                clock.return_value = 100
                yield b"event 2\n"
            return body()

        guard = VisibilityGuard(streaming_app, tmp, "olim", USERNAME,
                                PASSWORD, "Scoreboard")
        guard.state.update_settings(VisibilitySettings(hide_at=100))
        with patch("cmsranking.visibility.time.time",
                   return_value=99) as clock:
            response = Client(guard).get("/events")
            # Read inside the block: after it the real clock is far past
            # 100, and would cut the stream whatever the app did.
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

    def test_staff_of_a_hidden_group_get_private_no_store(self):
        # The header is set on the way out, so the /events handler must
        # still find the server handler where it looks for it.
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        seen = []

        class Handler:
            headers = None

            def start_response(self, status, headers, exc_info=None):
                self.headers = headers
                return lambda data: None

        class StaffGuard(VisibilityGuard):
            def _is_staff(self, request):
                return True

        def app(environ, start_response):
            seen.append(getattr(start_response, "__self__", None))
            start_response("200 OK", [("Cache-Control", "max-age=60"),
                                      ("X-Other", "kept")])
            return []

        handler = Handler()
        guard = StaffGuard(app, tmp, "olim", USERNAME, PASSWORD,
                           "Scoreboard")
        guard.state.update(True, None)
        guard(create_environ("/config"), handler.start_response)
        self.assertEqual(seen, [handler])
        self.assertEqual(handler.headers, [
            ("X-Other", "kept"), ("Cache-Control", "private, no-store")])

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


class TestLoginBodyLimit(VisibilityTestCase):
    """The login form is tiny: a bigger body is refused, however sent.

    Behind a real gevent server, which streams chunked bodies to the app
    and so leaves it to the app to stop reading them.

    """

    BOUNDARY = b"XXboundaryXX"
    FORM = b"Content-Type: application/x-www-form-urlencoded\r\n"
    MULTIPART = b"Content-Type: multipart/form-data; boundary=%s\r\n" \
        % BOUNDARY

    def setUp(self):
        super().setUp()
        self.put_contest("/olim")
        self.put_visibility("olim", True)
        server = WSGIServer(("127.0.0.1", 0), self.client.application,
                            log=None)
        server.start()
        self.addCleanup(server.stop)
        self.port = server.server_port

    def post(self, content_type: bytes, body: bytes,
             chunked: bool) -> socket.socket:
        """Send a login request, with the body chunked or not."""
        if chunked:
            head = b"Transfer-Encoding: chunked\r\n"
            body = b"".join(
                b"%x\r\n%s\r\n" % (len(body[i:i + 8192]), body[i:i + 8192])
                for i in range(0, len(body), 8192)) + b"0\r\n\r\n"
        else:
            head = b"Content-Length: %d\r\n" % len(body)
        stream = socket.create_connection(("127.0.0.1", self.port),
                                          timeout=3)
        self.addCleanup(stream.close)
        stream.sendall(b"POST /olim/staff-login HTTP/1.1\r\nHost: rws\r\n"
                       + content_type + head + b"\r\n" + body)
        return stream

    def multipart_with_file(self, size: int) -> bytes:
        return (b"--%s\r\nContent-Disposition: form-data; name=\"upload\"; "
                b"filename=\"a.bin\"\r\nContent-Type: "
                b"application/octet-stream\r\n\r\n%s\r\n--%s--\r\n"
                % (self.BOUNDARY, b"x" * size, self.BOUNDARY))

    def assert_refused(self, stream: socket.socket):
        received, closed = TestRealEventStream.drain(stream)
        self.assertTrue(received.startswith(b"HTTP/1.1 413"), received[:60])
        self.assertIn(b"Cache-Control: no-store", received)
        self.assertNotIn(b"Set-Cookie", received)
        # The rest of the body is not read by the app: no keep-alive.
        self.assertTrue(closed)

    def test_urlencoded_body_over_the_limit(self):
        # Both sides of the 500 kB that werkzeug allows a form by default,
        # and only when the length is declared.
        for size in [65536, 1_000_000]:
            for chunked in [False, True]:
                with self.subTest(size=size, chunked=chunked):
                    self.assert_refused(self.post(
                        self.FORM, b"password=" + b"x" * size, chunked))

    def test_multipart_file_part_over_the_limit(self):
        body = self.multipart_with_file(65536)
        for chunked in [False, True]:
            with self.subTest(chunked=chunked):
                self.assert_refused(self.post(self.MULTIPART, body, chunked))

    def test_normal_login_still_works(self):
        for chunked in [False, True]:
            with self.subTest(chunked=chunked):
                stream = self.post(self.FORM, b"password=s3cret", chunked)
                received = stream.recv(65536)
                self.assertTrue(received.startswith(b"HTTP/1.1 303"),
                                received[:60])
                self.assertIn(b"Set-Cookie: " + STAFF_COOKIE.encode(),
                              received)


class TestCutBody(unittest.TestCase):
    """A body cut short must not leave the browser waiting for the rest."""

    def test_connection_is_closed_when_the_body_is_cut(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)

        def app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain"),
                                      ("Content-Length", "12")])

            def body():
                yield b"first\n"
                # The group gets hidden while the body is being sent.
                guard.state.update(True, None)
                yield b"second"
            return body()

        guard = VisibilityGuard(app, tmp, "olim", USERNAME, PASSWORD,
                                "Scoreboard")
        server = WSGIServer(("127.0.0.1", 0), guard, log=None)
        server.start()
        self.addCleanup(server.stop)
        stream = socket.create_connection(("127.0.0.1", server.server_port),
                                          timeout=3)
        self.addCleanup(stream.close)
        stream.sendall(b"GET /file HTTP/1.1\r\nHost: rws\r\n\r\n")
        received, closed = TestRealEventStream.drain(stream)
        self.assertIn(b"first\n", received)
        self.assertNotIn(b"second", received)
        self.assertTrue(closed)


class TestPublicFrozenBanner(unittest.TestCase):

    def set_time_zone(self, value: str):
        """Make the process live in a time zone, until the test ends.

        value: a POSIX TZ string, e.g. CST6.

        """
        saved = os.environ.get("TZ")

        def restore():
            if saved is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved
            time.tzset()

        self.addCleanup(restore)
        os.environ["TZ"] = value
        time.tzset()

    def test_text_and_link(self):
        # 2023-11-14 22:13:20 UTC.
        for zone, expected in [("CST6", "16:13 (CST)"),
                               ("EET-2", "00:13 (EET)")]:
            with self.subTest(zone=zone):
                self.set_time_zone(zone)
                banner = public_frozen_banner(1_700_000_000).decode("utf-8")
                self.assertIn("Ranking congelado desde las %s" % expected,
                              banner)
                self.assertIn('<a style="color:#fff" href="staff-login">'
                              'Acceso staff</a>', banner)


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


class TestUnreadableState(unittest.TestCase):
    """A state file that is there but cannot be used hides the group."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        # rmtree cannot enter a directory that has lost its permissions.
        self.addCleanup(os.chmod, self.tmp, 0o700)
        self.path = os.path.join(self.tmp, VISIBILITY_FILE)

    def assert_fails_closed(self):
        with self.assertLogs("cmsranking.visibility", "ERROR"):
            state = VisibilityState(self.tmp)
        self.assertEqual((state.hidden, state.staff_password), (True, None))

    def test_no_file_means_visible(self):
        state = VisibilityState(self.tmp)
        self.assertEqual((state.hidden, state.staff_password),
                         (False, None))

    def test_directory_instead_of_file(self):
        os.mkdir(self.path)
        self.assert_fails_closed()

    def test_link_to_itself(self):
        # os.path.exists() says False for it, although the file is there.
        os.symlink(VISIBILITY_FILE, self.path)
        self.assert_fails_closed()

    @unittest.skipIf(os.geteuid() == 0, "root reads any file")
    def test_file_without_read_permission(self):
        with open(self.path, "w") as f:
            json.dump({"hidden": False, "staff_password": None,
                       "secret": "00"}, f)
        os.chmod(self.path, 0)
        self.assert_fails_closed()

    @unittest.skipIf(os.geteuid() == 0, "root enters any directory")
    def test_directory_without_search_permission(self):
        with open(self.path, "w") as f:
            json.dump({"hidden": False, "staff_password": None,
                       "secret": "00"}, f)
        os.chmod(self.tmp, 0)
        self.assert_fails_closed()

    def test_json_that_is_not_an_object(self):
        # A number or a string has no secret to pop.
        for content in ["3", '"hidden"']:
            with self.subTest(content=content):
                with open(self.path, "w") as f:
                    f.write(content)
                self.assert_fails_closed()

    def test_new_format_with_bad_times(self):
        times = {"hide_at": None, "show_at": None, "freeze_at": None,
                 "unfreeze_at": None, "staff_password": None,
                 "secret": "00"}
        for bad in [{"hide_at": True}, {"hide_at": 20, "show_at": 20}]:
            with self.subTest(bad=bad):
                with open(self.path, "w") as f:
                    json.dump(dict(times, **bad), f)
                self.assert_fails_closed()


class TestVisibilitySettings(unittest.TestCase):

    def test_new_format(self):
        s = parse_settings({"hide_at": 10, "show_at": 20, "freeze_at": None,
                            "unfreeze_at": None, "staff_password": STAFF_HASH})
        self.assertEqual((s.hide_at, s.show_at, s.staff_password),
                         (10, 20, STAFF_HASH))
        self.assertTrue(s.hidden(10))
        self.assertFalse(s.hidden(20))

    def test_old_format(self):
        self.assertEqual(
            parse_settings({"hidden": True, "staff_password": None}),
            HIDDEN_SINCE_ALWAYS)
        self.assertEqual(
            parse_settings({"hidden": False, "staff_password": None}),
            VisibilitySettings())

    def test_hidden_wins_over_frozen(self):
        s = VisibilitySettings(hide_at=10, freeze_at=5)
        self.assertTrue(s.frozen(7))
        self.assertFalse(s.frozen(12))
        self.assertTrue(s.hidden(12))

    def test_rejects(self):
        for body in [
                {"hide_at": True, "show_at": None, "freeze_at": None,
                 "unfreeze_at": None, "staff_password": None},
                {"hide_at": 1.5, "show_at": None, "freeze_at": None,
                 "unfreeze_at": None, "staff_password": None},
                {"hide_at": 20, "show_at": 20, "freeze_at": None,
                 "unfreeze_at": None, "staff_password": None},
                {"hide_at": None, "show_at": None, "freeze_at": 30,
                 "unfreeze_at": 10, "staff_password": None},
                {"hidden": True, "hide_at": 3, "staff_password": None},
                {"hide_at": None, "staff_password": None},
                []]:
            with self.assertRaises(ValueError, msg=body):
                parse_settings(body)

    def test_last_change(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        state = VisibilityState(tmp)
        state.update_settings(VisibilitySettings(freeze_at=100,
                                                 unfreeze_at=200))
        state.changed_at = 50
        self.assertEqual(state.last_change(99), 50)
        self.assertEqual(state.last_change(150), 100)
        self.assertEqual(state.last_change(250), 200)

    def test_only_a_new_window_counts_as_a_change_of_the_view(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        state = VisibilityState(tmp)
        state.changed_at = 50
        # Only the staff password differs: the public sees the same.
        self.assertTrue(state.update_settings(
            VisibilitySettings(staff_password=STAFF_HASH)))
        self.assertEqual(state.changed_at, 50)
        self.assertFalse(state.update_settings(
            VisibilitySettings(staff_password=STAFF_HASH)))
        self.assertEqual(state.changed_at, 50)
        with patch("cmsranking.visibility.time.time", return_value=80):
            self.assertTrue(state.update_settings(
                VisibilitySettings(hide_at=100, staff_password=STAFF_HASH)))
        self.assertEqual(state.changed_at, 80)


class TestScheduledHiding(VisibilityTestCase):

    def put_settings(self, group: str, **times):
        body = {"hide_at": None, "show_at": None, "freeze_at": None,
                "unfreeze_at": None, "staff_password": STAFF_HASH}
        body.update(times)
        return self.client.put("/%s/visibility" % group,
                               data=json.dumps(body),
                               content_type="application/json", headers=AUTH)

    def test_hidden_only_inside_the_window(self):
        self.put_contest("/olim")
        self.assertEqual(self.put_settings("olim", hide_at=100,
                                           show_at=200).status_code, 204)
        with patch("cmsranking.visibility.time.time", return_value=99):
            self.assertEqual(
                self.client.get("/olim/contests/").status_code, 200)
        with patch("cmsranking.visibility.time.time", return_value=100):
            self.assertEqual(
                self.client.get("/olim/contests/").status_code, 403)
        with patch("cmsranking.visibility.time.time", return_value=200):
            self.assertEqual(
                self.client.get("/olim/contests/").status_code, 200)

    def test_settings_survive_a_restart(self):
        self.put_contest("/olim")
        self.put_settings("olim", hide_at=100, show_at=200)
        self.client = self.make_client()
        with patch("cmsranking.visibility.time.time", return_value=150):
            self.assertEqual(
                self.client.get("/olim/contests/").status_code, 403)

    def test_old_file_is_read(self):
        group_dir = os.path.join(self.lib_dir, "groups", "olim")
        os.makedirs(group_dir)
        with open(os.path.join(group_dir, VISIBILITY_FILE), "w") as f:
            json.dump({"hidden": True, "staff_password": None,
                       "secret": "ab" * 32}, f)
        state = VisibilityState(group_dir)
        self.assertEqual(state.settings, HIDDEN_SINCE_ALWAYS)
        # The staff cookies signed with the secret survive the upgrade.
        self.assertEqual(state.secret, "ab" * 32)


class TestFrozenPublicView(VisibilityTestCase):

    TASK = {"name": "T", "short_name": "t", "contest": "c1", "order": 0,
            "max_score": 100.0, "extra_headers": [], "score_precision": 0,
            "score_mode": "max"}
    LIVE = {"early": {"t": 40.0}, "late": {"t": 90.0}}
    # The handlers of the scores, the history and the submissions refuse a
    # client that does not accept JSON, as the scoreboard's ajax calls do.
    JSON = {"Accept": "application/json"}

    def setUp(self):
        super().setUp()
        self.put_contest("/olim")
        for path, data in [
                ("tasks/", {"t": self.TASK}),
                ("users/", {
                    "early": {"f_name": "E", "l_name": "E", "team": None},
                    "late": {"f_name": "L", "l_name": "L", "team": None}}),
                ("submissions/", {
                    "s1": {"user": "early", "task": "t", "time": 100},
                    "s2": {"user": "late", "task": "t", "time": 300}}),
                ("subchanges/", {
                    "c1": {"submission": "s1", "time": 100, "score": 40.0},
                    "c2": {"submission": "s2", "time": 300, "score": 90.0}})]:
            self.assertEqual(self.client.put(
                "/olim/" + path, data=json.dumps(data),
                content_type="application/json",
                headers=AUTH).status_code, 204)
        self.client.put("/olim/visibility", data=json.dumps({
            "hide_at": None, "show_at": None, "freeze_at": 200,
            "unfreeze_at": 400, "staff_password": STAFF_HASH}),
            content_type="application/json", headers=AUTH)

    def at(self, now):
        return patch("cmsranking.visibility.time.time", return_value=now)

    def test_public_scores_are_the_snapshot(self):
        with self.at(350):
            response = self.client.get("/olim/scores", headers=self.JSON)
        self.assertEqual(response.json, {"early": {"t": 40.0}})
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_public_history_and_sublist_are_cut(self):
        with self.at(350):
            history = self.client.get("/olim/history", headers=self.JSON)
            self.assertEqual([h[2] for h in history.json], [100])
            sublist = self.client.get("/olim/sublist/late", headers=self.JSON)
            self.assertEqual(sublist.json, [])

    def test_raw_stores_are_forbidden(self):
        with self.at(350):
            for path in ("submissions/", "subchanges/", "submissions/s2"):
                self.assertEqual(
                    self.client.get("/olim/" + path).status_code, 403, path)

    def test_unknown_paths_are_forbidden(self):
        # The filter is an allow-list: what is not classified is refused.
        with self.at(350):
            for method, path in [("GET", "visibility"), ("GET", "whatever"),
                                 ("GET", "whatever/deeper"),
                                 ("HEAD", "whatever"), ("POST", "whatever"),
                                 ("GET", "ranking.html"),
                                 ("GET", "Ranking.html.bak")]:
                with self.subTest(method=method, path=path):
                    response = self.client.open("/olim/" + path,
                                                method=method)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.headers["Cache-Control"],
                                     "no-store")

    def test_static_files_pass(self):
        names = os.listdir(self.web_dir) + ["img/favicon.ico"]
        with self.at(350):
            for name in names:
                if os.path.isdir(os.path.join(self.web_dir, name)):
                    continue
                with self.subTest(name=name):
                    self.assertEqual(
                        self.client.get("/olim/" + name).status_code, 200)

    def test_other_data_passes(self):
        with self.at(350):
            for path in ("contests/", "tasks/", "users/", "config"):
                self.assertEqual(
                    self.client.get("/olim/" + path).status_code, 200, path)

    def test_staff_see_it_live(self):
        secret = self.client.application.apps["olim"].state.secret
        cookie = staff_cookie_value(secret, "olim", STAFF_HASH)
        with self.at(350):
            response = self.client.get("/olim/scores", headers=dict(
                self.JSON, Cookie="%s=%s" % (STAFF_COOKIE, cookie)))
        self.assertEqual(response.json, self.LIVE)

    def test_unfrozen_at_the_exact_end(self):
        with self.at(400):
            self.assertEqual(
                self.client.get("/olim/scores", headers=self.JSON).json,
                self.LIVE)

    def test_banners_and_staff_login_page(self):
        with self.at(350):
            page = self.client.get("/olim/").get_data(as_text=True)
            login = self.client.get("/olim/staff-login")
        self.assertIn("Ranking congelado desde las", page)
        self.assertIn('href="staff-login"', page)
        self.assertEqual(login.status_code, 200)
        self.assertIn('name="password"', login.get_data(as_text=True))
        # The tab must not say that the ranking is hidden.
        self.assertIn("<title>Acceso staff</title>",
                      login.get_data(as_text=True))
        self.assertNotIn("Ranking oculto", login.get_data(as_text=True))

    def test_staff_login_works_while_frozen(self):
        with self.at(350):
            response = self.client.post("/olim/staff-login",
                                        data={"password": "s3cret"})
        self.assertEqual(response.status_code, 303)

    def staff_headers(self) -> dict[str, str]:
        secret = self.client.application.apps["olim"].state.secret
        cookie = staff_cookie_value(secret, "olim", STAFF_HASH)
        return {"Cookie": "%s=%s" % (STAFF_COOKIE, cookie)}

    def test_staff_get_the_frozen_banner(self):
        with self.at(350):
            page = self.client.get(
                "/olim/", headers=self.staff_headers()).get_data(as_text=True)
        self.assertIn("Vista staff: ranking congelado", page)
        self.assertNotIn("Ranking congelado desde las", page)

    def test_staff_pages_of_a_frozen_group_are_private(self):
        # A shared cache that ignores cookies must not keep the live view,
        # nor the page with the staff banner.
        with self.at(350):
            for path in ("", "Ranking.html", "config", "scores"):
                with self.subTest(path=path):
                    response = self.client.get(
                        "/olim/" + path, headers=dict(
                            self.JSON, **self.staff_headers()))
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["Cache-Control"],
                                     "private, no-store")

    def test_public_index_of_a_frozen_group_is_revalidated(self):
        with self.at(350):
            for path in ("", "Ranking.html"):
                with self.subTest(path=path):
                    response = self.client.get("/olim/" + path)
                    self.assertEqual(response.headers["Cache-Control"],
                                     "no-cache")

    def test_wrong_password_while_frozen(self):
        with self.at(350), patch("cmsranking.visibility.gevent.sleep"):
            response = self.client.post("/olim/staff-login",
                                        data={"password": "wrong"})
        self.assertEqual(response.status_code, 401)
        text = response.get_data(as_text=True)
        self.assertIn("Acceso del staff al ranking en vivo.", text)
        self.assertIn("<title>Acceso staff</title>", text)
        self.assertNotIn("Este ranking está oculto", text)
        self.assertNotIn("Ranking oculto", text)

    def test_writes_are_still_authenticated_while_frozen(self):
        # The proxy keeps feeding a frozen group, also the stores that the
        # public may not read: the writes are answered before the freeze
        # is applied.
        data = json.dumps({
            "c1": {"submission": "s1", "time": 100, "score": 40.0}})
        with self.at(350):
            anonymous = self.client.put(
                "/olim/subchanges/", data=data,
                content_type="application/json")
            proxy = self.client.put(
                "/olim/subchanges/", data=data,
                content_type="application/json", headers=AUTH)
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(proxy.status_code, 204)

    def test_hidden_wins_over_frozen(self):
        self.client.put("/olim/visibility", data=json.dumps({
            "hide_at": 300, "show_at": None, "freeze_at": 200,
            "unfreeze_at": 400, "staff_password": STAFF_HASH}),
            content_type="application/json", headers=AUTH)
        with self.at(350):
            self.assertEqual(
                self.client.get("/olim/scores",
                                headers=self.JSON).status_code, 403)
            page = self.client.get("/olim/").get_data(as_text=True)
        self.assertIn("Este ranking está oculto por ahora.", page)
        self.assertNotIn("congelado", page)

    def test_every_route_is_classified(self):
        # A new data endpoint must be classified before it can leak.
        app = build_ranking_app(
            self.config, os.path.join(self.tmp, "x"), self.web_dir)
        # SharedDataMiddleware -> DispatcherMiddleware
        dispatcher = app.app
        mounts = {m.strip("/").split("/")[0] for m in dispatcher.mounts}
        routes = {r.rule.strip("/").split("/")[0]
                  for r in dispatcher.app.router.iter_rules()}
        # The static files are served from the top level as well.
        static = set(os.listdir(self.web_dir))
        self.assertLessEqual(mounts | routes | static,
                             set(FROZEN_ROUTES) | {""})


if __name__ == "__main__":
    unittest.main()
