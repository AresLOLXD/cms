# Participant Activity Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record, per participation, the intervals of activity with device, IP, start, last activity and how they ended, and show them (with alerts and CSV export) in AdminWebServer.

**Architecture:** CWS handlers report each authenticated request, login and logout to a per-process `ActivityRecorder` that only updates an in-memory buffer; an asyncio task flushes it every 60 s into the new `activity_intervals` table on the async session, under per-participation advisory locks. AWS reads the table with plain SQL (alerts computed on demand) and streams the CSV in keyset-paginated batches.

**Tech Stack:** Python 3.12, SQLAlchemy 2 (sync psycopg2 + async psycopg 3), PostgreSQL 16, Tornado, Jinja2, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-participant-activity-log-design.md`

## Global Constraints

- Code, comments, names and commit messages in English; Conventional Commits; commit trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- New file headers: `# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>` (AGPL header as in `cms/db/rankinggroup.py`).
- PEP 8, no pyflakes warnings, PEP 484 annotations, project docstring style (imperative first line, then argument/return sections).
- No per-request DB writes in CWS: handlers only touch the recorder's buffer.
- Defaults: `activity_flush_interval = 60` s, `activity_inactivity_threshold = 1800` s, device cookie `cms_device` lasting 365 days.
- Not recorded: failed logins, admin impersonation (cookie/header flag or API `admin_token`), user-agent. No backref on `Participation`.
- IP addresses are evidence only; the only alerts are "simultaneous activity" and "more than one device".
- Never push to main; never deploy (cms-live is frozen until 2026-10-10).

## Execution environment (read before any task)

- Worktree: `/var/home/areslolxd/Documentos/cms/.claude/worktrees/participant-activity-log`, branch `feat/participant-activity-log`. Stage paths explicitly (`git add <paths>`); never `git add -A`, stash, reset, amend or rebase.
- Run every Python tool through the wrapper, which sets `CMS_CONFIG` (private DB `activitylogfortesting` on the `cms-test-pg` podman container, port 55432), puts the worktree on `PYTHONPATH`, adds a `pg_dump` shim for the v16 server and uses a Python 3.12 venv:
  `bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q <paths>`
  `bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pyflakes <paths>`
  If the container is stopped: `podman start cms-test-pg`.
- Wrap pytest in `timeout --foreground --signal=ABRT 600`.
- Every test file in this plan belongs to the **asyncio** group (none imports a gevent-patching `cmscontrib` module; `cmscontrib.updaters.*` is safe). Never run them in one process with `cmstestsuite/unit_tests/cmscontrib`, `cmsranking`, `db/rankinggroup_test.py` or the two gevent `service/` files.

## Review Focus

- A participation deleted (bulk removal) between a request and the flush: its activity is dropped and the flush does not fail forever on the foreign key (Task 2, `test_deleted_participation_is_dropped_not_retried`).
- Two CWS shards flushing the same participation, the later activity first: one interval whose `started_at` is the earliest request (Task 2, `test_concurrent_flushes_of_two_shards_open_one_interval`; it fails without the advisory lock).
- A forged, expired or garbage `cms_device` cookie: a new id is issued, never a 500 (Task 4, `test_badly_signed_device_cookie_is_replaced`).
- After a password login the participation is expired: recording it must not reload it from the DB (Task 4, the existing `test_no_db_connection_after_*` tests in `login_offload_test.py`).
- An admin typing a malformed IP filter (`abc`, `10.0.0.`) gets a 400, and a network (`10.0.0.0/24`) or an IPv6 address filters correctly (Task 5, `test_invalid_ip_filter_is_a_400` and the filter tests).

---

### Task 1: `ActivityInterval` model and its migration

**Files:**
- Create: `cms/db/activity.py`
- Modify: `cms/db/__init__.py` (`__all__` and imports, around lines 50-110)
- Modify: `cms/db/base.py:22-62` (`_TYPE_MAP`)
- Create: `cmscontrib/updaters/fork_activity_intervals.py`
- Modify: `cmscontrib/SetupDB.py:31-33,161`
- Modify: `cmstestsuite/unit_tests/schema_diff_test.py:10,151`
- Test: `cmstestsuite/unit_tests/db/activity_test.py`

**Interfaces:**
- Produces: `cms.db.ActivityInterval` (columns `id, participation_id, device_id: uuid.UUID | None, ip: str (INET), started_at, last_seen_at, logged_out_at, started_by`); constants in `cms.db.activity`: `ACTIVITY_STARTED_BY_LOGIN = "login"`, `ACTIVITY_STARTED_BY_RESUMED = "resumed"`, `ACTIVITY_END_LOGOUT = "logout"`, `ACTIVITY_END_INACTIVITY = "inactivity"`, `ACTIVITY_END_ACTIVE = "active"`; `activity_end_reason(logged_out_at, last_seen_at, now, inactivity_threshold) -> str`; `cmscontrib.updaters.fork_activity_intervals.FORK_ACTIVITY_INTERVALS_SQL` and `apply_fork_activity_intervals_update() -> None`.
- Facts the next tasks rely on: `Base.__init__` ignores foreign-key columns, so `participation_id` is assigned after construction; psycopg2 (sync session) reads `INET` back as `ipaddress.IPv4Interface`/`IPv6Interface`, so display queries select `func.host(ip)`.

- [ ] **Step 1: Write the failing test**

Create `cmstestsuite/unit_tests/db/activity_test.py` (AGPL header with the 2026 copyright line, then):

```python
"""Tests for the ActivityInterval model.

"""

import ipaddress
import unittest
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import ActivityInterval, Participation
from cms.db.activity import ACTIVITY_END_ACTIVE, ACTIVITY_END_INACTIVITY, \
    ACTIVITY_END_LOGOUT, activity_end_reason


NOW = datetime(2026, 10, 12, 12, 0, 0)
THRESHOLD = timedelta(minutes=30)


class TestActivityEndReason(unittest.TestCase):

    def test_logout_wins(self):
        self.assertEqual(
            activity_end_reason(NOW, NOW, NOW, THRESHOLD),
            ACTIVITY_END_LOGOUT)

    def test_old_last_request_is_inactivity(self):
        self.assertEqual(
            activity_end_reason(None, NOW - timedelta(minutes=31), NOW,
                                THRESHOLD),
            ACTIVITY_END_INACTIVITY)

    def test_recent_last_request_is_active(self):
        self.assertEqual(
            activity_end_reason(None, NOW - timedelta(minutes=29), NOW,
                                THRESHOLD),
            ACTIVITY_END_ACTIVE)


class TestActivityIntervalModel(DatabaseMixin, unittest.TestCase):

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def add_interval(self, participation: Participation) -> ActivityInterval:
        self.session.flush()
        interval = ActivityInterval(
            device_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
            ip="10.0.0.5",
            started_at=NOW,
            last_seen_at=NOW,
            started_by="login")
        # Base.__init__ does not take foreign key columns.
        interval.participation_id = participation.id
        self.session.add(interval)
        return interval

    def test_round_trip(self):
        participation = self.add_participation()
        interval = self.add_interval(participation)
        self.session.commit()
        self.session.expire_all()

        self.assertEqual(interval.ip, ipaddress.ip_interface("10.0.0.5"))
        self.assertEqual(interval.device_id,
                         uuid.UUID("00000000-0000-4000-8000-000000000001"))
        self.assertIsNone(interval.logged_out_at)

    def test_deleting_the_participation_deletes_its_intervals(self):
        participation = self.add_participation()
        self.add_interval(participation)
        self.session.commit()

        self.session.delete(participation)
        self.session.commit()

        self.assertEqual(
            self.session.execute(select(ActivityInterval)).all(), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/db/activity_test.py`
Expected: collection ERROR, `ImportError: cannot import name 'ActivityInterval' from 'cms.db'`.

- [ ] **Step 3: Register the column types**

In `cms/db/base.py` add `import uuid` after `import typing`, change the dialect import to
```python
from sqlalchemy.dialects.postgresql import ARRAY, CIDR, INET, JSONB, OID, \
    UUID
```
and add to `_TYPE_MAP`, right after the `CIDR` entry:
```python
    INET: str,
    UUID: uuid.UUID,
```
(Without this, `Base.__declare_last__` raises `RuntimeError: Unknown SQLAlchemy column type for ColumnProperty device_id of ActivityInterval: UUID` at import.)

- [ ] **Step 4: Write the model**

Create `cms/db/activity.py` (AGPL header with the 2026 copyright line, then):

```python
"""Participant activity database interface for SQLAlchemy.

"""

from datetime import datetime, timedelta
from uuid import UUID as PythonUUID

from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.schema import Column, ForeignKey, Index
from sqlalchemy.types import DateTime, Enum, Integer

from . import Base, Participation


ACTIVITY_STARTED_BY_LOGIN = "login"
ACTIVITY_STARTED_BY_RESUMED = "resumed"

ACTIVITY_END_LOGOUT = "logout"
ACTIVITY_END_INACTIVITY = "inactivity"
ACTIVITY_END_ACTIVE = "active"


class ActivityInterval(Base):
    """Class to store a continuous interval of activity of a participation.

    One row covers the requests of one participation from one device
    and one IP address, with no gap longer than the inactivity
    threshold between them. How the interval ended is not stored: see
    activity_end_reason(). There is deliberately no relationship to
    (or backref on) Participation: DumpExporter follows every
    relationship, and the log is exported as CSV from AWS instead.

    """
    __tablename__ = 'activity_intervals'
    __table_args__ = (
        Index("ix_activity_intervals_participation_id_last_seen_at",
              "participation_id", "last_seen_at"),
    )

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    # Participation (id) the activity belongs to.
    participation_id: int = Column(
        Integer,
        ForeignKey(Participation.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False)

    # Anonymous id of the browser (the cms_device cookie), or None for
    # clients authenticated by the X-CMS-Authorization header.
    device_id: PythonUUID | None = Column(
        UUID(as_uuid=True),
        nullable=True)

    # Real client IP address, as resolved behind the proxies. It is
    # written as a str; psycopg2 reads it back as an ipaddress
    # interface, so queries for display select host(ip) instead.
    ip: str = Column(
        INET,
        nullable=False,
        index=True)

    # First and last request of the interval. On an explicit logout
    # last_seen_at equals logged_out_at.
    started_at: datetime = Column(
        DateTime,
        nullable=False)
    last_seen_at: datetime = Column(
        DateTime,
        nullable=False)

    # Time of the explicit logout that closed the interval, if any.
    logged_out_at: datetime | None = Column(
        DateTime,
        nullable=True)

    # Whether a username/password login opened the interval, or an
    # existing session (cookie, header or IP autologin) did.
    started_by: str = Column(
        Enum(ACTIVITY_STARTED_BY_LOGIN, ACTIVITY_STARTED_BY_RESUMED,
             name="activity_started_by"),
        nullable=False)


def activity_end_reason(
    logged_out_at: datetime | None,
    last_seen_at: datetime,
    now: datetime,
    inactivity_threshold: timedelta,
) -> str:
    """Return how an interval ended, or that it is still going on.

    The end is not stored: an interval without logout whose last
    request is older than the inactivity threshold ended then.

    logged_out_at: the interval's logout time, if any.
    last_seen_at: the interval's last request.
    now: the current time.
    inactivity_threshold: how long without requests ends an
        interval.

    return: one of ACTIVITY_END_LOGOUT, ACTIVITY_END_INACTIVITY and
        ACTIVITY_END_ACTIVE.

    """
    if logged_out_at is not None:
        return ACTIVITY_END_LOGOUT
    if last_seen_at < now - inactivity_threshold:
        return ACTIVITY_END_INACTIVITY
    return ACTIVITY_END_ACTIVE
```

In `cms/db/__init__.py`: in `__all__`, after the `"UserTestExecutable",` line of the usertest group, add
```python
    # activity
    "ActivityInterval",
```
and after `from .usertest import UserTest, UserTestFile, UserTestManager, \ UserTestResult, UserTestExecutable` add
```python
from .activity import ActivityInterval
```

- [ ] **Step 5: Run the model tests**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/db/activity_test.py`
Expected: 5 passed.

- [ ] **Step 6: Make the schema test cover the migration (failing)**

In `cmstestsuite/unit_tests/schema_diff_test.py` add after the `FORK_MULTI_CONTEST_SQL` import:
```python
from cmscontrib.updaters.fork_activity_intervals import \
    FORK_ACTIVITY_INTERVALS_SQL
```
and change the loop in `get_updated_schema` to:
```python
    # The fork's updates run twice: cmsSetupDB applies them at every
    # start, so they must be idempotent.
    for sql in [schema_sql, updater_sql,
                FORK_MULTI_CONTEST_SQL, FORK_ACTIVITY_INTERVALS_SQL,
                FORK_MULTI_CONTEST_SQL, FORK_ACTIVITY_INTERVALS_SQL]:
```

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/schema_diff_test.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'cmscontrib.updaters.fork_activity_intervals'`.

- [ ] **Step 7: Write the migration**

Create `cmscontrib/updaters/fork_activity_intervals.py` (AGPL header with the 2026 copyright line, then):

```python
"""Schema update of this fork for the participant activity log.

The SQL is a Python constant because Docker installs CMS non-editable
and setup.py does not package .sql files. Every statement is
idempotent, so it is safe to apply at each cmsSetupDB run. It creates
exactly what cmsInitDB creates for ActivityInterval (checked by
schema_diff_test).

"""

from cms.db import custom_psycopg2_connection


FORK_ACTIVITY_INTERVALS_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type WHERE typname = 'activity_started_by'
    ) THEN
        CREATE TYPE public.activity_started_by AS ENUM (
            'login',
            'resumed'
        );
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS public.activity_intervals (
    id serial PRIMARY KEY,
    participation_id integer NOT NULL
        REFERENCES public.participations(id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    device_id uuid,
    ip inet NOT NULL,
    started_at timestamp without time zone NOT NULL,
    last_seen_at timestamp without time zone NOT NULL,
    logged_out_at timestamp without time zone,
    started_by public.activity_started_by NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_activity_intervals_ip
    ON public.activity_intervals USING btree (ip);
CREATE INDEX IF NOT EXISTS ix_activity_intervals_participation_id_last_seen_at
    ON public.activity_intervals USING btree (participation_id, last_seen_at);
"""
```

Then copy the shape of `apply_fork_multi_contest_update()` from the end of `cmscontrib/updaters/fork_multi_contest.py` (read it first) into a new `apply_fork_activity_intervals_update() -> None` in this file, executing `FORK_ACTIVITY_INTERVALS_SQL` instead, with the same connection, commit and close handling and a docstring "Apply FORK_ACTIVITY_INTERVALS_SQL to the configured database."

In `cmscontrib/SetupDB.py` add, after the `fork_multi_contest` import:
```python
from cmscontrib.updaters.fork_activity_intervals import \
    apply_fork_activity_intervals_update
```
and in `setup_db()`, right after `apply_fork_multi_contest_update()`:
```python
    apply_fork_activity_intervals_update()
```

- [ ] **Step 8: Run the schema and model tests**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/schema_diff_test.py cmstestsuite/unit_tests/db/activity_test.py`
Expected: 6 passed (the migration, applied twice on the upgraded v1.5 schema, produces the same `pg_dump` as `cmsInitDB`).

- [ ] **Step 9: Lint and commit**

Run: `bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pyflakes cms/db/activity.py cms/db/base.py cms/db/__init__.py cmscontrib/updaters/fork_activity_intervals.py cmscontrib/SetupDB.py cmstestsuite/unit_tests/schema_diff_test.py cmstestsuite/unit_tests/db/activity_test.py`
Expected: no output.

```bash
git add cms/db/activity.py cms/db/base.py cms/db/__init__.py cmscontrib/updaters/fork_activity_intervals.py cmscontrib/SetupDB.py cmstestsuite/unit_tests/schema_diff_test.py cmstestsuite/unit_tests/db/activity_test.py
git commit -m "feat(db): add the activity_intervals table with its migration" -m "Store continuous intervals of activity of a participation from one device and one IP. cmsSetupDB applies an idempotent migration, checked against a fresh install by schema_diff_test." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `ActivityRecorder` buffer and flush

**Files:**
- Create: `cms/server/contest/activity.py`
- Test: `cmstestsuite/unit_tests/server/contest/activity_test.py`

**Interfaces:**
- Consumes: `cms.db.ActivityInterval`, `cms.db.activity.ACTIVITY_STARTED_BY_*` (Task 1); `cms.db.AsyncSessionGen`.
- Produces: `ActivityKey = tuple[int, UUID | None, str]`; `@dataclass PendingSegment(first_seen, last_seen, login: bool, logged_out_at=None)`; `class ActivityRecorder(inactivity_threshold: timedelta)` with `.inactivity_threshold`, `record(participation_id, device_id, ip, timestamp, login=False)`, `record_logout(participation_id, device_id, ip, timestamp)`, `pending() -> dict[ActivityKey, list[PendingSegment]]`, `async flush()`; module function `async write_pending_activity(session, pending, inactivity_threshold)` (patched by a test, so it must stay a module-level name looked up at call time).

- [ ] **Step 1: Write the failing tests**

Create `cmstestsuite/unit_tests/server/contest/activity_test.py` (AGPL header with the 2026 copyright line, then):

```python
"""Tests for the buffered recording of the participants' activity.

"""

import asyncio
import ipaddress
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import select

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import ActivityInterval, Participation, Session
from cms.db.async_session import async_engine
from cms.server.contest.activity import ActivityRecorder, PendingSegment


THRESHOLD = timedelta(minutes=30)
T0 = datetime(2026, 10, 12, 10, 0, 0)
DEVICE = uuid.UUID("00000000-0000-4000-8000-000000000001")
OTHER_DEVICE = uuid.UUID("00000000-0000-4000-8000-000000000002")
IP = "10.0.0.5"


def minutes(n: float) -> datetime:
    return T0 + timedelta(minutes=n)


class TestRecorderBuffer(unittest.TestCase):
    """The in-memory buffer, without a database."""

    def setUp(self):
        self.recorder = ActivityRecorder(THRESHOLD)

    def test_requests_of_one_key_make_one_segment(self):
        for n in (0, 1, 2):
            self.recorder.record(1, DEVICE, IP, minutes(n))
        self.assertEqual(self.recorder.pending(), {
            (1, DEVICE, IP): [PendingSegment(minutes(0), minutes(2), False)],
        })

    def test_out_of_order_request_does_not_move_last_seen_back(self):
        self.recorder.record(1, DEVICE, IP, minutes(2))
        self.recorder.record(1, DEVICE, IP, minutes(1))
        self.assertEqual(
            self.recorder.pending()[(1, DEVICE, IP)][0].last_seen, minutes(2))

    def test_login_starts_a_new_segment(self):
        self.recorder.record(1, DEVICE, IP, minutes(0))
        self.recorder.record(1, DEVICE, IP, minutes(1), login=True)
        self.assertEqual(self.recorder.pending()[(1, DEVICE, IP)], [
            PendingSegment(minutes(0), minutes(0), False),
            PendingSegment(minutes(1), minutes(1), True),
        ])

    def test_logout_closes_the_segment_and_later_activity_opens_another(self):
        self.recorder.record(1, DEVICE, IP, minutes(0))
        self.recorder.record_logout(1, DEVICE, IP, minutes(1))
        self.recorder.record(1, DEVICE, IP, minutes(2))
        self.assertEqual(self.recorder.pending()[(1, DEVICE, IP)], [
            PendingSegment(minutes(0), minutes(1), False, minutes(1)),
            PendingSegment(minutes(2), minutes(2), False),
        ])

    def test_gap_over_the_threshold_starts_a_new_segment(self):
        # Only happens when flushes fail for longer than the threshold.
        self.recorder.record(1, DEVICE, IP, minutes(0))
        self.recorder.record(1, DEVICE, IP, minutes(31))
        self.assertEqual(len(self.recorder.pending()[(1, DEVICE, IP)]), 2)

    def test_devices_and_ips_are_separate_keys(self):
        self.recorder.record(1, DEVICE, IP, minutes(0))
        self.recorder.record(1, OTHER_DEVICE, IP, minutes(0))
        self.recorder.record(1, None, IP, minutes(0))
        self.recorder.record(1, DEVICE, "10.0.0.6", minutes(0))
        self.assertEqual(len(self.recorder.pending()), 4)


class FlushTestBase(DatabaseMixin, unittest.IsolatedAsyncioTestCase):
    """A participation committed to the database, and a recorder."""

    def setUp(self):
        super().setUp()
        self.delete_data()
        self.participation = self.add_participation()
        self.session.commit()
        self.participation_id = self.participation.id
        self.session.close()
        self.recorder = ActivityRecorder(THRESHOLD)

    async def asyncTearDown(self):
        # Each test runs on its own event loop; don't let pooled
        # connections outlive it.
        await async_engine.dispose()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def intervals(self) -> list[ActivityInterval]:
        with Session() as session:
            return session.execute(
                select(ActivityInterval)
                .order_by(ActivityInterval.started_at,
                          ActivityInterval.id)
            ).scalars().all()


class TestFlush(FlushTestBase):

    async def test_first_flush_opens_an_interval(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(1))
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.participation_id, self.participation_id)
        self.assertEqual(interval.device_id, DEVICE)
        # psycopg2 reads INET as an interface.
        self.assertEqual(interval.ip, ipaddress.ip_interface(IP))
        self.assertEqual(interval.started_at, minutes(0))
        self.assertEqual(interval.last_seen_at, minutes(1))
        self.assertIsNone(interval.logged_out_at)
        self.assertEqual(interval.started_by, "resumed")
        self.assertEqual(self.recorder.pending(), {})

    async def test_activity_within_the_threshold_extends_the_interval(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        await self.recorder.flush()
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(29))
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.started_at, minutes(0))
        self.assertEqual(interval.last_seen_at, minutes(29))

    async def test_activity_after_the_threshold_opens_a_new_interval(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        await self.recorder.flush()
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(31))
        await self.recorder.flush()

        first, second = self.intervals()
        self.assertEqual(first.last_seen_at, minutes(0))
        self.assertEqual(second.started_at, minutes(31))
        self.assertEqual(second.started_by, "resumed")

    async def test_login_opens_a_new_interval_marked_login(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        await self.recorder.flush()
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(1),
                             login=True)
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(2))
        await self.recorder.flush()

        first, second = self.intervals()
        self.assertEqual(first.started_by, "resumed")
        self.assertEqual(first.last_seen_at, minutes(0))
        self.assertEqual(second.started_by, "login")
        self.assertEqual(second.started_at, minutes(1))
        self.assertEqual(second.last_seen_at, minutes(2))

    async def test_logout_closes_the_interval(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        await self.recorder.flush()
        self.recorder.record_logout(
            self.participation_id, DEVICE, IP, minutes(5))
        await self.recorder.flush()
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(6))
        await self.recorder.flush()

        first, second = self.intervals()
        self.assertEqual(first.last_seen_at, minutes(5))
        self.assertEqual(first.logged_out_at, minutes(5))
        self.assertEqual(second.started_at, minutes(6))
        self.assertIsNone(second.logged_out_at)

    async def test_header_clients_are_stored_without_device(self):
        self.recorder.record(self.participation_id, None, IP, minutes(0))
        await self.recorder.flush()
        self.recorder.record(self.participation_id, None, IP, minutes(1))
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertIsNone(interval.device_id)
        self.assertEqual(interval.last_seen_at, minutes(1))

    async def test_ipv6_addresses_are_stored(self):
        self.recorder.record(self.participation_id, DEVICE, "2001:db8::1",
                             minutes(0))
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.ip, ipaddress.ip_interface("2001:db8::1"))

    async def test_deleted_participation_is_dropped_not_retried(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        with Session() as session:
            session.delete(session.get(Participation, self.participation_id))
            session.commit()

        await self.recorder.flush()

        self.assertEqual(self.intervals(), [])
        self.assertEqual(self.recorder.pending(), {})

    async def test_failed_flush_keeps_the_activity_for_the_next_one(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        with patch("cms.server.contest.activity.write_pending_activity",
                   side_effect=OSError("database unavailable")), \
                self.assertLogs("cms.server.contest.activity", "ERROR"):
            await self.recorder.flush()
        self.assertEqual(self.intervals(), [])
        # Activity buffered while the database was unavailable.
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(1))

        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.started_at, minutes(0))
        self.assertEqual(interval.last_seen_at, minutes(1))

    async def test_concurrent_flushes_of_two_shards_open_one_interval(self):
        other_shard = ActivityRecorder(THRESHOLD)
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        other_shard.record(self.participation_id, DEVICE, IP, minutes(1))

        await asyncio.gather(self.recorder.flush(), other_shard.flush())

        [interval] = self.intervals()
        self.assertEqual(interval.started_at, minutes(0))
        self.assertEqual(interval.last_seen_at, minutes(1))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/server/contest/activity_test.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'cms.server.contest.activity'`.

- [ ] **Step 3: Write the recorder**

Create `cms/server/contest/activity.py` (AGPL header with the 2026 copyright line, then):

```python
"""Buffered recording of the participants' activity for CWS.

Handlers report each authenticated request, login and logout to an
ActivityRecorder, which only updates an in-memory buffer. A periodic
flush writes the buffer to the activity_intervals table in one
transaction, on the async session, so that no request ever waits for
the database because of this log.

"""

import dataclasses
import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cms.db import ActivityInterval, AsyncSessionGen, Participation
from cms.db.activity import ACTIVITY_STARTED_BY_LOGIN, \
    ACTIVITY_STARTED_BY_RESUMED


logger = logging.getLogger(__name__)


# (participation id, device id, IP address) of a stream of requests.
ActivityKey = tuple[int, UUID | None, str]


@dataclasses.dataclass
class PendingSegment:
    """Activity of one key not written to the database yet.

    first_seen and last_seen are the first and the last request of the
    segment; login tells whether a login started it; logged_out_at is
    the time of the logout that ended it, if any.

    """
    first_seen: datetime
    last_seen: datetime
    login: bool
    logged_out_at: datetime | None = None


class ActivityRecorder:
    """Buffer the participants' activity and write it periodically.

    One instance per CWS process. record() and record_logout() never
    touch the database; flush() writes what they buffered.

    """

    def __init__(self, inactivity_threshold: timedelta):
        """Create an empty recorder.

        inactivity_threshold: how long without requests ends an
            interval of activity.

        """
        self.inactivity_threshold = inactivity_threshold
        self._pending: dict[ActivityKey, list[PendingSegment]] = {}

    def record(
        self,
        participation_id: int,
        device_id: UUID | None,
        ip: str,
        timestamp: datetime,
        login: bool = False,
    ):
        """Buffer one authenticated request (or a login).

        participation_id: the participation that made the request.
        device_id: the device it came from, or None if unknown.
        ip: the real IP address it came from.
        timestamp: the time of the request.
        login: whether the request is a successful login, which always
            starts a new interval.

        """
        segments = self._pending.setdefault(
            (participation_id, device_id, ip), [])
        last = segments[-1] if segments else None
        if (login or last is None or last.logged_out_at is not None
                or timestamp - last.last_seen > self.inactivity_threshold):
            segments.append(PendingSegment(timestamp, timestamp, login))
        else:
            last.last_seen = max(last.last_seen, timestamp)

    def record_logout(
        self,
        participation_id: int,
        device_id: UUID | None,
        ip: str,
        timestamp: datetime,
    ):
        """Buffer an explicit logout.

        See record() for the arguments.

        """
        self.record(participation_id, device_id, ip, timestamp)
        self._pending[(participation_id, device_id, ip)][-1] \
            .logged_out_at = timestamp

    def pending(self) -> dict[ActivityKey, list[PendingSegment]]:
        """Return the buffered activity (for tests and diagnostics)."""
        return self._pending

    async def flush(self):
        """Write the buffered activity to the database.

        The buffer is swapped out first, so requests served while the
        flush waits on the database go to a fresh one. If writing
        fails, the swapped-out activity is put back in front of the
        new one and the next flush retries it.

        """
        pending, self._pending = self._pending, {}
        if not pending:
            return
        try:
            async with AsyncSessionGen() as session:
                await write_pending_activity(
                    session, pending, self.inactivity_threshold)
                await session.commit()
        except Exception:
            logger.error("Could not store the participants' activity; "
                         "the next flush retries it.", exc_info=True)
            for key, segments in self._pending.items():
                pending.setdefault(key, []).extend(segments)
            self._pending = pending


async def write_pending_activity(
    session: AsyncSession,
    pending: dict[ActivityKey, list[PendingSegment]],
    inactivity_threshold: timedelta,
):
    """Merge buffered activity into the activity_intervals table.

    Each participation is locked first (a transaction-level advisory
    lock, taken in ascending order to avoid deadlocks), so that two
    CWS shards flushing the same participation at the same time do not
    open the same interval twice. Activity of participations deleted in
    the meantime is dropped.

    session: the session to write with; the caller commits.
    pending: the buffered activity, by key.
    inactivity_threshold: the longest gap that still extends an
        interval.

    """
    participation_ids = sorted({key[0] for key in pending})
    for participation_id in participation_ids:
        await session.execute(
            select(func.pg_advisory_xact_lock(participation_id)))
    existing_ids = set((await session.execute(
        select(Participation.id)
        .where(Participation.id.in_(participation_ids))
    )).scalars().all())

    for (participation_id, device_id, ip), segments in pending.items():
        if participation_id not in existing_ids:
            continue
        interval: ActivityInterval | None = (await session.execute(
            select(ActivityInterval)
            .where(ActivityInterval.participation_id == participation_id)
            .where(ActivityInterval.device_id == device_id)
            .where(ActivityInterval.ip == ip)
            .order_by(ActivityInterval.last_seen_at.desc())
            .limit(1)
        )).scalars().first()
        for segment in segments:
            if (interval is not None
                    and interval.logged_out_at is None
                    and not segment.login
                    and segment.first_seen - interval.last_seen_at
                    <= inactivity_threshold):
                # Another shard may have written later activity first.
                interval.started_at = min(
                    interval.started_at, segment.first_seen)
                interval.last_seen_at = max(
                    interval.last_seen_at, segment.last_seen)
                interval.logged_out_at = segment.logged_out_at
            else:
                interval = ActivityInterval(
                    device_id=device_id,
                    ip=ip,
                    started_at=segment.first_seen,
                    last_seen_at=segment.last_seen,
                    logged_out_at=segment.logged_out_at,
                    started_by=(ACTIVITY_STARTED_BY_LOGIN if segment.login
                                else ACTIVITY_STARTED_BY_RESUMED))
                # Base.__init__ does not take foreign key columns.
                interval.participation_id = participation_id
                session.add(interval)
```

(`ActivityInterval.device_id == None` compiles to `IS NULL`, which is what header clients need.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/server/contest/activity_test.py`
Expected: 16 passed. Run `-k concurrent` three more times: it must pass every time.

- [ ] **Step 5: Lint and commit**

Run: `bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pyflakes cms/server/contest/activity.py cmstestsuite/unit_tests/server/contest/activity_test.py`
Expected: no output.

```bash
git add cms/server/contest/activity.py cmstestsuite/unit_tests/server/contest/activity_test.py
git commit -m "feat(cws): buffer the participants' activity and flush it in batches" -m "ActivityRecorder keeps requests, logins and logouts in memory and merges them into activity_intervals on the async session, under per-participation advisory locks so that concurrent shards open one interval. A failed flush keeps its activity for the next one." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Configuration and ContestWebServer wiring

**Files:**
- Modify: `cms/conf.py:117-136` (`CWSConfig`)
- Modify: `config/cms.sample.toml` (end of `[contest_web_server]`, after `#contest_admin_token`)
- Modify: `cms/server/contest/server.py:39-55,119-124`
- Test: `cmstestsuite/unit_tests/server/contest/server_test.py`

**Interfaces:**
- Consumes: `ActivityRecorder` (Task 2).
- Produces: `config.contest_web_server.activity_flush_interval: float = 60.0`, `config.contest_web_server.activity_inactivity_threshold: int = 1800`; `ContestWebServer.activity_recorder: ActivityRecorder`, flushed every `activity_flush_interval` and once more when `_async_run` ends.

- [ ] **Step 1: Write the failing tests**

In `cmstestsuite/unit_tests/server/contest/server_test.py` replace the imports with:
```python
import unittest
from unittest.mock import AsyncMock, patch

from cms import config
from cms.io import WebService
from cms.server.contest.server import ContestWebServer
```
and add before `if __name__ == "__main__":`:
```python
class TestActivityFlush(unittest.IsolatedAsyncioTestCase):
    """The activity log is flushed periodically and at shutdown."""

    def test_flush_is_scheduled_every_flush_interval(self):
        with patch.object(ContestWebServer, "add_timeout") as add_timeout:
            server = ContestWebServer(0)
        add_timeout.assert_any_call(
            server.activity_recorder.flush, None,
            config.contest_web_server.activity_flush_interval)

    async def test_shutdown_flushes_what_is_left(self):
        server = ContestWebServer(0)
        server.activity_recorder.flush = AsyncMock()
        with patch.object(WebService, "_async_run",
                          AsyncMock(return_value=True)):
            self.assertTrue(await server._async_run())
        server.activity_recorder.flush.assert_awaited_once_with()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/server/contest/server_test.py`
Expected: 2 failed with `AttributeError: 'ContestWebServer' object has no attribute 'activity_recorder'` (or on `activity_flush_interval`).

- [ ] **Step 3: Add the configuration**

In `cms/conf.py`, `CWSConfig`, after `contest_admin_token: str | None = None`:
```python

    # Participant activity log: seconds between writes of the buffered
    # activity, and seconds without requests that end an interval.
    activity_flush_interval: float = 60.0
    activity_inactivity_threshold: int = 30 * 60  # 30 minutes
```
In `config/cms.sample.toml`, after the line `#contest_admin_token = "CHANGE-ME"`:
```toml

# Participant activity log (AWS > contest > Activity). Each CWS buffers
# the activity in memory and writes it every activity_flush_interval
# seconds. activity_inactivity_threshold seconds without requests end
# an interval of activity.
activity_flush_interval = 60
activity_inactivity_threshold = 1800
```

- [ ] **Step 4: Wire the recorder into ContestWebServer**

In `cms/server/contest/server.py`: change `from datetime import datetime` to `from datetime import datetime, timedelta`; add `from .activity import ActivityRecorder` before `from .handlers import HANDLERS`; at the end of `__init__`, after the `self.proxy_service = self.connect_to(...)` statement, add:
```python

        self.activity_recorder = ActivityRecorder(timedelta(
            seconds=config.contest_web_server.activity_inactivity_threshold))
        self.add_timeout(self.activity_recorder.flush, None,
                         config.contest_web_server.activity_flush_interval)

    async def _async_run(self) -> bool:
        try:
            return await super()._async_run()
        finally:
            # The HTTP server has stopped: store what is left.
            await self.activity_recorder.flush()
```
(`add_timeout` starts the repeater only once the loop runs; `async_repeater` awaits coroutine results and logs exceptions. `WebService._async_run` stops the HTTP server in its own `finally`, so the last flush runs after the requests drained. An empty buffer makes `flush()` return without touching the DB.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/server/contest/server_test.py`
Expected: 3 passed.

- [ ] **Step 6: Lint and commit**

Run: `bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pyflakes cms/conf.py cms/server/contest/server.py cmstestsuite/unit_tests/server/contest/server_test.py`
Expected: no output.

```bash
git add cms/conf.py config/cms.sample.toml cms/server/contest/server.py cmstestsuite/unit_tests/server/contest/server_test.py
git commit -m "feat(cws): flush the activity log periodically and at shutdown" -m "Add activity_flush_interval and activity_inactivity_threshold to [contest_web_server] and give each ContestWebServer an ActivityRecorder." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Record requests, logins and logouts in the CWS handlers

**Files:**
- Modify: `cms/server/contest/handlers/contest.py` (imports; constants after `NOTIFICATION_SUCCESS`; `ContestHandler.__init__`; end of `get_current_user`; new `get_device_id`)
- Modify: `cms/server/contest/handlers/main.py` (`LoginHandler.post`, `LogoutHandler.post`, sqlalchemy import)
- Modify: `cms/server/contest/handlers/api.py` (`ApiLoginHandler.post`, sqlalchemy import)
- Modify: `cmstestsuite/unit_tests/server/contest/login_offload_test.py`
- Test: `cmstestsuite/unit_tests/server/contest/activity_handlers_test.py`

**Interfaces:**
- Consumes: `self.service.activity_recorder` (Task 3), `PendingSegment` (Task 2).
- Produces: `cms.server.contest.handlers.contest.DEVICE_COOKIE_NAME = "cms_device"`, `DEVICE_COOKIE_DAYS = 365`; `ContestHandler.get_device_id() -> uuid.UUID`; `ContestHandler.activity_device_id: uuid.UUID | None` (set by `get_current_user`).

- [ ] **Step 1: Write the failing end-to-end tests**

Create `cmstestsuite/unit_tests/server/contest/activity_handlers_test.py` (AGPL header with the 2026 copyright line, then):

```python
"""Tests for what a real ContestWebServer records in the activity log.

"""

import json
import unittest
import uuid

import tornado.web
from tornado.httpclient import HTTPResponse

from cmstestsuite.unit_tests.server.contest.xsrf_error_page_test import \
    CwsTestBase

from cms.db import Participation, User
from cms.server.contest.handlers.contest import DEVICE_COOKIE_NAME
from cmscommon.datetime import make_datetime, make_timestamp


class ActivityHandlersTests:
    """Tests that hold in both modes."""

    def setUp(self):
        super().setUp()
        self.participation_id = self.session.query(Participation.id) \
            .join(User).filter(User.username == "myuser").scalar()
        self.session.close()

    @property
    def main_page(self) -> str:
        """The path of the contest's main page."""
        return self.contest_path or "/"

    @property
    def login_cookie_name(self) -> str:
        return self.contest_name + "_login"

    def pending(self) -> dict:
        return self.cws.activity_recorder.pending()

    def signed(self, name: str, value: str) -> str:
        return tornado.web.create_signed_value(
            self.cws.application.settings["cookie_secret"], name, value
        ).decode()

    async def get_with(self, path: str, cookies: dict[str, str] | None = None,
                       headers: dict[str, str] | None = None) -> HTTPResponse:
        headers = dict(headers or {})
        if cookies:
            headers["Cookie"] = "; ".join(
                "%s=%s" % item for item in cookies.items())
        return await self.client.fetch(
            "http://127.0.0.1:%d%s" % (self.port, path), headers=headers,
            follow_redirects=False, raise_error=False)

    async def login(self) -> dict[str, str]:
        """Log in as a browser would; return the cookies it then holds."""
        token = await self.fetch_xsrf_token()
        response = await self.post(
            self.contest_path + "/login",
            {"username": "myuser", "password": "mypass", "_xsrf": token},
            cookies={"_xsrf": token})
        self.assertEqual(response.code, 302)
        cookies = self.set_cookies(response)
        cookies["_xsrf"] = token
        return cookies

    def device_of(self, cookies: dict[str, str]) -> uuid.UUID:
        value = tornado.web.decode_signed_value(
            self.cws.application.settings["cookie_secret"],
            DEVICE_COOKIE_NAME, cookies[DEVICE_COOKIE_NAME],
            max_age_days=365)
        return uuid.UUID(value.decode())

    async def test_login_records_a_login_with_a_new_device(self):
        cookies = await self.login()

        device = self.device_of(cookies)
        [(key, segments)] = self.pending().items()
        self.assertEqual(key, (self.participation_id, device, "127.0.0.1"))
        self.assertEqual([s.login for s in segments], [True])

    async def test_later_requests_reuse_the_device(self):
        cookies = await self.login()

        response = await self.get_with(self.main_page, cookies)

        self.assertEqual(response.code, 200)
        self.assertNotIn(DEVICE_COOKIE_NAME, self.set_cookies(response))
        [(key, segments)] = self.pending().items()
        self.assertEqual(key[1], self.device_of(cookies))
        self.assertEqual(len(segments), 1)
        self.assertGreater(segments[0].last_seen, segments[0].first_seen)

    async def test_badly_signed_device_cookie_is_replaced(self):
        cookies = await self.login()
        cookies[DEVICE_COOKIE_NAME] = "forged"

        response = await self.get_with(self.main_page, cookies)

        new_device = self.device_of(self.set_cookies(response))
        self.assertIn((self.participation_id, new_device, "127.0.0.1"),
                      self.pending())

    async def test_logout_records_the_logout(self):
        cookies = await self.login()

        response = await self.post(
            self.contest_path + "/logout", {"_xsrf": cookies["_xsrf"]},
            cookies=cookies)

        self.assertEqual(response.code, 302)
        [segments] = self.pending().values()
        self.assertIsNotNone(segments[-1].logged_out_at)

    async def test_anonymous_requests_are_not_recorded(self):
        response = await self.get_with(self.main_page)

        self.assertEqual(response.code, 200)
        self.assertEqual(self.pending(), {})
        self.assertNotIn(DEVICE_COOKIE_NAME, self.set_cookies(response))

    async def test_impersonation_is_not_recorded(self):
        cookie = json.dumps(
            ["myuser", "", make_timestamp(make_datetime()), True])

        response = await self.get_with(
            self.main_page,
            {self.login_cookie_name: self.signed(self.login_cookie_name,
                                                 cookie)})

        self.assertEqual(response.code, 200)
        self.assertEqual(self.pending(), {})

    async def test_header_authentication_has_no_device(self):
        cookies = await self.login()
        self.pending().clear()

        response = await self.get_with(
            self.main_page,
            headers={"X-CMS-Authorization": cookies[self.login_cookie_name]})

        self.assertEqual(response.code, 200)
        self.assertEqual(list(self.pending()),
                         [(self.participation_id, None, "127.0.0.1")])
        self.assertNotIn(DEVICE_COOKIE_NAME, self.set_cookies(response))


class SingleContestActivityHandlersTest(ActivityHandlersTests, CwsTestBase):
    multi_contest = False


class MultiContestActivityHandlersTest(ActivityHandlersTests, CwsTestBase):
    multi_contest = True


del CwsTestBase


if __name__ == "__main__":
    unittest.main()
```

(`del CwsTestBase` keeps pytest from collecting the imported base class in this module.)

- [ ] **Step 2: Give the login tests a recorder and expect the login to be recorded**

In `cmstestsuite/unit_tests/server/contest/login_offload_test.py`:
- imports: add `import uuid` and `from datetime import timedelta` with the other stdlib imports, and `from cms.server.contest.activity import ActivityRecorder, PendingSegment` after `from cms.server.contest import authentication`;
- after `CHECK_TIMEOUT = 2.0` add `DEVICE = uuid.UUID("00000000-0000-4000-8000-000000000001")`;
- in `LoginHandlerOffloadTests.make_handler`, before `self.stub_response(handler)`:
```python
        handler.application = MagicMock()
        handler.application.service.activity_recorder = \
            ActivityRecorder(timedelta(minutes=30))
        handler.get_device_id = lambda: DEVICE
```
  and add the method:
```python
    def recorded_activity(self, handler) -> dict:
        return handler.service.activity_recorder.pending()
```
- extend `test_successful_login` and `test_failed_login`:
```python
    async def test_successful_login(self):
        handler = self.make_handler()
        await self.run_post(handler)
        self.assert_login_succeeded(handler)
        self.assertEqual(self.recorded_activity(handler), {
            (self.participation_id, self.login_device_id, "127.0.0.1"):
                [PendingSegment(self.timestamp, self.timestamp, True)],
        })

    async def test_failed_login(self):
        handler = self.make_handler(password="wrong")
        await self.run_post(handler)
        self.assert_login_failed(handler)
        self.assertEqual(self.recorded_activity(handler), {})
```
- `TestLoginHandlerOffload`: add class attribute `login_device_id = DEVICE`;
- `TestApiLoginHandlerOffload`: add
```python
    # API clients authenticate with a header, which carries no device.
    login_device_id = None
```
- at the end of `TestApiLoginHandlerOffload.test_admin_token_login`:
```python
        # An admin impersonating the user is not the user's activity.
        self.assertEqual(self.recorded_activity(handler), {})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/server/contest/activity_handlers_test.py cmstestsuite/unit_tests/server/contest/login_offload_test.py`
Expected: `ImportError: cannot import name 'DEVICE_COOKIE_NAME'` for the new file; in `login_offload_test.py` `test_successful_login` fails (nothing recorded) for both handlers.

- [ ] **Step 4: Device cookie and request recording in `ContestHandler`**

In `cms/server/contest/handlers/contest.py`: add `import uuid` after `import typing`; after `NOTIFICATION_SUCCESS = "success"` add
```python

# Anonymous id of the browser, for the participant activity log. It is
# global (not per contest), so a computer is recognized in every contest.
DEVICE_COOKIE_NAME = "cms_device"
DEVICE_COOKIE_DAYS = 365
```
in `ContestHandler.__init__`, after `self.impersonated_by_admin = False`:
```python
        # Device of the current request for the activity log: None
        # until get_current_user() authenticates it, and for clients
        # authenticated by the X-CMS-Authorization header.
        self.activity_device_id: uuid.UUID | None = None
        self._device_id: uuid.UUID | None = None
```
replace the end of `get_current_user` (`self.impersonated_by_admin = impersonated` / `return participation`) with:
```python
        self.impersonated_by_admin = impersonated
        if participation is not None and not impersonated:
            self.activity_device_id = \
                None if authorization_header is not None \
                else self.get_device_id()
            self.service.activity_recorder.record(
                participation.id, self.activity_device_id,
                self.request.remote_ip, self.timestamp)
        return participation

    def get_device_id(self) -> uuid.UUID:
        """Return the anonymous id of the browser, issuing it if needed.

        The id lives in the signed cms_device cookie. A missing,
        expired or badly signed cookie is replaced by a new random id.

        return: the device id.

        """
        if self._device_id is None:
            value = self.get_secure_cookie(
                DEVICE_COOKIE_NAME, max_age_days=DEVICE_COOKIE_DAYS)
            try:
                self._device_id = uuid.UUID(value.decode("ascii"))
            except (AttributeError, UnicodeDecodeError, ValueError):
                self._device_id = uuid.uuid4()
                self.set_secure_cookie(
                    DEVICE_COOKIE_NAME, str(self._device_id),
                    expires_days=DEVICE_COOKIE_DAYS, httponly=True)
        return self._device_id
```
(`get_secure_cookie` returns `None` for a missing or badly signed cookie, hence `AttributeError`. `max_age_days` must be passed: Tornado's default of 31 days would reject older device cookies.)

- [ ] **Step 5: Logins and logout**

In `cms/server/contest/handlers/main.py`: change `from sqlalchemy import select, func` to `from sqlalchemy import inspect, select, func`; in `LoginHandler.post` replace the final `if participation is None: ... else: self.redirect(next_page)` with:
```python
        if participation is None:
            self.redirect(error_page)
        else:
            # The login expired the participation: read its id from the
            # identity key, as loading it again would need the database.
            self.service.activity_recorder.record(
                inspect(participation).identity[0], self.get_device_id(),
                self.request.remote_ip, self.timestamp, login=True)
            self.redirect(next_page)
```
and replace `LogoutHandler.post`'s body with:
```python
        participation: Participation | None = self.current_user
        if participation is not None and not self.impersonated_by_admin:
            self.service.activity_recorder.record_logout(
                participation.id, self.activity_device_id,
                self.request.remote_ip, self.timestamp)
        self.clear_cookie(self.contest.name + "_login")
        self.redirect(self.contest_url())
```
In `cms/server/contest/handlers/api.py`: change `from sqlalchemy import select` to `from sqlalchemy import inspect, select`; in `ApiLoginHandler.post`, right before `if participation is None: self.json({"error": "Login failed"}, 403)`, add:
```python
        if participation is not None and admin_token == "":
            # API clients authenticate with the X-CMS-Authorization
            # header, which carries no device. The login expired the
            # participation: read its id from the identity key.
            self.service.activity_recorder.record(
                inspect(participation).identity[0], None,
                self.request.remote_ip, self.timestamp, login=True)
```
(Using `participation.id` here instead would reload the expired object and make the existing `test_no_db_connection_after_a_successful_check` tests fail; that is intended.)

- [ ] **Step 6: Run the CWS tests**

Run: `timeout --foreground --signal=ABRT 900 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/server/contest`
Expected: all pass (about 290 tests; the new file contributes 14, `login_offload_test.py` 45).

- [ ] **Step 7: Lint and commit**

Run: `bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pyflakes cms/server/contest/handlers/contest.py cms/server/contest/handlers/main.py cms/server/contest/handlers/api.py cmstestsuite/unit_tests/server/contest/login_offload_test.py cmstestsuite/unit_tests/server/contest/activity_handlers_test.py`
Expected: no output.

```bash
git add cms/server/contest/handlers/contest.py cms/server/contest/handlers/main.py cms/server/contest/handlers/api.py cmstestsuite/unit_tests/server/contest/login_offload_test.py cmstestsuite/unit_tests/server/contest/activity_handlers_test.py
git commit -m "feat(cws): record participants' requests, logins and logouts" -m "Identify browsers with an anonymous signed cms_device cookie and report every authenticated request to the activity recorder; header-authenticated API clients have no device, and admin impersonation is not recorded." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: AWS activity page with alerts

**Files:**
- Create: `cms/server/admin/handlers/contestactivity.py`
- Create: `cms/server/admin/templates/contest_activity.html`
- Create: `cms/server/admin/templates/macro/activity.html`
- Modify: `cms/server/admin/handlers/__init__.py` (import block near line 45; routes after the ranking routes near line 189)
- Modify: `cms/server/admin/templates/base.html:240` (contest menu)
- Modify: `cmscommon/datetime.py:77-90` (`get_timezone` accepts `user=None`)
- Test: `cmstestsuite/unit_tests/server/admin/contestactivity_test.py`

**Interfaces:**
- Consumes: `ActivityInterval`, `activity_end_reason`, `ACTIVITY_END_*` (Task 1); config fields (Task 3).
- Produces (module `cms.server.admin.handlers.contestactivity`): `ACTIVITY_PAGE_SIZE = 100`; `IPNetwork`; `inactivity_threshold() -> timedelta`; `simultaneous_margin() -> timedelta`; `parse_activity_filters(username: str, ip: str) -> tuple[str, IPNetwork | None]` (raises `HTTPError(400)`); `select_intervals(contest_id: int, username: str = "", network: IPNetwork | None = None) -> Select` (row fields `id, participation_id, user_id, username, first_name, last_name, device_id, ip (text via host()), started_at, last_seen_at, logged_out_at, started_by`); `find_simultaneous_activity(session, contest_id, participation_id=None) -> list[Row]` (fields `first_id, second_id, user_id, username, first_device_id, first_ip, second_device_id, second_ip, overlap_start, overlap_end`); `find_multiple_devices(session, contest_id) -> list[Row]` (fields `user_id, username, devices`); `activity_render_params(session, contest, participation_id=None) -> dict` (keys `activity_simultaneous`, `activity_flagged_ids`, `activity_end_reason`, `timezone`); `ContestActivityHandler`. Macros `macro/activity.html`: `intervals_table(url, contest, intervals, end_reason, flagged_ids, show_user)`, `simultaneous_table(url, contest, rows)`.
- An interval's time range is `[started_at, last_seen_at]` (on logout `last_seen_at == logged_out_at`; while active `last_seen_at` lags at most one flush, absorbed by the margin).

- [ ] **Step 1: Write the failing tests**

Create `cmstestsuite/unit_tests/server/admin/contestactivity_test.py` (AGPL header with the 2026 copyright line, then):

```python
"""Tests for the AWS pages of the participants' activity.

"""

import ipaddress
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import tornado.web

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import ActivityInterval, Participation
from cms.server.admin.handlers.contestactivity import \
    ContestActivityHandler, find_multiple_devices, \
    find_simultaneous_activity, parse_activity_filters, select_intervals
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cms.server.util import Url


T0 = datetime(2026, 10, 12, 10, 0, 0)
LAPTOP = uuid.UUID("00000000-0000-4000-8000-000000000001")
PHONE = uuid.UUID("00000000-0000-4000-8000-000000000002")


def minutes(n: float) -> datetime:
    return T0 + timedelta(minutes=n)


class ActivityTestBase(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.session.flush()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def add_interval(self, participation: Participation,
                     device: uuid.UUID | None, ip: str,
                     start: float, end: float) -> ActivityInterval:
        interval = ActivityInterval(
            device_id=device, ip=ip, started_at=minutes(start),
            last_seen_at=minutes(end), started_by="resumed")
        interval.participation_id = participation.id
        self.session.add(interval)
        self.session.flush()
        return interval

    def simultaneous(self, participation_id=None):
        return find_simultaneous_activity(
            self.session, self.contest.id, participation_id)


class TestParseActivityFilters(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(parse_activity_filters(" ", ""), ("", None))

    def test_single_address_and_network(self):
        self.assertEqual(parse_activity_filters("u", "10.0.0.5"),
                         ("u", ipaddress.ip_network("10.0.0.5/32")))
        self.assertEqual(parse_activity_filters("", "10.0.0.7/24")[1],
                         ipaddress.ip_network("10.0.0.0/24"))

    def test_invalid_ip_filter_is_a_400(self):
        for text in ("abc", "10.0.0.", "10.0.0.0/33"):
            with self.subTest(text=text), \
                    self.assertRaises(tornado.web.HTTPError) as error:
                parse_activity_filters("", text)
            self.assertEqual(error.exception.status_code, 400)


class TestSelectIntervals(ActivityTestBase):

    def test_ip_is_plain_text_and_filters_work(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 10)
        self.add_interval(self.participation, LAPTOP, "2001:db8::1", 20, 30)
        other = self.add_participation()  # Another contest.
        self.session.flush()
        self.add_interval(other, LAPTOP, "10.0.0.5", 0, 10)

        def ips(username="", network=None):
            return sorted(row.ip for row in self.session.execute(
                select_intervals(self.contest.id, username, network)))

        self.assertEqual(ips(), ["10.0.0.5", "2001:db8::1"])
        self.assertEqual(ips(network=ipaddress.ip_network("10.0.0.0/24")),
                         ["10.0.0.5"])
        self.assertEqual(ips(network=ipaddress.ip_network("2001:db8::/32")),
                         ["2001:db8::1"])
        self.assertEqual(ips(username=self.participation.user.username),
                         ["10.0.0.5", "2001:db8::1"])
        self.assertEqual(ips(username="nobody"), [])


class TestSimultaneousActivity(ActivityTestBase):

    def test_two_devices_overlapping_are_reported(self):
        first = self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        second = self.add_interval(self.participation, PHONE, "189.1.1.1", 30, 90)

        [row] = self.simultaneous()

        self.assertEqual({row.first_id, row.second_id}, {first.id, second.id})
        self.assertEqual((row.overlap_start, row.overlap_end),
                         (minutes(30), minutes(60)))
        self.assertEqual(row.username, self.participation.user.username)

    def test_dynamic_ip_change_on_one_device_is_not_reported(self):
        self.add_interval(self.participation, LAPTOP, "189.1.1.1", 0, 60)
        self.add_interval(self.participation, LAPTOP, "189.1.1.2", 60, 120)
        self.assertEqual(self.simultaneous(), [])

    def test_overlap_under_the_margin_is_not_reported(self):
        # The default margin is 2 minutes (twice the flush interval).
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, PHONE, "10.0.0.6", 59, 90)
        self.assertEqual(self.simultaneous(), [])

    def test_without_device_different_ips_are_reported(self):
        self.add_interval(self.participation, None, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, LAPTOP, "10.0.0.6", 0, 60)
        self.assertEqual(len(self.simultaneous()), 1)

    def test_without_device_same_ip_is_not_reported(self):
        self.add_interval(self.participation, None, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.assertEqual(self.simultaneous(), [])

    def test_filter_by_participation(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, PHONE, "10.0.0.6", 0, 60)
        other = self.add_participation(contest=self.contest)
        self.session.flush()

        self.assertEqual(len(self.simultaneous(self.participation.id)), 1)
        self.assertEqual(self.simultaneous(other.id), [])


class TestMultipleDevices(ActivityTestBase):

    def test_counts_distinct_non_null_devices(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 10)
        self.add_interval(self.participation, LAPTOP, "10.0.0.6", 20, 30)
        self.add_interval(self.participation, PHONE, "10.0.0.6", 40, 50)
        single = self.add_participation(contest=self.contest)
        self.session.flush()
        self.add_interval(single, LAPTOP, "10.0.0.7", 0, 10)
        self.add_interval(single, None, "10.0.0.7", 0, 10)

        [row] = find_multiple_devices(self.session, self.contest.id)

        self.assertEqual(row.user_id, self.participation.user_id)
        self.assertEqual(row.devices, 2)


class PageTestBase(ActivityTestBase):
    """Build AWS handlers that render with the real templates."""

    def make_handler(self, handler_class=ContestActivityHandler,
                     **arguments):
        handler = handler_class.__new__(handler_class)
        handler.sql_session = self.session
        handler.contest = None
        handler.r_params = None
        handler._current_user = MagicMock(permission_all=True)
        handler._current_user.name = "admin"
        handler.request = MagicMock()
        handler.application = MagicMock()
        handler.application.service.jinja2_environment = AWS_ENVIRONMENT
        handler.static_url_helper = lambda *args, **kwargs: ""
        handler.url = Url("/")
        handler.xsrf_form_html = lambda: ""
        handler.get_query_argument = \
            lambda name, default=None: arguments.get(name, default)
        handler.chunks = []
        handler.write = handler.chunks.append
        return handler


class TestContestActivityPage(PageTestBase):

    def test_renders_alerts_and_intervals(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, PHONE, "189.1.1.1", 30, 90)
        self.session.commit()
        handler = self.make_handler(ip="189.1.1.0/24")

        handler._get_sync(str(self.contest.id))

        page = "".join(handler.chunks)
        self.assertIn("Simultaneous activity (1)", page)
        self.assertIn("More than one device (1)", page)
        self.assertIn("189.1.1.1", page)
        self.assertNotIn(">10.0.0.5<", page)  # Filtered out of the table.
        self.assertIn("activity/csv?ip=189.1.1.0%2F24", page)

    def test_unknown_contest_is_a_404(self):
        with self.assertRaises(tornado.web.HTTPError) as error:
            self.make_handler()._get_sync("999999")
        self.assertEqual(error.exception.status_code, 404)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/server/admin/contestactivity_test.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'cms.server.admin.handlers.contestactivity'`.

- [ ] **Step 3: Let `get_timezone` take no user**

In `cmscommon/datetime.py`, `get_timezone`: annotate `user: "User | None"`, document `user: the user owning the timezone, or None for the contest's own.`, and change `if user.timezone is not None:` to `if user is not None and user.timezone is not None:`.

- [ ] **Step 4: Write the handler module**

Create `cms/server/admin/handlers/contestactivity.py` (AGPL header with the 2026 copyright line, then):

```python
"""Participant activity handlers for AWS for a specific contest.

"""

import asyncio
import functools
import ipaddress
from datetime import timedelta

import tornado.web
from sqlalchemy import Select, and_, distinct, func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session, aliased
from sqlalchemy.types import DateTime

from cms import config
from cms.db import ActivityInterval, Contest, Participation, User
from cms.db.activity import activity_end_reason
from cmscommon.datetime import get_timezone, make_datetime
from .base import BaseHandler, require_permission


ACTIVITY_PAGE_SIZE = 100

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def inactivity_threshold() -> timedelta:
    """Return how long without requests ends an interval."""
    return timedelta(
        seconds=config.contest_web_server.activity_inactivity_threshold)


def simultaneous_margin() -> timedelta:
    """Return the shortest overlap reported as simultaneous activity.

    It is twice the flush interval, which bounds how stale the
    last_seen_at of an active interval can be.

    """
    return timedelta(
        seconds=2 * config.contest_web_server.activity_flush_interval)


def parse_activity_filters(
    username: str, ip: str
) -> tuple[str, IPNetwork | None]:
    """Parse the filters of the activity page and of its CSV.

    username: the username to show only, or empty for all.
    ip: an IP address or network to show only, or empty for all.

    return: the stripped username and the network (None for all).

    raise (tornado.web.HTTPError): 400 if ip is not an address or a
        network.

    """
    username = username.strip()
    ip = ip.strip()
    if ip == "":
        return username, None
    try:
        return username, ipaddress.ip_network(ip, strict=False)
    except ValueError:
        raise tornado.web.HTTPError(
            400, "Invalid IP address or network: %s", ip)


def select_intervals(
    contest_id: int,
    username: str = "",
    network: IPNetwork | None = None,
) -> Select:
    """Return the query of the intervals of a contest, with their user.

    The IP address is selected as plain text (host()), since psycopg2
    reads INET values as ipaddress interfaces.

    contest_id: the contest.
    username: only this user's intervals, if not empty.
    network: only the intervals from this network, if not None.

    return: the query; callers add ordering and limits.

    """
    query = (
        select(ActivityInterval.id,
               ActivityInterval.participation_id,
               Participation.user_id,
               User.username,
               User.first_name,
               User.last_name,
               ActivityInterval.device_id,
               func.host(ActivityInterval.ip).label("ip"),
               ActivityInterval.started_at,
               ActivityInterval.last_seen_at,
               ActivityInterval.logged_out_at,
               ActivityInterval.started_by)
        .join(Participation,
              Participation.id == ActivityInterval.participation_id)
        .join(User, User.id == Participation.user_id)
        .where(Participation.contest_id == contest_id))
    if username != "":
        query = query.where(User.username == username)
    if network is not None:
        query = query.where(ActivityInterval.ip.op("<<=")(str(network)))
    return query


def find_simultaneous_activity(
    session: Session,
    contest_id: int,
    participation_id: int | None = None,
) -> list[Row]:
    """Return the pairs of intervals active at the same time.

    Two intervals of one participation count when they come from
    different devices (or, if either has no device, from different
    IP addresses) and overlap by more than simultaneous_margin(). A
    dynamic IP change on one device gives consecutive intervals of the
    same device, which never count.

    session: the session to query with.
    contest_id: the contest.
    participation_id: only this participation's pairs, if not None.

    return: one row per pair, most recent overlap first.

    """
    first = aliased(ActivityInterval)
    second = aliased(ActivityInterval)
    overlap_start = func.greatest(
        first.started_at, second.started_at, type_=DateTime)
    overlap_end = func.least(
        first.last_seen_at, second.last_seen_at, type_=DateTime)
    query = (
        select(first.id.label("first_id"),
               second.id.label("second_id"),
               Participation.user_id,
               User.username,
               first.device_id.label("first_device_id"),
               func.host(first.ip).label("first_ip"),
               second.device_id.label("second_device_id"),
               func.host(second.ip).label("second_ip"),
               overlap_start.label("overlap_start"),
               overlap_end.label("overlap_end"))
        .select_from(first)
        .join(second, and_(second.participation_id == first.participation_id,
                           second.id > first.id))
        .join(Participation, Participation.id == first.participation_id)
        .join(User, User.id == Participation.user_id)
        .where(Participation.contest_id == contest_id)
        .where(or_(first.device_id != second.device_id,
                   and_(or_(first.device_id.is_(None),
                            second.device_id.is_(None)),
                        first.ip != second.ip)))
        .where(overlap_end - overlap_start > simultaneous_margin())
        .order_by(overlap_start.desc()))
    if participation_id is not None:
        query = query.where(first.participation_id == participation_id)
    return session.execute(query).all()


def find_multiple_devices(session: Session, contest_id: int) -> list[Row]:
    """Return the participations seen on more than one device.

    session: the session to query with.
    contest_id: the contest.

    return: rows with user_id, username and the number of devices,
        most devices first.

    """
    devices = func.count(distinct(ActivityInterval.device_id))
    query = (
        select(Participation.user_id, User.username,
               devices.label("devices"))
        .select_from(ActivityInterval)
        .join(Participation,
              Participation.id == ActivityInterval.participation_id)
        .join(User, User.id == Participation.user_id)
        .where(Participation.contest_id == contest_id)
        .group_by(Participation.id, Participation.user_id, User.username)
        .having(devices > 1)
        .order_by(devices.desc(), User.username))
    return session.execute(query).all()


def activity_render_params(
    session: Session,
    contest: Contest,
    participation_id: int | None = None,
) -> dict:
    """Return the render parameters shared by the activity views.

    session: the session to query with.
    contest: the contest.
    participation_id: only this participation's alerts, if not None.

    return: the simultaneous activity, the ids of the intervals in it,
        the end reason function for the templates and the timezone.

    """
    simultaneous = find_simultaneous_activity(
        session, contest.id, participation_id)
    return {
        "activity_simultaneous": simultaneous,
        "activity_flagged_ids":
            {row.first_id for row in simultaneous}
            | {row.second_id for row in simultaneous},
        "activity_end_reason": functools.partial(
            activity_end_reason, now=make_datetime(),
            inactivity_threshold=inactivity_threshold()),
        "timezone": get_timezone(None, contest),
    }


class ContestActivityHandler(BaseHandler):
    """Shows the participants' activity in a contest, with its alerts.

    """
    def _get_sync(self, contest_id: str):
        self.contest = self.safe_get_item(Contest, contest_id)
        ip_text = self.get_query_argument("ip", "")
        username, network = parse_activity_filters(
            self.get_query_argument("username", ""), ip_text)
        page = int(self.get_query_argument("page", 0))

        query = select_intervals(self.contest.id, username, network)
        count = self.sql_session.execute(
            select(func.count()).select_from(query.subquery())
        ).scalar_one()
        filters = {}
        if username != "":
            filters["username"] = username
        if network is not None:
            filters["ip"] = ip_text.strip()

        self.r_params = self.render_params()
        self.r_params.update(
            activity_render_params(self.sql_session, self.contest))
        self.r_params.update({
            "activity_intervals": self.sql_session.execute(
                query.order_by(ActivityInterval.last_seen_at.desc(),
                               ActivityInterval.id.desc())
                .offset(page * ACTIVITY_PAGE_SIZE)
                .limit(ACTIVITY_PAGE_SIZE)).all(),
            "activity_count": count,
            "activity_page": page,
            "activity_pages":
                (count + ACTIVITY_PAGE_SIZE - 1) // ACTIVITY_PAGE_SIZE,
            "activity_page_url": functools.partial(
                self.url, "contest", self.contest.id, "activity", **filters),
            "activity_csv_url": self.url(
                "contest", self.contest.id, "activity", "csv", **filters),
            "activity_filters": {"username": username,
                                 "ip": filters.get("ip", "")},
            "multiple_devices":
                find_multiple_devices(self.sql_session, self.contest.id),
            "simultaneous_margin": simultaneous_margin(),
        })
        self.render("contest_activity.html", **self.r_params)

    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self, contest_id: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, contest_id)
```

- [ ] **Step 5: Write the templates and routes**

Create `cms/server/admin/templates/macro/activity.html`:

```jinja
{% macro device(device_id) -%}
{% if device_id is none %}<span title="Authenticated by header: no device">&mdash;</span>{% else %}<span title="{{ device_id }}">{{ (device_id|string)[:8] }}</span>{% endif %}
{%- endmacro %}


{% macro intervals_table(url, contest, intervals, end_reason, flagged_ids, show_user) -%}
{#
Show intervals of activity, as returned by select_intervals.

end_reason (function): activity_end_reason bound to now and the threshold.
flagged_ids (set): ids of the intervals in simultaneous activity.
show_user (bool): whether to add a username column.
#}
{% if intervals %}
<table class="bordered">
  <thead>
    <tr>
      {% if show_user %}<th>Username</th>{% endif %}
      <th>Device</th>
      <th>IP address</th>
      <th>Start</th>
      <th>Last activity</th>
      <th>End</th>
      <th>Started by</th>
    </tr>
  </thead>
  <tbody>
    {% for row in intervals %}
    <tr{% if row.id in flagged_ids %} style="background-color: #fdd;"{% endif %}>
      {% if show_user %}<td><a href="{{ url("contest", contest.id, "user", row.user_id, "edit") }}">{{ row.username }}</a></td>{% endif %}
      <td>{{ device(row.device_id) }}</td>
      <td>{{ row.ip }}</td>
      <td>{{ row.started_at|format_datetime }}</td>
      <td>{{ row.last_seen_at|format_datetime }}</td>
      <td>{{ end_reason(row.logged_out_at, row.last_seen_at) }}</td>
      <td>{{ row.started_by }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
No activity recorded.
{% endif %}
{%- endmacro %}


{% macro simultaneous_table(url, contest, rows) -%}
{#
Show pairs of intervals active at the same time, as returned by
find_simultaneous_activity.
#}
{% if rows %}
<table class="bordered">
  <thead>
    <tr>
      <th>Username</th>
      <th>First device / IP</th>
      <th>Second device / IP</th>
      <th>Overlap from</th>
      <th>Overlap to</th>
    </tr>
  </thead>
  <tbody>
    {% for row in rows %}
    <tr>
      <td><a href="{{ url("contest", contest.id, "user", row.user_id, "edit") }}">{{ row.username }}</a></td>
      <td>{{ device(row.first_device_id) }} / {{ row.first_ip }}</td>
      <td>{{ device(row.second_device_id) }} / {{ row.second_ip }}</td>
      <td>{{ row.overlap_start|format_datetime }}</td>
      <td>{{ row.overlap_end|format_datetime }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
None.
{% endif %}
{%- endmacro %}
```

Create `cms/server/admin/templates/contest_activity.html`:

```jinja
{% extends "base.html" %}
{% import 'macro/pages.html' as macro_pages %}
{% import 'macro/activity.html' as macro_activity with context %}

{% block core %}
<div class="core_title">
  <h1>Activity</h1>
</div>
<p>Times are in the contest's timezone.</p>

<h2 id="title_simultaneous_activity" class="toggling_on">Simultaneous activity ({{ activity_simultaneous|length }})</h2>
<div id="simultaneous_activity">
  <p>The same participation active from two devices (or, without a device, from two IP addresses) at the same time for more than {{ simultaneous_margin.total_seconds()|int }} seconds. A dynamic IP change on one device is not reported.</p>
  {{ macro_activity.simultaneous_table(url, contest, activity_simultaneous) }}
  <div class="hr"></div>
</div>

<h2 id="title_multiple_devices" class="toggling_on">More than one device ({{ multiple_devices|length }})</h2>
<div id="multiple_devices">
  <p>Informational only: another browser, a private window or cleared cookies also count as a new device.</p>
  {% if multiple_devices %}
  <table class="bordered">
    <thead><tr><th>Username</th><th>Devices</th></tr></thead>
    <tbody>
      {% for row in multiple_devices %}
      <tr>
        <td><a href="{{ url("contest", contest.id, "user", row.user_id, "edit") }}">{{ row.username }}</a></td>
        <td>{{ row.devices }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  None.
  {% endif %}
  <div class="hr"></div>
</div>

<h2 id="title_activity_intervals" class="toggling_on">Intervals ({{ activity_count }})</h2>
<div id="activity_intervals">
  <form action="{{ url("contest", contest.id, "activity") }}" method="GET">
    Username: <input type="text" name="username" value="{{ activity_filters.username }}">
    IP address or network: <input type="text" name="ip" value="{{ activity_filters.ip }}" placeholder="10.0.0.0/24">
    <input type="submit" value="Filter">
    <a href="{{ url("contest", contest.id, "activity") }}">Clear</a>
  </form>
  <p>Download as <a href="{{ activity_csv_url }}">csv</a> (with the filters above; times in UTC).</p>
  {{ macro_activity.intervals_table(url, contest, activity_intervals, activity_end_reason, activity_flagged_ids, true) }}
  {{ macro_pages.selector(activity_page_url, activity_page, activity_pages) }}
</div>
{% endblock core %}
```

In `cms/server/admin/templates/base.html`, after the contest menu's `Ranking` entry (line 240):
```jinja
            <li class="menu_entry"><a class="menu_link" href="{{ url("contest", contest.id, "activity") }}">Activity</a></li>
```

In `cms/server/admin/handlers/__init__.py`, after the `from .contestranking import \ RankingHandler` import:
```python
from .contestactivity import \
    ContestActivityHandler
```
and after the two `RankingHandler` routes:
```python

    # Contest's participant activity

    (r"/contest/([0-9]+)/activity", ContestActivityHandler),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/server/admin/contestactivity_test.py cmstestsuite/unit_tests/locale`
Expected: all pass. If the page test fails on a base.html variable the mocks lack (e.g. an `admin` attribute), extend `make_handler` with it rather than changing the templates.

- [ ] **Step 7: Lint and commit**

Run: `bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pyflakes cms/server/admin/handlers/contestactivity.py cms/server/admin/handlers/__init__.py cmscommon/datetime.py cmstestsuite/unit_tests/server/admin/contestactivity_test.py`
Expected: no output.

```bash
git add cms/server/admin/handlers/contestactivity.py cms/server/admin/handlers/__init__.py cms/server/admin/templates/contest_activity.html cms/server/admin/templates/macro/activity.html cms/server/admin/templates/base.html cmscommon/datetime.py cmstestsuite/unit_tests/server/admin/contestactivity_test.py
git commit -m "feat(admin): show the participants' activity with its alerts" -m "Add a contest Activity page: simultaneous activity from two devices, participations seen on more than one device, and the intervals filtered by user and IP address or network." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Activity section on the participation page

**Files:**
- Modify: `cms/server/admin/handlers/contestuser.py:207-233` (`ParticipationHandler._get_sync`, imports)
- Modify: `cms/server/admin/templates/participation.html` (imports at the top; new section after "Participation information", before "Questions")
- Test: `cmstestsuite/unit_tests/server/admin/contestactivity_test.py` (new class)

**Interfaces:**
- Consumes: `select_intervals`, `activity_render_params` (Task 5); macros `macro/activity.html` (Task 5).
- Produces: `participation.html` render params `activity_intervals`, `activity_simultaneous`, `activity_flagged_ids`, `activity_end_reason`, `timezone`.

- [ ] **Step 1: Write the failing test**

Append to `cmstestsuite/unit_tests/server/admin/contestactivity_test.py`, before any `if __name__` block (add `from cms.server.admin.handlers.contestuser import ParticipationHandler` to the imports):

```python
class TestParticipationPage(PageTestBase):
    """The participation page shows its own intervals and alerts."""

    def test_shows_only_this_participation(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, PHONE, "189.1.1.1", 30, 90)
        other = self.add_participation(contest=self.contest)
        self.session.flush()
        self.add_interval(other, LAPTOP, "172.16.0.9", 0, 60)
        self.session.commit()
        handler = self.make_handler(ParticipationHandler)

        handler._get_sync(str(self.contest.id),
                          str(self.participation.user_id))

        page = "".join(handler.chunks)
        self.assertIn("189.1.1.1", page)
        self.assertNotIn("172.16.0.9", page)
        self.assertEqual(len(handler.r_params["activity_intervals"]), 2)
        self.assertEqual(len(handler.r_params["activity_flagged_ids"]), 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/server/admin/contestactivity_test.py -k TestParticipationPage`
Expected: FAIL with `KeyError: 'activity_intervals'` (or a missing-mock error from `participation.html`; add the missing attribute to `make_handler` and rerun until the failure is the `KeyError`).

- [ ] **Step 3: Implement**

In `cms/server/admin/handlers/contestuser.py` add to the imports:
```python
from cms.db import ActivityInterval
from .contestactivity import activity_render_params, select_intervals
```
(merge `ActivityInterval` into the existing `from cms.db import ...` line if there is one). In `ParticipationHandler._get_sync`, after `self.r_params["teams"] = ...` and before `self.render(...)`:
```python
        self.r_params.update(activity_render_params(
            self.sql_session, self.contest, participation.id))
        self.r_params["activity_intervals"] = self.sql_session.execute(
            select_intervals(self.contest.id)
            .where(ActivityInterval.participation_id == participation.id)
            .order_by(ActivityInterval.started_at.desc(),
                      ActivityInterval.id.desc())).all()
```
In `cms/server/admin/templates/participation.html` add after the existing `{% import 'macro/markdown_input.html' as macro_markdown %}`:
```jinja
{% import 'macro/activity.html' as macro_activity with context %}
```
and, between the closing `</div>` of `participation_info` and `<h2 id="title_questions"`:
```jinja
<h2 id="title_activity" class="toggling_on">Activity</h2>
<div id="activity">
  <p>Times are in the contest's timezone. The whole contest's log is under <a href="{{ url("contest", contest.id, "activity") }}">Activity</a>.</p>
  {% if activity_simultaneous %}
  <p><strong>Simultaneous activity</strong> (its intervals are highlighted below):</p>
  {{ macro_activity.simultaneous_table(url, contest, activity_simultaneous) }}
  {% endif %}
  {{ macro_activity.intervals_table(url, contest, activity_intervals, activity_end_reason, activity_flagged_ids, false) }}
  <div class="hr"></div>
</div>


```

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/server/admin`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

Run: `bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pyflakes cms/server/admin/handlers/contestuser.py cmstestsuite/unit_tests/server/admin/contestactivity_test.py`
Expected: no output.

```bash
git add cms/server/admin/handlers/contestuser.py cms/server/admin/templates/participation.html cmstestsuite/unit_tests/server/admin/contestactivity_test.py
git commit -m "feat(admin): show a participation's activity on its page" -m "List all its intervals and highlight those in simultaneous activity, for disputes." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Streamed CSV export

**Files:**
- Modify: `cms/server/admin/handlers/contestactivity.py` (new functions and handler)
- Modify: `cms/server/admin/handlers/__init__.py` (import and route)
- Test: `cmstestsuite/unit_tests/server/admin/contestactivity_csv_test.py`

**Interfaces:**
- Consumes: `select_intervals`, `parse_activity_filters`, `inactivity_threshold` (Task 5); `activity_end_reason`, `ACTIVITY_END_*` (Task 1). The page already links to `url("contest", id, "activity", "csv", **filters)` (Task 5).
- Produces: `CSV_BATCH_SIZE = 1000`, `CSV_HEADER`, `iso_utc(dt) -> str`, `csv_row(row, now) -> list[str]`, `format_csv_rows(rows) -> str`, `ContestActivityCsvHandler` at `/contest/<id>/activity/csv`.
- Why not `yield_per`: in AWS a `write()` from an executor thread only buffers (see `BaseHandler.finish`), so true streaming alternates an executor fetch of one keyset batch with `write()` + `await flush()` on the loop, releasing the DB connection between batches.

- [ ] **Step 1: Write the failing tests**

Create `cmstestsuite/unit_tests/server/admin/contestactivity_csv_test.py` (AGPL header with the 2026 copyright line, then):

```python
"""Tests for the CSV export of the participants' activity.

"""

import csv
import io
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import tornado.web

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import ActivityInterval
from cms.server.admin.handlers import contestactivity
from cms.server.admin.handlers.contestactivity import \
    CSV_HEADER, ContestActivityCsvHandler, iso_utc


LAPTOP = uuid.UUID("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 10, 12, 12, 0, 0)


class TestIsoUtc(unittest.TestCase):

    def test_explicit_offset(self):
        self.assertEqual(iso_utc(datetime(2026, 10, 12, 10, 0, 0)),
                         "2026-10-12T10:00:00+00:00")
        self.assertEqual(iso_utc(None), "")


class TestActivityCsv(DatabaseMixin, unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.session.flush()
        self.username = self.participation.user.username

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def add_interval(self, ip, start, end, logged_out=False, device=LAPTOP):
        interval = ActivityInterval(
            device_id=device, ip=ip,
            started_at=NOW - timedelta(minutes=start),
            last_seen_at=NOW - timedelta(minutes=end),
            logged_out_at=NOW - timedelta(minutes=end) if logged_out else None,
            started_by="login")
        interval.participation_id = self.participation.id
        self.session.add(interval)

    async def download(self, **arguments) -> list[list[str]]:
        self.session.commit()
        handler = ContestActivityCsvHandler.__new__(ContestActivityCsvHandler)
        handler.sql_session = self.session
        handler.get_query_argument = \
            lambda name, default=None: arguments.get(name, default)
        handler.set_header = MagicMock()
        chunks = []
        handler.write = chunks.append
        handler.flush = AsyncMock()
        with patch.object(contestactivity, "make_datetime",
                          return_value=NOW):
            await handler._get_csv(str(self.contest.id))
        return list(csv.reader(io.StringIO("".join(chunks))))

    async def test_columns_and_end_reasons(self):
        self.add_interval("10.0.0.5", 120, 100, logged_out=True)
        self.add_interval("10.0.0.6", 90, 60)            # inactivity
        self.add_interval("2001:db8::1", 10, 1, device=None)  # active

        header, logout, inactivity, active = await self.download()

        self.assertEqual(header, CSV_HEADER)
        self.assertEqual(logout[0], self.username)
        self.assertEqual(logout[3], str(LAPTOP))
        self.assertEqual(logout[4], "10.0.0.5")
        self.assertEqual(logout[5], "2026-10-12T10:00:00+00:00")
        self.assertEqual(logout[7], "2026-10-12T10:20:00+00:00")
        self.assertEqual(logout[8:], ["logout", "login"])
        self.assertEqual(inactivity[7], "2026-10-12T11:00:00+00:00")
        self.assertEqual(inactivity[8], "inactivity")
        self.assertEqual(active[3], "")
        self.assertEqual(active[4], "2001:db8::1")
        self.assertEqual(active[7:9], ["", "active"])

    async def test_filters_and_batches(self):
        for n in range(5):
            self.add_interval("10.0.0.%d" % (n + 1), 50, 40)
        self.add_interval("172.16.0.1", 50, 40)

        with patch.object(contestactivity, "CSV_BATCH_SIZE", 2):
            rows = await self.download(ip="10.0.0.0/24")

        self.assertEqual(len(rows), 1 + 5)
        self.assertNotIn("172.16.0.1", [row[4] for row in rows])

    async def test_empty_log_is_only_the_header(self):
        self.assertEqual(await self.download(), [CSV_HEADER])

    async def test_invalid_filter_is_a_400(self):
        with self.assertRaises(tornado.web.HTTPError) as error:
            await self.download(ip="abc")
        self.assertEqual(error.exception.status_code, 400)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/server/admin/contestactivity_csv_test.py`
Expected: collection ERROR, `ImportError: cannot import name 'CSV_HEADER'`.

- [ ] **Step 3: Implement**

In `cms/server/admin/handlers/contestactivity.py`: add `import csv` and `import io` to the stdlib imports, change `from datetime import timedelta` to `from datetime import datetime, timedelta, timezone`, and import the constants:
```python
from cms.db.activity import ACTIVITY_END_INACTIVITY, ACTIVITY_END_LOGOUT, \
    activity_end_reason
```
Add after `ACTIVITY_PAGE_SIZE = 100`:
```python
CSV_BATCH_SIZE = 1000
CSV_HEADER = ["username", "first_name", "last_name", "device_id", "ip",
              "started_at", "last_seen_at", "ended_at", "end_reason",
              "started_by"]
```
Add after `activity_render_params`:
```python
def iso_utc(dt: datetime | None) -> str:
    """Return a stored (naive UTC) time in ISO 8601 with its offset.

    dt: the time, or None.

    return: the formatted time, or an empty string for None.

    """
    return "" if dt is None else dt.replace(tzinfo=timezone.utc).isoformat()


def csv_row(row: Row, now: datetime) -> list[str]:
    """Return the CSV fields of an interval from select_intervals.

    row: the interval.
    now: the current time, to tell active intervals from ended ones.

    return: the fields, in the order of CSV_HEADER.

    """
    reason = activity_end_reason(
        row.logged_out_at, row.last_seen_at, now, inactivity_threshold())
    ended_at = {ACTIVITY_END_LOGOUT: row.logged_out_at,
                ACTIVITY_END_INACTIVITY: row.last_seen_at}.get(reason)
    return [row.username, row.first_name, row.last_name,
            "" if row.device_id is None else str(row.device_id),
            row.ip, iso_utc(row.started_at), iso_utc(row.last_seen_at),
            iso_utc(ended_at), reason, row.started_by]


def format_csv_rows(rows) -> str:
    """Return rows of fields as CSV text.

    rows (iterable of list of str): the rows.

    return: the CSV text.

    """
    output = io.StringIO()
    csv.writer(output).writerows(rows)
    return output.getvalue()
```
Add at the end of the module:
```python
class ContestActivityCsvHandler(BaseHandler):
    """Downloads the participants' activity in a contest as CSV.

    The rows are streamed in batches of CSV_BATCH_SIZE, read by
    increasing id, so that the whole log never sits in memory and the
    database connection is given back between batches.

    """
    def _read_filters_sync(
        self, contest_id: str
    ) -> tuple[str, IPNetwork | None]:
        try:
            self.safe_get_item(Contest, contest_id)
            return parse_activity_filters(
                self.get_query_argument("username", ""),
                self.get_query_argument("ip", ""))
        finally:
            self.sql_session.rollback()

    def _fetch_batch_sync(
        self,
        contest_id: str,
        username: str,
        network: IPNetwork | None,
        after_id: int,
    ) -> list[Row]:
        try:
            return self.sql_session.execute(
                select_intervals(int(contest_id), username, network)
                .where(ActivityInterval.id > after_id)
                .order_by(ActivityInterval.id)
                .limit(CSV_BATCH_SIZE)).all()
        finally:
            # Give the connection back while the batch is sent.
            self.sql_session.rollback()

    async def _get_csv(self, contest_id: str):
        loop = asyncio.get_running_loop()
        username, network = await loop.run_in_executor(
            None, self._read_filters_sync, contest_id)
        now = make_datetime()
        self.set_header("Content-Type", "text/csv")
        self.set_header("Content-Disposition",
                        "attachment; filename=\"activity.csv\"")
        self.write(format_csv_rows([CSV_HEADER]))
        after_id = 0
        while True:
            rows = await loop.run_in_executor(
                None, self._fetch_batch_sync, contest_id, username,
                network, after_id)
            if not rows:
                break
            self.write(format_csv_rows(csv_row(row, now) for row in rows))
            await self.flush()
            after_id = rows[-1].id

    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self, contest_id: str):
        await self._get_csv(contest_id)
```
(The tests call `_get_csv` directly to skip the permission decorator, which needs a real request.)

In `cms/server/admin/handlers/__init__.py` extend the import to
```python
from .contestactivity import \
    ContestActivityHandler, \
    ContestActivityCsvHandler
```
and add after the activity route:
```python
    (r"/contest/([0-9]+)/activity/csv", ContestActivityCsvHandler),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout --foreground --signal=ABRT 600 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/server/admin`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

Run: `bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pyflakes cms/server/admin/handlers/contestactivity.py cms/server/admin/handlers/__init__.py cmstestsuite/unit_tests/server/admin/contestactivity_csv_test.py`
Expected: no output.

```bash
git add cms/server/admin/handlers/contestactivity.py cms/server/admin/handlers/__init__.py cmstestsuite/unit_tests/server/admin/contestactivity_csv_test.py
git commit -m "feat(admin): export the participants' activity as CSV" -m "Stream the filtered intervals in keyset batches with ISO 8601 UTC times, the end reason and the end time." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Final verification (after Task 7)

- [ ] Run the whole suite in its two groups (the asyncio group must include every file this plan added):

```bash
GEVENT_SERVICE_FILES="cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py cmstestsuite/unit_tests/service/twophase_reenqueue_test.py"
timeout --foreground --signal=ABRT 1800 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests/cmscontrib cmstestsuite/unit_tests/cmsranking cmstestsuite/unit_tests/db/rankinggroup_test.py $GEVENT_SERVICE_FILES
IGNORE_ARGS="--ignore=cmstestsuite/unit_tests/cmscontrib --ignore=cmstestsuite/unit_tests/cmsranking --ignore=cmstestsuite/unit_tests/db/rankinggroup_test.py"
for f in $GEVENT_SERVICE_FILES; do IGNORE_ARGS="$IGNORE_ARGS --ignore=$f"; done
timeout --foreground --signal=ABRT 1800 bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pytest -p no:cacheprovider -q cmstestsuite/unit_tests $IGNORE_ARGS
```
Expected: no new failures compared to `main` (check `docker/_cms-test-internal.sh` for the current gevent file list first).

- [ ] Run `bash /home/areslolxd/.claude/jobs/0348eeb8/tmp/py.sh pyflakes cms cmscommon cmscontrib cmsranking cmstaskenv cmstestsuite` — expected: no new warnings.
- [ ] Push the branch: `git push origin feat/participant-activity-log`.
