#!/usr/bin/env python3

"""Asyncio-based RPC communication, wire-compatible with cms.io.rpc.

This module mirrors cms/io/rpc.py's class structure and external
behavior exactly, translating gevent's concurrency primitives to
asyncio's per the plan's mechanical mapping table. The wire protocol
(JSON messages delimited by \\r\\n over TCP) is identical to rpc.py's,
so an AsyncRemoteServiceClient/Server can talk to an old, unmigrated
cms.io.rpc.RemoteServiceServer/Client with no adapter.

"""

import asyncio
from collections.abc import Callable
from datetime import datetime
import json
import logging
import socket
from time import monotonic
import traceback
import typing
import uuid
from weakref import WeakSet

from cms.conf import Address, ServiceCoord
from cms.util import get_service_address
from cms.io.rpc import FailureSeriesLog, RPCError, \
    ServiceNotConfiguredError

if typing.TYPE_CHECKING:
    from cms.io.async_service import AsyncService


logger = logging.getLogger(__name__)


# Incoming messages larger than this are dropped to avoid DOS attacks.
# Shared by AsyncRemoteServiceBase and by AsyncService's asyncio.start_server
# call (via the `limit=` kwarg), so there's exactly one source of truth for
# this value across both files.
MAX_MESSAGE_SIZE = 1024 * 1024

# How many seconds a fire-and-forget RPC (one nobody waits on for real
# work) waits for its answer before its caller gives up on it. Against a
# peer that is connected but stuck, nothing else would ever end the
# call: it would stay pending, together with its request, for good.
FIRE_AND_FORGET_TIMEOUT = 30.0


class NotificationFailureLatch:
    """Log the failed notifications to one peer without flooding the logs.

    It is for a service that tells a peer about each piece of work it
    has done, fire-and-forget, and drops the notifications that fail
    because the peer's sweeper finds that work anyway (EvaluationService
    with ScoringService, ScoringService with ProxyService). Logging
    every dropped notification would flood the logs for as long as the
    peer is down or stuck, and logging none hides that it is. So it
    logs:
    - the first failure as a WARNING, naming both ends, why it failed
      and what that costs;
    - while the failures last, a reminder WARNING, with how many
      notifications were dropped since when;
    - the first success after them as an INFO, with that count;
    - each failure as a DEBUG line saying what it was about.
    A WARNING is logged at most once every WARNING_INTERVAL seconds,
    across outages too, so that a peer that flaps between failing and
    not can't flood the logs either: an outage that starts sooner logs
    only DEBUG until the interval is over, and then a reminder. Its end
    is an INFO only if it logged a WARNING, so that each WARNING has
    its INFO, and DEBUG otherwise.
    A peer that is not configured (ServiceNotConfiguredError) is not
    failing: that is only a DEBUG line, and it is not counted.

    schedule_rpc (cms.server.util) doesn't use it, and keeps logging
    every failure: the admin actions it notifies about are rare.

    """

    WARNING_INTERVAL = 60.0

    def __init__(
        self, log: logging.Logger, source: str, peer: ServiceCoord,
        consequence: str,
    ):
        """Prepare to log the notifications from source to peer.

        log: the logger to log to (the caller's).
        source: the name of the service that sends the notifications.
        peer: the service that receives them.
        consequence: what the failures cost, which ends the WARNINGs
            (e.g. "nothing is lost, its sweeper will score these
            submissions").

        """
        self._log = log
        self._source = source
        self._peer = peer
        self._consequence = consequence
        # How many notifications were dropped since the last success
        # (or the start), since when, and whether that outage logged a
        # WARNING; and when the last WARNING was (in monotonic()
        # seconds), whatever the outage.
        self._dropped = 0
        self._failing_since: datetime | None = None
        self._warned = False
        self._last_warning: float | None = None

    def succeeded(self):
        """Note that a notification reached the peer."""
        if self._dropped == 0:
            return
        # stacklevel=2, here and in failed(): the records point at the
        # caller, the method that sent the notification, as the
        # detailed log shows it.
        self._log.log(logging.INFO if self._warned else logging.DEBUG,
                      "%s can notify %s again: %d notification(s) dropped "
                      "since %s.", self._source, self._peer, self._dropped,
                      self._failing_since.strftime("%Y-%m-%d %H:%M:%S"),
                      stacklevel=2)
        self._dropped = 0
        self._failing_since = None
        self._warned = False

    def failed(self, notification: str, error: Exception):
        """Note that a notification did not reach the peer.

        notification: what it was about, for the DEBUG line (e.g. "the
            evaluation of submission 1 on dataset 2").
        error: why: the RPCError its call raised, or the TimeoutError
            of giving up on the answer.

        """
        if isinstance(error, ServiceNotConfiguredError):
            self._log.debug("%s did not notify %s of %s: it is not "
                            "configured.", self._source, self._peer,
                            notification, stacklevel=2)
            return

        if isinstance(error, TimeoutError):
            reason = "no answer in time"
        else:
            reason = "%s: %s" % (error.__class__.__name__, error)
        self._log.debug("%s did not notify %s of %s (%s).", self._source,
                        self._peer, notification, reason, stacklevel=2)
        # A remote error carries the peer's traceback: the DEBUG line
        # has it all, the WARNINGs only the first line, so they stay one
        # line each (the peer's error is logged at ERROR anyway).
        reason = reason.partition("\n")[0]

        now = monotonic()
        self._dropped += 1
        if self._dropped == 1:
            self._failing_since = datetime.now()
        if (self._last_warning is not None
                and now - self._last_warning < self.WARNING_INTERVAL):
            return
        self._last_warning = now
        self._warned = True
        if self._dropped == 1:
            self._log.warning("%s cannot notify %s (%s); %s.", self._source,
                              self._peer, reason, self._consequence,
                              stacklevel=2)
        else:
            self._log.warning(
                "%s still cannot notify %s (%s): %d notification(s) "
                "dropped since %s; %s.", self._source, self._peer, reason,
                self._dropped,
                self._failing_since.strftime("%Y-%m-%d %H:%M:%S"),
                self._consequence, stacklevel=2)


class AsyncRemoteServiceBase:
    """Base class for both ends of an asyncio RPC connection.

    See cms.io.rpc.RemoteServiceBase for the connected/disconnected
    state machine this mirrors exactly.

    """
    MAX_MESSAGE_SIZE = MAX_MESSAGE_SIZE

    def __init__(self, remote_address: Address):
        self._local_address = None
        self.remote_address = remote_address
        self._connection_event = asyncio.Event()

        self._on_connect_handlers: list[Callable[[object], typing.Any]] = list()
        self._on_disconnect_handlers: list[Callable[[object], typing.Any]] = list()

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

        self._read_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

        # Keep references to fire-and-forget tasks so they aren't
        # garbage-collected mid-run (mirrors rpc.py's pattern of never
        # awaiting spawned handler/process tasks directly).
        self._background_tasks: set[asyncio.Task] = set()

    def _spawn(self, coro: typing.Coroutine) -> asyncio.Task:
        """Fire-and-forget a coroutine, keeping a strong reference."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    @property
    def connected(self) -> bool:
        return self._connection_event.is_set()

    async def wait_for_connection(self, timeout: float | None = None) -> bool:
        if timeout is None:
            await self._connection_event.wait()
            return True
        try:
            await asyncio.wait_for(self._connection_event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def add_on_connect_handler(self, handler: Callable[[object], typing.Any]):
        self._on_connect_handlers.append(handler)

    def add_on_disconnect_handler(self, handler: Callable[[object], typing.Any]):
        self._on_disconnect_handlers.append(handler)

    def _repr_remote(self) -> str:
        return str(self.remote_address)

    def initialize_streams(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        plus: object,
    ):
        """Activate the communication on the given stream pair.

        Equivalent to cms.io.rpc.RemoteServiceBase.initialize, but
        takes an already-connected (StreamReader, StreamWriter) pair
        instead of a raw socket -- both asyncio.start_server's handler
        and asyncio.open_connection() hand you streams directly, there
        is no socket to construct makefile() wrappers from.

        """
        if self.connected:
            raise RuntimeError("Already connected.")

        self._reader = reader
        self._writer = writer
        self._connection_event.set()
        sockname = writer.get_extra_info("sockname")
        self._local_address = "%s:%d" % (sockname[0], sockname[1])

        logger.info("Established connection with %s (local address: %s).",
                    self._repr_remote(), self._local_address)

        for handler in self._on_connect_handlers:
            self._spawn(self._call_handler(handler, plus))

    async def _call_handler(self, handler: Callable, plus: object):
        try:
            result = handler(plus)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.error("Connect/disconnect handler raised.", exc_info=True)

    def finalize(self, reason: str = ""):
        if not self.connected:
            return

        local_address = self._local_address

        # Ensure the transport is closed even on the passive-disconnect
        # path (finalize() called directly after the remote end closed
        # the connection, without disconnect() having closed our writer
        # first). writer.close() is idempotent, so this is safe to call
        # even when disconnect() already closed it.
        try:
            self._writer.close()
        except OSError as error:
            logger.warning("Couldn't close connection to %s: %s.",
                           self._repr_remote(), error)

        self._reader = None
        self._writer = None
        self._local_address = None
        self._connection_event.clear()

        logger.info("Terminated connection with %s (local address: %s): %s",
                    self._repr_remote(), local_address, reason)

        for handler in self._on_disconnect_handlers:
            self._spawn(self._call_handler(handler, None))

    def disconnect(self, reason: str = "Disconnection requested.") -> bool:
        if not self.connected:
            return False

        writer = self._writer
        try:
            writer.close()
        except OSError as error:
            logger.warning("Couldn't disconnect from %s: %s.",
                           self._repr_remote(), error)
        finally:
            self.finalize(reason=reason)
        return True

    async def _read(self) -> bytes:
        if not self.connected:
            raise OSError("Not connected.")

        try:
            async with self._read_lock:
                if not self.connected:
                    raise OSError("Not connected.")
                try:
                    data = await self._reader.readuntil(b"\r\n")
                except asyncio.IncompleteReadError as error:
                    # Connection closed before a full message arrived;
                    # mirrors rpc.py's "EOF with no trailing \r\n" case.
                    if len(error.partial) == 0:
                        return b""
                    raise OSError("Connection closed mid-message.") from error
                except asyncio.LimitOverrunError as error:
                    logger.error(
                        "The client sent a message larger than %d bytes "
                        "(that is MAX_MESSAGE_SIZE). Consider raising "
                        "that value if the message seemed legit.",
                        self.MAX_MESSAGE_SIZE)
                    self.finalize("Client misbehaving.")
                    raise OSError("Message too long.") from error
        except OSError as error:
            if self.connected:
                logger.warning("Failed reading from socket: %s.", error)
                self.finalize("Read failed.")
                raise error
            else:
                return b""

        return data

    async def _write(self, data: bytes):
        if not self.connected:
            raise OSError("Not connected.")

        if len(data + b'\r\n') > self.MAX_MESSAGE_SIZE:
            logger.error(
                "A message wasn't sent to %r because it was larger than "
                "%d bytes (that is MAX_MESSAGE_SIZE). Consider raising "
                "that value if the message seemed legit.",
                self._repr_remote(), self.MAX_MESSAGE_SIZE)
            raise OSError("Message too long.")

        try:
            async with self._write_lock:
                if not self.connected:
                    raise OSError("Not connected.")
                self._writer.write(data + b'\r\n')
                await self._writer.drain()
        except OSError as error:
            self.finalize("Write failed.")
            logger.warning("Failed writing to socket: %s.", error)
            raise error


class AsyncRemoteServiceServer(AsyncRemoteServiceBase):
    """The server side of an asyncio RPC communication."""

    def __init__(self, local_service: "AsyncService", remote_address: Address):
        super().__init__(remote_address)
        self.local_service = local_service
        self.pending_incoming_requests_tasks: WeakSet[asyncio.Task] = WeakSet()

    def finalize(self, reason=""):
        super().finalize(reason)
        for task in self.pending_incoming_requests_tasks:
            task.cancel()
        self.pending_incoming_requests_tasks.clear()

    async def run(self):
        """Read messages and dispatch a task per message, forever."""
        while True:
            try:
                data = await self._read()
            except OSError:
                break

            if len(data) == 0:
                self.finalize("Connection closed.")
                break

            task = self._spawn(self.process_data(data))
            self.pending_incoming_requests_tasks.add(task)

    async def process_data(self, data: bytes):
        try:
            message = json.loads(data.decode('utf-8'))
        except ValueError:
            self.disconnect("Bad request received")
            logger.warning("Cannot parse incoming message, discarding.")
            return

        await self.process_incoming_request(message)

    async def process_incoming_request(self, request: dict):
        # json.loads() may give any JSON value, not only an object.
        if (not isinstance(request, dict)
                or not {"__id", "__method", "__data"}.issubset(request)):
            self.disconnect("Bad request received")
            logger.warning("Request is missing some fields, ignoring.")
            return

        id_ = request["__id"]

        response = {"__id": id_, "__data": None, "__error": None}

        method_name = request["__method"]

        if not hasattr(self.local_service, method_name):
            response["__error"] = "Method %s doesn't exist." % method_name
        else:
            method = getattr(self.local_service, method_name)

            if not getattr(method, "rpc_callable", False):
                response["__error"] = "Method %s isn't callable." % method_name
            else:
                try:
                    result = method(**request["__data"])
                    if asyncio.iscoroutine(result):
                        result = await result
                    response["__data"] = result
                except Exception as error:
                    response["__error"] = "%s: %s\n%s" % \
                        (error.__class__.__name__, error,
                         traceback.format_exc())

        try:
            data = json.dumps(response).encode('utf-8')
        except (TypeError, ValueError):
            logger.warning("JSON encoding failed.", exc_info=True)
            return

        try:
            await self._write(data)
        except OSError:
            return


class AsyncRemoteServiceClient(AsyncRemoteServiceBase):
    """The client side of an asyncio RPC communication."""

    def __init__(
        self, remote_service_coord: ServiceCoord, auto_retry: float | None = None
    ):
        super().__init__(get_service_address(remote_service_coord))
        self.remote_service_coord = remote_service_coord

        self.pending_outgoing_requests: dict[str, dict] = dict()
        self.pending_outgoing_requests_results: dict[str, asyncio.Future] = dict()

        self.auto_retry = auto_retry

        self._loop_task: asyncio.Task | None = None

        # The failures to resolve the peer's name, and the malformed
        # answers: each comes back at every retry while it lasts, so
        # they are logged a series at a time.
        self._resolve_failures = FailureSeriesLog(logger)
        self._bad_answers = FailureSeriesLog(logger)

    def _repr_remote(self):
        return f"{self.remote_address} ({self.remote_service_coord})"

    def finalize(self, reason=""):
        super().finalize(reason)
        for result in self.pending_outgoing_requests_results.values():
            if not result.done():
                result.set_exception(RPCError(reason))
        self.pending_outgoing_requests.clear()
        self.pending_outgoing_requests_results.clear()

    async def _connect(self):
        try:
            addresses = await asyncio.get_running_loop().getaddrinfo(
                self.remote_address.ip, self.remote_address.port,
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

        for family, type_, proto, _canonname, sockaddr in addresses:
            host, port, *_rest = sockaddr
            try:
                logger.debug("Trying to connect to %s at port %d.", host, port)
                reader, writer = await asyncio.open_connection(
                    host, port, limit=self.MAX_MESSAGE_SIZE)
            except OSError as error:
                logger.debug("Couldn't connect to %s at %s port %d: %s.",
                             self._repr_remote(), host, port, error)
            else:
                self.initialize_streams(reader, writer, self.remote_service_coord)
                break

    async def _run(self):
        while True:
            await self._connect()
            while not self.connected and self.auto_retry is not None:
                await asyncio.sleep(self.auto_retry)
                await self._connect()
            if self.connected:
                await self.run()
            if self.auto_retry is None:
                break
            # However the connection ended, wait before the next one: a
            # peer that accepts and then closes at once would otherwise
            # be reconnected to as fast as the loop can go.
            await asyncio.sleep(self.auto_retry)

    def connect(self):
        if self._loop_task is not None and not self._loop_task.done():
            raise RuntimeError("Already (auto-re)connecting")
        self._loop_task = asyncio.create_task(self._run())

    def disconnect(self, reason="Disconnection requested."):
        if super().disconnect(reason=reason):
            # _loop_task is only set once connect() has been called;
            # a client wired up via initialize_streams() directly
            # (bypassing connect()'s auto-connect/auto-retry loop, as
            # in the test suite) has none to cancel.
            if self._loop_task is not None:
                self._loop_task.cancel()
                self._loop_task = None

    def _drop_connection(self, reason: str):
        """Close the connection, but not for good.

        Unlike disconnect(), it leaves the connect loop running: run()
        then reads the end of the connection, and _run reconnects
        after auto_retry seconds, if it has to.

        reason: why, for the log.

        """
        if self._writer is not None:
            # Unlike close(), abort() ends the connection even with
            # unsent data buffered (a peer that doesn't read), so run()
            # is sure to read its end, as shutdown() does in rpc.py.
            self._writer.transport.abort()
        super().disconnect(reason=reason)

    async def run(self):
        # The reader of this connection, which tells process_data
        # whether a line still comes from the current connection.
        reader = self._reader
        while True:
            try:
                data = await self._read()
            except OSError:
                break

            if len(data) == 0:
                self.finalize("Connection closed.")
                break

            self._spawn(self.process_data(data, reader))

    async def process_data(
        self, data: bytes, reader: asyncio.StreamReader | None
    ):
        # run() reads every line already buffered before any is
        # processed. Once the connection has ended (because one of them
        # was a malformed answer, say), the others are discarded,
        # whatever they are, instead of being taken as answers; and
        # run() reads no more, as _read() returns nothing once we're
        # disconnected.
        if reader is not self._reader:
            return

        try:
            message = json.loads(data.decode('utf-8'))
        except ValueError:
            self._drop_connection("Bad response received")
            self._bad_answers.failed(
                "Cannot parse incoming message from %s, discarding.",
                self._repr_remote())
            return

        self.process_incoming_response(message)

    def process_incoming_response(self, response: dict):
        # json.loads() may give any JSON value, not only an object.
        if (not isinstance(response, dict)
                or not {"__id", "__data", "__error"}.issubset(response)):
            self._drop_connection("Bad response received")
            self._bad_answers.failed(
                "Response from %s is missing some fields, ignoring.",
                self._repr_remote())
            return
        if self._bad_answers.failing:
            self._bad_answers.succeeded(
                "%s sends well-formed answers again.", self._repr_remote())

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
            if not result.done():
                result.set_exception(RPCError(error))
        else:
            if not result.done():
                result.set_result(response["__data"])

    async def execute_rpc(self, method: str, data: dict) -> typing.Any:
        """Send an RPC request and await its result.

        Unlike rpc.py's execute_rpc (which returns a not-yet-resolved
        AsyncResult immediately), this is a coroutine: awaiting it
        blocks until the response arrives, returning the value or
        raising RPCError. This is the natural async equivalent -- a
        caller that wants fire-and-forget behavior can wrap the call
        in asyncio.create_task() itself.

        Whatever happens to the connection meanwhile, the call fails
        with RPCError. Cancelling it (e.g. with asyncio.wait_for)
        gives up on the answer: the request stops being pending and a
        late answer is logged and ignored.

        """
        id_ = uuid.uuid4().hex
        request = {"__id": id_, "__method": method, "__data": data}

        loop = asyncio.get_running_loop()
        result: asyncio.Future = loop.create_future()

        try:
            data_encoded = json.dumps(request).encode("utf-8")
        except (TypeError, ValueError):
            logger.error("JSON encoding failed.", exc_info=True)
            raise RPCError("JSON encoding failed.")

        self.pending_outgoing_requests[id_] = request
        self.pending_outgoing_requests_results[id_] = result

        try:
            try:
                await self._write(data_encoded)
            except OSError:
                raise RPCError("Write failed.")
            return await result
        finally:
            # However this ends, the request is no longer pending. The
            # entries may already be gone (finalize() empties both
            # dicts when the connection drops) or still be there (the
            # caller was cancelled, e.g. by a timeout): pop() copes
            # with both.
            self.pending_outgoing_requests.pop(id_, None)
            self.pending_outgoing_requests_results.pop(id_, None)
            # finalize() may have failed the future while the request
            # was still being written, when nothing awaits it yet.
            # Retrieve its exception, or asyncio reports it as lost.
            if result.done() and not result.cancelled():
                result.exception()

    def __getattr__(self, method: str):
        """Syntactic sugar: unresolved attributes become RPC proxies.

        Unlike rpc.py's version, there is no callback/plus kwarg
        support here -- callers use `await client.method(...)`
        directly (a coroutine) instead of a callback, since asyncio
        code is already inside an event loop and can simply await.
        A caller that wants the old fire-and-forget-with-callback
        style can do
        `asyncio.create_task(client.method(...)).add_done_callback(cb)`
        itself; see Review Focus item 1 for why this simpler contract
        is safe to choose here (no existing async caller needs the
        callback style yet -- Task 5's LogService doesn't call any
        remote method via this proxy at all).

        """
        async def remote_method(**data):
            return await self.execute_rpc(method=method, data=data)

        # Make the closure identify the real RPC (e.g. in logs).
        remote_method.__name__ = method
        remote_method.__qualname__ = "%s.%s" % (
            self.remote_service_coord, method)
        return remote_method


class AsyncFakeRemoteServiceClient(AsyncRemoteServiceClient):
    """An AsyncRemoteServiceClient not actually connected to anything."""

    def __init__(self, remote_service_coord: ServiceCoord, auto_retry=None):
        AsyncRemoteServiceBase.__init__(self, Address("None", 0))
        self.remote_service_coord = remote_service_coord
        self.pending_outgoing_requests = dict()
        self.pending_outgoing_requests_results = dict()
        self.auto_retry = auto_retry

    def connect(self):
        pass

    def disconnect(self):
        return True

    async def execute_rpc(self, method, data):
        raise ServiceNotConfiguredError(
            "Called a method of a non-configured service.")
