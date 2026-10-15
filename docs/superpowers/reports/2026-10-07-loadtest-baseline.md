# Load-Test Baseline: Fork vs Upstream, Full Profile and Ramp

**Date:** 2026-10-07 (runs made 2026-10-07 01:25-07:54 UTC)
**Issue:** #7. Inputs for #5, #6, #29, #39 and #42.
**Harness:** `cmstestsuite/loadtest/` at 197ab8c7 (this branch). The raw
output of each run (`out/<run>/`: logs, DB export, throwaway passwords) is
git-ignored and kept locally only. The small per-run results (`summary.md`,
`metrics.json`, the image toolchain record and the process census) and
the host sampler's log are in `cmstestsuite/loadtest/results/2026-10-07/`,
so the comparison tables can be reproduced with `compare.py` on them,
for example `python3 compare.py results/2026-10-07/base-fork-*
results/2026-10-07/base-upstream-* --median`. The analysis scripts are in
`cmstestsuite/loadtest/adhoc/`; they need a full `out/<run>`.

Every number below comes from runs made today on one host with one harness.
The recovered `REPORT-2026-09-30.md` used the recovered harness before
today's fixes. Its driver loaded the contest page at a path that returns 404
now, and it fixed the contest start before hashing the passwords. Its
numbers are **not** comparable with these and are not used here.

## Summary

- **Headline: two-phase screening and dependencies (the `full` profile)
  cut the work the Workers really do, and at 500 users that is the
  difference between a backlog and none.** Evaluate jobs actually run
  fell from 22.2 to 17.0 per submission. Worker time in evaluate jobs fell
  41-49 % at 250 users (against each portable fork run), and at 500 users
  51 % against upstream portable and 61 % against fork portable. At 500
  users the drain went from 291 s to 82 s and scored p95 from 228 s to
  56 s (upstream portable: 167 s and 109 s). About 80 % or more of the
  time saved is two-phase screening: it skips 7.0 testcases per TLE
  submission, the 7 non-screening testcases of the large group, which are
  the 1.1 s time-limit runs. Dependencies remove more *jobs* on WA
  submissions, but cheap ones. Neither the DB's Evaluation rows nor
  the job-group counts measure this. `full` changes two things at once,
  and screening alone was not run, so section 3 says what can be
  attributed to which.
- **250 users, 3 runs per target, alternating.** Both builds were correct
  in every run: 0 HTTP errors, 0 rejected, 0 stuck, 0 score mismatches,
  RWS == CMS. The fork is clearly better on login p95 (0.218 s against
  0.574 s): it checks bcrypt on a thread pool, off the event loop. It is
  somewhat better on submit latency and on scored p95, but the per-run
  ranges overlap. Nothing else differs by more than 20 %.
- **The ramp was capped at 500 users by the user** (2026-10-07). At 500
  users neither build broke. The fork broke at **1000 users** (one run,
  fork only). 30 submissions were rejected, 24 of them sent 0.0-3.1 s
  *before* the stop, and submit p95 in the end burst was 2.8 s. This is
  #5: the CWS event loops stalled for 2-9.5 s with the host CPU saturated.
- **The first component to saturate is the Workers** (8 Workers, 8/8 busy):
  about 42-50 testcase jobs/s, or about 2 submissions/s, with these cheap
  testcases. ES, SS, PS, RWS and PostgreSQL never saturated.
- **At 500 users upstream drained faster than the fork** (167 s against
  291 s). It is **not** ES's `post_finish_lock`: under saturation the fork's
  Workers lost *less* time between job groups (4.0 % against 8.5 %). The
  whole gap is the time per short job (110 ms against 74 ms mean), on
  Worker, sandbox and task-type code that is identical in both builds. The
  same fork code ran at about 80 ms per job in its other runs. The cause is
  inconclusive with one run per target; see [4.2](#42-why-upstream-drained-faster-than-the-fork-at-500-users).
- **For 2026-10-10 the risk is Worker and CPU capacity at the end, not CWS
  or PostgreSQL** (section 6). The production host is a small VM: 6
  vCPUs on 3 physical cores (SMT), with slower, older server-class cores.
  Real OMI 2025 submissions cost about 10× the Worker time of the
  harness's. For
  190-270 contestants with ≤ 20 testcases per task, the estimate is
  6,500-10,300 sandbox-seconds in the last 15 minutes, compilation
  included, against about 3,150-4,400 thread-seconds left for the
  sandboxes. The last result would come about 7-34 minutes after the stop
  without screening. At the upper end it could be about an hour, and it
  would be shorter if 2025's own wall times were inflated by sharing.
  With screening it would be none to about 26 minutes; screening needs
  the testcases renamed, an ops change. These are estimates. False TLEs:
  low risk on the wall clock with the current 6 Workers. The CPU-time
  effect of SMT concentrates in tasks with tight limits, so calibrate
  those limits on the production host. No code change before 10-10 is
  justified by this evidence.

## Run Plan and What Ran

| run | target | profile | users (loada + loadb) | CWS | Workers | rate /min | outcome |
|---|---|---|---|---|---|---|---|
| base-fork-1 | fork | portable | 250 (175 + 75) | 2 | 8 | 45 | clean |
| base-upstream-1 | upstream | portable | 250 | 2 | 8 | 45 | clean |
| base-fork-2 | fork | portable | 250 | 2 | 8 | 45 | clean |
| base-upstream-2 | upstream | portable | 250 | 2 | 8 | 45 | clean |
| base-fork-3 | fork | portable | 250 | 2 | 8 | 45 | clean |
| base-upstream-3 | upstream | portable | 250 | 2 | 8 | 45 | clean |
| full-fork-1 | fork | full | 250 | 2 | 8 | 45 | clean |
| ramp-fork-500 | fork | portable | 500 (350 + 150) | 4 | 8 | 90 | clean; drain 291 s |
| ramp-fork-1000 | fork | portable | 1000 (700 + 300) | 4 | 8 | 180 | **breaks**: 30 rejected |
| ramp-upstream-500 | upstream | portable | 500 | 4 | 8 | 90 | clean; drain 167 s |
| ramp-fork-full-500 | fork | full | 500 (350 + 150) | 4 | 8 | 90 | clean; drain 82 s (the A/B of the fork-only features that #7 asks for) |

- The baseline ran in the order f1, u1, f2, u2, f3, u3, so that drift on the
  host hits both targets alike. A teardown came after every run.
- Ramp levels use `--cws 4 --workers 8` on both targets, because
  production has run 4 CWS since 2026-10-04. The baseline keeps the
  `run.sh` defaults (2 CWS). In the ramp table, then, the 250-user point
  has 2 CWS and the others 4.
- **The user capped the ramp at 500 users** (decision of 2026-10-07).
  `ramp-fork-1000` had already run and is kept as the fork's breaking
  level. There is no upstream run at 1000 users and no run at 2000 on
  either target, so upstream's breaking level is unknown.
- `ramp-upstream-1000` was already queued and started at 07:50. It was
  stopped at 07:54, during the contest setup and before the driver
  started, when the decision arrived. It applied no load and is not a
  result, and its output is kept only locally. A repeat pair
  at 500 users (`ramp-fork-500-2`, `ramp-upstream-500-2`) was queued in the
  same batch and cancelled before it started.
- No run failed for an infrastructure reason, and none was rerun.

Commands: `./run.sh --target <t> --profile portable --name base-<t>-<n>`
(defaults), `./run.sh --target fork --profile full --name full-fork-1`,
`./run.sh --target <t> --profile <p> --name ramp-... --users-a 350 --users-b 150
--rate 90 --cws 4 --workers 8` (and 700/300/180 for 1000), each followed by
`./teardown.sh <t>`.

## 1. Setup

**Host:** a single test workstation running a Fedora-based Linux
distribution, kernel `7.2.7-ogc1.1.fc44.x86_64`. AMD Ryzen 7 5800HS (a
mobile CPU): **8 cores, 16 threads** (SMT), max 4.46 GHz, `amd-pstate-epp` with governor
`powersave` and EPP `balance_performance`, on AC power. 38.6 GiB RAM.
Docker 29.8.1, rootful. PostgreSQL `docker.io/postgres:17-alpine`
(`sha256:b0f9560a…`), with the default `max_connections` of 100. Its data
lives on an encrypted btrfs volume on a consumer QLC NVMe SSD. The
CPU ran hot throughout: Tctl 90-95 °C and a mean clock of 2.9-3.6 GHz
during the runs. Other light background load on the workstation used
about 0.3-0.5 logical CPUs at rest.

**Refs:**

| target | ref | commit | image |
|---|---|---|---|
| fork | dc08291f | dc08291f9d5130fa91f668846c1b708fb05f8863 | `cmsload-fork:latest`, built 2026-10-06T23:50:49Z |
| upstream | cc9dfafb | cc9dfafb0bccae6fadda66b84548660cd3be02e5 | `cmsload-upstream:latest`, built 2026-10-06T23:58:54Z |

The fork image, built from dc08291f, is valid for the fork target:
`git diff beta dc08291f` and `git diff dc08291f HEAD` over `cms`,
`cmscommon`, `cmscontrib`, `cmsranking`, `cmstaskenv`, `Dockerfile`,
`pyproject.toml` and `constraints.txt` are both empty. This branch differs
from beta (599d87bd) only in the harness and the docs. The harness is
mounted into the containers, so every run used the harness at 197ab8c7.

**Image toolchains** (`out/images/*.txt`, copied to each run's
`image.txt` in the results), identical on both images:
g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0, Python 3.12.3, javac
21.0.12.1, isolate 2.7.

**Scenario knobs** (README defaults, which are the `full1` values of
2026-09-30): login window 150 s, contest 1500 s, end burst 300 s,
`--end-burst-factor` 1.0, steady rate 45 submissions/min per 250 users
(scaled with the users in the ramp), 8 Workers, 2 CWS (4 in the ramp),
`cookie_duration = 18000`. Users split 70 % `loada`, 30 % `loadb`.

**How much each simulated user submits** (from `db_export.json`): at 250
and 500 users the mean per run is 5.31-5.49 submissions per user in the
25-minute contest, the median 5, the range 1-13. That is about 13 per
user per hour, about 2 of them in the last 5 minutes. At 1000 users the
mean fell to 4.19 (median 4, range 1-8). The driver is a closed loop, and
users who wait minutes for a result submit less.

## 2. Baseline (portable, 250 users, 3 runs per target)

`python3 compare.py out/base-fork-* out/base-upstream-* --median`:

| metric | fork | upstream |
|---|---|---|
| runs | 3 | 3 |
| users | 250 | 250 |
| submissions_sent | 1339 | 1328 |
| submissions_rejected | 0 | 0 |
| login_failures | 0 | 0 |
| http_errors | 0 | 0 |
| score_mismatches | 0 | 0 |
| rws_pairs | 700 | 700 |
| rws_mismatches | 0 | 0 |
| login_p50 | 0.215 | 0.217 |
| login_p95 | 0.218 | 0.574 |
| submit_p50 | 0.021 | 0.027 |
| submit_p95 | 0.035 | 0.059 |
| submit_end_p95 | 0.05 | 0.079 |
| scored_p50 | 11 | 11.4 |
| scored_p95 | 37 | 48.5 |
| scored_max | 55.4 | 65.2 |
| drain_after_stop_s | 52 | 62 |
| peak_pg_connections | 26 | 30 |

Per run (`python3 compare.py out/base-fork-{1,2,3} out/base-upstream-{1,2,3}`):

| metric | base-fork-1 | base-fork-2 | base-fork-3 | base-upstream-1 | base-upstream-2 | base-upstream-3 |
|---|---|---|---|---|---|---|
| submissions_sent | 1339 | 1328 | 1361 | 1327 | 1328 | 1362 |
| login_p50 | 0.26 | 0.214 | 0.215 | 0.261 | 0.217 | 0.216 |
| login_p95 | 0.268 | 0.218 | 0.218 | 0.78 | 0.574 | 0.547 |
| submit_p50 | 0.025 | 0.021 | 0.021 | 0.029 | 0.023 | 0.027 |
| submit_p95 | 0.055 | 0.035 | 0.034 | 0.094 | 0.042 | 0.059 |
| submit_end_p95 | 0.094 | 0.05 | 0.044 | 0.079 | 0.071 | 0.147 |
| scored_p50 | 12.1 | 9.6 | 11 | 11.5 | 11.3 | 11.4 |
| scored_p95 | 40.3 | 29.8 | 37 | 36.5 | 48.5 | 49.7 |
| scored_max | 64.3 | 55.4 | 54 | 61.3 | 65.2 | 77.8 |
| drain_after_stop_s | 60 | 52 | 51 | 58 | 62 | 75 |
| peak_pg_connections | 26 | 24 | 27 | 30 | 28 | 33 |

The correctness rows (rejected, login failures, HTTP errors, mismatches,
RWS) are 0 in every run, with 0 stuck submissions and no `SHORT` line in
any process census.

`login_p50` is one bcrypt check (about 0.21-0.23 s, the same code on both
builds), so it works as a gauge of host speed. It went from 0.214 s to
0.261 s between runs of the same build. The host's speed drifted by about
20 % between runs, and both members of the first pair (f1, u1) ran on a
slower host. The alternation spread this drift evenly between the
targets, but a difference smaller than about 20 % in a single pair of runs
is within noise.

Rows that differ by more than 20 % between the medians:

- **login_p95, -62 % (0.218 s against 0.574 s), with ranges far apart**
  (fork 0.218-0.268, upstream 0.547-0.78). The fork checks bcrypt in a
  4-thread pool off the event loop (`_PASSWORD_CHECK_POOL` in
  `cms/server/contest/authentication.py`), and bcrypt releases the GIL.
  On upstream, bcrypt runs on the gevent loop and blocks the whole CWS
  process, so logins that arrive together queue behind each other (60 % of
  users arrive in the last 50 s before the start). p50 is the same on both
  because a lone login costs one bcrypt either way. This is the clearest
  improvement of the fork.
- **submit_p50, -22 % (0.021 against 0.027 s); submit_p95, -41 % (0.035
  against 0.059 s); submit_end_p95, -37 % (0.05 against 0.079 s).** The
  direction holds in every pair for submit_p50 and submit_p95, but not for
  submit_end_p95 (pair 1: fork 0.094 against upstream 0.079 s), and the
  ranges overlap: fork submit_p95 0.034-0.055 against upstream
  0.042-0.094. A real but modest gain, in
  tens of milliseconds.
- **scored_p95, -24 % (37 against 48.5 s).** The ranges overlap: fork
  29.8-40.3, upstream 36.5-49.7. In the steady phase p95 is the same on
  both (20.9-22.3 s). The gap is all in the end burst: p95 39.7-49.9 s on
  the fork against 44.7-62.7 s upstream. ES flushes are about the same
  length (p50 0.20-0.32 s against 0.21-0.28 s), and the end-burst Worker
  idle time between job groups is similar (41 % against 46 % in
  base-*-2). What makes the fork's end burst drain sooner is not
  established.
- Under 20 %: scored_max -15 %, drain -16 %, peak PG connections -13 %. The
  others match.

Other baseline observations:

- CWS CPU per request (monitor, all threads): fork 11.8-14.3 ms, upstream
  12.7-15.5 ms. ES CPU over the run: fork 165-211 s, upstream 144-182 s.
  Worker processes: fork 953-1152 s, upstream 829-1029 s. These come from
  `adhoc/cpu.py`; see [Method](#method-of-the-ad-hoc-analyses).
- The worst CWS RPC `echo` was 82-185 ms on the fork and 206-490 ms on
  upstream. No sample passed 1 s.
- The PostgreSQL log had slow COMMITs (over 1 s) only on upstream: 2 in
  u1 (1.48 s and 2.06 s at the same instant) and 1 in u3 (1.45 s). The
  fork had none. The other `FATAL` lines are the DB's own start-up and
  shutdown.
- The fork used **fewer** PostgreSQL connections per process, not more as
  the spec expected: CWS 3 against 5-8, ES 5-8 against 5.

## 3. The Full Profile (third configuration) and the Two-Phase/DAG A/B

`full` (dependencies up to 4 levels, two-phase screening, both contests
ranked) runs on the fork only. Against the median of base-fork-* (the
median columns are `compare.summarize()` of base-fork-1..3, the same
numbers `compare.py --median` prints for those runs):

| metric | base-fork-median (portable, 3 runs) | full-fork-1 |
|---|---|---|
| users | 250 | 250 |
| submissions_sent | 1339 | 1338 |
| submissions_rejected | 0 | 0 |
| login_failures | 0 | 0 |
| http_errors | 0 | 0 |
| score_mismatches | 0 | 0 |
| rws_pairs | 700 | 925 |
| rws_mismatches | 0 | 0 |
| login_p50 | 0.215 | 0.257 |
| login_p95 | 0.218 | 0.265 |
| submit_p50 | 0.021 | 0.022 |
| submit_p95 | 0.035 | 0.048 |
| submit_end_p95 | 0.05 | 0.075 |
| scored_p50 | 11 | 18.5 |
| scored_p95 | 37 | 44.9 |
| scored_max | 55.4 | 74 |
| drain_after_stop_s | 52 | 45 |
| peak_pg_connections | 26 | 25 |

Reading:

- full-fork-1 is correct: 0 errors, 0 mismatches, RWS == CMS on 925 pairs
  (both contests are ranked).
- login_p50 0.257 s puts it on the slow-host side (like f1 and u1), so
  part of the +20-50 % in login and submit is host drift.
- **Latency is higher at 250 users: scored p50 18.5 s against 11 s.**
  Screening and dependencies split a submission into stages, and every
  stage waits for an ES result flush. The gap between flushes was p50 4.0 s
  (portable 2.6-3.0 s). Each flush is also heavier, p50 0.68 s against
  0.20-0.32 s, since ES synthesizes the skipped evaluations and advances
  the dependencies inside `post_finish_lock`. Workers waited 502 worker-s
  in all for ES to take their results, against 127-177 worker-s on
  portable (`lockwait.py`). This is the debounced flush of #6.

### 3.1 The screening A/B: what the Workers really did

**What does not measure the work:**

- **Evaluation rows in the DB.** They are almost equal: 30100 in
  full-fork-1 against 29812 in base-fork-1 (22.5 against 22.3 per
  submission), and 58900 in ramp-fork-full-500 against 60784 in
  ramp-fork-500. ES writes a synthetic row (outcome 0, text "Skipped after
  screening phase failure", or the dependency text) for every testcase it
  skips. In the ES log of full-fork-1, `_advance_two_phase` synthesized
  4301 rows and `_advance_dependencies` 3060. With the 22739 jobs that
  ran, that is exactly the 30100 rows. For ramp-fork-full-500 it is 45251
  + 8475 + 5174 = 58900.
- **Job groups.** Two-phase sends a submission in stages, so it makes
  *more* job groups: 9036 in ramp-fork-full-500 against 5348 in
  ramp-fork-500, and 6766 against 6577 at 250 users.

**What does:** the jobs the Workers ran and their wall time, from the
"Starting job"/"Finished job" pairs of `cmslog/Worker-*/[0-9]*.log`
(`last.log` is a copy and is skipped).

The sum of `execution_time` and `execution_wall_clock_time` of the
evaluations could not be measured for these runs. `db_export.py` exports
only the number of evaluations per submission, and every teardown
removes the DB volume (`compose down -v`, by design). Job wall time is the
time a Worker is taken by the job: the sandbox's wall time plus the
per-job overhead (isolate init and cleanup, file handling, output
check). For capacity that is the number that matters. Exporting the two
columns and the skipped-row count from `db_export.py` would make the next
run measure sandbox time directly (follow-up, not done here).

All jobs, with compilation (`adhoc/jobs.py`):

| run | submissions | evaluate jobs run | jobs / submission | busy time in all jobs | busy s / submission |
|---|---|---|---|---|---|
| base-fork-1 | 1339 | 29812 | 22.3 | 5035 s | 3.76 |
| base-fork-2 | 1328 | 29362 | 22.1 | 4310 s | 3.25 |
| base-fork-3 | 1361 | 30290 | 22.3 | 4522 s | 3.32 |
| full-fork-1 | 1338 | 22739 | **17.0** | **2745 s** | **2.05** |
| ramp-fork-500 | 2743 | 60784 | 22.2 | 11281 s | 4.11 |
| ramp-upstream-500 | 2682 | 59426 | 22.2 | 8689 s | 3.24 |
| ramp-fork-full-500 | 2666 | 45251 | **17.0** | **4513 s** | **1.69** |

Evaluate jobs only, per solution kind (`adhoc/abkind.py`; it joins
`db_export.json`, `submissions.jsonl` and the ES and Worker logs):

| run | kind | subs | jobs run / sub | skipped by two-phase / sub | skipped by dependencies / sub | evaluate busy s / sub | of which jobs > 0.5 s |
|---|---|---|---|---|---|---|---|
| base-fork-2 | tle | 175 | 23.8 | - | - | 11.47 | 10.51 |
| full-fork-1 | tle | 196 | 15.0 | 7.0 | 1.8 | 2.38 | 1.18 |
| base-fork-2 | wa_small | 139 | 24.0 | - | - | 1.73 | 0 |
| full-fork-1 | wa_small | 147 | 12.0 | 2.6 | 9.4 | 1.06 | 0 |
| base-fork-2 | wa_zero | 59 | 23.2 | - | - | 1.60 | 0 |
| full-fork-1 | wa_zero | 61 | 5.2 | 7.6 | 13.1 | 0.45 | 0 |
| base-fork-2 | wa_overflow / re | 184 / 100 | 24.0 / 23.5 | - | - | 1.73 / 2.76 | 0 |
| full-fork-1 | wa_overflow / re | 200 / 98 | 15.2 / 15.3 | 7.0 / 7.0 | 1.6 / 1.9 | 1.31 / 1.47 | 0 |
| base-fork-2 | ac, ac_py, ac_java | 579 | 23.6-24.2 | - | - | 1.70-3.20 | 0 |
| full-fork-1 | ac, ac_py, ac_java | 559 | 23.4-24.8 | 0 | 0 | 2.09-3.96 | 0 |
| base-fork-1 / -2 / -3 | **all** | 1339 / 1328 / 1361 | 22.1-22.3 | - | - | **3.52 / 3.06 / 3.13** | 1.44 / 1.39 / 1.45 |
| full-fork-1 | **all** | 1338 | 17.0 | 3.2 | 2.3 | **1.81** | 0.17 |
| ramp-fork-500 | **all** | 2743 | 22.2 | - | - | **3.80** | 1.51 |
| ramp-upstream-500 | **all** | 2682 | 22.2 | - | - | **3.03** | 1.49 |
| ramp-fork-full-500 | **all** | 2666 | 17.0 | 3.2 | 1.9 | **1.48** | 0.16 |
| ramp-fork-full-500 | tle | 358 | 14.9 | 7.0 | 2.0 | 2.13 | 1.16 |

(CE submissions run no evaluate job in either profile, and AC ones skip
nothing.)

**The `full` profile did reduce the Workers' work:**

- At 250 users: evaluate jobs -24 % (22.3 to 17.0 per submission), and
  evaluate time per submission **-41 to -49 %** against each portable run
  (1.81 s against 3.06-3.52 s).
- At 500 users: -61 % against ramp-fork-500 (1.48 s against 3.80 s), and
  -51 % against ramp-upstream-500 (3.03 s). The second figure is the
  fairer one, since ramp-fork-500's jobs were slow (4.2).
- Almost all the time saved is the time-limit runs. Jobs over 0.5 s occur
  only on TLE submissions (about 1.1 s each: the 1 s limit plus overhead).
  They fell from 1.39-1.45 s to 0.17 s per submission, or 1.27 of the 1.32
  s saved per submission at 250 users. The short jobs fell in number but
  took about the same total, because full-fork-1 ran on a slower host
  (login p50 0.257 s; short jobs 97 ms mean against 80 ms).

**What can be attributed to screening alone, and what cannot.** `full`
turns on two-phase screening *and* `depends_on` (and ranks both contests).
No run had screening without dependencies: the harness has no such
profile.

- *Two-phase.* The ES log attributes every skipped row to its mechanism.
  Two-phase skipped 7.0 testcases on average per TLE, RE and wa_overflow
  submission: the 7 non-screening testcases of the failed group, which on
  a TLE submission are the 7 time-limit runs of the large group. That
  alone removes about 7 × 1.12 s ≈ 7.8 s of the 9.1 s saved per TLE
  submission at 250 users (11.47 s to 2.38 s). The 1.8 dependency skips
  per TLE submission are worth at most 2.0 s, even if all of them were
  time-limit runs. Since the time-limit runs are nearly all of the time
  saved, **about 80 % or more of the saved Worker time is screening's.**
- *Dependencies.* They skip more *jobs* on wrong answers (9.4 and 13.1 per
  wa_small and wa_zero submission against 2.6 and 7.6 for two-phase) but
  cheap ones (about 70-80 ms each). Their share of the time saved is at
  most about 20 %.
- *Not attributable:* a screening-only run would also run the screening
  testcases of groups that dependencies now skip whole, and the rest of a
  group whenever its screening passes. Its savings would therefore be
  somewhat below the combined ones, by an amount these runs cannot give.
  The latency cost at low load (below) mixes both mechanisms: the
  two-phase stage plus up to 4 dependency levels, each waiting for an ES
  flush.

**At 250 users the Workers had slack, so less work did not show as less
latency.** Workers were busy 2.0 of 8 on average in full-fork-1 against
2.7-3.0 on portable. The drain fell a little (45 s against 51-60 s), but
scored p50 rose from 11 s to 18.5 s and p95 from 37 s to 44.9 s, the cost
of the extra stages. Screening pays off once the Workers are the
bottleneck:

**A/B at 500 users (4 CWS, 8 Workers),** with upstream portable for
reference:

| metric | ramp-fork-500 (portable) | ramp-fork-full-500 (full) | ramp-upstream-500 (portable) |
|---|---|---|---|
| submissions_sent | 2743 | 2666 | 2682 |
| rejected / HTTP errors / mismatches | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| submit_p95 / submit_end_p95 | 0.072 / 0.162 s | 0.043 / 0.063 s | 0.046 / 0.055 s |
| scored_p50 | 19.6 s | 17.2 s | 16.3 s |
| scored_p95 | 228 s | **55.5 s** | 109 s |
| scored_max | 294 s | 85 s | 170 s |
| drain_after_stop_s | 291 s | **82 s** | 167 s |
| ES queue max | 11557 operations | 950 operations | 7441 operations |
| Workers busy, mean | 5.5 of 8 | 3.6 of 8 | 4.6 of 8 |
| evaluate busy s / submission | 3.80 | **1.48** | 3.03 |
| short-job time, mean | 110 ms | 79 ms | 74 ms |

At 500 users the fork's features relieve the Worker bottleneck. With a
quarter fewer jobs, and the time-limit runs gone, the Workers never fell
behind for long, and full beats both portable runs on scored p95 and
drain. Part of the gap to ramp-fork-500 is that run's slow jobs (4.2);
against upstream, full still halves the drain and scored p95. Two caveats
remain: `full` also changes the scoring (dependencies), and the solution
mix of the scenario (about 14 % TLE, 37 % wrong or crashing, 7 %
compilation errors) decides how much screening saves. A real contest's
mix differs (section 6).

## 4. Ramp

| target / profile | users | CWS | subs sent (per user) | rejected | HTTP errors | login p95 | submit p95 | submit end p95 | scored p50 | scored p95 | drain after stop | ES queue max | Workers busy mean | max CWS echo | peak PG |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fork portable (median of 3) | 250 | 2 | 1339 (5.4) | 0 | 0 | 0.218 | 0.035 | 0.05 | 11 | 37 | 52 s | 1674-1932 | 2.7-3.0 | 185 ms | 26 |
| fork portable | 500 | 4 | 2743 (5.5) | 0 | 0 | 0.268 | 0.072 | 0.162 | 19.6 | 228 | 291 s | 11557 | 5.5 | 999 ms | 32 |
| **fork portable** | **1000** | 4 | 4219 (4.2) | **30** | 0 | 0.293 | **1.61** | **2.78** | 210 | 742 | 838 s | 39550 | 6.3 | **9518 ms** | 32 |
| fork full | 500 | 4 | 2666 (5.3) | 0 | 0 | 0.22 | 0.043 | 0.063 | 17.2 | 55.5 | 82 s | 950 | 3.6 | 173 ms | 33 |
| upstream portable (median of 3) | 250 | 2 | 1328 (5.3) | 0 | 0 | 0.574 | 0.059 | 0.079 | 11.4 | 48.5 | 62 s | 2105-2503 | 2.3-3.0 | 490 ms | 30 |
| upstream portable | 500 | 4 | 2682 (5.4) | 0 | 0 | 0.401 | 0.046 | 0.055 | 16.3 | 109 | 167 s | 7441 | 4.6 | 200 ms | 49 |

(Latencies in seconds. Login failures and score and RWS mismatches are 0
in every run, and no run had stuck submissions.)

**Breaking level.** The fork breaks at **1000 users** on the criterion
"rejected submissions" (30 of 4219, 0.7 %). HTTP errors stayed at 0
(< 0.5 %), the drain was 838 s (< 900 s), nothing was stuck, and no
container ran out of memory (cms 1.48 GiB at the end). Upstream did not
break at 500, and by the user's cap it was not run higher, so its breaking
point is unknown.

### 4.1 Which component saturates first

**Workers first.** At 500 users all 8 were busy from the end burst until
the queue was empty. At 1000 users all 8 were busy from minute 1 of the
contest until the drain ended (monitor, queue per minute: `+1:1857/8.0
... +37:5300/8.0`). In the saturated windows (end burst and drain; see
the table in 4.2) 8 Workers did 41.7-50.0 testcase jobs/s, which at about
22 jobs per submission is 1.9-2.3 submissions/s, or 113-135 per minute.
At 1000 users the steady phase reached only about 116 submissions/min of
the 180 asked for. The closed-loop users wait for their results, so the
queue levelled off at 7,000-8,000 operations instead of growing. What
users saw: scored p50 210 s.

**The breaking component is CWS, at the end burst, with the host CPU
saturated:**

- **Host:** in the last 30 s before the stop, 14.7-15.4 of 16 logical CPUs
  were busy (`results/2026-10-07/hostsample.log`), with a load average of 21-24 on 8
  physical cores. The `cms` container used 6.1-6.6 CPUs (docker stats),
  `db` 0.7-0.9 and the driver 0.35. About 8 CPUs went to the isolate
  sandboxes, which run in the host's `box-*` cgroups, outside the
  container (see the caveats).
- **CWS:** the RPC `echo` of the four shards took 1.6-3.4 s at -26 s and
  2.2-9.5 s at -8 s (normally about 1 ms). The submit POSTs in the last
  5 s took p50 2.45 s and max 5.4 s on the client. In CWS's own access
  log their in-handler time was 0.1-2.1 s, mostly 0.5-1.3 s (normally
  20-40 ms), so 1-3 s more passed before the handler ran.
- **ES:** `echo` max 331 ms, CPU at most about 0.6 of a core. Not
  saturated.
- **PostgreSQL:** at most 5 active backends out of 32 connections, and no
  slow COMMIT in the last 100 s before the stop. There were advisory-lock
  waits of the activity recorder, a consequence of the stall (see 5, #39).
  Not saturated.
- **SS, PS, RWS:** `echo` max 26 ms, 14 ms and n/a; RWS answered every
  `/scores` poll (0 errors). Not saturated.

Whether 4 CWS processes alone, on hosts without the sandboxes, would carry
the 1000-user end burst cannot be told on one host. The sandboxes took
about half the CPU at the moment CWS stalled.

### 4.2 Why upstream drained faster than the fork at 500 users

Hypothesis tested (#6): the fork's ES `post_finish_lock` and debounced
result flush steal Worker time when the Workers are saturated.

The test splits Worker time from the Worker logs over the saturated
window (from stop - 300 s to the last monitor sample with a non-empty
queue; `adhoc/satwindow.py`):

| run | window | jobs done | jobs/s | in jobs | gaps inside a group | idle between groups | jobs per group | idle per group | evaluate job p50 | Worker python CPU per job |
|---|---|---|---|---|---|---|---|---|---|---|
| ramp-fork-500 | 586 s | 24437 | **41.7** | 95.6 % | 0.4 % | **4.0 %** | 25.0 | 189 ms | **102 ms** | **42.7 ms** |
| ramp-upstream-500 | 458 s | 22897 | **50.0** | 91.2 % | 0.3 % | **8.5 %** | 17.8 | 242 ms | **70 ms** | **30.2 ms** |
| ramp-fork-1000 | 1130 s | 49784 | 44.1 | 95.3 % | 0.4 % | 4.2 % | 24.9 | 188 ms | 88 ms | 37.7 ms |
| ramp-fork-full-500 | 368 s | 18345 | 49.8 | 61.6 % | 0.3 % | 38.0 % | 8.9 | 546 ms | 75 ms | 33.2 ms |

**Verdict: refuted.** Under saturation the fork's Workers sat idle between
job groups (waiting for ES to take a result and send new work, lock waits
included) for 4.0 % of the time, against 8.5 % on upstream. The fork's
idle per group is also shorter. ES costs the fork's Workers *less*
capacity than upstream's. `lockwait.py` reports more total result wait on
the fork (363 against 183 worker-s), but that lag runs to ES's
"succeeded" log line, after the processing, and the worker-side idle time
above shows it does not hold the Workers.

**What does differ is the time per job.** Both builds ran about 22.2
evaluate jobs per submission. TLE-like jobs (over 0.5 s) took the same
time on both (1.19 s against 1.13 s mean). Short jobs took 110 ms on the
fork and 74 ms on upstream (means), and the Worker processes' own CPU per
job was 42.7 ms against 30.2 ms.

Why the fork's jobs were slower in that run is **inconclusive**. The
evidence points away from fork code and towards host state, but one run
per target cannot settle it:

- The code these jobs run is the same: `git diff cc9dfafb dc08291f` over
  `cms/service/Worker.py`, `cms/grading/Sandbox.py`, `Job.py`,
  `tasktypes/`, `languages/` and the trusted, whitediff and stats steps
  is empty. Both Workers are still gevent services. They differ in
  libraries (SQLAlchemy 2 against 1.3, and the rest of `constraints.txt`).
- The same fork code ran short jobs at about 80 ms (mean 80-99 ms at 250
  users, 79 ms in ramp-fork-full-500), about as fast as upstream (71-95 ms
  at 250). The 105-110 ms cases (ramp-fork-500, ramp-fork-1000) are the
  runs with the most host load.
- In ramp-fork-500 *every* fork process was slower per unit of work than
  in the same fork code's other runs. CWS used 16.6 ms of CPU per request,
  against 11.8-14.3 ms at 250 users and 12.5 ms in ramp-fork-full-500
  (same users, same 4 CWS). A uniform slowdown across unrelated code paths
  fits a slower host better than a slower component.
- ramp-fork-500 ran right after full-fork-1, on a hotter host than
  ramp-upstream-500: mean Tctl 93.8 °C against 90.3 °C, mean clock 3043
  against 3188 MHz, load average 13.2 against 8.5. With 8 physical cores,
  more than 8 busy threads share cores through SMT and every thread slows
  down.
- A feedback loop amplifies any start: slower jobs give longer queues,
  longer queues give more status polls (19753 against 16568) and more CWS
  CPU, which loads the host further. The fork's `cms` container averaged
  353 % CPU against 275 %.
- Against that, the fork's Worker processes used somewhat more CPU per job
  in the baseline too (median 32.5 ms against 31.7 ms; pairs 38.6/31.7,
  32.5/28.3, 32.3/33.7). That is small and mixed, but a modest real
  library cost cannot be ruled out.

**To settle it:** repeat the 500-user pair (`ramp-fork-500-2`,
`ramp-upstream-500-2`, about 80 minutes), ideally with the host at a
stable temperature. It was queued and cancelled under the user's decision
to go straight to the report.

### 4.3 The 30 rejected submissions at 1000 users (#5)

- When they were sent (`submissions.jsonl`, `t_submit - stop`): 24 at
  -3.1 to -0.0 s, so before the stop, and 6 at 0.0 to +1.0 s, after it.
  The 6 late ones were refused correctly (the end burst is scheduled up to
  the stop, and those requests left late, behind the user's other
  requests or the driver's own loop). The 24 early ones are the finding.
  The last *accepted* submission was sent 1.5 s before the stop, and 197
  sent in the last 10 s were accepted.
- Why: the request time is taken when the handler object is built
  (`CommonRequestHandler.__init__`, `cms/server/util.py:268`). That is
  after the request waited for the stalled event loop. The phase check
  sees the contest as over, and `SubmitHandler.post`
  (`cms/server/contest/handlers/tasksubmission.py:82-85`) redirects to the
  contest page with no notification. All 30 got `302` to `../../../loada`
  or `../../../loadb`. CWS logs nothing for this case: the `302` is the
  same status as an accepted submission.
- The stall: see 4.1. CWS event loops stopped for 2-9.5 s, submits took
  2.45 s p50 and up to 5.4 s in the last 5 s, and the host CPU was
  saturated.
- This matches #5 and the workaround noted there: 4 CWS were not enough at
  1000 users on this host.

## 5. Inputs for Other Issues

### #29 (Go): what saturates first, and is it Python-bound?

- **First: the Workers**, about 2 submissions/s with 8 Workers on these
  synthetic testcases. Most of their cost is not Python. Over a whole run
  the Worker processes spent 30-43 ms of their own CPU per evaluate job,
  on jobs of 66-110 ms (median). The isolate sandboxes took another 3-8
  logical CPUs outside the containers. Real testcases are far longer than
  "sum of N numbers", which shrinks the Python share further. The lever is
  more Workers and Worker hosts, and two-phase screening, which cut
  evaluate time 41-61 % (51 % against upstream at 500 users; section 3.1).
  A rewrite would at most cut the 30-43 ms of Python per job.
- **Then: CWS at the end burst** (break at 1000 users, 4 CWS, shared
  host). CWS costs 12-17 ms of CPU per request on both builds (Tornado,
  SQLAlchemy, templates: Python), so the per-request cost is
  Python-bound. The rejections, though, come from a design issue that a
  fix can address without a new stack: the time taken after the loop wait
  and the sync DB work on the loop (#5). More CWS processes and hosts
  scale it horizontally.
- **Not saturated at any level:** ES (≤ 0.6 core), SS, PS, RWS and
  PostgreSQL (≤ 5 active backends). ES's event loop is not saturated, but
  on the fork it is blocked for tens of milliseconds far more often than
  upstream's: its `echo` p99 was 18-39 ms against 3-11 ms at 250 users
  (medians 36 against 3.6 ms, about 10×), and 46-61 ms in the
  full-profile and 1000-user runs (max 98-331 ms on the fork). The cause
  was not investigated; it is an input for #6.
- These runs cannot support conclusions about 100,000 participants. One
  host, and 1000 users at most; the scaling question needs multi-host runs.
- On 2026-10-07 the #29 gate was decided from these results: no migration
  for now.

### #42: peak PostgreSQL connections and the pool size

| run | users | CWS | peak backends (cmsdb) | per process, max |
|---|---|---|---|---|
| base-fork-* | 250 | 2 | 24-27 | CWS 3, ES 5-8, PS 1-3, SS 1, AWS 1, Worker 1-2 |
| base-upstream-* | 250 | 2 | 28-33 | CWS 5-8, ES 5, PS 2-3, SS 1, AWS 1, Worker 1-2 |
| ramp-fork-500 | 500 | 4 | 32 | CWS 3, ES 8, PS 2, SS 1, Worker 1-2 |
| ramp-fork-1000 | 1000 | 4 | 32 | CWS 3, ES 5, PS 2, SS 2, Worker 1-2 |
| ramp-fork-full-500 | 500 | 4 | 33 | CWS 3, ES 9, PS 3, SS 1, Worker 1-2 |
| ramp-upstream-500 | 500 | 4 | 49 | CWS 5-7, **ES 15**, PS 2, SS 1, Worker 1-2 |

- The fork never passed 33 of `max_connections = 100`, 1000 users
  included. Per process it never passed 3 per CWS and 9 for ES, far below
  the SQLAlchemy defaults of `pool_size` 5 + `max_overflow` 10 per engine
  (×2 engines on the fork). Upstream's ES hit exactly 15, its pool
  ceiling, at 500 users.
- The per-process maxima add up to about 3 × CWS + 2 × Workers + 15 (ES,
  SS, PS, AWS, monitor), or 43 for 4 CWS and 8 Workers. The maxima do not
  happen together: the simultaneous peak with that layout was 32-33.
  Under load the harness rose from about 17 backends at rest to 33 at
  peak, about 16 more. Production has about 36 at rest (#42), so an
  estimated peak is about 36 + 16 ≈ 52 at 190-270 contestants. That is
  conservative: production now runs 6 Workers, not 8 (sum of maxima 39
  instead of 43).
- Suggestion: **keep `max_connections = 100`.** If the pools are bounded,
  `pool_size` 5 and `max_overflow` 5 per engine would still be 2-3× any
  per-process maximum seen. These numbers come from 2-second samples.
  Short-lived unpooled LargeObject connections (`cms/db/fsobject.py`) can
  slip between them, so give the budget a margin rather than sizing it to
  the peak.

### #39: DB stalls

- **Yes, at 1000 users (fork).** There were 23 COMMITs of 1.1-6.6 s in two
  clusters, at -248 to -216 s and at -122 to -118 s before the stop. At
  -122.5 s seven backends' COMMITs of 4.2-6.6 s finished at the same
  instant. That points to a storage (WAL fsync) stall on the test
  workstation's encrypted btrfs volume on a QLC SSD rather than lock
  contention, so check production disk latency before relying on these
  numbers. The effects:
  - ES's result flush that started at -129.7 s took **11.3 s**, under
    `post_finish_lock`, and Workers waited up to **10.3 s** for ES to take
    their results (`lockwait.py` max). This is the scenario #39 describes.
  - CWS `echo` reached 4.6 s at -132 s (CWS commits on the event loop).
- Also at 1000 users: at the stop the activity recorder's flush (advisory
  locks in namespace `0x41435456`) waited **22.6-24.4 s** for a lock. The
  monitor saw a transaction **idle in transaction for 23.7 s** at +13.5 s.
  A CWS whose loop was stalled held its flush transaction open while other
  shards waited on the same participations. It does not block requests
  (the flush is async), but it holds connections and locks.
- Upstream at 250 users: 3 COMMITs of 1.45-2.06 s (u1 and u3). Fork at 250
  and 500, and upstream at 500: none.
- For the proposed timeouts: `lock_timeout` 30 s and
  `idle_in_transaction_session_timeout` 120 s would not have fired falsely
  in any run (worst 24.4 s and 23.7 s). A CWS `statement_timeout` under
  about 7 s would have killed COMMITs at 1000 users.

### #5 and #6: rejected or late submissions, ES latency

- **#5:** rejected only at 1000 users. There were 24 sent before the stop
  (0.0-3.1 s) and 6 after (4.3). None at 250 or 500 users on either build.
- **#6, ES latency:** scored p50 is about 11 s at 250 users on both builds
  and 16-20 s at 500. Under saturation the latency is Worker queueing
  (scored p95 228 s fork / 109 s upstream at 500, 742 s at 1000). The
  flushes (`flushstats.py`, whole run):

  | run | flushes | duration p50 / p95 / max | results per flush p50 | gap p50 / p95 |
  |---|---|---|---|---|
  | base-fork-1..3 | 367-386 | 0.20-0.32 / 0.38-0.57 / 0.54-0.93 s | 100 | 2.6-3.0 / 9.8-11.6 s |
  | base-upstream-1..3 | 374-389 | 0.21-0.28 / 0.41-0.76 / 0.90-1.76 s | 100 | 2.7-3.2 / 9.8-12.1 s |
  | full-fork-1 | 309 | 0.68 / 1.26 / 1.81 s | 85 | 4.0 / 11.1 s |
  | ramp-fork-500 | 625 | 0.48 / 0.82 / 1.61 s | 101 | 2.4 / 4.9 s |
  | ramp-upstream-500 | 605 | 0.34 / 0.52 / 1.16 s | 100 | 1.9 / 6.2 s |
  | ramp-fork-full-500 | 470 | 0.82 / 1.41 / 1.91 s | 103 | 2.1 / 11.1 s |
  | ramp-fork-1000 | 973 | 0.51 / 0.99 / 11.27 s | 100 | 2.3 / 3.9 s |

  The fork's flush still waits for the 100-result batch. In the full
  profile, where every stage waits for a flush, that is the main cost at
  low load (scored p50 18.5 s against 11 s). Workers lost 4.0-4.2 % of
  their capacity to ES under saturation on the fork (8.5 % upstream),
  much less than the 13-17 % #6 quotes from the recovered run. In the full
  profile the result waits grow 3-4× (502 against 127-177 worker-s at
  250; 1506 against 363 worker-s at 500). That costs nothing while the
  Workers have slack (ramp-fork-full-500 was 38 % idle), but it would cost
  capacity under saturation. So the premise of releasing the Worker before
  the lock (13-17 % of capacity lost) was not confirmed here. The max-age
  flush does matter, because every two-phase or dependency stage waits for
  a flush. The fork's ES loop is also blocked more often than upstream's
  (`echo` p99 about 10× upstream's at 250 users; see #29 above). On
  2026-10-07 #6 was moved to wave 1 of the post-10-10 roadmap.
- **Perceived latency** (browser backoff, `summary.md`): 1.15-1.35× the
  server latency at p50 in every run and phase. At 1000 users the end
  burst saw p95 1023 s perceived against 786 s server latency.

## 6. Against OMI 2025 and Readiness for 2026-10-10

Production facts come from read-only SQL on the production host's
database (2026-10-07) and from the deployment's own configuration.
Everything marked *estimate* is arithmetic on stated assumptions, not a
measurement.

### 6.1 The harness against OMI 2025

| | harness, 250 users | OMI 2025 day 1 | OMI 2025 day 2 |
|---|---|---|---|
| contestants | 250 | 99 (96 submitted) | 99 (96 submitted) |
| contest length | 25 min | 5 h | 5 h |
| submissions per contestant | 5.3-5.5 (≈ 13 per hour) | 14 (2.8 per hour) | 23.8 (4.8 per hour) |
| submissions per minute, mean / peak | ≈ 53 / ≈ 100 (end burst) | 4.5 / 23 | 7.6 / 40 |
| share at the end | 37 % in the last 5 min | 11-12 % in the last 15 min | 11-12 % in the last 15 min |
| evaluations per submission | 22.3 (17.0 run with `full`) | 36.6 | 70.1 |
| time limits | 1 s | 1-10 s | 1-10 s |
| Worker time per submission | 3.3-3.8 s (job wall time, all jobs) | ≈ 34-37 s (sandbox wall time, last 15 min) | ≈ 36-39 s |
| Worker time per evaluation | ≈ 0.15-0.17 s | ≈ 0.93-1.0 s | ≈ 0.52-0.56 s |
| busy-Worker equivalents at the end | 5-7.5 of 8 in the last 5 min | 6.1 (last 15 min) | 11.0 (last 15 min) |
| submit to scored, p50 / p95 / max | 11 / 37 / 55 s | 8 / 27 / 274 s | 11 / 128 / 524 s (8.7 min) |

(OMI 2025 Worker time per submission = the last 15 minutes' sandbox wall
time, 5452 s and 9899 s, divided by the 11-12 % of the day's submissions
sent in those minutes. It excludes compilation, 2.2-3.0 s on average,
which section 6.2 adds back, and the per-job overhead. It is wall time,
which sharing inflates; see the biases in 6.2.)

Reading:

- Each harness contestant submits 2.7-4.7× as often as a 2025 one, so the
  harness at 250 users loads **CWS** about as much as 10-10 will (section 6.2:
  about 46-112 submissions per minute at the 10-10 peak, against the
  harness's ≈ 100 per minute end burst, which 2 CWS carried with 0
  rejections and a worst `echo` of 185 ms on the fork and 490 ms upstream).
- Each real submission costs about **10×** the Worker time of a harness
  one: longer time limits, more and larger testcases, a Communication task
  per day. Day 2 of 2025's last 15 minutes needed about 11 busy-Worker
  equivalents of sandbox wall time and left a backlog (p95 128 s, max
  8.7 min) with 99 contestants. The harness's "Workers saturate first" finding therefore
  carries over to 10-10, and more strongly, while its capacity in
  submissions per second does not (README limit: synthetic, cheap
  testcases).
- 2025 ran an older fork build (gevent era), not upstream cc9dfafb, on 4
  or 6 cores (not known for sure), so it is not a third point of the
  build comparison.

### 6.2 Projection for 2026-10-10 (estimate)

Assumptions:

- **A1:** 190-270 contestants (130-190 in one contest and 60-80 in the
  other), each behaving like a 2025 contestant: 14-23.8 submissions in a
  5-hour day, 11-12 % of them in the last 15 minutes and 7-10 % in the 15
  minutes before (2025: 19-21 % in the last 30). If 10-10's length or task
  count differs, scale accordingly.
- **A2:** at most 20 testcases per task, so at most 20 evaluations per
  compiled submission without screening (2025: 36.6-70.1). Compilation
  takes what it took in 2025 (2.2-3.0 s per submission, all C++). The
  testcase count does not change it.
- **A3:** sandbox time per evaluation as in 2025 (0.52-1.0 s). It depends
  on 10-10's time limits and testcase sizes, which are unknown.
- **A4:** the production host is a small VM: **6 vCPUs on 3 physical
  cores (SMT)**, with slower, older server-class cores. The services,
  the database included, share the host. It runs 4 CWS and **6
  Workers**, with two-phase on.
- **A5, supply:**
  - *In work:* 3 cores × 1.2-1.3 (the SMT gain) ≈ 3.6-3.9 core-equivalents
    in all. Each thread is also slower than the test workstation's (a
    Zen 3 at about 3 GHz under load), by about 1.4-2× (estimate). The
    harness's capacity numbers do not carry over.
  - *In threads* (the time isolate and the DB measure): at the end the
    services take about **1.1-2.5 of the 6 threads**. Basis: in the
    harness's 250-user end burst (about 100 submissions/min, about the
    10-10 peak), CWS, ES, PostgreSQL, SS, PS and LogService took 0.6-0.7
    of a workstation CPU. The per-job overhead (Worker processes, isolate
    set-up) was about 0.055-0.07 CPU-s per job (the `cms` container's
    end-burst CPU minus those services, divided by the jobs per second).
    That is about 0.2-0.55 CPU at the roughly 3.5-8 evaluate jobs/s of
    10-10 (3.5-4.9 threads at about 0.6-1.0 s per evaluation on these
    slower cores). Then ×1.4-2 for the slower
    threads. That leaves **3.5-4.9 threads, or 3,150-4,400
    thread-seconds per 15 minutes**, for the sandboxes. More Workers
    than that do not add CPU.

Estimates:

- **Submission rate:** 9-21 per minute on average and 46-112 at the peak
  minute (2025's means and peaks × 190/96 to 270/96).
- **Demand in the last 15 minutes:** 2025's measured sandbox wall time
  (5,452 s on day 1 and 9,899 s on day 2) × the contestant ratio × 20 /
  evaluations per submission, **plus the compilation of those
  submissions**:

  | case | submissions in the last 15 min | evaluation | compilation | total sandbox-s |
  |---|---|---|---|---|
  | day-1-like, 190 contestants | 292 | 5,896 | 642 | 6,539 |
  | day-1-like, 270 contestants | 453 | 8,379 | 996 | 9,375 |
  | day-2-like, 190 contestants | 497 | 5,590 | 1,492 | 7,081 |
  | day-2-like, 270 contestants | 771 | 7,943 | 2,313 | 10,256 |

  That is **about 6,500-10,300 sandbox-seconds**, or 7.3-11.4 sandboxes
  busy for the whole 15 minutes, against 3.5-4.9 threads. The 15 minutes
  before carry another 3,800-9,300 s.
- **Backlog at the stop without screening:** the time to the last result
  is 900 s × (demand / supply − 1). With no backlog at T-15 that is
  **about 7-34 minutes** (6,539 s against 4,400, then 10,256 s against
  3,150). At the upper end the T-30 to T-15 window (up to 9,300 s) also
  exceeds the supply. The queue then grows from T-30, and the last result
  could come **about an hour** (63 minutes) after the stop. 2025 needed
  4.6 and 8.7 minutes.
- **With screening applied:** the harness saved 41-51 % of evaluate time
  with its solution mix (14 % TLE, 37 % wrong or crashing). A real mix is
  unknown. Assuming a 20-50 % saving on the evaluations, and none on
  compilation, the demand becomes 3,600-8,700 s: **none to about 26
  minutes**.
- **Biases.** Their direction is known, their size is not.
  - *Towards a shorter backlog:* the demand is 2025's sandbox **wall**
    time, while the supply is CPU threads. In the close of 2025's day 2,
    the record implies at least about 7-7.5 sandbox-wall-seconds per
    second: 9,899 s, or about 10,700 s with compilation, all scored within
    15 + 8.7 minutes. That is more than its host's 4-6 vCPUs. So more
    sandboxes ran than there were threads, and sharing inflated each
    one's wall time. The factor is at least about 1.2. It is at most
    about 2-3, from the 0 wall-clock TLEs: a TLE run that got less than
    1/(2 + 1/TL) of a thread would have hit the wall limit. Deflated by
    that factor, the backlog without screening is none to about 26
    minutes.
  - *Towards a longer backlog:* this VM's slower, older cores may be
    slower than 2025's host's, and 2025's sandboxes may often have had an idle SMT
    sibling. Either way each evaluation takes longer here: up to about
    1.5× when both threads of a core are busy. The CPU of Communication
    tasks' managers is not in the evaluation times, and compiling on
    these cores may take longer than 2025's 2.2-3.0 s.
- **Reading:** expect the last result **about 7-34 minutes after the stop
  without screening, and none to about 26 minutes with it**. These are
  estimates; the plausible bracket runs from no backlog to about an hour.
- **CWS and PostgreSQL:** not expected to limit. The peak submission rate
  is about the harness's 250-user end burst, and PostgreSQL never passed
  33 connections or 5 active backends in any run. One caveat: at the end
  the services share 3 slow cores with saturated sandboxes, the condition
  under which CWS stalled at 1000 users here. Fewer Workers leave the
  services more CPU.
- **Timing fairness (false TLEs).** There are two paths:
  1. *Wall clock.* The limit is 2 × TL + 1 s
     (`cms/grading/steps/evaluation.py:205`). A run within its CPU limit
     is killed by the wall clock only if it gets less than 1/(2 + 1/TL)
     of a thread: 33 % for TL 1 s, 44-45 % for 3.5-5 s, 48 % for 10 s.
     With 6 Workers on the 3.5-4.9 threads left, each busy sandbox gets
     about 58-82 % of a thread (estimate). With 8 Workers it would get
     44-61 %, at the threshold for long limits; with 5, 70-98 %. In 2025
     there were 0 wall-clock TLEs among 583 submissions with a TLE.
     **Risk with 6 Workers: low.**
  2. *CPU time under SMT.* When both threads of a core are busy, each
     runs at about 60-65 % of an idle core (estimate, from the 1.2-1.3×
     SMT gain), and isolate counts the thread's time as CPU time. The
     same program then needs more CPU time. This happens with any Worker
     count once the threads are busy, and the wall-clock audit below
     cannot see it. In 2025's record, the CPU TLEs are spread evenly from
     minute 288 to the end and repeat per task, so they look genuine.
     Passing evaluations in the last 30 minutes against before (an
     uncontrolled comparison, since the solution mix changes): short
     programs ran 2-3× slower, but only by about 20 ms; long-running
     tasks moved -17 % to +13 %. Nothing suggests near-limit passes were
     pushed over the limit. But one task (TL 3.5 s) had 269 passing
     evaluations above 80 % of its limit, and limits that tight are where
     this risk concentrates. Whether 2025 ran on this kind of SMT host is
     not known. **Risk: low to moderate, concentrated in tasks whose
     intended solutions run close to the limit.**

### 6.3 What must or should be done before 2026-10-10

**(1) Ops-only changes, allowed during the code freeze,** ranked.
Capacity can come only from less work per submission (screening, the
≤ 20 testcases) and from not wasting the 3 cores.

1. **Must: make screening apply to the 10-10 tasks.** Two-phase is on,
   but it does nothing for testcases named `000`, `001`, … (italy_yaml).
   For tasks with `GroupMin`/`GroupMul`/`GroupThreshold` and groups that
   are exactly the subtasks, rename the testcases to `<group>-<nn>-<tag>`,
   with `sample` and `scr-…` screening tags, as in
   `docs/two-phase-grading.md`. Do it on a dataset clone if there are
   submissions, and check every codename for an accidental `scr`. **Never
   on `Sum` tasks**, where skipping changes scores. Evidence: 41-51 % less
   evaluate time at 250-500 users, about 80 % of it from skipping
   time-limit runs; the drain at 500 users went from 291 s to 82 s. The
   real gain is unknown; 20-50 % is an estimate. Verify on the clone, with
   known solutions, that the scores match the original dataset.
2. **Must: no more than 6 Workers, never back to 8; recommend 5.** The
   sandboxes cannot use more than the 3.5-4.9 threads the services leave
   (estimate). So 5 Workers give them the same CPU as 6 whenever the
   services use a thread or more, which they do at the end. They also
   leave CWS and PostgreSQL more room in the end burst, and keep each
   sandbox further from the wall-clock threshold: 70-98 % of a thread,
   against 58-82 % with 6. 8 Workers would put sandboxes at 44-61 % of a
   thread, at the wall-clock threshold for long limits.
3. **Must: publish the final ranking only after the ES queue is empty**
   and no submission is pending (runbook). Expect about 7-34 minutes
   without screening and up to about 26 with it, possibly up to about an
   hour at the upper end (estimates; 2025 needed 4.6 and 8.7 minutes with
   99 contestants).
4. **Must: run the agreed post-contest TLE audit.** Run the TLE query on
   the 10-10 contests. If no evaluation has the wall-clock message
   ("Execution timed out (wall clock limit exceeded)"), re-evaluate
   nothing. Otherwise re-evaluate only those submissions, with the
   per-submission "E" button in AWS: a full per-task re-evaluation would
   take hours on this host. This catches the wall-clock path only.
5. **Must check: memory.** The Workers' count × the largest task memory
   limit, plus PostgreSQL and the services (the `cms` container used
   1.1-1.5 GiB and PostgreSQL 0.1-0.15 GiB in the harness), must fit in
   the host's RAM with a margin.
6. **Should: calibrate the time limits on the production host itself.**
   Run the reference solutions there, and the slowest solutions meant to
   pass, ideally while the host is busy, for example during a rehearsal
   with the Workers loaded. Set each limit so that they sit well below
   about 50-60 % of it. This is the only mitigation for the CPU-time path
   under SMT, which no audit can detect afterwards. Tasks whose good
   solutions run near the limit come first; 2025 had one with 269 passing
   evaluations above 80 % of its limit.
7. **Should: a stop margin for #5.** Make the CMS stop time about 1 minute
   later than the announced end (the workaround in #5), or keep the stop
   and accept the risk. In-time submissions were rejected only at 1000
   users here (24 of them), with none at 250-500 users in 10 runs. But the
   production host's CPU will be saturated at the end, the condition of
   that failure. This is a rules decision for the organizers.
8. **Should: keep 4 CWS and `max_connections = 100`** (no change). The
   peak was 33 backends in any run. Production has about 36 at rest, for
   an estimated peak of about 52 (section 5, #42).
9. **Could: measure the production host's disk sync latency** (for
   example `pg_test_fsync`). COMMIT stalls of 4-6.6 s on the test
   workstation froze an ES flush for 11 s at 1000 users.
10. **During the contest:** watch the ES queue (AWS) and the host load,
    and run nothing else heavy on the production host.

**(2) Code changes** (they would need a deploy by about 2026-10-07):

| change | evidence | verdict |
|---|---|---|
| #5: take the submission time on arrival, or a grace window; visible error | only at 1000 users on a shared 8-core workstation; 0 at 250-500 users; the stop margin (ops item 7) covers the case | **wait**: touches contest timing 3 days before the contest; weak evidence at 10-10's scale |
| #6: max-age flush, release the Worker before `post_finish_lock`, bulk inserts | the premise of releasing the Worker before the lock was not confirmed: under saturation the fork's Workers lose 4.0-4.2 % to ES (upstream 8.5 %). The max-age flush matters for latency, since two-phase and dependency stages each wait for a flush (`full` scored p50 18.5 s against 11 s) | **after 10-10** (wave 1 of the post-10-10 roadmap): a latency gain, not a capacity gain, and not worth a deploy 3 days before the contest |
| #39: DB connection, lock and idle timeouts | stalls only at 1000 users on the test workstation's QLC disk; the proposed values would not have fired falsely (worst lock wait 24.4 s, idle in transaction 23.7 s); never run with them on | **wait**: changes every connection; untested under load |
| #42: pool sizes | peak 33 of 100 | **no change needed** |
| #6: cap the status-poll backoff | perceived latency 1.15-1.35× the server's | **wait**: cosmetic |

No code change before 2026-10-10 is justified by this evidence. The
levers for the real risk, Worker capacity and timing at the end, are
screening (task data), the Worker count against the threads, time
limits calibrated on the production host, the post-contest TLE audit,
and publishing the ranking only after the queue drains.

## 7. Caveats

From the design doc:

- The two builds have different architectures (gevent/WSGI, Tornado 4.5 and
  SQLAlchemy 1.3 upstream; asyncio, Tornado 6 and SQLAlchemy 2 on the
  fork). That difference is what the comparison measures.
- Fork-only work cannot be turned off and must be stated: the participant
  activity recorder, cookie refresh on polls, and more PG connections per
  process.
- `full` has no upstream counterpart. It is reported as a third
  configuration.

From the README's limits:

- **One host:** the driver, the database, RWS and every CMS service share
  the same cores, so the driver and PostgreSQL take CPU from CMS.
- **No proxy and no TLS:** the driver talks to the CWS shards directly
  over HTTP, round-robin; nginx's buffering and TLS cost are not there.
- **Synthetic, cheap testcases:** "sum of N numbers" runs in milliseconds,
  so the workers are faster than with real tasks; worker capacity
  numbers do not carry over.
- **One client IP:** every user comes from the driver container's
  address.
- **Fork-only work that cannot be turned off:** the participant activity
  recorder, the cookie refresh on polls and the extra PostgreSQL
  connections per process run on the fork in both profiles.

Observed today:

- **Host speed drifts by about 20 %** between runs (bcrypt login p50
  0.214-0.261 s with the same code). The workstation's mobile CPU ran at
  90-95 °C with a mean clock of 2.9-3.6 GHz. The baseline alternation spreads this
  evenly, but the ramp runs are single runs. A difference under about
  20 % between two single runs is noise, and the 500-user fork/upstream
  gap (4.2) may be partly or wholly this.
- **8 physical cores, not 16:** the 16 logical CPUs are SMT threads. Past
  about 8 busy threads, every thread slows down.
- **docker stats does not count contestant code:** isolate puts sandboxes
  in its own cgroups on the host (`box-*`), outside the `cms` container.
  The `cms` CPU in `summary.md` and `metrics.json` therefore excludes
  compilation and evaluation in the sandbox. The host-wide busy CPUs
  (`results/2026-10-07/hostsample.log`, recorded from ramp-fork-1000 on; ramp-fork-500
  has only clock, temperature and load) show the sandboxes at 3-8
  logical CPUs.
- **The "fork-only extra PG connections" did not show:** the fork used
  fewer connections per process than upstream (section 5, #42).
- **The driver is a closed loop:** when grading is slow, users submit less
  (4.19 per user at 1000 users against 5.4 at 250-500, about 116 against
  180 submissions/min asked). The offered load at 1000 users is lower than
  nominal, and a real contest with impatient resubmissions would be
  harsher.
- **Per-process CPU from the monitor is unreliable inside stalls:** a
  sample's time is taken before its RPC `echo` calls, which can take
  seconds. Section 4.1 uses docker stats and the host sampler for the end
  burst.
- **2-second PG sampling** can miss short-lived connections (unpooled
  LargeObject connections), so the peak counts are lower bounds.
- **Storage:** the DB volume is on an encrypted btrfs volume on a QLC
  SSD. The COMMIT stalls at 1000 users may be specific to this
  workstation.
- **Other light background load** ran on the workstation, using about
  0.3-0.5 logical CPUs at rest.
- **Harness fix made before the runs** (197ab8c7): `setup_contest.py`
  fixed the contest start before hashing the users' bcrypt passwords
  (0.23 s each), which ate into the login window. At 1000 users or more
  the driver would have started after the contest start. Every run in
  this report has the fix; REPORT-2026-09-30 did not.
- **Coverage:** one full-profile run at 250 users; single runs at 500 and
  1000; no upstream run past 500 and no run at 2000 (user cap).
- **No sandbox times:** `db_export.py` exports only the number of
  evaluations, and the DB volume is removed at teardown, so the
  evaluations' `execution_time` and `execution_wall_clock_time` are not
  available for these runs. Worker work is measured as job wall time from
  the Worker logs, which includes the per-job overhead.
- **No screening-only run:** `full` turns on two-phase and `depends_on`
  together. The split of the saving between them (3.1) rests on the ES
  log's skip counts per mechanism, and the latency cost of the stages
  cannot be split.
- **The OMI 2025 comparison and the 10-10 projection** (section 6) combine
  production numbers measured differently (sandbox wall time from the DB)
  with harness numbers (job wall time from logs), on different hardware
  and builds. 2025's host and Worker count are not known for sure, and the
  production host's per-thread speed against the test workstation's
  (about 1.4-2× slower) is an estimate. They are order-of-magnitude
  estimates.

## Method of the Ad-hoc Analyses

Besides `analyze.py`, `compare.py`, `flushstats.py` and `lockwait.py`,
these scripts were used. They are in `cmstestsuite/loadtest/adhoc/` (run
them from `cmstestsuite/loadtest/`), and they read only a full run
directory, `out/<run>`, which is kept locally:

- `abkind.py`: per solution kind, the evaluate jobs run, the Worker time
  and the skipped testcases per mechanism (ES log), joining
  `db_export.json` and `submissions.jsonl`.
- `jobs.py`: completed "Starting job"/"Finished job" pairs per Worker log
  (`[0-9]*.log`; `last.log` is a copy), busy time in jobs, and
  submissions per user from `db_export.json`.
- `jobdur.py`: evaluate and compile job duration percentiles, and Worker
  process CPU per job (from `monitor.jsonl`).
- `satwindow.py`: Worker time split into jobs, in-group gaps and idle
  between groups, over the saturated window.
- `cpu.py`: CPU per service group over the run, and CWS CPU per request.
- `windows.py`: per-window CPU, `echo`, queue and PG samples.
- `hostsample.sh`: every 10 s, the host's busy logical CPUs (from
  `/proc/stat`), mean clock, Tctl and load average.
