#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2014-2017 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2015 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2019 Edoardo Morassutto <edoardo.morassutto@gmail.com>
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

"""Tests for RPC system.

"""

import json
import logging
import socket
import unittest
from collections.abc import Callable
from unittest.mock import Mock, patch

import gevent
import gevent.event
import gevent.socket
from gevent.server import StreamServer

from cms import Address, ServiceCoord
from cms.io import RPCError, rpc_method, RemoteServiceServer, \
    RemoteServiceClient
from cms.io.rpc import FailureSeriesLog


class MockService:
    def not_rpc_callable(self):
        pass

    @rpc_method
    def raise_exception(self):
        raise RuntimeError()

    @rpc_method
    def return_unencodable(self):
        return RuntimeError()

    @rpc_method
    def echo(self, value):
        return value

    @rpc_method
    def echo_slow(self, value):
        gevent.sleep(0.01)
        return value

    @rpc_method
    def infinite(self):
        event = gevent.event.Event()
        event.wait()


class TestRPC(unittest.TestCase):

    def setUp(self):
        patcher = patch("cms.io.rpc.get_service_address")
        self.mock = patcher.start()
        self.addCleanup(patcher.stop)

        self.service = MockService()
        self.servers = list()
        self.clients = list()
        self.spawn_listener()

    def spawn_listener(self, host: str = "127.0.0.1", port: int = 0):
        """Start listening on the given host and port.

        Each incoming connection will cause a RemoteServiceServer to be
        instantiated (and therefore a greenlet to be spawned) and to be
        inserted in self.servers. The listening host and port will also
        be stored as self.host and self.port.

        host: the hostname or IP address
        port: the port (0 causes any available port to be chosen)

        """
        self._server = StreamServer((host, port), self.handle_new_connection)
        self._server.start()
        self.host = self._server.server_host
        self.port = self._server.server_port
        self.mock.return_value = Address(self.host, self.port)

    def kill_listener(self):
        """Stop listening."""
        # Some code may kill the listener in the middle of a test, and
        # plan to spawn a new one afterwards. Yet if the test fails
        # then upon tearDown _server will still be unset.
        if hasattr(self, "_server"):
            self._server.stop()
            del self._server
        # We leave self.host and self.port.

    def handle_new_connection(self, socket_: socket.socket, address: tuple[str, int]):
        """Create a new RemoteServiceServer to handle a new connection.

        Instantiate a RemoteServiceServer, add it to self.servers and
        have it listen on the given socket (which is for a connection
        opened by a remote host from the given address). This method
        will block until the socket gets closed.

        socket_: the socket to use
        address: the (ip address, port) of the remote part

        """
        server = RemoteServiceServer(self.service, address)
        self.servers.append(server)
        server.handle(socket_)

    def get_client(
        self, coord: ServiceCoord, block: bool = True, auto_retry: float | None = None
    ) -> RemoteServiceClient:
        """Obtain a new RemoteServiceClient to connect to a server.

        Instantiate a RemoteServiceClient, spawn its greenlet and add
        it to self.clients. It will try to connect to the service at
        the given coordinates.

        coord: the (name, shard) of the service
        block: whether to wait for the connection to be
            established before returning
        auto_retry: how long to wait after a disconnection
            before trying to reconnect
        return: a client

        """
        client = RemoteServiceClient(coord, auto_retry)
        client.connect()
        if block:
            client._connection_event.wait()
        self.clients.append(client)
        return client

    def disconnect_servers(self):
        """Disconnect all registered servers from their clients."""
        servers = self.servers.copy()
        for server in servers:
            if server.connected:
                server.disconnect()

    def disconnect_clients(self):
        """Disconnect all registered clients from their servers."""
        clients = self.clients.copy()
        for client in clients:
            if client.connected:
                client.disconnect()

    @staticmethod
    def sleep():
        """Pause the greenlet so other work can be done."""
        gevent.sleep(0.005)

    def tearDown(self):
        self.kill_listener()
        self.disconnect_clients()
        self.disconnect_servers()
        self.sleep()

    def test_method_not_existent(self):
        client = self.get_client(ServiceCoord("Foo", 0))
        result = client.not_existent()
        result.wait()
        self.assertFalse(result.successful())
        self.assertIsInstance(result.exception, RPCError)

    def test_method_not_rpc_callable(self):
        client = self.get_client(ServiceCoord("Foo", 0))
        result = client.not_rpc_callable()
        result.wait()
        self.assertFalse(result.successful())
        self.assertIsInstance(result.exception, RPCError)

    def test_method_raise_exception(self):
        client = self.get_client(ServiceCoord("Foo", 0))
        result = client.raise_exception()
        result.wait()
        self.assertFalse(result.successful())
        self.assertIsInstance(result.exception, RPCError)

    def test_method_send_unencodable(self):
        client = self.get_client(ServiceCoord("Foo", 0))
        result = client.echo(value=RuntimeError())
        result.wait()
        self.assertFalse(result.successful())
        self.assertIsInstance(result.exception, RPCError)

    # TODO Are we sure we want this to be the correct behavior? That
    # means that if the server (by mistake?) sends unencodable data
    # then the client will never get any data nor any error. It will
    # get stuck until it goes in timeout.
    def test_method_return_unencodable(self):
        client = self.get_client(ServiceCoord("Foo", 0))
        result = client.return_unencodable()
        result.wait(timeout=0.002)
        self.assertFalse(result.ready())

    def test_method_return_bool(self):
        client = self.get_client(ServiceCoord("Foo", 0))
        result = client.echo(value=True)
        result.wait()
        self.assertTrue(result.successful())
        self.assertIs(result.value, True)

    def test_method_return_int(self):
        client = self.get_client(ServiceCoord("Foo", 0))
        result = client.echo(value=42)
        result.wait()
        self.assertTrue(result.successful())
        self.assertEqual(result.value, 42)

    def test_method_return_string(self):
        client = self.get_client(ServiceCoord("Foo", 0))
        result = client.echo(value="Hello World")
        result.wait()
        self.assertTrue(result.successful())
        self.assertEqual(result.value, "Hello World")

    def test_method_return_list(self):
        client = self.get_client(ServiceCoord("Foo", 0))
        result = client.echo(value=["Hello", 42, "World"])
        result.wait()
        self.assertTrue(result.successful())
        self.assertEqual(result.value, ["Hello", 42, "World"])

    @patch("cms.io.rpc.gevent.socket.socket")
    @patch("cms.io.rpc.gevent.socket.getaddrinfo")
    def test_background_connect(self, getaddrinfo_mock, socket_mock):
        # Calling getaddrinfo breaks the mocking of the socket, so it is mocked
        # as well. It returns the addresses associated with the service, in
        # this case just one.
        getaddrinfo_mock.return_value = [
            (gevent.socket.AF_INET, gevent.socket.SOCK_STREAM, 6, "",
                (self.host, self.port))]
        # Patch the connect method of sockets so that it blocks until
        # we set the done_event (we will do so at the end of the test).
        connect_mock = socket_mock.return_value.connect
        done_event = gevent.event.Event()
        connect_mock.side_effect = lambda _: done_event.wait()
        # Connect to the RPC server in non-blocking mode and make sure
        # that we indeed don't block (i.e., take more than 0.001s).
        with gevent.Timeout(0.001) as timeout:
            try:
                client = self.get_client(ServiceCoord("Foo", 0), block=False)
            except gevent.Timeout as t:
                if t is not timeout:
                    raise
                self.fail("Connecting blocks")
        # As socket.connect() never returned, the RPC client cannot have
        # connected.
        self.assertFalse(client.connected)
        # Unblock the socket's connect method and make sure it actually
        # got called (otherwise this whole tests is pointless). Also,
        # yield to other greenlets so that they can be awoken after the
        # event triggered.
        done_event.set()
        gevent.sleep()
        getaddrinfo_mock.assert_called_once_with(self.host, self.port,
                                                 type=socket.SOCK_STREAM)
        connect_mock.assert_called_once_with((self.host, self.port))

    def test_autoreconnect1(self):
        client = self.get_client(ServiceCoord("Foo", 0), auto_retry=0.002)
        self.sleep()
        self.assertTrue(client.connected)
        self.disconnect_servers()
        gevent.sleep(0.1)
        self.assertTrue(client.connected,
                        "Autoreconnect didn't kick in "
                        "after server disconnected")

    def test_autoreconnect2(self):
        client = self.get_client(ServiceCoord("Foo", 0), auto_retry=0.002)
        self.sleep()
        self.assertTrue(client.connected)
        self.disconnect_servers()
        self.kill_listener()
        self.sleep()
        self.assertFalse(client.connected)
        self.spawn_listener(port=self.port)
        self.sleep()
        self.assertTrue(client.connected, "Autoreconnect didn't kick in "
                                          "after server came back online")

    def test_autoreconnect3(self):
        client = self.get_client(ServiceCoord("Foo", 0), auto_retry=0.002)
        self.sleep()
        self.assertTrue(client.connected)
        self.disconnect_clients()
        self.sleep()
        self.assertFalse(client.connected, "Autoreconnect still active "
                                           "after explicit disconnection")

    def test_concurrency(self):
        client = self.get_client(ServiceCoord("Foo", 0))
        result1 = client.infinite()
        self.sleep()
        result2 = client.echo(value=True)
        self.sleep()
        self.assertTrue(result2.successful())
        self.assertIs(result2.value, True)
        self.assertFalse(result1.ready())
        self.disconnect_clients()
        self.assertTrue(result1.ready())
        self.assertFalse(result1.successful())
        self.assertIsInstance(result1.exception, RPCError)

    def test_callbacks(self):
        coord = ServiceCoord("Foo", 0)
        client = self.get_client(coord)
        self.sleep()
        client.disconnect()
        on_connect_handler = Mock()
        client.add_on_connect_handler(on_connect_handler)
        on_disconnect_handler = Mock()
        client.add_on_disconnect_handler(on_disconnect_handler)

        client.connect()
        self.sleep()
        self.assertTrue(client.connected)
        on_connect_handler.assert_called_once_with(coord)
        on_connect_handler.reset_mock()
        self.assertFalse(on_disconnect_handler.called)

        client.disconnect()
        self.sleep()
        self.assertFalse(client.connected)
        self.assertFalse(on_connect_handler.called)
        on_disconnect_handler.assert_called_once_with()
        on_disconnect_handler.reset_mock()

        client.connect()
        self.sleep()
        self.assertTrue(client.connected)
        on_connect_handler.assert_called_once_with(coord)
        on_connect_handler.reset_mock()
        self.assertFalse(on_disconnect_handler.called)

        self.disconnect_servers()
        gevent.sleep(0.1)
        self.assertFalse(client.connected)
        self.assertFalse(on_connect_handler.called)
        on_disconnect_handler.assert_called_once_with()
        on_disconnect_handler.reset_mock()

    def test_reusability(self):
        client = self.get_client(ServiceCoord("Foo", 0))

        on_connect_handler = Mock()
        client.add_on_connect_handler(on_connect_handler)
        on_disconnect_handler = Mock()
        client.add_on_disconnect_handler(on_disconnect_handler)

        for i in range(10):
            self.assertTrue(client.connected)
            result = client.echo(value=42)
            result.wait()
            self.assertTrue(result.successful())
            self.assertEqual(result.value, 42)
            self.assertTrue(client.connected)

            self.sleep()
            client.disconnect()
            self.assertFalse(client.connected)
            self.sleep()
            client.connect()
            client._connection_event.wait()
            self.assertTrue(client.connected)

        self.sleep()

        self.assertEqual(on_connect_handler.call_count, 10)
        self.assertEqual(on_disconnect_handler.call_count, 10)

    def test_double_connect_client(self):
        # Check that asking an already-connected client to connect
        # again causes an error.
        client = self.get_client(ServiceCoord("Foo", 0))
        self.assertRaises(Exception, client.connect)

    def test_double_connect_server(self):
        # Check that asking an already-connected server to initialize
        # again its connection causes an error.
        self.get_client(ServiceCoord("Foo", 0))
        self.sleep()
        self.assertRaises(Exception, self.servers[0].initialize, "foo")

    def test_double_disconnect_client(self):
        # Check that asking a non-connected client to disconnect is
        # harmless (i.e. disconnection is idempotent).
        client = self.get_client(ServiceCoord("Foo", 0))
        client.disconnect()
        self.sleep()
        client.disconnect()
        self.sleep()

    def test_double_disconnect_server(self):
        # Check that asking a non-connected server to disconnect is
        # harmless (i.e. disconnection is idempotent).
        self.get_client(ServiceCoord("Foo", 0))
        self.sleep()
        self.servers[0].disconnect()
        self.sleep()
        self.servers[0].disconnect()
        self.sleep()

    def test_send_invalid_json(self):
        sock = gevent.socket.create_connection((self.host, self.port))
        sock.sendall(b"foo\r\n")
        self.sleep()
        # Malformed messages cause the connection to be closed.
        self.assertFalse(self.servers[0].connected)
        sock.close()

    def test_send_incomplete_json(self):
        sock = gevent.socket.create_connection((self.host, self.port))
        sock.sendall(b'{"__id": "foo"}\r\n')
        self.sleep()
        # Malformed messages cause the connection to be closed.
        self.assertFalse(self.servers[0].connected)
        sock.close()

    def test_send_non_object_json(self):
        for payload in (b"null", b"[]", b'"x"'):
            with self.subTest(payload=payload):
                count = len(self.servers)
                sock = gevent.socket.create_connection((self.host, self.port))
                sock.sendall(payload + b"\r\n")
                # Malformed messages cause the connection to be closed:
                # wait for the server of this very connection to do so.
                self.wait_until(lambda: len(self.servers) > count
                                and not self.servers[count].connected)
                sock.close()

    def spawn_custom_listener(
        self, handle: Callable[[socket.socket, tuple], None]
    ):
        """Point the clients at a new listener with its own handler.

        It is stopped when the test ends, after tearDown has
        disconnected the clients.

        handle: the handler of each new connection.

        """
        server = StreamServer(("127.0.0.1", 0), handle)
        server.start()
        self.addCleanup(server.stop)
        self.mock.return_value = Address("127.0.0.1", server.server_port)

    def stop_when_done(self, client: RemoteServiceClient):
        """Kill the connect loop of client when the test ends."""
        self.addCleanup(
            lambda: client._loop is not None and client._loop.kill())

    @staticmethod
    def wait_until(predicate: Callable[[], bool], timeout: float = 2.0):
        with gevent.Timeout(timeout, AssertionError("Condition not met.")):
            while not predicate():
                gevent.sleep(0.005)

    @staticmethod
    def flaky_getaddrinfo(failures: int) -> Callable:
        """Return a getaddrinfo that fails failures times, then works."""
        real_getaddrinfo = gevent.socket.getaddrinfo
        calls = 0

        def getaddrinfo(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls <= failures:
                raise socket.gaierror(
                    socket.EAI_NONAME, "Name or service not known")
            return real_getaddrinfo(*args, **kwargs)

        return getaddrinfo

    def test_name_resolution_failures_do_not_end_the_loop(self):
        with patch("cms.io.rpc.gevent.socket.getaddrinfo",
                   self.flaky_getaddrinfo(2)):
            with self.assertLogs("cms.io.rpc", "DEBUG") as logs:
                client = self.get_client(
                    ServiceCoord("Foo", 0), block=False, auto_retry=0.01)
                self.stop_when_done(client)
                self.assertTrue(client.wait_for_connection(timeout=2))

        self.assertFalse(client._loop.dead)
        self.assertEqual(client.echo(value=42).get(timeout=2), 42)
        # One WARNING for the outage, DEBUG for each retry, one INFO
        # when it ends.
        self.assertEqual(
            [record.levelname for record in logs.records
             if "resolve" in record.getMessage()],
            ["WARNING", "DEBUG", "INFO"])

    def test_one_shot_client_that_cannot_resolve_ends_quietly(self):
        # Without auto_retry, it ends up not connected, as when the
        # connection is refused.
        with patch("cms.io.rpc.gevent.socket.getaddrinfo",
                   self.flaky_getaddrinfo(1)):
            with self.assertLogs("cms.io.rpc", "WARNING"):
                client = self.get_client(ServiceCoord("Foo", 0), block=False)
                client._loop.join(timeout=2)

        self.assertTrue(client._loop.successful())
        self.assertFalse(client.connected)

    def test_malformed_answers_make_the_client_reconnect(self):
        # Connection n gets bad_answers[n], unasked; once they are over,
        # a real server answers.
        bad_answers = [b"not json", b"null", b"[]", b'"x"', b'{"__id": 1}']
        connections = []

        def answer_badly(socket_: socket.socket, address: tuple):
            connections.append(address)
            if len(connections) > len(bad_answers):
                self.handle_new_connection(socket_, address)
                return
            socket_.sendall(bad_answers[len(connections) - 1] + b"\r\n")
            while socket_.recv(1024):
                pass

        self.spawn_custom_listener(answer_badly)

        with self.assertLogs("cms.io.rpc", "DEBUG") as logs:
            client = self.get_client(
                ServiceCoord("Foo", 0), block=False, auto_retry=0.01)
            self.stop_when_done(client)
            self.wait_until(
                lambda: len(connections) > len(bad_answers)
                and client.connected)
            self.assertEqual(client.echo(value=42).get(timeout=2), 42)

        self.assertFalse(client._loop.dead)
        # Only the first one is a WARNING, so that a peer that keeps
        # answering garbage can't flood the logs.
        self.assertEqual(
            [record.levelname for record in logs.records
             if "Cannot parse" in record.getMessage()
             or "missing some fields" in record.getMessage()
             or "well-formed" in record.getMessage()],
            ["WARNING"] + ["DEBUG"] * (len(bad_answers) - 1) + ["INFO"])
        # The WARNING names the peer: it's the only line of the series
        # the Overview shows.
        warning, = [record.getMessage() for record in logs.records
                    if record.levelname == "WARNING"
                    and "Cannot parse" in record.getMessage()]
        self.assertIn("(Foo,0)", warning)

    def test_answer_after_garbage_on_dropped_connection_is_stale(self):
        # Each connection gets a malformed line and then a well-formed
        # one, which the client reads from the connection the first one
        # made it drop: only an answer on the live connection (from the
        # real server, in the end) ends the series of malformed ones.
        well_formed = b'{"__id": "stale", "__data": null, "__error": null}'
        connections = []

        def answer_badly(socket_: socket.socket, address: tuple):
            connections.append(address)
            if len(connections) > 3:
                self.handle_new_connection(socket_, address)
                return
            socket_.sendall(b"garbage\r\n" + well_formed + b"\r\n")
            while socket_.recv(1024):
                pass

        self.spawn_custom_listener(answer_badly)

        with self.assertLogs("cms.io.rpc", "DEBUG") as logs:
            client = self.get_client(
                ServiceCoord("Foo", 0), block=False, auto_retry=0.01)
            self.stop_when_done(client)
            self.wait_until(
                lambda: len(connections) > 3 and client.connected)
            self.assertEqual(client.echo(value=42).get(timeout=2), 42)

        self.assertEqual(
            [record.levelname for record in logs.records
             if "Cannot parse" in record.getMessage()
             or "well-formed" in record.getMessage()],
            ["WARNING", "DEBUG", "DEBUG", "INFO"])

    def test_flapping_name_resolution_warns_once(self):
        # The name resolves at every other try, and the peer refuses the
        # connection: each try is a new series of failures, but only
        # one WARNING (and its INFO) is logged per WARNING_INTERVAL.
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            self.mock.return_value = Address(*unused.getsockname())
        real_getaddrinfo = gevent.socket.getaddrinfo
        calls = 0

        def flapping_getaddrinfo(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls % 2 == 1:
                raise socket.gaierror(
                    socket.EAI_NONAME, "Name or service not known")
            return real_getaddrinfo(*args, **kwargs)

        with patch("cms.io.rpc.gevent.socket.getaddrinfo",
                   flapping_getaddrinfo):
            with self.assertLogs("cms.io.rpc", "DEBUG") as logs:
                client = self.get_client(
                    ServiceCoord("Foo", 0), block=False, auto_retry=0.01)
                self.stop_when_done(client)
                self.wait_until(lambda: calls >= 10)

        levels = [record.levelname for record in logs.records
                  if "resolve" in record.getMessage()]
        self.assertEqual(levels[:2], ["WARNING", "INFO"])
        self.assertEqual(set(levels[2:]), {"DEBUG"})

    def test_answers_that_cannot_be_processed_are_malformed(self):
        # An answer whose id isn't a string (a list can't even be looked
        # up) and one nested too deep for json.loads (RecursionError,
        # not ValueError) are malformed answers like the others: the
        # client fails the call, drops the connection and reconnects,
        # and the next call is answered.
        bad_answers = {
            "id that is a list":
                b'{"__id": [], "__data": null, "__error": null}',
            "JSON nested too deep": b"[" * 100_000 + b"]" * 100_000,
        }
        for name, bad_answer in bad_answers.items():
            with self.subTest(name):
                connections = []

                def answer_badly(socket_: socket.socket, address: tuple,
                                 bad_answer: bytes = bad_answer):
                    connections.append(address)
                    if len(connections) > 1:
                        self.handle_new_connection(socket_, address)
                        return
                    socket_.makefile("rb").readline()
                    socket_.sendall(bad_answer + b"\r\n")
                    while socket_.recv(1024):
                        pass

                self.spawn_custom_listener(answer_badly)
                client = self.get_client(
                    ServiceCoord("Foo", 0), auto_retry=0.01)
                self.stop_when_done(client)

                first = client.echo(value=1)
                first.wait(timeout=2)
                self.assertIsInstance(first.exception, RPCError)
                self.wait_until(
                    lambda: len(connections) > 1 and client.connected)
                self.assertEqual(client.echo(value=42).get(timeout=2), 42)

    def test_lines_read_after_a_drop_are_discarded(self):
        # Each connection gets, in one write, a malformed line and then
        # two well-formed answers with unknown ids. The client drops
        # the connection at the first line and discards the other two:
        # they aren't taken as answers, so no "No pending request"
        # WARNING is logged at each reconnect.
        lines = (b"garbage\r\n"
                 b'{"__id": "a", "__data": null, "__error": null}\r\n'
                 b'{"__id": "b", "__data": null, "__error": null}\r\n')
        connections = []

        def answer_badly(socket_: socket.socket, address: tuple):
            connections.append(address)
            if len(connections) > 3:
                self.handle_new_connection(socket_, address)
                return
            socket_.sendall(lines)
            while socket_.recv(1024):
                pass

        self.spawn_custom_listener(answer_badly)

        with self.assertLogs("cms.io.rpc", "DEBUG") as logs:
            client = self.get_client(
                ServiceCoord("Foo", 0), block=False, auto_retry=0.01)
            self.stop_when_done(client)
            self.wait_until(
                lambda: len(connections) > 3 and client.connected)
            self.assertEqual(client.echo(value=42).get(timeout=2), 42)

        self.assertEqual([record.getMessage() for record in logs.records
                          if "No pending request" in record.getMessage()],
                         [])
        # The first malformed line is the only WARNING.
        self.assertEqual([record.levelname for record in logs.records
                          if record.levelno >= logging.WARNING],
                         ["WARNING"])

    def test_answer_read_just_before_the_end_is_delivered(self):
        # The peer answers and closes the connection at once, so the
        # client reads the answer and then the end of the connection.
        # The answer must reach the caller, not be failed as "Connection
        # closed.", whether or not the "Terminated connection" record
        # yields inside finalize() (as the RPC write of a
        # LogServiceHandler can).
        def answer_and_close(socket_: socket.socket, address: tuple):
            request = json.loads(socket_.makefile("rb").readline())
            socket_.sendall(json.dumps(
                {"__id": request["__id"], "__data": "answer",
                 "__error": None}).encode("utf-8") + b"\r\n")
            socket_.shutdown(socket.SHUT_RDWR)

        yielding = False

        def yield_on_terminated(record: logging.LogRecord) -> bool:
            if yielding and record.getMessage().startswith(
                    "Terminated connection"):
                gevent.sleep(0)
            return True

        self.spawn_custom_listener(answer_and_close)
        rpc_logger = logging.getLogger("cms.io.rpc")
        rpc_logger.addFilter(yield_on_terminated)
        self.addCleanup(rpc_logger.removeFilter, yield_on_terminated)

        # assertLogs enables the INFO records the filter acts on.
        with self.assertLogs("cms.io.rpc", "DEBUG"):
            results = []
            for yielding in [False] * 5 + [True] * 5:
                client = self.get_client(ServiceCoord("Foo", 0))
                result = client.echo(value=42)
                result.wait(timeout=2)
                results.append(result.value if result.successful()
                               else result.exception)

        self.assertEqual(results, ["answer"] * 10)

    def test_peer_that_stops_reading_and_answers_garbage(self):
        # The peer stops reading, so the client's writes pile up
        # unsent, and then it sends a malformed line: the client must
        # drop the connection anyway, fail the calls stuck behind it,
        # and reconnect. With a small, fixed send buffer, a few calls
        # are enough, whatever the kernel's TCP buffer tuning.
        connections = []
        release = gevent.event.Event()

        def stop_reading(socket_: socket.socket, address: tuple):
            connections.append(address)
            if len(connections) > 1:
                self.handle_new_connection(socket_, address)
                return
            gevent.sleep(0.5)
            socket_.sendall(b"garbage\r\n")
            release.wait()

        self.spawn_custom_listener(stop_reading)
        self.addCleanup(release.set)
        client = self.get_client(ServiceCoord("Foo", 0), auto_retry=0.05)
        self.stop_when_done(client)
        client._socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)

        big = "x" * 500_000
        calls = [gevent.spawn(client.echo, value=big) for _ in range(4)]
        gevent.joinall(calls, timeout=3)

        self.assertTrue(all(call.ready() for call in calls))
        results = [call.value for call in calls]
        for result in results:
            result.wait(timeout=1)
        self.assertEqual([type(result.exception) for result in results],
                         [RPCError] * len(calls))
        self.wait_until(lambda: len(connections) > 1 and client.connected)
        self.assertEqual(client.echo(value=42).get(timeout=2), 42)

    def test_reconnects_no_faster_than_auto_retry(self):
        # A peer that accepts and closes at once must not be
        # reconnected to as fast as the loop can go.
        auto_retry = 0.1
        connections = []

        def close_at_once(socket_: socket.socket, address: tuple):
            connections.append(address)
            socket_.close()

        self.spawn_custom_listener(close_at_once)

        client = self.get_client(
            ServiceCoord("Foo", 0), block=False, auto_retry=auto_retry)
        self.stop_when_done(client)
        gevent.sleep(1)

        self.assertGreaterEqual(len(connections), 2)
        self.assertLessEqual(len(connections), 1 / auto_retry + 2)


class TestFailureSeriesLog(unittest.TestCase):

    def setUp(self):
        self.now = 1000.0
        patcher = patch("cms.io.rpc.monotonic", lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.logger = logging.getLogger("cmstestsuite.failureserieslog")
        self.series = FailureSeriesLog(self.logger)

    def log(self, *outcomes: bool) -> list[str]:
        """Log a failure for each False, a success for each True.

        return: the level names of what that logged.

        """
        records = []
        handler = logging.Handler(logging.DEBUG)
        handler.emit = records.append
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG)
        try:
            for succeeded in outcomes:
                if succeeded:
                    self.series.succeeded("success")
                else:
                    self.series.failed("failure")
        finally:
            self.logger.removeHandler(handler)
        return [record.levelname for record in records]

    def test_series_is_a_warning_then_debug_then_an_info(self):
        self.assertEqual(self.log(False, False, False, True),
                         ["WARNING", "DEBUG", "DEBUG", "INFO"])
        self.assertFalse(self.series.failing)

    def test_success_without_failures_logs_nothing(self):
        self.assertEqual(self.log(True), [])

    def test_flapping_warns_once_a_minute(self):
        self.assertEqual(self.log(False, True), ["WARNING", "INFO"])
        for _ in range(4):
            self.now += 1
            self.assertEqual(self.log(False, True), ["DEBUG", "DEBUG"])

        # A minute after the WARNING, a new series warns again.
        self.now += 56
        self.assertEqual(self.log(False, True), ["WARNING", "INFO"])

    def test_series_started_too_soon_warns_once_the_minute_is_over(self):
        self.log(False, True)
        self.now += 10
        self.assertEqual(self.log(False), ["DEBUG"])
        self.now += 49
        self.assertEqual(self.log(False), ["DEBUG"])
        self.now += 1
        self.assertEqual(self.log(False, False), ["WARNING", "DEBUG"])
        self.assertEqual(self.log(True), ["INFO"])


if __name__ == "__main__":
    unittest.main()
