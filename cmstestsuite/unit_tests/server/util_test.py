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

        # Per-test override of the kwargs passed to fetch() by TestHandler
        # below; read at request time, so each test can set it right before
        # calling self._request().
        self.fetch_kwargs = {"filename": "thefile.bin"}

        outer = self

        class TestHandler(FileHandlerMixin):
            async def get(inner_self):
                inner_self.application.service = MagicMock(
                    file_cacher=outer.fake_cacher)
                await inner_self.fetch(
                    "somedigest", "application/octet-stream",
                    **outer.fetch_kwargs)

        application = tornado.web.Application([(r"/f", TestHandler)])
        self.server = tornado.httpserver.HTTPServer(application)
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        self.server.add_sockets(sockets)
        self.port = sockets[0].getsockname()[1]
        self.addAsyncCleanup(self._stop)

    async def _stop(self):
        self.server.stop()
        await self.server.close_all_connections()

    async def _request(self):
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
        return response

    async def test_serves_full_content_and_content_disposition_header(self):
        response = await self._request()
        self.assertIn(b"200 OK", response)
        self.assertIn(b"thefile.bin", response)
        self.assertIn(self.content, response)

    async def test_cache_control_header_present(self):
        response = await self._request()
        self.assertIn(b"200 OK", response)
        self.assertIn(b"Cache-Control: no-cache, private", response)

    async def test_no_filename_means_no_content_disposition_header(self):
        self.fetch_kwargs = {}
        response = await self._request()
        self.assertIn(b"200 OK", response)
        self.assertNotIn(b"Content-Disposition", response)

    async def test_disposition_inline(self):
        self.fetch_kwargs = {
            "filename": "thefile.bin", "disposition": "inline"}
        response = await self._request()
        self.assertIn(b"200 OK", response)
        self.assertIn(
            b'Content-Disposition: inline; filename="thefile.bin"',
            response)

    async def test_non_ascii_filename_does_not_crash(self):
        self.fetch_kwargs = {"filename": "日本語.txt"}
        response = await self._request()
        self.assertIn(b"200 OK", response)
        self.assertIn(b"Content-Disposition", response)
        self.assertIn(self.content, response)

    async def test_key_error_from_get_file_returns_404(self):
        self.fake_cacher.get_file.side_effect = KeyError()
        response = await self._request()
        self.assertIn(b"404", response)

    async def test_tombstone_error_returns_503(self):
        from cms.db.filecacher import TombstoneError
        self.fake_cacher.get_file.side_effect = TombstoneError()
        response = await self._request()
        self.assertIn(b"503", response)


class MultiContestDecoratorReturnsFetchResultTest(unittest.IsolatedAsyncioTestCase):
    """Regression test for a Critical bug: multi_contest used to not
    `return` the wrapped function's result, so when the wrapped `get()` is
    `async def` (as it now is for the CWS file-download handlers), the
    coroutine it creates was discarded instead of being returned to
    Tornado's dispatcher, which never awaited it -- the file body never got
    written and the request silently completed with an empty 200 response.

    """

    async def asyncSetUp(self):
        import tornado.httpserver
        import tornado.netutil
        import tornado.web
        from unittest.mock import MagicMock
        from cms.server.util import FileHandlerMixin, multi_contest

        self.content = b"payload contents"

        self.fake_cacher = MagicMock()
        self.fake_cacher.CHUNK_SIZE = 1024
        import io
        self.fake_cacher.get_file.return_value = io.BytesIO(self.content)
        self.fake_cacher.get_size.return_value = len(self.content)

        fake_cacher = self.fake_cacher

        # The single-contest case (is_multi_contest() == False): the URL
        # doesn't carry a contest name, so get() is called with no extra
        # arguments and multi_contest just forwards the call through
        # unchanged. This is the common case exercised by all 5 real CWS
        # download endpoints when running in single-contest mode.
        class SingleContestHandler(FileHandlerMixin):
            def is_multi_contest(inner_self):
                return False

            @multi_contest
            async def get(inner_self):
                inner_self.application.service = MagicMock(
                    file_cacher=fake_cacher)
                await inner_self.fetch(
                    "somedigest", "application/octet-stream",
                    filename="thefile.bin")

        # The multi-contest case (is_multi_contest() == True): the URL
        # carries a leading contest-name path component that Tornado passes
        # as the first positional argument to get(); multi_contest is
        # supposed to swallow it and forward the rest.
        class MultiContestHandler(FileHandlerMixin):
            def is_multi_contest(inner_self):
                return True

            @multi_contest
            async def get(inner_self):
                inner_self.application.service = MagicMock(
                    file_cacher=fake_cacher)
                await inner_self.fetch(
                    "somedigest", "application/octet-stream",
                    filename="thefile.bin")

        application = tornado.web.Application([
            (r"/single", SingleContestHandler),
            (r"/multi/([^/]*)", MultiContestHandler),
        ])
        self.server = tornado.httpserver.HTTPServer(application)
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        self.server.add_sockets(sockets)
        self.port = sockets[0].getsockname()[1]
        self.addAsyncCleanup(self._stop)

    async def _stop(self):
        self.server.stop()
        await self.server.close_all_connections()

    async def _request(self, path: str) -> bytes:
        import asyncio
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", self.port)
        try:
            writer.write(
                ("GET %s HTTP/1.1\r\nHost: localhost\r\n"
                 "Connection: close\r\n\r\n" % path).encode("ascii"))
            await writer.drain()
            response = b""
            while True:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=5)
                if not chunk:
                    break
                response += chunk
        finally:
            writer.close()
        return response

    async def test_single_contest_get_body_is_actually_served(self):
        response = await self._request("/single")
        self.assertIn(b"200 OK", response)
        self.assertIn(self.content, response)

    async def test_multi_contest_get_body_is_actually_served(self):
        response = await self._request("/multi/somecontest")
        self.assertIn(b"200 OK", response)
        self.assertIn(self.content, response)


if __name__ == "__main__":
    unittest.main()
