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
import unittest

import tornado.web
from tornado.httpclient import AsyncHTTPClient
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_sockets

from cms import ServiceCoord
from cms.io.async_rpc import AsyncFakeRemoteServiceClient
from cms.server.admin.handlers.base import BaseHandler


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
        app.real_client = AsyncFakeRemoteServiceClient(
            ServiceCoord("ProxyService", 0))
        sockets = bind_sockets(0, "127.0.0.1")
        self.port = sockets[0].getsockname()[1]
        self.server = HTTPServer(app)
        self.server.add_sockets(sockets)
        self.client = AsyncHTTPClient()

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
                "cms.server.admin.handlers.base", level="WARNING") as logs:
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

    async def test_real_client_failure_names_service_and_method(self):
        with self.assertLogs(
                "cms.server.admin.handlers.base", level="WARNING") as logs:
            response = await self._fetch("?real=1")
            for _ in range(250):
                if logs.records:
                    break
                await asyncio.sleep(0.02)
        self.assertEqual(response.code, 200)
        message = "\n".join(logs.output)
        self.assertIn("reinitialize", message)
        self.assertIn("ProxyService", message)


if __name__ == "__main__":
    unittest.main()
