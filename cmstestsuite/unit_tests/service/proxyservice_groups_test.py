"""Tests for ProxyService in group mode (no contest id)."""

import asyncio
import json
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urljoin

import requests.exceptions
from sqlalchemy import text
from sqlalchemy.orm import object_session

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin
from cmstestsuite.unit_tests.service.proxyexecutor_test import \
    FakeRetryWaits

from cms import config
from cms.conf import Address
from cms.db import RankingGroup
from cms.service.ProxyService import ProxyExecutor, ProxyOperation, \
    ProxyService, encode_id
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

        # An executor waits before sending again what a ranking could
        # not take: skip those waits, and record them.
        self.retry_waits = FakeRetryWaits()
        for name, fake in (("asyncio", self.retry_waits),
                           ("monotonic", self.retry_waits.monotonic)):
            retry_patcher = patch("cms.service.ProxyService." + name, fake)
            retry_patcher.start()
            self.addCleanup(retry_patcher.stop)

        # Count the batches all the executors are sending, for _settle().
        self.executions_in_flight = 0
        real_execute = ProxyExecutor.execute

        async def counting_execute(executor, entries):
            self.executions_in_flight += 1
            try:
                await real_execute(executor, entries)
            finally:
                self.executions_in_flight -= 1

        execute_patcher = patch.object(
            ProxyExecutor, "execute", counting_execute)
        execute_patcher.start()
        self.addCleanup(execute_patcher.stop)

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
        await self._settle(service)
        return service

    async def _settle(self, service: ProxyService, timeout: float = 10.0):
        """Wait until the service has sent everything it enqueued.

        Call it right after awaiting the service method under test.
        The operations that method enqueues from its worker thread reach
        the executors in order, all before the method's own result does,
        but an executor may send them in several batches. So waiting
        for some request to show up is not enough: a later batch would
        land after the test cleared the recorded requests, or after it
        checked them. Empty queues in all the executors (there is one
        for each ranking) with no batch in flight mean the rankings
        have received everything.

        service: the service whose executors to wait for.
        timeout: seconds after which to fail the test.

        """
        executors = service._executors
        deadline = time.monotonic() + timeout
        while (any(executor.get_status() for executor in executors)
               or self.executions_in_flight):
            if time.monotonic() > deadline:
                self.fail("The service did not finish sending its "
                          "operations in %s seconds." % timeout)
            await asyncio.sleep(0.005)

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
        await self._settle(service)
        self.assertEqual(self.put_urls(), [])

    async def test_submission_scored_goes_to_its_group(self):
        service = await self.start()
        self.clear_requests()
        await service.submission_scored(self.sub_b.id)
        await self._settle(service)
        self.assertIn(url("omips/submissions/"), self.put_urls())
        self.assertNotIn(url("olim/submissions/"), self.put_urls())

    async def test_moving_contest_resets_old_group(self):
        service = await self.start()
        self.clear_requests()
        self.contest_a.ranking_group = self.omips
        self.session.commit()
        await service.reinitialize()
        await self._settle(service)
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
        await self._settle(service)
        self.assertNotEqual(self.put_urls(), [])
        self.assertEqual(self.delete_urls(), [])

    async def test_regenerate_group_only_touches_its_namespace(self):
        service = await self.start()
        self.clear_requests()
        await service.regenerate_ranking("olim")
        await self._settle(service)
        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        # Already-sent scores are sent again.
        self.assertIn(url("olim/submissions/"), self.put_urls())
        self.assertFalse(any("omips/" in u for u in self.put_urls()))

    async def test_regenerate_root_in_group_mode_empties_it(self):
        service = await self.start()
        self.clear_requests()
        await service.regenerate_ranking(None)
        await self._settle(service)
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
        await self._settle(service)

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
        await self._settle(service)
        self.assertEqual(self.put_urls(), [])

        # Once the task is fixed, the next reinitialize sends the
        # contest and all of its submissions.
        self.broken_datasets.clear()
        await service.reinitialize()
        await self._settle(service)
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
        await self._settle(service)
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

    async def test_connection_error_is_retried_without_losing_data(self):
        delivered: dict[str, dict] = dict()
        attempts: list[str] = list()

        def flaky_put(target, body, **kwargs):
            attempts.append(target)
            if len(attempts) == 1:
                raise requests.exceptions.ConnectionError("refused")
            delivered.setdefault(target, dict()).update(json.loads(body))
            return self.requests_put.return_value

        self.requests_put.side_effect = flaky_put
        await self.start()

        # The very first request failed, and the executor waited for
        # a moment before sending that data again.
        self.assertEqual(self.retry_waits.waits, [1])
        for group, contest, submission in (
                ("olim", self.contest_a, self.sub_a),
                ("omips", self.contest_b, self.sub_b)):
            self.assertEqual(
                set(delivered[url("%s/contests/" % group)]),
                {encode_id(contest.name)})
            self.assertEqual(
                set(delivered[url("%s/tasks/" % group)]),
                {encode_id(contest.tasks[0].name)})
            self.assertEqual(
                set(delivered[url("%s/submissions/" % group)]),
                {"%d" % submission.id})
            self.assertEqual(
                len(delivered[url("%s/subchanges/" % group)]), 1)

    async def test_rejected_group_does_not_lose_the_others(self):
        service = await self.start()
        self.clear_requests()

        # RWS refuses the submissions of OLIM, all or nothing.
        def put(target, *args, **kwargs):
            response = MagicMock()
            response.status_code = \
                400 if target == url("olim/submissions/") else 200
            return response

        self.requests_put.side_effect = put

        # Both scores are queued together, so they go out in one batch.
        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            for submission in (self.sub_a, self.sub_b):
                for operation in service.operations_for_score(submission):
                    service.enqueue(operation)
            await self._settle(service)

        # OMIPS got its data, and so did the rest of OLIM's.
        self.assertEqual(set(self.put_payload(url("omips/submissions/"))),
                         {"%d" % self.sub_b.id})
        self.assertIn(url("omips/subchanges/"), self.put_urls())
        self.assertIn(url("olim/subchanges/"), self.put_urls())
        # What RWS refused is not sent again.
        self.assertEqual(self.retry_waits.waits, [])
        self.assertEqual(self.put_urls().count(url("olim/submissions/")), 1)
        # The operator learns how to repair OLIM, and only OLIM.
        hints = [line for line in logs.output if "Regenerate" in line]
        self.assertEqual(len(hints), 1)
        self.assertIn("olim", hints[0])
        self.assertIn("submissions", hints[0])

    async def test_sweep_retries_a_broken_contest(self):
        self.break_contest(self.contest_b)
        service = await self.start()
        self.assertEqual(service._broken_contests, {self.contest_b.id})
        self.assertNotIn(url("omips/contests/"), self.put_urls())
        self.clear_requests()

        # Whatever broke it is over, and nobody edits anything in AWS.
        self.broken_datasets.clear()
        await service._missing_operations()
        await self._settle(service)

        # The next sweep sends the contest and the scores held back.
        self.assertEqual(service._broken_contests, set())
        self.assertEqual(set(self.put_payload(url("omips/contests/"))),
                         {encode_id(self.contest_b.name)})
        self.assertEqual(set(self.put_payload(url("omips/submissions/"))),
                         {"%d" % self.sub_b.id})
        self.assertFalse(any("olim/" in u for u in self.put_urls()))

    async def test_contests_broken_by_a_database_error_all_heal(self):
        # A failed statement aborts the PostgreSQL transaction, so the
        # contests that follow in the same initialize() fail as well.
        database_is_failing = [True]
        real_operations = ProxyService._operations_for_contest

        def operations_for_contest(service, contest):
            if database_is_failing[0] and contest.id == self.contest_a.id:
                object_session(contest).execute(text("SELECT 1 / 0"))
            return real_operations(service, contest)

        with patch.object(ProxyService, "_operations_for_contest",
                          operations_for_contest):
            service = await self.start()
            self.assertEqual(
                service._broken_contests,
                {self.contest_a.id, self.contest_b.id})
            # Only the visibility settings of the groups got there.
            self.assertCountEqual(
                self.put_urls(),
                [url("olim/visibility"), url("omips/visibility")])

            database_is_failing[0] = False
            await service._missing_operations()
            await self._settle(service)

        self.assertEqual(service._broken_contests, set())
        for group, contest, submission in (
                ("olim", self.contest_a, self.sub_a),
                ("omips", self.contest_b, self.sub_b)):
            self.assertEqual(
                set(self.put_payload(url("%s/contests/" % group))),
                {encode_id(contest.name)})
            self.assertEqual(
                set(self.put_payload(url("%s/submissions/" % group))),
                {"%d" % submission.id})

    async def test_healed_contest_gets_all_its_scores_again(self):
        service = await self.start()
        # A regenerate empties OMIPS while its contest cannot be built.
        # The score of its submission was sent before, but it is gone.
        self.break_contest(self.contest_b)
        await service.regenerate_ranking("omips")
        await self._settle(service)
        self.assertEqual(self.delete_urls(),
                         [url("omips/contests/"), url("omips/users/")])
        self.assertEqual(service._broken_contests, {self.contest_b.id})
        self.clear_requests()

        self.broken_datasets.clear()
        await service._missing_operations()
        await self._settle(service)

        self.assertEqual(service._broken_contests, set())
        self.assertEqual(set(self.put_payload(url("omips/contests/"))),
                         {encode_id(self.contest_b.name)})
        self.assertEqual(set(self.put_payload(url("omips/submissions/"))),
                         {"%d" % self.sub_b.id})

    async def test_healing_contest_is_held_back_until_its_data_is_queued(
        self,
    ):
        # Scores that arrive meanwhile from another thread must queue
        # up behind the contest and its tasks, or RWS refuses them.
        self.break_contest(self.contest_b)
        service = await self.start()
        self.broken_datasets.clear()

        held_back = list()
        real_enqueue = service._threadsafe_enqueue

        def spy(operation, *args):
            held_back.append(self.contest_b.id in service._broken_contests)
            real_enqueue(operation, *args)

        service._threadsafe_enqueue = spy
        await service._missing_operations()
        await self._settle(service)

        # First the four operations of the contest, then its scores.
        self.assertEqual(held_back[:4], [True] * 4)
        self.assertEqual(held_back[4:], [False] * (len(held_back) - 4))
        self.assertEqual(service._broken_contests, set())

    async def test_sweep_leaves_alone_what_is_already_there(self):
        service = await self.start()
        self.clear_requests()
        self.assertEqual(await service._missing_operations(), 0)
        await self._settle(service)
        self.assertEqual(self.put_urls(), [])

    async def test_broken_contest_logs_a_traceback_only_once(self):
        self.break_contest(self.contest_b)
        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            # It breaks here, and the first sweep tries it again.
            service = await self.start()
            await service._missing_operations()
            await service._missing_operations()
        tracebacks = [r for r in logs.records if r.exc_info]
        retries = [r for r in logs.records if not r.exc_info]
        self.assertEqual(len(tracebacks), 1)
        self.assertEqual(len(retries), 3)
        for record in retries:
            self.assertIn(self.contest_b.name, record.getMessage())
            self.assertIn("No testcase matches", record.getMessage())

        # Once it works again, breaking it is news again.
        self.broken_datasets.clear()
        await service._missing_operations()
        self.assertEqual(service._broken_contests, set())
        self.break_contest(self.contest_b)
        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            await service.reinitialize()
        self.assertEqual(len([r for r in logs.records if r.exc_info]), 1)

    async def check_reinitialize_recovers_from_failure_in(self, name: str):
        """Fail a reinitialize once, in the method called name, and repeat.

        The reinitialize empties OLIM (it loses a contest) before it
        breaks: the next one has to know, and empty OLIM again to fill
        it in full.

        """
        contest_a2, sub_a2 = self.add_contest_with_submission(self.olim)
        self.session.commit()
        service = await self.start()
        old_mapping = {group: set(contests) for group, contests
                       in service._group_contests.items()}
        self.clear_requests()

        self.contest_a.ranking_group = self.omips
        self.session.commit()
        real_method = getattr(service, name)
        failures = [RuntimeError("the database went away")]

        def flaky_method(*args, **kwargs):
            if failures:
                raise failures.pop()
            return real_method(*args, **kwargs)

        setattr(service, name, flaky_method)
        with self.assertRaises(RuntimeError):
            await service.reinitialize()
        await self._settle(service)
        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        self.assertEqual(service._group_contests, old_mapping)

        self.clear_requests()
        await service.reinitialize()
        await self._settle(service)
        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        self.assertEqual(set(self.put_payload(url("olim/contests/"))),
                         {encode_id(contest_a2.name)})
        self.assertEqual(set(self.put_payload(url("olim/submissions/"))),
                         {"%d" % sub_a2.id})
        self.assertIn("%d" % self.sub_a.id,
                      self.put_payload(url("omips/submissions/")))

    async def test_reinitialize_failing_early_is_redone_in_full(self):
        await self.check_reinitialize_recovers_from_failure_in("initialize")

    async def test_reinitialize_failing_while_resending_is_redone_in_full(
        self,
    ):
        await self.check_reinitialize_recovers_from_failure_in(
            "_enqueue_submissions")

    async def test_settle_waits_for_every_executor(self):
        # The service has one executor for each configured ranking.
        second_ranking = "http://rws:secret@localhost:8891/"
        rankings = (RANKING, second_ranking)
        with patch.object(config.proxy_service, "rankings", rankings):
            service = await self.start()
        self.assertEqual(len(service._executors), 2)
        self.clear_requests()

        # Only the second executor has work, and nothing yields to its
        # run loop before _settle() looks at the queues: a _settle()
        # that only watched the first executor would return right away.
        service._executors[1].enqueue(ProxyOperation(
            ProxyExecutor.TEAM_TYPE, {"team": {"name": "Team"}}))
        await self._settle(service)

        for executor in service._executors:
            self.assertEqual(executor.get_status(), [])
        self.assertEqual(self.put_urls(), [urljoin(second_ranking, "teams/")])

    async def test_visibility_is_sent_before_group_data(self):
        service = await self.start()
        await self._settle(service)
        urls = self.put_urls()
        visibility = url("olim/visibility")
        self.assertIn(visibility, urls)
        self.assertLess(urls.index(visibility),
                        urls.index(url("olim/contests/")))
        self.assertEqual(self.put_payload(visibility),
                         {"hidden": False, "staff_password": None})

    async def test_reinitialize_sends_new_visibility(self):
        service = await self.start()
        await self._settle(service)
        self.requests_put.reset_mock()
        self.olim.hidden = True
        self.olim.staff_password = "plaintext:pw"
        self.session.commit()
        await service.reinitialize()
        await self._settle(service)
        self.assertEqual(self.put_payload(url("olim/visibility")),
                         {"hidden": True, "staff_password": "plaintext:pw"})

    async def test_regenerate_sends_visibility(self):
        service = await self.start()
        await self._settle(service)
        self.requests_put.reset_mock()
        await service.regenerate_ranking("olim")
        await self._settle(service)
        self.assertIn(url("olim/visibility"), self.put_urls())

    async def test_legacy_mode_never_sends_visibility(self):
        service = await self.start(contest_id=self.contest_a.id)
        await self._settle(service)
        self.assertFalse(
            any(u.endswith("/visibility") for u in self.put_urls()))

    async def test_rejected_visibility_holds_back_group_data(self):
        def put(target, *args, **kwargs):
            response = MagicMock()
            response.status_code = 400 if target.endswith(
                "olim/visibility") else 200
            return response
        self.requests_put.side_effect = put
        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            service = await self.start()
            await self._settle(service)
        self.assertNotIn(url("olim/contests/"), self.put_urls())
        self.assertTrue(any("rejected the visibility of group olim" in line
                            for line in logs.output))

    def refuse_visibility(self, groups: set[str]):
        """Make the ranking answer 400 to the visibility of groups.

        groups: the groups whose visibility is refused, for as long as
            they are in the set.

        """
        def put(target, *args, **kwargs):
            response = MagicMock()
            response.status_code = 400 if any(
                target == url("%s/visibility" % group)
                for group in groups) else 200
            return response

        self.requests_put.side_effect = put

    async def test_rejected_visibility_on_reinitialize_holds_back_group_data(
        self,
    ):
        service = await self.start()
        self.clear_requests()
        self.refuse_visibility({"olim"})

        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            await service.reinitialize()
            await self._settle(service)
            # Scores that come later go in batches of their own.
            for submission in (self.sub_a, self.sub_b):
                await service.submission_scored(submission.id)
                await self._settle(service)

        # Nothing of OLIM got there but its refused visibility.
        self.assertEqual(
            [u for u in self.put_urls() if u.startswith(url("olim/"))],
            [url("olim/visibility")])
        # OMIPS is not affected.
        for resource in ("visibility", "contests/", "submissions/"):
            self.assertIn(url("omips/" + resource), self.put_urls())
        # The operator is told once, not for each batch held back.
        hints = [line for line in logs.output
                 if "rejected the visibility of group olim" in line]
        self.assertEqual(len(hints), 1)

    async def test_group_data_flows_again_once_its_visibility_is_taken(self):
        service = await self.start()
        refused = {"olim"}
        self.refuse_visibility(refused)
        await service.reinitialize()
        await self._settle(service)

        # RWS is fixed, and the group is saved again in AWS.
        refused.clear()
        self.clear_requests()
        await service.reinitialize()
        await self._settle(service)
        await service.submission_scored(self.sub_a.id)
        await self._settle(service)

        urls = self.put_urls()
        self.assertLess(urls.index(url("olim/visibility")),
                        urls.index(url("olim/contests/")))
        self.assertIn(url("olim/submissions/"), urls)

    async def test_visibility_is_enqueued_after_the_reset_and_before_data(
        self,
    ):
        # The executor only puts the settings first within one batch: the
        # service has to enqueue them first, as another thread enqueues
        # them and batches can split anywhere.
        # OLIM keeps a contest after losing one: reinitialize resets it
        # and fills it again.
        self.add_contest_with_submission(self.olim)
        self.session.commit()
        service = await self.start()
        enqueued: list[ProxyOperation] = list()
        real_enqueue = service._threadsafe_enqueue

        def spy(operation, *args, **kwargs):
            enqueued.append(operation)
            real_enqueue(operation, *args, **kwargs)

        service._threadsafe_enqueue = spy
        names = {ProxyExecutor.RESET_TYPE: "reset",
                 ProxyExecutor.VISIBILITY_TYPE: "visibility"}

        def check_order(group: str, with_reset: bool):
            kinds = [names.get(operation.type_, "data")
                     for operation in enqueued if operation.group == group]
            head = (["reset"] if with_reset else []) + ["visibility"]
            self.assertEqual(kinds[:len(head)], head, group)
            self.assertEqual(set(kinds[len(head):]), {"data"}, group)

        self.contest_a.ranking_group = self.omips
        self.session.commit()
        await service.reinitialize()
        await self._settle(service)
        check_order("olim", with_reset=True)
        check_order("omips", with_reset=False)

        enqueued.clear()
        await service.regenerate_ranking("olim")
        await self._settle(service)
        check_order("olim", with_reset=True)
        self.assertFalse(
            any(operation.group == "omips" for operation in enqueued))


if __name__ == "__main__":
    unittest.main()
