"""Tests for per-group ranking namespaces in RWS."""

import json
import os
import shutil
import tempfile
import unittest
from base64 import b64encode
from importlib.resources import files

from werkzeug.test import Client

from cmsranking.Config import Config
from cmsranking.RankingWebServer import NamespaceDispatcher, \
    build_ranking_app


USERNAME = "rws"
PASSWORD = "secret"
AUTH = {"Authorization": "Basic " + b64encode(
    ("%s:%s" % (USERNAME, PASSWORD)).encode()).decode()}
CONTEST = {"name": "Day 1", "begin": 0, "end": 10, "score_precision": 0}


class TestNamespaces(unittest.TestCase):

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

    def put_contest(self, prefix: str, auth: bool = True):
        return self.client.put(
            prefix + "/contests/", data=json.dumps({"c1": CONTEST}),
            content_type="application/json", headers=AUTH if auth else {})

    def get_contests(self, prefix: str):
        return self.client.get(prefix + "/contests/")

    def test_root_namespace_unchanged(self):
        self.assertEqual(self.put_contest("").status_code, 204)
        self.assertEqual(self.get_contests("").json, {"c1": CONTEST})

    def test_put_creates_isolated_group_namespace(self):
        self.assertEqual(self.put_contest("/olim").status_code, 204)
        self.assertTrue(
            os.path.isdir(os.path.join(self.lib_dir, "groups", "olim")))
        self.assertEqual(self.get_contests("/olim").json, {"c1": CONTEST})
        self.assertEqual(self.get_contests("").json, {})

    def test_unauthenticated_put_does_not_create_namespace(self):
        self.assertEqual(self.put_contest("/olim", auth=False).status_code,
                         401)
        self.assertFalse(
            os.path.exists(os.path.join(self.lib_dir, "groups", "olim")))

    def test_get_unknown_namespace_is_404(self):
        self.assertEqual(self.get_contests("/omips").status_code, 404)

    def test_reserved_name_is_served_by_root(self):
        self.put_contest("")
        # "contests" is reserved, so /contests/ is the root store, not a
        # namespace called "contests".
        self.assertEqual(self.get_contests("").json, {"c1": CONTEST})
        self.assertFalse(
            os.path.exists(os.path.join(self.lib_dir, "groups", "contests")))

    def test_namespaces_reload_from_disk(self):
        self.put_contest("/olim")
        self.client.delete("/omips/users/", headers=AUTH)  # empty namespace
        self.client = self.make_client()
        self.assertEqual(self.get_contests("/olim").json, {"c1": CONTEST})
        self.assertEqual(self.client.get("/omips/users/").status_code, 200)

    def test_group_without_trailing_slash_redirects(self):
        self.put_contest("/olim")
        response = self.client.get("/olim")
        self.assertEqual(response.status_code, 301)
        self.assertTrue(response.headers["Location"].endswith("/olim/"))

    def test_group_serves_frontend(self):
        self.put_contest("/olim")
        response = self.client.get("/olim/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")

    def test_delete_list_only_affects_its_namespace(self):
        self.put_contest("")
        self.put_contest("/olim")
        self.assertEqual(
            self.client.delete("/olim/contests/", headers=AUTH).status_code,
            204)
        self.assertEqual(self.get_contests("/olim").json, {})
        self.assertEqual(self.get_contests("").json, {"c1": CONTEST})


if __name__ == "__main__":
    unittest.main()
