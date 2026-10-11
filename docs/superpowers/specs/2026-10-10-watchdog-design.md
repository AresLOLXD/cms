# Watchdog for Hung Programs (#38) — Design Doc

**Date:** 2026-10-10
**Issue:** #38 (wave C of the pre-10-10 risk audit).
**Status:** Designed. A panel of three specialist subagents chose the approach, as the user asked on 2026-10-07 for backlog issues (see the Decision Record). The spec review is pending.
**Target:** `beta`. The implementation branch starts from `beta` **after #39 stage 1 is merged there** (Decision Record, point 18). This spec commit is rebased onto that point.

## Problem

Nothing restarts a CMS program that is alive but hung.

- **What supervisord and Docker do today:**
  - supervisord restarts a program only when it exits (`autorestart=true`).
  - Docker restarts the `cms` container only when supervisord exits. It never acts on "unhealthy".
  - The only healthcheck, AWS `/`, tests AWS's event loop, nothing else.
- **What that cost:** on 2026-10-03 AWS was hung for an hour (pool exhaustion plus a sync DB call on its loop) until a human restarted it.
- **Hangs that `echo` cannot see:**
  - `echo` still answers while ES is stuck under `post_finish_lock`: grading stops and nothing notices.
  - A hung ScoringService (SS) or ProxyService (PS) executor is just as invisible.
- **Programs that stay down after a database outage:**
  - With `startretries=3` and `startsecs=1`, a program that cannot reach PostgreSQL at startup goes FATAL after about 7 s.
  - It then stays down until a human starts it.
- **Workers:** a Worker killed with SIGKILL leaves its `isolate` children behind.
  - isolate 2.7 then locks that box: every `--init` on it fails with "This box is currently in use by another process" until the orphan ends.
- **Smaller defects found on the way:**
  - The Checker is in the generated service list (`docker/generate_config.py:142`) but never started, so AWS shows it as "Not running".
  - Tornado logs every request at INFO, so frequent probes would flood the logs.
  - **The production Caddy runs the health-check defaults** (one failed check ejects a shard for 30 s), and with `lb_policy cookie` it keeps sending a pinned user to a dead shard. Today, even a manual `supervisorctl restart` of one CWS gives that shard's users 502s for up to 30 s.

The investigation, with file and line references, is in the session's `w38-findings.md`.

## Goal

1. **Detect a hung program and restart only that program, never the whole container.**
   - **CWS and AWS:**
     - with the Caddy profile of section 4, Caddy moves users off a hung shard within about 35 s;
     - the watchdog restarts the shard after 65-90 s of unresponsiveness, plus at most 10 s to stop it.
   - **ES:** its locked section held for more than 180 s while issuing no database statement for 60 s.
   - **SS:** one execution longer than 180 s. **PS:** one execution longer than 300 s.
   - **Deviation from the agreed "about a minute":** the HTTP trip is about 1.5 minutes. It must stay above the 60 s pool and transport timeouts of the #39 ladder: below them it would restart shards that are about to heal by themselves. Caddy's failover delivers the minute that users see.
2. **No restart storms.**
   - No restart while PostgreSQL is unreachable or out of connection slots, or while a lock holder blocks CMS.
   - No restart while several programs fail together.
   - A restart budget per program and an overall one.
3. **Evidence is kept before each action** (or each would-action in observe mode): a database activity snapshot, the holder's stack and the process state.
4. **A busy Worker is never treated as hung.** Workers are judged only by ES's own verdict (`disabled_by_timeout`), never by `echo` or by how long they have been busy.
5. **Programs left FATAL by a database outage are started again** once the database is back.
6. **Observe mode comes first.**
   - The same policy runs, but logs "WOULD restart" instead of acting.
   - Enforce is switched on per class of program, through `.env`.
7. **Operators can tell what happened:**
   - in the AWS Overview's log table;
   - in `./logs.sh`;
   - in `./status.sh`;
   - in an evidence directory.

   The runbook says what each alert means and how to turn the watchdog off.
8. **No change on a healthy system:**
   - no action at all;
   - probes cost a few cheap requests every 15 s;
   - probe requests are logged at DEBUG.

## Decision Record

**Approaches put to the panel** (full text in the session's `w38-approaches.md`):
- **A, an in-image watchdog:** a supervisord program in the `cms` container.
- **B, a host watchdog:** a systemd timer on the VM that uses `docker exec`.
- **C, minimal:** health RPCs, alerts and Caddy failover, with no automatic restarts.

**Verdicts. All three chose A:**

- **Operations panelist (devops-incident-responder): A, hardened for contest day.**
  - It argued from what the operator lives through.
  - **Thresholds and budget:**
    - HTTP trip at 5 misses among the last 6 probes, a sliding window: the 10-03 livelock had brief gaps that reset a consecutive count;
    - budget of 2 per program and 3 overall per sliding 60 minutes, kept in a journal;
    - the ES trip excludes an invalidation (re-evaluation) holder, which only alerts.
  - **Gates and guards:**
    - a lock-wait gate;
    - 120 s of sustained green after a database outage before any restart;
    - detection of host or VM pauses.
  - **Evidence and alerting:**
    - a USR1 allow-list, because USR1 kills processes that do not register it;
    - hourly near-miss summaries;
    - a "did it help" line;
    - a manual `--snapshot`.
  - **Rollout:**
    - Workers alert-only in v1;
    - observe mode for 14 days or more, with at least one session with users and 7 nightly restarts;
    - enforce switched on per class.
- **Architecture panelist (architect-reviewer): A′, a layered watchdog.**
  - **Shape:**
    - a pure policy core (fake-clock tests) with probe and control adapters;
    - a dry-run actuator, plus a `/proc` process source so it can run in the load-test harness, which has no supervisord;
    - three merges: service-side health, then the watchdog in observe mode, then the enforce flip.
  - **Restart primitive:**
    - a verified pidfd, then TERM, a wait, KILL, and supervisord's autorestart;
    - never `supervisorctl restart`.
  - **Detection:**
    - the ES "no progress" signal counts the holder thread's SQL statements;
    - thresholds are code constants, pinned against the #39 ladder by a test.
  - **Order:** wait for #39 stage 1 to merge, because the lock-wait veto needs its `application_name`.
- **Infrastructure panelist (docker-expert): A, verified by experiments** (kit in the session's `w38-infra-exp/`):
  - **supervisord signals:**
    - `supervisorctl signal` reaches only the group leader and orphans isolate;
    - a group signal from a sibling process is clean, followed by autorestart.
  - **Commands and the database:**
    - `supervisorctl` has no timeout and ignores `SUPERVISOR_SERVER_URL`;
    - `pg_isready` returns 0 while all connection slots are taken.
  - **Container and supervisord state:**
    - a stopped supervisord blocks every program on its stdout pipe within about 7 s;
    - `supervisorctl status` exits 3 when any program is not RUNNING, which breaks `./status.sh`.
  - **Its proposals:**
    - a tested Caddy profile;
    - `stopasgroup` and `killasgroup` everywhere;
    - `startretries=30`;
    - watchdog priority 90.

**Decided:** A, in the architecture panelist's layered form. The points where the panelists agreed, or were not contradicted:

1. **No host timer and no autoheal.** A whole-container hang is out of scope (Out of Scope).
2. **A restart is a process-group signal.** The watchdog sends TERM to the group, waits 10 s, sends KILL to whatever remains, and lets supervisord's `autorestart` start the program again. It never runs `supervisorctl restart`, `stop` or `start` on a RUNNING program: those block for up to 41 s, and if the watchdog dies between stop and start the program stays STOPPED.
3. **Programs are joined by the `cms<Service> <shard>` naming.** It appears in supervisord's `command=`, in `/proc/<pid>/cmdline` and in #39's `application_name`.
4. **Thresholds are code constants, not configuration.** A test pins them against the ladder.
5. **Workers are judged only through ES's WorkerPool.** The isolate concern is settled by the group signal (infra experiment X2).
6. **supervisord policy:**
   - `stopasgroup=true` and `killasgroup=true` everywhere;
   - `startretries=30`;
   - Checker removed;
   - `./status.sh` fixed.
7. **The probe path is logged at DEBUG.**
8. **Observe mode comes first. Enforce is a `.env` flip, decided per class.**

**Disagreements, and how they were settled.** The rule was the majority, or else the more conservative choice, because production has no staging.

| Point | Proposals | Settled as |
|---|---|---|
| HTTP and RPC liveness trip | 3 in a row, 45 s (infra); 5 in a row (architecture); 5 of the last 6 (operations) | **5 misses among the last 6 probes, 15 s apart.** Two panelists stay above the 60 s timeouts, and the sliding window catches livelocks |
| Budget | 2 per program and 3 overall per 60 min (operations); 2 per program and 4 overall per 15 min (architecture); 3 per program and 4 overall per 10 min (infra) | **2 per program and 3 overall per sliding 60 min**, the most conservative. FATAL starts have their own budget of 2 per program per 60 min (operations) |
| Where the budget lives | `run/` tmpfs (architecture, infra); a journal on the `cms-logs` volume (operations) | **The ledger lives in `run/`** (majority). It survives watchdog restarts, and a container restart, which is a human action, resets it. **Every action is also appended to a journal in `log/watchdog/`** for postmortems (operations) |
| Heartbeat in the compose healthcheck | yes (infra); not yet, because it gates `up --wait` and a watchdog bug would fail a contest-day deploy (architecture, operations) | **Not in v1** (majority). The heartbeat is shown in `./status.sh` |
| DB gate | `pg_isready` plus `SELECT 1` (operations, infra); one `psql` query only (architecture) | **One `psql` query per cycle**, which also returns the activity snapshot. Every failure `pg_isready` detects also fails a query, and psql's error text classifies the reason. Neither of the other panelists named a case where `pg_isready` changes a decision |
| Workers | alert only in v1 (operations); restart on ES's disabled-by-timeout (architecture, infra) | **Restart only in enforce mode, only when the `workers` class is listed**, and that class is not in the default list. After 180 s of `disabled_by_timeout` (operations' value, the most conservative) or "never connected" |
| TERM wait | 10 s for every program (operations, infra); 40 s for a CWS whose loop still answers RPC (architecture) | **10 s for every program** (majority). An HTTP trip means the loop has not answered for 65-90 s, so a flush would not run anyway |
| FATAL start condition | 2 green cycles (architecture); after a red→green transition since the program's last start (infra); 30 s of green (operations) | **A red→green transition since the program's last start, then 30 s (2 cycles) of green.** A program that went FATAL with the database up is left to a human |
| Watchdog priority | 80 (architecture); 90 (infra) | **90**: it starts last and stops first. Both are above the loader's 70 |
| Mode knobs | `off\|observe\|enforce` (architecture); `alert\|restart`, with no off (infra); `off`, plus a list of classes (operations) | **`CMS_WATCHDOG=off\|observe\|enforce`**, default `observe`, and **`CMS_WATCHDOG_RESTART`**: the classes that enforce may act on, default `web,core,infra` |
| Caddy `response_header_timeout` | 90 s (operations); 120 s (architecture); 180 s (infra) | **120 s**, the median. It must exceed `pool_timeout` (60 s) plus #39 stage 2's `lock_timeout` (30 s) plus handler time. Revisit when stage 2 lands |
| AWS behind Caddy | a 300 s header timeout (operations); retries only, no timeout (infra) | **Retries only** (infra, tested): admin exports can be slow, and AWS has no failover target |
| How long observe mode lasts | 14 days (operations); drills plus one session (architecture) | **A list of gates instead of a fixed time** (Rollout). It includes one session with users and 7 nightly restarts |

## Design

### 1. Overview

There are two parts on one branch. They ship in the same release.

- **Part 1, service-side health.**
  - A `health()` RPC on every asyncio service.
  - Busy-section tracking in ES, SS and PS.
  - New WorkerPool status fields.
  - faulthandler on USR1.
  - Probe log demotion.
  - The supervisord policy, the Checker removal and the `./status.sh` fix.
  - Part 1 changes no behaviour on its own, apart from the supervisord policy, the Checker removal and the quieter probe logs.
- **Part 2, the watchdog.**
  - `cms/watchdog/` with the `cmsWatchdog` script.
  - Its supervisord block, its knobs, the operator documentation and the Caddy profile.
  - The harness flag.
- **The enforce flip** is an operator change in `.env`, made later (Rollout).

```
supervisord ──┬── cmsLogService 0 … cmsContestWebServer N … cmsWorker N   (unchanged, plus the group flags)
              └── cmsWatchdog --mode observe|enforce --restart <classes>   (new, priority 90)
                    │ every 15 s
                    ├─ supervisord XML-RPC (unix socket): program states, pids, start times
                    ├─ psql: the DB gate plus a pg_stat_activity snapshot
                    ├─ HTTP GET /static/favicon.ico on each CWS and AWS
                    ├─ RPC health() on each asyncio service; workers_status() on ES
                    ├─ HTTP GET / on each configured ranking (alert only)
                    └─ policy → holds/vetoes → actuator (signal group | dry run) → evidence, alerts, ledger, status
```

### 2. Service side (Part 1)

#### 2.1 `cms/io/health.py`: busy sections

This is a small stdlib-only module.

```python
class BusyTracker:
    """Track the sections of a service that must not hang, per thread."""

    def busy(self, what: str) -> ContextManager[None]: ...
        # records (what, start=monotonic, statements=0, last_statement=start)
        # for the current thread; drops the record on exit
    def start(self, what: str) -> None: ...
    def stop(self) -> None: ...
    def count_statements_of(self, engine) -> None: ...
        # event.listen(engine, "before_cursor_execute", ...): +1 statement and
        # last_statement=monotonic for the current thread's record, if any
    def report(self, stack_after_s: float = 30.0) -> list[dict]: ...
        # [{"what", "age_s", "statements", "idle_s", "stack"}], oldest first.
        # idle_s = time since the last statement (or since start if none).
        # stack: the last 30 frames of that thread, only once age_s >= stack_after_s.
```

- Each thread changes only its own record. The loop reads a `list(dict.items())` copy, which the GIL makes atomic. There is no lock, and `report()` never takes `post_finish_lock`.
- The engine is a parameter, so `cms.io` does not import `cms.db`.

#### 2.2 `health()` RPC and faulthandler

- **`AsyncService`:**
  - it records its start time;
  - in `run()`, not `__init__`, so that tests are unaffected, it calls `faulthandler.register(signal.SIGUSR1, all_threads=True)`;
  - it gains a sync RPC method, answered on the event loop, so that any answer at all proves the loop is free:

  ```python
  @rpc_method
  def health(self) -> dict:
      # {"v": 1, "pid": int, "uptime_s": float,
      #  "busy": self._busy.report() or [], **self._health_extra()}
  ```

  `_health_extra()` returns `{}` by default.
- **The gevent `Service.run()`** (the Worker) registers faulthandler the same way. It needs no `health()`.
- **`pid`** lets the watchdog check that the process answering on the port is the one supervisord started. A mismatch means an orphan holds the port.
- **Version handling.** A missing field, or a `v` it does not know, disables only the rule that needs it. The watchdog logs one alert and never trips on it.

#### 2.3 Sections in ES, SS and PS

- **ES:**
  - `with_post_finish_lock` (`EvaluationService.py:261`) calls `self._busy.start("post_finish_lock:" + func.__name__)` when `_post_finish_depth` goes from 0 to 1, under the lock.
  - It calls `self._busy.stop()` when the depth returns to 0, in the existing `finally`, after `_flush_loop_actions()`, and it increments `self._post_finish_sections`.
  - Only the outermost section is tracked. Threads waiting for the lock are not listed.
  - `count_statements_of(cms.db.engine)` is called at startup.
  - `_health_extra()` returns `{"post_finish": {"sections": int}, "queue": int}`.
  - The sweeper period `117.0` becomes the constant `EvaluationService.SWEEPER_PERIOD_S`, for the ladder test.
- **SS:** `with self._busy.busy("score")` wraps the body of `ScoringExecutor._execute_sync` (`ScoringService.py:98`).
- **PS:** `with self._busy.busy("send")` wraps the body of `ProxyExecutor._execute_sync` (`ProxyService.py:591`). The retry sleep `_wait_for_retry` (`:574`) is outside it.

#### 2.4 WorkerPool status

`get_status()` (`workerpool.py:624`) adds these per shard: `timeout_s`, `answered_at` (timestamp or `None`), `disabled_by_timeout` and `disabled_at`.
- **`disabled_at`** is a new timestamp:
  - set in `check_timeouts` next to `_disabled_by_timeout[shard] = True` (`:707`);
  - cleared wherever that flag is cleared (`:270`, `:733`, `:775`).
- The keys are additive, and AWS ignores them.

#### 2.5 Probe log demotion

`WebService` gets a Tornado `log_function`:
- `GET /static/favicon.ico` with a status below 400 is logged at DEBUG;
- every other request is logged as today.

This covers both Caddy's checks and the watchdog's.

#### 2.6 supervisord policy (`docker/generate_config.py`)

- **Every program block**, including the loader, gets:
  - `stopasgroup=true` and `killasgroup=true`;
  - `startretries=30`. supervisord's backoff is linear, so this gives about 8 minutes before FATAL.

  `startsecs` stays at its default of 1. CWS keeps `stopwaitsecs=40` for the operator's own stops.
- **The Checker** is removed from `[services]` (`:142`).
- **The tests** in `docker/test_generate_config.py` follow:
  - the flags are on every block;
  - Checker is absent;
  - the existing `stopwaitsecs` tests keep their meaning.
- **`docs/docker-scripts.md`**, which describes `startretries` 3, follows too.

#### 2.7 `./status.sh`

- `supervisorctl status` exits 3 whenever any program is not RUNNING, even a deliberately STOPPED one. Today the script then prints "(container not running)".
- The fixed script:
  - shows the table for exit codes 0 and 3;
  - prints "(container not running)" only for the other codes;
  - then prints `cmsWatchdog --status`, once Part 2 exists.

### 3. The watchdog (Part 2)

#### 3.1 Modules

| Module | Role | May import |
|---|---|---|
| `cms/watchdog/policy.py` | **Pure.** Turns observations into decisions: dataclasses `Observation`, `ProgramFacts`, `Decision`. Holds every threshold constant | stdlib only |
| `cms/watchdog/probes.py` | HTTP, RPC, the `psql` gate and snapshot, `/proc` facts | stdlib, `cms.config` |
| `cms/watchdog/control.py` | Process sources (`SupervisorSource`, `ProcSource`) and actuators (`SignalActuator`, `DryRunActuator`) | stdlib |
| `cms/watchdog/main.py` | Wiring, the cycle, alerts, ledger, journal, status file, self-guard, CLI | the above, `cms.config` |

- **Dependencies point inward:** `policy` knows nothing about sockets, supervisord or PostgreSQL. `probes` and `control` know nothing about rules.
- **The watchdog does not share failure modes with what it watches.** It is not an `AsyncService`, has no `[services]` entry, and does not use `AsyncRemoteServiceClient` or the `cms.log` handlers.
- **It lives in `cms/` rather than `cmscontrib/`,** because several `cmscontrib` modules call gevent's `patch_all()` at import, and the watchdog's tests belong in the asyncio pytest group.
- **The script** is a `console_scripts` entry in `setup.py`: `cmsWatchdog=cms.watchdog.main:main`.

#### 3.2 Finding programs and ports

**Programs come from a process source.**

- **`SupervisorSource`:**
  - It speaks XML-RPC on the unix socket named by `SUPERVISOR_SERVER_URL` (which supervisord exports to its children), falling back to `unix:///tmp/supervisor.sock`.
  - Each call has a 5 s socket timeout and runs in a 1-thread executor under `asyncio.wait_for`.
  - `getAllProcessInfo()` gives each program's name, state, pid and start time. Program commands come from `getAllConfigInfo()`.
  - The plan verifies `getAllConfigInfo()` against the image's supervisor version. If it does not return the command, the source parses the generated supervisord configuration file instead.
- **`ProcSource`** (the harness):
  - It scans `/proc/*/cmdline` and takes start times from `/proc/<pid>/stat`.
  - A program is RUNNING while present, and ABSENT once it has been seen and is gone. ABSENT only alerts.
  - It cannot signal or start anything, so `main` forces observe mode with it.
- **Commands are parsed with the `cms<Service> <shard>` rule.** Only these kinds are managed: LogService, ResourceService, EvaluationService, ScoringService, ProxyService, ContestWebServer, AdminWebServer and Worker. Everything else is never acted on: the loader (node), TelegramBot (no RPC server), the watchdog itself, and any unknown command.

**Classes,** used by the holds and by `CMS_WATCHDOG_RESTART`:
- `web`: every CWS, plus AWS;
- `core`: ES, SS, PS;
- `infra`: LogService, ResourceService;
- `workers`: every Worker.

**Ports come from `cms.config`.**
- **RPC:** `config.services[coord]`. A `0.0.0.0` or empty host is probed at `127.0.0.1`.
- **HTTP:** `config.contest_web_server.listen_port[shard]` and `config.admin_web_server.listen_port`, at `127.0.0.1`.
- **Rankings:** the host and port of each `config.proxy_service.rankings` URL. The probe sends no credentials.

The probed set is what is running, so services that are listed but never started are ignored: ResourceService in the harness, for example.

#### 3.3 The cycle

The cycle is one asyncio loop with a monotonic 15 s schedule.

1. **Measure the lag** between the scheduled and the actual start of the cycle.
   - If the lag is over 5 s, the cycle is **starved**: it records the observations and makes no decisions.
   - If more than 30 s passed since the previous cycle, the watchdog logs "paused for N s" and clears every miss window. This covers a host pause, a VM pause or `docker pause`.
2. **Gather the probes concurrently,** each under its own timeout (3.4):
   - the process source;
   - the DB gate and snapshot;
   - HTTP;
   - `health()` of each asyncio service;
   - `workers_status()` on ES, only if ES's `health()` answered;
   - the rankings.
3. **Decide.** `policy.decide(state, observation, now)` returns a new state and a list of `Decision`s.
4. **Act.** Each action runs as its own task, with at most one action in flight. Probing continues during an action's 10 s TERM wait.
5. **Record.** Write the status file and the ledger (temporary file, then rename), append to the journal, and emit the alerts. Then re-arm the self-guard.

#### 3.4 Probes

| Probe | How | Timeout | OK when |
|---|---|---|---|
| HTTP | raw `asyncio.open_connection`, `GET /static/favicon.ico HTTP/1.0` | 5 s | the status line is 2xx or 3xx |
| RPC | the CMS JSON-RPC wire format on asyncio streams (as `cmstestsuite/loadtest/monitor.py` does): `health`, plus `workers_status` on ES | 3 s | an answer with no error |
| Ranking | `GET / HTTP/1.0` | 5 s | any HTTP status line |
| DB gate and snapshot | one `psql -X -A -t -v ON_ERROR_STOP=1` subprocess (3.4.1) | `PGCONNECT_TIMEOUT=3`, `statement_timeout=3000`, killed at 7 s | exit 0 and valid JSON |
| `/proc` facts (evidence only) | State, `wchan`, per-thread states, and the change in utime+stime over the last cycle | none needed | — |

##### 3.4.1 The DB gate

**How it runs.**
- The `psql` environment is built from `config.database.url`, with the `+psycopg2` driver suffix stripped:
  - `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` (environment only, never argv) and `PGDATABASE`;
  - `PGAPPNAME=cmsWatchdog`;
  - `PGOPTIONS='-c statement_timeout=3000'`.
- There is no persistent connection, and no transaction stays open.
- The query returns a JSON array with one row per backend of the current database:
  - `pid`, `application_name`, `client_port`;
  - `state`, `wait_event_type`, `wait_event`;
  - the ages of `xact_start`, `query_start` and `state_change`;
  - `pg_blocking_pids(pid)`;
  - `left(query, 300)`.

**What it decides.**
- **Green** means the query returned. **Red** carries a reason classified from stderr: refused, timeout, too many clients, authentication, or other. A connection exhaustion is red: it blinds the gate and the snapshot together.
- The same snapshot feeds the lock-wait hold (3.6) and the evidence files (3.8). Query text goes only to evidence files, never into alerts.

#### 3.5 Rules

Rules turn observations into trips. A trip becomes an action only if no hold applies (3.6).

| Rule | Programs | Trips when | Early WARNING |
|---|---|---|---|
| R1 loop/HTTP | CWS N, AWS | the favicon probe failed in 5 of the last 6 cycles | 3 of the last 6 |
| R2 loop/RPC | ES, SS, PS, LogService, ResourceService | `health()` failed in 5 of the last 6 cycles | 3 of the last 6 |
| R3 ES section | ES | the oldest `busy` entry is a `post_finish_lock:*` section with `age_s` > 180 and `idle_s` ≥ 60 | `age_s` > 90 |
| R3 SS section | SS | the `score` section's `age_s` > 180 | > 90 |
| R3 PS section | PS | the `send` section's `age_s` > 300, and every ranking answers its probe | > 150 |
| R4 Worker | Worker N | (a) ES reports `disabled_by_timeout`, `now − disabled_at` ≥ 180 s, and the process started before `disabled_at`; or (b) the program has been RUNNING ≥ 300 s, ES has been up ≥ 120 s, at least one other Worker is connected, and this one is not | — |
| R5 FATAL | any managed program | supervisord reports FATAL, the gate went red and then green after the program's last start, and the gate has been green for 2 cycles | — |
| Ranking | each configured ranking | `GET /` failed 3 cycles in a row | alert only, never an action |

**Rules about the rules:**
- **Invalidations.** An ES section whose holder is an invalidation (a re-evaluation) never trips R3. Past 180 s it alerts "re-evaluation still running for N min" every 5 minutes. The plan pins the exact outermost decorated name with a test that runs an invalidation and reads `health()`.
- **Supervisord states.** Only RUNNING programs are judged. STOPPED, STOPPING, STARTING, BACKOFF, EXITED and UNKNOWN are never acted on: they belong to supervisord or to the operator. FATAL is handled only by R5.
- **Incidents.** An incident opens at the first trip and closes after 2 consecutive passes, with a "Resolved:" WARNING. Within an open incident, a second action needs a new trip after the first action's grace, and it consumes budget.
- **Observe and enforce run the same policy.** In observe mode, a would-action is booked in the ledger and the journal as if it had been done. The observe log therefore predicts enforce, including budget exhaustion.

#### 3.6 Holds

Holds turn a trip into an alert and nothing else. They are checked in this order, and the alert names the first one that applies.

| Hold | Applies when |
|---|---|
| H1 grace | the program started less than 120 s ago (by its supervisord start time, whoever started it), or the watchdog itself started less than 120 s ago |
| H2 database | the gate is red. Restarts also wait until the gate has been green for 120 s in a row, and miss windows are cleared when it turns green, so a program must fail again *after* the database is back. R5 has its own condition |
| H3 class correlation | 2 or more programs of the trip's class are failing: ≥ 3 misses among their last 6 probes, or a section older than half its trip. Programs that are over budget do not count. For Workers this means 2 or more R4 trips at once, which gives one alert |
| H4 global | more than 1/3 of the probed programs failed any probe in this cycle |
| H5 starvation | the cycle is starved (3.3) |
| H6 lock wait | the snapshot shows a backend whose `application_name` starts with `cms`, with `wait_event_type = 'Lock'` and a statement older than 30 s. Restarting a waiter cures nothing. The alert names each blocker, from `pg_blocking_pids`: its pid, `application_name`, state, transaction age, and that ending it is a human decision |
| H7 budget | 2 restarts of this program, or 3 restarts in total, within the last 60 minutes (sliding). FATAL starts count separately: 2 per program per 60 minutes. The alert says "will not restart X before HH:MM UTC" |
| H8 in flight | another action is still running |
| H9 supervisord | supervisord did not answer in this cycle |
| H10 class not enabled | enforce mode, but the class is not in `CMS_WATCHDOG_RESTART`. The action becomes a would-action |

#### 3.7 Actions

**`SignalActuator.restart(program)`:**
1. Write the evidence (3.8).
2. Open `os.pidfd_open(pid)`, then re-read supervisord. Go on only if the program is still RUNNING with that pid. This binds the pidfd to the right process.
3. `os.killpg(pid, SIGTERM)`. supervisord children lead their own process group, so the Worker's isolate keeper and box receive it too.
4. Wait up to 10 s for the pidfd to become readable, which means the leader exited.
5. If any process of the group remains, including the leader, `os.killpg(pid, SIGKILL)`. A `ProcessLookupError` means the group is already empty.
6. supervisord sees an unexpected exit and restarts the program. Its new pid leads a new group, which these signals cannot reach.
7. Confirm that a new pid is RUNNING within 30 s, or alert. A program that ends FATAL is left to R5.
8. **"Did it help" line:**
   - "X answers again N s after the restart", at the first pass;
   - or "X still failing 120 s after the restart".

**`SignalActuator.start(program)`:** `supervisor.startProcess(name, False)`, for FATAL programs only.

**`DryRunActuator`:** the same interface. It logs "WOULD restart …" or "WOULD start …" at WARNING and signals nothing. Evidence is still written, including the USR1 dump.

#### 3.8 Evidence

Before every action or would-action:

1. **`log/watchdog/<UTC timestamp>-<program>.json`**, in `config.log_dir` (the `cms-logs` volume in production). It holds:
   - the decision: the rule, the holds checked, and the program's last 6 observations;
   - supervisord's process info;
   - the last `health()` payload, which carries the holder's stack when the loop was free;
   - the snapshot, with each backend attributed by `application_name`;
   - the `/proc` facts.
2. **A USR1 stack dump**, sent only if the program's kind is on a fixed allow-list and its `/proc` State is not `T`. The allow-list holds the CMS Python services whose base class registers faulthandler: the kinds in 3.2.
   - A test pins the allow-list against the classes that register.
   - USR1's default action kills a process, and Node treats it as "open the inspector".
   - The watchdog waits 1 s after the signal. faulthandler writes every thread's stack to stderr, which reaches `./logs.sh`.
3. **A line in `log/watchdog/actions.jsonl`:** time, mode, program, rule, action, and the evidence file name. This is the postmortem timeline.

`cmsWatchdog --snapshot <program>` runs steps 1 and 2 on demand, with no action, before a manual restart.

#### 3.9 Alerts

- **Channels:**
  - **stdout,** always, which reaches `./logs.sh`;
  - **LogService,** through one `Log` RPC, best effort with a 2 s timeout. It has `service_name="Watchdog"`, `service_shard=0`, WARNING or ERROR. It reaches the AWS Overview, which keeps the last 100 WARNING+ records. The plan pins the fields against what `LogService.Log` (`LogService.py:77`) rebuilds, with a test.
- **Every alert says** what happened, the evidence, and the next step. For example:
  > Watchdog: restarted cmsContestWebServer 2 (pid 1234→1301) after 5 failed favicon probes in 90 s; DB OK; evidence log/watchdog/…json; stacks in ./logs.sh.
- **Rate limit:** one line per (program, rule) per 5 minutes while the problem stays open, so it stays in the Overview's 100 rows.
- **Not rate-limited:** state changes, meaning a trip, an action, "Resolved:", the gate turning red or green, a budget exhausted, and entering or leaving a hold.
- **Hourly near-miss summary at INFO:**
  - the highest favicon latency per CWS and for AWS;
  - the longest ES section and its holder;
  - the longest SS and PS executions;
  - the number of red-gate cycles.

  Observe mode is only informative if it shows how close the trips came.

#### 3.10 State and files

| File | Content | Survives |
|---|---|---|
| `run/watchdog-ledger.json` (`config.run_dir`, a tmpfs in production) | action and would-action times, for the budgets; open incidents; when the gate was last red | watchdog restarts, but not container restarts (a human action) |
| `run/watchdog.json` | the heartbeat and status, written each cycle after supervisord answered: time, mode, cycle duration, starved, the gate and its reason, per program its state, misses and section age, open incidents, budget used | rewritten every cycle |
| `log/watchdog/actions.jsonl`, `log/watchdog/*.json` | the journal and the evidence | everything (the `cms-logs` volume) |

- The files are written atomically (temporary file, then rename). The ledger is reloaded at startup.
- `cmsWatchdog --status` prints a summary from the status file, including the file's age: "watchdog: observe, last cycle 4 s ago, 0 open alerts".

#### 3.11 Self-guard

- `faulthandler.dump_traceback_later(60, exit=True)` is re-armed every cycle. A wedged watchdog dumps its stack and exits, supervisord restarts it, and the ledger keeps its budgets.
- Every probe has a timeout, every subprocess is killed at its bound, and every supervisord call has a socket timeout.

#### 3.12 Modes, knobs and kill switches

- **Modes:**
  - `cmsWatchdog --mode observe|enforce --restart <classes> [--process-source supervisor|proc]`;
  - `proc` forces observe.
- **`.env` knobs,** validated by `generate_config.py` the way `CMS_LOG_DEBUG` is: an invalid value exits.
  - **`CMS_WATCHDOG=off|observe|enforce`**, default `observe`. For `off`, no program block is written.
  - **`CMS_WATCHDOG_RESTART`**, a comma-separated subset of `web,core,infra,workers`, default `web,core,infra`. It matters only in enforce mode.
- **The program block:** `[program:cmswatchdog]` with `priority=90`, `stopwaitsecs=5`, the group flags and `startretries=30`.
- **Kill switches,** both in the runbook:
  - **runtime:** `supervisorctl -c <conf> stop cmswatchdog`. No downtime, and it lasts until the container restarts;
  - **persistent:** `CMS_WATCHDOG=off` in `.env`, then `./up.sh` (a container recreate).

### 4. Caddy profile (documented; the operator applies it)

The repo has no Caddyfile. `docs/docker-deployment.md` carries this reference for the CWS site, based on the infra panelist's profile measured on Caddy 2.11.7. Its header timeout is 120 s (Decision Record).

```
reverse_proxy 127.0.0.1:<cws ports …> {
    lb_policy cookie
    lb_try_duration 15s
    lb_try_interval 250ms

    health_uri /static/favicon.ico
    health_interval 10s
    health_timeout 5s
    health_fails 3
    health_passes 1

    fail_duration 10s
    max_fails 1

    transport http {
        dial_timeout 3s
        response_header_timeout 120s
    }
}
```

**AWS:** `lb_try_duration 15s`, `lb_try_interval 250ms` and `dial_timeout 3s`, with no active or passive checks and no header timeout.

**Do not set** `unhealthy_status`, `unhealthy_latency`, `unhealthy_request_count` or `lb_retry_match`. The default retries only GET, which is right, because a POST may already have committed.

The document also says:
- **Why each value:**
  - ejection needs 25-35 s of unresponsiveness, against the observed 1.8-3 s commit freezes;
  - passive `max_fails 1` is what makes the cookie policy fail over a dead shard;
  - the header timeout must exceed `pool_timeout` plus handler time.
- **Revisit when #39 stage 2 lands:** `health_fails 5`, and possibly a 180 s header timeout.
- **An optional `handle_errors` page for 502 and 503:** "The server is busy. Wait a minute and reload. Submissions already sent are saved: check your list before sending again."
- **How to apply it:**
  - back up the site file;
  - `caddy validate`, then a graceful reload;
  - check that the admin API's `/reverse_proxy/upstreams` shows `fails: 0`;
  - never on a contest day, and never in the same window as a CMS deploy.

### 5. Operator documentation

Every English edit is followed in `docs/locale/es/LC_MESSAGES/*.po`.

- **`docs/contest-day.md` (the runbook), a new section "When the watchdog alerts":**
  - what each alert and hold means, and what to do;
  - how to read the evidence directory;
  - `--snapshot` before a manual restart;
  - both kill switches.
  - **What contestants see when a CWS restarts:** a `/submit` cut off by a restart may have committed, so contestants should check their list before sending again. Up to 60 s of activity records are lost, and the restart times for the multi-device review are in `actions.jsonl`.
  - The game-day steps (Validation).
  - **A line valid today:** "if every shard returns 503 'no upstreams', it heals by itself within about 30 s; restart nothing."
- **`docs/docker-deployment.md`:** the watchdog, its two knobs and the Caddy profile (section 4).
- **`docs/docker-scripts.md`:** `startretries`, the group flags, and the `./status.sh` output.
- **`.env.example`:** both knobs, with their defaults.

## Testing

Unit tests run in the asyncio pytest group (`cmstestsuite/unit_tests/watchdog/`, plus the service tests). `docker/test_generate_config.py` follows the generator.

- **`policy.py`:** table-driven scenarios with a fake clock. Each scenario is a list of observations and the expected decisions.
  - R1-R5 each trip at exactly their threshold, and not one cycle earlier. The 5-of-6 window trips on a livelock pattern (miss, miss, pass, miss, miss, miss).
  - Each hold H1-H10, alone.
  - Starved cycles and the "paused" reset.
  - Sustained green: no restart for 120 s after the gate turns green, and windows cleared.
  - R5 needs the red→green transition, and has its own budget.
  - An ES section with recent statements never trips, and an invalidation holder only alerts.
  - Workers: a Worker that restarted after `disabled_at` is not tripped, and an unhealthy ES suppresses R4.
  - STOPPED, STARTING and BACKOFF are ignored. Unmanaged kinds are never acted on.
  - Budget exhaustion, recovery after the window, and the 5-minute repeats.
  - Incidents open and close.
  - **Invariants across all scenarios:**
    - at most one action in flight;
    - no action while the gate is red;
    - observe and enforce return identical decision lists, apart from the actuator.
- **Ladder test.** It asserts that:
  - the liveness trip (5 × 15 s) exceeds `pool_timeout` (`cms/db/__init__.py`) and #39's transport bound (`LIBPQ_DEFAULTS["tcp_user_timeout"]`);
  - the ES trip exceeds `EvaluationService.SWEEPER_PERIOD_S`;
  - the PS trip exceeds `ProxyService` `READ_TIMEOUT` and `MAX_RETRY_WAIT`;
  - the Worker rule's 180 s exceeds `FIRE_AND_FORGET_TIMEOUT` (`cms/io/async_rpc.py`), so ES's own `quit` attempt has ended before the watchdog acts.

  Moving any rung of the ladder makes this test name the watchdog.
- **`probes.py`:**
  - **HTTP** against a threaded `http.server`: 200, 404, a server that accepts and never answers, and a refused connection.
  - **RPC** against a small asyncio server that speaks the CMS wire format.
  - **The DB gate** with the subprocess faked: green; refused; timeout, after which the process is killed; "too many clients"; and a password that never appears in argv. Plus one test against the test PostgreSQL.
  - **Snapshot parsing** from a fixture.
- **`control.py`:**
  - **The command parser:** `cmsContestWebServer 2 -c 5`, `cmsWorker 0` and `cmsEvaluationService 0` parse to their kinds; `cmsTelegramBot 0 -c 3` is unmanaged; the tsx loader gives `None`.
  - **`SupervisorSource`** against a fake XML-RPC server on a unix socket.
  - **`ProcSource`** against a fake `/proc` tree.
  - **`SignalActuator`** against real child processes:
    - a child that exits on TERM;
    - a child that ignores TERM and is killed after the wait;
    - an already-exited pid, which is a no-op;
    - a pid mismatch on the re-check, which is refused with nothing signalled;
    - a grandchild in the group, which is reached.
  - **`DryRunActuator`** signals nothing.
- **`main.py`:**
  - one full cycle with every adapter faked;
  - atomic status and ledger writes, and the ledger reloaded after a restart;
  - the alert rate limit;
  - the self-guard re-armed each cycle;
  - `--status` and `--snapshot`;
  - the `Log` RPC fields against `LogService.Log`.
- **Service side:**
  - **`BusyTracker`:**
    - records bound to their own thread;
    - the order of the report;
    - the stack only after its threshold;
    - `idle_s` follows statements;
    - concurrent start and stop.
  - **ES `health()`:**
    - while a `@with_post_finish_lock` method blocks on a `threading.Event` in the executor, `health()` from the loop answers with the holder's name, its age and a stack that contains the method;
    - a waiter is not listed;
    - on release, `busy` is empty and `sections` went up by 1.
  - **SS and PS:** a blocked `_execute_sync` shows `score` or `send`. The PS retry sleep is not counted.
  - **WorkerPool:** the new fields, and `disabled_at` set and cleared.
  - **faulthandler,** in a subprocess: `run()` a minimal service, send USR1, and stderr has "Current thread" while the process is still alive.
  - **`log_function`:** a favicon 200 is logged at DEBUG; a 404 or any other path at INFO.
  - **The USR1 allow-list** matches the classes that register faulthandler.
- **`generate_config`:**
  - the watchdog block for `observe` and `enforce`, and its absence for `off`;
  - invalid `CMS_WATCHDOG` and `CMS_WATCHDOG_RESTART` values exit;
  - the group flags and `startretries` are on every block;
  - Checker is absent;
  - the existing `stopwaitsecs` tests still pass.

## Validation runs

These run locally, only when the user allows them, as for #39. A run that needs sudo (the `nsenter`/iptables fault technique) asks first.

**Rig P, actuation.**
- **Setup:**
  - a local `docker/docker-compose.prod.yml --profile localdb` on rootful Docker (isolate works there);
  - `CMS_CWS_COUNT=2`, `CMS_WORKER_COUNT=2`, `CMS_WATCHDOG=enforce`, `CMS_WATCHDOG_RESTART=web,core,infra,workers`;
  - a local Caddy 2.11.7 with the section 4 profile in front.
- Only this rig has supervisord and the real restart path.
- **Pass** is read from `./logs.sh`, the evidence directory and `supervisorctl status`.

| Drill | Injection | Pass |
|---|---|---|
| D1 hung CWS | `kill -STOP` CWS 1 | Caddy ejects it at about 35 s. At 65-90 s: an evidence file (State T, so no stack, which is expected), TERM, KILL after 10 s, autorestart, a "did it help" line. Caddy readmits it. CWS 0 is untouched. Ledger 1/2 |
| D2 correlated web | STOP both CWS | no restart; one H3 alert every 5 min; SIGCONT gives "Resolved" lines and no action |
| D3 hung AWS | STOP AWS | restarted; the Overview shows the line after recovery |
| D4 DB outage and FATAL | `docker pause` the db for 5 min, then `docker stop` it for 5 min. During each, restart SS by hand so that it goes FATAL | zero restarts while red; a red alert every 5 min; no restart decision for 120 s after green; SS started 30 s after green |
| D5 lock holder | in psql, `BEGIN; LOCK TABLE submission_results IN ACCESS EXCLUSIVE MODE;` then submit a solution | H6 alert naming the psql pid; no ES restart; after COMMIT, "Resolved" and the submission is scored |
| D6 hung ES loop | STOP ES | R2 restart; the startup sweep re-enqueues; the submission's score is correct (checked in the DB) |
| D7 ES stuck under its lock, and the ladder order | drop only ES's DB connections (client ports from the snapshot), with #39's `faults.py` technique. (a) With #39's kill-switch URL suffix, R3 trips at about 180 s with no statements: a restart, and grading resumes. (b) With #39's defaults, the write fails at about 60 s, the lock is released, and nothing trips | as stated |
| D8 long re-evaluation | re-evaluate a whole contest on the database a 1000-user harness run left | no restart; "re-evaluation still running" if it passes 180 s. Its duration is recorded as the margin |
| D9 hung ranking | replace RWS with a listener that accepts and never answers | PS never trips (each call ends at 120 s); a "ranking not answering" alert |
| D10 stuck Worker | STOP a Worker in the middle of a job | ES disables it at ≥ 600 s. 180 s later: `killpg`, autorestart, reconnect, re-enabled. No `isolate` is left in `ps`, and the next `--init` on that box succeeds. Without `workers` in the list: an alert only |
| D11 budget | STOP CWS 1 again after each restart; then hang three different programs within an hour | exactly 2 restarts of CWS 1, then "will not restart before HH:MM" every 5 min; the 4th program is not restarted |
| D12 pauses | (a) STOP the watchdog for 5 min, then CONT; (b) `docker pause cms` for 120 s | `./status.sh` shows a stale heartbeat; on resume "paused for N s" and no action |
| D13 operator actions | `./up.sh` recreate; `supervisorctl stop` of a CWS and of a Worker; `stop cmswatchdog` | no action within the grace; STOPPED programs are never started or counted; no actions while the watchdog is stopped |
| D14 watchdog dies mid-restart | `kill -9` the watchdog between TERM and KILL | nothing is left STOPPED; the new watchdog keeps the ledger and re-detects after its grace |

**Harness H: false positives under load, after #39 stage 1 is in `beta`.**
- `run.sh --watchdog` runs `cmsWatchdog --mode observe --process-source proc` in the harness `cms` container.
- It adds a `watchdog.md` row to the outputs: the WOULD lines, the trips and holds, and the probe latency p50 and p99.

| Run | Pass |
|---|---|
| H1 `full1` (250 users) | zero WOULD lines and zero holds; the gate always green; favicon and `health()` p99 under 1/3 of their timeouts; every near-miss maximum under half its trip |
| H2 1000 users | the same |
| H3 `--faults` (#39 F1-F4) | the gate red only inside the F2 and F3 windows; holds logged there; zero WOULD lines; nothing for F1 and F4 |

**Game day on production.** The operator runs it in a quiet window, from the runbook:
- SIGSTOP CWS 3 for 120 s, then SS for 240 s;
- check the Caddy log, the Overview, `./logs.sh` and the evidence directory;
- once in observe mode, and once right after enforce is switched on.

## Rollout

Each step is its own deploy, never on a contest day, never within 3 days before one, and never together with a #39 stage.

1. **The Caddy profile, alone,** after a local check of the snippet on the same Caddy version.
2. **#39 stage 1.**
3. **#38 Parts 1 and 2,** in observe mode (the default).
4. **Enforce,** for the classes that met these gates:
   - D1-D14 pass on the deployed commit, and H1-H3 pass;
   - at least one session with real users (a contest, or a rehearsal of 2 hours or more) ran in observe mode;
   - every WOULD line is explained, and no correlation alert appeared outside known database events;
   - near-miss maxima stayed under half of each trip: favicon under 30 s, ES section under 90 s, SS under 90 s, PS under 150 s;
   - the nightly 05:00 restart was observed at least 7 times (it exercises the gate and R5);
   - the operator ran the game day from the runbook.

   Then the operator sets `CMS_WATCHDOG=enforce` in `.env` and runs `./up.sh` between contests, and repeats the game day.
5. **`workers` is added to `CMS_WATCHDOG_RESTART` later,** once observe mode shows no false Worker alerts.

## Out of Scope

- **A host watchdog or autoheal,** and therefore a hang of the whole container or of supervisord. Revisit only if one is ever seen.
- **The heartbeat in the compose healthcheck.** It is a follow-up once observe mode has been stable.
- **Restarting `ranking`,** which only the host can do. The watchdog only alerts about it.
- **Restarting shards one at a time during a correlated failure.** The runbook gives that procedure to a human.
- **Pushing alerts to Telegram,** or a watchdog summary line on the Overview.
- **The non-blocking Docker logging mode.**
- **#39 stage 2.** The watchdog's section data will show its effect.

## Open Points for the Operator (not blocking)

- **Apply the Caddy profile** (Rollout step 1). Until then, a restart of one shard gives its pinned users 502s for up to 30 s, whether a human or the watchdog does it.
- **The private contest-day report script** should copy `log/watchdog/` (the journal and the evidence).
