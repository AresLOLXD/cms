#!/usr/bin/env python3

"""Tests for cms.io.static_handler.MultiLocationStaticFileHandler."""

import asyncio
import os
import re
import tempfile
import unittest

import tornado.httpserver
import tornado.netutil
import tornado.web

from cms.io.static_handler import MultiLocationStaticFileHandler


# Pinned here on purpose, rather than imported from the module under
# test: the contract is "a year", whatever the implementation calls it.
ONE_YEAR_IN_SECONDS = 365 * 24 * 60 * 60


def max_age(headers):
    """Return the max-age of a response's Cache-Control, 0 if it has none."""
    match = re.search(r"max-age=(\d+)", headers.get("cache-control", ""))
    return int(match.group(1)) if match else 0


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

    async def _get_head(self, path):
        """Return the status line and the lowercased headers of a GET."""
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            writer.write(
                ("GET %s HTTP/1.1\r\nHost: localhost\r\n\r\n" % path)
                .encode())
            await writer.drain()
            head = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=5)
        finally:
            writer.close()
        status_line, *header_lines = head.decode("latin-1").split("\r\n")
        headers = {}
        for line in header_lines:
            name, _, value = line.partition(":")
            headers[name.lower()] = value.strip()
        return status_line, headers

    async def test_hashed_url_is_cached_for_a_year(self):
        # static_url() (see StaticFileHasher) appends "?h=<content hash>"
        # to every static URL it builds.
        status, headers = await self._get_head(
            "/static/shared.txt?h=0123456789abcdef01234567")
        self.assertIn("200 OK", status)
        self.assertEqual(headers.get("cache-control"), "max-age=31536000")
        self.assertIn("expires", headers)

    async def test_unhashed_url_is_not_cached_for_a_year(self):
        # Without a hash in the URL, a file that changes could never be
        # picked up by browsers that already have it.
        status, headers = await self._get_head("/static/shared.txt")
        self.assertIn("200 OK", status)
        self.assertLess(max_age(headers), ONE_YEAR_IN_SECONDS)

    async def test_versioned_url_is_still_cached_for_a_long_time(self):
        # "?v=" is StaticFileHandler's own convention, and keeps working.
        status, headers = await self._get_head("/static/shared.txt?v=1")
        self.assertIn("200 OK", status)
        self.assertGreaterEqual(max_age(headers), ONE_YEAR_IN_SECONDS)


if __name__ == "__main__":
    unittest.main()
