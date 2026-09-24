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
import json
import logging
import socket
import traceback
import typing
import uuid
from weakref import WeakSet

from cms.conf import Address, ServiceCoord
from cms.util import get_service_address
from cms.io.rpc import RPCError

if typing.TYPE_CHECKING:
    from cms.io.async_service import AsyncService


logger = logging.getLogger(__name__)


# Incoming messages larger than this are dropped to avoid DOS attacks.
# Shared by AsyncRemoteServiceBase and by AsyncService's asyncio.start_server
# call (via the `limit=` kwarg), so there's exactly one source of truth for
# this value across both files.
MAX_MESSAGE_SIZE = 1024 * 1024


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
        if not {"__id", "__method", "__data"}.issubset(request.keys()):
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
        except OSError:
            logger.warning("Cannot resolve %s.", self.remote_address)
            raise

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

    async def run(self):
        while True:
            try:
                data = await self._read()
            except OSError:
                break

            if len(data) == 0:
                self.finalize("Connection closed.")
                break

            self._spawn(self.process_data(data))

    async def process_data(self, data: bytes):
        try:
            message = json.loads(data.decode('utf-8'))
        except ValueError:
            self.disconnect("Bad response received")
            logger.warning("Cannot parse incoming message, discarding.")
            return

        self.process_incoming_response(message)

    def process_incoming_response(self, response: dict):
        if not {"__id", "__data", "__error"}.issubset(response.keys()):
            self.disconnect("Bad response received")
            logger.warning("Response is missing some fields, ignoring.")
            return

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
            await self._write(data_encoded)
        except OSError:
            del self.pending_outgoing_requests[id_]
            del self.pending_outgoing_requests_results[id_]
            raise RPCError("Write failed.")

        return await result

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
        raise RPCError("Called a method of a non-configured service.")
