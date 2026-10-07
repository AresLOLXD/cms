# Load-Test Harness and Fork-vs-Upstream Baseline — Design Doc

**Date:** 2026-10-06
**Issue:** #7 (wave 0 of `2026-10-06-post-10-10-roadmap-design.md`)
**Status:** Designed and approved autonomously. The user asked on 2026-10-06
for the backlog to advance without stopping, always through brainstorming.
Decisions marked *(autonomous)* took the recommended option and can be
revisited.

## Goal

Measure with numbers, not impressions, so that we can:

1. See how much the fork improved over upstream CMS under the same load.
2. Find which component saturates first as the load grows, which is the
   input to the Go decision of #29.
3. Size `max_connections` and the pool sizes (#42) and check the DB timeouts
   (#39).

The harness built on 2026-09-30 was lost with its temporary directory. It
was recovered on 2026-10-06 from that session's transcript into the
git-ignored `.superpowers/loadtest-recovered-2026-09-30/`, and it is the
starting point. It ran 5,920 submissions with 0 errors and produced the
numbers behind #5 and #6.

## What the Recovered Harness Does

- **`compose.yml`:** a production-shaped stack. One `cms` container runs
  every service under supervisord, next to PostgreSQL 17, the standalone
  ranking and a `driver` container.
- **`setup_contest.py`:** creates two contests (`loada`, `loadb`) with
  ranking groups, tasks with GroupMin/GroupMul/GroupThreshold, `depends_on`
  chains and testcases named for two-phase screening.
- **`scenario.py`:** the shared scenario data and `expected_score()`.
- **`driver.py`:** an aiohttp client. Users log in during a login window,
  submit at a steady rate plus an end burst, poll results, read
  notifications and poll RWS.
- **`monitor.py`:** samples ES queue and worker status, PostgreSQL
  connections and lock waits over RPC and SQL.
- **`db_export.py`, `analyze.py`:** export the final scores and compare each
  one with `expected_score()`, check RWS against CMS and write
  `summary.md`.
- **`flushstats.py`, `lockwait.py`:** log analyses of ES flushes and of how
  long finished results wait for `post_finish_lock`.
- **`run.sh`, `teardown.sh`, `solutions/`, `driver/Dockerfile`:** support
  files.

## Gaps Found (Explore Report, 2026-10-06)

- **`db_export.py:27`** passes `rounded=True` to `task_score`, a parameter
  removed in e0e4f8b7. It fails on both builds.
- **Upstream `cc9dfafb` has none of these fork features:** `depends_on`
  (upstream silently ignores the key and scores without gating), two-phase
  evaluation, ranking groups and `Contest.active`, multi-contest
  ProxyService, or the env-driven Docker image (`generate_config.py`,
  entrypoint, supervisord, `cmsSetupDB`, `Dockerfile.ranking`).
- **Upstream ProxyService is single-contest** (`-c <id>`), so upstream can
  rank only one contest, at the RWS root.
- **Upstream config** (`cms/conf.py` + `conf_parser`) warns about unknown
  keys and ignores them. Upstream's own Dockerfile has no entrypoint.
  `cmsResourceService -a ALL 0` starts every service except LogService and
  ProxyService.
- **The HTTP and RPC surfaces the driver and monitor use are identical** in
  both builds: login, submit, status JSON, notifications, RWS `/scores`,
  RPC framing and `echo`/`queue_status`/`workers_status`.
- **`flushstats.py`** matches `_write_results_sync] Done`. Upstream logs
  `write_results] Done`.
- Workers, sandbox, languages and task types have no diff between the
  builds.

## Decisions

| # | Decision | Choice |
|---|---|---|
| L1 | Where the harness lives | `cmstestsuite/loadtest/`, with a README. It is not part of the pytest suite: no `*_test.py` names, and it must stay pyflakes-clean. |
| L2 | Baseline | Upstream `cc9dfafb`, the merge-base of the fork and upstream (the last upstream commit merged, 2026-09-26). Only our changes differ. *(autonomous)* |
| L3 | Builds | Each **target** is built from a git ref with `git archive <ref>` and that ref's own `Dockerfile`: fork `beta` (or any fork ref) and upstream `cc9dfafb`. |
| L4 | Service start | The same start script for both targets, bypassing supervisord, the fork entrypoint and ResourceService: every service is started explicitly as `cms<Service> <shard> -c ALL` (each script accepts `-c`, ignored where unused), then `cmsProxyService` once the contests exist. `cmsResourceService -a ALL` was rejected because it starts ProxyService on the fork but skips it upstream. No autorestart: a crash under load is a finding. *(autonomous)* |
| L5 | Config | One hand-written `cms.toml` template rendered by the runner for both targets: the same ports, workers and CWS count, `cookie_duration = 18000`, AWS on 8898 (upstream's 8889 default clashes with CWS shard 1). Fork-only keys (`two_phase_evaluation`) are harmless upstream. The same goes for `cms_ranking.toml`. |
| L6 | Profiles | **`portable`:** both contests without `depends_on`, two-phase off, only `loada` ranked at the RWS root (ProxyService `-c <loada id>`), driver polls `/scores`, analyzer compares RWS for `loada` only. **`full`:** the recovered scenario (dependencies, two-phase, groups), fork target only. |
| L7 | Readiness | HTTP probes of the CWS/RWS ports plus an RPC `echo` to each service replace the `supervisorctl` checks. |
| L8 | Isolation | The compose project name is `cmsload-<target>` and parametrized everywhere, including the `analyze.py` docker-stats filter. The runner refuses to start while another `cmsload-`, `cmsci-` or stress stack runs, because isolate cgroups are shared. |
| L9 | Where it runs | Locally, on rootful Docker on the test workstation (`sg docker -c "docker --context default ..."`). A 4-vCPU hosted runner is too small for the scale questions, and there is no self-hosted runner. *(autonomous)* |
| L10 | Run plan | (a) `portable`, 250 users with the 2026-09-30 `full1` parameters, 3 runs per target. (b) `full` on the fork, 1 run. (c) A ramp on the fork with `portable` at 250, 500, 1000 and 2000 users, stopping at the first level with errors, rejected submissions or a backlog that never drains. (d) The same ramp on upstream up to the same level or its breaking point. *(autonomous)* |
| L11 | Toolchain parity | The runner records the g++/python/java and isolate versions of each image in the run output. A mismatch is reported, not fixed. |
| L12 | Results | Raw output in `cmstestsuite/loadtest/out/<run>/` (git-ignored). The written comparison goes in `docs/superpowers/reports/<date the report is written>-loadtest-baseline.md` and a comment on #7, plus #29, #39 and #42 where they need it. It stays out of the Sphinx manual, so there is no `.po` burden. |

## Components

- **`scenario.py`:** the existing data plus a `PROFILES` mapping. `portable`
  strips `depends_on`. `expected_score()` takes the profile, so the scores
  match each build.
- **`setup_contest.py`:** guards the `RankingGroup` import and the
  `active`/`ranking_group` keyword arguments (`hasattr`). On the fork it
  keeps `active=True`, or CWS answers 404. It takes `--profile` and prints
  the contest ids for the runner.
- **`config/cms.toml.tmpl`, `config/cms_ranking.toml.tmpl`, `start.sh`:**
  the rendered config and the uniform start script of L4 and L5.
- **`compose.yml`:** the same services, with the image chosen by the
  target (`LOAD_TARGET` selects `cmsload-<target>:latest`) instead of
  built from `./src`, and the config and start script mounted.
- **`build.sh <target> <ref>`:** runs `git archive` into a temporary
  context, `docker build`, and tags `cmsload-<target>:latest`. It
  records the ref, its commit and the toolchain versions of the image in
  `out/images/<target>.txt`.
- **`run.sh`:** takes `--target`, `--profile`, a users count and the
  scenario knobs. It brings the stack up, starts ProxyService after setup
  (with `-c <loada id>` for `portable` and `-c ALL` for `full`: without
  `-c`, ProxyService asks for a contest on the terminal, or exits when it
  has none), runs the monitor,
  the stats sampler and the driver, then exports and collects. The
  readiness probes of L7 replace `supervisorctl`.
- **`driver.py`:** polls the RWS scores paths listed in `users.json`
  (`ranked`, written by `setup_contest.py`): `scores` for `portable`,
  `<group>/scores` for `full`.
- **`analyze.py`:** the profile-aware RWS check and the project filter.
- **`db_export.py`:** the `rounded=` fix.
- **`flushstats.py`:** a pattern that matches both builds.
- **`compare.py`:** reads several `out/<run>/summary.md` or metrics JSON
  files and prints a side-by-side table: login, submit and score latency
  percentiles, drain time after the stop, CPU and RAM per service, peak PG
  connections, errors, rejected submissions. The analyzer gains a
  machine-readable `metrics.json` next to `summary.md` for it.
- **`README.md`:** prerequisites (rootful Docker, isolate cgroups), the
  build/run/teardown commands, the profiles and knobs, what each summary
  number means, and the known limits (one host, no proxy/TLS, synthetic
  cheap testcases, one client IP).

## Error Handling

- Every script exits non-zero with a clear message when a precondition
  fails: another stack running, an image missing, a service not ready
  within the timeout, or the setup failing.
- The runner always collects logs, even when the driver fails (trap), so a
  failed run can still be diagnosed.
- The analyzer reports mismatches and errors as numbers. It never hides
  them: a run with errors is a result, not a crash.

## Testing

- Unit tests (`cmstestsuite/unit_tests/loadtest/`, collected by pytest in
  the asyncio group, with no Docker and no DB):
  - `scenario.expected_score()` for both profiles: dependency gating on
    `full`, none on `portable`;
  - the config template renderer;
  - `compare.py` on two small fixture `metrics.json` files;
  - the `analyze.py` metrics extraction on a tiny fixture run directory.
- **Smoke run per target:** `portable`, 20 users, 8-minute contest, before
  any long run. It must give 0 errors, 0 mismatches and RWS == CMS.
- pyflakes on `cmstestsuite/loadtest`.

## Out of Scope

- Fixing anything the runs reveal. Findings become issues, or comments on
  the existing ones.
- A GitHub Actions workflow for load runs.
- Multi-host runs, a reverse proxy or TLS, or several client IPs.
- Measuring the multi-contest path upstream, which upstream cannot do.

## Comparability Caveats (for the report)

- The two builds have different architectures (gevent/WSGI, Tornado 4.5 and
  SQLAlchemy 1.3 upstream; asyncio, Tornado 6 and SQLAlchemy 2 on the
  fork). That difference is what the comparison measures.
- Fork-only work cannot be turned off and must be stated: the participant
  activity recorder, cookie refresh on polls, and more PG connections per
  process.
- `full` has no upstream counterpart. It is reported as a third
  configuration.
