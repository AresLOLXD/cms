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

"""Tests for the scores of RWS as they were at a given time."""

import os
import shutil
import tempfile
import unittest

from cmscommon.constants import SCORE_MODE_MAX
from cmsranking.Contest import Contest
from cmsranking.Scoring import Score, ScoringStore
from cmsranking.Store import Store
from cmsranking.Subchange import Subchange
from cmsranking.Submission import Submission
from cmsranking.Task import Task
from cmsranking.Team import Team
from cmsranking.User import User


def submission(user: str, task: str, time: int) -> Submission:
    sub = Submission()
    sub.user, sub.task, sub.time = user, task, time
    return sub


def change(key: str, sub_key: str, time: int, score: float | None = None,
           token: bool | None = None) -> Subchange:
    ch = Subchange()
    ch.key, ch.submission, ch.time = key, sub_key, time
    ch.score, ch.token, ch.extra = score, token, None
    return ch


class TestScoreAt(unittest.TestCase):

    def setUp(self):
        self.score = Score(SCORE_MODE_MAX)
        self.score.create_submission("s1", submission("u", "t", 100))
        self.score.create_subchange("c1", change("c1", "s1", 100, 30.0))
        self.score.create_submission("s2", submission("u", "t", 200))
        self.score.create_subchange("c2", change("c2", "s2", 200, 80.0))

    def test_score_before_and_after_each_change(self):
        self.assertEqual(self.score.score_at(99), 0.0)
        self.assertEqual(self.score.score_at(100), 30.0)
        self.assertEqual(self.score.score_at(199), 30.0)
        self.assertEqual(self.score.score_at(200), 80.0)
        self.assertEqual(self.score.score_at(10 ** 12), 80.0)

    def test_late_evaluation_of_an_early_submission_counts(self):
        # The change carries the submission's time, not the evaluation's.
        self.score.create_submission("s3", submission("u", "t", 150))
        self.score.create_subchange("c3", change("c3", "s3", 150, 50.0))
        self.assertEqual(self.score.score_at(160), 50.0)

    def test_submissions_at_hides_later_submissions_and_results(self):
        subs = self.score.submissions_at(150)
        self.assertEqual(set(subs), {"s1"})
        self.assertEqual(subs["s1"].score, 30.0)
        # The live objects are not modified.
        self.assertEqual(self.score._submissions["s2"].score, 80.0)

    def test_submissions_at_includes_a_submission_made_exactly_at_t(self):
        subs = self.score.submissions_at(200)
        self.assertEqual(set(subs), {"s1", "s2"})
        self.assertEqual(subs["s2"].score, 80.0)

    def test_submissions_at_ignores_a_later_token(self):
        self.score.create_subchange(
            "c4", change("c4", "s1", 300, token=True))
        self.assertIs(self.score.submissions_at(250)["s1"].token, False)
        self.assertIs(self.score.submissions_at(300)["s1"].token, True)


class TestScoringStoreAt(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        stores = {}
        stores["subchange"] = Store(
            Subchange, os.path.join(tmp, "subchanges"), stores)
        stores["submission"] = Store(
            Submission, os.path.join(tmp, "submissions"), stores,
            [stores["subchange"]])
        stores["user"] = Store(
            User, os.path.join(tmp, "users"), stores,
            [stores["submission"]])
        stores["team"] = Store(
            Team, os.path.join(tmp, "teams"), stores, [stores["user"]])
        stores["task"] = Store(
            Task, os.path.join(tmp, "tasks"), stores,
            [stores["submission"]])
        stores["contest"] = Store(
            Contest, os.path.join(tmp, "contests"), stores,
            [stores["task"]])
        # Like build_ranking_app: this also creates the empty directories.
        for store in stores.values():
            store.load_from_disk()
        self.scoring = ScoringStore(stores)
        stores["contest"].create(
            "c", {"name": "C", "begin": 0, "end": 10 ** 6,
                  "score_precision": 0})
        stores["task"].create(
            "t", {"name": "T", "short_name": "t", "contest": "c",
                  "order": 0, "max_score": 100.0, "extra_headers": [],
                  "score_precision": 0, "score_mode": "max"})
        for u in ("early", "late"):
            stores["user"].create(
                u, {"f_name": u, "l_name": u, "team": None})
        stores["submission"].create(
            "s1", {"user": "early", "task": "t", "time": 100})
        stores["subchange"].create(
            "c1", {"submission": "s1", "time": 100, "score": 40.0})
        stores["submission"].create(
            "s2", {"user": "late", "task": "t", "time": 300})
        stores["subchange"].create(
            "c2", {"submission": "s2", "time": 300, "score": 90.0})

    def test_scores_at(self):
        self.assertEqual(self.scoring.get_scores_at(200),
                         {"early": {"t": 40.0}})
        self.assertEqual(self.scoring.get_scores_at(300),
                         {"early": {"t": 40.0}, "late": {"t": 90.0}})

    def test_history_until(self):
        self.assertEqual(
            [h[2] for h in self.scoring.get_global_history(until=200)],
            [100])
        self.assertEqual(len(list(self.scoring.get_global_history())), 2)

    def test_history_until_includes_the_boundary(self):
        self.assertEqual(
            [h[2] for h in self.scoring.get_global_history(until=300)],
            [100, 300])

    def test_submissions_at_includes_a_submission_made_exactly_at_t(self):
        self.assertEqual(
            set(self.scoring.get_submissions_at("late", "t", 300)), {"s2"})

    def test_submissions_at(self):
        self.assertEqual(
            self.scoring.get_submissions_at("late", "t", 200), {})
        self.assertEqual(
            set(self.scoring.get_submissions_at("early", "t", 200)),
            {"s1"})
