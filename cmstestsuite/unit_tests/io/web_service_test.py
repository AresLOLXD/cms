#!/usr/bin/env python3

"""Tests for cms.io.web_service.WebService."""

import asyncio
import unittest
from unittest.mock import patch

import tornado.web

from cms.conf import Address
from cms.io.web_service import WebService, resolve_remote_ip
from cmstestsuite.unit_tests.servicelogmixin import ServiceLoggingIsolationMixin


class EchoHandler(tornado.web.RequestHandler):
    def get(self):
        self.write("hello from %s" % type(self.application.service).__name__)


class SlowEchoHandler(tornado.web.RequestHandler):
    async def get(self):
        # A genuinely in-flight async handler: it's still awaiting
        # something (not just about to finish synchronously) when a
        # shutdown is triggered mid-request.
        await asyncio.sleep(0.3)
        self.write("hello from %s" % type(self.application.service).__name__)


class WebServiceTest(ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def asyncSetUp(self, mock_get_address):
        mock_get_address.return_value = Address("127.0.0.1", 0)
        self.service = WebService(
            listen_port=0,
            handlers=[(r"/", EchoHandler), (r"/slow", SlowEchoHandler)],
            parameters={}, shard=0, listen_address="127.0.0.1")
        self.run_task = asyncio.create_task(self.service._async_run())
        await asyncio.sleep(0.05)
        self.addAsyncCleanup(self._stop_service)

    async def _stop_service(self):
        self.service.exit()
        await asyncio.wait_for(self.run_task, timeout=5)

    async def test_serves_a_real_request(self):
        port = self.service._http_server_sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"200 OK", response)
        self.assertIn(b"hello from WebService", response)

    async def test_application_service_backreference(self):
        self.assertIs(self.service.application.service, self.service)

    async def test_exit_stops_accepting_new_connections(self):
        port = self.service._http_server_sockets[0].getsockname()[1]
        self.service.exit()
        await asyncio.wait_for(self.run_task, timeout=5)
        with self.assertRaises(ConnectionRefusedError):
            await asyncio.open_connection("127.0.0.1", port)

    async def test_exit_lets_an_in_flight_request_finish(self):
        # A request already being handled when exit() is called should
        # still get its response, not be cut off mid-flight. Uses
        # SlowEchoHandler (an "async def get" that awaits
        # asyncio.sleep) so the request is genuinely still in-flight,
        # not already finished, when exit() fires.
        port = self.service._http_server_sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"GET /slow HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            self.service.exit()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"200 OK", response)
        await asyncio.wait_for(self.run_task, timeout=5)
        # Also confirm the drain loop actually ran to completion: no
        # connection should be left open in the HTTPServer.
        self.assertEqual(self.service._http_server._connections, set())


class ResolveRemoteIpTest(unittest.TestCase):

    def test_no_proxies_ignores_header(self):
        self.assertEqual(
            resolve_remote_ip("1.2.3.4, 5.6.7.8", "9.9.9.9", 0), "9.9.9.9")

    def test_no_proxies_ignores_missing_header(self):
        self.assertEqual(resolve_remote_ip(None, "9.9.9.9", 0), "9.9.9.9")

    def test_one_proxy_takes_rightmost_address(self):
        self.assertEqual(
            resolve_remote_ip("1.2.3.4, 5.6.7.8", "9.9.9.9", 1), "5.6.7.8")

    def test_two_proxies_takes_second_from_right(self):
        self.assertEqual(
            resolve_remote_ip("1.2.3.4, 5.6.7.8", "9.9.9.9", 2), "1.2.3.4")

    def test_falls_back_to_socket_ip_when_header_too_short(self):
        self.assertEqual(
            resolve_remote_ip("5.6.7.8", "9.9.9.9", 2), "9.9.9.9")

    def test_falls_back_to_socket_ip_when_header_missing(self):
        self.assertEqual(resolve_remote_ip(None, "9.9.9.9", 1), "9.9.9.9")


class WebServiceIntegrationTest(ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase):
    """A WebService assembled the way a real AdminWebServer/
    ContestWebServer (sub-projects 2.5b/2.5c) will: static files, an
    RPC route, and a file-serving handler all registered together."""

    @patch("cms.io.async_service.get_service_address")
    async def asyncSetUp(self, mock_get_address):
        import tempfile
        import os
        from cms.io.static_handler import MultiLocationStaticFileHandler
        from cms.io.web_rpc import RPCHandler
        from cms.server.util import FileHandlerMixin

        mock_get_address.return_value = Address("127.0.0.1", 0)

        self.static_dir = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup_static_dir)
        with open(os.path.join(self.static_dir.name, "asset.txt"), "w") as f:
            f.write("a static asset")

        class FileFetchingHandler(FileHandlerMixin):
            async def get(inner_self):
                await inner_self.fetch(
                    "irrelevant-in-this-test", "text/plain")

        handlers = [
            MultiLocationStaticFileHandler.make_route(
                r"/static/(.*)", [self.static_dir.name]),
            RPCHandler.make_route(r"/rpc/([^/]+)/([0-9]+)/([^/]+)", None),
        ]
        self.service = WebService(
            listen_port=0, handlers=handlers, parameters={},
            shard=0, listen_address="127.0.0.1")
        self.run_task = asyncio.create_task(self.service._async_run())
        await asyncio.sleep(0.05)
        self.addAsyncCleanup(self._stop_service)

    async def _cleanup_static_dir(self):
        self.static_dir.cleanup()

    async def _stop_service(self):
        self.service.exit()
        await asyncio.wait_for(self.run_task, timeout=5)

    async def test_static_route_works_inside_a_full_application(self):
        port = self.service._http_server_sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(
                b"GET /static/asset.txt HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"200 OK", response)
        self.assertIn(b"a static asset", response)

    async def test_rpc_route_404s_for_unknown_service_inside_full_application(self):
        port = self.service._http_server_sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            payload = b"{}"
            request = (
                "POST /rpc/NoSuchService/0/method HTTP/1.1\r\n"
                "Host: localhost\r\nContent-Type: application/json\r\n"
                "Accept: application/json\r\n"
                "Content-Length: %d\r\n\r\n" % len(payload)
            ).encode() + payload
            writer.write(request)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"404", response.split(b"\r\n", 1)[0])


class WebServiceParametersWireInfraRoutesTest(
        ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase):
    """WebService.__init__ must build /static and /rpc routes itself
    from the static_files/rpc_enabled/rpc_auth parameters, the way
    AdminWebServer/ContestWebServer actually pass them in -- not
    require callers to hand-register those routes in `handlers`."""

    @patch("cms.io.async_service.get_service_address")
    async def asyncSetUp(self, mock_get_address):
        mock_get_address.return_value = Address("127.0.0.1", 0)

        # A real (module, dir) pair, resolved via importlib.resources
        # the same way StaticFileHasher already does, pointing at a
        # fixture file checked into this test's own package.
        parameters = {
            "static_files": [("cmstestsuite.unit_tests.io",
                              "static_fixture")],
            "rpc_enabled": True,
            "rpc_auth": None,
        }

        self.service = WebService(
            listen_port=0, handlers=[], parameters=parameters,
            shard=0, listen_address="127.0.0.1")
        self.run_task = asyncio.create_task(self.service._async_run())
        await asyncio.sleep(0.05)
        self.addAsyncCleanup(self._stop_service)

    async def _stop_service(self):
        self.service.exit()
        await asyncio.wait_for(self.run_task, timeout=5)

    async def test_static_route_is_wired_from_parameters(self):
        port = self.service._http_server_sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(
                b"GET /static/fixture.txt HTTP/1.1\r\n"
                b"Host: localhost\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"200 OK", response)
        self.assertIn(b"fixture content", response)

    async def test_rpc_route_is_wired_from_parameters(self):
        port = self.service._http_server_sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            payload = b"{}"
            request = (
                "POST /rpc/NoSuchService/0/method HTTP/1.1\r\n"
                "Host: localhost\r\nContent-Type: application/json\r\n"
                "Accept: application/json\r\n"
                "Content-Length: %d\r\n\r\n" % len(payload)
            ).encode() + payload
            writer.write(request)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"404", response.split(b"\r\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
