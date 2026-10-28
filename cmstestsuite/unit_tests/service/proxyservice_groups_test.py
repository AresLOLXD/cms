"""Tests for ProxyService in group mode (no contest id)."""

import asyncio
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import urljoin

import requests.exceptions
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, OperationalError, \
    PendingRollbackError, TimeoutError as PoolTimeoutError
from sqlalchemy.orm import object_session

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.testidgenerator import unique_unicode_id
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin
from cmstestsuite.unit_tests.service.proxyexecutor_test import \
    FakeRetryWaits

from cms import config
from cms.conf import Address
from cms.db import Contest, Dataset, RankingGroup, SessionGen
from cms.service.ProxyService import ProxyExecutor, ProxyOperation, \
    ProxyService, RefusedEntity, _is_database_error, encode_id
from cmscommon.constants import SCORE_MODE_MAX
from cmscommon.datetime import make_timestamp


RANKING = config.proxy_service.rankings[0]


def url(resource: str) -> str:
    return urljoin(RANKING, resource)


# What ProxyService sends to a ranking namespace, as "<method> <resource>"
# (see namespace_traffic). The reset of a namespace:
RESET = ["DELETE contests/", "DELETE users/"]
# Its visibility settings:
VISIBILITY = ["PUT visibility"]
# What initialize() sends for its contests: the contests, their users and
# their tasks (the contests of these tests have no teams).
CONTEST_DATA = {"PUT contests/", "PUT users/", "PUT tasks/"}
# What sending the scores of some submissions takes:
SCORES = {"PUT submissions/", "PUT subchanges/"}


class TestIsDatabaseError(unittest.TestCase):

    def test_database_errors_are_told_from_data_errors(self):
        for error, expected in (
            (OperationalError("SELECT 1", {}, Exception("timeout")), True),
            (PendingRollbackError("Can't reconnect"), True),
            (PoolTimeoutError("QueuePool limit reached"), True),
            (ValueError("No testcase matches"), False),
            (AttributeError("no score type"), False),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertIs(_is_database_error(error), expected)


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

    def in_another_thread(self, function, *args):
        """Call function on a thread of its own, as another RPC would."""
        with ThreadPoolExecutor(1) as pool:
            return pool.submit(function, *args).result()

    def add_scored_submission(self, contest):
        """Add and commit a scored submission to the task of contest."""
        task = contest.tasks[0]
        submission = self.add_submission(
            task=task, participation=contest.participations[0])
        result = self.add_submission_result(
            submission=submission, dataset=task.active_dataset)
        result.compilation_outcome = "ok"
        result.evaluation_outcome = "ok"
        result.score = 100
        result.score_details = dict()
        result.public_score = 50
        result.public_score_details = dict()
        result.ranking_score_details = ["100"]
        self.session.commit()
        return submission

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

    async def test_regenerate_enqueues_the_reset_with_the_data_in_one_go(self):
        service = await self.start()
        singles = list()
        batches = list()
        real_enqueue_all = service._threadsafe_enqueue_all

        def spy_all(operations, *args, **kwargs):
            batches.append(list(operations))
            real_enqueue_all(operations, *args, **kwargs)

        service._threadsafe_enqueue = \
            lambda *args, **kwargs: singles.append(args)
        service._threadsafe_enqueue_all = spy_all
        await service.regenerate_ranking("olim")
        await self._settle(service)

        # One callback of the loop: nothing queued by another thread
        # can come between the reset and the data it was built for.
        self.assertEqual(singles, [])
        self.assertEqual(len(batches), 1)
        types = [operation.type_ for operation in batches[0]]
        self.assertEqual(types[:2], [ProxyExecutor.RESET_TYPE,
                                     ProxyExecutor.VISIBILITY_TYPE])
        self.assertIn(ProxyExecutor.CONTEST_TYPE, types)
        self.assertEqual(types[-2:], [ProxyExecutor.SUBMISSION_TYPE,
                                      ProxyExecutor.SUBCHANGE_TYPE])
        self.assertEqual({operation.group for operation in batches[0]},
                         {"olim"})

    async def test_regenerate_failing_to_read_leaves_the_ranking_alone(self):
        contest_a2, _ = self.add_contest_with_submission(self.olim)
        contest_a3, _ = self.add_contest_with_submission(self.olim)
        self.session.commit()
        # The second contest is held back at the start, and works again
        # when the regenerate reads it: the build marks its scores as
        # sent, and the third contest hits the database error.
        self.break_contest(contest_a2)
        service = await self.start()
        self.broken_datasets.clear()
        service.scores_sent_to_rankings.discard(self.sub_a.id)
        scores = set(service.scores_sent_to_rankings)
        tokens = set(service.tokens_sent_to_rankings)
        database_is_failing = self.fail_the_database_at(contest_a3)
        self.clear_requests()

        with self.assertLogs("cms.service.ProxyService", "ERROR") as logs:
            with self.assertRaises(DBAPIError):
                await service.regenerate_ranking("olim")
        await self._settle(service)

        # The scoreboard is as it was, not empty.
        self.assertEqual(self.delete_urls(), [])
        self.assertEqual(self.put_urls(), [])
        # The one held back before stays so, and so does the one that
        # hit the error: the sweep sends it in full once it can.
        self.assertEqual(service._broken_contests,
                         {contest_a2.id, contest_a3.id})
        self.assertEqual(service.scores_sent_to_rankings, scores)
        self.assertEqual(service.tokens_sent_to_rankings, tokens)
        failure = [r for r in logs.records
                   if r.getMessage().startswith("Regenerate")]
        self.assertEqual(len(failure), 1)
        self.assertFalse(failure[0].exc_info)
        self.assertIn("olim", failure[0].getMessage())
        self.assertIn("division by zero", failure[0].getMessage())

        # Once the database answers, the sweep sends the score the
        # build had marked, and the contests held back in full.
        database_is_failing[0] = False
        await service._missing_operations()
        await self._settle(service)
        self.assertEqual(service._broken_contests, set())
        self.assertIn("%d" % self.sub_a.id,
                      self.put_payload(url("olim/submissions/")))

    async def test_regenerate_resends_a_score_its_reset_could_wipe(self):
        contest_a2, sub_a2 = self.add_contest_with_submission(self.olim)
        self.session.commit()
        service = await self.start()
        # The build itself marks this score as sent: it must stay so.
        service.scores_sent_to_rankings.discard(sub_a2.id)
        scored = list()
        real_operations = ProxyService._operations_for_contest

        def operations_for_contest(service_, contest):
            # While regenerate reads its second contest, another thread
            # scores a submission of the first one, which it has read
            # already: the score goes out before the reset, and is not
            # in what the reset is followed by.
            if contest.id == contest_a2.id and not scored:
                scored.append(self.add_scored_submission(self.contest_a))
                self.in_another_thread(
                    service_._submission_scored_sync, scored[0].id)
            return real_operations(service_, contest)

        self.clear_requests()
        with patch.object(ProxyService, "_operations_for_contest",
                          operations_for_contest):
            await service.regenerate_ranking("olim")
        await self._settle(service)
        new_id = scored[0].id
        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        self.assertIn(sub_a2.id, service.scores_sent_to_rankings)
        self.assertNotIn(new_id, service.scores_sent_to_rankings)

        # The sweep sends it again, as the ranking lost it.
        self.clear_requests()
        await service._missing_operations()
        await self._settle(service)
        self.assertEqual(set(self.put_payload(url("olim/submissions/"))),
                         {"%d" % new_id})

    async def test_regenerate_resends_a_score_changed_while_it_reads(self):
        contest_a2, _ = self.add_contest_with_submission(self.olim)
        self.session.commit()
        service = await self.start()
        real_operations = ProxyService._operations_for_contest
        rescored = list()

        def operations_for_contest(service_, contest):
            # The build has read the score of the first contest (100)
            # when another thread rescores that submission: the new
            # score is queued ahead of the reset, and the build still
            # sends the old one. The submission was in the sent set
            # already, so only what threads pass through
            # operations_for_score tells.
            if contest.id == contest_a2.id and not rescored:
                rescored.append(1)
                self.sub_a.get_result().score = 30
                self.session.commit()
                self.in_another_thread(
                    service_._submission_scored_sync, self.sub_a.id)
            return real_operations(service_, contest)

        self.clear_requests()
        with patch.object(ProxyService, "_operations_for_contest",
                          operations_for_contest):
            await service.regenerate_ranking("olim")
        await self._settle(service)
        self.assertNotIn(self.sub_a.id, service.scores_sent_to_rankings)

        # The sweep sends the new score after the old one.
        self.clear_requests()
        await service._missing_operations()
        await self._settle(service)
        subchanges = self.put_payload(url("olim/subchanges/"))
        self.assertEqual(
            [value["score"] for value in subchanges.values()
             if value["submission"] == "%d" % self.sub_a.id], [30])

    async def test_regenerate_marks_its_group_for_the_next_sweep(self):
        # Contest data that a reinitialize queues while the build reads
        # is wiped by the reset, and the build may have read it before.
        service = await self.start()
        await service.regenerate_ranking("olim")
        await self._settle(service)
        self.assertEqual(service._groups_to_repair, {"olim"})
        self.clear_requests()
        await service._missing_operations()
        await self._settle(service)
        self.assertEqual(set(self.put_payload(url("olim/contests/"))),
                         {encode_id(self.contest_a.name)})

    async def test_regenerate_looks_at_other_threads_after_scheduling_reset(
        self,
    ):
        service = await self.start()
        scored = list()
        real_enqueue_all = service._threadsafe_enqueue_all

        def enqueue_all(operations):
            # Another thread scores a submission after the build read
            # its contest, right before the reset is scheduled: the
            # score is queued ahead of it.
            scored.append(self.add_scored_submission(self.contest_a))
            self.in_another_thread(
                service._submission_scored_sync, scored[0].id)
            real_enqueue_all(operations)

        service._threadsafe_enqueue_all = enqueue_all
        await service.regenerate_ranking("olim")
        await self._settle(service)
        self.assertNotIn(scored[0].id, service.scores_sent_to_rankings)

    async def test_regenerates_of_two_groups_run_one_at_a_time(self):
        service = await self.start()
        guard = threading.Lock()
        inside = [0, 0]
        real_operations = ProxyService._operations_for_contest

        def slow_operations(service_, contest):
            with guard:
                inside[0] += 1
                inside[1] = max(inside)
            time.sleep(0.2)
            try:
                return real_operations(service_, contest)
            finally:
                with guard:
                    inside[0] -= 1

        self.clear_requests()
        with patch.object(ProxyService, "_operations_for_contest",
                          slow_operations):
            await asyncio.gather(service.regenerate_ranking("olim"),
                                 service.regenerate_ranking("omips"))
        await self._settle(service)
        self.assertEqual(inside[1], 1)
        self.assertCountEqual(
            self.delete_urls(),
            [url("olim/contests/"), url("olim/users/"),
             url("omips/contests/"), url("omips/users/")])

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
        # The operator learns what became of OLIM's, and only OLIM's.
        hints = [line for line in logs.output if " refused " in line]
        self.assertEqual(len(hints), 1)
        self.assertIn("refused 1 of 1 submissions of group olim", hints[0])
        self.assertIn("sent again after the contest data", hints[0])
        # Regenerate is not needed for it.
        self.assertFalse(any("Regenerate" in line for line in logs.output))

    async def test_refused_scores_leave_the_sent_sets(self):
        service = await self.start()
        service.scores_sent_to_rankings.update({7, 8, 9})
        service.tokens_sent_to_rankings.update({7, 8, 9})

        service._on_refused([
            RefusedEntity("olim", ProxyExecutor.SUBMISSION_TYPE, "7",
                          {"user": "bob", "task": "sum", "time": 0}),
            RefusedEntity("olim", ProxyExecutor.SUBCHANGE_TYPE, "08s",
                          {"submission": "8", "time": 0, "score": 0.0,
                           "extra": []}),
            RefusedEntity("olim", ProxyExecutor.SUBCHANGE_TYPE, "09t",
                          {"submission": "9", "time": 0, "token": True})])

        # A submission and a score subchange are scores, a token
        # subchange is a token: nothing else leaves the sets.
        self.assertNotIn(7, service.scores_sent_to_rankings)
        self.assertNotIn(8, service.scores_sent_to_rankings)
        self.assertIn(9, service.scores_sent_to_rankings)
        self.assertNotIn(9, service.tokens_sent_to_rankings)
        self.assertIn(7, service.tokens_sent_to_rankings)
        self.assertIn(8, service.tokens_sent_to_rankings)
        self.assertEqual(service._groups_to_repair, {"olim"})

    async def test_refused_entities_are_read_as_the_builders_make_them(self):
        self.add_token(submission=self.sub_a)
        self.session.commit()
        service = await self.start()

        submission, score = service.operations_for_score(self.sub_a)
        _, token = service.operations_for_token(self.sub_a)
        [(score_key, score_data)] = score.data.items()
        [(token_key, token_data)] = token.data.items()
        self.assertEqual(list(submission.data), ["%d" % self.sub_a.id])
        self.assertTrue(score_key.endswith("s"))
        self.assertTrue(token_key.endswith("t"))
        self.assertEqual(score_data["submission"], "%d" % self.sub_a.id)
        self.assertEqual(token_data["submission"], "%d" % self.sub_a.id)

        service._on_refused([RefusedEntity(
            "olim", ProxyExecutor.SUBCHANGE_TYPE, token_key, token_data)])
        self.assertNotIn(self.sub_a.id, service.tokens_sent_to_rankings)
        self.assertIn(self.sub_a.id, service.scores_sent_to_rankings)
        service._on_refused([RefusedEntity(
            "olim", ProxyExecutor.SUBCHANGE_TYPE, score_key, score_data)])
        self.assertNotIn(self.sub_a.id, service.scores_sent_to_rankings)

    async def test_refused_contest_data_only_marks_the_group(self):
        service = await self.start()
        scores = set(service.scores_sent_to_rankings)
        tokens = set(service.tokens_sent_to_rankings)
        self.assertEqual(scores, {self.sub_a.id, self.sub_b.id})

        service._on_refused([RefusedEntity(
            "omips", ProxyExecutor.USER_TYPE, "bob",
            {"f_name": "Bob", "l_name": "", "team": "nowhere"})])

        self.assertEqual(service.scores_sent_to_rankings, scores)
        self.assertEqual(service.tokens_sent_to_rankings, tokens)
        self.assertEqual(service._groups_to_repair, {"omips"})

    async def test_the_sweep_repairs_a_group_once(self):
        # OLIM gets two more contests, one of them broken.
        contest_d, _ = self.add_contest_with_submission(self.olim)
        contest_e, _ = self.add_contest_with_submission(self.olim)
        self.session.commit()
        self.break_contest(contest_e)
        service = await self.start()

        calls: list[tuple] = []
        real_contest_data = service._enqueue_contest_data
        real_submissions = service._enqueue_submissions

        def contest_data(contest):
            calls.append(("contest data", contest.id))
            return real_contest_data(contest)

        def submissions(session, contest, only_missing):
            calls.append(("submissions", contest.id, only_missing))
            return real_submissions(session, contest, only_missing)

        service._enqueue_contest_data = contest_data
        service._enqueue_submissions = submissions
        service._groups_to_repair.add("olim")

        await service._missing_operations()
        await self._settle(service)

        # The contest data of each contest of OLIM, then its missing
        # scores; the broken one, once, as each sweep tries it again.
        a, b, d, e = (self.contest_a.id, self.contest_b.id, contest_d.id,
                      contest_e.id)
        self.assertEqual(calls, [
            ("contest data", a), ("submissions", a, True),
            ("submissions", b, True),
            ("contest data", d), ("submissions", d, True),
            ("contest data", e)])
        self.assertEqual(service._groups_to_repair, set())

        # The next sweep only tries the broken contest again.
        calls.clear()
        await service._missing_operations()
        await self._settle(service)
        self.assertEqual(calls, [
            ("submissions", a, True), ("submissions", b, True),
            ("submissions", d, True), ("contest data", e)])

    async def test_a_failed_sweep_keeps_the_groups_to_repair(self):
        service = await self.start()
        service._groups_to_repair.add("olim")

        def broken_sweep(session):
            # The ranking refuses a user of OMIPS while the sweep runs:
            # putting OLIM back must not lose that mark.
            service._on_refused([RefusedEntity(
                "omips", ProxyExecutor.USER_TYPE, "bob",
                {"f_name": "Bob", "l_name": "", "team": "nowhere"})])
            raise RuntimeError("database is down")

        with patch.object(service, "_contests_to_send",
                          side_effect=broken_sweep):
            with self.assertRaisesRegex(RuntimeError, "database is down"):
                await service._missing_operations()
        await self._settle(service)

        self.assertEqual(service._groups_to_repair, {"olim", "omips"})

    async def test_a_refusal_during_a_sweep_is_repaired_by_the_next_one(self):
        service = await self.start()
        repaired: list[int] = []
        real_contest_data = service._enqueue_contest_data
        real_submissions = service._enqueue_submissions

        def contest_data(contest):
            repaired.append(contest.id)
            return real_contest_data(contest)

        # The ranking refuses a user of OMIPS while the sweep runs.
        def submissions(session, contest, only_missing):
            if contest.id == self.contest_a.id:
                service._on_refused([RefusedEntity(
                    "omips", ProxyExecutor.USER_TYPE, "bob",
                    {"f_name": "Bob", "l_name": "", "team": "nowhere"})])
            return real_submissions(session, contest, only_missing)

        service._enqueue_contest_data = contest_data
        service._enqueue_submissions = submissions
        await service._missing_operations()
        await self._settle(service)
        self.assertEqual(repaired, [])
        self.assertEqual(service._groups_to_repair, {"omips"})

        service._enqueue_submissions = real_submissions
        await service._missing_operations()
        await self._settle(service)
        self.assertEqual(repaired, [self.contest_b.id])
        self.assertEqual(service._groups_to_repair, set())

    async def test_legacy_mode_repairs_the_root(self):
        service = await self.start(self.contest_c.id)
        refusals = [400]

        # The ranking refuses the score once.
        def put(target, *args, **kwargs):
            response = MagicMock()
            response.status_code = refusals.pop() \
                if target == url("submissions/") and refusals else 200
            return response

        self.requests_put.side_effect = put
        with self.assertLogs("cms.service.ProxyService", "WARNING"):
            for operation in service.operations_for_score(self.sub_c):
                service.enqueue(operation)
            await self._settle(service)
        self.assertEqual(service._groups_to_repair, {None})
        self.clear_requests()

        await service._missing_operations()
        await self._settle(service)

        self.assertEqual(set(self.put_payload(url("contests/"))),
                         {encode_id(self.contest_c.name)})
        self.assertEqual(set(self.put_payload(url("submissions/"))),
                         {"%d" % self.sub_c.id})

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
        # Of OLIM, only its visibility settings, which each sweep sends.
        self.assertEqual([u for u in self.put_urls() if "olim/" in u],
                         [url("olim/visibility")])

    def fail_the_database_at(self, contest) -> list[bool]:
        """Make reading the data of contest abort its transaction.

        A failed statement aborts the PostgreSQL transaction, so a
        session that went on would fail for every contest after it.

        return: a one-item list, the switch: set it to False to heal.

        """
        database_is_failing = [True]
        real_operations = ProxyService._operations_for_contest

        def operations_for_contest(service, other):
            if database_is_failing[0] and other.id == contest.id:
                object_session(other).execute(text("SELECT 1 / 0"))
            return real_operations(service, other)

        patcher = patch.object(ProxyService, "_operations_for_contest",
                               operations_for_contest)
        patcher.start()
        self.addCleanup(patcher.stop)
        return database_is_failing

    async def test_contests_broken_by_a_database_error_all_heal(self):
        service = await self.start()
        self.clear_requests()
        database_is_failing = self.fail_the_database_at(self.contest_a)

        # The reinitialize stops at the first contest: going on would
        # fail for the others, on the aborted transaction. The contests
        # not read yet are held, as the first one.
        with self.assertLogs("cms.service.ProxyService", "INFO") as logs:
            with self.assertRaises(DBAPIError):
                await service.reinitialize()
        await self._settle(service)
        self.assertEqual(service._broken_contests,
                         {self.contest_a.id, self.contest_b.id})
        # Only the visibility settings of the groups got there.
        self.assertEqual(
            set(self.put_urls()),
            {url("olim/visibility"), url("omips/visibility")})
        warnings = [r for r in logs.records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 1)
        self.assertIsNone(warnings[0].exc_info)
        self.assertIn("Database error", warnings[0].getMessage())
        self.assertIn(self.contest_a.name, warnings[0].getMessage())
        self.assertIn("division by zero", warnings[0].getMessage())
        self.assertFalse(any("Cannot build" in r.getMessage()
                             for r in logs.records))

        # The sweep fails the same way while it lasts, and it keeps
        # them held.
        with self.assertLogs("cms.service.ProxyService", "WARNING"):
            with self.assertRaises(DBAPIError):
                await service._missing_operations()
        self.assertEqual(service._broken_contests,
                         {self.contest_a.id, self.contest_b.id})

        database_is_failing[0] = False
        self.clear_requests()
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

    async def test_a_database_error_at_startup_stops_the_service(self):
        # Like the unguarded SessionGen before it: supervisord starts
        # the service again, and a restart starts clean.
        self.fail_the_database_at(self.contest_b)
        with self.assertRaises(DBAPIError):
            await self.start()

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
            # The visibility settings go first, whatever the contests.
            if operation.type_ != ProxyExecutor.VISIBILITY_TYPE:
                held_back.append(
                    self.contest_b.id in service._broken_contests)
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
        # Only the visibility settings are sent again, at every sweep.
        self.assertCountEqual(
            self.put_urls(),
            [url("olim/visibility"), url("omips/visibility")])

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
        with self.assertLogs("cms.service.ProxyService", "ERROR") as logs:
            with self.assertRaises(RuntimeError):
                await service.reinitialize()
        await self._settle(service)
        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        self.assertEqual(service._group_contests, old_mapping)
        # Nobody else logs it: the RPC server does not, and AWS stops
        # waiting for the answer. The operator learns what to repair.
        self.assertEqual(len(logs.records), 1)
        self.assertIsNotNone(logs.records[0].exc_info)
        self.assertIn("olim", logs.output[0])
        self.assertIn("Regenerate", logs.output[0])

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

    async def test_reinitialize_failing_without_lost_groups_is_logged(self):
        service = await self.start()

        def failing_initialize():
            raise RuntimeError("the database went away")

        service.initialize = failing_initialize
        with self.assertLogs("cms.service.ProxyService", "ERROR") as logs:
            with self.assertRaises(RuntimeError):
                await service.reinitialize()
        self.assertEqual(len(logs.records), 1)
        self.assertIsNotNone(logs.records[0].exc_info)
        # No group was being reset: there is nothing to regenerate.
        self.assertNotIn("Regenerate", logs.output[0])

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
                         {"hide_at": None, "show_at": None,
                          "freeze_at": None, "unfreeze_at": None,
                          "staff_password": None})

    async def test_visibility_windows_are_sent(self):
        self.olim.freeze_at = datetime(2026, 10, 10, 19, 0)
        self.session.commit()
        service = await self.start()
        await self._settle(service)
        expected_freeze_at = int(make_timestamp(datetime(2026, 10, 10, 19, 0)))
        payload = self.put_payload(url("olim/visibility"))
        self.assertEqual(payload,
                         {"hide_at": None, "show_at": None,
                          "freeze_at": expected_freeze_at, "unfreeze_at": None,
                          "staff_password": None})
        self.assertIsInstance(payload["freeze_at"], int)

    async def test_freeze_and_unfreeze_in_the_same_second_stay_ordered(self):
        # The "freeze now" and "unfreeze now" buttons store microsecond
        # times: a double click makes both fall in the same second, and
        # the ranking answers 400 to an end that is not after its start.
        self.olim.freeze_at = datetime(2026, 10, 10, 19, 0, 0, 300000)
        self.olim.unfreeze_at = datetime(2026, 10, 10, 19, 0, 0, 800000)
        self.session.commit()
        service = await self.start()
        await self._settle(service)
        second = int(make_timestamp(datetime(2026, 10, 10, 19, 0)))
        payload = self.put_payload(url("olim/visibility"))
        self.assertEqual(payload["freeze_at"], second)
        self.assertEqual(payload["unfreeze_at"], second + 1)
        self.assertGreater(payload["unfreeze_at"], payload["freeze_at"])
        self.assertIsInstance(payload["freeze_at"], int)
        self.assertIsInstance(payload["unfreeze_at"], int)

    async def test_hide_and_show_in_the_same_second_stay_ordered(self):
        self.olim.hide_at = datetime(2026, 10, 10, 19, 0, 0, 300000)
        self.olim.show_at = datetime(2026, 10, 10, 19, 0, 0, 800000)
        self.session.commit()
        service = await self.start()
        await self._settle(service)
        second = int(make_timestamp(datetime(2026, 10, 10, 19, 0)))
        payload = self.put_payload(url("olim/visibility"))
        self.assertEqual(payload["hide_at"], second)
        self.assertEqual(payload["show_at"], second + 1)
        self.assertGreater(payload["show_at"], payload["hide_at"])
        self.assertIsInstance(payload["hide_at"], int)
        self.assertIsInstance(payload["show_at"], int)

    async def test_ends_on_a_whole_second_are_not_widened(self):
        # Only a fractional end is rounded up: one that is already on a
        # whole second stays that second, whatever its start is.
        self.olim.hide_at = datetime(2026, 10, 10, 19, 0, 0, 500000)
        self.olim.show_at = datetime(2026, 10, 10, 19, 0, 1)
        self.olim.freeze_at = datetime(2026, 10, 10, 20, 0)
        self.olim.unfreeze_at = datetime(2026, 10, 10, 20, 0, 2)
        self.session.commit()
        service = await self.start()
        await self._settle(service)
        hide = int(make_timestamp(datetime(2026, 10, 10, 19, 0)))
        freeze = int(make_timestamp(datetime(2026, 10, 10, 20, 0)))
        self.assertEqual(self.put_payload(url("olim/visibility")),
                         {"hide_at": hide, "show_at": hide + 1,
                          "freeze_at": freeze, "unfreeze_at": freeze + 2,
                          "staff_password": None})

    async def test_reinitialize_sends_new_visibility(self):
        service = await self.start()
        await self._settle(service)
        self.requests_put.reset_mock()
        self.olim.hidden = True
        self.olim.hide_at = datetime(2026, 1, 1)
        self.olim.staff_password = "plaintext:pw"
        self.session.commit()
        await service.reinitialize()
        await self._settle(service)
        expected_hide_at = int(make_timestamp(datetime(2026, 1, 1)))
        self.assertEqual(self.put_payload(url("olim/visibility")),
                         {"hide_at": expected_hide_at, "show_at": None,
                          "freeze_at": None, "unfreeze_at": None,
                          "staff_password": "plaintext:pw"})

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
        real_enqueue_all = service._threadsafe_enqueue_all

        def spy_all(operations, *args, **kwargs):
            enqueued.extend(operations)
            real_enqueue_all(operations, *args, **kwargs)

        service._threadsafe_enqueue_all = spy_all
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

    async def test_sweep_sends_the_visibility_of_every_group(self):
        service = await self.start()
        self.clear_requests()
        # Changed in the database, and no reinitialize tells ProxyService
        # (lost while it restarted, say).
        self.omips.hidden = True
        self.omips.hide_at = datetime(2026, 1, 1)
        self.omips.staff_password = "plaintext:pw"
        self.session.commit()

        await service._missing_operations()
        await self._settle(service)

        self.assertEqual(self.put_payload(url("olim/visibility")),
                         {"hide_at": None, "show_at": None,
                          "freeze_at": None, "unfreeze_at": None,
                          "staff_password": None})
        expected_hide_at = int(make_timestamp(datetime(2026, 1, 1)))
        self.assertEqual(self.put_payload(url("omips/visibility")),
                         {"hide_at": expected_hide_at, "show_at": None,
                          "freeze_at": None, "unfreeze_at": None,
                          "staff_password": "plaintext:pw"})

    async def test_sweep_in_legacy_mode_sends_no_visibility(self):
        service = await self.start(contest_id=self.contest_a.id)
        self.clear_requests()
        await service._missing_operations()
        await self._settle(service)
        self.assertFalse(
            any(u.endswith("/visibility") for u in self.put_urls()))

    async def test_group_whose_visibility_was_rejected_recovers_by_sweep(
        self,
    ):
        refused = {"olim"}
        self.refuse_visibility(refused)
        service = await self.start()
        # RWS is fixed, and nobody saves the group in AWS again.
        refused.clear()
        self.clear_requests()

        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            await service._missing_operations()
            await self._settle(service)
        await service.submission_scored(self.sub_a.id)
        await self._settle(service)

        self.assertIn(url("olim/visibility"), self.put_urls())
        self.assertIn(url("olim/submissions/"), self.put_urls())
        self.assertTrue(any("accepted the visibility of group olim" in line
                            for line in logs.output))

    async def test_sweep_hides_a_group_before_sending_its_data(self):
        service = await self.start()
        batches: list[list[tuple[int, str | None]]] = []
        executor = service._executors[0]
        real_execute = executor.execute

        async def recording_execute(entries):
            batches.append([(e.item.type_, e.item.group) for e in entries])
            await real_execute(entries)

        executor.execute = recording_execute
        # Batches may split anywhere when the sweep runs in its thread:
        # it has to queue the settings first, too.
        enqueued: list[ProxyOperation] = list()
        real_enqueue = service._threadsafe_enqueue

        def spy(operation, *args, **kwargs):
            enqueued.append(operation)
            real_enqueue(operation, *args, **kwargs)

        service._threadsafe_enqueue = spy
        self.clear_requests()
        # OLIM is hidden in AWS and a score of it was missed: only the
        # sweep tells the rankings.
        self.olim.hidden = True
        self.olim.hide_at = datetime(2026, 1, 1)
        self.session.commit()
        service.scores_sent_to_rankings.discard(self.sub_a.id)

        # Run on the event loop, the sweep queues all of its operations
        # before the executor takes any: they go in a single batch. They
        # reach the queue once the loop runs, which the yield lets it do.
        service._missing_operations_sync()
        await asyncio.sleep(0)
        await self._settle(service)

        self.assertEqual(
            [operation.type_ for operation in enqueued
             if operation.group == "olim"],
            [ProxyExecutor.VISIBILITY_TYPE, ProxyExecutor.SUBMISSION_TYPE,
             ProxyExecutor.SUBCHANGE_TYPE])
        self.assertEqual(len(batches), 1)
        self.assertIn((ProxyExecutor.VISIBILITY_TYPE, "olim"), batches[0])
        self.assertIn((ProxyExecutor.SUBMISSION_TYPE, "olim"), batches[0])
        urls = self.put_urls()
        self.assertLess(urls.index(url("olim/visibility")),
                        urls.index(url("olim/submissions/")))
        expected_hide_at = int(make_timestamp(datetime(2026, 1, 1)))
        self.assertEqual(self.put_payload(url("olim/visibility")),
                         {"hide_at": expected_hide_at, "show_at": None,
                          "freeze_at": None, "unfreeze_at": None,
                          "staff_password": None})


    # -- what each transition sends to which namespace -------------------

    def record_requests(self) -> list[tuple[str, str]]:
        """Record, in order, every request made to the rankings from now on.

        The two mocks of the requests keep their own calls apart: this is
        the only way to see a reset (a DELETE) between the PUTs.

        return: the list the (method, URL) of each request is added to.

        """
        log: list[tuple[str, str]] = []

        def recording(method: str, response: MagicMock):
            def request(target, *args, **kwargs):
                log.append((method, target))
                return response
            return request

        self.requests_put.side_effect = recording(
            "PUT", self.requests_put.return_value)
        self.requests_delete.side_effect = recording(
            "DELETE", self.requests_delete.return_value)
        return log

    @staticmethod
    def namespace_traffic(
        log: list[tuple[str, str]]
    ) -> dict[str | None, list[str]]:
        """Split the recorded requests by ranking namespace.

        log: the requests, as record_requests() returns them.

        return: for each namespace (None is the root), what was sent to
            it, as "<method> <resource>", in the order it was sent.

        """
        traffic: dict[str | None, list[str]] = dict()
        for method, target in log:
            resource = target[len(RANKING):]
            head, _, rest = resource.partition("/")
            if head in ProxyExecutor.RESOURCE_PATHS and rest == "":
                group, path = None, resource
            else:
                group, path = head, rest
            traffic.setdefault(group, list()).append(
                "%s %s" % (method, path))
        return traffic

    def assert_traffic(
        self,
        log: list[tuple[str, str]],
        expected: dict[str | None, tuple[bool, bool, set[str]]],
    ):
        """Check which namespaces got what, and in which order.

        A namespace is first reset, if it is, then given its visibility
        settings, if it is, and only then gets data: no other request
        may come before them, nor be repeated. The data of a namespace
        is compared as a set, since the executor may send it in several
        batches, and so in several orders.

        log: the requests, as record_requests() returns them.
        expected: for each namespace that got something (and only
            for them): whether it was reset, whether it got its
            visibility settings, and the set of the data it got.

        """
        traffic = self.namespace_traffic(log)
        self.assertEqual(set(traffic), set(expected))
        for group, (reset, visibility, data) in expected.items():
            head = ((RESET if reset else [])
                    + (VISIBILITY if visibility else []))
            self.assertEqual(traffic[group][:len(head)], head, group)
            self.assertEqual(set(traffic[group][len(head):]), data, group)

    def payload_keys(self, resource: str) -> set[str]:
        """Return the ids sent to a resource since the last clear."""
        return set(self.put_payload(url(resource)))

    async def test_contest_joining_a_group_and_leaving_it_again(self):
        service = await self.start()
        self.clear_requests()
        log = self.record_requests()

        # It joins OLIM: nothing is emptied, its data goes to OLIM along
        # with the data of the others (what a reinitialize sends), and
        # only its own scores do, as the group kept the other ones.
        self.contest_c.ranking_group = self.olim
        self.session.commit()
        await service.reinitialize()
        await self._settle(service)

        self.assert_traffic(log, {
            "olim": (False, True, CONTEST_DATA | SCORES),
            "omips": (False, True, CONTEST_DATA)})
        self.assertEqual(
            self.payload_keys("olim/contests/"),
            {encode_id(self.contest_a.name), encode_id(self.contest_c.name)})
        self.assertEqual(self.payload_keys("olim/submissions/"),
                         {"%d" % self.sub_c.id})
        self.assertEqual(self.payload_keys("omips/contests/"),
                         {encode_id(self.contest_b.name)})
        self.assertEqual(service._group_contests, {
            "olim": {self.contest_a.id, self.contest_c.id},
            "omips": {self.contest_b.id}})

        # It leaves OLIM again: the ranking cannot delete one contest, so
        # OLIM is emptied and filled without it, scores of the contest
        # that stays included. OMIPS is not touched but for what a
        # reinitialize always sends.
        self.clear_requests()
        log.clear()
        self.contest_c.ranking_group = None
        self.session.commit()
        await service.reinitialize()
        await self._settle(service)

        self.assert_traffic(log, {
            "olim": (True, True, CONTEST_DATA | SCORES),
            "omips": (False, True, CONTEST_DATA)})
        self.assertEqual(self.payload_keys("olim/contests/"),
                         {encode_id(self.contest_a.name)})
        self.assertEqual(self.payload_keys("olim/submissions/"),
                         {"%d" % self.sub_a.id})
        self.assertEqual(self.payload_keys("omips/contests/"),
                         {encode_id(self.contest_b.name)})
        self.assertEqual(service._group_contests, {
            "olim": {self.contest_a.id}, "omips": {self.contest_b.id}})

    async def test_group_left_by_its_last_contest_is_emptied_and_sent_nothing(
        self,
    ):
        service = await self.start()
        self.clear_requests()
        log = self.record_requests()

        self.contest_a.ranking_group = None
        self.session.commit()
        await service.reinitialize()
        await self._settle(service)

        # OLIM still exists, so it keeps its visibility settings, but it
        # has no data to send.
        self.assert_traffic(log, {
            "olim": (True, True, set()),
            "omips": (False, True, CONTEST_DATA)})
        self.assertEqual(service._group_contests,
                         {"omips": {self.contest_b.id}})

        # And it fills again, without a reset, when a contest returns.
        self.clear_requests()
        log.clear()
        self.contest_a.ranking_group = self.olim
        self.session.commit()
        await service.reinitialize()
        await self._settle(service)

        self.assert_traffic(log, {
            "olim": (False, True, CONTEST_DATA | SCORES),
            "omips": (False, True, CONTEST_DATA)})
        self.assertEqual(self.payload_keys("olim/submissions/"),
                         {"%d" % self.sub_a.id})

    async def test_deleted_group_is_reset_and_gets_no_visibility(self):
        service = await self.start()
        self.clear_requests()
        log = self.record_requests()

        # The contests of the group are left without one (the foreign
        # key sets it to NULL).
        self.session.delete(self.olim)
        self.session.commit()
        await service.reinitialize()
        await self._settle(service)

        # Its namespace is emptied and nothing more: there are no
        # settings to send for a group that is not there.
        self.assert_traffic(log, {
            "olim": (True, False, set()),
            "omips": (False, True, CONTEST_DATA)})
        self.assertEqual(service._group_contests,
                         {"omips": {self.contest_b.id}})

    async def test_renamed_group_resets_the_old_name_and_fills_the_new_one(
        self,
    ):
        service = await self.start()
        self.clear_requests()
        log = self.record_requests()

        self.olim.name = "olim2"
        self.session.commit()
        await service.reinitialize()
        await self._settle(service)

        # For the ranking these are two groups: the old namespace is
        # emptied for good, the new one is sent in full, scores included.
        self.assert_traffic(log, {
            "olim": (True, False, set()),
            "olim2": (False, True, CONTEST_DATA | SCORES),
            "omips": (False, True, CONTEST_DATA)})
        self.assertEqual(self.payload_keys("olim2/contests/"),
                         {encode_id(self.contest_a.name)})
        self.assertEqual(self.payload_keys("olim2/submissions/"),
                         {"%d" % self.sub_a.id})
        self.assertEqual(service._group_contests, {
            "olim2": {self.contest_a.id}, "omips": {self.contest_b.id}})

    async def test_legacy_reinitialize_sends_only_the_root_whatever_the_groups(
        self,
    ):
        # The contest served in legacy mode belongs to a group, which
        # this mode ignores.
        service = await self.start(self.contest_a.id)
        self.assertEqual(service._group_contests, {})
        self.clear_requests()
        log = self.record_requests()

        await service.reinitialize()
        await self._settle(service)

        # No reset, no visibility settings, and no scores: only what
        # initialize() sends, to the root of the ranking.
        self.assert_traffic(log, {None: (False, False, CONTEST_DATA)})
        self.assertEqual(self.payload_keys("contests/"),
                         {encode_id(self.contest_a.name)})

        # Moving the contest to another group, or out of any, changes
        # nothing either.
        for group in (self.omips, None):
            self.clear_requests()
            log.clear()
            self.contest_a.ranking_group = group
            self.session.commit()
            await service.reinitialize()
            await self._settle(service)

            self.assert_traffic(log, {None: (False, False, CONTEST_DATA)})
            self.assertEqual(self.payload_keys("contests/"),
                             {encode_id(self.contest_a.name)})
            self.assertEqual(service._group_contests, {})

    def switch_to_new_dataset(self, contest, score: float | None):
        """Make a new dataset the active one of the contest's task.

        The score type of the new dataset has a maximum score of 250
        (the others have 100).

        contest: a contest of these tests.
        score: the score of the contest's submission on the new
            dataset, or None if it has not been judged on it yet.

        return: the task, and the new dataset.

        """
        task = contest.tasks[0]
        dataset = self.add_dataset(task=task)
        if score is not None:
            result = self.add_submission_result(
                submission=task.submissions[0], dataset=dataset)
            result.compilation_outcome = "ok"
            result.evaluation_outcome = "ok"
            result.score = score
            result.score_details = dict()
            result.public_score = score
            result.public_score_details = dict()
            result.ranking_score_details = ["%d" % score]
        task.active_dataset = dataset
        self.session.commit()

        harness_score_type = Dataset.score_type_object
        score_type = MagicMock()
        score_type.max_score = 250
        score_type.ranking_headers = ["250"]
        new_dataset_id = dataset.id

        def score_type_object(dataset):
            if dataset.id == new_dataset_id:
                return score_type
            return harness_score_type.fget(dataset)

        patcher = patch("cms.db.Dataset.score_type_object",
                        property(score_type_object))
        patcher.start()
        self.addCleanup(patcher.stop)
        return task, dataset

    async def test_dataset_updated_sends_the_new_score_and_task_of_its_group(
        self,
    ):
        service = await self.start()
        task, _ = self.switch_to_new_dataset(self.contest_a, score=70)
        self.clear_requests()
        log = self.record_requests()

        await service.dataset_updated(task.id)
        await self._settle(service)

        # A reinitialize for every group, which is what the tasks with a
        # new maximum score need, but no reset (nothing is deleted), and
        # scores of the task's group only.
        self.assert_traffic(log, {
            "olim": (False, True, CONTEST_DATA | SCORES),
            "omips": (False, True, CONTEST_DATA)})
        tasks = self.put_payload(url("olim/tasks/"))
        self.assertEqual(tasks[encode_id(task.name)]["max_score"], 250)
        self.assertEqual(tasks[encode_id(task.name)]["extra_headers"],
                         ["250"])
        other_task = self.contest_b.tasks[0]
        self.assertEqual(
            self.put_payload(url("omips/tasks/"))[
                encode_id(other_task.name)]["max_score"], 100)
        self.assertEqual(self.payload_keys("olim/submissions/"),
                         {"%d" % self.sub_a.id})
        subchanges = self.put_payload(url("olim/subchanges/"))
        self.assertEqual([subchange["score"]
                          for subchange in subchanges.values()], [70])

    async def test_dataset_updated_without_new_score_keeps_the_old_one(
        self,
    ):
        service = await self.start()
        task, _ = self.switch_to_new_dataset(self.contest_a, score=None)
        self.clear_requests()
        log = self.record_requests()

        await service.dataset_updated(task.id)
        await self._settle(service)

        # The new tasks are sent, and no score: the submission has none
        # on the new dataset yet, and the one the ranking has stays.
        self.assert_traffic(log, {
            "olim": (False, True, CONTEST_DATA),
            "omips": (False, True, CONTEST_DATA)})
        self.assertEqual(
            self.put_payload(url("olim/tasks/"))[
                encode_id(task.name)]["max_score"], 250)

    async def test_dataset_updated_of_a_contest_without_group_sends_nothing(
        self,
    ):
        service = await self.start()
        task, _ = self.switch_to_new_dataset(self.contest_c, score=70)
        self.clear_requests()
        log = self.record_requests()

        await service.dataset_updated(task.id)
        await self._settle(service)

        self.assertEqual(log, [])

    async def test_dataset_updated_also_applies_a_group_change_not_told_yet(
        self,
    ):
        # The reinitialize AWS sends when a contest changes group did not
        # arrive: the dataset update carries out the change too.
        service = await self.start()
        task, _ = self.switch_to_new_dataset(self.contest_a, score=70)
        self.contest_a.ranking_group = self.omips
        self.session.commit()
        self.clear_requests()
        log = self.record_requests()

        await service.dataset_updated(task.id)
        await self._settle(service)

        # OLIM is emptied (it has no contest now), and the contest and its
        # score are sent to OMIPS, once each, with the contest that was
        # already there.
        self.assert_traffic(log, {
            "olim": (True, True, set()),
            "omips": (False, True, CONTEST_DATA | SCORES)})
        self.assertEqual(
            self.payload_keys("omips/contests/"),
            {encode_id(self.contest_a.name), encode_id(self.contest_b.name)})
        self.assertEqual(self.payload_keys("omips/submissions/"),
                         {"%d" % self.sub_a.id})
        self.assertEqual(
            [subchange["score"] for subchange in
             self.put_payload(url("omips/subchanges/")).values()], [70])


class ArchiveSweepTestCase(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):
    """A ProxyService over a DB of ranking groups with no submissions."""

    def setUp(self):
        super().setUp()
        for method, status in (("put", 200), ("delete", 204)):
            patcher = patch("cms.service.ProxyService.requests." + method)
            patcher.start().return_value.status_code = status
            self.addCleanup(patcher.stop)

    def tearDown(self):
        # Group mode sends every grouped contest in the DB, and group
        # names are unique: start each test from an empty DB.
        self.delete_data()
        super().tearDown()

    def build_service(self, contest_id: int | None = None) -> ProxyService:
        """Build a ProxyService (group mode unless contest_id is given)."""
        for target in ("cms.io.async_service.get_service_address",
                       "cms.io.async_rpc.get_service_address"):
            patcher = patch(target, return_value=Address("127.0.0.1", 0))
            patcher.start()
            self.addCleanup(patcher.stop)
        with patch.object(
                ProxyService, "start_sweeper", lambda self, timeout: None):
            service = ProxyService(0, contest_id)
        service._loop = asyncio.get_running_loop()
        self.addCleanup(service._disconnect_all)
        return service

    def add_ranking_group(self, *contest_stops: list[datetime],
                          **kwargs) -> RankingGroup:
        """Add and commit a ranking group.

        contest_stops: one list for each contest of the group, with the
            stop of each participation group of that contest.

        """
        ranking_group = RankingGroup(
            name=unique_unicode_id(), description="D", **kwargs)
        self.session.add(ranking_group)
        for stops in contest_stops:
            self.add_contest(
                ranking_group=ranking_group,
                groups=[self.get_group(**({} if stop is None else {"stop": stop}))
                        for stop in stops])
        self.session.commit()
        return ranking_group


class TestDefaultArchiveAt(ArchiveSweepTestCase):

    async def test_the_last_contest_decides(self):
        # Scenario: The last contest decides.
        # The first contest has two participation groups: the larger of
        # all the stops wins, and the first contest ends after the
        # second one's group that ends first.
        group = self.add_ranking_group(
            [datetime(2030, 1, 1, 12), datetime(2030, 1, 3, 12)],
            [datetime(2030, 1, 2, 12), datetime(2030, 1, 5, 6)])
        service = self.build_service()
        self.assertEqual(service._default_archive_at(self.session, group),
                         datetime(2030, 1, 6, 6))

    async def test_a_group_with_no_contests(self):
        # Scenario: A group with no contests.
        group = self.add_ranking_group()
        service = self.build_service()
        self.assertIsNone(service._default_archive_at(self.session, group))

    async def test_a_contest_with_the_default_stop_gives_2100(self):
        # "No end" is 2100-01-01: the group is archived in 2100, not now.
        group = self.add_ranking_group([None])
        service = self.build_service()
        self.assertEqual(service._default_archive_at(self.session, group),
                         datetime(2100, 1, 2))


NOW = datetime(2030, 1, 10, 12)


class TestFillArchiveDefaults(ArchiveSweepTestCase):

    def setUp(self):
        super().setUp()
        patcher = patch("cms.service.ProxyService.make_datetime",
                        return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fill(self, service: ProxyService):
        with SessionGen() as session:
            service._fill_archive_defaults(session)

    def archive_at(self, ranking_group: RankingGroup) -> datetime | None:
        """Read the archive_at stored in the DB, with a session of its own."""
        with SessionGen() as session:
            return session.get(RankingGroup, ranking_group.id).archive_at

    async def test_a_group_with_a_contest_that_has_not_ended_gets_the_default(
        self,
    ):
        # Scenario: A group with a contest that has not ended gets the
        # default (and it is committed: it is read back by another session).
        end = NOW + timedelta(hours=1)
        group = self.add_ranking_group([end])
        service = self.build_service()
        self.fill(service)
        self.assertEqual(self.archive_at(group), end + timedelta(hours=24))

    async def test_a_past_archive_at_is_kept(self):
        # Scenario: A past archive_at is kept, even if a later default
        # could be computed (the group stays archived).
        in_the_past = self.add_ranking_group(
            [NOW + timedelta(days=30)], archive_at=datetime(2020, 1, 1))
        service = self.build_service()
        self.fill(service)
        self.assertEqual(self.archive_at(in_the_past), datetime(2020, 1, 1))

    async def test_a_contest_added_later_postpones_a_future_archive_at(self):
        # Scenario: A contest added later postpones a future archive_at:
        # the stored value was the default of the first contest alone.
        first_end = NOW + timedelta(hours=1)
        second_end = NOW + timedelta(hours=5)
        group = self.add_ranking_group(
            [first_end], [second_end],
            archive_at=first_end + timedelta(hours=24))
        service = self.build_service()
        self.fill(service)
        self.assertEqual(self.archive_at(group),
                         second_end + timedelta(hours=24))

    async def test_a_later_archive_at_is_not_moved_earlier(self):
        # Scenario: A later archive_at is not moved earlier: a stored value
        # later than the default, and exactly equal to it, stay as they are.
        end = NOW + timedelta(hours=1)
        default = end + timedelta(hours=24)
        later = self.add_ranking_group(
            [end], archive_at=default + timedelta(days=3))
        equal = self.add_ranking_group([end], archive_at=default)
        service = self.build_service()
        self.fill(service)
        self.assertEqual(self.archive_at(later), default + timedelta(days=3))
        self.assertEqual(self.archive_at(equal), default)

    async def test_contests_that_already_ended_do_not_archive_a_group_by_surprise(
        self,
    ):
        # Scenario: Contests that already ended do not archive a group by
        # surprise: the end plus 24 hours in the past, and exactly now.
        long_ago = self.add_ranking_group([NOW - timedelta(days=5)])
        exactly_now = self.add_ranking_group([NOW - timedelta(hours=24)])
        service = self.build_service()
        self.fill(service)
        self.assertIsNone(self.archive_at(long_ago))
        self.assertIsNone(self.archive_at(exactly_now))

    async def test_a_group_with_no_contests_stays_empty(self):
        # Scenario: A group with no contests.
        group = self.add_ranking_group()
        service = self.build_service()
        self.fill(service)
        self.assertIsNone(self.archive_at(group))

    async def test_legacy_mode_does_nothing(self):
        # Legacy single-contest mode has no ranking groups to archive.
        group = self.add_ranking_group([NOW + timedelta(hours=1)])
        contest_id = self.session.execute(
            select(Contest.id).where(Contest.ranking_group_id == group.id)
        ).scalar()
        service = self.build_service(contest_id)
        self.fill(service)
        self.assertIsNone(self.archive_at(group))

    async def test_a_database_error_does_not_stop_the_sweep(self):
        # Scenario: A group with a contest that has not ended gets the
        # default, even when the first commit fails: the session is rolled
        # back, the sweep goes on, and the next sweep fills it.
        end = NOW + timedelta(hours=1)
        group = self.add_ranking_group([end])
        service = self.build_service()
        events: list[str] = []
        service._threadsafe_enqueue = lambda operation: events.append(
            "enqueue %d" % operation.type_)

        @contextmanager
        def failing_commit_once():
            with SessionGen() as session:
                real_commit = session.commit
                real_rollback = session.rollback

                def commit():
                    session.commit = real_commit
                    session.flush()
                    raise OperationalError(
                        "COMMIT", {}, Exception("timeout"))

                def rollback():
                    events.append("rollback")
                    real_rollback()

                session.commit = commit
                session.rollback = rollback
                yield session

        with patch("cms.service.ProxyService.SessionGen",
                   failing_commit_once), \
                self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            service._missing_operations_sync()

        self.assertEqual(
            len([line for line in logs.output
                 if "default archive_at" in line]), 1)
        self.assertIsNone(self.archive_at(group))
        self.assertEqual(events[0], "rollback")
        self.assertIn("enqueue %d" % ProxyExecutor.VISIBILITY_TYPE, events)

        service._missing_operations_sync()
        self.assertEqual(self.archive_at(group), end + timedelta(hours=24))

    async def test_a_failing_update_inside_the_fill_is_handled(self):
        # Scenario: A group with a contest that has not ended gets the
        # default, even when the UPDATE of the first of two groups fails
        # (it used to fail at the autoflush of the read of the second,
        # outside the error handling): one WARNING, a rollback, the
        # sweep goes on, and the next sweep stores both defaults.
        end = NOW + timedelta(hours=1)
        groups = [self.add_ranking_group([end]) for _ in range(2)]
        service = self.build_service()
        events: list[str] = []
        service._threadsafe_enqueue = lambda operation: events.append(
            "enqueue %d" % operation.type_)

        @contextmanager
        def failing_update_once():
            with SessionGen() as session:
                real_flush = session.flush

                def flush(*args, **kwargs):
                    if session.dirty:
                        session.flush = real_flush
                        raise OperationalError(
                            "UPDATE", {}, Exception("timeout"))
                    real_flush(*args, **kwargs)

                session.flush = flush
                yield session

        with patch("cms.service.ProxyService.SessionGen",
                   failing_update_once), \
                self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            service._missing_operations_sync()

        self.assertEqual(
            len([line for line in logs.output
                 if "default archive_at" in line]), 1)
        self.assertEqual([self.archive_at(group) for group in groups],
                         [None, None])
        self.assertIn("enqueue %d" % ProxyExecutor.VISIBILITY_TYPE, events)

        service._missing_operations_sync()
        self.assertEqual([self.archive_at(group) for group in groups],
                         [end + timedelta(hours=24)] * 2)


class ArchiveTrafficTestCase(ArchiveSweepTestCase):
    """Ranking groups with scored submissions, and a spy on what is queued.

    The clock is NOW; group_a is archived, group_b is not (its
    archive_at is in the future).
    """

    def setUp(self):
        super().setUp()
        score_type = MagicMock()
        score_type.max_score = 100
        score_type.ranking_headers = ["100"]
        for target, new in (
                ("cms.db.Dataset.score_type_object",
                 property(lambda dataset: score_type)),
                ("cms.service.ProxyService.make_datetime",
                 MagicMock(return_value=NOW))):
            patcher = patch(target, new)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = self.add_user()
        self.group_a, self.contest_a, self.sub_a = \
            self.add_group_with_submission(archive_at=NOW - timedelta(days=1))
        self.group_b, self.contest_b, self.sub_b = \
            self.add_group_with_submission(archive_at=NOW + timedelta(days=1))

    def add_group_with_submission(self, **kwargs):
        """Add and commit a group with a contest and a scored submission."""
        group = RankingGroup(
            name=unique_unicode_id(), description="D", **kwargs)
        self.session.add(group)
        contest, submission = \
            TestProxyServiceGroups.add_contest_with_submission(self, group)
        self.session.commit()
        return group, contest, submission

    def spy(self, service: ProxyService) -> list[ProxyOperation]:
        """Return the list the operations the service enqueues go to."""
        operations: list[ProxyOperation] = []
        service._threadsafe_enqueue = operations.append
        service._threadsafe_enqueue_all = operations.extend
        return operations

    @staticmethod
    def groups_of(operations: list[ProxyOperation]) -> set[str | None]:
        return {operation.group for operation in operations}


class TestSkipArchivedEvents(ArchiveTrafficTestCase):

    async def test_a_score_event_for_an_archived_group(self):
        # Scenario: A score event for an archived group (and, for group_b,
        # Correction during the grace period: its archive_at is in the
        # future and the new score is enqueued).
        service = self.build_service()
        operations = self.spy(service)
        service._submission_scored_sync(self.sub_a.id)
        self.assertEqual(operations, [])
        service._submission_scored_sync(self.sub_b.id)
        self.assertEqual(self.groups_of(operations), {self.group_b.name})

    async def test_a_token_event_for_an_archived_group(self):
        # Scenario: A token event for an archived group.
        self.add_token(submission=self.sub_a)
        self.add_token(submission=self.sub_b)
        self.session.commit()
        service = self.build_service()
        operations = self.spy(service)
        service._submission_tokened_sync(self.sub_a.id)
        self.assertEqual(operations, [])
        service._submission_tokened_sync(self.sub_b.id)
        self.assertEqual(self.groups_of(operations), {self.group_b.name})

    async def test_a_dataset_update_for_an_archived_group(self):
        # Scenario: A dataset update for an archived group.
        service = self.build_service()
        operations = self.spy(service)
        service._dataset_updated_sync(self.contest_a.tasks[0].id)
        self.assertEqual(operations, [])

    async def test_a_contest_added_to_an_archived_group(self):
        # Scenario: A contest added to an archived group: a contest of no
        # group is moved into it, and its score event sends nothing.
        group_c, contest_c, sub_c = self.add_group_with_submission()
        contest_c.ranking_group = self.group_a
        self.session.commit()
        service = self.build_service()
        operations = self.spy(service)
        service._submission_scored_sync(sub_c.id)
        self.assertEqual(operations, [])

    async def test_the_archive_boundary(self):
        # Scenario: At and after archive_at: archived exactly at now, and
        # not one microsecond before.
        service = self.build_service()
        operations = self.spy(service)
        self.group_b.archive_at = NOW
        self.session.commit()
        service._submission_scored_sync(self.sub_b.id)
        self.assertEqual(operations, [])
        self.group_b.archive_at = NOW + timedelta(microseconds=1)
        self.session.commit()
        service._submission_scored_sync(self.sub_b.id)
        self.assertEqual(self.groups_of(operations), {self.group_b.name})

    async def test_a_group_with_no_archive_at_still_sends(self):
        # Scenario: No archive_at: never archived.
        self.group_b.archive_at = None
        self.session.commit()
        service = self.build_service()
        operations = self.spy(service)
        service._submission_scored_sync(self.sub_b.id)
        self.assertEqual(self.groups_of(operations), {self.group_b.name})

    async def test_a_contest_of_no_group_is_still_ignored(self):
        # A contest with ranking_group None keeps its old behavior.
        _, contest_c, sub_c = self.add_group_with_submission()
        contest_c.ranking_group = None
        self.session.commit()
        service = self.build_service()
        operations = self.spy(service)
        service._submission_scored_sync(sub_c.id)
        self.assertEqual(operations, [])

    async def test_legacy_mode_is_unchanged(self):
        # Legacy single-contest mode ignores ranking groups, archived or
        # not.
        service = self.build_service(self.contest_a.id)
        operations = self.spy(service)
        self.assertTrue(service._is_sent(self.contest_a))
        service._submission_scored_sync(self.sub_a.id)
        self.assertEqual(self.groups_of(operations), {None})


class TestSkipArchivedSweep(ArchiveTrafficTestCase):

    async def test_the_sweep_skips_an_archived_group(self):
        # Scenario: The sweep skips an archived group: nothing for it
        # (its final visibility went out when the service was built),
        # and the other group still gets its visibility, its contest
        # data and its scores.
        service = self.build_service()
        operations = self.spy(service)
        service._missing_operations_sync()
        self.assertEqual(self.groups_of(operations), {self.group_b.name})
        self.assertEqual(
            {operation.type_ for operation in operations},
            {ProxyExecutor.VISIBILITY_TYPE, ProxyExecutor.SUBMISSION_TYPE,
             ProxyExecutor.SUBCHANGE_TYPE})

    async def test_initialize_skips_an_archived_group(self):
        # Scenario: Initialize and reinitialize leave an archived group
        # alone (initialize half): no contest data, and no visibility
        # beyond the final one that the service sent when it was built.
        service = self.build_service()
        operations = self.spy(service)
        service.initialize()
        self.assertEqual(self.groups_of(operations), {self.group_b.name})
        self.assertIn(ProxyExecutor.VISIBILITY_TYPE,
                      {operation.type_ for operation in operations})

    async def test_the_visibility_of_a_named_archived_group(self):
        # Scenario: The sweep skips an archived group (a Regenerate or
        # any other caller asking for the visibility of one group): the
        # final visibility already went out when the service was built.
        service = self.build_service()
        operations: list[ProxyOperation] = []
        with SessionGen() as session:
            service._enqueue_visibility(
                session, self.group_a.name, enqueue=operations.append)
            self.assertEqual(operations, [])
            service._enqueue_visibility(
                session, self.group_b.name, enqueue=operations.append)
        self.assertEqual(self.groups_of(operations), {self.group_b.name})

    async def test_contests_to_send_can_include_archived_groups(self):
        service = self.build_service()
        with SessionGen() as session:
            self.assertEqual(
                [c.id for c in service._contests_to_send(session)],
                [self.contest_b.id])
            self.assertEqual(
                {c.id for c in service._contests_to_send(
                    session, include_archived=True)},
                {self.contest_a.id, self.contest_b.id})

    async def test_legacy_mode_ignores_the_archive(self):
        # Legacy single-contest mode behaves as before.
        service = self.build_service(self.contest_a.id)
        operations = self.spy(service)
        with SessionGen() as session:
            self.assertEqual(
                [c.id for c in service._contests_to_send(session)],
                [self.contest_a.id])
        service.initialize()
        self.assertEqual(self.groups_of(operations), {None})


class TestArchivedGroupsAndReinitialize(ArchiveTrafficTestCase):

    def operation_types(self, operations: list[ProxyOperation],
                        group: str) -> list[int]:
        return [operation.type_ for operation in operations
                if operation.group == group]

    async def test_a_group_archived_after_the_mapping_is_not_reset(self):
        # Scenario: Group archived while the ranking holds its data, and
        # Initialize and reinitialize leave an archived group alone: the
        # service knew the group when it was open; it is archived now.
        # A different group that really lost its contest is still reset.
        self.group_a.archive_at = NOW + timedelta(days=1)
        group_c, contest_c, _ = self.add_group_with_submission()
        service = self.build_service()
        self.group_a.archive_at = NOW - timedelta(days=1)
        contest_c.ranking_group = None
        self.session.commit()
        operations = self.spy(service)

        service._reinitialize_sync()
        service._missing_operations_sync()

        types_c = self.operation_types(operations, group_c.name)
        self.assertEqual(types_c.count(ProxyExecutor.RESET_TYPE), 1)
        # Only the final visibility: it is sent once, when the service
        # first finds the group archived (here, at the reinitialize).
        self.assertEqual(self.operation_types(operations, self.group_a.name),
                         [ProxyExecutor.VISIBILITY_TYPE])
        self.assertIn(self.group_b.name, self.groups_of(operations))

    async def test_a_group_that_lost_a_contest_while_archived_is_not_reset(
        self,
    ):
        # Scenario: Group archived while the ranking holds its data: the
        # contest leaves the archived group, and its namespace keeps the
        # final standings.
        self.group_a.archive_at = NOW + timedelta(days=1)
        self.session.commit()
        service = self.build_service()
        self.group_a.archive_at = NOW - timedelta(days=1)
        self.contest_a.ranking_group = self.group_b
        self.session.commit()
        operations = self.spy(service)

        service._reinitialize_sync()

        # No reset: only the final visibility, as the group was found
        # archived now.
        self.assertEqual(self.operation_types(operations, self.group_a.name),
                         [ProxyExecutor.VISIBILITY_TYPE])
        self.assertEqual(service._group_contests,
                         {self.group_b.name: {self.contest_a.id,
                                              self.contest_b.id}})

    async def test_the_initial_mapping_includes_an_archived_group(self):
        # Scenario: Initialize and reinitialize leave an archived group
        # alone (the mapping built by __init__ keeps the archived group).
        service = self.build_service()
        self.assertEqual(service._group_contests,
                         {self.group_a.name: {self.contest_a.id},
                          self.group_b.name: {self.contest_b.id}})

    async def test_a_contest_assigned_to_an_archived_group_sends_nothing(
        self,
    ):
        # Scenario: A contest added to an archived group, through a
        # reinitialize: neither its data nor its submissions.
        group_c, contest_c, _ = self.add_group_with_submission()
        contest_c.ranking_group = None
        self.session.commit()
        service = self.build_service()
        contest_c.ranking_group = self.group_a
        self.session.commit()
        operations = self.spy(service)

        service._reinitialize_sync()

        self.assertEqual(self.operation_types(operations, self.group_a.name),
                         [])
        self.assertEqual(service._group_contests[self.group_a.name],
                         {self.contest_a.id, contest_c.id})

    async def test_unarchiving_resumes_the_sending(self):
        # Scenario: Unarchiving resumes the sending: archive_at moved to
        # the future, and the next sweep sends the visibility and what
        # the group misses.
        service = self.build_service()
        operations = self.spy(service)
        service._missing_operations_sync()
        self.assertEqual(self.operation_types(operations, self.group_a.name),
                         [])

        self.group_a.archive_at = NOW + timedelta(days=1)
        self.session.commit()
        service._missing_operations_sync()

        types = self.operation_types(operations, self.group_a.name)
        self.assertEqual(types[0], ProxyExecutor.VISIBILITY_TYPE)
        self.assertIn(ProxyExecutor.SUBMISSION_TYPE, types)


class TestFinalVisibility(ArchiveTrafficTestCase):

    NO_WINDOWS = {"hide_at": None, "show_at": None,
                  "freeze_at": None, "unfreeze_at": None}

    def build_spied_service(self) -> tuple[ProxyService, list[ProxyOperation]]:
        """Build a service whose spy also sees the initialize of __init__."""
        operations: list[ProxyOperation] = []
        with patch.object(ProxyService, "_threadsafe_enqueue",
                          lambda service, operation:
                          operations.append(operation)):
            service = self.build_service()
        service._threadsafe_enqueue = operations.append
        service._threadsafe_enqueue_all = operations.extend
        return service, operations

    def visibilities(self, operations: list[ProxyOperation],
                     group: RankingGroup) -> list[ProxyOperation]:
        return [operation for operation in operations
                if operation.group == group.name
                and operation.type_ == ProxyExecutor.VISIBILITY_TYPE]

    async def test_a_hidden_group_is_archived(self):
        # Scenario: A hidden group is archived: one visibility with no
        # window, and the stored staff password.
        self.group_a.hide_at = NOW - timedelta(days=2)
        self.group_a.staff_password = "secret"
        self.session.commit()
        service, operations = self.build_spied_service()
        service._missing_operations_sync()
        (final,) = self.visibilities(operations, self.group_a)
        self.assertEqual(
            final.data, {**self.NO_WINDOWS, "staff_password": "secret"})
        # Nothing else goes to the archived group.
        self.assertEqual(
            [operation for operation in operations
             if operation.group == self.group_a.name], [final])

    async def test_a_frozen_group_is_archived(self):
        # Scenario: A frozen group is archived: one visibility with no
        # window, also when the group is hidden and frozen at once.
        self.group_a.hide_at = NOW - timedelta(days=3)
        self.group_a.show_at = NOW + timedelta(days=3)
        self.group_a.freeze_at = NOW - timedelta(days=2)
        self.group_a.unfreeze_at = NOW + timedelta(days=2)
        self.session.commit()
        service, operations = self.build_spied_service()
        service._missing_operations_sync()
        (final,) = self.visibilities(operations, self.group_a)
        self.assertEqual(
            final.data, {**self.NO_WINDOWS, "staff_password": None})

    async def test_the_final_visibility_is_sent_once(self):
        # Scenario: The final visibility is sent once: two sweeps and an
        # initialize while the group stays archived.
        service, operations = self.build_spied_service()
        service._missing_operations_sync()
        service._missing_operations_sync()
        service.initialize()
        self.assertEqual(len(self.visibilities(operations, self.group_a)), 1)

    async def test_unarchiving_forgets_the_final_visibility(self):
        # Scenario: Unarchiving forgets the final visibility (and
        # Unarchiving resumes the sending): archived, then unarchived
        # (the normal windows go out), then archived again.
        self.group_a.freeze_at = NOW - timedelta(days=2)
        self.session.commit()
        service, operations = self.build_spied_service()
        service._missing_operations_sync()
        self.assertEqual(len(self.visibilities(operations, self.group_a)), 1)

        self.group_a.archive_at = NOW + timedelta(days=1)
        self.session.commit()
        service._missing_operations_sync()
        normal = self.visibilities(operations, self.group_a)[1]
        self.assertIsNotNone(normal.data["freeze_at"])

        self.group_a.archive_at = NOW - timedelta(days=1)
        self.session.commit()
        service._missing_operations_sync()
        service._missing_operations_sync()
        visibilities = self.visibilities(operations, self.group_a)
        self.assertEqual(len(visibilities), 3)
        self.assertIsNone(visibilities[2].data["freeze_at"])

    async def test_a_new_service_sends_it_once_more(self):
        # Scenario: The final visibility is sent once, per ProxyService
        # run: the mark is in memory, a restart sends it again.
        first, operations = self.build_spied_service()
        first._missing_operations_sync()
        second, operations_2 = self.build_spied_service()
        second._missing_operations_sync()
        self.assertEqual(len(self.visibilities(operations, self.group_a)), 1)
        self.assertEqual(len(self.visibilities(operations_2, self.group_a)), 1)


class TestRegenerateArchivedSafetyNet(ArchiveTrafficTestCase):

    async def test_regenerate_on_an_archived_group_enqueues_nothing(self):
        # Scenario: Regenerate on an archived group (the safety net: no
        # reset and no data; the WARNING is tested below).
        service = self.build_service()
        operations = self.spy(service)
        service._regenerate_ranking_sync(self.group_a.name)
        self.assertEqual(operations, [])

    async def test_regenerate_on_a_group_that_is_not_archived(self):
        # Scenario: Regenerate on a group that is not archived: the reset
        # comes first, then its visibility and data.
        service = self.build_service()
        operations = self.spy(service)
        service._regenerate_ranking_sync(self.group_b.name)
        self.assertEqual(self.groups_of(operations), {self.group_b.name})
        self.assertEqual(operations[0].type_, ProxyExecutor.RESET_TYPE)
        self.assertIn(ProxyExecutor.SUBMISSION_TYPE,
                      {operation.type_ for operation in operations})


class TestRegenerateArchivedWarning(ArchiveTrafficTestCase):

    async def test_regenerate_on_an_archived_group(self):
        # Scenario: Regenerate on an archived group: no operation, and
        # one WARNING that names the group and its archive_at.
        service = self.build_service()
        operations = self.spy(service)
        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            service._regenerate_ranking_sync(self.group_a.name)
        self.assertEqual(operations, [])
        (record,) = logs.records
        message = record.getMessage()
        self.assertIn(self.group_a.name, message)
        self.assertIn(str(self.group_a.archive_at), message)
        self.assertIn("archived", message)

    async def test_regenerate_on_a_group_that_is_not_archived(self):
        # Scenario: Regenerate on a group that is not archived: it
        # regenerates and logs no warning.
        service = self.build_service()
        operations = self.spy(service)
        with self.assertNoLogs("cms.service.ProxyService", "WARNING"):
            service._regenerate_ranking_sync(self.group_b.name)
        self.assertEqual(operations[0].type_, ProxyExecutor.RESET_TYPE)
        self.assertIn(ProxyExecutor.SUBMISSION_TYPE,
                      {operation.type_ for operation in operations})

    async def test_regenerate_of_the_root_and_of_an_unknown_group(self):
        # Scenario: Regenerate on a group that is not archived (the root
        # namespace and a name that is no group): as before, a reset and
        # no warning or exception.
        service = self.build_service()
        for group in (None, "no-such-group"):
            operations = self.spy(service)
            with self.assertNoLogs("cms.service.ProxyService", "WARNING"):
                service._regenerate_ranking_sync(group)
            self.assertEqual(operations[0].type_, ProxyExecutor.RESET_TYPE)
            self.assertEqual(operations[0].group, group)


if __name__ == "__main__":
    unittest.main()
