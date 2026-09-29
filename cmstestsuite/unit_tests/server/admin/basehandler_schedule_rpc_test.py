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

"""Regression test for BaseHandler.schedule_rpc().

AWS handler bodies run in an executor thread, and the remote-service
client methods are coroutines. schedule_rpc() must actually run the
coroutine on the event loop, without blocking the worker thread and
without failing the request if the RPC raises.

"""

import asyncio
import logging
import unittest
from unittest.mock import patch

import tornado.web
from tornado.httpclient import AsyncHTTPClient
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_sockets

from cms import ServiceCoord
from cms.conf import Address
from cms.io.async_rpc import AsyncFakeRemoteServiceClient, \
    AsyncRemoteServiceClient
from cms.server.admin.handlers.base import BaseHandler
from cmstestsuite.unit_tests.stuckpeer import StuckPeer, connect_client


class _RpcHandler(BaseHandler):

    async def prepare(self):
        # Skip the DB-backed authentication of the real prepare(), but
        # capture the loop the way it does.
        self._loop = asyncio.get_running_loop()

    async def get(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync)

    def _get_sync(self):
        if self.get_argument("real", None) is not None:
            self.schedule_rpc(self.application.real_client.reinitialize)
        elif self.get_argument("unconfigured", None) is not None:
            self.schedule_rpc(
                self.application.unconfigured_client.reinitialize)
        else:
            self.schedule_rpc(self.application.fake_rpc, x=1)
        self.write("ok")


class TestBaseHandlerScheduleRpc(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.called = asyncio.Event()
        self.received = None
        self.failure = None

        async def fake_rpc(**kwargs):
            self.received = kwargs
            self.called.set()
            if self.failure is not None:
                raise self.failure

        app = tornado.web.Application([(r"/", _RpcHandler)])
        app.fake_rpc = fake_rpc
        # A real client that never connects: its calls fail with a plain
        # RPCError. The other one stands for a service that is not
        # configured (e.g. ProxyService without rankings).
        app.real_client = self._unconnected_client("ProxyService")
        app.unconfigured_client = AsyncFakeRemoteServiceClient(
            ServiceCoord("ProxyService", 0))
        self.application = app
        sockets = bind_sockets(0, "127.0.0.1")
        self.port = sockets[0].getsockname()[1]
        self.server = HTTPServer(app)
        self.server.add_sockets(sockets)
        self.client = AsyncHTTPClient()

    @staticmethod
    def _unconnected_client(service_name):
        with patch("cms.io.async_rpc.get_service_address",
                   return_value=Address("127.0.0.1", 0)):
            return AsyncRemoteServiceClient(ServiceCoord(service_name, 0))

    async def asyncTearDown(self):
        self.client.close()
        self.server.stop()
        await self.server.close_all_connections()

    async def _fetch(self, query=""):
        return await self.client.fetch(
            "http://127.0.0.1:%d/%s" % (self.port, query),
            raise_error=False)

    async def test_rpc_is_actually_run(self):
        response = await self._fetch()
        self.assertEqual(response.code, 200)
        await asyncio.wait_for(self.called.wait(), timeout=5)
        self.assertEqual(self.received, {"x": 1})

    async def test_rpc_exception_is_logged_and_request_succeeds(self):
        self.failure = RuntimeError("boom")
        with self.assertLogs(
                "cms.server.util", level="WARNING") as logs:
            response = await self._fetch()
            await asyncio.wait_for(self.called.wait(), timeout=5)
            # Let the done callback run.
            for _ in range(50):
                if logs.records:
                    break
                await asyncio.sleep(0.02)
        self.assertEqual(response.code, 200)
        self.assertIn("fake_rpc", "\n".join(logs.output))
        self.assertIn("boom", "\n".join(logs.output))
        # An unexpected exception type keeps its traceback.
        self.assertIsNotNone(logs.records[0].exc_info)

    async def test_real_client_failure_names_service_and_method(self):
        with self.assertLogs(
                "cms.server.util", level="WARNING") as logs:
            response = await self._fetch("?real=1")
            for _ in range(250):
                if logs.records:
                    break
                await asyncio.sleep(0.02)
        self.assertEqual(response.code, 200)
        message = "\n".join(logs.output)
        self.assertIn("reinitialize", message)
        self.assertIn("ProxyService", message)
        # An RPCError is logged without a traceback.
        self.assertIsNone(logs.records[0].exc_info)

    async def test_rpc_error_of_a_configured_service_is_a_warning(self):
        # Only the calls on a service that is not configured are let
        # off: a service that is configured but unreachable, such as
        # EvaluationService, is worth a warning.
        self.application.real_client = self._unconnected_client(
            "EvaluationService")
        with self.assertLogs(
                "cms.server.util", level="WARNING") as logs:
            response = await self._fetch("?real=1")
            for _ in range(250):
                if logs.records:
                    break
                await asyncio.sleep(0.02)
        self.assertEqual(response.code, 200)
        self.assertEqual([record.levelno for record in logs.records],
                         [logging.WARNING])
        self.assertIn("EvaluationService", logs.output[0])

    async def test_rpc_to_a_service_not_configured_is_only_debug(self):
        with self.assertLogs(
                "cms.server.util", level="DEBUG") as logs:
            response = await self._fetch("?unconfigured=1")
            for _ in range(250):
                if logs.records:
                    break
                await asyncio.sleep(0.02)
            # Time for anything else to be logged.
            await asyncio.sleep(0.1)
        self.assertEqual(response.code, 200)
        self.assertEqual([record.levelno for record in logs.records],
                         [logging.DEBUG])
        message = logs.output[0]
        self.assertIn("reinitialize", message)
        self.assertIn("ProxyService", message)

    async def test_rpc_the_peer_never_answers_is_dropped(self):
        # A peer that is connected but stuck: without a bound the call
        # would stay pending, together with its request, for good.
        peer = StuckPeer()
        await peer.start()
        self.addAsyncCleanup(peer.stop)
        client = await connect_client(ServiceCoord("ProxyService", 0), peer)
        self.addCleanup(client.disconnect)
        self.application.real_client = client

        with patch("cms.io.async_rpc.FIRE_AND_FORGET_TIMEOUT", 0.05):
            with self.assertLogs(
                    "cms.server.util", level="WARNING") as logs:
                response = await self._fetch("?real=1")
                await peer.wait_for_requests(1)
                for _ in range(100):
                    if logs.records:
                        break
                    await asyncio.sleep(0.02)

        self.assertEqual(response.code, 200)
        message = "\n".join(logs.output)
        self.assertIn("reinitialize", message)
        self.assertIn("ProxyService", message)
        self.assertEqual(client.pending_outgoing_requests, {})
        self.assertEqual(client.pending_outgoing_requests_results, {})


if __name__ == "__main__":
    unittest.main()
