# Bulk Import of Users and Participations in AWS — Implementation Plan (Phase A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin upload one CSV on a contest's page in AWS. The
import creates the missing users and registers each row's participation
(day password, team, group) in that contest, with a column-mapping step,
all-or-nothing validation and a progress bar.

**Architecture:**
- **Logic.** A pure module `cms/server/admin/bulkimport.py` reads and
  validates the CSV and applies it in one transaction. A module
  `cms/server/admin/importjobs.py` keeps in-memory jobs, each running in a
  thread: it hashes the passwords with a 4-thread pool, then applies the
  rows.
- **Handlers.** Two handlers in `cms/server/admin/handlers/contestuser.py`
  serve the page (a GET, and a POST with the file and the mapping) and the
  job status (JSON), which the page polls.
- **Front end.** No server state is kept between the upload and the
  mapping: the browser reads the CSV header itself (FileReader).

**Tech Stack:** Python 3.12. Tornado AWS runs its handlers in an executor
(`_get_sync`/`_post_sync`). The database is SQLAlchemy 2.0 (`select()`)
on PostgreSQL. Templates are Jinja2. The page script is vanilla JS; AWS
has no front-end framework.

**Spec:** `docs/superpowers/specs/2026-09-29-aws-bulk-import-design.md`

## Global Constraints

- **Branch:** `carga-masiva`, in its own worktree. Merge into `beta` only if
  everything passes before 2026-10-02.
- **Fields.**
  - `username`, `first_name`, `last_name` and `password` are required.
  - `team` (a team code; the team must exist) and `group` (a group name of
    this contest; empty means the main group) are optional.
  - `password` is the day password: the participation password of this
    contest.
- **Format.**
  - UTF-8, with the BOM ignored. The header is row 1. The delimiter is `,`
    or `;`, detected from the header line.
  - Cells are stripped, except `password`, which is used as typed.
- **Validation: all or nothing.** On any error, nothing is written. Errors
  read `fila N: …`, with the header as row 1. The error cases are:
  - a required field that is not mapped;
  - a required cell that is empty;
  - a username repeated in the file;
  - an unknown team code;
  - an unknown group of this contest;
  - a password over 72 bytes;
  - more than 5000 data rows;
  - a file over 2 MB.
- **Apply.**
  - A new user gets a random account password:
    `hash_password(generate_random_password(), "bcrypt")`.
  - An existing user (matched by username) gets `first_name` and
    `last_name` updated, and nothing else.
  - A new participation gets `password=hash_password(row password, "bcrypt")`,
    the team (or None) and the group (or the main group).
  - An existing participation gets its password, team and group replaced.
  - Nothing else is touched.
  - Everything happens in one transaction. After the commit, schedule
    `proxy_service.reinitialize`.
- **Passwords** never appear in a response, a page, an error or a log line.
  An error names the row and the column, never the value of a password.
- **Jobs.**
  - Jobs live in memory, with a random id, an owner (the admin id) and a
    contest.
  - `status` is one of `running`, `done` or `error`. Each job also has
    `processed` and `total`, and a `summary` or an `error`.
  - A job is evicted 1 hour after creation.
  - There is one running job per contest; a second one is refused.
  - Only the owner can read a job; any other request gets 404.
- **Permissions.** Only admins with `PERMISSION_ALL`. XSRF applies, as in
  all of AWS.
- **Page texts** are in Spanish: "Importar CSV", "Solo validar",
  "Importar", "Procesando X de N".
- **Code style** (`CLAUDE.md`): PEP 8, PEP 484, the project docstring
  format, English code and comments, Conventional Commits. Never amend,
  rebase or reset.
- **Tests.**
  - Use `.venv/bin/pytest` in the asyncio group, wrapped in
    `timeout --foreground --signal=ABRT 900`.
  - Use a private DB: copy the controller's `ctrl-cms.toml` and rename the
    database.
  - Replace bcrypt with a fast fake in the unit tests.

## Review Focus

1. **A CSV saved by Excel in Spanish** (`;`, a BOM, a header like
   "Contraseña"). It is read correctly, and the mapping is pre-assigned.
   Task 1 and Task 5 pin it.
2. **Re-uploading the same file.** Nothing is created twice: users and
   participations are updated, and the counts say "actualizados". Task 2
   and Task 3 pin it.
3. **A database error in the middle of the apply.** Nothing is written,
   and the job reports an error. Task 3 pins it.
4. **A second admin polling someone else's job id.** They get 404. Task 4
   and Task 5 pin it.
5. **A password with leading or trailing spaces.** It is kept as typed,
   and the contestant logs in with exactly that. Task 1 pins it.

---

### Task 1: Read the CSV with a column mapping

**Files:**
- Create: `cms/server/admin/bulkimport.py`
- Test (create): `cmstestsuite/unit_tests/server/admin/bulkimport_test.py`

**Interfaces:**
- Produces:

```python
FIELDS: tuple[str, ...] = ("username", "first_name", "last_name",
                           "password", "team", "group")
REQUIRED: frozenset[str] = frozenset({"username", "first_name",
                                      "last_name", "password"})
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_PASSWORD_BYTES = 72

@dataclasses.dataclass(frozen=True)
class ImportRow:
    line: int            # 1-based line in the file; the header is 1
    username: str
    first_name: str
    last_name: str
    password: str
    team: str | None     # code, or None if unmapped/empty
    group: str | None    # name, or None if unmapped/empty

def read_rows(data: bytes, mapping: dict[str, str]
              ) -> tuple[list[ImportRow], list[str]]: ...
    # mapping: field -> header name ("" or missing = unmapped)
    # returns (rows, errors); errors non-empty means rows must not be used
```

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for reading the bulk import CSV of AWS."""

import unittest

from cms.server.admin.bulkimport import MAX_ROWS, read_rows

MAPPING = {"username": "usuario", "first_name": "nombre",
           "last_name": "apellido", "password": "contraseña",
           "team": "estado", "group": ""}


def csv_bytes(text: str, bom: bool = False) -> bytes:
    data = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


class TestReadRows(unittest.TestCase):

    def test_semicolon_and_bom(self):
        rows, errors = read_rows(csv_bytes(
            "usuario;nombre;apellido;contraseña;estado\n"
            "ana;Ana;López; s3cr3t ;JAL\n", bom=True), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row.line, row.username, row.first_name,
                          row.last_name, row.team, row.group),
                         (2, "ana", "Ana", "López", "JAL", None))
        # The password is kept as typed.
        self.assertEqual(row.password, " s3cr3t ")

    def test_comma_and_quotes(self):
        rows, errors = read_rows(csv_bytes(
            'usuario,nombre,apellido,contraseña,estado\n'
            'beto,"Roberto, Jr.",Pérez,pw,\n'), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0].first_name, "Roberto, Jr.")
        self.assertIsNone(rows[0].team)

    def test_unmapped_required_field(self):
        mapping = dict(MAPPING, password="")
        _, errors = read_rows(csv_bytes("usuario,nombre,apellido\n"),
                              mapping)
        self.assertTrue(any("password" in e for e in errors))

    def test_mapping_to_a_missing_header(self):
        mapping = dict(MAPPING, team="equipo")
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña\nana,A,L,p\n"), mapping)
        self.assertTrue(any("equipo" in e for e in errors))

    def test_empty_required_cell_and_duplicates(self):
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,,\n"
            "ana,Ana,López,pw,\n"), MAPPING)
        self.assertIn("fila 2: la contraseña está vacía", errors)
        self.assertIn("fila 3: el usuario ana está repetido (fila 2)",
                      errors)

    def test_password_too_long_does_not_echo_it(self):
        secret = "ñ" * 37
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,A,L,%s,\n" % secret), MAPPING)
        self.assertEqual(len(errors), 1)
        self.assertNotIn(secret, errors[0])
        self.assertIn("fila 2", errors[0])

    def test_limits(self):
        _, errors = read_rows(b"x" * (2 * 1024 * 1024 + 1), MAPPING)
        self.assertTrue(errors)
        body = "usuario,nombre,apellido,contraseña,estado\n" + "".join(
            "u%d,A,L,p,\n" % i for i in range(MAX_ROWS + 1))
        _, errors = read_rows(csv_bytes(body), MAPPING)
        self.assertTrue(any(str(MAX_ROWS) in e for e in errors))

    def test_not_utf8(self):
        _, errors = read_rows("usuario\nñ\n".encode("latin-1"), MAPPING)
        self.assertTrue(any("UTF-8" in e for e in errors))
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkimport_test.py -q`
Expected: an ImportError (the `bulkimport` module does not exist).

- [ ] **Step 3: Implement `read_rows`**

```python
"""Bulk import of users and participations from a CSV (AWS).

One CSV per contest: each row creates the user if missing and registers
its participation in the contest, with the day's password.

"""

import csv
import dataclasses
import io

FIELDS: tuple[str, ...] = ("username", "first_name", "last_name",
                           "password", "team", "group")
REQUIRED: frozenset[str] = frozenset({"username", "first_name",
                                      "last_name", "password"})
LABELS = {"username": "el usuario", "first_name": "el nombre",
          "last_name": "el apellido", "password": "la contraseña",
          "team": "el equipo", "group": "el grupo"}
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_PASSWORD_BYTES = 72


@dataclasses.dataclass(frozen=True)
class ImportRow:
    """One data row of the file, already mapped to the import fields."""
    line: int
    username: str
    first_name: str
    last_name: str
    password: str
    team: str | None
    group: str | None


def read_rows(data: bytes, mapping: dict[str, str]
              ) -> tuple[list[ImportRow], list[str]]:
    """Parse the CSV and map its columns to the import fields.

    data: the raw file.
    mapping: import field -> header of the column that holds it ("" or
        missing for an unmapped optional field).

    return: the rows and the list of errors; if there is any error the
        rows must not be used. Errors never contain a password.

    """
    if len(data) > MAX_BYTES:
        return [], ["el archivo pasa de 2 MB"]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], ["el archivo no está en UTF-8"]
    first_line = text.split("\n", 1)[0]
    delimiter = ";" if first_line.count(";") > first_line.count(",") \
        else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        return [], ["el archivo está vacío"]

    errors: list[str] = []
    columns: dict[str, int] = {}
    for field in FIELDS:
        name = (mapping.get(field) or "").strip()
        if not name:
            if field in REQUIRED:
                errors.append("falta asignar la columna de %s (%s)"
                              % (LABELS[field], field))
            continue
        if name not in header:
            errors.append("la columna %s no está en el archivo" % name)
            continue
        columns[field] = header.index(name)
    if errors:
        return [], errors

    rows: list[ImportRow] = []
    seen: dict[str, int] = {}
    for line, cells in enumerate(reader, start=2):
        if not any(cell.strip() for cell in cells):
            continue
        if len(rows) >= MAX_ROWS:
            return [], ["el archivo pasa de %d filas" % MAX_ROWS]

        def cell(field: str) -> str:
            index = columns.get(field)
            if index is None or index >= len(cells):
                return ""
            value = cells[index]
            return value if field == "password" else value.strip()

        values = {field: cell(field) for field in FIELDS}
        for field in REQUIRED:
            if values[field] == "":
                errors.append("fila %d: %s está vacío" % (
                    line, LABELS[field]) if field != "password" else
                    "fila %d: la contraseña está vacía" % line)
        if len(values["password"].encode("utf-8")) > MAX_PASSWORD_BYTES:
            errors.append("fila %d: la contraseña pasa de %d bytes"
                          % (line, MAX_PASSWORD_BYTES))
        username = values["username"]
        if username:
            if username in seen:
                errors.append("fila %d: el usuario %s está repetido "
                              "(fila %d)" % (line, username, seen[username]))
            else:
                seen[username] = line
        rows.append(ImportRow(
            line=line, username=username,
            first_name=values["first_name"],
            last_name=values["last_name"], password=values["password"],
            team=values["team"] or None, group=values["group"] or None))
    return rows, errors
```

Check that the messages match the tests exactly: "la contraseña está
vacía", and "el usuario ana está repetido (fila 2)". The generic message
for an empty required field reads "fila N: el nombre está vacío". Keep
these strings; the page shows them as they are.

- [ ] **Step 4: Run the tests to see them pass**

Run: the same command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add cms/server/admin/bulkimport.py cmstestsuite/unit_tests/server/admin/bulkimport_test.py
git commit -m "feat(aws): read the bulk import CSV with a column mapping"
```

---

### Task 2: Validate against the database, and preview

**Files:**
- Modify: `cms/server/admin/bulkimport.py`, appending `ImportPlan` and
  `plan_import`.
- Test: `cmstestsuite/unit_tests/server/admin/bulkimport_test.py`, adding a
  DB-backed class that uses `DatabaseMixin`
  (`cmstestsuite/unit_tests/databasemixin.py`).

**Interfaces:**
- Consumes: `ImportRow` and `read_rows` (Task 1).
- Produces:

```python
@dataclasses.dataclass
class ImportPlan:
    new_users: list[str]              # usernames
    updated_users: list[str]
    new_participations: list[str]     # usernames
    updated_participations: list[str]
    teams: dict[str, int]             # code -> team id
    groups: dict[str, int]            # name -> group id (this contest)
    main_group_id: int
    def summary(self) -> dict[str, int]: ...  # the four counts

def plan_import(session, contest_id: int, rows: list[ImportRow]
                ) -> tuple[ImportPlan | None, list[str]]: ...
```

- [ ] **Step 1: Write the failing tests**

In a `TestPlanImport(DatabaseMixin, unittest.TestCase)` class:
- set up a contest (`self.add_contest()`), whose main group is
  `contest.main_group`;
- add a team with `self.add_team(code="JAL")` (use the mixin's helper, or
  create `Team(code="JAL", name="Jalisco")` if there is none);
- add an existing user `ana` with a participation in the contest, and an
  existing user `beto` without one.

Rows (built directly as `ImportRow`s): `ana` (team JAL), `beto`, and a new
`carla`.

Expect:
- `new_users == ["carla"]`;
- `updated_users == ["ana", "beto"]`;
- `new_participations == ["beto", "carla"]`;
- `updated_participations == ["ana"]`;
- `summary() == {"usuarios_nuevos": 1, "usuarios_actualizados": 2, "participaciones_nuevas": 2, "participaciones_actualizadas": 1}`.

Add a second test with an unknown team `XYZ` and an unknown group `tarde`.
It expects `(None, ["fila 2: el equipo XYZ no existe", "fila 2: el grupo tarde no existe en este concurso"])`,
and asserts that the DB is unchanged (count users before and after).

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkimport_test.py -q -k Plan`
Expected: an ImportError on `plan_import`.

- [ ] **Step 3: Implement**

```python
from sqlalchemy import select

from cms.db import Contest, Group, Participation, Team, User


@dataclasses.dataclass
class ImportPlan:
    """What an import would do; built without writing anything."""
    new_users: list[str]
    updated_users: list[str]
    new_participations: list[str]
    updated_participations: list[str]
    teams: dict[str, int]
    groups: dict[str, int]
    main_group_id: int

    def summary(self) -> dict[str, int]:
        return {"usuarios_nuevos": len(self.new_users),
                "usuarios_actualizados": len(self.updated_users),
                "participaciones_nuevas": len(self.new_participations),
                "participaciones_actualizadas":
                    len(self.updated_participations)}


def plan_import(session, contest_id: int, rows: list[ImportRow]
                ) -> tuple[ImportPlan | None, list[str]]:
    """Check the rows against the database and plan the import.

    session: a read-only use of a session; nothing is added or flushed.
    contest_id: the contest the participations go to.
    rows: the rows from read_rows, without errors.

    return: the plan, or None and the errors (unknown teams or groups).

    """
    contest = session.get(Contest, contest_id)
    teams = dict(session.execute(select(Team.code, Team.id)).all())
    groups = dict(session.execute(
        select(Group.name, Group.id).filter(Group.contest_id == contest_id)
    ).all())
    errors: list[str] = []
    for row in rows:
        if row.team is not None and row.team not in teams:
            errors.append("fila %d: el equipo %s no existe"
                          % (row.line, row.team))
        if row.group is not None and row.group not in groups:
            errors.append("fila %d: el grupo %s no existe en este concurso"
                          % (row.line, row.group))
    if errors:
        return None, errors
    usernames = [row.username for row in rows]
    existing = set(session.execute(
        select(User.username).filter(User.username.in_(usernames))
    ).scalars())
    participating = set(session.execute(
        select(User.username).join(Participation)
        .filter(Participation.contest_id == contest_id,
                User.username.in_(usernames))
    ).scalars())
    return ImportPlan(
        new_users=[u for u in usernames if u not in existing],
        updated_users=[u for u in usernames if u in existing],
        new_participations=[u for u in usernames if u not in participating],
        updated_participations=[u for u in usernames if u in participating],
        teams=teams, groups=groups,
        main_group_id=contest.main_group_id), []
```

If `contest.main_group_id` is None (a contest with no main group), return
`None, ["el concurso no tiene grupo principal"]`.

- [ ] **Step 4: Run the tests to see them pass.** Run the same command,
  then the whole file.

- [ ] **Step 5: Commit**

```bash
git add cms/server/admin/bulkimport.py cmstestsuite/unit_tests/server/admin/bulkimport_test.py
git commit -m "feat(aws): check a bulk import against the database and plan it"
```

---

### Task 3: Hash the passwords with progress and apply in one transaction

**Files:**
- Modify: `cms/server/admin/bulkimport.py`, appending `hash_passwords` and
  `apply_import`.
- Test: `bulkimport_test.py`, adding classes.

**Interfaces:**
- Consumes: `ImportRow`, `ImportPlan` (Tasks 1-2).
- Produces:

```python
HASH_THREADS = 4

def hash_passwords(rows: list[ImportRow], new_users: set[str],
                   progress: Callable[[], None]
                   ) -> dict[str, tuple[str, str | None]]: ...
    # username -> (participation password hash, account password hash
    #              for new users or None); calls progress() once per row

def apply_import(session, contest_id: int, rows: list[ImportRow],
                 plan: ImportPlan,
                 hashes: dict[str, tuple[str, str | None]]) -> None: ...
    # adds/updates everything in the session; the caller commits
```

- [ ] **Step 1: Write the failing tests**

- **`hash_passwords`.** Patch `cms.server.admin.bulkimport.hash_password`
  with `lambda p, method="bcrypt": "fake:" + p`, and
  `generate_random_password` with `lambda: "RANDOM"`. With 3 rows, of which
  one is new:
  - the result maps each username to `("fake:<pw>", ...)`;
  - the new user's second value is `"fake:RANDOM"`, and the others' is
    `None`;
  - `progress` was called 3 times;
  - the pool used `HASH_THREADS` workers. Assert it by patching
    `concurrent.futures.ThreadPoolExecutor` with a wrapper that records its
    `max_workers`.
- **`apply_import`**, with the same DB fixture as Task 2, fake hashes and
  one commit. Expect:
  - `carla` is created with first and last names, account password
    `"fake:RANDOM"`, and a participation with password `"fake:pw"` in the
    main group;
  - `ana` has updated names, and her participation has the new password,
    team JAL and the named group;
  - `ana`'s participation keeps `hidden=True`, set up before the import;
  - `beto` has a new participation;
  - the account password of existing users is unchanged.
- **All or nothing.** Patch the session's `flush` (or a
  `Participation.__init__` side effect) to raise on the second new
  participation. Call `apply_import` inside the same `SessionGen` block
  the job will use, without committing, then roll back. Expect no new user
  and no changed participation.

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkimport_test.py -q -k "Hash or Apply"`
Expected: an ImportError on the new functions.

- [ ] **Step 3: Implement**

```python
import concurrent.futures
from collections.abc import Callable

from cmscommon.crypto import generate_random_password, hash_password

HASH_THREADS = 4


def hash_passwords(rows: list[ImportRow], new_users: set[str],
                   progress: Callable[[], None]
                   ) -> dict[str, tuple[str, str | None]]:
    """Hash every password of the import, a few at a time.

    bcrypt costs about 0.2 s per password and releases the GIL, so a
    small pool divides the wait.

    rows: the rows to import.
    new_users: the usernames that do not exist yet.
    progress: called once each time a row is done.

    return: username -> (participation password, account password or
        None), both as authentication strings.

    """
    def work(row: ImportRow) -> tuple[str, tuple[str, str | None]]:
        participation = hash_password(row.password, "bcrypt")
        account = hash_password(generate_random_password(), "bcrypt") \
            if row.username in new_users else None
        return row.username, (participation, account)

    result: dict[str, tuple[str, str | None]] = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=HASH_THREADS,
            thread_name_prefix="aws-import-hash") as pool:
        for future in concurrent.futures.as_completed(
                [pool.submit(work, row) for row in rows]):
            username, hashes = future.result()
            result[username] = hashes
            progress()
    return result


def apply_import(session, contest_id: int, rows: list[ImportRow],
                 plan: ImportPlan,
                 hashes: dict[str, tuple[str, str | None]]) -> None:
    """Write the import into the session; the caller commits.

    session: the session of the transaction.
    contest_id: the contest of the participations.
    rows: the rows to import.
    plan: from plan_import, made in this same process just before.
    hashes: from hash_passwords.

    """
    usernames = [row.username for row in rows]
    users = {u.username: u for u in session.execute(
        select(User).filter(User.username.in_(usernames))).scalars()}
    participations = {p.user.username: p for p in session.execute(
        select(Participation).join(User)
        .filter(Participation.contest_id == contest_id,
                User.username.in_(usernames))).scalars()}
    contest = session.get(Contest, contest_id)
    for row in rows:
        participation_hash, account_hash = hashes[row.username]
        user = users.get(row.username)
        if user is None:
            user = User(username=row.username, first_name=row.first_name,
                        last_name=row.last_name, password=account_hash)
            session.add(user)
        else:
            user.first_name = row.first_name
            user.last_name = row.last_name
        team_id = plan.teams[row.team] if row.team is not None else None
        group_id = plan.groups[row.group] if row.group is not None \
            else plan.main_group_id
        participation = participations.get(row.username)
        if participation is None:
            participation = Participation(contest=contest, user=user)
            session.add(participation)
        participation.password = participation_hash
        participation.team_id = team_id
        participation.group_id = group_id
```

A user created in this same call has `account_hash` set: `plan.new_users`
decided it, and `hash_passwords` received `set(plan.new_users)`.

- [ ] **Step 4: Run the tests to see them pass**, then run the whole
  file.

- [ ] **Step 5: Commit**

```bash
git add cms/server/admin/bulkimport.py cmstestsuite/unit_tests/server/admin/bulkimport_test.py
git commit -m "feat(aws): hash the imported passwords in parallel and apply the import in one transaction"
```

---

### Task 4: Import jobs in memory

**Files:**
- Create: `cms/server/admin/importjobs.py`
- Test (create): `cmstestsuite/unit_tests/server/admin/importjobs_test.py`

**Interfaces:**
- Consumes: `read_rows` → `plan_import` → `hash_passwords` →
  `apply_import` (Tasks 1-3), and `SessionGen` from `cms.db`.
- Produces:

```python
JOB_TTL = 3600.0

@dataclasses.dataclass
class ImportJob:
    id: str
    owner_id: int
    contest_id: int
    total: int
    created_at: float
    status: str = "running"          # running | done | error
    processed: int = 0
    summary: dict[str, int] | None = None
    error: str | None = None
    def as_json(self) -> dict: ...   # never contains rows or passwords

class ImportJobStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic): ...
    def start(self, owner_id: int, contest_id: int, rows: list[ImportRow],
              on_done: Callable[[], None]) -> ImportJob: ...
        # raises ValueError("ya hay una importación en curso para este concurso")
        # runs the job in a daemon thread
    def get(self, job_id: str, owner_id: int) -> ImportJob | None: ...
        # None when unknown, expired (evicted) or owned by someone else

IMPORT_JOBS = ImportJobStore()      # the process-wide store AWS uses
```

- [ ] **Step 1: Write the failing tests**

- **A job runs to `done`.** Patch `hash_passwords`, `plan_import` and
  `apply_import` in `cms.server.admin.importjobs`, and patch `SessionGen`
  with a MagicMock context manager.
  - The fake `hash_passwords` calls `progress()` once per row.
  - Wait for the thread with a bounded poll: up to 5 s, every 10 ms.
  - Expect `status == "done"`, `processed == total == len(rows)`, the plan's
    summary, `on_done` called once, and `session.commit` called once.
- **Error path.** `apply_import` raises. Expect `status == "error"`,
  `error == "no se pudo aplicar la importación; no se guardó nada"`, no
  commit, `on_done` not called, and the traceback logged. Check with
  `assertLogs`, and assert that no row data appears in the log output.
- **Plan errors at run time** (the DB changed since validation, e.g. a
  team was deleted). Expect `status == "error"` with the plan's error
  lines, and no commit.
- **One job per contest.** While one job is running, `start` for the same
  contest raises ValueError. Block the fake hash on a `threading.Event`.
  Another contest is allowed.
- **Owner check.** `get(job.id, other_owner)` is None, and `get(job.id, owner)`
  is the job.
- **TTL.** With an injected clock, after `JOB_TTL + 1` s, `get` returns None
  and the job is evicted.
- **`as_json`.** It has exactly `status`, `processed`, `total`, `summary` and
  `error`.

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/server/admin/importjobs_test.py -q`
Expected: an ImportError.

- [ ] **Step 3: Implement**

```python
"""In-memory bulk import jobs of AWS, with their progress.

"""

import dataclasses
import logging
import secrets
import threading
import time
from collections.abc import Callable

from cms.db import SessionGen
from cms.server.admin.bulkimport import ImportRow, apply_import, \
    hash_passwords, plan_import

logger = logging.getLogger(__name__)

JOB_TTL = 3600.0
APPLY_FAILED = "no se pudo aplicar la importación; no se guardó nada"


@dataclasses.dataclass
class ImportJob:
    """One import running (or finished) in this AWS process."""
    id: str
    owner_id: int
    contest_id: int
    total: int
    created_at: float
    status: str = "running"
    processed: int = 0
    summary: dict[str, int] | None = None
    error: str | None = None

    def as_json(self) -> dict:
        return {"status": self.status, "processed": self.processed,
                "total": self.total, "summary": self.summary,
                "error": self.error}


class ImportJobStore:
    """The import jobs of this process, each run in its own thread."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._jobs: dict[str, ImportJob] = {}
        self._lock = threading.Lock()

    def _evict(self) -> None:
        cutoff = self._clock() - JOB_TTL
        for job_id in [i for i, j in self._jobs.items()
                       if j.created_at < cutoff]:
            del self._jobs[job_id]

    def start(self, owner_id: int, contest_id: int, rows: list[ImportRow],
              on_done: Callable[[], None]) -> ImportJob:
        """Start importing rows into a contest, in a thread.

        owner_id: the admin who starts it.
        contest_id: the contest.
        rows: the rows, already validated by read_rows and plan_import.
        on_done: called after the commit (to notify ProxyService).

        return: the job.

        raise (ValueError): if a job is already running for the contest.

        """
        with self._lock:
            self._evict()
            if any(j.contest_id == contest_id and j.status == "running"
                   for j in self._jobs.values()):
                raise ValueError(
                    "ya hay una importación en curso para este concurso")
            job = ImportJob(id=secrets.token_urlsafe(16), owner_id=owner_id,
                            contest_id=contest_id, total=len(rows),
                            created_at=self._clock())
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, rows, on_done),
                         name="aws-import-%s" % job.id[:8],
                         daemon=True).start()
        return job

    def _run(self, job: ImportJob, rows: list[ImportRow],
             on_done: Callable[[], None]) -> None:
        try:
            with SessionGen() as session:
                plan, errors = plan_import(session, job.contest_id, rows)
                if plan is None:
                    job.error = "; ".join(errors)
                    job.status = "error"
                    return

                def progress() -> None:
                    job.processed += 1

                hashes = hash_passwords(rows, set(plan.new_users),
                                        progress)
                apply_import(session, job.contest_id, rows, plan, hashes)
                session.commit()
            job.summary = plan.summary()
            job.status = "done"
            logger.info("Bulk import into contest %d by admin %d: %s.",
                        job.contest_id, job.owner_id, job.summary)
            on_done()
        except Exception:
            logger.exception("Bulk import into contest %d failed.",
                             job.contest_id)
            job.error = APPLY_FAILED
            job.status = "error"

    def get(self, job_id: str, owner_id: int) -> ImportJob | None:
        """Return the job if it exists, is not expired and is owner's."""
        with self._lock:
            self._evict()
            job = self._jobs.get(job_id)
        if job is None or job.owner_id != owner_id:
            return None
        return job


IMPORT_JOBS = ImportJobStore()
```

`job.processed += 1` runs only on the job thread, because
`as_completed` hands the futures back in that thread. Readers only read
it, so no lock is needed. The `logger.exception` message contains only
the contest id and the traceback, and no row data. The test checks that.

- [ ] **Step 4: Run the tests to see them pass.** Run them 5 times, since
  they involve threads.

- [ ] **Step 5: Commit**

```bash
git add cms/server/admin/importjobs.py cmstestsuite/unit_tests/server/admin/importjobs_test.py
git commit -m "feat(aws): run bulk imports as in-memory jobs with progress"
```

---

### Task 5: The page, the status endpoint and the link

**Files:**
- Modify: `cms/server/admin/handlers/contestuser.py`, adding
  `ImportUsersHandler` and `ImportJobStatusHandler`.
- Modify: `cms/server/admin/handlers/__init__.py`, adding the imports and
  two routes after `/contest/([0-9]+)/users/add` (line ~143).
- Create: `cms/server/admin/templates/contest_users_import.html`.
- Modify: `cms/server/admin/templates/contest_users.html`, adding a link
  "Importar CSV" next to the "Add a new user" form.
- Test (create): `cmstestsuite/unit_tests/server/admin/contest_users_import_test.py`.
  Follow the handler-test patterns in
  `cmstestsuite/unit_tests/server/admin/rankinggroup_test.py` (a handler
  built with `__new__` and mocked helpers) and the template render helper
  there (`AWS_ENVIRONMENT`).

**Interfaces:**
- Consumes: `read_rows`, `plan_import` (validation and "Solo validar"),
  and `IMPORT_JOBS.start`/`get` (Task 4).
- Produces the routes:
  - `(r"/contest/([0-9]+)/users/import", ImportUsersHandler)`: GET the
    page, `?job=<id>` for the progress view; POST the file and the mapping.
  - `(r"/contest/([0-9]+)/users/import/([A-Za-z0-9_-]+)/status", ImportJobStatusHandler)`:
    GET the JSON of `ImportJob.as_json()`, or 404.

- [ ] **Step 1: Write the failing tests**

- **POST "Solo validar" with a valid file.** Build the handler with
  `__new__`, and fake:
  - `self.request.files = {"file": [{"body": b"..."}]}`;
  - `get_argument` for `action=validate` and `map_<field>`;
  - `safe_get_item`, `render` (capture its params) and `sql_session`.

  Patch `plan_import` to return a plan. Expect `render` called with
  `summary` and no `errors`. `IMPORT_JOBS.start` is not called.
- **POST "Importar", valid.** `IMPORT_JOBS.start` is called with
  `(admin id, contest id, rows, on_done)`, then there is a redirect to
  `…/users/import?job=<id>`. `on_done` schedules
  `proxy_service.reinitialize`: call it and check `schedule_rpc`.
- **POST with errors** (from `read_rows` or `plan_import`). `render` gets
  `errors`, and nothing starts.
- **POST while a job is running** (`start` raises ValueError). `render`
  gets `errors` containing its message.
- **A file over the limit or missing.** An error is rendered.
- **Status handler.**
  - The owner gets 200 and `as_json()`.
  - Another admin, or an unknown id, gets 404.
  - The JSON never contains the key `rows` or any password (build a job
    with a known password in its rows and check the body).
- **Template render** (core block, like `TestRankingGroupTemplates`). The
  page has:
  - `<input type="file" name="file"`;
  - one `<select name="map_<field>">` per field;
  - buttons `name="action" value="validate"` ("Solo validar") and
    `value="import"` ("Importar");
  - the alias list in the script (`"contraseña"`, `"estado"`).

  With `job` set, it renders a `<progress>` and the status URL. With
  `errors`, it renders them as a list, escaped.
- **Permissions.** Both handlers' `post`/`get` are decorated with
  `require_permission(BaseHandler.PERMISSION_ALL)`. Assert it the way the
  existing tests do for other handlers, or by calling as a non-all admin
  and expecting 403 if a helper exists.

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/server/admin/contest_users_import_test.py -q`
Expected: ImportError, or a missing template.

- [ ] **Step 3: Implement the handlers**

```python
import json

from cms.server.admin.bulkimport import FIELDS, read_rows, plan_import
from cms.server.admin.importjobs import IMPORT_JOBS


class ImportUsersHandler(BaseHandler):
    """Bulk import of users and participations into a contest (CSV)."""

    def _render_page(self, **extra) -> None:
        self.r_params = self.render_params()
        self.r_params["contest"] = self.contest
        self.r_params["fields"] = FIELDS
        self.r_params.update(extra)
        self.render("contest_users_import.html", **self.r_params)

    def _get_sync(self, contest_id: str) -> None:
        self.contest = self.safe_get_item(Contest, contest_id)
        job_id = self.get_argument("job", None)
        job = None if job_id is None else \
            IMPORT_JOBS.get(job_id, self.current_user.id)
        self._render_page(job=job)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self, contest_id: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, contest_id)

    def _post_sync(self, contest_id: str) -> None:
        self.contest = self.safe_get_item(Contest, contest_id)
        files = self.request.files.get("file")
        if not files:
            self._render_page(errors=["elige un archivo CSV"])
            return
        mapping = {field: self.get_argument("map_" + field, "")
                   for field in FIELDS}
        rows, errors = read_rows(files[0]["body"], mapping)
        plan = None
        if not errors:
            plan, errors = plan_import(self.sql_session, self.contest.id,
                                       rows)
        if errors:
            self._render_page(errors=errors)
            return
        if self.get_argument("action", "validate") != "import":
            self._render_page(summary=plan.summary(), validated=True)
            return
        service = self.service

        def on_done() -> None:
            self.schedule_rpc(service.proxy_service.reinitialize)

        try:
            job = IMPORT_JOBS.start(self.current_user.id, self.contest.id,
                                    rows, on_done)
        except ValueError as error:
            self._render_page(errors=[str(error)])
            return
        self.redirect(self.url("contest", self.contest.id, "users",
                               "import") + "?job=" + job.id)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self, contest_id: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync, contest_id)


class ImportJobStatusHandler(BaseHandler):
    """The progress of an import job, as JSON, for its owner only."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self, contest_id: str, job_id: str):
        job = IMPORT_JOBS.get(job_id, self.current_user.id)
        if job is None or str(job.contest_id) != contest_id:
            raise tornado.web.HTTPError(404)
        self.set_header("Content-Type", "application/json")
        self.set_header("Cache-Control", "no-store")
        self.write(json.dumps(job.as_json()))
```

Check two things against `cms/server/admin/handlers/base.py` and adapt if
they differ:
- how the logged-in admin is exposed (`self.current_user` is an `Admin`
  with `.id`);
- whether `render_params()` needs the contest set first.

The form must be `enctype="multipart/form-data"` for `request.files`.

- [ ] **Step 4: Implement the template** (`contest_users_import.html`)

```html
{% extends "base.html" %}

{% block core %}
<div class="core_title">
  <h1>Importar CSV &mdash; {{ contest.name }}</h1>
</div>

<p>Un renglón por concursante: crea los usuarios que falten y registra su
participación en este concurso con la contraseña del día. Si el usuario o
la participación ya existen, se actualizan. Si hay cualquier error, no se
guarda nada.</p>

{% if errors %}
<div class="notification error">
  <p>No se aplicó nada. Corrige estos errores y vuelve a subir el archivo:</p>
  <ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul>
</div>
{% endif %}

{% if validated and summary %}
<div class="notification">
  <p>El archivo es válido. Al importarlo:</p>
  <ul>
    <li>Usuarios nuevos: {{ summary.usuarios_nuevos }}</li>
    <li>Usuarios actualizados: {{ summary.usuarios_actualizados }}</li>
    <li>Participaciones nuevas: {{ summary.participaciones_nuevas }}</li>
    <li>Participaciones actualizadas: {{ summary.participaciones_actualizadas }}</li>
  </ul>
</div>
{% endif %}

{% if job %}
<div id="import_job" data-status-url="{{ url("contest", contest.id, "users", "import", job.id, "status") }}">
  <p id="import_text">Procesando {{ job.processed }} de {{ job.total }}</p>
  <progress id="import_bar" value="{{ job.processed }}" max="{{ job.total }}" style="width: 100%"></progress>
  <div id="import_result"></div>
</div>
{% else %}
<form enctype="multipart/form-data" method="POST"
      action="{{ url("contest", contest.id, "users", "import") }}">
  {{ xsrf_form_html|safe }}
  <p><input type="file" name="file" id="import_file" accept=".csv,text/csv" required /></p>
  <table>
    {% for field in fields %}
    <tr>
      <td>{{ field }}{% if field in ("username", "first_name", "last_name", "password") %} *{% endif %}</td>
      <td><select name="map_{{ field }}" data-field="{{ field }}"><option value="">(sin asignar)</option></select></td>
    </tr>
    {% endfor %}
  </table>
  <p>La importación cifra las contraseñas y puede tardar ~30 s por cada 300 concursantes.</p>
  <button type="submit" name="action" value="validate">Solo validar</button>
  <button type="submit" name="action" value="import">Importar</button>
</form>
{% endif %}

<script>
(function () {
    "use strict";
    var ALIASES = {
        "username": ["username", "usuario", "user"],
        "first_name": ["first_name", "nombre", "nombres"],
        "last_name": ["last_name", "apellido", "apellidos"],
        "password": ["password", "contraseña", "contrasena", "clave"],
        "team": ["team", "equipo", "estado"],
        "group": ["group", "grupo"]
    };
    function norm(s) {
        return s.trim().toLowerCase().normalize("NFD")
            .replace(/[̀-ͯ]/g, "").replace(/^"|"$/g, "");
    }
    var fileInput = document.getElementById("import_file");
    if (fileInput) {
        fileInput.addEventListener("change", function () {
            var file = fileInput.files[0];
            if (!file) { return; }
            var reader = new FileReader();
            reader.onload = function () {
                var text = String(reader.result).replace(/^﻿/, "");
                var first = text.split(/\r?\n/)[0];
                var sep = (first.split(";").length > first.split(",").length) ? ";" : ",";
                var headers = first.split(sep).map(function (h) {
                    return h.trim().replace(/^"|"$/g, "");
                });
                document.querySelectorAll("select[data-field]").forEach(function (select) {
                    var field = select.getAttribute("data-field");
                    select.length = 1;
                    headers.forEach(function (h) {
                        var option = document.createElement("option");
                        option.value = h;
                        option.textContent = h;
                        if (ALIASES[field].indexOf(norm(h)) !== -1) {
                            option.selected = true;
                        }
                        select.appendChild(option);
                    });
                });
            };
            reader.readAsText(file.slice(0, 65536), "utf-8");
        });
    }
    var job = document.getElementById("import_job");
    if (job) {
        var url = job.getAttribute("data-status-url");
        var poll = function () {
            fetch(url, {credentials: "same-origin"}).then(function (r) {
                if (!r.ok) { throw new Error(r.status); }
                return r.json();
            }).then(function (s) {
                document.getElementById("import_bar").value = s.processed;
                document.getElementById("import_bar").max = s.total;
                document.getElementById("import_text").textContent =
                    "Procesando " + s.processed + " de " + s.total;
                var out = document.getElementById("import_result");
                if (s.status === "done") {
                    out.textContent = "Listo. Usuarios nuevos: " + s.summary.usuarios_nuevos +
                        ", actualizados: " + s.summary.usuarios_actualizados +
                        ". Participaciones nuevas: " + s.summary.participaciones_nuevas +
                        ", actualizadas: " + s.summary.participaciones_actualizadas + ".";
                } else if (s.status === "error") {
                    out.textContent = "Error: " + s.error;
                } else {
                    setTimeout(poll, 500);
                }
            }).catch(function () {
                document.getElementById("import_result").textContent =
                    "No se pudo consultar el progreso; recarga la página.";
            });
        };
        poll();
    }
}());
</script>
{% endblock core %}
```

In `contest_users.html`, add after the "Add a new user" form:

```html
{% if admin.permission_all %}
<p><a href="{{ url("contest", contest.id, "users", "import") }}">Importar CSV</a></p>
{% endif %}
```

If `url(...)` does not accept the job id or `"status"` as a path part, build
the status URL with `url("contest", contest.id, "users", "import")` + `"/" + job.id + "/status"`.

- [ ] **Step 5: Add the routes** in `handlers/__init__.py`, and add the
  two class names to the `from .contestuser import …` list:

```python
    (r"/contest/([0-9]+)/users/import", ImportUsersHandler),
    (r"/contest/([0-9]+)/users/import/([A-Za-z0-9_-]+)/status",
     ImportJobStatusHandler),
```

- [ ] **Step 6: Run the tests to see them pass**

Run the new file, then the whole `cmstestsuite/unit_tests/server/admin/`.
Run `eslint` only if `cms/server/admin/templates` is covered by the eslint
config; the script is inline, so check its style by eye: 4 spaces and
double quotes.

- [ ] **Step 7: Commit**

```bash
git add cms/server/admin/handlers/contestuser.py cms/server/admin/handlers/__init__.py cms/server/admin/templates/contest_users_import.html cms/server/admin/templates/contest_users.html cmstestsuite/unit_tests/server/admin/contest_users_import_test.py
git commit -m "feat(aws): import users and participations of a contest from a CSV"
```

---

### Task 6: Operator documentation

**Files:**
- Create: `docs/importing-users.md`
- Modify: `docs/cms-loader.md`, adding one line at the top: "AWS can now
  import users and participations itself; see
  [importing-users.md](importing-users.md). CMS-Loader will be removed
  after 2026-10-10."

- [ ] **Step 1: Write `docs/importing-users.md`**, in English, consistent
  with the other docs:
  - **Where:** contest → Users → "Importar CSV".
  - **The fields**, with the Spanish aliases the page recognises.
  - **The day password:** it is the participation password of that
    contest. Upload one file per contest day, with that day's passwords.
  - **Accounts:** new accounts get a random password, so only the day
    password logs in.
  - **Existing users and participations** are updated. Re-upload a
    corrected file to fix mistakes.
  - **Teams and groups** must exist first.
  - **"Solo validar"** first, then "Importar". Hashing takes about 30 s per
    300 rows.
  - **Nothing can be downloaded:** passwords are stored hashed, so keep the
    original file safe.
  - **Excel:** save it as "CSV UTF-8". Commas or semicolons both work.
- [ ] **Step 2: Commit**

```bash
git add docs/importing-users.md docs/cms-loader.md
git commit -m "docs: explain the bulk import of users and participations in AWS"
```

---

### Task 7: End-to-end verification in Chromium (no code)

Run it on a local stack built from the branch tip, on rootful Docker (see
`.superpowers/scratch/e2e-stack/stack.sh` in the beta worktree and its
CDP driver in `e2e-browser/`):

1. **Import.** Create teams (for example 10 state codes) and a group
   "tarde" in a contest. Build a CSV of about 300 rows, `;`-separated with
   a BOM and Spanish headers, then:
   - "Solo validar" shows the right counts;
   - "Importar" shows the bar advancing to 300 and then the summary;
   - the time is recorded.
2. **Login.** A contestant logs in to CWS with the day password. The
   account password (random) does not work.
3. **Re-upload.** Upload the file again with 2 names corrected and 1
   password changed: the counts say "actualizados", and the new password
   works.
4. **Errors.** Upload a file with an unknown team and a repeated user.
   The errors are listed, and nothing changes in the DB.
5. **Security.** A second admin without full permission gets 403 on the
   page. The status URL of another admin's job gives 404. No password
   appears in the AWS log.
6. **Ranking.** After the import, the users appear in the ranking of the
   contest's group: ProxyService was reinitialized.
