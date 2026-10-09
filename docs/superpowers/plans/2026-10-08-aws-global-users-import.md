# Global Users Import and Optional Contest Password — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CSV import of accounts (with their account password) to the global Users list of AWS, and make the password column of the contest CSV import optional so new participations log in with the account password.

**Architecture:** Reuse the contest import. `read_rows` takes the field set; new `plan_user_import` / `hash_account_passwords` / `apply_user_import` sit next to the contest ones in `bulkimport.py`; `ImportJob.contest_id = None` marks a global job; the contest page handler becomes a base class shared with a new global handler in the same module; one Jinja template serves both pages.

**Tech Stack:** Python 3.12, Tornado (AWS handlers), SQLAlchemy (PostgreSQL), Jinja2 with `StrictUndefined`, bcrypt via `cmscommon.crypto`, unittest/pytest, Sphinx + gettext `.po` for the manual.

**Spec:** `docs/superpowers/specs/2026-10-08-aws-global-users-import-design.md`

## Global Constraints

- Base: `origin/main` at `8a4528b3` or later (it carries the shared-columns fix: any CSV column may feed several fields except the password column). Branch `worktree-aws-global-users-import`, worktree `.claude/worktrees/aws-global-users-import`.
- Code, identifiers and comments in English; every message shown on the page stays in Spanish, written exactly as given in this plan.
- PEP 8, PEP 484 annotations, no pyflakes warnings, docstrings in the project format (imperative first line, then `name: ...` / `return: ...` / `raise (Error): ...` sections, as in `bulkimport.py`). Match the surrounding comment density.
- A cell or header of the column mapped to `password` never reaches an error message, a log line or the page.
- Global import fields: `username`, `first_name`, `last_name`, `password`, all required. Contest import: `username`, `first_name`, `last_name` required; `password`, `team`, `group` optional.
- Existing tests keep passing; a test may only change where this plan says the behaviour changes.
- Commits: Conventional Commits, scope `aws` for code (`docs` for the manual), body explaining why, last line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push, merge or open a PR (the controller does it once, after the final review). GitHub Actions are disabled on the fork: verify locally only.
- Running tests (from the worktree root): create the venv once with `uv venv -q --python 3.12 .venv && uv pip install -q --python .venv/bin/python -c constraints.txt -e ".[devel]"`. Start the DB with `podman start cms-test-pg`. Use the config `/home/areslolxd/.claude/jobs/ae26e043/tmp/cms.toml` (a copy of `config/cms.sample.toml` whose `url` is `postgresql+psycopg2://cmsuser:cmsuser@localhost:55432/cmsdbfortesting`; create it if missing) and prefix every command with `CMS_CONFIG=/home/areslolxd/.claude/jobs/ae26e043/tmp/cms.toml`. The first time, init the DB: `.venv/bin/python -c "from cms.db import init_db, drop_db; drop_db(); init_db()"`. Run one pytest process at a time. Never run a bare `pytest` over the whole suite (gevent monkey-patching hangs asyncio tests). pyflakes: `uvx -q pyflakes <files>`.
- Short name used below: `T=CMS_CONFIG=/home/areslolxd/.claude/jobs/ae26e043/tmp/cms.toml` (write it out in full when running).

## Review Focus

1. **A header-less file on the global page with a field mapped to the password "header".** The page must not echo it, neither in errors nor in the `data-selected` of another select. Pinned in Task 5 (shared `_render_page`) and Task 6 (global page).
2. **Re-importing the same accounts while contestants are logged in to a contest whose participation has no password.** They must stay logged in: the stored account hash is kept when the password still matches. Pinned in Task 3 (real bcrypt + CWS cookie).
3. **The status URL of a global job asked through the contest route, or the reverse.** 404 both ways, revealing nothing. Pinned in Task 6.
4. **A contest file without a password column whose row names a user that is missing.** Every such row must be reported, in row order, together with any team or group error of other rows, and nothing applied. Pinned in Task 2.
5. **A global file that also has team/group/extra columns.** Only the four selectors exist, the extra columns are ignored, and nothing about participations changes. Pinned in Task 1 (`read_rows`) and Task 3 (apply leaves participations alone).

---

### Task 1: `read_rows` takes the field set; the contest password becomes optional

**Files:**
- Modify: `cms/server/admin/bulkimport.py` (constants at lines 49-60, `ImportRow` at 70-87, `read_rows` at 105-230)
- Test: `cmstestsuite/unit_tests/server/admin/bulkimport_test.py` (class `TestReadRows`)
- Test: `cmstestsuite/unit_tests/server/admin/contest_users_import_test.py:205-219` (one test that relied on the password being required)

**Interfaces:**
- Produces:
  - `FIELDS: tuple[str, ...]` (unchanged: the six contest fields)
  - `REQUIRED: frozenset[str] = frozenset({"username", "first_name", "last_name"})` (contest; password removed)
  - `USER_FIELDS: tuple[str, ...] = ("username", "first_name", "last_name", "password")`
  - `USER_REQUIRED: frozenset[str] = frozenset(USER_FIELDS)`
  - `ImportRow.password: str | None` — `None` when the password field is not mapped.
  - `read_rows(data: bytes, mapping: dict[str, str], fields: tuple[str, ...] = FIELDS, required: frozenset[str] = REQUIRED) -> tuple[list[ImportRow], list[str]]`. `fields` must contain `username`, `first_name`, `last_name` and `password`. A field not in `fields` reads as `None` (`team`, `group`).

- [ ] **Step 1: Write the failing tests**

In `bulkimport_test.py`, change the import line to also take the new names:

```python
from cms.server.admin.bulkimport import HASH_THREADS, MAX_ROWS, USER_FIELDS, \
    USER_REQUIRED, ImportRow, apply_import, hash_passwords, plan_import, \
    read_rows
```

Add next to `MAPPING`:

```python
USER_MAPPING = {"username": "usuario", "first_name": "nombre",
                "last_name": "apellido", "password": "contraseña"}


def read_user_rows(text: str, mapping: dict[str, str] | None = None):
    """Read a CSV with the fields of the global users import."""
    return read_rows(csv_bytes(text),
                     USER_MAPPING if mapping is None else mapping,
                     USER_FIELDS, USER_REQUIRED)
```

Replace `test_unmapped_required_field` and `test_unmapped_required_fields_read_well_in_spanish` with:

```python
    def test_unmapped_required_field(self):
        mapping = dict(MAPPING, username="")
        _, errors = read_rows(csv_bytes("usuario,nombre,apellido\n"),
                              mapping)
        self.assertIn(
            "falta asignar la columna para el usuario (username)", errors)

    def test_unmapped_required_fields_read_well_in_spanish(self):
        for fields, required, mapping, cases in (
                (None, None, MAPPING, (
                    ("username", "el usuario"), ("first_name", "el nombre"),
                    ("last_name", "el apellido"))),
                (USER_FIELDS, USER_REQUIRED, USER_MAPPING, (
                    ("username", "el usuario"), ("first_name", "el nombre"),
                    ("last_name", "el apellido"),
                    ("password", "la contraseña")))):
            for field, text in cases:
                with self.subTest(fields=fields, field=field):
                    args = () if fields is None else (fields, required)
                    _, errors = read_rows(
                        csv_bytes("usuario,nombre,apellido,contraseña,"
                                  "estado\n"),
                        dict(mapping, **{field: ""}), *args)
                    self.assertEqual(
                        errors,
                        ["falta asignar la columna para %s (%s)"
                         % (text, field)])
```

Add to `TestReadRows`:

```python
    def test_the_contest_password_may_be_left_unmapped(self):
        rows, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,pw,JAL\n"), dict(MAPPING, password=""))
        self.assertEqual(errors, [])
        self.assertIsNone(rows[0].password)
        self.assertEqual((rows[0].username, rows[0].team), ("ana", "JAL"))

    def test_a_mapped_contest_password_still_needs_every_cell(self):
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,,\n"), MAPPING)
        self.assertEqual(errors, ["fila 2: la contraseña está vacía"])

    def test_the_global_fields(self):
        # Extra columns, a team among them, are ignored.
        rows, errors = read_user_rows(
            "usuario,nombre,apellido,contraseña,estado,grupo\n"
            "ana,Ana,López, pw ,JAL,tarde\n")
        self.assertEqual(errors, [])
        self.assertEqual(rows, [ImportRow(
            line=2, username="ana", first_name="Ana", last_name="López",
            password="pw", team=None, group=None)])

    def test_the_global_password_column_is_exclusive_and_never_echoed(self):
        # A file without a header row: its first row holds a password.
        text = "ana,Ana,López,S3CRET\nbeto,Beto,Ruiz,pw2\n"
        mapping = {"username": "ana", "first_name": "S3CRET",
                   "last_name": "López", "password": "S3CRET"}
        rows, errors = read_user_rows(text, mapping)
        self.assertEqual(rows, [])
        self.assertEqual(errors, ["la columna de la contraseña está "
                                  "asignada a más de un campo"])

    def test_the_global_rows_check_the_password_as_the_contest_does(self):
        _, errors = read_user_rows(
            "usuario,nombre,apellido,contraseña\n"
            "ana,Ana,López,\n"
            "beto,Beto,Ruiz,%s\n"
            "carla,Carla,Sanz,a\tb\n" % ("ñ" * 37))
        self.assertEqual(errors, [
            "fila 2: la contraseña está vacía",
            "fila 3: la contraseña pasa de 72 bytes",
            "fila 4: la contraseña tiene caracteres no permitidos"])

    def test_headerless_file_with_two_fields_on_one_column(self):
        # The team and the group read the same column; the password has
        # its own one and is never echoed.
        text = "ana;Ana;López;s3cret;JAL\nbeto;Beto;Ruiz;pw;JAL\n"
        mapping = {"username": "ana", "first_name": "Ana",
                   "last_name": "López", "password": "s3cret",
                   "team": "JAL", "group": "JAL"}
        rows, errors = read_rows(csv_bytes(text), mapping)
        self.assertEqual(errors, [])
        self.assertEqual([(r.username, r.team, r.group) for r in rows],
                         [("beto", "JAL", "JAL")])
```

In `contest_users_import_test.py`, `test_errors_of_the_file_are_shown_and_nothing_starts` (line 205) leaves the password unassigned to cause an error; that is no longer an error. Make it leave the username unassigned instead:

```python
    def test_errors_of_the_file_are_shown_and_nothing_starts(self):
        # The username column is not assigned to any header.
        mapping = {**MAPPING, "username": ""}
        handler = make_handler(form=import_form("import", mapping))
        with mock.patch(MODULE + ".plan_import") as plan_import, \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            handler._post_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertEqual(len(params["errors"]), 1)
        self.assertIn("username", params["errors"][0])
        self.assertIsNone(params["summary"])
        plan_import.assert_not_called()
        jobs.start.assert_not_called()
        handler.redirect.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `CMS_CONFIG=... .venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkimport_test.py -k TestReadRows -q`
Expected: FAIL — `ImportError: cannot import name 'USER_FIELDS'` (the whole module fails to import).

- [ ] **Step 3: Implement**

In `bulkimport.py` replace the constants:

```python
FIELDS: tuple[str, ...] = ("username", "first_name", "last_name",
                           "password", "team", "group")
# Without a password column, the participations get none of their own and
# log in with the account password.
REQUIRED: frozenset[str] = frozenset({"username", "first_name",
                                      "last_name"})
# The global import of users: accounts with their own password.
USER_FIELDS: tuple[str, ...] = ("username", "first_name", "last_name",
                                "password")
USER_REQUIRED: frozenset[str] = frozenset(USER_FIELDS)
```

In `ImportRow`, change the field and document it:

```python
    line: int
    username: str
    first_name: str
    last_name: str
    password: str | None
    team: str | None
    group: str | None
```

and add to its docstring, after `line`:

```
    password: None when the file has no password column (a contest import
        that leaves the participation passwords alone).
```

Change the signature and docstring of `read_rows`:

```python
def read_rows(data: bytes, mapping: dict[str, str],
              fields: tuple[str, ...] = FIELDS,
              required: frozenset[str] = REQUIRED
              ) -> tuple[list[ImportRow], list[str]]:
    """Parse the CSV and map its columns to the import fields.

    data: the raw file.
    mapping: import field -> header of the column that holds it ("" or
        missing for an unmapped optional field).
    fields: the fields of the import; they include username, first_name,
        last_name and password. A field out of them reads as None.
    required: the fields that must be mapped and filled in every row. A
        mapped password must be filled in every row too.

    return: the rows and the list of errors; if there is any error the
        rows must not be used. Errors never contain a data cell of the
        column mapped to password.

    """
```

In the mapping loop, iterate `fields` and check `required`:

```python
    for field in fields:
        name = (mapping.get(field) or "").strip()
        if not name:
            if field in required:
                errors.append("falta asignar la columna para %s (%s)"
                              % (LABELS[field], field))
            continue
```

(the rest of the loop body is unchanged). Then, in the row loop, replace from `values = {field: cell(field) for field in FIELDS}` down to the `rows.append(...)` call with:

```python
        values = {field: cell(field) for field in fields}
        for field in fields:
            if field in must_fill and not values[field]:
                errors.append("fila %d: %s" % (line, EMPTY_MESSAGES[field]))
            elif field != "password" and "\x00" in values[field]:
                # The database refuses NUL in a text, so the job would
                # fail at the commit. The password has a check of its own
                # below. Spanish contracts "de el" into "del".
                of_label = ("de " + LABELS[field]).replace("de el ", "del ")
                errors.append("fila %d: la celda %s tiene caracteres no "
                              "permitidos" % (line, of_label))
```

keep the 72-byte, control-character, username and repeated-user checks exactly as they are (they read `values["password"]` and `values["username"]`, which exist for every field set), and build the row with:

```python
        rows.append(ImportRow(
            line=line, username=username,
            first_name=values["first_name"],
            last_name=values["last_name"],
            password=values["password"] if "password" in columns else None,
            team=values.get("team") or None,
            group=values.get("group") or None))
```

Define `must_fill` right after the `if errors: return [], errors` that ends the mapping checks:

```python
    # A mapped password must be in every row, even where it is optional:
    # an empty cell would be an empty password.
    must_fill = required | ({"password"} & columns.keys())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `CMS_CONFIG=... .venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkimport_test.py cmstestsuite/unit_tests/server/admin/contest_users_import_test.py -q`
Expected: PASS (the template still marks the password with `*` until Task 5, and its tests still expect that).

- [ ] **Step 5: Commit**

```bash
git add cms/server/admin/bulkimport.py cmstestsuite/unit_tests/server/admin/bulkimport_test.py cmstestsuite/unit_tests/server/admin/contest_users_import_test.py
git commit -m "feat(aws): let read_rows take the fields of an import

The contest import no longer requires a password column, and the global
users import will read four required fields with the same rules.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Contest import without a password column

**Files:**
- Modify: `cms/server/admin/bulkimport.py` (`plan_import`, `hash_passwords`, `apply_import`)
- Test: `cmstestsuite/unit_tests/server/admin/bulkimport_test.py` (`TestPlanImport`, `TestHashPasswords`, `TestApplyImport`, `TestReimportAndTheContestCookie`)

**Interfaces:**
- Consumes: `ImportRow.password: str | None` (Task 1).
- Produces:
  - `plan_import(session, contest_id, rows)` — unchanged signature; with `row.password is None`, a username that does not exist is the row error `"fila %d: el usuario %s no existe; sin columna de contraseña solo se pueden inscribir usuarios que ya existen"`.
  - `_hash_or_keep(password: str, stored: str | None) -> str` (module-private helper).
  - `_hash_in_pool(rows: list[ImportRow], work: Callable[[ImportRow], tuple[str, T]], progress: Callable[[], None]) -> dict[str, T]` (module-private).
  - `hash_passwords(rows, new_users, stored_passwords, progress) -> dict[str, tuple[str | None, str | None]]` — participation hash is `None` for a row without password.
  - `apply_import(...)` — unchanged signature; a `None` participation hash leaves an existing participation's password alone and creates a new one with no password.

- [ ] **Step 1: Write the failing tests**

Add to `TestPlanImport`:

```python
    def test_without_a_password_only_existing_users_can_be_registered(self):
        # beto exists without a participation, carla and dora do not exist,
        # and row 3 also has an unknown team: every error, in row order.
        rows = [import_row(2, "beto", password=None),
                import_row(3, "carla", team="XYZ", password=None),
                import_row(4, "ana", password=None),
                import_row(5, "dora", password=None)]
        result = plan_import(self.session, self.contest.id, rows)
        self.assertEqual(result, (None, [
            "fila 3: el equipo XYZ no existe",
            "fila 3: el usuario carla no existe; sin columna de contraseña "
            "solo se pueden inscribir usuarios que ya existen",
            "fila 5: el usuario dora no existe; sin columna de contraseña "
            "solo se pueden inscribir usuarios que ya existen"]))

    def test_without_a_password_existing_users_are_planned(self):
        plan, errors = plan_import(self.session, self.contest.id, [
            import_row(2, "ana", password=None),
            import_row(3, "beto", password=None)])
        self.assertEqual(errors, [])
        self.assertEqual(plan.new_users, [])
        self.assertEqual(plan.new_participations, ["beto"])
        self.assertEqual(plan.updated_participations, ["ana"])
```

Change `import_row` so the password can be `None`:

```python
def import_row(line: int, username: str, team: str | None = None,
               group: str | None = None,
               password: str | None = "pw") -> ImportRow:
```

Add to `TestHashPasswords`:

```python
    def test_a_row_without_password_hashes_no_participation(self):
        rows = [import_row(2, "ana", password=None)]
        calls = []
        with mock.patch("cms.server.admin.bulkimport.validate_password") \
                as validate:
            result = hash_passwords(rows, set(), {"ana": "bcrypt:x"},
                                    lambda: calls.append(None))
        self.assertEqual(result, {"ana": (None, None)})
        self.assertEqual(len(calls), 1)
        validate.assert_not_called()
```

Add to `TestApplyImport`:

```python
    def test_without_a_password_participation_passwords_are_left_alone(self):
        # ana takes part with a day password; beto is registered now.
        self.ana_participation.password = "bcrypt:day-ana"
        self.session.flush()
        rows = [import_row(2, "ana", password=None),
                import_row(3, "beto", password=None)]

        apply_import(self.session, self.contest.id, rows,
                     self.plan(self.session, rows),
                     {"ana": (None, None), "beto": (None, None)})
        self.session.commit()

        participations = {p.user.username: p for p in
                          self.session.query(Participation).filter(
                              Participation.contest_id == self.contest.id)}
        self.assertEqual(participations["ana"].password, "bcrypt:day-ana")
        self.assertIsNone(participations["beto"].password)
        # The account passwords are not touched either.
        self.assertEqual(self.ana.password, "account:ana")
        self.assertEqual(self.beto.password, "account:beto")
```

Add to `TestReimportAndTheContestCookie`:

```python
    def test_without_a_password_the_account_password_logs_in(self):
        self.beto.password = hash_password_for_tests("cuenta-beto")
        self.session.commit()
        rows = [import_row(2, "beto", password=None)]

        self.run_import(rows)

        self.assertIsNone(self.stored_password("beto"))
        cookie = self.log_in("beto", "cuenta-beto")
        # Importing the same file again keeps the contestant logged in.
        self.run_import(rows)
        self.assertTrue(self.cookie_authenticates(cookie))
```

with, at module level next to `import_row`:

```python
def hash_password_for_tests(password: str) -> str:
    """Hash with real bcrypt (the tests lower its rounds)."""
    return hash_password(password, "bcrypt")
```

and `from cmscommon.crypto import hash_password` in the imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `CMS_CONFIG=... .venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkimport_test.py -q -k "password or without"`
Expected: FAIL — no "no existe" errors; `hash_password(None, ...)` raises `TypeError`/`AttributeError`; `apply_import` writes `None` over the day password.

- [ ] **Step 3: Implement**

In `plan_import`, read the existing usernames before the row checks, and check all rows in one pass (replace the block from `errors: list[str] = []` to the `existing = set(...)` statement):

```python
        usernames = [row.username for row in rows]
        existing = set(session.execute(
            select(User.username).filter(User.username.in_(usernames))
        ).scalars())
        errors: list[str] = []
        for row in rows:
            if row.team is not None and row.team not in teams:
                errors.append("fila %d: el equipo %s no existe"
                              % (row.line, row.team))
            if row.group is not None and row.group not in groups:
                errors.append("fila %d: el grupo %s no existe en este "
                              "concurso" % (row.line, row.group))
            # A new user would get a random account password, and with no
            # participation password nothing could log in.
            if row.password is None and row.username not in existing:
                errors.append(
                    "fila %d: el usuario %s no existe; sin columna de "
                    "contraseña solo se pueden inscribir usuarios que ya "
                    "existen" % (row.line, row.username))
        if errors:
            return None, errors
```

(the `current = session.execute(...)` query that follows stays as is). Update its docstring `return:` to add "or a user does not exist and the rows have no password".

Replace `hash_passwords` with two helpers and the new body:

```python
def _hash_or_keep(password: str, stored: str | None) -> str:
    """Hash a password, unless the stored bcrypt hash still matches it.

    The CWS cookie holds the stored string, and a new hash of the same
    password is another string (bcrypt salts it), so replacing it would
    log the contestant out. Checking costs about as much as hashing.

    password: the password of the row.
    stored: the authentication string stored now, if any.

    return: the authentication string to store.

    """
    if stored is not None and stored.startswith("bcrypt:") \
            and validate_password(stored, password):
        return stored
    return hash_password(password, "bcrypt")


def _hash_in_pool(rows: list[ImportRow],
                  work: Callable[[ImportRow], tuple[str, T]],
                  progress: Callable[[], None]) -> dict[str, T]:
    """Run the hashing work of every row, a few rows at a time.

    bcrypt costs about 0.2 s per password and releases the GIL, so a
    small pool divides the wait.

    rows: the rows to import.
    work: hashes one row; returns its username and what it computed.
    progress: called once each time a row is done.

    return: username -> what work computed for it.

    """
    result: dict[str, T] = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=HASH_THREADS,
            thread_name_prefix="aws-import-hash",
            initializer=_lower_thread_priority) as pool:
        try:
            for future in concurrent.futures.as_completed(
                    [pool.submit(work, row) for row in rows]):
                username, value = future.result()
                result[username] = value
                progress()
        except BaseException:
            # Do not hash the rows still queued for a result nobody will
            # use; only the ones already running are waited for.
            pool.shutdown(wait=True, cancel_futures=True)
            raise
    return result


def hash_passwords(rows: list[ImportRow], new_users: set[str],
                   stored_passwords: dict[str, str],
                   progress: Callable[[], None]
                   ) -> dict[str, tuple[str | None, str | None]]:
    """Hash every password of a contest import, a few at a time.

    A participation keeps its stored bcrypt password when the row's
    password still matches it (see _hash_or_keep).

    rows: the rows to import.
    new_users: the usernames that do not exist yet.
    stored_passwords: username -> participation password stored in the
        contest, from the plan.
    progress: called once each time a row is done.

    return: username -> (participation password, or None for a row
        without password; account password, or None for an existing
        user), both as authentication strings.

    """
    def work(row: ImportRow
             ) -> tuple[str, tuple[str | None, str | None]]:
        participation = None if row.password is None else _hash_or_keep(
            row.password, stored_passwords.get(row.username))
        account = hash_password(generate_random_password(), "bcrypt") \
            if row.username in new_users else None
        return row.username, (participation, account)

    return _hash_in_pool(rows, work, progress)
```

Add `from typing import TypeVar` to the imports and `T = TypeVar("T")` after the constants.

In `apply_import`, replace the line `participation.password = participation_hash` with:

```python
        # Without a password column, an existing participation keeps its
        # password and a new one has none, so the account password logs
        # in.
        if participation_hash is not None:
            participation.password = participation_hash
```

and update its docstring line for `hashes:` to "from hash_passwords; a None participation password leaves the participation's alone."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `CMS_CONFIG=... .venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkimport_test.py cmstestsuite/unit_tests/server/admin/importjobs_test.py cmstestsuite/unit_tests/server/admin/importjobs_db_test.py -q`
Expected: PASS (the `TestHashPasswords` pool, priority and cancellation tests still pass through `_hash_in_pool`).

- [ ] **Step 5: Commit**

```bash
git add cms/server/admin/bulkimport.py cmstestsuite/unit_tests/server/admin/bulkimport_test.py
git commit -m "feat(aws): register existing users in a contest without a password column

New participations then have no password of their own and log in with the
account password; existing ones keep theirs. A user that does not exist is
a row error, since a random account password could never log in.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Plan, hash and apply a global users import

**Files:**
- Modify: `cms/server/admin/bulkimport.py` (new code after `apply_import`)
- Test: `cmstestsuite/unit_tests/server/admin/bulkimport_test.py` (new classes at the end, before `if __name__`)

**Interfaces:**
- Consumes: `ImportRow`, `_hash_or_keep`, `_hash_in_pool` (Task 2).
- Produces:
  - `UserImportPlan(new_users: list[str], updated_users: list[str], stored_passwords: dict[str, str] = {})` with `summary() -> dict[str, int]` returning exactly `{"usuarios_nuevos": n, "usuarios_actualizados": m}`; `stored_passwords` out of `repr`.
  - `plan_user_import(session: Session, rows: list[ImportRow]) -> tuple[UserImportPlan, list[str]]` (errors always `[]`; the tuple matches `plan_import`).
  - `hash_account_passwords(rows: list[ImportRow], stored_passwords: dict[str, str], progress: Callable[[], None]) -> dict[str, str]`.
  - `apply_user_import(session: Session, rows: list[ImportRow], hashes: dict[str, str]) -> None`.

- [ ] **Step 1: Write the failing tests**

Extend the import line with `UserImportPlan, apply_user_import, hash_account_passwords, plan_user_import`. Append:

```python
def user_row(line: int, username: str, password: str = "pw",
             first_name: str = "Nombre") -> ImportRow:
    return ImportRow(line=line, username=username, first_name=first_name,
                     last_name="Apellido", password=password, team=None,
                     group=None)


class TestPlanUserImport(ImportFixtureMixin, unittest.TestCase):

    def test_plan(self):
        self.ana.password = "bcrypt:account-ana"
        self.session.flush()

        plan, errors = plan_user_import(self.session, [
            user_row(2, "ana"), user_row(3, "carla")])

        self.assertEqual(errors, [])
        self.assertEqual(plan.new_users, ["carla"])
        self.assertEqual(plan.updated_users, ["ana"])
        self.assertEqual(plan.stored_passwords["ana"], "bcrypt:account-ana")
        self.assertEqual(plan.summary(), {"usuarios_nuevos": 1,
                                          "usuarios_actualizados": 1})
        self.assertNotIn("bcrypt:account-ana", repr(plan))

    def test_nothing_is_added_or_flushed(self):
        pending = self.add_user(username="pending")
        flushes = []

        def record_flush(session, flush_context, instances):
            flushes.append(instances)

        event.listen(self.session, "before_flush", record_flush)
        self.addCleanup(event.remove, self.session, "before_flush",
                        record_flush)

        plan_user_import(self.session, [user_row(2, "carla")])

        self.assertEqual(flushes, [])
        self.assertEqual(list(self.session.new), [pending])


class TestHashAccountPasswords(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch("cms.server.admin.bulkimport.hash_password",
                             lambda p, method="bcrypt": "fake:" + p)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_hashes_keep_a_matching_bcrypt_hash_and_report_progress(self):
        stored = {"ana": "bcrypt:pw-ana", "beto": "bcrypt:old-beto",
                  "carla": "plaintext:pw-carla"}
        rows = [user_row(2, "ana", "pw-ana"), user_row(3, "beto", "pw-beto"),
                user_row(4, "carla", "pw-carla"),
                user_row(5, "dora", "pw-dora")]
        calls = []
        with mock.patch("cms.server.admin.bulkimport.validate_password",
                        lambda s, p: s.split(":", 1)[1] == p):
            result = hash_account_passwords(rows, stored,
                                            lambda: calls.append(None))
        self.assertEqual(result, {"ana": "bcrypt:pw-ana",
                                  "beto": "fake:pw-beto",
                                  "carla": "fake:pw-carla",
                                  "dora": "fake:pw-dora"})
        self.assertEqual(len(calls), 4)


class TestApplyUserImport(ImportFixtureMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.addCleanup(self.delete_data)
        self.ana.password = "account:ana"
        self.ana.email = "ana@example.com"
        self.ana_participation.password = "day:ana"
        self.ana_participation.team_id = self.team.id
        self.session.flush()

    def test_apply(self):
        rows = [user_row(2, "ana", first_name="Ana María"),
                user_row(3, "carla", first_name="Carla")]

        apply_user_import(self.session, rows,
                          {"ana": "fake:ana", "carla": "fake:carla"})
        self.session.commit()

        users = {u.username: u for u in self.session.query(User)}
        self.assertEqual((users["carla"].first_name, users["carla"].password),
                         ("Carla", "fake:carla"))
        self.assertEqual((users["ana"].first_name, users["ana"].password),
                         ("Ana María", "fake:ana"))
        # Nothing else about the user, and nothing of its participations.
        self.assertEqual(users["ana"].email, "ana@example.com")
        self.assertEqual(self.ana_participation.password, "day:ana")
        self.assertEqual(self.ana_participation.team_id, self.team.id)
        self.assertEqual(self.session.query(Participation).count(), 1)

    def test_nothing_is_committed(self):
        self.session.commit()
        with SessionGen() as session:
            apply_user_import(session, [user_row(2, "carla")],
                              {"carla": "fake:carla"})
        with SessionGen() as session:
            self.assertIsNone(session.execute(
                select(User).filter(User.username == "carla")
            ).scalar_one_or_none())


class TestGlobalReimportAndTheContestCookie(ImportFixtureMixin,
                                            unittest.TestCase):
    """Accounts imported again, against the cookie check of CWS.

    beto takes part in the contest with no participation password, so the
    account password is the one CWS checks and stores in the cookie.

    """

    def setUp(self):
        super().setUp()
        self.addCleanup(self.delete_data)
        patcher = mock.patch("cmscommon.crypto.BCRYPT_ROUNDS", 4)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.add_participation(user=self.beto, contest=self.contest)
        self.session.commit()
        self.timestamp = make_datetime()
        self.ip_address = ipaddress.ip_address("10.0.0.1")
        self.rows = [user_row(2, "beto", "cuenta-beto"),
                     user_row(3, "ana", "cuenta-ana")]

    def run_import(self, rows):
        with SessionGen() as session:
            plan, errors = plan_user_import(session, rows)
            self.assertEqual(errors, [])
            hashes = hash_account_passwords(rows, plan.stored_passwords,
                                            lambda: None)
            apply_user_import(session, rows, hashes)
            session.commit()

    def log_in(self, username, password):
        self.session.expire_all()
        participation, cookie = validate_login(
            self.session, self.contest, self.timestamp, username, password,
            self.ip_address)
        self.assertIsNotNone(cookie)
        return cookie

    def cookie_authenticates(self, cookie):
        self.session.expire_all()
        participation, _, _ = authenticate_request(
            self.session, self.contest, self.timestamp, cookie, None,
            self.ip_address)
        return participation is not None

    def test_the_same_accounts_again_keep_the_contestant_logged_in(self):
        self.run_import(self.rows)
        cookie = self.log_in("beto", "cuenta-beto")

        self.rows[1] = dataclasses.replace(self.rows[1], first_name="Ana M.")
        self.run_import(self.rows)

        self.assertTrue(self.cookie_authenticates(cookie))

    def test_a_changed_account_password_logs_out(self):
        self.run_import(self.rows)
        cookie = self.log_in("beto", "cuenta-beto")

        self.rows[0] = dataclasses.replace(self.rows[0],
                                           password="cuenta-beto-2")
        self.run_import(self.rows)

        self.assertFalse(self.cookie_authenticates(cookie))
        self.log_in("beto", "cuenta-beto-2")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `CMS_CONFIG=... .venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkimport_test.py -q`
Expected: FAIL — `ImportError: cannot import name 'UserImportPlan'`.

- [ ] **Step 3: Implement**

Append to `bulkimport.py`:

```python
@dataclasses.dataclass
class UserImportPlan:
    """What a global users import would do; built without writing anything.

    new_users: usernames of the users that do not exist yet.
    updated_users: usernames of the users that already exist.
    stored_passwords: username -> account password stored now (an
        authentication string), for the existing users. It is never shown
        or logged, so it is out of summary() and of the representation of
        the plan.

    """
    new_users: list[str]
    updated_users: list[str]
    stored_passwords: dict[str, str] = dataclasses.field(
        default_factory=dict, repr=False)

    def summary(self) -> dict[str, int]:
        """Count the new and the updated users."""
        return {"usuarios_nuevos": len(self.new_users),
                "usuarios_actualizados": len(self.updated_users)}


def plan_user_import(session: Session, rows: list[ImportRow]
                     ) -> tuple[UserImportPlan, list[str]]:
    """Plan a global users import against the database.

    session: a read-only use of a session; nothing is added or flushed.
    rows: the rows from read_rows with USER_FIELDS, without errors.

    return: the plan and no errors (the same shape as plan_import, for
        the callers that run either).

    """
    usernames = [row.username for row in rows]
    with session.no_autoflush:
        stored = dict(session.execute(
            select(User.username, User.password)
            .filter(User.username.in_(usernames))).all())
    return UserImportPlan(
        new_users=[u for u in usernames if u not in stored],
        updated_users=[u for u in usernames if u in stored],
        stored_passwords=stored), []


def hash_account_passwords(rows: list[ImportRow],
                           stored_passwords: dict[str, str],
                           progress: Callable[[], None]) -> dict[str, str]:
    """Hash the account password of every row, a few at a time.

    An existing user keeps its stored bcrypt hash when the row's password
    still matches it: in a contest where the participation has no password
    of its own, the CWS cookie holds that hash.

    rows: the rows to import, every one with a password.
    stored_passwords: username -> account password stored now, from the
        plan.
    progress: called once each time a row is done.

    return: username -> account password, as an authentication string.

    """
    def work(row: ImportRow) -> tuple[str, str]:
        return row.username, _hash_or_keep(
            row.password, stored_passwords.get(row.username))

    return _hash_in_pool(rows, work, progress)


def apply_user_import(session: Session, rows: list[ImportRow],
                      hashes: dict[str, str]) -> None:
    """Write a global users import into the session; the caller commits.

    The users are read again here, so a user that another import created
    after the plan is updated instead of inserted twice.

    session: the session of the transaction.
    rows: the rows to import.
    hashes: from hash_account_passwords.

    """
    usernames = [row.username for row in rows]
    users = {u.username: u for u in session.execute(
        select(User).filter(User.username.in_(usernames))).scalars()}
    for row in rows:
        user = users.get(row.username)
        if user is None:
            session.add(User(username=row.username,
                             first_name=row.first_name,
                             last_name=row.last_name,
                             password=hashes[row.username]))
        else:
            user.first_name = row.first_name
            user.last_name = row.last_name
            user.password = hashes[row.username]
```

`hash_account_passwords` receives rows that always carry a password (the global fields require it); `_hash_or_keep` takes `str`, so add `assert row.password is not None` as the first line of `work` to keep the type checker and a future caller honest.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `CMS_CONFIG=... .venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkimport_test.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cms/server/admin/bulkimport.py cmstestsuite/unit_tests/server/admin/bulkimport_test.py
git commit -m "feat(aws): plan, hash and apply a global import of user accounts

The accounts get the row's password as their account password, keeping a
stored bcrypt hash that still matches so a contestant logged in with it
stays logged in.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Jobs for the global import

**Files:**
- Modify: `cms/server/admin/importjobs.py`
- Test: `cmstestsuite/unit_tests/server/admin/importjobs_test.py`, `cmstestsuite/unit_tests/server/admin/importjobs_db_test.py`

**Interfaces:**
- Consumes: `plan_user_import`, `hash_account_passwords`, `apply_user_import` (Task 3).
- Produces:
  - `ImportJob.contest_id: int | None` — `None` is a global users import.
  - `ImportJobStore.start(owner_id: int, contest_id: int | None, rows, on_done) -> ImportJob` — raises `ValueError("ya hay una importación de usuarios en curso")` when a global job runs (contest message unchanged).
  - `ImportJobStore.running_job(contest_id: int | None) -> ImportJob | None`.
  - Log lines: contest jobs exactly as today; global jobs say `global users` instead of `into contest N`.

- [ ] **Step 1: Write the failing tests**

In `importjobs_test.py`, patch the global functions too (in `ImportJobsTestCase.setUp`):

```python
        for name in ("SessionGen", "plan_import", "hash_passwords",
                     "apply_import", "plan_user_import",
                     "hash_account_passwords", "apply_user_import"):
```

and after the existing return values:

```python
        self.user_plan = UserImportPlan(
            new_users=["user0"], updated_users=["user1", "user2"],
            stored_passwords={"user2": STORED_PASSWORD})
        self.plan_user_import.return_value = (self.user_plan, [])
        self.hash_account_passwords.side_effect = self.hash_every_account
        self.apply_user_import.return_value = None
```

with

```python
    @staticmethod
    def hash_every_account(rows, stored_passwords, progress):
        for _ in rows:
            progress()
        return ACCOUNT_HASHES
```

and the constant `ACCOUNT_HASHES = {"user0": "account-hash"}`; import `UserImportPlan` from `cms.server.admin.bulkimport`. Add:

```python
class TestGlobalJobs(ImportJobsTestCase):

    def test_a_global_job_runs_the_users_import(self):
        store = ImportJobStore()

        job = store.start(OWNER_ID, None, self.rows, self.on_done)
        self.wait_for_job(job)

        self.assertEqual((job.status, job.processed), ("done", 3))
        self.assertEqual(job.summary, {"usuarios_nuevos": 1,
                                       "usuarios_actualizados": 2})
        self.plan_user_import.assert_called_once_with(self.session,
                                                      self.rows)
        self.hash_account_passwords.assert_called_once_with(
            self.rows, {"user2": STORED_PASSWORD}, mock.ANY)
        self.apply_user_import.assert_called_once_with(
            self.session, self.rows, ACCOUNT_HASHES)
        self.session.commit.assert_called_once_with()
        self.on_done.assert_called_once_with()
        for contest_step in (self.plan_import, self.hash_passwords,
                             self.apply_import):
            contest_step.assert_not_called()

    def test_one_global_job_at_a_time_besides_the_contest_ones(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def blocking_hash(rows, stored_passwords, progress):
            started.set()
            release.wait(5)
            return ACCOUNT_HASHES
        self.hash_account_passwords.side_effect = blocking_hash
        store = ImportJobStore()

        job = store.start(OWNER_ID, None, self.rows, self.on_done)
        self.assertTrue(started.wait(5))
        with self.assertRaises(ValueError) as raised:
            store.start(OWNER_ID + 1, None, self.rows, self.on_done)
        self.assertEqual(str(raised.exception),
                         "ya hay una importación de usuarios en curso")
        self.assertIs(store.running_job(None), job)
        self.assertIsNone(store.running_job(CONTEST_ID))
        contest_job = store.start(OWNER_ID, CONTEST_ID, self.rows,
                                  self.on_done)

        release.set()
        self.wait_for_job(job)
        self.wait_for_job(contest_job)
        self.assertEqual((job.status, contest_job.status), ("done", "done"))

    def test_a_global_failure_logs_no_row_data(self):
        def failing_apply(*args):
            raise RuntimeError(LEAK_MESSAGE)
        self.apply_user_import.side_effect = failing_apply
        store = ImportJobStore()

        with self.assertLogs(MODULE, level="ERROR") as logs:
            job = store.start(OWNER_ID, None, self.rows, self.on_done)
            self.wait_for_job(job)

        self.assertEqual((job.status, job.error), ("error", APPLY_FAILED))
        output = "\n".join(logs.output)
        self.assertIn("global users", output)
        self.assertIn("RuntimeError", output)
        self.assertNotIn(LEAK_USERNAME, output)
        self.assertNotIn(LEAK_PASSWORD, output)
        self.assertIsNone(logs.records[0].exc_info)
```

In `importjobs_db_test.py`, import `USER_FIELDS` is not needed; add to `TestImportJobAgainstTheDatabase`:

```python
    def test_a_global_job_imports_the_accounts(self):
        rows = [dataclasses.replace(row, team=None) for row in self.rows]
        job = ImportJobStore().start(OWNER_ID, None, rows, self.on_done)
        self.wait(job)

        self.assertEqual((job.status, job.error), ("done", None))
        self.assertEqual(job.summary, {"usuarios_nuevos": 1,
                                       "usuarios_actualizados": 1})
        # The page reads these keys of the summary, among others.
        with open(PAGE, encoding="utf-8") as page:
            read_by_the_page = set(re.findall(r"s\.summary\.(\w+)",
                                              page.read()))
        self.assertLessEqual(set(job.summary), read_by_the_page)
        self.assertNotIn(NEW_PASSWORD, json.dumps(job.as_json()))
        with SessionGen() as session:
            users = {u.username: u for u in session.execute(
                select(User)).scalars()}
            self.assertTrue(validate_password(
                users[NEW_USERNAME].password, NEW_PASSWORD))
            self.assertTrue(validate_password(
                users[EXISTING_USERNAME].password, EXISTING_PASSWORD))
            # The participation and its day password are left alone.
            participation = session.execute(
                select(Participation).join(Participation.user)
                .filter(User.username == EXISTING_USERNAME)).scalar_one()
            self.assertTrue(validate_password(participation.password,
                                              "old-ana"))
            self.assertEqual(session.query(Participation).count(), 1)
```

Refactor `run_job` so both tests share the wait (and add `import dataclasses`):

```python
    def run_job(self):
        """Start a job for the rows and wait, at most 30 s, for its end."""
        job = ImportJobStore().start(OWNER_ID, self.contest.id, self.rows,
                                     self.on_done)
        self.wait(job)
        return job

    def wait(self, job) -> None:
        """Wait, at most 30 s, until the thread of the job is over."""
        name = "aws-import-%s" % job.id[:8]
        deadline = time.monotonic() + 30
        while any(t.name == name for t in threading.enumerate()):
            if time.monotonic() > deadline:
                self.fail("the thread of the import job did not finish")
            time.sleep(0.02)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `CMS_CONFIG=... .venv/bin/pytest cmstestsuite/unit_tests/server/admin/importjobs_test.py cmstestsuite/unit_tests/server/admin/importjobs_db_test.py -q`
Expected: FAIL — `AttributeError: ... does not have the attribute 'plan_user_import'`.

- [ ] **Step 3: Implement**

In `importjobs.py`:

```python
from cms.server.admin.bulkimport import ImportRow, apply_import, \
    apply_user_import, hash_account_passwords, hash_passwords, \
    plan_import, plan_user_import
```

Docstring of `ImportJob`: `contest_id: the contest the import goes to, or None for a global users import.` and type `contest_id: int | None`. Add a helper after `_APPLY_LOCK`:

```python
def _describe(job: "ImportJob") -> str:
    """Say what a job imports, for the log lines."""
    if job.contest_id is None:
        return "of global users"
    return "into contest %d" % job.contest_id
```

In `start` (signature `contest_id: int | None`, docstring "contest_id: the contest, or None for a global users import."):

```python
            if self._find_running(contest_id) is not None:
                raise ValueError(
                    "ya hay una importación de usuarios en curso"
                    if contest_id is None
                    else "ya hay una importación en curso para este concurso")
```

`_find_running` and `running_job` take `contest_id: int | None` (docstrings: "the contest, or None for the global users import"). In `_run`:

```python
        global_import = job.contest_id is None
        try:
            with SessionGen() as session:
                if global_import:
                    plan, errors = plan_user_import(session, rows)
                else:
                    plan, errors = plan_import(session, job.contest_id, rows)
                if plan is None:
                    job.error = "; ".join(errors)
                    job.status = "error"
                    return
                # The plan holds only plain values and the apply steps read
                # again what they write, so no transaction stays open,
                # idle, during the hashing.
                session.rollback()

                def progress() -> None:
                    job.processed += 1

                if global_import:
                    hashes = hash_account_passwords(
                        rows, plan.stored_passwords, progress)
                else:
                    hashes = hash_passwords(rows, set(plan.new_users),
                                            plan.stored_passwords, progress)
                with _APPLY_LOCK:
                    if global_import:
                        apply_user_import(session, rows, hashes)
                    else:
                        apply_import(session, job.contest_id, rows, plan,
                                     hashes)
                    session.commit()
            job.summary = plan.summary()
            job.status = "done"
            logger.info("Bulk import %s by admin %d: %s.",
                        _describe(job), job.owner_id, job.summary)
        except Exception as exc:
            logger.error("Bulk import %s failed with %s.\n%s",
                         _describe(job), type(exc).__name__,
                         "".join(traceback.format_tb(exc.__traceback__)))
            job.error = APPLY_FAILED
            job.status = "error"
            return
        try:
            on_done()
        except Exception as exc:
            logger.warning("Bulk import %s was saved, but ProxyService was "
                           "not notified (%s).",
                           _describe(job), type(exc).__name__)
```

Update the `_run` docstring: "A failure is logged with what the job imports (the contest or the global users), the class of the exception ...".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `CMS_CONFIG=... .venv/bin/pytest cmstestsuite/unit_tests/server/admin/importjobs_test.py cmstestsuite/unit_tests/server/admin/importjobs_db_test.py -q`
Expected: PASS (the contest log tests still find `str(CONTEST_ID)` in "into contest 7").

- [ ] **Step 5: Commit**

```bash
git add cms/server/admin/importjobs.py cmstestsuite/unit_tests/server/admin/importjobs_test.py cmstestsuite/unit_tests/server/admin/importjobs_db_test.py
git commit -m "feat(aws): run global users imports as jobs

A job with no contest runs the users import; one runs at a time, besides
the contest ones, and every write still goes through the same lock.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Shared import page (contest side) and the password-header leak

**Files:**
- Modify: `cms/server/admin/handlers/contestuser.py:349-488` (`ImportUsersHandler`, `ImportJobStatusHandler`)
- Modify: `cms/server/admin/templates/contest_users_import.html`
- Test: `cmstestsuite/unit_tests/server/admin/contest_users_import_test.py`

**Interfaces:**
- Consumes: `FIELDS`, `REQUIRED`, `read_rows(..., fields, required)` (Task 1).
- Produces:
  - `ImportPageHandler(BaseHandler)` in `contestuser.py`, with class attributes `fields`, `required`, `running_notice`, `repeated_notice`; hooks `_plan(rows)` and `_import_url()`; shared `_render_page(mapping=None, errors=None, summary=None, job=None, notice=None)`, `_show_page()`, `_upload()`; attribute `self.contest` (`None` for the global page).
  - `_write_job_status(handler: BaseHandler, job: ImportJob) -> None` module helper.
  - Template parameters: `contest` (a contest or `None`), `fields`, `required`, `mapping`, `errors`, `summary`, `job`, `notice`.

- [ ] **Step 1: Write the failing tests**

In `contest_users_import_test.py`: import `REQUIRED` from `cms.server.admin.bulkimport`; in `TestImportTemplates.render_import` add `params.setdefault("required", REQUIRED)`. Change the expected password label in `test_every_field_has_a_label_in_spanish` to `"map_password": "Contraseña del día (password)"` (the field is optional now). Add:

```python
class TestTheContestPasswordIsOptional(unittest.TestCase):

    def test_the_handler_passes_the_required_fields(self):
        handler = make_handler(form=import_form("validate"))
        with mock.patch(MODULE + ".plan_import",
                        return_value=(make_plan(), [])):
            handler._post_sync(str(CONTEST_ID))

        self.assertEqual(rendered_params(handler)["required"], REQUIRED)

    def test_without_a_password_column_the_rows_carry_none(self):
        handler = make_handler(form=import_form(
            "validate", {**MAPPING, "password": ""}))
        with mock.patch(MODULE + ".plan_import",
                        return_value=(make_plan(), [])) as plan_import:
            handler._post_sync(str(CONTEST_ID))

        rows = plan_import.call_args.args[2]
        self.assertEqual([row.password for row in rows], [None])
        self.assertEqual(rendered_params(handler)["errors"], [])

    def test_the_page_explains_an_unassigned_password(self):
        html = TestImportTemplates().render_import()

        self.assertIn("las participaciones nuevas entran con la contraseña "
                      "de su cuenta", html)
        self.assertNotIn("(password) *", html)
```

Add to `TestPageRendersWhatTheHandlerPasses`:

```python
    def test_a_field_on_the_password_header_does_not_echo_it(self):
        # A file without a header row, with the team mapped to the
        # password's "header": read_rows refuses it, and the page shown
        # again must not carry the header in the team's select either.
        headerless = ("ana,Ana,Pérez,%s\nbob,Bob,Ruiz,pw2\n"
                      % PASSWORD).encode("utf-8")
        mapping = {"username": "ana", "first_name": "Ana",
                   "last_name": "Pérez", "password": PASSWORD,
                   "team": PASSWORD}
        handler = make_handler(data=headerless,
                               form=import_form("validate", mapping))
        handler._post_sync(str(CONTEST_ID))

        html = self.render_last_page(handler)

        self.assertIn("la columna de la contraseña está asignada a más de "
                      "un campo", html)
        self.assertIn('data-selected="ana"', html)
        self.assertNotIn(PASSWORD, html)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `CMS_CONFIG=... .venv/bin/pytest cmstestsuite/unit_tests/server/admin/contest_users_import_test.py -q`
Expected: FAIL — `KeyError: 'required'`, the leak test finds `PASSWORD` in the team's `data-selected`, the label still has ` *`.

- [ ] **Step 3: Implement the handler base**

In `contestuser.py`, import `REQUIRED` too (`from cms.server.admin.bulkimport import FIELDS, REQUIRED, plan_import, read_rows`), and replace `ImportUsersHandler` and `ImportJobStatusHandler` with:

```python
def _shown_mapping(mapping: dict[str, str]) -> dict[str, str]:
    """Return the mapping to show again, without the password's header.

    In a file without a header row, the header chosen for the password is
    a real password; the page never echoes it, not even as the column of
    another field.

    mapping: import field -> header the admin chose for it.

    """
    password_name = (mapping.get("password") or "").strip()
    return {field: "" if field != "password" and password_name
            and header.strip() == password_name else header
            for field, header in mapping.items()}


class ImportPageHandler(BaseHandler):
    """The page of a bulk import of users from a CSV.

    GET shows the form, or the progress of the job given by ?job= (with
    &repetido=1, also a notice that the file just sent was not imported).
    POST reads the file: "validate" only reports what an import would do,
    "import" starts the job and redirects to its progress page.

    A subclass sets self.contest (None for the global users import) and
    says what is imported: the fields, the required ones, the plan and the
    URL of the page.

    """
    fields: tuple[str, ...] = FIELDS
    required: frozenset[str] = REQUIRED
    # Said when an import of the same kind is running; the file is fine,
    # so it is not one of the errors that say that nothing was applied.
    running_notice = ("Hay una importación en curso para este concurso; "
                      "espera a que termine.")
    # Said with the progress of the admin's own running import, when a
    # second file was sent while it ran: that file was not imported.
    repeated_notice = ("Ya tenías una importación en curso en este "
                       "concurso; este es su progreso. El archivo que "
                       "acabas de enviar no se importó.")
    contest: Contest | None

    def _plan(self, rows: list) -> tuple[object | None, list[str]]:
        """Plan the import of the rows; see plan_import."""
        raise NotImplementedError

    def _import_url(self) -> str:
        """Return the URL of the page."""
        raise NotImplementedError

    def _contest_id(self) -> int | None:
        return None if self.contest is None else self.contest.id

    def _render_page(self, mapping: dict[str, str] | None = None,
                     errors: list[str] | None = None,
                     summary: dict[str, int] | None = None,
                     job: ImportJob | None = None,
                     notice: str | None = None) -> None:
        """Render the page.

        The template gets every one of these parameters, as AWS renders
        with StrictUndefined.

        mapping: import field -> header the admin chose for it, so that
            the choice survives the page being shown again.
        errors: why nothing was imported, to list them.
        summary: what an import of the file would do, after "validate".
        job: the import job whose progress to show, instead of the form.
        notice: something to tell the admin that is not an error, as
            errors say that nothing was applied.

        """
        self.r_params = self.render_params()
        self.r_params["contest"] = self.contest
        self.r_params["fields"] = self.fields
        self.r_params["required"] = self.required
        self.r_params["mapping"] = _shown_mapping(mapping or {})
        self.r_params["errors"] = errors or []
        self.r_params["summary"] = summary
        self.r_params["job"] = job
        self.r_params["notice"] = notice
        self.render("contest_users_import.html", **self.r_params)

    def _job_url(self, job: ImportJob) -> str:
        """Return the URL of the page that shows the progress of a job."""
        return self._import_url() + "?job=" + job.id

    def _show_page(self) -> None:
        """Show the form, or the progress of the job of ?job=."""
        job_id = self.get_argument("job", None)
        if job_id is None:
            self._render_page()
            return
        job = IMPORT_JOBS.get(job_id, self.current_user.id)
        if job is None or job.contest_id != self._contest_id():
            # A notice, not an error: the job may have been saved before it
            # expired or the AWS restarted, so "nothing was applied" would
            # be false.
            self._render_page(notice=(
                "No se encontró la importación: no existe o ya expiró. "
                "Revisa la lista de usuarios para ver si se aplicó."))
            return
        # A second file was sent while this import ran and was refused: the
        # admin must not take the progress for the one of that file.
        repeated = bool(self.get_argument("repetido", None))
        self._render_page(job=job, notice=(
            self.repeated_notice if repeated else None))

    def _upload(self) -> None:
        """Validate the file sent, and start its import if asked to."""
        mapping = {field: self.get_argument("map_" + field, "")
                   for field in self.fields}
        files = self.request.files.get("file")
        if not files:
            self._render_page(mapping, errors=["elige un archivo CSV"])
            return
        rows, errors = read_rows(files[0]["body"], mapping, self.fields,
                                 self.required)
        plan = None
        if not errors:
            plan, errors = self._plan(rows)
        if errors:
            self._render_page(mapping, errors=errors)
            return
        if self.get_argument("action", "validate") != "import":
            self._render_page(mapping, summary=plan.summary())
            return
        service = self.service

        def on_done() -> None:
            # Called from the job's thread, once the import is saved.
            self.schedule_rpc(service.proxy_service.reinitialize)

        try:
            job = IMPORT_JOBS.start(self.current_user.id, self._contest_id(),
                                    rows, on_done)
        except ValueError:
            # An import of the same kind is running. If it is this admin's,
            # follow it instead of losing its progress page. The file just
            # sent may not be the one that is running, so the page is told
            # that it was not imported. Otherwise there is nothing wrong
            # with the file: the admin only has to wait.
            running = IMPORT_JOBS.running_job(self._contest_id())
            if running is not None \
                    and running.owner_id == self.current_user.id:
                self.redirect(self._job_url(running) + "&repetido=1")
                return
            self._render_page(mapping, notice=self.running_notice)
            return
        self.redirect(self._job_url(job))


class ImportUsersHandler(ImportPageHandler):
    """Bulk import of users and participations into a contest (CSV)."""

    def _plan(self, rows: list) -> tuple[object | None, list[str]]:
        return plan_import(self.sql_session, self.contest.id, rows)

    def _import_url(self) -> str:
        return self.url("contest", self.contest.id, "users", "import")

    def _get_sync(self, contest_id: str) -> None:
        self.contest = self.safe_get_item(Contest, contest_id)
        self._show_page()

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self, contest_id: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, contest_id)

    def _post_sync(self, contest_id: str) -> None:
        self.contest = self.safe_get_item(Contest, contest_id)
        self._upload()

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self, contest_id: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync, contest_id)


def _write_job_status(handler: BaseHandler, job: ImportJob) -> None:
    """Write the progress of a job as JSON, never cached."""
    handler.set_header("Content-Type", "application/json")
    handler.set_header("Cache-Control", "no-store")
    handler.write(json.dumps(job.as_json()))


class ImportJobStatusHandler(BaseHandler):
    """The progress of a contest import job, as JSON, for its owner only."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self, contest_id: str, job_id: str) -> None:
        job = IMPORT_JOBS.get(job_id, self.current_user.id)
        if job is None or str(job.contest_id) != contest_id:
            raise tornado.web.HTTPError(404)
        _write_job_status(self, job)
```

Keep the existing tests' expectations: `test_validate_shows_the_summary_and_starts_nothing` still checks `plan_import(handler.sql_session, CONTEST_ID, rows)` and `params["fields"] == FIELDS`.

- [ ] **Step 4: Implement the template**

In `contest_users_import.html`:

1. Title and introduction (replace lines 30-37):

```html
<div class="core_title">
  <h1>Importar CSV &mdash; {{ contest.name if contest else "Usuarios" }}</h1>
</div>

{% if contest %}
<p>Un renglón por concursante: crea los usuarios que falten y registra su
participación en este concurso con la contraseña del día. Sin columna de
contraseña, solo inscribe usuarios que ya existen y las participaciones
nuevas entran con la contraseña de su cuenta. Si el usuario o la
participación ya existen, se actualizan. Si hay cualquier error, no se
guarda nada.</p>
{% else %}
<p>Un renglón por usuario: crea las cuentas que falten con su contraseña y
actualiza el nombre, el apellido y la contraseña de las que ya existen. No
registra a nadie en ningún concurso. Si hay cualquier error, no se guarda
nada.</p>
<p>La contraseña de la cuenta solo se usa en los concursos donde la
participación no tiene contraseña propia.</p>
{% endif %}
{% set import_url = url("contest", contest.id, "users", "import") if contest else url("users", "import") %}
{% set users_url = url("contest", contest.id, "users") if contest else url("users") %}
```

2. Summary (replace lines 61-64): keep the two user lines; wrap the two participation lines in `{% if summary.participaciones_nuevas is defined %} ... {% endif %}`.

3. Job block (lines 76-78):

```html
<div id="import_job" data-status-url="{{ url("contest", contest.id, "users", "import", job.id, "status") if contest else url("users", "import", job.id, "status") }}"
     data-users-url="{{ users_url }}"
     data-import-url="{{ import_url }}">
```

4. Form action (line 85): `action="{{ import_url }}">`.

5. Labels and marks (lines 95-101):

```html
  {% set labels = {"username": "Usuario (username)", "first_name": "Nombre (first_name)", "last_name": "Apellidos (last_name)", "password": "Contraseña del día (password)" if contest else "Contraseña (password)", "team": "Equipo (team)", "group": "Grupo (group)"} %}
  <table{% if mapping %} data-has-mapping="1"{% endif %}>
    {% for field in fields %}
    <tr>
      <td><label for="map_{{ field }}">{{ labels[field] }}{% if field in required %} *{% endif %}</label></td>
      <td><select name="map_{{ field }}" data-field="{{ field }}" data-selected="{% if field != "password" %}{{ (mapping or {}).get(field, "") }}{% endif %}" id="map_{{ field }}"><option value="">(sin asignar)</option></select></td>
      {% if field == "password" and contest %}<td class="import-note">Sin asignar: las participaciones nuevas entran con la contraseña de su cuenta y las existentes conservan la suya. Los usuarios deben existir.</td>{% endif %}
      {% if field == "team" %}<td rowspan="2" class="import-note">Vacío o sin asignar: la participación se queda sin equipo / en el grupo principal.</td>{% endif %}
    </tr>
    {% endfor %}
  </table>
```

6. Script, the "done" line (lines 250-254):

```javascript
                    var counts = " Usuarios nuevos: " + s.summary.usuarios_nuevos +
                        ", actualizados: " + s.summary.usuarios_actualizados + ".";
                    // The global import of users has no participations.
                    if (s.summary.participaciones_nuevas !== undefined) {
                        counts += " Participaciones nuevas: " + s.summary.participaciones_nuevas +
                            ", actualizadas: " + s.summary.participaciones_actualizadas + ".";
                    }
                    lead.appendChild(document.createTextNode(counts));
```

(the `equipos_quitados` / `movidas_al_grupo_principal` checks already compare `> 0`, which is false for `undefined`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `CMS_CONFIG=... .venv/bin/pytest cmstestsuite/unit_tests/server/admin/ -q`
Expected: PASS (the whole admin directory: the contest page, its template tests, the jobs and the DB test that compares the summary keys with `s.summary.*`).

- [ ] **Step 6: Commit**

```bash
git add cms/server/admin/handlers/contestuser.py cms/server/admin/templates/contest_users_import.html cmstestsuite/unit_tests/server/admin/contest_users_import_test.py
git commit -m "refactor(aws): share the import page and keep the password header off it

The contest import page becomes a base handler and a template that the
global users import can reuse, with the password optional for a contest.
A field mapped to the header chosen for the password no longer echoes it
in its select when the page is shown again.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The global "Importar CSV" page on Users

**Files:**
- Modify: `cms/server/admin/handlers/contestuser.py` (new classes after `ImportJobStatusHandler`)
- Modify: `cms/server/admin/handlers/__init__.py` (imports at lines 56-63; routes in the "Users/Teams" block at lines 226-236)
- Modify: `cms/server/admin/templates/users.html` (the link)
- Test: `cmstestsuite/unit_tests/server/admin/contest_users_import_test.py`

**Interfaces:**
- Consumes: `ImportPageHandler`, `_write_job_status` (Task 5); `USER_FIELDS`, `USER_REQUIRED`, `plan_user_import` (Tasks 1, 3); `IMPORT_JOBS.start(owner, None, ...)` (Task 4).
- Produces: `ImportGlobalUsersHandler` at `/users/import`, `GlobalImportJobStatusHandler` at `/users/import/([A-Za-z0-9_-]+)/status`.

- [ ] **Step 1: Write the failing tests**

Import the new names in `contest_users_import_test.py`:

```python
from cms.server.admin.bulkimport import FIELDS, MAX_BYTES, REQUIRED, \
    USER_FIELDS, USER_REQUIRED, ImportPlan, UserImportPlan, read_rows
from cms.server.admin.handlers.contestuser import \
    GlobalImportJobStatusHandler, ImportGlobalUsersHandler, \
    ImportJobStatusHandler, ImportUsersHandler
```

Add:

```python
GLOBAL_RUNNING_NOTICE = ("Hay una importación de usuarios en curso; espera "
                         "a que termine.")
GLOBAL_REPEATED_NOTICE = ("Ya tenías una importación de usuarios en curso; "
                          "este es su progreso. El archivo que acabas de "
                          "enviar no se importó.")


def make_user_plan() -> UserImportPlan:
    return UserImportPlan(new_users=["ana"], updated_users=[])


class TestGlobalImportPage(unittest.TestCase):

    def render(self, handler) -> str:
        template = AWS_ENVIRONMENT.get_template("contest_users_import.html")
        return "".join(template.blocks["core"](
            template.new_context(dict(rendered_params(handler)))))

    def test_validate_shows_the_summary_of_users_only(self):
        handler = make_handler(ImportGlobalUsersHandler,
                               form=import_form("validate"))
        with mock.patch(MODULE + ".plan_user_import",
                        return_value=(make_user_plan(), [])) as plan, \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            handler._post_sync()

        params = rendered_params(handler)
        self.assertIsNone(params["contest"])
        self.assertEqual(params["fields"], USER_FIELDS)
        self.assertEqual(params["required"], USER_REQUIRED)
        plan.assert_called_once_with(
            handler.sql_session,
            read_rows(CSV, MAPPING, USER_FIELDS, USER_REQUIRED)[0])
        jobs.start.assert_not_called()
        html = self.render(handler)
        self.assertIn("Usuarios nuevos: 1", html)
        # The script of the page names them, so look for the list item.
        self.assertNotIn("<li>Participaciones nuevas", html)
        self.assertIn('action="/users/import"', html)
        self.assertIn("Contraseña (password) *", html)
        self.assertNotIn("Contraseña del día", html)
        self.assertNotIn('name="map_team"', html)
        self.assertIn("La contraseña de la cuenta solo se usa en los "
                      "concursos donde la participación no tiene "
                      "contraseña propia.", html)
        self.assertNotIn(PASSWORD, html)

    def test_the_password_is_required(self):
        handler = make_handler(ImportGlobalUsersHandler, form=import_form(
            "validate", {**MAPPING, "password": ""}))
        with mock.patch(MODULE + ".plan_user_import") as plan:
            handler._post_sync()

        self.assertEqual(rendered_params(handler)["errors"], [
            "falta asignar la columna para la contraseña (password)"])
        plan.assert_not_called()

    def test_import_starts_a_global_job(self):
        handler = make_handler(ImportGlobalUsersHandler,
                               form=import_form("import"))
        with mock.patch(MODULE + ".plan_user_import",
                        return_value=(make_user_plan(), [])), \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.start.return_value = SimpleNamespace(id="job-1")
            handler._post_sync()

        jobs.start.assert_called_once_with(
            ADMIN_ID, None,
            read_rows(CSV, MAPPING, USER_FIELDS, USER_REQUIRED)[0],
            mock.ANY)
        handler.redirect.assert_called_once_with("/users/import?job=job-1")
        on_done = jobs.start.call_args.args[3]
        on_done()
        handler.schedule_rpc.assert_called_once_with(
            handler.application.service.proxy_service.reinitialize)

    def test_a_running_global_import(self):
        for owner, redirected in ((ADMIN_ID, True), (ADMIN_ID + 1, False)):
            handler = make_handler(ImportGlobalUsersHandler,
                                   form=import_form("import"))
            with mock.patch(MODULE + ".plan_user_import",
                            return_value=(make_user_plan(), [])), \
                    mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
                jobs.start.side_effect = ValueError("running")
                jobs.running_job.return_value = SimpleNamespace(
                    id="job-1", owner_id=owner)
                handler._post_sync()

            jobs.running_job.assert_called_once_with(None)
            if redirected:
                handler.redirect.assert_called_once_with(
                    "/users/import?job=job-1&repetido=1")
            else:
                self.assertIn(GLOBAL_RUNNING_NOTICE, self.render(handler))

    def test_the_progress_of_a_global_job_and_its_urls(self):
        handler = make_handler(ImportGlobalUsersHandler,
                               form={"job": "job-1", "repetido": "1"})
        job = SimpleNamespace(id="job-1", contest_id=None, processed=2,
                              total=4)
        with mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.get.return_value = job
            handler._get_sync()

        html = self.render(handler)
        self.assertIn("Procesando 2 de 4", html)
        self.assertIn(GLOBAL_REPEATED_NOTICE, html)
        self.assertIn('data-status-url="/users/import/job-1/status"', html)
        self.assertIn('data-users-url="/users"', html)
        self.assertIn('data-import-url="/users/import"', html)

    def test_a_contest_job_is_not_shown_on_the_global_page(self):
        handler = make_handler(ImportGlobalUsersHandler, form={"job": "job-1"})
        job = SimpleNamespace(id="job-1", contest_id=CONTEST_ID,
                              processed=2, total=4)
        with mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.get.return_value = job
            handler._get_sync()

        html = self.render(handler)
        self.assertIn(NOT_FOUND, html)
        self.assertNotIn("<progress", html)

    def test_a_header_less_file_never_echoes_the_password(self):
        headerless = ("ana,Ana,Pérez,%s\nbob,Bob,Ruiz,pw2\n"
                      % PASSWORD).encode("utf-8")
        for mapping in (
                {"username": "ana", "first_name": "Ana",
                 "last_name": "Pérez", "password": PASSWORD},
                {"username": PASSWORD, "first_name": "Ana",
                 "last_name": "Pérez", "password": PASSWORD}):
            handler = make_handler(ImportGlobalUsersHandler,
                                   data=headerless,
                                   form=import_form("validate", mapping))
            with mock.patch(MODULE + ".plan_user_import",
                            return_value=(make_user_plan(), [])):
                handler._post_sync()

            self.assertNotIn(PASSWORD, self.render(handler), msg=mapping)


class TestGlobalImportJobStatus(unittest.TestCase):

    def setUp(self):
        self.store = ImportJobStore()
        patcher = mock.patch(MODULE + ".IMPORT_JOBS", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start_job(self, contest_id: int | None) -> ImportJob:
        rows = read_rows(CSV, MAPPING, USER_FIELDS, USER_REQUIRED)[0]
        with mock.patch.object(ImportJobStore, "_run"):
            return self.store.start(ADMIN_ID, contest_id, rows,
                                    lambda: None)

    def test_the_owner_gets_the_json_of_a_global_job(self):
        job = self.start_job(None)
        handler = make_handler(GlobalImportJobStatusHandler)

        asyncio.run(handler.get(job.id))

        body = handler.write.call_args.args[0]
        self.assertEqual(json.loads(body), job.as_json())
        self.assertNotIn(PASSWORD, body)
        handler.set_header.assert_any_call("Cache-Control", "no-store")

    def test_jobs_of_the_other_kind_or_owner_are_404(self):
        global_job = self.start_job(None)
        contest_job = self.start_job(CONTEST_ID)
        cases = (
            (GlobalImportJobStatusHandler, (contest_job.id,), ADMIN_ID),
            (ImportJobStatusHandler, (str(CONTEST_ID), global_job.id),
             ADMIN_ID),
            (GlobalImportJobStatusHandler, (global_job.id,), ADMIN_ID + 1))
        for handler_class, args, admin_id in cases:
            handler = make_handler(handler_class, admin_id=admin_id)
            with self.subTest(handler=handler_class.__name__, args=args):
                with self.assertRaises(tornado.web.HTTPError) as caught:
                    asyncio.run(handler.get(*args))
                self.assertEqual(caught.exception.status_code, 404)
                handler.write.assert_not_called()


class TestGlobalImportRoutesAndPermissions(unittest.TestCase):

    def route_of(self, handler_class) -> str:
        (pattern,) = [route[0] for route in HANDLERS
                      if route[1] is handler_class]
        return pattern

    def test_the_routes(self):
        self.assertRegex("/users/import",
                         "^%s$" % self.route_of(ImportGlobalUsersHandler))
        pattern = self.route_of(GlobalImportJobStatusHandler)
        for _ in range(200):
            job_id = secrets.token_urlsafe(16)
            url = Url("")("users", "import", job_id, "status")
            match = re.fullmatch(pattern, url)
            self.assertIsNotNone(match, msg=url)
            self.assertEqual(match.groups(), (job_id,))

    def test_everything_needs_all_permissions(self):
        for handler_class, method, args in (
                (ImportGlobalUsersHandler, "get", ()),
                (ImportGlobalUsersHandler, "post", ()),
                (GlobalImportJobStatusHandler, "get", ("job-1",))):
            handler = make_handler(handler_class, permission_all=False)
            handler._get_sync = mock.MagicMock()
            handler._post_sync = mock.MagicMock()
            with self.subTest(handler=handler_class.__name__, method=method):
                # As in TestPermissions: the check raises on the call.
                with self.assertRaises(tornado.web.HTTPError) as caught:
                    getattr(handler, method)(*args)
                self.assertEqual(caught.exception.status_code, 403)
                handler._get_sync.assert_not_called()
                handler._post_sync.assert_not_called()

    def test_the_users_list_links_to_the_import_for_full_admins_only(self):
        for permission_all in (True, False):
            html = TestImportTemplates().render_core(
                "users.html", user_list=[],
                admin=SimpleNamespace(permission_all=permission_all))
            self.assertEqual('<a href="/users/import">Importar CSV</a>'
                             in html, permission_all)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `CMS_CONFIG=... .venv/bin/pytest cmstestsuite/unit_tests/server/admin/contest_users_import_test.py -q`
Expected: FAIL — `ImportError: cannot import name 'GlobalImportJobStatusHandler'`.

- [ ] **Step 3: Implement**

In `contestuser.py`, import `USER_FIELDS, USER_REQUIRED, plan_user_import` too, and add after `ImportJobStatusHandler`:

```python
class ImportGlobalUsersHandler(ImportPageHandler):
    """Bulk import of user accounts, with their password (CSV).

    It registers nobody in any contest; see ImportUsersHandler for that.

    """
    fields = USER_FIELDS
    required = USER_REQUIRED
    running_notice = ("Hay una importación de usuarios en curso; espera a "
                      "que termine.")
    repeated_notice = ("Ya tenías una importación de usuarios en curso; "
                       "este es su progreso. El archivo que acabas de "
                       "enviar no se importó.")

    def _plan(self, rows: list) -> tuple[object | None, list[str]]:
        return plan_user_import(self.sql_session, rows)

    def _import_url(self) -> str:
        return self.url("users", "import")

    def _get_sync(self) -> None:
        self.contest = None
        self._show_page()

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync)

    def _post_sync(self) -> None:
        self.contest = None
        self._upload()

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)


class GlobalImportJobStatusHandler(BaseHandler):
    """The progress of a global users import job, as JSON, for its owner."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self, job_id: str) -> None:
        job = IMPORT_JOBS.get(job_id, self.current_user.id)
        if job is None or job.contest_id is not None:
            raise tornado.web.HTTPError(404)
        _write_job_status(self, job)
```

In `handlers/__init__.py`, add `ImportGlobalUsersHandler` and `GlobalImportJobStatusHandler` to the `from .contestuser import` list, and in the "Users/Teams" routes, after `(r"/users/remove", BulkRemoveUsersHandler),`:

```python
    (r"/users/import", ImportGlobalUsersHandler),
    (r"/users/import/([A-Za-z0-9_-]+)/status", GlobalImportJobStatusHandler),
```

In `users.html`, after the `core_title` div:

```html
{% if admin.permission_all %}
<p><a href="{{ url("users", "import") }}">Importar CSV</a></p>
{% endif %}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `CMS_CONFIG=... .venv/bin/pytest cmstestsuite/unit_tests/server/admin/ -q` and `uvx -q pyflakes cms/server/admin cmstestsuite/unit_tests/server/admin`
Expected: PASS, no pyflakes output.

- [ ] **Step 5: Commit**

```bash
git add cms/server/admin/handlers/contestuser.py cms/server/admin/handlers/__init__.py cms/server/admin/templates/users.html cmstestsuite/unit_tests/server/admin/contest_users_import_test.py
git commit -m "feat(aws): import user accounts from a CSV on the Users list

The page creates the missing accounts with their password and updates the
names and password of the existing ones, with the same mapping, checks
and progress as the contest import. It replaces CMS-Loader's Import Users.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The manual (English and Spanish)

**Files:**
- Modify: `docs/importing-users.md`
- Modify: `docs/locale/es/LC_MESSAGES/importing-users.po`
- Modify: `docs/superpowers/specs/2026-10-08-aws-global-users-import-design.md` (two lines, see Step 3)

**Interfaces:** none (docs only).

- [ ] **Step 1: Edit `docs/importing-users.md`**

1. Intro (first paragraph): after "...with that day's password." add: "It can also create the user accounts alone, with their own password, from the global Users list (see [Importing accounts](#importing-accounts))."
2. "## Where": add a line `**Users → "Importar CSV"**` for the account import, with the same permission note.
3. Fields table: the `password` row's "Required" becomes `no` and its text "The day password (see below). At most 72 bytes. Without this column, see [Without a password column](#without-a-password-column)."
4. "## The day password": replace the paragraph "New accounts get a random account password ..." with:

   "New accounts get a random account password that nobody knows, so only the day password logs in. To give the contestants an account password instead, import the accounts first on **Users → "Importar CSV"** (see [Importing accounts](#importing-accounts)), then import the contest without a password column."

   and add a subsection:

   ```markdown
   ### Without a password column

   Leave the password selector on **"(sin asignar)"** to register users who
   already have an account password:

   - A new participation gets no password of its own, so the contestant logs
     in with the account password.
   - An existing participation keeps its password, whatever it is.
   - Every user in the file must already exist. Otherwise the row gets
     "fila N: el usuario X no existe; sin columna de contraseña solo se pueden
     inscribir usuarios que ya existen" and nothing is applied: a new account
     would get a random password and could never log in.

   When the file has a column called `password` or `contraseña`, the page
   assigns it on its own: set it back to "(sin asignar)" before validating,
   and again after the page shows errors, since the password selector is
   always guessed again.
   ```

5. New section before "## Removing users and participations":

   ```markdown
   ## Importing accounts

   **Users → "Importar CSV"** creates or updates user accounts with their
   account password, and registers nobody in any contest.

   | Field | Required | What it holds |
   |-------|----------|---------------|
   | `username` | yes | The login, with the same rules as above. |
   | `first_name` | yes | First name |
   | `last_name` | yes | Last name |
   | `password` | yes | The account password. At most 72 bytes, no control characters. |

   The file, the column selectors, "Solo validar", "Importar", the progress
   bar and the errors work as in the contest import. Other columns, such as
   `team` or `group`, are ignored.

   - A **new user** is created with the row's names and password.
   - An **existing user** gets the row's first name, last name and password.
     If the password did not change, the stored hash is kept, so a contestant
     logged in with it stays logged in. Nothing else of the user changes, and
     no participation changes.

   The account password only logs in to the contests where the participation
   has no password of its own. To use it for a contest day, import the same
   file in the contest with the password left "(sin asignar)" (see
   [Without a password column](#without-a-password-column)).
   ```

6. Errors table: add rows
   - `"fila N: el usuario X no existe; sin columna de contraseña solo se pueden inscribir usuarios que ya existen"` | `Import the accounts first on Users → "Importar CSV", or assign the password column.`
   - `"Hay una importación de usuarios en curso; espera a que termine."` | `Another admin is importing accounts. Wait and upload again.`

- [ ] **Step 2: Update the Spanish translation**

Regenerate the catalog template and see which messages changed:

```bash
python3 -m venv /home/areslolxd/.claude/jobs/ae26e043/tmp/docsvenv 2>/dev/null || true
/home/areslolxd/.claude/jobs/ae26e043/tmp/docsvenv/bin/pip install -q -r docs/requirements.txt
/home/areslolxd/.claude/jobs/ae26e043/tmp/docsvenv/bin/sphinx-build -q -W --keep-going -b gettext docs /home/areslolxd/.claude/jobs/ae26e043/tmp/pot
/home/areslolxd/.claude/jobs/ae26e043/tmp/docsvenv/bin/python docs/check_translations.py /home/areslolxd/.claude/jobs/ae26e043/tmp/pot docs/locale/es/LC_MESSAGES
```

For every message the check reports as missing or stale in `importing-users.po`, edit the entry so its `msgid` is the new English text exactly and its `msgstr` is the Spanish translation, in the tone of the neighbouring entries (Spanish texts quoted from the page stay verbatim). Remove obsolete entries the check reports. Leave no `#, fuzzy`.

- [ ] **Step 3: Align the spec with the code**

In the spec, under "Code" → `plan_import`, replace "and the plan records that participation passwords are not touched" with "and `apply_import` leaves a participation password alone when the row has none". Under "Testing", replace "and the functional tests on the rootful Docker harness" with "and the whole unit suite in its two pytest groups".

- [ ] **Step 4: Verify**

Run the check of Step 2 again (expect no problems), then:

```bash
/home/areslolxd/.claude/jobs/ae26e043/tmp/docsvenv/bin/python -m pytest docs/tests -q
/home/areslolxd/.claude/jobs/ae26e043/tmp/docsvenv/bin/sphinx-build -q -W --keep-going -b html -D language=es docs /home/areslolxd/.claude/jobs/ae26e043/tmp/html-es
```

Expected: tests pass; the build exits 0; the Spanish page contains "Importar cuentas" (or the heading you translated) and "sin columna de contraseña".

- [ ] **Step 5: Commit**

```bash
git add docs/importing-users.md docs/locale/es/LC_MESSAGES/importing-users.po docs/superpowers/specs/2026-10-08-aws-global-users-import-design.md
git commit -m "docs(aws): document the accounts import and the optional contest password

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Whole-branch verification and end to end (controller)

**Files:** none changed unless a check fails (then fix in the owning task's files and commit with a `fix(aws): ...` message).

- [ ] **Step 1: Whole unit suite, in its two groups** (from CLAUDE.md; check `docker/_cms-test-internal.sh` for the current gevent file list)

```bash
GEVENT_SERVICE_FILES="cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py cmstestsuite/unit_tests/service/twophase_reenqueue_test.py"
CMS_CONFIG=... .venv/bin/pytest -q cmstestsuite/unit_tests/cmscontrib cmstestsuite/unit_tests/cmsranking cmstestsuite/unit_tests/db/rankinggroup_test.py $GEVENT_SERVICE_FILES
IGNORE_ARGS="--ignore=cmstestsuite/unit_tests/cmscontrib --ignore=cmstestsuite/unit_tests/cmsranking --ignore=cmstestsuite/unit_tests/db/rankinggroup_test.py"
for f in $GEVENT_SERVICE_FILES; do IGNORE_ARGS="$IGNORE_ARGS --ignore=$f"; done
CMS_CONFIG=... .venv/bin/pytest -q cmstestsuite/unit_tests $IGNORE_ARGS
uvx -q pyflakes cms cmscommon cmscontrib cmsranking cmstaskenv cmstestsuite
```

Expected: both groups pass (compare any failure against `origin/main` before blaming the branch), no pyflakes output.

- [ ] **Step 2: End to end in Chromium on local processes**

With the worktree venv and `CMS_CONFIG` on the test DB: reset it (`drop_db(); init_db()`), create an admin (`.venv/bin/cmsAddAdmin -p <pw> admin`), start `cmsLogService 0` and `cmsAdminWebServer 0` in the background, create a contest (with a task-free setup and password login allowed) from AWS, then start `cmsContestWebServer 0 -c <contest id>`. Using the chrome-devtools tools:

1. AWS → Users → "Importar CSV": upload a 3-row CSV (`usuario,nombre,apellido,contraseña`), "Solo validar" shows "Usuarios nuevos: 3" and no participation lines; choose the file again and "Importar": the bar finishes with "Listo. Usuarios nuevos: 3, actualizados: 0."
2. Contest → Users → "Importar CSV": the same file, password selector set to "(sin asignar)"; "Solo validar" shows 3 new participations; "Importar".
3. CWS: log in as one user with the account password; it works.
4. AWS → Users → "Importar CSV": the same file again → "actualizados: 3"; reload CWS: still logged in.
5. Contest import with a 4th row for a user that does not exist and the password unassigned: the error "fila 5: el usuario … no existe; sin columna de contraseña …" and nothing applied.

Stop the services afterwards.

- [ ] **Step 3: Final review gate**

Dispatch the final whole-branch review (code-reviewer for correctness and password safety). After it is clean, the controller merges into `main` (merge commit, pushed from a detached worktree with `HEAD:main`), merges `main` into `beta` the same way, and reports. The version bump (omi.7), tag and deploy are the user's call.
