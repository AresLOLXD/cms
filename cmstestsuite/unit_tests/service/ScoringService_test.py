#!/usr/bin/env python3

"""Tests for cms.service.ScoringService."""

import asyncio
import threading
import time
import unittest
from unittest.mock import patch

from sqlalchemy import text

import cms.service.ScoringService as ScoringServiceModule
from cms.conf import Address, ServiceCoord
from cms.db import Session, SubmissionResult
from cms.io.async_rpc import AsyncRemoteServiceClient, AsyncRemoteServiceServer
from cms.io.rpc import rpc_method
from cms.service.ScoringService import ScoringExecutor, ScoringService
from cms.service.scoringoperations import ScoringOperation
from cmscommon.datetime import make_datetime
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin
from cmstestsuite.unit_tests.stuckpeer import StuckPeer, connect_client


class RecordingProxyService:
    """A minimal stand-in for ProxyService, recording calls received."""

    def __init__(self):
        self.submission_scored_calls: list[int] = []

    @rpc_method
    def submission_scored(self, submission_id: int) -> None:
        self.submission_scored_calls.append(submission_id)


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


class ScoringServiceTest(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):

    async def asyncSetUp(self):
        self._servers: list[asyncio.Server] = []
        self._clients: list[AsyncRemoteServiceClient] = []

        address_patcher = patch(
            "cms.io.async_service.get_service_address",
            return_value=Address("127.0.0.1", 0))
        address_patcher.start()
        self.addCleanup(address_patcher.stop)

        # ScoringService.__init__ connects to LogService (via
        # AsyncService.__init__) and to ProxyService: both go through
        # async_rpc's own imported reference to get_service_address.
        rpc_address_patcher = patch(
            "cms.io.async_rpc.get_service_address",
            return_value=Address("127.0.0.1", 0))
        rpc_address_patcher.start()
        self.addCleanup(rpc_address_patcher.stop)

        # The service is built with a loop already running, so its
        # sweeper would start at once and enqueue (and score, in the
        # background) whatever earlier tests left in the DB, racing
        # with the executor calls the tests make by hand.
        with patch.object(
                ScoringService, "start_sweeper", lambda self, timeout: None):
            self.service = ScoringService(shard=0)
        # ScoringService.__init__ auto-connects (auto_retry=0.5) to
        # LogService and (if rankings are configured) ProxyService;
        # disconnect both the same way Checker_test.py/
        # ResourceService_test.py do for the service under test.
        self.addCleanup(self.service._disconnect_all)

        # Wire a real, connected peer standing in for ProxyService, and
        # point both the service and its executor at it (they each
        # cached the disconnected proxy returned by connect_to() at
        # construction time).
        self.proxy_service_stub = RecordingProxyService()
        self.proxy_client = await self._add_connected_service(
            ServiceCoord("ProxyService", 0), self.proxy_service_stub)
        self.service.proxy_service = self.proxy_client
        self.service.get_executor().proxy_service = self.proxy_client

    async def asyncTearDown(self):
        for client in self._clients:
            client.disconnect()
        for server in self._servers:
            server.close()
            await server.wait_closed()
        # What a test commits outlives it (DatabaseMixin only rolls
        # back): start each test from an empty DB.
        self.session.rollback()
        self.delete_data()

    async def _add_connected_service(
        self, coord: ServiceCoord, local_service: object
    ) -> AsyncRemoteServiceClient:
        """Wire a real, connected peer for coord into self.service.

        coord: the coord to register the peer under.
        local_service: object exposing the RPC methods to serve.

        return: the connected client, already stored in
            self.service.remote_services[coord].

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

    def _build_submission_result(
        self, compilation_outcome: bool = False, active_dataset: bool = True,
    ):
        """Build a minimal scoreable submission/dataset/result trio.

        compilation_outcome: outcome to give the submission result's
            compilation (False, i.e. "failed", makes it need scoring
            without requiring any evaluation to be set up).
        active_dataset: whether to make the dataset the task's active
            one, so scoring it triggers a ProxyService notification.

        return: the created submission, dataset, and submission result.

        """
        contest = self.add_contest()
        task = self.add_task(contest=contest, score_precision=0)
        dataset = self.add_dataset(
            task=task, score_type="Sum", score_type_parameters=1)
        participation = self.add_participation(contest=contest)
        submission = self.add_submission(
            task=task, participation=participation)
        submission_result = self.add_submission_result(
            submission=submission, dataset=dataset)
        submission_result.set_compilation_outcome(compilation_outcome)
        if active_dataset:
            task.active_dataset = dataset
        self.session.commit()
        return submission, dataset, submission_result

    async def test_execute_scores_submission_and_notifies_proxy(self):
        submission, dataset, submission_result = \
            self._build_submission_result(
                compilation_outcome=False, active_dataset=True)

        executor = self.service.get_executor()
        executor.enqueue(ScoringOperation(submission.id, dataset.id))
        entry = await executor._pop()

        await executor.execute(entry)

        self.session.expire_all()
        self.assertTrue(submission_result.scored())
        self.assertIsNotNone(submission_result.score)

        # submission_scored is fire-and-forget (_spawn), so give the
        # spawned task a chance to reach the peer before asserting.
        await asyncio.sleep(0.1)
        self.assertEqual(
            self.proxy_service_stub.submission_scored_calls, [submission.id])

    async def test_execute_on_inactive_dataset_does_not_notify_proxy(self):
        submission, dataset, submission_result = \
            self._build_submission_result(
                compilation_outcome=False, active_dataset=False)

        executor = self.service.get_executor()
        executor.enqueue(ScoringOperation(submission.id, dataset.id))
        entry = await executor._pop()

        await executor.execute(entry)

        self.session.expire_all()
        self.assertTrue(submission_result.scored())

        await asyncio.sleep(0.1)
        self.assertEqual(self.proxy_service_stub.submission_scored_calls, [])

    async def test_execute_on_already_scored_result_is_a_noop(self):
        submission, dataset, submission_result = \
            self._build_submission_result(
                compilation_outcome=False, active_dataset=True)
        submission_result.set_evaluation_outcome()
        submission_result.score = 0.0
        submission_result.score_details = []
        submission_result.public_score = 0.0
        submission_result.public_score_details = []
        submission_result.ranking_score_details = []
        submission_result.scored_at = make_datetime()
        self.session.commit()
        original_score = submission_result.score
        original_score_details = submission_result.score_details
        original_scored_at = submission_result.scored_at

        executor = self.service.get_executor()
        executor.enqueue(ScoringOperation(submission.id, dataset.id))
        entry = await executor._pop()

        await executor.execute(entry)

        self.session.expire_all()
        # The "noop" claim means neither the score fields nor the
        # notification happened, not just the latter.
        self.assertEqual(submission_result.score, original_score)
        self.assertEqual(
            submission_result.score_details, original_score_details)
        self.assertEqual(submission_result.scored_at, original_scored_at)

        await asyncio.sleep(0.1)
        self.assertEqual(self.proxy_service_stub.submission_scored_calls, [])

    async def test_execute_on_missing_submission_raises(self):
        executor = self.service.get_executor()
        executor.enqueue(ScoringOperation(123456789, 987654321))
        entry = await executor._pop()

        with self.assertRaises(ValueError):
            await executor.execute(entry)

    async def test_execute_on_missing_dataset_raises(self):
        submission, _dataset, _submission_result = \
            self._build_submission_result(
                compilation_outcome=False, active_dataset=True)

        executor = self.service.get_executor()
        executor.enqueue(ScoringOperation(submission.id, 987654321))
        entry = await executor._pop()

        with self.assertRaises(ValueError):
            await executor.execute(entry)

    async def test_execute_on_result_not_ready_for_scoring_raises(self):
        # Compiled successfully but not yet evaluated: neither
        # compilation_failed() nor evaluated() holds, so needs_scoring()
        # is False and scored() is also False -- the third ValueError
        # branch in _execute_sync.
        submission, dataset, _submission_result = \
            self._build_submission_result(
                compilation_outcome=True, active_dataset=True)

        executor = self.service.get_executor()
        executor.enqueue(ScoringOperation(submission.id, dataset.id))
        entry = await executor._pop()

        with self.assertRaises(ValueError):
            await executor.execute(entry)

    async def test_execute_notification_failure_is_ignored(self):
        # Reproduces the "Task exception was never retrieved" regression:
        # the fire-and-forget ProxyService notification must not leave
        # an unretrieved exception on the task _spawn creates when the
        # RPC fails (here, because the peer is disconnected).
        submission, dataset, _submission_result = \
            self._build_submission_result(
                compilation_outcome=False, active_dataset=True)

        loop = asyncio.get_running_loop()
        unhandled_exceptions = []
        original_handler = loop.get_exception_handler()
        loop.set_exception_handler(
            lambda _loop, context: unhandled_exceptions.append(context))
        self.addCleanup(loop.set_exception_handler, original_handler)

        # Disconnect the peer before execute() gets a chance to notify
        # it, so the notification RPC fails with RPCError.
        self.proxy_client.disconnect()

        executor = self.service.get_executor()
        executor.enqueue(ScoringOperation(submission.id, dataset.id))
        entry = await executor._pop()

        await executor.execute(entry)

        # Give the spawned notification task a chance to run (and fail)
        # before asserting nothing was left unhandled.
        for _ in range(5):
            await asyncio.sleep(0)

        self.assertEqual(unhandled_exceptions, [])

    async def test_proxy_notification_the_peer_never_answers_is_dropped(
        self
    ):
        # A ProxyService that is connected but stuck: without a bound
        # every notification would stay pending, together with its
        # request, for good. Its sweeper sends the score anyway.
        peer = StuckPeer()
        await peer.start()
        self.addAsyncCleanup(peer.stop)
        client = await connect_client(ServiceCoord("ProxyService", 0), peer)
        self.addCleanup(client.disconnect)
        executor = self.service.get_executor()
        executor.proxy_service = client

        submission, dataset, _submission_result = \
            self._build_submission_result(
                compilation_outcome=False, active_dataset=True)
        executor.enqueue(ScoringOperation(submission.id, dataset.id))
        entry = await executor._pop()

        # The service's own executor may score what earlier tests left
        # behind meanwhile, and notify the same peer: look for ours.
        ours = "submission %d was scored" % submission.id
        with patch.object(
                ScoringServiceModule, "FIRE_AND_FORGET_TIMEOUT", 0.05):
            with self.assertLogs(
                    "cms.service.ScoringService", level="WARNING") as logs:
                await executor.execute(entry)
                for _ in range(200):
                    if any(ours in line for line in logs.output) \
                            and not client.pending_outgoing_requests:
                        break
                    await asyncio.sleep(0.01)

        self.assertIn({"__method": "submission_scored",
                       "__data": {"submission_id": submission.id}},
                      [{key: request[key] for key in ("__method", "__data")}
                       for request in peer.requests])
        self.assertEqual(
            len([line for line in logs.output if ours in line]), 1)
        self.assertEqual(client.pending_outgoing_requests, {})
        self.assertEqual(client.pending_outgoing_requests_results, {})

    async def test_missing_operations_finds_and_enqueues_unscored_result(self):
        submission, dataset, _ = self._build_submission_result(
            compilation_outcome=False, active_dataset=True)

        count = await self.service._missing_operations()

        self.assertEqual(count, 1)
        operation = ScoringOperation(submission.id, dataset.id)
        self.assertIn(operation, self.service.get_executor())
        # Dequeue before the test ends: this test isn't exercising the
        # executor's own dispatch loop, and leaving the operation queued
        # would let the background executor.run() loop pick it up and
        # execute it during asyncTearDown, racing DatabaseMixin's teardown.
        self.service.dequeue(operation)

    async def test_invalidate_submission_invalidates_and_reenqueues(self):
        submission, dataset, submission_result = \
            self._build_submission_result(
                compilation_outcome=False, active_dataset=True)
        submission_result.set_evaluation_outcome()
        submission_result.score = 42.0
        submission_result.score_details = []
        submission_result.public_score = 42.0
        submission_result.public_score_details = []
        submission_result.ranking_score_details = []
        self.session.commit()

        await self.service.invalidate_submission(submission_id=submission.id)

        self.session.expire_all()
        self.assertFalse(submission_result.scored())
        self.assertIsNone(submission_result.score)

        operation = ScoringOperation(submission.id, dataset.id)
        self.assertIn(operation, self.service.get_executor())
        # Same reasoning as test_missing_operations_finds_and_enqueues_
        # unscored_result: dequeue so the background executor.run() loop
        # has nothing left to race asyncTearDown with.
        self.service.dequeue(operation)

    async def test_invalidate_submission_over_real_rpc_round_trip(self):
        # Drives invalidate_submission through a real RPC request/reply,
        # not a direct coroutine call: this is what exercises
        # AsyncRemoteServiceServer.process_incoming_request's
        # asyncio.iscoroutine(result) branch for an @rpc_method with
        # real (async) work to do.
        submission, dataset, submission_result = \
            self._build_submission_result(
                compilation_outcome=False, active_dataset=True)
        submission_result.set_evaluation_outcome()
        submission_result.score = 42.0
        submission_result.score_details = []
        submission_result.public_score = 42.0
        submission_result.public_score_details = []
        submission_result.ranking_score_details = []
        self.session.commit()

        server, port = await _start_server(self.service)
        self._servers.append(server)
        with patch("cms.io.async_rpc.get_service_address",
                   return_value=Address("127.0.0.1", 0)):
            client = AsyncRemoteServiceClient(ServiceCoord("Caller", 0))
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        client.initialize_streams(reader, writer, plus=None)
        asyncio.create_task(client.run())
        self._clients.append(client)

        await client.execute_rpc(
            "invalidate_submission", {"submission_id": submission.id})

        # invalidate_submission both invalidates the old score and
        # re-enqueues the result; the background executor.run() loop may
        # win the race and re-score it before we get to check, so assert
        # on the end-to-end outcome (re-scored) rather than on a
        # queue-membership snapshot that would be inherently racy here.
        for _ in range(50):
            self.session.expire_all()
            if submission_result.scored():
                break
            await asyncio.sleep(0.05)
        self.assertTrue(submission_result.scored())
        self.assertIsNotNone(submission_result.score)


class ScoringVsInvalidationRaceTest(DatabaseMixin, unittest.TestCase):
    """Scoring a result while EvaluationService invalidates it."""

    def tearDown(self):
        # The test commits from other sessions: leave an empty DB.
        self.session.rollback()
        self.delete_data()
        super().tearDown()

    def _wait_until_blocked_by(self, blocker_pid: int, timeout: float = 10.0):
        """Wait until some backend is waiting on a lock of blocker_pid.

        blocker_pid: the PostgreSQL backend pid holding the lock.
        timeout: seconds to wait before failing the test.

        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with Session() as probe:
                blocked = probe.execute(
                    text("SELECT count(*) FROM pg_stat_activity "
                         "WHERE :pid = ANY(pg_blocking_pids(pid))"),
                    {"pid": blocker_pid}).scalar_one()
            if blocked > 0:
                return
            time.sleep(0.02)
        self.fail("ScoringService never blocked on the invalidation.")

    def test_score_is_not_written_onto_an_invalidated_result(self):
        # EvaluationService's invalidate_evaluation NULLs the outcome
        # and flushes, but commits only later; ScoringService must not
        # write the score computed from the old evaluations on top of
        # the invalidated row once that commit lands.
        contest = self.add_contest()
        task = self.add_task(contest=contest, score_precision=0)
        dataset = self.add_dataset(
            task=task, score_type="Sum", score_type_parameters=1)
        task.active_dataset = dataset
        testcase = self.add_testcase(dataset=dataset, public=True)
        participation = self.add_participation(contest=contest)
        submission = self.add_submission(
            task=task, participation=participation)
        submission_result = self.add_submission_result(
            submission=submission, dataset=dataset)
        submission_result.set_compilation_outcome(True)
        self.add_evaluation(submission_result=submission_result,
                            testcase=testcase, outcome="1.0")
        submission_result.set_evaluation_outcome()
        self.session.commit()
        result_id = (submission.id, dataset.id)
        self.assertTrue(submission_result.needs_scoring())

        # EvaluationService's side: invalidate and flush, not commit.
        es_session = Session()
        self.addCleanup(es_session.close)
        es_pid = es_session.execute(
            text("SELECT pg_backend_pid()")).scalar_one()
        SubmissionResult.get_from_id(result_id, es_session) \
            .invalidate_evaluation()
        es_session.flush()

        errors: list[Exception] = []

        def score():
            try:
                ScoringExecutor(proxy_service=None)._execute_sync(
                    ScoringOperation(submission.id, dataset.id))
            except Exception as error:
                errors.append(error)

        scorer = threading.Thread(target=score, daemon=True)
        scorer.start()
        self._wait_until_blocked_by(es_pid)
        es_session.commit()
        scorer.join(10)
        self.assertFalse(scorer.is_alive())

        # The scorer saw the invalidated result: nothing to score yet.
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.session.expire_all()
        self.assertIsNone(submission_result.evaluation_outcome)
        self.assertFalse(submission_result.scored())
        self.assertIsNone(submission_result.score)

        # Once re-evaluated, the result needs scoring again.
        submission_result.set_evaluation_outcome()
        self.assertTrue(submission_result.needs_scoring())


if __name__ == "__main__":
    unittest.main()
