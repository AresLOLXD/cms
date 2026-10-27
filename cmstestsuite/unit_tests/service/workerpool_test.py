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
import json
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from cms.conf import ServiceCoord
from cms.io.async_rpc import AsyncFakeRemoteServiceClient
from cms.service.esoperations import ESOperation
from cms.service.workerpool import (
    JOB_OVERHEAD_S, WorkerPool, job_group_timeout)
from cmscommon.datetime import make_datetime, make_timestamp
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

        service.action_finished.assert_awaited_once_with(
            {"jobs": []}, 0, None, dispatch_id=None)


def _pool(workers: int = 1) -> WorkerPool:
    """Return a pool with connected workers and nothing dispatched."""
    service = MagicMock()
    service._loop = None
    service.contest_id = None
    # Nothing is dispatched for real: drop the spawned coroutines.
    service._spawn.side_effect = lambda coroutine: coroutine.close()
    service.connect_to.side_effect = \
        lambda coord, on_connect: MagicMock(connected=True)
    pool = WorkerPool(service)
    for shard in range(workers):
        pool.add_worker(ServiceCoord("Worker", shard))
    return pool


def _operation(testcase_codename: str) -> ESOperation:
    return ESOperation(ESOperation.EVALUATION, 42, 7, testcase_codename)


def _busy_for(pool: WorkerPool, shard: int, seconds: float) -> None:
    """Make a worker look acquired and dispatched `seconds` ago."""
    started = make_datetime() - timedelta(seconds=seconds)
    pool._start_time[shard] = started
    pool._dispatched_at[shard] = started


class TestDuplicateOperation(unittest.TestCase):
    """The same operation assigned to two workers at once."""

    def setUp(self):
        self.pool = _pool(2)

    def test_releasing_both_workers_forgets_every_operation(self):
        duplicate = _operation("001")
        only_first = _operation("002")
        only_second = _operation("003")
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


class TestDispatchIdOfTheAnswer(unittest.IsolatedAsyncioTestCase):

    async def test_dispatch_passes_its_id_to_action_finished(self):
        service = MagicMock()
        service.action_finished = AsyncMock()
        pool = WorkerPool(service)
        worker = MagicMock()
        worker.execute_job_group = AsyncMock(return_value={"jobs": []})
        pool._worker[0] = worker

        await pool._dispatch_to_worker(0, {"jobs": []}, 5)

        service.action_finished.assert_awaited_once_with(
            {"jobs": []}, 0, None, dispatch_id=5)


class TestDispatchingTheJobGroup(unittest.IsolatedAsyncioTestCase):
    """The two places where dispatching feeds check_timeouts."""

    def setUp(self):
        self.pool = _pool()
        self.operations = [_operation("001")]
        self.shard = self.pool.acquire_worker(self.operations)
        self.dispatch = self.pool._current_dispatch[self.shard]
        self.pool._service.action_finished = AsyncMock()

    async def test_dispatching_marks_the_worker_as_answered(self):
        self.pool._worker[self.shard].execute_job_group = \
            AsyncMock(return_value={"jobs": []})
        self.assertIsNone(self.pool._answered_at[self.shard])

        await self.pool._dispatch_to_worker(
            self.shard, {"jobs": []}, self.dispatch)

        self.assertIsNotNone(self.pool._answered_at[self.shard])

    async def test_the_timeout_is_set_before_the_worker_is_asked(self):
        timeout_during_the_call = []

        async def execute_job_group(job_group_dict):
            timeout_during_the_call.append(self.pool._timeout[self.shard])
            return {"jobs": []}

        self.pool._worker[self.shard].execute_job_group = execute_job_group

        with patch.object(self.pool, "_build_job_group_dict",
                          return_value=TEN_SECOND_EVALUATIONS), \
                patch("cms.service.workerpool.config") as config:
            config.sandbox.compilation_sandbox_max_time_s = 20.0
            config.sandbox.trusted_sandbox_max_time_s = 10.0
            await self.pool._build_and_dispatch(
                self.shard, self.operations, self.dispatch)

        self.assertEqual(timeout_during_the_call, [timedelta(seconds=1825)])


class TestDispatchIds(unittest.TestCase):
    """Each job group has an id; an answer with another id is stale."""

    def setUp(self):
        self.pool = _pool()

    def test_each_job_group_gets_a_new_id(self):
        shard = self.pool.acquire_worker([_operation("001")])
        first = self.pool._current_dispatch[shard]
        self.assertIsNotNone(first)
        self.pool.release_worker(shard, first)
        self.pool.acquire_worker([_operation("002")])
        self.assertNotEqual(self.pool._current_dispatch[shard], first)

    def test_the_current_answer_releases_the_worker(self):
        shard = self.pool.acquire_worker([_operation("001")])
        dispatch = self.pool._current_dispatch[shard]

        self.assertIs(self.pool.release_worker(shard, dispatch), False)

        self.assertIs(self.pool._operations[shard],
                      WorkerPool.WORKER_INACTIVE)
        self.assertIsNone(self.pool._current_dispatch[shard])

    def test_a_stale_answer_does_not_release_the_current_job_group(self):
        shard = self.pool.acquire_worker([_operation("001")])
        old = self.pool._current_dispatch[shard]
        # Something else released the worker (e.g. the timeout), and it
        # took a new job group before the old answer arrived.
        self.pool.release_worker(shard)
        self.assertEqual(self.pool.acquire_worker([_operation("002")]), shard)
        current = self.pool._current_dispatch[shard]

        with self.assertLogs("cms.service.workerpool", level="INFO") as logs:
            self.assertIs(self.pool.release_worker(shard, old), True)

        self.assertIn("stale answer", "\n".join(logs.output))
        self.assertEqual(self.pool._operations[shard], [_operation("002")])
        self.assertEqual(self.pool._current_dispatch[shard], current)

    def test_an_answer_after_check_connections_released_the_worker_is_ignored(
            self):
        operation = _operation("001")
        shard = self.pool.acquire_worker([operation])
        dispatch = self.pool._current_dispatch[shard]
        self.pool._worker[shard].connected = False
        self.assertEqual(self.pool.check_connections(), [operation])

        # The RPC error of the dropped connection arrives afterwards.
        self.assertIs(self.pool.release_worker(shard, dispatch), True)

        self.assertIs(self.pool._operations[shard],
                      WorkerPool.WORKER_INACTIVE)

    def test_a_release_without_id_behaves_as_before(self):
        shard = self.pool.acquire_worker([_operation("001")])
        self.assertIs(self.pool.release_worker(shard), False)
        with self.assertLogs("cms.service.workerpool", level="ERROR"):
            with self.assertRaises(ValueError):
                self.pool.release_worker(shard)


TEN_SECOND_EVALUATIONS = {
    "jobs": [{"type": "evaluation", "time_limit": 10.0}] * 25}


class TestTimeouts(unittest.TestCase):
    """When check_timeouts gives up on a worker."""

    def setUp(self):
        self.pool = _pool()
        self.operation = _operation("001")
        self.shard = self.pool.acquire_worker([self.operation])
        self.dispatch = self.pool._current_dispatch[self.shard]

    def _set_ten_second_timeout(self, dispatch_id: int | None):
        with patch("cms.service.workerpool.config") as config:
            config.sandbox.compilation_sandbox_max_time_s = 20.0
            config.sandbox.trusted_sandbox_max_time_s = 10.0
            self.pool._set_timeout(
                self.shard, dispatch_id, TEN_SECOND_EVALUATIONS)

    def test_a_worker_past_its_timeout_is_disabled(self):
        _busy_for(self.pool, self.shard, 601)

        with self.assertLogs("cms.service.workerpool", level="ERROR") as logs:
            lost = self.pool.check_timeouts()

        self.assertEqual(lost, [self.operation])
        self.assertEqual(self.pool._operations[self.shard],
                         WorkerPool.WORKER_DISABLED)
        self.assertIn("(timeout 0:10:00)", logs.output[0])

    def test_a_worker_within_its_derived_timeout_is_not_disabled(self):
        self._set_ten_second_timeout(self.dispatch)
        self.assertEqual(self.pool._timeout[self.shard],
                         timedelta(seconds=1825))

        _busy_for(self.pool, self.shard, 1000)
        self.assertEqual(self.pool.check_timeouts(), [])
        self.assertEqual(self.pool._operations[self.shard], [self.operation])

        _busy_for(self.pool, self.shard, 1826)
        with self.assertLogs(
                "cms.service.workerpool", level="ERROR") as logs:
            self.assertEqual(self.pool.check_timeouts(), [self.operation])
        self.assertIn("(timeout 0:30:25)", logs.output[0])

    def test_the_timeout_of_a_stale_dispatch_is_not_stored(self):
        self._set_ten_second_timeout(self.dispatch + 1)
        self.assertEqual(self.pool._timeout[self.shard],
                         WorkerPool.WORKER_TIMEOUT)

    def test_a_timeout_that_cannot_be_computed_keeps_the_default(self):
        with self.assertLogs("cms.service.workerpool", level="ERROR"):
            self.pool._set_timeout(self.shard, self.dispatch, {"no": "jobs"})
        self.assertEqual(self.pool._timeout[self.shard],
                         WorkerPool.WORKER_TIMEOUT)

    def test_an_answered_worker_is_not_timed_out(self):
        _busy_for(self.pool, self.shard, 10_000)
        self.pool._mark_answered(self.shard, self.dispatch)

        self.assertIsNotNone(self.pool._answered_at[self.shard])
        self.assertEqual(self.pool.check_timeouts(), [])
        self.assertEqual(self.pool._operations[self.shard], [self.operation])

    def test_an_answer_not_handled_in_time_disables_the_worker(self):
        self.pool._mark_answered(self.shard, self.dispatch)
        self.pool._answered_at[self.shard] = (
            make_datetime() - WorkerPool.ANSWER_HANDLING_TIMEOUT
            - timedelta(seconds=1))

        with self.assertLogs("cms.service.workerpool", level="ERROR") as logs:
            lost = self.pool.check_timeouts()

        self.assertEqual(lost, [self.operation])
        self.assertEqual(self.pool._operations[self.shard],
                         WorkerPool.WORKER_DISABLED)
        self.assertIn("was not handled", logs.output[0])

    def test_a_stale_answer_does_not_mark_the_current_dispatch(self):
        self.pool._mark_answered(self.shard, self.dispatch + 1)
        self.assertIsNone(self.pool._answered_at[self.shard])

    def test_the_release_clears_the_answer(self):
        self.pool._mark_answered(self.shard, self.dispatch)
        self.pool.release_worker(self.shard, self.dispatch)
        self.assertIsNone(self.pool._answered_at[self.shard])

    def test_marking_an_unknown_worker_does_nothing(self):
        self.pool._mark_answered(99, 1)
        self.pool._mark_answered(self.shard, None)
        self.assertIsNone(self.pool._answered_at[self.shard])


class TestTheClockStartsAtDispatch(unittest.TestCase):
    """A worker is timed from the moment its job group is sent."""

    def setUp(self):
        self.now = make_datetime()
        patcher = patch("cms.service.workerpool.make_datetime",
                        side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pool = _pool()
        self.operation = _operation("001")
        self.shard = self.pool.acquire_worker([self.operation])
        self.dispatch = self.pool._current_dispatch[self.shard]

    def _advance(self, seconds: float):
        self.now += timedelta(seconds=seconds)

    def test_an_undispatched_worker_gets_the_longer_bound(self):
        # Its job group may be waiting for an executor thread: not its
        # fault, but not for ever either.
        self.assertIsNone(self.pool._dispatched_at[self.shard])

        self._advance(601)
        self.assertEqual(self.pool.check_timeouts(), [])

        self._advance(WorkerPool.ANSWER_HANDLING_TIMEOUT.total_seconds())
        with self.assertLogs("cms.service.workerpool", level="ERROR") as logs:
            self.assertEqual(self.pool.check_timeouts(), [self.operation])
        self.assertIn("(timeout 0:30:00)", logs.output[0])

    def test_a_dispatched_worker_is_timed_from_the_dispatch(self):
        self._advance(700)
        self.pool._set_timeout(self.shard, self.dispatch, {"jobs": []})

        # 1299 s after the acquire, 599 s after the dispatch.
        self._advance(599)
        self.assertEqual(self.pool.check_timeouts(), [])

        self._advance(2)
        with self.assertLogs("cms.service.workerpool", level="ERROR") as logs:
            self.assertEqual(self.pool.check_timeouts(), [self.operation])
        self.assertIn("(timeout 0:10:00)", logs.output[0])

    def test_setting_the_timeout_starts_the_clock(self):
        self._advance(5)
        self.pool._set_timeout(self.shard, self.dispatch, {"jobs": []})

        self.assertEqual(self.pool._dispatched_at[self.shard], self.now)
        # The Overview's start time is still the acquire.
        self.assertEqual(self.pool._start_time[self.shard],
                         self.now - timedelta(seconds=5))

    def test_the_clock_starts_even_if_the_timeout_cannot_be_computed(self):
        with self.assertLogs("cms.service.workerpool", level="ERROR"):
            self.pool._set_timeout(self.shard, self.dispatch, {"no": "jobs"})

        self.assertEqual(self.pool._dispatched_at[self.shard], self.now)
        self.assertEqual(self.pool._timeout[self.shard],
                         WorkerPool.WORKER_TIMEOUT)

    def test_a_stale_dispatch_does_not_start_the_clock(self):
        self.pool._set_timeout(self.shard, self.dispatch + 1, {"jobs": []})
        self.assertIsNone(self.pool._dispatched_at[self.shard])

    def test_the_release_stops_the_clock(self):
        self.pool._set_timeout(self.shard, self.dispatch, {"jobs": []})
        self.pool.release_worker(self.shard, self.dispatch)
        self.assertIsNone(self.pool._dispatched_at[self.shard])


class TestReenableOnReconnect(unittest.TestCase):
    """A worker the timeout disabled comes back when it reconnects."""

    def setUp(self):
        self.pool = _pool()
        self.coord = ServiceCoord("Worker", 0)

    def _time_out(self) -> int:
        shard = self.pool.acquire_worker([_operation("001")])
        _busy_for(self.pool, shard, 601)
        with self.assertLogs("cms.service.workerpool", level="ERROR"):
            self.pool.check_timeouts()
        self.assertEqual(self.pool._operations[shard],
                         WorkerPool.WORKER_DISABLED)
        return shard

    def test_a_worker_disabled_by_the_timeout_is_enabled_on_reconnect(self):
        shard = self._time_out()
        self.assertTrue(self.pool._disabled_by_timeout[shard])

        with self.assertLogs(
                "cms.service.workerpool", level="WARNING") as logs:
            self.pool.on_worker_connected(self.coord)

        self.assertIs(self.pool._operations[shard],
                      WorkerPool.WORKER_INACTIVE)
        self.assertFalse(self.pool._disabled_by_timeout[shard])
        self.assertIn("enabling it again", "\n".join(logs.output))

    def test_the_answer_of_the_lost_job_group_is_ignored_after_the_return(
            self):
        shard = self.pool.acquire_worker([_operation("001")])
        old_dispatch = self.pool._current_dispatch[shard]
        _busy_for(self.pool, shard, 601)
        with self.assertLogs("cms.service.workerpool", level="ERROR"):
            self.pool.check_timeouts()
        self.assertEqual(self.pool._operations[shard],
                         WorkerPool.WORKER_DISABLED)
        with self.assertLogs("cms.service.workerpool", level="WARNING"):
            self.pool.on_worker_connected(self.coord)
        new_operation = _operation("002")
        self.assertEqual(self.pool.acquire_worker([new_operation]), shard)
        new_dispatch = self.pool._current_dispatch[shard]
        self.assertNotEqual(new_dispatch, old_dispatch)

        # The answer of the lost job group arrives late.
        with self.assertLogs("cms.service.workerpool", level="INFO"):
            self.assertIs(self.pool.release_worker(shard, old_dispatch), True)

        self.assertEqual(self.pool._operations[shard], [new_operation])
        self.assertEqual(self.pool._current_dispatch[shard], new_dispatch)
        self.assertIs(self.pool.release_worker(shard, new_dispatch), False)

    def test_a_worker_disabled_after_its_answer_waited_comes_back_too(self):
        shard = self.pool.acquire_worker([_operation("001")])
        self.pool._mark_answered(shard, self.pool._current_dispatch[shard])
        self.pool._answered_at[shard] = (
            make_datetime() - WorkerPool.ANSWER_HANDLING_TIMEOUT
            - timedelta(seconds=1))
        with self.assertLogs("cms.service.workerpool", level="ERROR"):
            self.pool.check_timeouts()

        with self.assertLogs("cms.service.workerpool", level="WARNING"):
            self.pool.on_worker_connected(self.coord)

        self.assertIs(self.pool._operations[shard],
                      WorkerPool.WORKER_INACTIVE)

    def test_a_worker_disabled_by_an_operator_stays_disabled(self):
        self.pool.disable_worker(0)

        self.pool.on_worker_connected(self.coord)

        self.assertEqual(self.pool._operations[0], WorkerPool.WORKER_DISABLED)
        self.assertFalse(self.pool._disabled_by_timeout[0])

    def test_disabling_a_worker_the_timeout_disabled_keeps_it_disabled(self):
        # An operator presses Disable on a stale page.
        shard = self._time_out()

        with self.assertLogs("cms.service.workerpool", level="WARNING"):
            with self.assertRaises(ValueError):
                self.pool.disable_worker(shard)
        self.pool.on_worker_connected(self.coord)

        self.assertEqual(self.pool._operations[shard],
                         WorkerPool.WORKER_DISABLED)
        self.assertFalse(self.pool._disabled_by_timeout[shard])
        self.assertIsNone(self.pool._disabled_at[shard])

    def test_enabling_by_hand_clears_the_mark(self):
        shard = self._time_out()
        self.pool.enable_worker(shard)
        self.assertFalse(self.pool._disabled_by_timeout[shard])

    def test_the_first_connection_changes_nothing(self):
        with self.assertNoLogs("cms.service.workerpool", level="WARNING"):
            self.pool.on_worker_connected(self.coord)
        self.assertIs(self.pool._operations[0], WorkerPool.WORKER_INACTIVE)


class TestStatusOfDisabledWorkers(unittest.TestCase):
    """The fields cmsWatchdog (#38) reads from workers_status."""

    def setUp(self):
        self.pool = _pool()
        self.coord = ServiceCoord("Worker", 0)

    def _time_out(self) -> int:
        shard = self.pool.acquire_worker([_operation("001")])
        _busy_for(self.pool, shard, 601)
        with self.assertLogs("cms.service.workerpool", level="ERROR"):
            self.pool.check_timeouts()
        return shard

    def test_a_new_worker_is_not_disabled(self):
        status = self.pool.get_status()["0"]
        self.assertIs(status["disabled_by_timeout"], False)
        self.assertIsNone(status["disabled_at"])

    def test_the_timeout_sets_disabled_at(self):
        before = make_timestamp(make_datetime())
        self._time_out()
        after = make_timestamp(make_datetime())

        status = self.pool.get_status()["0"]

        self.assertIs(status["disabled_by_timeout"], True)
        self.assertGreaterEqual(status["disabled_at"], before)
        self.assertLessEqual(status["disabled_at"], after)
        # The RPC answer must stay JSON.
        json.dumps(self.pool.get_status())

    def test_reconnecting_clears_disabled_at(self):
        self._time_out()
        with self.assertLogs("cms.service.workerpool", level="WARNING"):
            self.pool.on_worker_connected(self.coord)
        status = self.pool.get_status()["0"]
        self.assertIs(status["disabled_by_timeout"], False)
        self.assertIsNone(status["disabled_at"])

    def test_enabling_or_disabling_by_hand_clears_disabled_at(self):
        shard = self._time_out()
        self.pool.enable_worker(shard)
        self.assertIsNone(self.pool.get_status()["0"]["disabled_at"])

        self._time_out()
        self.pool.enable_worker(shard)
        self.pool.disable_worker(shard)
        status = self.pool.get_status()["0"]
        self.assertIs(status["disabled_by_timeout"], False)
        self.assertIsNone(status["disabled_at"])


if __name__ == "__main__":
    unittest.main()
