# Follow-up of the bulk removal in AWS (issue #19) — Design

**Date:** 2026-10-24
**Status:** Decisions taken by a read-only specialist panel (python-pro) and
verified against main `10121ec8`; approved by the owner as a consolidated
design on 2026-10-24.

## Intent

The bulk removal of users (platform scope) and participations (contest
scope) is on main (`cms/server/admin/bulkremove.py`,
`handlers/bulkremove.py`, `templates/users_bulk_remove.html`). Its final
review left a checklist, and the owner added a later comment on the issue
with a real functional bug: removed users stay on the public scoreboard
until someone presses **Regenerate** on the ranking group.

## Goals

1. The "contest running" warning matches the real phase window.
2. Confirm refuses a plan that changed between preview and confirm.
3. One notification per outcome.
4. Removed users disappear from RWS as part of the removal.
5. Singular wording, capped audit log, cheap safety tests, dead handlers gone.

## Non-goals

- A shared test helper for the `add_user` regex pattern: `bulkremove_lists_test.py`
  already re-asserts it by hand with a comment; sharing would add an import
  from `functionaltestframework` into a unit test for one line.
- Per-user times (USACO-like `per_user_time` / `starting_time`) in the
  running warning: false positive only (`# shortcut:` comment).
- An HMAC on the plan fingerprint (the admin is already authorized; it only
  detects stale plans).
- Removing `RemoveUserHandler` / `RemoveParticipationHandler` (GET page +
  DELETE): still routed, the only single-item path.
- The `user_tests` count test in contest scope (display only).
- Fixing the single-item DELETE handlers' `reinitialize` (the UI no longer
  reaches them).

## Decisions

### 1. Running warning (`bulkremove.py`, `plan_removal`)
Replace `start <= now < stop` with the inclusive window of the traditional
case of `compute_actual_phase`:
`p.group.start + p.delay_time <= now <= p.group.stop + p.delay_time +
p.extra_time`. Do not call `Group.phase` (it has no per-participation
times). Works on the participations already loaded (`joinedload` group).
`# shortcut: ignores per_user_time/starting_time, false positive only,
upgrade if USACO contests are used`.

### 2. Re-plan on confirm
Confirm already re-plans but only compares the number of users found. Add
`RemovalPlan.fingerprint`: sha256 hex of
`[(user_id, submissions, user_tests, contests)...] + [running]` over users
sorted by id (include the counts: a new submission during review is data the
admin has not seen; do not include `ignored`). The preview template carries
`<input type="hidden" name="fingerprint" value="{{ plan.fingerprint }}">`.
Confirm refuses when the posted fingerprint differs or is missing,
re-renders the new plan and reuses `PLAN_CHANGED`, reworded to say what can
have changed (users, contests, submissions or the running state) and keeping
"No se borró nada". This replaces the `len(plan.users) != len(set(user_ids))`
check.

### 3. Notifications
Keep the Spanish one (it has the count). Do not touch `try_commit`
(shared by many handlers). Move `self.sql_session.commit()` inside the
existing `try` that wraps the delete, so a commit failure follows the
existing "No se borró nada" path with the driver message; drop the
`if self.try_commit():` wrapper.

### 4. Removed users on RWS
`reinitialize` resets only groups that lost contests, and RWS only merges
what it receives, so removed users linger. Smallest fix: `plan_removal` also
collects `RemovalPlan.groups`, the set of ranking-group names
(`p.contest.ranking_group.name`, `None` for the root) over the affected
participations (add `joinedload(Participation.contest)` with the ranking
group). After the commit, `schedule_rpc(self.service.proxy_service.
regenerate_ranking, group=g)` for each group, **replacing** the
`reinitialize` call in this handler. Check the real signature of
`regenerate_ranking` in `cms/service/ProxyService.py` and how the AWS
"Regenerate" button calls it. The confirmation text changes from "se
recalculará" to say the ranking of the listed groups is emptied and sent
again. The running warning should mention that regenerating a group during
a live contest is heavy. Add a handler test that the right groups get
`regenerate_ranking`.
Also update `docs/importing-users.md`: drop the "press Regenerate after a
removal" advice (and its Spanish `.po`).

### 5. Old POST handlers
Remove `UserListHandler.post`, `_post_sync`, `REMOVE` (`handlers/user.py`
~110-133) and `ContestUsersHandler.post`, `_post_sync`,
`REMOVE_FROM_CONTEST` (`handlers/contestuser.py` ~63, 84-111). Re-grep
templates, static JS, `cmstestsuite` and docs for callers before deleting
(`users.html` and `contest_users.html` post to the new bulk URLs). Drop
imports left unused (check `make_datetime`).

### 6. Wording, log, tests
- Singular in the confirmation: `{% if plan.users|length == 1 %}`.
- Audit log: first 200 usernames then "and N more".
- Tests in `bulkremove_db_test.py`: id-picked non-participant in contest
  scope (ignored, not deleted); the same user given by id and name (listed
  once); `start == now`, `now == stop` and `stop + extra_time` boundaries
  folded into the running test.

## Out of scope for this branch

Another lane (issue #17) edits `handlers/contestuser.py` and
`docs/importing-users.md` (and its `.po`); keep edits there small and local.

## Tasks

1. Running warning + boundary tests.
2. Fingerprint + confirm refusal + tests.
3. Single notification + commit inside the `try` + test.
4. RWS regeneration of affected groups + text + test + docs/`.po`.
5. Remove the old POST handlers.
6. Wording, log cap, remaining tests; docs check.
