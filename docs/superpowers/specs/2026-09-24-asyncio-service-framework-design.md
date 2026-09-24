# cms/io/ gevent → asyncio Migration — Design Spec

**Date:** 2026-09-24
**Status:** Approved
**Branch:** `beta` only (see "Relationship to `main`")

## Problem

CMS's service-oriented architecture is built on gevent: every service
(`EvaluationService`, `Worker`, `ScoringService`, `ProxyService`,
`ResourceService`, `LogService`, `Checker`, and the web services
`AdminWebServer`/`ContestWebServer`) inherits from `cms.io.service.Service`,
whose RPC transport (`cms/io/rpc.py`), connection loop, and timers are all
built on `gevent.spawn`, `gevent.socket`, `gevent.event`, and
`gevent.server.StreamServer`. This is sub-project 2.2 of the `beta` line's
broader modernization effort (see 2.1's SQLAlchemy migration, already
complete): the next step toward moving the whole system off gevent onto
Python's native `asyncio`, one sub-project at a time
(2.2 this one → 2.3 DB access → 2.4 per-service migration → 2.5 AWS/CWS +
Tornado native async).

## Goals

- A new, parallel `asyncio`-based service/RPC framework in `cms/io/`,
  functionally equivalent to the existing gevent one, with the same public
  interface (`connect_to`, `add_timeout`, `run`, `exit`, `@rpc_method`) so
  that a later sub-project (2.4) can migrate each service by swapping its
  base class, one service at a time, without a coordinated whole-system
  cutover.
- Wire-protocol compatibility preserved exactly: a new `AsyncService` must
  be able to send and receive RPCs with an old, unmigrated `Service`
  (gevent) with zero protocol changes, in both directions.
- One real service (`LogService`) migrated to the new framework within this
  sub-project, as an end-to-end validation that runs inside the existing
  Docker test image alongside every other (still-gevent) service.
- A new `cms/io/async_priorityqueue.py` (`AsyncPriorityQueue`), parallel to
  the existing `cms/io/priorityqueue.py` (untouched — see Architecture for
  why an in-place edit is unsafe), for `async_triggeredservice.py` to use.
- A documented, temporary bridge pattern (`loop.run_in_executor`) for any
  migrated service that still needs to call into `cms/db/` (still
  synchronous psycopg2/SQLAlchemy until sub-project 2.3), so 2.2 does not
  need to wait for 2.3 to be useful.

## Non-Goals

- **`cms/io/web_service.py` / `cms/io/web_rpc.py`** (`WebService`, the base
  class of `AdminWebServer`/`ContestWebServer`) — out of scope. `WebService`
  runs its HTTP transport via `gevent.pywsgi.WSGIServer` wrapping
  `tornado.wsgi.WSGIApplication`, which is legacy WSGI mode, not native
  Tornado async — that's sub-project 2.5's job, which migrates HTTP
  transport and RPC transport together to avoid running two concurrency
  schedulers (gevent's greenlet loop and asyncio's event loop) in the same
  process. `WebService` keeps subclassing the old gevent `Service` until
  2.5 lands.
- **`cms/db/` and `cms/io/PsycoGevent.py`** — out of scope, sub-project
  2.3's job (`AsyncSession` + an async driver). This sub-project only
  documents and uses the temporary `run_in_executor` bridge pattern; it
  does not touch `cms/db/` or the psycopg2/gevent cooperative wait
  callback at all.
- **`cmscontrib/`'s `gevent.monkey.patch_all()` scripts** (`AddUser.py`,
  `ImportContest.py`, and similar CLI tools) — these are one-shot CLI
  scripts, not long-running services; they don't inherit from `Service` and
  aren't part of the RPC framework. Untouched.
- **`cmsranking/`** — confirmed out of scope for the whole modernization
  effort (no dependency on `cms/io` or `cms/db`).
- **Migrating any service other than `LogService`** — every other service
  (`ResourceService`, `EvaluationService`, `Worker`, `ScoringService`,
  `ProxyService`, `Checker`) stays on the old gevent `Service` until
  sub-project 2.4 migrates it individually. `LogService` is migrated here
  purely as end-to-end validation of the new framework, not as the start of
  2.4's rollout.
- **Deleting the old gevent framework** — `cms/io/service.py` and
  `cms/io/rpc.py` stay untouched and fully functional throughout this
  sub-project and through all of 2.4; they're only removed once every
  service has been migrated off them (a cleanup step that belongs to the
  end of 2.4, not to this spec).

## Relationship to `main`

This work happens on `beta` only, same pattern as 2.1: `main` stays
aligned with upstream (still gevent-based) and never receives this
sub-project's changes.

## Architecture

### Coexistence, not cutover

CMS's RPC protocol is JSON messages delimited by `\r\n` over a plain TCP
socket (confirmed by reading `cms/io/rpc.py`'s `_read`/`_write` and
`process_incoming_request`/`process_incoming_response`) — nothing about the
wire format depends on gevent. This means a service built on the new
`AsyncService` can exchange RPCs with a service still running the old
gevent `Service`, in either direction, with no adapter layer. The whole
migration strategy for 2.2 → 2.4 rests on this fact: build the new
framework once, then let 2.4 flip services to it one at a time, with the
system running a mix of gevent and asyncio services throughout — never a
big-bang cutover of the whole fleet at once.

### New modules (parallel to the existing ones, nothing existing is modified)

- **`cms/io/async_rpc.py`** — `AsyncRemoteServiceBase`,
  `AsyncRemoteServiceServer`, `AsyncRemoteServiceClient`. Mirrors
  `rpc.py`'s class structure and public behavior exactly. `rpc.py`'s
  `rpc_method` decorator (`rpc.py:57-67`) has no gevent dependency at all
  — it only sets `func.rpc_callable = True` — so `async_rpc.py` imports and
  reuses it directly from `cms.io.rpc` rather than duplicating it; both
  `Service` and `AsyncService` RPC methods use the exact same decorator.
  Concurrency primitive mapping:
  - `gevent.socket` I/O via `.makefile()` → `asyncio.StreamReader`/
    `asyncio.StreamWriter` via `asyncio.open_connection`/
    `asyncio.start_server`.
  - `gevent.lock.RLock` (read/write locks) → `asyncio.Lock`.
  - `gevent.event.Event` (connection state) → `asyncio.Event`.
  - `gevent.event.AsyncResult` (pending RPC results) → `asyncio.Future`.
  - `gevent.spawn(handler, ...)` (on-connect/on-disconnect handlers,
    per-message processing) → `asyncio.create_task(...)`.
- **`cms/io/async_service.py`** — `AsyncService`. Mirrors `service.py`'s
  `Service` class:
  - `gevent.server.StreamServer` → `asyncio.start_server`.
  - `self.rpc_server.serve_forever()` (blocking run loop) →
    `asyncio.run(self._main())`, where `_main()` starts the server, awaits
    a shutdown `asyncio.Event` set by `exit()`, then awaits
    `server.wait_closed()`.
  - `add_timeout`'s `gevent.spawn_later`/`repeater` loop → an
    `asyncio.create_task` running an `async def repeater()` using
    `asyncio.sleep`.
  - Signal handling (`SIGINT`/`SIGTERM`): asyncio signal handlers can't run
    async code directly, so `exit()` is invoked via
    `loop.call_soon_threadsafe(...)` from the signal handler, same
    externally-visible behavior as today.
  - Same public method surface as `Service`: `connect_to`, `add_timeout`,
    `run`, `exit`, `get_backdoor_path`, `@rpc_method`-decorated `echo`/
    `quit`. (Backdoor server support: keep using `gevent.backdoor` for the
    UNIX socket REPL even on `AsyncService`, since it's a debugging
    convenience orthogonal to the RPC transport, not worth a bespoke
    asyncio REPL implementation for this sub-project — flagged as an
    accepted simplification, not a functional gap in the main service
    logic.)
- **`cms/io/async_triggeredservice.py`** — mirrors `triggeredservice.py`'s
  `Executor`/`TriggeredService` classes on top of `AsyncService`, for
  sub-project 2.4 to use once it reaches `EvaluationService`,
  `ScoringService`, or `ProxyService` (the three current consumers of
  `TriggeredService`).
- **`cms/io/async_priorityqueue.py`** (new, parallel — see the correction
  below for why this is NOT an in-place edit) — `AsyncPriorityQueue`,
  mirroring `priorityqueue.py`'s `PriorityQueue` class, for
  `async_triggeredservice.py` (and, later, 2.4's migrated
  `TriggeredService` consumers) to use.

  **Correction from the design conversation:** the original plan for this
  spec was to edit `priorityqueue.py` in place, reasoning that it's "a pure
  data structure, not a service." That reasoning was wrong.
  `PriorityQueue.pop(wait=True)`/`top(wait=True)` call `self._event.wait()`
  **synchronously** (gevent's cooperative blocking wait, no `await`) —
  exactly the blocking pattern `TriggeredService.run()` (`triggeredservice.py:139`)
  depends on today, and which `EvaluationService`, `ScoringService`, and
  `ProxyService` (all three still gevent-based, all three unmigrated until
  2.4) rely on in production right now. Swapping the `Event` class in place
  would make `.wait()` return an unawaited coroutine — silently
  non-blocking, turning `run()`'s loop into a CPU-spinning busy loop that
  never actually waits, breaking three live services this very sub-project
  is supposed to leave untouched. `priorityqueue.py` is therefore
  **entirely untouched** by this sub-project, same as every other
  gevent-based file outside the explicit Goals list, and `AsyncPriorityQueue`
  is a full parallel implementation (not a thin subclass — `pop`/`top`
  become `async def`, using `await self._event.wait()` on an
  `asyncio.Event`, which is a different calling convention from the sync
  version, not just a different Event class).

### The `run_in_executor` DB bridge (temporary, replaced by sub-project 2.3)

Any service migrated to `AsyncService` that still needs to call into
`cms/db/` (synchronous SQLAlchemy/psycopg2) wraps that call:

```python
result = await loop.run_in_executor(None, lambda: session.execute(...))
```

This is the standard pattern other async Python frameworks use for
bridging to blocking I/O (e.g. FastAPI's sync-endpoint thread pool). It
requires no changes to `cms/db/` or `PsycoGevent.py` — purely a call-site
concern on the migrated service's side. **This is explicitly temporary
and is sub-project 2.3's job to remove**: once `cms/db/` exposes an
`AsyncSession` with an async driver, every `run_in_executor(...)` wrapping
a DB call becomes a direct `await session.execute(...)`. Sub-project 2.3's
own plan must include a step that sweeps the codebase for this pattern
(`grep -rn "run_in_executor"`) and removes it wherever the wrapped call was
a DB access — this is the explicit handoff note from 2.2 to 2.3.

`LogService` (this sub-project's pilot) has no DB dependency, so it
doesn't exercise this bridge itself; the pattern is documented and tested
in isolation (a `cms/io/` unit test using a dummy blocking function) so
2.4's future service migrations have a proven, tested pattern to follow
the first time they need it.

## Data Flow and Error Handling

- **RPC error propagation:** unchanged contract — any exception raised
  inside an `@rpc_method` is caught, serialized into
  `response["__error"]` (class name + message + traceback), and logged;
  only the coroutine-spawning mechanism changes (`asyncio.create_task`
  instead of `gevent.spawn`).
- **Automatic reconnection** (`RemoteServiceClient(auto_retry=...)`):
  preserved with `asyncio.sleep()` replacing `gevent.sleep()` in the
  reconnect loop; same externally observable retry behavior.
- **Message size limits / malformed messages:** `MAX_MESSAGE_SIZE` and its
  enforcement are pure logic with no gevent dependency — copied unchanged.
- **Shutdown:** `exit()`'s contract (stop accepting new connections, let
  the process wind down) is preserved; `AsyncService.run()` returns only
  after the server has fully closed, same as `Service.run()` today.

## Testing

- New `cmstestsuite/unit_tests/io/` package (first direct unit tests for
  `cms/io/` — today it's only exercised indirectly through the services
  that use it): round-trip RPC success, RPC failure/error propagation,
  automatic reconnection after a dropped connection, and `add_timeout`'s
  repeated-call behavior.
- **Cross-interoperability test** (the load-bearing one, since the whole
  2.2→2.4 strategy depends on wire-protocol compatibility): a test that
  pairs an old gevent `Service` (as server) with a new `AsyncService` (as
  client) and confirms a real RPC round-trip succeeds, and the same test
  with the roles reversed (`AsyncService` server, gevent `Service`
  client).
- **Real pilot in Docker:** `LogService` running as `AsyncService` inside
  `docker-compose.test.yml`, receiving real log RPCs from the rest of the
  (still-gevent) system during the existing test suite run — if the full
  suite stays green with `LogService` migrated, that's the proof this
  coexistence strategy holds in a real running system, not just in an
  isolated test.
- `run_in_executor` bridge pattern: a small, isolated unit test (not tied
  to any real DB call, since `LogService` doesn't need one) proving the
  pattern itself works as documented, for 2.4 to copy when it's needed.

## Risks and Mitigations

- **A near-miss, caught during design, not left in the spec:** the
  original design for this spec proposed editing `priorityqueue.py` in
  place (`gevent.event.Event` → `asyncio.Event`). `PriorityQueue.pop`/`top`
  call `self._event.wait()` *synchronously* (gevent's cooperative blocking
  wait), which is exactly what `TriggeredService.run()` depends on today
  for `EvaluationService`/`ScoringService`/`ProxyService` — all three still
  gevent-based, all three untouched until 2.4. An in-place `Event` swap
  would silently turn `.wait()` into an unawaited coroutine (a no-op),
  turning those three services' queue-processing loops into CPU-spinning
  busy loops the moment this sub-project landed on `beta` — breaking live
  services this sub-project explicitly promises not to touch. Fixed by
  making `AsyncPriorityQueue` a full parallel file (`async_priorityqueue.py`)
  instead, leaving `priorityqueue.py` completely untouched. Recorded here
  so a future reader doesn't reach for the same shortcut.
- **Wire-protocol compatibility being assumed rather than verified:**
  mitigated directly by the cross-interoperability test above — this is
  the single most load-bearing test in this sub-project's suite, since the
  entire incremental-migration strategy (2.2 → 2.4) depends on it holding.
- **Signal handling correctness under asyncio** (a known sharp edge: signal
  handlers can't safely call async code): mitigated by using
  `loop.call_soon_threadsafe(...)` from the handler, the documented-correct
  pattern, and covered by the `LogService` pilot actually receiving
  `SIGTERM` during Docker container shutdown as part of the test suite run.
- **Backdoor REPL** (`start_backdoor`/`stop_backdoor`) staying
  gevent-based on an otherwise-asyncio service: accepted as a scoped
  simplification (see Architecture section) since it's an optional
  debugging feature, not part of the service's core request-handling path,
  and `gevent.backdoor.BackdoorServer` doesn't touch the service's main
  RPC loop at all — it opens its own independent UNIX socket.

## Handoff Notes to Later Sub-Projects

- **To 2.3 (DB access → async):** remove every `run_in_executor(...)`
  wrapping a `cms/db/` call, once `AsyncSession` + an async driver exist,
  by grepping for the pattern across whatever services 2.4 has migrated by
  that point.
- **To 2.4 (per-service migration):** the new `AsyncService`/
  `AsyncTriggeredService`/`AsyncPriorityQueue` classes and the
  `run_in_executor` bridge pattern are ready to use; migrate one service at
  a time, in any order, since coexistence with the remaining gevent
  services is proven by this sub-project's cross-interoperability test.
  `EvaluationService`/`ScoringService`/`ProxyService` (the three
  `TriggeredService` consumers) each own an independent queue instance —
  confirm this still holds at migration time — so migrating one doesn't
  require migrating the others simultaneously. Once every
  `TriggeredService` consumer is migrated, `priorityqueue.py` and the old
  `triggeredservice.py` become dead code and can be deleted (same cleanup
  moment as the rest of the old gevent framework).
- **To 2.5 (AWS/CWS + Tornado native async):** `WebService` still
  subclasses the old gevent `Service` after this sub-project — 2.5 is
  responsible for both flipping its base class to `AsyncService` (or a
  descendant) and replacing `gevent.pywsgi.WSGIServer`/`tornado.wsgi`
  with native async Tornado, together, to avoid ever running both
  concurrency schedulers in the same process.
