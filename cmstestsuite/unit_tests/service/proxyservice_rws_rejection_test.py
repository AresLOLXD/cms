"""What ProxyService loses when RWS refuses a round of scores (#13, S1).

RWS takes the entities of a PUT of a list all or none (see
cmstestsuite/unit_tests/cmsranking/test_put_list.py), and ProxyService
merges the entities of every operation of a round into one PUT per
entity type and namespace. These tests pin what that costs today.

"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from cms.conf import Address
from cms.db import RankingGroup
from cms.service.ProxyService import ProxyExecutor, ProxyService, encode_id
from cmsranking.Contest import Contest as RankingContest
from cmsranking.Entity import InvalidData
from cmsranking.Scoring import ScoringStore
from cmsranking.Store import Store
from cmsranking.Subchange import Subchange as RankingSubchange
from cmsranking.Submission import Submission as RankingSubmission
from cmsranking.Task import Task as RankingTask
from cmsranking.Team import Team as RankingTeam
from cmsranking.User import User as RankingUser
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


class FakeRanking:
    """A ranking that takes and refuses data the way RWS does.

    The data goes to the real RWS stores and entities, wired like
    build_ranking_app wires them, and a PUT of a list is answered like
    StoreHandler.put_list answers it: 400 if the store refuses it, 204
    otherwise. Only one namespace: the tests use a single group.

    """

    STORES = {"contests": "contest", "tasks": "task", "teams": "team",
              "users": "user", "submissions": "submission",
              "subchanges": "subchange"}

    def __init__(self, lib_dir: str):
        def path(name):
            return os.path.join(lib_dir, name)
        stores: dict = dict()
        stores["subchange"] = Store(
            RankingSubchange, path("subchanges"), stores)
        stores["submission"] = Store(
            RankingSubmission, path("submissions"), stores,
            [stores["subchange"]])
        stores["user"] = Store(
            RankingUser, path("users"), stores, [stores["submission"]])
        stores["team"] = Store(
            RankingTeam, path("teams"), stores, [stores["user"]])
        stores["task"] = Store(
            RankingTask, path("tasks"), stores, [stores["submission"]])
        stores["contest"] = Store(
            RankingContest, path("contests"), stores, [stores["task"]])
        for store in stores.values():
            store.load_from_disk()
        stores["scoring"] = ScoringStore(stores)
        stores["scoring"].init_store()
        self.stores = stores

    @staticmethod
    def _answer(status_code: int) -> MagicMock:
        response = MagicMock()
        response.status_code = status_code
        return response

    def put(self, url: str, body: str, **kwargs) -> MagicMock:
        resource = urlsplit(url).path.strip("/").split("/")[-1]
        if resource == "visibility":
            return self._answer(204)
        try:
            self.stores[self.STORES[resource]].merge_list(json.loads(body))
        except InvalidData:
            return self._answer(400)
        return self._answer(204)

    def delete(self, url: str, **kwargs) -> MagicMock:
        resource = urlsplit(url).path.strip("/").split("/")[-1]
        self.stores[self.STORES[resource]].delete_list()
        return self._answer(204)

    def has_submission(self, submission_id: int) -> bool:
        return str(submission_id) in self.stores["submission"]

    def score(self, username: str, task_name: str) -> float:
        return self.stores["scoring"].get_score(
            encode_id(username), encode_id(task_name))


async def idle_run(executor):
    """Stand in for ProxyExecutor.run: the test runs the rounds."""


class TestRejectedRoundOfScores(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    def setUp(self):
        super().setUp()
        self.group = RankingGroup(name="olim", description="OLIM")
        self.session.add(self.group)
        self.contest = self.add_contest(ranking_group=self.group)
        self.task = self.add_task(contest=self.contest, score_precision=0)
        self.dataset = self.add_dataset(
            task=self.task, score_type="Sum", score_type_parameters=1)
        self.task.active_dataset = self.dataset
        self.alice = self.add_participation(contest=self.contest)
        self.session.commit()

    def tearDown(self):
        # Group mode sends every grouped contest in the DB.
        self.delete_data()
        super().tearDown()

    async def asyncSetUp(self):
        for target in ("cms.io.async_service.get_service_address",
                       "cms.io.async_rpc.get_service_address"):
            patcher = patch(target, return_value=Address("127.0.0.1", 0))
            patcher.start()
            self.addCleanup(patcher.stop)

        lib_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, lib_dir)
        self.ranking = FakeRanking(lib_dir)
        for method in ("put", "delete"):
            patcher = patch("cms.service.ProxyService.requests." + method,
                            getattr(self.ranking, method))
            patcher.start()
            self.addCleanup(patcher.stop)

        # The executor does not run on its own: each next_round() is
        # one round, which takes everything queued, like run() does.
        with patch.object(ProxyService, "start_sweeper",
                          lambda self, timeout: None), \
                patch.object(ProxyExecutor, "run", idle_run):
            self.service = ProxyService(0, None)
        self.addCleanup(self.service._disconnect_all)
        self.executor = self.service.get_executor()
        await self.next_round()

    async def next_round(self):
        queue = self.executor._operation_queue
        entries = []
        while not queue.empty():
            entries.append(await queue.pop())
        await self.executor.execute(entries)

    def add_scored_submission(self, participation):
        submission = self.add_submission(
            task=self.task, participation=participation)
        result = self.add_submission_result(
            submission=submission, dataset=self.dataset)
        result.compilation_outcome = "ok"
        result.evaluation_outcome = "ok"
        result.score = 100.0
        result.score_details = []
        result.public_score = 100.0
        result.public_score_details = []
        result.ranking_score_details = ["100"]
        return submission

    async def test_unknown_user_loses_the_scores_sent_with_it(self):
        alice = self.alice.user.username
        self.assertIn(encode_id(alice), self.ranking.stores["user"])

        # Bob joins the contest behind ProxyService's back (as
        # cmsAddParticipation does, which CMS-Loader runs): nothing
        # sends him to RWS.
        bob = self.add_participation(contest=self.contest)
        alice_submission = self.add_scored_submission(self.alice)
        bob_submission = self.add_scored_submission(bob)
        self.session.commit()

        # ScoringService reports both scores before the next round.
        self.service._submission_scored_sync(alice_submission.id)
        self.service._submission_scored_sync(bob_submission.id)
        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            await self.next_round()

        # RWS refused the whole round because of Bob: Alice's valid
        # score is lost with his (and both subchanges with them).
        self.assertTrue(any("rejected the submissions" in line
                            for line in logs.output))
        self.assertFalse(self.ranking.has_submission(alice_submission.id))
        self.assertEqual(self.ranking.score(alice, self.task.name), 0)

        # Nothing sends it again: the sweeper takes it as sent (and
        # does not send Bob either)...
        self.assertEqual(self.service._missing_operations_sync(), 0)
        await self.next_round()
        self.assertFalse(self.ranking.has_submission(alice_submission.id))
        self.assertNotIn(encode_id(bob.user.username),
                         self.ranking.stores["user"])

        # ...nor does a reinitialize, which gets Bob to RWS, but not the
        # scores refused before.
        self.service._reinitialize_sync()
        await self.next_round()
        self.assertIn(encode_id(bob.user.username),
                      self.ranking.stores["user"])
        self.assertFalse(self.ranking.has_submission(alice_submission.id))

        # Only Regenerate sends it again (or restarting ProxyService,
        # whose first sweep sends every score).
        self.service._regenerate_ranking_sync(self.group.name)
        await self.next_round()
        self.assertTrue(self.ranking.has_submission(alice_submission.id))
        self.assertTrue(self.ranking.has_submission(bob_submission.id))
        self.assertEqual(self.ranking.score(alice, self.task.name), 100)

    async def test_known_users_lose_nothing(self):
        alice = self.alice.user.username
        first = self.add_scored_submission(self.alice)
        second = self.add_scored_submission(self.alice)
        self.session.commit()

        self.service._submission_scored_sync(first.id)
        self.service._submission_scored_sync(second.id)
        await self.next_round()

        self.assertTrue(self.ranking.has_submission(first.id))
        self.assertTrue(self.ranking.has_submission(second.id))
        self.assertEqual(self.ranking.score(alice, self.task.name), 100)


if __name__ == "__main__":
    unittest.main()
