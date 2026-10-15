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

"""Tests for EvaluationService's busy section in health() (#38).

The sections block on threading.Event objects and the invalidation
stops at its first SessionGen, so only the statement-counting test
reaches the database.

This file is asyncio-only: it must never share a process with the
gevent-based twophase files (see docker/_cms-test-internal.sh).

"""

import asyncio
import contextlib
import threading
import time
import unittest
from unittest.mock import patch

from sqlalchemy import text

from cms.conf import Address
from cms.db import SessionGen
from cms.io.health import BusyTracker
from cms.service.EvaluationService import EvaluationService, \
    with_post_finish_lock
from cmstestsuite.unit_tests.healthfakes import FakeClock, StopHere
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


INVALIDATION_SECTION = "post_finish_lock:_invalidate_submission_sync"


class SectionService(EvaluationService):
    """EvaluationService with sections a test can hold and release."""

    @with_post_finish_lock
    def _blocking_section_sync(self, entered: threading.Event,
                               release: threading.Event,
                               tids: list[int]) -> None:
        tids.append(threading.get_native_id())
        entered.set()
        release.wait(10)

    @with_post_finish_lock
    def _waiting_section_sync(self) -> str:
        return "done"

    @with_post_finish_lock
    def _outer_section_sync(self) -> list[dict]:
        return self._inner_section_sync()

    @with_post_finish_lock
    def _inner_section_sync(self) -> list[dict]:
        return self._busy.report()

    @with_post_finish_lock
    def _query_section_sync(self) -> list[dict]:
        with SessionGen() as session:
            session.execute(text("SELECT 1"))
        return self._busy.report()


class TestEvaluationServiceHealth(
    ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase,
):

    async def asyncSetUp(self):
        # As in evaluationservice_loop_actions_test.py: no Worker,
        # LogService or ScoringService is reachable.
        local_address = Address("127.0.0.1", 0)
        for patcher in (
            patch("cms.util.config.services", {}),
            patch("cms.io.async_service.get_service_address",
                  return_value=local_address),
            patch("cms.io.async_rpc.get_service_address",
                  return_value=local_address),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        with patch.object(
                EvaluationService, "start_sweeper",
                lambda self, timeout: None):
            self.service = SectionService(0)
        self.loop = asyncio.get_running_loop()
        self.service._loop = self.loop
        self.addCleanup(self.service._disconnect_all)
        self.release = threading.Event()
        self.addCleanup(self.release.set)

    async def asyncTearDown(self):
        for task in list(self.service._background_tasks):
            task.cancel()

    async def _wait(self, event: threading.Event):
        deadline = time.monotonic() + 5
        while not event.is_set():
            self.assertLess(time.monotonic(), deadline,
                            "the section did not start")
            await asyncio.sleep(0.01)

    async def test_health_names_the_holder_its_age_thread_and_stack(self):
        clock = FakeClock()
        self.service._busy = BusyTracker(clock=clock)
        entered, tids = threading.Event(), []
        section = self.loop.run_in_executor(
            None, self.service._blocking_section_sync, entered,
            self.release, tids)
        await self._wait(entered)
        clock.now += 31

        answer = self.service.health()

        self.assertEqual(len(answer["busy"]), 1)
        entry = answer["busy"][0]
        self.assertEqual(entry["what"],
                         "post_finish_lock:_blocking_section_sync")
        self.assertEqual(entry["age_s"], 31)
        self.assertEqual(entry["tid"], tids[0])
        self.assertIn("_blocking_section_sync", entry["stack"])
        self.assertEqual(answer["queue"], 0)
        self.release.set()
        await asyncio.wait_for(section, 5)
        self.assertEqual(self.service.health()["busy"], [])

    async def test_a_thread_waiting_for_the_lock_is_not_listed(self):
        entered, tids = threading.Event(), []
        holder = self.loop.run_in_executor(
            None, self.service._blocking_section_sync, entered,
            self.release, tids)
        await self._wait(entered)
        waiter = self.loop.run_in_executor(
            None, self.service._waiting_section_sync)
        await asyncio.sleep(0.2)

        self.assertFalse(waiter.done())
        self.assertEqual(
            [entry["what"] for entry in self.service.health()["busy"]],
            ["post_finish_lock:_blocking_section_sync"])
        self.release.set()
        self.assertEqual(await asyncio.wait_for(waiter, 5), "done")
        await asyncio.wait_for(holder, 5)
        self.assertEqual(self.service.health()["busy"], [])

    async def test_nested_calls_are_one_section_named_by_the_outermost(
            self):
        report = await self.loop.run_in_executor(
            None, self.service._outer_section_sync)
        self.assertEqual([entry["what"] for entry in report],
                         ["post_finish_lock:_outer_section_sync"])

    async def test_the_section_ends_even_when_the_flush_raises(self):
        with patch.object(self.service, "_flush_loop_actions",
                          side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                await self.loop.run_in_executor(
                    None, self.service._waiting_section_sync)
        self.assertEqual(self.service.health()["busy"], [])

    async def test_an_invalidation_is_the_section_the_watchdog_spares(
            self):
        entered = threading.Event()

        @contextlib.contextmanager
        def blocked_session():
            entered.set()
            self.release.wait(10)
            raise StopHere()
            yield  # Unreachable: it makes this a generator.

        with patch("cms.service.EvaluationService.SessionGen",
                   blocked_session):
            invalidation = asyncio.ensure_future(
                self.service.invalidate_submission(level="compilation"))
            await self._wait(entered)
            busy = self.service.health()["busy"]
            self.release.set()
            with self.assertRaises(StopHere):
                await asyncio.wait_for(invalidation, 5)

        self.assertEqual([entry["what"] for entry in busy],
                         [INVALIDATION_SECTION])
        self.assertEqual(self.service.health()["busy"], [])

    async def test_the_statements_of_a_section_are_counted(self):
        report = await self.loop.run_in_executor(
            None, self.service._query_section_sync)
        self.assertEqual(len(report), 1)
        self.assertGreaterEqual(report[0]["statements"], 1)

    def test_the_sweeper_period_is_a_constant(self):
        self.assertEqual(EvaluationService.SWEEPER_PERIOD_S, 117.0)


if __name__ == "__main__":
    unittest.main()
