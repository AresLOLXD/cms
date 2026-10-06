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
import json
import unittest
from unittest.mock import patch, PropertyMock

from cms.conf import Address
from cms.service.ProxyService import ProxyService
from cmscommon.constants import SCORE_MODE_MAX
from cmstestsuite.unit_tests.asyncwait import wait_until
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
        scored = self.new_sr_scored()
        result = self.new_sr_scored()
        self.add_token(submission=result.submission)
        # What the sweeper has to send: the score of both of them, and
        # the token of the last one. The unscored submission is not sent.
        self.scored_submissions = [scored.submission, result.submission]
        self.tokened_submission = result.submission

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

    def _put_calls(self) -> list[tuple[str, dict]]:
        """Return the resource and JSON body of each PUT sent so far.

        return: the PUTs in sending order, as pairs of the resource
            taken from the URL (e.g. "contests") and the decoded body.

        """
        return [(args[0].rstrip("/").rsplit("/", 1)[-1], json.loads(args[1]))
                for args, _ in self.requests_put.call_args_list]

    def _sent_submissions_and_subchanges(
        self,
    ) -> tuple[set[str], set[tuple[str, str]]]:
        """Return what the PUTs sent so far carry of the sweeper's data.

        return: the keys of the submissions sent, and the subchanges
            sent as pairs of the submission they refer to and their
            kind ("score" or "token").

        """
        submissions: set[str] = set()
        subchanges: set[tuple[str, str]] = set()
        for resource, body in self._put_calls():
            if resource == "submissions":
                submissions.update(body)
            elif resource == "subchanges":
                subchanges.update(
                    (change["submission"],
                     "token" if change.get("token") else "score")
                    for change in body.values())
        return submissions, subchanges

    async def test_startup(self):
        """Test that data is sent in the right order at startup."""
        await self._build_service()

        expected_submissions = {
            str(submission.id) for submission in self.scored_submissions}
        expected_subchanges = {
            (str(submission.id), "score")
            for submission in self.scored_submissions}
        expected_subchanges.add((str(self.tokened_submission.id), "token"))

        def everything_sent() -> bool:
            submissions, subchanges = self._sent_submissions_and_subchanges()
            return (expected_submissions <= submissions
                    and expected_subchanges <= subchanges)

        def describe() -> str:
            return "PUT %s, submissions and subchanges sent: %s" % (
                [resource for resource, _ in self._put_calls()],
                self._sent_submissions_and_subchanges())

        # The executor sends from a background task, and the sweeper
        # queues its operations one at a time from a worker thread: the
        # executor may wake after the first of them and send it alone,
        # so the PUTs, and how many there are, vary from run to run.
        # Wait for all the data instead of for a number of PUTs.
        await wait_until(everything_sent, describe=describe)

        puts = self._put_calls()
        resources = [resource for resource, _ in puts]
        order = "PUT order: %s" % resources

        # What is guaranteed: the entity types of a batch go out in
        # order, and the batches in the order the operations were queued.
        self.assertEqual(resources[0], "contests", order)
        first_submissions = resources.index("submissions")
        for resource in ("users", "teams", "tasks"):
            self.assertIn(resource, resources[:first_submissions], order)
        # A subchange is sent after the PUT carrying its submission.
        first_put_of_submission: dict[str, int] = dict()
        for index, (resource, body) in enumerate(puts):
            if resource == "submissions":
                for key in body:
                    first_put_of_submission.setdefault(key, index)
        for index, (resource, body) in enumerate(puts):
            if resource == "subchanges":
                for change in body.values():
                    self.assertIn(
                        change["submission"], first_put_of_submission, order)
                    self.assertLess(
                        first_put_of_submission[change["submission"]],
                        index, order)


if __name__ == "__main__":
    unittest.main()
