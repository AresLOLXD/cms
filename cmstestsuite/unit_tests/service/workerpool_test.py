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
from unittest.mock import AsyncMock, MagicMock, patch

from cms.conf import ServiceCoord
from cms.io.async_rpc import AsyncFakeRemoteServiceClient
from cms.service.workerpool import WorkerPool
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


if __name__ == "__main__":
    unittest.main()
