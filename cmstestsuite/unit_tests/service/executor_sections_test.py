#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
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

"""Tests for the busy sections of ScoringService and ProxyService (#38).

No database is needed: the scoring stops at its first SessionGen, the
rankings are mocked, and the services are built with their database
work patched out.

"""

import contextlib
import threading
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import requests

from cms.conf import Address
from cms.io.health import BusyTracker
from cms.io.priorityqueue import QueueEntry
from cms.service.ProxyService import ProxyExecutor, ProxyOperation, \
    ProxyService
from cms.service.ScoringService import ScoringExecutor, ScoringService
from cms.service.scoringoperations import ScoringOperation
from cmstestsuite.unit_tests.healthfakes import FakeClock, StopHere
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


RANKING = "http://rws:secret@localhost:8890/"
LOCAL = Address("127.0.0.1", 0)


def _entries(*operations: ProxyOperation) -> list[QueueEntry]:
    return [QueueEntry(operation, 0, datetime.now(), index)
            for index, operation in enumerate(operations)]


def _local_addresses() -> contextlib.ExitStack:
    """Patch the service addresses to a free loopback port."""
    stack = contextlib.ExitStack()
    stack.enter_context(patch("cms.io.async_service.get_service_address",
                              return_value=LOCAL))
    stack.enter_context(patch("cms.io.async_rpc.get_service_address",
                              return_value=LOCAL))
    return stack


class TestScoringSection(unittest.TestCase):

    def test_a_blocked_execution_is_the_section_score(self):
        tracker = BusyTracker()
        executor = ScoringExecutor(proxy_service=None, busy=tracker)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        errors: list[Exception] = []

        @contextlib.contextmanager
        def blocked_session():
            entered.set()
            release.wait(10)
            raise StopHere()
            yield  # Unreachable: it makes this a generator.

        def score():
            try:
                executor._execute_sync(ScoringOperation(1, 1))
            except StopHere as error:
                errors.append(error)

        with patch("cms.service.ScoringService.SessionGen",
                   blocked_session):
            thread = threading.Thread(target=score, daemon=True)
            thread.start()
            self.assertTrue(entered.wait(5))
            self.assertEqual([entry["what"] for entry in tracker.report()],
                             ["score"])
            release.set()
            thread.join(5)

        self.assertEqual(len(errors), 1)
        self.assertEqual(tracker.report(), [])

    def test_an_executor_built_without_a_tracker_has_its_own(self):
        self.assertIsInstance(
            ScoringExecutor(proxy_service=None)._busy, BusyTracker)


class TestServicesGiveTheirTracker(
    ServiceLoggingIsolationMixin, unittest.TestCase,
):
    """Built with no event loop running, so nothing starts."""

    def test_scoring_service(self):
        with _local_addresses(), patch.object(
                ScoringService, "start_sweeper",
                lambda self, timeout: None):
            service = ScoringService(0)
        self.assertIs(service.get_executor()._busy, service._busy)

    def test_proxy_service(self):
        with _local_addresses(), \
                patch.object(ProxyService, "start_sweeper",
                             lambda self, timeout: None), \
                patch.object(ProxyService, "_compute_group_contests",
                             lambda self, session: {}), \
                patch.object(ProxyService, "initialize",
                             lambda self: None):
            service = ProxyService(0)
        self.assertTrue(service._executors)
        for executor in service._executors:
            self.assertIs(executor._busy, service._busy)


class TestProxySection(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        put_patcher = patch("cms.service.ProxyService.requests.put")
        self.requests_put = put_patcher.start()
        self.addCleanup(put_patcher.stop)
        self.requests_put.return_value.status_code = 200
        self.clock = FakeClock()
        self.tracker = BusyTracker(clock=self.clock)
        self.executor = ProxyExecutor(RANKING, busy=self.tracker)

    def test_a_blocked_send_is_the_section_send(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def blocked_put(*args, **kwargs):
            entered.set()
            release.wait(10)
            return MagicMock(status_code=200)

        self.requests_put.side_effect = blocked_put
        batch = _entries(
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u1": {}}, None))
        thread = threading.Thread(target=self.executor._execute_sync,
                                  args=(batch,), daemon=True)
        thread.start()
        self.assertTrue(entered.wait(5))

        self.assertEqual(
            [(entry["what"], entry["statements"])
             for entry in self.tracker.report()],
            [("send", 1)])
        release.set()
        thread.join(5)
        self.assertEqual(self.tracker.report(), [])

    def test_each_request_is_a_step(self):
        seen: list[tuple] = []

        def put(*args, **kwargs):
            entry = self.tracker.report()[0]
            seen.append(
                (entry["statements"], entry["idle_s"], entry["age_s"]))
            self.clock.now += 10
            return MagicMock(status_code=200)

        self.requests_put.side_effect = put
        unsent = self.executor._execute_sync(_entries(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c1": {}}, None),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u1": {}}, None)))

        self.assertEqual(unsent, [])
        self.assertEqual(seen, [(1, 0, 0), (2, 0, 10)])
        self.assertEqual(self.tracker.report(), [])

    async def test_the_retry_wait_is_outside_the_section(self):
        self.requests_put.side_effect = \
            requests.exceptions.ConnectionError("down")
        during_wait: list[list[dict]] = []

        async def wait_for_retry(delay: float):
            during_wait.append(self.tracker.report())

        with patch.object(self.executor, "_wait_for_retry",
                          wait_for_retry):
            with self.assertLogs("cms.service.ProxyService",
                                 level="WARNING"):
                await self.executor.execute(_entries(ProxyOperation(
                    ProxyExecutor.USER_TYPE, {"u1": {}}, None)))

        self.assertEqual(during_wait, [[]])

    def test_an_executor_built_without_a_tracker_has_its_own(self):
        self.assertIsInstance(ProxyExecutor(RANKING)._busy, BusyTracker)


if __name__ == "__main__":
    unittest.main()
