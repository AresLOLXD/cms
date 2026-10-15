#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2013-2017 Luca Wehrstedt <luca.wehrstedt@gmail.com>
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

from collections.abc import Callable
import functools
import json
import logging
import socket
from time import monotonic
import traceback
from typing import Any
import typing
import uuid
from weakref import WeakSet

import gevent
import gevent.event
import gevent.lock
import gevent.socket

from cms.conf import Address, ServiceCoord
from cms.util import get_service_address

if typing.TYPE_CHECKING:
    from cms.io.service import Service


logger = logging.getLogger(__name__)


class RPCError(Exception):
    """Generic error during RPC communication."""
    pass


class ServiceNotConfiguredError(RPCError):
    """An RPC was called on a service that is not configured.

    That is not a failure: the service is simply not part of this
    deployment (e.g. ProxyService, when there is no ranking to send
    anything to), so callers that fire RPCs and forget about them have
    nothing to complain about.

    """
    pass


class FailureSeriesLog:
    """Log the series of a failure a client meets at each retry.

    It is for a failure that comes back every auto_retry seconds while
    it lasts (a name that doesn't resolve, a peer that answers
    garbage), so that it can't flood the logs. The first failure of a
    series is a WARNING, the others are DEBUG, and the first success
    after them is an INFO. A WARNING is logged at most once every
    WARNING_INTERVAL seconds, across series too, so that a failure
    that comes and goes can't flood the logs either: a series that
    starts sooner logs DEBUG until the interval is over, and then one
    WARNING. Its end is an INFO only if it logged a WARNING, so that
    each WARNING has its INFO, and DEBUG otherwise.

    It exists because that rule needs more state than a bool per
    failure (whether a series is going on, whether it warned, and when
    the last WARNING was), and both stacks must apply it alike. The
    state is per instance, never module-level: each client has its own,
    one per kind of failure. It lives here, in the lower layer, so that
    cms.io.async_rpc can use it too.

    """

    WARNING_INTERVAL = 60.0

    def __init__(self, log: logging.Logger):
        """Prepare to log a series of failures.

        log: the logger to log to (the client's).

        """
        self._log = log
        # Whether a series is going on and whether it logged a WARNING,
        # and when (in monotonic() seconds) the last WARNING was.
        self.failing = False
        self._warned = False
        self._last_warning: float | None = None

    def failed(self, message: str, *args: object, exc_info: bool = False):
        """Log a failure, as a WARNING or as DEBUG.

        message: the message to log, %-formatted with args.
        exc_info: whether to log the exception being handled too.

        """
        # stacklevel=2: the record points at the caller, the client
        # method that met the failure, as the detailed log shows it.
        now = monotonic()
        if not self._warned and (
                self._last_warning is None
                or now - self._last_warning >= self.WARNING_INTERVAL):
            self._warned = True
            self._last_warning = now
            self._log.warning(message, *args, exc_info=exc_info,
                              stacklevel=2)
        else:
            self._log.debug(message, *args, exc_info=exc_info,
                            stacklevel=2)
        self.failing = True

    def succeeded(self, message: str, *args: object):
        """Log the end of the series going on, if any.

        message: the message to log, %-formatted with args.

        """
        if not self.failing:
            return
        self._log.log(logging.INFO if self._warned else logging.DEBUG,
                      message, *args, stacklevel=2)
        self.failing = False
        self._warned = False


_T = typing.TypeVar("_T", bound=Callable)


def rpc_method(func: _T) -> _T:
    """Decorator for a method that other services are allowed to call.

    Does not do a lot, just defines the right method's attribute.

    func: the method to make RPC callable.
    return: the decorated method.

    """
    func.rpc_callable = True
    return func


class RemoteServiceBase:
    """Base class for both ends of a RPC connection.

    Just provides some basic helpers for I/O. It alternates between two
    states:
    - disconnected (when self.connected is False) means that no socket
      is bound and therefore no I/O can be performed. Attempting to
      read or write will fail. This is the default state.
    - connected (when self.connected is True) means that a socket is
      bound and (presumably) active. I/O can be performed. This state
      can be entered by calling initialize and can be exited by calling
      disconnect. It will also be exited when errors occur.

    When the state changes the on_connect or on_disconnect handlers
    will be fired.

    """
    # Incoming messages larger than 1 MiB are dropped to avoid DOS
    # attacks. XXX Check that this size is sensible.
    MAX_MESSAGE_SIZE = 1024 * 1024

    def __init__(self, remote_address: Address):
        """Prepare to handle a connection with the given remote address.

        remote_address: the address of the other end of the
            connection (origin or target, depending on its direction).

        """
        self._local_address = None
        self.remote_address = remote_address
        self._connection_event = gevent.event.Event()

        self._on_connect_handlers: list[Callable[[object], Any]] = list()
        self._on_disconnect_handlers: list[Callable[[object], Any]] = list()

        self._socket = None
        self._reader = None
        self._writer = None

        self._read_lock = gevent.lock.RLock()
        self._write_lock = gevent.lock.RLock()

    @property
    def connected(self) -> bool:
        """Return whether we're connected to the other endpoint.

        return: the status of the connection.

        """
        return self._connection_event.is_set()

    def wait_for_connection(self, timeout: float | None = None) -> bool:
        """Wait for a limited time until the connection comes up.

        timeout: maximum waiting time in seconds.

        result: the status of the connection.

        """
        return self._connection_event.wait(timeout)

    # TODO: the types here are not precise
    def add_on_connect_handler(self, handler: Callable[[object], Any]):
        """Register a callback for connection establishment.

        handler: a no-args(?) callable that gets notified when
            a new connection has been established.

        """
        self._on_connect_handlers.append(handler)

    def add_on_disconnect_handler(self, handler: Callable[[object], Any]):
        """Register a callback for connection termination.

        handler: a no-args callable that gets notified when
            a connection has been closed.

        """
        self._on_disconnect_handlers.append(handler)

    def _repr_remote(self) -> str:
        """Describe the other end of the connection.

        return: a human-readable sensible identifier for the
            remote address, for use in log messages and exceptions.

        """
        return str(self.remote_address)

    def initialize(self, sock: socket.socket, plus: object):
        """Activate the communication on the given socket.

        Put this class in its "connected" state, setting up all needed
        attributes. Call the on_connect callback.

        sock: the socket acting as low-level communication channel.
        plus: object to pass to the on_connect callbacks.

        """
        if self.connected:
            raise RuntimeError("Already connected.")

        self._socket = sock
        self._reader = self._socket.makefile('rb')
        self._writer = self._socket.makefile('wb')
        self._connection_event.set()
        # IPv4 addresses have two elements (host and port), IPv6 ones
        # have 4 elements (host, port, flowinfo and scopeid). We will
        # discard the last two ones, losing information, to simplify.
        self._local_address = "%s:%d" % self._socket.getsockname()[:2]

        logger.info("Established connection with %s (local address: %s).",
                    self._repr_remote(), self._local_address)

        for handler in self._on_connect_handlers:
            gevent.spawn(handler, plus)

    def finalize(self, reason: str = ""):
        """Deactivate the communication on the current socket.

        Take the class back to the disconnected state and call the
        on_disconnect callback.

        reason: the human-readable reason for closing the
            connection, to be put in log messages and exceptions.

        """
        if not self.connected:
            return

        local_address = self._local_address

        self._socket = None
        self._reader = None
        self._writer = None
        self._local_address = None
        self._connection_event.clear()

        logger.info("Terminated connection with %s (local address: %s): %s",
                    self._repr_remote(), local_address, reason)

        for handler in self._on_disconnect_handlers:
            gevent.spawn(handler)

    def disconnect(self, reason: str = "Disconnection requested.") -> bool:
        """Gracefully close the connection.

        return: True if the service was connected.

        """
        if not self.connected:
            return False

        try:
            self._socket.shutdown(socket.SHUT_RDWR)
            self._socket.close()
        except OSError as error:
            logger.warning("Couldn't disconnect from %s: %s.",
                           self._repr_remote(), error)
        finally:
            self.finalize(reason=reason)
        return True

    def _read(self) -> bytes:
        """Receive a message from the socket.

        Read from the socket until a "\\r\\n" is found. That is what we
        consider a "message" in the communication protocol.

        return: the retrieved message.

        raise (OSError): if reading fails.

        """
        if not self.connected:
            raise OSError("Not connected.")

        try:
            with self._read_lock:
                if not self.connected:
                    raise OSError("Not connected.")
                data = self._reader.readline(self.MAX_MESSAGE_SIZE)
                # If there weren't a "\r\n" between the last message
                # and the EOF we would have a false positive here.
                # Luckily there is one.
                if len(data) > 0 and not data.endswith(b"\r\n"):
                    logger.error(
                        "The client sent a message larger than %d bytes (that "
                        "is MAX_MESSAGE_SIZE). Consider raising that value if "
                        "the message seemed legit.", self.MAX_MESSAGE_SIZE)
                    self.finalize("Client misbehaving.")
                    raise OSError("Message too long.")
        except OSError as error:
            if self.connected:
                logger.warning("Failed reading from socket: %s.", error)
                self.finalize("Read failed.")
                raise error
            else:
                # The client was terminated willingly; its correct termination
                # is handled in disconnect(), so here we can just return.
                return b""

        return data

    def _write(self, data: bytes):
        """Send a message to the socket.

        Automatically append "\\r\\n" to make it a correct message.

        data: the message to transmit.

        raise (OSError): if writing fails.

        """
        if not self.connected:
            raise OSError("Not connected.")

        if len(data + b'\r\n') > self.MAX_MESSAGE_SIZE:
            logger.error(
                "A message wasn't sent to %r because it was larger than %d "
                "bytes (that is MAX_MESSAGE_SIZE). Consider raising that "
                "value if the message seemed legit.", self._repr_remote(),
                self.MAX_MESSAGE_SIZE)
            # No need to call finalize.
            raise OSError("Message too long.")

        try:
            with self._write_lock:
                if not self.connected:
                    raise OSError("Not connected.")
                # Does the same as self._socket.sendall.
                self._writer.write(data + b'\r\n')
                self._writer.flush()
        except OSError as error:
            self.finalize("Write failed.")
            logger.warning("Failed writing to socket: %s.", error)
            raise error


class RemoteServiceServer(RemoteServiceBase):
    """The server side of a RPC communication.

    Considers all messages coming from the other end as requests for
    RPCs executions. Will perform them and send results as responses.

    After having created an instance and initialized it with a socket
    the reader loop should be started by calling run.

    """
    def __init__(self, local_service: "Service", remote_address: Address):
        """Create a responder for the given service.

        local_service: the object whose methods should be
            called via RPC.

        For other arguments see RemoteServiceBase.

        """
        super().__init__(remote_address)
        self.local_service = local_service

        self.pending_incoming_requests_threads = WeakSet()

    def finalize(self, reason=""):
        """See RemoteServiceBase.finalize."""
        super().finalize(reason)

        for thread in self.pending_incoming_requests_threads:
            thread.kill(RPCError(reason), block=False)

        self.pending_incoming_requests_threads.clear()

    def handle(self, socket_: socket.socket):
        self.initialize(socket_, self.remote_address)
        self.run()

    def run(self):
        """Start listening for requests, and go on forever.

        Read messages from the socket and issue greenlets to parse
        them, execute methods and send the response to the client.
        This method won't return as long as there's something to read,
        it's therefore advisable to spawn a greenlet to call it.

        """
        while True:
            try:
                data = self._read()
            except OSError:
                break

            if len(data) == 0:
                self.finalize("Connection closed.")
                break

            gevent.spawn(self.process_data, data)

    def process_data(self, data: bytes):
        """Handle the message.

        JSON-decode it and forward it to process_incoming_request
        (unconditionally!).

        data: the message read from the socket.

        """
        # Decode the incoming data.
        try:
            message = json.loads(data.decode('utf-8'))
        except ValueError:
            self.disconnect("Bad request received")
            logger.warning("Cannot parse incoming message, discarding.")
            return

        self.process_incoming_request(message)

    def process_incoming_request(self, request: dict):
        """Handle the request.

        Parse the request, execute the method it asks for, format the
        result and send the response.

        request: the JSON-decoded request.

        """
        # Validate the request (json.loads() may give any JSON value,
        # not only an object).
        if (not isinstance(request, dict)
                or not {"__id", "__method", "__data"}.issubset(request)):
            self.disconnect("Bad request received")
            logger.warning("Request is missing some fields, ignoring.")
            return

        # Determine the ID.
        id_ = request["__id"]

        # Store the request.
        self.pending_incoming_requests_threads.add(gevent.getcurrent())

        # Build the response.
        response = {"__id": id_,
                    "__data": None,
                    "__error": None}

        method_name = request["__method"]

        if not hasattr(self.local_service, method_name):
            response["__error"] = "Method %s doesn't exist." % method_name
        else:
            method = getattr(self.local_service, method_name)

            if not getattr(method, "rpc_callable", False):
                response["__error"] = "Method %s isn't callable." % method_name
            else:
                try:
                    response["__data"] = method(**request["__data"])
                except Exception as error:
                    response["__error"] = "%s: %s\n%s" % \
                        (error.__class__.__name__, error,
                         traceback.format_exc())

        # Encode it.
        try:
            data = json.dumps(response).encode('utf-8')
        except (TypeError, ValueError):
            logger.warning("JSON encoding failed.", exc_info=True)
            return

        # Send it.
        try:
            self._write(data)
        except OSError:
            # Log messages have already been produced.
            return


class RemoteServiceClient(RemoteServiceBase):
    """The client side of a RPC communication.

    Considers all messages coming from the other end as responses for
    RPCs previously sent. Will parse them and forward them to the
    original callers.

    It also offers an interface to issue RPCs, with execute_rpc and
    some syntactic sugar.

    After having created an instance and initialized it with a socket
    the reader loop should be started by calling run.

    """
    def __init__(
        self, remote_service_coord: ServiceCoord, auto_retry: float | None = None
    ):
        """Create a caller for the service at the given coords.

        remote_service_coord: the coordinates (i.e. name
            and shard) of the service to which to send RPC requests.
        auto_retry: if a number is given then it's the
            interval (in seconds) between attempts to reconnect to the
            remote service in case the connection is lost; if not given
            no automatic reconnection attempts will occur.

        raise (KeyError): if the coordinates are not specified in the
            configuration.

        """
        super().__init__(get_service_address(remote_service_coord))
        self.remote_service_coord = remote_service_coord

        self.pending_outgoing_requests = dict()
        self.pending_outgoing_requests_results = dict()

        self.auto_retry = auto_retry

        self._loop = None

        # The failures to resolve the peer's name, and the malformed
        # answers: each comes back at every retry while it lasts, so
        # they are logged a series at a time.
        self._resolve_failures = FailureSeriesLog(logger)
        self._bad_answers = FailureSeriesLog(logger)

    def _repr_remote(self):
        """See RemoteServiceBase._repr_remote."""
        return f"{self.remote_address} ({self.remote_service_coord})"

    def finalize(self, reason=""):
        """See RemoteServiceBase.finalize."""
        super().finalize(reason)

        for result in self.pending_outgoing_requests_results.values():
            result.set_exception(RPCError(reason))

        self.pending_outgoing_requests.clear()
        self.pending_outgoing_requests_results.clear()

    def _connect(self):
        """Establish a connection and initialize that socket.

        """
        try:
            # Try to resolve the address, this can lead to many possible
            # addresses, we'll try all of them.
            addresses = gevent.socket.getaddrinfo(
                self.remote_address.ip,
                self.remote_address.port,
                type=socket.SOCK_STREAM)
        except OSError as error:
            # Not connected, as when the connection is refused: _run
            # tries again after auto_retry seconds, if it has to.
            if self._resolve_failures.failing:
                self._resolve_failures.failed(
                    "Still cannot resolve %s: %s.", self.remote_address,
                    error)
            else:
                self._resolve_failures.failed(
                    "Cannot resolve %s.", self.remote_address)
            return
        self._resolve_failures.succeeded(
            "%s resolves again.", self.remote_address)

        for family, type, proto, _canonname, sockaddr in addresses:
            try:
                host, port, *rest = sockaddr
                logger.debug("Trying to connect to %s at port %d.", host, port)
                sock = gevent.socket.socket(family, type, proto)
                sock.connect(sockaddr)
            except OSError as error:
                logger.debug("Couldn't connect to %s at %s port %d: %s.",
                             self._repr_remote(), host, port, error)
            else:
                self.initialize(sock, self.remote_service_coord)
                break

    def _run(self):
        """Maintain the connection up, if required.

        """
        while True:
            self._connect()
            while not self.connected and self.auto_retry is not None:
                gevent.sleep(self.auto_retry)
                self._connect()
            if self.connected:
                self.run()
            if self.auto_retry is None:
                break
            # However the connection ended, wait before the next one: a
            # peer that accepts and then closes at once would otherwise
            # be reconnected to as fast as the loop can go.
            gevent.sleep(self.auto_retry)

    def connect(self):
        """Connect and start the main loop.

        """
        if self._loop is not None and not self._loop.ready():
            raise RuntimeError("Already (auto-re)connecting")
        self._loop = gevent.spawn(self._run)

    def disconnect(self, reason="Disconnection requested."):
        """See RemoteServiceBase.disconnect."""
        if super().disconnect(reason=reason):
            self._loop.kill()
            self._loop = None

    def _drop_connection(self, reason: str):
        """Close the connection, but not for good.

        Unlike disconnect(), it leaves the connect loop running: run()
        then reads the end of the connection, and _run reconnects
        after auto_retry seconds, if it has to.

        reason: why, for the log.

        """
        super().disconnect(reason=reason)

    def run(self):
        """Start listening for responses, and go on forever.

        Read messages from the socket and process each one (parse it,
        determine the request it's for and fill the result) before
        reading the next. This method won't return as long as there's
        something to read, it's therefore advisable to spawn a greenlet
        to call it.

        """
        while True:
            try:
                data = self._read()
            except OSError:
                break

            if len(data) == 0:
                self.finalize("Connection closed.")
                break

            # Not in a greenlet of its own, unlike the server's requests
            # (an answer only fills a result, with no I/O but logs): an
            # answer read just before the end of the connection is then
            # delivered before finalize() fails the calls still pending,
            # even if finalize() yields; and once a malformed answer has
            # dropped the connection, the next _read() raises "Not
            # connected." and run() stops, so no later line of that
            # connection is processed.
            try:
                self.process_data(data)
            except Exception:
                # Nor may anything else going wrong with an answer end
                # the connect loop (as a greenlet of its own would have
                # contained it): it is logged, and the connection
                # dropped as for a malformed answer.
                self._drop_connection("Bad response received")
                self._bad_answers.failed(
                    "Cannot process the answer from %s, discarding it.",
                    self._repr_remote(), exc_info=True)

    def process_data(self, data: bytes):
        """Handle the message.

        JSON-decode it and forward it to process_incoming_response
        (unconditionally!).

        data: the message read from the socket.

        """
        # Decode the incoming data.
        try:
            message = json.loads(data.decode('utf-8'))
        except (ValueError, RecursionError):
            # RecursionError: JSON nested too deep to decode.
            self._drop_connection("Bad response received")
            self._bad_answers.failed(
                "Cannot parse incoming message from %s, discarding.",
                self._repr_remote())
            return

        self.process_incoming_response(message)

    def process_incoming_response(self, response: dict):
        """Handle the response.

        Parse the response, determine the request it's for and its
        associated result and fill it.

        response: the JSON-decoded response.

        """
        # Validate the response (json.loads() may give any JSON value,
        # not only an object).
        if (not isinstance(response, dict)
                or not {"__id", "__data", "__error"}.issubset(response)):
            self._drop_connection("Bad response received")
            self._bad_answers.failed(
                "Response from %s is missing some fields, ignoring.",
                self._repr_remote())
            return
        if not isinstance(response["__id"], str):
            # Our requests' ids are strings: any other id (a list can't
            # even be looked up) can't be the answer to one of them.
            self._drop_connection("Bad response received")
            self._bad_answers.failed(
                "Response from %s has an id that is not a string, "
                "ignoring.", self._repr_remote())
            return
        if self._bad_answers.failing:
            self._bad_answers.succeeded(
                "%s sends well-formed answers again.", self._repr_remote())

        # Determine the ID.
        id_ = response["__id"]

        if id_ not in self.pending_outgoing_requests:
            logger.warning("No pending request with id %s found.", id_)
            return

        request = self.pending_outgoing_requests.pop(id_)
        result = self.pending_outgoing_requests_results.pop(id_)
        error = response["__error"]

        if error is not None:
            err_msg = "%s signaled RPC for method %s was unsuccessful: %s." % (
                self.remote_service_coord, request["__method"], error)
            logger.error(err_msg)
            result.set_exception(RPCError(error))
        else:
            result.set(response["__data"])

    def execute_rpc(self, method: str, data: dict) -> gevent.event.AsyncResult:
        """Send an RPC request to the remote service.

        method: the name of the method to call.
        data: keyword arguments to pass to the methods.

        return: an object that holds (or will hold) the
            result of the call, either the value or the error that
            prevented successful completion.

        """
        # Determine the ID.
        id_ = uuid.uuid4().hex

        # Build the request.
        request = {"__id": id_,
                   "__method": method,
                   "__data": data}

        result = gevent.event.AsyncResult()

        # Encode it.
        try:
            data_encoded = json.dumps(request).encode("utf-8")
        except (TypeError, ValueError):
            logger.error("JSON encoding failed.", exc_info=True)
            result.set_exception(RPCError("JSON encoding failed."))
            return result

        # Send it.
        try:
            self._write(data_encoded)
        except OSError:
            result.set_exception(RPCError("Write failed."))
            return result

        # Store it.
        self.pending_outgoing_requests[id_] = request
        self.pending_outgoing_requests_results[id_] = result

        return result

    def __getattr__(self, method: str):
        """Syntactic sugar to enable a transparent proxy.

        All unresolved attributes on this object are interpreted as
        methods of the remote service and therefore a wrapper function
        is returned that will call execute_rpc with the proper args.

        As an additional comfort, one can also insert a "callback" and
        (optionally) a "plus" item among the keywords arguments in the
        call to the returned function to be notified when the RPC ends.
        The callback should be a callable able to receive the data and
        (optionally) the plus object as positional args and the error
        as a keyword arg. It will be run in a dedicated greenlet.

        method: the name of the accessed method.
        return (function): a proxy to a RPC.

        """

        def run_callback(
            func: Callable, plus: object, result: gevent.event.AsyncResult
        ):
            """Execute the given callback safely.

            Get data and/or error from result and call func passing it
            data, plus (if needed) and error. Catch, log and suppress
            all exceptions.

            func: the callback to invoke.
            plus: optional additional data.
            result: the result of a (finished) RPC call.

            """
            data = result.value
            error = None if result.successful() else "%s" % result.exception
            try:
                if plus is None:
                    func(data, error=error)
                else:
                    func(data, plus, error=error)
            except Exception as error:
                logger.error("RPC callback for %s.%s raised exception.",
                             self.remote_service_coord.name, method,
                             exc_info=True)

        def remote_method(**data):
            """Forward arguments to execute_rpc.

            """
            callback = data.pop("callback", None)
            plus = data.pop("plus", None)
            result = self.execute_rpc(method=method, data=data)
            if callback is not None:
                callback = functools.partial(run_callback, callback, plus)
                result.rawlink(functools.partial(gevent.spawn, callback))
            return result

        return remote_method


class FakeRemoteServiceClient(RemoteServiceClient):
    """A RemoteServiceClient not actually connected to anything.

    This is useful for connections to optional services, to avoid
    having to specify different behaviors in the case the services are
    or are not available.

    In all aspects, an object of this class behaves exactly like a
    RemoteServiceClient that will never be able to connect.

    """
    def __init__(self, remote_service_coord: ServiceCoord, auto_retry=None):
        """Initialization.

        This constructor does not call the parent constructor, because
        it would fail (as the service coord are not in the
        configuration). This is potentially a problem, but as this
        client will never connect not many member variable access are
        performed.

        """
        RemoteServiceBase.__init__(self, Address("None", 0))
        self.remote_service_coord = remote_service_coord
        self.pending_outgoing_requests = dict()
        self.pending_outgoing_requests_results = dict()
        self.auto_retry = auto_retry

    def connect(self):
        """Do nothing, as this is a fake client."""
        pass

    def disconnect(self):
        """Do nothing, as this is a fake client."""
        return True

    def execute_rpc(self, method, data):
        """Just return an AsyncResult encoding an error."""
        result = gevent.event.AsyncResult()
        result.set_exception(
            RPCError("Called a method of a non-configured service."))
        return result
