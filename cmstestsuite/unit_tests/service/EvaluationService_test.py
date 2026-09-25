#!/usr/bin/env python3

"""Tests for cms.service.EvaluationService."""

import asyncio
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import cms.service.EvaluationService as EvaluationServiceModule
from cms.conf import Address, ServiceCoord
from cms.grading.Job import CompilationJob
from cms.io.async_rpc import AsyncRemoteServiceClient, AsyncRemoteServiceServer
from cms.io.async_triggeredservice import AsyncTriggeredService
from cms.io.priorityqueue import PriorityQueue
from cms.io.rpc import rpc_method
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, Result
from cms.service.workerpool import WorkerPool
from cmscommon.datetime import make_datetime
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


class FakeWorker:
    """A minimal stand-in for the Worker RPC server WorkerPool talks to.

    Instead of actually compiling/evaluating, it echoes back the
    received job group with each job marked as done, letting tests
    drive the acquire -> execute -> action_finished -> write_results
    round trip without a real sandboxed Worker.

    """

    def __init__(self, compilation_success: bool = True):
        self.compilation_success = compilation_success
        self.received_job_groups: list[dict] = []
        self.precache_calls: list[int] = []
        self.quit_calls: list[str] = []

    @rpc_method
    async def execute_job_group(self, job_group_dict: dict) -> dict:
        self.received_job_groups.append(job_group_dict)
        for job in job_group_dict["jobs"]:
            job["success"] = True
            job["plus"] = {}
            if job["type"] == "compilation":
                job["compilation_success"] = self.compilation_success
                job["text"] = ["Compilation result."]
                job["executables"] = {}
        return job_group_dict

    @rpc_method
    async def precache_files(self, contest_id: int) -> None:
        self.precache_calls.append(contest_id)

    @rpc_method
    async def quit(self, reason: str) -> None:
        self.quit_calls.append(reason)


class FakeScoringService:
    """A minimal stand-in for ScoringService, recording new_evaluation calls."""

    def __init__(self):
        self.new_evaluation_calls: list[tuple[int, int]] = []

    @rpc_method
    async def new_evaluation(self, submission_id: int, dataset_id: int) -> None:
        self.new_evaluation_calls.append((submission_id, dataset_id))


async def _start_server(local_service: object) -> tuple[asyncio.Server, int]:
    """Start a loopback server exposing local_service's RPC methods.

    local_service: object exposing the RPC methods to serve.

    return: the started server (must be closed by the caller) and the
        port it is listening on.

    """
    async def handle_client(reader, writer):
        server_side = AsyncRemoteServiceServer(
            local_service, Address("127.0.0.1", 0))
        server_side.initialize_streams(reader, writer, plus=None)
        await server_side.run()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    asyncio.create_task(server.serve_forever())
    return server, server.sockets[0].getsockname()[1]


class EvaluationServiceTest(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    async def asyncSetUp(self):
        self._servers: list[asyncio.Server] = []
        self._clients: list[AsyncRemoteServiceClient] = []

        # EvaluationService.__init__ (via WorkerPool.__init__, which calls
        # get_service_shards("Worker") defined in cms.util) would
        # otherwise try to connect to every configured Worker shard;
        # tests wire their own worker peers instead.
        config_patcher = patch("cms.util.config.services", {})
        config_patcher.start()
        self.addCleanup(config_patcher.stop)

        address_patcher = patch(
            "cms.io.async_service.get_service_address",
            return_value=Address("127.0.0.1", 0))
        address_patcher.start()
        self.addCleanup(address_patcher.stop)

        # EvaluationService.__init__ connects to LogService (via
        # AsyncService.__init__) and to ScoringService: both go through
        # async_rpc's own imported reference to get_service_address.
        rpc_address_patcher = patch(
            "cms.io.async_rpc.get_service_address",
            return_value=Address("127.0.0.1", 0))
        rpc_address_patcher.start()
        self.addCleanup(rpc_address_patcher.stop)

        self.service = self._build_service()

        # Wire a real, connected peer standing in for ScoringService.
        self.scoring_service_stub = FakeScoringService()
        self.scoring_client = await self._add_connected_service(
            ServiceCoord("ScoringService", 0), self.scoring_service_stub)
        self.service.scoring_service = self.scoring_client

    async def asyncTearDown(self):
        for client in self._clients:
            client.disconnect()
        for server in self._servers:
            server.close()
            await server.wait_closed()

    def _build_service(self, contest_id: int | None = None) -> EvaluationService:
        """Build an EvaluationService safe to drive from a running loop.

        Mirrors ProxyService_test.py's _build_service: constructs the
        service while the test's own event loop is already running
        (unlike production, where the launcher builds the service
        before run() starts the loop), sets self._loop explicitly so
        thread-safe dispatch helpers (_push_to_queue,
        _threadsafe_notify_scoring_service, WorkerPool's
        _threadsafe_set_workers_available) take their
        call_soon_threadsafe branch as they would in production, and
        stubs out start_sweeper so a background sweep can't race with
        the test's own operations.

        """
        with patch.object(
                EvaluationService, "start_sweeper", lambda self, timeout: None):
            service = EvaluationService(shard=0, contest_id=contest_id)
        service._loop = asyncio.get_running_loop()
        self.addCleanup(service._disconnect_all)

        # EvaluationExecutor.max_operations_per_batch divides by
        # len(self.pool): with zero workers registered (the case here,
        # since config.services is patched to {}), the executor's
        # always-running background run() loop would crash with a
        # ZeroDivisionError as soon as anything is enqueued. Register one
        # placeholder worker (left unconnected, so acquire_worker never
        # actually hands operations to it) so tests that don't care about
        # real worker dispatch can still enqueue safely; tests that do
        # care add/override a real connected worker via
        # _add_connected_worker.
        service.get_executor().pool.add_worker(ServiceCoord("Worker", 0))
        return service

    async def _add_connected_service(
        self, coord: ServiceCoord, local_service: object
    ) -> AsyncRemoteServiceClient:
        """Wire a real, connected peer for coord.

        coord: the coord to register the peer under.
        local_service: object exposing the RPC methods to serve.

        return: the connected client.

        """
        server, port = await _start_server(local_service)
        self._servers.append(server)

        with patch("cms.io.async_rpc.get_service_address",
                   return_value=Address("127.0.0.1", 0)):
            client = AsyncRemoteServiceClient(coord)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        client.initialize_streams(reader, writer, plus=None)
        asyncio.create_task(client.run())
        self._clients.append(client)

        self.service.remote_services[coord] = client
        return client

    async def _add_connected_worker(
        self, shard: int, fake_worker: FakeWorker
    ) -> AsyncRemoteServiceClient:
        """Add a worker to the pool, backed by a real, connected peer.

        WorkerPool.add_worker's own connect_to() call creates a client
        that never actually connects (the patched address points
        nowhere); replace it with a client connected to a loopback
        server serving fake_worker, the same override-after-construct
        pattern used for ScoringService above.

        shard: the worker shard to add.
        fake_worker: the local service standing in for the Worker.

        return: the connected client, already stored in
            pool._worker[shard].

        """
        pool: WorkerPool = self.service.get_executor().pool
        pool.add_worker(ServiceCoord("Worker", shard))
        client = await self._add_connected_service(
            ServiceCoord("Worker", shard), fake_worker)
        pool._worker[shard] = client
        return client

    def _build_submission(self, with_testcase: bool = False):
        """Build a minimal contest/task/dataset/submission ready to judge.

        with_testcase: whether to add a testcase to the dataset, so a
            successful compilation has an evaluation operation to
            generate next.

        return: the created contest, task, dataset and submission.

        """
        contest = self.add_contest()
        task = self.add_task(contest=contest)
        dataset = self.add_dataset(task=task, task_type="Batch")
        task.active_dataset = dataset
        if with_testcase:
            self.add_testcase(dataset=dataset)
        participation = self.add_participation(contest=contest)
        submission = self.add_submission(task=task, participation=participation)
        self.session.commit()
        return contest, task, dataset, submission

    async def _wait_until(self, predicate, attempts: int = 50):
        """Poll predicate() until it is true, or give up.

        predicate: a zero-argument callable to poll.
        attempts: how many times to poll, sleeping 0.05s between tries.

        """
        for _ in range(attempts):
            if predicate():
                return
            await asyncio.sleep(0.05)

    # -- new_submission / new_user_test -------------------------------

    async def test_new_submission_enqueues_compilation(self):
        _contest, _task, dataset, submission = self._build_submission()
        operation = ESOperation(ESOperation.COMPILATION, submission.id, dataset.id)

        await self.service.new_submission(submission.id)

        # _push_to_queue's enqueue is dispatched via call_soon_threadsafe,
        # so the operation may not be visible until the next loop
        # iteration after new_submission()'s await returns.
        await self._wait_until(lambda: operation in self.service.get_executor())
        self.assertIn(operation, self.service.get_executor())

    async def test_new_submission_unknown_id_does_not_crash(self):
        # Should just log an error and return, not raise.
        await self.service.new_submission(123456789)

    async def test_new_user_test_enqueues_compilation(self):
        _contest, task, dataset, _submission = self._build_submission()
        participation = self.add_participation(contest=task.contest)
        user_test = self.add_user_test(task=task, participation=participation)
        self.session.commit()
        operation = ESOperation(
            ESOperation.USER_TEST_COMPILATION, user_test.id, dataset.id)

        await self.service.new_user_test(user_test.id)

        await self._wait_until(lambda: operation in self.service.get_executor())
        self.assertIn(operation, self.service.get_executor())

    # -- full acquire -> execute -> action_finished -> write_results --

    async def test_full_round_trip_writes_result_and_notifies_scoring(self):
        _contest, _task, dataset, submission = self._build_submission()
        fake_worker = FakeWorker(compilation_success=False)
        await self._add_connected_worker(0, fake_worker)

        operation = ESOperation(ESOperation.COMPILATION, submission.id, dataset.id)
        enqueued = await self.service.enqueue(
            operation, PriorityQueue.PRIORITY_HIGH, submission.timestamp)
        self.assertTrue(enqueued)

        # Give the executor's run() loop time to acquire the worker and
        # for action_finished to land the result in the cache.
        await self._wait_until(lambda: operation in self.service.result_cache)
        self.assertEqual(len(fake_worker.received_job_groups), 1)

        # Force an immediate flush instead of waiting up to
        # MAX_FLUSHING_TIME_SECONDS for the background flush loop.
        await self.service.result_cache.flush()

        self.session.expire_all()
        submission_result = submission.get_result(dataset)
        self.assertIsNotNone(submission_result)
        self.assertTrue(submission_result.compilation_failed())

        await self._wait_until(
            lambda: self.scoring_service_stub.new_evaluation_calls != [])
        self.assertEqual(
            self.scoring_service_stub.new_evaluation_calls,
            [(submission.id, dataset.id)])

    # -- post_finish_lock reentrancy ------------------------------------

    async def test_write_results_reentrant_lock_does_not_deadlock(self):
        # write_results -> compilation_ended -> submission_enqueue_operations
        # -> _enqueue_sync, all guarded by the same post_finish_lock: this
        # must complete without deadlocking, and the follow-up evaluation
        # operation it triggers must actually land in the queue.
        _contest, _task, dataset, submission = self._build_submission(
            with_testcase=True)
        testcase = next(iter(dataset.testcases.values()))

        operation = ESOperation(ESOperation.COMPILATION, submission.id, dataset.id)
        job = CompilationJob(
            operation=operation,
            task_type="Batch",
            task_type_parameters={},
            language=None,
            files={},
            managers={},
            success=True,
            compilation_success=True,
            text=["Compiled successfully."],
            plus={},
        )
        items = [(operation, Result(job, True))]

        await asyncio.wait_for(self.service.write_results(items), timeout=5)

        self.session.expire_all()
        submission_result = submission.get_result(dataset)
        self.assertTrue(submission_result.compilation_succeeded())

        evaluation_operation = ESOperation(
            ESOperation.EVALUATION, submission.id, dataset.id, testcase.codename)
        await self._wait_until(
            lambda: evaluation_operation in self.service.get_executor())
        self.assertIn(evaluation_operation, self.service.get_executor())

    # -- invalidate_submission -------------------------------------------

    async def test_invalidate_submission_clears_and_reenqueues_compilation(self):
        _contest, _task, dataset, submission = self._build_submission()
        submission_result = self.add_submission_result(
            submission=submission, dataset=dataset)
        submission_result.set_compilation_outcome(True)
        self.session.commit()
        self.assertTrue(submission_result.compiled())

        await self.service.invalidate_submission(
            submission_id=submission.id, level="compilation")

        self.session.expire_all()
        self.assertFalse(submission_result.compiled())

        operation = ESOperation(ESOperation.COMPILATION, submission.id, dataset.id)
        await self._wait_until(lambda: operation in self.service.get_executor())
        self.assertIn(operation, self.service.get_executor())

    async def test_invalidate_submission_rejects_bad_level(self):
        with self.assertRaises(ValueError):
            await self.service.invalidate_submission(level="not-a-level")

    # -- concurrency regressions -------------------------------------------

    async def test_execute_does_not_deadlock_under_executor_pressure(self):
        # Regression test: EvaluationExecutor.execute() used to hold
        # _current_execution_lock (a threading.RLock) across an await
        # on loop.run_in_executor (building the job group). With every
        # executor thread busy -- one inside invalidate_submission
        # waiting for that same lock, the other waiting for
        # post_finish_lock held by the first -- the job group could
        # never be built, deadlocking the whole service.
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
        # If this ever regresses, cancelling the executor's run() task
        # unwinds execute() and releases the lock, so the executor
        # threads can finish and the test fails instead of hanging at
        # loop shutdown.
        self.addCleanup(self._cancel_background_tasks)

        _contest, _task, dataset, submission = self._build_submission()
        fake_worker = FakeWorker(compilation_success=False)
        await self._add_connected_worker(0, fake_worker)
        pool: WorkerPool = self.service.get_executor().pool

        # Signal (from execute(), while it holds _current_execution_lock)
        # that the worker has been acquired.
        worker_acquired = threading.Event()
        original_add_operations = pool._add_operations

        def add_operations_and_signal(shard, operations):
            original_add_operations(shard, operations)
            worker_acquired.set()
        pool._add_operations = add_operations_and_signal

        # Make the invalidation reach its dequeue only once execute()
        # is inside its locked section.
        original_get_relevant_operations = \
            EvaluationServiceModule.get_relevant_operations

        def gated_get_relevant_operations(*args, **kwargs):
            operations = original_get_relevant_operations(*args, **kwargs)
            worker_acquired.wait(timeout=10)
            return operations
        get_relevant_patcher = patch.object(
            EvaluationServiceModule, "get_relevant_operations",
            gated_get_relevant_operations)
        get_relevant_patcher.start()
        self.addCleanup(get_relevant_patcher.stop)

        # Occupy both executor threads with invalidations: the first
        # holds post_finish_lock at the gate, the second waits for it.
        invalidations = [
            asyncio.create_task(self.service.invalidate_submission(
                submission_id=submission.id, level="compilation"))
            for _ in range(2)]
        await asyncio.sleep(0.1)

        # Push directly into the queue on the loop thread (the async
        # enqueue() would itself need an executor thread).
        operation = ESOperation(
            ESOperation.COMPILATION, submission.id, dataset.id)
        AsyncTriggeredService.enqueue(
            self.service, operation, PriorityQueue.PRIORITY_HIGH,
            submission.timestamp)

        async def worker_received_job_group():
            while fake_worker.received_job_groups == []:
                await asyncio.sleep(0.05)

        await asyncio.wait_for(
            asyncio.gather(*invalidations, worker_received_job_group()),
            timeout=10)
        self.assertTrue(worker_acquired.is_set())
        self.assertEqual(len(fake_worker.received_job_groups), 1)

    async def test_enqueue_sync_twice_enqueues_once(self):
        # Regression test: the push decided by _enqueue_sync lands on
        # the loop later, so a second call in between used to also
        # return True (and push a duplicate).
        _contest, _task, dataset, submission = self._build_submission()
        operation = ESOperation(
            ESOperation.COMPILATION, submission.id, dataset.id)
        loop = asyncio.get_running_loop()

        def enqueue_twice():
            return [self.service._enqueue_sync(
                operation, PriorityQueue.PRIORITY_HIGH,
                submission.timestamp) for _ in range(2)]

        results = await asyncio.wait_for(
            loop.run_in_executor(None, enqueue_twice), timeout=10)
        self.assertEqual(results, [True, False])

        # Same from two concurrent tasks (on two executor threads).
        other = ESOperation(
            ESOperation.USER_TEST_COMPILATION, 12345, dataset.id)
        results = await asyncio.wait_for(asyncio.gather(*(
            self.service.enqueue(
                other, PriorityQueue.PRIORITY_HIGH, submission.timestamp)
            for _ in range(2))), timeout=10)
        self.assertEqual(sorted(results), [False, True])

        await self._wait_until(lambda: self.service._pending_pushes == set())
        self.assertEqual(self.service._pending_pushes, set())

    async def test_release_racing_timeout_leaves_consistent_state(self):
        # Regression test: release_worker (from action_finished, on an
        # executor thread) racing check_timeouts (on the loop thread)
        # used to be able to leave a timed-out worker re-enabled while
        # also accepting its result and requeuing its operations.
        _contest, _task, dataset, submission = self._build_submission()
        fake_worker = FakeWorker()
        await self._add_connected_worker(0, fake_worker)
        pool: WorkerPool = self.service.get_executor().pool

        operation = ESOperation(
            ESOperation.COMPILATION, submission.id, dataset.id)
        operation.side_data = (
            PriorityQueue.PRIORITY_HIGH, submission.timestamp)
        pool._add_operations(0, [operation])
        pool._start_time[0] = make_datetime() - WorkerPool.WORKER_TIMEOUT \
            - timedelta(seconds=1)

        # Pause release_worker (on its executor thread) right after it
        # starts reading the worker state, so check_timeouts runs in the
        # middle of it unless the whole method is guarded.
        release_paused = threading.Event()

        class PausingDict(dict):
            def __getitem__(self, key):
                if threading.current_thread() is not \
                        threading.main_thread() \
                        and not release_paused.is_set():
                    release_paused.set()
                    time.sleep(0.3)
                return super().__getitem__(key)
        pool._ignore = PausingDict(pool._ignore)

        loop = asyncio.get_running_loop()
        release = loop.run_in_executor(None, pool.release_worker, 0)
        for _ in range(100):
            if release_paused.is_set():
                break
            await asyncio.sleep(0.01)
        self.assertTrue(release_paused.is_set())

        lost_operations = pool.check_timeouts()
        released = await asyncio.wait_for(release, timeout=10)

        if released is True:
            # The timeout won: the worker's result is ignored, its
            # operation handed back, and the worker disabled.
            self.assertEqual(lost_operations, [operation])
            self.assertEqual(pool._operations[0], WorkerPool.WORKER_DISABLED)
        else:
            # The release won: the result is accepted, nothing is
            # handed back, and the worker is available again.
            self.assertEqual(lost_operations, [])
            self.assertEqual(pool._operations[0], WorkerPool.WORKER_INACTIVE)
        self.assertFalse(pool._ignore[0])
        self.assertFalse(pool._schedule_disabling[0])
        self.assertIsNone(pool._start_time[0])
        self.assertNotIn(operation, pool)

    def _cancel_background_tasks(self):
        """Cancel every task the service spawned (e.g. executor run())."""
        for task in list(self.service._background_tasks):
            task.cancel()

    # -- check_workers_timeout / check_workers_connection ------------------

    async def test_check_workers_timeout_requeues_stale_operation(self):
        _contest, _task, dataset, submission = self._build_submission()
        fake_worker = FakeWorker()
        await self._add_connected_worker(0, fake_worker)
        pool: WorkerPool = self.service.get_executor().pool

        operation = ESOperation(ESOperation.COMPILATION, submission.id, dataset.id)
        operation.side_data = (PriorityQueue.PRIORITY_HIGH, submission.timestamp)
        pool._add_operations(0, [operation])
        # Simulate a worker that has been unresponsive for far longer
        # than WorkerPool.WORKER_TIMEOUT, instead of waiting 600 real
        # seconds.
        pool._start_time[0] = make_datetime() - WorkerPool.WORKER_TIMEOUT \
            - timedelta(seconds=1)

        await self.service.check_workers_timeout()

        await self._wait_until(lambda: operation in self.service.get_executor())
        self.assertIn(operation, self.service.get_executor())
        self.assertEqual(pool._operations[0], WorkerPool.WORKER_DISABLED)
        await self._wait_until(lambda: fake_worker.quit_calls != [])
        self.assertEqual(len(fake_worker.quit_calls), 1)

    async def test_check_workers_connection_requeues_disconnected_worker(self):
        _contest, _task, dataset, submission = self._build_submission()
        fake_worker = FakeWorker()
        client = await self._add_connected_worker(0, fake_worker)
        pool: WorkerPool = self.service.get_executor().pool

        operation = ESOperation(ESOperation.COMPILATION, submission.id, dataset.id)
        operation.side_data = (PriorityQueue.PRIORITY_HIGH, submission.timestamp)
        pool._add_operations(0, [operation])
        client.disconnect()

        await self.service.check_workers_connection()

        await self._wait_until(lambda: operation in self.service.get_executor())
        self.assertIn(operation, self.service.get_executor())

    # -- disable_worker / enable_worker -----------------------------------

    async def test_disable_worker_requeues_its_operations(self):
        _contest, _task, dataset, submission = self._build_submission()
        fake_worker = FakeWorker()
        await self._add_connected_worker(0, fake_worker)
        pool: WorkerPool = self.service.get_executor().pool

        operation = ESOperation(ESOperation.COMPILATION, submission.id, dataset.id)
        operation.side_data = (PriorityQueue.PRIORITY_HIGH, submission.timestamp)
        pool._add_operations(0, [operation])

        result = await self.service.disable_worker(0)

        self.assertTrue(result)
        await self._wait_until(lambda: operation in self.service.get_executor())
        self.assertIn(operation, self.service.get_executor())
        self.assertEqual(pool._operations[0], WorkerPool.WORKER_DISABLED)

    async def test_enable_worker_makes_worker_eligible_again(self):
        fake_worker = FakeWorker()
        await self._add_connected_worker(0, fake_worker)
        pool: WorkerPool = self.service.get_executor().pool
        pool.disable_worker(0)
        self.assertEqual(pool._operations[0], WorkerPool.WORKER_DISABLED)

        result = self.service.enable_worker(0)

        self.assertTrue(result)
        self.assertEqual(pool._operations[0], WorkerPool.WORKER_INACTIVE)


if __name__ == "__main__":
    unittest.main()
