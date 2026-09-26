#!/usr/bin/env python3

"""Tests confirming Tornado's HTTPServer runs on the same asyncio loop
AsyncService already owns -- the foundational assumption for migrating
WebService onto AsyncService (see the sub-project 2.5a design spec's
Architecture section)."""

import asyncio
import unittest

import tornado.httpserver
import tornado.ioloop
import tornado.web

from cms.io.async_service import AsyncService


class EchoHandler(tornado.web.RequestHandler):
    def get(self):
        self.write("ok")


class TestTornadoSharesAsyncioLoop(unittest.IsolatedAsyncioTestCase):

    async def test_ioloop_current_wraps_the_running_asyncio_loop(self):
        # The core claim: inside a coroutine already running under
        # asyncio (as every AsyncService method does), Tornado's
        # IOLoop.current() must return a wrapper around that exact
        # running loop, not a separate one.
        running_loop = asyncio.get_running_loop()
        tornado_loop = tornado.ioloop.IOLoop.current()
        self.assertIs(tornado_loop.asyncio_loop, running_loop)

    async def test_httpserver_serves_real_requests_without_its_own_thread(self):
        application = tornado.web.Application([(r"/", EchoHandler)])
        server = tornado.httpserver.HTTPServer(application)
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        server.add_sockets(sockets)
        port = sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(1024), timeout=5)
            self.assertIn(b"200 OK", response)
            self.assertIn(b"ok", response)
        finally:
            writer.close()
            server.stop()

    async def test_httpserver_and_asyncservice_coexist_in_one_process(self):
        # An AsyncService instance's own _async_run() sets self._loop;
        # confirm a Tornado HTTPServer constructed and started from
        # within that same running context uses the identical loop,
        # with no second thread involved.
        from unittest.mock import patch
        from cms.conf import Address

        with patch("cms.io.async_service.get_service_address",
                  return_value=Address("127.0.0.1", 0)):
            service = AsyncService(shard=0)
            run_task = asyncio.create_task(service._async_run())
            await asyncio.sleep(0.05)
            self.assertIsNotNone(service._loop)
            self.assertIs(service._loop, asyncio.get_running_loop())

            application = tornado.web.Application([(r"/", EchoHandler)])
            server = tornado.httpserver.HTTPServer(application)
            sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
            server.add_sockets(sockets)
            port = sockets[0].getsockname()[1]
            try:
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", port)
                writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
                await writer.drain()
                response = await asyncio.wait_for(
                    reader.read(1024), timeout=5)
                self.assertIn(b"200 OK", response)
            finally:
                writer.close()
                server.stop()
                service.exit()
                await asyncio.wait_for(run_task, timeout=2)


if __name__ == "__main__":
    unittest.main()
