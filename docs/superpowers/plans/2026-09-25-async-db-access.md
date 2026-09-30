# cms/db/ Async DB Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a parallel `AsyncSession`/`AsyncSessionGen` DB access layer for `cms/db/`, on top of the `psycopg` (v3) async driver, so sub-project 2.4 has a ready-to-use async DB layer when it migrates individual services.

**Architecture:** One new file, `cms/db/async_session.py`, mirrors `cms/db/session.py`'s `Session`/`SessionGen` with `AsyncSession`/`AsyncSessionGen`, reusing the existing declarative model classes unchanged (SQLAlchemy 2.0's `select()` style works identically against sync or async sessions). No existing `Session`/`SessionGen` call site is touched; no service is migrated.

**Tech Stack:** SQLAlchemy 2.0's `sqlalchemy.ext.asyncio` (`create_async_engine`, `async_sessionmaker`, `AsyncSession`), `psycopg` (v3) in async mode.

**Spec:** docs/superpowers/specs/2026-09-25-async-db-access-design.md

## Global Constraints

- No existing call site of `Session`/`SessionGen` (~41 files across `cms/service/`, `cms/server/`, `cms/grading/`, `cmscontrib/`, `cms/db/`) is modified by this plan.
- No service is migrated to use `AsyncSession` in this plan — that is sub-project 2.4's job.
- `cms/db/session.py`, `cms/io/PsycoGevent.py`, and `cms/db/fsobject.py` (`LargeObject`/`DBBackend`) are untouched.
- The async driver is `psycopg` (v3) in async mode — not `asyncpg`. Both `psycopg2` (sync) and `psycopg` (async) coexist in the same process; neither replaces the other in this plan.
- Model classes (`Base` and every subclass in `cms/db/*.py`) are unchanged and shared between sync and async access — no model duplication.
- `AsyncSession` has no two-phase-commit API (confirmed during plan-writing: `AsyncSession.begin()` takes no arguments and the class exposes no `begin_twophase`/`prepare`-equivalent methods, even though the underlying sync `Session` it wraps would accept a `twophase` constructor argument). `AsyncSessionGen` must raise `NotImplementedError` when `config.database.twophase_commit` is `True`, rather than silently running without two-phase guarantees.
- Anything that opens a real database connection must be verified against the Docker test image (`docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm --entrypoint bash testcms -c "..."`), not only the local `.venv` — this project's `.venv` has repeatedly drifted from the pinned Python version and dependency pins during this modernization effort (confirmed real, Docker-only-reproducible bugs in sub-project 2.2's Task 1 and its final review). `pyflakes` (which doesn't execute code) may run locally.

## Review Focus

- **A process importing both `cms.io` (which calls `make_psycopg_green()` unconditionally at import time) and `cms.db.async_session` in the same process** — the realistic shape of any service mid-migration in sub-project 2.4, using the sync bridge for some calls and the new async layer for others. `psycopg` (v3) is an entirely separate driver package from `psycopg2`, so the gevent wait callback (a `psycopg2.extensions`-specific hook) should have no way to affect it — but this line of work has repeatedly found real driver-coexistence bugs where "should have no way to" turned out wrong, so it must be verified, not assumed. Task 1's round-trip test explicitly imports `cms.io` first to exercise exactly this combination.
- **`config.database.twophase_commit = True`** — must fail loudly and immediately (`NotImplementedError` from `AsyncSessionGen.__aenter__`) the first time async DB access is attempted, not silently ignore the setting. Task 1 owns this test.
- **`AsyncSessionGen`'s rollback-on-exit-without-commit contract** — an implementer already familiar with the sync `SessionGen` might assume `async with AsyncSessionGen() as s: s.add(x)` with no explicit `await s.commit()` silently persists (an autocommit-like assumption). Task 1's round-trip test suite must prove the opposite: exiting without a commit discards the pending insert.
- **A raw, blocking `psycopg2` connection (the same pattern `LargeObject`'s `custom_psycopg2_connection()` uses) opened from inside a `loop.run_in_executor(None, ...)` worker thread, while the process-wide gevent wait callback is active** — the single riskiest unverified assumption carried over from sub-project 2.2's spec (which flagged it but explicitly deferred verification to this sub-project). Must be proven with a real query executed over a real executor-thread connection against the real test database, not a mock or an `isinstance` check. Task 2 owns this.
- **Two independent connection pools in the same process** (the sync `engine`'s pool and the new `async_engine`'s pool) whenever a future service uses both simultaneously during its 2.4 migration window — not exercised by any test in this plan (nothing uses both yet), and deliberately not a Non-Goal to fix here, but Task 1's code comments and this plan's Handoff section make it explicit so a 2.4 implementer doesn't assume connection-count sizing is unaffected by holding two pools open at once.

---

### Task 1: `cms/db/async_session.py` — AsyncEngine, AsyncSession, AsyncSessionGen

**Files:**
- Create: `cms/db/async_session.py`
- Modify: `cms/db/__init__.py` (add `async_session` imports/re-exports)
- Modify: `pyproject.toml` (add `psycopg[binary]` dependency)
- Modify: `constraints.txt` (pin `psycopg`/`psycopg-binary`)
- Test: `cmstestsuite/unit_tests/db/__init__.py` (new, empty package marker)
- Test: `cmstestsuite/unit_tests/db/async_session_test.py`

**Interfaces:**
- Consumes: `cms.conf.config.database.url` / `.debug` / `.twophase_commit` (existing `DatabaseConfig` fields, `cms/conf.py:81-84`, unchanged). `cms.db.RankingGroup` (existing model, `cms/db/rankinggroup.py`, unchanged — chosen for the round-trip test because it has no foreign keys or required relationships: just `name` and `description`, both non-nullable strings, no defaults needed).
- Produces (for later tasks and for sub-project 2.4 to consume): `cms.db.async_session.async_engine` (an `AsyncEngine`), `cms.db.async_session.AsyncSession` (an `async_sessionmaker` instance — call it to get an `AsyncSession` instance, e.g. `AsyncSession()`), `cms.db.async_session.AsyncSessionGen` (an async context manager class: `async with AsyncSessionGen() as session: ...`). All three re-exported from `cms.db` itself (`from cms.db import AsyncSession, AsyncSessionGen`), alongside — not replacing — the existing sync `Session`/`SessionGen`.

- [ ] **Step 1: Write the failing tests**

Create `cmstestsuite/unit_tests/db/__init__.py` (empty file — mirrors `cmstestsuite/unit_tests/io/__init__.py`, which sub-project 2.2 created the same way).

Create `cmstestsuite/unit_tests/db/async_session_test.py`:

```python
#!/usr/bin/env python3

"""Tests for cms.db.async_session."""

import unittest
from unittest.mock import patch

from sqlalchemy import select

# Importing cms.io first exercises the realistic condition this
# sub-project's Review Focus item 1 calls out: a process that has both
# cms.io (which calls make_psycopg_green() unconditionally at import
# time, cms/io/__init__.py) and cms.db.async_session imported at once,
# same as any service mid-migration in sub-project 2.4 would.
import cms.io  # noqa: F401
from cms.conf import config
from cms.db import RankingGroup
from cms.db.async_session import AsyncSessionGen


class TestAsyncSessionGenRoundTrip(unittest.IsolatedAsyncioTestCase):

    async def asyncTearDown(self):
        # Best-effort cleanup so repeated runs don't collide on
        # RankingGroup.name's unique constraint.
        async with AsyncSessionGen() as session:
            result = await session.execute(
                select(RankingGroup).where(
                    RankingGroup.name == self.name))
            for row in result.scalars().all():
                await session.delete(row)
            await session.commit()

    async def test_insert_commit_and_read_back(self):
        self.name = "async-db-round-trip-commit-test"
        async with AsyncSessionGen() as session:
            session.add(RankingGroup(
                name=self.name, description="round-trip test"))
            await session.commit()

        async with AsyncSessionGen() as session:
            result = await session.execute(
                select(RankingGroup).where(
                    RankingGroup.name == self.name))
            group = result.scalar_one()
            self.assertEqual(group.description, "round-trip test")

    async def test_rollback_discards_uncommitted(self):
        self.name = "async-db-round-trip-rollback-test"
        async with AsyncSessionGen() as session:
            session.add(RankingGroup(
                name=self.name, description="should not persist"))
            # No commit -- AsyncSessionGen.__aexit__ must roll back.

        async with AsyncSessionGen() as session:
            result = await session.execute(
                select(RankingGroup).where(
                    RankingGroup.name == self.name))
            self.assertIsNone(result.scalar_one_or_none())


class TestAsyncSessionGenTwophaseGuard(unittest.IsolatedAsyncioTestCase):

    async def test_raises_when_twophase_commit_enabled(self):
        with patch.object(config.database, "twophase_commit", True):
            with self.assertRaises(NotImplementedError):
                async with AsyncSessionGen():
                    pass

    async def test_does_not_raise_when_twophase_commit_disabled(self):
        with patch.object(config.database, "twophase_commit", False):
            async with AsyncSessionGen() as session:
                self.assertIsNotNone(session)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from the repo root, using the Docker test image — see Global Constraints):
```bash
docker compose -p cms-beta -f docker/docker-compose.test.yml build testcms
docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm --entrypoint bash testcms -c "pytest cmstestsuite/unit_tests/db/async_session_test.py -v"
```
Expected: FAIL with `ModuleNotFoundError: No module named 'cms.db.async_session'` (the module doesn't exist yet).

- [ ] **Step 3: Add the `psycopg[binary]` dependency**

In `pyproject.toml`, in the `dependencies` list, immediately after the existing `"psycopg2==2.9.10",` line, add:
```toml
    "psycopg[binary]==3.3.6", # https://www.psycopg.org/psycopg3/docs/news.html
```

- [ ] **Step 4: Pin the new dependency in `constraints.txt`**

In `constraints.txt` (alphabetically sorted, one `package==version` per line), insert these two lines between the existing `psutil==7.0.0` line and the existing `psycopg2==2.9.10` line:
```
psycopg==3.3.6
psycopg-binary==3.3.6
```
(`psycopg[binary]`'s binary extra installs as the separate `psycopg-binary` distribution; both need pinning. `psycopg` sorts before `psycopg-binary` sorts before `psycopg2` because `-` (0x2D) sorts before `2` (0x32).)

- [ ] **Step 5: Write `cms/db/async_session.py`**

```python
#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2013 Luca Wehrstedt <luca.wehrstedt@gmail.com>
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

"""Utilities related to SQLAlchemy async sessions.

Asyncio counterpart to cms.db.session: provides AsyncSession and
AsyncSessionGen on top of an async engine, for services migrated to
cms.io.async_service (sub-project 2.4) to use. No existing (synchronous)
call site is affected by this module -- see
docs/superpowers/specs/2026-09-25-async-db-access-design.md.

"""

import logging

from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession as _AsyncSession, async_sessionmaker, create_async_engine)

from cms.conf import config


logger = logging.getLogger(__name__)


def _async_database_url():
    """Derive the async engine's connection URL from config.database.url.

    Both cms.db's sync Session and this module's AsyncSession read
    connection parameters from the same cms.conf [database] section --
    only the driver component of the URL differs (postgresql+psycopg2
    for the sync engine, postgresql+psycopg for the async one; both
    route through the psycopg (v3) package, which supports both modes
    under the same dialect name). Returning the URL object itself
    (rather than str(url)) matters: str() on a SQLAlchemy URL masks the
    password with "***" by default, which would silently break every
    connection made with the swapped URL.

    return (sqlalchemy.engine.url.URL): the async engine's connection
        URL, with the real password intact.

    raise (AssertionError): if config.database.url isn't using the
        postgresql+psycopg2 driver this function expects to swap out.

    """
    sync_url = make_url(config.database.url)
    if sync_url.drivername != "postgresql+psycopg2":
        raise AssertionError(
            "cms.db.async_session expects config.database.url to use "
            "the postgresql+psycopg2 driver (got %r); it derives the "
            "async engine's URL by swapping that driver for "
            "postgresql+psycopg, the async mode of the same psycopg "
            "(v3) package." % sync_url.drivername)
    return sync_url.set(drivername="postgresql+psycopg")


# Mirrors cms/db/__init__.py's sync `engine = create_engine(...)` call:
# created once at import time, connects lazily on first actual use.
async_engine = create_async_engine(
    _async_database_url(), echo=config.database.debug,
    pool_timeout=60, pool_recycle=120)

AsyncSession = async_sessionmaker(async_engine)


class AsyncSessionGen:
    """Asyncio counterpart to cms.db.session.SessionGen.

    async with AsyncSessionGen() as session:
        await session.do_something()

    and at the end the session is automatically rolled back and
    closed. If one wants to commit the session, they have to call
    await session.commit() explicitly.

    Two-phase commit is not supported: AsyncSession has no
    begin_twophase()/prepare() equivalent -- SQLAlchemy's AsyncSession
    wraps a sync Session via a greenlet bridge, and while that inner
    sync Session would accept a twophase=True constructor argument,
    AsyncSession's own async transaction API (AsyncSession.begin())
    takes no such argument and exposes no two-phase methods at all. If
    config.database.twophase_commit is enabled, using this class would
    otherwise silently run without two-phase guarantees rather than
    failing outright -- __aenter__ raises instead, so this is caught
    the moment async DB access is actually attempted, not discovered
    later as a silent correctness gap.

    """

    def __init__(self):
        self.session: _AsyncSession | None = None

    async def __aenter__(self) -> _AsyncSession:
        if config.database.twophase_commit:
            raise NotImplementedError(
                "AsyncSessionGen does not support two-phase commit "
                "(config.database.twophase_commit is enabled, but "
                "SQLAlchemy's AsyncSession has no two-phase transaction "
                "API). Use the synchronous SessionGen for two-phase-"
                "commit code paths until this is resolved.")
        self.session = AsyncSession()
        return self.session

    async def __aexit__(self, unused1, unused2, unused3):
        await self.session.rollback()
        await self.session.close()
```

- [ ] **Step 6: Wire `async_session` into `cms/db/__init__.py`**

In `cms/db/__init__.py`, add to the `__all__` list, immediately after the existing `"Session", "ScopedSession", "SessionGen", "custom_psycopg2_connection",` line:
```python
    "async_engine", "AsyncSession", "AsyncSessionGen",
```

Immediately after the existing `from .session import Session, ScopedSession, SessionGen, \` / `    custom_psycopg2_connection` import block, add:
```python
from .async_session import async_engine, AsyncSession, AsyncSessionGen
```

- [ ] **Step 7: Install the new dependency and pyflakes-check locally**

```bash
.venv/bin/pip install "psycopg[binary]==3.3.6"
.venv/bin/pyflakes cms/db/async_session.py cms/db/__init__.py cmstestsuite/unit_tests/db/async_session_test.py
```
Expected: clean, no output. (`pyflakes` doesn't execute code, so this is safe to run against whatever Python version the local `.venv` happens to have — but does NOT substitute for the Docker test run in the next step, per Global Constraints.)

- [ ] **Step 8: Run the tests against the Docker test image and verify they pass**

```bash
docker compose -p cms-beta -f docker/docker-compose.test.yml build testcms
docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm --entrypoint bash testcms -c "pytest cmstestsuite/unit_tests/db/async_session_test.py -v"
```
Expected: PASS, all 4 tests (`test_insert_commit_and_read_back`, `test_rollback_discards_uncommitted`, `test_raises_when_twophase_commit_enabled`, `test_does_not_raise_when_twophase_commit_disabled`).

If `docker compose`/`podman` hangs due to this shared machine's known, transient `podman system service` lock contention (documented repeatedly during sub-project 2.2's execution — check with `timeout 30 podman ps -a` first), don't fight it more than a few minutes; fall back to a throwaway Python 3.12 venv against the same worktree (recipe: `psycopg2-binary` instead of `psycopg2` if there's no `pg_config` on the host, `PYTHONPATH` at the worktree root, `CMS_CONFIG` pointed at a real reachable Postgres). A real database connection is mandatory either way — this cannot be verified with mocks.

- [ ] **Step 9: Run the full `cms/db/`-adjacent regression suite**

```bash
docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm --entrypoint bash testcms -c "pytest cmstestsuite/unit_tests/db/ cmstestsuite/unit_tests/cmscontrib/ -v"
```
Expected: PASS — confirms nothing about the existing sync `Session`/`SessionGen`/model-import behavior regressed from adding the new module and the `cms/db/__init__.py` import.

- [ ] **Step 10: Commit**

```bash
git add cms/db/async_session.py cms/db/__init__.py pyproject.toml constraints.txt \
    cmstestsuite/unit_tests/db/__init__.py cmstestsuite/unit_tests/db/async_session_test.py
git commit -m "feat(db): add AsyncSession/AsyncSessionGen on psycopg (v3) async"
```

---

### Task 2: Verify `make_psycopg_green()` is safe for a raw connection in a `run_in_executor` worker thread

**Files:**
- Test: `cmstestsuite/unit_tests/db/psycopg_green_executor_test.py`

**Interfaces:**
- Consumes: `cms.db.custom_psycopg2_connection` (existing, `cms/db/session.py:76-113`, unchanged — the same raw-connection function `LargeObject` uses via `cms/db/fsobject.py`). `cms.io.PsycoGevent.is_psycopg_green` (existing, `cms/io/PsycoGevent.py:62-69`, unchanged). `cms.io` (importing it triggers the existing, unchanged `make_psycopg_green()` call at `cms/io/__init__.py:47`).
- Produces: nothing consumed by a later task — this is a standalone risk-verification task, proving or disproving the Risk documented in the spec and this plan's Review Focus. Its result (pass or fail) is itself the deliverable.

This task has no new production code to write — `custom_psycopg2_connection`, `is_psycopg_green`, and `make_psycopg_green` already exist and are unchanged. The "failing test" TDD cycle doesn't apply the usual way, because there's no new implementation to make the test pass against. Instead:

- [ ] **Step 1: Write the test**

Create `cmstestsuite/unit_tests/db/psycopg_green_executor_test.py`:

```python
#!/usr/bin/env python3

"""Tests proving that a raw, blocking psycopg2 connection (the same
kind LargeObject's custom_psycopg2_connection() opens, cms/db/fsobject.py)
behaves correctly when opened from inside an asyncio
loop.run_in_executor() worker thread, while the process-wide gevent
wait callback (cms.io.PsycoGevent.make_psycopg_green()) is active.

See docs/superpowers/specs/2026-09-25-async-db-access-design.md's Risks
section: this combination -- an AsyncService (sub-project 2.4) calling
FileCacher's default DBBackend, which uses LargeObject, via the
documented run_in_executor bridge (sub-project 2.2's spec) -- was left
as an untested assumption by sub-project 2.2. This test resolves it.

"""

import asyncio
import unittest

# Importing cms.io triggers make_psycopg_green() unconditionally at
# import time (cms/io/__init__.py) -- exactly the condition every real
# CMS service process runs under today, and that any AsyncService in
# sub-project 2.4 (which also imports cms.io indirectly, via
# cms.io.async_service) would run under too.
import cms.io  # noqa: F401
from cms.db import custom_psycopg2_connection
from cms.io.PsycoGevent import is_psycopg_green


class TestRawConnectionInExecutorThread(unittest.IsolatedAsyncioTestCase):

    def test_precondition_gevent_wait_callback_is_active(self):
        # If this fails, the test below wouldn't actually be exercising
        # the risk at all -- cms.io's own import-time side effect is
        # what's supposed to guarantee this.
        self.assertTrue(is_psycopg_green())

    async def test_query_completes_from_executor_thread(self):
        def run_query_in_thread():
            conn = custom_psycopg2_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return cur.fetchone()
            finally:
                conn.close()

        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, run_query_in_thread), timeout=5)
        self.assertEqual(result, (1,))

    async def test_concurrent_queries_from_multiple_executor_threads(self):
        # A single query completing could still hide a hang that only
        # shows up under real concurrency (e.g. the gevent wait callback
        # serializing on some global state it shouldn't). Run several at
        # once, in the default executor's thread pool.
        def run_query_in_thread(marker: int):
            conn = custom_psycopg2_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT %s", (marker,))
                    return cur.fetchone()
            finally:
                conn.close()

        loop = asyncio.get_running_loop()
        results = await asyncio.wait_for(
            asyncio.gather(*(
                loop.run_in_executor(None, run_query_in_thread, i)
                for i in range(5)
            )),
            timeout=10)
        self.assertEqual(sorted(r[0] for r in results), list(range(5)))
```

- [ ] **Step 2: Run the tests against the Docker test image**

```bash
docker compose -p cms-beta -f docker/docker-compose.test.yml build testcms
docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm --entrypoint bash testcms -c "pytest cmstestsuite/unit_tests/db/psycopg_green_executor_test.py -v"
```

**If all 3 tests PASS:** the risk is resolved — a raw `psycopg2` connection in a `run_in_executor` thread is safe under the process-wide gevent wait callback. Proceed to Step 3 (commit) with no other code changes.

**If any test FAILS or HANGS:** this is the real finding this task exists to surface, not a bug in the test itself to quietly work around. Do not modify `cms/io/PsycoGevent.py`, `cms/db/fsobject.py`, or `cms/db/session.py` to "fix" it — a real fix here needs its own design decision (e.g., should `LargeObject`/`DBBackend` unmake-green the connection for the duration of the executor-thread call via the existing `ungreen_psycopg()` context manager in `cms/io/PsycoGevent.py:87-105`? should it use a completely separate, never-green connection path?) that is outside this task's scope. Report `BLOCKED` with the exact failure/hang symptom, the full traceback or hang diagnosis (use the same `PYTHONFAULTHANDLER=1`/`timeout -s ABRT`-style diagnosis sub-project 2.2's Task 1 used to root-cause its own connection hang, if the failure mode is a hang rather than an exception), and stop — do not attempt a fix.

- [ ] **Step 3: Commit**

```bash
git add cmstestsuite/unit_tests/db/psycopg_green_executor_test.py
git commit -m "test(db): verify raw psycopg2 connections are safe in run_in_executor threads under make_psycopg_green()"
```
