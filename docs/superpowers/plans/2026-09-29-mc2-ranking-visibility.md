# MC-2 Ranking Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an organizer hide a ranking group's public scoreboard from AWS while the staff watch the live ranking with a per-group password, on the same URL.

**Architecture:** Two new `ranking_groups` columns are set from the AWS group pages. ProxyService pushes them to RWS as a `PUT /<group>/visibility` operation, sent before any group data. In RWS, a WSGI guard wraps each group namespace and enforces an allow-list while the group is hidden. The staff log in with a form that sets an HMAC-signed cookie.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Tornado (AWS), Werkzeug + gevent (RWS), `cmscommon.crypto` (bcrypt), pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-mc2-ranking-visibility-design.md`

## Global Constraints

**Repository and tools**
- Worktree: `/var/home/areslolxd/Documentos/cms/.worktrees/multi-contest-rankings`, branch `beta`. Never push.
- Python tools: only `.venv/bin/python3`, `.venv/bin/pytest`, `.venv/bin/pyflakes` from the worktree.
- DB tests need a private database named `<name>fortesting`:
  1. Create it: `PGPASSWORD=cmsuser createdb -h localhost -p 55432 -U cmsuser <name>fortesting`.
  2. Copy `/tmp/claude-1000/-var-home-areslolxd-Documentos-cms/05f74c70-0762-450a-b910-96002ced50c1/scratchpad/ctrl-cms.toml` and change the DB name in its `url` (line 72).
  3. `export CMS_CONFIG=<copy>`.
  4. Drop the database at the end.
- Test groups: `cmstestsuite/unit_tests/cmsranking/` and `db/rankinggroup_test.py` belong to the **gevent** group. Everything else here belongs to the **asyncio** group. Never mix the two groups in one pytest process.
- Wrap every pytest run in `timeout --foreground --signal=ABRT 600` and pass `-p no:cacheprovider`.

**Commits** (other agents commit in the same worktree)
- Stage your paths with `git add <paths>` and commit with `git commit -F <msgfile> -- <paths>`.
- Never use `git add -A`, `-a`, stash, reset, amend or rebase.
- Conventional Commits, in English, with a body.
- End each message with a blank line and `Co-Authored-By: <your accurate model> <noreply@anthropic.com>`.

**Code style**
- PEP 8, with lines of at most 79 columns. pyflakes clean.
- Follow the project's docstring format.
- Code and comments in English.
- New files carry the AGPL header used by the other files.

**Behaviour fixed by the spec**
- Notice text in Spanish: title `Ranking oculto`; body `Este ranking está oculto por ahora.`; label `Contraseña del staff`; error `Contraseña incorrecta.`
- Staff banner: `Vista staff: este ranking está oculto al público`.
- Cookie:
  - Name: `rws_staff`.
  - Value: HMAC-SHA256 with key `bytes.fromhex(secret)` over the message `<group>\n<stored hash>` (UTF-8), hex-encoded.
  - Flags: `HttpOnly`, `SameSite=Lax`, **no `Path`**, `Secure` only over HTTPS.
- Redirects and form actions are relative (`./`, `staff-login`, `staff-logout`).
- A failed login waits `FAILED_LOGIN_DELAY = 1.0` s.
- The notice, 403, login and logout responses send `Cache-Control: no-store`.
- `visibility.json` holds `hidden`, `staff_password` and `secret`. It is written atomically. When it is malformed, the group is treated as hidden with no staff password (fail closed).
- Passwords are hashed with `cmscommon.crypto.hash_password(password, "bcrypt")`. AWS rejects passwords longer than 72 UTF-8 bytes.
- Legacy mode (`contest_id` set) and the root ranking are unchanged.

## Review Focus

1. **A public page opened before the group is hidden.** A `/events` stream admitted while visible must yield nothing after hiding. Tested in Task 2 (`test_open_stream_is_cut_when_hidden`).
2. **RWS behind a reverse proxy that adds a path prefix.** The cookie has no `Path`, and redirects are relative. Tested in Task 3 (`test_login_sets_scoped_cookie_and_relative_redirect` with `SCRIPT_NAME` set).
3. **Passwords with spaces, non-ASCII characters, or over 72 bytes.**
   - AWS keeps spaces (`strip=False`), hashes the UTF-8 bytes, and rejects long passwords with a clear message. Tested in Task 4.
   - RWS validates the UTF-8 password. Tested in Task 3 (`test_non_ascii_password`).
4. **Stale caches across a visibility change or a login.** The notice, 403 and login responses carry `Cache-Control: no-store`. Tested in Tasks 2 and 3.
5. **A hidden group with no staff password.** Everybody sees the notice, login always fails, and AWS flags it in the list. Tested in Tasks 3 and 4.

## File Map

| File | Change | Task |
|---|---|---|
| `cms/db/rankinggroup.py` | add `hidden`, `staff_password` | 1 |
| `cmscontrib/updaters/fork_multi_contest.py` | idempotent `ALTER TABLE` for the two columns | 1 |
| `cmstestsuite/unit_tests/db/rankinggroup_visibility_test.py` | new, DB tests | 1 |
| `cmsranking/visibility.py` | new: state, guard, notice, update endpoint | 2, 3 |
| `cmsranking/RankingWebServer.py` | `NamespaceDispatcher` wraps namespaces in the guard | 2 |
| `cmstestsuite/unit_tests/cmsranking/test_visibility.py` | new, RWS tests | 2, 3 |
| `cms/server/admin/handlers/rankinggroup.py` | `read_ranking_group_visibility` and its wiring | 4 |
| `cms/server/admin/templates/{add_ranking_group,ranking_group,ranking_groups}.html` | form fields and markers | 4 |
| `cmstestsuite/unit_tests/server/admin/rankinggroup_test.py` | new test classes | 4 |
| `docs/multi-contest.md` | operator section "Hiding a ranking" | 4 |
| `cms/service/ProxyService.py` | `VISIBILITY_TYPE` and `_enqueue_visibility` | 5 |
| `cmstestsuite/unit_tests/service/proxyservice_groups_test.py` | visibility tests | 5 |

## Order and Parallelism

- Task 1 and Task 2 can start right away and in parallel. They touch different files.
- Task 3 comes after Task 2 (same files).
- Task 4 comes after Task 1, which provides the model columns.
- Task 5 comes after Task 1 **and** after the SP1 lane's commits to `cms/service/ProxyService.py` and its tests. That lane is rewriting `ProxyExecutor._execute_sync` right now.
- Task 6 runs last.

---

### Task 1: RankingGroup columns and schema migration

**Files:**
- Modify: `cms/db/rankinggroup.py`
- Modify: `cmscontrib/updaters/fork_multi_contest.py` (append to `FORK_MULTI_CONTEST_SQL`)
- Test: `cmstestsuite/unit_tests/db/rankinggroup_visibility_test.py` (new)

**Interfaces:**
- Produces:
  - `RankingGroup.hidden: bool`: not null, Python default `False`.
  - `RankingGroup.staff_password: str | None`: a `cmscommon.crypto` authentication string, or `None`.

- [ ] **Step 1: Write the failing test**

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

"""Tests for the MC-2 visibility columns of ranking groups."""

import unittest

from cms.db import RankingGroup, custom_psycopg2_connection
from cmscontrib.updaters.fork_multi_contest import FORK_MULTI_CONTEST_SQL
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin


def run_sql(sql: str) -> list[tuple]:
    """Execute sql in its own connection and return the fetched rows."""
    conn = custom_psycopg2_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall() if cursor.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


class TestRankingGroupVisibilityColumns(DatabaseMixin, unittest.TestCase):

    def test_defaults(self):
        group = RankingGroup(name="olim", description="OLIM")
        self.session.add(group)
        self.session.commit()
        self.session.refresh(group)
        self.assertIs(group.hidden, False)
        self.assertIsNone(group.staff_password)

    def test_fork_sql_upgrades_a_pre_mc2_table(self):
        # Simulate a database created before MC-2, with one group.
        run_sql("ALTER TABLE ranking_groups DROP COLUMN hidden, "
                "DROP COLUMN staff_password; "
                "INSERT INTO ranking_groups (name, description) "
                "VALUES ('old', 'Old');")
        # Idempotent: applying it twice must not fail.
        run_sql(FORK_MULTI_CONTEST_SQL)
        run_sql(FORK_MULTI_CONTEST_SQL)

        rows = run_sql("SELECT hidden, staff_password FROM ranking_groups "
                       "WHERE name = 'old';")
        self.assertEqual(rows, [(False, None)])
        defaults = run_sql(
            "SELECT column_name, column_default, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'ranking_groups' "
            "AND column_name IN ('hidden', 'staff_password') "
            "ORDER BY column_name;")
        self.assertEqual(defaults, [("hidden", None, "NO"),
                                    ("staff_password", None, "YES")])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout --foreground --signal=ABRT 600 .venv/bin/pytest -p no:cacheprovider cmstestsuite/unit_tests/db/rankinggroup_visibility_test.py -v`
Expected: FAIL. `test_defaults` fails with `AttributeError: 'RankingGroup' object has no attribute 'hidden'`, or with a TypeError on the unknown keyword.

- [ ] **Step 3: Add the columns to the model**

In `cms/db/rankinggroup.py`, change the import to `from sqlalchemy.types import Boolean, Integer, Unicode`. Then append after the `description` column:

```python

    # Whether the group's public scoreboard is hidden (MC-2): RWS then
    # shows a notice to the public and the live ranking only to staff.
    hidden: bool = Column(
        Boolean,
        nullable=False,
        default=False)

    # Staff password as a cmscommon.crypto authentication string (e.g.
    # "bcrypt:..."), or None when nobody can log in to a hidden ranking.
    staff_password: str | None = Column(
        Unicode,
        nullable=True)
```

- [ ] **Step 4: Extend the fork SQL**

In `cmscontrib/updaters/fork_multi_contest.py`, add these statements to `FORK_MULTI_CONTEST_SQL`, after the `DO $$ ... $$;` block and before the closing `"""`:

```sql

ALTER TABLE public.ranking_groups
    ADD COLUMN IF NOT EXISTS hidden boolean NOT NULL DEFAULT false;
ALTER TABLE public.ranking_groups ALTER COLUMN hidden DROP DEFAULT;
ALTER TABLE public.ranking_groups
    ADD COLUMN IF NOT EXISTS staff_password character varying;
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `timeout --foreground --signal=ABRT 600 .venv/bin/pytest -p no:cacheprovider cmstestsuite/unit_tests/db/rankinggroup_visibility_test.py cmstestsuite/unit_tests/db/ -v`
Expected: PASS, with no new failures in `db/`. `db/rankinggroup_test.py` is in the gevent group, so if you want to run it, run it in a separate command.

`schema_diff_test.py` fails locally for an unrelated reason (a `pg_dump` version mismatch). It is validated in the CI container in Task 6. Don't try to fix it locally.

- [ ] **Step 6: Commit**

```bash
git add cms/db/rankinggroup.py cmscontrib/updaters/fork_multi_contest.py cmstestsuite/unit_tests/db/rankinggroup_visibility_test.py
git commit -F msg.txt -- cms/db/rankinggroup.py cmscontrib/updaters/fork_multi_contest.py cmstestsuite/unit_tests/db/rankinggroup_visibility_test.py
# msg.txt: "feat(db): add visibility columns to ranking groups" + body
```

---

### Task 2: RWS visibility state, update endpoint and guard

**Files:**
- Create: `cmsranking/visibility.py`
- Modify: `cmsranking/RankingWebServer.py` (`NamespaceDispatcher`)
- Test: `cmstestsuite/unit_tests/cmsranking/test_visibility.py` (new)

**Interfaces:**
- Consumes: the HTTP contract from the spec. The request is `PUT /<group>/visibility` with Basic proxy credentials and the body `{"hidden": bool, "staff_password": str | null}`. The response is 204, or 400 for a bad body, or 401 without credentials.
- Produces:
  - `cmsranking.visibility.VISIBILITY_FILE = "visibility.json"`
  - `cmsranking.visibility.STAFF_COOKIE = "rws_staff"`
  - `class VisibilityState(group_dir: str)`, with attributes `hidden: bool`, `staff_password: str | None` and `secret: str`, and the method `update(hidden: bool, staff_password: str | None) -> None`.
  - `class VisibilityGuard(app, group_dir: str, group: str, username: str, password: str, realm_name: str)`, a WSGI callable with the class attribute `FAILED_LOGIN_DELAY = 1.0`. Task 3 fills in `_is_staff`, `_login`, `_logout` and `_with_banner`. In this task they are stubs: `_is_staff` returns `False`, and the others are not routed yet.
  - `NamespaceDispatcher._make_namespace(name: str)` returns a guarded app.

- [ ] **Step 1: Write the failing tests**

`cmstestsuite/unit_tests/cmsranking/test_visibility.py`. The AGPL header is as in Task 1; the rest follows.

```python
"""Tests for the MC-2 visibility guard of RWS group namespaces."""

import json
import os
import shutil
import tempfile
import unittest
from base64 import b64encode
from importlib.resources import files

from werkzeug.test import Client

from cmscommon.crypto import build_password
from cmsranking.Config import Config
from cmsranking.RankingWebServer import NamespaceDispatcher, \
    build_ranking_app
from cmsranking.visibility import VISIBILITY_FILE, VisibilityGuard, \
    VisibilityState


USERNAME = "rws"
PASSWORD = "secret"
AUTH = {"Authorization": "Basic " + b64encode(
    ("%s:%s" % (USERNAME, PASSWORD)).encode()).decode()}
CONTEST = {"name": "Day 1", "begin": 0, "end": 10, "score_precision": 0}
STAFF_HASH = build_password("s3cret", "plaintext")

# Every read endpoint of a namespace, including static files.
DATA_PATHS = ["contests/", "contests/c1", "tasks/", "teams/", "users/",
              "submissions/", "subchanges/", "sublist/u1", "scores",
              "history", "events", "config", "logo", "faces/u1",
              "flags/t1", "Ranking.js", "img/favicon.ico"]


class VisibilityTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.lib_dir = os.path.join(self.tmp, "lib")
        self.config = Config(username=USERNAME, password=PASSWORD,
                             lib_dir=self.lib_dir,
                             log_dir=os.path.join(self.tmp, "log"))
        self.web_dir = str(files("cmsranking") / "static")
        self.client = self.make_client()

    def make_client(self) -> Client:
        def make_app(lib_dir):
            return build_ranking_app(self.config, lib_dir, self.web_dir)
        dispatcher = NamespaceDispatcher(
            make_app(self.lib_dir), os.path.join(self.lib_dir, "groups"),
            make_app, USERNAME, PASSWORD, "Scoreboard")
        return Client(dispatcher)

    def put_contest(self, prefix: str):
        return self.client.put(
            prefix + "/contests/", data=json.dumps({"c1": CONTEST}),
            content_type="application/json", headers=AUTH)

    def put_visibility(self, group: str, hidden: bool,
                       staff_password: str | None = STAFF_HASH,
                       auth: bool = True):
        return self.client.put(
            "/%s/visibility" % group,
            data=json.dumps({"hidden": hidden,
                             "staff_password": staff_password}),
            content_type="application/json",
            headers=AUTH if auth else {})


class TestVisibilityUpdate(VisibilityTestCase):

    def test_group_without_state_is_public(self):
        self.put_contest("/olim")
        self.assertEqual(self.client.get("/olim/contests/").json,
                         {"c1": CONTEST})

    def test_put_requires_credentials(self):
        self.put_contest("/olim")
        response = self.put_visibility("olim", True, auth=False)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(os.path.exists(os.path.join(
            self.lib_dir, "groups", "olim", VISIBILITY_FILE)))

    def test_put_rejects_bad_bodies(self):
        self.put_contest("/olim")
        for body in ["not json", json.dumps([]),
                     json.dumps({"hidden": "yes", "staff_password": None}),
                     json.dumps({"hidden": True, "staff_password": 3}),
                     json.dumps({"hidden": True,
                                 "staff_password": "no-method"})]:
            response = self.client.put(
                "/olim/visibility", data=body,
                content_type="application/json", headers=AUTH)
            self.assertEqual(response.status_code, 400, msg=body)

    def test_put_creates_namespace(self):
        self.assertEqual(self.put_visibility("omips", True).status_code,
                         204)
        self.assertTrue(os.path.isfile(os.path.join(
            self.lib_dir, "groups", "omips", VISIBILITY_FILE)))

    def test_state_persists_across_restart(self):
        self.put_contest("/olim")
        self.put_visibility("olim", True)
        self.client = self.make_client()
        self.assertEqual(self.client.get("/olim/contests/").status_code,
                         403)


class TestHiddenGroup(VisibilityTestCase):

    def setUp(self):
        super().setUp()
        self.put_contest("/olim")
        self.put_visibility("olim", True)

    def test_root_page_shows_notice(self):
        response = self.client.get("/olim/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        text = response.get_data(as_text=True)
        self.assertIn("Este ranking está oculto por ahora.", text)
        self.assertIn('action="staff-login"', text)

    def test_every_data_endpoint_is_forbidden(self):
        for path in DATA_PATHS:
            response = self.client.get("/olim/" + path)
            self.assertEqual(response.status_code, 403, msg=path)
            self.assertEqual(response.headers["Cache-Control"], "no-store",
                             msg=path)

    def test_proxy_writes_still_accepted(self):
        response = self.client.put(
            "/olim/users/", data=json.dumps({}),
            content_type="application/json", headers=AUTH)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get("/olim/users/").status_code, 403)

    def test_unhide_restores_public_access(self):
        self.put_visibility("olim", False)
        self.assertEqual(self.client.get("/olim/contests/").json,
                         {"c1": CONTEST})

    def test_root_ranking_and_other_groups_unaffected(self):
        self.put_contest("")
        self.put_contest("/omips")
        self.assertEqual(self.client.get("/contests/").status_code, 200)
        self.assertEqual(self.client.get("/omips/contests/").status_code,
                         200)

    def test_malformed_state_fails_closed(self):
        self.put_contest("/omips")
        path = os.path.join(self.lib_dir, "groups", "omips",
                            VISIBILITY_FILE)
        with open(path, "w") as f:
            f.write("{broken")
        with self.assertLogs("cmsranking.visibility", "ERROR"):
            self.client = self.make_client()
        self.assertEqual(self.client.get("/omips/contests/").status_code,
                         403)


class TestOpenConnections(unittest.TestCase):

    def test_open_stream_is_cut_when_hidden(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        state_holder = {}

        def streaming_app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])

            def body():
                yield b"event 1\n"
                # The group gets hidden while the stream is open.
                state_holder["guard"].state.update(True, None)
                yield b"event 2\n"
            return body()

        guard = VisibilityGuard(streaming_app, tmp, "olim", USERNAME,
                                PASSWORD, "Scoreboard")
        state_holder["guard"] = guard
        response = Client(guard).get("/events")
        self.assertEqual(response.get_data(), b"event 1\n")


class TestVisibilityState(unittest.TestCase):

    def test_update_is_persisted_with_a_stable_secret(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        state = VisibilityState(tmp)
        state.update(True, STAFF_HASH)
        secret = state.secret
        state.update(False, None)
        reloaded = VisibilityState(tmp)
        self.assertEqual((reloaded.hidden, reloaded.staff_password,
                          reloaded.secret), (False, None, secret))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout --foreground --signal=ABRT 600 .venv/bin/pytest -p no:cacheprovider cmstestsuite/unit_tests/cmsranking/test_visibility.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'cmsranking.visibility'`.

- [ ] **Step 3: Create `cmsranking/visibility.py`**

The AGPL header is as in Task 1; the rest follows.

```python
"""Visibility of RWS group rankings: hidden scoreboards and a
password-protected live view for the staff (MC-2).

"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile

from werkzeug.wrappers import Request, Response

from cmscommon.crypto import parse_authentication


logger = logging.getLogger(__name__)


VISIBILITY_FILE = "visibility.json"
STAFF_COOKIE = "rws_staff"

NO_STORE = {"Cache-Control": "no-store"}

NOTICE_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ranking oculto</title>
<style>
body {{ font-family: sans-serif; background: #f4f4f4; color: #222;
       display: flex; min-height: 100vh; margin: 0;
       align-items: center; justify-content: center; }}
main {{ background: #fff; padding: 2em; border-radius: 8px;
       max-width: 26em; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15); }}
.error {{ color: #b00020; }}
</style>
</head>
<body>
<main>
<h1>{group}</h1>
<p>Este ranking está oculto por ahora.</p>
<form method="post" action="staff-login">
<label>Contraseña del staff
<input type="password" name="password"
       autocomplete="current-password" required>
</label>
<button type="submit">Entrar</button>
</form>
{error}
</main>
</body>
</html>
"""


def staff_cookie_value(secret: str, group: str, staff_password: str) -> str:
    """Return the value of a valid staff cookie for a group.

    secret: the group's secret, hex-encoded.
    group: the group name.
    staff_password: the stored authentication string.

    return: the hex-encoded HMAC-SHA256.

    """
    message = ("%s\n%s" % (group, staff_password)).encode("utf-8")
    return hmac.new(bytes.fromhex(secret), message,
                    hashlib.sha256).hexdigest()


class VisibilityState:
    """The visibility settings of one group, stored in its directory.

    """

    def __init__(self, group_dir: str):
        self.path = os.path.join(group_dir, VISIBILITY_FILE)
        self.hidden = False
        self.staff_password: str | None = None
        self.secret = secrets.token_hex(32)
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            hidden = data["hidden"]
            staff_password = data["staff_password"]
            secret = data["secret"]
            if not isinstance(hidden, bool) \
                    or not isinstance(secret, str) or secret == "" \
                    or not (staff_password is None
                            or isinstance(staff_password, str)):
                raise ValueError("Wrong types.")
            bytes.fromhex(secret)
        except (OSError, ValueError, KeyError, TypeError):
            logger.error("Cannot read %s: hiding the ranking until its "
                         "visibility is sent again.", self.path,
                         exc_info=True)
            self.hidden = True
            self.staff_password = None
            return
        self.hidden = hidden
        self.staff_password = staff_password
        self.secret = secret

    def update(self, hidden: bool, staff_password: str | None):
        """Replace the settings, storing them atomically first.

        hidden: whether the public scoreboard is hidden.
        staff_password: the staff authentication string, or None.

        """
        data = {"hidden": hidden, "staff_password": staff_password,
                "secret": self.secret}
        directory = os.path.dirname(self.path)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".vis-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp_path, self.path)
        except BaseException:
            os.unlink(tmp_path)
            raise
        self.hidden = hidden
        self.staff_password = staff_password


class _CutWhenHidden:
    """Wrap a response body so it stops once the group gets hidden."""

    def __init__(self, iterable, state: VisibilityState):
        self._iterable = iterable
        self._state = state

    def __iter__(self):
        for chunk in self._iterable:
            if self._state.hidden:
                return
            yield chunk

    def close(self):
        close = getattr(self._iterable, "close", None)
        if close is not None:
            close()


class VisibilityGuard:
    """WSGI middleware enforcing the visibility of one group namespace.

    """

    FAILED_LOGIN_DELAY = 1.0

    def __init__(self, app, group_dir: str, group: str, username: str,
                 password: str, realm_name: str):
        self.app = app
        self.group = group
        self.username = username
        self.password = password
        self.realm_name = realm_name
        self.state = VisibilityState(group_dir)

    def _writer_authorized(self, request: Request) -> bool:
        return request.authorization is not None and \
            request.authorization.type == "basic" and \
            request.authorization.username == self.username and \
            request.authorization.password == self.password

    def _is_staff(self, request: Request) -> bool:
        # Filled in by Task 3.
        return False

    def __call__(self, environ, start_response):
        request = Request(environ)
        path = request.path
        if path == "/visibility":
            return self._update(request)(environ, start_response)
        if self._is_staff(request):
            return self.app(environ, start_response)
        if not self.state.hidden:
            return _CutWhenHidden(self.app(environ, start_response),
                                  self.state)
        if request.method in ("PUT", "DELETE"):
            # The store handlers check the proxy's credentials.
            return self.app(environ, start_response)
        if path == "/" and request.method in ("GET", "HEAD"):
            return self._notice()(environ, start_response)
        return Response("Este ranking está oculto.", status=403,
                        mimetype="text/plain",
                        headers=NO_STORE)(environ, start_response)

    def _notice(self, error: bool = False, status: int = 200) -> Response:
        body = NOTICE_TEMPLATE.format(
            group=self._escaped_group(),
            error='<p class="error">Contraseña incorrecta.</p>'
                  if error else "")
        return Response(body, status=status, mimetype="text/html",
                        headers=NO_STORE)

    def _escaped_group(self) -> str:
        # Group names are validated to [a-z0-9_-] by RWS and AWS.
        return re.sub(r"[^a-z0-9_-]", "", self.group)

    def _update(self, request: Request) -> Response:
        if request.method != "PUT":
            return Response(status=405, headers={"Allow": "PUT"})
        if not self._writer_authorized(request):
            logger.warning("Unauthorized visibility update.",
                           extra={"location": request.url})
            return Response(
                "Unauthorized", status=401, headers={
                    "WWW-Authenticate":
                        'Basic realm="%s"' % self.realm_name})
        try:
            data = json.loads(request.get_data(as_text=True))
            hidden = data["hidden"]
            staff_password = data["staff_password"]
            if not isinstance(hidden, bool):
                raise ValueError("hidden must be a boolean.")
            if staff_password is not None:
                if not isinstance(staff_password, str):
                    raise ValueError("staff_password must be a string.")
                parse_authentication(staff_password)
        except (ValueError, KeyError, TypeError) as error:
            logger.warning("Bad visibility update: %s.", error)
            return Response(str(error), status=400, mimetype="text/plain")
        self.state.update(hidden, staff_password)
        logger.info("Ranking group %s is now %s.", self.group,
                    "hidden" if hidden else "visible")
        return Response(status=204)
```

`time` and `validate_password` are added in Task 3, where they are first used, so this commit stays pyflakes-clean.

- [ ] **Step 4: Wire the guard into `NamespaceDispatcher`**

In `cmsranking/RankingWebServer.py`, add `from cmsranking.visibility import VisibilityGuard`. In `NamespaceDispatcher`, add the method below and use it in both places that currently call `self.app_factory(...)` for a group: the startup loop in `__init__` and the creation on the first authenticated write in `__call__`.

```python
    def _make_namespace(self, name: str):
        """Build the guarded application of the group namespace name."""
        path = os.path.join(self.groups_dir, name)
        return VisibilityGuard(
            self.app_factory(path), path, name,
            self.username, self.password, self.realm_name)
```

The two call sites become `self.apps[name] = self._make_namespace(name)`. The root app stays unguarded.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `timeout --foreground --signal=ABRT 600 .venv/bin/pytest -p no:cacheprovider cmstestsuite/unit_tests/cmsranking/ -v`
Expected: every test in `test_visibility.py` passes, and `test_namespaces.py` is unchanged and passes too.

- [ ] **Step 6: Commit**

```bash
git add cmsranking/visibility.py cmsranking/RankingWebServer.py cmstestsuite/unit_tests/cmsranking/test_visibility.py
git commit -F msg.txt -- cmsranking/visibility.py cmsranking/RankingWebServer.py cmstestsuite/unit_tests/cmsranking/test_visibility.py
# msg.txt: "feat(rws): hide group rankings behind a visibility guard" + body
```

---

### Task 3: RWS staff login, cookie, logout and banner

**Files:**
- Modify: `cmsranking/visibility.py`
- Test: `cmstestsuite/unit_tests/cmsranking/test_visibility.py`

**Interfaces:**
- Consumes: `VisibilityGuard`, `VisibilityState`, `staff_cookie_value` and `NO_STORE` from Task 2.
- Produces:
  - `POST staff-login` with the form field `password`.
  - `GET staff-logout`.
  - The cookie `rws_staff`.
  - The staff banner on `GET /` for a hidden group.

- [ ] **Step 1: Write the failing tests** (append to `test_visibility.py`)

```python
from unittest.mock import patch

from cmscommon.crypto import hash_password
from cmsranking.visibility import STAFF_COOKIE, staff_cookie_value


class TestStaffLogin(VisibilityTestCase):

    def setUp(self):
        super().setUp()
        patcher = patch("cmsranking.visibility.time.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)
        self.put_contest("/olim")
        self.put_visibility("olim", True)

    def login(self, password: str, group: str = "olim", **environ):
        return self.client.post(
            "/%s/staff-login" % group, data={"password": password},
            environ_overrides=environ)

    def cookie_from(self, response) -> str:
        header = response.headers["Set-Cookie"]
        return header.split(";", 1)[0].split("=", 1)[1]

    def test_login_sets_scoped_cookie_and_relative_redirect(self):
        response = self.login("s3cret", SCRIPT_NAME="/ranking")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "./")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        header = response.headers["Set-Cookie"]
        self.assertTrue(header.startswith(STAFF_COOKIE + "="))
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Lax", header)
        self.assertNotIn("Path=", header)
        self.assertNotIn("Secure", header)

    def test_secure_cookie_over_https(self):
        response = self.client.post(
            "/olim/staff-login", data={"password": "s3cret"},
            headers={"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", response.headers["Set-Cookie"])

    def test_staff_cookie_grants_access_and_banner(self):
        cookie = self.cookie_from(self.login("s3cret"))
        headers = {"Cookie": "%s=%s" % (STAFF_COOKIE, cookie)}
        self.assertEqual(
            self.client.get("/olim/contests/", headers=headers).json,
            {"c1": CONTEST})
        page = self.client.get("/olim/", headers=headers)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Vista staff: este ranking está oculto al público",
                      page.get_data(as_text=True))

    def test_wrong_password(self):
        response = self.login("wrong")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertIn("Contraseña incorrecta.",
                      response.get_data(as_text=True))
        self.sleep.assert_called_once_with(1.0)

    def test_no_staff_password_means_nobody_logs_in(self):
        self.put_visibility("olim", True, staff_password=None)
        self.assertEqual(self.login("s3cret").status_code, 401)

    def test_password_change_invalidates_cookie(self):
        cookie = self.cookie_from(self.login("s3cret"))
        self.put_visibility("olim", True,
                            staff_password=build_password("new", "plaintext"))
        response = self.client.get(
            "/olim/contests/",
            headers={"Cookie": "%s=%s" % (STAFF_COOKIE, cookie)})
        self.assertEqual(response.status_code, 403)

    def test_cookie_of_another_group_is_rejected(self):
        self.put_contest("/omips")
        self.put_visibility("omips", True)
        cookie = self.cookie_from(self.login("s3cret"))
        response = self.client.get(
            "/omips/contests/",
            headers={"Cookie": "%s=%s" % (STAFF_COOKIE, cookie)})
        self.assertEqual(response.status_code, 403)

    def test_logout_clears_cookie(self):
        response = self.client.get("/olim/staff-logout")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "./")
        header = response.headers["Set-Cookie"]
        self.assertTrue(header.startswith(STAFF_COOKIE + "=;"))
        self.assertIn("Expires=", header)
        self.assertNotIn("Path=", header)

    def test_non_ascii_password(self):
        self.put_visibility("olim", True, staff_password=build_password(
            "contraseña", "plaintext"))
        self.assertEqual(self.login("contraseña").status_code, 303)

    def test_bcrypt_password(self):
        self.put_visibility("olim", True,
                            staff_password=hash_password("s3cret"))
        self.assertEqual(self.login("s3cret").status_code, 303)

    def test_visible_group_has_no_banner(self):
        self.put_visibility("olim", False)
        cookie = staff_cookie_value(
            self.client.application.apps["olim"].state.secret, "olim",
            STAFF_HASH)
        page = self.client.get(
            "/olim/", headers={"Cookie": "%s=%s" % (STAFF_COOKIE, cookie)})
        self.assertNotIn("Vista staff", page.get_data(as_text=True))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout --foreground --signal=ABRT 600 .venv/bin/pytest -p no:cacheprovider cmstestsuite/unit_tests/cmsranking/test_visibility.py -v`
Expected: the `TestStaffLogin` tests FAIL. `staff-login` gets 403, because the path is not in the allow-list yet.

- [ ] **Step 3: Implement login, logout, the cookie check and the banner**

In `cmsranking/visibility.py`:

0. Add `import time` to the imports, and change the crypto import to `from cmscommon.crypto import parse_authentication, validate_password`.

1. Add the constants:

```python
STAFF_BANNER = (
    '<div style="background:#b00020;color:#fff;padding:0.5em;'
    'text-align:center;font-family:sans-serif;">Vista staff: este '
    'ranking está oculto al público &middot; '
    '<a style="color:#fff" href="staff-logout">Salir</a></div>'
).encode("utf-8")
BODY_TAG = re.compile(rb"<body[^>]*>", re.IGNORECASE)
```

2. Replace `_is_staff` and add the helpers:

```python
    def _is_staff(self, request: Request) -> bool:
        stored = self.state.staff_password
        cookie = request.cookies.get(STAFF_COOKIE)
        if stored is None or cookie is None:
            return False
        return hmac.compare_digest(
            cookie, staff_cookie_value(self.state.secret, self.group,
                                       stored))

    @staticmethod
    def _is_https(request: Request) -> bool:
        return request.scheme == "https" or request.headers.get(
            "X-Forwarded-Proto", "").lower() == "https"

    def _login(self, request: Request) -> Response:
        password = request.form.get("password", "")
        stored = self.state.staff_password
        valid = False
        if stored is not None and password != "":
            try:
                valid = validate_password(stored, password)
            except ValueError:
                valid = False
        if not valid:
            time.sleep(self.FAILED_LOGIN_DELAY)
            return self._notice(error=True, status=401)
        response = Response(status=303, headers=dict(
            NO_STORE, Location="./"))
        response.set_cookie(
            STAFF_COOKIE,
            staff_cookie_value(self.state.secret, self.group, stored),
            path=None, httponly=True, samesite="Lax",
            secure=self._is_https(request))
        return response

    def _logout(self) -> Response:
        response = Response(status=303, headers=dict(
            NO_STORE, Location="./"))
        response.delete_cookie(STAFF_COOKIE, path=None)
        return response

    def _with_banner(self, environ, start_response):
        captured = {}

        def capture(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = headers
            return lambda data: None

        body_iter = self.app(environ, capture)
        try:
            body = b"".join(body_iter)
        finally:
            close = getattr(body_iter, "close", None)
            if close is not None:
                close()
        body = BODY_TAG.sub(lambda m: m.group(0) + STAFF_BANNER, body,
                            count=1)
        dropped = {"content-length", "last-modified", "etag",
                   "cache-control"}
        headers = [(k, v) for k, v in captured["headers"]
                   if k.lower() not in dropped]
        headers += [("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store")]
        start_response(captured["status"], headers)
        return [body]
```

3. Route the new paths in `__call__`. Replace the body after the `/visibility` branch with:

```python
        if path == "/staff-logout":
            return self._logout()(environ, start_response)
        if self._is_staff(request):
            if self.state.hidden and path == "/" and \
                    request.method == "GET":
                return self._with_banner(environ, start_response)
            return self.app(environ, start_response)
        if not self.state.hidden:
            return _CutWhenHidden(self.app(environ, start_response),
                                  self.state)
        if request.method in ("PUT", "DELETE"):
            # The store handlers check the proxy's credentials.
            return self.app(environ, start_response)
        if path == "/" and request.method in ("GET", "HEAD"):
            return self._notice()(environ, start_response)
        if path == "/staff-login" and request.method == "POST":
            return self._login(request)(environ, start_response)
        return Response("Este ranking está oculto.", status=403,
                        mimetype="text/plain",
                        headers=NO_STORE)(environ, start_response)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout --foreground --signal=ABRT 600 .venv/bin/pytest -p no:cacheprovider cmstestsuite/unit_tests/cmsranking/ -v`
Expected: every test passes. Also run `.venv/bin/pyflakes cmsranking/visibility.py`; it must be clean.

- [ ] **Step 5: Commit**

```bash
git add cmsranking/visibility.py cmstestsuite/unit_tests/cmsranking/test_visibility.py
git commit -F msg.txt -- cmsranking/visibility.py cmstestsuite/unit_tests/cmsranking/test_visibility.py
# msg.txt: "feat(rws): let the staff log in to hidden group rankings" + body
```

---

### Task 4: AWS form, handlers, list markers and operator docs

**Files:**
- Modify: `cms/server/admin/handlers/rankinggroup.py`
- Modify: `cms/server/admin/templates/add_ranking_group.html`, `ranking_group.html`, `ranking_groups.html`
- Modify: `docs/multi-contest.md`
- Test: `cmstestsuite/unit_tests/server/admin/rankinggroup_test.py`

**Interfaces:**
- Consumes: `RankingGroup.hidden` and `RankingGroup.staff_password` from Task 1.
- Produces:
  - Form fields `hidden` (checkbox), `staff_password` (password input) and `remove_staff_password` (checkbox).
  - `read_ranking_group_visibility(handler, attrs) -> None`.

- [ ] **Step 1: Write the failing tests** (append to `rankinggroup_test.py`)

```python
from cms.db import RankingGroup
from cms.server.admin.handlers.rankinggroup import RankingGroupHandler, \
    read_ranking_group_visibility
from cmscommon.crypto import validate_password


def visibility_handler(form: dict) -> MagicMock:
    """Return a fake handler reading hidden/password fields from form."""
    handler = MagicMock()

    def get_bool(dest, name):
        dest[name] = bool(form.get(name, False))

    def get_argument(name, default=None, strip=True):
        value = form.get(name, default)
        if strip and isinstance(value, str):
            value = value.strip()
        return value
    handler.get_bool.side_effect = get_bool
    handler.get_argument.side_effect = get_argument
    return handler


class TestReadRankingGroupVisibility(unittest.TestCase):

    def test_hidden_checkbox(self):
        attrs = dict()
        read_ranking_group_visibility(
            visibility_handler({"hidden": "on"}), attrs)
        self.assertIs(attrs["hidden"], True)
        attrs = dict()
        read_ranking_group_visibility(visibility_handler({}), attrs)
        self.assertIs(attrs["hidden"], False)

    def test_new_password_is_hashed_with_spaces_kept(self):
        attrs = dict()
        read_ranking_group_visibility(
            visibility_handler({"staff_password": " contraseña "}), attrs)
        self.assertTrue(attrs["staff_password"].startswith("bcrypt:"))
        self.assertTrue(validate_password(attrs["staff_password"],
                                          " contraseña "))

    def test_empty_password_keeps_the_current_one(self):
        attrs = {"staff_password": "bcrypt:old"}
        read_ranking_group_visibility(
            visibility_handler({"staff_password": ""}), attrs)
        self.assertEqual(attrs["staff_password"], "bcrypt:old")

    def test_remove_password(self):
        attrs = {"staff_password": "bcrypt:old"}
        read_ranking_group_visibility(
            visibility_handler({"remove_staff_password": "on"}), attrs)
        self.assertIsNone(attrs["staff_password"])

    def test_new_and_remove_conflict(self):
        with self.assertRaises(ValueError):
            read_ranking_group_visibility(
                visibility_handler({"staff_password": "x",
                                    "remove_staff_password": "on"}),
                dict())

    def test_password_over_72_bytes_is_rejected(self):
        with self.assertRaises(ValueError):
            read_ranking_group_visibility(
                visibility_handler({"staff_password": "ñ" * 37}), dict())


class TestRankingGroupHandlerSavesVisibility(unittest.TestCase):

    def test_edit_hides_and_sets_password(self):
        group = RankingGroup(name="olim", description="OLIM",
                             hidden=False, staff_password=None)
        handler = RankingGroupHandler.__new__(RankingGroupHandler)
        handler.application = MagicMock()
        handler.safe_get_item = MagicMock(return_value=group)
        form = {"name": "olim", "description": "OLIM", "hidden": "on",
                "staff_password": "pw"}

        def get_string(dest, name, empty=""):
            if name in form:
                dest[name] = form[name] if form[name] != "" else empty
        fake = visibility_handler(form)
        handler.get_string = MagicMock(side_effect=get_string)
        handler.get_bool = fake.get_bool
        handler.get_argument = fake.get_argument
        handler.try_commit = MagicMock(return_value=True)
        handler.schedule_rpc = MagicMock()
        handler.redirect = MagicMock()
        handler.url = MagicMock(return_value="/ranking_group/1")

        handler._post_sync("1")

        self.assertIs(group.hidden, True)
        self.assertTrue(validate_password(group.staff_password, "pw"))
        handler.schedule_rpc.assert_called_once_with(
            handler.service.proxy_service.reinitialize)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout --foreground --signal=ABRT 600 .venv/bin/pytest -p no:cacheprovider cmstestsuite/unit_tests/server/admin/rankinggroup_test.py -v`
Expected: collection fails with `ImportError: cannot import name 'read_ranking_group_visibility'`.

- [ ] **Step 3: Implement the form reading and its wiring**

In `cms/server/admin/handlers/rankinggroup.py`, add `from cmscommon.crypto import hash_password`, then the constant and the function below, right after `read_ranking_group_attrs`:

```python
# bcrypt only uses the first 72 bytes of a password.
MAX_STAFF_PASSWORD_BYTES = 72


def read_ranking_group_visibility(handler: BaseHandler, attrs: dict):
    """Read the visibility fields of the form (MC-2) into attrs.

    An empty password field keeps attrs["staff_password"] as it is, so
    editing a group without retyping the password keeps it.

    handler: the handler whose request carries the form.
    attrs: where to store hidden and staff_password.

    raise (ValueError): if a new password and its removal are both
        requested, or if the new password is too long.

    """
    handler.get_bool(attrs, "hidden")
    new_password = handler.get_argument("staff_password", "", strip=False)
    remove = handler.get_argument(
        "remove_staff_password", None) is not None
    if new_password and remove:
        raise ValueError(
            "Set a new staff password or remove it, not both.")
    if remove:
        attrs["staff_password"] = None
    elif new_password:
        if len(new_password.encode("utf-8")) > MAX_STAFF_PASSWORD_BYTES:
            raise ValueError(
                "The staff password is too long (at most %d bytes)."
                % MAX_STAFF_PASSWORD_BYTES)
        attrs["staff_password"] = hash_password(new_password, "bcrypt")
```

Call it right after `read_ranking_group_attrs(self, attrs)` in both handlers:
- in `AddRankingGroupHandler._post_sync`, before `RankingGroup(**attrs)`;
- in `RankingGroupHandler._post_sync`, before `group.set_attrs(attrs)`.

The call is `read_ranking_group_visibility(self, attrs)`.

- [ ] **Step 4: Update the templates**

In `ranking_group.html`, add these rows inside the form table, after the Description row:

```html
      <tr>
        <td>
          <span class="info" title="While hidden, the public scoreboard shows a notice and no data. The staff log in there with the staff password to see the live ranking."></span>
          Hide ranking from the public
        </td>
        <td><input type="checkbox" name="hidden" {% if ranking_group.hidden %}checked{% endif %}/></td>
      </tr>
      <tr>
        <td>
          <span class="info" title="Leave empty to keep the current password."></span>
          Staff password
        </td>
        <td>
          <input type="password" name="staff_password" autocomplete="new-password"/>
          {% if ranking_group.staff_password %}(set){% else %}(not set){% endif %}
          <label><input type="checkbox" name="remove_staff_password"/> Remove staff password</label>
        </td>
      </tr>
```

In `add_ranking_group.html`, add the same two rows, with `name="hidden"` unchecked by default and without the "(set)" marker or the remove checkbox.

In `ranking_groups.html`:
- Add two headers after `Contests`: `<th>Hidden</th><th>Staff password</th>`.
- Add the matching cells:

```html
        <td>{% if g.hidden %}yes{% if not g.staff_password %} (nobody can see it: no staff password){% endif %}{% else %}no{% endif %}</td>
        <td>{% if g.staff_password %}set{% else %}not set{% endif %}</td>
```

- [ ] **Step 5: Operator docs**

Add a section `## Hiding a ranking (staff view)` to `docs/multi-contest.md` that explains:
- Tick "Hide ranking from the public" and set a staff password on the group page. The public URL then shows a notice, and the staff log in on that same page.
- To reveal the ranking, untick the box. It takes effect within seconds.
- Changing the password logs out every staff session.
- A hidden group without a password is visible to nobody.
- Rolling RWS back to a version without this feature makes hidden groups public.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `timeout --foreground --signal=ABRT 600 .venv/bin/pytest -p no:cacheprovider cmstestsuite/unit_tests/server/admin/ -v`
Expected: PASS, with no new failures.

- [ ] **Step 7: Commit**

```bash
git add cms/server/admin/handlers/rankinggroup.py cms/server/admin/templates/add_ranking_group.html cms/server/admin/templates/ranking_group.html cms/server/admin/templates/ranking_groups.html cmstestsuite/unit_tests/server/admin/rankinggroup_test.py docs/multi-contest.md
git commit -F msg.txt -- cms/server/admin/handlers/rankinggroup.py cms/server/admin/templates/add_ranking_group.html cms/server/admin/templates/ranking_group.html cms/server/admin/templates/ranking_groups.html cmstestsuite/unit_tests/server/admin/rankinggroup_test.py docs/multi-contest.md
# msg.txt: "feat(aws): configure ranking visibility and staff password" + body
```

---

### Task 5: ProxyService visibility operation

**Prerequisite:** the SP1 lane has committed its changes to `cms/service/ProxyService.py` and `proxyservice_groups_test.py`. Check with `git log --oneline -5 -- cms/service/ProxyService.py`. Re-read `ProxyExecutor._execute_sync` as committed before starting: SP1 changes how failures are handled, per group and per entity type.

**Files:**
- Modify: `cms/service/ProxyService.py`
- Test: `cmstestsuite/unit_tests/service/proxyservice_groups_test.py`

**Interfaces:**
- Consumes:
  - `RankingGroup.hidden` and `RankingGroup.staff_password` (Task 1).
  - The RWS endpoint `PUT <ranking>/<group>/visibility` (Task 2).
- Produces:
  - `ProxyExecutor.VISIBILITY_TYPE`.
  - `ProxyService._enqueue_visibility(session, group: str | None = None) -> None`.

- [ ] **Step 1: Write the failing tests** (append to `proxyservice_groups_test.py`, reusing its fixtures `start()`, `_settle()`, `put_urls()` and `put_payload()`)

```python
    async def test_visibility_is_sent_before_group_data(self):
        service = await self.start()
        await self._settle(service)
        urls = self.put_urls()
        visibility = url("olim/visibility")
        self.assertIn(visibility, urls)
        self.assertLess(urls.index(visibility),
                        urls.index(url("olim/contests/")))
        self.assertEqual(self.put_payload(visibility),
                         {"hidden": False, "staff_password": None})

    async def test_reinitialize_sends_new_visibility(self):
        service = await self.start()
        await self._settle(service)
        self.requests_put.reset_mock()
        self.olim.hidden = True
        self.olim.staff_password = "plaintext:pw"
        self.session.commit()
        await service.reinitialize()
        await self._settle(service)
        self.assertEqual(self.put_payload(url("olim/visibility")),
                         {"hidden": True, "staff_password": "plaintext:pw"})

    async def test_regenerate_sends_visibility(self):
        service = await self.start()
        await self._settle(service)
        self.requests_put.reset_mock()
        await service.regenerate_ranking("olim")
        await self._settle(service)
        self.assertIn(url("olim/visibility"), self.put_urls())

    async def test_legacy_mode_never_sends_visibility(self):
        service = await self.start(contest_id=self.contest_a.id)
        await self._settle(service)
        self.assertFalse(
            any(u.endswith("/visibility") for u in self.put_urls()))

    async def test_rejected_visibility_holds_back_group_data(self):
        def put(target, *args, **kwargs):
            response = MagicMock()
            response.status_code = 400 if target.endswith(
                "olim/visibility") else 200
            return response
        self.requests_put.side_effect = put
        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            service = await self.start()
            await self._settle(service)
        self.assertNotIn(url("olim/contests/"), self.put_urls())
        self.assertTrue(any("Regenerate" in line for line in logs.output))
```

These names already exist in the file's fixture: `url()`, `self.olim` and `self.omips` (`RankingGroup` rows), `self.contest_a` (group `olim`), `self.session`, `self.requests_put`, `self.start()` and `self._settle()`. If SP1 renamed any of them, follow SP1's version.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout --foreground --signal=ABRT 600 .venv/bin/pytest -p no:cacheprovider cmstestsuite/unit_tests/service/proxyservice_groups_test.py -v`
Expected: the new tests FAIL, because no `.../visibility` PUT is ever made.

- [ ] **Step 3: Implement**

In `ProxyExecutor`, after the `RESET_TYPE` block:

```python
    # Pseudo-type of an operation that sends the visibility settings of
    # a ranking group namespace (MC-2): {"hidden": bool,
    # "staff_password": str | None}. Sent after resets and before any
    # data, so a hidden namespace never exposes data, even briefly.
    VISIBILITY_TYPE = TYPE_COUNT + 1
```

In `_execute_sync`, as committed by SP1, do the following:
- Collect the visibility entries, last one wins per group: `visibilities[item.group] = item.data`. Keep them out of the per-type data lists.
- After the resets and **before** any data PUT, send each one with:

```python
                operation = "sending visibility to ranking %s%s" % (
                    self._visible_ranking, self._prefix(group))
                logger.debug(operation.capitalize())
                safe_put_data(self._ranking,
                              "%svisibility" % self._prefix(group),
                              settings, operation)
```

- Apply SP1's failure policy to it, as its own (group, type) pair:
  - Transport errors and 5xx are requeued.
  - On a 4xx, log the SP1 "use Regenerate for group X" WARNING, and also **drop that group's data in this batch**. Never send data for a group whose visibility could not be delivered.

In `ProxyService`, add `RankingGroup` to the `cms.db` imports and this method:

```python
    def _enqueue_visibility(self, session: Session,
                            group: str | None = None) -> None:
        """Enqueue the visibility settings of the ranking groups.

        Only in group mode: legacy mode has no groups.

        session: the session to read the groups with.
        group: only this group, or None for every group.

        """
        if self.contest_id is not None:
            return
        query = select(RankingGroup)
        if group is not None:
            query = query.filter(RankingGroup.name == group)
        for ranking_group in session.execute(query).scalars().all():
            self._threadsafe_enqueue(ProxyOperation(
                ProxyExecutor.VISIBILITY_TYPE,
                {"hidden": ranking_group.hidden,
                 "staff_password": ranking_group.staff_password},
                ranking_group.name))
```

Call it in two places:
- In `initialize()`, as the first statement inside `with SessionGen() as session:`, before the contest loop: `self._enqueue_visibility(session)`.
- In `_regenerate_ranking_sync`, right after enqueuing the RESET, when `group is not None`: `self._enqueue_visibility(session, group)`. Move the RESET enqueue inside the existing `with SessionGen() as session:` block if needed.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout --foreground --signal=ABRT 600 .venv/bin/pytest -p no:cacheprovider cmstestsuite/unit_tests/service/proxyservice_groups_test.py cmstestsuite/unit_tests/service/ProxyService_test.py -v`
Expected: PASS. Then loop the groups test file 10 times; it must pass 10 out of 10.

- [ ] **Step 5: Commit**

```bash
git add cms/service/ProxyService.py cmstestsuite/unit_tests/service/proxyservice_groups_test.py
git commit -F msg.txt -- cms/service/ProxyService.py cmstestsuite/unit_tests/service/proxyservice_groups_test.py
# msg.txt: "feat(proxy): send ranking group visibility to RWS" + body
```

---

### Task 6: End-to-end verification

This task changes no code, unless it finds a bug. A bug goes back to its task's owner as a failing test first.

- [ ] **Step 1: Full CI target on rootful Docker.**
  1. Snapshot `HEAD` with `git archive` into scratch and pre-create `codecov/` with mode 777.
  2. Run the CI with `sg docker -c "docker --context default compose -p cms-mc2-ci -f docker/docker-compose.test.yml run -T --rm testcms"`.

  Expected: both unit groups are green, and `schema_diff_test` passes in the container. The functional suite runs with isolate; compare it with the baseline run.
- [ ] **Step 2: Browser flow in Chromium,** with the UI-testing agent. Bring up a multi-contest stack as documented in `docs/multi-contest.md` and `docs/docker-scripts.md`, on rootful Docker with a unique compose project, publishing AWS and RWS locally. Then:
  1. In AWS, create the group `olim`, assign a contest to it, tick Hide, and set the staff password `Prueba-2026`.
  2. Open `/<ranking>/olim/`: the notice appears.
  3. Open `/<ranking>/olim/contests/` and `/<ranking>/olim/events`: both return 403.
  4. Log in with a wrong password: an error appears after about 1 s.
  5. Log in with the right password: the live ranking appears, with the staff banner.
  6. Submit a solution: the staff view updates live.
  7. Untick Hide in AWS: within seconds the public URL shows the ranking without logging in.
  8. View the source of the AWS group page: neither the password nor its hash appears.
- [ ] **Step 3: Clean up.** Take the compose projects down with `-v` and remove their images.

---

## Self-Review Notes

- **Spec coverage.**
  - Data model and migration: Task 1.
  - AWS: Task 4.
  - ProxyService: Task 5.
  - RWS state, update endpoint, allow-list, fail-closed, notice, open streams and `no-store`: Task 2.
  - Login, cookie, logout and banner: Task 3.
  - Operations docs: Task 4.
  - Testing section: Tasks 1-6.
- **Names stay consistent across tasks:** `VISIBILITY_FILE`, `STAFF_COOKIE`, `VisibilityState.update(hidden, staff_password)`, `VisibilityGuard(app, group_dir, group, username, password, realm_name)`, `staff_cookie_value(secret, group, staff_password)`, `read_ranking_group_visibility(handler, attrs)`, `ProxyExecutor.VISIBILITY_TYPE` and `_enqueue_visibility(session, group=None)`.
- **Known dependency.** Task 5's executor edit rides on SP1's rewrite of `_execute_sync`. Its step 3 names the exact behaviour to add, not line numbers.
