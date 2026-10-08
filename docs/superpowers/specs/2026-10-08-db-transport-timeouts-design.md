# Database Transport Timeouts (#39, stage 1) — Design Doc

**Date:** 2026-10-08
**Issue:** #39, stage 1 of 2 (wave 1 of `2026-10-06-post-10-10-roadmap-design.md`)
**Status:** Designed. The approach was chosen by a panel of three specialist subagents, as the user asked for backlog issues on 2026-10-07 (see Decision Record). Target: `beta`, after the 2026-10-10 contest.

## Problem

No CMS database connection has a timeout. `cms/db/__init__.py:90` and `cms/db/async_session.py:87` set only `pool_timeout` and `pool_recycle`.

- **What hangs today.** A dead or half-open TCP connection to PostgreSQL blocks the caller until the kernel gives up, about 15 minutes. So does a connect that the server never answers, and a lock wait.
  - In EvaluationService (ES) the call runs under `post_finish_lock`, so grading stops while the service still answers `echo`.
  - In ContestWebServer (CWS) every handler runs its queries synchronously on the event loop, so one stuck query freezes a whole shard (about a quarter of the contestants).
- **`connect_timeout` would not help as is.** Two independent investigations measured this in the CMS image (`db39-*-findings.md`, summarised below). Every CMS process makes psycopg2 gevent-green at import (`cms/io/__init__.py:50`), and green psycopg2 never applies libpq's `connect_timeout`: a connect with `connect_timeout=2` to a listener that never answers still hung after 25 s.
- **Turning hangs into errors exposes code that handles errors badly.** Three such paths matter for a disconnect:
  - **(8a) ES phase-2 window.** If the commit at `EvaluationService.py:1065` succeeds and the one at `:1117` fails, every evaluation exists but the outcome stays NULL. In a contest with no gated dataset (two-phase or dependencies), the plain sweeper never finalizes such a result, and the submission stays "Evaluating" for good. Production runs two-phase today, and the gated sweeper does finalize these (`EvaluationService.py:486-490`), but any other contest is silently affected. A real disconnect or an ES crash between the commits can already cause it today.
  - **(8b) ES per-row catch-all.** `write_results_one_object_and_type` (`:1171`) catches every exception per row (`:1186-1203`). After a disconnect, each later row of the batch fails on its own, under `post_finish_lock`, and the Overview Logs table fills with one ERROR per row.
  - **(8c) `SessionGen.__exit__`** (`cms/db/session.py:71-73`) calls `rollback()` then `close()` with no `try/finally`. On a dead connection, `rollback()` raises, so a block whose work had succeeded still raises on exit, and `close()` is skipped.

## Goal

1. **Bounded detection.** A dead or half-open connection makes the call fail within about 70 s instead of hanging. A connect that the server never answers fails within about 10 s. This holds on every connection path: the sync engine, the async engine (activity recorder) and `custom_psycopg2_connection` (LargeObject, `drop_db`, the fork updaters).
2. **Self-healing.** After such a failure, every service recovers by itself once PostgreSQL answers, with no restart: pages within a minute, grading within about 2 minutes (the ES sweeper period, 117 s), and scores within about 6 minutes (347 s). No submission is left "Evaluating".
3. **Bounded deploy locks.** A deploy's schema update (`cmsSetupDB`, the prod `db-init`) never queues CMS behind its exclusive lock on `contests` for more than 5 s. If the update cannot get the lock, it fails visibly, and the running version keeps serving.
4. **No change on a healthy database.** Apart from the deploy's DDL bound (Goal 3), nothing in this stage fires against a healthy PostgreSQL, however busy or slow: the server's kernel answers keepalives even while PostgreSQL is saturated, and the connect deadline only applies while connecting. Queries, lock waits and long transactions of the running services behave exactly as today.
5. **Operators can tell what happened.** Each failure has a log line that names it, and the runbook says what to do. It also says how to switch every new timeout off with the database URL alone.

## Decision Record

**Approaches put to the panel** (full text in the session's `db39-approaches.md`):
- **A, transport only:** client-side TCP keepalives, `tcp_user_timeout`, a connect deadline, and the fixes a disconnect exposes.
- **B, full profile in one branch:** A plus a 30 s `lock_timeout` for all processes and a 120 s `idle_in_transaction_session_timeout` for services only, chosen per process by a `do_connect` hook, plus the fixes those expose.
- **C, staged:** A first, and the session timeouts second, with values taken from data.

**Verdicts:**

| Panelist (agent) | Choice | Key arguments |
|---|---|---|
| Database (database-administrator) | C. The DDL bound moves into stage 1. **No `options` in stage 1.** | Nothing in stage 1 can fire on a healthy database, so it needs fault tests, not load tuning. The 30 s `lock_timeout` margin rests on one saturated run (1.2x on the only retrying path), and 30 s does not unfreeze a CWS shard while the holder stays. Setting `options` in code silently disables `PGOPTIONS`. "Stop at the first `OperationalError`" also catches deadlocks and serialization failures, so 8b must stop on disconnects only. |
| Operations (sre-engineer) | C. The DDL bound moves into stage 1. | With no staging environment, each deploy should carry one kind of mechanism: stage 1 fires only when the path to PostgreSQL is dead. The likeliest freeze of every shard is the operator's own deploy, when db-init's ACCESS EXCLUSIVE on `contests` queues behind an export or an open CLI prompt. Stage 1 covers what the #38 watchdog can neither see nor cure. |
| Architecture (architect-reviewer) | C′: stage by mechanism. Branch 1 adds a global 30 s `lock_timeout` and server-side keepalives in `options`. The per-service idle timeout comes only if evidence asks. | `lock_timeout` is static, so it needs none of the per-process machinery. A false `lock_timeout` is loud and retried. One fork-owned module keeps the boundaries clean. |

**Decided:** C, stage 1 as in this document. The DDL bound is in it (all three).
- **`lock_timeout` and server-side settings move to stage 2** (two of three). The database panelist showed that the 30 s margin is thin, and that any `options` set in code disables `PGOPTIONS`. The operations panelist argued for one mechanism class per deploy. Stage 2 therefore carries everything that goes through `options`, and handles `PGOPTIONS` once:
  - `lock_timeout`;
  - the idle timeout;
  - the server-side `tcp_keepalives_*`;
  - `client_connection_check_interval`.
- **8b stops on disconnects only** (database and operations). Architecture's broader `OperationalError` rule is set aside: it would drop a whole batch on one deadlock.
- **`application_name` per program is in stage 1.** Operations listed it as "should", and the database panelist wanted it for stage 2's data. It is a libpq keyword, not an `options` switch, so it adds no new mechanism.

## Design

### 1. Connection defaults: a fork-owned module

`cms/db/connect_options.py` (new) owns the libpq keywords that every CMS connection gets. It imports only `sqlalchemy.engine.url` and the standard library, never the `cms.db` package, which imports it.

- **Defaults:**

  | keyword | value | why |
  |---|---|---|
  | `connect_timeout` | `10` | Seconds. Blocking libpq honours it, and section 2 makes green psycopg2 honour it. |
  | `keepalives` | `1` | Already libpq's default; kept explicit. |
  | `keepalives_idle` | `30` | Seconds. |
  | `keepalives_interval` | `10` | Seconds. |
  | `keepalives_count` | `3` | |
  | `tcp_user_timeout` | `60000` | **Milliseconds** (60 s). Honoured by the image's system libpq 16.15 and by psycopg 3's bundled libpq 18. |
  | `application_name` | the program name: the basename of `sys.argv[0]`, cut to 63 bytes | Names each holder in `pg_stat_activity` and in the PostgreSQL log. |

- **`with_connection_defaults(url: URL) -> URL`** returns `url` with the defaults merged **under** its query (`{**defaults, **url.query}`). The user wins key by key. The rest of the query passes through untouched, including `host=/socket/dir` and `sslmode`. The function works on URL objects and never renders the URL with `str()`, which would mask the password.
- **No `options` in this stage.** The function never adds `options`, so a user's `options` and `PGOPTIONS` behave exactly as today.
- **No new configuration fields.** Every value can be overridden or turned off with the URL query, which in Docker is the only knob that reaches the processes. Section 6 gives the kill-switch suffix.

**Application points**, one line each, all through `with_connection_defaults`:
- the sync engine (`cms/db/__init__.py:90`);
- the async engine (`cms/db/async_session.py:87`), wrapped at the call site so `_async_database_url()` stays pure;
- `custom_psycopg2_connection` (`cms/db/session.py`), whose existing `args.update(database_url.query)` then carries the keys to LargeObject, `drop_db` and the updaters.

`connect_args` is not used: SQLAlchemy lets `connect_args` override the URL query, which would invert "the user wins", and it never reaches `custom_psycopg2_connection`.

### 2. Connect deadline for green psycopg2

In `cms/io/PsycoGevent.py:gevent_wait_callback`:

- **When it applies.** Only while `conn.status == extensions.STATUS_SETUP`, that is, while connecting.
- **Where the value comes from.** The connection's own `connect_timeout`, read with `extensions.parse_dsn(conn.dsn)`. A value of 0, or no value, means no deadline. A positive value under 2 s is raised to 2 s, as libpq does. So the URL override applies here too, and `cms/io` needs no import from `cms/db`.
- **How it works.** The remaining time is passed to `wait_read` and `wait_write`. When it runs out, the callback raises `psycopg2.OperationalError("timeout expired")`, the same message as blocking libpq. `test_db_connection` and log searches then treat both modes alike.
- **Never on queries.** A client-side cut does not cancel the server's work, so query waits never get a deadline.
- **Vendored file.** `PsycoGevent.py` is a vendored MIT file. A comment notes that the fork now changes it.
- **Limits, documented in the code:** with a multi-host URL the deadline covers the whole connect, not each host. A DNS lookup that blocks before the socket exists is not covered.

### 3. Fork DDL updaters: 5 s lock bound

`cmscontrib/updaters/fork_multi_contest.py` and `fork_activity_intervals.py` run, in the same transaction and before their update SQL:

```sql
SET LOCAL lock_timeout = '5s'
```

They already take an ACCESS EXCLUSIVE lock on `contests`, even when there is nothing to change. When the lock cannot be had in time (SQLSTATE 55P03):
- the updater fails;
- `cmsSetupDB` exits non-zero with one operator message: that it could not lock the table within 5 s, that nothing was changed, and that the running version keeps serving. It also names the holder query in the runbook and says to run `./up.sh` again.

There is no retry loop: rerunning the deploy is safe. `init_db()` is untouched in this stage; stage 2's global `lock_timeout` bounds it.

### 4. Error classification

One helper, `is_disconnect(error) -> bool`, in `cms/db/connect_options.py` or next to `SessionGen`, says whether a SQLAlchemy `DBAPIError` means that the connection is gone. It is true when:
- `error.connection_invalidated` is set;
- or the SQLSTATE is in class 08 (connection exception);
- or it is SQLAlchemy's disconnect detection on a driver error that has no SQLSTATE.

Code that reacts to a disconnect uses this helper, not an exception class. psycopg 3 reports some kills as `InternalError` (8g), and deadlocks and serialization failures are `OperationalError` too.

### 5. Fixes exposed by a disconnect

- **8c, `SessionGen.__exit__`.**
  - Rollback in `try`, `close()` in `finally`.
  - **Body succeeded, rollback raises a disconnect:** log a WARNING ("Lost the database connection before the session's rollback: …") and do not raise. The server has already discarded the transaction, so the implicit rollback happened.
  - **Body succeeded, rollback raises something else:** propagate it, as today.
  - **Body raised:** the body's exception propagates in every case.
  - `AsyncSessionGen` gets the same rule if it lacks it.
- **8b, ES per-row catch-all.** In `write_results_one_object_and_type`, a disconnect (by `is_disconnect`) is re-raised before the per-row `except Exception`.
  - The batch stops at the first disconnect, and ES logs one ERROR naming it, for example "Lost the database connection while writing results (…); the sweeper will redo them within about 2 minutes".
  - `FlushingDict.flush` drops the batch, and the sweeper re-enqueues the work, as today.
  - Every other error (IntegrityError, lock, deadlock, a bad row) keeps today's per-row handling.
- **8a, ES phase-2 window.** In the plain (ungated) branch of `_missing_operations_sync`, also select the submission results that are to be evaluated, have an Evaluation row for every testcase of the dataset, and have a NULL `evaluation_outcome`. Finalize them with the existing finalization block of `submission_enqueue_operations` (`EvaluationService.py:487-491`), called directly or extracted as a small helper.
  - Do not merge the two commits in `write_results`: the two-phase and dependency code between them reads the committed rows, and the sweeper fix also covers an ES crash between the commits.
  - The query runs once per sweep (117 s) as one aggregated join. Its plan is checked on a contest-sized database.

### 6. Operator documentation

`docs/contest-day.md` (and its Spanish `.po`):

- **A new section, "When the database misbehaves",** after "During the contest":
  - **Outage, the shared entry.** Run `pg_isready -t 3` from inside the `cms` container, then check PostgreSQL itself (status, log, disk).
    - Do not restart CMS while PostgreSQL does not answer, and do not edit `.env` because of a "connection string" error.
    - Once PostgreSQL answers, CMS recovers by itself on the Goal 2 timings. Then run `./status.sh`.
    - Do not press Regenerate while database errors continue: a failed regenerate can leave a scoreboard empty (8e).
  - **Everything frozen, no errors, and `pg_isready` hangs.** PostgreSQL itself is stalled, usually on its disk. No CMS timeout can cut this, and restarting CMS will not help.
  - **The holder query**, one copy-paste command that lists open transactions with `application_name`, `now() - xact_start`, `pg_blocking_pids(pid)` and the first characters of the query. Also how to end a holder with `pg_terminate_backend(<pid>)`.
- **Rows for the "When something goes wrong" table**, one for each:
  - the connect deadline (`timeout expired`);
  - a lost connection (`server closed the connection unexpectedly`, `could not receive data from server`, `Connection timed out`);
  - the db-init lock message.
- **The kill switch.** This URL suffix brings back today's transport behaviour. It restarts every service, and it is meant for errors under normal load with a healthy database:

  ```
  ?connect_timeout=0&keepalives_idle=7200&keepalives_interval=75&keepalives_count=9&tcp_user_timeout=0
  ```

  Merge it with `&` if the URL already has a query.
- **Under "Do not rebuild or update the deployment on the day":**
  - an emergency deploy may now fail at db-init with the lock message, and that failure is safe;
  - do not deploy while `./export.sh` or a CLI tool is running;
  - SQL applied by hand starts with `SET lock_timeout = '5s';`.

## Testing

**Unit and integration tests**, run in the CI suite inside the production image on ubuntu:noble and debian:bookworm, in the right gevent or asyncio group:
- **`with_connection_defaults`** (table-driven):
  - the defaults are present;
  - a user keyword wins;
  - `tcp_user_timeout` is 60000;
  - a Unix-socket host passes through;
  - a password with special characters survives;
  - `options` is never added, and a user's `options` is kept as is.
- **`custom_psycopg2_connection`:** the existing `session_test.py` expectations gain the defaults.
- **The three connection paths, on the test PostgreSQL:**
  - `getsockopt(TCP_USER_TIMEOUT) == 60000`;
  - the keepalive socket options;
  - `SHOW application_name`.
- **Connect deadline:**
  - a local listener that accepts and never answers makes a green connect with `connect_timeout=2` raise "timeout expired" within [2, 3.5) s;
  - with `connect_timeout=0` it does not raise within that time;
  - a green `select pg_sleep(3)` on the test PostgreSQL is not cut.
- **`SessionGen` exit:**
  - after a body that succeeded, a `pg_terminate_backend` of the session's backend does not raise, and `close()` runs;
  - a non-disconnect error still propagates.
- **`is_disconnect`:** an invalidated error, class 08, an idle kill (psycopg 3 `InternalError`), a deadlock (40P01, false), and a lock timeout (55P03, false).
- **8b:** a disconnect on row 1 stops the batch with one ERROR, and the later rows are not attempted. An IntegrityError or a ValueError still goes row by row.
- **8a:** a result with every evaluation and a NULL outcome, in a contest without a gated dataset, is finalized by one plain sweep.
- **DDL:** while a second connection holds a transaction that read `contests`, the updater fails with 55P03 in about 5 s, and `cmsSetupDB` exits non-zero with the operator message.

**Fault runs** on the GitHub Actions load test: a new fault mode of `loadtest.yml`, with smoke-sized load. The runner has sudo, so faults are injected into the PostgreSQL container from the runner.

| # | Fault | Pass |
|---|---|---|
| F1 | `pg_terminate_backend` of every CMS backend, twice in the middle of the run, in the `portable` and `full` profiles. The portable profile has no gated dataset, so it exercises 8a's plain sweeper. | No restart is needed. Nothing is stuck after the drain. The logs show the named line. |
| F2 | `tc netem loss 100%` on the PostgreSQL container's interface for 90 s, a true half-open | Every process gets its error within 75 s of the fault start. Everything resumes within 2 minutes of the fault end, without a restart. Nothing is stuck after the drain. |
| F3 | `docker pause` of PostgreSQL for 30 s, a stalled server whose kernel stays alive | Expected: CMS waits until the resume, then recovers with no damage. This documents the limit the runbook states. |
| F4 | `cmsSetupDB` while a `psql` session holds `BEGIN; SELECT 1 FROM contests LIMIT 1;` idle | db-init exits non-zero within 15 s with the message, and CWS keeps answering (p99 ≤ 6 s in the window). |

**No-fault regression runs:** the contest-shaped `full` profile, 4 repeats, compared per CPU model with the beta baseline. Pass:
- zero transport errors in the CMS logs (`timeout expired`, `timed out`, `closed the connection`);
- every submission ends in a final state;
- scored p95, CWS p99, flush p95 and drain time within the run-to-run spread.

## Out of Scope

- **Stage 2,** a separate design after this stage has run in a contest. It covers:
  - `lock_timeout`, with values from data;
  - the idle timeout for services only, through the per-process profile;
  - the server-side `tcp_keepalives_*` and `client_connection_check_interval`;
  - the `options` and `PGOPTIONS` merge;
  - the lock-error policy for 8b (skip one object, keep the batch);
  - 8g as a general rule.
- **Follow-up issues** for the other exposed paths:
  - (8d) ProxyService turns transient errors into "broken" contests;
  - (8e) `regenerate_ranking` sends its RESET first;
  - (8f) AWS `try_commit` shows a generic 500 on a connection error;
  - (8h) LargeObject connections are not closed.
- **A stalled PostgreSQL whose kernel is alive.** No client timeout can cut it. Detection belongs to the #38 watchdog, designed after this stage, with a shared timeout ladder.
- **`statement_timeout` and `pool_pre_ping`:** not set. A COMMIT that waits on fsync cannot be cancelled, and pre-ping costs time on the CWS event loop for little gain.

## Open Points for the Operator (not blocking)

- **The production PostgreSQL version.** It matters for stage 2's GUCs: an unknown GUC in `options` is FATAL on every connection.
- **Whether Compose keeps the old `cms` container serving while `db-init` runs.** F4 checks this in the harness. On the server, watch `docker compose events` during a deploy.
- **Optional, a production change:** turning on `log_lock_waits` on the host PostgreSQL would give stage 2 real lock-wait data from a contest. The user decides.
