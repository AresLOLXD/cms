"""Tests for ProxyService in group mode (no contest id)."""

import gevent.monkey
gevent.monkey.patch_all()  # noqa

import json
import unittest
from unittest.mock import patch, PropertyMock
from urllib.parse import urljoin

import gevent

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.db import RankingGroup
from cms.service.ProxyService import ProxyService, encode_id
from cmscommon.constants import SCORE_MODE_MAX


RANKING = config.proxy_service.rankings[0]


def url(resource: str) -> str:
    return urljoin(RANKING, resource)


class TestProxyServiceGroups(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()

        patcher = patch("cms.db.Dataset.score_type_object",
                        new_callable=PropertyMock)
        score_type = patcher.start().return_value
        self.addCleanup(patcher.stop)
        score_type.max_score = 100
        score_type.ranking_headers = ["100"]

        patcher = patch("requests.put")
        self.requests_put = patcher.start()
        self.addCleanup(patcher.stop)
        self.requests_put.return_value.status_code = 200

        patcher = patch("requests.delete")
        self.requests_delete = patcher.start()
        self.addCleanup(patcher.stop)
        self.requests_delete.return_value.status_code = 204

        self.olim = RankingGroup(name="olim", description="OLIM")
        self.omips = RankingGroup(name="omips", description="OMIPS")
        self.session.add_all([self.olim, self.omips])

        self.user = self.add_user()
        self.contest_a, self.sub_a = self.add_contest_with_submission(
            self.olim)
        self.contest_b, self.sub_b = self.add_contest_with_submission(
            self.omips)
        self.contest_c, self.sub_c = self.add_contest_with_submission(None)
        self.session.commit()

    def tearDown(self):
        # Group mode sends every grouped contest in the DB, and group
        # names are unique: start each test from an empty DB.
        self.delete_data()
        super().tearDown()

    def add_contest_with_submission(self, group):
        contest = self.add_contest(ranking_group=group)
        task = self.add_task(contest=contest)
        task.score_mode = SCORE_MODE_MAX
        dataset = self.add_dataset(task=task)
        task.active_dataset = dataset
        participation = self.add_participation(user=self.user,
                                               contest=contest)
        submission = self.add_submission(task=task,
                                         participation=participation)
        result = self.add_submission_result(submission=submission,
                                            dataset=dataset)
        result.compilation_outcome = "ok"
        result.evaluation_outcome = "ok"
        result.score = 100
        result.score_details = dict()
        result.public_score = 50
        result.public_score_details = dict()
        result.ranking_score_details = ["100"]
        return contest, submission

    def start(self, contest_id=None) -> ProxyService:
        service = ProxyService(0, contest_id)
        gevent.sleep(0.1)
        return service

    def clear_requests(self):
        self.requests_put.reset_mock()
        self.requests_delete.reset_mock()

    def put_urls(self) -> list[str]:
        return [c.args[0] for c in self.requests_put.call_args_list]

    def put_payload(self, target: str) -> dict:
        payload = dict()
        for c in self.requests_put.call_args_list:
            if c.args[0] == target:
                payload.update(json.loads(c.args[1]))
        return payload

    def delete_urls(self) -> list[str]:
        return [c.args[0] for c in self.requests_delete.call_args_list]

    def test_startup_sends_each_contest_to_its_group(self):
        self.start()
        self.assertEqual(set(self.put_payload(url("olim/contests/"))),
                         {encode_id(self.contest_a.name)})
        self.assertEqual(set(self.put_payload(url("omips/contests/"))),
                         {encode_id(self.contest_b.name)})
        self.assertNotIn(url("contests/"), self.put_urls())
        self.assertIn(url("olim/submissions/"), self.put_urls())

    def test_legacy_mode_sends_to_root(self):
        self.start(self.contest_c.id)
        self.assertEqual(set(self.put_payload(url("contests/"))),
                         {encode_id(self.contest_c.name)})
        self.assertFalse(any("olim/" in u or "omips/" in u
                             for u in self.put_urls()))

    def test_submission_of_contest_without_group_is_ignored(self):
        service = self.start()
        self.clear_requests()
        service.submission_scored(self.sub_c.id)
        gevent.sleep(0.1)
        self.assertEqual(self.put_urls(), [])

    def test_submission_scored_goes_to_its_group(self):
        service = self.start()
        self.clear_requests()
        service.submission_scored(self.sub_b.id)
        gevent.sleep(0.1)
        self.assertIn(url("omips/submissions/"), self.put_urls())
        self.assertNotIn(url("olim/submissions/"), self.put_urls())

    def test_moving_contest_resets_old_group(self):
        service = self.start()
        self.clear_requests()
        self.contest_a.ranking_group = self.omips
        self.session.commit()
        service.reinitialize()
        gevent.sleep(0.1)
        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        self.assertIn(encode_id(self.contest_a.name),
                      self.put_payload(url("omips/contests/")))
        self.assertIn(url("omips/submissions/"), self.put_urls())

    def test_shared_user_single_entry_and_no_reset(self):
        # Day 2 of OLIM, with the same contestant as day 1.
        contest_a2, _ = self.add_contest_with_submission(self.olim)
        self.session.commit()
        service = self.start()
        self.assertEqual(set(self.put_payload(url("olim/users/"))),
                         {encode_id(self.user.username)})
        self.assertEqual(set(self.put_payload(url("olim/contests/"))),
                         {encode_id(self.contest_a.name),
                          encode_id(contest_a2.name)})
        self.clear_requests()
        self.contest_a.description = "Renamed"
        self.session.commit()
        service.reinitialize()
        gevent.sleep(0.1)
        self.assertEqual(self.delete_urls(), [])

    def test_regenerate_group_only_touches_its_namespace(self):
        service = self.start()
        self.clear_requests()
        service.regenerate_ranking("olim")
        gevent.sleep(0.1)
        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        # Already-sent scores are sent again.
        self.assertIn(url("olim/submissions/"), self.put_urls())
        self.assertFalse(any("omips/" in u for u in self.put_urls()))

    def test_regenerate_root_in_group_mode_empties_it(self):
        service = self.start()
        self.clear_requests()
        service.regenerate_ranking(None)
        gevent.sleep(0.1)
        self.assertEqual(self.delete_urls(),
                         [url("contests/"), url("users/")])
        self.assertEqual(self.put_urls(), [])


if __name__ == "__main__":
    unittest.main()
