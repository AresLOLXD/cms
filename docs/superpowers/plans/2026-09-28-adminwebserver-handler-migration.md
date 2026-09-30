# AdminWebServer Handler Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `cms/server/admin/handlers/` and `cms/server/admin/authentication.py` off the dead WSGI-shaped `AWSAuthMiddleware` and off blocking-on-the-main-thread synchronous `get`/`post`/`delete` methods, onto the native-Tornado async framework `WebService` was migrated to in sub-project 2.5a.

**Architecture:** Session/auth state moves from `AWSAuthMiddleware`'s `werkzeug.local` trick into `BaseHandler` itself, using Tornado's native `get_secure_cookie`/`set_secure_cookie`/`clear_cookie`. `current_user` is resolved once per request, off the main thread, inside `BaseHandler.prepare()`. Every synchronous `get`/`post`/`delete` method in `cms/server/admin/handlers/` (99 methods across 15 files, plus 4 factory-closure `get` methods in `base.py`'s `SimpleHandler`/`SimpleContestHandler`) is mechanically split into a renamed sync body (`_get_sync`/`_post_sync`/`_delete_sync`) plus a thin `async def` wrapper that awaits `loop.run_in_executor(None, self._x_sync, *args)`, exactly mirroring 2.5a's `FileHandlerMixin.fetch()` precedent (`cms/server/util.py:72-91`). AWS's `/rpc` endpoint authorization (`is_rpc_authorized`) is fixed by passing the `RPCHandler` instance into the `rpc_auth` callback so it can decode the session cookie itself.

**Tech Stack:** Python 3.12, Tornado (native async, no gevent), SQLAlchemy 2.0 (`select()` style, still synchronous/blocking — not migrated to the async DB layer here), `asyncio.run_in_executor` for offloading blocking DB calls.

**Spec:** `docs/superpowers/specs/2026-09-28-adminwebserver-handler-migration-design.md` (including its two addenda: `is_rpc_authorized` and `current_user` pre-warming)

## Global Constraints

- Task ordering matters: Task 1 must run before Tasks 2, 3, and 4 (they all depend on `BaseHandler`'s new cookie helpers and/or its `import asyncio`). Tasks 5-18 (one per remaining handler file) have no dependency on each other or on Tasks 3-4, and can run in any order, or in parallel if using subagent-driven-development's dispatch. Task 19 must run last.
- Do not touch `ContestWebServer` (`cms/server/contest/`) — out of scope, sub-project 2.5c.
- Do not migrate any query to the async DB layer (`AsyncSessionGen`, sub-project 2.3) — handlers keep using `self.sql_session` (blocking SQLAlchemy), just moved off the main event loop thread via `run_in_executor`.
- Do not change handler business logic, template names, redirect targets, or URL routes — this is a mechanical sync-to-async wrapping plus an auth-mechanism swap, not a behavior change.
- Do not remove the generic `WebService.auth_handler`/`auth_middleware` hook from `cms/io/web_service.py` — it becomes unused dead code for every current `WebService` subclass after this plan, but deleting 2.5a's own framework code is not this plan's job.
- Every file touched must stay `pyflakes`-clean (project rule, `CLAUDE.md`).
- Follow the project's git-stash safety rule if anything needs to be set aside: never bare `git stash`/`git stash pop`.
- Every task's commit reuses this branch's existing trailer format: run `git log -1 --format=%B` before committing to copy the exact `Co-Authored-By` line.

## Review Focus

- **A cookie signed before this migration, presented after it, on a still-running old process during a rolling deploy or right after a config reload.** `get_secure_cookie` uses the same `cookie_secret` as the old `create_signed_value`, but Tornado's own signed-cookie wire format differs from calling `create_signed_value` directly with a raw JSON string — a pre-migration cookie must fail decoding cleanly (treated as "no session", not a 500). Task 1's `_get_session_admin_id` must handle a decode failure as "no session," not raise.
- **An `admin_id` in a validly-decoded cookie that no longer maps to an enabled `Admin` row** (e.g. the admin was disabled after logging in). Must fall through to "no session" and clear the cookie, not raise or silently treat the request as authenticated. Covered by Task 1.
- **A request to a `@require_permission`-protected page with no cookie at all** (a fresh browser). Must redirect to `login`, not raise `AttributeError` (the current pre-2.5b state, per `server.py`'s TODO comment) or 500. Covered by Task 1 + manual verification in the final task.
- **An RPC call through `/rpc/...` from a logged-out session** (no valid `awslogin` cookie). `is_rpc_authorized` must reject with 403, not raise `AttributeError` on `self.auth_handler` (today's state) or silently allow. Covered by Task 3.
- **A handler that both reads and mutates request-scoped state Tornado itself expects on the main thread** (e.g. `self.set_header`, `self.redirect`, `self.write`) now running inside `run_in_executor`. These calls are not coroutine-safe by contract, but are safe here because each handler instance is only ever touched by one thread at a time (the executor thread, while the main thread is suspended awaiting it) — no concurrent access. Flagged for the per-file review pass in Tasks 5-18, not a new automated test (no test infrastructure exists for this today).

---

## Task 1: `BaseHandler` native-cookie session + delete `authentication.py`

**Files:**
- Modify: `cms/server/admin/handlers/base.py:255-280` (`get_current_user`), `base.py:307-312` (`prepare`)
- Delete: `cms/server/admin/authentication.py`
- Modify: `cms/server/admin/server.py:46-83` (remove the `TODO(2.5b)` comment block and `self.auth_handler: None = None` line; no `auth_middleware` is passed — AWS never needs the generic `WebService.auth_handler` hook)
- Test: manual verification only (see Step 6below) — no unit test infrastructure exists for AWS's auth flow today (per spec's Testing section)

**Interfaces:**
- Produces: `BaseHandler._get_session_admin_id(self) -> int | None`, `BaseHandler._set_admin_session(self, admin_id: int) -> None`, both consumed by Task 2's `LoginHandler`/`LogoutHandler`/`NotificationsHandler` rewrite and by Task 3's `is_rpc_authorized`.
- Produces: `BaseHandler.COOKIE_NAME = "awslogin"` class attribute, consumed by Task 3.
- Consumes: nothing from other tasks (this is the foundation task).

- [ ] **Step 1: Delete `cms/server/admin/authentication.py`**

```bash
git rm cms/server/admin/authentication.py
```

- [ ] **Step 2: Replace `BaseHandler.get_current_user()` with the native-cookie version**

In `cms/server/admin/handlers/base.py`, replace the existing `get_current_user` method (lines 255-280):

```python
    def get_current_user(self) -> Admin | None:
        """Gets the current admin from cookies.

        return: if a valid cookie is retrieved, return
            the Admin object, otherwise None.

        """
        admin_id = self._get_session_admin_id()
        if admin_id is None:
            return None

        # Load admin.
        admin = self.sql_session.execute(
            select(Admin)
            .filter(Admin.id == admin_id)
            .filter(Admin.enabled.is_(True))
        ).scalars().first()
        if admin is None:
            self.clear_cookie(self.COOKIE_NAME)
            return None

        # Maybe refresh the cookie.
        if self.refresh_cookie:
            self._set_admin_session(admin_id)

        return admin

    def _get_session_admin_id(self) -> int | None:
        """Decode the awslogin cookie and return its admin id.

        Applies the same expiry/shape checks the old
        AWSAuthMiddleware._verify_cookie() did. Any failure (missing
        cookie, bad signature, malformed JSON, expired timestamp,
        wrong field types) is treated as "no session," not an error.

        return: the admin id stored in a valid, non-expired cookie,
            or None.

        """
        raw = self.get_secure_cookie(
            self.COOKIE_NAME,
            # We do our own expiry checking below, so an upper bound
            # here is fine (same reasoning as the old middleware's
            # max_age_days).
            max_age_days=math.ceil(
                config.admin_web_server.cookie_duration / 60 / 60 / 24),
        )
        if raw is None:
            return None
        try:
            session = json.loads(raw.decode())
        except (ValueError, UnicodeDecodeError):
            self.clear_cookie(self.COOKIE_NAME)
            return None

        admin_id = session.get("id", None)
        timestamp = session.get("timestamp", None)
        if admin_id is None or timestamp is None:
            self.clear_cookie(self.COOKIE_NAME)
            return None
        if not isinstance(admin_id, int) or not isinstance(timestamp, float):
            self.clear_cookie(self.COOKIE_NAME)
            return None
        if make_timestamp() - timestamp > config.admin_web_server.cookie_duration:
            self.clear_cookie(self.COOKIE_NAME)
            return None

        return admin_id

    def _set_admin_session(self, admin_id: int):
        """Write a fresh, signed awslogin cookie for the given admin.

        Used both at login and to refresh an existing session's
        expiry (there is no separate "refresh" operation: refreshing
        is just re-issuing the cookie with a new timestamp).

        admin_id: the id of the admin to store in the session.

        """
        payload = json.dumps({"id": admin_id, "timestamp": make_timestamp()})
        self.set_secure_cookie(self.COOKIE_NAME, payload, httponly=True)
```

Add the class attribute near the top of `BaseHandler` (right after the existing `PERMISSION_ALL`/`PERMISSION_MESSAGING`/`AUTHENTICATED` constants, `base.py:228-230`):

```python
    COOKIE_NAME = "awslogin"
```

Add `import math` to `base.py`'s imports if not already present (it is not — confirm with `grep -n "^import math" cms/server/admin/handlers/base.py`).

- [ ] **Step 3: Pre-warm `current_user` in `prepare()`**

Replace `BaseHandler.prepare()` (`base.py:307-312`):

```python
    async def prepare(self):
        """This method is executed at the beginning of each request.

        Resolves and caches current_user here, off the main thread,
        so every later synchronous read of self.current_user in this
        request (the @require_permission/@tornado.web.authenticated
        check, render_params()) hits Tornado's own cache instead of
        re-running a blocking DB query on the event loop thread.

        """
        await super().prepare()
        self.contest = None
        loop = asyncio.get_running_loop()
        self._current_user = await loop.run_in_executor(
            None, self.get_current_user)
```

Add `import asyncio` to `base.py`'s imports (confirm it's not already present).

- [ ] **Step 4: Update `cms/server/admin/server.py`**

Remove the `# TODO(2.5b): ...` comment block and the `"auth_middleware"` mention from the `parameters` dict comment (lines 57-71 region — the comment, not any active code, since `auth_middleware` was never actually being passed). Change:

```python
        self.auth_handler: None = None  # TODO(2.5b): real type once rewired
```

to simply removing that line entirely — `AdminWebServer` no longer has or needs an `auth_handler` attribute; `BaseHandler` now handles its own session state directly, and Task 3 rewires `is_rpc_authorized` to decode the cookie itself rather than reading `self.auth_handler.admin_id`.

- [ ] **Step 5: Verify imports and lint**

```bash
grep -n "^import math\|^import asyncio" cms/server/admin/handlers/base.py
.venv/bin/pyflakes cms/server/admin/handlers/base.py cms/server/admin/server.py
```

Expected: both imports present, pyflakes clean. Note `cms/server/admin/authentication.py` is now gone — confirm nothing else imports it:

```bash
grep -rn "authentication import\|from .authentication\|admin.authentication" cms/ | grep -v ".pyc"
```

Expected: no output (nothing references it outside the deleted file itself).

- [ ] **Step 6: Manual smoke check (Login only — full flow verified in the final task)**

```bash
.venv/bin/python3 -c "
import cms.server.admin.handlers.base
import cms.server.admin.server
print('imports OK')
"
```

Expected: `imports OK`, no traceback. (Full login/logout/expiry behavior can't be exercised standalone yet since `main.py`'s `LoginHandler`/`LogoutHandler` are still calling the now-deleted `self.service.auth_handler.set/clear` until Task 2 — this step only confirms Task 1's own files import cleanly.)

- [ ] **Step 7: Commit**

```bash
git add -A cms/server/admin/handlers/base.py cms/server/admin/server.py cms/server/admin/authentication.py
git commit -m "feat(aws): move admin session cookies into BaseHandler, native Tornado

Replaces AWSAuthMiddleware's werkzeug-local WSGI trick with Tornado's
own get_secure_cookie/set_secure_cookie, and pre-warms current_user
in prepare() via run_in_executor so its DB lookup never blocks the
main event loop thread."
```

(Copy the exact `Co-Authored-By` trailer from `git log -1 --format=%B` before finalizing this commit message.)

---

## Task 2: `main.py` — Login/Logout wiring + establish the `run_in_executor` handler pattern

**Files:**
- Modify: `cms/server/admin/handlers/main.py` (all 5 sync methods: `LoginHandler.post:49`, `LogoutHandler.post:96`, `ResourcesHandler.get:103`, `NotificationsHandler.get:139`, `MarkdownRenderHandler.post:173`)

**Interfaces:**
- Consumes: `BaseHandler._set_admin_session(self, admin_id: int)` and `BaseHandler.COOKIE_NAME` from Task 1.
- Produces: the canonical worked example of the mechanical `run_in_executor` transformation, referenced by name in Tasks 4-18 (each of those tasks restates the same recipe in full against its own file's methods — this task does not need to be read by their implementers, it only fixes the pattern's shape).

- [ ] **Step 1: Add `import asyncio`**

`main.py`'s current imports (lines 28-39) don't include it — add `import asyncio` near the top with the other stdlib imports (`json`, `logging`).

- [ ] **Step 2: Rewrite `LoginHandler.post` and `LogoutHandler.post`**

Before (`main.py:45-99`):

```python
class LoginHandler(SimpleHandler("login.html", authenticated=False)):
    """Login handler.

    """
    def post(self):
        error_args = {"login_error": "true"}
        next_page: str = self.get_argument("next", None)
        if next_page is not None:
            error_args["next"] = next_page
        next_page = normalize_login_next_page(next_page, self.url, self.url())
        error_page = self.url("login", **error_args)

        username: str = self.get_argument("username", "")
        password: str = self.get_argument("password", "")
        admin: Admin | None = self.sql_session.execute(
            select(Admin).filter(Admin.username == username)
        ).scalars().first()

        if admin is None:
            logger.warning("Nonexistent admin account: %s", username)
            self.redirect(error_page)
            return

        try:
            allowed = validate_password(admin.authentication, password)
        except ValueError:
            logger.warning("Unable to validate password for admin %r", username,
                           exc_info=True)
            allowed = False

        if not allowed or not admin.enabled:
            if not allowed:
                logger.info("Login error for admin %r from IP %s.", username,
                            self.request.remote_ip)
            elif not admin.enabled:
                logger.info("Login successful for admin %r from IP %s, but "
                            "account is disabled.", username,
                            self.request.remote_ip)
            self.redirect(error_page)
            return

        logger.info("Admin logged in: %r from IP %s.", username,
                    self.request.remote_ip)
        self.service.auth_handler.set(admin.id)
        self.redirect(next_page)


class LogoutHandler(BaseHandler):
    """Logout handler.

    """
    def post(self):
        self.service.auth_handler.clear()
        self.redirect(self.url())
```

After — this is the canonical worked example (a no-argument `post`, and one with the `self.service.auth_handler` calls replaced):

```python
class LoginHandler(SimpleHandler("login.html", authenticated=False)):
    """Login handler.

    """
    async def post(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)

    def _post_sync(self):
        error_args = {"login_error": "true"}
        next_page: str = self.get_argument("next", None)
        if next_page is not None:
            error_args["next"] = next_page
        next_page = normalize_login_next_page(next_page, self.url, self.url())
        error_page = self.url("login", **error_args)

        username: str = self.get_argument("username", "")
        password: str = self.get_argument("password", "")
        admin: Admin | None = self.sql_session.execute(
            select(Admin).filter(Admin.username == username)
        ).scalars().first()

        if admin is None:
            logger.warning("Nonexistent admin account: %s", username)
            self.redirect(error_page)
            return

        try:
            allowed = validate_password(admin.authentication, password)
        except ValueError:
            logger.warning("Unable to validate password for admin %r", username,
                           exc_info=True)
            allowed = False

        if not allowed or not admin.enabled:
            if not allowed:
                logger.info("Login error for admin %r from IP %s.", username,
                            self.request.remote_ip)
            elif not admin.enabled:
                logger.info("Login successful for admin %r from IP %s, but "
                            "account is disabled.", username,
                            self.request.remote_ip)
            self.redirect(error_page)
            return

        logger.info("Admin logged in: %r from IP %s.", username,
                    self.request.remote_ip)
        self._set_admin_session(admin.id)
        self.redirect(next_page)


class LogoutHandler(BaseHandler):
    """Logout handler.

    """
    async def post(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)

    def _post_sync(self):
        self.clear_cookie(self.COOKIE_NAME)
        self.redirect(self.url())
```

Note: `LoginHandler` inherits from `SimpleHandler("login.html", authenticated=False)`, whose `get` closure lives in `base.py` and is handled separately in Task 4 — only `post` belongs to `LoginHandler` itself here.

- [ ] **Step 3: Rewrite `ResourcesHandler.get`, `NotificationsHandler.get`, `MarkdownRenderHandler.post`**

Apply the identical mechanical transformation (rename body to `_get_sync`/`_post_sync`, add an `async def` wrapper awaiting `loop.run_in_executor(None, self._x_sync, *args)`, keeping any `@require_permission(...)` decorator on the new `async def` wrapper, not the renamed sync body) to:

- `ResourcesHandler.get(self, shard=None, contest_id=None)` (`main.py:103`) — has default-valued kwargs; pass them through positionally: `await loop.run_in_executor(None, self._get_sync, shard, contest_id)`.
- `NotificationsHandler.get(self)` (`main.py:139`) — no arguments.
- `MarkdownRenderHandler.post(self)` (`main.py:173`) — no arguments.

Each keeps its existing `@require_permission(BaseHandler.AUTHENTICATED)` decorator on the new `async def get`/`post`.

- [ ] **Step 4: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/main.py
grep -nE "^    def (get|post)\(" cms/server/admin/handlers/main.py
```

Expected: pyflakes clean; the grep must show no remaining un-prefixed `def get`/`def post` (only the `_get_sync`/`_post_sync` names and, separately, `async def get`/`async def post` — adjust the grep pattern to `^    (async )?def (get|post)\(` if needed to sanity-check both forms are as expected).

```bash
.venv/bin/python3 -c "import cms.server.admin.handlers.main; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add cms/server/admin/handlers/main.py
git commit -m "feat(aws): migrate main.py handlers to async + run_in_executor

Login/Logout now use BaseHandler's native cookie session helpers
instead of the removed auth_handler. Establishes the mechanical
run_in_executor wrapping pattern used across the rest of this
sub-project."
```

---

## Task 3: `/rpc` endpoint authorization — pass the handler into `rpc_auth`

**Files:**
- Modify: `cms/io/web_rpc.py:86-181` (`RPCHandler.initialize`, `.post`, `.make_route`)
- Modify: `cms/server/admin/server.py:105-107` (`is_rpc_authorized`)

**Interfaces:**
- Consumes: `BaseHandler.COOKIE_NAME` from Task 1 (reused directly; do not duplicate the `"awslogin"` literal). `BaseHandler._get_session_admin_id` itself is NOT reusable here (that's an instance method expecting a `BaseHandler`; `RPCHandler` doesn't inherit from `BaseHandler` — see module docstring at `web_rpc.py:20-22`), so `is_rpc_authorized` re-implements the same cookie-decode-and-look-up-admin logic against the raw `RPCHandler` instance instead. Run this task after Task 1.
- Produces: `rpc_auth` callback signature becomes `Callable[[RequestHandler, str, int, str], bool]` (was `Callable[[str, int, str], bool]`) — this is `cms/io/`'s only current consumer of `rpc_auth`/`make_route`, confirmed by `grep -rn rpc_auth cms/ cmsranking/` returning only `AdminWebServer`.

- [ ] **Step 1: Change `RPCHandler`'s `rpc_auth` call site to pass `self`**

In `cms/io/web_rpc.py`, update the type hints and the call:

```python
    def initialize(
        self, rpc_auth: Callable[["RPCHandler", str, int, str], bool] | None = None
    ):
        self._rpc_auth = rpc_auth
```

And in `post()` (`web_rpc.py:123-127`):

```python
        if self._rpc_auth is not None:
            loop = asyncio.get_running_loop()
            authorized = await loop.run_in_executor(
                None, self._rpc_auth, self, service_name, shard_int, method)
            if not authorized:
                raise tornado.web.HTTPError(403)
```

(Only the added `self,` argument changes.) Update `make_route`'s docstring/type hint too (`web_rpc.py:161-181`):

```python
    @classmethod
    def make_route(
        cls,
        url_pattern: str,
        rpc_auth: Callable[["RPCHandler", str, int, str], bool] | None,
    ) -> tuple[str, type, dict]:
        """Build a Tornado route entry for this handler.

        url_pattern: the URL regex, with three capture groups for
            service name, shard, and method (e.g.
            r"/rpc/([^/]+)/([0-9]+)/([^/]+)").
        rpc_auth: a function taking (handler, service_name, shard, method)
            and returning whether the request is allowed, or None to allow
            all requests.

        return: a (pattern, handler_class, kwargs) tuple usable
            directly in a tornado.web.Application's handlers list.

        """
        return (url_pattern, cls, {"rpc_auth": rpc_auth})
```

- [ ] **Step 2: Rewrite `AdminWebServer.is_rpc_authorized`**

In `cms/server/admin/server.py`, replace (lines 105-107):

```python
    def is_rpc_authorized(self, service: str, shard: int, method: str):
        return rpc_authorization_checker(self.auth_handler.admin_id,
                                         service, shard, method)
```

with:

```python
    def is_rpc_authorized(
        self, handler: "RPCHandler", service: str, shard: int, method: str,
    ) -> bool:
        admin_id = self._get_rpc_admin_id(handler)
        return rpc_authorization_checker(admin_id, service, shard, method)

    @staticmethod
    def _get_rpc_admin_id(handler: "RPCHandler") -> int | None:
        """Decode the awslogin cookie directly off an RPCHandler.

        RPCHandler doesn't inherit from BaseHandler (see
        cms/io/web_rpc.py's module docstring), so it can't reuse
        BaseHandler._get_session_admin_id -- but get_secure_cookie is
        a plain tornado.web.RequestHandler method, available here too,
        since cookie_secret is an Application-level setting.

        handler: the RPCHandler instance serving the current request.

        return: the admin id from a valid, non-expired, enabled-admin
            session, or None.

        """
        raw = handler.get_secure_cookie(
            BaseHandler.COOKIE_NAME,
            max_age_days=math.ceil(
                config.admin_web_server.cookie_duration / 60 / 60 / 24),
        )
        if raw is None:
            return None
        try:
            session = json.loads(raw.decode())
        except (ValueError, UnicodeDecodeError):
            return None

        admin_id = session.get("id", None)
        timestamp = session.get("timestamp", None)
        if admin_id is None or timestamp is None:
            return None
        if not isinstance(admin_id, int) or not isinstance(timestamp, float):
            return None
        if make_timestamp() - timestamp > config.admin_web_server.cookie_duration:
            return None

        with SessionGen() as sql_session:
            admin = sql_session.execute(
                select(Admin)
                .filter(Admin.id == admin_id)
                .filter(Admin.enabled.is_(True))
            ).scalars().first()
        return admin_id if admin is not None else None
```

Add the needed imports to `server.py`: `import json`, `import math`, `import typing`, `from sqlalchemy import select` (may already be partially imported — check), `from cms.db import Admin, SessionGen` (extend the existing `from cms.db import ...` line rather than duplicating it), `from cmscommon.datetime import make_timestamp`, `from .handlers.base import BaseHandler` (for `BaseHandler.COOKIE_NAME` — reuses Task 1's constant instead of duplicating the literal `"awslogin"` string), and `from cms.io.web_rpc import RPCHandler` under `if typing.TYPE_CHECKING:` purely for the type hint — avoid a real circular import.

- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/io/web_rpc.py cms/server/admin/server.py
.venv/bin/python3 -c "
import cms.io.web_rpc
import cms.server.admin.server
print('OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add cms/io/web_rpc.py cms/server/admin/server.py
git commit -m "fix(aws): resolve /rpc endpoint auth without the removed auth_handler

Passes the RPCHandler instance into the rpc_auth callback so
AdminWebServer.is_rpc_authorized can decode the awslogin cookie
itself, closing the gap 2.5a's handoff note flagged for 2.5b."
```

---

## Task 4: `base.py` — `SimpleHandler`/`SimpleContestHandler` factory closures

**Files:**
- Modify: `cms/server/admin/handlers/base.py:704-734`

**Interfaces:**
- Consumes: nothing new (uses the `run_in_executor` pattern from Task 2).
- Produces: the actual `get` implementation inherited by `LoginHandler`, `ContestHandler`, `ContestListHandler`, `UserListHandler`, `TeamListHandler`, `AddAdminHandler`, `AddTeamHandler`, `AddUserHandler`, `AddTaskHandler`, `TaskListHandler`, `RankingGroupListHandler`, `GroupListHandler`, and any other `SimpleHandler(...)`/`SimpleContestHandler(...)`-based class across the other handler files — those classes only add their own `post`, they all get `get` from here.

- [ ] **Step 1: Rewrite the three `get` closures inside `SimpleHandler`**

Before (`base.py:704-722`):

```python
def SimpleHandler(page, authenticated=True, permission_all=False) -> type[BaseHandler]:
    if permission_all:
        class Cls(BaseHandler):
            @require_permission(BaseHandler.PERMISSION_ALL)
            def get(self):
                self.r_params = self.render_params()
                self.render(page, **self.r_params)
    elif authenticated:
        class Cls(BaseHandler):
            @require_permission(BaseHandler.AUTHENTICATED)
            def get(self):
                self.r_params = self.render_params()
                self.render(page, **self.r_params)
    else:
        class Cls(BaseHandler):
            def get(self):
                self.r_params = self.render_params()
                self.render(page, **self.r_params)
    return Cls
```

After:

```python
def SimpleHandler(page, authenticated=True, permission_all=False) -> type[BaseHandler]:
    if permission_all:
        class Cls(BaseHandler):
            @require_permission(BaseHandler.PERMISSION_ALL)
            async def get(self):
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._get_sync)

            def _get_sync(self):
                self.r_params = self.render_params()
                self.render(page, **self.r_params)
    elif authenticated:
        class Cls(BaseHandler):
            @require_permission(BaseHandler.AUTHENTICATED)
            async def get(self):
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._get_sync)

            def _get_sync(self):
                self.r_params = self.render_params()
                self.render(page, **self.r_params)
    else:
        class Cls(BaseHandler):
            async def get(self):
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._get_sync)

            def _get_sync(self):
                self.r_params = self.render_params()
                self.render(page, **self.r_params)
    return Cls
```

- [ ] **Step 2: Rewrite `SimpleContestHandler`'s `get`**

Before (`base.py:725-734`):

```python
def SimpleContestHandler(page) -> type[BaseHandler]:
    class Cls(BaseHandler):
        @require_permission(BaseHandler.AUTHENTICATED)
        def get(self, contest_id: str):
            self.contest = self.safe_get_item(Contest, contest_id)

            self.r_params = self.render_params()
            self.render(page, **self.r_params)

    return Cls
```

After:

```python
def SimpleContestHandler(page) -> type[BaseHandler]:
    class Cls(BaseHandler):
        @require_permission(BaseHandler.AUTHENTICATED)
        async def get(self, contest_id: str):
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._get_sync, contest_id)

        def _get_sync(self, contest_id: str):
            self.contest = self.safe_get_item(Contest, contest_id)

            self.r_params = self.render_params()
            self.render(page, **self.r_params)

    return Cls
```

(`base.py` already has `import asyncio` from Task 1, Step 3.)

- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/base.py
.venv/bin/python3 -c "
from cms.server.admin.handlers.base import SimpleHandler, SimpleContestHandler
SimpleHandler('x.html')
SimpleContestHandler('y.html')
print('OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/base.py
git commit -m "feat(aws): migrate SimpleHandler/SimpleContestHandler get() to async"
```

---

## Task 5: `admin.py`

**Files:**
- Modify: `cms/server/admin/handlers/admin.py` (`AddAdminHandler.post:70`, `AdminsHandler.get:98`, `AdminHandler.get:122`, `AdminHandler.post:130`, `AdminHandler.delete:159`)

**Interfaces:**
- Consumes: the `run_in_executor` pattern from Task 2. No other cross-task interface.

- [ ] **Step 1: Add `import asyncio`** to `admin.py`'s imports (currently: `logging`, `sqlalchemy.select`, `cms.db.Admin`, `cmscommon.crypto.hash_password`, `cmscommon.datetime.make_datetime`, `.base` — no `asyncio`).

- [ ] **Step 2: Convert each method using the established recipe**

For each of the 5 methods below: rename the existing body to `_get_sync`/`_post_sync`/`_delete_sync` (same name, same parameters, unchanged body), and add a new `async def get`/`post`/`delete` with the same parameters and any existing decorator, whose entire body is:

```python
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._x_sync, <same positional args>)
```

Targets:
- `AddAdminHandler.post(self)` — `admin.py:70`
- `AdminsHandler.get(self)` — `admin.py:98`
- `AdminHandler.get(self, admin_id: str)` — `admin.py:122` → `run_in_executor(None, self._get_sync, admin_id)`
- `AdminHandler.post(self, admin_id: str)` — `admin.py:130` → `run_in_executor(None, self._post_sync, admin_id)`
- `AdminHandler.delete(self, admin_id: str)` — `admin.py:159` → `run_in_executor(None, self._delete_sync, admin_id)`

- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/admin.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/admin.py
```

Expected: pyflakes clean; grep returns nothing (all renamed to `_x_sync` or converted to `async def`).

```bash
.venv/bin/python3 -c "import cms.server.admin.handlers.admin; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/admin.py
git commit -m "feat(aws): migrate admin.py handlers to async + run_in_executor"
```

---

## Task 6: `contest.py`

**Files:**
- Modify: `cms/server/admin/handlers/contest.py` (`AddContestHandler.post:50`, `ContestHandler.post:86`, `OverviewHandler.get:161`, `ResourcesListHandler.get:171`, `ContestListHandler.post:193`, `RemoveContestHandler.get:214`, `RemoveContestHandler.delete:225`)

**Interfaces:**
- Consumes: the `run_in_executor` pattern from Task 2.

- [ ] **Step 1: Add `import asyncio`** to `contest.py`'s imports.

- [ ] **Step 2: Convert each method** using the Task 5-style recipe (rename body to `_x_sync` with unchanged parameters/body; new `async def` wrapper awaits `run_in_executor` with the same positional args; keep existing decorators on the wrapper):

- `AddContestHandler.post(self)` — `contest.py:50`
- `ContestHandler.post(self, contest_id: str)` — `contest.py:86`
- `OverviewHandler.get(self, contest_id: str | None = None)` — `contest.py:161` → pass `contest_id` through
- `ResourcesListHandler.get(self, contest_id: str | None = None)` — `contest.py:171`
- `ContestListHandler.post(self)` — `contest.py:193`
- `RemoveContestHandler.get(self, contest_id)` — `contest.py:214`
- `RemoveContestHandler.delete(self, contest_id)` — `contest.py:225`

- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/contest.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/contest.py
.venv/bin/python3 -c "import cms.server.admin.handlers.contest; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/contest.py
git commit -m "feat(aws): migrate contest.py handlers to async + run_in_executor"
```

---

## Task 7: `contestannouncement.py`

**Files:**
- Modify: `cms/server/admin/handlers/contestannouncement.py` (`AddAnnouncementHandler.post:48`, `AnnouncementHandler.delete:71`)

- [ ] **Step 1: Add `import asyncio`.**
- [ ] **Step 2: Convert** `AddAnnouncementHandler.post(self, contest_id: str)` (line 48) and `AnnouncementHandler.delete(self, contest_id: str, ann_id: str)` (line 71) using the established recipe.
- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/contestannouncement.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/contestannouncement.py
.venv/bin/python3 -c "import cms.server.admin.handlers.contestannouncement; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/contestannouncement.py
git commit -m "feat(aws): migrate contestannouncement.py handlers to async + run_in_executor"
```

---

## Task 8: `contestquestion.py`

**Files:**
- Modify: `cms/server/admin/handlers/contestquestion.py` (`QuestionsHandler.get:54`, `QuestionActionHandler.post:89`)

Note: `QuestionReplyHandler`, `QuestionIgnoreHandler`, `QuestionClaimHandler` all subclass `QuestionActionHandler` and inherit its `post` unchanged (they only override an internal abstract hook, not `post` itself) — converting `QuestionActionHandler.post` covers all three.

- [ ] **Step 1: Add `import asyncio`.**
- [ ] **Step 2: Convert** `QuestionsHandler.get(self, contest_id)` (line 54) and `QuestionActionHandler.post(self, contest_id, question_id)` (line 89).
- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/contestquestion.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/contestquestion.py
.venv/bin/python3 -c "import cms.server.admin.handlers.contestquestion; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/contestquestion.py
git commit -m "feat(aws): migrate contestquestion.py handlers to async + run_in_executor"
```

---

## Task 9: `contestranking.py`

**Files:**
- Modify: `cms/server/admin/handlers/contestranking.py` (`RankingHandler.get:45`)

- [ ] **Step 1: Add `import asyncio`.**
- [ ] **Step 2: Convert** `RankingHandler.get(self, contest_id, format="online")` (line 45) — pass both `contest_id` and `format` through positionally: `run_in_executor(None, self._get_sync, contest_id, format)`.
- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/contestranking.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/contestranking.py
.venv/bin/python3 -c "import cms.server.admin.handlers.contestranking; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/contestranking.py
git commit -m "feat(aws): migrate contestranking.py handlers to async + run_in_executor"
```

---

## Task 10: `contestsubmission.py`

**Files:**
- Modify: `cms/server/admin/handlers/contestsubmission.py` (`ContestSubmissionsHandler.get:40`, `ContestUserTestsHandler.get:57`)

- [ ] **Step 1: Add `import asyncio`.**
- [ ] **Step 2: Convert** `ContestSubmissionsHandler.get(self, contest_id)` (line 40) and `ContestUserTestsHandler.get(self, contest_id)` (line 57).
- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/contestsubmission.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/contestsubmission.py
.venv/bin/python3 -c "import cms.server.admin.handlers.contestsubmission; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/contestsubmission.py
git commit -m "feat(aws): migrate contestsubmission.py handlers to async + run_in_executor"
```

---

## Task 11: `contesttask.py`

**Files:**
- Modify: `cms/server/admin/handlers/contesttask.py` (`ContestTasksHandler.get:44`, `ContestTasksHandler.post:56`, `AddContestTaskHandler.post:164`)

- [ ] **Step 1: Add `import asyncio`.**
- [ ] **Step 2: Convert** `ContestTasksHandler.get(self, contest_id)` (line 44), `ContestTasksHandler.post(self, contest_id)` (line 56), `AddContestTaskHandler.post(self, contest_id)` (line 164).
- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/contesttask.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/contesttask.py
.venv/bin/python3 -c "import cms.server.admin.handlers.contesttask; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/contesttask.py
git commit -m "feat(aws): migrate contesttask.py handlers to async + run_in_executor"
```

---

## Task 12: `contestuser.py`

**Files:**
- Modify: `cms/server/admin/handlers/contestuser.py` (`ContestUsersHandler.get:60`, `ContestUsersHandler.post:75`, `RemoveParticipationHandler.get:107`, `RemoveParticipationHandler.delete:128`, `AddContestUserHandler.post:151`, `ParticipationHandler.get:189`, `ParticipationHandler.post:213`, `MessageHandler.post:279`)

- [ ] **Step 1: Add `import asyncio`.**
- [ ] **Step 2: Convert all 8 methods**, passing through each method's existing positional parameters (`contest_id`, and where present `user_id`):

- `ContestUsersHandler.get(self, contest_id)` — line 60
- `ContestUsersHandler.post(self, contest_id)` — line 75
- `RemoveParticipationHandler.get(self, contest_id, user_id)` — line 107
- `RemoveParticipationHandler.delete(self, contest_id, user_id)` — line 128
- `AddContestUserHandler.post(self, contest_id)` — line 151
- `ParticipationHandler.get(self, contest_id, user_id)` — line 189
- `ParticipationHandler.post(self, contest_id, user_id)` — line 213
- `MessageHandler.post(self, contest_id, user_id)` — line 279

- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/contestuser.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/contestuser.py
.venv/bin/python3 -c "import cms.server.admin.handlers.contestuser; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/contestuser.py
git commit -m "feat(aws): migrate contestuser.py handlers to async + run_in_executor"
```

---

## Task 13: `dataset.py`

**Files:**
- Modify: `cms/server/admin/handlers/dataset.py` (20 methods, see list below)

**Interfaces:**
- Consumes: the `run_in_executor` pattern from Task 2. This is the largest single file in the plan — treat it as one task since all 20 conversions are the same mechanical action, but check in on `pyflakes` after every 4-5 methods rather than only once at the end, to catch a mis-paste early.

- [ ] **Step 1: Add `import asyncio`.**
- [ ] **Step 2: Convert all 20 methods**, passing through each method's existing positional parameters unchanged:

- `DatasetSubmissionsHandler.get(self, dataset_id)` — line 62
- `CloneDatasetHandler.get(self, dataset_id_to_copy)` — line 94
- `CloneDatasetHandler.post(self, dataset_id_to_copy)` — line 116
- `RenameDatasetHandler.get(self, dataset_id)` — line 185
- `RenameDatasetHandler.post(self, dataset_id)` — line 196
- `DeleteDatasetHandler.get(self, dataset_id)` — line 227
- `DeleteDatasetHandler.post(self, dataset_id)` — line 238
- `ActivateDatasetHandler.get(self, dataset_id)` — line 255
- `ActivateDatasetHandler.post(self, dataset_id)` — line 281
- `ToggleAutojudgeDatasetHandler.post(self, dataset_id)` — line 328
- `AddManagerHandler.get(self, dataset_id)` — line 351
- `AddManagerHandler.post(self, dataset_id)` — line 362
- `DeleteManagerHandler.delete(self, dataset_id, manager_id)` — line 402
- `AddTestcaseHandler.get(self, dataset_id)` — line 423
- `AddTestcaseHandler.post(self, dataset_id)` — line 434
- `AddTestcasesHandler.get(self, dataset_id)` — line 495
- `AddTestcasesHandler.post(self, dataset_id)` — line 506
- `DeleteTestcaseHandler.delete(self, dataset_id, testcase_id)` — line 558
- `DownloadTestcasesHandler.get(self, dataset_id)` — line 581
- `DownloadTestcasesHandler.post(self, dataset_id)` — line 592

- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/dataset.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/dataset.py
.venv/bin/python3 -c "import cms.server.admin.handlers.dataset; print('OK')"
```

Expected: pyflakes clean, grep empty (all 20 converted).

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/dataset.py
git commit -m "feat(aws): migrate dataset.py handlers to async + run_in_executor"
```

---

## Task 14: `rankinggroup.py`

**Files:**
- Modify: `cms/server/admin/handlers/rankinggroup.py` (`RankingGroupListHandler.post:63`, `AddRankingGroupHandler.post:78`, `RankingGroupHandler.get:104`, `RankingGroupHandler.post:117`, `RemoveRankingGroupHandler.get:146`, `RemoveRankingGroupHandler.delete:158`, `RegenerateRankingHandler.post:175`)

- [ ] **Step 1: Add `import asyncio`.**
- [ ] **Step 2: Convert all 7 methods**:

- `RankingGroupListHandler.post(self)` — line 63
- `AddRankingGroupHandler.post(self)` — line 78
- `RankingGroupHandler.get(self, group_id: str)` — line 104
- `RankingGroupHandler.post(self, group_id: str)` — line 117
- `RemoveRankingGroupHandler.get(self, group_id: str)` — line 146
- `RemoveRankingGroupHandler.delete(self, group_id: str)` — line 158
- `RegenerateRankingHandler.post(self)` — line 175

- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/rankinggroup.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/rankinggroup.py
.venv/bin/python3 -c "import cms.server.admin.handlers.rankinggroup; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/rankinggroup.py
git commit -m "feat(aws): migrate rankinggroup.py handlers to async + run_in_executor"
```

---

## Task 15: `submission.py`

**Files:**
- Modify: `cms/server/admin/handlers/submission.py` (`SubmissionHandler.get:52`, `SubmissionDiffHandler.get:97`, `SubmissionCommentHandler.post:180`, `SubmissionOfficialStatusHandler.post:204`)

Note: `SubmissionFileHandler.get` (line 82) is already `async def` (a `FileHandlerMixin.fetch()` caller from 2.5a) — do not touch it.

- [ ] **Step 1: Add `import asyncio`.**
- [ ] **Step 2: Convert the 4 sync methods**:

- `SubmissionHandler.get(self, submission_id, dataset_id=None)` — line 52
- `SubmissionDiffHandler.get(self, old_id, new_id)` — line 97
- `SubmissionCommentHandler.post(self, submission_id, dataset_id=None)` — line 180
- `SubmissionOfficialStatusHandler.post(self, submission_id, dataset_id=None)` — line 204

- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/submission.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/submission.py
```

Expected: pyflakes clean; grep shows nothing (the already-async `SubmissionFileHandler.get` won't match this pattern since it's `async def`).

```bash
.venv/bin/python3 -c "import cms.server.admin.handlers.submission; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/submission.py
git commit -m "feat(aws): migrate submission.py handlers to async + run_in_executor"
```

---

## Task 16: `task.py`

**Files:**
- Modify: `cms/server/admin/handlers/task.py` (14 methods, see list below)

- [ ] **Step 1: Add `import asyncio`.**
- [ ] **Step 2: Convert all 14 methods**:

- `AddTaskHandler.post(self)` — line 53
- `TaskHandler.get(self, task_id)` — line 112
- `TaskHandler.post(self, task_id)` — line 127
- `AddStatementHandler.get(self, task_id)` — line 233
- `AddStatementHandler.post(self, task_id)` — line 242
- `StatementHandler.delete(self, task_id, statement_id)` — line 301
- `AddAttachmentHandler.get(self, task_id)` — line 321
- `AddAttachmentHandler.post(self, task_id)` — line 330
- `AttachmentHandler.delete(self, task_id, attachment_id)` — line 373
- `AddDatasetHandler.get(self, task_id)` — line 399
- `AddDatasetHandler.post(self, task_id)` — line 415
- `TaskListHandler.post(self)` — line 474
- `RemoveTaskHandler.get(self, task_id)` — line 495
- `RemoveTaskHandler.delete(self, task_id)` — line 505

- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/task.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/task.py
.venv/bin/python3 -c "import cms.server.admin.handlers.task; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/task.py
git commit -m "feat(aws): migrate task.py handlers to async + run_in_executor"
```

---

## Task 17: `user.py`

**Files:**
- Modify: `cms/server/admin/handlers/user.py` (18 methods, see list below)

- [ ] **Step 1: Add `import asyncio`.**
- [ ] **Step 2: Convert all 18 methods**:

- `UserHandler.get(self, user_id)` — line 44
- `UserHandler.post(self, user_id)` — line 64
- `UserListHandler.post(self)` — line 109
- `TeamListHandler.post(self)` — line 131
- `RemoveUserHandler.get(self, user_id)` — line 152
- `RemoveUserHandler.delete(self, user_id)` — line 168
- `RemoveTeamHandler.get(self, team_id)` — line 186
- `RemoveTeamHandler.delete(self, team_id)` — line 200
- `TeamHandler.get(self, team_id)` — line 231
- `TeamHandler.post(self, team_id)` — line 239
- `AddTeamHandler.post(self)` — line 270
- `AddUserHandler.post(self)` — line 302
- `AddParticipationHandler.post(self, user_id)` — line 341
- `EditParticipationHandler.post(self, user_id)` — line 379
- `GroupListHandler.post(self, contest_id)` — line 425
- `AddGroupHandler.post(self, contest_id)` — line 483
- `GroupHandler.get(self, contest_id, group_id)` — line 518
- `GroupHandler.post(self, contest_id, group_id)` — line 530

- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/user.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/user.py
.venv/bin/python3 -c "import cms.server.admin.handlers.user; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/user.py
git commit -m "feat(aws): migrate user.py handlers to async + run_in_executor"
```

---

## Task 18: `usertest.py`

**Files:**
- Modify: `cms/server/admin/handlers/usertest.py` (`UserTestHandler.get:34`)

Note: `UserTestFileHandler.get` (line 62) is already `async def` (2.5a `FileHandlerMixin.fetch()` caller) — do not touch it.

- [ ] **Step 1: Add `import asyncio`.**
- [ ] **Step 2: Convert** `UserTestHandler.get(self, user_test_id, dataset_id=None)` (line 34).
- [ ] **Step 3: Verify**

```bash
.venv/bin/pyflakes cms/server/admin/handlers/usertest.py
grep -nE "^    def (get|post|delete)\(" cms/server/admin/handlers/usertest.py
.venv/bin/python3 -c "import cms.server.admin.handlers.usertest; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cms/server/admin/handlers/usertest.py
git commit -m "feat(aws): migrate usertest.py handlers to async + run_in_executor"
```

---

## Task 19: Whole-sub-project verification

**Files:**
- None modified — verification only.

- [ ] **Step 1: Confirm no synchronous handler methods remain anywhere under `cms/server/admin/handlers/`**

```bash
grep -rnE "^    def (get|post|delete)\(" cms/server/admin/handlers/*.py
```

Expected: no output at all (every method across all 15 files plus the `base.py` factories is now either `async def` or a renamed `_x_sync` helper — the grep pattern only matches bare `def get`/`def post`/`def delete`, which should no longer exist).

- [ ] **Step 2: Full lint pass**

```bash
.venv/bin/pyflakes cms cmscommon cmscontrib cmsranking cmstaskenv cmstestsuite
```

Expected: clean (project-wide rule, `CLAUDE.md`).

- [ ] **Step 3: Confirm `cms/server/admin/authentication.py` is gone and unreferenced**

```bash
test ! -f cms/server/admin/authentication.py && echo "deleted, OK"
grep -rn "AWSAuthMiddleware" cms/ cmscommon/ cmscontrib/ cmsranking/ cmstestsuite/ 2>/dev/null
```

Expected: "deleted, OK", and the grep returns nothing.

- [ ] **Step 4: Functional test suite**

```bash
dropdb --if-exists cmsdbfortesting && createdb cmsdbfortesting && .venv/bin/cmsInitDB
.venv/bin/cmsRunFunctionalTests -v
```

Expected: passes at the same rate as on `origin/beta` before this plan started (no new failures attributable to AWS). If a functional test was already failing pre-existing on `beta` (check with `git stash` — no, per this repo's stash safety rule, instead check by running the same command against `git worktree` at the pre-plan commit if a failure looks suspicious), don't treat it as a regression from this plan; otherwise, stop and fix before continuing.

- [ ] **Step 5: Manual smoke test (the Review Focus scenarios)**

Start the stack locally (or via `./up.sh` if using the Docker workflow) and, using a browser:

1. Visit any `@require_permission`-gated AWS page while logged out (e.g. `/`) — confirm it redirects to `/login`, not a 500.
2. Log in with valid admin credentials — confirm redirect to the originally-requested page (or the default page) succeeds, and the `awslogin` cookie is set (check browser dev tools).
3. Navigate a few pages that render lists (e.g. the contest list, user list, task list — these use the `SimpleHandler` factory `get` from Task 4) — confirm they render normally.
4. Temporarily set `admin_web_server.cookie_duration` very low (e.g. `5`) in your local `cms.conf`, wait past it, then reload a protected page — confirm it redirects to login (expired-session path). Revert the config change afterward.
5. Log out — confirm redirect and that the `awslogin` cookie is cleared (dev tools).
6. If the AWS UI has any button that triggers an RPC call (e.g. an "invalidate submission" action, or a worker-control button on the resources page) — click it while logged in as an admin with sufficient permission, and confirm it succeeds (not a 403) — this exercises Task 3's `is_rpc_authorized` fix end-to-end.

Report the outcome of each of these 6 checks explicitly before considering this task (and the whole plan) done — do not claim success without having actually run them (per `superpowers:verification-before-completion`).

- [ ] **Step 6: Final commit (if Step 5 required any config revert or fixup)**

Only if Step 5's manual testing required a code fix (not just a local `cms.conf` change, which isn't committed): commit that fix with an accurate message describing what was found and corrected.

---
