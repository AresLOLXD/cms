#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 The CMS development team
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

"""Tests for AdminWebServer.submissions_status.

"""

import asyncio
import contextlib
import json
import threading
import time
import unittest
from collections.abc import Iterator
from unittest import mock

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.server.admin.pool_exhaustion_test import \
    longest_loop_stall

from cms.conf import Address
from cms.io.async_rpc import AsyncRemoteServiceServer
from cms.server.admin import server
from cms.server.admin.server import AdminWebServer
from cmscommon.datetime import make_datetime


class TestConstruction(unittest.TestCase):
    """AdminWebServer must be constructible without raising.

    Regression test for a TypeError that used to be raised at startup
    (WebService.__init__ tried to construct the old, WSGI-shaped
    AWSAuthMiddleware(app) as auth_middleware(), which doesn't accept
    zero arguments) -- see this sub-project's task 7 fix round 1.

    """

    def test_constructs_without_raising(self):
        server = AdminWebServer(0)
        self.assertIsNone(server.auth_handler)


class TestSubmissionsStatus(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(task=self.task)
        self.task.active_dataset = self.dataset

        # A submission with no result at all: it hasn't started compiling.
        self.add_submission(self.task, self.participation)

        # A submission whose result exists but hasn't been compiled yet.
        submission = self.add_submission(self.task, self.participation)
        self.add_submission_result(submission, self.dataset)

        # A submission whose compilation failed.
        self.add_submission_with_results(
            self.task, self.participation, compilation_outcome=False)

        # A submission that compiled successfully but hasn't been
        # evaluated yet.
        self.add_submission_with_results(
            self.task, self.participation, compilation_outcome=True)

        self.session.commit()

    def test_counts_all_contests(self):
        stats = AdminWebServer.compute_submissions_status(None)
        self.assertEqual(stats["total"], 4)
        # Both the submission with no result and the one with an
        # uncompiled result are bucketed as "compiling".
        self.assertEqual(stats["compiling"], 2)
        self.assertEqual(stats["compilation_fail"], 1)
        self.assertEqual(stats["evaluating"], 1)
        self.assertEqual(stats["max_compilations"], 0)
        self.assertEqual(stats["max_evaluations"], 0)
        self.assertEqual(stats["scoring"], 0)
        self.assertEqual(stats["scored"], 0)

    def test_counts_restricted_by_contest(self):
        other_contest = self.add_contest()
        other_task = self.add_task(contest=other_contest)
        other_dataset = self.add_dataset(task=other_task)
        other_task.active_dataset = other_dataset
        other_participation = self.add_participation(contest=other_contest)
        self.add_submission_with_results(
            other_task, other_participation, compilation_outcome=True)
        self.session.commit()

        stats = AdminWebServer.compute_submissions_status(self.contest.id)
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["compiling"], 2)

        other_stats = AdminWebServer.compute_submissions_status(
            other_contest.id)
        self.assertEqual(other_stats["total"], 1)
        self.assertEqual(other_stats["evaluating"], 1)
        self.assertEqual(other_stats["compiling"], 0)


class TestSubmissionsStatusRpc(
    DatabaseMixin, unittest.IsolatedAsyncioTestCase
):
    """The RPC that the Overview page calls every few seconds.

    The RPC server runs RPC methods in the event loop thread, so the
    queries of submissions_status must run somewhere else, or each
    call stalls the whole server.

    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Built once, and outside the event loop: this way it doesn't
        # try to connect to the services of the configuration.
        cls.aws = AdminWebServer(0)

    def setUp(self):
        super().setUp()
        # Each test has its own event loop, and the semaphore that
        # serializes the queries binds to the loop of its first contended
        # use: start every test with a new one.
        slot_patch = mock.patch.object(
            server, "_submissions_status_slot", None)
        slot_patch.start()
        self.addCleanup(slot_patch.stop)
        self.contest = self.add_contest()
        participation = self.add_participation(contest=self.contest)
        task = self.add_task(contest=self.contest)
        dataset = self.add_dataset(task=task)
        task.active_dataset = dataset
        self.add_submission_with_results(
            task, participation, compilation_outcome=False)
        self.add_submission_with_results(
            task, participation, compilation_outcome=True)
        self.session.commit()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    async def call_rpc(self, **arguments) -> dict:
        """Call submissions_status as the RPC server does.

        arguments: the arguments of the RPC.

        return: the reply sent back to the caller.

        """
        remote = AsyncRemoteServiceServer(
            self.aws, Address("127.0.0.1", 0))
        remote._write = mock.AsyncMock()
        await remote.process_incoming_request({
            "__id": "1", "__method": "submissions_status",
            "__data": arguments})
        remote._write.assert_awaited_once()
        return json.loads(remote._write.await_args.args[0])

    @staticmethod
    def slow_session_gen(threads: list[int], delay: float):
        """Wrap SessionGen, to record the thread that opens it and to wait.

        threads: where to add the id of the thread opening a session.
        delay: how long to wait before opening it, in seconds.

        return: a replacement for SessionGen.

        """
        real_session_gen = server.SessionGen

        def session_gen():
            threads.append(threading.get_ident())
            time.sleep(delay)
            return real_session_gen()

        return session_gen

    @contextlib.contextmanager
    def tracked_queries(self, delay: float) -> Iterator[dict[str, int]]:
        """Count the queries of the block, and how many run at once.

        delay: how long each of them takes, in seconds.

        return: the counts of the queries "started" and of the highest
            number of them running at the same time ("peak").

        """
        compute = AdminWebServer.compute_submissions_status
        lock = threading.Lock()
        counts = {"started": 0, "running": 0, "peak": 0}

        def tracked_compute(contest_id):
            with lock:
                counts["started"] += 1
                counts["running"] += 1
                counts["peak"] = max(counts["peak"], counts["running"])
            try:
                time.sleep(delay)
                return compute(contest_id)
            finally:
                with lock:
                    counts["running"] -= 1

        with mock.patch.object(AdminWebServer, "compute_submissions_status",
                               staticmethod(tracked_compute)):
            yield counts

    async def test_replies_with_the_statistics(self):
        reply = await self.call_rpc(contest_id=self.contest.id)

        self.assertIsNone(reply["__error"])
        self.assertEqual(reply["__data"], {
            "compiling": 0, "max_compilations": 0, "compilation_fail": 1,
            "evaluating": 1, "max_evaluations": 0, "scoring": 0,
            "scored": 0, "total": 2})

    async def test_queries_run_outside_the_event_loop_thread(self):
        threads = []
        with mock.patch.object(
                server, "SessionGen", self.slow_session_gen(threads, 0)):
            reply = await self.call_rpc(contest_id=self.contest.id)

        self.assertIsNone(reply["__error"])
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], threading.get_ident())

    async def test_the_event_loop_keeps_running_during_the_queries(self):
        stop = asyncio.Event()
        ticker = asyncio.create_task(longest_loop_stall(stop))
        # Let the ticker start measuring.
        await asyncio.sleep(0.05)
        with mock.patch.object(
                server, "SessionGen", self.slow_session_gen([], 0.5)):
            reply = await self.call_rpc(contest_id=self.contest.id)
        stop.set()
        stall = await ticker

        self.assertIsNone(reply["__error"])
        self.assertLess(stall, 0.25)

    async def test_concurrent_calls_run_the_queries_one_at_a_time(self):
        with self.tracked_queries(0.1) as counts:
            replies = await asyncio.gather(*(
                self.call_rpc(contest_id=self.contest.id)
                for _ in range(5)))

        self.assertEqual(counts["started"], 5)
        self.assertEqual(counts["peak"], 1)
        expected = AdminWebServer.compute_submissions_status(self.contest.id)
        for reply in replies:
            self.assertIsNone(reply["__error"])
            self.assertEqual(reply["__data"], expected)

    async def test_cancelled_call_keeps_its_turn_until_the_query_ends(self):
        with self.tracked_queries(0.3) as counts:
            first = asyncio.create_task(
                self.call_rpc(contest_id=self.contest.id))
            while counts["started"] == 0:
                await asyncio.sleep(0.01)
            # The query can't be cancelled: it goes on in its thread.
            first.cancel()
            reply = await self.call_rpc(contest_id=self.contest.id)

        self.assertIsNone(reply["__error"])
        self.assertEqual(counts["started"], 2)
        self.assertEqual(counts["peak"], 1)


class TestNotifications(unittest.TestCase):
    """add_notification/take_notifications from concurrent threads."""

    def setUp(self):
        self.server = AdminWebServer(0)

    def test_take_returns_all_and_empties(self):
        now = make_datetime()
        self.server.add_notification(now, "a", "1")
        self.server.add_notification(now, "b", "2")
        self.assertEqual(self.server.take_notifications(),
                         [(now, "a", "1"), (now, "b", "2")])
        self.assertEqual(self.server.notifications, [])
        self.assertEqual(self.server.take_notifications(), [])

    def test_concurrent_add_and_take_delivers_each_exactly_once(self):
        writers, per_writer = 4, 2000
        now = make_datetime()
        taken = []
        writers_done = threading.Event()

        def write(writer):
            for i in range(per_writer):
                self.server.add_notification(now, str(writer), str(i))

        def take():
            while not writers_done.is_set():
                taken.extend(self.server.take_notifications())
            taken.extend(self.server.take_notifications())

        taker = threading.Thread(target=take)
        taker.start()
        threads = [threading.Thread(target=write, args=(w,))
                   for w in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        writers_done.set()
        taker.join()

        self.assertEqual(len(taken), writers * per_writer)
        self.assertEqual(
            sorted((s, t) for _, s, t in taken),
            sorted((str(w), str(i))
                   for w in range(writers) for i in range(per_writer)))
        self.assertEqual(self.server.notifications, [])


if __name__ == "__main__":
    unittest.main()
