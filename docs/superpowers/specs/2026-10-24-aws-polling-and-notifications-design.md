# AWS: no 403 burst from an expired Overview, and question notifications for the open contest's group (issues #74, #59) — Design

**Date:** 2026-10-24
**Status:** Decisions by a read-only panel (javascript-pro) and a tie-breaker
(architect-reviewer); the owner approved the consolidated design.

## #74 — expired session, CrowdSec ban

### Facts
Only POSTs to `/admin/rpc/...` answer 403 on an expired session (`RPCHandler`
raises 403; `tornado.web.authenticated` raises 403 on non-GET). Page GETs and
`GET /notifications` get a 302 to login, which CrowdSec does not count. The
cookie is re-issued every 15 s by `/notifications` and lasts 10 h, so the real
triggers are a wake after a long sleep, logout in another tab, a disabled
admin or a rotated secret — all mid-session; every round must be gated, not
only the first. The Docker config runs exactly one ResourceService shard
(`docker/generate_config.py:199`), so the Resources page is not at risk.

### Decision (overview.html only)
Probe first on every round: `update_statuses` sends `LogService.last_messages`
alone; its callback sends `queue_status`, `workers_status` and (with a contest
selected) `submissions_status`, unless `response["status"] === "not
authorized"` (gate on "not 403", not on "ok": a 503 or a timeout must still let
the rest go, the logs matter most when EvaluationService is down).
`poll_rpc_request` already calls `stop_updating_statuses` before the callback.
At most one 403 per tab per round, always the same path. Keep the proxy
whitelist as defence in depth.

Non-goals: a 401 contract on the RPC endpoint (`web_rpc_test` pins 403), a
redirect to login on 403, a global request queue, a session-age gate in
`poll_rpc_request`, the Resources page.

## #59 — notifications of the open contest and its ranking group

### Decision (tie-breaker: Option B)
- On a contest page (`base.html` has `contest`) pass `contest.id` to
  `CMS.AWSUtils` (a new constructor argument, contest branch only);
  `update_notifications` appends `&contest_id=N` when set. Global pages
  (no contest, including ranking-group pages) send nothing and keep showing
  everything (silently dropping questions there would hide unanswered ones).
- `NotificationsHandler._get_sync` (`cms/server/admin/handlers/main.py`):
  select `Question, Participation.contest_id` joined on Participation (removes
  the per-question lazy load). With `contest_id`: restrict to
  `Participation.contest_id IN (select Contest.id where Contest.id == cid OR
  Contest.ranking_group_id == (scalar subquery: ranking_group_id of cid))`,
  NULL handled in SQL (ungrouped = itself only; a Python `a == b` would match
  every ungrouped contest). Only `contest_id` comes from the page; the group is
  looked up on the server. An unknown or non-integer `contest_id` returns an
  empty list (not a 404: admin-path 404s feed CrowdSec, and a stale tab of a
  deleted contest polls every 15 s).
- The simple service notifications stay global. Desktop notifications follow
  automatically.
- Tell operators the tradeoff (docs): a contest whose page, or whose group's
  page, nobody has open no longer pings; global pages see everything.
- Non-goals: per-admin "contests I follow", a clarification-group model, a
  scope for ranking-group pages, `Question.ignored` handling, the silent
  JSON.parse error of an expired tab.

## Tests
- JS (no runner in the repo): a throwaway node probe in the scratch dir that
  loads the real `aws_utils.js` with a stub `$` and a counting stub of
  `cmsrpc_request`, and `update_statuses` cut from the template: 403 → exactly
  1 call and the interval cleared; ok → 4; 503 → 4; probe pending → 0 new
  calls; the notifications URL with and without contest_id. Report the probe
  output; do not commit it. ESLint is not installed: `node --check`.
- DB-backed (`DatabaseMixin`; `add_contest`, `add_participation`,
  `add_question` exist): `NotificationsHandler._get_sync` with a small stub
  `self`: contests A and B in group G, C in group H, D and E ungrouped:
  contest_id=A → questions of A and B; D → only D, never E; no parameter → all;
  unknown id → empty; the query count does not grow with the number of
  questions.

## Docs (en + es .po by hand)
`docs/docker-deployment.md` reverse-proxy section: AWS polls /admin/rpc/, an
expired session answers 403, the Overview now sends one request first,
CrowdSec's admin-probing scenario counts distinct 403/404 paths, and if bans
persist whitelist POST + 403 + `^/admin/rpc/`. One line in the AWS chapter
about the notification scope.

## Tasks
1. #74 overview.html + probe + docs. 2. #59 server filter + tests. 3. #59 JS +
probe + docs; docs check.
