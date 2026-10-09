# Database Transport Timeouts (#39, stage 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every CMS database connection fails within bounded time when
PostgreSQL stops answering, every service heals by itself once it answers
again, and the fork's schema updaters never queue CMS behind their locks
for more than 5 s.

**Architecture:**
- A fork-owned module, `cms/db/connect_options.py`, merges libpq keywords
  under the URL query of the three connection paths (sync engine, async
  engine, `custom_psycopg2_connection`) and owns the disconnect rule.
- The vendored gevent wait callback enforces `connect_timeout` during a
  green connect.
- Three paths that a disconnect exposes are fixed: the session exit (8c),
  the ES write path (8b) and the ES phase-2 window (8a, healed by the plain
  sweeper).
- The fork DDL updaters bound their lock waits with `SET LOCAL`, and
  `cmsSetupDB` turns a timeout into one message and exit status 1.
- The runbook explains it all, and a fault mode of the load test validates
  it.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, psycopg2 (green, through
gevent) and psycopg 3 (async), PostgreSQL 16 locally and 15/17 in CI and
the load test, `unittest` with pytest, Sphinx + MyST + sphinx-intl, bash,
GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-08-db-transport-timeouts-design.md`
(the binding authority; read it before any task).

## Global Constraints

### Where and how to work

- **Worktree.** Work only in
  `/var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts` (branch
  `feat/db-transport-timeouts`). Do not push, do not merge, do not use
  `git stash`. Only the controller pushes, in the steps marked
  **Controller** (Tasks 8 and 10).
- **Python environment.** Create the venv once, if `.venv` is missing:
  `uv venv -q --python 3.12 .venv && uv pip install -q --python .venv/bin/python -c constraints.txt -e ".[devel]"`.
- **Test command.** Every test run is
  `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider <files>`.
  It uses the podman PostgreSQL `cms-test-pg` on localhost:55432 (TCP).
  Run one pytest process at a time. Never run a bare `pytest` over the
  whole suite.
- **Test groups** (`docker/_cms-test-internal.sh`, the repo `CLAUDE.md`):
  - gevent group: `cmstestsuite/unit_tests/cmscontrib/`,
    `cmstestsuite/unit_tests/cmsranking/`,
    `cmstestsuite/unit_tests/db/rankinggroup_test.py`,
    `cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py`
    and `cmstestsuite/unit_tests/service/twophase_reenqueue_test.py`. Run
    these in a pytest process of their own, never together with an
    asyncio-group file.
  - asyncio group: everything else, including every new test file of this
    plan except the `SetupDBTest.py` additions. A new asyncio-group file
    must not import `cmscontrib` (directly or through another module).
  - A test that needs a TCP database (`getsockopt`) skips when `CMS_CONFIG`
    points to a Unix socket.
- **Python style.** English, PEP 8 (79 columns), PEP 484 hints on every new
  or changed signature. Docstrings use the format of
  `.github/CONTRIBUTING.md`: an imperative first line, a blank line, then
  `name: description`, `return: description` and
  `raise (Exception): when` sections, each separated by a blank line, and a
  blank line before the closing quotes.
- **Lint.** `uvx -q pyflakes <touched .py files>` prints nothing.
  `uvx -q pycodestyle --max-line-length=79 <touched .py files>` reports no
  warning on a line the task adds or changes (`cms/db/__init__.py` has
  E402 lines and `cms/service/EvaluationService.py` 19 warnings already).
  `shellcheck cmstestsuite/loadtest/run.sh` prints nothing.
- **New files** start with the fork's header, copied from
  `cmscontrib/updaters/fork_multi_contest.py`:

  ```python
  #!/usr/bin/env python3

  # Contest Management System - http://cms-dev.github.io/
  # Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
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
  ```

  In the code blocks of this plan, `# (fork header)` stands for these 17
  lines, verbatim.
- **Commits.** Conventional Commits. The message is the subject, a blank
  line, a short body, a blank line, `Refs #39`, a blank line, then
  `Co-Authored-By: <your model name> <noreply@anthropic.com>` (for
  example `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; name
  the model that writes the commit).
- **Repository hygiene.** No host names, server paths, IP addresses,
  `.env` contents or credentials in the repository. Production details
  stay generic: "the host PostgreSQL", "the cms container".
- **GitHub.** Always `gh -R AresLOLXD/cms` (the default resolves to
  upstream).

### Exact values (verbatim from the spec)

| Item | Value |
|---|---|
| `connect_timeout` | `10` (seconds) |
| `keepalives` | `1` |
| `keepalives_idle` | `30` (seconds) |
| `keepalives_interval` | `10` (seconds) |
| `keepalives_count` | `3` |
| `tcp_user_timeout` | `60000` (**milliseconds**) |
| `fallback_application_name` | `PROGRAM_NAME`: the basename of `sys.argv[0]`, plus a space and `sys.argv[1]` when that argument is a decimal integer (`cmsContestWebServer 2`, `cmsWorker 5`); not cut |
| Merge rule | `{**defaults, **url.query}` on URL objects: the user wins key by key; never `str(url)`; never `connect_args`; never an `options` keyword |
| Kill-switch suffix | `?connect_timeout=0&keepalives_idle=7200&keepalives_interval=75&keepalives_count=9&tcp_user_timeout=0` |
| Green connect deadline | the connection's own `connect_timeout` (`extensions.parse_dsn(conn.dsn)`), read once before the first `poll()` and only when `conn.status == extensions.STATUS_SETUP`; 0 or absent = no deadline; a positive value under 2 s = 2 s; raises `psycopg2.OperationalError("timeout expired")`; never on queries |
| DDL bound | `cursor.execute("SET LOCAL lock_timeout = '5s'")` in each updater's own transaction, not in the `FORK_*_SQL` constants; no retry loop |
| Disconnect rule | `isinstance(error, DBAPIError) and (error.connection_invalidated or (sqlstate(error.orig) or "").startswith("08"))`, `sqlstate` reading `orig.pgcode` (psycopg2) or `orig.sqlstate` (psycopg 3) |

### Exact messages

- `cmsSetupDB` (stderr, exit status 1, no traceback), `<tables>` being
  `contests or ranking_groups` (multi-contest updater) or
  `activity_intervals` (activity-intervals updater):
  `cmsSetupDB could not lock <tables> within 5 s: another session is using them. Nothing was changed. Find that session with the holder query in the contest-day runbook ("When the database misbehaves"), wait for it to finish or end it, then run the setup again.`
- ES, one ERROR: `Lost the database connection while writing worker results (<error>); the sweeper will redo them within about 2 minutes.`
- Session exit, WARNING: `Lost the database connection before the session's rollback: <error>`
- ES plain sweep, WARNING: `Finalized result <submission>(<dataset>): all its evaluations were stored but its outcome was not (a write was cut).`
- `<error>` is `describe_error(error)` (Task 1): the driver error's message
  with its lines joined by spaces, so that the log line stays one line and
  holds the texts the runbook greps for (`server closed the connection unexpectedly`,
  `could not receive data from server`, `Connection timed out`,
  `timeout expired`).

### Exact bounds of the validation runs

| Run | Bound |
|---|---|
| F1 | `pg_terminate_backend` of every CMS backend (`application_name LIKE 'cms%'`), twice |
| F2 | two-way DROP for 90 s; partition step 3 minutes after the drop ends; each database call fails within 75 s of its own start; then pages within 1 minute, ES commits within about 2 minutes, scores within about 6 minutes |
| F3 | `docker pause` of PostgreSQL for 30 s; new connections fail with `timeout expired` at about 10 s |
| F4 | `cmsSetupDB` exits 1 within 15 s with the message; CWS p99 ≤ 6 s from the start of `cmsSetupDB` to 5 s after it exits |
| No-fault | zero `timeout expired`, `Connection timed out`, `closed the connection` in the CMS logs; every submission final; scored p95, CWS p99, flush p95 and drain time within the run-to-run spread of a beta baseline of the same batch |

The plan turns "about 2 minutes" into 150 s and "about 6 minutes" into
420 s (the sweeper periods, 117 s and 347 s, plus the work), and "about
10 s" into at most 12 s (`faultstats.py`, Task 9).

## Review Focus

The five inputs or conditions the spec implies but does not test, the most
likely to bite first, each pinned by a test in the task that owns the code:

1. **A `CMS_DB_URL` that already has a query** (`?sslmode=require`,
   `?host=/var/run/postgresql`, a multi-host `?host=a&host=b`). Its keys must
   pass through untouched, and the runbook's kill switch, joined with `&`,
   must still win. Tests: `test_the_rest_of_the_query_passes_through` and
   `test_the_kill_switch_merges_into_a_url_with_a_query` (Task 1).
2. **A process with an empty or odd command line** (`sys.argv == []` or
   `[""]` in an embedded interpreter, `python3 -c`). `PROGRAM_NAME` must be
   `""` or `-c`, and a connection with an empty fallback name must still
   work. Tests: `test_an_empty_command_line_gives_an_empty_name` and
   `test_an_empty_program_name_still_connects` (Task 1).
3. **An `asyncio.CancelledError` that lands in `AsyncSessionGen`'s rollback
   during a shutdown.** It must still propagate (it is not an `Exception`),
   and the session must still be closed. Test:
   `test_a_cancelled_rollback_still_propagates_and_closes` (Task 3).
4. **The fault injector stopped in the middle of a fault** (step timeout,
   Ctrl-C, `run.sh`'s EXIT trap). It must remove both DROP rules and unpause
   PostgreSQL, or the collection, the teardown and the next run hang. Tests:
   `test_cleanup_after_an_interrupted_drop_removes_both_rules` and
   `test_cleanup_after_an_interrupted_pause_unpauses_postgresql` (Task 9).
5. **A fault run cut short**, so that `faults.json` lacks a fault or the
   export is missing. `faultstats.py` must report the gap as a FAIL row and
   still print the table, not crash. Tests:
   `test_a_fault_that_did_not_run_fails_its_row` and
   `test_a_missing_export_fails_instead_of_crashing` (Task 9).

## File Structure

| File | New? | Responsibility | Task |
|---|---|---|---|
| `cms/db/connect_options.py` | new | `LIBPQ_DEFAULTS`, `program_name`, `PROGRAM_NAME`, `with_connection_defaults`, `sqlstate`, `is_disconnect`, `describe_error`; later `log_rollback_error` | 1, 3 |
| `cms/db/__init__.py` | | sync engine through `with_connection_defaults` | 1 |
| `cms/db/async_session.py` | | async engine through `with_connection_defaults`; `AsyncSessionGen.__aexit__` | 1, 3 |
| `cms/db/session.py` | | `custom_psycopg2_connection` through `with_connection_defaults`; `SessionGen.__exit__` | 1, 3 |
| `config/cms.sample.toml` | | comment on the defaults next to `url` | 1 |
| `cms/io/PsycoGevent.py` | | `connect_deadline`, deadline in `gevent_wait_callback` | 2 |
| `cms/service/EvaluationService.py` | | 8b (`_lost_connection`, `_write_results_sync` wrapper, `_write_results_in_session`, per-row re-raise); 8a (`_finalize_complete_result`, plain sweep) | 4, 5 |
| `cms/service/esoperations.py` | | `get_submission_results_to_finalize` | 5 |
| `cmscontrib/updaters/fork_ddl.py` | new | `SchemaLockTimeout`, `apply_fork_sql` | 6 |
| `cmscontrib/updaters/fork_multi_contest.py`, `fork_activity_intervals.py` | | apply through `apply_fork_sql` | 6 |
| `cmscontrib/SetupDB.py` | | turn `SchemaLockTimeout` into the message and `False` (exit 1) | 6 |
| `docs/contest-day.md`, `docs/locale/es/LC_MESSAGES/contest-day.po` | | runbook | 7, 10 |
| `.github/workflows/loadtest.yml` | | branch gating, `runner-check` job, `faults` job | 8, 9 |
| `cmstestsuite/loadtest/faults.py` | new | the injector | 9 |
| `cmstestsuite/loadtest/faultstats.py` | new | the pass criteria of a fault run | 9 |
| `cmstestsuite/loadtest/run.sh`, `README.md` | | `--faults` | 9 |
| `cmstestsuite/unit_tests/db/connect_options_test.py` | new | Task 1 tests | 1 |
| `cmstestsuite/unit_tests/db/session_test.py` | | expectations gain the defaults | 1 |
| `cmstestsuite/unit_tests/io/psycogevent_test.py` | new | Task 2 tests | 2 |
| `cmstestsuite/unit_tests/dbfault.py` | new | `terminate_backends`, a test helper | 3 |
| `cmstestsuite/unit_tests/db/session_exit_test.py` | new | Task 3 tests | 3 |
| `cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py` | new | Task 4 and 5 tests | 4, 5 |
| `cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py` | | Task 6 tests (gevent group) | 6 |
| `cmstestsuite/unit_tests/loadtest/faults_test.py`, `faultstats_test.py` | new | Task 9 tests | 9 |

### Changes to the suggested decomposition, and why

- **`describe_error` joins Task 1.** The spec writes `<error>` in three
  messages. SQLAlchemy's `str()` of an error adds `[SQL: …]`, its parameters
  and a link on extra lines, which would split one ERROR across lines in
  `./logs.sh` and fill the Overview **Logs** cell. One helper renders the
  driver's message on one line, next to the disconnect rule it serves.
- **The kill-switch "green connect gets no deadline" check is in Task 2.**
  It needs the deadline of Task 2. Task 1 checks the kill switch on the
  three paths' sockets.
- **The rollback rule of 8c is one helper, `log_rollback_error`, in
  `connect_options`.** `SessionGen` and `AsyncSessionGen` must follow the
  same rule; one function keeps them from drifting.
- **8b moves the `with SessionGen()` block into `_write_results_in_session`
  without re-indenting it.** `_write_results_sync` keeps its name, its
  "Starting commit process..." and its "Done" lines, which
  `cmstestsuite/loadtest/flushstats.py` matches by function name
  (`write_results(_sync)?\] Done`).
- **`cmsSetupDB`'s catch is in `setup_db()`**, not in `scripts/cmsSetupDB`:
  `setup_db()` already returns `False` for a failed step, and the script
  already exits 1 on `False`. The observable behaviour is the spec's (exit
  1, the message, no traceback), the script stays unchanged, and the catch
  can be tested in-process too. The two updaters share `apply_fork_sql`
  (new fork-owned `cmscontrib/updaters/fork_ddl.py`), which runs the
  `SET LOCAL`.
- **The activity-intervals DDL test holds a ROW EXCLUSIVE lock, not a
  read.** Measured on PostgreSQL 16: `CREATE INDEX IF NOT EXISTS` takes
  SHARE, which an ACCESS SHARE reader does not block, while a writer's
  ROW EXCLUSIVE (the activity recorder's inserts) does. The multi-contest
  test keeps the spec's reader of `contests`.
- **The runner-capability check is its own task (8), with a controller
  gate.** The spec runs it "before the fault mode exists"; Task 9 starts
  only if it passes.
- **Every quoted log string in the runbook is a code span.**
  `docs/check_translations.py` checks code spans only, not double-quoted
  text, so code spans are what keeps them verbatim in the `.po`.
- **The DROP rules add `! -i lo` / `! -o lo`.** They are what makes the
  spec's "leave loopback traffic inside the netns alone" true.
- **The fault run injects F1 to F4 in one run per profile**, on a fixed
  schedule (`faults.py`) with a 2100 s contest, so that each fault's
  recovery window ends before the next fault and before the end burst.

---

### Task 1: Connection defaults for every connection path

**Files:**
- Create: `cms/db/connect_options.py`
- Modify: `cms/db/__init__.py:32-37` (imports) and `:90-91` (`engine`)
- Modify: `cms/db/async_session.py:41-45` (imports) and `:85-89` (`async_engine`)
- Modify: `cms/db/session.py:29-38` (imports) and `:76-94` (`custom_psycopg2_connection`)
- Modify: `config/cms.sample.toml:70-72` (comment above `url`)
- Create: `cmstestsuite/unit_tests/db/connect_options_test.py` (asyncio group)
- Modify: `cmstestsuite/unit_tests/db/session_test.py` (asyncio group)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces (in `cms/db/connect_options.py`):
  - `LIBPQ_DEFAULTS: dict[str, str]`;
  - `program_name(argv: Sequence[str]) -> str` and
    `PROGRAM_NAME: str = program_name(sys.argv)`;
  - `with_connection_defaults(url: URL) -> URL`;
  - `sqlstate(error: BaseException | None) -> str | None`;
  - `is_disconnect(error: BaseException) -> bool`;
  - `describe_error(error: BaseException) -> str`.
- Produces (in `cmstestsuite/unit_tests/db/connect_options_test.py`):
  `KILL_SWITCH: str`, `with_kill_switch(url: str) -> str`, which Task 2's
  tests import.

- [ ] **Step 1: Create the venv and check the starting point.**
  - Run: `test -x .venv/bin/python || (uv venv -q --python 3.12 .venv && uv pip install -q --python .venv/bin/python -c constraints.txt -e ".[devel]")`
  - Run: `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/db/session_test.py cmstestsuite/unit_tests/db/async_session_test.py -q`
  - Expected: all pass.

- [ ] **Step 2: Write the failing tests.** Create
  `cmstestsuite/unit_tests/db/connect_options_test.py`:

```python
# (fork header)

"""Tests for cms.db.connect_options, the connection defaults (#39)."""

import asyncio
import os
import socket
import sys
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import IntegrityError, InternalError, \
    OperationalError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from cms.conf import config
from cms.db import custom_psycopg2_connection, engine
from cms.db.async_session import _async_database_url, async_engine
from cms.db.connect_options import LIBPQ_DEFAULTS, PROGRAM_NAME, \
    describe_error, is_disconnect, program_name, sqlstate, \
    with_connection_defaults


# The suffix of the runbook (docs/contest-day.md, "Switching the
# timeouts off"). Keep the two identical.
KILL_SWITCH = ("?connect_timeout=0&keepalives_idle=7200"
               "&keepalives_interval=75&keepalives_count=9"
               "&tcp_user_timeout=0")

# SO_KEEPALIVE, TCP_KEEPIDLE, TCP_KEEPINTVL, TCP_KEEPCNT and
# TCP_USER_TIMEOUT of a connection's socket.
DEFAULT_SOCKET = (1, 30, 10, 3, 60000)
KILL_SWITCH_SOCKET = (1, 7200, 75, 9, 0)

EXPECTED_DEFAULTS = {
    "connect_timeout": "10",
    "keepalives": "1",
    "keepalives_idle": "30",
    "keepalives_interval": "10",
    "keepalives_count": "3",
    "tcp_user_timeout": "60000",
    "fallback_application_name": PROGRAM_NAME,
}


def with_kill_switch(url: str) -> str:
    """Return url with KILL_SWITCH added, as the runbook says to.

    url: a database URL, with or without a query.

    return: the URL with the suffix, joined with "&" when url already
        has a query.

    """
    if "?" in url:
        return url + "&" + KILL_SWITCH[1:]
    return url + KILL_SWITCH


def _database_over_tcp() -> bool:
    """Tell whether CMS_CONFIG reaches the test database over TCP."""
    url = make_url(config.database.url)
    host = url.query.get("host", url.host)
    return isinstance(host, str) and not host.startswith("/")


def _socket_options(fileno: int) -> tuple[int, int, int, int, int]:
    """Return the keepalive and user-timeout options of a socket.

    fileno: the socket's file descriptor; it stays open.

    return: SO_KEEPALIVE, TCP_KEEPIDLE, TCP_KEEPINTVL, TCP_KEEPCNT and
        TCP_USER_TIMEOUT.

    """
    sock = socket.socket(fileno=os.dup(fileno))
    try:
        return (sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE),
                sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE),
                sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL),
                sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT),
                sock.getsockopt(socket.IPPROTO_TCP,
                                socket.TCP_USER_TIMEOUT))
    finally:
        sock.close()


def _custom_connection_state() -> tuple[tuple[int, ...], str]:
    """Return the socket options and the application_name of a new
    custom_psycopg2_connection().

    """
    connection = custom_psycopg2_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SHOW application_name")
        return _socket_options(connection.fileno()), cursor.fetchone()[0]
    finally:
        connection.close()


async def _async_engine_state(
    some_engine: AsyncEngine
) -> tuple[tuple[int, ...], str]:
    """Return the socket options and the application_name of a
    connection of an async engine.

    """
    async with some_engine.connect() as connection:
        raw = await connection.get_raw_connection()
        fileno = raw.dbapi_connection.driver_connection.fileno()
        name = (await connection.execute(
            text("SHOW application_name"))).scalar_one()
        return _socket_options(fileno), name


class _DriverError(Exception):
    """A stand-in for a driver error, with or without a SQLSTATE."""

    def __init__(self, message: str = "", pgcode: str | None = None,
                 sqlstate: str | None = None):
        super().__init__(message)
        if pgcode is not None:
            self.pgcode = pgcode
        if sqlstate is not None:
            self.sqlstate = sqlstate


def _sqlalchemy_error(error_class: type, orig: Exception,
                      invalidated: bool = False) -> Exception:
    """Return a SQLAlchemy DBAPIError of error_class wrapping orig."""
    return error_class("SELECT 1", {}, orig,
                       connection_invalidated=invalidated)


class TestWithConnectionDefaults(unittest.TestCase):

    @staticmethod
    def _query(url: str) -> dict:
        return dict(with_connection_defaults(make_url(url)).query)

    def test_the_defaults_are_added(self):
        self.assertEqual(self._query("postgresql+psycopg2://u:p@h:5432/db"),
                         EXPECTED_DEFAULTS)
        # Milliseconds: "60" would abort connections after 60 ms.
        self.assertEqual(LIBPQ_DEFAULTS["tcp_user_timeout"], "60000")

    def test_a_user_keyword_wins(self):
        query = self._query("postgresql+psycopg2://u@h/db"
                            "?keepalives_idle=5&tcp_user_timeout=0")
        self.assertEqual(query, {**EXPECTED_DEFAULTS,
                                 "keepalives_idle": "5",
                                 "tcp_user_timeout": "0"})

    def test_the_rest_of_the_query_passes_through(self):
        for url, extra in (
                ("postgresql+psycopg2://u@/db?host=/var/run/postgresql",
                 {"host": "/var/run/postgresql"}),
                ("postgresql+psycopg2://u@h/db?sslmode=require",
                 {"sslmode": "require"}),
                ("postgresql+psycopg2://u@/db?host=a&host=b",
                 {"host": ("a", "b")})):
            with self.subTest(url=url):
                self.assertEqual(self._query(url),
                                 {**EXPECTED_DEFAULTS, **extra})

    def test_a_password_with_special_characters_survives(self):
        url = with_connection_defaults(make_url(
            "postgresql+psycopg2://us%40r:p%40ss%3Aw%2Fd%25@h/db"))
        self.assertEqual(url.username, "us@r")
        self.assertEqual(url.password, "p@ss:w/d%")

    def test_options_is_never_added_and_a_users_options_is_kept(self):
        self.assertNotIn("options",
                         self._query("postgresql+psycopg2://u@h/db"))
        query = self._query("postgresql+psycopg2://u@h/db"
                            "?options=-c%20lock_timeout%3D5s")
        self.assertEqual(query["options"], "-c lock_timeout=5s")

    def test_a_users_application_name_is_kept(self):
        # libpq uses fallback_application_name only when no
        # application_name is given.
        query = self._query(
            "postgresql+psycopg2://u@h/db?application_name=mine")
        self.assertEqual(query["application_name"], "mine")

    def test_the_kill_switch_merges_into_a_url_with_a_query(self):
        query = self._query(with_kill_switch(
            "postgresql+psycopg2://u@h/db?sslmode=require"))
        self.assertEqual(query, {
            **EXPECTED_DEFAULTS, "sslmode": "require",
            "connect_timeout": "0", "keepalives_idle": "7200",
            "keepalives_interval": "75", "keepalives_count": "9",
            "tcp_user_timeout": "0"})


class TestProgramName(unittest.TestCase):

    def test_a_shard_number_is_kept(self):
        self.assertEqual(program_name(["/x/cmsContestWebServer", "2"]),
                         "cmsContestWebServer 2")

    def test_other_arguments_are_dropped(self):
        self.assertEqual(program_name(["/x/cmsDumpExporter", "-h"]),
                         "cmsDumpExporter")
        self.assertEqual(program_name(["/x/cmsWorker"]), "cmsWorker")

    def test_an_empty_command_line_gives_an_empty_name(self):
        self.assertEqual(program_name([]), "")
        self.assertEqual(program_name([""]), "")
        self.assertEqual(program_name(["-c"]), "-c")

    def test_the_module_value_names_this_process(self):
        self.assertEqual(PROGRAM_NAME, program_name(sys.argv))


class TestDisconnectRule(unittest.TestCase):

    def test_sqlstate_reads_both_drivers(self):
        self.assertEqual(sqlstate(_DriverError(pgcode="40P01")), "40P01")
        self.assertEqual(sqlstate(_DriverError(sqlstate="25P03")), "25P03")
        self.assertIsNone(sqlstate(_DriverError("Connection refused")))
        self.assertIsNone(sqlstate(None))

    def test_an_invalidated_connection_is_a_disconnect(self):
        error = _sqlalchemy_error(
            OperationalError,
            _DriverError("server closed the connection unexpectedly"),
            invalidated=True)
        self.assertTrue(is_disconnect(error))

    def test_a_connection_exception_sqlstate_is_a_disconnect(self):
        for orig in (_DriverError(pgcode="08006"),
                     _DriverError(sqlstate="08001")):
            with self.subTest(code=sqlstate(orig)):
                self.assertTrue(is_disconnect(
                    _sqlalchemy_error(OperationalError, orig)))

    def test_lock_and_transaction_errors_are_not_disconnects(self):
        for code in ("40P01", "40001", "55P03"):
            with self.subTest(code=code):
                self.assertFalse(is_disconnect(_sqlalchemy_error(
                    OperationalError, _DriverError(pgcode=code))))
        self.assertFalse(is_disconnect(_sqlalchemy_error(
            IntegrityError, _DriverError(pgcode="23505"))))

    def test_errors_that_do_not_come_from_the_driver_are_not(self):
        self.assertFalse(is_disconnect(PendingRollbackError("rolled back")))
        self.assertFalse(is_disconnect(ValueError("bad row")))

    def test_describe_error_gives_the_driver_message_on_one_line(self):
        error = _sqlalchemy_error(
            OperationalError,
            _DriverError("server closed the connection unexpectedly\n"
                         "\tThis probably means\n"),
            invalidated=True)
        self.assertEqual(describe_error(error),
                         "server closed the connection unexpectedly "
                         "This probably means")
        self.assertEqual(describe_error(ValueError("bad row")), "bad row")
        self.assertEqual(describe_error(ValueError()), "ValueError")


class TestPsycopg3IdleKill(unittest.IsolatedAsyncioTestCase):
    """psycopg 3 reports an idle kill as InternalError (8g)."""

    async def test_an_idle_kill_is_a_disconnect(self):
        url = with_connection_defaults(_async_database_url()) \
            .update_query_dict(
                {"options": "-c idle_in_transaction_session_timeout=500"})
        idle_engine = create_async_engine(url)
        self.addAsyncCleanup(idle_engine.dispose)
        async with idle_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            await asyncio.sleep(1.5)
            with self.assertRaises(InternalError) as caught:
                await connection.execute(text("SELECT 1"))
        self.assertEqual(sqlstate(caught.exception.orig), "25P03")
        self.assertTrue(is_disconnect(caught.exception))


class TestProgramNameOnTheServer(unittest.TestCase):

    def test_an_empty_program_name_still_connects(self):
        connection = custom_psycopg2_connection(fallback_application_name="")
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone(), (1,))
        finally:
            connection.close()


@unittest.skipUnless(_database_over_tcp(),
                     "the test database is reached over a Unix socket")
class TestConnectionPaths(unittest.TestCase):
    """The defaults reach the sockets of the sync paths."""

    def test_the_sync_engine(self):
        with engine.connect() as connection:
            fileno = connection.connection.dbapi_connection.fileno()
            self.assertEqual(_socket_options(fileno), DEFAULT_SOCKET)
            name = connection.execute(
                text("SHOW application_name")).scalar_one()
        self.assertEqual(name, PROGRAM_NAME)

    def test_custom_psycopg2_connection(self):
        self.assertEqual(_custom_connection_state(),
                         (DEFAULT_SOCKET, PROGRAM_NAME))

    def test_the_kill_switch(self):
        switched_url = with_kill_switch(config.database.url)
        with patch.object(config.database, "url", switched_url):
            switched = create_engine(
                with_connection_defaults(make_url(config.database.url)))
            custom_state = _custom_connection_state()
        self.addCleanup(switched.dispose)
        with switched.connect() as connection:
            fileno = connection.connection.dbapi_connection.fileno()
            self.assertEqual(_socket_options(fileno), KILL_SWITCH_SOCKET)
        self.assertEqual(custom_state[0], KILL_SWITCH_SOCKET)


@unittest.skipUnless(_database_over_tcp(),
                     "the test database is reached over a Unix socket")
class TestAsyncConnectionPath(unittest.IsolatedAsyncioTestCase):
    """The defaults reach the socket of the async engine."""

    async def asyncTearDown(self):
        await async_engine.dispose()

    async def test_the_async_engine(self):
        self.assertEqual(await _async_engine_state(async_engine),
                         (DEFAULT_SOCKET, PROGRAM_NAME))

    async def test_the_kill_switch(self):
        switched_url = with_kill_switch(config.database.url)
        with patch.object(config.database, "url", switched_url):
            switched = create_async_engine(
                with_connection_defaults(_async_database_url()))
        self.addAsyncCleanup(switched.dispose)
        socket_options, _ = await _async_engine_state(switched)
        self.assertEqual(socket_options, KILL_SWITCH_SOCKET)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Update the expectations of `session_test.py`.** Replace
  everything from the line `from cms.db.session import custom_psycopg2_connection`
  to the end of `test_missing_password` with:

```python
from cms.db import connect_options
from cms.db.session import custom_psycopg2_connection


# custom_psycopg2_connection() adds the connection defaults (#39) under
# the URL's own query.
DEFAULTS = {**connect_options.LIBPQ_DEFAULTS,
            "fallback_application_name": connect_options.PROGRAM_NAME}


def _patch_db(s):
    """Patch the db connection string in the configuration"""
    return patch.object(config.database, "url", s)


@patch("psycopg2.connect")
class TestCustomPsycopg2Connect(unittest.TestCase):
    """Tests for the function custom_psycopg2_connect."""

    def test_network(self, connect):
        # Simple TCP connection.
        with _patch_db("postgresql+psycopg2://usrn:pwd@hooost:5433/dbname"):
            r = custom_psycopg2_connection()
            self.assertIs(r, connect.return_value)
            connect.assert_called_once_with(
                host="hooost",
                port=5433,
                user="usrn",
                password="pwd",
                database="dbname",
                **DEFAULTS)

    def test_network_missing_port(self, connect):
        # If port is missing, the default 5432 is used.
        with _patch_db("postgresql+psycopg2://usrn:pwd@hooost/dbname"):
            r = custom_psycopg2_connection()
            self.assertIs(r, connect.return_value)
            connect.assert_called_once_with(
                host="hooost",
                port=5432,
                user="usrn",
                password="pwd",
                database="dbname",
                **DEFAULTS)

    def test_unix_domain_socket(self, connect):
        with _patch_db("postgresql+psycopg2://usrn:pwd@/dbname"):
            r = custom_psycopg2_connection()
            self.assertIs(r, connect.return_value)
            connect.assert_called_once_with(
                host=None,
                port=None,
                user="usrn",
                password="pwd",
                database="dbname",
                **DEFAULTS)

    def test_unix_domain_socket_custom_dir(self, connect):
        with _patch_db("postgresql+psycopg2://"
                       "usrn:pwd@/dbname?host=/var/postgresql"):
            r = custom_psycopg2_connection()
            self.assertIs(r, connect.return_value)
            connect.assert_called_once_with(
                host="/var/postgresql",
                port=None,
                user="usrn",
                password="pwd",
                database="dbname",
                **DEFAULTS)

    def test_missing_password(self, connect):
        with _patch_db("postgresql+psycopg2://u@h:1/db"):
            r = custom_psycopg2_connection()
            self.assertIs(r, connect.return_value)
            connect.assert_called_once_with(
                host="h",
                port=1,
                user="u",
                password=None,
                database="db",
                **DEFAULTS)

    def test_a_url_keyword_overrides_its_default(self, connect):
        with _patch_db("postgresql+psycopg2://u@h:1/db?keepalives_idle=5"):
            custom_psycopg2_connection()
            connect.assert_called_once_with(
                host="h",
                port=1,
                user="u",
                password=None,
                database="db",
                **{**DEFAULTS, "keepalives_idle": "5"})
```

  Keep `test_not_psycopg2` and the `__main__` block as they are.

- [ ] **Step 4: Run the tests and see them fail.**
  - Run: `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/db/connect_options_test.py cmstestsuite/unit_tests/db/session_test.py -q`
  - Expected: collection errors, `ModuleNotFoundError: No module named 'cms.db.connect_options'`
    (or `ImportError: cannot import name 'connect_options'`).

- [ ] **Step 5: Create `cms/db/connect_options.py`.**

```python
# (fork header)

"""The settings every CMS database connection gets, and the disconnect
rule.

Every connection to PostgreSQL (the sync engine, the async engine and
custom_psycopg2_connection) gets the libpq keywords of LIBPQ_DEFAULTS
and a fallback_application_name, merged under the query of the
configured URL, where the user's keys win. They bound how long a
connect, or a call on a dead connection, can wait (#39). No "options"
keyword is ever added, so a user's options and PGOPTIONS behave as
before.

This module imports only SQLAlchemy and the standard library: the
cms.db package imports it before it creates its engines.

"""

import os
import sys
from collections.abc import Sequence

from sqlalchemy.engine.url import URL
from sqlalchemy.exc import DBAPIError


# Libpq keywords, with string values as in a URL query.
LIBPQ_DEFAULTS: dict[str, str] = {
    # Seconds. Green psycopg2 enforces it in cms.io.PsycoGevent.
    "connect_timeout": "10",
    # Already libpq's default; kept explicit.
    "keepalives": "1",
    "keepalives_idle": "30",
    "keepalives_interval": "10",
    "keepalives_count": "3",
    # Milliseconds, unlike the others: "60" would mean 60 ms.
    "tcp_user_timeout": "60000",
}


def program_name(argv: Sequence[str]) -> str:
    """Return the name a program shows in pg_stat_activity.

    It is the basename of argv[0], followed by a space and argv[1] when
    that argument is a decimal integer (the shard of a service), for
    example "cmsContestWebServer 2". It is not cut: the server clips
    it to its own limit.

    argv: the program's command line, as in sys.argv.

    return: the name; empty when argv is.

    """
    if not argv:
        return ""
    name = os.path.basename(argv[0])
    if len(argv) > 1 and argv[1].isdecimal():
        name += " " + argv[1]
    return name


PROGRAM_NAME = program_name(sys.argv)


def with_connection_defaults(url: URL) -> URL:
    """Return url with the CMS connection defaults under its query.

    The defaults are LIBPQ_DEFAULTS and fallback_application_name set
    to PROGRAM_NAME. A key of url's query wins over its default, and
    the rest of the query (host=/socket/dir, sslmode, ...) passes
    through. It works on URL objects, because str(url) would mask the
    password.

    url: the configured database URL.

    return: the URL to connect with.

    """
    defaults = {**LIBPQ_DEFAULTS, "fallback_application_name": PROGRAM_NAME}
    return url.set(query={**defaults, **url.query})


def sqlstate(error: BaseException | None) -> str | None:
    """Return the SQLSTATE of a driver error.

    error: a psycopg2 error (its pgcode) or a psycopg 3 one (its
        sqlstate), usually the orig of a SQLAlchemy DBAPIError.

    return: the five-character code, or None when the error has none,
        as with a failed connect.

    """
    return getattr(error, "pgcode", None) or getattr(error, "sqlstate", None)


def is_disconnect(error: BaseException) -> bool:
    """Tell whether an error says that the database connection is gone.

    That is a SQLAlchemy error whose connection SQLAlchemy invalidated
    (which covers driver errors with no SQLSTATE, such as "server
    closed the connection unexpectedly", and psycopg 3's InternalError
    on an idle kill), or one whose SQLSTATE is of class 08, "connection
    exception". Deadlocks (40P01), serialization failures (40001) and
    lock timeouts (55P03) are OperationalErrors too, but not
    disconnects: react to a disconnect with this rule, never with an
    exception class.

    error: any exception.

    return: whether the connection is gone.

    """
    return isinstance(error, DBAPIError) and (
        error.connection_invalidated
        or (sqlstate(error.orig) or "").startswith("08"))


def describe_error(error: BaseException) -> str:
    """Return the message of an error's driver error, on one line.

    SQLAlchemy's own text adds the statement and a link on more lines;
    the driver's message is what names the failure ("server closed the
    connection unexpectedly", "timeout expired", ...).

    error: any exception, usually a SQLAlchemy DBAPIError.

    return: the lines of the driver error's message (of error's own
        message, if it has no driver error) joined by spaces, or the
        class name when the message is empty.

    """
    cause = getattr(error, "orig", None) or error
    text = " ".join(line.strip() for line in str(cause).splitlines()
                    if line.strip())
    return text or type(cause).__name__
```

- [ ] **Step 6: Apply it to the sync engine.** In `cms/db/__init__.py`,
  change the imports at lines 34-37 to:

```python
from sqlalchemy import create_engine, select, MetaData
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import configure_mappers, joinedload, subqueryload

from cms import config
from .connect_options import with_connection_defaults
```

  and replace lines 90-91 (`engine = create_engine(config.database.url, ...`)
  with:

```python
engine = create_engine(
    with_connection_defaults(make_url(config.database.url)),
    echo=config.database.debug, pool_timeout=60, pool_recycle=120)
```

- [ ] **Step 7: Apply it to the async engine.** In
  `cms/db/async_session.py`, add after `from cms.conf import config`
  (line 45):

```python
from .connect_options import with_connection_defaults
```

  and replace lines 85-89 (the comment and `async_engine = ...`) with:

```python
# Mirrors cms/db/__init__.py's sync `engine = create_engine(...)` call:
# created once at import time, connects lazily on first actual use. The
# connection defaults are added here, so that _async_database_url()
# stays the plain driver swap.
async_engine = create_async_engine(
    with_connection_defaults(_async_database_url()),
    echo=config.database.debug, pool_timeout=60, pool_recycle=120)
```

- [ ] **Step 8: Apply it to `custom_psycopg2_connection`.** In
  `cms/db/session.py`, add after `from cms.conf import config` (line 37):

```python
from .connect_options import with_connection_defaults
```

  In the docstring of `custom_psycopg2_connection`, replace the paragraph
  `kwargs: additional values to use as query parameters in the connection URL.`
  with:

```python
    The URL gets the connection defaults of cms.db.connect_options,
    under its own query.

    kwargs: additional values to use as query parameters in the
        connection URL; they override the URL's query and the defaults.
```

  and replace line 94 (`database_url: URL = make_url(config.database.url)`)
  with:

```python
    database_url: URL = with_connection_defaults(
        make_url(config.database.url))
```

  Its existing `args.update(database_url.query)` then carries the keys to
  LargeObject, `drop_db` and the updaters. Do not touch `args.update(kwargs)`.

- [ ] **Step 9: Document the defaults in the sample config.** In
  `config/cms.sample.toml`, replace the line `# Connection string for the database.`
  above `url` (line 71) with:

```toml
# Connection string for the database.
# Every connection also gets these libpq settings, unless the URL's own
# query sets them: connect_timeout=10 (seconds), keepalives=1,
# keepalives_idle=30 and keepalives_interval=10 (seconds),
# keepalives_count=3, tcp_user_timeout=60000 (milliseconds, not
# seconds), and fallback_application_name, the program's name. For
# example, "...?keepalives_idle=60" changes only that one.
```

- [ ] **Step 10: Run the tests and see them pass.**
  - Run: `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/db/connect_options_test.py cmstestsuite/unit_tests/db/session_test.py cmstestsuite/unit_tests/db/async_session_test.py cmstestsuite/unit_tests/db/psycopg_green_executor_test.py cmstestsuite/unit_tests/db/util_test.py -q`
  - Expected: all pass, none skipped (the test database is on TCP).
  - Run: `.venv/bin/python -c "import tomllib; tomllib.load(open('config/cms.sample.toml', 'rb'))"`
  - Expected: no output (the sample still parses).

- [ ] **Step 11: Lint.**
  - Run: `uvx -q pyflakes cms/db/connect_options.py cms/db/__init__.py cms/db/async_session.py cms/db/session.py cmstestsuite/unit_tests/db/connect_options_test.py cmstestsuite/unit_tests/db/session_test.py`
  - Expected: no output.
  - Run: `uvx -q pycodestyle --max-line-length=79 cms/db/connect_options.py cms/db/async_session.py cms/db/session.py cmstestsuite/unit_tests/db/connect_options_test.py cmstestsuite/unit_tests/db/session_test.py`
  - Expected: no output. For `cms/db/__init__.py`, only the existing E402
    lines.

- [ ] **Step 12: Commit.**

```bash
git add cms/db/connect_options.py cms/db/__init__.py cms/db/async_session.py \
    cms/db/session.py config/cms.sample.toml \
    cmstestsuite/unit_tests/db/connect_options_test.py \
    cmstestsuite/unit_tests/db/session_test.py
git commit -F - <<'EOF'
feat(db): give every database connection timeouts and a program name

Merge connect_timeout, TCP keepalives, tcp_user_timeout and
fallback_application_name under the URL query of the sync engine, the
async engine and custom_psycopg2_connection, so that the user's URL
still wins. Add the disconnect rule (is_disconnect) and a one-line
rendering of driver errors for the fixes that follow.

Refs #39

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task 2: The connect deadline of green psycopg2

**Files:**
- Modify: `cms/io/PsycoGevent.py:35-39` (a fork note and the imports) and
  `:72-84` (`gevent_wait_callback`)
- Create: `cmstestsuite/unit_tests/io/psycogevent_test.py` (asyncio group)

**Interfaces:**
- Consumes: `with_connection_defaults` (Task 1);
  `with_kill_switch(url: str) -> str` from
  `cmstestsuite/unit_tests/db/connect_options_test.py` (Task 1).
- Produces (in `cms/io/PsycoGevent.py`): `MIN_CONNECT_TIMEOUT_S = 2`;
  `connect_deadline(conn) -> float | None`; `gevent_wait_callback` keeps
  its signature `(conn, timeout=None)`.

- [ ] **Step 1: Write the failing tests.** Create
  `cmstestsuite/unit_tests/io/psycogevent_test.py`:

```python
# (fork header)

"""Tests for the connect deadline of cms.io.PsycoGevent (#39)."""

import gc
import os
import socket
import time
import unittest
from types import SimpleNamespace

import gevent
import psycopg2
from psycopg2 import extensions
from sqlalchemy.engine.url import make_url

from cms.db import custom_psycopg2_connection
from cms.db.connect_options import with_connection_defaults
from cms.io.PsycoGevent import connect_deadline, is_psycopg_green, \
    make_psycopg_green
from cmstestsuite.unit_tests.db.connect_options_test import \
    with_kill_switch


class TestConnectDeadline(unittest.TestCase):
    """Which waits get a deadline, and how long it is."""

    @staticmethod
    def _seconds_left(status: int, dsn: str) -> float | None:
        deadline = connect_deadline(SimpleNamespace(status=status, dsn=dsn))
        return None if deadline is None else deadline - time.monotonic()

    def test_a_connect_gets_its_connect_timeout(self):
        self.assertAlmostEqual(
            self._seconds_left(extensions.STATUS_SETUP,
                               "host=h connect_timeout=10"), 10, delta=0.5)

    def test_a_connect_timeout_under_2_seconds_counts_as_2(self):
        self.assertAlmostEqual(
            self._seconds_left(extensions.STATUS_SETUP,
                               "host=h connect_timeout=1"), 2, delta=0.5)

    def test_zero_or_no_connect_timeout_means_no_deadline(self):
        for dsn in ("host=h connect_timeout=0", "host=h"):
            with self.subTest(dsn=dsn):
                self.assertIsNone(
                    self._seconds_left(extensions.STATUS_SETUP, dsn))

    def test_a_query_never_gets_a_deadline(self):
        for status in (extensions.STATUS_READY, extensions.STATUS_BEGIN):
            with self.subTest(status=status):
                self.assertIsNone(self._seconds_left(
                    status, "host=h connect_timeout=10"))


class TestGreenConnect(unittest.TestCase):
    """A green connect to a server that accepts and never answers."""

    def setUp(self):
        make_psycopg_green()
        self.assertTrue(is_psycopg_green())
        # The kernel completes the handshake; nobody ever answers.
        self.listener = socket.socket()
        self.addCleanup(self.listener.close)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(64)
        self.port = self.listener.getsockname()[1]

    def _connect(self, **keywords: str) -> None:
        psycopg2.connect(host="127.0.0.1", port=self.port, user="cms",
                         dbname="cms", **keywords)

    def _seconds_to_fail(self, **keywords: str) -> float:
        started = time.monotonic()
        with self.assertRaises(psycopg2.OperationalError) as caught:
            self._connect(**keywords)
        self.assertEqual(str(caught.exception), "timeout expired")
        return time.monotonic() - started

    def test_a_silent_server_fails_at_the_connect_timeout(self):
        elapsed = self._seconds_to_fail(connect_timeout="2")
        self.assertGreaterEqual(elapsed, 2)
        self.assertLess(elapsed, 3.5)

    def test_a_connect_timeout_of_1_waits_2_seconds(self):
        elapsed = self._seconds_to_fail(connect_timeout="1")
        self.assertGreaterEqual(elapsed, 2)
        self.assertLess(elapsed, 3.5)

    def test_a_zero_connect_timeout_has_no_deadline(self):
        started = time.monotonic()
        with self.assertRaises(gevent.Timeout):
            with gevent.Timeout(3.5):
                self._connect(connect_timeout="0")
        self.assertGreaterEqual(time.monotonic() - started, 3.5)

    def test_the_kill_switch_removes_the_deadline(self):
        url = with_connection_defaults(make_url(with_kill_switch(
            "postgresql+psycopg2://cms@127.0.0.1:%d/cms" % self.port)))
        with self.assertRaises(gevent.Timeout):
            with gevent.Timeout(3.5):
                self._connect(**url.query)

    def test_timed_out_connects_leave_no_file_descriptor(self):
        # The first timed wait of a thread creates its gevent hub's own
        # descriptors (eventpoll, eventfd): count after it.
        with self.assertRaises(psycopg2.OperationalError):
            self._connect(connect_timeout="2")
        gc.collect()
        before = len(os.listdir("/proc/self/fd"))
        for _ in range(5):
            try:
                self._connect(connect_timeout="2")
            except psycopg2.OperationalError:
                pass
        # The collector frees a connection that a reference cycle still
        # holds; a leaked one would stay.
        gc.collect()
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)


class TestQueriesHaveNoDeadline(unittest.TestCase):

    def test_a_long_query_is_not_cut(self):
        make_psycopg_green()
        connection = custom_psycopg2_connection(connect_timeout="2")
        try:
            cursor = connection.cursor()
            started = time.monotonic()
            cursor.execute("SELECT pg_sleep(3)")
            cursor.fetchall()
            self.assertGreaterEqual(time.monotonic() - started, 3)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and see them fail.**
  - Run: `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/io/psycogevent_test.py -q`
  - Expected: a collection error, `ImportError: cannot import name 'connect_deadline'`.

- [ ] **Step 3: Implement the deadline.** In `cms/io/PsycoGevent.py`,
  replace lines 35-39 (the imports) with:

```python
# The fork changes this vendored file: gevent_wait_callback enforces
# connect_timeout during a connect (see connect_deadline, #39).

import time
from contextlib import contextmanager

import psycopg2
from gevent.socket import wait_read, wait_write
from psycopg2 import extensions


# Libpq's floor for connect_timeout: a positive value under 2 s acts as
# 2 s.
MIN_CONNECT_TIMEOUT_S = 2
```

  and replace `gevent_wait_callback` (lines 72-84) with:

```python
def connect_deadline(conn) -> float | None:
    """Return when the connect a wait callback serves must give up.

    Libpq leaves connect_timeout to the application when it connects
    without blocking, as green psycopg2 does. The value is the
    connection's own connect_timeout, so that a URL's value (0
    included) applies: 0 or none means no deadline, and a positive
    value under MIN_CONNECT_TIMEOUT_S counts as that, as in libpq.
    Limits: with a multi-host URL the deadline covers the whole
    connect, not each host; a DNS lookup that blocks before the socket
    exists is not covered.

    conn: the connection the callback waits for, before its first
        poll(): its status leaves STATUS_SETUP as soon as the connect
        makes progress.

    return: the time.monotonic() deadline, or None when the call is not
        a connect or has no deadline.

    """
    if conn.status != extensions.STATUS_SETUP:
        return None
    value = extensions.parse_dsn(conn.dsn).get("connect_timeout")
    if not value or int(value) <= 0:
        return None
    return time.monotonic() + max(int(value), MIN_CONNECT_TIMEOUT_S)


def gevent_wait_callback(conn, timeout=None):
    """A wait callback useful to allow gevent to work with Psycopg.

    During a connect it also enforces connect_timeout (see
    connect_deadline): it closes the connection and raises
    psycopg2.OperationalError("timeout expired"), libpq's own message.
    It never bounds a query: a client-side cut would not cancel the
    server's work.

    """
    deadline = connect_deadline(conn)
    while 1:
        state = conn.poll()
        if state == extensions.POLL_OK:
            break
        elif state == extensions.POLL_READ:
            wait = wait_read
        elif state == extensions.POLL_WRITE:
            wait = wait_write
        else:
            raise psycopg2.OperationalError(
                "Bad result from poll: %r" % state)
        if deadline is None:
            wait(conn.fileno(), timeout=timeout)
            continue
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                wait(conn.fileno(), timeout=remaining)
                continue
            except TimeoutError:
                # gevent's socket.timeout.
                pass
        # Close now: gevent keeps the last timeout error, and with it
        # this frame, so the socket would stay open until the next one.
        conn.close()
        raise psycopg2.OperationalError("timeout expired")
```

- [ ] **Step 4: Run the tests and see them pass.**
  - Run: `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/io/psycogevent_test.py cmstestsuite/unit_tests/db/psycopg_green_executor_test.py cmstestsuite/unit_tests/db/connect_options_test.py -q`
  - Expected: all pass. `psycogevent_test.py` takes about 30 s (the
    timed-out connects).

- [ ] **Step 5: Lint.**
  - Run: `uvx -q pyflakes cms/io/PsycoGevent.py cmstestsuite/unit_tests/io/psycogevent_test.py`
  - Run: `uvx -q pycodestyle --max-line-length=79 cms/io/PsycoGevent.py cmstestsuite/unit_tests/io/psycogevent_test.py`
  - Expected: no output from either.

- [ ] **Step 6: Commit.**

```bash
git add cms/io/PsycoGevent.py cmstestsuite/unit_tests/io/psycogevent_test.py
git commit -F - <<'EOF'
fix(io): enforce connect_timeout on green psycopg2 connects

Libpq leaves connect_timeout to the application in non-blocking mode,
so a green connect to a server that never answers waited forever. The
gevent wait callback now reads the connection's own connect_timeout
during a connect and raises "timeout expired", as blocking libpq does.
Queries keep no client-side deadline.

Refs #39

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task 3: Session exit after a lost connection (8c)

**Files:**
- Modify: `cms/db/connect_options.py` (add `logging`, `logger` and
  `log_rollback_error`)
- Modify: `cms/db/session.py:29-30` (imports) and `:53-73` (`SessionGen`)
- Modify: `cms/db/async_session.py:39-45` (imports) and `:132-145`
  (`AsyncSessionGen.__aexit__`)
- Create: `cmstestsuite/unit_tests/dbfault.py` (test helper, not collected)
- Create: `cmstestsuite/unit_tests/db/session_exit_test.py` (asyncio group)

**Interfaces:**
- Consumes: `is_disconnect`, `describe_error` (Task 1).
- Produces:
  - `cms.db.connect_options.log_rollback_error(error: Exception, body_failed: bool) -> bool`:
    True when it logged the error and the caller must swallow it, False
    when the caller must re-raise it;
  - `cmstestsuite.unit_tests.dbfault.terminate_backends(condition: str, params: tuple = ()) -> int`,
    used by Task 4's tests.

- [ ] **Step 1: Write the test helper.** Create
  `cmstestsuite/unit_tests/dbfault.py`:

```python
# (fork header)

"""Cut database connections from a test, as a failing network would."""

import time

from cms.db import custom_psycopg2_connection


def terminate_backends(condition: str, params: tuple = ()) -> int:
    """End the backends that match a condition, and wait until they are gone.

    The killer's own backend never matches.

    condition: an SQL condition on the columns of pg_stat_activity,
        with %s placeholders.
    params: the values of the placeholders.

    return: how many backends were ended.

    raise (AssertionError): if one is still there after 10 seconds.

    """
    killer = custom_psycopg2_connection()
    try:
        killer.autocommit = True
        cursor = killer.cursor()
        cursor.execute(
            "SELECT pid FROM pg_stat_activity "
            "WHERE pid <> pg_backend_pid() AND (" + condition + ")", params)
        pids = [pid for (pid,) in cursor.fetchall()]
        for pid in pids:
            cursor.execute("SELECT pg_terminate_backend(%s)", (pid,))
        deadline = time.monotonic() + 10
        while pids:
            cursor.execute("SELECT count(*) FROM pg_stat_activity "
                           "WHERE pid = ANY(%s)", (pids,))
            if cursor.fetchone()[0] == 0:
                break
            if time.monotonic() > deadline:
                raise AssertionError(
                    "backends %s still there after 10 s" % pids)
            time.sleep(0.1)
        return len(pids)
    finally:
        killer.close()
```

- [ ] **Step 2: Write the failing tests.** Create
  `cmstestsuite/unit_tests/db/session_exit_test.py`:

```python
# (fork header)

"""Tests for the exit of SessionGen and AsyncSessionGen when the
connection is lost (#39, 8c).

"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from cms.db import SessionGen, engine
from cms.db.async_session import AsyncSessionGen, async_engine
from cms.db.connect_options import log_rollback_error
from cmstestsuite.unit_tests.dbfault import terminate_backends


LOST = ("Lost the database connection before the session's rollback: "
        "server closed the connection unexpectedly")


def _lost_connection_error() -> OperationalError:
    return OperationalError(
        "ROLLBACK", {},
        Exception("server closed the connection unexpectedly"),
        connection_invalidated=True)


class TestLogRollbackError(unittest.TestCase):
    """The rule, without a database."""

    def test_after_a_failed_block_any_error_is_debug(self):
        with self.assertLogs("cms.db.connect_options", "DEBUG") as logs:
            self.assertTrue(log_rollback_error(ValueError("x"), True))
        self.assertEqual([r.levelname for r in logs.records], ["DEBUG"])

    def test_a_lost_connection_after_a_good_block_is_a_warning(self):
        with self.assertLogs("cms.db.connect_options", "DEBUG") as logs:
            self.assertTrue(
                log_rollback_error(_lost_connection_error(), False))
        self.assertEqual([(r.levelname, r.getMessage())
                          for r in logs.records], [("WARNING", LOST)])

    def test_another_error_after_a_good_block_must_propagate(self):
        with self.assertNoLogs("cms.db.connect_options", "DEBUG"):
            self.assertFalse(log_rollback_error(ValueError("x"), False))


class TestSessionGenExit(unittest.TestCase):

    def test_a_lost_connection_after_a_good_block_is_not_an_error(self):
        checked_out = engine.pool.checkedout()
        with self.assertLogs("cms.db.connect_options", "WARNING") as logs:
            with SessionGen() as session:
                pid = session.execute(
                    text("SELECT pg_backend_pid()")).scalar_one()
                terminate_backends("pid = %s", (pid,))
        # The driver's text varies with the libpq version: check the
        # fixed part only.
        self.assertTrue(logs.records[0].getMessage().startswith(
            "Lost the database connection before the session's rollback: "))
        # close() ran: the connection went back to the pool.
        self.assertEqual(engine.pool.checkedout(), checked_out)

    def test_another_rollback_error_still_propagates(self):
        checked_out = engine.pool.checkedout()
        with self.assertRaises(ValueError):
            with SessionGen() as session:
                session.execute(text("SELECT 1"))
                session.rollback = MagicMock(
                    side_effect=ValueError("rollback failed"))
        self.assertEqual(engine.pool.checkedout(), checked_out)

    def test_the_block_error_wins_over_a_rollback_error(self):
        checked_out = engine.pool.checkedout()
        with self.assertLogs("cms.db.connect_options", "DEBUG") as logs:
            with self.assertRaises(KeyError):
                with SessionGen() as session:
                    pid = session.execute(
                        text("SELECT pg_backend_pid()")).scalar_one()
                    terminate_backends("pid = %s", (pid,))
                    raise KeyError("the block's own error")
        self.assertEqual([r.levelname for r in logs.records], ["DEBUG"])
        self.assertEqual(engine.pool.checkedout(), checked_out)


class TestAsyncSessionGenExit(unittest.IsolatedAsyncioTestCase):

    async def asyncTearDown(self):
        await async_engine.dispose()

    @staticmethod
    def _checked_out() -> int:
        return async_engine.sync_engine.pool.checkedout()

    async def test_a_lost_connection_after_a_good_block_is_not_an_error(self):
        checked_out = self._checked_out()
        with self.assertLogs("cms.db.connect_options", "WARNING") as logs:
            async with AsyncSessionGen() as session:
                pid = (await session.execute(
                    text("SELECT pg_backend_pid()"))).scalar_one()
                terminate_backends("pid = %s", (pid,))
        self.assertTrue(logs.records[0].getMessage().startswith(
            "Lost the database connection before the session's rollback: "))
        self.assertEqual(self._checked_out(), checked_out)

    async def test_another_rollback_error_still_propagates(self):
        checked_out = self._checked_out()
        with self.assertRaises(ValueError):
            async with AsyncSessionGen() as session:
                await session.execute(text("SELECT 1"))
                session.rollback = AsyncMock(
                    side_effect=ValueError("rollback failed"))
        self.assertEqual(self._checked_out(), checked_out)

    async def test_the_block_error_wins_over_a_rollback_error(self):
        checked_out = self._checked_out()
        with self.assertLogs("cms.db.connect_options", "DEBUG") as logs:
            with self.assertRaises(KeyError):
                async with AsyncSessionGen() as session:
                    pid = (await session.execute(
                        text("SELECT pg_backend_pid()"))).scalar_one()
                    terminate_backends("pid = %s", (pid,))
                    raise KeyError("the block's own error")
        self.assertEqual([r.levelname for r in logs.records], ["DEBUG"])
        self.assertEqual(self._checked_out(), checked_out)

    async def test_a_cancelled_rollback_still_propagates_and_closes(self):
        checked_out = self._checked_out()
        with self.assertRaises(asyncio.CancelledError):
            async with AsyncSessionGen() as session:
                await session.execute(text("SELECT 1"))
                session.rollback = AsyncMock(
                    side_effect=asyncio.CancelledError())
        self.assertEqual(self._checked_out(), checked_out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests and see them fail.**
  - Run: `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/db/session_exit_test.py -q`
  - Expected: a collection error, `ImportError: cannot import name 'log_rollback_error'`.

- [ ] **Step 4: Add the rule to `cms/db/connect_options.py`.** Add
  `import logging` above `import os`, add after the imports:

```python
logger = logging.getLogger(__name__)
```

  and append at the end of the module:

```python
def log_rollback_error(error: Exception, body_failed: bool) -> bool:
    """Log an error of the rollback that ends a session, if harmless.

    SessionGen and AsyncSessionGen roll back on exit. When their block
    raised, the block's own error is the one to report, so a rollback
    error is only logged at DEBUG. When the block succeeded and the
    rollback finds the connection gone, the server has already
    discarded the transaction: that is a WARNING. Any other rollback
    error must propagate, as before.

    error: the error that the rollback raised.
    body_failed: whether the session's block raised.

    return: True if the error was logged and must not propagate; False
        if the caller must re-raise it.

    """
    if body_failed:
        logger.debug("The rollback after a failed session block failed "
                     "too: %s", describe_error(error))
        return True
    if is_disconnect(error):
        logger.warning("Lost the database connection before the session's "
                       "rollback: %s", describe_error(error))
        return True
    return False
```

- [ ] **Step 5: Use it in `SessionGen`.** In `cms/db/session.py`, add
  `from types import TracebackType` after `import typing` (line 30), add
  `log_rollback_error` to the import from Task 1:

```python
from .connect_options import log_rollback_error, with_connection_defaults
```

  and replace `__exit__` (lines 71-73) with:

```python
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # A rollback on a lost connection raises; it must neither hide
        # the block's own error nor skip close() (see log_rollback_error).
        try:
            self.session.rollback()
        except Exception as error:
            if not log_rollback_error(error, exc_value is not None):
                raise
        finally:
            self.session.close()
```

- [ ] **Step 6: Use it in `AsyncSessionGen`.** In
  `cms/db/async_session.py`, change the Task 1 import to:

```python
from .connect_options import log_rollback_error, with_connection_defaults
```

  and replace `__aexit__` (lines 132-145) with:

```python
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # try/finally so that if rollback() itself raises (e.g. a second
        # asyncio.CancelledError landing while it is suspended during a
        # shutdown that cancels tasks), the session's connection is
        # still returned to the pool rather than held until GC. A
        # rollback error on a lost connection must not hide the block's
        # own error either (see log_rollback_error); a CancelledError is
        # not an Exception, so it still propagates.
        try:
            await self.session.rollback()
        except Exception as error:
            if not log_rollback_error(error, exc_value is not None):
                raise
        finally:
            await self.session.close()
```

- [ ] **Step 7: Run the tests and see them pass.**
  - Run: `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/db/session_exit_test.py cmstestsuite/unit_tests/db/session_test.py cmstestsuite/unit_tests/db/async_session_test.py cmstestsuite/unit_tests/db/connect_options_test.py cmstestsuite/unit_tests/db/util_test.py -q`
  - Expected: all pass.

- [ ] **Step 8: Lint.**
  - Run: `uvx -q pyflakes cms/db/connect_options.py cms/db/session.py cms/db/async_session.py cmstestsuite/unit_tests/dbfault.py cmstestsuite/unit_tests/db/session_exit_test.py`
  - Run: `uvx -q pycodestyle --max-line-length=79 cms/db/connect_options.py cms/db/session.py cms/db/async_session.py cmstestsuite/unit_tests/dbfault.py cmstestsuite/unit_tests/db/session_exit_test.py`
  - Expected: no output from either.

- [ ] **Step 9: Commit.**

```bash
git add cms/db/connect_options.py cms/db/session.py cms/db/async_session.py \
    cmstestsuite/unit_tests/dbfault.py \
    cmstestsuite/unit_tests/db/session_exit_test.py
git commit -F - <<'EOF'
fix(db): close sessions and keep their result when the connection is lost

SessionGen rolled back and closed with no try/finally, so a block whose
work had succeeded raised on a dead connection and skipped close(), and
AsyncSessionGen's rollback error replaced the block's own. Both now
close in finally, log a lost connection after a good block as a
WARNING, and let the block's error win.

Refs #39

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task 4: The ES write path on a lost connection (8b)

**Files:**
- Modify: `cms/service/EvaluationService.py:40-44` (imports), after `:282`
  (new `_lost_connection`), `:997-1169` (`_write_results_sync`, split
  into a wrapper and `_write_results_in_session`), `:1171-1203`
  (`write_results_one_object_and_type`)
- Create: `cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py`
  (asyncio group)

**Interfaces:**
- Consumes: `is_disconnect`, `sqlstate`, `describe_error`,
  `with_connection_defaults` (Task 1); `terminate_backends` (Task 3).
- Produces:
  - module function `_lost_connection(error: BaseException) -> bool` in
    `cms/service/EvaluationService.py`;
  - `EvaluationService._write_results_in_session(self, by_object_and_type: dict[tuple[str, int, int, bool], list[tuple[ESOperation, Result]]]) -> None`;
  - in the test file, the base class `EvaluationServiceDatabaseTestCase`
    (Task 5 adds a class that derives from it), with:
    - attributes `service: EvaluationService`, `dataset`, `testcases`
      (3 `Testcase`, codenames `t0`..`t2`), `submission`,
      `submission_result` (compiled, no evaluation);
    - `service._threadsafe_notify_scoring_service`, replaced by a
      `MagicMock`;
    - `_evaluation_items() -> list[tuple[ESOperation, Result]]` and
      `_load_result() -> SubmissionResult`.

- [ ] **Step 1: Write the failing tests.** Create
  `cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py`:

```python
# (fork header)

"""Tests for EvaluationService when the database connection is lost
(#39: 8b, the write path; 8a, the phase-2 window).

"""

import asyncio
import socket
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import IntegrityError, OperationalError, \
    PendingRollbackError
from sqlalchemy.orm import sessionmaker

from cms.conf import Address, ServiceCoord, config
from cms.db import Submission, engine
from cms.db.async_session import async_engine
from cms.db.connect_options import with_connection_defaults
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, Result
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.dbfault import terminate_backends
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


LOST = ("Lost the database connection while writing worker results "
        "(%s); the sweeper will redo them within about 2 minutes.")


class EvaluationServiceDatabaseTestCase(
    ServiceLoggingIsolationMixin, DatabaseMixin,
    unittest.IsolatedAsyncioTestCase,
):
    """An EvaluationService on the test database, with one compiled
    submission of a task with TESTCASES testcases.

    """

    TESTCASES = 3

    async def asyncSetUp(self):
        # As in write_results_creates_result_test.py: no real Worker,
        # LogService or ScoringService is reachable.
        local_address = Address("127.0.0.1", 0)
        for patcher in (
            patch("cms.util.config.services", {}),
            patch("cms.io.async_service.get_service_address",
                  return_value=local_address),
            patch("cms.io.async_rpc.get_service_address",
                  return_value=local_address),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        contest = self.add_contest()
        task = self.add_task(contest=contest)
        self.dataset = self.add_dataset(task=task)
        task.active_dataset = self.dataset
        self.testcases = [
            self.add_testcase(self.dataset, codename="t%d" % index)
            for index in range(self.TESTCASES)]
        participation = self.add_participation(contest=contest)
        self.submission, (self.submission_result,) = \
            self.add_submission_with_results(task, participation, True)
        # The service reads and writes with its own sessions.
        self.session.commit()

        # Built as in write_results_creates_result_test.py, with the
        # sweeper's timer stubbed out: the tests sweep by themselves.
        with patch.object(EvaluationService, "start_sweeper",
                          lambda self, timeout: None):
            self.service = EvaluationService(0)
        self.service._loop = asyncio.get_running_loop()
        self.addCleanup(self.service._disconnect_all)
        self.service.get_executor().pool.add_worker(
            ServiceCoord("Worker", 0))
        # There is no ScoringService: record the notifications.
        self.service._threadsafe_notify_scoring_service = MagicMock()

    async def asyncTearDown(self):
        for task in list(self.service._background_tasks):
            task.cancel()
        await async_engine.dispose()

    def tearDown(self):
        # The sweeper looks at every submission of the database.
        self.delete_data()
        super().tearDown()

    def _evaluation_items(self) -> list[tuple[ESOperation, Result]]:
        """Return a worker result for each testcase of the submission."""
        return [(ESOperation(ESOperation.EVALUATION, self.submission.id,
                             self.dataset.id, testcase.codename),
                 Result(MagicMock(), True))
                for testcase in self.testcases]

    def _load_result(self):
        """Return the submission's result, read again from the DB."""
        self.session.expire_all()
        submission = Submission.get_from_id(self.submission.id, self.session)
        return submission.get_result(self.dataset)


class TestWriteResultsOnALostConnection(EvaluationServiceDatabaseTestCase):

    async def test_a_disconnect_on_the_first_row_stops_the_batch(self):
        lost = OperationalError(
            "INSERT", {},
            Exception("server closed the connection unexpectedly"),
            connection_invalidated=True)
        with patch.object(self.service, "write_results_one_row",
                          side_effect=lost) as write_row, \
                self.assertLogs("cms.service.EvaluationService",
                                "ERROR") as logs:
            await self.service.write_results(self._evaluation_items())
        # The later rows are not attempted.
        self.assertEqual(write_row.call_count, 1)
        self.assertEqual(
            [record.getMessage() for record in logs.records],
            [LOST % "server closed the connection unexpectedly"])

    async def test_an_unusable_transaction_stops_the_batch_too(self):
        with patch.object(self.service, "write_results_one_row",
                          side_effect=PendingRollbackError("rolled back")) \
                as write_row:
            # Not a lost connection: FlushingDict logs it, as before.
            with self.assertRaises(PendingRollbackError):
                await self.service.write_results(self._evaluation_items())
        self.assertEqual(write_row.call_count, 1)

    async def test_a_disconnect_at_the_first_query_is_one_error(self):
        items = self._evaluation_items()
        self.session.commit()
        # Leave one idle connection in the pool, and cut it: the flush
        # gets it, as after a server restart.
        engine.dispose()
        with engine.connect() as connection:
            pid = connection.execute(
                text("SELECT pg_backend_pid()")).scalar_one()
        self.assertEqual(terminate_backends("pid = %s", (pid,)), 1)
        for operation, result in items:
            self.service.result_cache.add(operation, result)
        with self.assertLogs("cms.service.EvaluationService",
                             "ERROR") as logs, \
                self.assertNoLogs("cms.service.flushingdict", "ERROR"):
            await self.service.result_cache.flush()
        self.assertEqual(len(logs.records), 1)
        message = logs.records[0].getMessage()
        # The driver's text varies with the libpq version.
        self.assertTrue(message.startswith(
            "Lost the database connection while writing worker results ("))
        self.assertTrue(message.endswith(
            "); the sweeper will redo them within about 2 minutes."))

    async def test_a_failed_connect_at_the_first_query_is_one_error(self):
        items = self._evaluation_items()
        self.session.commit()
        silent = socket.socket()
        self.addCleanup(silent.close)
        silent.bind(("127.0.0.1", 0))
        silent.listen(8)
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        closed_port = closed.getsockname()[1]
        closed.close()
        base = make_url(config.database.url)
        for case, port, expected in (
                ("refused", closed_port, "Connection refused"),
                ("silent", silent.getsockname()[1], "timeout expired")):
            with self.subTest(case=case):
                failing = create_engine(with_connection_defaults(base.set(
                    host="127.0.0.1", port=port,
                    query={"connect_timeout": "2"})))
                self.addCleanup(failing.dispose)
                with patch("cms.db.session.Session", sessionmaker(failing)), \
                        self.assertLogs("cms.service.EvaluationService",
                                        "ERROR") as logs:
                    await self.service.write_results(items)
                self.assertEqual(len(logs.records), 1)
                self.assertIn(expected, logs.records[0].getMessage())

    async def test_other_errors_still_go_row_by_row(self):
        errors = [IntegrityError("INSERT", {}, Exception("duplicate key")),
                  ValueError("bad row"), None]
        with patch.object(self.service, "write_results_one_row",
                          side_effect=errors) as write_row, \
                self.assertLogs("cms.service.EvaluationService",
                                "WARNING") as logs:
            await self.service.write_results(self._evaluation_items())
        self.assertEqual(write_row.call_count, 3)
        messages = [record.getMessage() for record in logs.records]
        self.assertIn("Integrity error while inserting worker result.",
                      messages)
        self.assertIn("Unexpected exception while inserting worker result.",
                      messages)
        self.assertEqual([message for message in messages
                          if message.startswith("Lost the database")], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and see them fail.**
  - Run: `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py -q`
  - Expected: `test_a_disconnect_on_the_first_row_stops_the_batch` fails
    (3 calls, 3 "Unexpected exception" ERRORs);
    `test_an_unusable_transaction_stops_the_batch_too` fails (3 calls,
    then `PendingRollbackError` from `commit()` or no error at all);
    `test_a_disconnect_at_the_first_query_is_one_error` fails (no ES
    ERROR; flushingdict logs "Unexpected error while flushing.");
    `test_a_failed_connect_at_the_first_query_is_one_error` fails (the
    `OperationalError` propagates);
    `test_other_errors_still_go_row_by_row` passes already.

- [ ] **Step 3: Import the rule.** In `cms/service/EvaluationService.py`,
  replace line 41 (`from sqlalchemy.exc import IntegrityError`) with:

```python
from sqlalchemy.exc import IntegrityError, OperationalError, \
    PendingRollbackError
```

  and add after line 44 (`from cms.db.session import Session`):

```python
from cms.db.connect_options import describe_error, is_disconnect, sqlstate
```

- [ ] **Step 4: Add `_lost_connection`.** After `with_post_finish_lock`
  (it ends at line 282, `return wrapped`), add:

```python
def _lost_connection(error: BaseException) -> bool:
    """Tell whether a results write failed because the database is gone.

    A disconnect (see is_disconnect) counts, and so does a failed
    connect: SQLAlchemy raises it as an OperationalError whose driver
    error has no SQLSTATE ("Connection refused", the connect deadline's
    "timeout expired"), without marking it invalidated. Once a
    disconnect has emptied the pool, that is what every flush meets
    until PostgreSQL answers again.

    error: the error that ended the write.

    return: whether the batch was lost to the connection, not to its
        data.

    """
    return is_disconnect(error) or (
        isinstance(error, OperationalError) and sqlstate(error.orig) is None)
```

- [ ] **Step 5: Wrap the session block of `_write_results_sync`.**
  - In its docstring, add before the closing quotes (line 1013):

```python

        A lost database connection (see _lost_connection) drops the
        batch with one ERROR line; the sweeper redoes its results.
```

  - Insert, between the `by_object_and_type[t].append(...)` loop (line
    1033) and `with SessionGen() as session:` (line 1035), these lines,
    leaving one blank line before `try:` and one before the new `def`:

```python
        try:
            self._write_results_in_session(by_object_and_type)
        except Exception as error:
            # Not the results' fault: drop the batch with one line, and
            # the sweeper redoes it (see _lost_connection).
            if not _lost_connection(error):
                raise
            logger.error(
                "Lost the database connection while writing worker "
                "results (%s); the sweeper will redo them within about 2 "
                "minutes.", describe_error(error))
            return

        logger.info("Done")

    def _write_results_in_session(
        self,
        by_object_and_type: dict[
            tuple[str, int, int, bool], list[tuple[ESOperation, Result]]],
    ) -> None:
        """Write the results of _write_results_sync() in one session.

        Called only by _write_results_sync(), holding post_finish_lock;
        the same rule applies: nothing in this call graph may await.

        by_object_and_type: the results, grouped by (operation type,
            object id, dataset id, archive_sandbox).

        """
```

  - Leave the `with SessionGen() as session:` block (old lines
    1035-1167) exactly as it is, at the same indentation: it is now the
    body of `_write_results_in_session`. It uses only `self`,
    `by_object_and_type` and the session.
  - Delete the old last two lines of `_write_results_sync` (line 1168,
    blank, and line 1169, `logger.info("Done")`): "Done" is now logged
    by `_write_results_sync` itself, so that
    `cmstestsuite/loadtest/flushstats.py` (`write_results(_sync)?\] Done`)
    still matches it.

- [ ] **Step 6: Stop the per-row loop at a lost connection.** In
  `write_results_one_object_and_type`, add to the docstring, before the
  closing quotes:

```python

        raise (sqlalchemy.exc.DBAPIError): on a lost connection (see
            cms.db.connect_options.is_disconnect), which stops the loop.
        raise (sqlalchemy.exc.PendingRollbackError): if the transaction
            can no longer be used, which stops the loop too.
```

  and replace the `except Exception:` clause (old lines 1196-1203) with:

```python
            except Exception as error:
                # A lost connection, or a transaction it left unusable,
                # is not this row's fault, and no later row can succeed:
                # stop, and let _write_results_sync drop the batch.
                if is_disconnect(error) \
                        or isinstance(error, PendingRollbackError):
                    raise
                # Defend against any exception. A poisonous results that fails
                # here is attempted again without limits, thus can enter in
                # all batches to write. Without the catch-all, it will prevent
                # the whole batch to be written over and over. See issue #888.
                logger.error(
                    "Unexpected exception while inserting worker result.",
                    exc_info=True)
```

- [ ] **Step 7: Run the tests and see them pass.**
  - Run (asyncio group): `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py cmstestsuite/unit_tests/service/write_results_creates_result_test.py cmstestsuite/unit_tests/service/evaluationservice_failures_test.py cmstestsuite/unit_tests/service/twophase_write_results_test.py cmstestsuite/unit_tests/service/subtaskdag_write_results_test.py cmstestsuite/unit_tests/service/evaluationservice_lock_order_test.py cmstestsuite/unit_tests/service/evaluationservice_loop_actions_test.py cmstestsuite/unit_tests/service/flushingdict_test.py cmstestsuite/unit_tests/service/EvaluationService_test.py -q`
  - Expected: all pass.
  - Run (gevent group, its own process): `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py cmstestsuite/unit_tests/service/twophase_reenqueue_test.py -q`
  - Expected: all pass.

- [ ] **Step 8: Lint.**
  - Run: `uvx -q pyflakes cms/service/EvaluationService.py cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py`
  - Expected: no output.
  - Run: `uvx -q pycodestyle --max-line-length=79 cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py`
  - Expected: no output. For `EvaluationService.py`, compare with HEAD,
    which does not have the task's changes yet:
    `diff <(git show HEAD:cms/service/EvaluationService.py | uvx -q pycodestyle --max-line-length=79 - | sed -E 's/^[^:]*:[0-9]+:[0-9]+: //' | sort) <(uvx -q pycodestyle --max-line-length=79 cms/service/EvaluationService.py | sed -E 's/^[^:]*:[0-9]+:[0-9]+: //' | sort)`
    prints nothing new on the `>` side.

- [ ] **Step 9: Commit.**

```bash
git add cms/service/EvaluationService.py \
    cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py
git commit -F - <<'EOF'
fix(service): drop an ES results batch with one line on a lost connection

After a disconnect, every later row of a flush failed on its own with
an ERROR, and the next lookup aborted the flush with "Unexpected error
while flushing". The per-row loop now stops at a disconnect or an
unusable transaction, and _write_results_sync turns a lost connection
(a disconnect, or a connect that failed) into one ERROR; the sweeper
redoes the results. Other row errors keep the per-row handling.

Refs #39

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task 5: The plain sweeper heals the phase-2 window (8a)

**Files:**
- Modify: `cms/service/esoperations.py:33` (import `func`) and after
  `:583` (new `get_submission_results_to_finalize`, between
  `get_submission_results_to_evaluate` and `any_dataset_declares_dependencies`)
- Modify: `cms/service/EvaluationService.py:58-63` (the `.esoperations` import),
  `:487-492` (finalization of `submission_enqueue_operations`), and the
  plain branch of `_missing_operations_sync` (`:560-564`)
- Modify: `cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py`
  (new class at the end, before `if __name__ == "__main__":`)

**Interfaces:**
- Consumes: `EvaluationServiceDatabaseTestCase` and its attributes
  (Task 4).
- Produces:
  - `cms.service.esoperations.get_submission_results_to_finalize(session: Session, contest_id: int | None = None) -> list[SubmissionResult]`;
  - `EvaluationService._finalize_complete_result(self, submission_result: SubmissionResult) -> None`.

- [ ] **Step 1: Write the failing tests.** In
  `evaluationservice_disconnect_test.py`, add to the imports:

```python
from cms.db import SubmissionResult
from cms.service.esoperations import get_submission_results_to_finalize
```

  (merge `SubmissionResult` into the existing `from cms.db import ...`
  line, and `get_submission_results_to_finalize` into the existing
  `from cms.service.esoperations import ESOperation`), then add this class
  before `if __name__ == "__main__":`:

```python
class TestPlainSweepFinalizesCutResults(EvaluationServiceDatabaseTestCase):
    """8a: a write cut between the two commits of _write_results_sync
    leaves every evaluation stored but no outcome.

    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # The plain path: no two-phase grading (and no dependencies).
        patcher = patch.object(config.global_, "two_phase_evaluation", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _add_evaluations(self, submission_result: SubmissionResult,
                         testcases: list) -> None:
        for testcase in testcases:
            self.add_evaluation(submission_result, testcase)
        self.session.commit()

    async def test_a_complete_result_without_outcome_is_finalized(self):
        self._add_evaluations(self.submission_result, self.testcases)
        submission_id, dataset_id = self.submission.id, self.dataset.id
        with self.assertLogs("cms.service.EvaluationService",
                             "WARNING") as logs:
            await self.service._missing_operations()
        self.assertTrue(self._load_result().evaluated())
        self.assertEqual(
            [record.getMessage() for record in logs.records],
            ["Finalized result %d(%d): all its evaluations were stored "
             "but its outcome was not (a write was cut)."
             % (submission_id, dataset_id)])
        self.service._threadsafe_notify_scoring_service \
            .assert_called_once_with(submission_id=submission_id,
                                     dataset_id=dataset_id)

    async def test_a_result_with_a_missing_evaluation_is_left_alone(self):
        self._add_evaluations(self.submission_result, self.testcases[:-1])
        with self.assertNoLogs("cms.service.EvaluationService", "WARNING"):
            await self.service._missing_operations()
        self.assertFalse(self._load_result().evaluated())
        self.service._threadsafe_notify_scoring_service.assert_not_called()

    async def test_only_unfinalized_complete_results_of_the_contest(self):
        self._add_evaluations(self.submission_result, self.testcases)
        task = self.dataset.task
        # The same, already finalized: not selected.
        _, (finished,) = self.add_submission_with_results(
            task, self.submission.participation, True)
        self._add_evaluations(finished, self.testcases)
        finished.set_evaluation_outcome()
        # Complete and without outcome, in another contest.
        other_contest = self.add_contest()
        other_task = self.add_task(contest=other_contest)
        other_dataset = self.add_dataset(task=other_task)
        other_task.active_dataset = other_dataset
        other_testcase = self.add_testcase(other_dataset)
        _, (other,) = self.add_submission_with_results(
            other_task, self.add_participation(contest=other_contest), True)
        self.add_evaluation(other, other_testcase)
        self.session.commit()

        def keys(contest_id: int | None) -> list[tuple[int, int]]:
            return sorted(
                (result.submission_id, result.dataset_id)
                for result in get_submission_results_to_finalize(
                    self.session, contest_id))

        mine = (self.submission.id, self.dataset.id)
        theirs = (other.submission_id, other.dataset_id)
        self.assertEqual(keys(task.contest_id), [mine])
        self.assertEqual(keys(other_contest.id), [theirs])
        self.assertEqual(keys(None), sorted([mine, theirs]))
```

- [ ] **Step 2: Run the tests and see them fail.**
  - Run: `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py -q`
  - Expected: a collection error,
    `ImportError: cannot import name 'get_submission_results_to_finalize'`.

- [ ] **Step 3: Add the selection.** In `cms/service/esoperations.py`,
  change line 33 to `from sqlalchemy import case, func, literal, select`,
  and add after `get_submission_results_to_evaluate`:

```python
def get_submission_results_to_finalize(
    session: Session, contest_id: int | None = None
) -> list[SubmissionResult]:
    """Return the results that have every evaluation but no outcome.

    EvaluationService writes the evaluations and the outcome in two
    commits; a write cut between them (or a crash there) leaves such a
    result. No evaluation operation finalizes it on the plain sweep
    path; submission_enqueue_operations() does on the gated one. The
    filters are those of get_submission_results_to_evaluate(), plus one
    Evaluation row per testcase of the dataset.

    session: the database session to use.
    contest_id: the contest whose results to look at. If None, look at
        every contest.

    return: the submission results to finalize.

    """
    if contest_id is None:
        contest_filter = literal(True)
    else:
        contest_filter = Task.contest_id == contest_id

    evaluation_count = (
        select(func.count(Evaluation.id))
        .where(Evaluation.submission_id == SubmissionResult.submission_id)
        .where(Evaluation.dataset_id == SubmissionResult.dataset_id)
        .correlate(SubmissionResult)
        .scalar_subquery())
    testcase_count = (
        select(func.count(Testcase.id))
        .where(Testcase.dataset_id == SubmissionResult.dataset_id)
        .correlate(SubmissionResult)
        .scalar_subquery())
    return session.execute(
        select(SubmissionResult)
        .join(SubmissionResult.dataset)
        .join(SubmissionResult.submission)
        .join(Submission.task)
        .filter(
            contest_filter &
            (FILTER_SUBMISSION_DATASETS_TO_JUDGE) &
            (FILTER_SUBMISSION_RESULTS_TO_EVALUATE) &
            (evaluation_count == testcase_count))
    ).scalars().all()
```

  `Evaluation`, `Testcase`, `Submission`, `SubmissionResult` and `Task`
  are already imported there (line 35-36).

- [ ] **Step 4: Extract the finalization.** In
  `cms/service/EvaluationService.py`, add
  `get_submission_results_to_finalize, \` to the `.esoperations` import,
  on its own line after `any_dataset_declares_dependencies, \` (line 59).
  In `submission_enqueue_operations`, replace the `else:` branch of lines
  487-492:

```python
                else:
                    logger.info("Result %d(%d) has already all evaluations, "
                                "finalizing it.", submission.id, dataset.id)
                    submission_result.set_evaluation_outcome()
                    submission_result.sa_session.commit()
                    self.evaluation_ended(submission_result)
```

  with:

```python
                else:
                    self._finalize_complete_result(submission_result)
```

  and add this method right after `submission_enqueue_operations` (after
  its `return new_operations`):

```python
    def _finalize_complete_result(
        self, submission_result: SubmissionResult
    ) -> None:
        """Set the outcome of a result that has every evaluation.

        Commit it, then do what follows an evaluation (evaluation_ended:
        notify ScoringService). Called holding post_finish_lock, by
        submission_enqueue_operations() and by the plain sweep of
        _missing_operations_sync().

        submission_result: a result with an evaluation for each testcase
            of its dataset, and no outcome yet.

        """
        logger.info("Result %d(%d) has already all evaluations, "
                    "finalizing it.", submission_result.submission_id,
                    submission_result.dataset_id)
        submission_result.set_evaluation_outcome()
        submission_result.sa_session.commit()
        self.evaluation_ended(submission_result)
```

- [ ] **Step 5: Heal on the plain sweep.** In `_missing_operations_sync`,
  replace the plain branch (lines 560-564):

```python
            else:
                for operation, priority, timestamp in \
                        get_submissions_operations(session, self.contest_id):
                    if self._enqueue_sync(operation, priority, timestamp):
                        counter += 1
```

  with:

```python
            else:
                for operation, priority, timestamp in \
                        get_submissions_operations(session, self.contest_id):
                    if self._enqueue_sync(operation, priority, timestamp):
                        counter += 1

                # A write cut between the two commits of
                # _write_results_sync (or an ES crash there) leaves a
                # result with every evaluation but no outcome, which no
                # operation finalizes on this path; the gated path above
                # does it in submission_enqueue_operations().
                # post_finish_lock keeps this from seeing the normal gap
                # between those commits.
                for submission_result in get_submission_results_to_finalize(
                        session, self.contest_id):
                    self._finalize_complete_result(submission_result)
                    logger.warning(
                        "Finalized result %d(%d): all its evaluations were "
                        "stored but its outcome was not (a write was cut).",
                        submission_result.submission_id,
                        submission_result.dataset_id)
```

- [ ] **Step 6: Run the tests and see them pass.**
  - Run (asyncio group): `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py cmstestsuite/unit_tests/service/esoperations_test.py cmstestsuite/unit_tests/service/twophase_esoperations_test.py cmstestsuite/unit_tests/service/subtaskdag_esoperations_test.py cmstestsuite/unit_tests/service/evaluationservice_failures_test.py cmstestsuite/unit_tests/service/twophase_write_results_test.py cmstestsuite/unit_tests/service/subtaskdag_write_results_test.py cmstestsuite/unit_tests/service/twophase_gate_consistency_test.py cmstestsuite/unit_tests/service/twophase_e2e_test.py cmstestsuite/unit_tests/service/EvaluationService_test.py -q`
  - Expected: all pass.
  - Run (gevent group, its own process): `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py cmstestsuite/unit_tests/service/twophase_reenqueue_test.py -q`
  - Expected: all pass (they exercise the gated finalization that now
    goes through `_finalize_complete_result`).

- [ ] **Step 7: Lint.**
  - Run: `uvx -q pyflakes cms/service/EvaluationService.py cms/service/esoperations.py cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py`
  - Expected: no output.
  - Run: `uvx -q pycodestyle --max-line-length=79 cms/service/esoperations.py cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py`
    and the HEAD comparison of Task 4 Step 8 for `EvaluationService.py`.
  - Expected: nothing new.

- [ ] **Step 8: Commit.**

```bash
git add cms/service/EvaluationService.py cms/service/esoperations.py \
    cmstestsuite/unit_tests/service/evaluationservice_disconnect_test.py
git commit -F - <<'EOF'
fix(service): finalize results whose outcome write was cut

ES commits a result's evaluations and its outcome separately. A
disconnect or a crash between the two left every evaluation stored and
no outcome, and in a contest without gated datasets nothing ever
finalized it, so the submission stayed "Evaluating". The plain sweep
now finalizes such results through the same helper as the gated path,
with a WARNING.

Refs #39

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task 6: Bound the fork DDL updaters' lock waits to 5 s

**Files:**
- Create: `cmscontrib/updaters/fork_ddl.py`
- Modify: `cmscontrib/updaters/fork_multi_contest.py:27` (import) and
  `:81-91` (`apply_fork_multi_contest_update`)
- Modify: `cmscontrib/updaters/fork_activity_intervals.py:26` (import) and
  `:63-73` (`apply_fork_activity_intervals_update`)
- Modify: `cmscontrib/SetupDB.py:30-37` (imports) and `:155-168`
  (`setup_db`)
- Modify: `cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py` (gevent
  group)

**Interfaces:**
- Consumes: `custom_psycopg2_connection` with the defaults of Task 1.
- Produces (in `cmscontrib/updaters/fork_ddl.py`):
  - `class SchemaLockTimeout(Exception)`, built as
    `SchemaLockTimeout(tables: str)`, whose `str()` is the exact
    `cmsSetupDB` message of the Global Constraints;
  - `apply_fork_sql(sql: str, tables: str) -> None`.

- [ ] **Step 1: Write the failing tests.** In
  `cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py`, replace the
  imports (lines 21-31) with:

```python
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import Admin, Contest, custom_psycopg2_connection
from cmscommon.crypto import validate_password
from cmscontrib.SetupDB import ensure_first_admin
from cmscontrib.updaters.fork_ddl import SchemaLockTimeout, apply_fork_sql


# The source tree, for scripts/cmsSetupDB; the tests run from it, in the
# CI image too.
REPO_ROOT = Path(__file__).resolve().parents[3]

LOCK_MESSAGE = (
    "cmsSetupDB could not lock %s within 5 s: another session is using "
    "them. Nothing was changed. Find that session with the holder query "
    "in the contest-day runbook (\"When the database misbehaves\"), wait "
    "for it to finish or end it, then run the setup again.")
```

  and add before `if __name__ == "__main__":`:

```python
class TestSchemaLockTimeout(DatabaseMixin, unittest.TestCase):
    """The fork's schema updates give up on a locked table (#39)."""

    def setUp(self):
        super().setUp()
        # Another session; psycopg2 does not autocommit, so what it runs
        # stays in an open transaction.
        self.holder = custom_psycopg2_connection()
        self.addCleanup(self.holder.close)

    def _hold(self, statement: str) -> None:
        self.holder.cursor().execute(statement)

    @staticmethod
    def _run_setup_db() -> tuple[subprocess.CompletedProcess, float]:
        """Run cmsSetupDB as db-init does; return it and its duration."""
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            path for path in (str(REPO_ROOT), env.get("PYTHONPATH"))
            if path)
        # No first admin to create: ensure_first_admin() only warns.
        env.pop("CMS_ADMIN_USER", None)
        env.pop("CMS_ADMIN_PASSWORD", None)
        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "cmsSetupDB")],
            env=env, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=60)
        return completed, time.monotonic() - started

    def test_apply_fork_sql_gives_up_after_5_seconds(self):
        self._hold("SELECT 1 FROM contests LIMIT 1")
        started = time.monotonic()
        with self.assertRaises(SchemaLockTimeout) as caught:
            apply_fork_sql("ALTER TABLE public.contests "
                           "ALTER COLUMN active DROP DEFAULT", "contests")
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 5)
        self.assertLess(elapsed, 8)
        self.assertEqual(str(caught.exception), LOCK_MESSAGE % "contests")

    def test_setup_db_exits_1_when_a_session_read_contests(self):
        # F4's holder: it read contests and stays idle in its transaction.
        self._hold("SELECT 1 FROM contests LIMIT 1")
        completed, elapsed = self._run_setup_db()
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn(LOCK_MESSAGE % "contests or ranking_groups",
                      completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertGreaterEqual(elapsed, 5)
        self.assertLess(elapsed, 15)

    def test_setup_db_exits_1_when_a_session_writes_activity(self):
        # A reader would not block it: CREATE INDEX IF NOT EXISTS takes
        # a SHARE lock, which only writers block (ROW EXCLUSIVE, as the
        # activity recorder's inserts take).
        self._hold("LOCK TABLE activity_intervals IN ROW EXCLUSIVE MODE")
        completed, elapsed = self._run_setup_db()
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn(LOCK_MESSAGE % "activity_intervals", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertLess(elapsed, 15)

    def test_setup_db_succeeds_again_once_the_session_ended(self):
        self._hold("SELECT 1 FROM contests LIMIT 1")
        self.holder.rollback()
        completed, _ = self._run_setup_db()
        self.assertEqual(completed.returncode, 0, completed.stderr)
```

- [ ] **Step 2: Run the tests and see them fail.**
  - Run: `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py -q`
  - Expected: a collection error,
    `ModuleNotFoundError: No module named 'cmscontrib.updaters.fork_ddl'`.

- [ ] **Step 3: Create `cmscontrib/updaters/fork_ddl.py`.**

```python
# (fork header)

"""Apply a fork schema update without queueing CMS behind its locks.

An idempotent ALTER TABLE or CREATE INDEX still takes its lock when it
changes nothing, and while it waits for that lock every later query on
the table waits behind it: every Contest Web Server freezes. So each
update gives up after 5 s and changes nothing, and cmsSetupDB tells the
operator what to do (#39). There is no retry: each try would freeze the
tables for 5 s again, and running the setup again is safe.

"""

import psycopg2.errors

from cms.db import custom_psycopg2_connection


class SchemaLockTimeout(Exception):
    """A fork schema update could not lock its tables within 5 s."""

    def __init__(self, tables: str):
        """Create the error, with the message cmsSetupDB prints.

        tables: the tables the update locks, as the message names them
            (PostgreSQL's lock-timeout error names none).

        """
        super().__init__(
            "cmsSetupDB could not lock %s within 5 s: another session is "
            "using them. Nothing was changed. Find that session with the "
            "holder query in the contest-day runbook (\"When the database "
            "misbehaves\"), wait for it to finish or end it, then run the "
            "setup again." % tables)
        self.tables = tables


def apply_fork_sql(sql: str, tables: str) -> None:
    """Apply one fork schema update, in one transaction.

    sql: the update's statements.
    tables: the tables they lock, for the message of SchemaLockTimeout.

    raise (SchemaLockTimeout): if a lock was not granted within 5 s;
        the transaction is rolled back, so nothing is changed.

    """
    conn = custom_psycopg2_connection()
    try:
        cursor = conn.cursor()
        # The same transaction as the update: psycopg2 does not
        # autocommit, and SET LOCAL ends with the transaction.
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        try:
            cursor.execute(sql)
        except psycopg2.errors.LockNotAvailable as error:
            raise SchemaLockTimeout(tables) from error
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Use it in the updaters.** In
  `cmscontrib/updaters/fork_multi_contest.py`, replace
  `from cms.db import custom_psycopg2_connection` (line 27) with
  `from cmscontrib.updaters.fork_ddl import apply_fork_sql`, and
  `apply_fork_multi_contest_update` (lines 81-91) with:

```python
def apply_fork_multi_contest_update() -> None:
    """Apply the fork's idempotent schema update to the database.

    raise (SchemaLockTimeout): if another session keeps contests or
        ranking_groups locked for 5 s; nothing is changed then.

    """
    apply_fork_sql(FORK_MULTI_CONTEST_SQL, "contests or ranking_groups")
```

  In `cmscontrib/updaters/fork_activity_intervals.py`, replace
  `from cms.db import custom_psycopg2_connection` (line 26) with
  `from cmscontrib.updaters.fork_ddl import apply_fork_sql`, and
  `apply_fork_activity_intervals_update` (lines 63-73) with:

```python
def apply_fork_activity_intervals_update() -> None:
    """Apply FORK_ACTIVITY_INTERVALS_SQL to the configured database.

    raise (SchemaLockTimeout): if another session keeps
        activity_intervals locked for 5 s (a writer: CREATE INDEX IF
        NOT EXISTS takes a SHARE lock); nothing is changed then.

    """
    apply_fork_sql(FORK_ACTIVITY_INTERVALS_SQL, "activity_intervals")
```

  The `FORK_*_SQL` constants do not change: `schema_diff_test.py` and
  `rankinggroup_visibility_test.py` run them directly.

- [ ] **Step 5: Turn the timeout into the message and exit 1.** In
  `cmscontrib/SetupDB.py`, add after the two updater imports (line 35):

```python
from cmscontrib.updaters.fork_ddl import SchemaLockTimeout
```

  and replace `setup_db` (lines 155-168) with:

```python
def setup_db() -> bool:
    """Initialize or update DB schema, create first admin, offer sample contest.

    return: True on success, False if a required step failed; a schema
        update that could not lock its tables within 5 s is one, and
        it changed nothing.

    """
    init_db()  # raises on failure; return value is always True
    # init_db creates missing tables but never alters existing ones.
    try:
        apply_fork_multi_contest_update()
        apply_fork_activity_intervals_update()
    except SchemaLockTimeout as error:
        # One line for the operator, no traceback; scripts/cmsSetupDB
        # exits 1 on False.
        logger.critical("%s", error)
        return False
    if not ensure_first_admin():
        return False
    offer_sample_contest()
    return True
```

  `scripts/cmsSetupDB` does not change: `main()` already returns 1 when
  `setup_db()` returns False.

- [ ] **Step 6: Run the tests and see them pass.**
  - Run (gevent group, its own process): `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py -q`
  - Expected: all pass, in about 30 s.
  - Run (asyncio group, another process): `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/schema_diff_test.py cmstestsuite/unit_tests/db/rankinggroup_visibility_test.py -q`
  - Expected: all pass, except that locally `schema_diff_test.py` fails
    with "server version mismatch" from `pg_dump` (the host's Homebrew
    `pg_dump` is 15, `cms-test-pg` is 16), with or without this task. It
    runs in CI (Task 10, V0), and this task does not change the
    `FORK_*_SQL` it compares.

- [ ] **Step 7: Lint.**
  - Run: `uvx -q pyflakes cmscontrib/updaters/fork_ddl.py cmscontrib/updaters/fork_multi_contest.py cmscontrib/updaters/fork_activity_intervals.py cmscontrib/SetupDB.py cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py`
  - Run: `uvx -q pycodestyle --max-line-length=79 cmscontrib/updaters/fork_ddl.py cmscontrib/updaters/fork_multi_contest.py cmscontrib/updaters/fork_activity_intervals.py cmscontrib/SetupDB.py cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py`
  - Expected: no output from pyflakes; no warning from pycodestyle on a
    line this task added or changed.

- [ ] **Step 8: Commit.**

```bash
git add cmscontrib/updaters/fork_ddl.py \
    cmscontrib/updaters/fork_multi_contest.py \
    cmscontrib/updaters/fork_activity_intervals.py cmscontrib/SetupDB.py \
    cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py
git commit -F - <<'EOF'
feat(setup): give up on a locked table after 5 s in the fork updaters

The idempotent DDL of the fork updaters takes ACCESS EXCLUSIVE on
contests (and SHARE on activity_intervals) even when it changes
nothing, so a deploy that met an open transaction froze every Contest
Web Server behind its lock. Each updater now sets lock_timeout = 5s in
its own transaction; cmsSetupDB then prints one message naming the
tables and exits 1, with nothing changed.

Refs #39

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task 7: The contest-day runbook and its Spanish catalog

**Files:**
- Modify: `docs/contest-day.md` (line 75 under "Before the start"; line
  116 under "During the contest"; a new section between line 143 and
  "## At the end" at line 145; line 194 and the end of the table of "When
  something goes wrong")
- Modify: `docs/locale/es/LC_MESSAGES/contest-day.po`

**Interfaces:**
- Consumes: the exact messages of Tasks 4 and 6, and the kill-switch
  suffix and post-deploy check of the Global Constraints.
- Produces: the headings "When the database misbehaves", "PostgreSQL
  does not answer", "The holder query" (quoted by the `cmsSetupDB`
  message and by Task 10's V1 step), and the provisional sentence
  `Check with ./status.sh whether the running version still serves.`,
  which Task 10 (V1) replaces.

Rules for this task: keep code spans and log strings verbatim in the
`.po` (`docs/check_translations.py` checks every code span); every log
string in the English text is a code span for that reason; no `#, fuzzy`
entry may remain; follow `docs/locale/es/GLOSSARY.md` (address the reader
as "tú", "registros" for logs, "despliegue", "contenedor", "ranking",
"falla", interface labels such as **Regenerate**, **Logs** and
**Overview** unchanged); escape `"` as `\"` inside a `msgstr`.

- [ ] **Step 1: Edit "Before the start".** Replace line 75
  (`- Do not rebuild or update the deployment on the day.`) with:

```markdown
- Do not rebuild or update the deployment on the day. If you must deploy in
  an emergency:
  - Do not deploy while `./export.sh` or another CLI tool is running.
  - The deploy can stop at `db-init` with
    `cmsSetupDB could not lock … within 5 s`: another session is using a
    table that the update changes, and nothing is changed. Check with
    `./status.sh` whether the running version still serves. Find that
    session with the holder query (see "When the database misbehaves"),
    wait for it to finish or end it, then run `./up.sh` again.
  - Start any SQL you apply by hand with `SET lock_timeout = '5s';`, so
    that it gives up instead of making the contest wait behind its lock.
```

- [ ] **Step 2: Edit the Regenerate advice of "During the contest".**
  Replace lines 116-118 with:

```markdown
- **A scoreboard does not match the database:** press **Regenerate** on its
  ranking group (see "When a scoreboard is wrong" in
  [multi-contest.md](multi-contest.md)). Not while database errors
  continue: a failed regenerate can leave the scoreboard empty.
```

- [ ] **Step 3: Add the new section.** Insert after line 143 (the
  "Extending a contest" item) and its blank line, before `## At the end`:

~~~~markdown
## When the database misbehaves

CMS does not wait forever for PostgreSQL. A new connection that gets no
answer fails after 10 seconds with `timeout expired`. A call on a
connection that died fails within about a minute, with
`server closed the connection unexpectedly`,
`could not receive data from server` or `Connection timed out`. Pages then
show an error instead of hanging, and every service reconnects by itself
once PostgreSQL answers again.

The driver's error text is in `./logs.sh --tail 2000 cms`. The **Logs**
table on **Overview** shows only the message of each line, not the error
text that follows it.

### PostgreSQL does not answer

1. Check from inside the `cms` container, with the URL of `.env` without
   its `+psycopg2` (the command strips it, as `contest.sh` does):

   ```bash
   docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
       exec -T cms bash -c 'pg_isready -t 3 -d "${CMS_DB_URL/+psycopg2/}"'
   ```

   Without `-d`, `pg_isready` checks the container's own socket instead.
2. Then check PostgreSQL itself on its host: its status, its log and the
   free disk space.
3. Do not restart CMS while PostgreSQL does not answer, and do not edit
   `.env` because a log line says
   `Is the connection string in cms.toml correct?`.
4. Once PostgreSQL answers, CMS recovers by itself: pages within a
   minute, grading within about 2 minutes and scores within about 6
   minutes. Then run `./status.sh`: every program must be `RUNNING`.
5. If grading or scoring has not resumed 3 minutes after PostgreSQL
   answers, run the holder query (below) and end every session whose
   `state` is `idle in transaction` and whose `open_for` is longer than
   the time since the outage began. After a network cut, PostgreSQL can
   keep such a transaction, and its locks, for about 2 hours.
6. Do not press **Regenerate** while database errors continue: a failed
   regenerate can leave a scoreboard empty until it is pressed again.

### PostgreSQL is stalled

Pages and grading freeze, new connections fail with `timeout expired`,
and `pg_isready` reports `no response`. PostgreSQL itself is stuck,
usually on its disk. No CMS timeout can cut this, and restarting CMS does
not help: check the disk and the PostgreSQL log on its host.

### The holder query

It lists the open transactions, the oldest first, with the program that
holds each one (`application_name`, for example `cmsContestWebServer 2`)
and the sessions that wait for it (`blocked_by`). Run it from the `cms`
container:

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
    exec -T cms bash -c 'psql "${CMS_DB_URL/+psycopg2/}" -c "SELECT pid, application_name, state, wait_event_type, now() - xact_start AS open_for, pg_blocking_pids(pid) AS blocked_by, left(query, 60) AS query FROM pg_stat_activity WHERE datname = current_database() AND xact_start IS NOT NULL ORDER BY xact_start;"'
```

Or run the same `SELECT` with `psql` on the database host.

To end a session, run `SELECT pg_terminate_backend(<pid>);` the same way.
The `cms` role can end its own sessions; ending another role's session
needs a superuser.

### Switching the timeouts off

If these errors show up under normal load while PostgreSQL is healthy,
add this suffix to `CMS_DB_URL` in `.env`, with `&` instead of its `?` if
the URL already has a `?`:

```text
?connect_timeout=0&keepalives_idle=7200&keepalives_interval=75&keepalives_count=9&tcp_user_timeout=0
```

Then run `./up.sh` and choose no rebuild (1): it recreates the
containers, so every service restarts. CMS then waits for PostgreSQL
without a limit again, as before. The suffix does not switch off the
5-second lock limit of `cmsSetupDB` (see "Before the start").

### Checking the settings after a deploy

A new connection must get these settings. In the `cms` container:

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
    exec -T cms python3 -c 'import os, socket; from cms.db import custom_psycopg2_connection; c = custom_psycopg2_connection(); s = socket.socket(fileno=os.dup(c.fileno())); print("TCP_USER_TIMEOUT", s.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT)); s.close(); k = c.cursor(); k.execute("SHOW application_name"); print("application_name", k.fetchone()[0])'
```

It must print `TCP_USER_TIMEOUT 60000` (or the value that `CMS_DB_URL`
sets) and a non-empty `application_name`: here `-c`, the name of a
`python3 -c` program. Then
`./logs.sh --tail 2000 cms | grep -E 'timeout expired|closed the connection|Lost the database'`
must print nothing.
~~~~

- [ ] **Step 4: Edit the table of "When something goes wrong".** Replace
  the cell `**Regenerate** on its ranking group` of line 194 with
  `**Regenerate** on its ranking group, unless database errors continue`,
  and append these rows at the end of the table:

```markdown
| Pages show an error after about 10 seconds, and the logs say `timeout expired` | A new database connection got no answer: follow "PostgreSQL does not answer" | "When the database misbehaves" (above) |
| The logs say `server closed the connection unexpectedly`, `could not receive data from server`, `Connection timed out` or `Lost the database connection while writing worker results` | Once, from one service: nothing, it reconnects by itself. From several services at once: follow "PostgreSQL does not answer" | "When the database misbehaves" (above) |
| A deploy stops at `db-init` with `cmsSetupDB could not lock … within 5 s` | Find the session with the holder query, wait for it to finish or end it, then run `./up.sh` again | "When the database misbehaves" (above) |
```

- [ ] **Step 5: Create the docs venv** (outside the repository, once):
  - Run: `test -x /home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-docs-venv/bin/sphinx-build || (uv venv -q --python 3.12 /home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-docs-venv && uv pip install -q --python /home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-docs-venv/bin/python -r docs/requirements.txt pytest==9.1.1)`

- [ ] **Step 6: Update the catalog.** In one Bash call:

```bash
rm -rf /tmp/db39-pot && \
/home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-docs-venv/bin/sphinx-build -q -W --keep-going -b gettext docs /tmp/db39-pot && \
/home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-docs-venv/bin/sphinx-intl update -p /tmp/db39-pot -l es -d docs/locale && \
git diff --name-only docs/locale | grep -v 'contest-day\.po$' | xargs -r git checkout -- && \
git status --short docs
```

  Expected: only `docs/contest-day.md` and
  `docs/locale/es/LC_MESSAGES/contest-day.po` changed (the other
  catalogs got only header changes, which the `git checkout` drops).

- [ ] **Step 7: Translate.** In `contest-day.po`, give each entry below
  its `msgstr`, with `"` escaped as `\"`. For the entries that
  `sphinx-intl` marked `#, fuzzy` (the changed paragraphs), replace the
  old translation and delete the `#, fuzzy` line and the `#|` lines
  above the entry.

  | English (msgid) | Spanish (msgstr) |
  |---|---|
  | Do not rebuild or update the deployment on the day. If you must deploy in an emergency: | No reconstruyas ni actualices el despliegue el día de la competencia. Si tienes que desplegar por una emergencia: |
  | Do not deploy while `./export.sh` or another CLI tool is running. | No despliegues mientras `./export.sh` u otra herramienta de línea de comandos esté corriendo. |
  | The deploy can stop at `db-init` with `cmsSetupDB could not lock … within 5 s`: another session is using a table that the update changes, and nothing is changed. Check with `./status.sh` whether the running version still serves. Find that session with the holder query (see "When the database misbehaves"), wait for it to finish or end it, then run `./up.sh` again. | El despliegue puede detenerse en `db-init` con `cmsSetupDB could not lock … within 5 s`: otra sesión está usando una tabla que la actualización cambia, y no se cambia nada. Revisa con `./status.sh` si la versión en ejecución sigue atendiendo. Encuentra esa sesión con la consulta de sesiones abiertas (ver "Cuando la base de datos falla"), espera a que termine o termínala, y luego ejecuta `./up.sh` de nuevo. |
  | Start any SQL you apply by hand with `SET lock_timeout = '5s';`, so that it gives up instead of making the contest wait behind its lock. | Empieza todo SQL que apliques a mano con `SET lock_timeout = '5s';`, para que se rinda en lugar de hacer esperar a la competencia detrás de su bloqueo. |
  | **A scoreboard does not match the database:** press **Regenerate** on its ranking group (see "When a scoreboard is wrong" in [multi-contest.md](multi-contest.md)). Not while database errors continue: a failed regenerate can leave the scoreboard empty. | **Un ranking no coincide con la base de datos:** presiona **Regenerate** en su grupo de ranking (ver "Cuando un ranking está mal" en [Ejecutar varias competencias a la vez](multi-contest.md)). No mientras sigan los errores de la base de datos: un regenerate fallido puede dejar el ranking vacío. |
  | When the database misbehaves | Cuando la base de datos falla |
  | CMS does not wait forever for PostgreSQL. A new connection that gets no answer fails after 10 seconds with `timeout expired`. A call on a connection that died fails within about a minute, with `server closed the connection unexpectedly`, `could not receive data from server` or `Connection timed out`. Pages then show an error instead of hanging, and every service reconnects by itself once PostgreSQL answers again. | CMS no espera para siempre a PostgreSQL. Una conexión nueva que no recibe respuesta falla a los 10 segundos con `timeout expired`. Una llamada en una conexión que murió falla en un minuto aproximadamente, con `server closed the connection unexpectedly`, `could not receive data from server` o `Connection timed out`. Entonces las páginas muestran un error en lugar de quedarse colgadas, y cada servicio se vuelve a conectar por sí solo en cuanto PostgreSQL responde de nuevo. |
  | The driver's error text is in `./logs.sh --tail 2000 cms`. The **Logs** table on **Overview** shows only the message of each line, not the error text that follows it. | El texto del error del driver está en `./logs.sh --tail 2000 cms`. La tabla **Logs** de **Overview** muestra solo el mensaje de cada línea, no el texto del error que viene después. |
  | PostgreSQL does not answer | PostgreSQL no responde |
  | Check from inside the `cms` container, with the URL of `.env` without its `+psycopg2` (the command strips it, as `contest.sh` does): | Revisa desde dentro del contenedor `cms`, con la URL de `.env` sin su `+psycopg2` (el comando lo quita, como hace `contest.sh`): |
  | Without `-d`, `pg_isready` checks the container's own socket instead. | Sin `-d`, `pg_isready` revisa en cambio el socket del propio contenedor. |
  | Then check PostgreSQL itself on its host: its status, its log and the free disk space. | Luego revisa el propio PostgreSQL en su host: su estado, su registro y el espacio libre en disco. |
  | Do not restart CMS while PostgreSQL does not answer, and do not edit `.env` because a log line says `Is the connection string in cms.toml correct?`. | No reinicies CMS mientras PostgreSQL no responda, y no edites `.env` porque una línea del registro diga `Is the connection string in cms.toml correct?`. |
  | Once PostgreSQL answers, CMS recovers by itself: pages within a minute, grading within about 2 minutes and scores within about 6 minutes. Then run `./status.sh`: every program must be `RUNNING`. | En cuanto PostgreSQL responde, CMS se recupera por sí solo: las páginas en un minuto, la evaluación en unos 2 minutos y los puntajes en unos 6 minutos. Luego ejecuta `./status.sh`: todos los programas deben estar `RUNNING`. |
  | If grading or scoring has not resumed 3 minutes after PostgreSQL answers, run the holder query (below) and end every session whose `state` is `idle in transaction` and whose `open_for` is longer than the time since the outage began. After a network cut, PostgreSQL can keep such a transaction, and its locks, for about 2 hours. | Si la evaluación o el cálculo de puntajes no se reanudó 3 minutos después de que PostgreSQL responda, ejecuta la consulta de sesiones abiertas (abajo) y termina cada sesión cuyo `state` sea `idle in transaction` y cuyo `open_for` sea más largo que el tiempo desde que empezó la caída. Tras un corte de red, PostgreSQL puede conservar una transacción así, con sus bloqueos, durante unas 2 horas. |
  | Do not press **Regenerate** while database errors continue: a failed regenerate can leave a scoreboard empty until it is pressed again. | No presiones **Regenerate** mientras sigan los errores de la base de datos: un regenerate fallido puede dejar un ranking vacío hasta que se presione de nuevo. |
  | PostgreSQL is stalled | PostgreSQL está atascado |
  | Pages and grading freeze, new connections fail with `timeout expired`, and `pg_isready` reports `no response`. PostgreSQL itself is stuck, usually on its disk. No CMS timeout can cut this, and restarting CMS does not help: check the disk and the PostgreSQL log on its host. | Las páginas y la evaluación se congelan, las conexiones nuevas fallan con `timeout expired` y `pg_isready` informa `no response`. El propio PostgreSQL está atascado, normalmente en su disco. Ningún límite de tiempo de CMS puede cortar esto, y reiniciar CMS no ayuda: revisa el disco y el registro de PostgreSQL en su host. |
  | The holder query | La consulta de sesiones abiertas |
  | It lists the open transactions, the oldest first, with the program that holds each one (`application_name`, for example `cmsContestWebServer 2`) and the sessions that wait for it (`blocked_by`). Run it from the `cms` container: | Lista las transacciones abiertas, la más antigua primero, con el programa que tiene cada una (`application_name`, por ejemplo `cmsContestWebServer 2`) y las sesiones que la esperan (`blocked_by`). Ejecútala desde el contenedor `cms`: |
  | Or run the same `SELECT` with `psql` on the database host. | O ejecuta el mismo `SELECT` con `psql` en el host de la base de datos. |
  | To end a session, run `SELECT pg_terminate_backend(<pid>);` the same way. The `cms` role can end its own sessions; ending another role's session needs a superuser. | Para terminar una sesión, ejecuta `SELECT pg_terminate_backend(<pid>);` de la misma forma. El rol `cms` puede terminar sus propias sesiones; terminar la sesión de otro rol requiere un superusuario. |
  | Switching the timeouts off | Desactivar los límites de tiempo |
  | If these errors show up under normal load while PostgreSQL is healthy, add this suffix to `CMS_DB_URL` in `.env`, with `&` instead of its `?` if the URL already has a `?`: | Si estos errores aparecen con carga normal mientras PostgreSQL está sano, agrega este sufijo a `CMS_DB_URL` en `.env`, con `&` en lugar de su `?` si la URL ya tiene un `?`: |
  | Then run `./up.sh` and choose no rebuild (1): it recreates the containers, so every service restarts. CMS then waits for PostgreSQL without a limit again, as before. The suffix does not switch off the 5-second lock limit of `cmsSetupDB` (see "Before the start"). | Luego ejecuta `./up.sh` y elige no reconstruir (1): recrea los contenedores, así que todos los servicios se reinician. CMS vuelve a esperar a PostgreSQL sin límite, como antes. El sufijo no desactiva el límite de 5 segundos de los bloqueos de `cmsSetupDB` (ver "Antes del inicio"). |
  | Checking the settings after a deploy | Verificar la configuración después de un despliegue |
  | A new connection must get these settings. In the `cms` container: | Una conexión nueva debe recibir esta configuración. En el contenedor `cms`: |
  | It must print `TCP_USER_TIMEOUT 60000` (or the value that `CMS_DB_URL` sets) and a non-empty `application_name`: here `-c`, the name of a `python3 -c` program. Then `./logs.sh --tail 2000 cms \| grep -E 'timeout expired\|closed the connection\|Lost the database'` must print nothing. | Debe imprimir `TCP_USER_TIMEOUT 60000` (o el valor que define `CMS_DB_URL`) y un `application_name` no vacío: aquí `-c`, el nombre de un programa `python3 -c`. Luego `./logs.sh --tail 2000 cms \| grep -E 'timeout expired\|closed the connection\|Lost the database'` no debe imprimir nada. |
  | `**Regenerate** on its ranking group, unless database errors continue` (table cell) | **Regenerate** en su grupo de ranking, salvo que sigan los errores de la base de datos |
  | Pages show an error after about 10 seconds, and the logs say `timeout expired` | Las páginas muestran un error tras unos 10 segundos, y los registros dicen `timeout expired` |
  | A new database connection got no answer: follow "PostgreSQL does not answer" | Una conexión nueva a la base de datos no recibió respuesta: sigue "PostgreSQL no responde" |
  | "When the database misbehaves" (above) | "Cuando la base de datos falla" (arriba) |
  | The logs say `server closed the connection unexpectedly`, `could not receive data from server`, `Connection timed out` or `Lost the database connection while writing worker results` | Los registros dicen `server closed the connection unexpectedly`, `could not receive data from server`, `Connection timed out` o `Lost the database connection while writing worker results` |
  | Once, from one service: nothing, it reconnects by itself. From several services at once: follow "PostgreSQL does not answer" | Una vez, de un servicio: nada, se vuelve a conectar por sí solo. De varios servicios a la vez: sigue "PostgreSQL no responde" |
  | A deploy stops at `db-init` with `cmsSetupDB could not lock … within 5 s` | Un despliegue se detiene en `db-init` con `cmsSetupDB could not lock … within 5 s` |
  | Find the session with the holder query, wait for it to finish or end it, then run `./up.sh` again | Encuentra la sesión con la consulta de sesiones abiertas, espera a que termine o termínala, y luego ejecuta `./up.sh` de nuevo |

  In the table above, `\|` stands for a plain `|` (it is escaped only for
  this plan's table); the `.po` has `|`.
  - Then run Step 6's `sphinx-intl update` command once more, so that the
    wrapping of the new entries matches the file, and check with
    `grep -c '#, fuzzy' docs/locale/es/LC_MESSAGES/contest-day.po` that
    it prints `0`.

- [ ] **Step 8: Run the docs CI steps on a copy** (so that no `_build` or
  `.mo` file lands in the worktree). Create
  `/home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-docs-check.sh` with:

```bash
#!/bin/bash
# Run the steps of .github/workflows/docs.yml on a copy of the worktree.
set -u
SRC=/var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts
DST=/home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-docs-check
VENV=/home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-docs-venv
mkdir -p "$DST"
rsync -a --delete --exclude .git --exclude .venv --exclude .superpowers \
    --exclude 'docs/_build' "$SRC/" "$DST/"
cd "$DST" || exit 1
rc=0
"$VENV/bin/sphinx-build" -q -W --keep-going -j auto -b html docs docs/_build/html/en || rc=1
"$VENV/bin/python" -m pytest -p no:cacheprovider docs/tests -q || rc=1
"$VENV/bin/sphinx-build" -q -W --keep-going -b gettext docs docs/_build/gettext || rc=1
"$VENV/bin/python" docs/check_translations.py docs/_build/gettext docs/locale/es/LC_MESSAGES || rc=1
"$VENV/bin/sphinx-build" -q -W --keep-going -j auto -b html -D language=es docs docs/_build/html/es || rc=1
echo "docs check rc=$rc"
exit $rc
```

  - Run: `bash /home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-docs-check.sh`
  - Expected: no warning, no checker line, and `docs check rc=0`.
  - Open `/home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-docs-check/docs/_build/html/es/contest-day.html`
    with `grep -c 'Cuando la base de datos falla'`: at least 2 (the
    heading and its quotes), so the Spanish build used the catalog.

- [ ] **Step 9: Commit.**

```bash
git add docs/contest-day.md docs/locale/es/LC_MESSAGES/contest-day.po
git commit -F - <<'EOF'
docs(contest-day): say what to do when the database misbehaves

Add "When the database misbehaves": the outage entry, a stalled
PostgreSQL, the holder query, the kill switch of the transport
timeouts and the post-deploy check. Add the matching rows of "When
something goes wrong", the emergency-deploy notes on the 5 s DDL bound,
and "not while database errors continue" next to Regenerate. Spanish
catalog updated.

Refs #39

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task 8: The runner-capability check (gate for the fault mode)

**Files:**
- Modify: `.github/workflows/loadtest.yml` (header comment after line 9;
  `pair` job at line 63; `compare` job at lines 210-213; new job
  `runner-check` at the end)

**Interfaces:**
- Consumes: nothing.
- Produces: the branch gating that Task 9's `faults` job relies on:
  `loadtest/faults-*` and `loadtest/runner-check-*` branches run neither
  `pair` nor `compare`.

- [ ] **Step 1: Gate the existing jobs.** In
  `.github/workflows/loadtest.yml`, add after line 9
  (`# (workflow_dispatch would need this file on the default branch first.)`):

```yaml
#
# Two more branch prefixes run something else, and not the pair and
# compare jobs:
# - loadtest/faults-<topic>: the "faults" job, a fault run of the fork
#   (run.sh --faults, see the README), smoke-sized, once per profile;
# - loadtest/runner-check-<topic>: the "runner-check" job, which checks in
#   5 minutes that a runner can drop a container's traffic with nsenter
#   and iptables, and pause the container.
```

  Under `  pair:` (line 63), add as its first key:

```yaml
    if: >-
      ${{ !startsWith(github.ref_name, 'loadtest/faults')
      && !startsWith(github.ref_name, 'loadtest/runner-check') }}
```

  and replace the `if:` of `compare` (line 213, `if: ${{ !cancelled() }}`)
  with:

```yaml
    if: ${{ !cancelled() && needs.pair.result != 'skipped' }}
```

- [ ] **Step 2: Add the `runner-check` job** at the end of the file:

```yaml

  # Can this runner inject the faults of a fault run? It checks nsenter
  # and iptables in a container's network namespace (the DROP rules must
  # block the container's port from outside and leave its loopback
  # alone) and docker pause, and says whether sch_netem is loaded.
  runner-check:
    if: ${{ startsWith(github.ref_name, 'loadtest/runner-check') }}
    runs-on: ubuntu-24.04
    timeout-minutes: 5

    defaults:
      run:
        shell: bash

    steps:
      - name: Check nsenter, iptables and docker pause
        run: |
          dc() { sg docker -c "$(printf '%q ' docker --context default "$@")"; }
          probe() { timeout 3 bash -c "exec 3<>/dev/tcp/$1/5432"; }
          rules() {
            sudo nsenter -t "$pid" -n iptables "$1" INPUT ! -i lo \
                -p tcp --dport 5432 -j DROP
            sudo nsenter -t "$pid" -n iptables "$1" OUTPUT ! -o lo \
                -p tcp --sport 5432 -j DROP
          }
          dc run -d --name netcheck -e POSTGRES_PASSWORD=check \
              docker.io/postgres:17-alpine
          for _ in $(seq 30); do
            dc exec netcheck pg_isready -h 127.0.0.1 -q && break
            sleep 1
          done
          pid=$(dc inspect -f '{{.State.Pid}}' netcheck)
          ip=$(dc inspect -f '{{.NetworkSettings.IPAddress}}' netcheck)
          probe "$ip"
          echo "port 5432 is open before the rules"
          rules -I
          sudo nsenter -t "$pid" -n iptables -S
          if probe "$ip"; then
            echo "::error::the DROP rules did not block port 5432"
            exit 1
          fi
          echo "port 5432 is blocked by the rules"
          dc exec netcheck pg_isready -h 127.0.0.1 -q
          echo "the loopback inside the namespace still works"
          rules -D
          probe "$ip"
          echo "port 5432 is open again"
          dc pause netcheck
          dc unpause netcheck
          echo "docker pause and unpause work"
          dc rm -f netcheck
          sudo iptables --version
          if lsmod | grep -qw sch_netem; then
            echo "sch_netem: loaded"
          else
            echo "sch_netem: not loaded"
          fi
```

- [ ] **Step 3: Check the file.**
  - Run: `.venv/bin/python -c "import yaml; jobs = yaml.safe_load(open('.github/workflows/loadtest.yml'))['jobs']; print(sorted(jobs)); print(jobs['pair']['if']); print(jobs['compare']['if']); print(jobs['runner-check']['if'])"`
  - Expected:
    `['compare', 'pair', 'runner-check']`, then
    `${{ !startsWith(github.ref_name, 'loadtest/faults') && !startsWith(github.ref_name, 'loadtest/runner-check') }}`,
    `${{ !cancelled() && needs.pair.result != 'skipped' }}` and
    `${{ startsWith(github.ref_name, 'loadtest/runner-check') }}`.
  - Run: `.venv/bin/python -c "import yaml; open('/tmp/db39-runner-check.sh', 'w').write(yaml.safe_load(open('.github/workflows/loadtest.yml'))['jobs']['runner-check']['steps'][0]['run'])" && shellcheck -s bash /tmp/db39-runner-check.sh`
  - Expected: no output from shellcheck.

- [ ] **Step 4: Commit.**

```bash
git add .github/workflows/loadtest.yml
git commit -F - <<'EOF'
ci(loadtest): check that a runner can inject database faults

Add a runner-check job, run by loadtest/runner-check-* branches, that
drops a PostgreSQL container's port with nsenter and iptables, checks
that its loopback still works, pauses it, and reports whether
sch_netem is loaded. loadtest/faults-* and loadtest/runner-check-*
branches no longer run the pair and compare jobs.

Refs #39

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

- [ ] **Step 5 (Controller): Run the check, and gate Task 9 on it.**
  - Check that the branch tracks no remote (the session's pre-push hook
    pulls only when it does): `git rev-parse --abbrev-ref @{u}` must fail
    with "no upstream configured".
  - Run: `git push origin HEAD:refs/heads/loadtest/runner-check-db39`
  - Find the run and watch it in the background:
    `gh -R AresLOLXD/cms run list --branch loadtest/runner-check-db39 --limit 1 --json databaseId -q '.[0].databaseId'`,
    then `gh -R AresLOLXD/cms run watch <id> --exit-status` (background),
    and read `gh -R AresLOLXD/cms run view <id> --log`.
  - Pass: the job is green and its log has "port 5432 is blocked by the
    rules", "the loopback inside the namespace still works" and "docker
    pause and unpause work". Note the `sch_netem:` line for the final
    report.
  - Then: `git push origin --delete loadtest/runner-check-db39`.
  - **If it fails, stop before Task 9** and tell the user: the fault mode
    cannot run on GitHub runners as designed; the alternative is to run
    `run.sh --faults` on the local rootful Docker (it needs `sudo` there).
    Do not change the injection method without the user's decision.

### Task 9: The fault mode of the load test

Start only after Task 8's controller gate passed.

**Files:**
- Create: `cmstestsuite/loadtest/faults.py`
- Create: `cmstestsuite/loadtest/faultstats.py`
- Modify: `cmstestsuite/loadtest/run.sh` (usage at lines 5-25, variables
  at lines 34-35 and 179-180, options at line 56, checks after line 76,
  `collect` at line 191, the driver start at line 357, the end at line 372)
- Modify: `cmstestsuite/loadtest/README.md` (new section "## Fault runs"
  before "## Limits", line 397)
- Modify: `.github/workflows/loadtest.yml` (new job `faults` before
  `runner-check`)
- Create: `cmstestsuite/unit_tests/loadtest/faults_test.py` (asyncio
  group, stdlib only)
- Create: `cmstestsuite/unit_tests/loadtest/faultstats_test.py` (asyncio
  group, stdlib only)

**Interfaces:**
- Consumes: the `cmsSetupDB` message (Task 6), the ES ERROR line
  (Task 4), Tornado's request log
  (`… [Contest,0 Main web::log_request] 500 GET /path (ip) 61000.00ms`),
  `run.sh`'s `out/<name>/` files (`users.json` "start", `requests.jsonl`
  with `t`, `dur`, `status`, `shard`; `db_export.json` "submissions" with
  `timestamp`, `scored_at`; `processes_end.txt` with `SHORT`), and the
  gating of Task 8.
- Produces:
  - `faults.py`: constants `F1_AT`, `F3_AT`, `F3_PAUSE_S`, `F4_AT`,
    `F2_AT`, `F2_DROP_S`, `F2_STEP_AFTER_S`, `MIN_CONTEST_S`,
    `TERMINATE_SQL`, `PARTITION_STEP_SQL`, `HOLDER_READY_SQL`,
    `HOLDER_SCRIPT`, `SETUP_DB_MESSAGE`, `DROP_RULES`;
    `run_command(argv, check=True)`, `iptables_argv(pid, action, rule)`,
    `class Injector` (`docker`, `compose`, `psql`, `db_container`,
    `db_pid`, `save`, `sleep_until`, `f1`, `f2`, `f3`, `f4`, `cleanup`,
    `run_schedule`), `main()`;
  - `faultstats.py`: the limits (`CALL_LIMIT_S`, `PAGES_LIMIT_S`,
    `ES_LIMIT_S`, `SCORES_LIMIT_S`, `F1_WINDOW_S`, `F3_CONNECT_LIMIT_S`,
    `F4_EXIT_LIMIT_S`, `F4_P99_LIMIT_S`, `F4_AFTER_S`),
    `check(run_dir) -> list[tuple[str, str, str, str, bool]]`,
    `main(argv) -> int`;
  - `run.sh --faults`, which writes `out/<name>/faults.json`,
    `faults.log` and `faults.md`.

- [ ] **Step 1: Write the failing tests of the injector.** Create
  `cmstestsuite/unit_tests/loadtest/faults_test.py`:

```python
# (fork header)

"""Tests for the fault injector of the load test (faults.py)."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import faults  # noqa: E402
import faultstats  # noqa: E402


# What the commands of every test answer: the container and its pid.
ANSWERS = {
    "ps -q db": (0, "c0ffee\n", ""),
    "{{.State.Pid}}": (0, "4242\n", ""),
}


class FakeRunner:
    """Stand in for run_command: record the commands and answer them."""

    def __init__(self, answers: dict[str, tuple[int, str, str]]):
        self.commands: list[list[str]] = []
        self.answers = answers

    def __call__(self, argv: list[str],
                 check: bool = True) -> subprocess.CompletedProcess:
        self.commands.append(argv)
        joined = " ".join(argv)
        for needle, (returncode, stdout, stderr) in self.answers.items():
            if needle in joined:
                return subprocess.CompletedProcess(
                    argv, returncode, stdout, stderr)
        return subprocess.CompletedProcess(argv, 0, "", "")


class FakeTime:
    """A clock that moves only when the injector sleeps."""

    def __init__(self, interrupt_at: float | None = None):
        self.now = 1_800_000_000.0
        self.interrupt_at = interrupt_at

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        if seconds == self.interrupt_at:
            # What run.sh's SIGTERM does, through main()'s handler.
            raise SystemExit(143)
        self.now += seconds


class FakeStdin:
    def __init__(self):
        self.text = ""

    def write(self, text: str) -> None:
        self.text += text

    def close(self) -> None:
        pass


class FakePopen:
    """Stand in for subprocess.Popen, for the holder's psql."""

    def __init__(self, argv: list[str], **keywords):
        self.argv = argv
        self.stdin = FakeStdin()
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


class InjectorTest(unittest.TestCase):

    def setUp(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        self.out = os.path.join(directory, "faults.json")
        self.popens: list[FakePopen] = []

    def _injector(self, answers: dict | None = None,
                  time_source: FakeTime | None = None) -> faults.Injector:
        self.runner = FakeRunner({**(answers or {}), **ANSWERS})
        self.time = time_source or FakeTime()

        def popen(argv: list[str], **keywords) -> FakePopen:
            process = FakePopen(argv, **keywords)
            self.popens.append(process)
            return process

        return faults.Injector(
            "cmsload-fork", "/l/compose.yml", "/l/.env.load", self.out,
            run=self.runner, popen=popen, sleep=self.time.sleep,
            clock=self.time.clock)

    def test_docker_goes_through_the_rootful_engine(self):
        injector = self._injector()
        self.assertEqual(
            injector.docker("pause", "c0ffee"),
            ["sg", "docker", "-c", "docker --context default pause c0ffee"])
        self.assertEqual(
            injector.compose("ps", "-q", "db")[3],
            "docker --context default compose -p cmsload-fork "
            "-f /l/compose.yml --env-file /l/.env.load ps -q db")

    def test_the_drop_rules_are_two_way_on_5432_and_spare_loopback(self):
        self.assertEqual(
            faults.iptables_argv(4242, "-I", faults.DROP_RULES[0]),
            ["sudo", "nsenter", "-t", "4242", "-n", "iptables", "-I",
             "INPUT", "!", "-i", "lo", "-p", "tcp", "--dport", "5432",
             "-j", "DROP"])
        self.assertEqual(
            faults.DROP_RULES[1],
            ("OUTPUT", "!", "-o", "lo", "-p", "tcp", "--sport", "5432",
             "-j", "DROP"))

    def test_f1_ends_the_cms_backends(self):
        injector = self._injector({"pg_terminate_backend": (0, "7\n", "")})
        injector.f1()
        self.assertEqual(injector.records[-1]["fault"], "F1")
        self.assertEqual(injector.records[-1]["terminated"], 7)
        self.assertEqual(shlex.split(self.runner.commands[-1][3])[-1],
                         faults.TERMINATE_SQL)
        self.assertIn("application_name LIKE 'cms%'", faults.TERMINATE_SQL)

    def test_f2_drops_both_ways_then_does_the_partition_step(self):
        injector = self._injector(
            {"idle in transaction%": (0, "2\n", "")})
        started = self.time.now
        injector.f2()
        record = injector.records[-1]
        self.assertEqual(record["start"], started)
        self.assertEqual(record["end"], started + faults.F2_DROP_S)
        self.assertEqual(record["partition_step"],
                         record["end"] + faults.F2_STEP_AFTER_S)
        self.assertEqual(record["partition_step_terminated"], 2)
        self.assertEqual(
            [command for command in self.runner.commands
             if command[0] == "sudo"],
            [faults.iptables_argv(4242, "-I", faults.DROP_RULES[0]),
             faults.iptables_argv(4242, "-I", faults.DROP_RULES[1]),
             faults.iptables_argv(4242, "-D", faults.DROP_RULES[1]),
             faults.iptables_argv(4242, "-D", faults.DROP_RULES[0])])
        self.assertEqual(shlex.split(self.runner.commands[-1][3])[-1],
                         faults.PARTITION_STEP_SQL.format(end=record["end"]))
        self.assertEqual(injector.rules, [])

    def test_cleanup_after_an_interrupted_drop_removes_both_rules(self):
        injector = self._injector(
            time_source=FakeTime(interrupt_at=faults.F2_DROP_S))
        with self.assertRaises(SystemExit):
            injector.f2()
        injector.cleanup()
        self.assertEqual(
            self.runner.commands[-2:],
            [faults.iptables_argv(4242, "-D", faults.DROP_RULES[1]),
             faults.iptables_argv(4242, "-D", faults.DROP_RULES[0])])
        self.assertEqual(injector.rules, [])

    def test_cleanup_after_an_interrupted_pause_unpauses_postgresql(self):
        injector = self._injector(
            time_source=FakeTime(interrupt_at=faults.F3_PAUSE_S))
        with self.assertRaises(SystemExit):
            injector.f3()
        injector.cleanup()
        self.assertEqual(self.runner.commands[-1],
                         injector.docker("unpause", "c0ffee"))
        self.assertIsNone(injector.paused)

    def test_f4_runs_cmssetupdb_behind_an_idle_reader(self):
        injector = self._injector({
            "application_name = ": (0, "1\n", ""),
            "cmsSetupDB": (1, "", "2026 - CRITICAL [x] "
                           + faults.SETUP_DB_MESSAGE + ": another\n")})
        injector.f4()
        record = injector.records[-1]
        self.assertEqual((record["fault"], record["exit_status"],
                          record["message"]), ("F4", 1, True))
        self.assertEqual(self.popens[0].stdin.text, faults.HOLDER_SCRIPT)
        self.assertIn("psql", self.popens[0].argv[3])
        self.assertIsNone(injector.holder)

    def test_the_records_are_saved_as_json(self):
        injector = self._injector({"pg_terminate_backend": (0, "3\n", "")})
        injector.f1()
        injector.save()
        with open(self.out) as f:
            self.assertEqual(json.load(f), injector.records)

    def test_the_schedule_is_in_order_and_ends_before_the_end_burst(self):
        offsets = [faults.F1_AT[0], faults.F1_AT[1], faults.F3_AT,
                   faults.F4_AT, faults.F2_AT]
        self.assertEqual(offsets, sorted(offsets))
        # The fault run's end burst is the last 120 s of the contest.
        recovered = faults.F2_AT + faults.F2_DROP_S + faults.F2_STEP_AFTER_S
        self.assertLessEqual(recovered + faultstats.SCORES_LIMIT_S,
                             faults.MIN_CONTEST_S - 120)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Write the failing tests of the analysis.** Create
  `cmstestsuite/unit_tests/loadtest/faultstats_test.py`:

```python
# (fork header)

"""Tests for the pass criteria of a fault run (faultstats.py)."""

import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import faultstats  # noqa: E402


START = 1_800_000_000.0  # the contest start
F2_START = START + 1080
RECOVERED = START + 1350  # F2's partition step
ES = "EvaluationService::_write_results_sync"
LOST = ("Lost the database connection while writing worker results "
        "(server closed the connection unexpectedly); the sweeper will "
        "redo them within about 2 minutes.")


def stamp(moment: float) -> str:
    """Return the time of a CMS log line (UTC)."""
    return datetime.datetime.fromtimestamp(
        moment, datetime.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S,%f")[:23]


class FaultStatsTest(unittest.TestCase):
    """A small run where every criterion passes; tests break one each."""

    def setUp(self):
        self.run_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.run_dir)
        self.faults = [
            {"fault": "F1", "start": START + 120, "end": START + 121,
             "terminated": 9},
            {"fault": "F1", "start": START + 180, "end": START + 181,
             "terminated": 8},
            {"fault": "F3", "start": START + 600, "end": START + 630},
            {"fault": "F4", "start": START + 1000, "end": START + 1006,
             "exit_status": 1, "duration": 6.0, "message": True},
            {"fault": "F2", "start": F2_START, "end": F2_START + 90,
             "partition_step": RECOVERED, "partition_step_terminated": 0},
        ]
        self.logs = {
            "EvaluationService-0": [
                (START + 125, ES, LOST),
                (START + 185, ES, LOST),
                (F2_START + 5, ES, "Starting commit process..."),
                (F2_START + 65, ES, LOST),
                (RECOVERED + 30, ES, "Starting commit process..."),
                (RECOVERED + 31, ES, "Done"),
            ],
            "ContestWebServer-0": [
                (START + 612, "web::log_request",
                 "500 GET /loada (172.18.0.4) 10012.00ms"),
                (F2_START + 70, "web::log_request",
                 "500 GET /loada (172.18.0.4) 61000.00ms"),
            ],
            "ScoringService-0": [
                (F2_START + 1, "async_triggeredservice::run",
                 "Executing operation `scoring submission 1 on dataset 1'."),
                (F2_START + 62, "async_triggeredservice::run",
                 "Unexpected error when executing operation `scoring "
                 "submission 1 on dataset 1'."),
            ],
        }
        self.requests = [
            {"t": START + 640, "dur": 0.1, "status": 200,
             "shard": "cms:8888"},
            {"t": START + 1001, "dur": 4.0, "status": 200,
             "shard": "cms:8888"},
            {"t": RECOVERED + 5, "dur": 0.1, "status": 200, "shard": "rws"},
            {"t": RECOVERED + 20, "dur": 0.1, "status": 200,
             "shard": "cms:8888"},
        ]
        self.submissions: list[dict] | None = [
            {"timestamp": F2_START - 10, "scored_at": RECOVERED + 200},
            {"timestamp": START + 10, "scored_at": START + 40},
        ]
        self.census = "cmsEvaluationService expected=1 found=1\n"

    def _write(self) -> None:
        with open(os.path.join(self.run_dir, "faults.json"), "w") as f:
            json.dump(self.faults, f)
        for service, lines in self.logs.items():
            directory = os.path.join(self.run_dir, "cmslog", service)
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, "2026-10-08-00-00-00.log")
            with open(path, "w") as f:
                for moment, where, message in lines:
                    f.write("%s - INFO [%s] %s\n"
                            % (stamp(moment), where, message))
        with open(os.path.join(self.run_dir, "requests.jsonl"), "w") as f:
            for request in self.requests:
                f.write(json.dumps(request) + "\n")
        if self.submissions is not None:
            path = os.path.join(self.run_dir, "db_export.json")
            with open(path, "w") as f:
                json.dump({"submissions": self.submissions}, f)
        with open(os.path.join(self.run_dir, "processes_end.txt"),
                  "w") as f:
            f.write(self.census)

    def _rows(self) -> dict[tuple[str, str], tuple[str, bool]]:
        self._write()
        return {(fault, criterion): (value, passed)
                for fault, criterion, value, _, passed
                in faultstats.check(self.run_dir)}

    def _main(self) -> tuple[int, str]:
        self._write()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = faultstats.main(["faultstats.py", self.run_dir])
        return status, output.getvalue()

    def test_a_good_run_passes_every_criterion(self):
        status, output = self._main()
        self.assertEqual(status, 0, output)
        self.assertNotIn("FAIL", output)
        rows = self._rows()
        expected = {
            ("F1", "lost-connection log lines within 120 s (#1)"):
                ("2", True),
            ("F2", "longest ES flush that lost the connection"):
                ("60.0 s of 1", True),
            ("F2", "longest CWS 500 answer"): ("61.0 s of 1", True),
            ("F2", "longest SS operation that failed"):
                ("61.0 s of 1", True),
            ("F2", "pages answer again after"): ("20.0 s", True),
            ("F2", "ES commits again after"): ("31.0 s", True),
            ("F2", "earlier submissions scored after"): ("200.0 s", True),
            ("F3", "longest CWS 500 answer"): ("10.0 s of 1", True),
            ("F3", "pages answer again after"): ("10.0 s", True),
            ("F4", "CWS p99 from its start to 5 s after its exit"):
                ("4.0 s", True),
            ("all", "submissions never scored"): ("0", True),
        }
        for key, value in expected.items():
            with self.subTest(row=key):
                self.assertEqual(rows[key], value)

    def test_a_call_that_failed_too_late_fails_its_row(self):
        self.logs["EvaluationService-0"][3] = (F2_START + 85, ES, LOST)
        rows = self._rows()
        self.assertEqual(
            rows[("F2", "longest ES flush that lost the connection")],
            ("80.0 s of 1", False))
        self.assertEqual(self._main()[0], 1)

    def test_a_fault_that_did_not_run_fails_its_row(self):
        self.faults = [record for record in self.faults
                       if record["fault"] != "F2"]
        self.assertEqual(self._rows()[("F2", "ran")], ("no", False))
        self.assertEqual(self._main()[0], 1)

    def test_a_missing_export_fails_instead_of_crashing(self):
        self.submissions = None
        self.assertEqual(self._rows()[("all", "submissions never scored")],
                         ("no db_export.json", False))
        self.assertEqual(self._main()[0], 1)

    def test_a_dead_process_fails(self):
        self.census = "cmsWorker expected=8 found=7 SHORT\n"
        self.assertEqual(
            self._rows()[("all", "CMS processes still running at the end")],
            ("no", False))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests and see them fail.**
  - Run: `.venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/loadtest/faults_test.py cmstestsuite/unit_tests/loadtest/faultstats_test.py -q`
    (no database: `CMS_CONFIG` is not needed)
  - Expected: collection errors, `ModuleNotFoundError: No module named 'faults'`
    and `'faultstats'`.

- [ ] **Step 4: Create `cmstestsuite/loadtest/faults.py`** (mode 755):

```python
# (fork header)

"""Inject the database faults of a fault run (run.sh --faults, #39).

It runs on the host, next to run.sh, while the driver runs. At fixed
times after the contest start ("start" in users.json) it injects:

- F1, at 120 and 180 s: pg_terminate_backend of every CMS backend;
- F3, at 600 s: docker pause of PostgreSQL for 30 s;
- F4, at 1000 s: cmsSetupDB while a psql session holds a read of
  contests, idle in its transaction;
- F2, at 1080 s: a two-way DROP of PostgreSQL's port for 90 s, then,
  180 s after its end, the runbook's partition step: end the CMS
  backends left idle in a transaction since before that end.

It writes each fault's start and end (epoch seconds) to faults.json,
again after each fault, for faultstats.py. CMS backends are those whose
application_name starts with "cms", so the monitor and psql are spared.
The DROP rules go into PostgreSQL's network namespace (sudo nsenter):
they need only netfilter and leave the loopback inside the container
alone. However it exits, the injector removes its rules and unpauses
PostgreSQL. Stdlib only.

"""

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable


# Seconds after the contest start.
F1_AT = (120, 180)
F3_AT = 600
F3_PAUSE_S = 30
F4_AT = 1000
F2_AT = 1080
F2_DROP_S = 90
F2_STEP_AFTER_S = 180
# The scores need about 6 minutes after the partition step, before the
# end burst: run.sh refuses a shorter --contest.
MIN_CONTEST_S = 2100

CMS_BACKENDS = "application_name LIKE 'cms%' AND pid <> pg_backend_pid()"
TERMINATE_SQL = ("SELECT count(pg_terminate_backend(pid)) "
                 "FROM pg_stat_activity WHERE " + CMS_BACKENDS)
PARTITION_STEP_SQL = (
    "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
    "WHERE " + CMS_BACKENDS + " AND state LIKE 'idle in transaction%' "
    "AND xact_start < to_timestamp({end})")
HOLDER_READY_SQL = (
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE application_name = 'psql' AND state = 'idle in transaction'")
# psql runs \! in its own shell while its transaction stays open, so the
# holder is idle in transaction for 40 s.
HOLDER_SCRIPT = ("BEGIN;\nSELECT 1 FROM contests LIMIT 1;\n"
                 "\\! sleep 40\nCOMMIT;\n")
SETUP_DB_MESSAGE = ("cmsSetupDB could not lock contests or ranking_groups "
                    "within 5 s")
# In PostgreSQL's namespace: what arrives at its port and what leaves
# from it, except on the loopback.
DROP_RULES = (
    ("INPUT", "!", "-i", "lo", "-p", "tcp", "--dport", "5432", "-j",
     "DROP"),
    ("OUTPUT", "!", "-o", "lo", "-p", "tcp", "--sport", "5432", "-j",
     "DROP"),
)

Runner = Callable[..., subprocess.CompletedProcess]


def run_command(argv: list[str],
                check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and capture its output as text.

    argv: the command.
    check: whether a non-zero exit status raises.

    return: the finished process.

    raise (subprocess.CalledProcessError): if check and it failed.

    """
    return subprocess.run(argv, check=check, capture_output=True,
                          text=True, timeout=120)


def iptables_argv(pid: int, action: str,
                  rule: tuple[str, ...]) -> list[str]:
    """Return the command that adds or deletes a rule in a namespace.

    pid: a process of the network namespace.
    action: "-I" to add the rule, "-D" to delete it.
    rule: the chain and the match of the rule.

    return: the command.

    """
    return ["sudo", "nsenter", "-t", str(pid), "-n", "iptables", action,
            *rule]


class Injector:
    """Inject the faults into one load-test stack, and record them."""

    def __init__(self, project: str, compose_file: str, env_file: str,
                 out: str, run: Runner = run_command,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time):
        """Prepare the injector.

        project, compose_file, env_file: how run.sh calls compose for
            the run.
        out: the path of faults.json.
        run, popen, sleep, clock: the tests replace them.

        """
        self.project = project
        self.compose_file = compose_file
        self.env_file = env_file
        self.out = out
        self.run = run
        self.popen = popen
        self.sleep = sleep
        self.clock = clock
        self.records: list[dict] = []
        # What is in place and must be undone: the DROP rules (with the
        # pid of their namespace), the paused container, the holder.
        self.rules: list[tuple[int, tuple[str, ...]]] = []
        self.paused: str | None = None
        self.holder: subprocess.Popen | None = None

    def docker(self, *args: str) -> list[str]:
        """Return a docker command for the rootful engine, as run.sh."""
        return ["sg", "docker", "-c", shlex.join(
            ["docker", "--context", "default", *args])]

    def compose(self, *args: str) -> list[str]:
        """Return a compose command for the run's stack."""
        return self.docker("compose", "-p", self.project,
                           "-f", self.compose_file,
                           "--env-file", self.env_file, *args)

    def psql(self, sql: str) -> str:
        """Run one statement in the db container; return its output."""
        return self.run(self.compose(
            "exec", "-T", "db", "psql", "-U", "cms", "-d", "cmsdb",
            "-At", "-c", sql)).stdout.strip()

    def db_container(self) -> str:
        """Return the id of the run's PostgreSQL container."""
        return self.run(self.compose("ps", "-q", "db")).stdout.strip()

    def db_pid(self) -> int:
        """Return the host pid of the PostgreSQL container."""
        return int(self.run(self.docker(
            "inspect", "-f", "{{.State.Pid}}",
            self.db_container())).stdout.strip())

    def save(self) -> None:
        """Write the records to faults.json, atomically."""
        partial = self.out + ".tmp"
        with open(partial, "w") as f:
            json.dump(self.records, f, indent=1)
        os.replace(partial, self.out)

    def sleep_until(self, moment: float) -> None:
        """Sleep until an epoch time, if it is still ahead."""
        delay = moment - self.clock()
        if delay > 0:
            self.sleep(delay)

    def f1(self) -> None:
        """End every CMS backend."""
        start = self.clock()
        terminated = int(self.psql(TERMINATE_SQL))
        self.records.append({"fault": "F1", "start": start,
                             "end": self.clock(), "terminated": terminated})

    def f2(self) -> None:
        """Drop PostgreSQL's traffic both ways, then do the runbook's
        partition step.

        """
        pid = self.db_pid()
        start = self.clock()
        for rule in DROP_RULES:
            self.run(iptables_argv(pid, "-I", rule))
            self.rules.append((pid, rule))
        self.sleep(F2_DROP_S)
        self._delete_rules()
        end = self.clock()
        record = {"fault": "F2", "start": start, "end": end}
        self.records.append(record)
        self.save()
        self.sleep_until(end + F2_STEP_AFTER_S)
        record["partition_step"] = self.clock()
        record["partition_step_terminated"] = int(
            self.psql(PARTITION_STEP_SQL.format(end=end)))

    def f3(self) -> None:
        """Pause PostgreSQL: its kernel still answers, it does not."""
        container = self.db_container()
        start = self.clock()
        self.run(self.docker("pause", container))
        self.paused = container
        self.sleep(F3_PAUSE_S)
        self.run(self.docker("unpause", container))
        self.paused = None
        self.records.append({"fault": "F3", "start": start,
                             "end": self.clock()})

    def f4(self) -> None:
        """Run cmsSetupDB while a psql session holds a read of contests."""
        self.holder = self.popen(
            self.compose("exec", "-T", "db", "psql", "-U", "cms",
                         "-d", "cmsdb", "-q"),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, text=True)
        self.holder.stdin.write(HOLDER_SCRIPT)
        self.holder.stdin.close()
        for _ in range(30):
            if self.psql(HOLDER_READY_SQL) != "0":
                break
            self.sleep(1)
        start = self.clock()
        setup = self.run(self.compose("exec", "-T", "cms", "cmsSetupDB"),
                         check=False)
        end = self.clock()
        self.holder.wait(timeout=60)
        self.holder = None
        self.records.append({
            "fault": "F4", "start": start, "end": end,
            "exit_status": setup.returncode,
            "duration": round(end - start, 1),
            "message": SETUP_DB_MESSAGE in setup.stdout + setup.stderr})

    def _delete_rules(self) -> None:
        """Delete the DROP rules in place, the last added first."""
        while self.rules:
            pid, rule = self.rules.pop()
            self.run(iptables_argv(pid, "-D", rule), check=False)

    def cleanup(self) -> None:
        """Undo whatever fault is still in place."""
        self._delete_rules()
        if self.paused is not None:
            self.run(self.docker("unpause", self.paused), check=False)
            self.paused = None
        if self.holder is not None:
            self.holder.kill()
            self.holder = None

    def run_schedule(self, contest_start: float) -> None:
        """Inject every fault at its time, and record each one.

        contest_start: the contest start, in epoch seconds.

        """
        schedule = ((F1_AT[0], self.f1), (F1_AT[1], self.f1),
                    (F3_AT, self.f3), (F4_AT, self.f4), (F2_AT, self.f2))
        for offset, inject in schedule:
            self.sleep_until(contest_start + offset)
            inject()
            self.save()
            print(json.dumps(self.records[-1]), flush=True)


def main() -> int:
    """Parse the arguments and inject the faults."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--compose-file", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--users-file", required=True,
                        help="users.json, for the contest start")
    parser.add_argument("--out", required=True, help="faults.json")
    args = parser.parse_args()
    with open(args.users_file) as f:
        contest_start = json.load(f)["start"]
    injector = Injector(args.project, args.compose_file, args.env_file,
                        args.out)
    # run.sh ends the injector with SIGTERM if the run ends first.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(143))
    try:
        injector.run_schedule(contest_start)
    finally:
        injector.cleanup()
        injector.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Create `cmstestsuite/loadtest/faultstats.py`** (mode
  755):

```python
# (fork header)

"""Check the pass criteria of a fault run (run.sh --faults, #39).

Usage: faultstats.py out/<run>

It reads faults.json (written by faults.py), the CMS logs in cmslog/,
the driver's requests.jsonl, db_export.json and processes_end.txt,
prints one markdown row per criterion of the fault table of
docs/superpowers/specs/2026-10-08-db-transport-timeouts-design.md
("Validation runs"), and exits 1 if one fails, a fault that did not run
included. Stdlib only.

"""

import datetime
import glob
import json
import os
import re
import sys


# F2: each database call fails within this many seconds of its start.
CALL_LIMIT_S = 75
# After F2 or F3: pages answer within a minute, ES commits again within
# about 2 minutes (its sweeper runs every 117 s), and the scores arrive
# within about 6 minutes (the SS and PS sweepers run every 347 s), plus
# the time to do the work.
PAGES_LIMIT_S = 60
ES_LIMIT_S = 150
SCORES_LIMIT_S = 420
# F1: the logs name the lost connection within this many seconds.
F1_WINDOW_S = 120
# F3: a new connection fails at the connect deadline of 10 s.
F3_CONNECT_LIMIT_S = 12
# F4: cmsSetupDB exits within 15 s, and CWS p99 stays at most 6 s from
# its start to 5 s after it exits.
F4_EXIT_LIMIT_S = 15
F4_P99_LIMIT_S = 6
F4_AFTER_S = 5

DISCONNECT = re.compile(
    r"Lost the database connection"
    r"|server closed the connection unexpectedly"
    r"|terminating connection due to administrator command"
    r"|could not receive data from server|Connection timed out")
ES_LOST = "Lost the database connection while writing worker results"
# Tornado's request log: "] 500 GET /path (ip) 61000.00ms".
ACCESS = re.compile(r"\] (\d{3}) [A-Z]+ \S+ \(.*\) ([0-9.]+)ms$")
SS_START = re.compile(r"Executing operations? `(.+?)'")
SS_ERROR = re.compile(r"Unexpected error when executing operations? `(.+?)'")

Row = tuple[str, str, str, str, bool]


def log_time(line: str) -> float:
    """Return the time of a CMS log line, written in UTC.

    line: the log line.

    return: its time, in epoch seconds.

    raise (ValueError): if the line does not start with a time, as the
        lines of a traceback.

    """
    return datetime.datetime.strptime(
        line[:23], "%Y-%m-%d %H:%M:%S,%f").replace(
            tzinfo=datetime.timezone.utc).timestamp()


def read_logs(run_dir: str, service: str) -> list[tuple[float, str]]:
    """Return the timed lines of a service's logs, in time order.

    run_dir: the run directory.
    service: how the log directories' names start, as
        "EvaluationService"; "" for every service.

    return: (time, line) pairs.

    """
    entries = []
    pattern = os.path.join(run_dir, "cmslog", service + "*", "[0-9]*.log")
    for path in glob.glob(pattern):
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    entries.append((log_time(line), line.rstrip("\n")))
                except ValueError:
                    continue
    entries.sort(key=lambda entry: entry[0])
    return entries


def load_jsonl(path: str) -> list[dict]:
    """Return the objects of a JSON-lines file; none if it is missing."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _load_json(path: str, default):
    """Return the content of a JSON file, or default if it is missing."""
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def _read_text(path: str) -> str | None:
    """Return the content of a text file, or None if it is missing."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def pct(values: list[float], fraction: float) -> float | None:
    """Return a percentile as analyze.py computes it, or None."""
    values = sorted(values)
    return values[int((len(values) - 1) * fraction)] if values else None


def fmt(seconds: float | None) -> str:
    """Format a duration for the table."""
    return "-" if seconds is None else "%.1f s" % seconds


def first_after(times: list[float], moment: float) -> float | None:
    """Return how long after moment the first of times comes, or None."""
    later = [t for t in times if t >= moment]
    return min(later) - moment if later else None


def es_failed_flushes(es_logs: list[tuple[float, str]], start: float,
                      end: float) -> list[float]:
    """Return how long each ES flush that lost the connection took.

    A flush runs from "Starting commit process..." to its ERROR line;
    only the flushes that started between start and end count.

    """
    durations = []
    began = None
    for t, line in es_logs:
        if "Starting commit process" in line:
            began = t
        elif ES_LOST in line and began is not None:
            if start <= began <= end:
                durations.append(t - began)
            began = None
        elif line.endswith("] Done"):
            began = None
    return durations


def cws_500s(cws_logs: list[tuple[float, str]], start: float,
             end: float) -> list[float]:
    """Return the request times of the CWS 500 answers to the requests
    that started between start and end.

    """
    times = []
    for t, line in cws_logs:
        match = ACCESS.search(line)
        if match is not None and match.group(1) == "500":
            took = float(match.group(2)) / 1000
            if start <= t - took <= end:
                times.append(took)
    return times


def ss_failed_operations(ss_logs: list[tuple[float, str]], start: float,
                         end: float) -> list[float]:
    """Return how long each SS operation that failed took; only those
    that started between start and end count.

    """
    began: dict[str, float] = {}
    durations = []
    for t, line in ss_logs:
        failed = SS_ERROR.search(line)
        if failed is not None:
            first = began.pop(failed.group(1), None)
            if first is not None and start <= first <= end:
                durations.append(t - first)
            continue
        started = SS_START.search(line)
        if started is not None:
            began[started.group(1)] = t
    return durations


def _longest(fault: str, what: str, durations: list[float],
             limit: float) -> Row:
    """Return the row of the longest of some durations."""
    worst = max(durations, default=None)
    return (fault, "longest " + what,
            "%s of %d" % (fmt(worst), len(durations)),
            "<= %g s" % limit, worst is None or worst <= limit)


def _within(fault: str, what: str, seconds: float | None,
            limit: float) -> Row:
    """Return the row of a delay that must exist and stay in a limit."""
    return (fault, what, fmt(seconds), "<= %g s" % limit,
            seconds is not None and seconds <= limit)


def check(run_dir: str) -> list[Row]:
    """Return one row per criterion of a fault run.

    run_dir: the run directory (out/<run>).

    return: (fault, criterion, value, limit, passed) tuples.

    """
    rows: list[Row] = []
    by_fault: dict[str, list[dict]] = {}
    for record in _load_json(os.path.join(run_dir, "faults.json"), []):
        by_fault.setdefault(record["fault"], []).append(record)
    every = read_logs(run_dir, "")
    es = read_logs(run_dir, "EvaluationService")
    cws = read_logs(run_dir, "ContestWebServer")
    ss = read_logs(run_dir, "ScoringService")
    requests = [request for request
                in load_jsonl(os.path.join(run_dir, "requests.jsonl"))
                if request["shard"] != "rws"]
    pages = [request["t"] for request in requests
             if 200 <= request["status"] < 400]
    es_done = [t for t, line in es if line.endswith("] Done")]
    export = _load_json(os.path.join(run_dir, "db_export.json"), None)
    submissions = export["submissions"] if export is not None else []

    f1 = by_fault.get("F1", [])
    rows.append(("F1", "injections", str(len(f1)), "2", len(f1) == 2))
    for index, record in enumerate(f1, 1):
        start = record["start"]
        lines = sum(1 for t, line in every
                    if start <= t <= start + F1_WINDOW_S
                    and DISCONNECT.search(line))
        rows.append(("F1", "backends ended (#%d)" % index,
                     str(record["terminated"]), "> 0",
                     record["terminated"] > 0))
        rows.append(("F1", "lost-connection log lines within %d s (#%d)"
                     % (F1_WINDOW_S, index), str(lines), "> 0", lines > 0))

    f2 = by_fault.get("F2", [{}])[0]
    if "partition_step" not in f2:
        rows.append(("F2", "ran", "no", "yes", False))
    else:
        window = (f2["start"] - CALL_LIMIT_S, f2["end"])
        answers = cws_500s(cws, *window)
        rows.append(_longest("F2", "ES flush that lost the connection",
                             es_failed_flushes(es, *window), CALL_LIMIT_S))
        rows.append(_longest("F2", "CWS 500 answer", answers, CALL_LIMIT_S))
        rows.append(_longest("F2", "SS operation that failed",
                             ss_failed_operations(ss, *window),
                             CALL_LIMIT_S))
        rows.append(("F2", "CWS 500 answers during the drop",
                     str(len(answers)), "> 0", bool(answers)))
        rows.append(("F2", "backends ended by the partition step",
                     str(f2["partition_step_terminated"]), "recorded",
                     True))
        recovered = max(f2["end"], f2["partition_step"])
        rows.append(_within("F2", "pages answer again after",
                            first_after(pages, recovered), PAGES_LIMIT_S))
        rows.append(_within("F2", "ES commits again after",
                            first_after(es_done, recovered), ES_LIMIT_S))
        backlog = [submission for submission in submissions
                   if submission["timestamp"] <= recovered
                   and (submission["scored_at"] is None
                        or submission["scored_at"] >= f2["start"])]
        if any(submission["scored_at"] is None for submission in backlog):
            scored_after = None
        else:
            scored_after = max([submission["scored_at"] - recovered
                                for submission in backlog] + [0.0])
        rows.append(_within("F2", "earlier submissions scored after",
                            scored_after, SCORES_LIMIT_S))

    f3 = by_fault.get("F3", [{}])[0]
    if "end" not in f3:
        rows.append(("F3", "ran", "no", "yes", False))
    else:
        expired = sum(1 for t, line in every
                      if f3["start"] <= t <= f3["end"] + 15
                      and "timeout expired" in line)
        rows.append(("F3", "'timeout expired' log lines", str(expired),
                     "reported", True))
        rows.append(_longest("F3", "CWS 500 answer",
                             cws_500s(cws, f3["start"], f3["end"]),
                             F3_CONNECT_LIMIT_S))
        rows.append(_within("F3", "pages answer again after",
                            first_after(pages, f3["end"]), PAGES_LIMIT_S))

    f4 = by_fault.get("F4", [{}])[0]
    if "exit_status" not in f4:
        rows.append(("F4", "ran", "no", "yes", False))
    else:
        rows.append(("F4", "cmsSetupDB exit status",
                     str(f4["exit_status"]), "1", f4["exit_status"] == 1))
        rows.append(_within("F4", "cmsSetupDB duration", f4["duration"],
                            F4_EXIT_LIMIT_S))
        rows.append(("F4", "lock message printed",
                     "yes" if f4["message"] else "no", "yes",
                     bool(f4["message"])))
        p99 = pct([request["dur"] for request in requests
                   if f4["start"] <= request["t"]
                   <= f4["end"] + F4_AFTER_S], 0.99)
        rows.append(_within(
            "F4", "CWS p99 from its start to %d s after its exit"
            % F4_AFTER_S, p99, F4_P99_LIMIT_S))

    census = _read_text(os.path.join(run_dir, "processes_end.txt"))
    alive = census is not None and "SHORT" not in census
    rows.append(("all", "CMS processes still running at the end",
                 "yes" if alive else "no", "yes", alive))
    if export is None:
        rows.append(("all", "submissions never scored",
                     "no db_export.json", "0", False))
    else:
        stuck = sum(1 for submission in submissions
                    if submission["scored_at"] is None)
        rows.append(("all", "submissions never scored", str(stuck), "0",
                     stuck == 0))
    return rows


def main(argv: list[str]) -> int:
    """Print the table of a run; return 1 if a criterion failed."""
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    rows = check(argv[1])
    print("| fault | criterion | value | limit | result |")
    print("|---|---|---|---|---|")
    for fault, criterion, value, limit, passed in rows:
        print("| %s | %s | %s | %s | %s |" % (
            fault, criterion, value, limit, "pass" if passed else "FAIL"))
    return 0 if all(row[4] for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 6: Run the tests and see them pass.**
  - Run: `chmod 755 cmstestsuite/loadtest/faults.py cmstestsuite/loadtest/faultstats.py && .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/loadtest -q`
  - Expected: all pass (the new files and the existing harness tests).

- [ ] **Step 7: Add `--faults` to `run.sh`.**
  - In the usage comment, change line 9 to
    `#            [--poll-cap SECONDS] [--faults]` and add after line 21
    (`# the backoff has no limit, like upstream's page.`):

```bash
# --faults (fork target only, --contest 2100 or more) also runs
# faults.py, which injects the database faults of #39 at fixed times of
# the contest, and then faultstats.py, which writes out/<name>/faults.md
# (see "Fault runs" in the README). It needs passwordless sudo.
```

  - Add after line 35 (`UA=175 UB=75 ...`):

```bash
FAULTS=0
```

  - Add before `    -h | --help) usage; exit 0 ;;` (line 57):

```bash
    --faults) FAULTS=1; shift ;;
```

  - Add after the `full`/`upstream` check (after line 76):

```bash
if ((FAULTS)); then
  if [[ $TARGET != fork ]]; then
    echo "--faults runs on the fork target only" >&2
    exit 2
  fi
  if ! [[ $CONTEST =~ ^[0-9]+$ ]] || ((CONTEST < 2100)); then
    echo "--faults needs --contest 2100 or more: the scores after the last fault arrive about 1770 s after the start" >&2
    exit 2
  fi
fi
```

  - Change lines 179-180 (`COLLECTED=` and `STATS_PID=`) to:

```bash
COLLECTED=
STATS_PID=
FAULTS_PID=
```

  - In `collect`, after line 191
    (`[[ -z $STATS_PID ]] || kill "$STATS_PID" 2>/dev/null`), add:

```bash
  # The injector first: on SIGTERM it removes its rules and unpauses
  # PostgreSQL, which the export below needs.
  if [[ -n $FAULTS_PID ]]; then
    kill "$FAULTS_PID" 2>/dev/null
    wait "$FAULTS_PID" 2>/dev/null
  fi
```

  - Before the comment `# The driver runs in the background so that a signal to this script runs`
    (line 357), add:

```bash
if ((FAULTS)); then
  python3 "$HERE/faults.py" --project "$PROJ" \
    --compose-file "$HERE/compose.yml" --env-file "$HERE/.env.load" \
    --users-file "$OUT/users.json" --out "$OUT/faults.json" \
    >"$OUT/faults.log" 2>&1 &
  FAULTS_PID=$!
fi

```

  - After `python3 "$HERE/analyze.py" "$OUT" --project-prefix "$PROJ"`
    (line 372), add:

```bash
if ((FAULTS)); then
  python3 "$HERE/faultstats.py" "$OUT" >"$OUT/faults.md" ||
    echo "A fault criterion failed, see $OUT/faults.md" >&2
  cat "$OUT/faults.md"
fi
```

  - Run: `shellcheck cmstestsuite/loadtest/run.sh && bash -n cmstestsuite/loadtest/run.sh && bash cmstestsuite/loadtest/run.sh --target upstream --profile portable --name x --faults; echo "exit $?"`
  - Expected: no shellcheck output, then `--faults runs on the fork target only`
    and `exit 2`.
  - Run: `bash cmstestsuite/loadtest/run.sh --target fork --profile portable --name x --contest 600 --faults; echo "exit $?"`
  - Expected: the `--contest 2100 or more` message and `exit 2` (before
    anything touches Docker).

- [ ] **Step 8: Add the `faults` job** to `.github/workflows/loadtest.yml`,
  before the `runner-check` job:

```yaml

  # A fault run (run.sh --faults): smoke-sized load, the fork only, one
  # job per profile. faults.md, in the summary and the artifact, has the
  # pass or FAIL of every criterion; a FAIL does not fail the job.
  faults:
    if: ${{ startsWith(github.ref_name, 'loadtest/faults') }}
    runs-on: ubuntu-24.04
    timeout-minutes: 150

    strategy:
      fail-fast: false
      matrix:
        profile: [portable, full]

    env:
      PROFILE: ${{ matrix.profile }}

    defaults:
      run:
        shell: bash
        working-directory: cmstestsuite/loadtest

    steps:
      - uses: actions/checkout@v5

      - name: Record the runner
        run: |
          mkdir -p out
          {
            echo "fault run, profile $PROFILE"
            echo "nproc: $(nproc)"
            lscpu | grep '^Model name'
            echo "kernel: $(uname -r)"
          } | tee out/runner.txt

      - name: Build the fork image
        run: ./build.sh fork HEAD

      # The README's smoke values, but a 2100 s contest: the faults and
      # their recovery end about 1770 s after the start (faults.py).
      - name: Run with faults
        timeout-minutes: 120
        run: |
          name=faults-$PROFILE
          rc=0
          ./run.sh --target fork --profile "$PROFILE" --name "$name" \
              --users-a 14 --users-b 6 --login-window 60 --contest 2100 \
              --end-burst 120 --rate 10 --workers "$LOAD_WORKERS" \
              --cws "$LOAD_CWS" --faults 2>&1 |
            tee "out/run-$name.log" || rc=$?
          ./teardown.sh fork 2>&1 | tee -a "out/run-$name.log" || rc=$?
          exit "$rc"

      - name: Summarize the run
        if: always()
        run: |
          shopt -s nullglob
          {
            echo "## Fault run, profile $PROFILE"
            echo
            for file in out/faults-*/faults.md; do cat "$file"; done
          } >>"$GITHUB_STEP_SUMMARY"

      - name: Upload the results
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: loadtest-faults-${{ matrix.profile }}
          path: cmstestsuite/loadtest/out/
          overwrite: true
          retention-days: 30
```

  - Run: `.venv/bin/python -c "import yaml; jobs = yaml.safe_load(open('.github/workflows/loadtest.yml'))['jobs']; print(sorted(jobs)); print(jobs['faults']['if'])"`
  - Expected: `['compare', 'faults', 'pair', 'runner-check']` and
    `${{ startsWith(github.ref_name, 'loadtest/faults') }}`.
  - Run: `.venv/bin/python -c "import yaml; open('/tmp/db39-faults-run.sh', 'w').write(yaml.safe_load(open('.github/workflows/loadtest.yml'))['jobs']['faults']['steps'][3]['run'])" && shellcheck -s bash /tmp/db39-faults-run.sh`
  - Expected: no output from shellcheck.

- [ ] **Step 9: Document it.** In `cmstestsuite/loadtest/README.md`, add
  before `## Limits` (line 397):

~~~~markdown
## Fault runs

`run.sh --faults` (fork target only, `--contest` of 2100 s or more) also
starts `faults.py` on the host, next to the driver. It injects the
database faults of #39 at fixed times after the contest start, and
writes their start and end times to `out/<name>/faults.json`:

| Time (s) | Fault |
|---|---|
| 120, 180 | F1: `pg_terminate_backend` of every CMS backend (`application_name LIKE 'cms%'`) |
| 600 | F3: `docker pause` of PostgreSQL for 30 s |
| 1000 | F4: `cmsSetupDB` while a `psql` session holds `BEGIN; SELECT 1 FROM contests LIMIT 1;` idle |
| 1080 | F2: an iptables DROP of PostgreSQL's port, both ways, for 90 s, in its network namespace (`sudo nsenter`); 180 s after its end, the runbook's partition step |

After the run, `faultstats.py out/<name>` checks the pass criteria of
`docs/superpowers/specs/2026-10-08-db-transport-timeouts-design.md` and
writes `out/<name>/faults.md`: one row per criterion, `pass` or `FAIL`.
The injector needs passwordless `sudo`, as GitHub runners have. Whatever
way it stops, it removes its rules and unpauses PostgreSQL.

On GitHub Actions, push the commit to a `loadtest/faults-<topic>` branch:
the `faults` job runs the fork once per profile, smoke-sized, and shows
`faults.md` in its summary (a `FAIL` does not fail the job). A
`loadtest/runner-check-<topic>` branch runs only the 5-minute check that
a runner can use `nsenter`, `iptables` and `docker pause`.
~~~~

- [ ] **Step 10: Lint.**
  - Run: `uvx -q pyflakes cmstestsuite/loadtest/faults.py cmstestsuite/loadtest/faultstats.py cmstestsuite/unit_tests/loadtest/faults_test.py cmstestsuite/unit_tests/loadtest/faultstats_test.py`
  - Run: `uvx -q pycodestyle --max-line-length=79 cmstestsuite/loadtest/faults.py cmstestsuite/loadtest/faultstats.py cmstestsuite/unit_tests/loadtest/faults_test.py cmstestsuite/unit_tests/loadtest/faultstats_test.py`
  - Expected: no output from either (the `# noqa: E402` imports are
    those of `analyze_test.py`).

- [ ] **Step 11: Commit.**

```bash
git add cmstestsuite/loadtest/faults.py cmstestsuite/loadtest/faultstats.py \
    cmstestsuite/loadtest/run.sh cmstestsuite/loadtest/README.md \
    .github/workflows/loadtest.yml \
    cmstestsuite/unit_tests/loadtest/faults_test.py \
    cmstestsuite/unit_tests/loadtest/faultstats_test.py
git commit -F - <<'EOF'
test(loadtest): add a fault mode that checks the database timeouts

run.sh --faults injects the four faults of the #39 design during a
run (backends terminated, a two-way DROP of PostgreSQL's port with the
runbook's partition step, a paused PostgreSQL, and cmsSetupDB behind a
reader of contests) and records them in faults.json; faultstats.py
checks the pass criteria. A loadtest/faults-* branch runs it on GitHub
Actions, once per profile.

Refs #39

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task 10: Validation (Controller)

The controller runs these after Tasks 1-9 are committed and reviewed.
Watch every CI run it starts in the background
(`gh -R AresLOLXD/cms run watch <id> --exit-status`) and report each
result. Before any push, check that the branch tracks no remote:
`git rev-parse --abbrev-ref @{u}` must fail with "no upstream
configured" (the session's pre-push hook pulls only when it does). Keep
downloads in `/var/home/areslolxd/Documentos/cms/.superpowers/` (outside
git) and scratch files in `/home/areslolxd/.claude/jobs/4c257a1a/tmp/`.

**Files:**
- Modify (V1 only): `docs/contest-day.md` and
  `docs/locale/es/LC_MESSAGES/contest-day.po` (the provisional sentence
  of Task 7 Step 1).

**Interfaces:**
- Consumes: everything above; `run.sh --faults` and `faultstats.py`
  (Task 9); the `faults`, `runner-check`, `pair` and `compare` jobs
  (Tasks 8, 9).
- Produces: the results for the final report and the comment on #39.

- [ ] **V0: The CI suite on both images.**
  - Run: `git push origin HEAD:refs/heads/feat/db-transport-timeouts`
    (no `-u`), then find the `ci` run:
    `gh -R AresLOLXD/cms run list --branch feat/db-transport-timeouts --workflow ci --limit 1 --json databaseId -q '.[0].databaseId'`
    and watch it.
  - Pass: both jobs (`ubuntu:noble`, `debian:bookworm`) are green, and in
    `gh -R AresLOLXD/cms run view <id> --log` the lines of
    `connect_options_test.py`, `psycogevent_test.py`,
    `session_exit_test.py`, `evaluationservice_disconnect_test.py`,
    `SetupDBTest.py`, `faults_test.py` and `faultstats_test.py` show no
    `s` (the CI database is TCP, so nothing may skip). The docs workflow
    runs too (`docs/**` changed) and must be green.

- [ ] **V1: How Compose orders `db-init` (local rootful Docker).**
  - Build this branch's image: `/var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts/cmstestsuite/loadtest/build.sh fork HEAD`
  - Create `/home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-v1.sh`:

```bash
#!/bin/bash
# V1 of the #39 plan: does the running cms container keep serving while
# db-init runs during a deploy of docker-compose.prod.yml?
set -euo pipefail
REPO=/var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts
V1=/home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-v1
dc() { sg docker -c "$(printf '%q ' docker --context default "$@")"; }
C() {
  dc compose -f "$V1/docker/docker-compose.prod.yml" --env-file "$V1/.env" \
      -p v1order --profile localdb "$@"
}
token() { python3 -c 'import secrets; print(secrets.token_hex(16))'; }
rm -rf "$V1"
mkdir -p "$V1/docker" "$V1/dumps"
cp "$REPO/docker/docker-compose.prod.yml" "$V1/docker/"
# "../.env" of the compose file resolves to $V1/.env. The build contexts
# are not used (--no-build).
touch "$V1/Dockerfile" "$V1/docker/Dockerfile.ranking"
cat >"$V1/.env" <<EOF
CMS_DB_URL=postgresql+psycopg2://cms:v1pass@db:5432/cmsdb
POSTGRES_USER=cms
POSTGRES_PASSWORD=v1pass
POSTGRES_DB=cmsdb
CMS_SECRET_KEY=$(token)
CMS_ADMIN_USER=admin
CMS_ADMIN_PASSWORD=$(token)
CMS_CONTEST_ID=1
CMS_CWS_HTTP_PORT=18888
CMS_CWS_HTTP_PORT_END=18888
CMS_AWS_HTTP_PORT=18889
CMS_RWS_HTTP_PORT=18890
CMS_LOADER_PORT=19995
CMS_RWS_PASSWORD=$(token)
CMS_CWS_COUNT=1
CMS_WORKER_COUNT=1
EOF
for service in cms db-init ranking; do
  dc tag cmsload-fork:latest "v1order-$service:latest"
done
# A new image id for cms and db-init, as a rebuild gives.
new_build() {
  local service
  for service in cms db-init; do
    printf 'FROM v1order-%s:latest\nLABEL v1-deploy=%s\n' "$service" "$1" |
      dc build -q -t "v1order-$service:latest" -
  done
}
# record LABEL: a deploy, with Compose's events and an AWS poller around it.
record() {
  local label=$1 events poller
  C events --json >"$V1/events-$label.jsonl" &
  events=$!
  while true; do
    printf '%s %s\n' "$(date +%s.%N)" \
      "$(curl -s -o /dev/null -w '%{http_code}' --max-time 1 http://127.0.0.1:18889/ || true)"
    sleep 0.5
  done >"$V1/aws-$label.log" &
  poller=$!
  C ps -q cms >"$V1/cms-before-$label.txt"
  set +e
  # What ./up.sh shows the operator: the runbook says that it does not
  # print db-init's lock message (./logs.sh db-init does).
  C up -d --wait --wait-timeout 90 --no-build >"$V1/up-output-$label.txt" 2>&1
  echo "exit $?" >"$V1/up-$label.txt"
  set -e
  sleep 5
  kill "$events" "$poller"
  C ps -a >"$V1/ps-$label.txt"
  C ps -q cms >"$V1/cms-after-$label.txt"
}
C up -d --wait --wait-timeout 180 --no-build   # the first deploy
new_build 2
record a                                        # a plain deploy
# b: a reader of contests keeps its transaction open for 60 s.
printf 'BEGIN;\nSELECT 1 FROM contests LIMIT 1;\n\\! sleep 60\nCOMMIT;\n' |
  C exec -T db psql -U cms -d cmsdb -q &
holder=$!
sleep 3
new_build 3
record b
C logs --no-color db-init >"$V1/db-init-b.log" 2>&1
wait "$holder" || true
echo "V1 records in $V1"
```

  - Run: `bash /home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-v1.sh`
  - Read the records:

```bash
python3 - /home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-v1 <<'EOF'
import json, os, sys
v1 = sys.argv[1]
read = lambda name: open(os.path.join(v1, name)).read().strip()
for label in ("a", "b"):
    print("== deploy", label, read("up-%s.txt" % label),
          "| cms before", read("cms-before-%s.txt" % label)[:12],
          "after", read("cms-after-%s.txt" % label)[:12])
    for line in open(os.path.join(v1, "events-%s.jsonl" % label)):
        event = json.loads(line)
        if event.get("service") in ("cms", "db-init") and event.get(
                "action") in ("create", "start", "kill", "stop", "die",
                              "destroy"):
            print(" ", event["time"], event["service"], event["action"],
                  event.get("id", "")[:12])
    polls = [line.split() for line in open(os.path.join(v1, "aws-%s.log" % label))]
    bad = [poll for poll in polls if len(poll) < 2 or poll[1] != "200"]
    print("  AWS polls:", len(polls), "not 200:", len(bad), bad[:3])
    print("  up printed:", read("up-output-%s.txt" % label)[-600:])
print(read("db-init-b.log")[-600:])
EOF
```

  - Decide:
    - **Case A, the running version keeps serving:** in deploy a, the
      old `cms` id has no `kill`, `stop` or `die` before db-init's `die`;
      in deploy b, `up` exits non-zero, `db-init-b.log` has
      `cmsSetupDB could not lock contests or ranking_groups within 5 s`,
      `cms-after-b` equals `cms-before-b`, and the AWS polls stay 200.
    - **Case B, the contest is down meanwhile:** the old `cms` stops
      before db-init starts, and the AWS polls fail while db-init runs
      (in deploy b, no `cms` runs after the failed `up`).
    - Anything else: stop and report the records to the user.
    - In either case, note what `up` printed in deploy b
      (`up-output-b.txt`, which is what `./up.sh` shows). If it holds
      `cmsSetupDB could not lock`, report it: the runbook says that
      `./up.sh` does not print the message and that `./logs.sh db-init`
      shows it, and that sentence must then change.
  - Replace the provisional sentence of `docs/contest-day.md` (Task 7
    Step 1), `Check with ./status.sh whether the running version still serves.`
    (with its code span), by the case's sentence, and its `msgstr`
    (`Revisa con ./status.sh si la versión en ejecución sigue atendiendo.`)
    in the same entry of `contest-day.po` (update the `msgid` the way Task
    7 Step 6 does, then the `msgstr`, with no `#, fuzzy` left):
    - Case A: `Meanwhile the running version keeps serving: Compose replaces the cms container only after db-init succeeds.`
      / `Mientras tanto, la versión en ejecución sigue atendiendo: Compose reemplaza el contenedor cms solo después de que db-init termine bien.`
    - Case B: `Meanwhile the contest is down: Compose stops the running cms container before db-init runs, and starts the new one only after db-init succeeds.`
      / `Mientras tanto, la competencia está caída: Compose detiene el contenedor cms en ejecución antes de que corra db-init, y arranca el nuevo solo después de que db-init termine bien.`

    In both languages, write `cms` and `db-init` as code spans.
  - Run `bash /home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-docs-check.sh`
    (Task 7 Step 8): `docs check rc=0`.
  - Commit `docs/contest-day.md` and `contest-day.po` as
    `docs(contest-day): say how a deploy behaves while db-init waits`
    (body: the case and its evidence in one sentence; `Refs #39`;
    the `Co-Authored-By` trailer).
  - Clean up: `sg docker -c "docker --context default compose -f /home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-v1/docker/docker-compose.prod.yml --env-file /home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-v1/.env -p v1order --profile localdb down -v --remove-orphans"`
    and `sg docker -c "docker --context default image rm v1order-cms:latest v1order-db-init:latest v1order-ranking:latest"`.
  - **F4b: `cmsSetupDB` under load, with no holder (local rootful
    Docker, the image built above).** It shows that the 5 s bound does
    not fire on a busy, healthy database (Goal 4).
    - Start a smoke-sized run (it leaves the stack up). Remove an
      earlier `out/db39-f4b` first: `run.sh` keeps the directory, and
      its old `users.json` would start the step below at once.
      `rm -rf /var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts/cmstestsuite/loadtest/out/db39-f4b && /var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts/cmstestsuite/loadtest/run.sh --target fork --profile portable --name db39-f4b --users-a 14 --users-b 6 --login-window 60 --contest 480 --end-burst 120 --rate 10`
    - From a second shell, during its end burst (60 s before the
      contest's end), run `cmsSetupDB` in the `cms` container and record
      its exit status, its duration and its output:

```bash
OUT=/var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts/cmstestsuite/loadtest/out/db39-f4b
LT=/var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts/cmstestsuite/loadtest
until [[ -s $OUT/users.json ]]; do sleep 5; done
start=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["start"])' "$OUT/users.json")
sleep "$(python3 -c "import time; print(max(0, $start + 420 - time.time()))")"
began=$(date +%s.%N)
sg docker -c "docker --context default compose -p cmsload-fork -f $LT/compose.yml --env-file $LT/.env.load exec -T cms cmsSetupDB" >"$OUT/f4b.log" 2>&1
echo "exit $? after $(python3 -c "import time; print(round(time.time() - $began, 1))") s" | tee -a "$OUT/f4b.log"
```

    - Pass: exit 0, and `f4b.log` has no `could not lock`. Keep the
      duration for the report.
    - Then: `/var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts/cmstestsuite/loadtest/teardown.sh fork`.

- [ ] **V2: The fault runs F1-F4 (GitHub Actions, both profiles).**
  - Run: `git push origin HEAD:refs/heads/loadtest/faults-db39`, find the
    `loadtest` run of that branch
    (`gh -R AresLOLXD/cms run list --branch loadtest/faults-db39 --limit 1 --json databaseId -q '.[0].databaseId'`)
    and watch it (about 50 minutes; only the two `faults` jobs run).
  - Download: `gh -R AresLOLXD/cms run download <id> -D /var/home/areslolxd/Documentos/cms/.superpowers/loadtest-db39-faults`
  - Read: `cat /var/home/areslolxd/Documentos/cms/.superpowers/loadtest-db39-faults/loadtest-faults-*/faults-*/faults.md`
  - Pass: in both `faults.md`, every row is `pass`. That covers, per the
    spec's table: F1 (backends ended twice, the lost connection named in
    the logs, no process died, nothing stuck), F2 (each failing call
    within 75 s, then pages, ES and scores back within the limits after
    the partition step; the partition step's count is recorded), F3 (new
    connections fail at about 10 s; pages back after the resume) and F4
    (exit 1 within 15 s with the message; CWS p99 ≤ 6 s).
  - Also read `faults-*/faults.log` (the injector's record lines) and
    note F2's `partition_step_terminated` and F3's `'timeout expired'`
    count for the report.
  - If a row fails: read that run's `cmslog/`, `start.log` and
    `faults.log` in the artifact, report the cause, and do not change a
    limit of `faultstats.py` without the user's decision.
  - Then: `git push origin --delete loadtest/faults-db39`.

- [ ] **V3: No-fault regression runs with a beta baseline in the same
  batch.**
  - Push both sides at once, each with the `full` profile, from detached
    worktrees (one Bash call per side, so the hook's working directory
    stays the feature worktree):

```bash
BASE=$(git -C /var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts merge-base HEAD beta)
for side in regress baseline; do
  if [[ $side == regress ]]; then
    commit=$(git -C /var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts rev-parse HEAD)
  else
    commit=$BASE
  fi
  dir=/home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-$side
  git -C /var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts worktree add --detach "$dir" "$commit"
  sed -i 's/^  LOAD_PROFILE: portable$/  LOAD_PROFILE: full/' "$dir/.github/workflows/loadtest.yml"
  test "$(grep -c '^  LOAD_PROFILE: full$' "$dir/.github/workflows/loadtest.yml")" = 1
  git -C "$dir" commit -qam "ci(loadtest): run the full profile (#39 $side)" \
      -m "Refs #39" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  git -C "$dir" push origin HEAD:refs/heads/loadtest/db39-$side
done
```

  - Watch both `loadtest` runs (4 `pair` jobs each, fork only, then
    `compare`), then download them to
    `/var/home/areslolxd/Documentos/cms/.superpowers/loadtest-db39-regress`
    and `.../loadtest-db39-baseline` with `gh -R AresLOLXD/cms run download <id> -D <dir>`.
  - Compare per CPU model:

```bash
python3 - /var/home/areslolxd/Documentos/cms/.superpowers/loadtest-db39-regress /var/home/areslolxd/Documentos/cms/.superpowers/loadtest-db39-baseline <<'EOF'
"""Per CPU model and side: scored p95, CWS p99, flush p95, drain, stuck
submissions and transport errors of every full run (#39 V3)."""
import collections, datetime, glob, json, os, re, statistics, sys

# "Connection timed out", not a bare "timed out", which would also match
# the sandbox's "Execution timed out" and "Compilation timed out".
TRANSPORT = re.compile(
    r"timeout expired|Connection timed out|closed the connection")


def log_time(line):
    return datetime.datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f").replace(
        tzinfo=datetime.timezone.utc).timestamp()


def pct(values, fraction):
    values = sorted(values)
    return values[int((len(values) - 1) * fraction)] if values else float("nan")


def run_metrics(run_dir):
    metrics = json.load(open(os.path.join(run_dir, "metrics.json")))
    cws = [request["dur"] for request in map(json.loads, open(os.path.join(run_dir, "requests.jsonl")))
           if request["shard"] != "rws"]
    flushes, began = [], None
    for path in sorted(glob.glob(os.path.join(run_dir, "cmslog", "EvaluationService-0", "[0-9]*.log"))):
        for line in open(path, errors="replace"):
            if "Starting commit process" in line:
                began = log_time(line)
            elif line.rstrip().endswith("] Done") and began is not None:
                flushes.append(log_time(line) - began)
                began = None
    errors = sum(1 for path in glob.glob(os.path.join(run_dir, "cmslog", "*", "[0-9]*.log"))
                 for line in open(path, errors="replace") if TRANSPORT.search(line))
    submissions = json.load(open(os.path.join(run_dir, "db_export.json")))["submissions"]
    return {"scored_p95": metrics["scored_p95"], "cws_p99": pct(cws, 0.99),
            "flush_p95": pct(flushes, 0.95), "drain_s": metrics["drain_after_stop_s"],
            "stuck": sum(1 for s in submissions if s["scored_at"] is None),
            "transport_errors": errors}


def cpu_model(artifact):
    for line in open(os.path.join(artifact, "runner.txt")):
        if line.startswith("Model name"):
            return line.split(":", 1)[1].strip()
    return "unknown"


table = collections.defaultdict(list)
for side, root in (("feature", sys.argv[1]), ("baseline", sys.argv[2])):
    for artifact in sorted(glob.glob(os.path.join(root, "loadtest-[0-9]*"))):
        for run_dir in glob.glob(os.path.join(artifact, "ci-*-fork")):
            if os.path.exists(os.path.join(run_dir, "metrics.json")):
                table[(cpu_model(artifact), side)].append(run_metrics(run_dir))
keys = ["scored_p95", "cws_p99", "flush_p95", "drain_s", "stuck", "transport_errors"]
print("| CPU model | side | runs | " + " | ".join(keys) + " |")
print("|" + "---|" * (len(keys) + 3))
for (model, side), runs in sorted(table.items()):
    cells = ["%.2f (%.2f-%.2f)" % (statistics.median(values), min(values), max(values))
             for values in ([run[key] for run in runs] for key in keys)]
    print("| %s | %s | %d | %s |" % (model, side, len(runs), " | ".join(cells)))
EOF
```

  - Pass: every feature run has `stuck` 0 and `transport_errors` 0, and,
    for each CPU model with runs on both sides, the feature's median of
    `scored_p95`, `cws_p99`, `flush_p95` and `drain_s` is at most the
    baseline's maximum on that model (the run-to-run spread). If no CPU
    model has runs on both sides, re-run both workflows once
    (`gh -R AresLOLXD/cms run rerun <id>`); if still none, report the
    table without a verdict.
  - Then delete both branches (`git push origin --delete loadtest/db39-regress loadtest/db39-baseline`)
    and both worktrees
    (`git -C /var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts worktree remove --force <dir>`).

- [ ] **V4: `EXPLAIN ANALYZE` of the 8a selection, on a `full` run's
  database (local rootful Docker).**
  - Run a local full run (it leaves the stack up):
    `/var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts/cmstestsuite/loadtest/run.sh --target fork --profile full --name db39-explain`
    (the image of V1 is this branch's; about 40 minutes).
  - Create `/home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-explain-8a.py`:

```python
"""EXPLAIN ANALYZE the 8a selection exactly as ES builds it (#39 V4)."""
from sqlalchemy.dialects import postgresql

from cms.db import SessionGen
from cms.service.esoperations import get_submission_results_to_finalize

with SessionGen() as session:
    statements = []
    real_execute = session.execute

    def capture(statement, *args, **kwargs):
        statements.append(statement)
        return real_execute(statement, *args, **kwargs)

    session.execute = capture
    found = get_submission_results_to_finalize(session, None)
    session.execute = real_execute
    sql = str(statements[0].compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True}))
    print("results to finalize:", len(found))
    result = session.connection().exec_driver_sql(
        "EXPLAIN (ANALYZE, BUFFERS) " + sql)
    for (line,) in result:
        print(line)
```

  - Run it twice: once during the run's end burst (the last 300 s of the
    contest; from a second shell) and once after `run.sh` returns:
    `sg docker -c "docker --context default compose -p cmsload-fork -f /var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts/cmstestsuite/loadtest/compose.yml --env-file /var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts/cmstestsuite/loadtest/.env.load exec -T cms python3 -" < /home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-explain-8a.py`
    (ES runs with `-c ALL`, so the selection has no contest filter, as
    here).
  - Pass: `Execution Time` under 200 ms both times (the sweep runs every
    117 s under `post_finish_lock`). Keep both plans for the report.
  - Then: `/var/home/areslolxd/Documentos/cms/.worktrees/db-timeouts/cmstestsuite/loadtest/teardown.sh fork`.

- [ ] **V5: Report.** Draft
  `/home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-validation.md`: V0-V4,
  one paragraph each, with the numbers (the case of V1, the `faults.md`
  of both profiles, the V3 table, the two V4 execution times and the
  `sch_netem` line of Task 8). Post it with
  `gh -R AresLOLXD/cms issue comment 39 -F /home/areslolxd/.claude/jobs/4c257a1a/tmp/db39-validation.md`,
  then report to the user. Merging into `beta` is the user's call, after
  this report.

---

## Spec coverage

| Spec item | Where |
|---|---|
| §1 module, defaults, `PROGRAM_NAME`, merge rule, no `options`, no new fields, sample comment, three application points, no `connect_args` | Task 1 |
| §2 green connect deadline, its limits, vendored-file note | Task 2 |
| §3 DDL `SET LOCAL lock_timeout = '5s'`, per-updater `<tables>`, `cmsSetupDB` exit 1 with the message, no retry | Task 6 |
| §4 `is_disconnect`, `sqlstate`, never by class | Task 1 (rule), Tasks 3-4 (use) |
| §5 8c session exit, sync and async | Task 3 |
| §5 8b write wrapper, connect failures, per-row re-raise | Task 4 |
| §5 8a selection, `_finalize_complete_result`, WARNING, no merge of commits | Task 5 |
| §6 runbook section, outage entry, stalled PostgreSQL, holder query, Regenerate warnings, table rows, kill switch, deploy notes, post-deploy check, Spanish `.po` | Task 7; V1's sentence in Task 10 |
| Testing, unit and integration (both groups, TCP skip) | Tasks 1-6, 9 |
| Testing, V1 | Task 10 V1 |
| Testing, fault runs F1-F4, injector, `faults.json`, victims, two-way DROP, partition step, runner check | Tasks 8, 9; Task 10 V2 |
| Testing, no-fault regression with a beta baseline | Task 10 V3 |
| `EXPLAIN ANALYZE` of the 8a query | Task 10 V4 |
| Out of scope (stage 2, 8d-8h, `statement_timeout`, `pool_pre_ping`) | not implemented; Global Constraints forbid them |
