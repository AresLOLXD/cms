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

"""Tests of what EvaluationService does when things go wrong.

EvaluationService_test.py covers the happy path and the races between
its threads. These pin today's behavior around failures, with two-phase
evaluation off unless a test says otherwise (twophase_e2e_test.py drives
the same paths with it on):

- a worker whose RPC fails in the middle of a job: what action_finished
  does with the error, and how the sweeper brings the lost operation
  back;
- the sweeper, driven through _sweep() as its loop does, finding what
  was missed and leaving alone what is already in flight;
- results written from an executor thread while another thread is
  already writing, and results that cannot be written.

The service is real, with its own executor, worker pool and result
cache; the workers and ScoringService are scripted peers on loopback
sockets.

This file is asyncio-only: it must never share a process with the
gevent-based twophase files (see docker/_cms-test-internal.sh).

"""

import asyncio
import threading
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import cms.service.EvaluationService as EvaluationServiceModule
from cms.conf import Address, ServiceCoord
from cms.db import Submission, UserTest
from cms.grading import twophase
from cms.grading.Job import CompilationJob, EvaluationJob
from cms.io.async_rpc import AsyncRemoteServiceClient
from cms.io.async_triggeredservice import AsyncTriggeredService
from cms.io.priorityqueue import PriorityQueue
from cms.io.rpc import rpc_method
from cms.service.esoperations import MAX_COMPILATION_TRIES, ESOperation
from cms.service.EvaluationService import EvaluationService, Result
from cms.service.workerpool import WorkerPool
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin
from cmstestsuite.unit_tests.service.EvaluationService_test import \
    FakeScoringService, FakeWorker, _start_server


class ControllableWorker(FakeWorker):
    """A FakeWorker whose failures and pace the tests control.

    FakeWorker always answers, right away and successfully, and leaves
    the outcome of evaluation jobs unset. This one can also hold its
    answers back (so that its jobs stay in flight), lose the job group
    with an error (as a crashing worker does: the RPC then fails on the
    ES side), or answer with failed jobs (success=False, as a worker
    does on an internal error).

    """

    def __init__(self, compilation_success: bool = False):
        """Create the worker.

        compilation_success: what the compilation jobs answer. The
            default is a compilation error of the contestant, which ES
            takes as a normal, final outcome and reports to
            ScoringService (a successful one would go on to evaluation).

        """
        super().__init__(compilation_success)

        # (type, object id, testcase codename or None) of every job
        # received, in order.
        self.jobs: list[tuple[str, int, str | None]] = []

        # Clear it to hold back every answer.
        self.answers_released = asyncio.Event()
        self.answers_released.set()

        # Whether to lose the job groups with an error instead of
        # answering them.
        self.crash = False

        # Whether to answer with failed jobs.
        self.fail_jobs = False

    @rpc_method
    async def execute_job_group(self, job_group_dict: dict) -> dict:
        for job in job_group_dict["jobs"]:
            operation = job["operation"]
            self.jobs.append((job["type"], operation["object_id"],
                              operation["testcase_codename"]))
        await self.answers_released.wait()
        if self.crash:
            raise RuntimeError("simulated worker crash")

        answer = await super().execute_job_group(job_group_dict)
        for job in answer["jobs"]:
            if job["type"] == "evaluation":
                job["outcome"] = "1.0"
                job["text"] = ["Output is correct"]
                job["plus"] = {
                    "execution_time": 0.1,
                    "execution_wall_clock_time": 0.1,
                    "execution_memory": 1024,
                }
            if self.fail_jobs:
                job["success"] = False
        return answer


class RunLabellingWorker(ControllableWorker):
    """A ControllableWorker whose answers tell which run produced them.

    Every job of the n-th job group it answers (counting from 1) gets
    the text ["run n"]: it ends up as the compilation text or the
    evaluation text in the DB, so a test can tell a stale result from a
    fresh one.

    """

    def __init__(self, compilation_success: bool = False):
        super().__init__(compilation_success)
        self.answered = 0

    @rpc_method
    async def execute_job_group(self, job_group_dict: dict) -> dict:
        answer = await super().execute_job_group(job_group_dict)
        self.answered += 1
        for job in answer["jobs"]:
            job["text"] = ["run %d" % self.answered]
        return answer


@dataclass
class Fixture:
    """A contest with one submission, and the objects it refers to."""

    contest: object
    task: object
    dataset: object
    submission: object

    def compilation(self) -> ESOperation:
        """Return the compilation operation of the submission."""
        return ESOperation(
            ESOperation.COMPILATION, self.submission.id, self.dataset.id)

    def evaluation(self, codename: str) -> ESOperation:
        """Return the evaluation operation of the submission on codename."""
        return ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            codename)

    @property
    def key(self) -> tuple[int, int]:
        """Return the (submission id, dataset id) ScoringService gets."""
        return (self.submission.id, self.dataset.id)


class EvaluationServiceFailurePathsTest(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    async def asyncSetUp(self):
        self._servers: list[asyncio.Server] = []
        self._clients: list[AsyncRemoteServiceClient] = []
        self._peer_tasks: list[asyncio.Task] = []
        self._workers: list[ControllableWorker] = []
        self._gates: list[threading.Event] = []

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
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        # Built like EvaluationService_test.py's _build_service does:
        # while the test's event loop is running, with the sweeper's
        # timer stubbed out. Tests call _sweep() themselves, which is
        # exactly what each round of the timer does.
        with patch.object(
                EvaluationService, "start_sweeper",
                lambda self, timeout: None):
            service = EvaluationService(shard=0)
        service._loop = asyncio.get_running_loop()
        self.addCleanup(service._disconnect_all)
        self.service = service
        self.pool: WorkerPool = service.get_executor().pool

        # Results are written right away, not after
        # MAX_FLUSHING_TIME_SECONDS.
        service.result_cache.flush_latency_seconds = 0

        # An unconnected placeholder, so the executor's batch size (which
        # divides by the pool size) works before a real worker is wired.
        self.pool.add_worker(ServiceCoord("Worker", 0))

        self.scoring_stub = FakeScoringService()
        service.scoring_service = await self._connect_peer(
            ServiceCoord("ScoringService", 0), self.scoring_stub)

        # Count the notifications synchronously, when they are decided
        # (on the thread that writes the results), so that "notified
        # exactly once" needs no sleeping to rule out a late duplicate.
        self.notifications = MagicMock(
            wraps=service._threadsafe_notify_scoring_service)
        service._threadsafe_notify_scoring_service = self.notifications

    async def asyncTearDown(self):
        # Stop the service first, so nothing new starts while peers and
        # data are being torn down.
        for task in list(self.service._background_tasks):
            task.cancel()
        # Let go of the threads a test left waiting.
        for gate in self._gates:
            gate.set()
        for worker in self._workers:
            worker.answers_released.set()
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

    async def _start_worker(
        self, worker: ControllableWorker, shard: int = 0
    ) -> AsyncRemoteServiceClient:
        """Connect the worker to the service as the Worker of a shard.

        worker: the local service standing in for the Worker.
        shard: the shard it takes; shard 0 is the placeholder every test
            starts with, any other one is added to the pool.

        return: the connected client.

        """
        self._workers.append(worker)
        if shard not in self.pool._worker:
            self.pool.add_worker(ServiceCoord("Worker", shard))
        client = await self._connect_peer(
            ServiceCoord("Worker", shard), worker)
        self.pool._worker[shard] = client
        return client

    def _add_fixture(self, testcases: int = 0, compiled: bool = False):
        """Add a contest with the task and one submission of it.

        The dataset is the active one, and has the given testcases,
        named "t0", "t1" and so on.

        testcases: how many testcases the dataset has.
        compiled: whether the submission already has a result with a
            successful compilation, as if ES had been stopped after it.

        return: the fixture.

        """
        contest = self.add_contest()
        task = self.add_task(contest=contest)
        dataset = self.add_dataset(
            task=task, task_type="Batch",
            task_type_parameters=["alone", ["", ""], "diff"])
        task.active_dataset = dataset
        for index in range(testcases):
            self.add_testcase(dataset, codename="t%d" % index)
        participation = self.add_participation(contest=contest)
        if compiled:
            submission, _ = self.add_submission_with_results(
                task, participation, True)
        else:
            submission = self.add_submission(
                task=task, participation=participation)
        self.session.commit()
        return Fixture(contest, task, dataset, submission)

    def _add_submission_of(self, fixture: Fixture):
        """Add another, brand new submission of the fixture's task."""
        participation = self.add_participation(contest=fixture.contest)
        submission = self.add_submission(
            task=fixture.task, participation=participation)
        self.session.commit()
        return submission

    @staticmethod
    def _evaluation_job(
        operation: ESOperation, outcome: str = "1.0"
    ) -> EvaluationJob:
        """Return the job a worker answers for an evaluation operation.

        operation: the evaluation operation.
        outcome: the outcome of the testcase; "0.0" fails it.

        return: the job.

        """
        return EvaluationJob(
            operation=operation, success=True, outcome=outcome,
            text=["Output is correct"], shard=0, sandboxes=[],
            sandbox_digests={},
            plus={"execution_time": 0.1, "execution_wall_clock_time": 0.1,
                  "execution_memory": 1024})

    def _compiled(self, fixture: Fixture) -> bool:
        """Return whether the DB has a compilation outcome for the fixture."""
        result = self._load_result(fixture)
        return result is not None and result.compilation_outcome is not None

    def _load_result(self, fixture: Fixture):
        """Return the fixture's submission result, fresh from the DB.

        return: the result, or None if the DB has none.

        """
        self.session.expire_all()
        submission = Submission.get_from_id(
            fixture.submission.id, self.session)
        return submission.get_result(fixture.dataset)

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
                        for operations in self.pool._operations.values())
                and not cache.d and not cache.fd
                and not self.service._pending_operations)

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

    def _record_action_finished(self) -> list[tuple]:
        """Record the arguments of every call to action_finished.

        return: the list the (data, shard, error) tuples are added to.

        """
        calls: list[tuple] = []
        real_action_finished = self.service.action_finished

        async def recording_action_finished(data, shard, error=None):
            calls.append((data, shard, error))
            return await real_action_finished(data, shard, error)

        self.service.action_finished = recording_action_finished
        return calls

    def _watch_writes(
        self, gated: bool = False
    ) -> tuple[threading.Event, threading.Event, list[int]]:
        """Record the thread of every row written to the DB.

        gated: whether to make the first row written wait for the test.

        return: an event set once a thread is writing the first row, the
            event that lets a gated write go on, and the list the ident
            of the thread of every row written is added to.

        """
        writing = threading.Event()
        proceed = threading.Event()
        self._gates.append(proceed)
        writer_threads: list[int] = []
        real_write_row = self.service.write_results_one_row

        def watching_write_row(*args, **kwargs):
            writer_threads.append(threading.get_ident())
            if len(writer_threads) == 1:
                writing.set()
                if gated:
                    proceed.wait(timeout=30)
            return real_write_row(*args, **kwargs)

        self.service.write_results_one_row = watching_write_row
        return writing, proceed, writer_threads

    # -- a worker whose RPC fails in the middle of a job -----------------

    async def test_rpc_error_loses_the_job_group_without_counting_a_try(self):
        # Pins today's behavior: when the RPC to the worker fails, the
        # pool reports it to action_finished, which frees the worker and
        # drops the job group. Nothing is written, no try is counted,
        # ScoringService hears nothing, and the operation is not queued
        # again: only the sweeper finds it (the next test).
        fixture = self._add_fixture()
        worker = ControllableWorker()
        worker.crash = True
        await self._start_worker(worker)
        finished = self._record_action_finished()

        with self.assertLogs(
                "cms.service.EvaluationService", level="ERROR") as logs:
            self.assertTrue(await self.service.enqueue(
                fixture.compilation(), PriorityQueue.PRIORITY_HIGH,
                fixture.submission.timestamp))
            await self._wait_for(
                lambda: len(finished) == 1, "action_finished to be called")
            await self._wait_until_idle()

        # No answer, and the error of the RPC as the reason.
        data, shard, error = finished[0]
        self.assertIsNone(data)
        self.assertEqual(shard, 0)
        self.assertIn("simulated worker crash", error)
        self.assertEqual(
            [record.getMessage() for record in logs.records],
            ["Received error from Worker (see above), job group lost."])
        # The worker is free for more work.
        self.assertEqual(self.pool._operations[0], WorkerPool.WORKER_INACTIVE)
        self.assertIsNone(self.pool._start_time[0])
        # The operation is nowhere: not queued, not in the pool, not in
        # the result cache, and nothing was written for it.
        self.assertEqual(worker.jobs, [("compilation", fixture.submission.id,
                                        None)])
        self.assertNotIn(fixture.compilation(), self.service.get_executor())
        self.assertNotIn(fixture.compilation(), self.service.result_cache)
        self.assertIsNone(self._load_result(fixture))
        self.assertEqual(self.notifications.call_count, 0)
        self.assertEqual(self.scoring_stub.new_evaluation_calls, [])

    async def test_sweeper_recovers_a_compilation_lost_to_an_rpc_error(self):
        fixture = self._add_fixture()
        worker = ControllableWorker()
        worker.crash = True
        await self._start_worker(worker)
        await self.service.new_submission(fixture.submission.id)
        await self._wait_until_idle()
        self.assertEqual(len(worker.jobs), 1)
        self.assertIsNone(self._load_result(fixture))

        # The worker is back.
        worker.crash = False
        with self.assertLogs(
                "cms.io.async_triggeredservice", level="INFO") as logs:
            await self.service._sweep()
            await self._wait_until_idle()

        self.assertIn("Found 1 missed operation(s)", logs.output[-1])
        # Sent twice: the job group that was lost, and the one that made it.
        self.assertEqual(worker.jobs, 2 * [("compilation",
                                            fixture.submission.id, None)])
        result = self._load_result(fixture)
        self.assertTrue(result.compilation_failed())
        # The lost dispatch was not counted as a failed try.
        self.assertEqual(result.compilation_tries, 0)
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [fixture.key])

        # Nothing is left to find.
        self.assertEqual(await self.service._missing_operations(), 0)

    async def test_sweeper_recovers_evaluations_lost_to_an_rpc_error(self):
        fixture = self._add_fixture(testcases=3, compiled=True)
        worker = ControllableWorker()
        worker.crash = True
        await self._start_worker(worker)
        await self.service.new_submission(fixture.submission.id)
        await self._wait_until_idle()

        # However the testcases were batched, each was sent once, and
        # lost.
        sent_once = [("evaluation", fixture.submission.id, "t%d" % index)
                     for index in range(3)]
        self.assertCountEqual(worker.jobs, sent_once)
        result = self._load_result(fixture)
        self.assertEqual(result.evaluations, [])
        self.assertEqual(result.evaluation_tries, 0)
        self.assertEqual(self.notifications.call_count, 0)

        worker.crash = False
        with self.assertLogs(
                "cms.io.async_triggeredservice", level="INFO") as logs:
            await self.service._sweep()
            await self._wait_until_idle()

        self.assertIn("Found 3 missed operation(s)", logs.output[-1])
        # Each testcase went to the worker twice, and was written once.
        self.assertCountEqual(worker.jobs, sent_once + sent_once)
        result = self._load_result(fixture)
        self.assertEqual(sorted(e.codename for e in result.evaluations),
                         ["t0", "t1", "t2"])
        self.assertTrue(result.evaluated())
        self.assertEqual(result.evaluation_tries, 0)
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [fixture.key])

    async def test_failed_jobs_count_one_try_each_and_the_sweeper_gives_up(
        self
    ):
        # Pins today's behavior: a job the worker reports as failed (not
        # an RPC error) counts as one try. It is not queued again by
        # write_results() itself, as the operation is still in the result
        # cache at that point: the sweeper does it, until the tries run
        # out. ES then gives up on the submission without telling
        # ScoringService.
        fixture = self._add_fixture()
        worker = ControllableWorker()
        worker.fail_jobs = True
        await self._start_worker(worker)
        await self.service.new_submission(fixture.submission.id)
        await self._wait_until_idle()

        self.assertEqual(len(worker.jobs), 1)
        result = self._load_result(fixture)
        self.assertEqual(result.compilation_tries, 1)
        self.assertIsNone(result.compilation_outcome)

        for tries in range(2, MAX_COMPILATION_TRIES + 1):
            self.assertEqual(await self.service._missing_operations(), 1)
            await self._wait_until_idle()
            self.assertEqual(len(worker.jobs), tries)
            self.assertEqual(self._load_result(fixture).compilation_tries,
                             tries)

        # No more tries: the sweeper leaves it alone.
        self.assertEqual(await self.service._missing_operations(), 0)
        await self._wait_until_idle()
        self.assertEqual(len(worker.jobs), MAX_COMPILATION_TRIES)
        self.assertIsNone(self._load_result(fixture).compilation_outcome)
        self.assertEqual(self.notifications.call_count, 0)

    async def test_rpc_error_after_check_workers_connection_is_swallowed(self):
        # The connection to a worker drops in the middle of a job, and
        # check_workers_connection (on its timer) looks at the pool just
        # before the failed RPC reaches action_finished. The operation is
        # queued again by the first, and the late action_finished finds
        # the worker released already: it fails, and the pool logs that
        # without letting it escape.
        fixture = self._add_fixture()
        worker = ControllableWorker()
        worker.answers_released.clear()
        client = await self._start_worker(worker)
        await self.service.new_submission(fixture.submission.id)
        await self._wait_for(lambda: len(worker.jobs) == 1,
                             "the job to reach the worker")
        finished = self._record_action_finished()
        executor = self.service.get_executor()

        client.disconnect()
        with self.assertLogs("cms.service.workerpool", level="ERROR") as logs:
            # Nothing runs between the two: the RPC error is only
            # delivered when the loop next gets control.
            await self.service.check_workers_connection()
            await self._wait_for(
                lambda: len(finished) == 1, "action_finished to be called")
            await self._wait_for(
                lambda: len(logs.records) == 2, "the failure to be logged")
        await self._wait_for(
            lambda: executor._currently_executing == [fixture.compilation()],
            "the operation to be held for a worker")

        data, shard, error = finished[0]
        self.assertIsNone(data)
        self.assertEqual(shard, 0)
        self.assertIsNotNone(error)
        self.assertEqual(
            [record.getMessage() for record in logs.records],
            ["Trying to release worker while it's inactive.",
             "Unexpected error in action_finished for worker 0."])
        # The operation is queued again, exactly once (held for a worker,
        # as the only one is disconnected), and nothing else happened.
        self.assertEqual(executor._currently_executing,
                         [fixture.compilation()])
        self.assertEqual(len(executor._operation_queue), 0)
        self.assertNotIn(fixture.compilation(), self.pool)
        self.assertEqual(self.pool._operations[0], WorkerPool.WORKER_INACTIVE)
        self.assertNotIn(fixture.compilation(), self.service.result_cache)
        self.assertEqual(len(worker.jobs), 1)
        self.assertIsNone(self._load_result(fixture))
        self.assertEqual(self.notifications.call_count, 0)

    async def test_check_workers_connection_after_rpc_error_finds_nothing(
        self
    ):
        # The order of events when a worker dies in the middle of a job:
        # the failed RPC comes first, and frees the worker. When
        # check_workers_connection looks at the pool, there is nothing
        # left to give back, so the operation waits for the sweeper.
        fixture = self._add_fixture()
        worker = ControllableWorker()
        worker.answers_released.clear()
        client = await self._start_worker(worker)
        await self.service.new_submission(fixture.submission.id)
        await self._wait_for(lambda: len(worker.jobs) == 1,
                             "the job to reach the worker")
        finished = self._record_action_finished()
        executor = self.service.get_executor()

        client.disconnect()
        await self._wait_for(
            lambda: len(finished) == 1, "action_finished to be called")
        await self._wait_until_idle()
        self.assertEqual(self.pool._operations[0], WorkerPool.WORKER_INACTIVE)

        await self.service.check_workers_connection()
        self.assertNotIn(fixture.compilation(), executor)

        # Only a sweep brings it back (held for a worker, as the only one
        # is still disconnected).
        with self.assertLogs(
                "cms.io.async_triggeredservice", level="INFO") as logs:
            await self.service._sweep()
        self.assertIn("Found 1 missed operation(s)", logs.output[-1])
        await self._wait_for(lambda: fixture.compilation() in executor,
                             "the sweeper's operation to reach the executor")

    async def test_worker_released_when_the_job_group_cannot_be_built(self):
        # The operation refers to a submission that is gone: building
        # the job group fails before anything is sent. The worker must
        # not stay stuck until WORKER_TIMEOUT.
        fixture = self._add_fixture()
        worker = ControllableWorker()
        await self._start_worker(worker)
        finished = self._record_action_finished()
        operation = ESOperation(
            ESOperation.COMPILATION, 987654321, fixture.dataset.id)

        with self.assertLogs("cms.service.workerpool", level="ERROR") as logs:
            self.assertTrue(await self.service.enqueue(
                operation, PriorityQueue.PRIORITY_HIGH,
                fixture.submission.timestamp))
            await self._wait_for(
                lambda: len(finished) == 1, "action_finished to be called")
            await self._wait_until_idle()

        data, shard, error = finished[0]
        self.assertIsNone(data)
        self.assertEqual(shard, 0)
        self.assertTrue(error)
        self.assertEqual(logs.records[0].getMessage(),
                         "Failed to build job group for worker 0.")
        self.assertEqual(worker.jobs, [])
        self.assertEqual(self.pool._operations[0], WorkerPool.WORKER_INACTIVE)
        self.assertNotIn(operation, self.service.get_executor())
        self.assertEqual(self.notifications.call_count, 0)

    async def test_malformed_answer_frees_the_worker_and_is_dropped(self):
        fixture = self._add_fixture()
        operation = fixture.compilation()
        # As if the pool had just handed the worker the operation.
        self.pool._add_operations(0, [operation])

        with self.assertLogs(
                "cms.service.EvaluationService", level="ERROR") as logs:
            await self.service.action_finished(
                {"jobs": [{"type": "no-such-type"}]}, 0)

        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].levelname, "ERROR")
        self.assertTrue(logs.records[0].getMessage().startswith(
            "Couldn't build JobGroup for data"))
        self.assertIsNotNone(logs.records[0].exc_info)
        self.assertEqual(self.pool._operations[0], WorkerPool.WORKER_INACTIVE)
        self.assertNotIn(operation, self.pool)
        self.assertNotIn(operation, self.service.result_cache)
        self.assertIsNone(self._load_result(fixture))
        self.assertEqual(self.notifications.call_count, 0)

    async def test_result_of_an_operation_invalidated_in_flight_is_ignored(
        self
    ):
        # The result the worker was computing when the submission was
        # invalidated is not the one to keep: it is dropped, the
        # operation runs again, and only that second result is written
        # and notified.
        fixture = self._add_fixture()
        worker = ControllableWorker()
        worker.answers_released.clear()
        await self._start_worker(worker)
        await self.service.new_submission(fixture.submission.id)
        await self._wait_for(lambda: len(worker.jobs) == 1,
                             "the job to reach the worker")

        await self.service.invalidate_submission(
            submission_id=fixture.submission.id, level="compilation")
        with self.assertLogs(
                "cms.service.EvaluationService", level="INFO") as logs:
            worker.answers_released.set()
            await self._wait_until_idle()

        ignored = [line for line in logs.output
                   if "result ignored as requested" in line]
        self.assertEqual(len(ignored), 1)
        # Sent twice, the answer of the first is the one dropped.
        self.assertEqual(len(worker.jobs), 2)
        self.assertTrue(self._load_result(fixture).compilation_failed())
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [fixture.key])

    def _ignored_lines(self, logs) -> list[str]:
        """Return the log lines of the results dropped as requested."""
        return [line for line in logs.output
                if "result ignored as requested" in line]

    async def test_answer_arriving_during_the_invalidation_is_ignored(self):
        # The worker answers while the invalidation holds the lock, and
        # the loop runs the invalidation's dequeue (and its ignore) only
        # after that answer was handled: the ignore the invalidation does
        # on its own thread is what drops the stale answer.
        fixture = self._add_fixture()
        worker = RunLabellingWorker()
        worker.answers_released.clear()
        await self._start_worker(worker)
        await self.service.new_submission(fixture.submission.id)
        await self._wait_for(lambda: len(worker.jobs) == 1,
                             "the job to reach the worker")
        loop = asyncio.get_running_loop()
        cached: list[tuple[ESOperation, list[str]]] = []
        real_add = self.service.result_cache.add

        def recording_add(operation, result):
            cached.append((operation, result.job.text))
            real_add(operation, result)

        self.service.result_cache.add = recording_add

        # The answer reaches ES once the invalidation holds the lock.
        answer_arrived = threading.Event()
        self._gates.append(answer_arrived)
        real_action_finished = self.service.action_finished

        async def signalling_action_finished(data, shard, error=None):
            answer_arrived.set()
            await real_action_finished(data, shard, error)

        self.service.action_finished = signalling_action_finished
        real_get_relevant_operations = \
            EvaluationServiceModule.get_relevant_operations

        def answering_get_relevant_operations(*args, **kwargs):
            loop.call_soon_threadsafe(worker.answers_released.set)
            answer_arrived.wait(timeout=10)
            return real_get_relevant_operations(*args, **kwargs)

        # The loop applies the dequeue only once the answer was handled.
        answer_handled = threading.Event()
        self._gates.append(answer_handled)
        real_action_finished_sync = self.service._action_finished_sync

        def signalling_action_finished_sync(*args, **kwargs):
            try:
                return real_action_finished_sync(*args, **kwargs)
            finally:
                answer_handled.set()

        self.service._action_finished_sync = signalling_action_finished_sync
        real_dequeue = self.service.dequeue

        def late_dequeue(operation):
            answer_handled.wait(timeout=10)
            return real_dequeue(operation)

        self.service.dequeue = late_dequeue

        with patch.object(EvaluationServiceModule, "get_relevant_operations",
                          answering_get_relevant_operations), \
                self.assertLogs("cms.service.EvaluationService",
                                level="INFO") as logs:
            await self.service.invalidate_submission(
                submission_id=fixture.submission.id, level="compilation")
            await self._wait_until_idle()

        self.assertEqual(len(self._ignored_lines(logs)), 1)
        self.assertEqual(cached, [(fixture.compilation(), ["run 2"])])
        self.assertEqual(len(worker.jobs), 2)
        result = self._load_result(fixture)
        self.assertTrue(result.compilation_failed())
        self.assertEqual(result.compilation_text, ["run 2"])
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [fixture.key])

    async def test_operation_held_for_a_worker_is_not_sent_after_invalidation(
        self
    ):
        # The executor has popped the operation and waits for a worker
        # when the invalidation comes: the dequeue takes it out of
        # _currently_executing, so only the copy queued again is sent.
        busy = self._add_fixture()
        waiting = self._add_submission_of(busy)
        waiting_operation = ESOperation(
            ESOperation.COMPILATION, waiting.id, busy.dataset.id)
        worker = RunLabellingWorker()
        worker.answers_released.clear()
        await self._start_worker(worker)
        executor = self.service.get_executor()
        self.assertTrue(await self.service.enqueue(
            busy.compilation(), PriorityQueue.PRIORITY_HIGH,
            busy.submission.timestamp))
        await self._wait_for(lambda: len(worker.jobs) == 1,
                             "the first job to reach the worker")
        self.assertTrue(await self.service.enqueue(
            waiting_operation, PriorityQueue.PRIORITY_HIGH,
            waiting.timestamp))
        await self._wait_for(
            lambda: executor._currently_executing == [waiting_operation],
            "the operation to wait for a worker")

        await self.service.invalidate_submission(
            submission_id=waiting.id, level="compilation")

        self.assertEqual(executor._currently_executing, [])
        self.assertIn(waiting_operation, executor._operation_queue)
        self.assertEqual(self.service._pending_operations, {})
        worker.answers_released.set()
        await self._wait_until_idle()
        self.assertCountEqual(
            [job[1] for job in worker.jobs],
            [busy.submission.id, waiting.id])
        self.assertEqual(self.notifications.call_count, 2)

    async def test_invalidation_also_drops_the_archiving_twin(self):
        # An invalidation asking to archive the sandbox queues operations
        # with archive_sandbox=True, which are not equal to the ones
        # get_relevant_operations() builds. A later invalidation must
        # drop them all the same.
        fixture = self._add_fixture()
        twin = ESOperation(
            ESOperation.COMPILATION, fixture.submission.id,
            fixture.dataset.id, archive_sandbox=True)
        worker = RunLabellingWorker()
        worker.answers_released.clear()
        await self._start_worker(worker)
        await self.service.invalidate_submission(
            submission_id=fixture.submission.id, level="compilation",
            archive_sandbox=True)
        await self._wait_for(lambda: twin in self.pool,
                             "the twin to reach the worker")

        with self.assertLogs(
                "cms.service.EvaluationService", level="INFO") as logs:
            await self.service.invalidate_submission(
                submission_id=fixture.submission.id, level="compilation")
            self.assertIn(twin, self.pool._operations_to_ignore[0])
            worker.answers_released.set()
            await self._wait_until_idle()

        self.assertEqual(len(self._ignored_lines(logs)), 1)
        self.assertEqual(len(worker.jobs), 2)
        self.assertEqual(self._load_result(fixture).compilation_text,
                         ["run 2"])
        self.assertEqual(self.notifications.call_count, 1)

    # -- the sweeper, driven through _sweep() ----------------------------

    async def test_sweep_finds_what_es_was_never_told_about(self):
        # ES was down when the contestants submitted, so it got no
        # new_submission or new_user_test. Only the sweeper can find them.
        fixture = self._add_fixture()
        participation = self.add_participation(contest=fixture.contest)
        user_test = self.add_user_test(
            task=fixture.task, participation=participation)
        self.session.commit()
        worker = ControllableWorker()
        await self._start_worker(worker)

        with self.assertLogs(
                "cms.io.async_triggeredservice", level="INFO") as logs:
            await self.service._sweep()
            await self._wait_until_idle()

        self.assertIn("Found 2 missed operation(s)", logs.output[-1])
        self.assertCountEqual(
            worker.jobs,
            [("compilation", fixture.submission.id, None),
             ("compilation", user_test.id, None)])
        self.assertTrue(self._load_result(fixture).compilation_failed())
        self.session.expire_all()
        user_test = UserTest.get_from_id(user_test.id, self.session)
        self.assertTrue(
            user_test.get_result(fixture.dataset).compilation_failed())
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [fixture.key])

    async def test_sweep_leaves_alone_what_is_queued_or_with_a_worker(self):
        busy = self._add_fixture()
        worker = ControllableWorker()
        worker.answers_released.clear()
        await self._start_worker(worker)
        executor = self.service.get_executor()

        # Its operation is with the worker, which does not answer.
        self.assertEqual(await self.service._missing_operations(), 1)
        await self._wait_for(lambda: len(worker.jobs) == 1,
                             "the job to reach the worker")
        self.assertIn(busy.compilation(), self.pool)

        # A second submission has to wait for the only worker: its
        # operation is held by the executor.
        waiting = self._add_submission_of(busy)
        waiting_operation = ESOperation(
            ESOperation.COMPILATION, waiting.id, busy.dataset.id)
        with self.assertLogs(
                "cms.io.async_triggeredservice", level="INFO") as logs:
            await self.service._sweep()
        self.assertIn("Found 1 missed operation(s)", logs.output[-1])
        await self._wait_for(lambda: waiting_operation in executor,
                             "the operation to reach the executor")

        # Nothing left to find, until an answer comes.
        for _ in range(2):
            self.assertEqual(await self.service._missing_operations(), 0)
        self.assertEqual(len(worker.jobs), 1)
        self.assertEqual(self.notifications.call_count, 0)

        # Both are then done exactly once.
        worker.answers_released.set()
        await self._wait_until_idle()
        self.assertEqual(
            sorted(job[1] for job in worker.jobs),
            sorted([busy.submission.id, waiting.id]))
        self.assertEqual(self.notifications.call_count, 2)
        self.assertCountEqual(
            self.scoring_stub.new_evaluation_calls,
            [busy.key, (waiting.id, busy.dataset.id)])

    async def test_sweep_leaves_alone_a_result_not_yet_written(self):
        # An operation is also "in flight" while its result waits in the
        # cache for the flush: it is not in the queue nor in a worker,
        # and not in the DB yet.
        fixture = self._add_fixture()
        worker = ControllableWorker()
        await self._start_worker(worker)
        self.service.result_cache.flush_latency_seconds = 3600
        await self.service.new_submission(fixture.submission.id)
        await self._wait_for(
            lambda: fixture.compilation() in self.service.result_cache,
            "the result to reach the cache")
        self.assertIsNone(self._load_result(fixture))

        self.assertEqual(await self.service._missing_operations(), 0)

        await self.service.result_cache.flush()
        self.assertTrue(self._load_result(fixture).compilation_failed())
        self.assertEqual(await self.service._missing_operations(), 0)
        await self._wait_until_idle()
        self.assertEqual(len(worker.jobs), 1)
        self.assertEqual(self.notifications.call_count, 1)

    async def test_sweep_does_not_queue_what_a_new_submission_just_queued(
        self
    ):
        # The RPC and the sweeper run on two executor threads, and the
        # push of the first lands on the loop later: the sweeper must
        # not push the same operation again.
        fixture = self._add_fixture()
        executor = self.service.get_executor()
        pushes: list[ESOperation] = []
        real_enqueue = executor.enqueue

        def recording_enqueue(item, *args, **kwargs):
            pushes.append(item)
            return real_enqueue(item, *args, **kwargs)

        executor.enqueue = recording_enqueue
        loop = asyncio.get_running_loop()

        def new_submission_then_sweep():
            # Holding the loop keeps the push from landing before the
            # sweep: it is still in flight for it.
            release = threading.Event()
            loop.call_soon_threadsafe(release.wait, 10)
            try:
                self.service._new_submission_sync(fixture.submission.id)
                return self.service._missing_operations_sync()
            finally:
                release.set()

        found = await asyncio.wait_for(
            loop.run_in_executor(None, new_submission_then_sweep), timeout=10)
        await self._wait_for(
            lambda: fixture.compilation() in executor
            and not self.service._pending_operations,
            "the push to land")

        self.assertEqual(found, 0)
        self.assertEqual(pushes, [fixture.compilation()])

    async def test_sweeper_loop_survives_a_failing_sweep(self):
        # The real loop of the sweeper: a sweep that fails (the database
        # went away, say) is logged and does not stop it, and the next
        # one, asked for by search_operations_not_done, finds the work.
        fixture = self._add_fixture()
        worker = ControllableWorker()
        await self._start_worker(worker)
        sweeps: list[int] = []
        real_sweep = self.service._missing_operations_sync

        def failing_once():
            sweeps.append(1)
            if len(sweeps) == 1:
                raise RuntimeError("the database went away")
            return real_sweep()

        self.service._missing_operations_sync = failing_once

        with self.assertLogs(
                "cms.io.async_triggeredservice", level="ERROR") as logs:
            # A timeout far away: only the event wakes it up again.
            self.service.start_sweeper(3600)
            await self._wait_for(lambda: len(logs.records) == 1,
                                 "the first sweep to fail")
        self.assertEqual(
            logs.records[0].getMessage(),
            "Unexpected error when searching for missed operations.")
        self.assertIsNotNone(logs.records[0].exc_info)
        self.assertEqual(worker.jobs, [])

        self.service.search_operations_not_done()
        await self._wait_for(lambda: len(sweeps) == 2, "the second sweep")
        await self._wait_for(lambda: len(worker.jobs) == 1,
                             "the operation the sweep found to be sent")
        await self._wait_until_idle()

        self.assertEqual(len(worker.jobs), 1)
        self.assertTrue(self._load_result(fixture).compilation_failed())
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [fixture.key])

    # -- what the coroutines queue, and how --------------------------------

    async def test_what_a_coroutine_queues_has_landed_when_it_returns(self):
        # The loop actions of a _sync method are handed to the loop
        # before the method returns, and run_in_executor delivers its
        # result after them: once the await returns, every push and
        # dequeue it decided has been applied, with no waiting.
        executor = self.service.get_executor()

        def assert_landed(*operations: ESOperation):
            self.assertEqual(self.service._pending_operations, {})
            for operation in operations:
                self.assertIn(operation, executor)

        queued = self._add_fixture()
        self.assertTrue(await self.service.enqueue(
            queued.compilation(), PriorityQueue.PRIORITY_HIGH,
            queued.submission.timestamp))
        assert_landed(queued.compilation())

        submitted = self._add_fixture()
        await self.service.new_submission(submitted.submission.id)
        assert_landed(submitted.compilation())

        participation = self.add_participation(contest=queued.contest)
        user_test = self.add_user_test(
            task=queued.task, participation=participation)
        self.session.commit()
        await self.service.new_user_test(user_test.id)
        assert_landed(ESOperation(
            ESOperation.USER_TEST_COMPILATION, user_test.id,
            queued.dataset.id))

        invalidated = self._add_fixture(compiled=True)
        await self.service.invalidate_submission(
            submission_id=invalidated.submission.id, level="compilation")
        assert_landed(invalidated.compilation())

        written = self._add_fixture(testcases=2)
        job = CompilationJob(
            operation=written.compilation(), task_type="Batch",
            task_type_parameters={}, language=None, files={}, managers={},
            success=True, compilation_success=True,
            text=["Compiled successfully."], plus={})
        await self.service.write_results(
            [(written.compilation(), Result(job, True))])
        assert_landed(written.evaluation("t0"), written.evaluation("t1"))

        swept = self._add_fixture()
        with self.assertLogs(
                "cms.io.async_triggeredservice", level="INFO") as logs:
            await self.service._sweep()
        assert_landed(swept.compilation())
        self.assertIn("Found 1 missed operation(s)", logs.output[-1])

    async def test_a_sweep_sends_what_it_finds_in_one_job_group(self):
        # The pushes of a section reach the queue together, so the
        # executor batches them as it always did.
        fixture = self._add_fixture(testcases=3, compiled=True)
        worker = ControllableWorker()
        await self._start_worker(worker)

        await self.service._sweep()
        await self._wait_until_idle()

        self.assertEqual(len(worker.received_job_groups), 1)
        self.assertCountEqual(
            worker.jobs,
            [("evaluation", fixture.submission.id, "t%d" % index)
             for index in range(3)])
        self.assertTrue(self._load_result(fixture).evaluated())

    # -- results written from an executor thread -------------------------

    async def test_results_arriving_while_another_thread_writes_are_kept(self):
        # The DB is written by an executor thread that holds
        # post_finish_lock. The answer of a second worker meanwhile is
        # handled by another executor thread, which has to wait for the
        # lock. The loop keeps serving the workers in the meantime, and
        # both results are written and notified exactly once.
        first = self._add_fixture()
        second = self._add_fixture()
        workers = [ControllableWorker(), ControllableWorker()]
        for shard, worker in enumerate(workers):
            await self._start_worker(worker, shard)
        writing, proceed, writer_threads = self._watch_writes(gated=True)
        loop_thread = threading.get_ident()

        self.assertTrue(await self.service.enqueue(
            first.compilation(), PriorityQueue.PRIORITY_HIGH,
            first.submission.timestamp))
        await self._wait_for(writing.is_set,
                             "a thread to start writing the first result")

        # Pushed from the loop, as enqueue() would need the lock.
        AsyncTriggeredService.enqueue(
            self.service, second.compilation(), PriorityQueue.PRIORITY_HIGH,
            second.submission.timestamp)
        await self._wait_for(
            lambda: sum(len(worker.jobs) for worker in workers) == 2,
            "the second job to reach a worker, with the write held")
        self.assertFalse(proceed.is_set())
        self.assertEqual(self.notifications.call_count, 0)
        self.assertIsNone(self._load_result(second))

        proceed.set()
        await self._wait_until_idle()

        sent = sorted(job for worker in workers for job in worker.jobs)
        self.assertEqual(sent, sorted([
            ("compilation", first.submission.id, None),
            ("compilation", second.submission.id, None)]))
        self.assertEqual(len(writer_threads), 2)
        self.assertNotIn(loop_thread, writer_threads)
        for fixture in (first, second):
            self.assertTrue(self._load_result(fixture).compilation_failed())
        self.assertEqual(self.notifications.call_count, 2)
        self.assertCountEqual(
            self.scoring_stub.new_evaluation_calls,
            [first.key, second.key])

    async def test_operations_queued_by_the_writing_thread_land_on_the_loop(
        self
    ):
        # write_results runs on an executor thread, and what it queues
        # next (the evaluations, after a successful compilation) has to
        # reach the asyncio queue through the loop.
        fixture = self._add_fixture(testcases=3)
        _, _, writer_threads = self._watch_writes()
        queue = self.service.get_executor()._operation_queue
        push_threads: list[int] = []
        real_push = queue.push

        def recording_push(*args, **kwargs):
            push_threads.append(threading.get_ident())
            return real_push(*args, **kwargs)

        operation = fixture.compilation()
        job = CompilationJob(
            operation=operation, task_type="Batch", task_type_parameters={},
            language=None, files={}, managers={}, success=True,
            compilation_success=True, text=["Compiled successfully."],
            plus={})

        with patch.object(queue, "push", recording_push):
            await asyncio.wait_for(
                self.service.write_results([(operation, Result(job, True))]),
                timeout=10)
            await self._wait_for(
                lambda: len(push_threads) == 3
                and not self.service._pending_operations,
                "the evaluations to be queued")

        loop_thread = threading.get_ident()
        self.assertNotIn(loop_thread, writer_threads)
        self.assertEqual(len(writer_threads), 1)
        self.assertEqual(set(push_threads), {loop_thread})
        for index in range(3):
            self.assertIn(fixture.evaluation("t%d" % index),
                          self.service.get_executor())

    async def test_row_that_cannot_be_written_does_not_stop_its_batch(self):
        first = self._add_fixture()
        second = self._add_fixture()
        poisoned = MagicMock()
        poisoned.plus = {}
        poisoned.to_submission.side_effect = RuntimeError("cannot be stored")
        good_job = CompilationJob(
            operation=second.compilation(), success=True,
            compilation_success=False, text=["Compilation failed."],
            plus={})

        with self.assertLogs(
                "cms.service.EvaluationService", level="ERROR") as logs:
            await self.service.write_results([
                (first.compilation(), Result(poisoned, True)),
                (second.compilation(), Result(good_job, True))])

        errors = [record for record in logs.records
                  if record.levelname == "ERROR"]
        self.assertEqual(
            [record.getMessage() for record in errors],
            ["Unexpected exception while inserting worker result."])
        self.assertIsNotNone(errors[0].exc_info)
        self.assertFalse(self._compiled(first))
        self.assertTrue(self._load_result(second).compilation_failed())
        self.assertEqual(self.notifications.call_count, 1)

    async def test_duplicate_evaluation_is_refused_and_not_counted_twice(self):
        # The DB is the last guard against a result written twice (say,
        # a worker answering for an operation already given to another
        # one): the unique constraint refuses it, and the rest of the
        # batch is still written.
        fixture = self._add_fixture(testcases=2, compiled=True)

        def result_of(codename: str) -> tuple[ESOperation, Result]:
            operation = fixture.evaluation(codename)
            job = self._evaluation_job(operation)
            return operation, Result(job, True)

        await self.service.write_results([result_of("t0")])
        self.assertEqual(self.notifications.call_count, 0)

        with self.assertLogs(
                "cms.service.EvaluationService", level="WARNING") as logs:
            await self.service.write_results(
                [result_of("t0"), result_of("t1")])

        self.assertEqual(
            [record.getMessage() for record in logs.records
             if record.levelname == "WARNING"],
            ["Integrity error while inserting worker result."])
        result = self._load_result(fixture)
        self.assertEqual(sorted(e.codename for e in result.evaluations),
                         ["t0", "t1"])
        self.assertTrue(result.evaluated())
        self.assertEqual(self.notifications.call_count, 1)

    async def test_results_of_objects_that_are_gone_do_not_stop_their_batch(
        self
    ):
        # The results of a dataset, submission or user test that is gone
        # (deleted while it was being judged) are skipped and logged; the
        # healthy results before and after them in the same batch are
        # still written, and notified to ScoringService once each.
        first = self._add_fixture()
        second = self._add_fixture()

        def failed_compilation(operation: ESOperation):
            job = CompilationJob(
                operation=operation, success=True,
                compilation_success=False, text=["Compilation failed."],
                plus={})
            return operation, Result(job, True)

        no_dataset = ESOperation(
            ESOperation.COMPILATION, first.submission.id, 987654321)
        no_submission = ESOperation(
            ESOperation.COMPILATION, 987654321, first.dataset.id)
        no_user_test = ESOperation(
            ESOperation.USER_TEST_COMPILATION, 987654321, first.dataset.id)
        no_user_test_evaluation = ESOperation(
            ESOperation.USER_TEST_EVALUATION, 987654321, first.dataset.id)

        with self.assertLogs(
                "cms.service.EvaluationService", level="INFO") as logs:
            await self.service.write_results([
                failed_compilation(first.compilation()),
                failed_compilation(no_dataset),
                failed_compilation(no_submission),
                failed_compilation(no_user_test),
                (no_user_test_evaluation, Result(
                    self._evaluation_job(no_user_test_evaluation), True)),
                failed_compilation(second.compilation())])
        await self._wait_until_idle()

        self.assertEqual(
            sorted(record.getMessage() for record in logs.records
                   if record.levelname == "ERROR"),
            ["Could not find %s in the database." % missing
             for missing in ("dataset 987654321", "submission 987654321",
                             "user test 987654321",
                             "user test 987654321")])
        # The loop that ends the operations says what it skips too.
        self.assertEqual(
            [record.getMessage() for record in logs.records
             if "not found, not ending" in record.getMessage()],
            ["Result of %s not found, not ending its %s." % skipped
             for skipped in (
                 ("submission %d(987654321)" % first.submission.id,
                  "compilation"),
                 ("submission 987654321(%d)" % first.dataset.id,
                  "compilation"),
                 ("user test 987654321(%d)" % first.dataset.id,
                  "compilation"),
                 ("user test 987654321(%d)" % first.dataset.id,
                  "evaluation"))])
        for fixture in (first, second):
            self.assertTrue(self._load_result(fixture).compilation_failed())
        self.assertEqual(self.notifications.call_count, 2)
        self.assertCountEqual(
            self.scoring_stub.new_evaluation_calls,
            [first.key, second.key])

    async def test_evaluation_on_a_gone_dataset_does_not_stop_its_batch(
        self
    ):
        # A dataset deleted while a contestant's submission was being
        # evaluated on it: that result is skipped, even though its 0
        # evaluations match the 0 testcases of a dataset that is gone.
        # The last evaluation of another contestant's submission, in the
        # same batch, still completes it: its outcome is committed (read
        # back with the test's own session) and ScoringService is told
        # once.
        sibling = self._add_fixture(testcases=1, compiled=True)
        other_submission = self._add_submission_of(sibling)
        on_gone_dataset = ESOperation(
            ESOperation.EVALUATION, other_submission.id, 987654321, "t0")
        sibling_operation = sibling.evaluation("t0")

        with self.assertLogs(
                "cms.service.EvaluationService", level="ERROR") as logs:
            await self.service.write_results([
                (sibling_operation,
                 Result(self._evaluation_job(sibling_operation), True)),
                (on_gone_dataset,
                 Result(self._evaluation_job(on_gone_dataset), True))])
        await self._wait_until_idle()

        self.assertIn("Could not find dataset 987654321 in the database.",
                      [record.getMessage() for record in logs.records])
        result = self._load_result(sibling)
        self.assertEqual([e.codename for e in result.evaluations], ["t0"])
        self.assertEqual(result.evaluation_outcome, "ok")
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [sibling.key])

    async def test_evaluation_of_a_gone_submission_does_not_stop_its_batch(
        self
    ):
        # A submission deleted while it was being evaluated, on a dataset
        # that still has its testcases: its result is skipped, and since
        # its evaluations don't add up to the testcases, only the loop
        # that ends the operations meets it. The last evaluation of
        # another submission, after it in the batch, still completes
        # that one, which is notified once.
        sibling = self._add_fixture(testcases=1, compiled=True)
        of_gone_submission = ESOperation(
            ESOperation.EVALUATION, 987654321, sibling.dataset.id, "t0")
        sibling_operation = sibling.evaluation("t0")

        with self.assertLogs(
                "cms.service.EvaluationService", level="INFO") as logs:
            await self.service.write_results([
                (of_gone_submission,
                 Result(self._evaluation_job(of_gone_submission), True)),
                (sibling_operation,
                 Result(self._evaluation_job(sibling_operation), True))])
        await self._wait_until_idle()

        self.assertEqual(
            [record.getMessage() for record in logs.records
             if record.levelname == "ERROR"],
            ["Could not find submission 987654321 in the database."])
        self.assertIn(
            "Result of submission 987654321(%d) not found, not ending its "
            "evaluation." % sibling.dataset.id,
            [record.getMessage() for record in logs.records])
        result = self._load_result(sibling)
        self.assertEqual(result.evaluation_outcome, "ok")
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [sibling.key])

    async def test_gone_keys_with_two_phase_on_do_not_stop_their_batch(self):
        # The real contests run with two-phase evaluation on. Next to the
        # results of a dataset and of a submission that are gone, a
        # submission whose screening passes gets its phase 2 queued, and
        # one whose screening fails gets the rest of its group skipped,
        # is finalized and is notified once.
        self.enterContext(
            patch.object(twophase, "enabled", return_value=True))
        passing = self._add_fixture(compiled=True)
        failing = self._add_fixture(compiled=True)
        for fixture in (passing, failing):
            for codename in ("s1-00-sample", "s1-01-normal"):
                self.add_testcase(fixture.dataset, codename=codename)
        self.session.commit()
        other_submission = self._add_submission_of(passing)
        on_gone_dataset = ESOperation(
            ESOperation.EVALUATION, other_submission.id, 987654321,
            "s1-00-sample")
        of_gone_submission = ESOperation(
            ESOperation.EVALUATION, 987654321, passing.dataset.id,
            "s1-00-sample")

        def evaluated(operation: ESOperation, outcome: str):
            job = self._evaluation_job(operation, outcome)
            return operation, Result(job, True)

        queue = self.service.get_executor()._operation_queue
        pushed: list[ESOperation] = []
        real_push = queue.push

        def recording_push(item, *args, **kwargs):
            pushed.append(item)
            return real_push(item, *args, **kwargs)

        with patch.object(queue, "push", recording_push), \
                self.assertLogs(
                    "cms.service.EvaluationService", level="ERROR") as logs:
            await self.service.write_results([
                evaluated(on_gone_dataset, "1.0"),
                evaluated(passing.evaluation("s1-00-sample"), "1.0"),
                evaluated(of_gone_submission, "1.0"),
                evaluated(failing.evaluation("s1-00-sample"), "0.0")])
            await self._wait_for(
                lambda: not self.service._pending_operations,
                "the phase 2 operations to be queued")
        await self._wait_for(
            lambda: len(self.scoring_stub.new_evaluation_calls)
            == self.notifications.call_count,
            "the notifications to reach ScoringService")

        self.assertEqual(
            sorted(record.getMessage() for record in logs.records),
            ["Could not find dataset 987654321 in the database.",
             "Could not find submission 987654321 in the database."])
        # Screening passed: phase 2 is queued, and nothing is final yet.
        self.assertEqual(pushed, [passing.evaluation("s1-01-normal")])
        self.assertIn(passing.evaluation("s1-01-normal"),
                      self.service.get_executor())
        result = self._load_result(passing)
        self.assertEqual([e.codename for e in result.evaluations],
                         ["s1-00-sample"])
        self.assertIsNone(result.evaluation_outcome)
        # Screening failed: the rest is skipped, and the result is final.
        result = self._load_result(failing)
        self.assertEqual(
            sorted((e.codename, e.outcome) for e in result.evaluations),
            [("s1-00-sample", "0.0"), ("s1-01-normal", "0.0")])
        self.assertEqual(result.evaluation_outcome, "ok")
        self.assertEqual(self.notifications.call_count, 1)
        self.assertEqual(self.scoring_stub.new_evaluation_calls,
                         [failing.key])


if __name__ == "__main__":
    unittest.main()
