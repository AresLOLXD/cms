#!/usr/bin/env python3

"""Tests for cms.service.ProxyService."""

import asyncio
import json
import unittest
from datetime import datetime
from urllib.parse import urljoin
from unittest.mock import AsyncMock, patch

import requests.exceptions

from cms.conf import Address, ServiceCoord
from cms.io.async_rpc import AsyncRemoteServiceClient, AsyncRemoteServiceServer
from cms.io.priorityqueue import QueueEntry
from cms.service.ProxyService import \
    ProxyExecutor, ProxyOperation, ProxyService
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


RANKING = "http://rws:secret@localhost:8890/"


def _entries(*operations: ProxyOperation) -> list[QueueEntry[ProxyOperation]]:
    """Wrap operations into QueueEntry objects, as the executor expects.

    operations: the operations to wrap, in arrival order.

    return: the corresponding queue entries.

    """
    return [QueueEntry(op, 0, datetime.now(), i)
            for i, op in enumerate(operations)]


class ProxyExecutorTest(unittest.IsolatedAsyncioTestCase):
    """Tests for ProxyExecutor's per-group batching, resets, and retries."""

    async def asyncSetUp(self):
        put_patcher = patch("cms.service.ProxyService.requests.put")
        self.requests_put = put_patcher.start()
        self.addCleanup(put_patcher.stop)
        self.requests_put.return_value.status_code = 200

        delete_patcher = patch("cms.service.ProxyService.requests.delete")
        self.requests_delete = delete_patcher.start()
        self.addCleanup(delete_patcher.stop)
        self.requests_delete.return_value.status_code = 204

        self.executor = ProxyExecutor(RANKING)

    def _put_bodies(self) -> dict[str, dict]:
        """Return the JSON bodies sent to requests.put, keyed by URL."""
        bodies: dict[str, dict] = {}
        for c in self.requests_put.call_args_list:
            bodies.setdefault(c.args[0], {}).update(json.loads(c.args[1]))
        return bodies

    def _delete_urls(self) -> list[str]:
        return [c.args[0] for c in self.requests_delete.call_args_list]

    def test_execute_sync_combines_batch_into_one_put_per_type(self):
        entries = _entries(
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u1": {}}, "olim"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u2": {}}, "omips"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u3": {}}, "olim"))

        failed = self.executor._execute_sync(entries)

        self.assertFalse(failed)
        bodies = self._put_bodies()
        self.assertEqual(
            bodies[urljoin(RANKING, "olim/users/")], {"u1": {}, "u3": {}})
        self.assertEqual(
            bodies[urljoin(RANKING, "omips/users/")], {"u2": {}})

    def test_execute_sync_reset_drops_earlier_data_for_its_group(self):
        entries = _entries(
            ProxyOperation(ProxyExecutor.USER_TYPE, {"stale": {}}, "olim"),
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim"),
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"))

        failed = self.executor._execute_sync(entries)

        self.assertFalse(failed)
        self.assertEqual(
            self._delete_urls(),
            [urljoin(RANKING, "olim/contests/"),
             urljoin(RANKING, "olim/users/")])
        bodies = self._put_bodies()
        # The reset dropped the earlier "stale" user payload: nothing
        # left to put for users, only the contest data queued after it.
        self.assertNotIn(urljoin(RANKING, "olim/users/"), bodies)
        self.assertEqual(
            bodies[urljoin(RANKING, "olim/contests/")], {"c": {}})

    async def test_execute_recovers_from_send_failure_after_sleeping(self):
        self.requests_put.side_effect = \
            requests.exceptions.RequestException("boom")
        entries = _entries(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}))

        with patch("cms.service.ProxyService.asyncio.sleep",
                   new_callable=AsyncMock) as sleep_mock:
            await self.executor.execute(entries)

        sleep_mock.assert_awaited_once_with(ProxyExecutor.FAILURE_WAIT)


class ProxyServiceTest(
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

        # ProxyService.__init__ (via AsyncService.__init__) connects to
        # LogService, which goes through async_rpc's own imported
        # reference to get_service_address.
        rpc_address_patcher = patch(
            "cms.io.async_rpc.get_service_address",
            return_value=Address("127.0.0.1", 0))
        rpc_address_patcher.start()
        self.addCleanup(rpc_address_patcher.stop)

        put_patcher = patch("cms.service.ProxyService.requests.put")
        self.requests_put = put_patcher.start()
        self.addCleanup(put_patcher.stop)
        self.requests_put.return_value.status_code = 200

        delete_patcher = patch("cms.service.ProxyService.requests.delete")
        self.requests_delete = delete_patcher.start()
        self.addCleanup(delete_patcher.stop)
        self.requests_delete.return_value.status_code = 204

    async def asyncTearDown(self):
        for client in self._clients:
            client.disconnect()
        for server in self._servers:
            server.close()
            await server.wait_closed()

    def _build_service(self, contest_id: int | None = None) -> ProxyService:
        """Build a ProxyService and make its _threadsafe_enqueue safe.

        Unlike in production (where the launcher script builds the
        service before run() starts the event loop), this test
        constructs the service while the test's own event loop is
        already running, so AsyncTriggeredService.add_executor's
        _call_when_running spawns the executor's run() loop as a
        background task immediately. self._loop, though, is only ever
        set by run()/_async_run(), which we never call here -- so it
        would incorrectly stay None (making _threadsafe_enqueue take
        its "no loop yet" direct-call branch) even though a real
        executor.run() task may concurrently be blocked awaiting the
        queue. Set it explicitly, mirroring what _async_run() does, so
        _threadsafe_enqueue takes its call_soon_threadsafe branch, same
        as it would in production once the service is actually running.

        The same "event loop already running" quirk also makes
        start_sweeper's _call_when_running fire the sweeper immediately
        instead of after its usual delay, so a background sweep could
        otherwise race with (and do the work of) the method a test
        means to exercise. No test in this file exercises the
        sweeper's own periodic behaviour, so it's stubbed out here
        unconditionally.

        contest_id: the contest id to pass to ProxyService (legacy
            mode), or None for group mode.

        return: the constructed service.

        """
        with patch.object(
                ProxyService, "start_sweeper", lambda self, timeout: None):
            service = ProxyService(shard=0, contest_id=contest_id)
        service._loop = asyncio.get_running_loop()
        self.addCleanup(service._disconnect_all)
        return service

    async def _wait_until_put(
        self, path_substring: str, timeout: float = 2
    ) -> None:
        """Poll until a PUT with the given URL fragment was recorded.

        Used right after building a service (whose __init__ always
        enqueues an initial initialize() batch) to let that initial
        batch actually land before resetting the mocks, so later
        assertions only observe what the method under test itself did.
        Like _wait_until, gives up silently on timeout and lets the
        caller's own assertions report the failure.

        path_substring: a fragment expected in one of the PUT URLs.
        timeout: how many seconds to poll for before giving up.

        """
        attempts = max(1, int(timeout / 0.05))
        await self._wait_until(
            lambda: any(path_substring in u for u in self._put_urls()),
            attempts=attempts)

    def _build_scored_submission(self, contest=None):
        """Build a contest (or reuse one) with one already-scored submission.

        contest: the contest to add the submission to, or None to
            create a new one.

        return: the contest, task, dataset, submission and submission
            result created.

        """
        contest = contest if contest is not None else self.add_contest()
        task = self.add_task(contest=contest, score_precision=0)
        dataset = self.add_dataset(
            task=task, score_type="Sum", score_type_parameters=1)
        task.active_dataset = dataset
        participation = self.add_participation(contest=contest)
        submission = self.add_submission(
            task=task, participation=participation)
        result = self.add_submission_result(
            submission=submission, dataset=dataset)
        result.compilation_outcome = "ok"
        result.evaluation_outcome = "ok"
        result.score = 100
        result.score_details = []
        result.public_score = 100
        result.public_score_details = []
        result.ranking_score_details = ["100"]
        self.session.commit()
        return contest, task, dataset, submission, result

    async def _start_server(self, local_service: object) -> int:
        """Start a loopback server exposing local_service's RPC methods.

        local_service: object exposing the RPC methods to serve.

        return: the port the server listens on.

        """
        async def handle_client(reader, writer):
            server_side = AsyncRemoteServiceServer(
                local_service, Address("127.0.0.1", 0))
            server_side.initialize_streams(reader, writer, plus=None)
            await server_side.run()

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        self._servers.append(server)
        asyncio.create_task(server.serve_forever())
        return server.sockets[0].getsockname()[1]

    async def _rpc_client(self, port: int) -> AsyncRemoteServiceClient:
        """Connect a loopback RPC client to the given port.

        port: the port to connect to.

        return: the connected client.

        """
        with patch("cms.io.async_rpc.get_service_address",
                   return_value=Address("127.0.0.1", 0)):
            client = AsyncRemoteServiceClient(ServiceCoord("Caller", 0))
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        client.initialize_streams(reader, writer, plus=None)
        asyncio.create_task(client.run())
        self._clients.append(client)
        return client

    def _put_urls(self) -> list[str]:
        return [c.args[0] for c in self.requests_put.call_args_list]

    async def _wait_until(self, predicate, attempts: int = 50):
        """Poll predicate() until it is true, or give up.

        The background executor.run() task (spawned by
        ProxyService.__init__'s add_executor, since a running loop
        already exists when the service is constructed in a test)
        processes enqueued operations asynchronously, so assertions on
        their effects (the mocked HTTP calls) need to wait for it.

        predicate: a zero-argument callable to poll.
        attempts: how many times to poll, sleeping 0.05s between tries.

        """
        for _ in range(attempts):
            if predicate():
                return
            await asyncio.sleep(0.05)

    async def test_missing_operations_enqueues_scored_submission(self):
        contest, task, dataset, submission, result = \
            self._build_scored_submission()
        service = self._build_service(contest_id=contest.id)
        await self._wait_until_put("contests/")
        self.requests_put.reset_mock()

        count = await asyncio.wait_for(
            service._missing_operations(), timeout=5)

        # operations_for_score() returns 2 operations (submission and
        # subchange); the submission has no token.
        self.assertEqual(count, 2)
        await self._wait_until(
            lambda: any(u.endswith("submissions/")
                       for u in self._put_urls()))
        self.assertTrue(
            any(u.endswith("submissions/") for u in self._put_urls()))
        self.assertTrue(
            any(u.endswith("subchanges/") for u in self._put_urls()))

    async def test_submission_scored_over_rpc_round_trip_for_sent_contest(
        self,
    ):
        # Drives submission_scored through a real RPC request/reply, not
        # a direct coroutine call: this exercises
        # AsyncRemoteServiceServer.process_incoming_request's
        # asyncio.iscoroutine(result) branch for an @rpc_method with
        # real (async) work to do.
        contest, task, dataset, submission, result = \
            self._build_scored_submission()
        service = self._build_service(contest_id=contest.id)
        await self._wait_until_put("contests/")
        self.requests_put.reset_mock()

        port = await self._start_server(service)
        client = await self._rpc_client(port)
        await asyncio.wait_for(
            client.execute_rpc(
                "submission_scored", {"submission_id": submission.id}),
            timeout=5)

        await self._wait_until(
            lambda: any(u.endswith("submissions/")
                       for u in self._put_urls()))
        self.assertTrue(
            any(u.endswith("submissions/") for u in self._put_urls()))

    async def test_submission_scored_for_unsent_contest_does_nothing(self):
        contest, task, dataset, submission, result = \
            self._build_scored_submission()
        # Group mode, and the contest has no ranking group: not sent.
        service = self._build_service(contest_id=None)
        self.requests_put.reset_mock()

        await service.submission_scored(submission.id)
        await asyncio.sleep(0.1)

        self.requests_put.assert_not_called()

    async def test_submission_scored_missing_submission_raises(self):
        service = self._build_service(contest_id=None)

        with self.assertRaises(KeyError):
            await service.submission_scored(123456789)

    async def test_submission_tokened_enqueues_for_sent_contest(self):
        contest, task, dataset, submission, result = \
            self._build_scored_submission()
        self.add_token(submission=submission)
        self.session.commit()
        service = self._build_service(contest_id=contest.id)
        await self._wait_until_put("contests/")
        self.requests_put.reset_mock()

        await asyncio.wait_for(
            service.submission_tokened(submission.id), timeout=5)

        await self._wait_until(
            lambda: any(u.endswith("subchanges/")
                       for u in self._put_urls()))
        self.assertTrue(
            any(u.endswith("submissions/") for u in self._put_urls()))
        self.assertTrue(
            any(u.endswith("subchanges/") for u in self._put_urls()))

    async def test_submission_tokened_for_unsent_contest_does_nothing(self):
        contest, task, dataset, submission, result = \
            self._build_scored_submission()
        self.add_token(submission=submission)
        self.session.commit()
        service = self._build_service(contest_id=None)
        self.requests_put.reset_mock()

        await service.submission_tokened(submission.id)
        await asyncio.sleep(0.1)

        self.requests_put.assert_not_called()

    async def test_submission_tokened_missing_submission_raises(self):
        service = self._build_service(contest_id=None)

        with self.assertRaises(KeyError):
            await service.submission_tokened(123456789)

    async def test_regenerate_ranking_invalid_group_raises_before_db_work(
        self,
    ):
        # regenerate_ranking's validation must run in the async def
        # wrapper, before asyncio.get_running_loop().run_in_executor(...)
        # is ever called (i.e. before any work is handed off to
        # _regenerate_ranking_sync). Patching run_in_executor directly
        # (rather than just asserting SessionGen was never called)
        # proves that, since the validation could otherwise be moved
        # inside _regenerate_ranking_sync and still pass a SessionGen-only
        # check (the raise would still happen before SessionGen either
        # way).
        service = self._build_service(contest_id=None)
        loop = asyncio.get_running_loop()

        with patch.object(loop, "run_in_executor") as run_in_executor:
            with self.assertRaises(ValueError):
                await service.regenerate_ranking("..")
            run_in_executor.assert_not_called()

    async def test_dataset_updated_reinitializes_and_resends_submissions(self):
        contest, task, dataset, submission, result = \
            self._build_scored_submission()
        service = self._build_service(contest_id=contest.id)
        await self._wait_until_put("contests/")
        self.requests_put.reset_mock()

        # This is the nested reinitialize()/initialize() call chain,
        # now running as dataset_updated -> _dataset_updated_sync ->
        # _reinitialize_sync -> initialize -> _enqueue_contest_data,
        # all inside a single run_in_executor thread: assert it
        # completes (without deadlocking or raising) and produces the
        # expected re-sends.
        await asyncio.wait_for(service.dataset_updated(task.id), timeout=5)

        await self._wait_until(
            lambda: any(u.endswith("submissions/")
                       for u in self._put_urls()))
        self.assertTrue(
            any(u.endswith("contests/") for u in self._put_urls()))
        self.assertTrue(
            any(u.endswith("submissions/") for u in self._put_urls()))

    async def test_threadsafe_enqueue_from_worker_thread_reaches_executor(
        self,
    ):
        # ProxyService.__init__'s add_executor already spawned
        # executor.run() as a background task on this test's event loop
        # (a loop is already running when the service is constructed
        # here, unlike in production): give it a tick to reach its
        # blocking `await self._pop(wait=True)`.
        service = self._build_service(contest_id=None)
        self.requests_delete.reset_mock()
        await asyncio.sleep(0)

        operation = ProxyOperation(ProxyExecutor.RESET_TYPE, {}, None)
        loop = asyncio.get_running_loop()
        # Actually go through run_in_executor, so this exercises the
        # cross-OS-thread path, not just a same-thread call.
        await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: service._threadsafe_enqueue(operation)),
            timeout=5)

        await self._wait_until(lambda: self.requests_delete.called)
        self.assertTrue(self.requests_delete.called)


if __name__ == "__main__":
    unittest.main()
