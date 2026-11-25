# Tasks

## 1. The lock bound on every connection

- [x] 1.1 Add the `options` string (30 s `lock_timeout`, then `PGOPTIONS`, then the URL's `options`) in `cms/db/connect_options.py`, and rewrite the stage-1 tests that assert `options` is never added; verify `connect_options_test.py` passes on PostgreSQL 15 and 18 (note: `db/session_test.py` asserted the exact connect() keywords too and was updated)
- [x] 1.2 Test `SHOW lock_timeout` on every path (sync engine, async engine, `custom_psycopg2_connection`, pooled large object, green connect), the fire test (1 s override, SQLSTATE 55P03 in about 1 s), the `PGOPTIONS` merge, the URL override and the kill switch; verify each test carries its `# Scenario:` tag and passes on PostgreSQL 15 and 18
- [x] 1.3 Assert that the fork's 5 s DDL bound still wins over the 30 s session default (`SetupDBTest.py`, gevent group); verify it passes on PostgreSQL 15 and 18

## 2. EvaluationService lock-timeout policy

- [x] 2.1 Log a 55P03 on a result row as one WARNING, skip the object's other rows, keep the batch; verify the new test in `evaluationservice_disconnect_test.py` passes and the existing 8b tests still do

## 3. Docs

- [x] 3.1 Update the kill-switch suffix and the "When the database misbehaves" text in `docs/contest-day.md`, its Spanish catalog, and the `[database]` comment of `config/cms.sample.toml`; verify `docs/check_translations.py` passes and the suffix matches `KILL_SWITCH` in the tests

## 4. Verify

- [x] 4.1 Run both pytest groups (separate processes) for `db/`, `io/`, `service/`, `cmscontrib/` on PostgreSQL 15 and 18, `pyflakes`, `openspec validate db-lock-timeout --strict`, and the Scenario-to-test check (note: on the local PostgreSQL 18 run, `schema_diff_test.py` fails only because the local `pg_dump` is 15.20; it passes on PostgreSQL 15)

## Workflow follow-up

- Review by the controller (fresh code-reviewer), then merge and archive the change in the same merge.
- Deploy in its own release, off a contest day, after the owner's questions on a pooler and the server-side ops steps.
