"""Tests for how RWS takes the entities sent in one PUT of a list.

ProxyService sends at most one PUT per entity type and round, with the
entities of every operation of the round merged in it, so what RWS does
with a list that is only partly valid decides what ProxyService loses
(see #13, item S1).

"""

import json
import os
import shutil
import tempfile
import unittest
from base64 import b64encode
from importlib.resources import files

from werkzeug.test import Client

from cmsranking.Config import Config
from cmsranking.RankingWebServer import build_ranking_app


USERNAME = "rws"
PASSWORD = "secret"
AUTH = {"Authorization": "Basic " + b64encode(
    ("%s:%s" % (USERNAME, PASSWORD)).encode()).decode()}

CONTEST = {"name": "Day 1", "begin": 0, "end": 10, "score_precision": 0}
TASK = {"name": "Sum", "short_name": "sum", "contest": "c1",
        "max_score": 100.0, "score_precision": 0, "extra_headers": [],
        "score_mode": "max", "order": 0}
ALICE = {"f_name": "Alice", "l_name": "A", "team": None}


class TestPutList(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        config = Config(username=USERNAME, password=PASSWORD,
                        lib_dir=os.path.join(self.tmp, "lib"),
                        log_dir=os.path.join(self.tmp, "log"))
        self.client = Client(build_ranking_app(
            config, config.lib_dir, str(files("cmsranking") / "static")))
        self.assertEqual(self.put("contests", {"c1": CONTEST}), 204)
        self.assertEqual(self.put("tasks", {"sum": TASK}), 204)
        self.assertEqual(self.put("users", {"alice": ALICE}), 204)

    def put(self, resource: str, entities: dict) -> int:
        """PUT entities to the list of resource, return the status."""
        return self.client.put(
            "/%s/" % resource, data=json.dumps(entities),
            content_type="application/json", headers=AUTH).status_code

    def get(self, resource: str):
        return self.client.get("/%s/" % resource).json

    def test_one_unknown_user_refuses_the_whole_list(self):
        status = self.put("submissions", {
            "1": {"user": "alice", "task": "sum", "time": 1},
            "2": {"user": "bob", "task": "sum", "time": 2}})

        # Bob is unknown, and Alice's valid submission goes with his.
        self.assertEqual(status, 400)
        self.assertEqual(self.get("submissions"), {})

    def test_one_unknown_submission_refuses_the_whole_list(self):
        self.assertEqual(self.put("submissions", {
            "1": {"user": "alice", "task": "sum", "time": 1}}), 204)

        status = self.put("subchanges", {
            "1s": {"submission": "1", "time": 1, "score": 100.0,
                   "extra": ["100"]},
            "2s": {"submission": "2", "time": 2, "score": 50.0,
                   "extra": ["50"]}})

        # Submission 2 is unknown, so the score of 1 is not taken.
        self.assertEqual(status, 400)
        self.assertEqual(self.get("subchanges"), {})
        self.assertEqual(self.client.get(
            "/scores", headers={"Accept": "application/json"}).json, {})


if __name__ == "__main__":
    unittest.main()
