# Archive ranking groups: the default archive_at (task group 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ProxyService's sweep stores, in an empty `RankingGroup.archive_at`, the end of the group's last contest plus 24 hours, only when that instant is still in the future.

**Architecture:** Two small private helpers in `cms/service/ProxyService.py` (one pure read, one that writes and commits), called at the start of `_missing_operations_sync` inside its existing `SessionGen`. First column ProxyService writes; a database error is logged and the sweep goes on.

**Tech Stack:** SQLAlchemy 2.x `select`, PostgreSQL, pytest (asyncio group; `cmstestsuite/unit_tests/service/proxyservice_groups_test.py` has the group-mode fixtures: read how it builds groups, contests and a ProxyService).

**Spec:** `openspec/changes/archive-ranking-groups/specs/ranking-group-archiving/spec.md`, requirement "archive_at gets a visible default" (all scenarios except "Updating an existing database", done in group 1); `design.md` decision 6; tasks 2.1 and 2.2 of `tasks.md`.

## Global Constraints

- The default is the LATEST `stop` of ALL participation `Group`s of ALL contests whose `ranking_group_id` is the group, plus a constant 24 hours (`ARCHIVE_GRACE = timedelta(hours=24)`, not an option); analysis times are not counted.
- Fill only when ALL hold: `archive_at is None`, the group has at least one contest, and the computed instant is strictly in the future of `now` (naive UTC, `cmscommon.datetime.make_datetime()`).
- Never overwrite a set `archive_at`; leave it empty in every other case (no contests, instant already past).
- Group mode only (`self.contest_id is None`); legacy single-contest mode does nothing.
- A database error while filling is logged (one WARNING line with `describe_error`, no traceback), the session is rolled back, and the sweep continues; the next sweep tries again.
- All code, comments and commit messages in English; Conventional Commits with the `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` trailer.

## Review Focus

- `Group.stop` defaults to 2100-01-01 ("no end"): such a contest gives an `archive_at` in 2100, not an immediate archive. Test in Task 1.
- A group with two contests, each with several participation groups: the maximum over all of them. Test in Task 1.
- A group whose contests all ended long ago, and one whose end plus 24 h equals `now` exactly (not strictly in the future): left empty. Test in Task 2.
- A group with `archive_at` set in the past by hand is untouched and stays archived. Test in Task 2.
- The commit fails (`OperationalError`): the sweep still enqueues the visibility and the missing operations, and the next sweep fills the field. Test in Task 2.
- Legacy mode (`contest_id` set): nothing is read or written. Test in Task 2.

---

### Task 1: the default instant (tasks.md 2.1)

**Files:**
- Modify: `cms/service/ProxyService.py` (module constant near the other module-level constants; the helper as a method of `ProxyService` next to `_compute_group_contests`)
- Test: `cmstestsuite/unit_tests/service/proxyservice_groups_test.py` (add a test class, DB-backed like its neighbours)

**Interfaces:**
- Produces: `ARCHIVE_GRACE: timedelta` (module level, `timedelta(hours=24)`); `ProxyService._default_archive_at(self, session: Session, ranking_group: RankingGroup) -> datetime | None`: returns the latest `Group.stop` of the group's contests plus `ARCHIVE_GRACE`, or `None` when the group has no contests.

- [ ] **Step 1: Write the failing tests** (class `TestDefaultArchiveAt`, comment each with its spec Scenario): `test_the_last_contest_decides` (Scenario "The last contest decides": two contests in the group, the second ends later -> its latest participation-group `stop` plus 24 h; the first contest has two participation groups with different stops: the larger of all wins), `test_a_group_with_no_contests` (Scenario "A group with no contests": returns None), and `test_a_contest_with_the_default_stop_gives_2100` (Review Focus: a contest whose group keeps `stop = datetime(2100, 1, 1)` returns `datetime(2100, 1, 2)`).
- [ ] **Step 2: Run them to verify they fail:** `.venv/bin/pytest cmstestsuite/unit_tests/service/proxyservice_groups_test.py -k DefaultArchiveAt -v` (with `CMS_CONFIG=` the lane config). Expected: FAIL (`_default_archive_at` does not exist).
- [ ] **Step 3: Implement** `ARCHIVE_GRACE` and `_default_archive_at`: one query, `select(func.max(Group.stop)).join(Contest, Group.contest_id == Contest.id).where(Contest.ranking_group_id == ranking_group.id)` (import `func` and `Group` as the module already imports its neighbours), return `None` if the scalar is `None`, else `latest + ARCHIVE_GRACE`.
- [ ] **Step 4: Run the new tests, then the whole `proxyservice_groups_test.py`** (own pytest process). Expected: PASS.
- [ ] **Step 5: Commit** `feat(proxy): compute the default archive_at of a ranking group (#51)`; tick 2.1 in `tasks.md` in the same commit.

### Task 2: the sweep fills it (tasks.md 2.2)

**Files:**
- Modify: `cms/service/ProxyService.py` (`_fill_archive_defaults`; call it first in `_missing_operations_sync`, inside the existing `with SessionGen() as session:` before `self._enqueue_visibility(session)`)
- Test: `cmstestsuite/unit_tests/service/proxyservice_groups_test.py` (class `TestFillArchiveDefaults`)

**Interfaces:**
- Consumes: `ProxyService._default_archive_at(session, ranking_group) -> datetime | None` and `ARCHIVE_GRACE` (Task 1); `RankingGroup.archive_at` (group 1); `cmscommon.datetime.make_datetime() -> datetime` (naive UTC now); `describe_error` from `cms.db.connect_options` (already imported).
- Produces: `ProxyService._fill_archive_defaults(self, session: Session) -> None`.

- [ ] **Step 1: Write the failing tests** (patch `cms.service.ProxyService.make_datetime` for `now`; comment each with its Scenario): `test_a_group_with_a_contest_that_has_not_ended_gets_the_default` (Scenario of that name: after the sweep helper runs and the session is re-read, `archive_at == end + 24h`, and it is committed: read it with a fresh session), `test_a_set_archive_at_is_kept` (also one set in the past by hand: untouched), `test_contests_that_already_ended_do_not_archive_a_group_by_surprise` (end + 24 h in the past, and the case end + 24 h == now: both stay None), `test_a_group_with_no_contests_stays_empty`, `test_legacy_mode_does_nothing` (a ProxyService built with a `contest_id`: no change), `test_a_database_error_does_not_stop_the_sweep` (make the commit raise `sqlalchemy.exc.OperationalError` once: `_missing_operations_sync` still returns and the visibility operations are enqueued, one WARNING is logged, and a second sweep fills the field).
- [ ] **Step 2: Run them to verify they fail:** `.venv/bin/pytest cmstestsuite/unit_tests/service/proxyservice_groups_test.py -k FillArchiveDefaults -v`. Expected: FAIL.
- [ ] **Step 3: Implement `_fill_archive_defaults(self, session)`:** return at once in legacy mode; for each `RankingGroup` with `archive_at IS NULL`, compute `_default_archive_at`; if it is not None and `> now`, set `archive_at`; if anything was set, `session.commit()` inside `try/except (DBAPIError, PoolTimeoutError)` (the types `_missing_operations_sync` and `try_commit` already handle: reuse what the module imports for `_is_database_error`) that rolls back and logs `logger.warning("Cannot store the default archive_at of the ranking groups (%s); the next sweep tries again.", describe_error(error))`. Call it from `_missing_operations_sync` as stated; it must not change what that method returns.
- [ ] **Step 4: Run the new tests, then the whole `proxyservice_groups_test.py`, `ProxyService_test.py` and `proxyexecutor_test.py`** (each in its own pytest process). Expected: PASS. `uvx -q pyflakes cms/service/ProxyService.py <test file>`: no new warnings.
- [ ] **Step 5: Commit** `feat(proxy): store the default archive_at of a ranking group in the sweep (#51)`; tick 2.2 in `tasks.md` in the same commit.
