# cms/db/ async DB access — design

## Problem

CMS is a distributed system communicating over JSON-RPC (`cms/io/`), which
sub-project 2.2 migrated to a parallel asyncio framework (`AsyncService`,
`AsyncRemoteServiceClient`, `AsyncTriggeredService`, `AsyncPriorityQueue`),
coexisting with the original gevent framework. `LogService` was migrated as
the pilot; every other service is still gevent-based and will migrate one
at a time in sub-project 2.4.

`LogService` has no database dependency, so sub-project 2.2 never had to
answer: how does a migrated `AsyncService` talk to `cms/db/`? Its own spec
documented a temporary bridge — `await loop.run_in_executor(None, lambda:
session.execute(...))`, wrapping the existing synchronous SQLAlchemy
`Session` — and named this sub-project's job precisely: once an
`AsyncSession` and an async driver exist, sweep the codebase for that
bridge pattern wrapping a DB call and remove it.

`cms/db/` today: SQLAlchemy 2.0 declarative models (`Base` and its ~15
subclasses across `cms/db/*.py`), a synchronous `Session`/`ScopedSession`
built by `sessionmaker(engine, twophase=config.database.twophase_commit)`
in `cms/db/session.py`, and a `SessionGen` context manager used across
roughly 41 files in `cms/service/`, `cms/server/`, `cms/grading/`,
`cmscontrib/`, and `cms/db/` itself. The driver is `psycopg2==2.9.10`
(sync-only), made gevent-cooperative process-wide via
`cms/io/PsycoGevent.py`'s `make_psycopg_green()`.

## Goals

- A new, fully parallel async DB access layer: an async SQLAlchemy engine,
  `AsyncSession`, and an `AsyncSessionGen` context manager mirroring the
  existing sync `SessionGen`.
- Reuse the existing declarative model classes (`Base`, `Contest`, `Task`,
  `Submission`, etc.) unchanged — SQLAlchemy 2.0's unified `select()` style
  works identically against a sync `Session` or an `AsyncSession`, so no
  model duplication is needed.
- Pick and adopt an async PostgreSQL driver: `psycopg` (v3), in async mode,
  chosen over `asyncpg` because it's the official successor to the
  currently-pinned `psycopg2` (nearer migration, same SQL-level behavior,
  no separate type-mapping layer to reconcile against `cms/db/types.py`'s
  existing `psycopg2.extras` usage).
- A test proving the new layer actually works against a real PostgreSQL
  database (Docker test image), not just against mocks.
- A targeted test proving that a raw, blocking `psycopg2` connection
  (as used by `LargeObject`/`DBBackend`, see Non-Goals) still behaves
  correctly when opened from inside a `run_in_executor` worker thread,
  given that `make_psycopg_green()` installs a process-wide gevent wait
  callback (see Risks).

## Non-Goals

- **No existing call site migrates.** None of the ~41 files using
  `Session`/`SessionGen` today are touched. This sub-project builds the
  infrastructure; sub-project 2.4 is what actually wires an `AsyncSession`
  into a migrated service, one service at a time — mirroring exactly how
  sub-project 2.2 built `AsyncService` without migrating anything but the
  `LogService` pilot.
- **No service is migrated in this sub-project.** Unlike 2.2 (which
  migrated `LogService` as an end-to-end pilot), 2.3 has no natural pilot
  service — nothing currently both needs a DB and runs on `AsyncService`.
  The new layer is proven in isolation, by its own tests against a real
  database, not by wiring it into a live service.
- **`LargeObject`/`DBBackend`/`FileCacher` stay fully synchronous.**
  `FileCacher` (`cms/db/filecacher.py`) — the default backend for all file
  storage (submissions, statements, testcases, compiled executables) unless
  a filesystem path is explicitly configured — is 100% synchronous today
  and doesn't touch `Session`/`AsyncSession` at all; `DBBackend`, its
  default implementation, stores files as PostgreSQL large objects via
  `LargeObject`, which opens its own raw `psycopg2` connection
  (`custom_psycopg2_connection()`, outside the SQLAlchemy pool) and issues
  raw SQL calls to `lo_*` functions (chosen specifically because
  psycopg2's native `lobject` API is incompatible with gevent — see
  `cms/db/fsobject.py`'s own docstring). None of this needs to change for
  2.3's goals: a future `AsyncService` that needs `FileCacher` calls it via
  `run_in_executor`, exactly like any other blocking call, with no async
  equivalent required. See Handoff Notes for the future path if this ever
  needs to change.
- **`cms/io/PsycoGevent.py` is untouched.** It remains the sync/gevent
  path's cooperative bridge; the new async driver needs no equivalent,
  since it's natively non-blocking.
- **`cmsranking/`, `cmscontrib/`'s CLI scripts, `cms/server/` (Tornado web
  layer)** remain out of scope for the whole modernization effort /
  deferred to sub-project 2.5, same as in 2.2's spec.

## Relationship to sub-project 2.2

2.2's spec named this sub-project's job explicitly: "sub-project 2.3's own
plan must include a step that sweeps the codebase for [the
`run_in_executor` DB-bridge] pattern (`grep -rn "run_in_executor"`) and
removes it wherever the wrapped call was a DB access." As of this spec,
that grep finds zero DB-related matches — `LogService` (2.2's only
migrated service) has no DB dependency, so the bridge pattern was
documented and tested in isolation but never actually used for a real DB
call. This sweep step is therefore a no-op today; it is repeated here for
completeness and will start finding real matches once 2.4 migrates a
DB-touching service using this sub-project's new `AsyncSession`.

## Architecture

`cms/db/async_session.py` (new file, `cms/db/session.py` untouched):

- `async_engine = create_async_engine(async_url, echo=config.database.debug)`,
  where `async_url` is derived from `config.database.url` by replacing its
  driver component (`postgresql+psycopg2://...`) with `postgresql+psycopg://...`.
  Both driver names route through the same `psycopg` (v3) package;
  `create_async_engine` (vs. `create_engine`) is what makes the engine
  async, not the URL scheme itself. Deriving the URL from the existing
  config means no new config field is needed — this sub-project reuses
  `config.database.url` as the single source of truth for connection
  parameters.
- `AsyncSession = async_sessionmaker(async_engine)` — mirrors
  `session.py`'s `Session = sessionmaker(engine, twophase=...)`. Whether
  `async_sessionmaker` accepts the same `twophase` argument in this
  project's pinned SQLAlchemy 2.0.54 + `psycopg` (v3) combination is
  **verified as part of implementation, not assumed here** — see Risks.
- `AsyncSessionGen`: an `async` context manager mirroring `SessionGen`
  exactly (`async with AsyncSessionGen() as session: ...`, `session.rollback()`
  + `session.close()` — both awaited — on exit).
- `cms/db/__init__.py` gains `AsyncSession`, `AsyncSessionGen` (and
  `async_engine`, if any caller needs the bare engine) added to its
  existing import-aggregator re-exports, alongside — not replacing — the
  synchronous `Session`, `ScopedSession`, `SessionGen` already exported
  there.
- Model classes (`Base` and every subclass in `cms/db/*.py`) are
  **unchanged**. SQLAlchemy 2.0's `select(Contest).where(...)` executes
  identically whether passed to a sync `Session.execute()` or an awaited
  `AsyncSession.execute()` — the ORM/query layer has no sync/async split,
  only the session and engine do.
- New dependency: `psycopg[binary]` added to `pyproject.toml`/
  `constraints.txt`, alongside — not replacing — `psycopg2==2.9.10`. Both
  drivers coexist in the same process without conflict: one only ever
  backs the sync `engine`, the other only the new `async_engine`.
  `psycopg[binary]` (not `psycopg[c]`) is chosen for the same reason the
  project already leans on `psycopg2-binary` in ad hoc local verification
  during 2.1/2.2 (observed repeatedly this session): not every dev/CI
  environment has `pg_config`/build toolchain available, and the
  project's production Dockerfile already has `libpq-dev` so either would
  build there, but `[binary]` removes the dependency for everywhere else.

## Data Flow and Error Handling

- **Error propagation:** unchanged in kind — SQLAlchemy exceptions
  (`IntegrityError`, etc.) raise the same way from `await
  session.execute(...)` as from `session.execute(...)`; callers already
  written to catch SQLAlchemy exceptions need no change when a future 2.4
  migration swaps which session type they hold.
- **Transactions:** `AsyncSessionGen`'s rollback-then-close-on-exit
  contract exactly mirrors `SessionGen`'s, so code migrating from one to
  the other in 2.4 changes only `with` → `async with` and adds `await` to
  each `session.execute(...)`/`session.commit()`/`session.flush()` call —
  not its transaction-boundary logic.
- **Connection pooling:** `create_async_engine` gets its own pool,
  independent of the sync `engine`'s pool — two separate connection pools
  to the same database in any process that (temporarily, during the 2.4
  transition) uses both. Not a concern for this sub-project specifically
  (nothing uses both yet), but worth noting for 2.4's per-service planning:
  a service mid-migration that still calls some sync DB code via
  `run_in_executor` *and* some async DB code directly will hold connections
  in both pools simultaneously.

## Testing

- New tests live in `cmstestsuite/unit_tests/db/` (mirrors the existing
  `cmstestsuite/unit_tests/io/` package sub-project 2.2 created) — this is
  the first directly-async-tested corner of `cms/db/`.
- **Real database, not mocks:** every test runs against the actual
  PostgreSQL test database (`docker-compose.test.yml`'s `testdb` service),
  consistent with how `cmstestsuite/unit_tests/databasemixin.py` already
  tests the sync path — an async driver/dialect choice can only be
  validated by actually speaking the wire protocol to a real server.
- **Round-trip test:** `AsyncSessionGen` + `select()` against a real model
  (e.g. `Contest` or another simple, already-used-in-tests model), insert,
  commit, a fresh session reading it back, rollback-discards-uncommitted.
- **`twophase_commit` compatibility test:** if the Architecture section's
  open verification question resolves to "supported," a test proving it;
  if "not supported by this driver/SQLAlchemy combination," the plan
  documents the limitation explicitly rather than silently dropping the
  config option's effect for async sessions.
- **The `make_psycopg_green()`-in-a-`run_in_executor`-thread risk test**
  (see Risks): opens a raw `psycopg2` connection (mirroring what
  `LargeObject`'s `custom_psycopg2_connection()` does) from inside a
  `loop.run_in_executor(None, ...)` worker thread, with the process-wide
  gevent wait callback active (as it always is — `cms/io/__init__.py`
  calls `make_psycopg_green()` unconditionally at import time), and
  confirms the connection behaves correctly (queries complete, no hang,
  no exception) despite no gevent hub running in that worker thread.
- No service, migrated or otherwise, uses any of this in production code
  as of this sub-project — consistent with the Non-Goals above.

## Risks and Mitigations

- **`twophase_commit` + async driver compatibility is unverified as of
  this spec.** `config.database.twophase_commit` defaults to `false` and
  its practical usage in this deployment is unconfirmed; SQLAlchemy's
  two-phase-commit support historically has had narrower driver coverage
  than single-phase. Mitigation: verify during implementation (Testing
  section above); if unsupported, document the limitation rather than
  silently ignoring the config flag for async sessions — a service that
  sets `twophase_commit = true` and later migrates to `AsyncSession` needs
  to know this explicitly, not discover it as a silent behavior change.
- **`make_psycopg_green()` is process-wide and could misbehave for a raw
  `psycopg2` connection opened inside a `run_in_executor` worker thread**
  (relevant to `LargeObject`/`DBBackend`, see Non-Goals) — a real OS
  thread has no gevent hub for `gevent_wait_callback`'s `wait_read`/
  `wait_write` calls to cooperate with. This is the same class of hazard
  sub-project 2.2's final review found and flagged for `cms/log.py`'s
  `FileHandler`/`shell_handler` gevent locks (elevated to a hard 2.4
  prerequisite in that sub-project's spec). Mitigation: the targeted test
  in this sub-project's Testing section proves or disproves this
  empirically before 2.4 ever depends on `FileCacher` from a migrated
  service, rather than leaving it as an untested assumption the way the
  logging-lock hazard was left in 2.2 until its final review caught it.
- **Two independent connection pools during the 2.4 transition window**
  (see Data Flow) — not a 2.3-scope risk (nothing uses both yet), but
  documented so 2.4's per-service plans size connection-pool config
  correctly rather than being surprised by doubled connection counts on a
  partially-migrated service.
- **Driver choice risk (psycopg v3 vs. asyncpg):** mitigated by the choice
  itself — psycopg v3 is the official successor to the already-pinned
  psycopg2, minimizing the surface where async-specific driver quirks
  could diverge from this project's years of psycopg2-based production
  experience. asyncpg was considered and rejected for this project
  specifically because of `cms/db/types.py`'s existing
  `psycopg2.extras.register_ipaddress()` and `cms/db/fsobject.py`'s
  `psycopg2.extensions`/`psycopg2.Binary` usage — reconciling those
  against a fully separate driver's type-mapping layer would have been
  unnecessary added risk for no corresponding benefit at this stage.

## Handoff Notes to Later Sub-Projects

- **To 2.4 (per-service migration):** `AsyncSession`/`AsyncSessionGen` are
  ready to use; migrate one service's DB access at a time, same
  incremental philosophy as the RPC framework migration. Grep for
  `run_in_executor` wrapping a DB call (per 2.2's original handoff note,
  repeated here) as each service migrates, replacing it with direct
  `await session.execute(...)` via the new `AsyncSessionGen`. If/when a
  migrated `AsyncService` needs `FileCacher` and its default `DBBackend`,
  consider giving `LargeObject` an async counterpart at that point: the
  same raw-SQL `lo_*`-function-call technique `LargeObject` already uses
  (chosen specifically to avoid psycopg2's gevent-incompatible native
  `lobject` API) would work identically over a `psycopg` async connection
  instead of `custom_psycopg2_connection()`'s sync one. Not needed before
  then — `FileCacher` stays fully synchronous and works via
  `run_in_executor` like any other blocking call until a real caller needs
  it not to.
- **To 2.5 (AWS/CWS + Tornado native async):** once `WebService` migrates
  to `AsyncService` (or a descendant) and native async Tornado, its
  request handlers should use this sub-project's `AsyncSession`/
  `AsyncSessionGen` directly rather than `run_in_executor`-wrapping the
  sync session — the whole point of migrating the web layer to native
  async is to stop blocking the event loop on I/O, and a wrapped sync DB
  call would silently reintroduce that blocking for every DB-touching
  request.
- **Eventual cleanup (post-2.4, not scoped to any single sub-project
  today):** once every `cms/db/` caller has migrated to `AsyncSession`,
  `cms/db/session.py`'s sync `Session`/`SessionGen`/`engine` and the
  `psycopg2` dependency become removable, same cleanup moment 2.2's spec
  already identified for `cms/io/service.py`/`rpc.py`/`priorityqueue.py`/
  `triggeredservice.py` once every `TriggeredService` consumer migrates.
