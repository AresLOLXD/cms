# Multi-Contest Deployment with Per-Group Rankings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One CMS stack runs several contests at once; from the AWS, without restarts, the operator marks contests active (visible in CWS) and assigns them to ranking groups, each served by RWS as an independent scoreboard at `/<group>/`.

**Architecture:** A new `RankingGroup` DB entity plus `Contest.active` / `Contest.ranking_group_id` (migrated automatically by `cmsSetupDB`). RWS gains a dispatcher that serves one isolated ranking app per group under `lib_dir/groups/<group>/`, keeping the root ranking unchanged. ProxyService runs without `-c` in "group mode", tags every operation with its group, lets each executor partition batches by group, resets a namespace when it loses a contest, and exposes a `regenerate_ranking` RPC used by an AWS button that replaces `clear-ranking.sh`.

**Tech Stack:** Python 3.11+, SQLAlchemy 1.3 (`cms/db`), PostgreSQL, gevent, Tornado (AWS/CWS), werkzeug (RWS), pytest/unittest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-23-multi-contest-rankings-design.md`

## Global Constraints

- Legacy mode must be unchanged: with `-c N` (ProxyService, CWS) the requests sent to RWS and the contests served are exactly as before; the RWS root namespace (`/`) behaves exactly as before.
- Upstream DB `version` in `cms/db/__init__.py` stays `49`; no `update_N.py` is added.
- Group name rule, defined once in `cmscommon/ranking_groups.py`: regex `[a-z0-9_-]+` (full match) and not in the reserved set `contests, tasks, teams, users, submissions, subchanges, faces, flags, sublist, history, scores, events, logo, config, lib, img, groups`.
- `active` only controls CWS visibility in multi-contest mode; ranking membership depends only on `ranking_group_id`.
- A `RESET` deletes only `contests/` and `users/` of a namespace (never `teams/`).
- All code, comments, identifiers and commit messages in English; docs in English (like the rest of `docs/`).
- `pyflakes` clean on every touched Python file; PEP 484 annotations on new functions; docstrings in the project format (imperative first line, then args/return/raise).
- Use `.venv/bin/python3` / `.venv/bin/pytest`, never system Python.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01DAtRQki2btdfj86biQhUQH
  ```

### Running DB-backed tests locally

Tests using `DatabaseMixin` need a PostgreSQL database whose name ends in `fortesting`. Set it up once per session:

```bash
mkdir -p /tmp/cms-mc/bin
podman run -d --rm --name cms-testdb \
  -e POSTGRES_USER=cmsuser -e POSTGRES_PASSWORD=cmsuser \
  -e POSTGRES_DB=cmsdbfortesting -p 55432:5432 docker.io/library/postgres:16
sed 's#^url = .*#url = "postgresql+psycopg2://cmsuser:cmsuser@localhost:55432/cmsdbfortesting"#' \
  config/cms.sample.toml > /tmp/cms-mc/cms-test.toml
# schema_diff_test needs pg_dump; this shim runs it inside the container.
cat > /tmp/cms-mc/bin/pg_dump <<'EOF'
#!/usr/bin/env bash
exec podman exec cms-testdb pg_dump --schema-only -U cmsuser cmsdbfortesting
EOF
chmod +x /tmp/cms-mc/bin/pg_dump
```

Then prefix DB-backed test runs with `PATH=/tmp/cms-mc/bin:$PATH CMS_CONFIG=/tmp/cms-mc/cms-test.toml` (abbreviated below as `$DBENV`). If `podman` is not available, `docker` works with the same arguments.

## Review Focus

1. **A contest moved from group A to group B while both scoreboards are live** — A must stop showing it (reset), B must show it; owned by Task 5 (`test_moving_contest_resets_old_group`).
2. **Two contests of one group sharing a contestant (day 1 + day 2)** — the user appears once and a normal reinitialize never empties the scoreboard; owned by Task 5 (`test_shared_user_single_entry_and_no_reset`).
3. **A group name that shadows an RWS path or static entry (`events`, `lib`, `img`, …)** — rejected by AWS, routed to the root by RWS; owned by Task 1 (`test_static_entries_are_reserved`) and Task 3 (`test_reserved_name_is_served_by_root`).
4. **RWS restart after namespaces were created** — namespaces (even empty ones) reload from disk; an unauthenticated write never creates one; owned by Task 3 (`test_namespaces_reload_from_disk`, `test_unauthenticated_put_does_not_create_namespace`).
5. **Importing a dump exported before this change, and exporting a contest that shares a group** — old dumps import with defaults; `RankingGroup` has no backref so exporting A never drags B; owned by Task 2 (`test_old_dump_contest_gets_defaults`, `test_ranking_group_has_no_relationships`).

---

### Task 1: Ranking group name validation (`cmscommon`)

**Files:**
- Create: `cmscommon/ranking_groups.py`
- Test: `cmstestsuite/unit_tests/cmscommon/ranking_groups_test.py`

**Interfaces:**
- Produces: `cmscommon.ranking_groups.GROUP_NAME_RE: re.Pattern`, `RESERVED_GROUP_NAMES: frozenset[str]`, `is_valid_group_name(name: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for ranking group name validation."""

import unittest
from importlib.resources import files

from cmscommon.ranking_groups import GROUP_NAME_RE, RESERVED_GROUP_NAMES, \
    is_valid_group_name


class TestIsValidGroupName(unittest.TestCase):

    def test_accepts_slugs(self):
        for name in ["olim", "omips", "day-1", "senior_2026", "a"]:
            self.assertTrue(is_valid_group_name(name), name)

    def test_rejects_invalid_characters(self):
        for name in ["", "Olim", "olim/", "ol im", "olím", "olim.",
                     "olim\n"]:
            self.assertFalse(is_valid_group_name(name), repr(name))

    def test_rejects_reserved_names(self):
        for name in RESERVED_GROUP_NAMES:
            self.assertFalse(is_valid_group_name(name), name)

    def test_static_entries_are_reserved(self):
        # Any top-level static entry that looks like a group name would be
        # shadowed by the namespace dispatcher.
        static = files("cmsranking") / "static"
        for entry in static.iterdir():
            if GROUP_NAME_RE.fullmatch(entry.name):
                self.assertIn(entry.name, RESERVED_GROUP_NAMES)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/cmscommon/ranking_groups_test.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cmscommon.ranking_groups'`

- [ ] **Step 3: Write minimal implementation**

`cmscommon/ranking_groups.py` (copy the AGPL header block from `cmscommon/constants.py`, with a `Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>` line):

```python
"""Validation of ranking group names.

A ranking group name is the first path segment of an RWS URL
(``/<group>/``), so it must be URL-safe and must not shadow any path
the root RWS app already serves.

"""

import re

__all__ = ["GROUP_NAME_RE", "RESERVED_GROUP_NAMES", "is_valid_group_name"]


GROUP_NAME_RE = re.compile(r"[a-z0-9_-]+")

# First path segments served by the root RWS app: store handlers,
# routed handlers and top-level entries of cmsranking/static/, plus
# "groups", the directory holding the namespaces.
RESERVED_GROUP_NAMES = frozenset({
    "contests", "tasks", "teams", "users", "submissions", "subchanges",
    "faces", "flags", "sublist", "history", "scores", "events", "logo",
    "config", "lib", "img", "groups",
})


def is_valid_group_name(name: str) -> bool:
    """Return whether name can be used as a ranking group name.

    name: the candidate name.

    return: True if it is a URL-safe slug that is not reserved.

    """
    return GROUP_NAME_RE.fullmatch(name) is not None \
        and name not in RESERVED_GROUP_NAMES
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/cmscommon/ranking_groups_test.py -v && .venv/bin/pyflakes cmscommon/ranking_groups.py cmstestsuite/unit_tests/cmscommon/ranking_groups_test.py`
Expected: 4 passed; no pyflakes output.

- [ ] **Step 5: Commit**

```bash
git add cmscommon/ranking_groups.py cmstestsuite/unit_tests/cmscommon/ranking_groups_test.py
git commit -m "feat(cmscommon): add ranking group name validation"
```

---

### Task 2: `RankingGroup` model, contest columns and automatic migration

**Files:**
- Create: `cms/db/rankinggroup.py`
- Modify: `cms/db/contest.py` (imports; new columns after the `main_group` relationship)
- Modify: `cms/db/__init__.py` (`__all__`, import)
- Create: `cmscontrib/updaters/fork_multi_contest.py`
- Modify: `cmscontrib/SetupDB.py` (`setup_db()`)
- Modify: `cmstestsuite/unit_tests/schema_diff_test.py` (`get_updated_schema`)
- Test: `cmstestsuite/unit_tests/db/rankinggroup_test.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `cms.db.RankingGroup` (columns `id: int`, `name: str` unique, `description: str`; **no relationships**); `Contest.active: bool` (default `False`), `Contest.ranking_group_id: int | None`, `Contest.ranking_group: RankingGroup | None`; `cmscontrib.updaters.fork_multi_contest.FORK_MULTI_CONTEST_SQL: str` and `apply_fork_multi_contest_update() -> None`.

- [ ] **Step 1: Write the failing tests**

`cmstestsuite/unit_tests/db/rankinggroup_test.py`:

```python
"""Tests for ranking groups, the new contest columns and their migration."""

import unittest
from unittest.mock import MagicMock

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import Contest, RankingGroup
from cmscontrib.DumpImporter import DumpImporter
from cmscontrib.updaters.fork_multi_contest import \
    apply_fork_multi_contest_update


class TestRankingGroupModel(DatabaseMixin, unittest.TestCase):

    def test_contest_defaults(self):
        contest = self.add_contest()
        self.session.commit()
        self.assertFalse(contest.active)
        self.assertIsNone(contest.ranking_group_id)
        self.assertIsNone(contest.ranking_group)

    def test_contest_can_join_group(self):
        group = RankingGroup(name="olim", description="OLIM")
        self.session.add(group)
        contest = self.add_contest(active=True, ranking_group=group)
        self.session.commit()
        self.assertEqual(contest.ranking_group.name, "olim")

    def test_deleting_group_unsets_contests(self):
        group = RankingGroup(name="olim", description="OLIM")
        self.session.add(group)
        contest = self.add_contest(ranking_group=group)
        self.session.commit()
        self.session.delete(group)
        self.session.commit()
        self.session.refresh(contest)
        self.assertIsNone(contest.ranking_group_id)

    def test_ranking_group_has_no_relationships(self):
        # DumpExporter follows every relationship: a backref to contests
        # would make exporting one contest drag every contest of its group.
        self.assertEqual(RankingGroup._rel_props, [])


class TestForkMigration(DatabaseMixin, unittest.TestCase):

    def test_update_is_idempotent(self):
        # init_db (run by DatabaseMixin) already created everything; the
        # update must be a no-op, also when applied twice.
        apply_fork_multi_contest_update()
        apply_fork_multi_contest_update()


class TestOldDumpCompatibility(unittest.TestCase):

    def test_old_dump_contest_gets_defaults(self):
        data = {"_class": "Contest", "name": "old", "description": "Old"}
        contest = DumpImporter.import_object(MagicMock(), data)
        self.assertFalse(contest.active)
        self.assertIsNone(contest.ranking_group_id)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$DBENV .venv/bin/pytest cmstestsuite/unit_tests/db/rankinggroup_test.py -v`
Expected: FAIL with `ImportError: cannot import name 'RankingGroup' from 'cms.db'`

- [ ] **Step 3: Add the model**

`cms/db/rankinggroup.py` (AGPL header as in `cms/db/admin.py`, with your copyright line):

```python
"""Ranking group database interface for SQLAlchemy.

"""

from sqlalchemy.schema import Column
from sqlalchemy.types import Integer, Unicode

from . import Base


class RankingGroup(Base):
    """Class to store a ranking group.

    A ranking group is an independent public scoreboard, served by RWS
    at /<name>/, that one or more contests are sent to. It deliberately
    has no relationship to its contests (see Contest.ranking_group):
    DumpExporter follows every relationship, and a backref would make
    exporting one contest also export every contest of its group.

    """
    __tablename__ = 'ranking_groups'

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    # URL slug, validated with cmscommon.ranking_groups.
    name: str = Column(
        Unicode,
        nullable=False,
        unique=True)

    # Human readable title.
    description: str = Column(
        Unicode,
        nullable=False)
```

In `cms/db/contest.py`, add `from .rankinggroup import RankingGroup` to the `typing.TYPE_CHECKING` block:

```python
if typing.TYPE_CHECKING:
    from . import Task, Participation, Group
    from .rankinggroup import RankingGroup
```

and insert right after the `main_group` relationship (before the `# These one-to-many relationships…` comment):

```python
    # Whether contestants can see and use this contest when CWS serves
    # all contests (it is ignored when CWS serves a single contest).
    active: bool = Column(
        Boolean,
        nullable=False,
        default=False)

    # Ranking group (id and object) this contest is sent to, or None
    # if it is not sent to any ranking.
    ranking_group_id: int | None = Column(
        Integer,
        ForeignKey("ranking_groups.id",
                   onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True)
    ranking_group: "RankingGroup | None" = relationship("RankingGroup")
```

In `cms/db/__init__.py`, add `"RankingGroup",` to `__all__` right after `"Contest", "Announcement",` under a `# rankinggroup` comment, and add the import right after `from .contest import Contest, Announcement`:

```python
from .rankinggroup import RankingGroup
```

- [ ] **Step 4: Add the migration module**

`cmscontrib/updaters/fork_multi_contest.py` (AGPL header):

```python
"""Schema update of this fork for multi-contest rankings.

The SQL is a Python constant because Docker installs CMS non-editable
and setup.py does not package .sql files. Every statement is
idempotent, so it is safe to apply at each cmsSetupDB run.

"""

from cms.db import custom_psycopg2_connection


FORK_MULTI_CONTEST_SQL = """
CREATE TABLE IF NOT EXISTS public.ranking_groups (
    id serial PRIMARY KEY,
    name character varying NOT NULL UNIQUE,
    description character varying NOT NULL
);

ALTER TABLE public.contests
    ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT false;
ALTER TABLE public.contests ALTER COLUMN active DROP DEFAULT;

ALTER TABLE public.contests
    ADD COLUMN IF NOT EXISTS ranking_group_id integer;
CREATE INDEX IF NOT EXISTS ix_contests_ranking_group_id
    ON public.contests USING btree (ranking_group_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'contests_ranking_group_id_fkey'
    ) THEN
        ALTER TABLE ONLY public.contests
            ADD CONSTRAINT contests_ranking_group_id_fkey
            FOREIGN KEY (ranking_group_id)
            REFERENCES public.ranking_groups(id)
            ON UPDATE CASCADE ON DELETE SET NULL;
    END IF;
END
$$;
"""


def apply_fork_multi_contest_update() -> None:
    """Apply the fork's idempotent schema update to the database.

    """
    conn = custom_psycopg2_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(FORK_MULTI_CONTEST_SQL)
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 5: Run the model tests**

Run: `$DBENV .venv/bin/pytest cmstestsuite/unit_tests/db/rankinggroup_test.py -v`
Expected: 6 passed.

- [ ] **Step 6: Make schema_diff_test apply the fork SQL (failing first)**

In `cmstestsuite/unit_tests/schema_diff_test.py`, add the import next to the others:

```python
from cmscontrib.updaters.fork_multi_contest import FORK_MULTI_CONTEST_SQL
```

and change the loop in `get_updated_schema`:

```python
    for sql in [schema_sql, updater_sql, FORK_MULTI_CONTEST_SQL]:
```

Before editing, run `$DBENV .venv/bin/pytest cmstestsuite/unit_tests/schema_diff_test.py -v` and confirm it FAILS ("Fresh schema contains extra statement" for `ranking_groups`/`active`) — this proves the model change is visible to the test. After editing, run it again.
Expected: PASS. If a statement differs (e.g. sequence options or constraint wording), change `FORK_MULTI_CONTEST_SQL` until the updated schema matches the fresh one printed in the error; do not touch the fresh model to fit the SQL.

- [ ] **Step 7: Apply the update in `cmsSetupDB`**

In `cmscontrib/SetupDB.py` add the import:

```python
from cmscontrib.updaters.fork_multi_contest import \
    apply_fork_multi_contest_update
```

and change `setup_db()`:

```python
    init_db()  # raises on failure; return value is always True
    # init_db creates missing tables but never alters existing ones.
    apply_fork_multi_contest_update()
    if not ensure_first_admin():
        return False
```

Update its docstring first line to `"""Initialize or update DB schema, create first admin, offer sample contest.`

- [ ] **Step 8: Run all DB-related tests and pyflakes**

Run: `$DBENV .venv/bin/pytest cmstestsuite/unit_tests/db/ cmstestsuite/unit_tests/schema_diff_test.py cmstestsuite/unit_tests/cmscontrib/ -q && .venv/bin/pyflakes cms/db cmscontrib/SetupDB.py cmscontrib/updaters/fork_multi_contest.py cmstestsuite/unit_tests/db/rankinggroup_test.py cmstestsuite/unit_tests/schema_diff_test.py`
Expected: all pass; no pyflakes output.

- [ ] **Step 9: Commit**

```bash
git add cms/db/rankinggroup.py cms/db/contest.py cms/db/__init__.py \
  cmscontrib/updaters/fork_multi_contest.py cmscontrib/SetupDB.py \
  cmstestsuite/unit_tests/schema_diff_test.py cmstestsuite/unit_tests/db/rankinggroup_test.py
git commit -m "feat(db): add ranking groups and contest active flag with automatic migration"
```

---

### Task 3: RWS namespaces per ranking group

**Files:**
- Modify: `cmsranking/RankingWebServer.py` (imports; new `build_ranking_app`; new `NamespaceDispatcher`; `main()`)
- Test: `cmstestsuite/unit_tests/cmsranking/test_namespaces.py`

**Interfaces:**
- Consumes: `cmscommon.ranking_groups.is_valid_group_name` (Task 1).
- Produces: `build_ranking_app(config: Config, lib_dir: str, web_dir: str) -> WSGI app`; `NamespaceDispatcher(root_app, groups_dir: str, app_factory: Callable[[str], WSGI app], username: str, password: str, realm_name: str)` with attribute `apps: dict[str, WSGI app]`. URLs: `/<group>/<anything>` → group app with the prefix stripped; `/<group>` → 301 to `/<group>/`.

- [ ] **Step 1: Write the failing tests**

`cmstestsuite/unit_tests/cmsranking/test_namespaces.py`:

```python
"""Tests for per-group ranking namespaces in RWS."""

import json
import os
import shutil
import tempfile
import unittest
from base64 import b64encode
from importlib.resources import files

from werkzeug.test import Client

from cmsranking.Config import Config
from cmsranking.RankingWebServer import NamespaceDispatcher, \
    build_ranking_app


USERNAME = "rws"
PASSWORD = "secret"
AUTH = {"Authorization": "Basic " + b64encode(
    ("%s:%s" % (USERNAME, PASSWORD)).encode()).decode()}
CONTEST = {"name": "Day 1", "begin": 0, "end": 10, "score_precision": 0}


class TestNamespaces(unittest.TestCase):

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

    def put_contest(self, prefix: str, auth: bool = True):
        return self.client.put(
            prefix + "/contests/", data=json.dumps({"c1": CONTEST}),
            content_type="application/json", headers=AUTH if auth else {})

    def get_contests(self, prefix: str):
        return self.client.get(prefix + "/contests/")

    def test_root_namespace_unchanged(self):
        self.assertEqual(self.put_contest("").status_code, 204)
        self.assertEqual(self.get_contests("").json, {"c1": CONTEST})

    def test_put_creates_isolated_group_namespace(self):
        self.assertEqual(self.put_contest("/olim").status_code, 204)
        self.assertTrue(
            os.path.isdir(os.path.join(self.lib_dir, "groups", "olim")))
        self.assertEqual(self.get_contests("/olim").json, {"c1": CONTEST})
        self.assertEqual(self.get_contests("").json, {})

    def test_unauthenticated_put_does_not_create_namespace(self):
        self.assertEqual(self.put_contest("/olim", auth=False).status_code,
                         401)
        self.assertFalse(
            os.path.exists(os.path.join(self.lib_dir, "groups", "olim")))

    def test_get_unknown_namespace_is_404(self):
        self.assertEqual(self.get_contests("/omips").status_code, 404)

    def test_reserved_name_is_served_by_root(self):
        self.put_contest("")
        # "contests" is reserved, so /contests/ is the root store, not a
        # namespace called "contests".
        self.assertEqual(self.get_contests("").json, {"c1": CONTEST})
        self.assertFalse(
            os.path.exists(os.path.join(self.lib_dir, "groups", "contests")))

    def test_namespaces_reload_from_disk(self):
        self.put_contest("/olim")
        self.client.delete("/omips/users/", headers=AUTH)  # empty namespace
        self.client = self.make_client()
        self.assertEqual(self.get_contests("/olim").json, {"c1": CONTEST})
        self.assertEqual(self.client.get("/omips/users/").status_code, 200)

    def test_group_without_trailing_slash_redirects(self):
        self.put_contest("/olim")
        response = self.client.get("/olim")
        self.assertEqual(response.status_code, 301)
        self.assertTrue(response.headers["Location"].endswith("/olim/"))

    def test_group_serves_frontend(self):
        self.put_contest("/olim")
        response = self.client.get("/olim/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")

    def test_delete_list_only_affects_its_namespace(self):
        self.put_contest("")
        self.put_contest("/olim")
        self.assertEqual(
            self.client.delete("/olim/contests/", headers=AUTH).status_code,
            204)
        self.assertEqual(self.get_contests("/olim").json, {})
        self.assertEqual(self.get_contests("").json, {"c1": CONTEST})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/cmsranking/test_namespaces.py -v`
Expected: FAIL with `ImportError: cannot import name 'NamespaceDispatcher'`

- [ ] **Step 3: Extract `build_ranking_app`**

In `cmsranking/RankingWebServer.py`:

1. Add imports: `from collections.abc import Callable`, `from werkzeug.utils import redirect`, and `from cmscommon.ranking_groups import is_valid_group_name`; change `from cmsranking.Config import PublicConfig, load_config` to `from cmsranking.Config import Config, PublicConfig, load_config`.
2. Add this function right above `def main()`, moving into it (not copying) the current block of `main()` that goes from `stores: dict[str, Store] = dict()` through the `wsgi_app = SharedDataMiddleware(...)` statement, replacing every `config.lib_dir` inside it with `lib_dir`:

```python
def build_ranking_app(config: Config, lib_dir: str, web_dir: str):
    """Build the WSGI app serving one ranking stored in lib_dir.

    config: the RWS configuration (credentials, buffer size, public
        settings).
    lib_dir: the directory holding this ranking's data.
    web_dir: the directory with the static frontend files.

    return: the WSGI application.

    """
    os.makedirs(lib_dir, exist_ok=True)

    stores: dict[str, Store] = dict()
    # ... the moved block, with config.lib_dir -> lib_dir ...

    return wsgi_app
```

3. In `main()`, where the moved block was, put:

```python
    def make_app(lib_dir: str):
        return build_ranking_app(config, lib_dir, web_dir)

    wsgi_app = NamespaceDispatcher(
        make_app(config.lib_dir), os.path.join(config.lib_dir, "groups"),
        make_app, config.username, config.password, config.realm_name)
```

- [ ] **Step 4: Add the dispatcher**

Add above `build_ranking_app`:

```python
class NamespaceDispatcher:
    """Route /<group>/... to a per-group ranking, the rest to the root.

    Each group namespace is a complete, isolated ranking built by
    app_factory over groups_dir/<group>/. Namespaces found on disk are
    loaded at startup; a new one is created by the first authenticated
    write (PUT or DELETE) addressed to it. Paths whose first segment is
    not a valid group name (including reserved ones) go to the root.

    """

    def __init__(
        self,
        root_app,
        groups_dir: str,
        app_factory: Callable[[str], object],
        username: str,
        password: str,
        realm_name: str,
    ):
        self.root_app = root_app
        self.groups_dir = groups_dir
        self.app_factory = app_factory
        self.username = username
        self.password = password
        self.realm_name = realm_name
        self.apps: dict[str, object] = dict()

        os.makedirs(groups_dir, exist_ok=True)
        for name in sorted(os.listdir(groups_dir)):
            path = os.path.join(groups_dir, name)
            if is_valid_group_name(name) and os.path.isdir(path):
                self.apps[name] = app_factory(path)

    def authorized(self, request: Request) -> bool:
        return request.authorization is not None and \
            request.authorization.type == "basic" and \
            request.authorization.username == self.username and \
            request.authorization.password == self.password

    def __call__(self, environ, start_response):
        path: str = environ.get("PATH_INFO", "")
        name, slash, rest = path.lstrip("/").partition("/")
        if not is_valid_group_name(name):
            return self.root_app(environ, start_response)

        app = self.apps.get(name)
        if app is None:
            request = Request(environ)
            if request.method not in ("PUT", "DELETE"):
                return NotFound()(environ, start_response)
            if not self.authorized(request):
                return CustomUnauthorized(self.realm_name)(
                    environ, start_response)
            logger.info("Creating ranking namespace %s.", name)
            app = self.app_factory(os.path.join(self.groups_dir, name))
            self.apps[name] = app

        script_name = environ.get("SCRIPT_NAME", "") + "/" + name
        if not slash:
            return redirect(script_name + "/", code=301)(
                environ, start_response)

        environ["SCRIPT_NAME"] = script_name
        environ["PATH_INFO"] = "/" + rest
        return app(environ, start_response)
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/cmsranking/ -v && .venv/bin/pyflakes cmsranking/RankingWebServer.py cmstestsuite/unit_tests/cmsranking/test_namespaces.py`
Expected: all pass (including the existing `test_seed.py` / `test_mx_states.py`); no pyflakes output.

- [ ] **Step 6: Smoke-test the real server**

```bash
cat > /tmp/cms-mc/rws.toml <<'EOF'
bind_address = "127.0.0.1"
http_port = 18890
username = "rws"
password = "secret"
lib_dir = "/tmp/cms-mc/rws-lib"
log_dir = "/tmp/cms-mc/rws-log"
EOF
CMS_RANKING_CONFIG=/tmp/cms-mc/rws.toml .venv/bin/cmsRankingWebServer &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" -u rws:secret -X PUT -H 'Content-Type: application/json' \
  -d '{"c1":{"name":"Day 1","begin":0,"end":10,"score_precision":0}}' http://127.0.0.1:18890/olim/contests/
curl -s http://127.0.0.1:18890/olim/contests/; echo
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18890/
kill %1
```
Expected: `204`, then `{"c1": {...}}`, then `200`.

- [ ] **Step 7: Commit**

```bash
git add cmsranking/RankingWebServer.py cmstestsuite/unit_tests/cmsranking/test_namespaces.py
git commit -m "feat(rws): serve an isolated ranking namespace per group"
```

---

### Task 4: ProxyService operations and executor per group

**Files:**
- Modify: `cms/service/ProxyService.py` (`safe_delete_data` new; `ProxyOperation`; `ProxyExecutor`)
- Test: `cmstestsuite/unit_tests/service/proxyexecutor_test.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ProxyOperation(type_: int, data: dict, group: str | None = None)` with attribute `group`; `ProxyExecutor.RESET_TYPE: int` (equals `TYPE_COUNT`); `ProxyExecutor.RESET_RESOURCE_PATHS = ["contests", "users"]`; `safe_delete_data(ranking: str, resource: str, operation: str) -> None` raising `CannotSendError`. Group `None` means the root namespace; group `"g"` sends to `"g/<resource>/"` relative to the ranking URL.

- [ ] **Step 1: Write the failing tests**

`cmstestsuite/unit_tests/service/proxyexecutor_test.py`:

```python
"""Tests for ProxyExecutor's per-group batching and resets."""

import gevent.monkey
gevent.monkey.patch_all()  # noqa

import unittest
from datetime import datetime
from unittest.mock import MagicMock, call, patch

from cms.io.priorityqueue import QueueEntry
from cms.service.ProxyService import ProxyExecutor, ProxyOperation


RANKING = "http://rws:secret@localhost:8890/"


def entries(*operations):
    return [QueueEntry(op, 0, datetime.now(), i)
            for i, op in enumerate(operations)]


class TestProxyExecutorGroups(unittest.TestCase):

    def setUp(self):
        self.calls = MagicMock()
        for name in ["safe_put_data", "safe_delete_data"]:
            patcher = patch("cms.service.ProxyService.%s" % name)
            self.calls.attach_mock(patcher.start(), name)
            self.addCleanup(patcher.stop)
        self.executor = ProxyExecutor(RANKING)

    def put_calls(self):
        return [(c.args[1], c.args[2]) for c in self.calls.mock_calls
                if c[0] == "safe_put_data"]

    def test_root_operations_use_plain_paths(self):
        self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}})))
        self.assertEqual(self.put_calls(), [("contests/", {"c": {}})])

    def test_group_operations_use_prefixed_paths(self):
        self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim")))
        self.assertEqual(self.put_calls(), [("olim/contests/", {"c": {}})])

    def test_batch_is_split_by_group(self):
        self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u1": {}}, "olim"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u2": {}}, "omips"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u3": {}}, "olim")))
        self.assertCountEqual(self.put_calls(), [
            ("olim/users/", {"u1": {}, "u3": {}}),
            ("omips/users/", {"u2": {}})])

    def test_reset_discards_earlier_data_and_runs_before_puts(self):
        self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.USER_TYPE, {"stale": {}}, "olim"),
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim"),
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim")))
        names = [c[0] for c in self.calls.mock_calls]
        self.assertEqual(names, ["safe_delete_data", "safe_delete_data",
                                 "safe_put_data"])
        self.assertEqual(
            [c.args[1] for c in self.calls.mock_calls[:2]],
            ["olim/contests/", "olim/users/"])
        self.assertEqual(self.put_calls(), [("olim/contests/", {"c": {}})])

    def test_reset_of_root_namespace(self):
        self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, None)))
        self.assertEqual(self.calls.mock_calls, [
            call.safe_delete_data(RANKING, "contests/", unittest.mock.ANY),
            call.safe_delete_data(RANKING, "users/", unittest.mock.ANY)])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/service/proxyexecutor_test.py -v`
Expected: FAIL (`TypeError: ProxyOperation.__init__() takes 3 positional arguments but 4 were given` / `AttributeError: ... RESET_TYPE`).

- [ ] **Step 3: Add `safe_delete_data`**

Right after `safe_put_data` in `cms/service/ProxyService.py`:

```python
def safe_delete_data(ranking: str, resource: str, operation: str):
    """Delete a whole resource list from ranking using a DELETE request.

    ranking: the URL of ranking server.
    resource: the relative path of the entity list.
    operation: a human-readable description of the operation
        we're performing (to produce log messages).

    raise (CannotSendError): in case of communication errors.

    """
    try:
        url = urljoin(ranking, resource)
        auth = urlsplit(url)
        res = requests.delete(url,
                              auth=(auth.username, auth.password),
                              verify=config.proxy_service.https_certfile)
    except requests.exceptions.RequestException as error:
        msg = "%s while %s: %s." % (type(error).__name__, operation, error)
        logger.warning(msg)
        raise CannotSendError(msg)
    if 400 <= res.status_code < 600:
        msg = "Status %s while %s." % (res.status_code, operation)
        logger.warning(msg)
        raise CannotSendError(msg)
```

- [ ] **Step 4: Tag operations with their group**

Replace `ProxyOperation` with:

```python
class ProxyOperation(QueueItem):

    def __init__(self, type_: int, data: dict, group: str | None = None):
        """Create an operation for the ranking namespace of group.

        type_: one of ProxyExecutor's *_TYPE constants.
        data: the entities to send, by id (empty for a reset).
        group: the ranking group namespace, or None for the root.

        """
        self.type_ = type_
        self.data = data
        self.group = group

    def __str__(self):
        return "sending data of type %s to ranking %s" % (
            self.type_, self.group if self.group is not None else "(root)")

    def to_dict(self):
        return {"type": self.type_,
                "data": self.data,
                "group": self.group}
```

- [ ] **Step 5: Partition batches by group in the executor**

In `ProxyExecutor`, right after `TYPE_COUNT = len(RESOURCE_PATHS)` add:

```python
    # Pseudo-type of an operation that empties a ranking namespace.
    # Deleting contests and users is enough: RWS cascades to tasks,
    # submissions and subchanges. Teams are kept because RWS seeds them
    # at startup; leftover teams are harmless.
    RESET_TYPE = TYPE_COUNT
    RESET_RESOURCE_PATHS = ["contests", "users"]
```

add this static method to the class:

```python
    @staticmethod
    def _prefix(group: str | None) -> str:
        """Return the resource path prefix of a ranking namespace."""
        return "" if group is None else "%s/" % group
```

and replace the body of `execute` from `# The cumulative data that we will try to send…` to the end of the method with:

```python
        # The cumulative data that we will try to send to the ranking,
        # per namespace (None is the root), built by combining items in
        # the queue.
        data: dict[str | None, list[dict]] = dict()
        # Namespaces to empty before sending data, in arrival order.
        resets: list[str | None] = list()

        for entry in entries:
            item = entry.item
            if item.type_ == self.RESET_TYPE:
                # Data queued before the reset is obsolete.
                data.pop(item.group, None)
                if item.group not in resets:
                    resets.append(item.group)
            else:
                group_data = data.setdefault(
                    item.group, list(dict() for i in range(self.TYPE_COUNT)))
                group_data[item.type_].update(item.data)

        try:
            for group in resets:
                for name in self.RESET_RESOURCE_PATHS:
                    operation = "deleting %s from ranking %s%s" % (
                        name, self._visible_ranking, self._prefix(group))
                    logger.debug(operation.capitalize())
                    safe_delete_data(self._ranking, "%s%s/" % (
                        self._prefix(group), name), operation)

            for group, group_data in data.items():
                for i in range(self.TYPE_COUNT):
                    # Send entities of type i.
                    if len(group_data[i]) > 0:
                        # We abuse the resource path as the English
                        # (plural) name for the entity type.
                        name = self.RESOURCE_PATHS[i]
                        operation = "sending %s to ranking %s%s" % (
                            name, self._visible_ranking, self._prefix(group))

                        logger.debug(operation.capitalize())
                        safe_put_data(
                            self._ranking, "%s%s/" % (
                                self._prefix(group), name),
                            group_data[i], operation)
                        group_data[i].clear()

        except CannotSendError:
            # A log message has already been produced.
            gevent.sleep(self.FAILURE_WAIT)
        except:
            # Whoa! That's unexpected!
            logger.error("Unexpected error.", exc_info=True)
            gevent.sleep(self.FAILURE_WAIT)
```

- [ ] **Step 6: Run the tests (new and existing)**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/service/proxyexecutor_test.py -v && $DBENV .venv/bin/pytest cmstestsuite/unit_tests/service/ProxyServiceTest.py -v && .venv/bin/pyflakes cms/service/ProxyService.py cmstestsuite/unit_tests/service/proxyexecutor_test.py`
Expected: 5 passed, then 1 passed (legacy startup order unchanged); no pyflakes output.

- [ ] **Step 7: Commit**

```bash
git add cms/service/ProxyService.py cmstestsuite/unit_tests/service/proxyexecutor_test.py
git commit -m "feat(proxy): route ranking operations per group and support namespace resets"
```

---

### Task 5: ProxyService group mode, reset on composition change, `regenerate_ranking`

**Files:**
- Modify: `cms/service/ProxyService.py` (`ProxyService` class)
- Modify: `cms/service/ResourceService.py` (drop the two "ProxyService requires contest_id" guards)
- Test: `cmstestsuite/unit_tests/service/proxyservice_groups_test.py`

**Interfaces:**
- Consumes: `ProxyOperation(type_, data, group)`, `ProxyExecutor.RESET_TYPE` (Task 4); `Contest.ranking_group`, `RankingGroup` (Task 2).
- Produces: `ProxyService(shard: int, contest_id: int | None = None)`; RPC `regenerate_ranking(group: str | None = None)` (Task 7's AWS button calls `proxy_service.regenerate_ranking(group=...)`); RPC `reinitialize()` now resets groups that lost contests.

- [ ] **Step 1: Write the failing tests**

`cmstestsuite/unit_tests/service/proxyservice_groups_test.py`:

```python
"""Tests for ProxyService in group mode (no contest id)."""

import gevent.monkey
gevent.monkey.patch_all()  # noqa

import json
import unittest
from unittest.mock import patch, PropertyMock
from urllib.parse import urljoin

import gevent

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.db import RankingGroup
from cms.service.ProxyService import ProxyService, encode_id
from cmscommon.constants import SCORE_MODE_MAX


RANKING = config.proxy_service.rankings[0]


def url(resource: str) -> str:
    return urljoin(RANKING, resource)


class TestProxyServiceGroups(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()

        patcher = patch("cms.db.Dataset.score_type_object",
                        new_callable=PropertyMock)
        score_type = patcher.start().return_value
        self.addCleanup(patcher.stop)
        score_type.max_score = 100
        score_type.ranking_headers = ["100"]

        patcher = patch("requests.put")
        self.requests_put = patcher.start()
        self.addCleanup(patcher.stop)
        self.requests_put.return_value.status_code = 200

        patcher = patch("requests.delete")
        self.requests_delete = patcher.start()
        self.addCleanup(patcher.stop)
        self.requests_delete.return_value.status_code = 204

        self.olim = RankingGroup(name="olim", description="OLIM")
        self.omips = RankingGroup(name="omips", description="OMIPS")
        self.session.add_all([self.olim, self.omips])

        self.user = self.add_user()
        self.contest_a, self.sub_a = self.add_contest_with_submission(
            self.olim)
        self.contest_b, self.sub_b = self.add_contest_with_submission(
            self.omips)
        self.contest_c, self.sub_c = self.add_contest_with_submission(None)
        self.session.commit()

    def add_contest_with_submission(self, group):
        contest = self.add_contest(ranking_group=group)
        task = self.add_task(contest=contest)
        task.score_mode = SCORE_MODE_MAX
        dataset = self.add_dataset(task=task)
        task.active_dataset = dataset
        participation = self.add_participation(user=self.user,
                                               contest=contest)
        submission = self.add_submission(task=task,
                                         participation=participation)
        result = self.add_submission_result(submission=submission,
                                            dataset=dataset)
        result.compilation_outcome = "ok"
        result.evaluation_outcome = "ok"
        result.score = 100
        result.score_details = dict()
        result.public_score = 50
        result.public_score_details = dict()
        result.ranking_score_details = ["100"]
        return contest, submission

    def start(self, contest_id=None) -> ProxyService:
        service = ProxyService(0, contest_id)
        gevent.sleep(0.1)
        return service

    def clear_requests(self):
        self.requests_put.reset_mock()
        self.requests_delete.reset_mock()

    def put_urls(self) -> list[str]:
        return [c.args[0] for c in self.requests_put.call_args_list]

    def put_payload(self, target: str) -> dict:
        payload = dict()
        for c in self.requests_put.call_args_list:
            if c.args[0] == target:
                payload.update(json.loads(c.args[1]))
        return payload

    def delete_urls(self) -> list[str]:
        return [c.args[0] for c in self.requests_delete.call_args_list]

    def test_startup_sends_each_contest_to_its_group(self):
        self.start()
        self.assertEqual(set(self.put_payload(url("olim/contests/"))),
                         {encode_id(self.contest_a.name)})
        self.assertEqual(set(self.put_payload(url("omips/contests/"))),
                         {encode_id(self.contest_b.name)})
        self.assertNotIn(url("contests/"), self.put_urls())
        self.assertIn(url("olim/submissions/"), self.put_urls())

    def test_legacy_mode_sends_to_root(self):
        self.start(self.contest_c.id)
        self.assertEqual(set(self.put_payload(url("contests/"))),
                         {encode_id(self.contest_c.name)})
        self.assertFalse(any("olim/" in u or "omips/" in u
                             for u in self.put_urls()))

    def test_submission_of_contest_without_group_is_ignored(self):
        service = self.start()
        self.clear_requests()
        service.submission_scored(self.sub_c.id)
        gevent.sleep(0.1)
        self.assertEqual(self.put_urls(), [])

    def test_submission_scored_goes_to_its_group(self):
        service = self.start()
        self.clear_requests()
        service.submission_scored(self.sub_b.id)
        gevent.sleep(0.1)
        self.assertIn(url("omips/submissions/"), self.put_urls())
        self.assertNotIn(url("olim/submissions/"), self.put_urls())

    def test_moving_contest_resets_old_group(self):
        service = self.start()
        self.clear_requests()
        self.contest_a.ranking_group = self.omips
        self.session.commit()
        service.reinitialize()
        gevent.sleep(0.1)
        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        self.assertIn(encode_id(self.contest_a.name),
                      self.put_payload(url("omips/contests/")))
        self.assertIn(url("omips/submissions/"), self.put_urls())

    def test_shared_user_single_entry_and_no_reset(self):
        # Day 2 of OLIM, with the same contestant as day 1.
        contest_a2, _ = self.add_contest_with_submission(self.olim)
        self.session.commit()
        service = self.start()
        self.assertEqual(set(self.put_payload(url("olim/users/"))),
                         {encode_id(self.user.username)})
        self.assertEqual(set(self.put_payload(url("olim/contests/"))),
                         {encode_id(self.contest_a.name),
                          encode_id(contest_a2.name)})
        self.clear_requests()
        self.contest_a.description = "Renamed"
        self.session.commit()
        service.reinitialize()
        gevent.sleep(0.1)
        self.assertEqual(self.delete_urls(), [])

    def test_regenerate_group_only_touches_its_namespace(self):
        service = self.start()
        self.clear_requests()
        service.regenerate_ranking("olim")
        gevent.sleep(0.1)
        self.assertEqual(self.delete_urls(),
                         [url("olim/contests/"), url("olim/users/")])
        # Already-sent scores are sent again.
        self.assertIn(url("olim/submissions/"), self.put_urls())
        self.assertFalse(any("omips/" in u for u in self.put_urls()))

    def test_regenerate_root_in_group_mode_empties_it(self):
        service = self.start()
        self.clear_requests()
        service.regenerate_ranking(None)
        gevent.sleep(0.1)
        self.assertEqual(self.delete_urls(),
                         [url("contests/"), url("users/")])
        self.assertEqual(self.put_urls(), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$DBENV .venv/bin/pytest cmstestsuite/unit_tests/service/proxyservice_groups_test.py -v`
Expected: FAIL (`TypeError: ProxyService.__init__() missing 1 required positional argument: 'contest_id'` in most tests).

- [ ] **Step 3: Constructor and contest selection helpers**

In `cms/service/ProxyService.py` change the signature and docstring of `__init__`:

```python
    def __init__(self, shard: int, contest_id: int | None = None):
        """Start the service with the given parameters.

        Create an instance of the ProxyService and make it listen on
        the address corresponding to the given shard.

        shard: the shard of the service, i.e. this instance
            corresponds to the shard-th entry in the list of addresses
            (hostname/port pairs) for this kind of service in the
            configuration file.
        contest_id: the ID of the only contest to send to the root of
            the rankings (legacy mode), or None to send every contest
            that has a ranking group to its group's namespace (group
            mode).

        """
```

and, right after `self.tokens_sent_to_rankings: set[int] = set()`, add:

```python
        # Last known mapping from ranking group name to the IDs of its
        # contests (always empty in legacy mode), used by reinitialize
        # to find groups that lost contests and must be reset.
        self._group_contests: dict[str, set[int]] = dict()
        with SessionGen() as session:
            self._group_contests = self._compute_group_contests(session)
```

Add these methods right after `__init__`:

```python
    def _is_sent(self, contest: Contest) -> bool:
        """Return whether the data of contest goes to a ranking."""
        if self.contest_id is not None:
            return contest.id == self.contest_id
        return contest.ranking_group is not None

    def _group_of(self, contest: Contest) -> str | None:
        """Return the namespace contest is sent to (None is the root).

        contest: a contest for which _is_sent is True.

        """
        if self.contest_id is not None:
            return None
        return contest.ranking_group.name

    def _contests_to_send(self, session: Session) -> list[Contest]:
        """Return the contests whose data goes to the rankings.

        raise (KeyError): in legacy mode, if the contest does not exist.

        """
        if self.contest_id is not None:
            contest = Contest.get_from_id(self.contest_id, session)
            if contest is None:
                logger.error("Received request for unexistent contest "
                             "id %s.", self.contest_id)
                raise KeyError("Contest not found.")
            return [contest]
        return session.query(Contest)\
            .filter(Contest.ranking_group_id.isnot(None))\
            .order_by(Contest.id).all()

    def _compute_group_contests(self, session: Session) -> dict[str, set[int]]:
        """Return the current mapping from group name to contest IDs."""
        mapping: dict[str, set[int]] = dict()
        if self.contest_id is None:
            for contest in self._contests_to_send(session):
                mapping.setdefault(
                    contest.ranking_group.name, set()).add(contest.id)
        return mapping
```

Add `Session` to the `cms.db` import: `from cms.db import SessionGen, Session, Contest, Participation, Task, Submission, get_submissions`.

- [ ] **Step 4: Initialization and submissions per contest**

Replace `_missing_operations` and `initialize` with:

```python
    def _enqueue_submissions(
        self, session: Session, contest: Contest, only_missing: bool
    ) -> int:
        """Enqueue the scores and tokens of the submissions of contest.

        only_missing: if True, skip what was already sent.

        return: the number of operations enqueued.

        """
        counter = 0
        submissions = get_submissions(session, contest_id=contest.id) \
            .filter(not_(Participation.hidden)) \
            .filter(Submission.official).all()

        for submission in submissions:
            # The submission result can be None if the dataset has
            # been just made live.
            sr = submission.get_result()
            if sr is None:
                continue

            if sr.scored() and not (
                    only_missing and
                    submission.id in self.scores_sent_to_rankings):
                for operation in self.operations_for_score(submission):
                    self.enqueue(operation)
                    counter += 1

            if submission.tokened() and not (
                    only_missing and
                    submission.id in self.tokens_sent_to_rankings):
                for operation in self.operations_for_token(submission):
                    self.enqueue(operation)
                    counter += 1

        return counter

    def _missing_operations(self):
        """Return a generator of data to be sent to the rankings..

        """
        counter = 0
        with SessionGen() as session:
            for contest in self._contests_to_send(session):
                counter += self._enqueue_submissions(
                    session, contest, only_missing=True)
        return counter

    def initialize(self):
        """Send basic data to all the rankings.

        It's data that's supposed to be sent before the contest, that's
        needed to understand what we're talking about when we send
        submissions: contest, users, tasks.

        No support for teams, flags and faces.

        """
        logger.info("Initializing rankings.")

        with SessionGen() as session:
            for contest in self._contests_to_send(session):
                self._enqueue_contest_data(contest)

    def _enqueue_contest_data(self, contest: Contest):
        """Enqueue the contest, its users, teams and tasks.

        """
        group = self._group_of(contest)
        contest_id = encode_id(contest.name)
        contest_data = {
            "name": contest.description,
            "begin": int(make_timestamp(contest.main_group.start)),
            "end": int(make_timestamp(contest.main_group.stop)),
            "score_precision": contest.score_precision}

        users = dict()
        teams = dict()

        for participation in contest.participations:
            user = participation.user
            team = participation.team
            if not participation.hidden:
                users[encode_id(user.username)] = {
                    "f_name": user.first_name,
                    "l_name": user.last_name,
                    "team": encode_id(team.code)
                            if team is not None else None,
                }
                if team is not None:
                    teams[encode_id(team.code)] = {
                        "name": team.name
                    }

        tasks = dict()

        for task in contest.tasks:
            score_type = task.active_dataset.score_type_object
            tasks[encode_id(task.name)] = {
                "short_name": task.name,
                "name": task.title,
                "contest": encode_id(contest.name),
                "order": task.num,
                "max_score": score_type.max_score,
                "extra_headers": score_type.ranking_headers,
                "score_precision": task.score_precision,
                "score_mode": task.score_mode,
            }

        self.enqueue(ProxyOperation(ProxyExecutor.CONTEST_TYPE,
                                    {contest_id: contest_data}, group))
        self.enqueue(ProxyOperation(ProxyExecutor.TEAM_TYPE, teams, group))
        self.enqueue(ProxyOperation(ProxyExecutor.USER_TYPE, users, group))
        self.enqueue(ProxyOperation(ProxyExecutor.TASK_TYPE, tasks, group))
```

- [ ] **Step 5: Route score/token operations to the contest's group**

In `operations_for_score` and `operations_for_token`, add as the first line of the body:

```python
        group = self._group_of(submission.task.contest)
```

and pass `group` as third argument to both `ProxyOperation(...)` calls in each method (e.g. `ProxyOperation(ProxyExecutor.SUBMISSION_TYPE, {submission_id: submission_data}, group)`).

In `submission_scored` and `submission_tokened`, replace the block starting at `# ScoringService sent us a submission of another contest` through its `return` with:

```python
            # The submission's contest is not sent to any ranking.
            if not self._is_sent(submission.task.contest):
                logger.debug("Ignoring submission %d of contest %d "
                             "(not sent to any ranking).",
                             submission.id, submission.task.contest_id)
                return
```

In `dataset_updated`, replace the block starting at `# This ProxyService may focus on a different contest` through its `return` with:

```python
            # The task's contest is not sent to any ranking.
            if not self._is_sent(task.contest):
                logger.debug("Ignoring dataset change for task %d of "
                             "contest %d (not sent to any ranking).",
                             task_id, task.contest_id)
                return
```

- [ ] **Step 6: Reset on composition change and `regenerate_ranking`**

Replace `reinitialize` with:

```python
    @rpc_method
    def reinitialize(self):
        """Repeat the initialization procedure for all rankings.

        This method is usually called via RPC when someone knows that
        some basic data (i.e. contest, tasks or users) changed and
        rankings need to be updated. Ranking groups that lost a contest
        (or were deleted) are emptied first and then sent again in full,
        since rankings only merge the data they receive.

        """
        logger.info("Reinitializing rankings.")
        with SessionGen() as session:
            new_mapping = self._compute_group_contests(session)
        lost = sorted(
            group for group, contest_ids in self._group_contests.items()
            if not contest_ids <= new_mapping.get(group, set()))
        self._group_contests = new_mapping

        for group in lost:
            logger.info("Ranking group %s lost contests, resetting it.",
                        group)
            self.enqueue(ProxyOperation(ProxyExecutor.RESET_TYPE, {}, group))

        self.initialize()

        if lost:
            with SessionGen() as session:
                for contest in self._contests_to_send(session):
                    if self._group_of(contest) in lost:
                        self._enqueue_submissions(
                            session, contest, only_missing=False)

    @rpc_method
    def regenerate_ranking(self, group: str | None = None):
        """Empty a ranking namespace and send all of its data again.

        Usually called by AdminWebServer when an admin asks to repair a
        ranking that got out of sync with the database.

        group: the ranking group whose namespace to regenerate, or None
            for the root namespace.

        """
        logger.info("Regenerating ranking %s.",
                    group if group is not None else "(root)")
        self.enqueue(ProxyOperation(ProxyExecutor.RESET_TYPE, {}, group))
        with SessionGen() as session:
            for contest in self._contests_to_send(session):
                if self._group_of(contest) == group:
                    self._enqueue_contest_data(contest)
                    self._enqueue_submissions(
                        session, contest, only_missing=False)
```

- [ ] **Step 7: Let ResourceService manage ProxyService in `ALL` mode**

In `cms/service/ResourceService.py`, in the restart loop change

```python
            if service.name == "LogService" or \
                    service.name == "ResourceService" or \
                    (self.contest_id is None and
                     service.name == "ProxyService"):
                continue
```

to

```python
            if service.name == "LogService" or \
                    service.name == "ResourceService":
                continue
```

and in `toggle_autorestart` delete the lines

```python
        # ProxyService requires contest_id
        if self.contest_id is None and name == "ProxyService":
            return None

```

(ProxyService now starts with `-c ALL`, which `contest_id_from_args` turns into `None`, i.e. group mode.)

- [ ] **Step 8: Run all ProxyService / ResourceService tests**

Run: `$DBENV .venv/bin/pytest cmstestsuite/unit_tests/service/proxyservice_groups_test.py cmstestsuite/unit_tests/service/ProxyServiceTest.py cmstestsuite/unit_tests/service/proxyexecutor_test.py cmstestsuite/unit_tests/service/ResourceServiceTest.py -v && .venv/bin/pyflakes cms/service/ProxyService.py cms/service/ResourceService.py cmstestsuite/unit_tests/service/proxyservice_groups_test.py`
Expected: all pass; no pyflakes output.

- [ ] **Step 9: Commit**

```bash
git add cms/service/ProxyService.py cms/service/ResourceService.py cmstestsuite/unit_tests/service/proxyservice_groups_test.py
git commit -m "feat(proxy): send every grouped contest to its ranking and add regenerate_ranking"
```

---

### Task 6: CWS serves only active contests in multi-contest mode

**Files:**
- Modify: `cms/server/contest/handlers/base.py:199-209` (`ContestListHandler.get`)
- Modify: `cms/server/contest/handlers/contest.py:106-131` (`ContestHandler.choose_contest`)
- Test: `cmstestsuite/unit_tests/server/contest/multicontest_test.py`

**Interfaces:**
- Consumes: `Contest.active` (Task 2).
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

`cmstestsuite/unit_tests/server/contest/multicontest_test.py`:

```python
"""Tests for serving only active contests in multi-contest mode."""

import unittest
from unittest.mock import MagicMock, patch

import tornado.web

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.server.contest.handlers.base import BaseHandler, ContestListHandler
from cms.server.contest.handlers.contest import ContestHandler


class TestActiveContests(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.active = self.add_contest(active=True)
        self.inactive = self.add_contest(active=False)
        self.session.commit()

    def make_contest_handler(self, contest_id, name) -> ContestHandler:
        handler = ContestHandler.__new__(ContestHandler)
        handler.service = MagicMock(contest_id=contest_id)
        handler.path_args = [name]
        handler.sql_session = self.session
        return handler

    def test_list_shows_only_active_contests(self):
        handler = ContestListHandler.__new__(ContestListHandler)
        handler.sql_session = self.session
        handler.render_params = MagicMock(return_value={})
        handler.render = MagicMock()
        handler.get()
        contest_list = handler.render.call_args.kwargs["contest_list"]
        self.assertEqual(set(contest_list), {self.active.name})

    def test_active_contest_is_served(self):
        handler = self.make_contest_handler(None, self.active.name)
        handler.choose_contest()
        self.assertEqual(handler.contest.id, self.active.id)

    @patch.object(BaseHandler, "render_params", return_value={})
    @patch.object(BaseHandler, "prepare")
    def test_inactive_contest_is_404(self, *_):
        handler = self.make_contest_handler(None, self.inactive.name)
        with self.assertRaises(tornado.web.HTTPError) as cm:
            handler.choose_contest()
        self.assertEqual(cm.exception.status_code, 404)

    def test_single_contest_mode_ignores_active(self):
        handler = self.make_contest_handler(self.inactive.id, "ignored")
        handler.choose_contest()
        self.assertEqual(handler.contest.id, self.inactive.id)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$DBENV .venv/bin/pytest cmstestsuite/unit_tests/server/contest/multicontest_test.py -v`
Expected: `test_list_shows_only_active_contests` and `test_inactive_contest_is_404` FAIL; the other two pass.

- [ ] **Step 3: Filter by `active`**

In `ContestListHandler.get` (`cms/server/contest/handlers/base.py`) replace

```python
        for contest in self.sql_session.query(Contest).all():
```

with

```python
        for contest in self.sql_session.query(Contest)\
                .filter(Contest.active.is_(True)).all():
```

In `choose_contest` (`cms/server/contest/handlers/contest.py`) replace

```python
            self.contest = self.sql_session.query(Contest)\
                .filter(Contest.name == contest_name).first()
```

with

```python
            # Inactive contests are not served, as if they did not exist.
            self.contest = self.sql_session.query(Contest)\
                .filter(Contest.name == contest_name)\
                .filter(Contest.active.is_(True)).first()
```

- [ ] **Step 4: Run the CWS tests**

Run: `$DBENV .venv/bin/pytest cmstestsuite/unit_tests/server/contest/ -q && .venv/bin/pyflakes cms/server/contest/handlers/base.py cms/server/contest/handlers/contest.py cmstestsuite/unit_tests/server/contest/multicontest_test.py`
Expected: all pass; no pyflakes output.

- [ ] **Step 5: Commit**

```bash
git add cms/server/contest/handlers/base.py cms/server/contest/handlers/contest.py cmstestsuite/unit_tests/server/contest/multicontest_test.py
git commit -m "feat(cws): serve only active contests in multi-contest mode"
```

---

### Task 7: AWS — ranking groups pages, contest fields, Regenerate buttons

**Files:**
- Create: `cms/server/admin/handlers/rankinggroup.py`
- Modify: `cms/server/admin/handlers/__init__.py` (imports, routes)
- Modify: `cms/server/admin/handlers/base.py:54-55` (import) and `render_params` (line ~349)
- Modify: `cms/server/admin/handlers/contest.py` (`ContestHandler.post`)
- Create: `cms/server/admin/templates/ranking_groups.html`, `add_ranking_group.html`, `ranking_group.html`, `ranking_group_remove.html`
- Modify: `cms/server/admin/templates/base.html` (sidebar, after the Teams block), `contest.html` (after the Description row), `contests.html` (two columns)
- Test: `cmstestsuite/unit_tests/server/admin/rankinggroup_test.py` (create `cmstestsuite/unit_tests/server/admin/__init__.py`, empty)

**Interfaces:**
- Consumes: `RankingGroup`, `Contest.active`, `Contest.ranking_group_id` (Task 2); `is_valid_group_name`, `RESERVED_GROUP_NAMES` (Task 1); RPC `proxy_service.regenerate_ranking(group=...)` and `proxy_service.reinitialize()` (Task 5).
- Produces: routes `/ranking_groups`, `/ranking_groups/add`, `/ranking_groups/regenerate`, `/ranking_groups/<id>/remove`, `/ranking_group/<id>`; render param `ranking_group_list` available to every AWS template.

- [ ] **Step 1: Write the failing tests**

`cmstestsuite/unit_tests/server/admin/rankinggroup_test.py`:

```python
"""Tests for the AWS ranking group handlers."""

import unittest
from unittest.mock import MagicMock

from cms.server.admin.handlers.rankinggroup import \
    RegenerateRankingHandler, read_ranking_group_attrs


def form_handler(form: dict) -> MagicMock:
    """Return a fake handler whose get_string reads from form."""
    handler = MagicMock()

    def get_string(dest, name, empty=""):
        if name in form:
            dest[name] = form[name] if form[name] != "" else empty
    handler.get_string.side_effect = get_string
    return handler


class TestReadRankingGroupAttrs(unittest.TestCase):

    def test_valid_group(self):
        attrs = dict()
        read_ranking_group_attrs(
            form_handler({"name": "olim", "description": "OLIM"}), attrs)
        self.assertEqual(attrs, {"name": "olim", "description": "OLIM"})

    def test_description_defaults_to_name(self):
        attrs = dict()
        read_ranking_group_attrs(
            form_handler({"name": "olim", "description": ""}), attrs)
        self.assertEqual(attrs["description"], "olim")

    def test_rejects_reserved_and_invalid_names(self):
        for name in ["events", "Olim", "", "a/b"]:
            with self.assertRaises(ValueError, msg=name):
                read_ranking_group_attrs(
                    form_handler({"name": name, "description": "x"}),
                    dict())


class TestRegenerateRankingHandler(unittest.TestCase):

    def run_post(self, group_arg: str) -> MagicMock:
        handler = RegenerateRankingHandler.__new__(RegenerateRankingHandler)
        handler.service = MagicMock()
        handler.get_argument = MagicMock(return_value=group_arg)
        handler.redirect = MagicMock()
        handler.url = MagicMock(return_value="/ranking_groups")
        RegenerateRankingHandler.post.__wrapped__(handler)
        return handler.service.proxy_service.regenerate_ranking

    def test_group(self):
        self.run_post("olim").assert_called_once_with(group="olim")

    def test_root(self):
        self.run_post("").assert_called_once_with(group=None)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/admin/rankinggroup_test.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cms.server.admin.handlers.rankinggroup'`

- [ ] **Step 3: Write the handlers**

`cms/server/admin/handlers/rankinggroup.py` (AGPL header as in `user.py`, your copyright line):

```python
"""Ranking group handlers for AWS.

"""

from cms.db import Contest, RankingGroup
from cmscommon.datetime import make_datetime
from cmscommon.ranking_groups import RESERVED_GROUP_NAMES, \
    is_valid_group_name

from .base import BaseHandler, SimpleHandler, require_permission


def read_ranking_group_attrs(handler: BaseHandler, attrs: dict):
    """Read and validate the ranking group fields of the form.

    handler: the handler whose request carries the form.
    attrs: where to store the name and the description.

    raise (ValueError): if the name is not a valid group name.

    """
    handler.get_string(attrs, "name")
    handler.get_string(attrs, "description")
    name = attrs.get("name")
    if name is None or not is_valid_group_name(name):
        raise ValueError(
            "Invalid ranking group name %r: use only lowercase letters, "
            "digits, '-' and '_', and none of: %s."
            % (name, ", ".join(sorted(RESERVED_GROUP_NAMES))))
    if not attrs.get("description"):
        attrs["description"] = name


class RankingGroupListHandler(SimpleHandler("ranking_groups.html")):
    """Get returns the list of all ranking groups, post perform
    operations on a specific group (removing it).

    """

    REMOVE = "Remove"

    @require_permission(BaseHandler.AUTHENTICATED)
    def post(self):
        group_id: str = self.get_argument("ranking_group_id")
        operation: str = self.get_argument("operation")

        if operation == self.REMOVE:
            self.redirect(self.url("ranking_groups", group_id, "remove"))
        else:
            self.service.add_notification(
                make_datetime(), "Invalid operation %s" % operation, "")
            self.redirect(self.url("ranking_groups"))


class AddRankingGroupHandler(
        SimpleHandler("add_ranking_group.html", permission_all=True)):
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback_page = self.url("ranking_groups", "add")

        try:
            attrs = dict()
            read_ranking_group_attrs(self, attrs)
            self.sql_session.add(RankingGroup(**attrs))

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.service.proxy_service.reinitialize()
            self.redirect(self.url("ranking_groups"))
        else:
            self.redirect(fallback_page)


class RankingGroupHandler(BaseHandler):
    """Show and edit a single ranking group.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, group_id: str):
        group = self.safe_get_item(RankingGroup, group_id)

        self.r_params = self.render_params()
        self.r_params["ranking_group"] = group
        self.r_params["group_contests"] = self.sql_session.query(Contest)\
            .filter(Contest.ranking_group_id == group.id)\
            .order_by(Contest.name).all()
        self.render("ranking_group.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, group_id: str):
        fallback_page = self.url("ranking_group", group_id)

        group = self.safe_get_item(RankingGroup, group_id)

        try:
            attrs = group.get_attrs()
            read_ranking_group_attrs(self, attrs)
            group.set_attrs(attrs)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            # A rename moves the ranking to a new namespace.
            self.service.proxy_service.reinitialize()
        self.redirect(fallback_page)


class RemoveRankingGroupHandler(BaseHandler):
    """Get returns a page asking for confirmation, delete actually
    removes the ranking group (its contests are kept, without group).

    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, group_id: str):
        group = self.safe_get_item(RankingGroup, group_id)

        self.r_params = self.render_params()
        self.r_params["ranking_group"] = group
        self.r_params["contest_count"] = self.sql_session.query(Contest)\
            .filter(Contest.ranking_group_id == group.id).count()
        self.render("ranking_group_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, group_id: str):
        group = self.safe_get_item(RankingGroup, group_id)

        self.sql_session.delete(group)
        if self.try_commit():
            self.service.proxy_service.reinitialize()

        # Maybe they'll want to do this again (for another group)
        self.write("../../ranking_groups")


class RegenerateRankingHandler(BaseHandler):
    """Empty a ranking (a group, or the root) and send it again.

    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        group: str = self.get_argument("group", "")
        self.service.proxy_service.regenerate_ranking(group=group or None)
        self.service.add_notification(
            make_datetime(), "Ranking regeneration requested",
            "Ranking %s is being emptied and sent again."
            % (group or "root"))
        self.redirect(self.url("ranking_groups"))
```

- [ ] **Step 4: Run the handler tests**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/admin/rankinggroup_test.py -v`
Expected: 5 passed.

- [ ] **Step 5: Wire routes and render params**

In `cms/server/admin/handlers/__init__.py`, add after the `from .main import …` block (keep alphabetical order of modules):

```python
from .rankinggroup import \
    AddRankingGroupHandler, \
    RankingGroupHandler, \
    RankingGroupListHandler, \
    RegenerateRankingHandler, \
    RemoveRankingGroupHandler
```

and in `HANDLERS`, right after the `# Users/Teams` group (before `# Admins`):

```python
    # Ranking groups

    (r"/ranking_groups", RankingGroupListHandler),
    (r"/ranking_groups/add", AddRankingGroupHandler),
    (r"/ranking_groups/regenerate", RegenerateRankingHandler),
    (r"/ranking_groups/([0-9]+)/remove", RemoveRankingGroupHandler),
    (r"/ranking_group/([0-9]+)", RankingGroupHandler),
```

In `cms/server/admin/handlers/base.py`, add `RankingGroup` to the `from cms.db import …` list and, in `render_params`, after the `team_list` line:

```python
        params["ranking_group_list"] = self.sql_session.query(RankingGroup)\
            .order_by(RankingGroup.name).all()
```

- [ ] **Step 6: Save `active` and the group from the contest page**

In `cms/server/admin/handlers/contest.py`, add `RankingGroup` to `from cms.db import …`, and in `ContestHandler.post` add right after `self.get_bool(attrs, "ip_autologin")`:

```python
            self.get_bool(attrs, "active")
            self.get_int(attrs, "ranking_group_id")
            if attrs["ranking_group_id"] is not None and \
                    RankingGroup.get_from_id(
                        attrs["ranking_group_id"], self.sql_session) is None:
                raise ValueError("Unknown ranking group.")
```

(`get_int` stores `None` for the empty "none" option; the existing `try_commit()` + `reinitialize()` call applies the change to the rankings.)

- [ ] **Step 7: Templates**

`cms/server/admin/templates/contest.html` — insert right after the Description row (the `</tr>` following `<td><textarea name="description">{{ contest.description }}</textarea></td>`):

```html
    <tr>
      <td>
        <span class="info" title="When CWS serves all contests (CMS_CONTEST_ID=ALL), only active contests are listed and can be entered. Ignored when CWS serves a single contest."></span>
        <label for="active">Active</label>
      </td>
      <td>
        <input type="checkbox" id="active" name="active" {{ "checked" if contest.active else "" }}/>
      </td>
    </tr>
    <tr>
      <td>
        <span class="info" title="The public scoreboard (RWS /<group>/) this contest is sent to. Contests of the same group are ranked together. Deactivating a contest does not remove it from its ranking; choose 'none' for that."></span>
        Ranking group
      </td>
      <td>
        <select name="ranking_group_id">
          <option value="" {{ "selected" if contest.ranking_group_id is none else "" }}>(none)</option>
          {% for g in ranking_group_list %}
          <option value="{{ g.id }}" {{ "selected" if contest.ranking_group_id == g.id else "" }}>{{ g.name }} — {{ g.description }}</option>
          {% endfor %}
        </select>
      </td>
    </tr>
```

`cms/server/admin/templates/contests.html` — add `<th>Active</th>` and `<th>Ranking group</th>` after `<th>Description</th>`, and after `<td>{{ c.description }}</td>`:

```html
        <td>{{ "yes" if c.active else "no" }}</td>
        <td>{{ c.ranking_group.name if c.ranking_group is not none else "" }}</td>
```

`cms/server/admin/templates/base.html` — right after the closing `</ul>` of the Teams block (the one following `(create new team...)`), before `{% else %}`:

```html

        <h1><a class="menu_link" href="{{ url("ranking_groups") }}">Ranking groups</a></h1>
        <ul class="menu">
          <li>
            <a class="menu_link" href="{{ url("ranking_groups") }}">
              (show the {{ ranking_group_list|length }} ranking groups...)
            </a>
          </li>
{% if admin.permission_all %}
          <li class="menu_entry">
            <a class="menu_link" href="{{ url("ranking_groups", "add") }}">
              (create new ranking group...)
            </a>
          </li>
{% endif %}
        </ul>
```

`cms/server/admin/templates/ranking_groups.html`:

```html
{% extends "base.html" %}

{% block core %}

<div class="core_title">
  <h1>Ranking groups</h1>
</div>

<p>
  Each ranking group is an independent public scoreboard, served by RWS at
  <code>/&lt;name&gt;/</code>. Assign a contest to a group from the contest's
  own page.
</p>

<form action="{{ url("ranking_groups") }}" method="POST">
  {{ xsrf_form_html|safe }}
  Edit selected group:
  <input type="submit" name="operation" value="Remove" {% if not admin.permission_all %} disabled {% endif %} />
  <table class="bordered">
    <thead>
      <tr>
        <th></th>
        <th>Name</th>
        <th>Description</th>
        <th>Contests</th>
      </tr>
    </thead>
    <tbody>
      {% for g in ranking_group_list %}
      <tr>
        <td>
          <input type="radio" name="ranking_group_id" value="{{ g.id }}" />
        </td>
        <td><a href="{{ url("ranking_group", g.id) }}">{{ g.name }}</a></td>
        <td>{{ g.description }}</td>
        <td>{% for c in contest_list if c.ranking_group_id == g.id %}{{ c.name }}{% if not loop.last %}, {% endif %}{% endfor %}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</form>

<h2>Regenerate a ranking</h2>
<p>
  Regenerating empties a scoreboard and sends all of its data again from the
  database. Use it when a scoreboard does not match the database. The root
  ranking is the one at <code>/</code>: it is used when ProxyService runs for a
  single contest, and regenerating it while using groups leaves it empty.
</p>
<table class="bordered">
  <tbody>
    <tr>
      <td>Root ranking (<code>/</code>)</td>
      <td>
        <form action="{{ url("ranking_groups", "regenerate") }}" method="POST"
              onsubmit="return confirm('Empty and regenerate the root ranking?');">
          {{ xsrf_form_html|safe }}
          <input type="hidden" name="group" value="" />
          <input type="submit" value="Regenerate" {% if not admin.permission_all %} disabled {% endif %} />
        </form>
      </td>
    </tr>
    {% for g in ranking_group_list %}
    <tr>
      <td>{{ g.name }} (<code>/{{ g.name }}/</code>)</td>
      <td>
        <form action="{{ url("ranking_groups", "regenerate") }}" method="POST"
              onsubmit="return confirm('Empty and regenerate the ranking {{ g.name }}?');">
          {{ xsrf_form_html|safe }}
          <input type="hidden" name="group" value="{{ g.name }}" />
          <input type="submit" value="Regenerate" {% if not admin.permission_all %} disabled {% endif %} />
        </form>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>

{% endblock core %}
```

`cms/server/admin/templates/add_ranking_group.html`:

```html
{% extends "base.html" %}

{% block core %}

<h1>New ranking group</h1>

<h2 id="title_general_info" class="toggling_on">General information</h2>
<div id="general_info">
  <!-- We use "multipart/form-data" to have Tornado distinguish between missing and empty values. -->
  <form enctype="multipart/form-data" action="{{ url("ranking_groups", "add") }}" method="POST">
    {{ xsrf_form_html|safe }}
    <table>
      <tr>
        <td>
          <span class="info" title="Used in the scoreboard URL (/<name>/): lowercase letters, digits, '-' and '_'."></span>
          Name
        </td>
        <td><input type="text" name="name"/></td>
      </tr>
      <tr>
        <td>Description</td>
        <td><input type="text" name="description"/></td>
      </tr>
    </table>
    <input type="submit"/>
    <input type="reset" value="Reset" />
  </form>
  <div class="hr"></div>
</div>

{% endblock core %}
```

`cms/server/admin/templates/ranking_group.html`:

```html
{% extends "base.html" %}

{% block core %}

<h1>{{ ranking_group.description }} ({{ ranking_group.name }})</h1>

<h2 id="title_general_info" class="toggling_on">General Information</h2>
<div id="general_info">
  <!-- We use "multipart/form-data" to have Tornado distinguish between missing and empty values. -->
  <form enctype="multipart/form-data" action="{{ url("ranking_group", ranking_group.id) }}" method="POST" style="display:inline;">
    {{ xsrf_form_html|safe }}
    <table>
      <tr>
        <td>
          <span class="info" title="Used in the scoreboard URL (/<name>/). Renaming moves the scoreboard to the new URL."></span>
          Name
        </td>
        <td><input type="text" name="name" value="{{ ranking_group.name }}" /></td>
      </tr>
      <tr>
        <td>Description</td>
        <td><input type="text" name="description" value="{{ ranking_group.description }}" /></td>
      </tr>
    </table>
    <input type="submit" value="Update" />
    <input type="reset" value="Reset" />
  </form>
  <form action="{{ url("ranking_groups") }}" method="POST" style="display:inline; float: right;">
    {{ xsrf_form_html|safe }}
    <input type="hidden" name="ranking_group_id" value="{{ ranking_group.id }}" />
    <input type="submit" name="operation" value="Remove" {% if not admin.permission_all %}
      disabled {% endif %} />
  </form>
  <div class="hr"></div>
</div>

<h2>Contests in this group</h2>
{% if group_contests|length == 0 %}
<p>No contest is sent to this ranking yet.</p>
{% else %}
<ul>
  {% for c in group_contests %}
  <li><a href="{{ url("contest", c.id) }}">{{ c.name }}</a>{% if not c.active %} (inactive){% endif %}</li>
  {% endfor %}
</ul>
{% endif %}

{% endblock core %}
```

`cms/server/admin/templates/ranking_group_remove.html`:

```html
{% extends "base.html" %}

{% block core %}
<div class="core_title">
  <h1>Remove ranking group</h1>
</div>

{% if admin.permission_all %}
<p>
  Are you sure you want to remove ranking group {{ ranking_group.name }} ({{ ranking_group.description }})?
  <br>
  {% if contest_count > 0 %}
  {{ contest_count }} contest(s) will no longer be sent to any ranking. The contests themselves are not removed.
  {% else %}
  No contest is sent to this ranking.
  {% endif %}
  <br>
  Its scoreboard will be emptied. This operation cannot be undone.
</p>
<br>
<a onclick="CMS.AWSUtils.ajax_delete('{{ url("ranking_groups", ranking_group.id, "remove") }}');" class="button-link">Yes, remove</a>
<a href="{{ url("ranking_groups") }}" class="button-link">No</a>

{% else %}
You do not have permission to remove a ranking group.
{% endif %}

{% endblock core %}
```

- [ ] **Step 8: Run tests and pyflakes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/ -q && .venv/bin/pyflakes cms/server/admin cmstestsuite/unit_tests/server/admin`
Expected: all pass; no pyflakes output.

- [ ] **Step 9: Manual check of the AWS pages**

With the test DB from Global Constraints: `CMS_CONFIG=/tmp/cms-mc/cms-test.toml .venv/bin/cmsInitDB`, then `CMS_CONFIG=/tmp/cms-mc/cms-test.toml .venv/bin/cmsAddAdmin -p admin admin`, and `CMS_CONFIG=/tmp/cms-mc/cms-test.toml .venv/bin/cmsAdminWebServer 0` (http://localhost:8889). Check: sidebar shows "Ranking groups"; creating `events` is rejected with a notification; creating `olim` works; a contest page shows Active + Ranking group and saving persists both; the contests list shows both columns; the Regenerate buttons ask for confirmation and show the "Ranking regeneration requested" notification (the RPC fails silently without ProxyService, like other `proxy_service` calls). Stop the server.

- [ ] **Step 10: Commit**

```bash
git add cms/server/admin cmstestsuite/unit_tests/server/admin
git commit -m "feat(aws): manage ranking groups, contest activation and ranking regeneration"
```

---

### Task 8: Docker — ranking volume and Telegram bot in `ALL` mode

**Files:**
- Modify: `docker/docker-compose.prod.yml` (service `ranking`, top-level `volumes`)
- Modify: `docker/Dockerfile.ranking` (create the data dir as `cmsuser`)
- Modify: `docker/generate_config.py` (`generate_supervisord_conf`)
- Test: `docker/test_generate_config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: named volume `cms-ranking-data` mounted at `/home/cmsuser/cms/lib/ranking` in the `ranking` container.

- [ ] **Step 1: Write the failing test**

Append to `docker/test_generate_config.py`:

```python
def test_supervisord_no_telegram_bot_when_all(monkeypatch, capsys):
    _set(monkeypatch, {
        "CMS_CONTEST_ID": "ALL",
        "CMS_TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
        "CMS_TELEGRAM_CHAT_ID": "-1001234567890",
    })
    conf = gc.generate_supervisord_conf()
    assert "cmstelegrambot" not in conf
    assert "Telegram bot" in capsys.readouterr().err
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest docker/test_generate_config.py -k telegram -v`
Expected: the new test FAILS (`assert 'cmstelegrambot' not in conf`); existing telegram tests pass.

- [ ] **Step 3: Skip the bot in `ALL` mode**

In `docker/generate_config.py`, `generate_supervisord_conf`, replace

```python
    if telegram_configured:
        blocks.append(
            program("cmstelegrambot", f"cmsTelegramBot 0 -c {contest_id}", 65)
        )
```

with

```python
    if telegram_configured and contest_id == "ALL":
        print("WARNING: the Telegram bot serves a single contest and is not "
              "started when CMS_CONTEST_ID=ALL.", file=sys.stderr)
    elif telegram_configured:
        blocks.append(
            program("cmstelegrambot", f"cmsTelegramBot 0 -c {contest_id}", 65)
        )
```

- [ ] **Step 4: Persist the ranking data**

In `docker/Dockerfile.ranking`, right after the `RUN … ./install.py --skip-isolate cms` line add:

```dockerfile
# Created as cmsuser so the named volume mounted here inherits the ownership.
RUN mkdir -p /home/cmsuser/cms/lib/ranking
```

In `docker/docker-compose.prod.yml`, service `ranking`, add after `restart: unless-stopped`:

```yaml
    volumes:
      - cms-ranking-data:/home/cmsuser/cms/lib/ranking
```

and add `  cms-ranking-data:` to the top-level `volumes:` list.

- [ ] **Step 5: Verify**

Run: `.venv/bin/pytest docker/test_generate_config.py -q && .venv/bin/pyflakes docker/generate_config.py docker/test_generate_config.py && docker compose -f docker/docker-compose.prod.yml config --quiet`
Expected: all pass; no pyflakes output; compose config valid (exit 0; set any variables it complains about in the environment for this check only).

- [ ] **Step 6: Commit**

```bash
git add docker/docker-compose.prod.yml docker/Dockerfile.ranking docker/generate_config.py docker/test_generate_config.py
git commit -m "feat(docker): persist ranking data and skip Telegram bot in ALL mode"
```

---

### Task 9: Documentation and removal of `clear-ranking.sh`

**Files:**
- Create: `docs/multi-contest.md`
- Create: `docs/migrating-to-multi-contest.md`
- Delete: `clear-ranking.sh`
- Modify: `docs/docker-scripts.md` (remove the `### clear-ranking.sh` section)
- Modify: `README.md` (fork features table + `CMS_CONTEST_ID` row)
- Modify: `.env.example` (comment above `CMS_CONTEST_ID=1`)

**Interfaces:** none.

- [ ] **Step 1: Write `docs/multi-contest.md`**

```markdown
# Running several contests at once

One CMS deployment can serve several contests at the same time (for example two
olympiads on the same exam day), each with its own public scoreboard.

## How it works

- `CMS_CONTEST_ID=ALL` in `.env` makes the Contest Web Server serve every
  **active** contest: contestants see the list at `/` and enter a contest at
  `/<contest_name>/`. Contests that are not active are neither listed nor
  reachable.
- A **ranking group** is an independent public scoreboard. The Ranking Web
  Server serves each group at `/<group>/` (for example `/olim/` and
  `/omips/`). Every contest assigned to a group is ranked there; contests of
  the same group are ranked together (useful for day 1 + day 2).
- Evaluation is shared: all contests use the same queue and the same workers.
  Size `CMS_WORKER_COUNT` for the combined load of all simultaneous contests.

Everything below is done in the Admin Web Server and applies immediately;
nothing needs restarting.

## Before an exam day

1. **Ranking groups → create new ranking group.** Name it with lowercase
   letters, digits, `-` or `_` (for example `olim`); the name is part of the
   scoreboard URL. Some names are reserved because the scoreboard already uses
   them (`events`, `lib`, `img`, …); the form tells you if you pick one.
2. **Each contest's page:** tick **Active** and choose its **Ranking group**,
   then save.
3. Open `http://<server>:<CMS_RWS_HTTP_PORT>/<group>/` to check each
   scoreboard.

## After the exam

- Untick **Active** to hide the contest from contestants. Its scoreboard stays
  published.
- To take a contest off a scoreboard, set its ranking group to **(none)**.

## One domain per scoreboard

The Ranking Web Server listens on a single port. Point each domain at its
group with your reverse proxy; this is configured once, not per exam:

    server {
        server_name ranking.olim.example;
        location / {
            proxy_pass http://127.0.0.1:8890/olim/;
            proxy_buffering off;   # live updates (server-sent events)
        }
    }

    server {
        server_name ranking.omips.example;
        location / {
            proxy_pass http://127.0.0.1:8890/omips/;
            proxy_buffering off;
        }
    }

The contestant domains all point at the Contest Web Server; contestants pick
their contest from the list, or you link them directly to
`/<contest_name>/`.

## When a scoreboard is wrong: Regenerate

If a scoreboard does not match the database (missing or stale scores, a
contest that should not be there), go to **Ranking groups** and press
**Regenerate** next to it. The scoreboard is emptied and all of its data is
sent again; it refills within seconds. This replaces the old
`clear-ranking.sh` script.

The **Root ranking** row is the scoreboard at `/`. It is only used when a
single contest is served (`CMS_CONTEST_ID=<id>`). After switching to
`CMS_CONTEST_ID=ALL`, regenerate it once to clear the old data.

## Limitations

- The Telegram bot serves a single contest and is not started with
  `CMS_CONTEST_ID=ALL`.
- All contests share one Admin Web Server; there are no per-contest admin
  permissions.
- Hiding, freezing or password-protecting a scoreboard is not available yet.
```

- [ ] **Step 2: Write `docs/migrating-to-multi-contest.md`**

```markdown
# Migrating from one deployment per contest to a single multi-contest deployment

This guide is for setups that ran parallel exams with **two (or more) copies**
of this Docker deployment on the same machine, each with its own database,
ports and scoreboard (for example scoreboards on 8890 and 7890 behind
different domains). After migrating, one deployment serves every contest and
each olympiad keeps its own scoreboard. See
[multi-contest.md](multi-contest.md) for day-to-day use.

Below, **stack A** is the deployment you keep and **stack B** the one you
retire. Run each command from that stack's directory.

## 1. Back up everything

For each stack, while it is running:

    # Database (local Docker database, --profile localdb)
    source .env
    docker compose -f docker/docker-compose.prod.yml --env-file .env \
      -p "$CMS_PROJECT_NAME" exec -T db \
      pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup-$(date +%F).sql

    # Contest data, as a CMS dump
    ./export.sh

If you use an external database, run `pg_dump` against the URL in
`CMS_DB_URL` instead. Keep these files until the first exam on the new setup
has finished.

## 2. Update stack A

    git pull
    ./restart.sh     # answer "yes" to rebuilding the image

The database schema is updated automatically when the stack starts (the
`db-init` container adds the `ranking_groups` table and the new contest
columns). Existing contests start as **inactive** and without a ranking group;
nothing changes for contestants until you switch to `CMS_CONTEST_ID=ALL`.
Check `./logs.sh` for errors before continuing.

## 3. Bring stack B's contests into stack A

1. **Check for name collisions first.** Usernames, team codes and contest
   names must be unique across both databases. List the ones present in both:

       # in each stack's directory (use its own .env)
       source .env
       docker compose -f docker/docker-compose.prod.yml --env-file .env \
         -p "$CMS_PROJECT_NAME" exec -T db psql -U "$POSTGRES_USER" \
         "$POSTGRES_DB" -Atc "SELECT username FROM users ORDER BY 1" > users.txt

       # then, with both files side by side
       comm -12 stackA/users.txt stackB/users.txt

   Repeat with `SELECT code FROM teams` and `SELECT name FROM contests`. If
   the same person has an account in both stacks, import only one copy: when
   exporting in stack B, leave users out, and add those participations in
   stack A afterwards. Rename anything else that collides in stack B (from its
   Admin Web Server) before exporting.
2. In stack B: `./export.sh` and pick the contests to move.
3. Copy the new `.zip` from stack B's `dumps/` into stack A's `dumps/`.
4. In stack A: `./import.sh`, pick that file, and **do not** wipe the
   database.

Dumps made before this update import fine: their contests arrive inactive and
without a ranking group.

## 4. Update stack A's `.env`

- `CMS_CONTEST_ID=ALL`
- `CMS_WORKER_COUNT`: at least the sum of what both stacks used.
- Nothing else needs to change: the scoreboard keeps `CMS_RWS_HTTP_PORT`
  (for example 8890). Stack B's ports (for example 7890) are no longer
  used.

Apply it with `./restart.sh`.

## 5. Configure the contests in the Admin Web Server

1. **Ranking groups:** create one per olympiad (for example `olim` and
   `omips`).
2. **Each contest:** tick **Active** where needed and choose its ranking
   group.
3. **Ranking groups → Root ranking → Regenerate:** clears the old
   single-contest scoreboard at `/`.

## 6. Update the reverse proxy (on the server)

Each scoreboard domain now points at the same port plus its group:

    # before
    server_name ranking.omips.example;  proxy_pass http://127.0.0.1:7890/;
    # after
    server_name ranking.omips.example;  proxy_pass http://127.0.0.1:8890/omips/;

Do the same for the other olympiad (`…:8890/olim/`), keep
`proxy_buffering off;`, and point every contestant domain at stack A's
Contest Web Server. Reload the proxy.

## 7. Verify, then retire stack B

- Each scoreboard domain shows only its own contests and updates live when a
  test submission is scored.
- The contest list shows exactly the active contests.
- Then stop stack B: `./down.sh` in its directory. Keep its volumes and the
  backups until you are confident.

### Rolling back

1. In stack A: set `CMS_CONTEST_ID` back to the previous contest id and
   `./restart.sh`.
2. In stack B: `./up.sh` (its data is untouched unless you deleted its
   volumes).
3. Restore the previous reverse-proxy configuration.

The new database columns are harmless to the old setup: with a single
contest id they are ignored. To get back the exact pre-migration database,
restore the `backup-*.sql` taken in step 1.
```

- [ ] **Step 3: Remove `clear-ranking.sh` and its docs**

```bash
git rm clear-ranking.sh
```

In `docs/docker-scripts.md` delete the whole `### clear-ranking.sh` section (from its heading through the paragraph ending "within ~6 minutes."), and insert in its place:

```markdown
> `clear-ranking.sh` was removed: use **Ranking groups → Regenerate** in the
> Admin Web Server instead (see [multi-contest.md](multi-contest.md)).
```

Then check nothing else references it: `grep -rn "clear-ranking" --include=*.md --include=*.sh . | grep -v docs/superpowers` → no output.

- [ ] **Step 4: README and `.env.example`**

In `README.md`, add a row to the fork features table (the one containing "External judge/bridge integration"):

```markdown
| Several contests at once | `CMS_CONTEST_ID=ALL` serves every active contest; each ranking group gets its own scoreboard at `/<group>/`, managed and regenerated from the Admin Web Server | [docs/multi-contest.md](docs/multi-contest.md), [migration guide](docs/migrating-to-multi-contest.md) |
```

and change the `CMS_CONTEST_ID` row text to:

```markdown
| `CMS_CONTEST_ID` | The numeric ID of the contest to serve, or `ALL` to serve every active contest at once (see [docs/multi-contest.md](docs/multi-contest.md)). You get the ID from the Admin interface after importing a contest — set it then and restart. |
```

In `.env.example`, directly above `CMS_CONTEST_ID=1`, add:

```bash
# Set to ALL to serve every contest marked Active in the Admin Web Server,
# each sent to its ranking group's scoreboard (/<group>/). See
# docs/multi-contest.md and docs/migrating-to-multi-contest.md.
```

- [ ] **Step 5: Verify links and commit**

Run: `grep -o "(docs/[a-z-]*\.md)\|([a-z-]*\.md)" README.md docs/multi-contest.md docs/migrating-to-multi-contest.md docs/docker-scripts.md | sort -u` and confirm every listed file exists.

```bash
git add docs/multi-contest.md docs/migrating-to-multi-contest.md docs/docker-scripts.md README.md .env.example
git commit -m "docs: document multi-contest rankings and migration; remove clear-ranking.sh"
```

---

### Task 10: End-to-end verification

**Files:** none (verification only; fix issues in the task that owns the code and re-run).

- [ ] **Step 1: Full unit test suite**

Run: `$DBENV .venv/bin/pytest -q && .venv/bin/pyflakes cms cmscommon cmscontrib cmsranking cmstaskenv cmstestsuite`
Expected: all pass; no pyflakes output.

- [ ] **Step 2: Local Docker run with two groups**

On a local machine (never on the production server): copy `.env.example` to a scratch `.env` with `CMS_CONTEST_ID=ALL`, local DB profile, and `./up.sh` with rebuild. In the Admin Web Server: create groups `olim` and `omips`; create contests A (active, `olim`), B (active, `omips`), C (inactive, no group), each with one task and one user; submit a solution in A and in B through the Contest Web Server.

Check:
- `http://localhost:8888/` lists A and B only; `/C/` returns 404.
- `http://localhost:8890/olim/` shows only A's task and A's score; `/omips/` only B's; both update live when a new submission is scored.
- Move A to `omips` and save → `/olim/` empties, `/omips/` shows A and B.
- Untick Active on B → B disappears from the CWS list but stays on `/omips/`.
- Regenerate `omips` → it empties and refills within seconds.
- `./down.sh && ./up.sh` (no rebuild) → `/omips/` still shows its data (volume).

- [ ] **Step 3: Record the result**

If everything passed, nothing to commit. Otherwise fix in the owning task's files with a test reproducing the failure, and commit with a `fix:` message.
