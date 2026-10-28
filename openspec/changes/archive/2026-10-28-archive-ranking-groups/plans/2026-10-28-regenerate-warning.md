# Archive ranking groups: the Regenerate warning (task group 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Regenerating the ranking of an archived group does nothing and logs a WARNING that names the group and its `archive_at`.

**Architecture:** The early return added in group 3 (task 3.4) at the top of `_regenerate_ranking_sync` gets a log line; nothing else changes.

**Tech Stack:** `cms/service/ProxyService.py`, pytest (asyncio group; the `TestRegenerateArchivedSafetyNet` class of group 3 in `cmstestsuite/unit_tests/service/proxyservice_groups_test.py`).

**Spec:** `openspec/changes/archive-ranking-groups/specs/ranking-group-archiving/spec.md`, requirement "Regenerate does nothing for an archived group" (both scenarios); `design.md` decision 8; task 5.1 of `tasks.md`.

## Global Constraints

- The WARNING names the group and `archive_at`, says that the group is archived and that moving `archive_at` to the future (or clearing it) publishes a correction; one WARNING per call.
- No reset, no put and no delete is enqueued for an archived group (group 3 already guarantees it; keep it).
- The RPC returns normally (no exception): AWS keeps saying "requested".
- A group that is not archived regenerates exactly as today.
- All code, comments and commit messages in English; Conventional Commits with the `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` trailer.

## Review Focus

- The warning text contains the group name and the `archive_at` value; assert both. Test in Task 1.
- Regenerate of the root namespace (`group=None`) never warns and works as before. Test in Task 1.
- Regenerate of an unknown group name behaves as before (no new exception). Test in Task 1.

---

### Task 1: warn on an ignored regenerate (tasks.md 5.1)

**Files:**
- Modify: `cms/service/ProxyService.py` (`_regenerate_ranking_sync`, the early return of group 3)
- Test: `cmstestsuite/unit_tests/service/proxyservice_groups_test.py` (extend `TestRegenerateArchivedSafetyNet`, or a new class `TestRegenerateArchivedWarning`)

- [ ] **Step 1: Write the failing tests** (comment each with its Scenario): `test_regenerate_on_an_archived_group` ("Regenerate on an archived group": `assertLogs("cms.service.ProxyService", "WARNING")` sees one record containing the group name and the `archive_at` value, and no operation is enqueued), `test_regenerate_on_a_group_that_is_not_archived` ("Regenerate on a group that is not archived": its RESET and data are enqueued as today and no warning about archiving is logged, use `assertNoLogs` or filter the records), `test_regenerate_of_the_root_and_of_an_unknown_group` (the two Review Focus cases).
- [ ] **Step 2: Run to verify they fail:** `-k "RegenerateArchived"`. Expected: the first FAILS (no warning).
- [ ] **Step 3: Implement** the `logger.warning(...)` in the early return, with the group name and `archive_at`; remove the "group 5 adds the warning" comment.
- [ ] **Step 4: Run the new tests, the whole `proxyservice_groups_test.py` and `ProxyService_test.py`; `uvx -q pyflakes` on the two changed files.** Expected: PASS, no new warnings.
- [ ] **Step 5: Commit** `feat(proxy): warn when a regenerate of an archived group is ignored (#51)`; tick 5.1.
