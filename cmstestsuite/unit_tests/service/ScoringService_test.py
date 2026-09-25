#!/usr/bin/env python3

"""Tests for cms.service.ScoringService."""

import asyncio
import unittest
from unittest.mock import patch

from cms.conf import Address, ServiceCoord
from cms.io.async_rpc import AsyncRemoteServiceClient, AsyncRemoteServiceServer
from cms.io.rpc import rpc_method
from cms.service.ScoringService import ScoringService
from cms.service.scoringoperations import ScoringOperation
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


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
    ServiceLoggingIsolationMixin, DatabaseMixin, unittest.IsolatedAsyncioTestCase
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

        self.service = ScoringService(shard=0)

        # Wire a real, connected peer standing in for ProxyService, and
        # point both the service and its executor at it (they each
        # cached the disconnected proxy returned by connect_to() at
        # construction time).
        self.proxy_service_stub = RecordingProxyService()
        proxy_client = await self._add_connected_service(
            ServiceCoord("ProxyService", 0), self.proxy_service_stub)
        self.service.proxy_service = proxy_client
        self.service.get_executor().proxy_service = proxy_client

    async def asyncTearDown(self):
        for client in self._clients:
            client.disconnect()
        for server in self._servers:
            server.close()
            await server.wait_closed()

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
        submission = self.add_submission(task=task, participation=participation)
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
        self.session.commit()

        executor = self.service.get_executor()
        executor.enqueue(ScoringOperation(submission.id, dataset.id))
        entry = await executor._pop()

        await executor.execute(entry)

        await asyncio.sleep(0.1)
        self.assertEqual(self.proxy_service_stub.submission_scored_calls, [])

    async def test_execute_on_missing_submission_raises(self):
        executor = self.service.get_executor()
        executor.enqueue(ScoringOperation(123456789, 987654321))
        entry = await executor._pop()

        with self.assertRaises(ValueError):
            await executor.execute(entry)

    async def test_missing_operations_finds_and_enqueues_unscored_result(self):
        submission, dataset, _ = self._build_submission_result(
            compilation_outcome=False, active_dataset=True)

        count = await self.service._missing_operations()

        self.assertEqual(count, 1)
        operation = ScoringOperation(submission.id, dataset.id)
        self.assertIn(operation, self.service.get_executor())

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


if __name__ == "__main__":
    unittest.main()
