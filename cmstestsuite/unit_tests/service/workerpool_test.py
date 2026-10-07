#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""Tests for the RPCs WorkerPool sends to the workers."""

import asyncio
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from cms.conf import ServiceCoord
from cms.io.async_rpc import AsyncFakeRemoteServiceClient
from cms.service.esoperations import ESOperation
from cms.service.workerpool import (
    JOB_OVERHEAD_S, WorkerPool, job_group_timeout)
from cmstestsuite.unit_tests.stuckpeer import StuckPeer, connect_client


class TestFireAndForget(unittest.IsolatedAsyncioTestCase):
    """The RPCs whose answer nobody needs are not waited for for ever."""

    async def asyncSetUp(self):
        self.peer = StuckPeer()
        await self.peer.start()
        self.client = await connect_client(
            ServiceCoord("Worker", 0), self.peer)

    async def asyncTearDown(self):
        self.client.disconnect()
        await self.peer.stop()

    async def test_gives_up_on_a_call_the_peer_never_answers(self):
        with patch("cms.service.workerpool.FIRE_AND_FORGET_TIMEOUT", 0.05):
            with self.assertLogs(
                    "cms.service.workerpool", level="WARNING") as logs:
                # The outer bound only turns a hang into a failure.
                await asyncio.wait_for(
                    WorkerPool._fire_and_forget(
                        self.client.precache_files(contest_id=1)),
                    timeout=5)

        self.assertEqual(len(self.peer.requests), 1)
        message = "\n".join(logs.output)
        self.assertIn("precache_files", message)
        self.assertIn("Worker,0", message)
        self.assertEqual(self.client.pending_outgoing_requests, {})
        self.assertEqual(self.client.pending_outgoing_requests_results, {})

    async def test_call_answered_in_time_is_not_reported(self):
        self.peer.release()

        with self.assertNoLogs("cms.service.workerpool", level="WARNING"):
            await WorkerPool._fire_and_forget(
                self.client.precache_files(contest_id=1))

        self.assertEqual(self.client.pending_outgoing_requests, {})

    async def test_rpc_error_is_swallowed(self):
        fake_client = AsyncFakeRemoteServiceClient(ServiceCoord("Worker", 1))

        with self.assertNoLogs("cms.service.workerpool", level="WARNING"):
            await WorkerPool._fire_and_forget(
                fake_client.precache_files(contest_id=1))


class TestDispatchToWorker(unittest.IsolatedAsyncioTestCase):

    async def test_job_group_is_awaited_however_long_it_takes(self):
        # A job group is real work, not fire-and-forget: it must not
        # be given up on after FIRE_AND_FORGET_TIMEOUT.
        service = MagicMock()
        service.action_finished = AsyncMock()
        pool = WorkerPool(service)
        worker = MagicMock()

        async def slow_execute_job_group(job_group_dict):
            await asyncio.sleep(0.2)
            return {"jobs": []}

        worker.execute_job_group = slow_execute_job_group
        pool._worker[0] = worker

        with patch("cms.service.workerpool.FIRE_AND_FORGET_TIMEOUT", 0.05):
            await pool._dispatch_to_worker(0, {"jobs": []})

        service.action_finished.assert_awaited_once_with({"jobs": []}, 0, None)


class TestDuplicateOperation(unittest.TestCase):
    """The same operation assigned to two workers at once."""

    def setUp(self):
        service = MagicMock()
        service._loop = None
        service.contest_id = None
        # Nothing is dispatched for real: drop the dispatch coroutine.
        service._spawn.side_effect = lambda coroutine: coroutine.close()
        service.connect_to.side_effect = \
            lambda coord, on_connect: MagicMock(connected=True)
        self.pool = WorkerPool(service)
        for shard in range(2):
            self.pool.add_worker(ServiceCoord("Worker", shard))

    @staticmethod
    def _operation(testcase_codename: str) -> ESOperation:
        return ESOperation(ESOperation.EVALUATION, 42, 7, testcase_codename)

    def test_releasing_both_workers_forgets_every_operation(self):
        duplicate = self._operation("001")
        only_first = self._operation("002")
        only_second = self._operation("003")
        first = self.pool.acquire_worker([duplicate, only_first])
        second = self.pool.acquire_worker([duplicate, only_second])
        self.assertNotEqual(first, second)

        self.assertIs(self.pool.release_worker(first), False)
        # The second worker is still running the duplicate.
        self.assertIn(duplicate, self.pool)
        self.assertIs(self.pool.release_worker(second), False)

        for shard in (first, second):
            self.assertIs(self.pool._operations[shard],
                          WorkerPool.WORKER_INACTIVE)
        self.assertEqual(self.pool._operations_reverse, {})
        for operation in (duplicate, only_first, only_second):
            self.assertNotIn(operation, self.pool)


MINIMUM = timedelta(seconds=600)


def _compilation_job() -> dict:
    return {"type": "compilation"}


def _evaluation_job(time_limit: float | None) -> dict:
    return {"type": "evaluation", "time_limit": time_limit}


class TestJobGroupTimeout(unittest.TestCase):
    """How long a worker may take on a job group."""

    @staticmethod
    def _timeout(jobs: list[dict], minimum: timedelta = MINIMUM) -> timedelta:
        # Compilation limit 20 s and trusted limit 10 s, as in
        # config/cms.sample.toml.
        return job_group_timeout({"jobs": jobs}, 20.0, 10.0, minimum)

    def test_the_overhead_per_job(self):
        self.assertEqual(JOB_OVERHEAD_S, 10)

    def test_compilations_get_their_wall_limit_and_the_overhead(self):
        # 25 x (2 x 20 + 1 + 10) = 1275 s.
        self.assertEqual(self._timeout([_compilation_job()] * 25),
                         timedelta(seconds=1275))

    def test_evaluations_get_two_user_stages_and_a_trusted_program(self):
        # 25 x (2 x (2 x 10 + 1) + (2 x 10 + 1) + 10) = 25 x 73 s.
        self.assertEqual(self._timeout([_evaluation_job(10.0)] * 25),
                         timedelta(seconds=1825))
        # 25 x (2 x (2 x 1 + 1) + 21 + 10) = 25 x 37 s.
        self.assertEqual(self._timeout([_evaluation_job(1.0)] * 25),
                         timedelta(seconds=925))

    def test_a_mixed_group_adds_its_jobs_up(self):
        # 51 s for the compilation, 37 s for the evaluation.
        self.assertEqual(
            self._timeout([_compilation_job(), _evaluation_job(1.0)],
                          minimum=timedelta(0)),
            timedelta(seconds=88))

    def test_the_timeout_is_never_below_the_minimum(self):
        self.assertEqual(self._timeout([_evaluation_job(1.0)]), MINIMUM)
        self.assertEqual(self._timeout([]), MINIMUM)

    def test_a_job_without_time_limit_gets_the_minimum(self):
        # Its sandbox has no wall limit, so the group cannot be bounded.
        jobs = [_evaluation_job(10.0)] * 25 + [_evaluation_job(None)]
        self.assertEqual(self._timeout(jobs), MINIMUM)


if __name__ == "__main__":
    unittest.main()
