# Beta Backlog and October 10 Contest Plan — Design Doc

**Date:** 2026-09-29  
**Status:** Approved decomposition; items tracked here

## Context and Constraints

The CMS fork's `beta` branch runs on **2026-10-10 (Saturday)** for a real multi-contest olympiad. Production stacks (`cms-live`, `cms-omips`, `cms-test`) currently run `main`.

**Timeline:**
- **Now (2026-09-29):** backlog triaged; work begins on October 10 package.
- **Friday 2026-10-02:** beta freeze. No new features; only bug fixes for Oct 10.
- **2026-10-03 to 2026-10-09:** testing and rehearsal on `cms-test` (multi-contest deployment).
- **Saturday 2026-10-10:** contest day. Beta goes live with MC-1 (multi-contest) and MC-2 minimal (ranking visibility per group). Staff runs live on beta; production stacks stay on `main`.

**October 10 requirements:**
- Multi-contest mode (MC-1): one stack, multiple contests, per-group rankings.
- MC-2 minimal: hide/unhide group scoreboard with staff password (already designed).
- Ranking reliability: no batch loss, no broken contests, timeouts on remote calls.
- Real CI validation: GitHub Actions with isolate before Friday.
- Live-contest hardening: admission control, event timeouts, loop blocking measurement.
- Quick wins: cache headers, type decorators, auth cleanup, log warnings, Tornado >= 6.5.8.

---

## Decisions Taken

All decisions below were made by the user on 2026-09-29 in the planning
conversation, on the options and recommendations presented.

| Decision | Detail |
|---|---|
| Production branch | All server stacks (cms-live, cms-omips, cms-test) run `main` today. "Urgent" = what affects `main` or blocks validating `beta`. |
| Deadline | A real contest day runs on `beta` on 2026-10-10 with MC-1 (multi-contest) and MC-2 (hide ranking + staff view). Freeze `beta` on Friday 2026-10-02; test 2026-10-03..09. |
| Decomposition | SP1-SP15 in four phases approved; re-prioritized into the October 10 package and "after October 10" (below). |
| SP1 send-failure policy (D1-B) | Requeue unsent data on transport errors and 5xx, with backoff; on 4xx drop only that (group, type) pair with a Regenerate hint; `requests` timeouts. |
| SP8 B2 (D2-B) | Imported contests arrive inactive unless restoring with `--drop`. |
| SP8 B1 + N1-B (D3-B) | The sweeper retries broken contests; reinitialize restores the group mapping on failure. |
| CI runners | GitHub-hosted runners once the user enables Actions; an optional self-hosted runner on a dedicated machine, push-only, label `cms-ci`, gated by the repository variable `SELF_HOSTED_RUNNER=true`. |
| SP2 scope | codecov permissions fix, overridable timeouts, functional-test timeouts, harness HTTP timeouts, config-generator tests in CI, harness fix cherry-picked to `main`, a side-by-side Python 3.12 venv. |
| MC-2 scope | Per group: hide the public scoreboard (the public sees a notice, data endpoints closed) and a staff view with a per-group password configured in AWS. Freezing and schedules are out of scope. |
| MC-2 approach | AWS -> ProxyService -> RWS, staff log in on the same URL with a signed cookie; chosen over HTTP Basic in RWS (browser popup for every visitor) and hand-configured nginx (not configurable in AWS). See `2026-09-29-mc2-ranking-visibility-design.md`. |
| CWS load spike | Approved to decide, with numbers, whether 2.5c or several CWS instances are needed before October 10. A14 is implemented before October 10 only if the spike shows the pool cliff. |

Not decided yet: see "Open Decisions Pending" (e.g. D4-B for the B11 log
noise).

---

## Work Packages by Phase

All items traced to source file `backlog-draft.md` (§ letter). Status marks: ✓ done (commit hash noted), ◐ in progress, ◻ planned.

### October 10 Package (freeze Friday 2026-10-02)

| Package | Items | Effort | Oct 10 | Status | Notes |
|---|---|---|---|---|---|
| **MC-2 minimal** | Visibility design | — | Must | ✓ Done | Spec: 2026-09-29-mc2-ranking-visibility-design.md |
| **SP1: Ranking Reliability** | B3, B4a, N2-B, B2, B1, N1-B | ~1.5-2d | Must | ◐ In progress | Transport/5xx requeue, 4xx drop+hint, inactive on import, sweeper retry. Needs tests. |
| **SP2: Real CI (isolate)** | N1-D, D1, D3, D9, D7, C12, N3-D | ~1d + user | Must | ◻ Planned | Actions enable (user click), codecov host fix (both branches), cherry-pick b6f37b07, upstream sync, .venv rebuild. |
| **SP3: Deployment & Checks** | Server checks, deployment plan, operator docs | ~0.5d | Must | ◻ Planned | MC-2 ops guide in docs/multi-contest.md; cms-test rehearsal plan (2026-10-03 to 2026-10-09). |
| **SP4: CWS Load Spike** | Measure beta vs main, A13 instrumented | ~0.5d | Should | ◻ Planned | Stress test 2.5c vs main latency. < 1.25x → land 2.5c alone. >= 1.25x → multi-CWS or full A14. Gates A14/instance decision. |
| **SP5: Live-Contest Hardening** | A13 (measure), A14, A15+N2-A+N7-A, V1, N4-A | ~1.5-2d | Should | ◻ Planned | Admission semaphore, log backpressure, RPC fire-and-forget timeouts, WebService shutdown order. A14 gates 2.5c. |
| **Q1: Code Quick Wins** | B7, B5, B6a+b, B8+B9d+B10b, B9b, B10h, B11, N8-A, A12 | ~1d | Should | ◻ Planned | Static cache, cache_ok, registry fix, auth cleanup, shutdown logs, xsrf error page, service log level, executor cleanup. |
| **Q2: Test Quick Wins** | C3, C9, C6, C7, C4, N2-C, N5-C, R-M2, R-N1/N2/N3 | ~1d | Should | ◻ Planned | _settle mixin, lock try/finally, handler/renderer tests, template smoke test, CWS error paths, nits. |
| **Q3: Doc Quick Wins** | E1/N1-A, E2, E4, E5, E6, E7a/b, E8, E9, E10 | ~0.5d | Should | ◻ Planned | README Worker, async "infrastructure only", CLAUDE.md beta architecture, specs 2.2/2.3/2.5a resolved notes, backdoor skip docs. |
| **Q4: Infra Quick Wins** | N5-D, N8-D, Tornado >= 6.5.8 (N2-D), optional D2 | ~0.5d | Should | ◻ Planned | .dockerignore (.worktrees, .venv, .pytest_cache), OCI image revision label, Tornado security bump. |

**Committed in Oct 10 package to date (HEAD 1b5a149d):**
- ✓ CWS RPC handler schedule_rpc moved to CommonRequestHandler (4d431e28)
- ✓ Every executor has `_settle` helper (b9d3926e)
- ✓ Output-only harness fixed for RWS (b6f37b07)
- ✓ Log lock-type test for gevent (db2675e4)
- ✓ CWS RPC regression fixed (1b5a149d)

---

### Post-October 10 (after freeze)

| Package | Items | Phase | After | Notes |
|---|---|---|---|---|
| **S1: 2.6a Gevent-Free Asyncio Runtime** | A2, A3, A4, A6 step 0, triggeredservice delete, N5-A, N8-A, N1-A | Async modernization | 2026-10-10 | ½-1d, parallel to 2.5c. Remove gevent from init scripts; move make_psycopg_green to cmsWorker only; backdoor skip. |
| **S2: 2.6b Single Pytest Group** | A5, A16/C10 asyncio migration | Async modernization | S1 done | ½d + CI. Drop patch_all from init scripts + 14 cmscontrib modules. Merge gevent and asyncio test runs. |
| **S3: 2.7 Hardening** | A14, N4-A, A13, A15+N2-A+N7-A, N3-A, N4-C | Performance hardening | S1+S2 done | 2-3.5d. Admission semaphore, session close off loop, measure + fix loop blocking, fire-and-forget timeouts, test concurrent reinitialize. A14 prerequisite for 2.5c to land on main. |
| **S4: Test Coverage** | C1, C2, N1-C, N3-C, C8 L1+manual checklist, N6-C | Test infrastructure | S2 done | 3-4d. ES failure paths, ProxyService group transitions, AWS handlers, ES contest_id filter, two-phase E2E, ScoringService sleeps. |
| **S5: 2.6c Worker → AsyncService** | A1, N6-A, final A6 deletion | Async modernization | After 2.5c/MC-2/MC-3 | 2-4d, high risk. SIGTERM semantics decision (D1-A). Stress test 30-60 min. PrometheusExporter off old client. |
| **S6: Operations & Deployment** | D5, N9-D, N3-D, D8, D6, D4, N6-D | Deployment plan | Ongoing | Multi-stack → 1 stack (D6), landing criteria (D8), pins (D4), isolate versions, Node EOL, fs-cache policy. |
| **S7: Optional / Long-Term** | A9a psycopg2→3, D2-A RWS gevent, D3-A async DB fate, C13 phases 1-3, C8 L2/L3 | Research | 2027 | psycopg3 sync (3-5d, low priority); RWS stays gevent; async DB dormant; functional suite full phases; two-phase full E2E. |

---

## Full Item Register

**Legend:** ID — Aliases (if merged from duplicates) | Description | Evidence (file:line) | Urgency (Alta/Media/Baja) | Effort (S/M/L) | Verdict (do now / SPx / not worth) | Reason.

### E — Documentation

| ID | Aliases | Description | Evidence | Urgency | Effort | Verdict | Reason |
|---|---|---|---|---|---|---|
| **E1** | N1-A | README:63 "Service migration" false; Worker still gevent (Worker.py:44) | README line 63 | Alta | S | Q3 | README is discoverable; easy reword. |
| **E2** | — | README:62 "Async DB access: Done" misleading; no actual consumers yet | README line 62 | Media | S | Q3 | Clarify "infrastructure only"; unblock readers. |
| **E3** | — | README:68 note about CWS RPC regression (1b5a149d) | README line 68 | Baja | S | Not worth | Regression fixed upstream; old news once commit is pushed. |
| **E4** | — | README:68 "Each stage has a spec" but 2.4 has none | README line 68 | Baja | S | Q3 | Consistency; minor oversight. |
| **E5** | — | CLAUDE.md:82,90,92 describes cms/io as gevent; wrong for beta (asyncio services; Worker/cmsranking/cmscontrib still gevent) | CLAUDE.md lines 82,90,92 | Media | S/M | Q3 | Architecture doc must match reality; beta is live soon. |
| **E6** | — | Specs 2.2 (319-345), 2.3 (208-214), 2.5a (119) mention gevent-lock hazard; resolved by 16408419/da657b70 | Spec files, lines noted | Baja | S | Q3 | Append "Resolved by [commits]" notes; preserve history. |
| **E7a** | — | Spec 2.5a:72-76 "no service uses gevent after 2.5" false (Worker still gevent) | 2.5a spec lines 72-76 | Alta | S | Q3 | Incorrect claim; misleads readers about 2.6a scope. |
| **E7b** | — | Spec 2.5a:41-42 "Worker out of scope of whole modernization" vs user ruling "out of scope of 2.4" | 2.5a spec lines 41-42 | Alta | S | Q3 | Clarify scope boundary; Worker is in 2.6c. |
| **E8** | — | Spec 2.5a:66,203 xheaders/ProxyFix + RPCHandler base mismatch (code: resolve_remote_ip; RequestHandler at web_rpc.py:43) | 2.5a spec lines 66,203; web_rpc.py line 43 | Baja | S | Q3 | Code and spec inconsistency; minor but needs clarity. |
| **E9** | — | Spec 2.5b:127-131 "no new unit tests" false (admin_session_test.py exists); plan checkboxes unmarked | 2.5b spec lines 127-131 | Media | S | Q3 | Spec inaccuracy; update facts and checkboxes. |
| **E10** | — | Two-phase spec:130 Spanish string "Skipped after screening phase failure" vs code N_() marker (evaluation.py:81) | evaluation.py line 81; spec line 130 | Baja | S | Q3 | Localization consistency; minor. |
| **E11** | — | docs/multi-contest.md link to MC-2 visibility | docs/multi-contest.md | Media | S | Planned after MC-2 | Avoid broken links; MC-2 spec exists; add link when code lands. |
| **E12** | — | docs/migrating-to-multi-contest.md:68-69 imported dumps carry `active` column; doc depends on B2 decision (SP1) | docs/migrating-to-multi-contest.md lines 68-69 | Media | S | SP1 | Blocked by B2 decision; update when SP1 lands. |
| **E13** | — | Optional 2.4 as-built note in architecture docs | Docs | Baja | M | Not worth | Nice-to-have; low priority. |

### B — Production Bugs & Code Minors

| ID | Aliases | Description | Evidence | Urgency | Effort | Verdict | Reason |
|---|---|---|---|---|---|---|
| **B1** | — | ProxyService sweeper never retries broken contests; executor error → permanent stall | ProxyService.py:552-560 | Media | S | SP1 | Blocks marathon contests; real risk. Fix: sweeper retry + N1-B reinitialize. |
| **B2** | — | Contest.active travels in dumps; imported contest visible immediately in ALL stack | DumpImporter, DumpExporter:359 | Alta | S | SP1 | Contests go live unexpectedly; critical UX bug. Decision D2: inactive unless --drop. |
| **B3** | — | ProxyExecutor: first CannotSendError aborts batch for ALL groups; ids marked sent → loss | ProxyService.py:287-319,649,681 | Alta | M | SP1 | Data loss across groups; worst-case in MC-1. Requeue transport/5xx, drop (group,type) on 4xx. |
| **B4a** | — | Missing Regenerate hint when ProxyExecutor drops a (group, type) batch | ProxyService, tests | Alta | S | SP1 | Operator guidance on 4xx drop; paired with B3. |
| **B4b** | — | Orphan RWS namespace docs (D5 decision: document in ops guide) | RWS | Baja | S | Q3 | Optional cleanup; only matters if D5 goes ahead. |
| **B5** | — | 5 TypeDecorators missing cache_ok=True; SQLAlchemy 2.0 issues warning | db/ models | Media | S | Q1 | Restores statement caching; minor perf + cleaner tests. |
| **B6a** | — | base.py deprecated as_declarative import chain | cms/db/base.py | Baja | S | Q1 | Cleanup; no behavioral change. |
| **B6b** | — | base.py deprecated registry(..., as_declarative) pattern | cms/db/base.py | Baja | S | Q1 | Modernize to registry(..., constructor=None).as_declarative_base() |
| **B7** | — | Static cache override lost; ?h= query param caching only works 1 request | web_service.py:40 | Media | S | Q1 | Fixes static file cache; test HTTP headers. |
| **B8** | — | Delete dead auth_handler/auth_middleware hooks; stale AWSAuthMiddleware mentions | util.py:290-293, web_rpc.py:114-121 | Baja | S | Q1 | Dead code cleanup; merged with B9d, B10b. |
| **B9a** | — | Connection keep-alive edge case (pre-existing, low priority) | — | Baja | M | Not worth | No evidence of real issue; upstream problem. |
| **B9b** | — | Keep-alive shutdown WARNING on every close; should be INFO + reword | web_service.py:236-256 | Baja | S | Q1 | Log noise reduction; easy fix. |
| **B9c** | — | Minor edge case (pre-existing) | — | Baja | S | Not worth | No reproduction; low priority. |
| **B9d** | — | (Merged with B8) | — | — | — | Q1 | Cleanup dead auth code. |
| **B9e** | — | Minor edge case (pre-existing) | — | Baja | S | Not worth | No reproduction; low priority. |
| **B9f** | — | (Covered by B8) | — | — | — | Q1 | Dead auth hook. |
| **B9g** | — | (Covered by B8) | — | — | — | Q1 | Dead auth hook. |
| **B10a** | — | write_error DB on loop blocks RPC (pre-existing) | — | Media | M | Not worth (defer 2.5c) | Low priority; measured stable. |
| **B10b** | — | (Merged with B8) | — | — | — | Q1 | Dead auth gates. |
| **B10c-e** | — | Various edge cases (doc only, pre-existing) | — | Baja | S | Not worth | Pre-existing; doc-only fixes. |
| **B10f** | — | Minor upstream issue | — | Baja | S | Not worth | Upstream responsibility. |
| **B10g** | — | Minor upstream issue | — | Baja | S | Not worth | Upstream responsibility. |
| **B10h** | — | XSRF 403 error page: url/static_url_helper missing in write_error (TypeError) | web_service.py, write_error | Baja | S | Q1 | Easy fix: set in __init__; test 403 rendering. |
| **B10i** | — | Edge case (pre-existing) | — | Baja | S | Not worth | No reproduction. |
| **B11** | — (R-M1) | schedule_rpc WARNING from fake client (ProxyService not configured, restarting) | handlers, util.py | Baja | S | Q1 | Log noise; Decision D4-B: DEBUG for ServiceNotConfiguredError. |
| **B12a** | — | Redundant lookups in ES loop (possible N+1) | EvaluationService.py:929-935 | Baja | M | Not worth | Measure first; possible N+1 but unproven impact. |
| **B12b-e** | — | Minor inefficiencies (pre-existing, low impact) | — | Baja | S | Not worth | No evidence of real issue. |
| **B13a** | — | filename None guard in CWSRequests loops | CWSRequests | Baja | S | Q1 | Optional; edge case safety. |
| **B13b-c** | — | Minor pre-existing issues | — | Baja | S | Not worth | No reproduction. |
| **B14a-b** | — | Pre-existing issues | — | Baja | S | Not worth | No evidence. |
| **B15** | — | Minor edge case (pre-existing) | — | Baja | S | Not worth | No reproduction. |

### C — Test Gaps & Test Infrastructure

| ID | Aliases | Description | Evidence | Urgency | Effort | Verdict | Reason |
|---|---|---|---|---|---|---|
| **C1** | — | ES failure-path tests (RPCError→action_finished, real sweeper via _sweep(), cross-thread) | cmstestsuite/unit_tests/service | Media | M | S4 | Important for 2.5c reliability. |
| **C2** | — | ProxyService group transitions untested (no-group↔group, delete, rename, legacy reinitialize, dataset_updated) | proxyservice_test.py | Media | M | S4 | Critical for MC-1; weak assertions. |
| **C3** | — | ProxyService_test/ProxyServiceTest silent _wait_until_put → shared _settle mixin + loud waits | cmstestsuite/unit_tests | Media | S | Q2 | Measured flake (1-5 / 400 on 1-2 CPU); easy fix. |
| **C4** | — | FSObject get_all/delete_all regression test (API has 0 callers; Decision D6: test vs delete) | cmstestsuite/unit_tests | Media | S | Q2 | Low ROI; recommend test to preserve API. |
| **C5** | — | SQLAlchemy deprecation warnings → errors (step 1-3: cache_ok + as_declarative + utcnow/utcfromtimestamp) | constraints.txt pins | Media | S | Q1 | Prep for clean CI. Step 4 (warnings as errors) is phase 2 (decision D1-C). |
| **C5.1** | (B5) | cache_ok TypeDecorators | — | — | — | Q1 | Merged with B5. |
| **C6** | — | RPCHandler resolve_remote_ip tests (3 cases, no DB) | cmstestsuite/unit_tests | Media | S | Q2 | Improves handler test coverage. |
| **C7** | — | render() in executor + write_error success/500 tests (Jinja DictLoader, no DB) | cmstestsuite/unit_tests | Media | S | Q2 | Renderer test coverage. |
| **C8** | — | two-phase gate never verified E2E; strategy L1/L2/L3 (Decision D2-C: L1 now + manual checklist) | cmstestsuite/functional | Alta (if flag enabled) | L | S4 L1 now | Gated by CMS_TWO_PHASE_EVALUATION enablement. L1 (unit E2E with FakeWorker) is a prerequisite for 2026-10-10 if two-phase is on. Manual checklist + L2 (functional) after. |
| **C9** | — | log_test try/finally around held lock | cmstestsuite/unit_tests | Media | S | Q2 | Robustness; easy fix. |
| **C10** | (A16) | Migrate twophase_evaluationservice_test + twophase_reenqueue_test to asyncio group | cmstestsuite/unit_tests | Media | S | Q2 | Database validation needed first (C12). |
| **C11** | — | harness `active` only matters for -c ALL run; skip until full CI | harness | Baja | S | Not worth | Low ROI; revisit after full CI green. |
| **C12** | (N4-D) | Local .venv Python 3.14.7 (vs 3.12), Babel 2.18 (vs 2.12.1), missing bs4/coverage/pytest-cov/prometheus_client/telegram, PG 16 vs CI 15, no cmsInitDB/cmsRunFunctionalTests | .venv, constraints.txt | Alta | S | SP2 | Validation broken locally; unblocks all local test runs. Rebuild .venv312 + PG15 local. |
| **C13** | — | Functional suite strategy (no global timeout, network calls unguarded, abort on TestException) | cmstestsuite/functional | Media | L | S4 phases 1-3 | Phase 0 (now): global timeout + network timeouts + monotonic deadlines (S). Phase 1: PR subset + nightly full. Phase 2: accept-only mode (AWS+CWS+DB+RPC, no Worker). Phase 3: ALL/groups/two-phase. Decision D4-C (informative until first green). |

### D — CI, Docker, Infrastructure, Environment

| ID | Aliases | Description | Evidence | Urgency | Effort | Verdict | Reason |
|---|---|---|---|---|---|---|
| **D1** | — | GitHub Actions enable (fork; upstream has 5 green commits) | github.com/AresLOLXD/cms/actions | Alta | — | SP2 | User: 1 click to enable. Gated by N1-D fix (sudoers codecov). |
| **D2** | — | Debian bookworm matrix stable; optional: fail-fast: false | _cms-test-internal.sh | Media | S | Not worth (keep as-is) | Upstream green; fail-fast helps debugging but not critical. |
| **D3** | — | _cms-test-internal.sh knobs: UNIT_TIMEOUT/FUNC_TIMEOUT, ulimit -c 0, functional under timeout --foreground | docker/_cms-test-internal.sh | Alta | S | SP2 | Enables clean shutdown + functional timeout in CI. Paired with C13 phase 0. |
| **D4** | — | Pins: CMS_LOADER_VERSION by tag (why was v1.0.6 pin reverted?); cms_rekarel by SHA; single image / no separate db-init / log rotation | docker | Baja | S | Not worth (defer decision D4-D pins) | User decision pending on all three (Loader behavior, single image, log rotation). Measure first. |
| **D5** | — | fs-cache-shared (6.8 GB): delete digest files only, prefer -mtime +14; do nothing unless disk > 80% | Production server | Baja | S | S6 | Maintenance task; user runs on server (has commands). |
| **D6** | — | 3 stacks (cms-live, cms-omips, cms-test) → 1 multi-contest stack (Decision D6: go/no-go after D1+D8) | Deployment | Media | L | S6 after Oct 10 | Worth it but only after landing criteria (D8) met + cms-test rehearsal (2026-10-03 to -10-09). |
| **D7** | — | Upstream sync: 5 new commits (italy_yaml ×4, c76c40d5 /dev/shm isolate rule), merge-tree clean, 2 trivial conflicts (README, pyproject) | upstream/main | Media | S | SP2 | Schedule: merge main into beta (after N1-D + D9). Weekly cadence recommended. |
| **D8** | — | Landing criteria (Decision D8: what lands first — MC-1 alone on main, or wait for MC-1+MC-2?) | Deployment | Media | M | S6 | Proposal: 3 green runs on exact commit, >=7d soak in cms-test, stress <=1.25x main, db-init + rollback rehearsal, Tornado >=6.5.8, docs updated, merge main first, rollback tag. |
| **D9** | — | Cherry-pick b6f37b07 (Karel/.txt harness fix) to main | git log | Media | S | SP2 | Generic fix; both branches need it. Do after N1-D, before D7 merge-tree. |
| **D10** | — | No other generic ports needed to main besides b6f37b07 and N1-D | Git diff main..beta | Baja | — | Not worth | b6f37b07 + N1-D are sufficient; asyncio-only fixes stay on beta. |
| **N1-D** | — | GitHub Actions first run fails: `sudo chown codecov` → sudoers only allows /usr/bin/isolate ("Task/Future exception was never retrieved"); codecov/ created root:root 755 | github workflow, Dockerfile:121 | Alta | S | SP2 | Host-side fix: `mkdir -p codecov && chmod 777 codecov` in workflow + docker/cms-test.sh. Apply both branches. QW + D9 together. |
| **N2-D** | — | Tornado 6.5.5 has 3 server advisories fixed in 6.5.8 | constraints.txt | Media | S | Q4 | Security bump; needs full suite green. |
| **N3-D** | — | isolate unpinned + cached apt; c76c40d5 requires isolate >= 2.6 (/dev/shm per-sandbox tmpfs) else side channel re-opens | Dockerfile | Alta (after D7) | S | SP2 (check), S6 (rebuild) | Verify versions on server; rebuild --no-cache if < 2.6. User ops task (C-N3 commands). |
| **N5-D** | — | .dockerignore misses .worktrees/ (501 MB!), nested **/.venv/, .agents/, .pytest_cache/ → images bust cache | .dockerignore | Media | S | Q4 | Speeds CI; easy fix. |
| **N6-D** | — | Node 20 EOL (2026-04-30) via setup_20.x → Node 22/24, cold build test needed | docker | Media | S/M | S6 | Migration work; deferred to S6. |
| **N8-D** | — | No git SHA label in images (ARG GIT_SHA + OCI label org.opencontainers.image.revision) | Dockerfile | Baja | S | Q4 | Improves traceability; easy metadata addition. |
| **N9-D** | — | Prod compose publishes AWS/Loader ports on all interfaces (docker bypasses ufw) | docker-compose | Media | S | S6 | Verify on server (C-N9 command); may need binding to localhost. User ops task. |
| **N10-D** | — | CI PG15 vs prod PG17 schema drift | CI config | Baja | M | Not worth (optional phase 2) | Nice-to-have; alignment lower priority than basic functionality. |

### A — Gevent/Async Leftovers

| ID | Aliases | Description | Evidence | Urgency | Effort | Verdict | Reason |
|---|---|---|---|---|---|---|
| **A1** | — | Worker → AsyncService: feasible (prototype +9/9 tests); SIGTERM semantics decision (D1-A: daemon thread/immediate exit). High risk for judging path | cmsranking_test.py, prototypes | Media | 2-4d | S5 after Oct 10 | Defer to post-MC-2/2.5c soak; validate with 30-60m stress test. Decision D1-A still pending (SIGTERM behavior). |
| **A2** | — | make_psycopg_green unconditional → move to scripts/cmsWorker; drop psycopg_green_executor_test | setup.py | Media | S | S1 | Conditional patch; only Worker needs it. Parallel to 2.5c. |
| **A3** | — | gevent.sleep(0)/gevent.socket in filecacher.py/util.py → time.sleep(0)/socket; `import gevent` in util.py is why every CMS process imports gevent | cms/util.py | Media | S | S1 | Reduces gevent footprint; parallel to 2.5c. |
| **A4** | — | Backdoor under asyncio accepts but never answers (verified) → warn+skip (Decision D5-A: ask if used in prod) | specs/2.2, cms/io | Media | S | S1 | Spec promised skip; needs decision on prod usage. Docs/Internals.rst update. |
| **A5** | — | patch_all in cmsInitDB/DropDB/SetupDB + 14 cmscontrib modules; can drop (only cmsWorker, cmsRankingWebServer, PrometheusExporter need gevent) | cmscontrib | Media | M | S2 | Merge test groups (cmsranking coexists with asyncio in CI: verified). Unblocks unified pytest run. |
| **A6** | — | Old gevent stack (2040 LOC + 935 LOC tests) deletable after A1/N6-A; step 0: move shared vocabulary (rpc_method, RPCError, PRIORITY_*) to cms/io/common.py | cms/io | Media | M/L | S1 step 0; S5 final | Refactor shared RPC vocab once, then delete old stack. |
| **A7** | — | Adopt AsyncSessionGen: MissingGreenlet risk (69 lazy relationships); bottleneck is PG pool | SQLAlchemy | Baja | M | Not worth | Keep dormant+lazy (Decision D3-A); delete in 1 quarter if A9a not done. |
| **A8** | — | Async LargeObject/FileCacher: psycopg3 has no LO API; most users sync by design | psycopg | Baja | L | Not worth | Low ROI; upstream gap. |
| **A9a** | — | psycopg2 → psycopg3 sync: optional sub-project (3-5d, URL compat shim, DB validation) | cmscommon/db | Baja | L | S7 optional | Long-term; decide in 1 quarter. Decision D4-A: defer. |
| **A9b** | — | Remove sync Session: does not pair with async layer | SQLAlchemy | Baja | L | Not worth | Mismatched scope; keep sync. |
| **A10** | — | 2PC in async: not necessary for single-PG deployment | SQLAlchemy | Baja | L | Not worth | Out of scope. |
| **A11** | — | Eager async engine: only make lazy if layer kept; premature | SQLAlchemy | Baja | S | Not worth | Unneeded; lazy strategy proven stable. |
| **A12** | — | Premise wrong: gevent had no shutdown grace; no data loss (executor thread finishes, sweepers commit) → fix misleading comment async_service.py:461 | async_service.py:461 | Baja | S | Q1 | Accuracy fix; optional drain ES result_cache on exit. |
| **A13** | (B-N3, C-N8) | ES loop blocked while executor holds post_finish_lock: lag == hold time (0.3/0.8/2.0s verified). Gate: measure under stress before promoting beta | cmstestsuite, EvaluationService.py | Media | S measure, M fix | SP4 (measure), S3 (fix) | Measured with real DB code. Decision D7-A: tiny _pending_lock vs serial 1-thread executor. Must measure spike first. |
| **A14** | (C-N4) | executor (min(32,cpu+4)) vs DB pool 15 cliff: TimeoutError 500s + 60s stalls (verified). Gate for 2.5c. Fix: asyncio.Semaphore admission in CommonRequestHandler.prepare | executor config, CommonRequestHandler | Media | M | S3 + SP5 | A14 is a **prerequisite for 2.5c landing on main**. Admission semaphore + session.close() off loop (N4-A). Decision D6-A (a) recommended. |
| **A15** | (C-N2-A) | execute_rpc without timeout → stuck peer accumulates 2.5 KB/call; log backpressure (N2-A) → 1 RPC task/record cap; WebService shutdown (N3-A) → drain HTTP after RPC | async_rpc.py | Media | S each | S3 (A15, N2-A, N3-A) | Fire-and-forget needs wait_for(30s), try/finally pop(), cap 1000 pending logs. Decision D8-A values. |
| **A16** | (C10) | two twophase tests to asyncio group; remove patch_all, add ServiceLoggingIsolationMixin, empty GEVENT_SERVICE_FILES | cmstestsuite/unit_tests/service | Media | S | Q2 | Database validation needed (C12). Unblocks merged test run (S2). |
| **N1-A** | (E1) | README + Internals.rst backdoor notes | — | — | — | Q3 | Merged with E1. |
| **N5-A** | (B8) | Delete dead auth hook | — | — | — | Q1 | Merged with B8. |
| **N4-A** | — | sql_session.close() (ROLLBACK) on loop thread each request → close in executor | CommonRequestHandler, services | Media | S | S3 | Paired with A14 admission semaphore. Prevents hung requests from holding DB connections. |
| **N6-A** | — | PrometheusExporter/AddSubmission off old client (pre-2.5c); delete after A1 | cmscontrib, cmsranking | Media | S | S5 final | Cleanup after 2.5c lands. Keep old stack until Worker is asyncio. |
| **N7-A** | (V1) | execute_rpc KeyError after peer drops → pop(id_, None); "Task/Future exception was never retrieved" in RService/Checker | async_rpc.py | Media | S | Q1 (V1) | Fixes exception spam at shutdown. QW + tests. |
| **N8-A** | — | exit() before loop exists is no-op (LogService.__init__ calls exit() on dir failure) → service keeps running | LogService | Media | S | Q1 | Correctness fix; exit cleanly. |

### V — Container Validation (docker-expert, from 1b5a149d validation)

| ID | Aliases | Description | Evidence | Urgency | Effort | Verdict | Reason |
|---|---|---|---|---|---|---|
| **V1** | (N7-A) | async_rpc.execute_rpc deletes pending request, drops KeyError when peer disconnects → pop(id_, None) | async_rpc.py | Media | S | Q1 (N7-A) | Fixes log spam. QW; same as N7-A. |
| **V2** | — | Functional-test config mismatch: cms-testdb.toml ranking URL `usern4me:passw0rd@localhost:8890` vs entrypoint-generated cms_ranking.toml (user `rws`, empty password) | docker, cms-testdb.toml | Media | S | SP2 C13 | ProxyService gets 401 from RWS in functional; test infra bug. Pair with C13 phase 0. |
| **V3** | — | Harness creates tasks via AWS before ProxyService starts → 80 RPC warnings ("Write failed."). Noise; pre-fix. | docker harness | Baja | — | Not worth (measure later) | Low priority ordering; pre-existing. May resolve after N1-D / log level fixes. |

### R — Code Review Findings (code-reviewer, commit 1b6611f0..1b5a149d)

| ID | Aliases | Description | Evidence | Urgency | Effort | Verdict | Reason |
|---|---|---|---|---|---|---|
| **R-M1** | (B11) | schedule_rpc logs WARNING for fake client scenario (ProxyService not configured, restarting) | handlers, util.py | Baja | S | Q1 (B11) | Minor; Decision D4-B: DEBUG for ServiceNotConfiguredError. |
| **R-M2** | — | handlers_schedule_rpc_test.py covers only happy paths; add error cases (UnacceptableSubmission, UserTest, Token, Testing→await_count 0) + commit-before-RPC ordering | tests | Media | S | Q2 | Important for 2.5c reliability. |
| **R-N1** | — | basehandler_schedule_rpc_test.py:18,44 stale wording (method now on CommonRequestHandler) | tests | Baja | S | Q2 | Nit; easy fix. |
| **R-N2** | — | cms/server/util.py schedule_rpc lacks PEP 484 type annotations | cms/server/util.py | Baja | S | Q2 | Consistency; easy fix. |
| **R-N3** | — | cws_submit_language_test mutates shared Tests.ALL_TESTS (harmless today) | tests | Baja | S | Q2 | Latent issue; test isolation. Easy fix. |

---

## Not Worth Doing (with Reasons)

These are verified as low-priority, pre-existing, blocked by decisions, or upstream responsibility:

**A:** A7, A8, A9b, A10, A11 (as problem), A4 (port only).

**B:** B9a, B9c, B9e, B10c/d/e (doc only), B10f, B10g, B10i, B12b-e, B13b/c, B14a/b, B15.

**C:** C11 (skip until full CI green), global -W error (wait for clean run), C5 phase 4 (post-cleanup).

**D:** D1(c) rootless cgroups, D2 fail-fast (keep as-is), D4 single image / db-init / pins decision pending, N7-D codecov token, N10-D PG17 CI, porting asyncio-only fixes to main, Tornado bump on main (diffs too large).

**E:** E3, E11 (blocked by MC-2), E13 (optional).

---

## Open Decisions Pending

| ID | Decision | Options | Recommended | Notes |
|---|---|---|---|---|
| **D1-A** | Worker SIGTERM semantics | (a) daemon thread / immediate exit; (b) graceful shutdown (risky); (c) measure first | (c) daemon thread/immediate exit | Defer to S5 after 2.5c/MC-2/MC-3 soak. High-risk change. |
| **D2-A** | RWS stays gevent or async? | (a) RWS async (redesign); (b) keep gevent (current) | (b) Keep gevent | Backward-compatible; Worker is higher priority. |
| **D3-A** | Async DB layer fate | (a) delete AsyncSessionGen (one quarter); (b) keep dormant+lazy (current); (c) redesign for eager | (b) Keep dormant+lazy | MissingGreenlet risk; lazy has proven stable. |
| **D4-A** | psycopg2 → psycopg3 sync migration | (a) do in ~1 quarter (3-5d); (b) defer; (c) never | (b) Defer | Low priority; upstream gap on LargeObject API. Decide in Q4 2026. |
| **D5-A** | Backdoor under asyncio | (a) skip (spec promise); (b) implement async version; (c) remove entirely | (a) Skip; ask prod usage first | Spec promised skip. Decision D5-A: ask if anyone uses it. |
| **D6-A** | Executor vs DB pool cliff (A14) | (a) admission semaphore (recommended); (b) reduce executor size; (c) increase pool | (a) Admission semaphore | Gates 2.5c landing. A14 + N4-A required before main. |
| **D7-A** | ES loop blocking (A13) | (a) tiny _pending_lock; (b) serial 1-thread executor; (c) measure first | (c) Measure under stress | Gate before promoting beta. Spike test (SP4) first. |
| **D8-A** | Fire-and-forget timeout values | (a) wait_for(30s), log cap 1000, RPC cap 2.5KB/call; (b) aggressive values; (c) measured | (a) Recommended baseline | See A15+N2-A+N3-A. Refine after load test. |
| **D1-C** | SQLAlchemy deprecation warnings as errors | (a) targeted filterwarnings error::SAWarning; (b) global -W error; (c) skip for now | (a) Targeted after C5.1-3 clean | Wait for full CI green; no RemovedIn20Warning in SA 2.0.54. |
| **D2-C** | Two-phase E2E gate level | (a) L1 unit E2E now + manual checklist; (b) L2 functional pass + flag on; (c) L3 dedicated task | (a) L1 now + manual | If CMS_TWO_PHASE_EVALUATION is on for 2026-10-10, L1 must pass. L2 after 2.5c lands. L3 optional. |
| **D5-C** | ProxyService concurrent reinitialize | (a) lock + test; (b) measure first; (c) redesign | (a) Lock + test (S4 N4-C) | Currently untested race; low priority post-Oct-10. |
| **D4-B** | B11 log noise (schedule_rpc WARNING) | (a) ServiceNotConfiguredError logged DEBUG (recommended); (b) all RPCError DEBUG; (c) no-op fake | (a) ServiceNotConfiguredError @ DEBUG | Fork Docker always configures ProxyService; applies only to scenarios A/B. |
| **D2-B** | Contest.active on import | (a) always inactive (breaks restore); (b) inactive unless --drop (recommended); (c) omit in exporter | (b) Inactive unless --drop | Prevents surprise activation. Needs E12 doc. |
| **D3-B** | Broken contests sweeper | (a) sweeper retry + N1-B (recommended); (b) exclude SQLAlchemyError; (c) both | (a) Sweeper retry + reinitialize | Unblocks marathon contests. N1-B: reinitialize restores mapping. |
| **D1-B** | ProxyExecutor send failure policy | (a) requeue unsent on transport/5xx, drop (group,type) on 4xx + hint (recommended); (b) unmark sent; (c) logs only | (a) Requeue + drop pair | Prevents cascade across groups. B4a hint tells operator "Regenerate this (group,type)". |
| **D6** | 3 stacks → 1 multi-contest | (a) go after D1+D8+soak; (b) no-go; (c) pilot first | (a) Go after criteria met | Worth it; high operational benefit. Requires landing criteria (D8) + cms-test rehearsal (2026-10-03 to -10-09). |
| **D4-D** | Pins: Loader version, rekarel SHA, single image, log rotation | (a) by tag (pin why); (b) always latest (Loader); (c) SHA via ARG (rekarel); (d) single image; (e) rotation only | (pending user) | CMS_LOADER_VERSION reverted in cdcebb01 after pin in 643530ac — ask why. Single image / no db-init: NOT worth it. Measure log rotation first. |

---

## Contest Day Setup and Rehearsal Schedule

### 2026-10-10 (Saturday) — Contest Day

**Beta goes live.** Stack config:
- CMS_CONTEST_ID=ALL (multi-contest mode)
- MC-1 (per-group rankings): one stack, one DB, one worker pool, multiple contests assigned to groups.
- MC-2 minimal (visibility): staff view with per-group password; public sees notice when hidden.
- October 10 package (above) committed and tested.

**Operations:**
- Before contest: hide each group in AWS + set staff password. Check public shows notice; staff can log in.
- During contest: staff watches live ranking at same URL (password login).
- At end: un-hide groups in AWS. Reveal takes effect within seconds.
- Docs: `docs/multi-contest.md` with operator guide for MC-2.

### 2026-10-03 to 2026-10-09 (Thursday-Friday) — Rehearsal on cms-test

**Goal:** Rehearse multi-contest deployment, test MC-2 staff view, verify no Oct 10 package regressions.

**Plan (user runs on server):**
- `cms-test` stack reset to main (current prod baseline).
- Backup prod data.
- Run MC-1 + MC-2 Beta deployment plan (D6 + D8 elements; see SP3 operator docs).
- Import 2+ test contests, assign to groups, hide/reveal via AWS.
- Staff log in, verify live ranking visible under password.
- Public URL shows notice; data endpoints return 403.
- Run 2-3 test submissions per group. Verify results flow to correct ranking.
- Revert to main; verify prod data intact.

**Go/no-go decision for 2026-10-10:** If rehearsal green, proceed with beta on contest day. If issues, pivot to main + single-contest workaround.

---

## Item Count by Area

- **E (Documentation):** 13 items (E1-E13)
- **B (Bugs & Code Minors):** 29 items (B1-B15, B4a/b, B6a/b, B9a-g, B10a-i, B12a-e, B13a-c, B14a-b)
- **C (Tests & Infrastructure):** 13 items (C1-C13, C5.1=B5 merged, C10=A16 merged, C12=N4-D merged)
- **D (CI/Docker/Infra):** 20 items (D1-D10, N1-D through N10-D)
- **A (Gevent/Async):** 20 items (A1-A16, N1-A through N8-A)
- **V (Container Validation):** 3 items (V1-V3)
- **R (Code Review):** 5 items (R-M1/M2, R-N1/N2/N3)

**Total unique items:** ~103 (accounting for aliases/merges noted in register).

---

## October 10 Success Criteria

All items in October 10 package (SP1-SP5, Q1-Q4, MC-2) must be:

1. **Coded and tested** (unit + integration tests green).
2. **Documented** (README, CLAUDE.md, operator guide for MC-2).
3. **Deployed to beta** (same commit in CI + docker/test loop).
4. **Rehearsed on cms-test** (2026-10-03 to 2026-10-09; see Rehearsal Schedule above).
5. **Approved by user** (no open blockers, decisions finalized).

**Current status (HEAD 1b5a149d):** Oct 10 package is ~40% committed (CWS RPC fix, _settle, output-only, log lock test, schedule_rpc handler move). SP1-SP5 and Q items are in progress or planned. MC-2 spec is complete.

**Freeze 2026-10-02:** No new Oct 10 items added after Friday; only critical bug fixes.
