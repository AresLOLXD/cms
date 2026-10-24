# CMS load-test harness

A contest-shaped load test of CMS on one host: a fresh stack in Docker, a
driver that behaves like browsers during a contest, and an analysis that
checks every score and reports latencies, saturation and errors. It runs
the same scenario against two **targets**, this fork and stock upstream
CMS, so that the numbers can be compared (issue #7).

It is not part of the pytest suite. Its unit tests live in
`cmstestsuite/unit_tests/loadtest/` and need no Docker; only
`db_export_test.py` needs the test database the other database tests use.

## What it measures

The scenario (`scenario.py`, `setup_contest.py`, `driver.py`):

- Two contests, `loada` (4 tasks) and `loadb` (3 tasks), with GroupMin,
  GroupMul and GroupThreshold subtasks. Every task is "print the sum of N
  numbers"; the subtask categories are small, medium and large (the large
  sum overflows 32 bits), and every group has a sample, a screening WA
  case and a screening TLE case.
- Users with bcrypt passwords, split between the contests (`--users-a`,
  `--users-b`).
- **Login burst:** every user arrives during the `--login-window` seconds
  before the start (60% of them in the last third), loads the contest
  page, logs in with the form and loads the page again, then polls
  `/notifications` every 30 s like `contest.html`.
- **Start burst:** within 20 s of the start every user reloads the
  contest page and opens 2-3 task descriptions and statements.
- **Steady phase:** a closed loop per user: think, sometimes browse,
  submit, poll the submission status with the backoff of
  `task_submissions.html` until it is final, maybe open the details. The
  poll starts at 1 s and multiplies the delay by a factor between 1.4
  and 1.6 that depends on the submission id, without a limit like
  upstream's page, so the polls land at about 1, 2.5, 4.8, 8, 13, 21, 32,
  49 and 75 s; `--poll-cap` limits each delay, like the fork's page. A
  poll that fails with status 0 (connection error) or 5xx is asked again
  after the next delay; a 4xx stops the poll of that submission, like the
  page. The think time is set so that all the users together send about
  `--rate` submissions per minute.
- **End burst:** in the last `--end-burst` seconds every user sends 1-3
  more submissions (times `--end-burst-factor`), skewed towards the stop,
  without waiting for the results.
- **Drain:** after the stop, the pending polls go on until every
  submission is final.
- A mix of solutions: AC in C++, Python and Java, WA, TLE, RE and a
  compilation error. `scenario.expected_score()` gives the score each one
  must get, and the analysis checks every submission against it.
- A ranking watcher polls the RWS scores of every ranked contest every
  2 s; at the end the final RWS scores are checked against CMS's own task
  scores.

Every request goes to the ContestWebServer shards round-robin, like an
nginx upstream.

Three **profiles**:

| profile | contests | subtask `depends_on` | two-phase evaluation | ranked on RWS | targets |
|---|---|---|---|---|---|
| `portable` | `loada`, `loadb` | removed | off | `loada` only, at the RWS root (ProxyService `-c <loada id>`) | fork, upstream |
| `full` | `loada`, `loadb` | yes, chains up to 4 levels | on | both, one ranking group each (ProxyService `-c ALL`) | fork only |
| `omi` | `loada` only, 4 tasks of its own (`loadb` is created empty) | yes, one chain of 4 levels | on | `loada` (ProxyService `-c ALL`) | fork only |

Two **targets**:

- `fork`: this repository at any git ref (`beta`, or the branch tip while
  you change the harness).
- `upstream`: stock upstream CMS at `cc9dfafb`, the merge-base of the fork
  and upstream (the last upstream commit merged, 2026-09-26), so that only
  our changes differ.

Both targets run the same processes the same way: `start.sh` starts
`cmsLogService 0`, then `cmsScoringService`, `cmsEvaluationService`, the
Workers, the ContestWebServer shards and `cmsAdminWebServer`, each as
`cms<Service> <shard> -c ALL`; `run.sh` starts `cmsProxyService` once the
contests exist. There is no supervisord and no ResourceService, and
nothing restarts a service: a crash under load is a finding, and it shows
in `start.log` and in the process census.

Comparability caveats (from the design doc):

- The two builds have different architectures (gevent/WSGI, Tornado 4.5
  and SQLAlchemy 1.3 upstream; asyncio, Tornado 6 and SQLAlchemy 2 on the
  fork). That difference is what the comparison measures.
- Fork-only work cannot be turned off and must be stated: the participant
  activity recorder, cookie refresh on polls, and more PG connections per
  process.
- `full` and `omi` have no upstream counterpart. `full` is reported as a
  third configuration; `omi` measures runtime variation, not a comparison
  (see "The `omi` profile").

### The `omi` profile

The other profiles compress a contest into 25 minutes with cheap tasks:
each simulated user submits 3-5 times faster than a real OMI 2025
contestant, a submission costs about a tenth of the Worker time, and every
solution runs in milliseconds. `omi` is shaped like a real OMI, so that N
harness users mean N contestants, and it exists to answer one question:
does the measured runtime of the same program vary under load (the
false-TLE risk on a host with SMT and more Workers than cores; issues #52,
#53 and #54)?

- **Tasks:** one contest, `loada`, with four Batch tasks (`omia`, `omib`,
  `omic`, `omicomm`). Each has four subtasks of five testcases, 20 in all:
  a sample, a screening WA case, a screening TLE case and two heavy ones.
  Score type GroupMin, 4 x 25 points, with a `depends_on` chain through the
  four subtasks of `omib`. One time limit, 2 s, and one language,
  C++20 / g++ (the Python and Java kinds have weight 0).
- **Solutions:** a new kind, `ac_cpu`, is correct and burns CPU: its input
  may end with a line `reps`, and it then spins that many iterations (three
  independent multiply chains, about 1.1 ns per iteration on a Zen 3, and
  1.45 times slower when the SMT sibling runs the same loop) before printing
  the sum. Only the heavy testcases have the line, so the others finish in
  milliseconds. It includes `<bits/stdc++.h>`, so that compiling takes
  seconds like OMI 2025's 2.2-3.0 s. Mix: `ac_cpu` 0.30, `wa_overflow`
  0.17, `tle` 0.15, `wa_small` 0.12, `re` 0.09, `ce` 0.08, `wa_zero` 0.09
  (plain `ac` is not sent). Only `ac` and `ac_cpu` score the maximum.
- **Communication is substituted.** OMI 2025 had one Communication task a
  day. `omicomm` is a fourth **Batch** task with a higher CPU cost per
  heavy testcase (1.0e9 iterations, about 1.1 s on a Zen 3; the three real
  Batch tasks use 6.0e8, about 0.7 s). The FIFO and two-sandbox path of a
  real Communication task is not exercised. If #52 needs it, that is a
  separate issue.
- **First estimates, to calibrate.** The `reps` of the heavy testcases
  (`scenario.OMI_TASKS`), the weights above and the 2 s limit were chosen
  from the baseline report (section 6.2: about 15 sandbox-seconds per
  submission with the compilation, 0.5-1.0 s per evaluation) and a
  measurement on one workstation. A submission here has at most 20
  evaluations against OMI 2025's 37-70, so the mean Worker time per
  submission is lower than 15 s. No run has been made on a runner yet: the
  first one must check the sandbox time per evaluation and per
  submission (`docker_stats.log`, `monitor.jsonl`), the compilation time
  and that the Workers saturate as intended, and then adjust the numbers.

Command lines (document, not presets). The rate follows OMI 2025's day 2:
0.8 x 24 submissions per contestant over 270 steady minutes is 0.071
submissions per user per minute, and the last 15 minutes hold 11.5% of the
submissions (about 2.8 per user), which the driver's 1-3 submissions per
user at `--end-burst-factor 1.0` approximates (mean 2.0;
`--end-burst-factor 1.4` gives about 2.7). The run is a slice of the day: 30
minutes at the steady rate and the 15-minute close.

```bash
# 100 users (OMI 2025 had 99); fits a 4-vCPU runner
./run.sh --target fork --profile omi --name omi-100 --users-a 100 --users-b 0 \
    --rate 7 --contest 2700 --end-burst 900 --end-burst-factor 1.0 --workers 8 --cws 4
# 500 users: --users-a 500 --rate 35, on a host with at least 16 CPUs
```

`run.sh` refuses `--profile omi` without `--users-b 0`: `loadb` has no
tasks. On GitHub Actions set `LOAD_PROFILE: omi` and `LOAD_USERS_B: "0"`.

## Prerequisites

- **Rootful Docker.** The `cms` container runs isolate, which needs
  `privileged: true` and `cgroup: host`; rootless Docker or podman cannot
  run it. Every script calls Docker as
  `sg docker -c "docker --context default ..."`, so your user must be in
  the `docker` group and the `default` context must be the rootful engine.
- **About 16 logical CPUs (8 cores with SMT)** for the default
  (full-size) run; the smoke run fits on far less.
- **No other `cmsload-`, `cmsci-` or stress stack running.** isolate's
  cgroups are shared with the host, and another stack would skew the
  numbers. `run.sh` refuses to start if it finds one; its own target's
  stack is replaced.
- `git` and `python3` (3.10 or newer, stdlib only) on the host.
- Network access the first time: `build.sh` builds the CMS image with
  the target's own `Dockerfile`, and compose builds the small
  `cmsload-driver` image (`driver/Dockerfile`).

Run every command from this directory:

```bash
cd cmstestsuite/loadtest
```

## Build

Build one image per target from a git ref, with that ref's own
`Dockerfile`:

```bash
./build.sh fork beta
./build.sh upstream cc9dfafb
```

Any ref works, for example `./build.sh fork HEAD` to test a change to the
harness. The image is tagged `cmsload-<target>:latest`, and
`out/images/<target>.txt` records the ref, its commit and the g++,
Python, Java and isolate versions of the image. Each build takes about
5-25 minutes.

## Run

```bash
./run.sh --target fork|upstream --profile portable|full|omi --name NAME [options]
```

| flag | default | meaning |
|---|---|---|
| `--target` | (required) | `fork` or `upstream`; uses the image `cmsload-<target>:latest` |
| `--profile` | (required) | `portable`, `full` or `omi`; `full` and `omi` run on the fork only, and `omi` needs `--users-b 0` |
| `--name` | (required) | the run name; the output goes to `out/<name>/`, which must not exist yet (or be empty) |
| `--users-a` | 175 | users in contest `loada` |
| `--users-b` | 75 | users in contest `loadb` |
| `--login-window` | 150 | seconds before the start during which the users log in |
| `--contest` | 1500 | contest length in seconds |
| `--end-burst` | 300 | length of the end burst, in seconds before the stop |
| `--rate` | 45 | target submissions per minute of all users together in the steady phase |
| `--end-burst-factor` | 1.0 | multiplies the 1-3 submissions per user of the end burst |
| `--workers` | 8 | Worker shards |
| `--cws` | 2 | ContestWebServer shards (HTTP ports 8888 and up) |
| `--request-time-header` | (none) | simulates a front proxy that stamps the arrival time of each submit: the driver sends `NAME: t=<ms since epoch>` and the fork's CWS is configured with `request_time_header = "NAME"`; the upstream target ignores the key with a warning |
| `--poll-cap` | (none) | longest delay in seconds between two status polls of a submission; the driver applies it after each multiplication of the backoff, like the fork's `task_submissions.html`, which caps it at 10 s (`PAGE_POLL_CAP_S` in `driver.py`; a test keeps it equal to the page's). Each submission caps at the value times (0.9 + hash * 0.2), the hash that staggers its backoff, so the capped polls of a burst of submissions do not line up in waves. Without the option the backoff has no limit, like upstream's page. The driver emulates the browser, so it applies to both targets. Must be a finite number greater than 0. `run.sh` records it in `users.json` |
| `--cpus` | every CPU of the host | limits the `cms` container (every service and sandbox in it) to this many CPUs, which may be fractional, to emulate a smaller host on a bigger one; a number greater than 0 and not above the host's CPUs. See "Limiting the CPUs" |
| `--watchdog` | off | also runs `cmsWatchdog` in observe mode with the `/proc` source in the `cms` container, and `watchdogstats.py` at the end (fork target only; see "Watchdog runs") |

The defaults are the `full1` values of the 2026-09-30 run. `run.sh`
brings the stack up with a fresh database, waits for every service
(HTTP probes of CWS, AWS and RWS, and an RPC `echo` to ES, SS and every
Worker), creates the contests with the start `--login-window` + 45 s
away, starts ProxyService, the monitor and a `docker stats` sampler,
runs the driver, then collects the DB export and the logs and runs
`analyze.py`. The stack is left running; see [Teardown](#teardown).

Smoke run (20 users, 8-minute contest, about 15 minutes in all), the
first thing to run after any change:

```bash
./run.sh --target fork --profile portable --name smoke-fork-portable --users-a 14 --users-b 6 --login-window 60 --contest 480 --end-burst 120 --rate 10
./teardown.sh fork
./run.sh --target upstream --profile portable --name smoke-upstream-portable --users-a 14 --users-b 6 --login-window 60 --contest 480 --end-burst 120 --rate 10
./teardown.sh upstream
./run.sh --target fork --profile full --name smoke-fork-full --users-a 14 --users-b 6 --login-window 60 --contest 480 --end-burst 120 --rate 10
./teardown.sh fork
```

A healthy smoke run has 20 of 20 logins, 0 HTTP errors, 0 rejected, 0
stuck and 0 mismatched submissions, `rws_mismatches` 0, and no `SHORT`
line in `processes_end.txt`.

The driver does not send a submit POST that would be created after the
contest stop (it checks the time again right before the POST, after the
optional task description GET). Runs made before that change sent 15-22
such POSTs near the stop on a loaded runner, all refused by design, so
their `submissions_sent` is higher by about that much and their
`submissions_rejected` includes them; `submissions_rejected_in_time` counts
only the rejections of POSTs created before the stop, and it is the number
that says something about the server.

Full run (250 users, 25-minute contest, the defaults; about 35 minutes):

```bash
./run.sh --target fork --profile portable --name full-fork-portable-1
./teardown.sh fork
```

A run takes longer than most terminal sessions like; run it with `nohup`
and a log file if you may disconnect:

```bash
nohup ./run.sh --target upstream --profile portable --name full-upstream-1 > out/full-upstream-1.log 2>&1 &
```

To stop a run, press Ctrl-C or send `kill -INT` or `kill -TERM` to the
`run.sh` pid: it stops the driver container at once and exits, and its
`EXIT` trap collects the logs and the DB export into `out/<name>/`, as it
does when a step fails. (A run started with `&` from a script, not from
an interactive shell, ignores SIGINT; send it SIGTERM.) `analyze.py`
runs only at the end of a complete run; run it by hand on an
interrupted one:

```bash
python3 analyze.py out/NAME --project-prefix cmsload-<target>
```

### Limiting the CPUs

`--cpus N` writes `LOAD_CMS_CPUS` to `.env.load`, and `compose.yml` turns it
into `cpus:` for the `cms` container: a CFS quota of N CPUs on everything in
it, services and sandboxes. It emulates a smaller production host on a
bigger machine (`--cpus 6` on the 16-thread workstation for a 6-vCPU VM).
The default is every CPU of the host, which limits nothing. It is not
needed on the 4-vCPU hosted runner. The PostgreSQL, ranking and driver
containers are not limited. Two limits to know:

- **A quota is not a cpuset.** It limits CPU time, not which hardware
  threads run it, so N CPUs of quota spread over all the host's threads and
  do not reproduce a host of N threads on N / 2 physical cores. The SMT
  pairing of the production VM is not emulated.
- **The sandboxes may escape it.** The `cms` container is `privileged` with
  `cgroup: host`, and isolate may place a sandbox in its own cgroup outside
  the container's quota. Then `cpus:` slows the services but not the
  sandboxes, which is exactly what #52 measures. **Not checked yet.** Check
  it in a smoke run with `--cpus` below the host's CPUs, while the driver
  runs: compare `cat /proc/PID/cgroup` and the `Cpus_allowed_list` line of
  `/proc/PID/status`, for a `cmsWorker` and for a running sandboxed program
  (`docker exec cmsload-fork-cms-1 ...`, then the PID of an `omia` or `suma`
  process shown by `top`). If the program's cgroup path is not under the
  Worker's, or `nr_throttled` in the `cpu.stat` of the container's cgroup
  never grows under a busy run, the quota does not reach the sandboxes. The
  fallback is `taskset` for the Workers in `start.sh` (#53 notes it); it is
  not implemented.

## Output

Everything goes to `out/<name>/` (git-ignored):

| file | written by | content |
|---|---|---|
| `users.json` | `setup_contest.py`, `run.sh` | target, poll cap (null when there is none), profile, ranked contests and their RWS paths, contest ids, start and stop, usernames and plaintext passwords |
| `requests.jsonl` | driver | one line per HTTP request: time, kind, phase, duration, status, ok, user, shard, error or body excerpt when it failed |
| `submissions.jsonl` | driver | one line per submission: user, task, solution kind, phase, accepted, opaque id, expected score, final status and score seen, polls (final status `poll_failed`, with the HTTP status in `poll_status`, when a 4xx stopped the poll); also one `login_failed` line per user who gave up logging in |
| `ranking.jsonl` | driver | every change of a (user, task) score seen on RWS |
| `progress.log`, `driver_stdout.log` | driver | a progress line every 10 s (phase, submitted, final, pending polls, request counters) |
| `monitor.jsonl` | `monitor.py` | a sample every 2 s: RPC `echo` round trip to ES, SS, PS, AWS and every CWS, ES queue and busy workers, CPU, RSS and PostgreSQL connections per CMS process, `pg_stat_activity` states, lock waits, longest idle-in-transaction |
| `docker_stats.log` | `run.sh` | `docker stats` of every container every 5 s, prefixed with the Unix time |
| `processes_start.txt`, `processes_end.txt` | `run.sh` | the process census: found and expected count of every `cms<Service>`; a missing one is marked `SHORT` and `run.sh` prints a warning |
| `db_export.json` | `db_export.py` | every submission (timestamp, status, score, `scored_at`, tries, evaluation count), CMS's task score of every participation and task, and every evaluation of the active datasets (`submission_id`, `testcase` codename, `outcome`, `text`, `execution_time` and `execution_wall_clock_time` in seconds; about 120 bytes a row, 4-7 MB at 500 users). Runs from before this key have no `evaluations` |
| `cmslog/` | the CMS services | the log directory of the `cms` container: one directory per service, plus `cms/`, LogService's merged log of all of them |
| `start.log` | `start.sh`, `run.sh` | the services' stdout and stderr, and one line per service start and exit (name, pid, status, UTC time) |
| `cms_stdout.log`, `db.log`, `ranking.log`, `db_init.log` | compose | the container logs of `cms`, PostgreSQL (slow statements over 1 s and lock waits are logged), RWS and `cmsInitDB` |
| `cms.toml` | `run.sh` | the rendered config, without the secret key, the database URL and the ranking URL |
| `image.txt` | `build.sh` | a copy of `out/images/<target>.txt` |
| `summary.md`, `metrics.json` | `analyze.py` | the analysis, below |

`run/` (the rendered config and `start.log` of the running stack) and
`.env.load` (throwaway passwords) are rewritten by every run.

### summary.md

- **Header:** target, profile, users per contest, contest length and
  start time.
- **HTTP latency by request kind and phase:** count, errors and p50, p95,
  p99 and max duration of every request kind (`home_anon`, `login`,
  `home_logged`, `contest_page`, `task_description`, `statement`,
  `notifications`, `submit`, `submissions_page`, `status_poll`,
  `submission_details`, `submissions_list`, `rws_scores`) in every phase
  (`login_burst`, `start_burst`, `steady`, `end_burst`, `drain`); then
  the login and submit percentiles, the failed requests grouped by kind,
  status and error, and the peak and mean CWS request rate.
- **Submissions:** login failures; submissions sent, rejected by CWS (and
  how many of those were created before the stop) and found in the DB; per phase, the server latency (`scored_at` minus the
  submission timestamp) and the perceived latency (the first poll that
  saw a final status, with the browser backoff); the same latencies over
  all phases, with the number of status polls; when the last one was
  scored after the stop; stuck submissions (never scored); rows the
  driver could not find on the submissions page; score mismatches
  against `expected_score()`; the solution mix; the histogram of
  (compilation tries, evaluation tries).
- **Runtime spread of the same program:** how much the measured runtime
  of one program varies, for `execution_time` (CPU) and for
  `execution_wall_clock_time`; absent when the export has no
  `evaluations`. Only the runs of fully correct submissions count (they
  run every testcase to the end, so a longer time is the host, not the
  program), grouped by (kind, task, testcase), since the same codename on
  another task has another input, and by the phase the driver sent the
  submission in, `steady` or `end_burst`. A (group, phase) with fewer than
  5 runs or a median under 0.1 s is skipped and counted (a few
  milliseconds of noise are a large spread that says nothing about a time
  limit). Per group and phase: `spread` = (max - min) / mean (the 10%
  criterion of #52) and p95 / p50; the table gives the median and p95 of
  each over the groups. The line "End-burst p50 over steady p50" is the
  direct SMT signal: per group, the burst median divided by the steady
  one. The headline also counts the runs of those submissions that hit the
  time limit, that is false TLEs. The phase is the one of the submission,
  not of its evaluation, so a long queue blurs it. No new `metrics.json`
  keys.
- **Ranking (RWS):** final RWS against CMS task scores of the ranked
  contests, and the ranking lag (RWS change seen minus the latest
  `scored_at` of that user and task; 2 s polling).
- **Internals:** the ES queue (max operations, busy workers) overall and
  per minute from the start; the RPC `echo` round trip per service (a slow
  echo means that service's event loop was blocked); per process, max
  PostgreSQL connections, CPU seconds and max RSS; PostgreSQL backends,
  active ones, lock waits and the longest idle-in-transaction.
- **Service logs:** WARNING, ERROR and CRITICAL lines per service, hits
  of known trouble patterns (tracebacks, timeouts, pool exhaustion,
  "missed operations" of the sweepers, ...) and the distinct
  WARNING/ERROR messages. The `cms` row is LogService's merged log, so it
  repeats the other rows.
- **PostgreSQL log:** slow statements and errors or lock waits.
- **Containers:** mean and max CPU (100% = one core) and the last memory
  use of every container of the stack.

### metrics.json

One flat object with the headline numbers, the ones `compare.py` reads.
A value is `null` when there is nothing to compute it from: the database
export, the monitor and the docker stats are optional inputs (for example
`db_export.json` is missing in a run that crashed), and a percentile of no
samples is `null`. The counts taken from the driver's files
(`submissions_sent`, `submissions_rejected`, `submissions_rejected_in_time`,
`login_failures`, `http_errors`, `status_polls`) are 0, not `null`, when
the file is missing. `compare.py` shows "-" for a key that an older
`metrics.json` does not have (`submissions_rejected_in_time` was added
later, and so were the `perceived_*` keys, `status_polls` and `poll_cap`).

| key | meaning |
|---|---|
| `run`, `target`, `profile` | run name, target and profile |
| `users` | users in both contests |
| `poll_cap` | the `--poll-cap` of the run in seconds, as `run.sh` recorded it in `users.json`; `null` when the run had no cap (the driver run by hand does not record it) |
| `submissions_sent` | submissions the driver sent |
| `submissions_rejected` | submissions CWS did not accept (no `submission_id` in the redirect) |
| `submissions_rejected_in_time` | the rejected submissions whose POST was created before the contest stop (`t_submit` < stop); these are the ones that point at the server |
| `login_failures` | users who gave up after 5 login attempts |
| `http_errors` | failed HTTP requests of every kind, `rws_scores` included |
| `score_mismatches` | scored submissions whose score differs from `expected_score()` |
| `rws_pairs`, `rws_mismatches` | (user, task) pairs of the ranked contests checked, and how many differ between RWS and CMS |
| `login_p50`, `login_p95` | login POST duration, seconds |
| `submit_p50`, `submit_p95`, `submit_end_p95` | submit POST duration, seconds, overall and in the end burst |
| `scored_p50`, `scored_p95`, `scored_max` | server submit-to-scored latency, seconds |
| `perceived_p50`, `perceived_p95`, `perceived_max` | perceived latency, seconds: the time of the first status poll that saw a final status minus the submit time (the browser backoff, so it depends on `--poll-cap`), over every submission that reached a final status, in all phases (`null` if none did); it needs no database export |
| `status_polls` | `status_poll` requests in `requests.jsonl`, failed ones included, in all phases (the `n` column of the `status_poll` rows of the HTTP table in `summary.md`) |
| `drain_after_stop_s` | seconds from the stop to the last `scored_at` |
| `peak_pg_connections` | max PostgreSQL backends of the CMS database in a monitor sample |
| `cpu_mean_by_container` | mean CPU percent per container |
| `mem_last_by_container` | last memory use per container, as `docker stats` prints it |

The number of stuck submissions is in `summary.md` only ("Never scored
(stuck)").

Two more analyses read the ES and Worker logs of a run, on either target:
`python3 flushstats.py out/NAME` (result flushes and server latency per
task and solution kind) and `python3 lockwait.py out/NAME` (how long
finished results wait for ES's `post_finish_lock`).

## Compare

```bash
python3 compare.py out/a out/b --median
```

prints a markdown table of the `metrics.json` keys, one column per run;
with `--median`, one column per target, the median of its runs. Give it
the runs of both targets, for example
`python3 compare.py out/full-fork-portable-* out/full-upstream-* --median`.
`--median` groups by target only, not by profile: pass the runs of one
profile at a time, and never mix `full` runs into a `portable`
comparison (`out/smoke-* --median` would blend the fork's `portable` and
`full` smoke runs into one column).

## Teardown

```bash
./teardown.sh fork
./teardown.sh upstream --images
```

stops the stack of the target and removes its containers, network and
volumes; `--images` also removes `cmsload-<target>:latest`. Tear down
after every run: the next run of the other target refuses to start while
this one is up.

## Running on GitHub Actions

The `loadtest` workflow (`.github/workflows/loadtest.yml`) runs the
fork against upstream on GitHub-hosted runners, so that no workstation
has to stay up for hours. Push the commit to test to a `loadtest/`
branch to start it, and delete the branch when the run is over:

```bash
git push origin HEAD:refs/heads/loadtest/<topic>
git push origin --delete loadtest/<topic>
```

- **Knobs:** the `env:` block at the top of the workflow holds the
  `run.sh` options (`LOAD_USERS_A`, `LOAD_USERS_B`, `LOAD_LOGIN_WINDOW`,
  `LOAD_CONTEST`, `LOAD_END_BURST`, `LOAD_RATE`,
  `LOAD_END_BURST_FACTOR`, `LOAD_WORKERS`, `LOAD_CWS`, `LOAD_PROFILE`,
  `LOAD_REQUEST_TIME_HEADER`, `LOAD_POLL_CAP`, `LOAD_CPUS`)
  and `UPSTREAM_REF`; change them in the commit you push. The values in
  the file are the `run.sh` defaults, except 4 CWS shards instead of 2
  (`LOAD_REQUEST_TIME_HEADER`, `LOAD_POLL_CAP` and `LOAD_CPUS` are empty,
  which leaves those options off).
- **Jobs:** four `pair` jobs, repeats 1 to 4, each on its own runner
  (`ubuntu-24.04`: 4 vCPUs and 16 GB, a quarter of the cores the
  full-size run asks for). Each one builds both images
  (`./build.sh fork HEAD`, `./build.sh upstream $UPSTREAM_REF`), then
  runs `ci-<repeat>-fork` and `ci-<repeat>-upstream` one after the other
  with a teardown in between: the fork first in odd repeats, upstream
  first in even ones, two of each. Because the fork and upstream of a
  repeat share a runner, the shared runner cancels the runner-to-runner
  variation (the CPU model varies: the three runners of one smoke test
  had three different ones). With `LOAD_PROFILE: full` or `omi` only the
  fork runs. Then `compare` runs `compare.py --median` and the per-run table
  over every run that has a `metrics.json`; `--median` runs for the
  `full` profile too, since one workflow run never mixes profiles.
- **Results:** the summary of each `pair` job shows the runner and the
  headline of its runs, the summary of `compare` both tables. The
  artifact `loadtest-<repeat>` is that job's `out/`: the two run
  directories, `run-<name>.log` (the output of `run.sh` and
  `teardown.sh`), `images/*.txt` and `runner.txt` (cores, CPU model,
  kernel, memory, disk, the scenario, the run order and the start, end
  and exit status of each run). The artifact `loadtest-compare` holds
  `compare.md`. A green job does not mean a clean run: it fails only
  when `run.sh` or `teardown.sh` does, and HTTP errors, stuck
  submissions and score mismatches show only in the summaries. The
  artifacts of this public repository are public; they hold throwaway
  data only (the test users' deterministic passwords and the config
  without its secrets) and are kept 30 days.
- **Cost and time:** standard runners are free on a public repository;
  the `pair` jobs take 4 of the 20 concurrent jobs of GitHub Free. A job
  takes about 30 minutes with the smoke values, the two image builds
  about 5 minutes of that. With the defaults it takes about 1.5 hours,
  an estimate from the local runs, not yet measured on a runner.

## Fault runs

`run.sh --faults` (fork target only, `--contest` of 2100 s or more) also
starts `faults.py` on the host, next to the driver. It injects the
database faults of #39 at fixed times after the contest start, and
writes their start and end times to `out/<name>/faults.json`:

| Time (s) | Fault |
|---|---|
| 120, 180 | F1: `pg_terminate_backend` of every CMS backend (`application_name LIKE 'cms%'`) |
| 600 | F3: `docker pause` of PostgreSQL for 30 s; 2 s in, a probe opens a fresh connection |
| 1000 | F4: `cmsSetupDB` while a `psql` session holds `BEGIN; SELECT 1 FROM contests LIMIT 1;` idle |
| 1080 | F2: an iptables DROP of PostgreSQL's port, both ways, for 90 s, in its network namespace (`sudo nsenter`); 180 s after its end, the runbook's partition step |

After the run, `faultstats.py out/<name>` checks the pass criteria of
`docs/superpowers/specs/2026-10-08-db-transport-timeouts-design.md` and
writes `out/<name>/faults.md`: one row per criterion, `pass` or `FAIL`.
After F2 and F3, each CWS shard has its own "pages answer again" row, so
one frozen shard fails the run even while the others answer.

F3's probe runs in the `cms` container and opens a new connection
through CMS's own path (green psycopg2, the connection defaults, no pool)
while PostgreSQL is paused. F3 passes only if that connect began inside
the pause, in a green psycopg2, and failed with `timeout expired` at
about 10 s, which proves the connect deadline. Any other probe fails it,
and its row says why: it connected at once (the pause did not stall
connects), it connected only after the resume or hung (the deadline did
not fire), or it never reached its connect (inconclusive). The
`timeout expired` log lines and the requests that started during the
pause are for information only, since pooled connections just wait for
the resume.

The injector needs passwordless `sudo`, as GitHub runners have. A fault
that fails or hangs gets its error in its record (its row fails), what
it left in place is undone, and the next fault still runs. If the
injector stops on an error or on SIGTERM (`run.sh` sends it at the end
of the run), it removes its rules and unpauses PostgreSQL first.

On GitHub Actions, push the commit to a `loadtest/faults-<topic>` branch:
the `faults` job runs the fork once per profile, smoke-sized, and shows
`faults.md` in its summary (a `FAIL` does not fail the job). A
`loadtest/runner-check-<topic>` branch runs only the 5-minute check that
a runner can use `nsenter`, `iptables` and `docker pause`.

## Watchdog runs

`run.sh --watchdog` (fork target only) runs
`cmsWatchdog --mode observe --process-source proc` in the `cms`
container from before ProxyService starts, with
`--status-history out/<name>/watchdog_status.jsonl` (one status per
cycle) and its output in `out/<name>/watchdog.log`. After the run,
`watchdogstats.py out/<name> --cws N --workers N` writes
`out/<name>/watchdog.md`: one row per criterion of "Harness L" in
`docs/superpowers/specs/2026-10-10-watchdog-design.md`, `pass` or
`FAIL`, then the probe latencies (p50 and p99 per program):

- no restart or start decision (the `WOULD`, `did not` and `could not`
  lines); holds and a red gate only inside the F2 and F3 windows of
  `faults.json` (none without `--faults`), except that an H2 hold may
  also be logged up to 120 s after a window, since it lasts that long
  after the gate returns green;
- every program (ES, SS, PS, LogService, every CWS, AWS, every Worker)
  in every cycle from the driver's first request; ProxyService may
  appear late, since `run.sh` starts it once the contests exist;
- cycles that cover the run (a watchdog that stops or stalls fails): the
  last one at most 45 s (3 cycles) before the driver's last request, no
  gap over 45 s between two from the driver's start on, and one inside
  every F2 and F3 window (widened as for the holds);
- favicon latencies for every CWS and AWS and `health()` latencies for
  every program but the Workers; favicon and `health()` p99 under a
  third of their timeouts (5 s and 3 s), and no samples fails;
- every near miss under half its trip: at most 2 misses in 6, no ES
  section older than 90 s with no statement for 30 s (re-evaluations
  aside), no SS execution over 90 s (unless it overlapped a
  re-evaluation), no PS send idle over 90 s; the longest ES `idle_s`
  inside a section is recorded as a margin.

With `--faults` too (L3), the criteria are those of L3: no restart or
start decision (the `WOULD`, `did not` and `could not` lines), holds and
a red gate only inside the F2 and F3 windows (an H2 hold also up to 120 s
after one; nothing for F1 and F4), every program in every cycle, cycles
that cover the run and every F2 and F3 window, and latency samples for
every probe. The holds and the red-gate cycles inside the windows are
recorded, not required. The p99 and near-miss rows are only recorded
(`(recorded: a fault run)`): F2 and F3 push them past half their trips by
design.

## Limits

- **One host:** the driver, the database, RWS and every CMS service share
  the same cores, so the driver and PostgreSQL take CPU from CMS.
- **No proxy and no TLS:** the driver talks to the CWS shards directly
  over HTTP, round-robin; nginx's buffering and TLS cost are not there.
- **Synthetic, cheap testcases:** "sum of N numbers" runs in milliseconds,
  so the workers are faster than with real tasks; worker capacity
  numbers do not carry over. The `omi` profile spins on purpose in its
  heavy testcases, but its `reps`, mix and limit are uncalibrated first
  estimates, and its fourth task only stands in for a Communication task.
- **One client IP:** every user comes from the driver container's
  address.
- **Fork-only work that cannot be turned off:** the participant activity
  recorder, the cookie refresh on polls and the extra PostgreSQL
  connections per process run on the fork in both profiles.

## History

`REPORT-2026-09-30.md` is the first run, on the fork only (main at
d3532b25, 250 users, the `full` scenario), made with the harness this
directory was recovered from.
