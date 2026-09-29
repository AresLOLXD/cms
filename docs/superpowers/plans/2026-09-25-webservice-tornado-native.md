# WebService → AsyncService + Tornado Native Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `cms.io.web_service.WebService` from `Service` (gevent) +
`tornado.wsgi.WSGIApplication` + `gevent.pywsgi.WSGIServer` to `AsyncService`
(asyncio) + a real `tornado.web.Application` + `tornado.httpserver.HTTPServer`
running on the same event loop, with no concrete `AdminWebServer`/
`ContestWebServer` handler touched.

**Architecture:** `WebService` becomes the sixth `AsyncService` subclass in
this codebase. Tornado 6+'s `IOLoop` is a thin wrapper over whatever asyncio
loop is currently running, so `HTTPServer` binds and serves inside
`AsyncService`'s own `_async_run()` coroutine with no second thread or loop.
Every WSGI-era middleware (`RPCMiddleware`, `FileServerMiddleware`,
`SharedDataMiddleware`, `ProxyFix`) is replaced by a native Tornado
equivalent with the same externally observable HTTP contract.

**Tech Stack:** Python 3.12, Tornado (bumped from the stale `4.5.3` pin to
the `6.5.5` actually installed in this project's venv), `asyncio`,
`cms.io.async_service.AsyncService` (built in sub-project 2.2).

**Spec:** `docs/superpowers/specs/2026-09-25-webservice-tornado-native-design.md`

## Global Constraints

- Branch `beta` only; this modernization line never merges to `main` (project-wide policy).
- No concrete handler in `cms/server/admin/handlers/` or `cms/server/contest/handlers/` is created, modified, or even imported by name in this plan — every file there stays exactly as it is (spec Non-Goals).
- No change to the wire protocol of `/rpc/<service>/<shard>/<method>` or to any static/file-serving URL contract (spec Non-Goals) — same status codes, same response body shape, same URL paths.
- `Worker` stays out of scope for the whole modernization effort (established policy).
- `cms/io/service.py`/`cms/io/rpc.py` (the gevent stack) is not touched (spec Non-Goals).
- Every new async method whose body does blocking I/O (DB via `FileCacher`, disk reads) must offload that work via `loop.run_in_executor`, matching the established pattern from sub-projects 2.3/2.4 — never block the event loop directly.
- Tests follow the `unittest.IsolatedAsyncioTestCase` + `ServiceLoggingIsolationMixin` (from `cmstestsuite/unit_tests/servicelogmixin.py`) pattern established across 2.2-2.4; no mocking of asyncio, the RPC layer, or `SessionGen`.
- PEP 8 / PEP 484 / this project's docstring format (imperative first line, then args/return/raise — see `CONTRIBUTING.md`); `pyflakes` clean on every file touched.

## Review Focus

- **Tornado version mismatch silently reintroduced**: if any new code (or a stale cached `.venv`) ends up running against the old `tornado==4.5.3` pin instead of the bumped one, the whole native-async architecture silently falls back to pre-asyncio-native `IOLoop` behavior with no obvious error — Task 1's own test must fail loudly if this happens, not just "happen to still pass."
- **`MultiLocationStaticFileHandler` resolving to the wrong file when two locations both have a match**: a static asset being silently served from the wrong package (e.g. CWS getting AWS's copy of a shared file, or vice versa) is a real, visible regression a casual read of the code might not catch — Task 4's tests must assert override order, not just "a file gets served."
- **`RPCHandler` losing exact byte-for-byte error-response parity with `RPCMiddleware`** (wrong status code, wrong JSON shape, or a missing `Accept`/`Content-Type` check) for any client currently depending on it — Task 5's tests must cover every branch `RPCMiddleware` has today, not just the happy path.
- **`FileHandlerMixin.fetch()`'s public signature changing** in a way that breaks 2.5b/2.5c's future call sites before they're even written — Task 6 must keep `fetch(digest, content_type, filename=None, disposition=None)`'s exact parameter names/order, only changing it from sync-header-setting to an awaited coroutine.
- **Shutdown leaving an in-flight HTTP request half-finished or a listening socket not actually released**, which would silently look fine in a quick manual test but break under a real restart/reload cycle — Task 3's shutdown test must exercise this directly (a real in-flight request racing `exit()`), not just check that `run()` returns.

---

## Task 1: Bump the Tornado dependency pin and remove obsolete pre-5.0 compatibility shims

**Files:**
- Modify: `pyproject.toml` (the `"tornado==4.5.3"` line and the `"backports.ssl-match-hostname==3.7.0.1"` line)
- Modify: `constraints.txt` (the `tornado==4.5.3` line)
- Modify: `cms/server/util.py:33-38` (remove the `collections.MutableMapping` monkey-patch block)
- Test: `cmstestsuite/unit_tests/server/util_test.py` (new, minimal)

**Interfaces:**
- Consumes: nothing from other tasks (this is the first task).
- Produces: a working project venv on Tornado 6.5.5 with no leftover Tornado-4-era workarounds, confirmed by the existing test suite still passing (baseline for every later task).

- [ ] **Step 1: Confirm the actually-installed Tornado version and that it's importable cleanly**

Run: `.venv/bin/python3 -c "import tornado; print(tornado.version)"`
Expected: `6.5.5` (confirmed already present in this project's venv; if a different version prints, stop and report — this task's later steps assume 6.5.5's API surface).

- [ ] **Step 2: Update the dependency pins**

In `pyproject.toml`, change:
```toml
    "tornado==4.5.3",         # http://www.tornadoweb.org/en/stable/releases.html
```
to:
```toml
    "tornado==6.5.5",         # http://www.tornadoweb.org/en/stable/releases.html
```
and remove this line entirely (it exists only to support `tornado<5.0`):
```toml
    "backports.ssl-match-hostname==3.7.0.1", # required by tornado<5.0
```
In `constraints.txt`, change `tornado==4.5.3` to `tornado==6.5.5`, and remove
the `backports.ssl-match-hostname==3.7.0.1` line if present there too (check
with `grep -n backports constraints.txt` first).

- [ ] **Step 3: Remove the now-unnecessary `collections.MutableMapping` monkey-patch**

In `cms/server/util.py`, delete lines 33-38:
```python
import collections
try:
    collections.MutableMapping
except:
    # Monkey-patch: Tornado 4.5.3 does not work on Python 3.11 by default
    collections.MutableMapping = collections.abc.MutableMapping
```
(This existed only because Tornado 4.5.3 imports `collections.MutableMapping`
directly, removed from `collections` itself in Python 3.10+; Tornado 6.x
doesn't do this.) Leave the surrounding imports (`typing`, `from tornado.web
import RequestHandler`, etc.) exactly as they are.

- [ ] **Step 4: Write a test confirming the monkey-patch is really gone and unnecessary**

```python
#!/usr/bin/env python3

"""Tests for cms.server.util's module-level setup."""

import unittest


class TestNoLegacyTornadoWorkarounds(unittest.TestCase):

    def test_no_mutablemapping_monkeypatch_needed(self):
        # cms.server.util used to monkey-patch collections.MutableMapping
        # back onto the collections module for Tornado 4.5.3's benefit.
        # Tornado 6.x doesn't need it -- importing this module shouldn't
        # touch collections.MutableMapping at all.
        import collections
        had_attr_before = hasattr(collections, "MutableMapping")
        import cms.server.util  # noqa: F401 (import side effect is the test)
        has_attr_after = hasattr(collections, "MutableMapping")
        self.assertEqual(had_attr_before, has_attr_after)

    def test_tornado_version_is_6x(self):
        import tornado
        self.assertTrue(
            tornado.version.startswith("6."),
            "Expected Tornado 6.x, got %r -- the rest of this migration "
            "assumes Tornado 6's native asyncio IOLoop integration." %
            tornado.version)
```

Save as `cmstestsuite/unit_tests/server/util_test.py`. If
`cmstestsuite/unit_tests/server/` has no `__init__.py`, create an empty one
(check first: `ls cmstestsuite/unit_tests/server/__init__.py`).

- [ ] **Step 5: Run the new test**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/util_test.py -v`
Expected: 2 passed.

- [ ] **Step 6: Re-install dependencies from the updated pins and confirm the existing test suite still passes**

Run: `.venv/bin/pip install -e ".[devel]" -c constraints.txt` (or this
project's equivalent sync command — check `CLAUDE.md`'s Installation section
if unsure), then:
Run: `.venv/bin/pyflakes cms/server/util.py`
Expected: clean, no output.
Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/ cmstestsuite/unit_tests/service/ -q`
Expected: all passing (this is the pre-migration baseline — nothing here
should break just from the version bump, since no server/handler code is
touched yet beyond the monkey-patch removal).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml constraints.txt cms/server/util.py cmstestsuite/unit_tests/server/util_test.py
git commit -m "chore: bump tornado pin to 6.5.5, drop pre-5.0 compat shims

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Prove Tornado's HTTPServer and AsyncService share one asyncio event loop

This is the single riskiest assumption the whole sub-project depends on
(spec Risks). Isolate and prove it before writing any other code against it.

**Files:**
- Test: `cmstestsuite/unit_tests/io/tornado_asyncio_loop_test.py` (new)

**Interfaces:**
- Consumes: `cms.io.async_service.AsyncService` (existing, from sub-project 2.2).
- Produces: empirical confirmation (or refutation) of the loop-sharing
  assumption, before Task 3 builds `WebService` on top of it. If this task's
  test fails in a way that shows the assumption is wrong, STOP and report —
  do not proceed to Task 3 with a workaround improvised on the spot.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3

"""Tests confirming Tornado's HTTPServer runs on the same asyncio loop
AsyncService already owns -- the foundational assumption for migrating
WebService onto AsyncService (see the sub-project 2.5a design spec's
Architecture section)."""

import asyncio
import unittest

import tornado.httpserver
import tornado.ioloop
import tornado.web

from cms.io.async_service import AsyncService


class EchoHandler(tornado.web.RequestHandler):
    def get(self):
        self.write("ok")


class TestTornadoSharesAsyncioLoop(unittest.IsolatedAsyncioTestCase):

    async def test_ioloop_current_wraps_the_running_asyncio_loop(self):
        # The core claim: inside a coroutine already running under
        # asyncio (as every AsyncService method does), Tornado's
        # IOLoop.current() must return a wrapper around that exact
        # running loop, not a separate one.
        running_loop = asyncio.get_running_loop()
        tornado_loop = tornado.ioloop.IOLoop.current()
        self.assertIs(tornado_loop.asyncio_loop, running_loop)

    async def test_httpserver_serves_real_requests_without_its_own_thread(self):
        application = tornado.web.Application([(r"/", EchoHandler)])
        server = tornado.httpserver.HTTPServer(application)
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        server.add_sockets(sockets)
        port = sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(1024), timeout=5)
            self.assertIn(b"200 OK", response)
            self.assertIn(b"ok", response)
        finally:
            writer.close()
            server.stop()

    async def test_httpserver_and_asyncservice_coexist_in_one_process(self):
        # An AsyncService instance's own _async_run() sets self._loop;
        # confirm a Tornado HTTPServer constructed and started from
        # within that same running context uses the identical loop,
        # with no second thread involved.
        from unittest.mock import patch
        from cms.conf import Address

        with patch("cms.io.async_service.get_service_address",
                  return_value=Address("127.0.0.1", 0)):
            service = AsyncService(shard=0)
            run_task = asyncio.create_task(service._async_run())
            await asyncio.sleep(0.05)
            self.assertIsNotNone(service._loop)
            self.assertIs(service._loop, asyncio.get_running_loop())

            application = tornado.web.Application([(r"/", EchoHandler)])
            server = tornado.httpserver.HTTPServer(application)
            sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
            server.add_sockets(sockets)
            port = sockets[0].getsockname()[1]
            try:
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", port)
                writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
                await writer.drain()
                response = await asyncio.wait_for(
                    reader.read(1024), timeout=5)
                self.assertIn(b"200 OK", response)
            finally:
                writer.close()
                server.stop()
                service.exit()
                await asyncio.wait_for(run_task, timeout=2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to see whether the assumption holds**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/tornado_asyncio_loop_test.py -v`
Expected: 3 passed. If any of the three fail, STOP — read the failure
carefully, determine whether it's a fixable test bug (e.g. a wrong import,
an `AsyncService` constructor detail this test got wrong) or a genuine
refutation of the loop-sharing assumption. If it's the latter, do not
continue to Task 3; report back with the failure details so the design can
be revisited.

- [ ] **Step 3: Commit**

```bash
git add cmstestsuite/unit_tests/io/tornado_asyncio_loop_test.py
git commit -m "test(io): prove Tornado HTTPServer and AsyncService share one event loop

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Rewrite `WebService` to extend `AsyncService` with a native `Application`/`HTTPServer`

**Files:**
- Modify: `cms/io/web_service.py` (full rewrite of the `WebService` class; `StaticFileHasher` stays, see Task 4 for where it's used)
- Test: `cmstestsuite/unit_tests/io/web_service_test.py` (new)

**Interfaces:**
- Consumes: `AsyncService` (`cms.io.async_service`), the loop-sharing fact
  proven in Task 2.
- Produces: `WebService.__init__(self, listen_port, handlers, parameters,
  shard=0, listen_address="")`, `self.application` (a `tornado.web.Application`,
  `self.application.service is self`), `self.static_file_hasher`
  (unchanged, `StaticFileHasher` from the existing code), `self.file_cacher`
  (unchanged, a `FileCacher`), `WebService.exit()` (inherited from
  `AsyncService`, now also stops accepting new HTTP connections). Later
  tasks (4-7) add handler classes and wire them into `handlers`/routes; this
  task doesn't register any custom route beyond the trivial one its own test
  needs.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3

"""Tests for cms.io.web_service.WebService."""

import asyncio
import unittest
from unittest.mock import patch

import tornado.web

from cms.conf import Address
from cms.io.web_service import WebService
from cmstestsuite.unit_tests.servicelogmixin import ServiceLoggingIsolationMixin


class EchoHandler(tornado.web.RequestHandler):
    def get(self):
        self.write("hello from %s" % type(self.application.service).__name__)


class WebServiceTest(ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    async def asyncSetUp(self, mock_get_address):
        mock_get_address.return_value = Address("127.0.0.1", 0)
        self.service = WebService(
            listen_port=0, handlers=[(r"/", EchoHandler)],
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
        # still get its response, not be cut off mid-flight.
        port = self.service._http_server_sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            self.service.exit()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"200 OK", response)
        await asyncio.wait_for(self.run_task, timeout=5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/web_service_test.py -v`
Expected: FAIL (`WebService` still built on the old `Service`/WSGI stack;
`_http_server_sockets` doesn't exist yet).

- [ ] **Step 3: Rewrite `cms/io/web_service.py`**

Replace the file's imports (remove `tornado.wsgi`, `gevent.pywsgi.WSGIServer`,
the three Werkzeug middleware imports, `.service.Service`, `.web_rpc.RPCMiddleware`
— those three middlewares and `RPCMiddleware` are rebuilt as native Tornado
pieces in Tasks 4/5/7, not imported here) with:
```python
import asyncio
import hashlib
import logging
import importlib.resources

import tornado.httpserver
import tornado.netutil
import tornado.web

from cms.db.filecacher import FileCacher
from .async_service import AsyncService
```
Keep `StaticFileHasher` exactly as it is (lines 51-102 of the current file —
unrelated to the server transport, still used by handlers via
`self.static_file_hasher`).

Replace the `WebService` class with:
```python
class WebService(AsyncService):
    """RPC service with Web server capabilities.

    """

    def __init__(
        self,
        listen_port: int,
        handlers: list,
        parameters: dict,
        shard: int = 0,
        listen_address: str = "",
    ):
        super().__init__(shard)

        static_files = parameters.pop('static_files', [])
        parameters.pop('rpc_enabled', False)
        parameters.pop('rpc_auth', None)
        parameters.pop('auth_middleware', None)
        num_proxies_used = parameters.pop('num_proxies_used', None) or 0

        self.application = tornado.web.Application(handlers, **parameters)
        self.application.service = self

        self.static_file_hasher = StaticFileHasher(static_files)

        self.file_cacher = FileCacher(self)

        self._listen_port = listen_port
        self._listen_address = listen_address
        self._http_server = tornado.httpserver.HTTPServer(
            self.application, xheaders=num_proxies_used > 0)
        self._http_server_sockets: list = []

    def run(self) -> bool:
        """Start the WebService.

        Both the HTTP server and the RPC server are started, on the
        same event loop.

        """
        return asyncio.run(self._async_run())

    async def _async_run(self) -> bool:
        self._http_server_sockets = tornado.netutil.bind_sockets(
            self._listen_port, address=self._listen_address or None)
        self._http_server.add_sockets(self._http_server_sockets)
        try:
            return await super()._async_run()
        finally:
            self._http_server.stop()
            await self._http_server.close_all_connections()
```

(The `rpc_enabled`/`rpc_auth`/`auth_middleware` parameters are popped but
unused in this task — Tasks 5 and 7 wire them into real behavior once
`RPCHandler` and the auth extension point exist; popping them now keeps
`tornado.web.Application(handlers, **parameters)` from choking on unknown
keys it would otherwise just silently store as inert settings, which is
harmless either way but popping them here documents that this task
consciously doesn't implement them yet.)

Check `AsyncService`'s actual current `_async_run`/`run` method names and
signatures in `cms/io/async_service.py` before writing this override — the
sketch above assumes `_async_run` is an `async def` coroutine method and
`run()` is a sync entry point calling `asyncio.run(...)`; confirm this
matches the real current code and adjust the override to call the real
method names if they differ.

- [ ] **Step 4: Run the test again to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/web_service_test.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run pyflakes**

Run: `.venv/bin/pyflakes cms/io/web_service.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add cms/io/web_service.py cmstestsuite/unit_tests/io/web_service_test.py
git commit -m "refactor(io): migrate WebService to AsyncService + native Tornado HTTPServer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: `MultiLocationStaticFileHandler` (replaces `SharedDataMiddleware`)

**Files:**
- Create: `cms/io/static_handler.py`
- Test: `cmstestsuite/unit_tests/io/static_handler_test.py` (new)

**Interfaces:**
- Consumes: nothing from other tasks (pure Tornado + `importlib.resources`).
- Produces: `MultiLocationStaticFileHandler`, a `tornado.web.StaticFileHandler`
  subclass configured via `.make_handler_spec(locations: list[tuple[str, str]])
  -> tuple[str, type, dict]`-style Tornado route entry (exact factory shape
  decided in Step 3 below) that Task 8 wires into `AdminWebServer`/
  `ContestWebServer`'s route lists in a later sub-project (2.5b/2.5c) — this
  task only builds and tests the handler class itself, not any concrete
  route registration for AWS/CWS.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/static_handler_test.py -v`
Expected: FAIL (`cms.io.static_handler` doesn't exist).

- [ ] **Step 3: Implement `cms/io/static_handler.py`**

```python
#!/usr/bin/env python3

"""A tornado.web.StaticFileHandler variant that searches an ordered list
of directories for each requested path, serving the first match found in
*reverse* location order (later-registered locations override earlier
ones for the same relative path) -- reproducing the "later added,
higher priority" behavior of Werkzeug's SharedDataMiddleware, which the
pre-Tornado-native WebService used for static/doc file serving.

"""

import os
import typing

import tornado.web


class MultiLocationStaticFileHandler(tornado.web.StaticFileHandler):
    """Serve static files from the first matching directory in a list.

    Directories are checked in reverse of the order given to
    make_route() -- i.e. a later directory in the list overrides an
    earlier one for the same relative path, matching
    SharedDataMiddleware's existing convention (callers that want a
    package's own static files to take priority over a shared default
    list that package's directory last).

    """

    # Set by make_route() via the `path` handler kwarg, per-instance
    # (Tornado re-instantiates the handler class for every request, but
    # passes the handler kwargs from the route registration each time).

    def initialize(self, locations: list[str]):
        # StaticFileHandler.initialize() normally takes a single `path`;
        # we override the whole method instead of calling super(), since
        # we manage directory resolution ourselves in get_absolute_path.
        self.root = None  # unused; kept for StaticFileHandler compatibility
        self._locations = list(reversed(locations))

    def get_absolute_path(self, root: str, path: str) -> str:
        for location in self._locations:
            candidate = os.path.abspath(os.path.join(location, path))
            # Guard against path traversal escaping the location root,
            # matching StaticFileHandler's own validate_absolute_path
            # check (which runs after this, on whatever we return).
            if not candidate.startswith(os.path.abspath(location) + os.sep):
                continue
            if os.path.isfile(candidate):
                return candidate
        # No location has it: return a path StaticFileHandler's own
        # existence check will correctly 404 on.
        return os.path.join(self._locations[0], path) if self._locations else path

    @classmethod
    def make_route(
        cls, url_pattern: str, locations: list[str]
    ) -> tuple[str, type, dict]:
        """Build a Tornado route entry for this handler.

        url_pattern: the URL regex, with one capture group for the
            relative file path (e.g. r"/static/(.*)").
        locations: directories to search, in "later overrides earlier"
            order.

        return: a (pattern, handler_class, kwargs) tuple usable directly
            in a tornado.web.Application's handlers list.

        """
        return (url_pattern, cls, {"path": "", "locations": locations})
```

Note `initialize(self, locations: list[str])` doesn't accept `path` as a
required positional the way the base class's route wiring normally expects
— `make_route` passes `"path": ""` alongside `"locations": [...]` in the
handler kwargs dict specifically so `StaticFileHandler.__init__`'s own
argument plumbing (which expects a `path` kwarg to exist) doesn't error;
`get_absolute_path` overrides the base class's use of `root`/`path`
entirely, so the empty placeholder value is never actually used for lookup.
Verify this doesn't raise at handler-construction time by running Step 4
below — if `tornado.web.StaticFileHandler`'s actual 6.5.5 implementation
requires something this sketch doesn't account for, adjust based on the
real error message rather than guessing further.

- [ ] **Step 4: Run the test again to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/static_handler_test.py -v`
Expected: 3 passed. If `initialize`/`get_absolute_path`'s interaction with
`StaticFileHandler`'s base implementation doesn't work as sketched, fix it
based on the actual Tornado 6.5.5 source
(`.venv/lib/python3.12/site-packages/tornado/web.py`, `StaticFileHandler`
class) rather than the sketch above, keeping the same public `make_route`
interface and test-observable behavior.

- [ ] **Step 5: Run pyflakes**

Run: `.venv/bin/pyflakes cms/io/static_handler.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add cms/io/static_handler.py cmstestsuite/unit_tests/io/static_handler_test.py
git commit -m "feat(io): add MultiLocationStaticFileHandler, replacing SharedDataMiddleware

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: `RPCHandler` (replaces `RPCMiddleware`)

**Files:**
- Create: `cms/io/web_rpc.py` (replace the existing WSGI-based `RPCMiddleware`
  entirely — same filename, new content; delete `cms/io/web_rpc_test.py` if
  it exists and predates this task, checking first with
  `find cmstestsuite -iname "*web_rpc*"`)
- Test: `cmstestsuite/unit_tests/io/web_rpc_test.py` (new, or rewritten if
  a prior WSGI-era test file with this name exists)

**Interfaces:**
- Consumes: `AsyncRemoteServiceClient` (via `service.remote_services`, as
  every 2.4-migrated service already does), `RPCError` (`cms.io.rpc`).
- Produces: `RPCHandler`, a `tornado.web.RequestHandler` subclass, and
  `RPCHandler.make_route(url_prefix: str, rpc_auth: Callable[[str, int, str], bool] | None) -> tuple[str, type, dict]`
  (same factory-returns-a-route-tuple convention as
  `MultiLocationStaticFileHandler.make_route`, for consistency — Task 8
  wires it into `WebService.__init__` when `rpc_enabled` is set).

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3

"""Tests for cms.io.web_rpc.RPCHandler."""

import asyncio
import json
import unittest
from unittest.mock import patch

import tornado.httpserver
import tornado.netutil
import tornado.web

from cms.conf import Address, ServiceCoord
from cms.io.async_service import AsyncService
from cms.io.rpc import rpc_method
from cms.io.web_rpc import RPCHandler
from cmstestsuite.unit_tests.servicelogmixin import ServiceLoggingIsolationMixin


class EchoingRemoteService(AsyncService):
    @rpc_method
    def echo(self, value: str) -> str:
        return value

    @rpc_method
    def boom(self):
        raise ValueError("deliberate failure")


class RPCHandlerTest(ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase):

    @patch("cms.io.async_service.get_service_address")
    @patch("cms.io.async_rpc.get_service_address")
    async def asyncSetUp(self, mock_rpc_address, mock_service_address):
        remote_port_holder = {}

        def address_of(coord):
            if coord.name == "EchoingRemoteService":
                return remote_port_holder["address"]
            return Address("127.0.0.1", 0)

        mock_service_address.side_effect = address_of
        mock_rpc_address.side_effect = address_of

        self.remote = EchoingRemoteService(shard=0)
        self.remote_task = asyncio.create_task(self.remote._async_run())
        await asyncio.sleep(0.05)
        remote_port_holder["address"] = Address(
            "127.0.0.1", self.remote._server.sockets[0].getsockname()[1])

        self.frontend = AsyncService(shard=0)
        self.frontend_task = asyncio.create_task(self.frontend._async_run())
        await asyncio.sleep(0.05)
        self.frontend.connect_to(ServiceCoord("EchoingRemoteService", 0))
        await asyncio.sleep(0.1)  # let the connection establish

        handler_spec = RPCHandler.make_route(r"/rpc/(.*)/(.*)/(.*)", None)
        application = tornado.web.Application([handler_spec])
        application.service = self.frontend
        self.server = tornado.httpserver.HTTPServer(application)
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        self.server.add_sockets(sockets)
        self.port = sockets[0].getsockname()[1]

        self.addAsyncCleanup(self._teardown)

    async def _teardown(self):
        self.server.stop()
        await self.server.close_all_connections()
        self.remote.exit()
        self.frontend.exit()
        await asyncio.wait_for(self.remote_task, timeout=5)
        await asyncio.wait_for(self.frontend_task, timeout=5)

    async def _post(self, path, body):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            payload = json.dumps(body).encode()
            request = (
                "POST %s HTTP/1.1\r\nHost: localhost\r\n"
                "Content-Type: application/json\r\n"
                "Accept: application/json\r\n"
                "Content-Length: %d\r\n\r\n" % (path, len(payload))
            ).encode() + payload
            writer.write(request)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(8192), timeout=5)
        finally:
            writer.close()
        status_line = response.split(b"\r\n", 1)[0]
        body_start = response.index(b"\r\n\r\n") + 4
        return status_line, json.loads(response[body_start:] or b"null")

    async def test_successful_call(self):
        status, body = await self._post(
            "/rpc/EchoingRemoteService/0/echo", {"value": "hi"})
        self.assertIn(b"200", status)
        self.assertEqual(body, {"data": "hi", "error": None})

    async def test_rpc_error_propagates_as_json_error(self):
        status, body = await self._post(
            "/rpc/EchoingRemoteService/0/boom", {})
        self.assertIn(b"200", status)
        self.assertIsNone(body["data"])
        self.assertIsNotNone(body["error"])

    async def test_unknown_service_is_404(self):
        status, _ = await self._post("/rpc/NoSuchService/0/echo", {})
        self.assertIn(b"404", status)

    async def test_malformed_json_is_400(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            payload = b"not json"
            request = (
                "POST /rpc/EchoingRemoteService/0/echo HTTP/1.1\r\n"
                "Host: localhost\r\nContent-Type: application/json\r\n"
                "Accept: application/json\r\n"
                "Content-Length: %d\r\n\r\n" % len(payload)
            ).encode() + payload
            writer.write(request)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"400", response.split(b"\r\n", 1)[0])

    async def test_wrong_content_type_is_415(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            payload = b"{}"
            request = (
                "POST /rpc/EchoingRemoteService/0/echo HTTP/1.1\r\n"
                "Host: localhost\r\nContent-Type: text/plain\r\n"
                "Accept: application/json\r\n"
                "Content-Length: %d\r\n\r\n" % len(payload)
            ).encode() + payload
            writer.write(request)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()
        self.assertIn(b"415", response.split(b"\r\n", 1)[0])

    async def test_auth_rejection_is_403(self):
        self.server.stop()
        await self.server.close_all_connections()
        handler_spec = RPCHandler.make_route(
            r"/rpc/(.*)/(.*)/(.*)", lambda service, shard, method: False)
        application = tornado.web.Application([handler_spec])
        application.service = self.frontend
        self.server = tornado.httpserver.HTTPServer(application)
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        self.server.add_sockets(sockets)
        self.port = sockets[0].getsockname()[1]

        status, _ = await self._post(
            "/rpc/EchoingRemoteService/0/echo", {"value": "hi"})
        self.assertIn(b"403", status)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/web_rpc_test.py -v`
Expected: FAIL (`RPCHandler` doesn't exist yet).

- [ ] **Step 3: Implement `cms/io/web_rpc.py`**

```python
#!/usr/bin/env python3

"""An HTTP interface to the internal RPC communications, native-Tornado
replacement for the old WSGI-based RPCMiddleware.

"""

import asyncio
import json
import logging
from collections.abc import Callable

import tornado.web

from cms import ServiceCoord
from cms.io.rpc import RPCError


logger = logging.getLogger(__name__)


RPC_TIMEOUT_SECONDS = 60.0


class RPCHandler(tornado.web.RequestHandler):
    """An HTTP-to-RPC proxy for the service this application runs for.

    Each remote RPC method can be called by making a POST request to
    "/<prefix>/<service>/<shard>/<method>". Arguments for the RPC
    should be given as a JSON-encoded object in the request body
    (always present, even if empty). See RPCMiddleware's own historical
    docstring (cms/io/web_rpc.py, pre-2.5a) for the full wire-format
    rationale this preserves unchanged.

    """

    def initialize(
        self, rpc_auth: Callable[[str, int, str], bool] | None = None
    ):
        self._rpc_auth = rpc_auth

    async def post(self, service_name: str, shard: str, method: str):
        coord = ServiceCoord(service_name, int(shard))

        if coord not in self.application.service.remote_services:
            raise tornado.web.HTTPError(404)

        if self._rpc_auth is not None and not self._rpc_auth(
                service_name, int(shard), method):
            raise tornado.web.HTTPError(403)

        content_type = self.request.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            raise tornado.web.HTTPError(415)

        accept = self.request.headers.get("Accept", "*/*")
        if "application/json" not in accept and "*/*" not in accept:
            raise tornado.web.HTTPError(406)

        try:
            data = json.loads(self.request.body)
        except ValueError:
            raise tornado.web.HTTPError(400)

        remote_service = self.application.service.remote_services[coord]
        if not remote_service.connected:
            raise tornado.web.HTTPError(503)

        try:
            result = await asyncio.wait_for(
                getattr(remote_service, method)(**data),
                timeout=RPC_TIMEOUT_SECONDS)
            error = None
        except asyncio.TimeoutError:
            result = None
            error = "Timed out waiting for a reply."
        except RPCError as rpc_error:
            result = None
            error = str(rpc_error)

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"data": result, "error": error}))

    @classmethod
    def make_route(
        cls,
        url_pattern: str,
        rpc_auth: Callable[[str, int, str], bool] | None,
    ) -> tuple[str, type, dict]:
        """Build a Tornado route entry for this handler.

        url_pattern: the URL regex, with three capture groups for
            service name, shard, and method (e.g.
            r"/rpc/([^/]+)/([0-9]+)/([^/]+)").
        rpc_auth: a function taking (service_name, shard, method) and
            returning whether the request is allowed, or None to allow
            all requests.

        return: a (pattern, handler_class, kwargs) tuple usable
            directly in a tornado.web.Application's handlers list.

        """
        return (url_pattern, cls, {"rpc_auth": rpc_auth})
```

- [ ] **Step 4: Run the test again to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/web_rpc_test.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run pyflakes**

Run: `.venv/bin/pyflakes cms/io/web_rpc.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add cms/io/web_rpc.py cmstestsuite/unit_tests/io/web_rpc_test.py
git commit -m "refactor(io): migrate RPCMiddleware to native RPCHandler

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: `FileHandlerMixin.fetch()` streams directly; delete `FileServerMiddleware`

**Files:**
- Modify: `cms/server/util.py` (`FileHandlerMixin.fetch`, lines ~70-98)
- Delete: `cms/server/file_middleware.py`
- Delete: `cmstestsuite/unit_tests/server/file_middleware_test.py` (its
  subject no longer exists; confirm no other test imports from it first —
  `grep -rln file_middleware cmstestsuite/` — before deleting)
- Test: `cmstestsuite/unit_tests/server/util_test.py` (extend the file Task 1 created)

**Interfaces:**
- Consumes: `FileCacher` (`cms.db.filecacher`, unchanged, sync — per sub-project
  2.3's established non-goal), `TombstoneError` (`cms.db.filecacher`).
- Produces: `async def fetch(self, digest: str, content_type: str,
  filename: str | None = None, disposition: str | None = None) -> None` —
  same name and parameter order as today, now a coroutine 2.5b/2.5c's
  future handler code must `await`.

- [ ] **Step 1: Write the failing test**

Append to `cmstestsuite/unit_tests/server/util_test.py` (from Task 1):
```python
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
            writer.write(b"GET /f HTTP/1.1\r\nHost: localhost\r\n\r\n")
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
```

(This appends `FetchStreamsFileTest` to the existing `util_test.py` from
Task 1 — keep `TestNoLegacyTornadoWorkarounds` in the same file, just add
this new class below it.)

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/util_test.py -v`
Expected: the new test FAILs (`fetch()` still does the old
header-setting-only WSGI handoff, doesn't actually write body content).

- [ ] **Step 3: Rewrite `FileHandlerMixin.fetch`**

In `cms/server/util.py`, remove the
`from cms.server.file_middleware import FileServerMiddleware` import, and
replace the `FileHandlerMixin` class body with:
```python
class FileHandlerMixin(RequestHandler):

    """Provide methods for serving files, streaming them directly from
    FileCacher without buffering the whole file in memory.

    """

    async def fetch(
        self,
        digest: str,
        content_type: str,
        filename: str | None = None,
        disposition: str | None = None,
    ):
        """Serve the file with the given digest.

        digest: the digest of the file that has to be served.
        content_type: the MIME type the file should be served as.
        filename: the name the file should be served as.
        disposition: value to set the Content-Disposition header to.

        """
        loop = asyncio.get_running_loop()
        file_cacher = self.application.service.file_cacher
        try:
            fobj, size = await loop.run_in_executor(
                None, self._open_file_and_size, file_cacher, digest)
        except KeyError:
            raise HTTPError(404)
        except TombstoneError:
            raise HTTPError(503)

        self.set_header("Content-Type", content_type)
        if filename is not None:
            disposition_value = disposition or "attachment"
            self.set_header(
                "Content-Disposition",
                '%s; filename="%s"' % (disposition_value, filename))

        chunk_size = file_cacher.CHUNK_SIZE
        try:
            while True:
                chunk = await loop.run_in_executor(None, fobj.read, chunk_size)
                if not chunk:
                    break
                self.write(chunk)
                await self.flush()
        finally:
            fobj.close()

    @staticmethod
    def _open_file_and_size(file_cacher, digest: str):
        """Open a cached file and get its size, synchronously.

        Runs inside loop.run_in_executor -- FileCacher's DB-backed
        lookup is a blocking call (see sub-project 2.3's design spec:
        FileCacher/DBBackend stay sync-only).

        """
        fobj = file_cacher.get_file(digest)
        size = file_cacher.get_size(digest)
        return fobj, size
```
Add the two new imports this needs at the top of the file:
```python
import asyncio

from tornado.web import HTTPError, RequestHandler
```
(merge with the existing `from tornado.web import RequestHandler` line
rather than duplicating it), and:
```python
from cms.db.filecacher import TombstoneError
```

- [ ] **Step 4: Delete the now-fully-unused `FileServerMiddleware`**

Run: `grep -rln "file_middleware\|FileServerMiddleware" --include="*.py" .`
Expected output: only `cmstestsuite/unit_tests/server/file_middleware_test.py`
(its own test, about to be deleted) — if `cms/io/web_service.py` or
`cms/server/util.py` still show up, you missed an import removal above; fix
that first.
```bash
git rm cms/server/file_middleware.py cmstestsuite/unit_tests/server/file_middleware_test.py
```

- [ ] **Step 5: Run the test again to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/util_test.py -v`
Expected: 3 passed (the 2 from Task 1 plus this task's new one).

- [ ] **Step 6: Run pyflakes**

Run: `.venv/bin/pyflakes cms/server/util.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add cms/server/util.py cmstestsuite/unit_tests/server/util_test.py
git commit -m "refactor(server): stream fetch() directly, remove FileServerMiddleware

FileServerMiddleware existed only to work around Tornado's WSGI
adapter buffering entire responses in memory. Native Tornado streams
correctly via write()/flush(), so FileHandlerMixin.fetch() can read
and stream a file's content directly, and the middleware (along with
its own test) is now fully unused.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Auth extension point on `CommonRequestHandler`

**Files:**
- Modify: `cms/server/util.py` (`CommonRequestHandler`, lines ~203-256)
- Modify: `cms/io/web_service.py` (accept and store an `auth_middleware`
  reference for handlers to read via `self.service`)
- Test: `cmstestsuite/unit_tests/server/util_test.py` (extend again)

**Interfaces:**
- Consumes: nothing new.
- Produces: `WebService.auth_handler: object | None` (the configured
  authenticator instance, or `None`); a documented protocol any future
  authenticator must implement: `async def authenticate(self, handler:
  CommonRequestHandler) -> bool`. `CommonRequestHandler.prepare()` calls it
  (via `self.service.auth_handler`) and raises `tornado.web.HTTPError(403)`
  on a `False` return, before any handler-specific `get`/`post` runs. 2.5b's
  future `AWSAuthMiddleware` rewrite implements this exact protocol against
  `WebService.auth_handler`.

- [ ] **Step 1: Write the failing test**

Append to `cmstestsuite/unit_tests/server/util_test.py`:
```python
class AuthExtensionPointTest(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        import asyncio
        import tornado.httpserver
        import tornado.netutil
        import tornado.web
        from unittest.mock import MagicMock
        from cms.server.util import CommonRequestHandler

        class OkHandler(CommonRequestHandler):
            def get(self):
                self.write("ok")

        self.service = MagicMock()

        class RejectingAuth:
            async def authenticate(self, handler):
                return False

        class AcceptingAuth:
            async def authenticate(self, handler):
                return True

        self.rejecting_service = MagicMock(auth_handler=RejectingAuth())
        self.accepting_service = MagicMock(auth_handler=AcceptingAuth())
        self.no_auth_service = MagicMock(auth_handler=None)

        self._handler_class = OkHandler
        self.addAsyncCleanup(self._stop_all)
        self._servers = []

    async def _stop_all(self):
        for server in self._servers:
            server.stop()
            await server.close_all_connections()

    async def _serve_with(self, service):
        import tornado.httpserver
        import tornado.netutil
        import tornado.web
        application = tornado.web.Application([(r"/", self._handler_class)])
        application.service = service
        server = tornado.httpserver.HTTPServer(application)
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        server.add_sockets(sockets)
        self._servers.append(server)
        return sockets[0].getsockname()[1]

    async def _get(self, port):
        import asyncio
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            return await asyncio.wait_for(reader.read(4096), timeout=5)
        finally:
            writer.close()

    async def test_no_auth_handler_allows_request(self):
        port = await self._serve_with(self.no_auth_service)
        response = await self._get(port)
        self.assertIn(b"200 OK", response)

    async def test_accepting_auth_handler_allows_request(self):
        port = await self._serve_with(self.accepting_service)
        response = await self._get(port)
        self.assertIn(b"200 OK", response)

    async def test_rejecting_auth_handler_blocks_request(self):
        port = await self._serve_with(self.rejecting_service)
        response = await self._get(port)
        self.assertIn(b"403", response)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/util_test.py -v`
Expected: the three new tests FAIL (`prepare()` doesn't check any auth
handler yet).

- [ ] **Step 3: Add the hook to `CommonRequestHandler.prepare()`**

In `cms/server/util.py`, change `CommonRequestHandler.prepare` from:
```python
    def prepare(self):
        """This method is executed at the beginning of each request.

        """
        super().prepare()
        self.url = Url(get_url_root(self.request.path))
        self.static_url_helper = self.service.static_file_hasher.make(self.url)
        self.set_header("Cache-Control", "no-cache, must-revalidate")
```
to:
```python
    async def prepare(self):
        """This method is executed at the beginning of each request.

        If the service was configured with an auth handler (see
        WebService's auth_handler attribute), it's consulted here,
        before any handler-specific get()/post() runs: a False return
        from its authenticate() coroutine aborts the request with 403.

        """
        super().prepare()
        auth_handler = getattr(self.service, "auth_handler", None)
        if auth_handler is not None:
            if not await auth_handler.authenticate(self):
                raise HTTPError(403)
        self.url = Url(get_url_root(self.request.path))
        self.static_url_helper = self.service.static_file_hasher.make(self.url)
        self.set_header("Cache-Control", "no-cache, must-revalidate")
```
(`prepare` becoming `async def` is supported by Tornado's `RequestHandler`
base class since Tornado 5 -- confirm this is still true in 6.5.5 by
checking `tornado.web.RequestHandler.prepare`'s docstring/type hints in the
installed package if you want extra confidence before relying on it.)

- [ ] **Step 4: Store `auth_handler` on `WebService`**

In `cms/io/web_service.py`'s `WebService.__init__` (from Task 3), change:
```python
        parameters.pop('auth_middleware', None)
```
to:
```python
        auth_middleware = parameters.pop('auth_middleware', None)
        self.auth_handler = auth_middleware() if auth_middleware is not None else None
```
(`auth_middleware` here is a *class* implementing the `authenticate`
protocol, matching how `AdminWebServer` already passes
`"auth_middleware": AWSAuthMiddleware` as a class object today — only what
that class is expected to implement changes, per this task and the spec's
"Auth extension point" section. `AWSAuthMiddleware` itself isn't rewritten
in this sub-project, so `AdminWebServer`'s existing configuration will
construct the *old*, WSGI-shaped `AWSAuthMiddleware`, whose `__call__` won't
match the new `authenticate()` protocol — this is expected and fine per the
spec's Non-Goals; 2.5b rewrites `AWSAuthMiddleware` to match. Do not attempt
to adapt or bridge the old `AWSAuthMiddleware` shape in this task.)

- [ ] **Step 5: Run the test again to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/util_test.py -v`
Expected: 6 passed (2 from Task 1, 1 from Task 6, 3 new).

- [ ] **Step 6: Run pyflakes**

Run: `.venv/bin/pyflakes cms/server/util.py cms/io/web_service.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add cms/server/util.py cms/io/web_service.py cmstestsuite/unit_tests/server/util_test.py
git commit -m "feat(server): add async auth extension point to CommonRequestHandler

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: Whole-sub-project integration test and functional-test sanity check

**Files:**
- Test: `cmstestsuite/unit_tests/io/web_service_test.py` (extend, from Task 3)

**Interfaces:**
- Consumes: everything from Tasks 3-7 together.
- Produces: one end-to-end test proving `WebService` with static files, an
  `/rpc` route, file streaming, and the auth hook all wired together in one
  `Application` actually work together — the shape a real
  `AdminWebServer`/`ContestWebServer` (2.5b/2.5c) will assemble.

- [ ] **Step 1: Write the failing test**

Append to `cmstestsuite/unit_tests/io/web_service_test.py`:
```python
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
```

- [ ] **Step 2: Run it to verify it fails or passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/web_service_test.py -v`
Expected: if Tasks 3-7 were each completed and committed correctly, these
two new tests should pass immediately (this task is integration
verification, not new production code) — if either fails, that's a real
integration bug between two previously-separately-tested pieces; fix it in
whichever of Tasks 3-7's code is actually at fault, not by changing this
test's assertions to match broken behavior.

- [ ] **Step 3: Run the full combined regression set for this sub-project**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/io/ cmstestsuite/unit_tests/server/ cmstestsuite/unit_tests/service/ -q`
Expected: all green (this covers every file touched or added across all 8
tasks, plus every 2.2-2.4 test file that shares the `io`/`service`
directories, confirming no regression in previously-migrated services).

- [ ] **Step 4: Note the functional-test follow-up for 2.5b**

This plan does not run `cmsRunFunctionalTests` (it requires a running DB and
full service stack, and 2.5a alone doesn't produce a runnable
`AdminWebServer`/`ContestWebServer` — Task 8's own `WebService` instance is
a test fixture, not the real servers, since Non-Goals excludes touching
`AdminWebServer`/`ContestWebServer` themselves in this sub-project). Add a
line to this task's commit message noting that 2.5b's plan should run
`cmsRunFunctionalTests` against a 2.5a-plus-2.5b state before considering
2.5b done, per the spec's Handoff Notes.

- [ ] **Step 5: Commit**

```bash
git add cmstestsuite/unit_tests/io/web_service_test.py
git commit -m "test(io): whole-sub-project integration test for WebService (2.5a)

Confirms MultiLocationStaticFileHandler, RPCHandler, and
FileHandlerMixin.fetch() all work together inside one real
WebService/Application, the shape 2.5b/2.5c's AdminWebServer/
ContestWebServer will assemble. Functional-test verification against
a real running server is deferred to 2.5b's own plan, since this
sub-project doesn't produce a runnable AdminWebServer/ContestWebServer
on its own.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
