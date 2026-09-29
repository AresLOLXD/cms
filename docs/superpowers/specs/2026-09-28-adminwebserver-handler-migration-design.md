# AdminWebServer handler migration to native Tornado async (sub-project 2.5b)

## Context

Sub-project 2.5a (`docs/superpowers/specs/2026-09-25-webservice-tornado-native.md`)
migrated `WebService`/`AsyncService` off gevent to native Tornado async, and
left the following handoff notes for a follow-up:

- Rewrite `AWSAuthMiddleware` (`cms/server/admin/authentication.py`), which
  is WSGI-shaped (`werkzeug.local.Local`/`LocalManager`) and currently
  completely unused — `AdminWebServer.auth_handler` is `None` since 2.5a — to
  fit into the native-Tornado world.
- Migrate each concrete `AdminWebServer` handler (under
  `cms/server/admin/handlers/`) from sync to async where it does blocking
  work, since there is no more gevent cooperative yielding under native
  Tornado: a blocking DB call inside a plain `def get`/`def post` now freezes
  the whole event loop for every other concurrent request on that process.

Scope: `cms/server/admin/handlers/` only (17 files, 4709 lines, 94
`get`/`post` methods, of which 91 are still plain synchronous `def`s doing
direct blocking `self.sql_session.execute(...)` calls) and
`cms/server/admin/authentication.py`. `ContestWebServer`'s own handlers are
out of scope (sub-project 2.5c).

## Architecture

`AWSAuthMiddleware` and its `werkzeug.local`/`werkzeug.wrappers` dependency
are removed entirely. Admin session management moves directly into
`BaseHandler` (`cms/server/admin/handlers/base.py`), using Tornado's native
secure-cookie mechanism (`self.get_secure_cookie`/`self.set_secure_cookie`/
`self.clear_cookie`) instead of the middleware's manual
`create_signed_value`/`decode_signed_value` + `werkzeug.local` thread-local
trick. That trick existed only to fake per-request instance state across a
shared WSGI app — Tornado already gives every request its own handler
instance, so it is unnecessary here. `AdminWebServer`'s `Application`
parameters already set `cookie_secret` (`cms/server/admin/server.py:54`) and
`xsrf_cookies: True` (line 72), so the native cookie API is already fully
wired and unused today.

Every currently-synchronous `get`/`post` method in
`cms/server/admin/handlers/` becomes `async def`, delegating its unchanged
body to `run_in_executor`, mirroring the precedent already established in
2.5a Task 6 (`FileHandlerMixin.fetch()`). This is a mechanical, per-file
wrapping change — it does not touch the async DB layer built in sub-project
2.3 (`AsyncSessionGen`); that is explicitly out of scope here as too
invasive for this sub-project.

## Components

- **`BaseHandler`** (`cms/server/admin/handlers/base.py`):
  - `get_current_user()` (already exists, line 255): stops calling
    `self.service.auth_handler.admin_id`; instead decodes
    `self.get_secure_cookie("awslogin", max_age_days=...)`, parses the JSON
    `{"id": ..., "timestamp": ...}` payload, and validates expiry precisely
    against `config.admin_web_server.cookie_duration` — the same exact
    check `AWSAuthMiddleware._verify_cookie()` does today (Tornado's own
    `max_age_days` check is too coarse-grained to reuse alone). On expiry,
    clears the cookie and returns `None`, same as today's `.clear()` path
    on the expiry branch. Malformed JSON inside an otherwise
    validly-signed cookie is treated as "no session" (`try/except
    (ValueError, KeyError)`), consistent with the existing defensive
    posture — not new validation scope.
  - New private helper `_set_admin_session(admin_id)`: builds the same JSON
    payload and calls `self.set_secure_cookie("awslogin", ...)`. Replaces
    `AWSAuthMiddleware.set()`.
  - New private helper `_refresh_admin_session()`: re-reads the current
    cookie, bumps `timestamp`, re-writes it. Replaces
    `AWSAuthMiddleware.refresh()`; called from the same places that call
    `.refresh()` today (inside `get_current_user()`'s valid-session path).
- **`LoginHandler.post()`** (`cms/server/admin/handlers/main.py:88`):
  `self.service.auth_handler.set(admin.id)` → `self._set_admin_session(admin.id)`.
- **`LogoutHandler.post()`** (`cms/server/admin/handlers/main.py:97`):
  `self.service.auth_handler.clear()` → `self.clear_cookie("awslogin")`.
- **`cms/server/admin/authentication.py`**: deleted entirely.
- **Mechanical `run_in_executor` wrapping**: no new shared helper function
  is introduced. Each `def get(self, ...)`/`def post(self, ...)` becomes
  `async def`, its current body is renamed to an internal sync method
  (e.g. `_get_sync`/`_post_sync`) taking the same arguments, and the new
  async body is one line: `await
  asyncio.get_event_loop().run_in_executor(None, self._get_sync, ...)`
  (via `functools.partial` where kwargs are needed). Applied file by file,
  mechanically, without touching each handler's internal query logic.

## Data flow

- **Login**: `LoginHandler.post()` validates credentials unchanged, then on
  success calls `self._set_admin_session(admin.id)`, which signs
  `{"id": admin.id, "timestamp": time.time()}` via
  `self.set_secure_cookie(...)`. The `Set-Cookie` header rides on the same
  response, no extra round trip.
- **Authenticated request**: `prepare()` (already `async def` since 2.5a
  Task 7) calls `self.current_user` (Tornado caches
  `get_current_user()`'s result per request), which now decodes the cookie
  directly instead of asking `self.service.auth_handler`. A valid,
  non-expired session gets its `timestamp` refreshed in the same request
  (`_refresh_admin_session()`) — same sliding-expiration behavior as today.
- **Business `get`/`post` methods**: enter as `async def`, delegate their
  sync body to `run_in_executor` (the default thread pool), where the
  actual `self.render(...)`/`self.redirect(...)` calls execute — same
  pattern `FileHandlerMixin.fetch()` already uses. The main event loop
  stays free for other connections while the DB round trip blocks that pool
  thread.
- **Logout**: `LogoutHandler.post()` calls `self.clear_cookie("awslogin")`,
  which adds an immediately-expiring `Set-Cookie` to the response.

## Error handling

- **Missing or corrupt cookie**: Tornado's `get_secure_cookie` already
  returns `None` on a signature/format failure — no extra try/except
  needed; treated as "no session", same as today's `decode_signed_value`
  failure path.
- **Expired cookie** (per our own `timestamp` check, not Tornado's coarse
  `max_age_days`): `get_current_user()` clears it and returns `None`, same
  as today — the handler falls through to Tornado's standard
  `@tornado.web.authenticated`/`require_permission` handling, which
  redirects to login.
- **Malformed JSON inside a validly-signed cookie**: caught and treated as
  "no session" (see Components above) — defensive parity with existing
  code, not new scope.
- **Exception inside a `run_in_executor`-wrapped sync body**:
  `run_in_executor` propagates the exception to the awaiting coroutine, so
  it reaches Tornado's standard error handling (`write_error`) exactly as
  it did when the method was directly synchronous — no behavior change.

## Testing

- New unit tests for the cookie/`run_in_executor` mechanism itself are added
  in `cmstestsuite/unit_tests/server/admin/admin_session_test.py`, validating
  framework-level plumbing already used in 2.5a (`FileHandlerMixin.fetch()`
  exercises the `run_in_executor` pattern).
- Existing coverage exercising AWS handlers (login/logout, or any
  `get`/`post` under `cms/server/admin/handlers/`) must keep passing
  unchanged — that is the existing safety net for this migration (same
  approach 2.5a took: verify existing ones don't break).
- Manual smoke check after implementation: run `cmsAdminWebServer` locally,
  log in, visit a `require_permission`-gated page, let (or force, via a
  temporarily lowered `cookie_duration`) the session expire and confirm it
  redirects to login, log out, and confirm the cookie is cleared and
  further access redirects to login. This replaces the current
  `werkzeug`/`Local`-based flow as the smoke test of the session contract.
- `cmsRunFunctionalTests -v` (already part of the CI pipeline, runs against
  a real DB) is the broadest available regression check for AWS — run
  before closing this sub-project, same as 2.5a, with no new cases unless
  the migration surfaces a real gap.
- Highest-risk area to watch during verification: the 91 handlers
  converted to `run_in_executor`. Any handler relying on mutable `self`
  state assumed to run on the main thread could behave differently under
  the executor. The implementation plan should include a per-file review
  pass, not a blind mechanical `sed`, even though the transformation itself
  is mechanical.

## Addendum: `/rpc` endpoint authorization (`is_rpc_authorized`)

Discovered while mapping this spec into an implementation plan:
`AdminWebServer.is_rpc_authorized()` (`cms/server/admin/server.py:105-106`),
the `rpc_auth` callback that protects AWS's `/rpc/<service>/<shard>/<method>`
endpoint (used by the admin UI's JS to invoke RPCs directly, e.g.
invalidating a submission), also reads `self.auth_handler.admin_id` today —
the only other consumer of that attribute besides `BaseHandler`. Removing
`auth_handler` entirely (as decided above) leaves it with no way to resolve
the calling admin. `rpc_auth` is used only by `AdminWebServer` (confirmed:
no other `WebService` subclass passes it) — this is unambiguously part of
2.5b's scope, not a pre-existing separate concern.

Resolution (user-selected): extend `RPCHandler.post()`
(`cms/io/web_rpc.py`) to pass `self` (the `RPCHandler` instance) as the
first argument to the `rpc_auth` callback, alongside `service_name`,
`shard_int`, `method`. `is_rpc_authorized` becomes
`is_rpc_authorized(self, handler, service, shard, method)`, decoding the
`awslogin` cookie directly off `handler` via `handler.get_secure_cookie(...)`
(available on any `tornado.web.RequestHandler`, not just `BaseHandler`,
since `cookie_secret` is an `Application`-level setting) and looking up the
`Admin` row the same way `BaseHandler.get_current_user()` does, then calling
`rpc_authorization_checker(admin_id, service, shard, method)` unchanged.
This is a small, targeted signature change to a 2.5a file, not a revival of
`auth_handler`/`AWSAuthMiddleware` — it closes the exact gap 2.5a's own
handoff note flagged as deferred to 2.5b. `RPCHandler.post()`'s separate,
pre-existing `auth_handler.authenticate(self)` gate (the generic
`WebService.auth_handler` hook from 2.5a) is left as dead code for AWS
(still `None`, still a no-op) — no other `WebService` subclass sets
`auth_middleware` either, so this hook stays unused project-wide after
2.5b, but removing the hook itself from `cms/io/` is out of scope here.

## Addendum: pre-warming `current_user` in `prepare()`

Also discovered while mapping this spec into a plan: `self.current_user` is
resolved lazily (Tornado's own `RequestHandler.current_user` property calls
`get_current_user()` once and caches it on `self._current_user`) the first
time something reads it. For an AWS request that has `@require_permission`,
that first read happens inside `require_permission`'s `newfunc`
(`base.py:196`), via the `@tornado.web.authenticated` check it wraps —
called synchronously by Tornado's dispatch, on the main event loop thread,
*before* the (now `async def`) `get`/`post` method gets a chance to hand
off to `run_in_executor`. Left unaddressed, `get_current_user()`'s blocking
`Admin` lookup would keep blocking the main thread on every authenticated
request regardless of the per-handler `run_in_executor` wrapping, defeating
this sub-project's purpose for the common case.

Fix (BaseHandler-only, no new fork): `BaseHandler.prepare()`
(`base.py:307`, already `async def` since 2.5a) resolves and caches
`current_user` itself, off the main thread:
`self._current_user = await loop.run_in_executor(None, self.get_current_user)`.
Every later read of `self.current_user` in that request (the
`@tornado.web.authenticated` check, `render_params()`) then hits Tornado's
existing cache and does no further DB work. This relies only on Tornado's
own `current_user` caching contract (documented behavior, not an internal
detail this project controls the stability of, but stable across the
Tornado versions this project pins).

## Out of scope

- `ContestWebServer`'s handlers (sub-project 2.5c).
- Migrating any query code to the async DB layer (`AsyncSessionGen`, from
  sub-project 2.3) — handlers stay on `self.sql_session`/blocking
  SQLAlchemy, just moved off the main event loop thread.
- Removing the now-fully-unused generic `WebService.auth_handler` /
  `auth_middleware` hook from `cms/io/web_service.py` and
  `cms/io/web_rpc.py` itself — it becomes dead code for every current
  `WebService` subclass after this sub-project, but deleting 2.5a's own
  framework code is not this sub-project's job.
