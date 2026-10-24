# OMI-shaped load-test profile and per-evaluation timings (issue #45) — Design

**Date:** 2026-10-24
**Status:** Decisions taken by read-only specialist panels (performance-engineer,
test-automator); approved by the owner as a consolidated design on
2026-10-24.

## Intent

The #7 harness compresses a contest into 25 minutes with cheap synthetic
tasks: each simulated user submits 3-5x faster than a real OMI 2025
contestant, about 22 evaluations per submission with a 1 s limit, 14 % of
submissions in Python or Java. OMI 2025 had 37-70 evaluations per
submission, limits of 1-10 s, one Communication task per day, only C++. The
harness exports only evaluation *counts*, so it cannot measure how much the
measured runtime of the same program varies under load (the false-TLE risk
on a host with SMT and more Workers than cores; issues #52, #53, #54).

## Facts verified in the code

`scenario.py` defines a profile only through `PROFILES`, `RANKED[profile]`,
`two_phase()`, `task_spec()`, plus the global `TASKS` (keyed by contest
name), `SOLUTIONS`, `TIME_LIMIT = 1.0` and `LANGUAGES`. `run.sh`,
`setup_contest.py` and the workflow test `== "full"` in 7 places. The driver
takes rate and burst from flags only. Every task is "sum of N numbers"
created as `Batch`; one solution file per kind is submitted to any task.

## Goals

1. A third profile `omi`, shaped like a real OMI, so that N harness users
   mean N contestants.
2. Export per-evaluation `execution_time`, `execution_wall_clock_time`,
   outcome and text, and analyse the spread of measured runtime for the same
   (solution kind, task, testcase) in the steady phase versus the end burst.
3. A `--cpus` knob to emulate a smaller production host on a bigger machine.
4. Document all of it in the harness README.

## Non-goals (decided)

- A real Communication task: the profile uses a **fourth Batch task as a
  stand-in** with the CPU cost per testcase of the OMI 2025 Communication
  task. The README and the report state plainly that Communication is
  substituted and that the FIFO / two-sandbox path is not exercised. If #52
  needs it, a separate issue.
- Python and Java kinds; the 15-minute pre-burst ramp; run presets in
  `run.sh` (document the command line instead); `execution_memory`;
  significance tests; #52's own submit-50-times profile; CPU PSI/steal in
  `monitor.py` (a separate small change, mention in the report).

## Decisions

### 1. Profile `omi` (fork only, like `full`: two-phase, dependencies, ranking group)
Add `"omi"` to `PROFILES`, `RANKED["omi"] = {"loada": "loada/scores"}`;
`two_phase()`, `task_spec()`, `run.sh`, `setup_contest.py` and the workflow
treat it like `full`; run with `--users-b 0` and no tasks for `loadb`.
- Tasks: one contest, 4 tasks (3 real Batch + the 4th Batch stand-in). Each
  task has 4 subtasks of a new category `"K"` with 5 testcases: sample,
  scr-wa, scr-tle and two heavy ones, i.e. 20 testcases per task. Score
  type GroupMin 4x25, with a `depends_on` chain on one task.
- One limit `TIME_LIMIT_OMI = 2.0` (no per-task key); only "C++20 / g++"
  (Python and Java weights 0).
- New solution kind `ac_cpu`: reads the normal input, spins `reps`
  iterations on a volatile sink, prints the true sum; `reps` is a trailing
  input line the other kinds ignore. Include `<bits/stdc++.h>` so the
  compile time is near OMI's 2.2-3.0 s. Only `ac` and `ac_cpu` pass `K`.
- Weights (sum 1.0): ac_cpu 0.30, wa_overflow 0.17, tle 0.15, wa_small 0.12,
  re 0.09, ce 0.08, wa_zero 0.09. **Calibrate** to the baseline report
  (`docs/superpowers/reports/2026-10-07-loadtest-baseline.md`, section 6.2):
  about 15 sandbox-seconds per submission including compile, 0.5-1.0 s per
  evaluation, 37-70 evaluations per submission. Numbers marked "calibrate"
  need a first run on GitHub Actions; say so.
- Rate curve with existing flags only: day-2 steady rate is 0.8 x 24 / 270
  min = 0.071 submissions per user per minute; the last 15 min hold 11.5 % of
  submissions (about 2.8 per user), which the driver's 1-3 end burst
  approximates at factor 1.0: `--users-a 100 --rate 7 --contest 2700
  --end-burst 900 --end-burst-factor 1.0 --workers 8 --cws 4` (100 users is
  close to OMI 2025's 99 and fits a 4-vCPU runner); at 500 users `--rate 35`
  needing at least 16 CPUs. Document, do not preset.

### 2. Export and analysis
- `db_export.py` adds `out["evaluations"]`: one row per Evaluation of the
  active dataset (same filter as submissions): `submission_id`, `testcase`
  (join `Testcase.codename`), `outcome`, `text`, `execution_time`,
  `execution_wall_clock_time`. About 120 B per row; ~34k rows (4-7 MB) at 500
  users, in-memory `json.dump` is fine.
- `analyze.py` gets a pure function `runtime_spread(evaluations,
  submissions_by_id, driver_subs)`: group by (kind, task, testcase) (the same
  codename on different tasks has different input), join kind and phase through
  `opaque_id` as the file already does, only kinds that run to completion
  (`execution_time` not None, in practice `ac` and `ac_cpu`), phase = the
  submission's driver phase (steady versus end_burst; `# shortcut:` a
  `scored_at`-based phase would be more exact). Per group and phase: n, min,
  p50, p95, max, spread = (max-min)/mean (#52's 10 % criterion), p95/p50;
  minimum group size 5, otherwise skipped and counted in a "skipped" line;
  per group the burst p50 / steady p50 ratio (the direct SMT signal);
  summarise as median and p95 over groups plus the TLE count; print for
  `execution_time` and for wall time. Skip the whole section when the export
  has no `evaluations` (the `run_small` fixture lacks them). No new
  `METRIC_KEYS`.

### 3. `--cpus`
`run.sh --cpus N`, validated like `--poll-cap`, writes `LOAD_CMS_CPUS` to
`.env.load`; default `$(nproc)` (a `cpus:` above the host count is an
error). `compose.yml` `cms:` gets `cpus: ${LOAD_CMS_CPUS}`. Workflow: add
`LOAD_CPUS: ""` to the env block and `[[ -n $LOAD_CPUS ]] && extra+=(--cpus
"$LOAD_CPUS")` in the Run step, as with `LOAD_POLL_CAP`. The name must not be
`LOAD_CMS_CPUS` (compose interpolates it and a shell variable would override).
**Key risk to document and check in a smoke run:** the `cms` container is
`privileged` with `cgroup: host`; isolate may place the sandbox in its own
cgroup outside the container's CFS quota, so `cpus:` would throttle the
services but not the sandboxes, which is exactly what #52 measures. The
check: log `/proc/<sandbox pid>/cgroup` and `Cpus_allowed_list`; the fallback
is `taskset` in `start.sh` (as #53 notes). Not needed on the 4-vCPU hosted
runner; meant for the 16-thread workstation (`--cpus 6`).

## Tests

Unit tests in the existing stdlib style under
`cmstestsuite/unit_tests/loadtest/`:
- `scenario_test`: `omi` is a valid profile; every task has 20 testcases;
  weights sum to 1; every kind is C++; `expected_score("ac_cpu")` is 100;
  `two_phase("omi")` is true.
- `analyze_test`: `runtime_spread` on synthetic rows (known spread, group
  below the minimum skipped, burst/steady ratio, missing `evaluations`).
- Only a GitHub Actions run verifies (report as NOT verified): the
  `db_export` SQL against a real database (a `DatabaseMixin` test is possible
  but costly: add it only if cheap), the `reps` and compile calibration, the
  `cpus:` effect on sandboxes, that 100 users saturate the Workers as
  intended, and real spread numbers. Do not trigger the workflow (CI
  budget): the coordinator decides.

## Docs

Harness README: the profile, the command lines, the export columns, the
analysis, `--cpus` and its cgroup caveat, the Communication stand-in. Also
amend the report's wording only if it claims something these changes
invalidate.

## Tasks

1. Scenario/solution/profile plumbing (`scenario.py`, `setup_contest.py`,
   `run.sh`, workflow) + tests.
2. `db_export.py` evaluations + `analyze.py` `runtime_spread` + tests.
3. `--cpus` knob.
4. README.
