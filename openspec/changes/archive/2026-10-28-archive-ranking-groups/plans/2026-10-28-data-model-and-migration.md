# Archive ranking groups: data model and migration (task group 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `RankingGroup` has a nullable `archive_at` and an `is_archived_at(now)` test, and existing databases get the column, empty, on the next `cmsSetupDB`.

**Architecture:** One `DateTime` column next to the MC-2 windows in `cms/db/rankinggroup.py`, one derived method (no stored flag), and a `fork_*.py` update guarded by an `information_schema` lookup, applied by `cmsSetupDB` and checked against `cmsInitDB` by `schema_diff_test`. Nothing else changes: ProxyService is group 3.

**Tech Stack:** SQLAlchemy, PostgreSQL, psycopg2, pytest (asyncio group: do not import anything that calls `gevent.monkey.patch_all()`).

**Spec:** `openspec/changes/archive-ranking-groups/specs/ranking-group-archiving/spec.md` (requirements "A ranking group is archived from its archive_at instant" and "archive_at gets a visible default", the last scenario only: "Updating an existing database"); decisions 1 and 5 of `design.md`; tasks 1.1 and 1.2 of `tasks.md`.

## Global Constraints

- `archive_at` is naive UTC and nullable; a group with no `archive_at` is never archived.
- Archived means `archive_at is not None and archive_at <= now` (the boundary instant is archived).
- No stored "archived" flag.
- The migration adds the column EMPTY for every existing row and never fills it.
- The update must be idempotent (cmsSetupDB runs it at every start) and take no lock on `ranking_groups` when the column already exists.
- New files get the header of `cmscontrib/updaters/fork_submission_limits.py` (Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>, AGPL).
- All code, comments and commit messages in English; Conventional Commits with the `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` trailer.

## Review Focus

- `archive_at == now` exactly: archived (the `<=` boundary); one microsecond earlier: not archived. Test in Task 1.
- `archive_at` moved from the past to the future: no longer archived. Test in Task 1.
- Existing groups WITH data (hide_at, a staff password) keep every value and get `archive_at` NULL after the update, also after running it twice. Test in Task 2.
- The update on a database where the column was dropped and rows exist; a second run changes nothing and takes no lock. Test in Task 2.
- `schema_diff_test` (upgraded schema vs fresh `cmsInitDB`): the column definition matches exactly (`timestamp without time zone`, nullable, no default). Task 2.

---

### Task 1: `RankingGroup.archive_at` and `is_archived_at` (tasks.md 1.1)

**Files:**
- Modify: `cms/db/rankinggroup.py` (after the `unfreeze_at` column, before `is_hidden_at`)
- Test: `cmstestsuite/unit_tests/db/rankinggroup_archive_test.py` (new)

**Interfaces:**
- Produces: `RankingGroup.archive_at: datetime | None` (`Column(DateTime, nullable=True)`); `RankingGroup.is_archived_at(self, now: datetime) -> bool`.

- [ ] **Step 1: Write the failing tests** in `cmstestsuite/unit_tests/db/rankinggroup_archive_test.py` (header like the neighbouring `rankinggroup_visibility_test.py`; no database needed, build `RankingGroup(name="olim", description="OLIM")` in memory; `unittest.TestCase`). Name each test or comment it with the Scenario it covers:
  - `test_before_archive_at` (Scenario "Before archive_at"): `archive_at = now + 1 hour` -> `is_archived_at(now)` is False.
  - `test_at_and_after_archive_at` (Scenario "At and after archive_at"): `archive_at = now` -> True; `archive_at = now - 1 microsecond` -> True; `archive_at = now + 1 microsecond` -> False; with `archive_at` in the past, True, and after moving it to `now + 1 hour` it is False again (stays archived only while it is not moved to the future).
  - `test_no_archive_at` (Scenario "No archive_at"): `archive_at = None` -> False for a very late `now` (`datetime(2100, 1, 1)`).
  - `test_the_column_defaults_to_empty` using the DB (`DatabaseMixin`, as `rankinggroup_visibility_test.py::test_defaults`): a committed group has `archive_at is None`.
- [ ] **Step 2: Run them to verify they fail:** `.venv/bin/pytest cmstestsuite/unit_tests/db/rankinggroup_archive_test.py -v` with `CMS_CONFIG=/tmp/claude-1000/-var-home-areslolxd-Documentos-cms/lanes/laneg/cms.toml`. Expected: FAIL (`archive_at`/`is_archived_at` do not exist).
- [ ] **Step 3: Implement** the column with a comment in the style of the MC-2 one ("Naive UTC; None means never archived: see the ranking-group-archiving spec") and `is_archived_at(self, now: datetime) -> bool` returning `self.archive_at is not None and self.archive_at <= now`, with a one-line docstring like `is_hidden_at`.
- [ ] **Step 4: Run the tests and `.venv/bin/pytest cmstestsuite/unit_tests/db/rankinggroup_visibility_test.py`** (the existing file, to see the model still works). Expected: PASS. Run each file in its own pytest process.
- [ ] **Step 5: Commit** `feat(db): add archive_at to ranking groups (#51)` (tick task 1.1 in `openspec/changes/archive-ranking-groups/tasks.md` in the same commit).

### Task 2: the update that adds the column to existing databases (tasks.md 1.2)

**Files:**
- Create: `cmscontrib/updaters/fork_ranking_group_archive.py`
- Modify: `cmscontrib/SetupDB.py` (import and call it right after `apply_fork_submission_limits_update()` in `setup_db`)
- Modify: `cmstestsuite/unit_tests/schema_diff_test.py` (import the SQL constant and append it to the `fork_sql` list of `get_updated_schema`)
- Modify: `cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py` (new class `TestForkRankingGroupArchive`, after `TestForkSubmissionLimits`)

**Interfaces:**
- Consumes: `apply_fork_sql(sql: str, tables: str) -> None` from `cmscontrib/updaters/fork_ddl.py` (rolls back and raises `SchemaLockTimeout` after 5 s of lock wait).
- Produces: `FORK_RANKING_GROUP_ARCHIVE_SQL: str` and `apply_fork_ranking_group_archive_update() -> None`.

- [ ] **Step 1: Write the failing tests** in `SetupDBTest.py`, class `TestForkRankingGroupArchive(DatabaseMixin, unittest.TestCase)`, modelled on `TestForkSubmissionLimits` (same helpers: a `_run(statement)`, a schema reader returning `format_type`, `attnotnull` and the default of the `archive_at` column from `pg_attribute`/`pg_attrdef`; `addCleanup(apply_fork_ranking_group_archive_update)` in `setUp`):
  - `test_adds_what_init_db_creates_and_a_second_run_changes_nothing`: the expected shape read from the fresh schema is `("archive_at", "timestamp without time zone", False)` with no default (assert it explicitly so an empty result cannot pass); drop the column, apply the update, shape equal; apply again, equal.
  - `test_existing_groups_are_left_empty` (Scenario "Updating an existing database"): insert two `ranking_groups` rows with values in other columns (`hide_at`, a `staff_password`), drop the column, apply the update twice, then assert both rows still have those values and `archive_at IS NULL`.
  - `test_takes_no_lock_on_ranking_groups_when_the_column_exists`: as `test_takes_no_lock_on_submissions_when_nothing_is_missing`, holding `LOCK TABLE ranking_groups IN ACCESS EXCLUSIVE MODE` from another connection.
  - `test_setup_db_adds_the_column`: drop the column, run `TestSchemaLockTimeout._run_setup_db()`, return code 0, column back.
- [ ] **Step 2: Run them to verify they fail:** `.venv/bin/pytest cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py -k RankingGroupArchive -v` (this file is in the gevent group: run it alone). Expected: FAIL on the missing import.
- [ ] **Step 3: Implement `FORK_RANKING_GROUP_ARCHIVE_SQL` and `apply_fork_ranking_group_archive_update() -> None`** in the new file, copying the structure of `fork_submission_limits.py`: a `DO $$ ... $$` block with `IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'ranking_groups' AND column_name = 'archive_at') THEN ALTER TABLE public.ranking_groups ADD COLUMN archive_at timestamp without time zone; END IF;`, a module docstring saying what it creates and why it never fills the column ("an existing group must not be archived by surprise: see the spec"), and `apply_fork_sql(FORK_RANKING_GROUP_ARCHIVE_SQL, "ranking_groups")` in the function. Then call it from `setup_db` in `SetupDB.py` right after `apply_fork_submission_limits_update()`.
- [ ] **Step 4: Wire `schema_diff_test`:** import `FORK_RANKING_GROUP_ARCHIVE_SQL` and append it to `fork_sql` in `get_updated_schema` (it runs twice there, so idempotence is exercised).
- [ ] **Step 5: Run and verify:** `SetupDBTest.py` alone (all of it, the new class and the old ones), and `cmstestsuite/unit_tests/schema_diff_test.py` alone. `schema_diff_test` needs a `pg_dump` that matches the server: if the host `pg_dump` is older than the DB, use the PG18 shim in the CI mirror (`/tmp/claude-1000/-var-home-areslolxd-Documentos-cms/lanes/ci/pgdump18/pg_dump` first in `PATH`, DB on the `cms-test-pg18` container, port 55433) and say so in the report. Expected: PASS. `uvx -q pyflakes` on the changed `.py` files: only warnings that already existed.
- [ ] **Step 6: Commit** `feat(db): add the archive_at update to cmsSetupDB (#51)` (tick task 1.2 in `tasks.md` in the same commit).
