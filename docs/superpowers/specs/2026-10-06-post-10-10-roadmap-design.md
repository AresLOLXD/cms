# Post-10-10 Backlog Roadmap — Design Doc

**Date:** 2026-10-06
**Status:** Approved in the planning conversation; each wave item gets its
own brainstorming before implementation. Updated 2026-10-07 with the Go gate
decision and the wave changes that followed from the #7 load tests.

## Context

The backlog lives in issues #5-#43 on the fork (`gh -R AresLOLXD/cms`).
The user wants to work through it on a separate integration branch, like
the old `beta`, and asked which issues come first. The main tension is
issue #29: a possible migration to a faster stack (Go). If that happens,
most internal Python work becomes wasted effort.

**Facts given by the user (2026-10-06):**

- The 100,000-participant goal of #29 is a medium-term aspiration with no
  date. Real contests today have a few hundred contestants.
- The next real contest after 2026-10-10 is 1-3 months away.
- There will be no self-hosted CI runner. CI runs on GitHub-hosted runners.
  GitHub Actions was enabled on the fork on 2026-10-06. Approval for all
  outside collaborators and a read-only workflow token were already set.

**Constraint:** nothing reaches `main` or production before the
2026-10-10 contest except contest hotfixes. Production runs tag
`omi-2026.10.04.2` (`1cdeec8a`, `1.6.dev0+omi.2`).

## Approach: Measure, Decide, Then Invest

Three approaches were weighed:

- **A (chosen): measure first.** Rebuild the load-test harness and find
  where the current stack breaks. Meanwhile, do the work that pays off with
  either stack. Decide on Go with numbers.
- **B: Go first.** Rejected. It would decide without data and spend a
  large effort on a goal with no date.
- **C: finish the Python modernization first.** Rejected. That work is lost
  if the numbers later point to Go.

### Classification Rule

- **Bugs that can hurt a real contest are fixed now, whatever the stack.**
  A migration would take months, and contests happen in between.
- **Internal Python performance work and refactors wait for the Go
  decision.**

## Waves

### Wave 0: Foundations

Run in parallel; they touch different files. Start #23 first because it
is smaller and makes CI trustworthy before new code lands on `beta`.

- **#23 Flaky tests and CI.** Make the suite pass repeatably on the hosted
  `test` job of `.github/workflows/main.yml` (noble and bookworm matrix).
  Also:
  - check that the Codecov step does not fail runs when `CODECOV_TOKEN` is
    missing;
  - decide whether to delete the unused `test-self-hosted` job.
- **#7 Load-test harness in the repo.** The 2026-09-30 harness was lost
  with its temporary directory and must be rebuilt. Steps:
  1. Run a baseline of the fork against upstream.
  2. Ramp up the load until one component saturates: CWS logins and
     bcrypt, ES dispatch, PostgreSQL or RWS. One host cannot reach 10k, but
     knowing which component breaks first is enough for the Go decision.
  3. Feed the numbers to #39 and #42.

### Wave 1: Contest Robustness

These items do not depend on the stack:

- #5 last-second submissions
- #6, the max-age flush first (added 2026-10-07, see the Go gate)
- #40 workers disabled forever
- #39 database timeouts and keepalives
- #38 watchdog
- #42 log rotation and connection budget
- #11 suspected bugs S2, S4 and S5
- #13, only S1 (RWS drops batches)
- #12
- #35

### Wave 2: Users and Operations

- #14-#19: RWS polish, DAG, bulk import and removal, retiring CMS-Loader
- #43 Telegram bot in ALL mode
- #26 Docker
- #34 dump download
- #33 OMI Box import API
- #30 i18n and dark theme, the largest item, last in the wave
- #45 an OMI-shaped load-test profile and per-evaluation timings
  (added 2026-10-07)

Wave 2 items join a merge only if they are ready and tested by the cut.
They never hold one back.

### Wave 3 (Paused Until the Go Decision; Unblocked 2026-10-07)

The milestone that held these items is now named Ola 3. The Go gate
below decided that the Python stack stays, so they are no longer
blocked; they come after waves 1 and 2.

- #8-#10 (EvaluationService); #6 moved to wave 1
- #13 except S1
- #20 and #21 (remove gevent)
- #22 (CWS database work off the event loop)
- #25 (deprecated APIs)
- #41 (AWS event-loop leftovers)
- #24 (unit-test gaps)
- D3-A and D4-A of #28

### Go Gate

The Go question of #29 is decided once #7 has numbers. The answer may be
partial, for example Go for one service (CWS or RWS) behind the same
database contract, rather than a full rewrite. The questions of #28 that
do not depend on Go (D6, MC-3, the DAG rule) are answered in the
brainstorming of the wave they belong to.

**Decided on 2026-10-07: no migration for now.** The #7 load tests
(`docs/superpowers/reports/2026-10-07-loadtest-baseline.md`) showed:

- The Workers saturate first: contestant code running inside isolate, which
  a rewrite of the services would not change.
- The Python services handled 250 and 500 harness users with 0 HTTP errors,
  0 rejected submissions and 0 score mismatches. The fork broke only at
  1000 users (in-time submissions rejected after CWS stalled on a saturated
  host, #5). Each harness user submits 3-5x faster than a real OMI 2025
  contestant, so that is far beyond the expected scale.
- Two-phase screening roughly halved Worker time at 500 users, so the
  capacity levers are screening and Workers on more machines.

Follow-ups: #6 moved to wave 1 for the max-age flush (two-phase stages wait
for the debounced result flush); the release-Worker-before-the-lock premise
of #6 was not confirmed. #29 stays open for a future target that needs more
than one host's worth of CWS/ES load. The load tests now run on
GitHub-hosted runners (`.github/workflows/loadtest.yml`, push to a
`loadtest/` branch), not on a developer workstation.

## Branch Mechanics

- **Base.** `beta` is created from `origin/main` `1cdeec8a` and is checked
  out in `.worktrees/beta`. It has no upstream set to `main`, so a push can
  never go to `main` by mistake.
- **Issue branches.** Each issue gets a branch from `beta` and is merged
  straight into `beta` after review, with no pull request.
- **Hotfixes until 10-10.** `main` only takes contest hotfixes, and each one
  is merged into `beta` (`main` → `beta`).
- **Upstream.** Upstream goes into `main` first, then from `main` into
  `beta`.
- **`beta` → `main`.** One merge per wave, not per issue. A merge needs:
  - the reviews done;
  - the hosted `test` job green on GitHub;
  - a run on cms-test.
- **Pushing merges.** The pre-push hook flattens merges, so a merge into
  `main` is pushed from a detached-HEAD worktree with `HEAD:main`.
- **Each deploy.** Bump `+omi.N` and tag `omi-<date>`.

## Cadence

1. **Now → 2026-10-10:** create `beta` and start wave 0. `main` gets
   nothing except contest hotfixes.
2. **First 3-5 weeks after 10-10:** finish waves 0 and 1 on `beta`, then
   decide on Go with the numbers from #7.
3. **2-3 weeks before the next contest:** merge waves 0 and 1 into `main`,
   deploy to cms-test, rehearse with the harness load, then deploy to
   production. Anything not ready by the cut waits for the next deploy.

## Out of Scope

Implementation details of each issue. Every item goes through its own
brainstorming (spike, bounded or architectural) before any code.
