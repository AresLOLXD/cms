#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2015 Stefano Maggiolo <s.maggiolo@gmail.com>
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

"""Tests for the scoring service.

"""

import asyncio
import unittest
from unittest.mock import patch, PropertyMock

from cms.conf import Address
from cms.service.ProxyService import ProxyService
from cmscommon.constants import SCORE_MODE_MAX
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


class TestProxyService(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    def setUp(self):
        super().setUp()

        patcher = patch("cms.db.Dataset.score_type_object",
                        new_callable=PropertyMock)
        self.score_type = patcher.start().return_value
        self.addCleanup(patcher.stop)
        self.score_type.max_score = 100
        self.score_type.ranking_headers = ["100"]

        patcher = patch("requests.put")
        self.requests_put = patcher.start()
        self.addCleanup(patcher.stop)
        self.requests_put.return_value.status_code = 200

        self.contest = self.add_contest()
        self.contest.score_precision = 2

        self.task = self.add_task(contest=self.contest)
        self.task.score_precision = 2
        self.task.score_mode = SCORE_MODE_MAX
        self.dataset = self.add_dataset(task=self.task)
        self.task.active_dataset = self.dataset

        self.team = self.add_team()
        self.user = self.add_user()
        self.participation = self.add_participation(user=self.user,
                                                    contest=self.contest,
                                                    team=self.team)

        self.new_sr_unscored()
        self.new_sr_scored()
        result = self.new_sr_scored()
        self.add_token(submission=result.submission)

        self.session.commit()

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

    def new_sr_unscored(self):
        submission = self.add_submission(task=self.task,
                                         participation=self.participation)
        result = self.add_submission_result(submission=submission,
                                            dataset=self.dataset)
        result.compilation_outcome = "ok"
        result.evaluation_outcome = "ok"
        return result

    def new_sr_scored(self):
        result = self.new_sr_unscored()
        result.score = 100
        result.score_details = dict()
        result.public_score = 50
        result.public_score_details = dict()
        result.ranking_score_details = ["100"]
        return result

    async def _wait_until(self, predicate, attempts: int = 50) -> None:
        """Poll predicate() until it is true, or give up.

        The background executor.run() task (spawned by
        ProxyService.__init__'s add_executor, since a running loop
        already exists when the service is constructed here) processes
        enqueued operations asynchronously, so assertions on their
        effects (the mocked HTTP calls) need to wait for it.

        predicate: a zero-argument callable to poll.
        attempts: how many times to poll, sleeping 0.05s between tries.

        """
        for _ in range(attempts):
            if predicate():
                return
            await asyncio.sleep(0.05)

    async def _build_service(self) -> ProxyService:
        """Build a ProxyService safe to drive from a running loop.

        Mirrors proxyservice_groups_test.py's start(): constructs the
        service while this test's own event loop is already running
        (so __init__'s initialize() call enqueues its initial contest/
        user/task batch synchronously, since self._loop is still None
        at that point), sets self._loop explicitly afterwards so any
        later _threadsafe_enqueue call takes its call_soon_threadsafe
        branch as it would in production, and stubs out start_sweeper
        so its background sweep can't race with the explicit
        _missing_operations() call below -- which does the same work
        the sweeper's first run would do in production, sending the
        already-scored/tokened submissions this test asserts on.

        """
        with patch.object(
                ProxyService, "start_sweeper", lambda self, timeout: None):
            service = ProxyService(0, self.contest.id)
        service._loop = asyncio.get_running_loop()
        self.addCleanup(service._disconnect_all)
        await service._missing_operations()
        return service

    async def test_startup(self):
        """Test that data is sent in the right order at startup."""
        await self._build_service()

        await self._wait_until(
            lambda: len(self.requests_put.call_args_list) >= 6)

        urls = [args[0] for args, _ in self.requests_put.call_args_list]

        self.assertTrue(urls[0].endswith("contests/"))
        self.assertTrue(any(urls[i].endswith("users/") for i in [1, 2, 3]))
        self.assertTrue(any(urls[i].endswith("teams/") for i in [1, 2, 3]))
        self.assertTrue(any(urls[i].endswith("tasks/") for i in [1, 2, 3]))
        self.assertTrue(urls[4].endswith("submissions/"))
        self.assertTrue(urls[5].endswith("subchanges/"))


if __name__ == "__main__":
    unittest.main()
