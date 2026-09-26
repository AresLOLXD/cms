# WebService → AsyncService + Tornado Native — Design Spec (sub-project 2.5a)

**Date:** 2026-09-25
**Status:** Approved
**Branch:** `beta` only (see "Relationship to `main`")

## Problem

`AdminWebServer` and `ContestWebServer` (both subclasses of `cms.io.web_service.WebService`)
are the last piece of CMS's service-oriented architecture still built on
gevent. `WebService` inherits from `cms.io.service.Service` (the gevent base
class, unmigrated) and serves requests through a chain of synchronous WSGI
middlewares (`tornado.wsgi.WSGIApplication`, Werkzeug's
`SharedDataMiddleware`/`DispatcherMiddleware`/`ProxyFix`, a custom
`RPCMiddleware` and `FileServerMiddleware`) fronted by
`gevent.pywsgi.WSGIServer`. Tornado itself is present only as a WSGI adapter
today — it never runs its own event loop, `HTTPServer`, or async request
handlers.

This is sub-project 2.5 of the `beta` line's broader gevent → asyncio
modernization effort (2.1 SQLAlchemy 1.3→2.0 → 2.2 `cms/io/` asyncio
framework → 2.3 async DB access → 2.4 all five non-web services migrated →
**2.5 AWS/CWS + Tornado native async, this one**). 2.5 itself is too large
for one spec/plan (~7000 lines across `cms/server/admin/handlers/` and
`cms/server/contest/handlers/`, plus the `WebService` infrastructure they
sit on) and has been decomposed into three sub-projects, each with its own
spec → plan → implementation cycle:

- **2.5a (this spec):** migrate `WebService` itself — the Tornado
  application/HTTP server, the RPC-over-HTTP bridge, static/file serving,
  and the base request-handler class — to `AsyncService` and a native
  Tornado `Application`/`HTTPServer` running on the same `asyncio` event
  loop the rest of the framework (built in 2.2-2.4) already uses. No
  concrete `AdminWebServer`/`ContestWebServer` handler logic changes in this
  sub-project.
- **2.5b:** migrate `AdminWebServer`'s own handlers (`cms/server/admin/handlers/`,
  ~4700 lines) onto the 2.5a foundation.
- **2.5c:** migrate `ContestWebServer`'s own handlers (`cms/server/contest/handlers/`,
  ~2300 lines) onto the 2.5a foundation.

`Worker` remains out of scope for the whole modernization effort (established
in 2.4).

## Goals

- `cms.io.web_service.WebService` becomes a subclass of `AsyncService`
  (`cms/io/async_service.py`, built in 2.2) instead of `Service`
  (`cms/io/service.py`, gevent), reusing its existing RPC/`connect_to`/
  `add_timeout`/lifecycle machinery exactly as every 2.4-migrated service
  already does.
- Requests are served by a real `tornado.web.Application` +
  `tornado.httpserver.HTTPServer`, not `tornado.wsgi.WSGIApplication` +
  `gevent.pywsgi.WSGIServer`. `HTTPServer` runs on the same `asyncio` event
  loop `AsyncService._async_run()` already owns — no second thread, no
  second event loop, no WSGI layer in between.
- Every existing WSGI middleware `WebService` builds today has a native
  Tornado equivalent providing the *same externally observable behavior*
  (same HTTP contract, same response bodies/headers/status codes for
  `/rpc`, same static-file serving semantics including the "first matching
  location wins" multi-directory lookup, same X-Forwarded-For handling):
  `RPCMiddleware` → a `RequestHandler`-based `/rpc/<service>/<shard>/<method>`
  route; `SharedDataMiddleware` (static files, `/docs`) → a Tornado
  `StaticFileHandler` subclass supporting multiple search locations;
  `FileServerMiddleware` → a direct-streaming helper method on
  `FileHandlerMixin` (no longer needs the header-sniffing middleware
  workaround — see Architecture); `ProxyFix` → `HTTPServer(xheaders=True)`.
- `CommonRequestHandler`/`FileHandlerMixin` (`cms/server/util.py`) keep
  working as the base class every concrete handler (2.5b/2.5c) extends,
  updated only where their current implementation assumes the WSGI
  lifecycle (e.g. `finish()`'s session-closing override) — their public
  contract (`self.sql_session`, `self.service`, `self.url`,
  `self.static_url_helper`, `refresh_cookie`) is unchanged in this
  sub-project, since 2.5b/2.5c are what decide how individual handlers use
  it going forward.
- `AWSAuthMiddleware` (`cms/server/admin/authentication.py`) is not
  rewritten in this sub-project (it's AdminWebServer-specific, 2.5b's
  concern), but `WebService` exposes a generic, documented extension point
  a future auth mechanism can hook into (replacing the
  `auth_middleware`-wraps-the-whole-WSGI-app pattern).
- Clean shutdown: `HTTPServer` stops accepting new connections and lets
  in-flight requests finish as part of the same `exit()`/`_exit_event`
  mechanism `AsyncService` already uses for its own RPC listener.
- Full test coverage for the pieces migrated in this sub-project (HTTPServer/
  event-loop coexistence, static file serving, the RPC-over-HTTP bridge,
  file streaming, and clean shutdown), following the `unittest.IsolatedAsyncioTestCase`
  + `ServiceLoggingIsolationMixin` pattern established across 2.2-2.4.

## Non-Goals

- No `AdminWebServer`/`ContestWebServer`-specific handler is migrated,
  rewritten, or even touched in this sub-project — every file under
  `cms/server/admin/handlers/` and `cms/server/contest/handlers/` is
  untouched (2.5b/2.5c). This includes not deciding, in this sub-project,
  how individual handlers should do DB access (sync `Session()` wrapped in
  `run_in_executor`, matching 2.4's established pattern, is the leading
  candidate given 2.3/2.4's finding that the ORM layer isn't
  `AsyncSession`-compatible — but that decision belongs to 2.5b/2.5c, which
  will have real handler bodies in front of them).
- `AWSAuthMiddleware` itself is not rewritten (2.5b).
- `Worker` stays out of scope (established policy from 2.4).
- No change to `cms/io/service.py`/`cms/io/rpc.py` (the gevent stack) — it
  remains in place for any process that still imports it, though after 2.5
  no CMS-shipped service does.
- No change to the wire protocol of `/rpc/<service>/<shard>/<method>` or to
  any static/file-serving URL contract — this is an internal
  implementation swap, not an API change for anything that talks to these
  servers (browsers, the functional test suite, external tools hitting
  `/rpc`).
- `cmsranking`'s own, independent web server is untouched (always out of
  scope for this whole modernization effort).

## Relationship to 2.2-2.4

2.5a is the direct continuation of 2.2's framework build-out: `WebService`
becomes the sixth (and last) `AsyncService` subclass shipped by CMS,
alongside `LogService` (2.2), `Checker`/`ResourceService`/`ScoringService`/
`ProxyService`/`EvaluationService` (2.4). It reuses that framework's
`connect_to`/`add_timeout`/`_call_when_running`/`_spawn`/logging-handler
fixes (`AsyncFileHandler`, the `shell_handler` threading-lock fix from 2.4's
own fix rounds) without modification — none of those are web-specific.

2.4's hardest lesson (`EvaluationService`'s reentrant-lock and cross-thread
queue-mutation bugs) does not directly recur here: 2.5a introduces no shared
mutable state analogous to `post_finish_lock`/`WorkerPool`'s worker-tracking
dicts. Where 2.5a's design *does* echo 2.4's established patterns: the RPC
bridge (`RPCHandler`) awaits the same `AsyncRemoteServiceClient` proxies
2.4's services already use, with the same `RPCError` handling; and file
streaming reads through the same synchronous `FileCacher` DB backend 2.3
flagged as staying sync-only (see 2.3's spec, "no existing call site
migrates") — `FileHandlerMixin`'s new streaming helper must not block the
event loop while reading a large file from `FileCacher`, so its DB/disk
reads run via `loop.run_in_executor`, matching the established
`run_in_executor` bridge pattern rather than inventing a new one.

## Architecture

### `WebService(AsyncService)`

`cms/io/web_service.py` is rewritten. The constructor signature stays the
same shape (`listen_port`, `handlers`, `parameters`, `shard`,
`listen_address`) so `AdminWebServer`/`ContestWebServer` (2.5b/2.5c) don't
need to change their own `__init__` call sites in this sub-project. Inside:

```python
class WebService(AsyncService):
    def __init__(self, listen_port, handlers, parameters, shard=0, listen_address=""):
        super().__init__(shard)
        ...
        num_proxies_used = parameters.pop("num_proxies_used", None) or 0
        self.application = tornado.web.Application(handlers, **parameters)
        self.application.service = self
        self._http_server = tornado.httpserver.HTTPServer(
            self.application, xheaders=num_proxies_used > 0)
        self._listen_port = listen_port
        self._listen_address = listen_address
```

Binding the socket and starting to accept connections happens inside
`_async_run` (or via `_call_when_running`, matching how `add_executor`/
`start_sweeper` defer work until the loop exists) — not in `__init__`,
since `WebService` instances are constructed by launcher scripts before the
event loop starts, same constraint every 2.4-migrated service already
respects. `run()`'s override adds
`self._http_server.listen(self._listen_port, address=self._listen_address)`
(or `add_sockets`, whichever composes better with `AsyncService`'s own
socket lifecycle — a plan-time implementation detail) before delegating to
`AsyncService.run()`, and stops the server (`self._http_server.stop()`,
awaiting in-flight requests via `await self._http_server.close_all_connections()`
if Tornado's version in use supports it — check `tornado`'s pinned version
in `constraints.txt` for the exact available shutdown API) during the same
shutdown sequence `AsyncService.exit()` already triggers.

**The one fact this whole sub-project's architecture depends on, and the
first thing the implementation plan verifies before building anything
else:** `tornado.ioloop.IOLoop.current()`, called from inside a coroutine
that's already running under `asyncio.run(...)` (which is how
`AsyncService.run()` drives its own event loop), returns an `AsyncIOLoop`
wrapping that *same* running loop — Tornado 6+'s documented, default
behavior. This means `HTTPServer.listen()` called from within
`AsyncService`'s own async context needs no bridging, no second thread, and
no second loop: it's the same loop, and coroutines scheduled by either
Tornado or `AsyncService`'s own machinery interleave normally. If this
assumption doesn't hold exactly as expected once tested against this
project's pinned `tornado` version, the whole sub-project's design needs
revisiting before any other work in the plan proceeds.

### Static and doc files: `MultiLocationStaticFileHandler`

A small subclass of `tornado.web.StaticFileHandler` that, given an ordered
list of `(module_name, dir)` locations (the same shape
`WebService`/`ContestWebServer` already pass as `static_files`/`docs_path`
today), resolves a requested path against each location in order and serves
the first match — reproducing `SharedDataMiddleware`'s existing "later
locations override earlier ones for the same relative path" behavior
(`WebService.__init__` already reverses the list for this reason; the new
handler keeps that same convention). Registered as ordinary routes in the
handler list (`/static/...`, and CWS's additional `/docs/...`), not as
middleware — Tornado has no middleware concept, everything is a routed
handler.

### RPC-over-HTTP: `RPCHandler`

Replaces `RPCMiddleware`. A `CommonRequestHandler` subclass registered at
`/rpc/([^/]+)/([0-9]+)/([^/]+)` (service name, shard, method — matching the
existing `/<service>/<int:shard>/<method>` Werkzeug rule). `post()`:

```python
async def post(self, service_name, shard, method):
    coord = ServiceCoord(service_name, int(shard))
    if coord not in self.service.remote_services:
        raise tornado.web.HTTPError(404)
    if self._rpc_auth is not None and not self._rpc_auth(service_name, int(shard), method):
        raise tornado.web.HTTPError(403)
    remote_service = self.service.remote_services[coord]
    if self.request.headers.get("Content-Type") != "application/json":
        raise tornado.web.HTTPError(415)
    try:
        data = json.loads(self.request.body)
    except ValueError:
        raise tornado.web.HTTPError(400)
    if not remote_service.connected:
        raise tornado.web.HTTPError(503)
    try:
        result = await asyncio.wait_for(
            getattr(remote_service, method)(**data), timeout=60)
        error = None
    except asyncio.TimeoutError:
        result, error = None, "Timed out waiting for a reply."
    except RPCError as rpc_error:
        result, error = None, str(rpc_error)
    self.set_header("Content-Type", "application/json")
    self.write(json.dumps({"data": result, "error": error}))
```
(Illustrative — the plan's task-level detail nails down exact status codes/
messages to match `RPCMiddleware`'s current ones precisely, including its
`Accept`-header check, which the snippet above omits for brevity.) `rpc_auth`
is passed the same way `AdminWebServer` passes `rpc_auth`/`is_rpc_authorized`
today (a callable stored on the handler or looked up via `self.service`).

### File streaming: `FileHandlerMixin`

`cms/server/util.py`'s existing `FileHandlerMixin` (read it before
implementing — its current WSGI-era contract, e.g. how it signals
"stream this file" to `FileServerMiddleware` via response headers, is
replaced by a direct method). New shape:
```python
async def serve_file(self, digest, filename=None, disposition="attachment", mimetype=None):
    loop = asyncio.get_running_loop()
    try:
        fobj, size = await loop.run_in_executor(
            None, self._open_file_and_size, digest)
    except KeyError:
        raise tornado.web.HTTPError(404)
    except TombstoneError:
        raise tornado.web.HTTPError(503)
    self.set_header("Content-Type", mimetype or "application/octet-stream")
    if filename is not None:
        self.set_header("Content-Disposition", f'{disposition}; filename="{filename}"')
    self.set_etag_header()
    # conditional-request handling (If-None-Match/Range) via Tornado's
    # own RequestHandler.check_etag_header() / compute_etag(), plus
    # manual Range support if this project's current behavior relies on
    # byte-range requests -- confirm during planning whether
    # response.make_conditional(..., accept_ranges=True) from the
    # current Werkzeug-based code is actually exercised anywhere
    # (functional tests, real usage) before deciding how much of that
    # to reproduce.
    while True:
        chunk = await loop.run_in_executor(None, fobj.read, FileCacher.CHUNK_SIZE)
        if not chunk:
            break
        self.write(chunk)
        await self.flush()
```
(Again illustrative, not final — the plan works out the exact conditional-
request/range-request parity requirements.) `_open_file_and_size` (a small
sync helper) wraps `FileCacher.get_file`/`get_size`, run via
`run_in_executor` since `FileCacher`'s DB-backed lookup is a blocking call
(per 2.3's established non-goal: `FileCacher`/`DBBackend` stay sync). Each
subsequent chunk read is *also* dispatched via `run_in_executor` (reading
from a `FileCacher`-returned file object can itself block on I/O) — this
avoids ever blocking the event loop for the read side, while `self.write`/
`await self.flush()` naturally stream the chunk to the client without
buffering the whole file in memory (the WSGI-buffering bug
`FileServerMiddleware`'s existence was working around no longer applies).

### Auth extension point

`WebService.__init__` keeps accepting an `auth_middleware`-shaped parameter
for backward compatibility during the transition, but its *meaning*
changes: instead of a WSGI-callable-wrapping class, it's documented as an
optional class implementing a small protocol (e.g. an `async def authenticate(self, handler) -> bool`
method, called from `CommonRequestHandler.prepare()` if the service was
configured with one) that 2.5b's `AWSAuthMiddleware` rewrite will implement.
This sub-project defines the hook and wires `prepare()` to call it, but
implements no concrete authenticator — `AdminWebServer`'s existing
`auth_middleware=AWSAuthMiddleware` wiring is left as dead/unused
configuration until 2.5b rewrites `AWSAuthMiddleware` itself to the new
protocol (2.5a's own tests use a trivial stub implementing the protocol, not
`AWSAuthMiddleware`).

### `CommonRequestHandler` changes

`self.sql_session = Session()` in `__init__` and the session-closing
override in `finish()` stay exactly as they are — this sub-project makes no
decision about per-handler DB access patterns (Non-Goals). The only change
here is anything that assumed the WSGI request lifecycle specifically (e.g.
`finish()`'s `OSError`-on-client-disconnect handling — confirm during
planning whether Tornado's native `HTTPServer` raises the same exception
type on a client disconnect mid-response, or a different one, and adjust
the `except` clause accordingly).

## Data Flow and Error Handling

A request arrives at `HTTPServer` → Tornado's router dispatches to the
matching `RequestHandler` → the handler's `prepare()`/`get()`/`post()` run as
coroutines on the shared event loop. Any RPC call a handler makes (in
2.5b/2.5c; 2.5a itself makes none from handler bodies except `RPCHandler`
itself) is a plain `await` against the same `AsyncRemoteServiceClient`
proxies every 2.4 service already uses — no thread hop needed for that part
specifically, since nothing here touches DB or another blocking resource
directly. Uncaught exceptions in a handler are handled by Tornado's own
`write_error`/`send_error` machinery, unchanged from today's behavior (this
sub-project doesn't touch error-page rendering). `RPCHandler`'s own error
handling is detailed in Architecture above: `RPCError`/timeout become the
existing `{"data": null, "error": "..."}` JSON body, connection/auth/method
issues become the existing 404/403/415/503 status codes — same external
contract as `RPCMiddleware` today.

## Testing

`cmstestsuite/unit_tests/io/web_service_test.py` (new), following the
`unittest.IsolatedAsyncioTestCase` + `ServiceLoggingIsolationMixin` pattern:

- **The core integration fact**: construct a minimal `WebService` inside a
  running event loop, confirm `HTTPServer` actually accepts a real TCP
  connection and serves a trivial handler's response, without needing a
  second thread or `IOLoop.start()` call of its own.
- **`MultiLocationStaticFileHandler`**: two locations with an overlapping
  filename, confirm the later (overriding) one wins, matching
  `SharedDataMiddleware`'s current behavior; a 404 for a path in neither
  location.
- **`RPCHandler`**: a real loopback RPC peer (same pattern established in
  `Checker_test.py` and reused throughout 2.4) — success, `RPCError`,
  disconnected-service (503), unknown-service (404), malformed JSON (400),
  wrong content type (415), and a timeout case (mock a peer that never
  responds, confirm the 60s wait_for path — shrink the timeout for the test
  rather than waiting the real 60s, matching 2.4's established testing
  conventions for timeout-heavy code).
- **File streaming**: serve a file larger than one `FileCacher.CHUNK_SIZE`
  through `serve_file`, confirm the full content arrives correctly and
  (this is the point of the whole rewrite) that it's actually streamed
  rather than buffered — e.g. by asserting multiple `write`/`flush` calls
  happened rather than one, or by observing memory/chunking behavior
  directly if that's more reliable to assert on.
- **Clean shutdown**: `exit()` on a `WebService` with an in-flight request
  lets that request finish before the server fully stops, and refuses new
  connections once stopped.

## Risks and Mitigations

- **The Tornado/asyncio-loop-sharing assumption (Architecture, above) is
  unverified until tested against this project's exact pinned `tornado`
  version.** Mitigation: the implementation plan's first task is exactly
  this verification, in isolation, before any other code is written against
  the assumption.
- **Multi-location static file resolution has no Tornado built-in** —
  `MultiLocationStaticFileHandler` is new code, not a thin wrapper around an
  existing Tornado class; get its "later overrides earlier" semantics
  exactly right, since it's directly observable (a wrong static asset served
  in production is a visible regression) and both AWS and CWS depend on
  their own package's static files being able to override `cms.server`'s
  shared ones.
- **`FileHandlerMixin`'s conditional-request (`ETag`/`If-None-Match`) and
  possible range-request behavior**: the current Werkzeug-based
  implementation (`response.make_conditional(request, accept_ranges=True, complete_length=size)`)
  may be relied on by real clients (browsers doing partial downloads/resume,
  or the functional test suite) in ways not obvious from reading the code
  alone. The plan should check whether `cmsRunFunctionalTests` (or any other
  existing test) exercises range/conditional requests against file-serving
  endpoints, and if not, treat full parity here as a best-effort goal
  confirmed by manual testing rather than an automatable requirement blocking
  the sub-project.
- **The auth extension point is speculative** (no concrete implementation
  exists yet in this sub-project) — there's a real risk 2.5b discovers the
  hook shape chosen here doesn't fit `AWSAuthMiddleware`'s actual needs once
  someone tries to implement it for real. Mitigation: keep the hook's
  surface area minimal (one async method, called once per request) so
  changing it later is cheap, and treat its exact shape as provisional,
  revisable by 2.5b without needing to reopen this spec.

## Handoff Notes to 2.5b/2.5c

- Every concrete handler file in `cms/server/admin/handlers/` and
  `cms/server/contest/handlers/` needs its own review for: (1) DB access
  pattern (leading candidate: sync `Session()` wrapped in
  `run_in_executor`, per 2.4's established, hard-won pattern — but confirm
  per-handler, the same way 2.4 confirmed per-service rather than assuming),
  (2) any direct RPC calls to migrated services (should become plain
  `await`, no bridging needed, per this spec's Data Flow section), (3) any
  handler currently relying on WSGI-specific behavior not preserved by this
  sub-project's native rewrite (flag anything found during 2.5a's own
  implementation that looks handler-facing, even though 2.5a itself
  shouldn't need to touch handler code to discover it).
- `AWSAuthMiddleware` (`cms/server/admin/authentication.py`) needs a full
  rewrite against the auth extension point this spec defines — not just a
  mechanical port, since the WSGI-middleware-wrapping-the-whole-app shape it
  has today has no direct native-Tornado equivalent.
- Confirm whether `cmsRunFunctionalTests` needs any updates once 2.5a lands
  (even before 2.5b/2.5c touch any handler) — the external HTTP contract is
  meant to be unchanged, but this is worth an explicit functional-test run
  against a 2.5a-only branch state before proceeding to 2.5b.
