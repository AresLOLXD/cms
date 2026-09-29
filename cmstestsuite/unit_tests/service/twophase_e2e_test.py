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

"""End-to-end tests of two-phase evaluation on a real EvaluationService.

The other twophase_* tests each exercise one piece of the feature (the
gate in submission_get_operations(), skip synthesis, write_results(),
the sweeper) on its own. These drive the whole flow: a real
EvaluationService with two_phase_evaluation on, its real executor and
worker pool, and a scripted Worker peer that answers evaluation jobs
with an outcome chosen by testcase codename. The submission goes from
new_submission() through compilation, screening, the phase 2 release (or
the skip synthesis) up to the notification of ScoringService, and the
final score is computed with a real GroupMin score type.

This file is asyncio-only: it must never share a process with the
gevent-based twophase files (see docker/_cms-test-internal.sh).

"""

import asyncio
import math
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from cms import config
from cms.conf import Address, ServiceCoord
from cms.db import Submission
from cms.grading.steps import EVALUATION_MESSAGES
from cms.io.async_rpc import AsyncRemoteServiceClient
from cms.io.rpc import rpc_method
from cms.service.esoperations import MAX_EVALUATION_TRIES, ESOperation
from cms.service.EvaluationService import EvaluationService
from cms.service.workerpool import WorkerPool
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin
from cmstestsuite.unit_tests.service.EvaluationService_test import \
    FakeScoringService, FakeWorker, _start_server


# The task used by every test: two groups (subtasks) of 50 points each.
# Screening cases are the ones tagged "sample" or containing "scr".
SCREENING_CODENAMES = ("s1-00-sample", "s1-01-scr-wa", "s2-00-sample")
PHASE_TWO_CODENAMES = ("s1-02-normal", "s2-01-normal")
ALL_CODENAMES = SCREENING_CODENAMES + PHASE_TWO_CODENAMES
SCORE_TYPE_PARAMETERS = [[50, "s1-.*"], [50, "s2-.*"]]

SKIP_TEXT = [EVALUATION_MESSAGES.get("skipped").message]


class ScriptedWorker(FakeWorker):
    """A FakeWorker answering evaluation jobs with scripted outcomes.

    FakeWorker leaves the outcome of evaluation jobs unset. This one
    picks it by testcase codename, and can also hold its answers back,
    report a failed job, or crash on a whole job group, to script what
    happens around a worker failure.

    """

    def __init__(self, outcomes: dict[str, str] | None = None):
        """Create the worker.

        outcomes: outcome to answer for each codename; any codename
            not listed here gets "1.0".

        """
        super().__init__()
        self.outcomes = outcomes or {}

        # (submission id, codename) of every evaluation job received,
        # grouped by the job group they arrived in.
        self.evaluation_batches: list[list[tuple[int, str]]] = []

        # Clear it to hold back the answer to every evaluation job group
        # (the worker then looks busy to the pool).
        self.evaluations_released = asyncio.Event()
        self.evaluations_released.set()

        # For each codename, how many times to answer with a failed job
        # (success=False, as a worker does on an internal error) before
        # answering normally.
        self.job_failures_left: dict[str, float] = {}

        # Whether to lose the evaluation job groups with an RPC error
        # instead of answering, as a crashing worker would.
        self.lose_evaluation_batches = False

    def dispatched(self) -> list[tuple[int, str]]:
        """Return every (submission id, codename) received, in order."""
        return [item for batch in self.evaluation_batches for item in batch]

    @rpc_method
    async def execute_job_group(self, job_group_dict: dict) -> dict:
        batch = [
            (job["operation"]["object_id"],
             job["operation"]["testcase_codename"])
            for job in job_group_dict["jobs"] if job["type"] == "evaluation"]
        if batch:
            self.evaluation_batches.append(batch)
            await self.evaluations_released.wait()
            if self.lose_evaluation_batches:
                raise RuntimeError("simulated worker crash")

        answer = await super().execute_job_group(job_group_dict)

        for job in answer["jobs"]:
            if job["type"] != "evaluation":
                continue
            codename = job["operation"]["testcase_codename"]
            if self.job_failures_left.get(codename, 0) > 0:
                self.job_failures_left[codename] -= 1
                job["success"] = False
                continue
            job["outcome"] = self.outcomes.get(codename, "1.0")
            job["text"] = [
                "Output is correct" if float(job["outcome"]) > 0
                else "Output isn't correct"]
            job["plus"] = {
                "execution_time": 0.1,
                "execution_wall_clock_time": 0.1,
                "execution_memory": 1024,
            }
        return answer


@dataclass
class Fixture:
    """A contest with the two-group task and one submission of it."""

    contest: object
    task: object
    dataset: object
    submission: object

    def operation(self, codename: str) -> ESOperation:
        """Return the evaluation operation of the submission on codename."""
        return ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            codename)

    def compilation_operation(self) -> ESOperation:
        """Return the compilation operation of the submission."""
        return ESOperation(
            ESOperation.COMPILATION, self.submission.id, self.dataset.id)

    @property
    def key(self) -> tuple[int, int]:
        """Return the (submission id, dataset id) ScoringService gets."""
        return (self.submission.id, self.dataset.id)


class TwoPhaseEndToEndTest(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    async def asyncSetUp(self):
        self._servers: list[asyncio.Server] = []
        self._clients: list[AsyncRemoteServiceClient] = []
        self._peer_tasks: list[asyncio.Task] = []
        self._workers: list[ScriptedWorker] = []
        self.service: EvaluationService | None = None

        # See EvaluationService_test.py's asyncSetUp for why these are
        # needed: no real Worker or ScoringService is reachable, tests
        # wire their own peers.
        local_address = Address("127.0.0.1", 0)
        for patcher in (
            patch("cms.util.config.services", {}),
            patch("cms.io.async_service.get_service_address",
                  return_value=local_address),
            patch("cms.io.async_rpc.get_service_address",
                  return_value=local_address),
            patch.object(config.global_, "two_phase_evaluation", True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        # Stop the service first, so nothing new starts while peers and
        # data are being torn down.
        if self.service is not None:
            for task in list(self.service._background_tasks):
                task.cancel()
        for worker in self._workers:
            worker.evaluations_released.set()
        for client in self._clients:
            client.disconnect()
        for task in self._peer_tasks:
            task.cancel()
        for server in self._servers:
            server.close()
            await server.wait_closed()

    def tearDown(self):
        # The service writes with its own sessions, so the fixtures are
        # committed: clean them up, or later tests (the sweeper looks at
        # every submission of the DB) would see them.
        self.delete_data()
        super().tearDown()

    # -- harness ---------------------------------------------------------

    async def _connect_peer(
        self, coord: ServiceCoord, local_service: object
    ) -> AsyncRemoteServiceClient:
        """Wire a real, connected peer for coord.

        coord: the coord to register the peer under.
        local_service: object exposing the RPC methods to serve.

        return: the connected client.

        """
        server, port = await _start_server(local_service)
        self._servers.append(server)

        client = AsyncRemoteServiceClient(coord)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        client.initialize_streams(reader, writer, plus=None)
        self._peer_tasks.append(asyncio.create_task(client.run()))
        self._clients.append(client)

        self.service.remote_services[coord] = client
        return client

    async def _start_service(self, contest_id: int | None = None):
        """Build the EvaluationService under test, with a fake ScoringService.

        Built the way EvaluationService_test.py does, while the test's
        event loop is running, with the sweeper's timer stubbed out (the
        tests call _missing_operations() themselves, which is exactly
        what each sweep does) and results flushed to the DB right away
        instead of after MAX_FLUSHING_TIME_SECONDS.

        contest_id: the contest the service is limited to, or None for
            the "all contests" mode.

        """
        with patch.object(
                EvaluationService, "start_sweeper",
                lambda self, timeout: None):
            service = EvaluationService(shard=0, contest_id=contest_id)
        service._loop = asyncio.get_running_loop()
        self.addCleanup(service._disconnect_all)
        self.service = service

        service.result_cache.flush_latency_seconds = 0

        # An unconnected placeholder, so the executor's batch size (which
        # divides by the pool size) works before a real worker is wired.
        service.get_executor().pool.add_worker(ServiceCoord("Worker", 0))

        self.scoring_stub = FakeScoringService()
        service.scoring_service = await self._connect_peer(
            ServiceCoord("ScoringService", 0), self.scoring_stub)

        # Count the notifications synchronously, when they are decided
        # (on the thread that writes the results), so that "notified
        # exactly once" needs no sleeping to rule out a late duplicate.
        self.notifications = MagicMock(
            wraps=service._threadsafe_notify_scoring_service)
        service._threadsafe_notify_scoring_service = self.notifications

    async def _start_worker(self, worker: ScriptedWorker):
        """Connect the worker to the service, as the only Worker shard.

        worker: the local service standing in for the Worker.

        """
        self._workers.append(worker)
        client = await self._connect_peer(ServiceCoord("Worker", 0), worker)
        self.service.get_executor().pool._worker[0] = client

    def _add_fixture(self, evaluations: dict[str, str] | None = None):
        """Add a contest with the task and one submission of it.

        The dataset is the active one, with a GroupMin score type over
        the two groups.

        evaluations: if None, the submission is brand new (no result
            yet). Otherwise its result exists, compiled, holding an
            evaluation with the given outcome for each codename here (as
            if ES had been stopped in the middle of the evaluation).

        return: the fixture.

        """
        contest = self.add_contest()
        task = self.add_task(contest=contest)
        dataset = self.add_dataset(
            task=task, task_type="Batch", score_type="GroupMin",
            score_type_parameters=SCORE_TYPE_PARAMETERS)
        task.active_dataset = dataset
        testcases = {
            codename: self.add_testcase(dataset, codename=codename)
            for codename in ALL_CODENAMES}
        participation = self.add_participation(contest=contest)
        if evaluations is None:
            submission = self.add_submission(
                task=task, participation=participation)
        else:
            submission, (result,) = self.add_submission_with_results(
                task, participation, True)
            for codename, outcome in evaluations.items():
                self.add_evaluation(
                    result, testcases[codename], outcome=outcome,
                    text=["Output is correct" if float(outcome) > 0
                          else "Output isn't correct"])
        self.session.commit()
        return Fixture(contest, task, dataset, submission)

    def _load_result(self, fixture: Fixture):
        """Return the fixture's submission result, fresh from the DB."""
        self.session.expire_all()
        submission = Submission.get_from_id(
            fixture.submission.id, self.session)
        return submission.get_result(fixture.dataset)

    def _score(self, fixture: Fixture) -> float:
        """Return the score the real GroupMin gives to the fixture."""
        result = self._load_result(fixture)
        return fixture.dataset.score_type_object.compute_score(result)[0]

    def _outcomes(self, fixture: Fixture) -> dict[str, str | None]:
        """Return the outcome of each evaluation the fixture has."""
        return {e.codename: e.outcome
                for e in self._load_result(fixture).evaluations}

    async def _wait_for(self, predicate, what: str, timeout: float = 15.0):
        """Poll predicate() until it is true, failing the test on timeout.

        predicate: a zero-argument callable to poll.
        what: what is being waited for, for the failure message.
        timeout: how long to wait, in seconds.

        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() > deadline:
                self.fail("Timed out after %gs waiting for %s." %
                          (timeout, what))
            await asyncio.sleep(0.02)

    def _is_idle(self) -> bool:
        """Return whether ES has nothing left in flight."""
        executor = self.service.get_executor()
        cache = self.service.result_cache
        # The worker threads update the pool and the cache together while
        # holding this lock; don't look in the middle of that.
        with self.service.post_finish_lock:
            return (
                len(executor._operation_queue) == 0
                and not executor._currently_executing
                and all(operations == WorkerPool.WORKER_INACTIVE
                        for operations in executor.pool._operations.values())
                and not cache.d and not cache.fd
                and not self.service._pending_operations)

    def _no_enqueue_pending(self) -> bool:
        """Return whether every enqueue ES decided on has landed."""
        with self.service.post_finish_lock:
            return not self.service._pending_operations

    async def _wait_until_idle(self):
        """Wait for everything ES was asked to do to be done.

        That is: the queue and the pool are empty, every result has been
        written to the DB, and every notification to ScoringService has
        been delivered. Nothing can be notified later, since nothing is
        left in flight.

        """
        idle_polls = 0

        def stably_idle() -> bool:
            nonlocal idle_polls
            idle_polls = idle_polls + 1 if self._is_idle() else 0
            return idle_polls >= 3

        await self._wait_for(stably_idle, "ES to go idle")
        await self._wait_for(
            lambda: len(self.scoring_stub.new_evaluation_calls)
            == self.notifications.call_count,
            "the notifications to reach ScoringService")

    async def _sweep_until_quiet(self, max_sweeps: int = 10):
        """Sweep, as the sweeper's timer does, until a sweep finds nothing.

        max_sweeps: how many sweeps to allow before failing the test.

        """
        for _ in range(max_sweeps):
            found = await self.service._missing_operations()
            await self._wait_until_idle()
            if found == 0:
                return
        self.fail("The sweeper still finds operations after %d sweeps." %
                  max_sweeps)

    async def _submit(self, fixture: Fixture):
        """Submit the fixture's submission and wait for ES to finish."""
        await self.service.new_submission(fixture.submission.id)
        await self._wait_until_idle()

    def _assert_notified_once(self, fixture: Fixture):
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(
            self.scoring_stub.new_evaluation_calls, [fixture.key])

    def _assert_not_notified(self):
        self.assertEqual(self.notifications.call_count, 0)
        self.assertEqual(self.scoring_stub.new_evaluation_calls, [])

    def _assert_skipped(self, fixture: Fixture, codenames):
        """Check that codenames hold the synthesized skipped evaluations."""
        evaluations = {
            e.codename: e for e in self._load_result(fixture).evaluations}
        for codename in codenames:
            evaluation = evaluations[codename]
            self.assertEqual(evaluation.outcome, "0.0", codename)
            self.assertEqual(evaluation.text, SKIP_TEXT, codename)
            self.assertIsNone(evaluation.evaluation_shard, codename)

    # -- phase 1 is screening only ---------------------------------------

    async def test_fresh_submission_dispatches_only_screening_operations(self):
        await self._start_service()
        worker = ScriptedWorker()
        # Hold the answers: no result can be written, so nothing else can
        # be released while the first evaluation dispatches are pending.
        worker.evaluations_released.clear()
        await self._start_worker(worker)
        fixture = self._add_fixture()

        await self.service.new_submission(fixture.submission.id)

        executor = self.service.get_executor()
        await self._wait_for(
            lambda: all(fixture.operation(codename) in executor
                        for codename in SCREENING_CODENAMES),
            "the screening operations to reach the executor")
        await self._wait_for(
            lambda: worker.dispatched(), "the first dispatch to the worker")
        # The enqueues are landed on the loop after they are decided: wait
        # for all of them before asserting that something is absent.
        await self._wait_for(
            self._no_enqueue_pending, "every enqueue to land")
        for codename in PHASE_TWO_CODENAMES:
            self.assertNotIn(fixture.operation(codename), executor, codename)
        self.assertLessEqual(
            {codename for _, codename in worker.dispatched()},
            set(SCREENING_CODENAMES))

        worker.evaluations_released.set()
        await self._wait_until_idle()
        self.assertEqual(len(self._load_result(fixture).evaluations),
                         len(ALL_CODENAMES))

    async def test_without_two_phase_every_testcase_is_dispatched_together(
        self
    ):
        # Control for the test above: with the flag off, the very same
        # setup has all the testcases pending at the same time, so that
        # test does tell the two modes apart.
        await self._start_service()
        worker = ScriptedWorker()
        worker.evaluations_released.clear()
        await self._start_worker(worker)
        fixture = self._add_fixture()

        with patch.object(config.global_, "two_phase_evaluation", False):
            await self.service.new_submission(fixture.submission.id)

            executor = self.service.get_executor()
            await self._wait_for(
                lambda: all(fixture.operation(codename) in executor
                            for codename in ALL_CODENAMES),
                "every operation to reach the executor")

            worker.evaluations_released.set()
            await self._wait_until_idle()

        self._assert_notified_once(fixture)
        self.assertEqual(self._score(fixture), 100)

    # -- phase 2, or the skip, after screening ---------------------------

    async def test_screening_failure_skips_the_rest_and_notifies_once(self):
        await self._start_service()
        worker = ScriptedWorker(
            outcomes={"s1-01-scr-wa": "0.0", "s2-00-sample": "0.0"})
        await self._start_worker(worker)
        fixture = self._add_fixture()

        await self._submit(fixture)

        # Both groups failed their screening: the workers only ever saw
        # the screening testcases.
        self.assertCountEqual(
            [codename for _, codename in worker.dispatched()],
            SCREENING_CODENAMES)
        # The other testcases got synthesized evaluations, which is what
        # lets the submission complete.
        result = self._load_result(fixture)
        self.assertTrue(result.evaluated())
        self.assertEqual(
            {e.codename for e in result.evaluations}, set(ALL_CODENAMES))
        self._assert_skipped(fixture, PHASE_TWO_CODENAMES)
        self._assert_notified_once(fixture)
        self.assertEqual(self._score(fixture), 0)

    async def test_screening_pass_releases_phase_two_and_notifies_once(self):
        await self._start_service()
        worker = ScriptedWorker()
        await self._start_worker(worker)
        fixture = self._add_fixture()

        await self._submit(fixture)

        # Every testcase was run, once.
        self.assertCountEqual(
            [codename for _, codename in worker.dispatched()],
            ALL_CODENAMES)
        # A group's phase 2 is only dispatched after its screening
        # results came back, that is, in a later job group.
        batch_of = {
            codename: index
            for index, batch in enumerate(worker.evaluation_batches)
            for _, codename in batch}
        for group in ("s1", "s2"):
            screening_done_at = max(
                batch_of[codename] for codename in SCREENING_CODENAMES
                if codename.startswith(group + "-"))
            for codename in PHASE_TWO_CODENAMES:
                if codename.startswith(group + "-"):
                    self.assertGreater(batch_of[codename], screening_done_at)
        result = self._load_result(fixture)
        self.assertTrue(result.evaluated())
        self.assertEqual(
            self._outcomes(fixture),
            {codename: "1.0" for codename in ALL_CODENAMES})
        self._assert_notified_once(fixture)
        self.assertEqual(self._score(fixture), 100)

    async def test_one_group_failing_and_one_passing_keeps_partial_score(self):
        await self._start_service()
        worker = ScriptedWorker(outcomes={"s1-01-scr-wa": "0.0"})
        await self._start_worker(worker)
        fixture = self._add_fixture()

        await self._submit(fixture)

        # s1 was skipped after its screening, s2 went on to phase 2.
        dispatched = [codename for _, codename in worker.dispatched()]
        self.assertCountEqual(
            dispatched,
            SCREENING_CODENAMES + ("s2-01-normal",))
        self._assert_skipped(fixture, ["s1-02-normal"])
        self.assertEqual(self._outcomes(fixture)["s2-01-normal"], "1.0")
        self.assertTrue(self._load_result(fixture).evaluated())
        self._assert_notified_once(fixture)
        # GroupMin: s1 scores 0, s2 keeps its 50 points.
        self.assertEqual(self._score(fixture), 50)

    async def test_partial_screening_outcome_counts_as_a_pass(self):
        # The pass rule is outcome > 0, not outcome == 1.
        await self._start_service()
        worker = ScriptedWorker(outcomes={"s2-00-sample": "0.5"})
        await self._start_worker(worker)
        fixture = self._add_fixture()

        await self._submit(fixture)

        self.assertCountEqual(
            [codename for _, codename in worker.dispatched()], ALL_CODENAMES)
        self._assert_notified_once(fixture)
        # s1 is fully correct (50), s2 is worth its minimum, 0.5 * 50.
        self.assertEqual(self._score(fixture), 75)

    # -- a worker failing in the middle of a phase -----------------------

    # A failed job is not re-enqueued by write_results() itself when it is
    # the only thing in its flush: the operation is still in the result
    # cache at that point, so the enqueue is skipped as a duplicate. The
    # retry then comes from a later result of the same submission, or
    # from the sweeper (as with two-phase off). The tests below do not
    # depend on which one it is, so they sweep until nothing is left.

    async def _check_transient_job_failure(self, codename: str):
        await self._start_service()
        worker = ScriptedWorker()
        worker.job_failures_left[codename] = 1
        await self._start_worker(worker)
        fixture = self._add_fixture()

        await self._submit(fixture)
        await self._sweep_until_quiet()

        # Only the failed testcase ran twice.
        self.assertCountEqual(
            [name for _, name in worker.dispatched()],
            ALL_CODENAMES + (codename,))
        result = self._load_result(fixture)
        self.assertEqual(result.evaluation_tries, 1)
        self.assertTrue(result.evaluated())
        self._assert_notified_once(fixture)
        self.assertEqual(self._score(fixture), 100)

    async def test_failed_screening_job_is_retried_and_completes(self):
        await self._check_transient_job_failure("s1-01-scr-wa")

    async def test_failed_phase_two_job_is_retried_and_completes(self):
        await self._check_transient_job_failure("s2-01-normal")

    async def test_screening_job_failing_for_good_gives_up_without_notifying(
        self
    ):
        # Pins today's behavior: after MAX_EVALUATION_TRIES failed jobs
        # ES gives up on the submission result (no more retries, not even
        # from the sweeper) and does not tell ScoringService: it stays
        # unscored until an admin invalidates it. The other group is not
        # held back meanwhile, and the gated group's phase 2 never runs.
        await self._start_service()
        worker = ScriptedWorker()
        worker.job_failures_left["s1-01-scr-wa"] = math.inf
        await self._start_worker(worker)
        fixture = self._add_fixture()

        await self._submit(fixture)
        await self._sweep_until_quiet()

        dispatched = [codename for _, codename in worker.dispatched()]
        self.assertEqual(dispatched.count("s1-01-scr-wa"),
                         MAX_EVALUATION_TRIES)
        self.assertCountEqual(
            [codename for codename in dispatched
             if codename != "s1-01-scr-wa"],
            ["s1-00-sample", "s2-00-sample", "s2-01-normal"])
        result = self._load_result(fixture)
        self.assertEqual(result.evaluation_tries, MAX_EVALUATION_TRIES)
        self.assertFalse(result.evaluated())
        self.assertEqual(
            set(self._outcomes(fixture)),
            {"s1-00-sample", "s2-00-sample", "s2-01-normal"})
        self._assert_not_notified()

    async def test_lost_job_group_is_recovered_by_the_sweeper(self):
        # Pins today's behavior: when the RPC to the worker fails, the
        # job group is lost (nothing is written, nothing is re-enqueued,
        # no try is counted) until the sweeper finds the missing
        # operations, and it finds them through the same gate as
        # everything else.
        await self._start_service()
        worker = ScriptedWorker()
        worker.lose_evaluation_batches = True
        await self._start_worker(worker)
        fixture = self._add_fixture()

        await self._submit(fixture)

        # Every screening dispatch was lost, and nothing else was tried.
        self.assertCountEqual(
            [codename for _, codename in worker.dispatched()],
            SCREENING_CODENAMES)
        result = self._load_result(fixture)
        self.assertEqual(result.evaluation_tries, 0)
        self.assertEqual(result.evaluations, [])
        self._assert_not_notified()

        worker.lose_evaluation_batches = False
        self.assertEqual(
            await self.service._missing_operations(),
            len(SCREENING_CODENAMES))
        await self._wait_until_idle()

        self.assertCountEqual(
            [codename for _, codename in worker.dispatched()],
            SCREENING_CODENAMES + ALL_CODENAMES)
        self.assertTrue(self._load_result(fixture).evaluated())
        self._assert_notified_once(fixture)
        self.assertEqual(self._score(fixture), 100)

    # -- the sweeper, in "all contests" mode and limited to a contest ----

    def _assert_pending(self, fixture: Fixture, codenames, pending: bool):
        executor = self.service.get_executor()
        for codename in codenames:
            self.assertEqual(
                fixture.operation(codename) in executor, pending, codename)

    async def test_sweeper_of_all_contests_finds_only_screening_operations(
        self
    ):
        await self._start_service(contest_id=None)
        fixtures = [self._add_fixture(evaluations={}) for _ in range(2)]

        found = await self.service._missing_operations()

        # The bulk SQL of the flag-off sweeper would also have found the
        # phase 2 testcases.
        self.assertEqual(found, 2 * len(SCREENING_CODENAMES))
        for fixture in fixtures:
            self._assert_pending(fixture, SCREENING_CODENAMES, True)
            self._assert_pending(fixture, PHASE_TWO_CODENAMES, False)

    async def test_sweeper_enqueues_the_compilation_of_an_uncompiled_one(
        self
    ):
        # A submission with no result at all, e.g. when its compilation
        # job group was lost: the sweeper must compile it, and nothing
        # else is due before that.
        await self._start_service(contest_id=None)
        fixture = self._add_fixture()

        found = await self.service._missing_operations()

        self.assertEqual(found, 1)
        self.assertIn(
            fixture.compilation_operation(), self.service.get_executor())
        self._assert_pending(fixture, ALL_CODENAMES, False)

    async def test_sweeper_of_a_contest_ignores_the_other_contests(self):
        # Two contests, as in the multi-contest deployments where each ES
        # is limited to one of them; the ES of a contest_id None sweeps
        # both (the test above).
        first = self._add_fixture(evaluations={})
        second = self._add_fixture(evaluations={})
        await self._start_service(contest_id=first.contest.id)

        found = await self.service._missing_operations()

        self.assertEqual(found, len(SCREENING_CODENAMES))
        self._assert_pending(first, SCREENING_CODENAMES, True)
        self._assert_pending(first, PHASE_TWO_CODENAMES, False)
        self._assert_pending(second, ALL_CODENAMES, False)

    async def test_sweeper_of_a_contest_resumes_an_interrupted_evaluation(
        self
    ):
        # ES was stopped after the screening results were written, before
        # the skip of s2 was synthesized and before s1's phase 2 ran.
        interrupted = {
            "s1-00-sample": "1.0", "s1-01-scr-wa": "1.0",  # s1 passed
            "s2-00-sample": "0.0",                         # s2 failed
        }
        first = self._add_fixture(evaluations=interrupted)
        second = self._add_fixture(evaluations=interrupted)
        await self._start_service(contest_id=first.contest.id)
        worker = ScriptedWorker()
        await self._start_worker(worker)

        # Only the released group's testcase: the gated one stays out.
        self.assertEqual(await self.service._missing_operations(), 1)
        await self._wait_until_idle()

        self.assertEqual(
            worker.dispatched(), [(first.submission.id, "s1-02-normal")])
        result = self._load_result(first)
        self.assertTrue(result.evaluated())
        self._assert_skipped(first, ["s2-01-normal"])
        self._assert_notified_once(first)
        self.assertEqual(self._score(first), 50)
        # The other contest's identical submission was left alone.
        self.assertFalse(self._load_result(second).evaluated())
        self.assertEqual(len(self._load_result(second).evaluations), 3)

    async def test_sweeper_of_all_contests_resumes_every_contest(self):
        interrupted = {
            "s1-00-sample": "1.0", "s1-01-scr-wa": "1.0",
            "s2-00-sample": "0.0",
        }
        first = self._add_fixture(evaluations=interrupted)
        second = self._add_fixture(evaluations={})
        await self._start_service(contest_id=None)
        worker = ScriptedWorker()
        await self._start_worker(worker)

        # The first one's released testcase, plus the second one's
        # screening testcases.
        self.assertEqual(
            await self.service._missing_operations(),
            1 + len(SCREENING_CODENAMES))
        await self._wait_until_idle()

        self.assertTrue(self._load_result(first).evaluated())
        self.assertTrue(self._load_result(second).evaluated())
        self._assert_skipped(first, ["s2-01-normal"])
        self.assertEqual(self._score(first), 50)
        self.assertEqual(self._score(second), 100)
        # Exactly one notification for each submission.
        self.assertEqual(self.notifications.call_count, 2)
        self.assertCountEqual(
            self.scoring_stub.new_evaluation_calls,
            [first.key, second.key])

    async def test_sweeper_finalizes_when_only_gated_operations_remain(self):
        # ES was stopped after s1's screening failed but before its skip
        # was synthesized, and everything else was evaluated. The sweeper
        # has no operation left to enqueue; it must synthesize the skip
        # and finalize the submission instead of leaving it forever.
        stopped = {
            "s1-00-sample": "1.0", "s1-01-scr-wa": "0.0",  # s1 failed
            "s2-00-sample": "1.0", "s2-01-normal": "1.0",  # s2 done
        }
        first = self._add_fixture(evaluations=stopped)
        second = self._add_fixture(evaluations=stopped)
        await self._start_service(contest_id=first.contest.id)

        self.assertEqual(await self.service._missing_operations(), 0)
        await self._wait_until_idle()

        self.assertTrue(self._load_result(first).evaluated())
        self._assert_skipped(first, ["s1-02-normal"])
        self._assert_notified_once(first)
        self.assertEqual(self._score(first), 50)
        self.assertFalse(self._load_result(second).evaluated())

        # And a second sweep has nothing more to do, notification
        # included.
        self.assertEqual(await self.service._missing_operations(), 0)
        await self._wait_until_idle()
        self._assert_notified_once(first)


if __name__ == "__main__":
    unittest.main()
