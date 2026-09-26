#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2018 Luca Wehrstedt <luca.wehrstedt@gmail.com>
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

"""Tests for utility functions for servers.

"""

import unittest

from cms.server.util import Url


class TestUrl(unittest.TestCase):

    def test_top_level(self):
        url = Url("/")
        self.assertEqual(url(), "/")
        self.assertEqual(url("foo"), "/foo")
        self.assertEqual(url("foo", "bar"), "/foo/bar")
        self.assertEqual(url("foo", 42), "/foo/42")

    def test_prefix(self):
        url = Url("/prefix")
        self.assertEqual(url(), "/prefix")
        self.assertEqual(url("foo"), "/prefix/foo")
        self.assertEqual(url("foo", "bar"), "/prefix/foo/bar")
        self.assertEqual(url("foo", 42), "/prefix/foo/42")

    def test_relative_prefix(self):
        url = Url("..")
        self.assertEqual(url(), "..")
        self.assertEqual(url("foo"), "../foo")
        self.assertEqual(url("foo", "bar"), "../foo/bar")
        self.assertEqual(url("foo", 42), "../foo/42")

    def test_top_level_extension(self):
        url = Url("/")
        self.assertEqual(url["foo"](), "/foo")
        self.assertEqual(url["foo"]("bar"), "/foo/bar")
        self.assertEqual(url[42]("bar"), "/42/bar")
        self.assertEqual(url["foo"](42), "/foo/42")
        self.assertEqual(url["foo"][42]("bar"), "/foo/42/bar")

    def test_prefix_extension(self):
        url = Url("/prefix")
        self.assertEqual(url["foo"](), "/prefix/foo")
        self.assertEqual(url["foo"]("bar"), "/prefix/foo/bar")
        self.assertEqual(url[42]("bar"), "/prefix/42/bar")
        self.assertEqual(url["foo"](42), "/prefix/foo/42")
        self.assertEqual(url["foo"][42]("bar"), "/prefix/foo/42/bar")

    def test_relative_prefix_extension(self):
        url = Url("..")
        self.assertEqual(url["foo"](), "../foo")
        self.assertEqual(url["foo"]("bar"), "../foo/bar")
        self.assertEqual(url[42]("bar"), "../42/bar")
        self.assertEqual(url["foo"](42), "../foo/42")
        self.assertEqual(url["foo"][42]("bar"), "../foo/42/bar")

    def test_query(self):
        url = Url("/foo")["bar"]
        self.assertEqual(url("baz", a="b"), "/foo/bar/baz?a=b")
        self.assertEqual(url("baz", a=42), "/foo/bar/baz?a=42")
        self.assertEqual(url("baz", a="b", b="a"),
                         "/foo/bar/baz?a=b&b=a")

    def test_escape(self):
        url = Url("/")
        self.assertEqual(url("foo/bar"), "/foo%2Fbar")
        self.assertEqual(url("foo?bar"), "/foo%3Fbar")
        self.assertEqual(url("foo&bar"), "/foo%26bar")
        self.assertEqual(url("foo#bar"), "/foo%23bar")
        kwargs = {"?foo": "/bar#",
                  "f=o?o": "?b=a&r"}
        self.assertEqual(url(**kwargs),
                         "/?%3Ffoo=%2Fbar%23&f%3Do%3Fo=%3Fb%3Da%26r")


class TestNoLegacyTornadoWorkarounds(unittest.TestCase):

    def test_no_mutablemapping_monkeypatch_needed(self):
        # cms.server.util used to monkey-patch collections.MutableMapping
        # back onto the collections module for Tornado 4.5.3's benefit.
        # Tornado 6.x doesn't need it -- importing this module shouldn't
        # touch collections.MutableMapping at all.
        import collections
        import importlib
        had_attr_before = hasattr(collections, "MutableMapping")
        module = importlib.import_module("cms.server.util")
        self.assertIsNotNone(module)
        has_attr_after = hasattr(collections, "MutableMapping")
        self.assertEqual(had_attr_before, has_attr_after)

    def test_tornado_version_is_6x(self):
        import tornado
        self.assertTrue(
            tornado.version.startswith("6."),
            "Expected Tornado 6.x, got %r -- the rest of this migration "
            "assumes Tornado 6's native asyncio IOLoop integration." %
            tornado.version)


class FetchStreamsFileTest(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        import tempfile
        import tornado.httpserver
        import tornado.netutil
        import tornado.web
        from unittest.mock import MagicMock
        from cms.server.util import FileHandlerMixin

        # A file bigger than one read chunk, to confirm streaming (not
        # buffering the whole thing) actually happens.
        self.chunk_size = 1024
        self.content = b"x" * (self.chunk_size * 3 + 17)

        self.fake_cacher = MagicMock()
        self.fake_cacher.CHUNK_SIZE = self.chunk_size
        import io
        self.fake_cacher.get_file.return_value = io.BytesIO(self.content)
        self.fake_cacher.get_size.return_value = len(self.content)

        class TestHandler(FileHandlerMixin):
            async def get(inner_self):
                inner_self.application.service = MagicMock(
                    file_cacher=self.fake_cacher)
                await inner_self.fetch(
                    "somedigest", "application/octet-stream",
                    filename="thefile.bin")

        application = tornado.web.Application([(r"/f", TestHandler)])
        self.server = tornado.httpserver.HTTPServer(application)
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        self.server.add_sockets(sockets)
        self.port = sockets[0].getsockname()[1]
        self.addAsyncCleanup(self._stop)

    async def _stop(self):
        self.server.stop()
        await self.server.close_all_connections()

    async def test_serves_full_content_and_content_disposition_header(self):
        import asyncio
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            writer.write(
                b"GET /f HTTP/1.1\r\nHost: localhost\r\n"
                b"Connection: close\r\n\r\n")
            await writer.drain()
            response = b""
            while True:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=5)
                if not chunk:
                    break
                response += chunk
                if len(response) > len(self.content) + 4096:
                    break
        finally:
            writer.close()
        self.assertIn(b"200 OK", response)
        self.assertIn(b"thefile.bin", response)
        self.assertIn(self.content, response)


if __name__ == "__main__":
    unittest.main()
