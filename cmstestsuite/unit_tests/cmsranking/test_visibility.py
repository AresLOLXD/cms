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
import shutil
import tempfile
import unittest
from base64 import b64encode
from importlib.resources import files

from werkzeug.test import Client

from cmscommon.crypto import build_password
from cmsranking.Config import Config
from cmsranking.RankingWebServer import NamespaceDispatcher, \
    build_ranking_app
from cmsranking.visibility import VISIBILITY_FILE, VisibilityGuard, \
    VisibilityState


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
        return Client(dispatcher)

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
