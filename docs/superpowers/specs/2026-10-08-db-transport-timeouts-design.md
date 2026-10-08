# Database Transport Timeouts (#39, stage 1) — Design Doc

**Date:** 2026-10-08
**Issue:** #39, stage 1 of 2 (wave 1 of `2026-10-06-post-10-10-roadmap-design.md`).
**Status:** Designed. The approach was chosen by a panel of three specialist subagents, as the user asked for backlog issues on 2026-10-07 (see the Decision Record). A spec review followed (11 Important and 18 Minor findings, all applied).
**Target:** `beta`, after the 2026-10-10 contest.

## Problem

No CMS database connection has a timeout. `cms/db/__init__.py:90` and `cms/db/async_session.py:87` set only `pool_timeout` and `pool_recycle`.

**How long a caller can block today:**

| Situation | How long it blocks |
|---|---|
| Unacknowledged data on a dead or half-open TCP connection | Until the kernel's retransmissions give up, about 15 minutes |
| A dead socket with nothing in flight, or a reply that never comes to a request already acknowledged | Until keepalive gives up, about 2 h 11 min |
| A connect that the server never answers | Indefinitely |
| A lock wait | No bound at all |

**Effect on the services:**
- In EvaluationService (ES), the call runs under `post_finish_lock`, so grading stops while the service still answers `echo`.
- In ContestWebServer (CWS), every handler runs its queries synchronously on the event loop, so one stuck query freezes a whole shard (about a quarter of the contestants).

**`connect_timeout` alone would not help.** Every process that imports `cms.io` makes psycopg2 gevent-green (`cms/io/__init__.py:50`). That is all the services, the web servers, AddSubmission and PrometheusExporter, but not `cmsSetupDB` or most CLI tools. Green psycopg2 never applies libpq's `connect_timeout`. Measured in the CMS image: a connect with `connect_timeout=2` to a listener that never answers still hung after 25 s.

**Turning hangs into errors exposes code that handles errors badly.** Three such paths matter for a disconnect:
- **(8a) ES phase-2 window.** If the commit at `EvaluationService.py:1065` succeeds and the one at `:1117` fails, every evaluation exists but the outcome stays NULL.
  - In a contest with no gated dataset (two-phase or dependencies), the plain sweeper never finalizes such a result, so the submission stays "Evaluating" for good.
  - Production runs two-phase today. The gated sweeper finalizes these through `submission_enqueue_operations` (`EvaluationService.py:487-492`), but any other contest is silently affected.
  - A real disconnect, or an ES crash between the two commits, can already cause it today.
- **(8b) ES per-row catch-all.** `write_results_one_object_and_type` (`:1171`) catches every exception per row (`:1186-1203`). After a disconnect, each later row of the same object fails on its own, under `post_finish_lock`, with one ERROR each. The next object's lookup (`:1047`) then aborts the flush, and `FlushingDict.flush` logs "Unexpected error while flushing" (`flushingdict.py:129-132`).
- **(8c) Session exit.** `SessionGen.__exit__` (`cms/db/session.py:71-73`) calls `rollback()` and then `close()`, with no `try/finally`. On a dead connection `rollback()` raises, so a block whose work had succeeded still raises on exit, and `close()` is skipped. `AsyncSessionGen` has a `try/finally`, but its rollback error replaces the body's error.

## Goal

1. **Bounded detection.** On every connection path — the sync engine, the async engine (activity recorder), and `custom_psycopg2_connection` (LargeObject, `drop_db`, the fork updaters):
   - a database call on a dead or half-open connection fails within about 70 s of its own start, instead of hanging;
   - a connect that the server never answers fails within about 10 s.
2. **Self-healing.** After such a failure, every service recovers by itself once PostgreSQL answers, with no restart, and no submission is left "Evaluating":
   - pages within a minute;
   - grading within about 2 minutes (the ES sweeper runs every 117 s);
   - scores within about 6 minutes (the SS and PS sweepers run every 347 s).

   **One case this stage cannot heal:** a symmetric network partition. There, a server backend whose client gave up can keep its transaction and locks until the server's own keepalive fires, about 2 h 11 min with the defaults. The runbook covers it (section 6), and Open Points name the host setting that bounds it.
3. **Bounded deploy locks.** The fork's schema updaters, which `cmsSetupDB` runs (production's `db-init`), never queue CMS behind their locks for more than 5 s.
   - If an updater cannot get its lock in time, it fails visibly and changes nothing.
   - `init_db()` itself is not bounded in this stage.
4. **No change on a healthy database.** Apart from the deploy's DDL bound (Goal 3), nothing in this stage fires against a PostgreSQL that answers, however busy it is. The server's kernel answers keepalives even while PostgreSQL is saturated, and the connect deadline applies only while connecting. Queries, lock waits and long transactions behave exactly as today.

   **The exception is a server that is alive but stalled**, for example on a full disk. New connections then fail at the 10 s connect deadline instead of waiting (Testing, F3).
5. **Operators can tell what happened.**
   - Each failure has a log line that names it.
   - The runbook says what to do.
   - The runbook says how to switch the transport timeouts off with the database URL alone. The DDL bound is not switched off this way.

## Decision Record

**Approaches put to the panel** (full text in the session's `db39-approaches.md`):
- **A, transport only:** client-side TCP keepalives, `tcp_user_timeout`, a connect deadline, and the fixes a disconnect exposes.
- **B, full profile in one branch:** A, plus a 30 s `lock_timeout` for all processes, plus a 120 s `idle_in_transaction_session_timeout` for services only, chosen per process by a `do_connect` hook. Plus the fixes those expose.
- **C, staged:** A first, then the session timeouts, with values taken from data.

**Verdicts:**

- **Database panelist (database-administrator): C, with the DDL bound moved into stage 1 and no `options` in stage 1.**
  - Nothing in stage 1 can fire on a healthy database, so it needs fault tests, not load tuning.
  - The 30 s `lock_timeout` margin rests on one saturated run, at 1.2x on the only retrying path. And 30 s does not unfreeze a CWS shard while the holder stays.
  - Setting `options` in code silently disables `PGOPTIONS`.
  - "Stop at the first `OperationalError`" also catches deadlocks and serialization failures, so 8b must stop on disconnects only.
- **Operations panelist (sre-engineer): C, with the DDL bound moved into stage 1.**
  - With no staging environment, each deploy should carry one kind of mechanism. Stage 1 fires only when the path to PostgreSQL is dead.
  - The likeliest freeze of every shard is the operator's own deploy: db-init's lock queued behind an export or an open CLI prompt.
  - Stage 1 covers what the #38 watchdog can neither see nor cure.
- **Architecture panelist (architect-reviewer): C′, staging by mechanism.**
  - Branch 1 adds a global 30 s `lock_timeout` and server-side keepalives in `options`.
  - The per-service idle timeout comes only if evidence asks for it.
  - `lock_timeout` is static, so it needs none of the per-process machinery. A false `lock_timeout` is loud and retried.
  - One fork-owned module keeps the boundaries clean.

**Decided:** C, with stage 1 as in this document. All three agreed that the DDL bound belongs in stage 1. The remaining points were settled as follows:

- **`lock_timeout` and server-side settings move to stage 2** (two of three).
  - The database panelist showed that the margin is thin and that `options` in code disables `PGOPTIONS`. The operations panelist argued for one mechanism class per deploy.
  - So stage 2 carries everything that goes through `options`, and handles `PGOPTIONS` once: `lock_timeout`, the idle timeout, the server-side `tcp_keepalives_*` and `client_connection_check_interval`.
- **8b stops on disconnects only** (database and operations). Architecture's broader `OperationalError` rule is set aside, because it would drop a whole batch on one deadlock.
- **`fallback_application_name` per program is in stage 1.** Operations listed it as "should", and the database panelist wanted it for stage 2's data. It is a libpq keyword, not an `options` switch.

## Design

### 1. Connection defaults: a fork-owned module

`cms/db/connect_options.py` (new) owns the libpq keywords that every CMS connection gets, and the disconnect rule (section 4).

- **Imports.** It imports only `sqlalchemy.engine.url`, `sqlalchemy.exc` and the standard library. It never imports the `cms.db` package, which imports it.
- **Defaults:**

  | keyword | value | why |
  |---|---|---|
  | `connect_timeout` | `10` | Seconds. Blocking libpq honours it, and section 2 makes green psycopg2 honour it too. |
  | `keepalives` | `1` | Already libpq's default; kept explicit. |
  | `keepalives_idle` | `30` | Seconds. |
  | `keepalives_interval` | `10` | Seconds. |
  | `keepalives_count` | `3` | |
  | `tcp_user_timeout` | `60000` | **Milliseconds** (60 s). Honoured by the system libpq of both images (16.x on noble, 15.x on bookworm) and by psycopg 3's bundled libpq 18. |
  | `fallback_application_name` | `PROGRAM_NAME`, see below | Names each holder in `pg_stat_activity` and in the PostgreSQL log. A user's `application_name` in the URL, or `PGAPPNAME`, still wins, so this cannot override them. |

- **`PROGRAM_NAME`** is computed once, at import:
  - the basename of `sys.argv[0]`;
  - followed by a space and `sys.argv[1]` when that argument is a decimal integer, which is the shard number;
  - for example `cmsContestWebServer 2` or `cmsWorker 5`.

  It is not cut: the server clips it to its own limit. Tests read the expected value from `connect_options.PROGRAM_NAME`.
- **`with_connection_defaults(url: URL) -> URL`** returns `url` with the defaults merged **under** its query, as `{**defaults, **url.query}`.
  - The user wins key by key.
  - The rest of the query passes through untouched, for example `host=/socket/dir` and `sslmode`.
  - It works on URL objects and never renders the URL with `str()`, which would mask the password.
- **No `options` in this stage.** The function never adds `options`, so a user's `options` and `PGOPTIONS` behave exactly as today.
- **No new configuration fields.**
  - Every transport value can be overridden or turned off with the URL query. In Docker, the URL is the only knob that reaches the processes.
  - Section 6 gives the kill-switch suffix.
  - `config/cms.sample.toml` gets a comment next to `url`: the defaults, that the query overrides them, and that `tcp_user_timeout` is in milliseconds. This helps installs without Docker.

**Application points**, one line each, all through `with_connection_defaults`:

| Path | Change |
|---|---|
| Sync engine | `cms/db/__init__.py:90` |
| Async engine | `cms/db/async_session.py:87`, wrapped at the call site so that `_async_database_url()` stays pure |
| `custom_psycopg2_connection` | `cms/db/session.py`. Its existing `args.update(database_url.query)` then carries the keys to LargeObject, `drop_db` and the updaters. |

`connect_args` is not used, for two reasons. SQLAlchemy lets `connect_args` override the URL query, which would invert "the user wins". And `connect_args` never reaches `custom_psycopg2_connection`.

### 2. Connect deadline for green psycopg2

The deadline goes in `cms/io/PsycoGevent.py:gevent_wait_callback`.

- **When it applies.** The callback reads `conn.status` **once, on entry, before its first `poll()`**.
  - If the status is `extensions.STATUS_SETUP`, the whole call is a connect, and every wait in it shares one deadline.
  - The status is never checked again inside the loop: after the first `poll()` it changes (to 20, CONNECTING).
- **The value.** The connection's own `connect_timeout`, read with `extensions.parse_dsn(conn.dsn)`.
  - 0, or no value, means no deadline.
  - A positive value under 2 s becomes 2 s, as libpq does.
  - So the URL override applies here too, and `cms/io` needs no import from `cms/db`.
- **How it works.** The remaining time is passed to `wait_read` and `wait_write`. When it runs out, the callback raises `psycopg2.OperationalError("timeout expired")`. That is the same message as blocking libpq, so `test_db_connection` and log searches treat both modes alike.
- **Never on queries.** A client-side cut does not cancel the server's work.
- **Vendored file.** `PsycoGevent.py` is a vendored MIT file. A comment notes that the fork now changes it.
- **Limits, stated in the code:**
  - With a multi-host URL, the deadline covers the whole connect, not each host.
  - A DNS lookup that blocks before the socket exists is not covered.

### 3. Fork DDL updaters: 5 s lock bound

The updaters are `cmscontrib/updaters/fork_multi_contest.py` and `fork_activity_intervals.py`.

- **Each one runs its own `cursor.execute("SET LOCAL lock_timeout = '5s'")`** in the same transaction, before its update SQL.
  - It is not part of the `FORK_*_SQL` constants, which `schema_diff_test.py:156-160` and `rankinggroup_visibility_test.py:59-94` run directly.
  - Both updaters take locks even when there is nothing to change. `fork_multi_contest` takes ACCESS EXCLUSIVE on `contests`. `fork_activity_intervals` takes a SHARE lock on `activity_intervals` through its `CREATE INDEX IF NOT EXISTS`, and that conflicts with the activity recorder's writes.
- **When a lock cannot be had in time** (SQLSTATE 55P03), the updater's transaction rolls back and nothing is changed.
- **`cmsSetupDB` catches the error and exits with status 1, without a traceback.** Today `scripts/cmsSetupDB` catches only `ConfigError`. It prints exactly:

  ```
  cmsSetupDB could not lock table <table> within 5 s: another session is using it. Nothing was changed. Find that session with the holder query in the contest-day runbook ("When the database misbehaves"), wait for it to finish or end it, then run the setup again.
  ```

  `<table>` is the table named in the error, or "a table" if the error names none.
- **No retry loop.** Running the setup again is safe.
- **Deployment-neutral text.** The text does not claim that the running version keeps serving: `cmsSetupDB` also runs outside Docker, and how Compose orders `db-init` is verified separately (Testing, V1).

### 4. Error classification

`is_disconnect(error: BaseException) -> bool`, in `cms/db/connect_options.py`, returns true when the error says that the connection is gone:

```python
isinstance(error, DBAPIError) and (
    error.connection_invalidated
    or (sqlstate(error.orig) or "").startswith("08"))
```

- `sqlstate(orig)` reads `orig.pgcode` (psycopg2) or `orig.sqlstate` (psycopg 3).
- Class 08 is "connection exception".
- For errors raised through a SQLAlchemy Connection, SQLAlchemy has already stored its own disconnect detection in `connection_invalidated`. That flag covers driver errors without a SQLSTATE, such as "server closed the connection unexpectedly", and psycopg 3's `InternalError` on an idle kill (8g).
- Code that reacts to a disconnect uses this rule, never an exception class. Deadlocks (40P01), serialization failures (40001) and lock timeouts (55P03) are `OperationalError` too, and they are **not** disconnects.

### 5. Fixes exposed by a disconnect

#### 8c: session exit

`SessionGen.__exit__` and `AsyncSessionGen.__aexit__` follow the same rule:

- `rollback()` runs inside `try`, and `close()` runs in `finally`.
- **The body succeeded, and rollback raises a disconnect:**
  - log a WARNING: "Lost the database connection before the session's rollback: <error>";
  - do not raise. The server has already discarded the transaction.
- **The body succeeded, and rollback raises anything else:** propagate that error, as today.
- **The body raised:**
  - a rollback error is logged at DEBUG and swallowed;
  - the body's own exception propagates.

#### 8b: ES write path

- **In `_write_results_sync`:**
  - the whole `with SessionGen()` block is wrapped;
  - when `is_disconnect(error)` holds, ES logs one ERROR and returns:

    ```
    Lost the database connection while writing worker results (<error>); the sweeper will redo them within about 2 minutes.
    ```

  This also covers the most common case, a disconnect outside the per-row loop: the first query, the `Submission` lookup, either commit, and phases 2 and 3. Because ES returns, `FlushingDict.flush` logs nothing more. The dropped results are re-enqueued by the sweeper, as today.
- **In `write_results_one_object_and_type`:**
  - a disconnect, or `PendingRollbackError`, is re-raised before the per-row `except Exception`, so the loop stops at the first one. Once the transaction is unusable, no later row can succeed.
  - Every other error keeps today's per-row handling: IntegrityError, a lock or a deadlock, a bad row.

#### 8a: ES phase-2 window

- **Selection.** The plain (ungated) branch of `_missing_operations_sync` also selects the results that:
  - are in the contest's datasets to judge;
  - match `FILTER_SUBMISSION_RESULTS_TO_EVALUATE`, the same filters as `get_submission_results_to_evaluate`;
  - have a NULL `evaluation_outcome`;
  - and have as many Evaluation rows for their (submission, dataset) as the dataset has testcases.
- **Finalization.** Lines `EvaluationService.py:488-492` move into one helper, `_finalize_complete_result(submission_result)`:
  - `set_evaluation_outcome()`, commit, `evaluation_ended`;
  - both `submission_enqueue_operations` and the new sweep call it;
  - for the healed case, the sweep logs a WARNING: "Finalized result <submission>(<dataset>): all its evaluations were stored but its outcome was not (a write was cut).".
- **No merge of the two commits** in `write_results`. The two-phase and dependency code between them reads the committed rows, and the sweeper fix also covers an ES crash between the commits.
- **Cost.**
  - The candidate set is only results without an outcome.
  - `post_finish_lock` keeps the sweep from seeing the normal gap between the two commits.
  - The plan's cost is checked with `EXPLAIN ANALYZE` on the database of a `full` load-test run.

### 6. Operator documentation

All of this goes in `docs/contest-day.md` and its Spanish `.po`. In the `.po`, quoted log strings and code spans stay verbatim, because `check_translations.py` checks them.

**A new section, "When the database misbehaves", after "During the contest":**

- **Outage, the shared entry:**
  1. From inside the `cms` container, run `pg_isready -t 3 -d "<url>"`, with the URL from `.env` stripped of its `+psycopg2` driver suffix, as `contest.sh` does. Without `-d`, it checks the local socket instead.
  2. Then check PostgreSQL itself: its status, its log and the disk.
  3. Do not restart CMS while PostgreSQL does not answer, and do not edit `.env` because of a "connection string" error.
  4. Once PostgreSQL answers, CMS recovers by itself, on the Goal 2 timings. Then run `./status.sh`.
  5. **If grading or scoring has not resumed 3 minutes after PostgreSQL answers:** run the holder query, and end any `idle in transaction` backend whose `xact_start` is older than the outage. This is the symmetric-partition case.
  6. Do not press Regenerate while database errors continue, because a failed regenerate can leave a scoreboard empty (8e). Say this here, and also next to the existing Regenerate advice (`contest-day.md:116` and `:194`).
- **Stalled PostgreSQL:** pages and grading are frozen, new connections fail with `timeout expired`, and `pg_isready` reports "no response". PostgreSQL itself is stalled, usually on its disk. No CMS timeout can cut this, and restarting CMS will not help.
- **The holder query.**
  - It is one copy-paste command, run on the host with `psql`, or with the container variant as `contest.sh` does.
  - It lists the open transactions with these columns:
    - `pid`, `application_name`, `state` and `wait_event_type`;
    - `now() - xact_start`;
    - `pg_blocking_pids(pid)`;
    - the first 60 characters of the query.
  - To end a holder, run `SELECT pg_terminate_backend(<pid>);`. The `cms` role can end its own backends; ending another role's backend needs a superuser.
- **Where the errors show:** the driver's error text appears in `./logs.sh`, not in the Overview Logs table, which shows only the message.

**Rows in the "When something goes wrong" table:**
- the connect deadline (`timeout expired`);
- a lost connection (`server closed the connection unexpectedly`, `could not receive data from server`, `Connection timed out`, and ES's "Lost the database connection while writing worker results");
- the `cmsSetupDB` lock message.

**The kill switch.** This URL suffix brings back today's transport behaviour:

```
?connect_timeout=0&keepalives_idle=7200&keepalives_interval=75&keepalives_count=9&tcp_user_timeout=0
```

- Use `&` to merge it into a URL that already has a query.
- Changing the URL restarts every service.
- It is meant for errors under normal load with a healthy database.
- It does not switch off the DDL bound.

**Under "Do not rebuild or update the deployment on the day":**
- an emergency deploy may now fail at `db-init` with the lock message, and that failure is safe;
- do not deploy while `./export.sh` or a CLI tool is running;
- start any SQL you apply by hand with `SET lock_timeout = '5s';`.

**After a deploy of this stage:**
- one command, run inside the running `cms` container, prints `getsockopt(TCP_USER_TIMEOUT)` and `SHOW application_name` for a fresh connection;
- then search `./logs.sh` for `timeout expired|closed the connection|Lost the database`. It must find nothing.

## Testing

### Unit and integration tests

These run in the CI suite inside the production image, on ubuntu:noble and debian:bookworm, in the right group of the gevent/asyncio split. Tests that need a TCP database (`getsockopt`) skip when `CMS_CONFIG` points to a Unix socket.

**Asyncio group (`db/` and `io/` tests, no `cmscontrib` import):**

- **`with_connection_defaults`**, table-driven:
  - the defaults are present;
  - a user keyword wins;
  - `tcp_user_timeout` is 60000;
  - a Unix-socket host passes through;
  - a password with special characters survives;
  - `options` is never added, and a user's `options` is kept as it is;
  - a user's `application_name` makes the fallback irrelevant.
- **`PROGRAM_NAME`:** `["/x/cmsContestWebServer", "2"]` gives `cmsContestWebServer 2`, and `["/x/cmsDumpExporter", "-h"]` gives `cmsDumpExporter`.
- **`custom_psycopg2_connection`:** the existing `session_test.py` expectations gain the defaults.
- **The three connection paths, on the test PostgreSQL:**
  - `getsockopt(TCP_USER_TIMEOUT) == 60000`;
  - the keepalive socket options;
  - `SHOW application_name` equals `PROGRAM_NAME`.
- **The kill switch, end to end.** The documented suffix is applied to the test URL, then:
  - on all three paths, `TCP_USER_TIMEOUT` is 0 and the keepalives are 7200/75/9;
  - a green connect gets no deadline.
- **Connect deadline**, against a local listener that accepts and never answers:
  - a green connect with `connect_timeout=2` raises "timeout expired" within [2, 3.5) s;
  - `connect_timeout=1` gives a deadline of about 2 s;
  - `connect_timeout=0` does not raise within 3.5 s;
  - after 5 timed-out connects, the process's open file-descriptor count is unchanged.

  A green `select pg_sleep(3)` on the test PostgreSQL, under `connect_timeout=2`, is not cut.
- **`is_disconnect`:**
  - true for an invalidated error, for class 08, and for psycopg 3's `InternalError` on an idle kill;
  - false for a deadlock (40P01), a serialization failure (40001), a lock timeout (55P03) and a plain IntegrityError.
- **`SessionGen` exit.** The body runs a query first, so that a transaction is open. Then:
  - after `pg_terminate_backend` of the session's backend, a body that succeeded does not raise, and `close()` runs;
  - a non-disconnect rollback error still propagates;
  - when the body raised, the body's error propagates.
- **`AsyncSessionGen` exit:** the same three cases.
- **8b:**
  - a disconnect on row 1 stops the loop with one ERROR (the fixed text), and the later rows are not attempted;
  - a disconnect at the first query of `_write_results_sync` logs the same single ERROR, and `FlushingDict.flush` logs nothing;
  - an IntegrityError or a ValueError still goes row by row.
- **8a:**
  - a result with every evaluation and a NULL outcome, in a contest without a gated dataset, is finalized by one plain sweep, with the WARNING;
  - a result with a missing evaluation is not finalized.

  This unit test is the real gate for 8a: the window is milliseconds wide, so F1 rarely creates that state.

**Gevent group (`cmscontrib/SetupDBTest.py`):**
- **DDL:** a second connection holds a transaction that has read `contests`. The multi-contest updater then fails with 55P03 in about 5 s, and `cmsSetupDB` exits 1 with the exact message.
- The same check for `activity_intervals` with the activity-intervals updater.

### Validation runs

**V1, Compose ordering (local rootful Docker).**
- Deploy `docker/docker-compose.prod.yml` with `up -d --wait` while `docker compose events` records the order.
- Record whether the old `cms` container still serves while `db-init` runs.
- Write the result into the runbook's deploy notes.

**Fault runs** use a new fault mode of the GitHub Actions load test:
- **Setup:**
  - smoke-sized load, fork only (no upstream pairing);
  - one run per profile (`portable` and `full`), through a matrix or two pushes.
- **Harness:** `run.sh` gets a background injector that writes each fault's start and end timestamps to `out/<name>/faults.json`, for a windowed analysis.
- **Injection:**
  - the PostgreSQL container's network namespace is reached with `docker inspect` (its PID) and `sudo nsenter -n`;
  - the main method is iptables DROP rules on INPUT and OUTPUT, which give a two-way drop and need only netfilter;
  - plus `docker pause`, and `compose exec db psql` for SQL faults.
- **Victims:** found by `application_name LIKE 'cms%'`, so that the harness monitor's own connection is spared.

| # | Fault | Pass criteria |
|---|---|---|
| F1 | `pg_terminate_backend` of every CMS backend, twice in the middle of the run. | No restart is needed, and nothing is stuck after the drain. The logs show "Lost the database connection" or the driver's disconnect text. |
| F2 | Two-way DROP between the CMS container and PostgreSQL, for 90 s. | Each database call fails within 75 s of its own start. Pages answer within 1 minute of the fault's end. ES commits again within about 2 minutes, and scores arrive within about 6 minutes, with no restart. Nothing is stuck after the drain. Backends left on the server after the fault are recorded: this is the Goal 2 caveat. |
| F3 | `docker pause` of PostgreSQL for 30 s: the server's kernel is alive, but PostgreSQL is stalled. | In-flight queries wait for the resume. New connections fail with `timeout expired` at about 10 s. After the resume, everything recovers with no restart and no damage. |
| F4 | `cmsSetupDB` while a `psql` session holds `BEGIN; SELECT 1 FROM contests LIMIT 1;` idle. | `cmsSetupDB` exits 1 within 15 s with the message. CWS p99 is ≤ 6 s in the window from the start of `cmsSetupDB` to 5 s after it exits. |

**Before the fault mode exists,** a 5-minute test job checks that the runner can run nsenter and iptables, and whether `sch_netem` is loaded.

**No-fault regression runs:**
- **Setup:** the contest-shaped `full` profile, 4 repeats, compared per CPU model with a beta baseline run **in the same batch**.
- **Pass:**
  - zero transport errors in the CMS logs (`timeout expired`, `timed out`, `closed the connection`);
  - every submission ends in a final state;
  - scored p95, CWS p99, flush p95 and drain time within the run-to-run spread.

## Out of Scope

- **Stage 2:** a separate design, made after this stage has run in a contest. It covers:
  - `lock_timeout`, with values from data;
  - the idle timeout for services only, through the per-process profile;
  - the server-side `tcp_keepalives_*` and `client_connection_check_interval`, which would bound the Goal 2 caveat from the client side;
  - the `options` and `PGOPTIONS` merge;
  - the lock-error policy for 8b (skip one object, keep the batch);
  - 8g as a general rule beyond `is_disconnect`.
- **Follow-up issues for the other exposed paths:**
  - (8d) ProxyService turns transient errors into "broken" contests;
  - (8e) `regenerate_ranking` sends its RESET first;
  - (8f) AWS `try_commit` shows a generic 500 on a connection error;
  - (8h) LargeObject connections are not closed.
- **A stalled PostgreSQL whose kernel is alive.** No client timeout can cut it. Detection belongs to the #38 watchdog, designed after this stage, with a shared timeout ladder.
- **`statement_timeout` and `pool_pre_ping`:** not set. A COMMIT that waits on fsync cannot be cancelled, and pre-ping costs time on the CWS event loop for little gain.

## Open Points for the Operator (not blocking)

- **The production PostgreSQL version.** It matters for stage 2's GUCs: an unknown GUC in `options` is FATAL on every connection.
- **Host-side keepalives** (a production change; the user decides). Setting `tcp_keepalives_idle`, `tcp_keepalives_interval` and `tcp_keepalives_count` in the host's `postgresql.conf` needs only a reload. It would make the server drop a backend whose client is gone within about a minute, and that closes the Goal 2 caveat for the symmetric-partition case.
- **`log_lock_waits` on the host PostgreSQL** (a production change; the user decides). It would give stage 2 real lock-wait data from a contest.
