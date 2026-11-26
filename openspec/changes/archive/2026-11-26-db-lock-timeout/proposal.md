# Proposal

## Why

No CMS database connection has a bound on lock waits (#39). A transaction
that holds a row or table lock (a stuck AWS action, a hand-run SQL session, a
leaked transaction) makes every session that needs that lock wait forever: in
EvaluationService under `post_finish_lock`, grading stops while the service
looks alive; in ContestWebServer a handler waiting on the event loop freezes
the whole shard. Stage 1 of #39 (omi.8) bounded the transport; this is stage
2a: bound the lock waits themselves.

## What Changes

- Every CMS connection (the sync engine and so every service, web server and
  CLI, the async engine, `custom_psycopg2_connection`, the pooled large-object
  connections, the gevent Worker's green connections) starts with
  `lock_timeout` = 30 s, sent as the libpq startup parameter `options`.
- **BREAKING (contract):** CMS now sets `options` itself. The `PGOPTIONS`
  environment variable and the URL's own `options` are merged into it, later
  entries winning (default, then `PGOPTIONS`, then the URL), so both keep
  working and can relax or tighten the bound. Before, setting `options` was
  left to the user.
- Kill switch: `options=-c%20lock_timeout%3D0` in the database URL disables the
  bound; the runbook's "Switching the timeouts off" suffix gains it.
- A per-transaction `SET LOCAL lock_timeout` (the fork's 5 s schema-update
  bound, the AWS import apply) keeps winning over the session default.
- EvaluationService: a lock timeout (SQLSTATE 55P03) while writing a result
  row is logged as one WARNING without a traceback; the other results of that
  object in the same write are skipped (they would wait for the same lock),
  the rest of the batch is written, and the sweeper redoes the skipped ones.
- Side effect, accepted: `cmsDropDB` / `cmsInitDB` (and `drop_db`/`init_db`)
  also give up after 30 s on a lock.

Not in this change: an idle-in-transaction timeout (2b), `statement_timeout`,
`transaction_timeout` (PostgreSQL 17+), `pool_pre_ping`, new config fields,
role-level settings.

## Capabilities

### New Capabilities

- `db-lock-timeout`: how long a CMS database connection waits for a lock, how
  the operator overrides or disables that bound, and how EvaluationService
  reports a lock timeout.

### Modified Capabilities

None.

## Impact

- Code: `cms/db/connect_options.py` (the `options` string),
  `cms/service/EvaluationService.py` (the 55P03 warning).
- Tests: `cmstestsuite/unit_tests/db/connect_options_test.py` (stage-1
  assertions that `options` is never added are rewritten),
  `io/psycogevent_test.py`, `cmscontrib/SetupDBTest.py`,
  `service/evaluationservice_disconnect_test.py`.
- Docs: `docs/contest-day.md` and its Spanish catalog, `config/cms.sample.toml`.
- Operations: a connection pooler or proxy that rejects the `options` startup
  parameter would refuse every CMS connection; check the real database path
  with that parameter before deploying, and keep the kill switch at hand (see
  the Migration Plan in `design.md`).
