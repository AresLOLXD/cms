# Archive ranking groups: the final visibility (task group 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a ranking group is found archived, ProxyService sends its visibility ONCE as shown and not frozen (every window empty, the stored staff password unchanged), and nothing more while it stays archived; unarchiving forgets the mark so the normal visibility resumes.

**Architecture:** An in-memory set of group names in `ProxyService`, used by `_enqueue_visibility` (the loop that today skips archived groups).

**Tech Stack:** `cms/service/ProxyService.py`, pytest (asyncio group; `ArchiveTrafficTestCase` and its spies in `cmstestsuite/unit_tests/service/proxyservice_groups_test.py`, added in group 3).

**Spec:** `openspec/changes/archive-ranking-groups/specs/ranking-group-archiving/spec.md`, requirement "An archived group shows its final standings" (all scenarios) and "Unarchiving resumes the sending"; `design.md` decision 7; task 4.1 of `tasks.md`.

## Global Constraints

- The final operation is a `ProxyExecutor.VISIBILITY_TYPE` operation for the group with `hide_at`, `show_at`, `freeze_at`, `unfreeze_at` all `None` and `staff_password` = the group's stored value (same payload keys as the normal operation).
- Sent once per group per ProxyService run while it stays archived; the mark is NOT persisted (a restart sends it once more: harmless).
- Not archived (including after unarchiving): the normal visibility is sent as before and the group's mark is removed.
- A `shortcut:` comment: the set is touched from executor threads with no lock, worst case the same operation is enqueued twice; add a lock if that ever matters.
- All code, comments and commit messages in English; Conventional Commits with the `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` trailer.

## Review Focus

- A group hidden AND frozen at archive time ends shown and not frozen (all four windows None). Test in Task 1.
- Two sweeps plus an `initialize` in a row while archived: exactly one final operation in total. Test in Task 1.
- Archived -> unarchived -> archived again: the final visibility is sent again; while unarchived the normal windows go out. Test in Task 1.
- A group with a staff password keeps it in the final operation. Test in Task 1.
- Legacy mode (`contest_id` set) sends no visibility at all, as before. Existing test must still pass.

---

### Task 1: send the final visibility once (tasks.md 4.1)

**Files:**
- Modify: `cms/service/ProxyService.py` (`__init__` for the set; `_enqueue_visibility`)
- Test: `cmstestsuite/unit_tests/service/proxyservice_groups_test.py` (class `TestFinalVisibility`)

**Interfaces:**
- Consumes: `ProxyService._is_archived(ranking_group) -> bool` (group 3); `_enqueue_visibility(self, session, group=None, enqueue=None)`.
- Produces: `ProxyService._final_visibility_sent: set[str]` (names of the archived groups whose final visibility was enqueued in this run).

- [ ] **Step 1: Write the failing tests** (comment each with its Scenario): `test_a_hidden_group_is_archived` ("A hidden group is archived": one VISIBILITY operation with the four windows None and the staff password), `test_a_frozen_group_is_archived`, `test_the_final_visibility_is_sent_once` (two `_missing_operations_sync` and one `initialize`: exactly one), `test_unarchiving_forgets_the_final_visibility` (archived -> `archive_at` moved to the future: the normal windows are sent -> archived again: the final one is sent again), `test_a_new_service_sends_it_once_more` (a fresh ProxyService over the same DB sends it once).
- [ ] **Step 2: Run to verify they fail:** `.venv/bin/pytest cmstestsuite/unit_tests/service/proxyservice_groups_test.py -k FinalVisibility -v` with `CMS_CONFIG=/tmp/claude-1000/-var-home-areslolxd-Documentos-cms/lanes/laneg/cms.toml`. Expected: FAIL.
- [ ] **Step 3: Implement:** `self._final_visibility_sent: set[str] = set()` in `__init__` next to the other sent sets; in the loop of `_enqueue_visibility`: archived -> if its name is in the set, `continue`; else enqueue the final operation and add the name; not archived -> `self._final_visibility_sent.discard(name)` then the normal operation. Update the docstring ("The archived groups get one final visibility, shown and not frozen"). Add the `shortcut:` comment.
- [ ] **Step 4: Run the new tests, the whole `proxyservice_groups_test.py`, `ProxyService_test.py`, `proxyexecutor_test.py`, `proxyservice_rws_rejection_test.py`** (each in its own pytest process); `uvx -q pyflakes` on the two changed files. Expected: PASS, no new warnings. Group 3's tests that assert "nothing is enqueued for an archived group" will now see the one final operation: update them to say "nothing except the final visibility" (they are the spec's exception) and say so in the report.
- [ ] **Step 5: Commit** `feat(proxy): send the final visibility of a group once when it is archived (#51)`; tick 4.1.
