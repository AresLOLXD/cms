# Watchdog for Hung Programs (#38) — Design Doc

**Date:** 2026-10-10
**Issue:** #38 (wave C of the pre-10-10 risk audit).
**Status:** Designed. A panel of three specialist subagents chose the approach, as the user asked on 2026-10-07 for backlog issues (see the Decision Record). A spec review followed (1 Critical, 11 Important and 20 Minor findings, all applied), then a re-check of the revision (1 Important and 10 Minor findings, all applied).
**Target:** `beta`. The implementation branch starts from `beta` **after #39 stage 1 is merged there** (the architecture panelist's "Order" bullet in the Decision Record). This spec commit is rebased onto that point.

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
  - A paused or blackholed database never produces FATAL, only restart cycling. Each start attempt waits longer than `startsecs=1`: #39's 10 s `connect_timeout`, or forever before #39. supervisord therefore counts the program as started and resets its backoff.
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
     - the watchdog restarts the shard 65-90 s after the first missed probe, plus at most 10 s to stop it.
   - **ES:** its locked section held for more than 180 s, with no database statement for 60 s, a holder thread that used less than 1 s of CPU in that minute, and no ES statement running on the server, apart from a lock wait.
   - **SS:** one execution longer than 180 s. **PS:** a send that has started no HTTP request for more than 180 s, while every ranking answers.
   - **Deviation from the agreed "about a minute":** the HTTP trip is about 1.5 minutes. It must stay above the 60 s pool and transport timeouts of the #39 ladder: below them it would restart shards that are about to heal by themselves. Caddy's failover delivers the minute that users see.
2. **No restart storms.**
   - No restart while PostgreSQL is unreachable or out of connection slots, or while a lock holder blocks CMS.
   - No restart while several programs fail together.
   - A restart budget per program, and two overall ones: one for Workers, one for the other classes.
3. **Evidence is attempted before each action** (or each would-action in observe mode), and a failure is alerted: a database activity snapshot, the holder's stack and the process state.
4. **A busy Worker is never treated as hung.** Workers are judged only by ES's own verdict (`disabled_by_timeout`), never by `echo` or by how long they have been busy.
5. **Programs left FATAL by a database outage are started again** once the database is back.
6. **Observe mode comes first.**
   - The same policy runs, but logs "WOULD restart" instead of acting.
   - It sends no signal but the evidence USR1, and only to a process that handles it.
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
   - probe requests are logged at DEBUG;
   - RPC probes reuse one connection per service, so a service logs "Established connection" only when the watchdog starts, after the service restarts, or after a failed probe.

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
    - three merges: service-side health, then the watchdog in observe mode, then the enforce flip;
    - observe mode predicts enforce's behaviour, including budget exhaustion, but it re-trips after each grace, because nothing was cured.
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

**Disagreements, and how they were settled.** The rule was the majority, or else the more conservative choice, because production has no staging. The last two rows are agreed values that the spec review changed.

| Point | Proposals | Settled as |
|---|---|---|
| HTTP and RPC liveness trip | 3 in a row, 45 s (infra); 5 in a row (architecture); 5 of the last 6 (operations) | **5 misses among the last 6 probes, 15 s apart.** Two panelists stay above the 60 s timeouts, and the sliding window catches livelocks |
| Budget | 2 per program and 3 overall per 60 min (operations); 2 per program and 4 overall per 15 min (architecture); 3 per program and 4 overall per 10 min (infra) | **2 per program and 3 overall per sliding 60 min**, the most conservative. FATAL starts have their own budget of 2 per program per 60 min (operations). **Workers have their own overall pool of 3 per sliding 60 min**, separate from the pool of web, core and infra, and the per-program limit of 2 stays; the spec review added it, because Worker restarts would otherwise use up the budget that a hung AWS or ES needs (cost if wrong: up to 3 extra Worker restarts per hour) |
| Where the budget lives | `run/` tmpfs (architecture, infra); a journal on the `cms-logs` volume (operations) | **The ledger lives in `run/`** (majority). It survives watchdog restarts. A container restart resets it: a human one, or the scheduled 05:00 restart, which is harmless. **Every action is also appended to a journal in `log/watchdog/`** for postmortems (operations) |
| Heartbeat in the compose healthcheck | yes (infra); not yet, because it gates `up --wait` and a watchdog bug would fail a contest-day deploy (architecture, operations) | **Not in v1** (majority). The heartbeat is shown in `./status.sh` |
| DB gate | `pg_isready` plus `SELECT 1` (operations, infra); one `psql` query only (architecture) | **One `psql` query per cycle**, which also returns the activity snapshot. Every failure `pg_isready` detects also fails a query, and psql's error text classifies the reason. Neither of the other panelists named a case where `pg_isready` changes a decision |
| Workers | alert only in v1 (operations); restart on ES's disabled-by-timeout (architecture, infra) | **Restart only in enforce mode, only when the `workers` class is listed**, and that class is not in the default list. After 180 s of `disabled_by_timeout` (operations' value, the most conservative) or "never connected" |
| TERM wait | 10 s for every program (operations, infra); 40 s for a CWS whose loop still answers RPC (architecture) | **10 s for every program** (majority). An HTTP trip means the loop has not answered for 65-90 s, so a flush would not run anyway |
| FATAL start condition | 2 green cycles (architecture); after a red→green transition since the program's last start (infra); 30 s of green (operations) | **The gate was red at some time in [last_start − 15 s, now), and it has been green for 2 cycles (30 s) since. One action starts every eligible FATAL program**, in class order infra → core → web → workers. A program that went FATAL with the database up is left to a human. The spec review fixed the window, which could be read two ways, and made the start one batch, because the correlation holds blocked single starts and one start per cycle took minutes |
| Watchdog priority | 80 (architecture); 90 (infra) | **90**: it starts last and stops first. Both are above the loader's 70 |
| Mode knobs | `off\|observe\|enforce` (architecture); `alert\|restart`, with no off (infra); `off`, plus a list of classes (operations) | **`CMS_WATCHDOG=off\|observe\|enforce`**, default `observe`, and **`CMS_WATCHDOG_RESTART`**: the classes that enforce may act on, default `web,core,infra` |
| Caddy `response_header_timeout` | 90 s (operations); 120 s (architecture); 180 s (infra) | **120 s**, the median. It must exceed `pool_timeout` (60 s) plus #39 stage 2's `lock_timeout` (30 s) plus handler time. Revisit when stage 2 lands |
| AWS behind Caddy | a 300 s header timeout (operations); retries only, no timeout (infra) | **Retries only** (infra, tested): admin exports can be slow, and AWS has no failover target |
| How long observe mode lasts | 14 days (operations); drills plus one session (architecture) | **A list of gates instead of a fixed time** (Rollout). It includes one session with users and 7 nightly restarts |
| ES section trip | held for more than 180 s, with no statement from the holder for 60 s (all three agreed) | **Also: the holder thread used less than 1 s of CPU over the last 60 s, and no ES backend is running a statement on the server, apart from a lock wait.** The spec review added both conditions, because one long statement, or long processing of a large result, also issues no statement for 60 s. Its re-check let lock waits through, so that the lock-wait hold can name an ES blocker |
| PS section trip | one execution longer than 300 s (all three agreed) | **No new HTTP request for 180 s inside a `send` section, while every ranking answers.** The spec review changed it, because one execution sends every type of every namespace and has no time bound while it makes progress |

## Design

### 1. Overview

There are two parts on one branch. They ship in the same release.

- **Part 1, service-side health.**
  - A `health()` RPC on every asyncio service.
  - Busy-section tracking in ES, SS and PS.
  - New WorkerPool status fields.
  - faulthandler on USR1.
  - Probe log demotion.
  - The pool timeout as a constant, `cms.db.POOL_TIMEOUT_S`, for the ladder test.
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
                    ├─ RPC health() on each asyncio service; workers_status() on ES (one persistent connection each)
                    ├─ HTTP GET / on each configured ranking (alert only)
                    └─ policy → holds/vetoes → actuator (ledger, evidence, then signal group | dry run) → alerts, status
```

### 2. Service side (Part 1)

#### 2.1 `cms/io/health.py`: busy sections

This is a small module that imports only the standard library at module level. `count_statements_of` imports `sqlalchemy.event` lazily, inside the method.

```python
class BusyTracker:
    """Track the sections of a service that must not hang, per thread."""

    def busy(self, what: str) -> ContextManager[None]: ...
        # start(what) on entry, stop() on exit
    def start(self, what: str) -> None: ...
        # records (what, tid=threading.get_native_id(), start=monotonic,
        # statements=0, last_statement=start) for the current thread,
        # replacing any stale record of that thread
    def stop(self) -> None: ...
        # drops the current thread's record
    def step(self) -> None: ...
        # +1 statement and last_statement=monotonic for the current
        # thread's record, if any
    def count_statements_of(self, engine) -> None: ...
        # imports sqlalchemy.event here, then
        # event.listen(engine, "before_cursor_execute", ...) calling step()
    def report(self, stack_after_s: float = 30.0) -> list[dict]: ...
        # [{"what", "tid", "age_s", "statements", "idle_s", "stack"}], oldest first.
        # tid: the holder's native thread id.
        # idle_s = time since the last statement or step (or since start if none).
        # stack: the last 30 frames of that thread, only once age_s >= stack_after_s.
```

- Each thread changes only its own record. The loop reads a `list(dict.items())` copy, which the GIL makes atomic. There is no lock, and `report()` never takes `post_finish_lock`.
- The engine is a parameter, so `cms.io` does not import `cms.db`.

#### 2.2 `health()` RPC and faulthandler

- **faulthandler:** one helper calls `faulthandler.register(signal.SIGUSR1, all_threads=True)`.
  - `AsyncService._async_run()` calls it. `WebService.run()` overrides `run()` and never calls `super().run()` (`cms/io/web_service.py:207-214`), but `WebService._async_run()` reaches the helper through `super()` (`:221`). So CWS and AWS register too.
  - The gevent `Service.run()` (the Worker) calls the same helper. It needs no `health()`.
  - Neither calls it from `__init__`, so constructing a service in a test registers nothing.
- **`AsyncService`:**
  - it records its start time;
  - it gains a sync RPC method, answered on the event loop, so that any answer at all proves the loop is free:

  ```python
  @rpc_method
  def health(self) -> dict:
      # {"pid": int, "uptime_s": float,
      #  "busy": self._busy.report() or [], **self._health_extra()}
  ```

  `_health_extra()` returns `{}` by default.
- **`pid`** lets the watchdog check that the process answering on the port is the one supervisord started. A mismatch gives an alert only. It is never a reason to signal the process that holds the port.

#### 2.3 Sections in ES, SS and PS

- **ES:**
  - `with_post_finish_lock` (`EvaluationService.py:261`) calls `self._busy.start("post_finish_lock:" + func.__name__)` when `_post_finish_depth` goes from 0 to 1, under the lock.
  - It calls `self._busy.stop()` when the depth returns to 0, in its own `finally` around `_dequeued_in_section.clear()` and `_flush_loop_actions()` (`:279-281`). If either raised before `stop()`, a stale record would stay, become a phantom section and give a false R3.
  - Only the outermost section is tracked. Threads waiting for the lock are not listed.
  - `count_statements_of(cms.db.engine)` is called at startup.
  - `_health_extra()` returns `{"queue": int}`, for the evidence only: no rule reads it.
  - The sweeper period `117.0` becomes the constant `EvaluationService.SWEEPER_PERIOD_S`, for the ladder test.
- **The executors reach their service's tracker through a new `busy` constructor argument.** Neither can reach it today. `ScoringExecutor`'s only attribute, `proxy_service` (`ScoringService.py:47-49`), is the remote ProxyService client that ScoringService passes in (`:203-207`), not the ScoringService. `ProxyExecutor.__init__` takes only the ranking and `on_refused` (`ProxyService.py:398-402`).
- **SS:** `with self._busy.busy("score")` wraps the body of `ScoringExecutor._execute_sync` (`ScoringService.py:98`).
- **PS:**
  - `with self._busy.busy("send")` wraps the body of `ProxyExecutor._execute_sync` (`ProxyService.py:591`).
  - `ProxyExecutor._send` (`:903`) calls `self._busy.step()` before each `safe_put_data` and `safe_delete_data` call. So `statements` counts HTTP requests, and `idle_s` is the time since the last one started.
  - The retry sleep `_wait_for_retry` (`:574`) is outside the section.
- **The pool timeout:** the literal `pool_timeout=60` of both engines (`cms/db/__init__.py`, `cms/db/async_session.py`) becomes the new constant `cms.db.POOL_TIMEOUT_S = 60`, which both engines use.

#### 2.4 WorkerPool status

`get_status()` (`workerpool.py:624`) adds two keys per shard: `disabled_by_timeout` and `disabled_at`.
- **`disabled_at`** is a new wall-clock timestamp, like the existing `start_time`:
  - set in `check_timeouts` next to `_disabled_by_timeout[shard] = True` (`:707`);
  - cleared wherever that flag is cleared (`:270`, `:733`, `:775`).
- The keys are additive, and AWS ignores them.

#### 2.5 Probe log demotion

`WebService` gets a Tornado `log_function`:
- `GET /static/favicon.ico` with a status below 400 is logged at DEBUG;
- every other request is logged as today.

This covers both Caddy's checks and the watchdog's.

The RPC server's "Established connection" and "Terminated connection" lines stay at INFO, because they matter for real service connections. The watchdog keeps them rare with persistent connections instead (3.4).

#### 2.6 supervisord policy (`docker/generate_config.py`)

- **Every program block**, including the loader, gets:
  - `stopasgroup=true` and `killasgroup=true`;
  - `startretries=30`. supervisord's backoff is linear, so this gives about 8 minutes before FATAL.

  `startsecs` stays at its default of 1. CWS keeps `stopwaitsecs=40` for the operator's own stops.
- **The Checker** is removed from `[services]` (`:142`).
- **The tests** in `docker/test_generate_config.py` follow:
  - the flags are on every block;
  - Checker is absent;
  - the CWS `stopwaitsecs` test (`:290-302`), which asserts that no other program has `stopwaitsecs`, excludes `cmswatchdog`;
  - the `stop_grace_period` test (`:112-127`) and the comment above `stop_grace_period` in `docker/docker-compose.prod.yml` add the watchdog's 5 s: 4 × 40 + 5 = 165 s, still under 180 s.
- **`docs/docker-scripts.md`**, which describes `startretries` 3, follows too.

#### 2.7 `./status.sh`

- `supervisorctl status` exits 3 whenever any program is not RUNNING, even a deliberately STOPPED one. Today the script then prints "(container not running)".
- `supervisorctl` has no timeout, and it hangs on a stopped supervisord (infra X6).
- The fixed script:
  - runs `supervisorctl status` under `timeout 10`;
  - shows the table for exit codes 0 and 3;
  - prints "(supervisord did not answer in 10 s)" for 124, the exit code of `timeout`;
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
- **Commands are parsed with the `cms<Service> <shard>` rule.**
  - The parser takes the first argument whose basename is `cms` followed by an upper-case letter, and the argument after it as the shard, as `cmstestsuite/loadtest/monitor.py:70-78` does.
  - That skips an interpreter argv[0]. The services are scripts run through their shebang (`setup.py:111-124`), so `/proc/<pid>/cmdline` reads `…/bin/python3 …/bin/cmsContestWebServer 0 -c 1`, while supervisord's `command=` has no interpreter.
  - Only these kinds are managed: LogService, ResourceService, EvaluationService, ScoringService, ProxyService, ContestWebServer, AdminWebServer and Worker.
  - Everything else is never acted on: the loader (node), TelegramBot (no RPC server), the watchdog itself, and any unknown command.

**Classes,** used by the holds, the budget pools and `CMS_WATCHDOG_RESTART`:
- `web`: every CWS, plus AWS;
- `core`: ES, SS, PS;
- `infra`: LogService, ResourceService;
- `workers`: every Worker.

**Ports come from `cms.config`.** A `0.0.0.0` or empty host is always probed at `127.0.0.1`.
- **RPC:** `config.services[coord]`.
- **HTTP:** `config.contest_web_server.listen_address[shard]` and `listen_port[shard]`, and `config.admin_web_server.listen_address` and `listen_port`.
- **Rankings:** the host and port of each `config.proxy_service.rankings` URL. The probe sends no credentials.

The probed set is what is running, so services that are listed but never started are ignored: ResourceService in the harness, for example.

#### 3.3 The cycle

The cycle is one asyncio loop with a monotonic 15 s schedule.

1. **Measure the lag** between the scheduled and the actual start of the cycle.
   - If the lag is over 5 s, the cycle is **starved**. Its probes go to the evidence and to the near-miss statistics, and enter the miss windows as *unknown* (3.5). It makes no decisions.
   - If more than 30 s passed since the previous cycle, the watchdog logs "paused for N s" and clears every miss window. This covers a short host pause, VM pause or `docker pause`. A pause that runs more than 60 s past the self-guard's last re-arm ends in the self-guard's exit instead (3.11).
2. **Gather the probes concurrently,** each under its own timeout (3.4):
   - the process source;
   - the DB gate and snapshot;
   - HTTP;
   - `health()` of each asyncio service;
   - `workers_status()` on ES, only if ES's `health()` answered;
   - the rankings.
3. **Decide.** `policy.decide(state, observation, mono, wall)` returns a new state and a list of `Decision`s.
4. **Act.** Each action runs as its own task, with at most one action in flight (H8). Probing continues during an action.
5. **Record.**
   - Write the status file every cycle, even when supervisord did not answer (3.10).
   - Write the ledger's cycle state (temporary file, then rename). An action was already booked before its first signal (3.7, step 0).
   - Append to the journal and emit the alerts.
   - Then re-arm the self-guard.

**Clocks.** The policy uses two clocks:
- **monotonic:** the schedule, the lag, the miss windows and the in-flight timers;
- **wall (UTC):** `disabled_at`, supervisord's start times, the ledger, the budgets and the "HH:MM UTC" of alerts.

A wall-clock step (NTP) only shifts the budget windows and the ages computed from wall times. A negative age counts as 0.

#### 3.4 Probes

| Probe | How | Timeout | OK when |
|---|---|---|---|
| HTTP | raw `asyncio.open_connection` to the configured listen address (3.2), `GET /static/favicon.ico HTTP/1.0` | 5 s | the status line is 2xx or 3xx |
| RPC | the CMS JSON-RPC wire format on asyncio streams, over one persistent connection per service: `health`, plus `workers_status` on ES | 3 s | an answer with no error |
| Ranking | `GET / HTTP/1.0` | 5 s | any HTTP status line |
| DB gate and snapshot | one `psql -X -A -t -v ON_ERROR_STOP=1` subprocess (3.4.1) | `PGCONNECT_TIMEOUT=3`, `statement_timeout=3000`, killed at 7 s | exit 0 and valid JSON |
| `/proc` facts (evidence, plus the ES holder's CPU for R3) | State, `wchan`, per-thread states, and the change in utime+stime over the last cycle. While ES has a section, the holder thread's utime+stime from `/proc/<pid>/task/<tid>/stat`, kept for 90 s | none needed | — |

**RPC connections.**
- The RPC server logs "Established connection with …" and "Terminated connection with …" at INFO for every connection (`cms/io/async_rpc.py:130`, `:166`). A new connection per probe would put two lines in every service's log 4 times a minute.
- So the watchdog keeps one connection per probed service and sends one request at a time on it. ES's `workers_status` uses ES's connection.
- On any timeout or error, it closes the connection and reconnects at the next cycle, so a late answer is never read.
- **One exception: a stale connection.** After any restart of a service, the old connection is dead. On EOF, `ECONNRESET` or `EPIPE` before any byte of the answer, the probe reconnects once, at once, within the same probe timeout. Only a failure of the fresh connection counts as a miss.
- "Established connection" therefore appears once when the watchdog starts, and again only after a failure or a restart of the service.
- The `Log` alerts to LogService use a persistent connection of their own, in the same way (3.9). Otherwise each alert line would be followed by connection lines.

##### 3.4.1 The DB gate

**How it runs.**
- The `psql` environment is built from `config.database.url`, with the `+psycopg2` driver suffix stripped:
  - `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` (percent-decoded; environment only, never argv) and `PGDATABASE`;
  - from the URL's query: `sslmode` → `PGSSLMODE`, and `host` (a socket directory) → `PGHOST`. Other parameters, such as #39's timeouts, are ignored, because the gate sets its own;
  - `PGAPPNAME=cmsWatchdog`;
  - `PGOPTIONS='-c statement_timeout=3000'`.
- There is no persistent connection, and no transaction stays open.
- The query returns a JSON array with one row per backend of the current database. It excludes the gate's own backend (`pid <> pg_backend_pid()`, as #39's `faults.py` `CMS_BACKENDS` does). Each row has:
  - `pid`, `application_name`, `client_port`;
  - `state`, `wait_event_type`, `wait_event`;
  - the ages of `backend_start`, `xact_start`, `query_start` and `state_change`;
  - `CASE WHEN wait_event_type = 'Lock' THEN pg_blocking_pids(pid) END`. Each `pg_blocking_pids` call takes the lock manager's partition locks, so the gate calls it only for lock waiters;
  - `left(query, 300)`.
- **Permissions.** On an external PostgreSQL where the CMS role is not a superuser, many columns are NULL in the rows of other roles' sessions, for example an operator's `postgres` psql. `docs/docker-deployment.md` documents the fix: a superuser runs `GRANT pg_read_all_stats TO <cms role>;`. The H3 alert shows a NULL field as "unknown".

**What it decides.**
- **Green** means the query returned. **Red** carries a reason classified from stderr: refused, timeout, too many clients, authentication, or other. A connection exhaustion is red: it blinds the gate and the snapshot together.
- The same snapshot feeds the lock-wait hold (H3), condition (c) of R3 for ES, and the evidence files (3.8). Query text goes only to evidence files, never into alerts.

#### 3.5 Rules

Rules turn observations into trips. A trip becomes an action only if no hold applies (3.6).

| Rule | Programs | Trips when | Early WARNING |
|---|---|---|---|
| R1 loop/HTTP | CWS N, AWS | the favicon probe failed in 5 of the last 6 known results | 3 of the last 6 |
| R2 loop/RPC | ES, SS, PS, LogService, ResourceService | `health()` failed in 5 of the last 6 known results | 3 of the last 6 |
| R3 ES section | ES | the oldest `busy` entry is a `post_finish_lock:*` section, and all of: (a) its `age_s` > 180 and its `idle_s` ≥ 60; (b) its holder thread's utime+stime, from `/proc/<pid>/task/<tid>/stat`, grew by less than 1 s since the newest sample that is at least 60 s old; (c) the snapshot shows no backend with `application_name = 'cmsEvaluationService 0'` in state `active` whose `wait_event_type` is not `Lock` and whose `wait_event` is not `ClientWrite` (a NULL counts as "not") | `age_s` > 90 and `idle_s` ≥ 30 |
| R3 SS section | SS | the `score` section's `age_s` > 180 | > 90 |
| R3 PS section | PS | a `send` section's `idle_s` > 180, and every ranking answers its probe | `idle_s` > 90 |
| R4 Worker | Worker N | (a) ES reports `disabled_by_timeout`, `now − disabled_at` ≥ 180 s, and the process started before `disabled_at`; or (b) the program has been RUNNING ≥ 300 s, ES has been up ≥ 120 s, at least one other Worker is connected, and this one is not | — |
| R5 FATAL | any managed program | supervisord reports FATAL, the gate was red at some time in [last_start − 15 s, now), and it has been green for 2 cycles since | — |
| Ranking | each configured ranking | `GET /` failed 3 cycles in a row | alert only, never an action |

**Rules about the rules:**
- **R3 for ES, why (b) and (c).**
  - A long legitimate statement shows as `active` on the server and uses no client CPU. A write lost on a dead connection shows the backend `idle in transaction`, or no backend at all.
  - A lock wait passes (c) on purpose. ES then trips, and H3 holds the trip with the blocker's details. Without that trip, a lock blocking only ES would get no alert that names the blocker.
  - (b) uses the newest CPU sample of the holder thread that is at least 60 s old. Without one, (b) is not met.
- **Half a trip.** H4, the early WARNINGs and the Rollout's near-miss gates use half of each trip, defined per rule:
  - R1 and R2: 3 misses among the last 6 known results;
  - R3 ES: `age_s` > 90 and `idle_s` ≥ 30;
  - R3 SS: `age_s` > 90;
  - R3 PS: `idle_s` > 90.
- **Invalidations.** An ES section whose holder is an invalidation (a re-evaluation) never trips R3. Past 180 s it alerts "re-evaluation still running for N min" every 5 minutes. The plan pins the exact outermost decorated name with a test that runs an invalidation and reads `health()`.
- **Supervisord states.** Only RUNNING programs are judged. STOPPED, STOPPING, STARTING, BACKOFF, EXITED and UNKNOWN are never acted on: they belong to supervisord or to the operator. FATAL is handled only by R5.
- **Windows.**
  - Each probed program keeps a window of its last 6 known results.
  - A starved cycle's results enter it as *unknown*, which counts as neither a miss nor a pass.
  - A program's windows are cleared when it gets a new pid, when the gate turns green (H2), and after a pause (3.3).
- **R5 and the holds.**
  - R5 obeys H1, H2 with its own condition (2 green cycles, not 120 s), H6 for the watchdog's own startup only, H7 with the FATAL budget, H8 and H9. H8 applies because the batch is one action.
  - It is exempt from H3, H4, H5 and the program part of H6: starting a FATAL program cannot make a correlated failure worse.
  - One R5 action starts every eligible FATAL program, in class order infra → core → web → workers.
  - The ledger keeps the gate's red intervals to answer R5's question (3.10).
- **Incidents.**
  - An incident opens at the first trip and closes after 2 consecutive passes, with a "Resolved:" WARNING.
  - Within an open incident, a second action needs a new trip after the first action's grace, and it consumes budget.
  - After a would-action, where the program never restarted, that grace is 120 s after the would-action (H6).
- **Observe and enforce run the same policy.**
  - In observe mode, a would-action is booked in the ledger and the journal as if it had been done. The observe log predicts enforce's behaviour, including budget exhaustion. But nothing is cured in observe mode, so a would-action re-trips after each grace.
  - In enforce mode, the would-actions of a class that is not enabled (H9) go to a shadow ledger instead. They are logged with the program's shadow count and never touch the real budgets. H7's limits apply to the shadow ledger, so beyond them such a trip only alerts, as it would in observe mode.

#### 3.6 Holds

Holds turn a trip into an alert and nothing else. They are checked in this order, from the most diagnostic to the least.
- The first hold that applies is the headline of the alert.
- The alert also lists every other hold that applies.
- When H3 applies, the alert always carries its blocker details.

| Hold | Applies when |
|---|---|
| H1 supervisord | supervisord did not answer in this cycle |
| H2 database | the gate is red. Restarts also wait until the gate has been green for 120 s in a row, and miss windows are cleared when it turns green, so a program must fail again *after* the database is back. R5 has its own condition (3.5) |
| H3 lock wait | the snapshot shows a backend whose `application_name` starts with `cms`, with `wait_event_type = 'Lock'` and a statement older than 30 s. Restarting a waiter cures nothing. The alert names each blocker, from `pg_blocking_pids`: its pid, `application_name`, state, transaction age (a NULL field shows as "unknown", 3.4.1), and that ending it is a human decision. A blocker whose `application_name` is a program the watchdog restarted is marked "orphan of pid N" (N, the old pid) only when its backend started before that program's new start time. The new process has the same `application_name`, so an unmarked blocker may be its own backend |
| H4 class correlation | 2 or more programs of the trip's class are failing: ≥ 3 misses among their last 6 known results, or a section past half its trip (3.5). Programs that are over budget do not count. For Workers this means 2 or more R4 trips at once, which gives one alert |
| H5 global | more than 1/3 of the programs probed directly over HTTP or RPC failed any probe in this cycle. Workers and rankings do not count, on either side of the fraction |
| H6 grace | the program started less than 120 s ago (by its supervisord start time, whoever started it), or its last would-action was less than 120 s ago, or the watchdog itself started less than 120 s ago |
| H7 budget | 2 restarts of this program, or 3 restarts in its pool, within the last 60 minutes (sliding). There are two pools: one for Workers, and one for web, core and infra together. FATAL starts count separately: 2 per program per 60 minutes. For a class that is not enabled in enforce mode (H9), the same limits apply to the shadow ledger's counts. A ledger that does not parse counts as every budget exhausted (3.10). The alert says "will not restart X before HH:MM UTC" |
| H8 in flight | another action is in flight. A restart is in flight until its 30 s confirmation (3.7, step 7) ends, and a start batch until its last `startProcess` call returns. The later "did it help" line blocks nothing |
| H9 class not enabled | enforce mode, but the class is not in `CMS_WATCHDOG_RESTART`. The action becomes a would-action, booked in the shadow ledger (3.10), within H7's limits |

A starved cycle makes no decisions at all (3.3), so it needs no hold. The spec review removed the starvation hold and renumbered the others. Its first-round findings cite the old numbers: old H9 → H1, H6 → H3, H3 → H4, H4 → H5, H1 → H6 and H10 → H9. H2, H7 and H8 kept their numbers.

#### 3.7 Actions

**`SignalActuator.restart(program)`:**
0. **Book** the action in the ledger (temporary file, then rename) and append it to the journal, before any signal. A watchdog that dies after this point has still counted the restart.
   - If step 1 refuses, the booking stays, which errs toward fewer restarts, and the journal records the refusal.
   - If the ledger write fails, the action stops there, with an alert, because an unbooked action would bypass the budget.
1. Open `os.pidfd_open(pid)`, then re-read the process source. Go on only if the program is still RUNNING with that pid. This binds the pidfd to the right process.
2. **Write the evidence (3.8),** including the USR1 dump. That signal goes to the leader only, with `signal.pidfd_send_signal` on the verified pidfd. A failed evidence write does not stop the action: it gives an alert.
3. `os.killpg(pid, SIGTERM)`. supervisord children lead their own process group, so TERM also reaches a Worker's isolate keeper, which ends its box. On KILL (step 5), the box dies with its PID namespace.
4. Wait up to 10 s for the pidfd to become readable, which means the leader exited.
5. If any process of the group remains, including the leader, `os.killpg(pid, SIGKILL)`. A `ProcessLookupError` means the group is already empty.
6. supervisord sees an unexpected exit and restarts the program. Its new pid leads a new group, which these signals cannot reach.
7. Confirm that a new pid is RUNNING within 30 s, or alert. A program that ends FATAL is left to R5. The action is in flight until this step ends (H8).
8. **"Did it help" line,** later, without blocking anything:
   - "X answers again N s after the restart", at the first pass;
   - or "X still failing 120 s after the restart".

**`SignalActuator.start(programs)`:** one R5 batch.
- It books every program of the batch (step 0), then calls `supervisor.startProcess(name, False)` for each one, in class order.
- It is for FATAL programs only.
- supervisord's `ALREADY_STARTED` fault, which happens when an operator started the program first, counts as success.

**`DryRunActuator`:** the same interface.
- It does what `SignalActuator` does up to and including the evidence and its USR1: steps 0-2 of a restart, or the booking of a start.
- Then it logs "WOULD restart …" or "WOULD start …" at WARNING.
- It sends only the evidence USR1, and only to a process that handles it.
- In enforce mode, H9's would-actions go through it too, booked in the shadow ledger.

#### 3.8 Evidence

Before every action or would-action (3.7, step 2):

1. **`log/watchdog/<UTC timestamp>-<program>.json`**, in `config.global_.log_dir` (`/home/cmsuser/cms/log`, the `cms-logs` volume in production). It holds:
   - the decision: the rule, every hold checked, and the program's last 6 observations;
   - supervisord's process info;
   - the last `health()` payload, which carries the holder's stack when the loop was free, and the `queue` and `pid` fields;
   - the snapshot, with each backend attributed by `application_name`;
   - the `/proc` facts.
2. **A USR1 stack dump,** sent to the leader only, with `signal.pidfd_send_signal` on the verified pidfd, and only if all of these hold:
   - **`/proc/<pid>/status` `SigCgt` has bit 10 set** (SIGUSR1: signal N is the mask `1 << (N − 1)`, so `0x200`), which means the process handles USR1. This check is the safety mechanism. USR1's default action terminates a process, and a process that hangs before it registers faulthandler does not handle it: a Worker that never connected, for example.
   - **The program's kind is on a fixed allow-list,** as an extra filter on top of that check. The allow-list holds the CMS Python services whose base class registers faulthandler: the kinds in 3.2. A test pins it against the classes that register. Node, for example, handles USR1 too, as "open the inspector".
   - **Its `/proc` State is not `T`.**

   It is never a group signal: a group USR1 would reach a Worker's isolate keeper, which does not handle it, and kill it.

   The watchdog waits 1 s after the signal. faulthandler writes every thread's stack to stderr, which reaches `./logs.sh`.
3. **A line in `log/watchdog/actions.jsonl`,** written at step 0 (3.7): time, mode, program, rule, action, and the planned evidence file name. The record marks that name as planned: the file is written later, at step 2, and does not exist if that step failed or never ran. This is the postmortem timeline.

**A failed write.** If the evidence file or the journal line cannot be written (for example, `cms-logs` is full), the action goes on and the watchdog alerts. A full disk must not disable recovery.

`cmsWatchdog --snapshot <program>` writes items 1 and 2 on demand, after the same pidfd check as 3.7 step 1, before a manual restart. It books nothing and takes no action.

#### 3.9 Alerts

- **Channels:**
  - **stdout,** always, which reaches `./logs.sh`;
  - **LogService,** through one `Log` RPC on a persistent connection of its own (3.4), best effort with a 2 s timeout.
    - It has `service_name="Watchdog"`, `service_shard=0`, WARNING or ERROR.
    - It reaches the AWS Overview, which keeps the last 100 WARNING+ records.
    - The plan pins the fields against what `LogService.Log` (`LogService.py:77`) rebuilds, with a test.
    - A restarted LogService starts with an empty Overview table. So when LogService gets a new pid, the watchdog sends the open alerts again.
- **Every alert says** what happened, the evidence, and the next step. For example:
  > Watchdog: restarted cmsContestWebServer 2 (pid 1234→1301) after 5 failed favicon probes in 90 s; DB OK; evidence log/watchdog/…json; stacks in ./logs.sh.
- **Rate limit:** one line per (program, rule) per 5 minutes while the problem stays open, so it stays in the Overview's 100 rows.
- **Not rate-limited:** state changes, meaning a trip, an action, "Resolved:", the gate turning red or green, a budget exhausted, and entering or leaving a hold.
- **Hourly near-miss summary at INFO:**
  - for R1 and R2, per program: the largest number of misses in any window of 6 known results, and the probe latency p99;
  - the longest ES section and its holder, marking invalidations, the longest ES `idle_s` inside a section, and every ES section that passed half its trip (3.5);
  - the longest SS execution, marking the executions that overlapped an invalidation;
  - the longest PS `idle_s` inside a `send` section;
  - the number of red-gate cycles and of starved cycles.

  Observe mode is only informative if it shows how close the trips came.

#### 3.10 State and files

| File | Content | Survives |
|---|---|---|
| `run/watchdog-ledger.json` (`config.global_.run_dir`, `/home/cmsuser/cms/run`, a tmpfs in production) | action and would-action times, for the budgets (the Workers pool, the pool of the other classes, and the FATAL starts); the shadow ledger of H9's would-actions in enforce mode; open incidents; the gate's red intervals (wall-clock start and end) that ended in the last 60 minutes, plus the current one, for R5 | watchdog restarts, but not container restarts: a human one, or the scheduled 05:00 restart, which only resets the budgets early and is harmless |
| `run/watchdog.json` | the heartbeat and status, written every cycle, with `supervisord: unreachable` when supervisord did not answer: time, mode, cycle duration, starved, the gate and its reason, per program its state, misses, section age and probe latencies, open incidents, budget used | rewritten every cycle |
| `log/watchdog/actions.jsonl`, `log/watchdog/*.json` | the journal and the evidence | everything (the `cms-logs` volume) |

- The files are written atomically (temporary file, then rename).
- The ledger is reloaded at startup. A missing ledger starts empty. A ledger that exists but does not parse counts as every budget exhausted for 60 minutes, with an alert, so that a crash that corrupts it cannot reset the budgets.
- `cmsWatchdog --status` prints a summary from the status file, including the file's age: "watchdog: observe, last cycle 4 s ago, 0 open alerts".
  - With `CMS_WATCHDOG=off`, which reaches the container through the compose file's `env_file`, it prints "watchdog: off (not configured)".
  - When the file is missing, it prints "no status file".

#### 3.11 Self-guard

- `faulthandler.dump_traceback_later(60, exit=True)` is re-armed every cycle. A wedged watchdog dumps its stack and exits, supervisord restarts it, and the ledger keeps its budgets.
- **Pauses.** Its timer counts on the monotonic clock, which keeps running through SIGSTOP and `docker pause`. A pause that runs more than 60 s past the last re-arm therefore ends like this, on resume:
  - the self-guard exits;
  - supervisord's autorestart starts the watchdog again;
  - the watchdog's own 120 s grace (H6) applies;
  - no action.

  A pause of more than 30 s that the self-guard survives is logged as "paused for N s" (3.3).
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
  - **After a restart, an H3 alert with a blocker marked "orphan of pid N":** this is the orphaned server backend of the killed process, which may still hold its locks. End it with the holder query and `pg_terminate_backend` of "When the database misbehaves" (#39). End only a blocker marked that way: an unmarked blocker with the same `application_name` may be the new process's own backend.
  - **Switching enforce on:** set `CMS_WATCHDOG_RESTART` explicitly in the same `.env` edit that sets `CMS_WATCHDOG=enforce`. List only the classes that passed their gates (Rollout).
  - **What contestants see when a CWS restarts:** a `/submit` cut off by a restart may have committed, so contestants should check their list before sending again. Up to 60 s of activity records are lost, and the restart times for the multi-device review are in `actions.jsonl`.
  - The game-day steps (Validation).
  - **A line valid today:** "if every shard returns 503 'no upstreams', it heals by itself within about 30 s; restart nothing."
- **`docs/docker-deployment.md`:**
  - the watchdog and its two knobs;
  - the Caddy profile (section 4);
  - the `pg_read_all_stats` grant for an external PostgreSQL whose CMS role is not a superuser (3.4.1).
- **`docs/docker-scripts.md`:** `startretries`, the group flags, and the `./status.sh` output, including its 10 s timeout.
- **`.env.example`:** both knobs, with their defaults.

## Testing

Unit tests run in the asyncio pytest group (`cmstestsuite/unit_tests/watchdog/`, plus the service tests). `docker/test_generate_config.py` follows the generator.

- **`policy.py`:** table-driven scenarios with a fake clock. Each scenario is a list of observations and the expected decisions.
  - **Rules:**
    - R1-R5 each trip at exactly their threshold, and not one cycle earlier. The 5-of-6 window trips on a livelock pattern (miss, miss, pass, miss, miss, miss).
    - An ES section with recent statements never trips. Neither does one whose holder thread used 1 s of CPU or more in the last 60 s, or one with an `active` ES backend that is neither waiting on a lock nor in `ClientWrite`. An invalidation holder only alerts.
    - An ES section waiting on a Lock gives an alert headed by H3, and no action.
    - (b) with cycle jitter: it uses the newest CPU sample at least 60 s old, and still trips.
    - A PS `send` section that keeps starting requests never trips, however old it is. It trips at `idle_s` > 180.
    - Workers: a Worker that restarted after `disabled_at` is not tripped, and an unhealthy ES suppresses R4.
    - STOPPED, STARTING and BACKOFF are ignored. Unmanaged kinds are never acted on.
  - **Holds:**
    - Each hold H1-H9, alone.
    - The order: 2 CWS failing plus a lock wait give an alert headed by H3, which names the blocker and also lists H4.
    - The orphan mark: a blocker with a restarted program's `application_name` is marked "orphan of pid N" only when its backend started before that program's new start time.
    - H4 counts a section only past its rule's half trip: a PS batch that keeps sending, or an ES section that keeps issuing statements, does not count however old it is.
    - Sustained green: no restart for 120 s after the gate turns green, and windows cleared.
  - **Windows:**
    - Starved cycles: their results are unknown, and 4 starved misses plus 1 real miss do not trip.
    - The "paused" reset.
    - A new pid clears the program's windows.
  - **R5:**
    - It needs a red period in [last_start − 15 s, now) and 2 green cycles since, and it has its own budget.
    - It ignores H3, H4, H5 and the program part of H6.
    - 10 programs go FATAL, the gate turns green, and all 10 are started within 2 cycles, in one batch, infra first.
  - **Budgets:**
    - Exhaustion, recovery after the window, and the 5-minute repeats.
    - The Workers pool and the pool of the other classes are separate, with 2 per program in both (H7).
    - In enforce mode, H9's would-actions go to the shadow ledger, with per-program shadow counts, and never touch the real budgets (H9). Beyond H7's limits on the shadow ledger, such a trip only alerts: no evidence, no USR1, no journal line.
  - **Incidents** open and close. In observe mode, a would-action's grace ends 120 s after it.
  - **Clocks:** a wall-clock step backwards gives ages of 0, never negative ones.
  - **Invariants across all scenarios:**
    - at most one action in flight;
    - no action while the gate is red;
    - observe, and enforce with every class enabled, return identical decision lists, apart from the actuator.
- **Ladder test.** It asserts that:
  - the earliest liveness trip beats the #39 ladder: `(MISSES_TO_TRIP − 1) × CYCLE_S + HTTP_TIMEOUT_S > max(POOL_TIMEOUT_S, int(LIBPQ_DEFAULTS["tcp_user_timeout"]) / 1000)`, which is 65 > 60 today. The same holds with `RPC_TIMEOUT_S` for R2 (63 > 60);
  - the ES trip exceeds `EvaluationService.SWEEPER_PERIOD_S`;
  - the PS trip's 180 s exceeds `CONNECT_TIMEOUT + READ_TIMEOUT` (125 s) and `ProxyExecutor.MAX_RETRY_WAIT`;
  - the Worker rule's 180 s exceeds `FIRE_AND_FORGET_TIMEOUT`, so ES's own `quit` attempt has ended before the watchdog acts.

  **The symbols it reads:**
  - `MISSES_TO_TRIP`, `CYCLE_S`, `HTTP_TIMEOUT_S` and `RPC_TIMEOUT_S`, new constants in `cms/watchdog/policy.py`;
  - `cms.db.POOL_TIMEOUT_S`, new in Part 1 (2.3);
  - `cms.db.connect_options.LIBPQ_DEFAULTS` (#39), whose `tcp_user_timeout` is a string in milliseconds;
  - `cms.service.ProxyService.CONNECT_TIMEOUT` and `READ_TIMEOUT`, module constants;
  - `ProxyExecutor.MAX_RETRY_WAIT`, a class attribute;
  - `EvaluationService.SWEEPER_PERIOD_S`, new in Part 1 (2.3);
  - `cms.io.async_rpc.FIRE_AND_FORGET_TIMEOUT`.

  Moving any rung of the ladder makes this test name the watchdog.
- **`probes.py`:**
  - **HTTP** against a threaded `http.server`: 200, 404, a server that accepts and never answers, and a refused connection. A `0.0.0.0` listen address is probed at `127.0.0.1`.
  - **RPC** against a small asyncio server that speaks the CMS wire format:
    - one connection serves every cycle;
    - after a timeout, the connection is closed, a late answer is never read, and the next cycle reconnects;
    - after a server restart between cycles, the probe meets EOF on the old connection, reconnects at once within its timeout, and passes; only a failure of the fresh connection is a miss.
    - **Against a real `AsyncService`:** after the first cycle, further cycles add no INFO record.
  - **The DB gate** with the subprocess faked:
    - green; refused; timeout, after which the process is killed; "too many clients";
    - a password that never appears in argv, and that is percent-decoded;
    - `sslmode` and `host` mapped to `PGSSLMODE` and `PGHOST`.

    Plus one test against the test PostgreSQL, which also checks that the gate's own backend is absent.
  - **Snapshot parsing** from a fixture, including the NULL fields of another role's backend and the `backend_start` age.
- **`control.py`:**
  - **The command parser:**
    - `cmsContestWebServer 2 -c 5`, `cmsWorker 0` and `cmsEvaluationService 0` parse to their kinds;
    - so does the interpreter form, `…/bin/python3 …/bin/cmsContestWebServer 0 -c 1`;
    - `cmsTelegramBot 0 -c 3` is unmanaged;
    - the tsx loader gives `None`.
  - **`SupervisorSource`** against a fake XML-RPC server on a unix socket.
  - **`ProcSource`** against a fake `/proc` tree, with interpreter command lines.
  - **`SignalActuator`** against real child processes:
    - the booking is on disk before the first signal;
    - a child that exits on TERM;
    - a child that ignores TERM and is killed after the wait;
    - an already-exited pid, which is a no-op;
    - a pid mismatch on the re-check, which is refused with nothing signalled;
    - a grandchild in the group, which is reached;
    - the evidence USR1 reaches the leader only, and a process whose `SigCgt` lacks USR1 is never sent it;
    - a failed evidence write still restarts, with an alert;
    - `start` treats `ALREADY_STARTED` as success.
  - **`DryRunActuator`** sends nothing but the evidence USR1, and only to a process that handles it.
- **`main.py`:**
  - one full cycle with every adapter faked;
  - atomic status and ledger writes, and the ledger reloaded after a restart;
  - a ledger that does not parse exhausts every budget for 60 minutes, with an alert;
  - the status file is written with `supervisord: unreachable` when supervisord does not answer;
  - the alert rate limit, and the open alerts sent again when LogService gets a new pid;
  - the self-guard re-armed each cycle;
  - `--status`, including `CMS_WATCHDOG=off` and a missing file, and `--snapshot`;
  - the `Log` RPC fields against `LogService.Log`.
- **Service side:**
  - **`BusyTracker`:**
    - records bound to their own thread, with the holder's native thread id;
    - the order of the report;
    - the stack only after its threshold;
    - `idle_s` follows statements and `step()`;
    - `start()` replaces a stale record of its thread;
    - concurrent start and stop.
  - **ES `health()`:**
    - while a `@with_post_finish_lock` method blocks on a `threading.Event` in the executor, `health()` from the loop answers with the holder's name, its age, its thread id and a stack that contains the method;
    - a waiter is not listed;
    - on release, `busy` is empty, even when `_flush_loop_actions()` raises.
  - **SS and PS:**
    - a blocked `_execute_sync` shows `score` or `send`;
    - each PS request advances `statements` and resets `idle_s`;
    - the PS retry sleep is not counted.
  - **WorkerPool:** the new fields, and `disabled_at` set and cleared.
  - **faulthandler,** in a subprocess: `run()` a real `WebService` subclass and send it USR1. The process survives, and stderr has "Current thread".
  - **`log_function`:** a favicon 200 is logged at DEBUG; a 404 or any other path at INFO.
  - **The USR1 allow-list** matches the classes that register faulthandler.
- **`generate_config`:**
  - the watchdog block for `observe` and `enforce`, and its absence for `off`;
  - invalid `CMS_WATCHDOG` and `CMS_WATCHDOG_RESTART` values exit;
  - the group flags and `startretries` are on every block;
  - Checker is absent;
  - the CWS `stopwaitsecs` test excludes `cmswatchdog`, and the `stop_grace_period` test counts the watchdog's 5 s.

## Validation runs

These run locally, only when the user allows them, as for #39. A run that needs sudo (the `nsenter`/iptables fault technique) asks first.

**Rig P, actuation.**
- **Setup:**
  - a local `docker/docker-compose.prod.yml --profile localdb` on rootful Docker (isolate works there);
  - `CMS_CWS_COUNT=2`, `CMS_WORKER_COUNT=2`, `CMS_WATCHDOG=enforce`, `CMS_WATCHDOG_RESTART=web,core,infra,workers`;
  - a local Caddy 2.11.7 with the section 4 profile in front.
- Only this rig has supervisord and the real restart path.
- **Single-connection drops** (D7, D15) need new DROP rules, added to #39's `faults.py` helper. #39's `DROP_RULES` drop the whole port 5432 (`faults.py:124-128`). The new rules drop a single client port, in PostgreSQL's network namespace. Each DROP ends right after the restart is confirmed, or after 300 s.
- **Pass** is read from `./logs.sh`, the evidence directory and `supervisorctl status`.

| Drill | Injection | Pass |
|---|---|---|
| D1 hung CWS | `kill -STOP` CWS 1 | Caddy ejects it at about 35 s. At 65-90 s: an evidence file (State T, so no stack, which is expected), TERM, KILL after 10 s, autorestart, a "did it help" line. Caddy readmits it. CWS 0 is untouched. Ledger 1/2 |
| D2 correlated web | STOP both CWS | no restart; one H4 alert every 5 min; SIGCONT gives "Resolved" lines and no action |
| D3 hung AWS | STOP AWS | restarted; the Overview shows the line after recovery |
| D4 DB outage and FATAL | (a) `docker pause` the db for 5 min and restart SS by hand: this leg checks only the gate and the holds, because a paused database never produces FATAL, only restart cycling. (b) `docker stop` the db for at least 10 min, so that failures are fast (name resolution or refusal), and restart SS by hand at its start | zero restarts while red; a red alert every 5 min; no restart decision for 120 s after green. In (a), SS cycles and never goes FATAL. In (b), SS goes FATAL, then an R5 batch starts it 30 s after green |
| D5 lock holder | in psql, `BEGIN; LOCK TABLE submission_results IN <mode> MODE;` then submit a solution. (a) `ACCESS EXCLUSIVE`, which also blocks the CWS. (b) `EXCLUSIVE`, which blocks ES's writes but not the CWS's plain SELECTs | an alert headed by H3 and naming the psql pid: in (a) even while 2 or more CWS shards fail (H4 listed after it); in (b) from ES's R3 trip alone, with no CWS failing. No ES restart. After COMMIT, "Resolved" and the submission is scored |
| D6 hung ES loop | STOP ES | R2 restart; the startup sweep re-enqueues; the submission's score is correct (checked in the DB) |
| D7 ES stuck under its lock, and the ladder order | drop only ES's DB connections (client ports from the snapshot), with the single-port rules. (a) With #39's kill-switch URL suffix, R3 trips at about 180 s with no statements: a restart, and grading resumes. After the restart, an orphaned server backend may still hold its locks: expect an H3 alert that marks it "orphan of pid N" until it ends, and the runbook step that ends it. (b) With #39's defaults, the write fails at about 60 s, the lock is released, and nothing trips | as stated |
| D8 long re-evaluation | re-evaluate a whole contest on the database a 1000-user harness run left | no restart; "re-evaluation still running" if it passes 180 s. Its duration and the longest ES `idle_s` inside a section are recorded as margins |
| D9 hung ranking | replace RWS with a listener that accepts and never answers | PS never trips: each request ends within 125 s, under the 180 s idle trip, and the ranking fails its probe. A "ranking not answering" alert |
| D10 stuck Worker | STOP a Worker in the middle of a job | ES disables it at ≥ 600 s. 180 s later: `killpg`, autorestart, reconnect, re-enabled. No `isolate` is left in `ps`, and the next `--init` on that box succeeds. Without `workers` in the list: an alert only |
| D11 budget | STOP CWS 1 again after each restart; then, within the same hour, hang two more programs of web, core and infra | exactly 2 restarts of CWS 1, then "will not restart before HH:MM" every 5 min. Of the two more programs, the first is restarted (pool 3/3), and the second gets an H7 hold |
| D12 pauses | (a) STOP the watchdog for 45 s, then CONT; (b) STOP it for 5 min, then CONT; (c) `docker pause cms` for 120 s | (a) "paused for 45 s", windows cleared, no action. (b) `./status.sh` shows a stale heartbeat. On resume in (b) and (c): the self-guard dumps the watchdog's stack and exits, supervisord restarts it, and after its 120 s grace there is no action |
| D13 operator actions | `./up.sh` recreate; `supervisorctl stop` of a CWS and of a Worker; `stop cmswatchdog` | no action within the grace; STOPPED programs are never started or counted; no actions while the watchdog is stopped |
| D14 watchdog dies mid-restart | `kill -9` the watchdog right after the TERM, three times in a row | nothing is left STOPPED; each new watchdog keeps the ledger and re-detects after its grace; the ledger shows 2 restarts, then an H7 hold |
| D15 SS stuck on a lost connection | while a submission is being scored, drop only SS's DB connections, with the single-port rules and #39's kill-switch URL suffix | SS trips R3 at about 180 s with no lock wait, is restarted, and scoring resumes |

**Harness L: false positives under load, after #39 stage 1 is in `beta`.** Its runs are named L1-L3, so that they never read as holds.
- `run.sh --watchdog` runs `cmsWatchdog --mode observe --process-source proc` in the harness `cms` container.
- It adds a `watchdog.md` row to the outputs: the WOULD lines, the trips and holds, and the probe latency p50 and p99.
- **Every run** also requires that every expected program (ES, SS, PS, LogService, every CWS, AWS, every Worker) appears in `watchdog.json` in every cycle from the driver's start, and that every program the watchdog probes has non-empty probe latency samples.
  - Earlier cycles may lack ProxyService: `start.sh` starts every other service (`cmstestsuite/loadtest/start.sh:19-20`), and `run.sh` starts ProxyService only once the contests exist (`run.sh:321-333`).
  - The run fails if a program never appears, or if it disappears after it appeared.

| Run | Pass |
|---|---|
| L1 `full1` (250 users) | zero WOULD lines and zero holds; the gate always green; favicon and `health()` p99 under 1/3 of their timeouts; every near-miss maximum under half its trip (3.5) |
| L2 1000 users | the same. The longest ES `idle_s` inside a section is recorded as a margin |
| L3 `--faults` (#39 F1-F4) | the gate red only inside the F2 and F3 windows; holds logged there; zero WOULD lines; nothing for F1 and F4 |

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
   - D1-D15 pass on the deployed commit, and harness runs L1-L3 pass;
   - at least one session with real users (a contest, or a rehearsal of 2 hours or more) ran in observe mode;
   - every WOULD line is explained, and no correlation alert appeared outside known database events;
   - near-miss maxima stayed under half of each trip (3.5):
     - at most 2 misses in any 6-result window for R1 and R2;
     - no ES section past `age_s` > 90 with `idle_s` ≥ 30, with invalidation sections excluded;
     - SS under 90 s, with executions that overlapped an invalidation excluded;
     - PS idle under 90 s;
   - the nightly 05:00 restart was observed at least 7 times (it exercises the gate);
   - the operator ran the game day from the runbook.

   Then, between contests, the operator:
   - sets `CMS_WATCHDOG=enforce` in `.env`;
   - in the same edit, sets `CMS_WATCHDOG_RESTART` explicitly, listing only the classes that passed their gates;
   - runs `./up.sh`;
   - repeats the game day.
5. **`workers` is added to `CMS_WATCHDOG_RESTART` later,** once observe mode shows no false Worker alerts.

## Out of Scope

- **A host watchdog or autoheal,** and therefore a hang of the whole container or of supervisord. Revisit only if one is ever seen.
- **The heartbeat in the compose healthcheck.** It is a follow-up once observe mode has been stable.
- **Restarting `ranking`,** which only the host can do. The watchdog only alerts about it.
- **Restarting shards one at a time during a correlated failure.** The runbook gives that procedure to a human.
- **Pushing alerts to Telegram,** or a watchdog summary line on the Overview.
- **The non-blocking Docker logging mode.**
- **Demoting the RPC server's connection lines.** They matter for real service connections. The watchdog's persistent connections keep them rare instead (3.4).
- **#39 stage 2.** The watchdog's section data will show its effect.

## Open Points for the Operator (not blocking)

- **Apply the Caddy profile** (Rollout step 1). Until then, a restart of one shard gives its pinned users 502s for up to 30 s, whether a human or the watchdog does it.
- **The private contest-day report script** should copy `log/watchdog/` (the journal and the evidence).
