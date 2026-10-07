# CMS load-test harness

A contest-shaped load test of CMS on one host: a fresh stack in Docker, a
driver that behaves like browsers during a contest, and an analysis that
checks every score and reports latencies, saturation and errors. It runs
the same scenario against two **targets**, this fork and stock upstream
CMS, so that the numbers can be compared (issue #7).

It is not part of the pytest suite. Its unit tests live in
`cmstestsuite/unit_tests/loadtest/` and need neither Docker nor a database.

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
  think time is set so that all the users together send about `--rate`
  submissions per minute.
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

Two **profiles**:

| profile | contests | subtask `depends_on` | two-phase evaluation | ranked on RWS | targets |
|---|---|---|---|---|---|
| `portable` | `loada`, `loadb` | removed | off | `loada` only, at the RWS root (ProxyService `-c <loada id>`) | fork, upstream |
| `full` | `loada`, `loadb` | yes, chains up to 4 levels | on | both, one ranking group each (ProxyService `-c ALL`) | fork only |

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
- `full` has no upstream counterpart. It is reported as a third
  configuration.

## Prerequisites

- **Rootful Docker.** The `cms` container runs isolate, which needs
  `privileged: true` and `cgroup: host`; rootless Docker or podman cannot
  run it. Every script calls Docker as
  `sg docker -c "docker --context default ..."`, so your user must be in
  the `docker` group and the `default` context must be the rootful engine.
- **About 16 cores** for the default (full-size) run; the smoke run fits
  on far less.
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
./run.sh --target fork|upstream --profile portable|full --name NAME [options]
```

| flag | default | meaning |
|---|---|---|
| `--target` | (required) | `fork` or `upstream`; uses the image `cmsload-<target>:latest` |
| `--profile` | (required) | `portable` or `full`; `full` runs on the fork only |
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
python3 analyze.py out/NAME --project-prefix cmsload-fork
```

## Output

Everything goes to `out/<name>/` (git-ignored):

| file | written by | content |
|---|---|---|
| `users.json` | `setup_contest.py`, `run.sh` | target, profile, ranked contests and their RWS paths, contest ids, start and stop, usernames and plaintext passwords |
| `requests.jsonl` | driver | one line per HTTP request: time, kind, phase, duration, status, ok, user, shard, error or body excerpt when it failed |
| `submissions.jsonl` | driver | one line per submission: user, task, solution kind, phase, accepted, opaque id, expected score, final status and score seen, polls; also one `login_failed` line per user who gave up logging in |
| `ranking.jsonl` | driver | every change of a (user, task) score seen on RWS |
| `progress.log`, `driver_stdout.log` | driver | a progress line every 10 s (phase, submitted, final, pending polls, request counters) |
| `monitor.jsonl` | `monitor.py` | a sample every 2 s: RPC `echo` round trip to ES, SS, PS, AWS and every CWS, ES queue and busy workers, CPU, RSS and PostgreSQL connections per CMS process, `pg_stat_activity` states, lock waits, longest idle-in-transaction |
| `docker_stats.log` | `run.sh` | `docker stats` of every container every 5 s, prefixed with the Unix time |
| `processes_start.txt`, `processes_end.txt` | `run.sh` | the process census: found and expected count of every `cms<Service>`; a missing one is marked `SHORT` and `run.sh` prints a warning |
| `db_export.json` | `db_export.py` | every submission (timestamp, status, score, `scored_at`, tries, evaluation count) and CMS's task score of every participation and task |
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
- **Submissions:** login failures; submissions sent, rejected by CWS and
  found in the DB; per phase, the server latency (`scored_at` minus the
  submission timestamp) and the perceived latency (the first poll that
  saw a final status, with the browser backoff); when the last one was
  scored after the stop; stuck submissions (never scored); rows the
  driver could not find on the submissions page; score mismatches
  against `expected_score()`; the solution mix; the histogram of
  (compilation tries, evaluation tries).
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
A value is `null` when its input is missing (for example `db_export.json`
in a run that crashed).

| key | meaning |
|---|---|
| `run`, `target`, `profile` | run name, target and profile |
| `users` | users in both contests |
| `submissions_sent` | submissions the driver sent |
| `submissions_rejected` | submissions CWS did not accept (no `submission_id` in the redirect) |
| `login_failures` | users who gave up after 5 login attempts |
| `http_errors` | failed HTTP requests of every kind, `rws_scores` included |
| `score_mismatches` | scored submissions whose score differs from `expected_score()` |
| `rws_pairs`, `rws_mismatches` | (user, task) pairs of the ranked contests checked, and how many differ between RWS and CMS |
| `login_p50`, `login_p95` | login POST duration, seconds |
| `submit_p50`, `submit_p95`, `submit_end_p95` | submit POST duration, seconds, overall and in the end burst |
| `scored_p50`, `scored_p95`, `scored_max` | server submit-to-scored latency, seconds |
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

## Limits

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

## History

`REPORT-2026-09-30.md` is the first run, on the fork only (main at
d3532b25, 250 users, the `full` scenario), made with the harness this
directory was recovered from.
