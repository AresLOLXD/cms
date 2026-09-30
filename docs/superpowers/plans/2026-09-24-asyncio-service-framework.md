# cms/io/ gevent → asyncio Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new, parallel `asyncio`-based service/RPC framework in `cms/io/` (`AsyncService`, async RPC client/server, `AsyncTriggeredService`, `AsyncPriorityQueue`), wire-protocol-compatible with the existing gevent one, and prove it works end-to-end by migrating one real service (`LogService`) to it — without touching or breaking any existing gevent-based service.

**Architecture:** Every new class mirrors an existing gevent class one-to-one, translating gevent's concurrency primitives to asyncio's via a fixed mapping table (no improvisation). The existing gevent files (`service.py`, `rpc.py`, `triggeredservice.py`, `priorityqueue.py`) are never modified — new files sit alongside them. The RPC wire protocol (JSON messages delimited by `\r\n` over TCP) doesn't depend on either concurrency model, which is what makes this coexistence safe; a dedicated cross-interoperability test proves it.

**Tech Stack:** Python 3.12, `asyncio` (stdlib), pytest, Docker (`docker-compose.test.yml`).

**Spec:** `docs/superpowers/specs/2026-09-24-asyncio-service-framework-design.md`

## Global Constraints

- Never modify `cms/io/service.py`, `cms/io/rpc.py`, `cms/io/triggeredservice.py`, or `cms/io/priorityqueue.py`. Every new class lives in a new, separate file. This is not a style preference — `PriorityQueue.pop(wait=True)` blocks synchronously on `gevent.event.Event.wait()`, which three live services (`EvaluationService`, `ScoringService`, `ProxyService`) depend on today; an in-place edit would silently break them (see the spec's "near-miss" note under Risks).
- Out of scope, do not touch: `cms/io/web_service.py`, `cms/io/web_rpc.py` (sub-project 2.5's job), `cms/db/`, `cms/io/PsycoGevent.py` (sub-project 2.3's job), `cmsranking/`, and every `cmscontrib/` CLI script's `gevent.monkey.patch_all()` call.
- `rpc_method` (the decorator from `cms/io/rpc.py:57-67`) has no gevent dependency — import and reuse it directly from `cms.io.rpc`, never redefine it.
- No service other than `LogService` is migrated in this plan. `LogService`'s migration is end-to-end validation of the new framework, not the start of the per-service rollout (that's sub-project 2.4).
- Every new class's public method surface matches its gevent counterpart's names, parameters, and return/raise behavior exactly, so a future service migration (2.4) is a base-class swap, not a rewrite of the service's own logic.
- `beta` branch only. Never touch `main`.
- `pyflakes` clean on every touched file.
- Verify everything that involves real sockets or the Docker test image against the real Docker test image (`docker/docker-compose.test.yml`), not an ad hoc local `.venv` — this repo's local venvs have repeatedly been found to silently substitute dependency versions. Pure-logic unit tests (no sockets, no service processes) may run via `.venv/bin/pytest` if the `.venv`'s Python version matches `.python-version` (3.12); if in doubt, use Docker.

### The Mechanical Mapping (apply exactly, never improvise)

| gevent | asyncio | Notes |
|---|---|---|
| `gevent.socket.socket(...)` + `.connect(...)` (blocking) | `await asyncio.open_connection(host, port)` | Returns `(StreamReader, StreamWriter)` instead of a raw socket. |
| `gevent.server.StreamServer(address, handler)` + `.start()`/`.serve_forever()`/`.stop()` | `await asyncio.start_server(handler, host, port)` returns a `Server`; `async with server:` / `await server.serve_forever()` / `server.close()` + `await server.wait_closed()` | Handler signature changes from `(sock, address)` to `async def handler(reader, writer)`. |
| `sock.makefile('rb')` / `.makefile('wb')` + `.readline(n)` / `.write(...)` + `.flush()` | `StreamReader.readuntil(b"\r\n")` / `StreamWriter.write(...)` + `await writer.drain()` | `readuntil` raises `asyncio.LimitOverrunError` or `asyncio.IncompleteReadError` instead of returning a truncated/empty read — both must be caught where the gevent code catches `OSError`. |
| `gevent.lock.RLock()` + `with lock:` | `asyncio.Lock()` + `async with lock:` | `asyncio.Lock` is not reentrant (no `RLock` equivalent) — confirm no code path already holds the lock when acquiring it again (checked per call site in Task 1). |
| `gevent.event.Event()` (`.set()`, `.clear()`, `.is_set()`, `.wait(timeout=...)`) | `asyncio.Event()` (`.set()`, `.clear()`, `.is_set()`, `await asyncio.wait_for(event.wait(), timeout=...)`) | `asyncio.Event.wait()` has no built-in timeout parameter — wrap in `asyncio.wait_for` when a timeout is needed. |
| `gevent.event.AsyncResult()` (`.set(value)`, `.set_exception(exc)`, `.get()`/await via `rawlink`) | `asyncio.get_running_loop().create_future()` (`.set_result(value)`, `.set_exception(exc)`, `await future`) | A `Future` can only be resolved once (matches `AsyncResult`'s semantics — both raise/no-op on a second `.set()`). |
| `gevent.spawn(func, *args)` (fire-and-forget coroutine/greenlet) | `asyncio.create_task(func(*args))` | Keep a reference to the task if anything needs to cancel or await it later; a fire-and-forget task with no kept reference risks being garbage-collected mid-run — assign it to a name and, where the gevent code never joins it either, add it to an instance-level `set()` the class holds (mirroring the existing `pending_incoming_requests_threads` `WeakSet()` pattern in `rpc.py`) so it isn't collected. |
| `gevent.spawn_later(seconds, func, *args)` | `asyncio.get_running_loop().call_later(seconds, lambda: asyncio.create_task(func(*args)))` or an `async def` sleep-then-call task | Task 2's `add_timeout` uses the sleep-then-call task form, matching the existing `repeater()` function's shape. |
| `gevent.sleep(seconds)` | `await asyncio.sleep(seconds)` | |
| `greenlet.kill(exc, block=False)` | `task.cancel()` | Cancellation delivers `asyncio.CancelledError` into the task at its next `await` point, not immediately — different timing from `greenlet.kill()`, but the same intent (stop pending work on disconnect). Task 1 confirms this doesn't change externally observable behavior for `RemoteServiceServer.finalize()`'s use case (killing pending request handlers on disconnect). |
| `gevent.getcurrent()` (identify the running greenlet, used as a `WeakSet` member) | `asyncio.current_task()` | Tasks support weak references the same way greenlets do — `WeakSet()` usage is unchanged. |

## Review Focus

1. **`execute_rpc`'s calling convention change is a deliberate scope cut, not an oversight — verify it's actually safe, not just asserted.** `rpc.py`'s `execute_rpc` returns an `AsyncResult` immediately and supports a `callback`/`plus` kwarg pair for fire-and-forget-with-callback usage (`rpc.py:628-727`); this plan's `AsyncRemoteServiceClient.execute_rpc` is instead a coroutine a caller awaits directly, with no callback mechanism at all — a real behavior cut, not a mechanical translation. Owned by Task 1 — the task's own text argues this is safe because nothing in this plan (`LogService`, the pilot) calls any remote method through this proxy at all. A reviewer must independently confirm that claim (grep `cms/service/LogService.py` for any outbound RPC call — there should be none) rather than trust the plan's assertion, and confirm `cms.io.rpc`'s own callback mechanism is untouched (still works for every still-gevent service using the old `RemoteServiceClient`).
2. **`asyncio.Lock` is not reentrant**, unlike `gevent.lock.RLock` — a call path that already holds `_read_lock`/`_write_lock` and re-enters it (directly or via a nested call) would deadlock instead of silently succeeding as it does today. Owned by Task 1 — check every caller of `_read`/`_write` for re-entrant locking before assuming a straight swap is safe.
3. **`AsyncPriorityQueue.pop(wait=True)`'s actual blocking behavior** — a test that only checks the non-blocking path (queue already has an item) wouldn't catch a `wait()` call that returns immediately without truly waiting (the exact bug that would have shipped if `priorityqueue.py` had been edited in place — see the spec's Risks section). Owned by Task 3 — the test must push an item from a *second* concurrent task while the first task is blocked in `pop(wait=True)`, and assert the blocked call only returns after the push, not before.
4. **Signal handling under asyncio** (`SIGINT`/`SIGTERM` can't safely run async code directly from a signal handler) — a naive port that calls `self.exit()` (which will eventually need to touch asyncio state) directly from `signal.signal(...)`'s handler could corrupt the event loop's internal state under real signal delivery, even if it happens to work when `exit()` is called as an ordinary Python function in a test. Owned by Task 2 — must use `loop.call_soon_threadsafe(...)`, and the test must simulate actual signal delivery (`os.kill(os.getpid(), signal.SIGTERM)`), not just call `.exit()` directly as a plain method call.
5. **The cross-interoperability test is the one test in this whole plan that a passing suite could still hide if written sloppily** — two `AsyncService` instances talking to each other proves nothing about gevent compatibility; the test must pair one real `cms.io.service.Service` (unmodified, gevent) with one real `AsyncService` (new), as both client-then-server and server-then-client, each direction sending an actual RPC and checking the real response value, not just checking that no exception was raised. Owned by Task 2.

---

### Task 1: `cms/io/async_rpc.py`

**Files:**
- Create: `cms/io/async_rpc.py`
- Test: `cmstestsuite/unit_tests/io/__init__.py` (new, empty — marks the test package)
- Test: `cmstestsuite/unit_tests/io/async_rpc_test.py`

**Interfaces:**
- Consumes: `rpc_method`, `RPCError` from `cms.io.rpc` (imported, not redefined — both are gevent-free).
- Produces: `AsyncRemoteServiceBase`, `AsyncRemoteServiceServer(local_service, remote_address)`, `AsyncRemoteServiceClient(remote_service_coord, auto_retry=None)`, `AsyncFakeRemoteServiceClient(remote_service_coord, auto_retry=None)` — same constructor signatures, same public method names (`connected` property, `wait_for_connection`, `add_on_connect_handler`, `add_on_disconnect_handler`, `initialize`, `finalize`, `disconnect`, `connect` (client only), `run`, `execute_rpc`, `__getattr__` proxy) as `cms.io.rpc`'s classes. Task 2 imports `AsyncRemoteServiceServer` and `AsyncRemoteServiceClient` from this module.

- [ ] **Step 1: Create the test package marker**

```bash
mkdir -p cmstestsuite/unit_tests/io
touch cmstestsuite/unit_tests/io/__init__.py
```

- [ ] **Step 2: Write the failing test for a basic RPC round-trip**

Create `cmstestsuite/unit_tests/io/async_rpc_test.py`:

```python
#!/usr/bin/env python3

"""Tests for cms.io.async_rpc."""

import asyncio
import unittest

from cms.io.async_rpc import AsyncRemoteServiceServer, AsyncRemoteServiceClient
from cms.io.rpc import rpc_method, RPCError
from cms.conf import Address, ServiceCoord


class FakeLocalService:
    """A minimal stand-in for cms.io.service.Service, for testing
    AsyncRemoteServiceServer without needing a real Service."""

    @rpc_method
    def echo(self, string: str) -> str:
        return string

    @rpc_method
    def fail(self):
        raise ValueError("deliberate failure")

    def not_rpc_callable(self):
        """Not decorated with @rpc_method -- must be rejected."""
        return "should never be called remotely"


class TestAsyncRpcRoundTrip(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.local_service = FakeLocalService()
        self.server_reader = None
        self.server_writer = None

        async def handle_client(reader, writer):
            server_side = AsyncRemoteServiceServer(
                self.local_service, Address("127.0.0.1", 0))
            server_side.initialize_streams(reader, writer, plus=None)
            await server_side.run()

        self.server = await asyncio.start_server(
            handle_client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        asyncio.create_task(self.server.serve_forever())

        self.client = AsyncRemoteServiceClient(
            ServiceCoord("FakeLocalService", 0))
        # Bypass service-address lookup (no cms.toml entry for this
        # coord) by connecting directly to the loopback test server.
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.client.initialize_streams(reader, writer, plus=None)
        asyncio.create_task(self.client.run())

    async def asyncTearDown(self):
        self.client.disconnect()
        self.server.close()
        await self.server.wait_closed()

    async def test_successful_call_returns_value(self):
        result = await self.client.execute_rpc("echo", {"string": "hello"})
        self.assertEqual(result, "hello")

    async def test_failing_call_raises_rpc_error(self):
        with self.assertRaises(RPCError):
            await self.client.execute_rpc("fail", {})

    async def test_non_rpc_method_is_rejected(self):
        with self.assertRaises(RPCError):
            await self.client.execute_rpc("not_rpc_callable", {})

    async def test_nonexistent_method_is_rejected(self):
        with self.assertRaises(RPCError):
            await self.client.execute_rpc("does_not_exist", {})


if __name__ == "__main__":
    unittest.main()
```

Note: `initialize_streams` (used instead of `rpc.py`'s `initialize(sock, plus)`, which takes a raw socket) is this module's equivalent entry point for a pre-established `(StreamReader, StreamWriter)` pair — `asyncio.start_server`'s connection handler and `asyncio.open_connection` both hand you a reader/writer pair directly, there's no raw socket to pass through `initialize()`'s old signature. Step 4 defines it.

- [ ] **Step 2b: Run test to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_rpc_test.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cms.io.async_rpc'`

- [ ] **Step 3: Write `AsyncRemoteServiceBase`**

In `cms/io/async_rpc.py`:

```python
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
import traceback
import typing
import uuid
from weakref import WeakSet

from cms.conf import Address, ServiceCoord
from cms.util import get_service_address
from cms.io.rpc import rpc_method, RPCError

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
```

`StreamWriter.get_extra_info("sockname")` returns a tuple like
`socket.getsockname()` does — take the first two elements the same way
`rpc.py`'s `initialize()` does (`self._socket.getsockname()[:2]`), for the
same IPv4/IPv6 reason documented there.

`readuntil(b"\r\n")` raises `asyncio.LimitOverrunError` if the buffer fills
past its limit before finding the delimiter (the async equivalent of
`rpc.py`'s manual `MAX_MESSAGE_SIZE` check after `readline`) — configure
`asyncio.start_server`/`asyncio.open_connection`'s stream reader limit to
`MAX_MESSAGE_SIZE` so this actually triggers at the right size (Step 4
wires this at the connection-establishment call sites).

- [ ] **Step 4: Write `AsyncRemoteServiceServer`**

Append to `cms/io/async_rpc.py`:

```python
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
```

`if asyncio.iscoroutine(result): result = await result` lets `@rpc_method`
handlers on `AsyncService` subclasses be either plain functions or
`async def` — `LogService`'s methods (Task 5) stay plain synchronous
functions (they don't need to await anything), while a future migrated
service's method can be `async def` if it needs to. Both work.

- [ ] **Step 5: Write `AsyncRemoteServiceClient` and `AsyncFakeRemoteServiceClient`**

Append to `cms/io/async_rpc.py`:

```python
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
                type=__import__("socket").SOCK_STREAM)
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
```

**Deliberate behavior difference from `rpc.py`, and why it's safe:**
`rpc.py`'s `execute_rpc` returns an `AsyncResult` immediately (fire off the
request, get a handle back); this module's `execute_rpc` is a coroutine
that resolves once the response arrives — `await client.method(...)` reads
naturally in async code, which already has `await` for "wait for this."
The old callback/`plus` kwarg mechanism (`rpc.py:678-725`) is dropped, not
translated: nothing in this plan needs it (`LogService`, Task 5's pilot,
makes no outgoing RPC calls through this proxy at all — it only *receives*
calls), and 2.4's future service migrations can build a
`create_task(...).add_done_callback(...)` wrapper themselves the day one
of them actually needs fire-and-forget-with-callback semantics, rather
than this plan guessing at an interface nobody exercises yet.

- [ ] **Step 6: Run the round-trip tests to verify they pass**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_rpc_test.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 7: Write and pass the reconnection test**

Add to `cmstestsuite/unit_tests/io/async_rpc_test.py`:

```python
class TestAsyncRpcReconnection(unittest.IsolatedAsyncioTestCase):

    async def test_client_reconnects_after_server_restart(self):
        local_service = FakeLocalService()

        async def handle_client(reader, writer):
            server_side = AsyncRemoteServiceServer(
                local_service, Address("127.0.0.1", 0))
            server_side.initialize_streams(reader, writer, plus=None)
            await server_side.run()

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        asyncio.create_task(server.serve_forever())

        client = AsyncRemoteServiceClient(
            ServiceCoord("FakeLocalService", 0), auto_retry=0.05)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        client.initialize_streams(reader, writer, plus=None)
        client_loop = asyncio.create_task(client.run())

        result = await client.execute_rpc("echo", {"string": "before"})
        self.assertEqual(result, "before")

        # Kill the connection from the client's side and reconnect
        # manually against a fresh server socket on the same port,
        # simulating a server restart.
        client.disconnect()
        client_loop.cancel()
        server.close()
        await server.wait_closed()

        server2 = await asyncio.start_server(handle_client, "127.0.0.1", port)
        asyncio.create_task(server2.serve_forever())

        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
        client.initialize_streams(reader2, writer2, plus=None)
        asyncio.create_task(client.run())

        result2 = await client.execute_rpc("echo", {"string": "after"})
        self.assertEqual(result2, "after")

        client.disconnect()
        server2.close()
        await server2.wait_closed()
```

- [ ] **Step 8: Run the full test file**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_rpc_test.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 8b: Verify no re-entrant lock acquisition (Review Focus item 2)**

`asyncio.Lock` is not reentrant, unlike `gevent.lock.RLock` — a code path
that already holds `_read_lock`/`_write_lock` and acquires it again would
deadlock instead of silently succeeding. Confirm this can't happen: run
`grep -n "_read_lock\|_write_lock" cms/io/async_rpc.py` and check every
call site — `_read`/`_write` are the only methods that acquire either
lock, each exactly once per call, and neither calls the other (or itself
recursively) while already holding it. Note this check's result explicitly
in your self-review (Step 10's commit message, or your task report if
running under subagent-driven-development) — a reviewer must be able to
see this was actually checked, not assumed.

- [ ] **Step 9: pyflakes**

Run: `.venv/bin/pyflakes cms/io/async_rpc.py cmstestsuite/unit_tests/io/async_rpc_test.py`
Expected: no output.

- [ ] **Step 10: Commit**

```bash
git add cms/io/async_rpc.py cmstestsuite/unit_tests/io/__init__.py cmstestsuite/unit_tests/io/async_rpc_test.py
git commit -m "feat(io): add asyncio RPC client/server, wire-compatible with cms.io.rpc"
```

---

### Task 2: `cms/io/async_service.py`

**Files:**
- Create: `cms/io/async_service.py`
- Test: `cmstestsuite/unit_tests/io/async_service_test.py`

**Interfaces:**
- Consumes: `AsyncRemoteServiceServer`, `AsyncRemoteServiceClient`, `AsyncFakeRemoteServiceClient` (Task 1); `rpc_method`, `RPCError` from `cms.io.rpc`; `ConfigError`, `config`, `mkdir`, `ServiceCoord`, `Address`, `get_service_address` from `cms` (same imports `cms/io/service.py` already uses).
- Produces: `AsyncService(shard: int = 0)` with the same public surface as `cms.io.service.Service`: `connect_to`, `add_timeout`, `exit`, `get_backdoor_path`, `start_backdoor`/`stop_backdoor` (`@rpc_method`), `run`, `echo`/`quit` (`@rpc_method`). Task 4 subclasses this for `AsyncTriggeredService`; Task 5 subclasses it for the migrated `LogService`.

- [ ] **Step 1: Write the failing test for basic service lifecycle**

Create `cmstestsuite/unit_tests/io/async_service_test.py`:

```python
#!/usr/bin/env python3

"""Tests for cms.io.async_service."""

import asyncio
import os
import signal
import unittest
from unittest.mock import patch

from cms.io.async_service import AsyncService
from cms.io.rpc import rpc_method


class EchoingAsyncService(AsyncService):

    @rpc_method
    def double(self, value: int) -> int:
        return value * 2


class TestAsyncServiceLifecycle(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_service_starts_and_stops(self, mock_get_address):
        mock_get_address.return_value = ("127.0.0.1", 0)
        service = EchoingAsyncService(shard=0)
        run_task = asyncio.create_task(service._async_run())
        # Give the server a moment to actually bind before checking.
        await asyncio.sleep(0.05)
        self.assertTrue(service._server.is_serving())
        service.exit()
        await asyncio.wait_for(run_task, timeout=2)
        self.assertFalse(service._server.is_serving())
```

Note: `AsyncService.run()` (see Step 3) is a synchronous entry point
(`asyncio.run(self._async_run())`) matching `Service.run()`'s contract, so
tests that need to interact with a running service from within an already-running
event loop (as `IsolatedAsyncioTestCase` provides) call the internal
`_async_run()` coroutine directly instead, as shown above.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_service_test.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cms.io.async_service'`

- [ ] **Step 3: Write `AsyncService`**

Create `cms/io/async_service.py`:

```python
#!/usr/bin/env python3

"""Asyncio-based service base class, mirroring cms.io.service.Service.

"""

import asyncio
from collections.abc import Callable
import errno
import functools
import logging
import os
import signal
import socket
import time
from typing import Any

import gevent
import gevent.socket
from gevent.backdoor import BackdoorServer

from cms import ConfigError, config, mkdir, ServiceCoord, Address, \
    get_service_address
from cms.log import root_logger, shell_handler, ServiceFilter, \
    DetailedFormatter, LogServiceHandler, FileHandler
from cms.io.rpc import rpc_method
from .async_rpc import AsyncRemoteServiceServer, AsyncRemoteServiceClient, \
    AsyncFakeRemoteServiceClient, MAX_MESSAGE_SIZE


logger = logging.getLogger(__name__)


async def async_repeater(func: Callable[[], Any], period: float):
    """Repeatedly call the given (possibly async) function.

    See cms.io.service.repeater -- same contract, asyncio-native sleep.

    """
    while True:
        call = time.monotonic()

        try:
            result = func()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.error("Unexpected error.", exc_info=True)

        await asyncio.sleep(max(call + period - time.monotonic(), 0))


class AsyncService:

    def __init__(self, shard: int = 0):
        self.name = self.__class__.__name__
        self.shard = shard
        self._my_coord = ServiceCoord(self.name, self.shard)

        self.remote_services: dict[ServiceCoord, AsyncRemoteServiceClient] = {}

        self.initialize_logging()

        try:
            self._listen_address = get_service_address(self._my_coord)
        except KeyError:
            raise ConfigError("Unable to find address for service %s. "
                              "Is it specified in core_services in cms.toml?" %
                              (self._my_coord,))

        self._server: asyncio.Server | None = None
        self.backdoor = None
        self._exit_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._background_tasks: set[asyncio.Task] = set()

    def initialize_logging(self):
        """See cms.io.service.Service.initialize_logging -- identical,
        no gevent dependency in this method at all."""
        filter_ = ServiceFilter(self.name, self.shard)

        shell_handler.addFilter(filter_)

        log_dir = os.path.join(config.global_.log_dir,
                               "%s-%d" % (self.name, self.shard))
        mkdir(config.global_.log_dir)
        mkdir(log_dir)

        log_filename = time.strftime("%Y-%m-%d-%H-%M-%S.log")

        file_handler = FileHandler(os.path.join(log_dir, log_filename),
                                   mode='w', encoding='utf-8')
        if config.global_.file_log_debug:
            file_log_level = logging.DEBUG
        else:
            file_log_level = logging.INFO
        file_handler.setLevel(file_log_level)
        file_handler.setFormatter(DetailedFormatter(False))
        file_handler.addFilter(filter_)
        root_logger.addHandler(file_handler)

        try:
            os.remove(os.path.join(log_dir, "last.log"))
        except OSError:
            pass
        os.symlink(log_filename, os.path.join(log_dir, "last.log"))

        if self.name != "LogService":
            log_service = self.connect_to(ServiceCoord("LogService", 0))
            remote_handler = LogServiceHandler(log_service)
            remote_handler.setLevel(logging.INFO)
            remote_handler.addFilter(filter_)
            root_logger.addHandler(remote_handler)

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        address = writer.get_extra_info("peername")
        remote_service = AsyncRemoteServiceServer(self, Address(address[0], address[1]))
        remote_service.initialize_streams(reader, writer, Address(address[0], address[1]))
        await remote_service.run()

    def connect_to(
        self,
        coord: ServiceCoord,
        on_connect: Callable[[object], Any] | None = None,
        on_disconnect: Callable[[object], Any] | None = None,
        must_be_present: bool = True,
    ) -> AsyncRemoteServiceClient:
        """See cms.io.service.Service.connect_to -- identical contract."""
        if coord not in self.remote_services:
            try:
                service = AsyncRemoteServiceClient(coord, auto_retry=0.5)
            except KeyError:
                if must_be_present:
                    raise ConfigError("Missing address and port for %s "
                                      "in cms.toml." % (coord, ))
                else:
                    service = AsyncFakeRemoteServiceClient(coord, None)
            service.connect()
            self.remote_services[coord] = service
        else:
            service = self.remote_services[coord]

        if on_connect is not None:
            service.add_on_connect_handler(on_connect)

        if on_disconnect is not None:
            service.add_on_disconnect_handler(on_disconnect)

        return service

    def add_timeout(
        self,
        func: Callable,
        plus: dict | None,
        seconds: float,
        immediately: bool = False,
    ):
        """See cms.io.service.Service.add_timeout -- identical contract."""
        if plus is None:
            plus = {}
        func = functools.partial(func, **plus)

        async def start_after_delay():
            if not immediately:
                await asyncio.sleep(seconds)
            await async_repeater(func, seconds)

        task = asyncio.create_task(start_after_delay())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def exit(self):
        """Terminate the service at the next step.

        Safe to call from a signal handler (schedules the actual
        asyncio-touching work via call_soon_threadsafe) or as an
        ordinary method call from already-running async code.

        """
        logger.warning("%s received request to shut down.", self._my_coord)
        if self._loop is not None and self._exit_event is not None:
            self._loop.call_soon_threadsafe(self._exit_event.set)

    def get_backdoor_path(self) -> str:
        return os.path.join(config.global_.run_dir, "%s_%d" % (self.name, self.shard))

    @rpc_method
    def start_backdoor(self, backlog=50):
        """See cms.io.service.Service.start_backdoor.

        Deliberately still gevent-based (gevent.backdoor.BackdoorServer)
        -- an optional debugging REPL on its own independent UNIX
        socket, orthogonal to the RPC/event loop this class otherwise
        runs on asyncio. See the spec's Architecture section for why
        this is an accepted scoped simplification, not a gap.

        """
        backdoor_path = self.get_backdoor_path()
        try:
            os.remove(backdoor_path)
        except FileNotFoundError:
            pass
        else:
            logger.warning("A backdoor socket has been found and deleted.")
        mkdir(os.path.dirname(backdoor_path))
        backdoor_sock = gevent.socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        backdoor_sock.setblocking(0)
        backdoor_sock.bind(backdoor_path)
        os.chmod(backdoor_path, 0o700)
        backdoor_sock.listen(backlog)
        self.backdoor = BackdoorServer(backdoor_sock, locals={'service': self})
        self.backdoor.start()

    @rpc_method
    def stop_backdoor(self):
        if self.backdoor is not None:
            self.backdoor.stop()
        backdoor_path = self.get_backdoor_path()
        try:
            os.remove(backdoor_path)
        except FileNotFoundError:
            pass

    def run(self) -> bool:
        """Starts the main loop of the service (synchronous entry
        point, matching cms.io.service.Service.run's contract: blocks
        the calling thread until the service shuts down).

        """
        return asyncio.run(self._async_run())

    async def _async_run(self) -> bool:
        self._loop = asyncio.get_running_loop()
        self._exit_event = asyncio.Event()

        self._loop.add_signal_handler(signal.SIGINT, self.exit)
        self._loop.add_signal_handler(signal.SIGTERM, self.exit)

        try:
            self._server = await asyncio.start_server(
                self._handle_connection,
                self._listen_address.ip, self._listen_address.port,
                limit=MAX_MESSAGE_SIZE)
        except socket.gaierror:
            logger.critical("Service %s could not listen on "
                            "specified address, because it cannot "
                            "be resolved.", self.name)
            return False
        except OSError as error:
            if error.errno == errno.EADDRINUSE:
                logger.critical("Listening port %s for service %s is "
                                "already in use, quitting.",
                                self._listen_address.port, self.name)
                return False
            elif error.errno == errno.EADDRNOTAVAIL:
                logger.critical("Service %s could not listen on "
                                "specified address, because it is not "
                                "available.", self.name)
                return False
            else:
                raise

        if config.global_.backdoor:
            self.start_backdoor()

        logger.info("%s %d up and running!", *self._my_coord)

        await self._exit_event.wait()

        logger.info("%s %d is shutting down", *self._my_coord)

        if config.global_.backdoor:
            self.stop_backdoor()

        self._server.close()
        await self._server.wait_closed()

        self._disconnect_all()
        return True

    def _disconnect_all(self):
        for service in self.remote_services.values():
            if service.connected:
                service.disconnect()

    @rpc_method
    def echo(self, string: str) -> str:
        return string

    @rpc_method
    def quit(self, reason: str = ""):
        logger.info("Trying to exit as asked by another service (%s).", reason)
        self.exit()
```

`MAX_MESSAGE_SIZE` here is the module-level constant defined in
`cms/io/async_rpc.py` (Task 1, Step 3) and imported above — the same value
`asyncio.start_server`'s `limit=` kwarg uses to cap incoming message size
server-side, matching `AsyncRemoteServiceClient._connect`'s
`asyncio.open_connection(..., limit=self.MAX_MESSAGE_SIZE)` (Task 1, Step 5)
on the client side. One constant, two call sites, no duplicated number.

- [ ] **Step 4: Run the lifecycle test**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_service_test.py -v`
Expected: PASS.

- [ ] **Step 5: Write and pass the `add_timeout` test**

Add to `cmstestsuite/unit_tests/io/async_service_test.py`:

```python
class TestAddTimeout(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_repeated_calls_happen_at_interval(self, mock_get_address):
        mock_get_address.return_value = ("127.0.0.1", 0)
        service = EchoingAsyncService(shard=0)
        service._loop = asyncio.get_running_loop()
        service._exit_event = asyncio.Event()

        calls = []
        service.add_timeout(lambda: calls.append(time.monotonic()),
                            plus=None, seconds=0.05, immediately=True)

        await asyncio.sleep(0.22)

        # Expect at least 4 calls in ~0.22s at a 0.05s period
        # (immediately=True means the first call has no initial delay).
        self.assertGreaterEqual(len(calls), 4)
```

Add `import time` to the test file's imports if not already present.

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_service_test.py -v`
Expected: both test classes PASS.

- [ ] **Step 7: Write and pass the signal-handling test (Review Focus item 4)**

Add to `cmstestsuite/unit_tests/io/async_service_test.py`:

```python
class TestSignalHandling(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_sigterm_triggers_clean_shutdown(self, mock_get_address):
        mock_get_address.return_value = ("127.0.0.1", 0)
        service = EchoingAsyncService(shard=0)
        run_task = asyncio.create_task(service._async_run())
        await asyncio.sleep(0.05)

        os.kill(os.getpid(), signal.SIGTERM)

        result = await asyncio.wait_for(run_task, timeout=2)
        self.assertTrue(result)
        self.assertFalse(service._server.is_serving())
```

This test only passes if `exit()`'s `call_soon_threadsafe` wiring is
correct — a naive direct call to async-touching code from inside the
signal handler risks corrupting the running event loop (Review Focus item
4); sending a real `SIGTERM` to the test process, rather than just calling
`service.exit()` as an ordinary method, is what actually exercises the
signal-delivery path.

- [ ] **Step 8: Run to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_service_test.py -v`
Expected: all tests PASS.

- [ ] **Step 9: Write and pass the cross-interoperability test (Review Focus item 5)**

Add to `cmstestsuite/unit_tests/io/async_service_test.py`:

```python
class TestGeventAsyncioInterop(unittest.IsolatedAsyncioTestCase):
    """Proves an AsyncService and an old, unmigrated gevent Service can
    exchange real RPCs in both directions -- the premise the entire
    2.2->2.4 incremental migration strategy depends on."""

    @patch("cms.io.async_service.get_service_address")
    @patch("cms.io.service.get_service_address")
    async def test_async_client_calls_gevent_server(
        self, mock_gevent_address, mock_async_address
    ):
        import threading
        from cms.io.service import Service
        from cms.io.rpc import rpc_method as gevent_rpc_method

        class GeventEchoService(Service):
            @gevent_rpc_method
            def triple(self, value: int) -> int:
                return value * 3

        mock_gevent_address.return_value = ("127.0.0.1", 24680)
        mock_async_address.return_value = ("127.0.0.1", 24681)

        gevent_service = GeventEchoService(shard=0)
        server_thread = threading.Thread(
            target=gevent_service.run, daemon=True)
        server_thread.start()
        await asyncio.sleep(0.2)  # let the gevent StreamServer bind

        from cms.io.async_rpc import AsyncRemoteServiceClient
        from cms.conf import ServiceCoord

        client = AsyncRemoteServiceClient(ServiceCoord("GeventEchoService", 0))
        client.connect()
        await client.wait_for_connection(timeout=2)

        result = await client.execute_rpc("triple", {"value": 7})
        self.assertEqual(result, 21)

        client.disconnect()
        gevent_service.exit()
```

Running a real gevent `Service` (which calls `gevent.server.StreamServer`
and blocks its own thread in `serve_forever()`) requires its own OS thread
alongside the test's asyncio event loop — gevent's greenlet scheduler and
asyncio's event loop each need their own thread to run in; they can't share
one. This is why the gevent side runs via `threading.Thread`, not
`asyncio.create_task`. `mock_gevent_address`/`mock_async_address` patch
`get_service_address` (imported separately in each of `cms.io.service` and
`cms.io.async_service`) to return fixed loopback ports instead of requiring
a real `cms.toml` entry for these test-only service names.

If this test proves flaky under CI/Docker timing (the `asyncio.sleep(0.2)`
guess for "has the gevent server finished binding" is inherently racy),
replace it with a poll loop that retries `client.connect()` +
`wait_for_connection(timeout=0.1)` for up to 2 seconds instead of a single
fixed sleep — note this as a concern in your report if you have to make
this change, so the reviewer can confirm the retry loop is sound.

- [ ] **Step 10: Run to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_service_test.py -v`
Expected: all tests PASS.

- [ ] **Step 11: Write and pass the `run_in_executor` DB-bridge pattern test**

Add to `cmstestsuite/unit_tests/io/async_service_test.py`:

```python
class TestRunInExecutorBridgePattern(unittest.IsolatedAsyncioTestCase):
    """Documents and proves the pattern future service migrations (2.4)
    will use to call blocking cms/db/ code from an AsyncService without
    blocking the event loop, until sub-project 2.3 makes cms/db/ itself
    async. Not tied to any real DB call -- a dummy blocking function
    stands in for "some synchronous, blocking call.\""""

    async def test_blocking_call_does_not_block_the_event_loop(self):
        import time as time_module

        def blocking_call():
            time_module.sleep(0.2)
            return "done"

        loop = asyncio.get_running_loop()

        # Prove the event loop stays responsive to other tasks while
        # the blocking call is running in a thread: a concurrently
        # scheduled task must be able to complete before the blocking
        # call returns.
        marker = []

        async def concurrent_task():
            await asyncio.sleep(0.05)
            marker.append("concurrent task ran")

        task = asyncio.create_task(concurrent_task())
        result = await loop.run_in_executor(None, blocking_call)

        self.assertEqual(result, "done")
        self.assertEqual(marker, ["concurrent task ran"])
        await task
```

- [ ] **Step 12: Run to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_service_test.py -v`
Expected: all tests PASS.

- [ ] **Step 13: pyflakes**

Run: `.venv/bin/pyflakes cms/io/async_service.py cmstestsuite/unit_tests/io/async_service_test.py`
Expected: no output. Fix any unused imports (e.g. `gevent` is used only
by `start_backdoor`/`stop_backdoor` — keep it; anything else pyflakes
flags should be removed).

- [ ] **Step 14: Commit**

```bash
git add cms/io/async_service.py cmstestsuite/unit_tests/io/async_service_test.py
git commit -m "feat(io): add AsyncService, wire-compatible with cms.io.service.Service"
```

---

### Task 3: `cms/io/async_priorityqueue.py`

**Files:**
- Create: `cms/io/async_priorityqueue.py`
- Test: `cmstestsuite/unit_tests/io/async_priorityqueue_test.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2 (independent of the RPC/service layer — pure data structure).
- Produces: `AsyncPriorityQueue` with the same public surface as
  `cms.io.priorityqueue.PriorityQueue`: `PRIORITY_EXTRA_HIGH`/`PRIORITY_HIGH`/
  `PRIORITY_MEDIUM`/`PRIORITY_LOW`/`PRIORITY_EXTRA_LOW` class constants,
  `push`, `remove`, `set_priority`, `length`, `empty`, `get_status`,
  `__len__`, `__contains__` (all synchronous, unchanged), and `top`/`pop`
  (now `async def`, since their `wait=True` path awaits an `asyncio.Event`
  — see Global Constraints on why this can't be a synchronous drop-in).
  Task 4 uses this for `AsyncTriggeredService`'s operation queue.

- [ ] **Step 1: Write the failing test for the blocking-wait behavior (Review Focus item 3)**

Create `cmstestsuite/unit_tests/io/async_priorityqueue_test.py`:

```python
#!/usr/bin/env python3

"""Tests for cms.io.async_priorityqueue."""

import asyncio
import unittest

from cms.io.async_priorityqueue import AsyncPriorityQueue
from cms.io.priorityqueue import QueueItem


class FakeItem(QueueItem):
    def __init__(self, title):
        self._title = title

    def __eq__(self, other):
        return self._title == other._title

    def __hash__(self):
        return hash(self._title)

    def __str__(self):
        return self._title


class TestAsyncPriorityQueueBasics(unittest.IsolatedAsyncioTestCase):

    async def test_push_and_pop_respects_priority(self):
        queue = AsyncPriorityQueue()
        low = FakeItem("low")
        high = FakeItem("high")
        queue.push(low, priority=AsyncPriorityQueue.PRIORITY_LOW)
        queue.push(high, priority=AsyncPriorityQueue.PRIORITY_HIGH)

        first = await queue.pop()
        self.assertEqual(first.item, high)
        second = await queue.pop()
        self.assertEqual(second.item, low)

    async def test_pop_without_wait_raises_on_empty(self):
        queue = AsyncPriorityQueue()
        with self.assertRaises(LookupError):
            await queue.pop(wait=False)


class TestAsyncPriorityQueueBlockingWait(unittest.IsolatedAsyncioTestCase):
    """Review Focus item 3: a test that only exercises the non-blocking
    path wouldn't catch a wait() that returns immediately without
    truly waiting. This test proves pop(wait=True) genuinely blocks
    until a concurrent push, not before."""

    async def test_pop_wait_true_blocks_until_concurrent_push(self):
        queue = AsyncPriorityQueue()
        item = FakeItem("delayed")

        events: list[str] = []

        async def popper():
            entry = await queue.pop(wait=True)
            events.append("popped")
            return entry

        async def pusher():
            await asyncio.sleep(0.1)
            events.append("about to push")
            queue.push(item)

        pop_task = asyncio.create_task(popper())
        push_task = asyncio.create_task(pusher())

        # Give the popper a chance to start waiting before the pusher
        # runs -- if pop(wait=True) doesn't actually block, "popped"
        # would appear before "about to push".
        await asyncio.sleep(0.02)
        self.assertFalse(pop_task.done(),
                         "pop(wait=True) returned before any item was "
                         "pushed -- it isn't actually blocking.")

        entry = await pop_task
        await push_task

        self.assertEqual(events, ["about to push", "popped"])
        self.assertEqual(entry.item, item)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_priorityqueue_test.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cms.io.async_priorityqueue'`

- [ ] **Step 3: Write `AsyncPriorityQueue`**

Create `cms/io/async_priorityqueue.py`:

```python
#!/usr/bin/env python3

"""Asyncio priority queue, mirroring cms.io.priorityqueue.PriorityQueue.

NOT an in-place edit of priorityqueue.py -- see the design spec's Risks
section for why that would silently break EvaluationService/
ScoringService/ProxyService (still gevent-based TriggeredService
consumers, unmigrated until sub-project 2.4). This is a full parallel
implementation; top()/pop() are async def, since asyncio.Event has no
synchronous blocking .wait() the way gevent.event.Event does.

"""

from datetime import datetime
import typing

import asyncio

from cms.io.priorityqueue import QueueEntry, QueueEntryDict, QueueItemT


class AsyncPriorityQueue(typing.Generic[QueueItemT]):
    """See cms.io.priorityqueue.PriorityQueue for the full contract this
    mirrors. Implementation (heap logic) is identical; only the
    blocking-wait mechanism in top()/pop() differs."""

    PRIORITY_EXTRA_HIGH = 0
    PRIORITY_HIGH = 1
    PRIORITY_MEDIUM = 2
    PRIORITY_LOW = 3
    PRIORITY_EXTRA_LOW = 4

    def __init__(self):
        self._queue: list[QueueEntry[QueueItemT]] = []
        self._reverse: dict[QueueItemT, int] = {}
        self._event = asyncio.Event()
        self._next_index = 0

    def __len__(self):
        return len(self._queue)

    def __contains__(self, item: QueueItemT) -> bool:
        return item in self._reverse

    def _swap(self, idx1: int, idx2: int):
        self._queue[idx1], self._queue[idx2] = \
            self._queue[idx2], self._queue[idx1]
        self._reverse[self._queue[idx1].item] = idx1
        self._reverse[self._queue[idx2].item] = idx2

    def _up_heap(self, idx: int) -> int:
        while idx > 0:
            parent = (idx - 1) // 2
            if self._queue[idx] < self._queue[parent]:
                self._swap(parent, idx)
                idx = parent
            else:
                break
        return idx

    def _down_heap(self, idx: int) -> int:
        last = len(self._queue) - 1
        while 2 * idx + 1 <= last:
            child = 2 * idx + 1
            if 2 * idx + 2 <= last and \
                    self._queue[2 * idx + 2] < self._queue[child]:
                child = 2 * idx + 2
            if self._queue[child] < self._queue[idx]:
                self._swap(child, idx)
                idx = child
            else:
                break
        return idx

    def _updown_heap(self, idx: int) -> int:
        idx = self._up_heap(idx)
        return self._down_heap(idx)

    def push(
        self,
        item: QueueItemT,
        priority: int | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        """Synchronous, same as PriorityQueue.push -- no wait involved."""
        from cmscommon.datetime import make_datetime

        if item in self._reverse:
            return False

        if priority is None:
            priority = AsyncPriorityQueue.PRIORITY_MEDIUM
        if timestamp is None:
            timestamp = make_datetime()

        index = self._next_index
        self._next_index += 1

        self._queue.append(QueueEntry(item, priority, timestamp, index))
        last = len(self._queue) - 1
        self._reverse[item] = last
        self._up_heap(last)

        self._event.set()

        return True

    async def top(self, wait: bool = False) -> QueueEntry[QueueItemT]:
        if not self.empty():
            return self._queue[0]
        else:
            if not wait:
                raise LookupError("Empty queue.")
            else:
                while True:
                    if self.empty():
                        await self._event.wait()
                        continue
                    return self._queue[0]

    async def pop(self, wait: bool = False) -> QueueEntry[QueueItemT]:
        top = await self.top(wait)
        last = len(self._queue) - 1
        self._swap(0, last)

        del self._reverse[top.item]
        del self._queue[last]

        if last > 0:
            self._down_heap(0)
        else:
            self._event.clear()
        return top

    def remove(self, item: QueueItemT) -> QueueEntry[QueueItemT]:
        pos = self._reverse[item]
        entry = self._queue[pos]

        last = len(self._queue) - 1
        self._swap(pos, last)

        del self._reverse[item]
        del self._queue[last]
        if pos != last:
            self._updown_heap(pos)

        if self.empty():
            self._event.clear()

        return entry

    def set_priority(self, item: QueueItemT, priority: int):
        pos = self._reverse[item]
        self._queue[pos].priority = priority
        self._updown_heap(pos)

    def length(self) -> int:
        return len(self._queue)

    def empty(self) -> bool:
        return self.length() == 0

    def get_status(self) -> list[QueueEntryDict]:
        from cmscommon.datetime import make_timestamp

        return [{'item': entry.item.to_dict(),
                 'priority': entry.priority,
                 'timestamp': make_timestamp(entry.timestamp)}
                for entry in self._queue]
```

Move the two `from cmscommon.datetime import ...` calls to top-of-file
imports instead of inline (written inline above only to keep this step's
diff visually adjacent to the mirrored `push`/`get_status` methods for
your review — clean this up before committing, matching `priorityqueue.py`'s
own top-of-file import style).

- [ ] **Step 4: Run to verify all tests pass**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_priorityqueue_test.py -v`
Expected: all 3 tests PASS, including the blocking-wait test from Step 1.

- [ ] **Step 5: pyflakes**

Run: `.venv/bin/pyflakes cms/io/async_priorityqueue.py cmstestsuite/unit_tests/io/async_priorityqueue_test.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add cms/io/async_priorityqueue.py cmstestsuite/unit_tests/io/async_priorityqueue_test.py
git commit -m "feat(io): add AsyncPriorityQueue, mirroring cms.io.priorityqueue.PriorityQueue"
```

---

### Task 4: `cms/io/async_triggeredservice.py`

**Files:**
- Create: `cms/io/async_triggeredservice.py`
- Test: `cmstestsuite/unit_tests/io/async_triggeredservice_test.py`

**Interfaces:**
- Consumes: `AsyncService` (Task 2, including its `_background_tasks: set`
  instance attribute — reused here for spawned executor/sweeper tasks, not
  redefined), `AsyncPriorityQueue` (Task 3), `QueueEntry`/`QueueEntryDict`/
  `QueueItem`/`QueueItemT` (unchanged, from `cms.io.priorityqueue` — plain
  data classes with no gevent dependency, reused as-is, not duplicated),
  `rpc_method` from `cms.io.rpc`.
- Produces: `AsyncExecutor` (abstract base, same contract as `Executor`:
  `enqueue`, `dequeue`, `get_status`, `__contains__`, `run`,
  `max_operations_per_batch`, abstract `execute`), `AsyncTriggeredService(AsyncService)`
  with `add_executor`, `get_executor`, `enqueue`, `dequeue`, `start_sweeper`,
  `search_operations_not_done` (`@rpc_method`), `queue_status` (`@rpc_method`).
  Not consumed by anything else in this plan — ready for sub-project 2.4 to
  subclass when it migrates `EvaluationService`/`ScoringService`/`ProxyService`.

**Important — `TriggeredService`'s real shape (confirmed by reading
`cms/io/triggeredservice.py` in full before writing this task):**
`TriggeredService` does NOT itself hold one queue — it's composition over a
*list* of `Executor` instances (each with its own queue, spawned as its own
greenlet via `add_executor`), plus a completely separate "sweeper loop"
mechanism (`start_sweeper`/`_sweeper_loop`/`_sweeper_event`, its own
`gevent.event.Event`, unrelated to any `Executor`'s queue). `TriggeredService.__init__`
takes only `shard` — executors are added afterward via `add_executor(executor)`,
never passed to `__init__`. Two independent gevent event primitives need
translating here (`Executor`'s `_operation_queue`'s wait, already solved by
Task 3, and `TriggeredService`'s own `_sweeper_event`), not one.

- [ ] **Step 1: Write the failing test for enqueue/dequeue and the sweeper**

Create `cmstestsuite/unit_tests/io/async_triggeredservice_test.py`:

```python
#!/usr/bin/env python3

"""Tests for cms.io.async_triggeredservice."""

import asyncio
import unittest
from unittest.mock import patch

from cms.io.async_triggeredservice import AsyncExecutor, AsyncTriggeredService
from cms.io.priorityqueue import QueueItem


class FakeOperation(QueueItem):
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return self.name == other.name

    def __hash__(self):
        return hash(self.name)

    def to_dict(self):
        return {"name": self.name}


class RecordingExecutor(AsyncExecutor):
    def __init__(self):
        super().__init__(batch_executions=False)
        self.executed: list = []

    async def execute(self, entry):
        self.executed.append(entry.item)


class RecordingTriggeredService(AsyncTriggeredService):
    pass


class TestEnqueueDequeue(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_enqueue_then_dequeue(self, mock_get_address):
        mock_get_address.return_value = ("127.0.0.1", 0)
        service = RecordingTriggeredService(shard=0)
        executor = RecordingExecutor()
        service.add_executor(executor)
        op = FakeOperation("compile")

        self.assertEqual(service.enqueue(op), 1)  # 1 executor accepted it
        self.assertEqual(service.enqueue(op), 0)   # duplicate, rejected by push()

        service.dequeue(op)
        self.assertNotIn(op, executor)


class TestSweeper(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_search_operations_not_done_wakes_sweeper_early(
        self, mock_get_address
    ):
        mock_get_address.return_value = ("127.0.0.1", 0)

        sweeps: list[int] = []

        class SweepingService(AsyncTriggeredService):
            def _missing_operations(self):
                sweeps.append(1)
                return 0

        service = SweepingService(shard=0)
        # A long timeout: only search_operations_not_done() should wake
        # this loop within the test's timeframe, not the timeout itself.
        service.start_sweeper(timeout=60)
        await asyncio.sleep(0.05)
        self.assertEqual(len(sweeps), 1, "expected exactly the initial sweep")

        service.search_operations_not_done()
        await asyncio.sleep(0.05)
        self.assertEqual(len(sweeps), 2,
                         "search_operations_not_done() should trigger an "
                         "immediate second sweep, not wait for the 60s timeout")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_triggeredservice_test.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cms.io.async_triggeredservice'`

- [ ] **Step 3: Write `AsyncExecutor`**

Create `cms/io/async_triggeredservice.py`:

```python
#!/usr/bin/env python3

"""Asyncio-based base classes for services relying on notifications and
sweeper loops, mirroring cms.io.triggeredservice.

"""

from abc import ABCMeta, abstractmethod
from datetime import datetime
import asyncio
import logging
import time
import typing

from cms.io.rpc import rpc_method
from cms.io.priorityqueue import QueueEntry, QueueEntryDict, QueueItemT
from .async_priorityqueue import AsyncPriorityQueue
from .async_service import AsyncService


logger = logging.getLogger(__name__)


class AsyncExecutor(typing.Generic[QueueItemT], metaclass=ABCMeta):
    """A class taking care of executing operations.

    See cms.io.triggeredservice.Executor for the full contract this
    mirrors (batch vs. one-at-a-time execution).

    """

    def __init__(self, batch_executions: bool = False):
        super().__init__()

        self._batch_executions = batch_executions
        self._operation_queue: AsyncPriorityQueue[QueueItemT] = AsyncPriorityQueue()

    def __contains__(self, item: QueueItemT) -> bool:
        return item in self._operation_queue

    def get_status(self) -> list[QueueEntryDict]:
        return self._operation_queue.get_status()

    def enqueue(
        self,
        item: QueueItemT,
        priority: int | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        return self._operation_queue.push(item, priority, timestamp)

    def dequeue(self, item: QueueItemT) -> QueueEntry[QueueItemT]:
        return self._operation_queue.remove(item)

    async def _pop(self, wait: bool = False) -> QueueEntry[QueueItemT]:
        return await self._operation_queue.pop(wait=wait)

    async def run(self):
        """Monitor the queue, and dispatch operations when available.

        See cms.io.triggeredservice.Executor.run for the full
        contract: an infinite loop, blocking until an element is
        present, then dispatching it (or a batch) to execute().

        """
        while True:
            to_execute = [await self._pop(wait=True)]
            if self._batch_executions:
                max_operations = self.max_operations_per_batch()
                while not self._operation_queue.empty() and (
                        max_operations == 0 or
                        len(to_execute) < max_operations):
                    to_execute.append(await self._pop())

            assert len(to_execute) > 0, "Expected at least one element."
            if self._batch_executions:
                try:
                    logger.info("Executing operations `%s' and %d more.",
                                to_execute[0].item, len(to_execute) - 1)
                    await self.execute(to_execute)
                    logger.info("Operations `%s' and %d more concluded "
                                "successfully.", to_execute[0].item,
                                len(to_execute) - 1)
                except Exception:
                    logger.error(
                        "Unexpected error when executing operation "
                        "`%s' (and %d more operations).", to_execute[0].item,
                        len(to_execute) - 1, exc_info=True)

            else:
                try:
                    logger.info("Executing operation `%s'.",
                                to_execute[0].item)
                    await self.execute(to_execute[0])
                    logger.info("Operation `%s' concluded successfully",
                                to_execute[0].item)
                except Exception:
                    logger.error(
                        "Unexpected error when executing operation `%s'.",
                        to_execute[0].item, exc_info=True)

    def max_operations_per_batch(self) -> int:
        """See cms.io.triggeredservice.Executor.max_operations_per_batch.

        Base implementation returns 0 (no limit) -- subclasses using
        batch_executions=True override this the same way Executor
        subclasses do today; not abstract, since a non-batch executor
        never calls it.

        """
        return 0

    @abstractmethod
    async def execute(self, entry: QueueEntry[QueueItemT] | list[QueueEntry[QueueItemT]]):
        """Perform a single operation (or a batch).

        async def, not sync, unlike Executor.execute: a real 2.4
        migration's implementation will need to await RPCs to other
        services and run_in_executor-wrapped DB calls -- see the
        spec's DB bridge pattern.

        """
        pass


ExecutorT = typing.TypeVar("ExecutorT", bound=AsyncExecutor)


class AsyncTriggeredService(AsyncService, typing.Generic[QueueItemT, ExecutorT]):
    """A service receiving notifications to perform an operation.

    See cms.io.triggeredservice.TriggeredService for the full
    contract this mirrors: a list of executors (each running its own
    task), plus a sweeper task that periodically searches for missed
    operations.

    """

    def __init__(self, shard: int):
        AsyncService.__init__(self, shard)

        self._executors: list[ExecutorT] = []

        self._sweeper_start: float | None = None
        self._sweeper_event = asyncio.Event()
        self._sweeper_started = False
        self._sweeper_timeout: float | None = None

    def add_executor(self, executor: ExecutorT):
        """Add an executor for the service, and start its run loop."""
        self._executors.append(executor)
        task = asyncio.create_task(executor.run())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def get_executor(self) -> ExecutorT:
        return self._executors[0]

    def enqueue(
        self,
        operation: QueueItemT,
        priority: int | None = None,
        timestamp: datetime | None = None,
    ) -> int:
        """See cms.io.triggeredservice.TriggeredService.enqueue.

        return: the number of executors that successfully added
            the operation to their queue.

        """
        ret = 0
        for executor in self._executors:
            if executor.enqueue(operation, priority, timestamp):
                ret += 1
        return ret

    def dequeue(self, operation: QueueItemT):
        for executor in self._executors:
            executor.dequeue(operation)

    def start_sweeper(self, timeout: float):
        """Start sweeper loop with given timeout."""
        if not self._sweeper_started:
            self._sweeper_started = True
            self._sweeper_timeout = timeout

            task = asyncio.create_task(self._sweeper_loop())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        else:
            logger.warning("Service tried to start the sweeper loop twice.")

    async def _sweeper_loop(self):
        """Regularly check for missed operations.

        See cms.io.triggeredservice.TriggeredService._sweeper_loop for
        the full contract: run the sweep once every _sweeper_timeout
        seconds, but a new sweep can be triggered early by
        search_operations_not_done() setting _sweeper_event.

        """
        while True:
            self._sweeper_start = time.monotonic()
            self._sweeper_event.clear()

            try:
                self._sweep()
            except Exception:
                logger.error("Unexpected error when searching for missed "
                             "operations.", exc_info=True)

            remaining = max(self._sweeper_start + self._sweeper_timeout -
                            time.monotonic(), 0)
            try:
                await asyncio.wait_for(self._sweeper_event.wait(), remaining)
            except asyncio.TimeoutError:
                # Expected: gevent.event.Event.wait(timeout) returns
                # False (rather than raising) on timeout, and the loop
                # continues identically either way -- this except
                # clause is that same "continue regardless" behavior,
                # just via asyncio.wait_for's raise-on-timeout API
                # instead of a bool return value.
                pass

    def _sweep(self):
        """Check for missed operations."""
        logger.info("Start looking for missing operations.")
        start_time = time.time()
        counter = self._missing_operations()
        logger.info("Found %d missed operation(s) in %d ms.",
                    counter, (time.time() - start_time) * 1000)

    def _missing_operations(self) -> int:
        """See cms.io.triggeredservice.TriggeredService._missing_operations.

        Base implementation returns 0 -- subclasses override.

        """
        return 0

    @rpc_method
    def search_operations_not_done(self):
        """Make the sweeper loop fire the sweeper as soon as possible."""
        self._sweeper_event.set()

    @rpc_method
    def queue_status(self) -> list[list[QueueEntryDict]]:
        return [executor.get_status() for executor in self._executors]
```

`asyncio.Event.set()`/`.clear()`/`.is_set()` are plain synchronous methods
(only `.wait()` is a coroutine) — `search_operations_not_done`'s
`self._sweeper_event.set()` needs no `await` and no `async def`, exactly
mirroring `TriggeredService`'s original sync `@rpc_method`.

- [ ] **Step 4: Run to verify the enqueue/dequeue and sweeper tests pass**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_triggeredservice_test.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Write and pass the run-loop dispatch test**

Add to `cmstestsuite/unit_tests/io/async_triggeredservice_test.py`:

```python
class TestRunLoop(unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def test_run_dispatches_enqueued_operation(self, mock_get_address):
        mock_get_address.return_value = ("127.0.0.1", 0)
        service = RecordingTriggeredService(shard=0)
        executor = RecordingExecutor()
        service.add_executor(executor)

        op = FakeOperation("evaluate")
        service.enqueue(op)

        await asyncio.sleep(0.1)

        self.assertEqual(executor.executed, [op])
```

Note this test doesn't need to manually spawn/cancel `executor.run()` —
`add_executor` already starts it as a background task (Step 3), matching
how `TriggeredService.add_executor` spawns it via `gevent.spawn` in the
original. The task keeps running after the test method returns (cleaned up
implicitly when the test's event loop is torn down by
`IsolatedAsyncioTestCase`) — this matches how the original gevent greenlet
would also outlive an individual test unless explicitly killed, so no
behavior is lost by not cancelling it here.

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/async_triggeredservice_test.py -v`
Expected: all tests PASS.

- [ ] **Step 7: pyflakes**

Run: `.venv/bin/pyflakes cms/io/async_triggeredservice.py cmstestsuite/unit_tests/io/async_triggeredservice_test.py`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add cms/io/async_triggeredservice.py cmstestsuite/unit_tests/io/async_triggeredservice_test.py
git commit -m "feat(io): add AsyncTriggeredService, mirroring cms.io.triggeredservice"
```

---

### Task 5: Migrate `LogService` (the pilot) + drop `cmsLogService`'s gevent monkey-patch

**Files:**
- Modify: `cms/service/LogService.py`
- Modify: `scripts/cmsLogService`
- Test: `cmstestsuite/unit_tests/service/LogServiceTest.py` (check whether this file already exists — if it doesn't, this step creates it; if it does, extend it rather than replacing it)

**Interfaces:**
- Consumes: `AsyncService` (Task 2).
- Produces: nothing consumed by later tasks in this plan (terminal task — this plan's Non-Goals explicitly exclude migrating any other service).

- [ ] **Step 1: Confirm the starting baseline is green**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml build testcms && docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q --ignore=cmstestsuite/unit_tests/cmscontrib/DownloadMexicanStateFlagsTest.py"`
Expected: no failures beyond the pre-existing, unrelated Pillow/PIL
collection gap already excluded above (confirm the exact count matches
whatever this branch's current baseline is — check `git log` for the most
recent full-suite result reported in this branch's history if unsure).

- [ ] **Step 2: Read the existing `LogServiceTest.py`**

`cmstestsuite/unit_tests/service/LogServiceTest.py` already exists (33
lines, one `TestLogService` class with a `test_last_messages` method).
Read it in full — its `setUp` constructs `LogService(0)` directly, with
**no mocking of `get_service_address` at all**, and this already passes
today against the current, gevent-based `Service.__init__`. Step 5 extends
this exact file; it is not recreated from scratch.

- [ ] **Step 3: Migrate `cms/service/LogService.py`**

Change:

```python
from cms.io import Service, rpc_method
```

to:

```python
from cms.io.async_service import AsyncService
from cms.io.rpc import rpc_method
```

Change:

```python
class LogService(Service):
```

to:

```python
class LogService(AsyncService):
```

Change:

```python
    def __init__(self, shard: int):
        Service.__init__(self, shard)
```

to:

```python
    def __init__(self, shard: int):
        AsyncService.__init__(self, shard)
```

No other changes to this file: `Log` and `last_messages`'s bodies don't
touch gevent, `cms/db/`, or anything else this plan's Global Constraints
restrict — confirm this by re-reading the file's full contents after your
edit and checking `grep -n "gevent" cms/service/LogService.py` returns
nothing.

- [ ] **Step 4: Drop the gevent monkey-patch from `scripts/cmsLogService`**

Remove these three lines from `scripts/cmsLogService`:

```python
# We enable monkey patching to make many libraries gevent-friendly
# (for instance, urllib3, used by requests)
import gevent.monkey
gevent.monkey.patch_all()  # noqa
```

Per the spec's dedicated note on this: `LogService`'s process has no
gevent dependency left once migrated, and running an asyncio event loop in
a gevent-monkey-patched process is unsupported territory. This change is
scoped to this one script only — do not touch any other `scripts/cms*`
entry point, since every other service is still gevent-based until
sub-project 2.4 migrates it individually.

- [ ] **Step 5: Extend `LogServiceTest.py` with one assertion**

Add this test class to `cmstestsuite/unit_tests/service/LogServiceTest.py`,
leaving `TestLogService` and its `test_last_messages` completely
untouched:

```python
from cms.io.async_service import AsyncService


class TestLogServiceIsAsyncService(unittest.TestCase):

    def test_log_service_subclasses_async_service(self):
        self.assertTrue(issubclass(LogService, AsyncService))
```

Add `from cms.io.async_service import AsyncService` near the file's
existing `from cms.service.LogService import LogService` import, not
inline inside the class. No mocking is needed here, or anywhere else in
this file: `TestLogService.setUp`'s existing `LogService(0)` call already
works with zero mocking today (confirmed in Step 2), because
`AsyncService.__init__` (Task 2) never touches a socket at construction
time — unlike the old `Service.__init__`'s `StreamServer(address, ...)`,
binding is deferred entirely to `_async_run()`/`.run()`, which this test
never calls. `get_service_address` still needs to resolve `"LogService"`
successfully either way (both before and after this migration), which it
already does via this project's real `cms.toml`/`cms-testdb.toml` test
configuration — nothing about that lookup changes.

- [ ] **Step 6: Run the test file**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/service/LogServiceTest.py -v`
Expected: both `TestLogService::test_last_messages` (unchanged, must still
pass exactly as it did before this migration) and the new
`TestLogServiceIsAsyncService::test_log_service_subclasses_async_service`
PASS. If `test_last_messages` fails in a way `TestLogService`'s own
unchanged code can't explain, that's a real regression in Step 3's
migration — stop and investigate rather than adding mocks to make the
existing test pass differently than it did before.

- [ ] **Step 7: Run the full suite against the real Docker test image**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml build testcms && docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q --ignore=cmstestsuite/unit_tests/cmscontrib/DownloadMexicanStateFlagsTest.py"`
Expected: identical pass/fail set to Step 1's baseline, plus this task's
new tests passing. This is the pilot's real validation per the spec: every
other service in the suite is still gevent-based and will send it real log
RPCs during this run (any service's `initialize_logging()` connects to
`LogService` and streams log records to it) — if the suite stays green
with `LogService` now running on `AsyncService`, that's the proof the
coexistence strategy holds in a real running system, not just in an
isolated unit test.

- [ ] **Step 8: pyflakes**

Run: `.venv/bin/pyflakes cms/service/LogService.py cmstestsuite/unit_tests/service/LogServiceTest.py`
Expected: no output.

- [ ] **Step 9: Verify no gevent import remains in the migrated files**

Run: `grep -n "gevent" cms/service/LogService.py scripts/cmsLogService`
Expected: no output.

- [ ] **Step 10: Commit**

```bash
git add cms/service/LogService.py scripts/cmsLogService cmstestsuite/unit_tests/service/LogServiceTest.py
git commit -m "feat(io): migrate LogService to AsyncService as end-to-end pilot

LogService now runs on the new asyncio framework (sub-project 2.2)
while every other service stays on gevent, proving the coexistence
strategy 2.4's per-service rollout depends on. Drops
gevent.monkey.patch_all() from scripts/cmsLogService, since its
process no longer has any gevent dependency and running asyncio
inside a gevent-monkey-patched process is unsupported."
```
