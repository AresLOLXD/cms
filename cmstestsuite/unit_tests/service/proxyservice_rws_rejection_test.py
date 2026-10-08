"""How ProxyService keeps the scores RWS refuses in a round (#13, S1).

RWS takes the entities of a PUT of a list all or none (see
cmstestsuite/unit_tests/cmsranking/test_put_list.py), and ProxyService
merges the entities of every operation of a round into one PUT per
entity type and namespace. These tests pin that a refused round loses
only what RWS refuses on its own, and that the next sweep sends the
contest data RWS lacks and then the scores it refused, without
Regenerate.

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

    async def score_with_an_unknown_user(self):
        """Send a round with the scores of Alice and of Bob, unknown to RWS.

        return: Bob's participation, Alice's and Bob's submissions, and
            the WARNING lines of the round.

        """
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
        return bob, alice_submission, bob_submission, logs.output

    async def test_unknown_user_does_not_lose_the_scores_sent_with_it(self):
        alice = self.alice.user.username
        self.assertIn(encode_id(alice), self.ranking.stores["user"])

        bob, alice_submission, bob_submission, warnings = \
            await self.score_with_an_unknown_user()

        # RWS refused Bob's score (and his subchange) on its own:
        # Alice's valid score got there in the same round.
        self.assertTrue(self.ranking.has_submission(alice_submission.id))
        self.assertEqual(self.ranking.score(alice, self.task.name), 100)
        self.assertFalse(self.ranking.has_submission(bob_submission.id))
        # The operator learns that Bob's will be sent again, without
        # being told to use Regenerate.
        refusals = [line for line in warnings if " refused " in line]
        self.assertEqual(len(refusals), 2)
        for line in refusals:
            self.assertIn("refused 1 of 2", line)
            self.assertIn("sent again after the contest data", line)
        self.assertFalse(any("Regenerate" in line for line in warnings))

    async def test_next_sweep_sends_the_unknown_user_then_his_score(self):
        alice = self.alice.user.username
        bob, _, bob_submission, _ = await self.score_with_an_unknown_user()
        self.assertNotIn(encode_id(bob.user.username),
                         self.ranking.stores["user"])

        # One sweep sends the contest data, Bob with it, then his
        # score: RWS takes all of it, with no Regenerate.
        self.service._missing_operations_sync()
        with self.assertNoLogs("cms.service.ProxyService", "WARNING"):
            await self.next_round()

        self.assertIn(encode_id(bob.user.username),
                      self.ranking.stores["user"])
        self.assertTrue(self.ranking.has_submission(bob_submission.id))
        self.assertEqual(
            self.ranking.score(bob.user.username, self.task.name), 100)
        self.assertEqual(self.ranking.score(alice, self.task.name), 100)

    async def test_a_repaired_group_is_not_repaired_again(self):
        await self.score_with_an_unknown_user()
        self.assertEqual(self.service._groups_to_repair, {self.group.name})
        self.service._missing_operations_sync()
        await self.next_round()
        self.assertEqual(self.service._groups_to_repair, set())

        # The next sweep sends no contest data (nor any score): only
        # the visibility settings of the group, which each sweep sends.
        self.assertEqual(self.service._missing_operations_sync(), 0)
        self.assertEqual(
            [(status["item"]["type"], status["item"]["group"])
             for status in self.executor.get_status()],
            [(ProxyExecutor.VISIBILITY_TYPE, self.group.name)])

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
