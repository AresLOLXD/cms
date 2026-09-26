"""Tests for ProxyService in group mode (no contest id)."""

import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urljoin

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin

from cms import config
from cms.conf import Address
from cms.db import RankingGroup
from cms.service.ProxyService import ProxyService, encode_id
from cmscommon.constants import SCORE_MODE_MAX


RANKING = config.proxy_service.rankings[0]


def url(resource: str) -> str:
    return urljoin(RANKING, resource)


class TestProxyServiceGroups(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    def setUp(self):
        super().setUp()

        score_type = MagicMock()
        score_type.max_score = 100
        score_type.ranking_headers = ["100"]
        # IDs of the datasets whose score type cannot be built, like
        # one with a subtask regexp matching no testcase.
        self.broken_datasets: set[int] = set()

        def score_type_object(dataset):
            if dataset.id in self.broken_datasets:
                raise ValueError("No testcase matches against the regexp")
            return score_type

        patcher = patch("cms.db.Dataset.score_type_object",
                        property(score_type_object))
        patcher.start()
        self.addCleanup(patcher.stop)

        put_patcher = patch("cms.service.ProxyService.requests.put")
        self.requests_put = put_patcher.start()
        self.addCleanup(put_patcher.stop)
        self.requests_put.return_value.status_code = 200

        delete_patcher = patch("cms.service.ProxyService.requests.delete")
        self.requests_delete = delete_patcher.start()
        self.addCleanup(delete_patcher.stop)
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

    async def asyncSetUp(self):
        address_patcher = patch(
            "cms.io.async_service.get_service_address",
            return_value=Address("127.0.0.1", 0))
        address_patcher.start()
        self.addCleanup(address_patcher.stop)

        # ProxyService.__init__ (via AsyncService.__init__) connects to
        # LogService, which goes through async_rpc's own imported
        # reference to get_service_address.
        rpc_address_patcher = patch(
            "cms.io.async_rpc.get_service_address",
            return_value=Address("127.0.0.1", 0))
        rpc_address_patcher.start()
        self.addCleanup(rpc_address_patcher.stop)

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

    async def start(self, contest_id: int | None = None) -> ProxyService:
        """Build a ProxyService and let its startup data reach the rankings.

        Mirrors ProxyService_test.py's _build_service: constructs the
        service while this test's own event loop is already running (so
        add_executor's _call_when_running spawns the executor's run()
        loop as a background task immediately, and the service's own
        __init__ enqueues its initial contest/user/task batch
        synchronously since self._loop is still None at that point).
        self._loop is then set explicitly, mirroring what _async_run()
        does, so later _threadsafe_enqueue calls take their
        call_soon_threadsafe branch, same as in production.

        start_sweeper is stubbed out, same as in ProxyService_test.py,
        so the periodic sweeper can't race with (and do the work of) a
        test's own operations; unlike ProxyService_test.py, tests in
        this file do rely on already-scored submissions being sent at
        startup (what the sweeper's first run would do in production),
        so _missing_operations() is awaited directly here once instead.

        contest_id: the contest id to pass to ProxyService (legacy
            mode), or None for group mode.

        return: the constructed, already-initialized service.

        """
        with patch.object(
                ProxyService, "start_sweeper", lambda self, timeout: None):
            service = ProxyService(0, contest_id)
        service._loop = asyncio.get_running_loop()
        self.addCleanup(service._disconnect_all)
        # Awaiting _missing_operations() yields control long enough
        # (via its own run_in_executor call) for the executor's run()
        # task to also process __init__'s already-queued contest/user/
        # task batch, before this then enqueues already-scored
        # submissions the same way the sweeper's first run would.
        await service._missing_operations()
        await self._wait_until(
            lambda: any("submissions/" in u for u in self.put_urls()))
        return service

    async def _wait_until(self, predicate, attempts: int = 50) -> None:
        """Poll predicate() until it is true, or give up.

        predicate: a zero-argument callable to poll.
        attempts: how many times to poll, sleeping 0.05s between tries.

        """
        for _ in range(attempts):
            if predicate():
                return
            await asyncio.sleep(0.05)

    def clear_requests(self):
        self.requests_put.reset_mock()
        self.requests_delete.reset_mock()

    def break_contest(self, contest):
        self.broken_datasets.add(contest.tasks[0].active_dataset.id)

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

    async def test_startup_sends_each_contest_to_its_group(self):
        await self.start()
        self.assertEqual(set(self.put_payload(url("olim/contests/"))),
                         {encode_id(self.contest_a.name)})
        self.assertEqual(set(self.put_payload(url("omips/contests/"))),
                         {encode_id(self.contest_b.name)})
        self.assertNotIn(url("contests/"), self.put_urls())
        self.assertIn(url("olim/submissions/"), self.put_urls())

    async def test_legacy_mode_sends_to_root(self):
        await self.start(self.contest_c.id)
        self.assertEqual(set(self.put_payload(url("contests/"))),
                         {encode_id(self.contest_c.name)})
        self.assertFalse(any("olim/" in u or "omips/" in u
                             for u in self.put_urls()))

    async def test_submission_of_contest_without_group_is_ignored(self):
        service = await self.start()
        self.clear_requests()
        await service.submission_scored(self.sub_c.id)
        await asyncio.sleep(0.1)
        self.assertEqual(self.put_urls(), [])

    async def test_submission_scored_goes_to_its_group(self):
        service = await self.start()
        self.clear_requests()
        await service.submission_scored(self.sub_b.id)
        await self._wait_until(lambda: self.put_urls() != [])
        self.assertIn(url("omips/submissions/"), self.put_urls())
        self.assertNotIn(url("olim/submissions/"), self.put_urls())

    async def test_moving_contest_resets_old_group(self):
        service = await self.start()
        self.clear_requests()
        self.contest_a.ranking_group = self.omips
        self.session.commit()
        await service.reinitialize()
        await self._wait_until(
            lambda: self.delete_urls() != [] and self.put_urls() != [])
        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        self.assertIn(encode_id(self.contest_a.name),
                      self.put_payload(url("omips/contests/")))
        self.assertIn(url("omips/submissions/"), self.put_urls())

    async def test_shared_user_single_entry_and_no_reset(self):
        # Day 2 of OLIM, with the same contestant as day 1.
        contest_a2, _ = self.add_contest_with_submission(self.olim)
        self.session.commit()
        service = await self.start()
        self.assertEqual(set(self.put_payload(url("olim/users/"))),
                         {encode_id(self.user.username)})
        self.assertEqual(set(self.put_payload(url("olim/contests/"))),
                         {encode_id(self.contest_a.name),
                          encode_id(contest_a2.name)})
        self.clear_requests()
        self.contest_a.description = "Renamed"
        self.session.commit()
        await service.reinitialize()
        await self._wait_until(lambda: self.put_urls() != [])
        self.assertEqual(self.delete_urls(), [])

    async def test_regenerate_group_only_touches_its_namespace(self):
        service = await self.start()
        self.clear_requests()
        await service.regenerate_ranking("olim")
        await self._wait_until(
            lambda: self.delete_urls() != [] and self.put_urls() != [])
        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        # Already-sent scores are sent again.
        self.assertIn(url("olim/submissions/"), self.put_urls())
        self.assertFalse(any("omips/" in u for u in self.put_urls()))

    async def test_regenerate_root_in_group_mode_empties_it(self):
        service = await self.start()
        self.clear_requests()
        await service.regenerate_ranking(None)
        await self._wait_until(lambda: self.delete_urls() != [])
        self.assertEqual(self.delete_urls(),
                         [url("contests/"), url("users/")])
        self.assertEqual(self.put_urls(), [])

    async def test_broken_contest_does_not_stop_startup(self):
        self.break_contest(self.contest_b)
        await self.start()
        # The other group is sent in full.
        self.assertEqual(set(self.put_payload(url("olim/contests/"))),
                         {encode_id(self.contest_a.name)})
        self.assertIn("%d" % self.sub_a.id,
                      self.put_payload(url("olim/submissions/")))
        # The broken contest and its submissions are held back, since
        # RWS would reject submissions of tasks it does not know.
        self.assertNotIn(url("omips/contests/"), self.put_urls())
        self.assertNotIn(url("omips/submissions/"), self.put_urls())

    async def test_reset_group_is_refilled_despite_broken_contest(self):
        contest_a2, sub_a2 = self.add_contest_with_submission(self.olim)
        contest_a3, sub_a3 = self.add_contest_with_submission(self.olim)
        self.session.commit()
        service = await self.start()
        self.clear_requests()

        # OLIM loses a contest, and one of its remaining ones breaks.
        self.contest_a.ranking_group = self.omips
        self.session.commit()
        self.break_contest(contest_a3)
        await service.reinitialize()
        await self._wait_until(
            lambda: self.delete_urls() != [] and self.put_urls() != [])

        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        self.assertEqual(set(self.put_payload(url("olim/contests/"))),
                         {encode_id(contest_a2.name)})
        self.assertEqual(set(self.put_payload(url("olim/submissions/"))),
                         {"%d" % sub_a2.id})
        self.assertIn(encode_id(self.contest_a.name),
                      self.put_payload(url("omips/contests/")))
        self.assertIn("%d" % self.sub_a.id,
                      self.put_payload(url("omips/submissions/")))

        # Live scores of the broken contest are held back too.
        self.clear_requests()
        await service.submission_scored(sub_a3.id)
        await asyncio.sleep(0.1)
        self.assertEqual(self.put_urls(), [])

        # Once the task is fixed, the next reinitialize sends the
        # contest and all of its submissions.
        self.broken_datasets.clear()
        await service.reinitialize()
        await self._wait_until(lambda: self.put_urls() != [])
        self.assertEqual(self.delete_urls(), [])
        self.assertIn(encode_id(contest_a3.name),
                      self.put_payload(url("olim/contests/")))
        self.assertIn("%d" % sub_a3.id,
                      self.put_payload(url("olim/submissions/")))

    async def test_regenerate_skips_only_broken_contest(self):
        contest_a2, sub_a2 = self.add_contest_with_submission(self.olim)
        self.session.commit()
        service = await self.start()
        self.clear_requests()
        self.break_contest(contest_a2)
        await service.regenerate_ranking("olim")
        await self._wait_until(
            lambda: self.delete_urls() != [] and self.put_urls() != [])
        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        self.assertEqual(set(self.put_payload(url("olim/contests/"))),
                         {encode_id(self.contest_a.name)})
        self.assertEqual(set(self.put_payload(url("olim/submissions/"))),
                         {"%d" % self.sub_a.id})

    async def test_regenerate_rejects_invalid_group(self):
        service = await self.start()
        self.clear_requests()
        for group in ["..", "olim/..", "", "users", 1]:
            with self.assertRaises(ValueError):
                await service.regenerate_ranking(group)
        self.assertEqual(self.delete_urls(), [])
        self.assertEqual(self.put_urls(), [])


if __name__ == "__main__":
    unittest.main()
