# Archive ranking groups: ProxyService stops sending to archived groups (task group 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ProxyService sends nothing to an archived ranking group (scores, tokens, datasets, contest data, visibility), without ever resetting its namespace; unarchiving resumes the normal sending.

**Architecture:** Filter at the existing gates in `cms/service/ProxyService.py`: `_is_sent`, `_contests_to_send` (new `include_archived` flag), `_enqueue_visibility`; keep the group mapping unfiltered so `_reinitialize_sync` does not see an archived group as `lost`; a safety net so `_regenerate_ranking_sync` cannot reset an archived group.

**Tech Stack:** SQLAlchemy ORM objects already loaded (`contest.ranking_group`), `RankingGroup.is_archived_at(now)` (group 1), `cmscommon.datetime.make_datetime()`, pytest (asyncio group; fixtures of `cmstestsuite/unit_tests/service/proxyservice_groups_test.py` and the `ArchiveSweepTestCase` base added in group 2).

**Spec:** `openspec/changes/archive-ranking-groups/specs/ranking-group-archiving/spec.md`, requirements "ProxyService sends nothing to an archived group" (all scenarios) and "The ranking keeps the final standings of an archived group"; `design.md` decisions 2 and 3; tasks 3.1-3.4 of `tasks.md`.

## Global Constraints

- Archived means `RankingGroup.is_archived_at(make_datetime())`, evaluated where the group is read, once per call (design decision 4).
- Group mode only: legacy single-contest mode (`self.contest_id is not None`) behaves exactly as before.
- `_compute_group_contests` MUST keep counting the contests of archived groups, otherwise `_reinitialize_sync` resets the group's namespace and wipes its final standings (design decision 3; the highest risk of the change).
- The final visibility of an archived group is group 4: here `_enqueue_visibility` simply skips archived groups.
- `_regenerate_ranking_sync` for an archived group returns BEFORE enqueuing the reset, silently (group 5 adds the WARNING).
- All code, comments and commit messages in English; Conventional Commits with the `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` trailer.

## Review Focus

- A group becomes archived while `_group_contests` already holds its contests: `_reinitialize_sync` sends NO reset to it, and still resets a different, non-archived group that lost a contest. Test in Task 3.
- `ProxyService.__init__` (the initial `_group_contests`, near line 1106) must include archived groups. Test in Task 3.
- A contest assigned to an archived group (it is "gained" by the mapping) sends no data and no submissions. Test in Task 1/3.
- Exactly at `archive_at == now`: archived (the `<=` boundary of group 1) so nothing is sent; one microsecond before: still sent. Test in Task 1.
- A group whose `archive_at` is moved to the future (unarchived): the next sweep sends its visibility and the missing operations again. Test in Task 3.
- A contest with `ranking_group is None` and legacy mode keep their old behavior. Test in Task 1.
- Regenerate on an archived group never enqueues a RESET. Test in Task 4.

---

### Task 1: the gates `_is_sent` and the event paths (tasks.md 3.1)

**Files:**
- Modify: `cms/service/ProxyService.py` (`_is_sent`, a new `_is_archived`)
- Test: `cmstestsuite/unit_tests/service/proxyservice_groups_test.py` (new class `TestSkipArchivedEvents`)

**Interfaces:**
- Produces: `ProxyService._is_archived(self, ranking_group: RankingGroup | None) -> bool` (`ranking_group is not None and ranking_group.is_archived_at(make_datetime())`); `_is_sent` returns False for a contest of an archived group in group mode (the three event paths `_submission_scored_sync`, `_submission_tokened_sync`, `_dataset_updated_sync` already call `_is_sent`, so they need no other change).

- [ ] **Step 1: Write the failing tests** (comment each with its Scenario; patch `cms.service.ProxyService.make_datetime` for `now`): `test_a_score_event_for_an_archived_group` ("A score event for an archived group": the sync handler enqueues nothing), `test_a_token_event_for_an_archived_group`, `test_a_dataset_update_for_an_archived_group`, `test_a_contest_added_to_an_archived_group` ("A contest added to an archived group": its score event enqueues nothing), `test_the_archive_boundary` (`archive_at == now`: nothing is sent; `archive_at == now + 1 microsecond`: the score is sent), `test_a_group_that_is_not_archived_still_sends` ("Correction during the grace period": `archive_at` in the future: the new score is enqueued), `test_legacy_mode_is_unchanged` (a ProxyService with a `contest_id`: `_is_sent` as before). Reuse how the existing events tests build a contest, a submission and a ProxyService.
- [ ] **Step 2: Run them to verify they fail:** `.venv/bin/pytest cmstestsuite/unit_tests/service/proxyservice_groups_test.py -k SkipArchivedEvents -v` (with `CMS_CONFIG=` the lane config). Expected: FAIL.
- [ ] **Step 3: Implement** `_is_archived` and the group-mode condition in `_is_sent` (`contest.ranking_group is not None and not self._is_archived(contest.ranking_group)`).
- [ ] **Step 4: Run the new tests and the whole `proxyservice_groups_test.py`.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(proxy): send no score, token or dataset event to an archived group (#51)`; tick 3.1.

### Task 2: the sweep, `initialize` and the visibility skip archived groups (tasks.md 3.2)

**Files:**
- Modify: `cms/service/ProxyService.py` (`_contests_to_send`, `_enqueue_visibility`)
- Test: same file (class `TestSkipArchivedSweep`)

**Interfaces:**
- Consumes: `_is_archived` (Task 1).
- Produces: `ProxyService._contests_to_send(self, session: Session, include_archived: bool = False) -> list[Contest]` (in group mode, drops the contests of archived groups unless `include_archived`; legacy mode ignores the flag); `_enqueue_visibility` skips archived groups.

- [ ] **Step 1: Write the failing tests** ("The sweep skips an archived group": two groups, one archived: `_missing_operations_sync` enqueues nothing for the archived one and still enqueues what the other misses, visibility included; "initialize skips it": `initialize()` enqueues no contest data and no visibility for the archived group; `_enqueue_visibility(session, "archived-name")` for a named archived group enqueues nothing).
- [ ] **Step 2: Run to verify they fail:** `-k SkipArchivedSweep`. Expected: FAIL.
- [ ] **Step 3: Implement** the flag (filter in Python after the query: `[c for c in contests if include_archived or not self._is_archived(c.ranking_group)]`) and the skip in `_enqueue_visibility`'s loop. Do NOT change `_compute_group_contests` yet.
- [ ] **Step 4: Run the new tests and the whole file.** Expected: PASS except tests that the next task fixes (none should break: `_compute_group_contests` still uses the default flag, so the mapping would drop archived groups; add the Task 3 test first if a reinitialize test of the old suite breaks and say so).
- [ ] **Step 5: Commit** `feat(proxy): skip archived groups in the sweep, initialize and visibility (#51)`; tick 3.2.

### Task 3: the group mapping keeps archived groups (tasks.md 3.3)

**Files:**
- Modify: `cms/service/ProxyService.py` (`_compute_group_contests`)
- Test: same file (class `TestArchivedGroupsAndReinitialize`)

**Interfaces:**
- Consumes: `_contests_to_send(session, include_archived)` (Task 2).

- [ ] **Step 1: Write the failing tests** (Scenarios "Initialize and reinitialize leave an archived group alone" and "Group archived while the ranking holds its data"; "Unarchiving resumes the sending"): (a) a group that becomes archived after `_group_contests` held its contests: `_reinitialize_sync` enqueues no RESET (and no put/delete) for it, and the RESET of a DIFFERENT non-archived group that lost a contest is still enqueued; (b) a freshly built `ProxyService`'s `_group_contests` includes the archived group (the initial mapping at construction); (c) a contest newly assigned to an archived group, then `_reinitialize_sync`: no data and no submissions are enqueued for it; (d) after the `archive_at` of the group is moved to the future, the next `_missing_operations_sync` enqueues its visibility and its missing operations again.
- [ ] **Step 2: Run to verify they fail** (`-k ArchivedGroupsAndReinitialize`): (a) must fail with a RESET for the archived group.
- [ ] **Step 3: Implement:** `_compute_group_contests` calls `self._contests_to_send(session, include_archived=True)`.
- [ ] **Step 4: Run the new tests, the whole `proxyservice_groups_test.py`, `ProxyService_test.py`, `proxyexecutor_test.py`, `proxyservice_rws_rejection_test.py`** (each in its own pytest process). Expected: PASS.
- [ ] **Step 5: Commit** `feat(proxy): keep archived groups in the group mapping so reinitialize never resets them (#51)`; tick 3.3.

### Task 4: Regenerate cannot reset an archived group (tasks.md 3.4)

**Files:**
- Modify: `cms/service/ProxyService.py` (`_regenerate_ranking_sync`)
- Test: same file (class `TestRegenerateArchivedSafetyNet`)

- [ ] **Step 1: Write the failing test** ("Regenerate on an archived group", safety-net half only): regenerating a named archived group enqueues NO operation at all (no RESET); regenerating a group that is not archived still enqueues its RESET and data (use the existing regenerate tests as the model).
- [ ] **Step 2: Run to verify it fails.** Expected: FAIL (a RESET is enqueued).
- [ ] **Step 3: Implement** at the top of `_regenerate_ranking_sync`, before the lock and the reset: for a non-None `group`, read the `RankingGroup` with that name in a short `SessionGen` and `return` if `self._is_archived(it)`. One comment line saying group 5 adds the warning. Keep the rest untouched.
- [ ] **Step 4: Run the new test, the whole `proxyservice_groups_test.py` and `ProxyService_test.py`; `uvx -q pyflakes cms/service/ProxyService.py <test file>`.** Expected: PASS, no new warnings.
- [ ] **Step 5: Commit** `fix(proxy): a regenerate cannot reset an archived group (#51)`; tick 3.4.
