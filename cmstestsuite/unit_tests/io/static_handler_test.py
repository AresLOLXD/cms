#!/usr/bin/env python3

"""Tests for cms.io.static_handler.MultiLocationStaticFileHandler."""

import asyncio
import os
import tempfile
import unittest

import tornado.httpserver
import tornado.netutil
import tornado.web

from cms.io.static_handler import MultiLocationStaticFileHandler


class MultiLocationStaticFileHandlerTest(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.dir_a = tempfile.TemporaryDirectory()
        self.dir_b = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup_dirs)
        with open(os.path.join(self.dir_a.name, "shared.txt"), "w") as f:
            f.write("from a")
        with open(os.path.join(self.dir_a.name, "only_in_a.txt"), "w") as f:
            f.write("only a")
        with open(os.path.join(self.dir_b.name, "shared.txt"), "w") as f:
            f.write("from b")

        # Locations are given in "earlier is overridden by later" order,
        # matching the existing SharedDataMiddleware convention.
        handler_spec = MultiLocationStaticFileHandler.make_route(
            r"/static/(.*)", [self.dir_a.name, self.dir_b.name])
        application = tornado.web.Application([handler_spec])
        self.server = tornado.httpserver.HTTPServer(application)
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        self.server.add_sockets(sockets)
        self.port = sockets[0].getsockname()[1]
        self.addAsyncCleanup(self._stop_server)

    async def _cleanup_dirs(self):
        self.dir_a.cleanup()
        self.dir_b.cleanup()

    async def _stop_server(self):
        self.server.stop()
        await self.server.close_all_connections()

    async def _get(self, path):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            writer.write(
                ("GET %s HTTP/1.1\r\nHost: localhost\r\n\r\n" % path)
                .encode())
            await writer.drain()
            return await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()

    async def test_later_location_overrides_earlier_for_same_path(self):
        response = await self._get("/static/shared.txt")
        self.assertIn(b"200 OK", response)
        self.assertIn(b"from b", response)

    async def test_falls_back_to_earlier_location_when_not_in_later(self):
        response = await self._get("/static/only_in_a.txt")
        self.assertIn(b"200 OK", response)
        self.assertIn(b"only a", response)

    async def test_404_when_in_no_location(self):
        response = await self._get("/static/nowhere.txt")
        self.assertIn(b"404", response)


if __name__ == "__main__":
    unittest.main()
