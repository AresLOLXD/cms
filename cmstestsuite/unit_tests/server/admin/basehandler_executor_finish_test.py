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

"""Regression test for BaseHandler.finish() called from an executor.

AWS handler bodies run via loop.run_in_executor and may call
self.redirect() or self.finish(chunk). Both do socket I/O, which
crashes off the event-loop thread. BaseHandler.finish() must defer
the real finish to Tornado's auto-finish on the loop thread.

"""

import asyncio
import unittest
from unittest.mock import MagicMock

import tornado.web
from tornado.httpclient import AsyncHTTPClient
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_sockets

from cms.server.admin.handlers.base import BaseHandler


class _ExecutorHandler(BaseHandler):

    async def prepare(self):
        # The behavior under test is finish(); skip the DB-backed
        # authentication and service lookups of the real prepare().
        pass

    async def get(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync)


class _RedirectHandler(_ExecutorHandler):
    def _get_sync(self):
        self.redirect("/somewhere")


class _FinishHandler(_ExecutorHandler):
    def _get_sync(self):
        self.finish("payload")


class _NotFoundHandler(_ExecutorHandler):
    def _get_sync(self):
        raise tornado.web.HTTPError(404)

    def render_params(self):
        # Keep the test DB-free: send BaseHandler.write_error down its
        # "can't build render params" fallback.
        raise RuntimeError("no render params in this test")


class TestBaseHandlerExecutorFinish(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        app = tornado.web.Application([
            (r"/redirect", _RedirectHandler),
            (r"/finish", _FinishHandler),
            (r"/notfound", _NotFoundHandler),
        ])
        # As WebService sets it: the handlers need it when created, for
        # the static file hasher.
        app.service = MagicMock()
        sockets = bind_sockets(0, "127.0.0.1")
        self.port = sockets[0].getsockname()[1]
        self.server = HTTPServer(app)
        self.server.add_sockets(sockets)
        self.client = AsyncHTTPClient()

    async def asyncTearDown(self):
        self.client.close()
        self.server.stop()
        await self.server.close_all_connections()

    async def _fetch(self, path):
        return await self.client.fetch(
            "http://127.0.0.1:%d%s" % (self.port, path),
            follow_redirects=False, raise_error=False)

    async def test_redirect_from_executor(self):
        response = await self._fetch("/redirect")
        self.assertEqual(response.code, 302)
        self.assertEqual(response.headers["Location"], "/somewhere")

    async def test_finish_chunk_from_executor(self):
        response = await self._fetch("/finish")
        self.assertEqual(response.code, 200)
        self.assertEqual(response.body, b"payload")

    async def test_http_error_from_executor_goes_through_write_error(self):
        response = await self._fetch("/notfound")
        self.assertEqual(response.code, 404)
        # BaseHandler.write_error's body, not Tornado's default page.
        self.assertEqual(response.body, b"A critical error has occurred :-(")


if __name__ == "__main__":
    unittest.main()
