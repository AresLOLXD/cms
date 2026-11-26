# Design

## Context

Stage 1 of #39 put every connection's libpq settings in one place:
`cms/db/connect_options.py`, `with_connection_defaults(url)`, which adds
`LIBPQ_DEFAULTS` under the URL's own query. Every path builds its URL with it:
the sync engine (`cms/db/__init__.py`), the async engine
(`cms/db/async_session.py`), `custom_psycopg2_connection` (`cms/db/session.py`).
Large objects use `engine.raw_connection()` (pooled since omi.17), so they
inherit the sync engine's URL; the gevent Worker uses the same engine with the
green wait callback, which does not touch startup parameters. Stage 1 never
added `options`, on purpose: libpq ignores `PGOPTIONS` as soon as `options` is
given (measured on PostgreSQL 18.7 by the panel). The fork's schema updates
(`cmscontrib/updaters/fork_ddl.py`) and the AWS import apply already use
`SET LOCAL lock_timeout` per transaction. Details and measurements: the panel
note `D1-db-timeouts-stage2.md` and
`docs/superpowers/specs/2026-10-08-db-transport-timeouts-design.md`.

## Goals / Non-Goals

**Goals:**
- One `options` string, built in `with_connection_defaults`, reaching every path.
- `PGOPTIONS` and the URL's `options` keep working and can override.
- EvaluationService's per-row loop treats 55P03 as the "skip one object, keep
  the batch" policy left open by stage 1 (8b).

**Non-Goals:**
- Per-process profiles, a `do_connect` hook, an idle timeout (2b).
- A retry loop on 55P03 anywhere (CWS/AWS show their usual error; ES relies on
  the sweeper; the activity recorder already puts its chunk back).
- 55P03 outside the per-row loop of ES (the first lookups of a flush): it keeps
  propagating to `FlushingDict`, as a deadlock does.

## Decisions

- **Startup `options`, not `SET` after connect or `ALTER ROLE`.** It is one
  place in code, tested in CI, exact per connection, and costs no round trip.
  `SET` on connect needs an event hook per engine and per raw path; `ALTER
  ROLE` is hidden server state, missing in dev and CI, and hits every client of
  the role. Trade-off: a startup `options` beats role-level settings, so an
  operator relaxes it through the URL or `PGOPTIONS`, not `ALTER ROLE`.
- **Merge order: `-c lock_timeout=30000`, then `PGOPTIONS`, then the URL's
  `options`, joined by spaces.** PostgreSQL applies `-c` switches in order, so
  the last one wins; the explicit URL beats the environment, which beats the
  code default. Joining preserves `PGOPTIONS` escapes (`\ `) untouched.
  `PGOPTIONS` is read when the URL is built (import time for the two engines,
  each call for `custom_psycopg2_connection`), which is when libpq would have
  read it for the process anyway.
- **Repeated keys.** The kill switch appended to a URL that already sets one of
  its keys repeats that key, which SQLAlchemy turns into a tuple that no driver
  accepts. Repeated `options` are joined in the URL's order; a repeated key of
  the CMS defaults (the rest of the suffix) takes its last value, as in libpq.
  Other keys pass through: SQLAlchemy needs the repeated `host` and `port` of a
  multi-host URL.
- **Kill switch in the URL, appended to the stage-1 suffix.** One documented
  suffix switches off every client-side bound; `options=-c%20lock_timeout%3D0`
  becomes its last item. It needs a restart of every service, as in stage 1.
- **30000 ms, uniform.** Above every measured wait (24.4 s worst, the activity
  flush on a saturated host, which retries); the 5 s DDL bound stays by
  `SET LOCAL`, which wins over a session value.
- **ES: return after the WARNING.** The later rows of the same object would wait
  for the same lock, 30 s each, under `post_finish_lock`; returning bounds the
  stall to one wait per locked object. The savepoint around each row already
  keeps the transaction usable, so the rest of the batch is written.

## Risks / Trade-offs

- [A pooler or proxy rejects the `options` startup parameter, or an unknown GUC
  is FATAL] → every connection fails at once, loudly. `lock_timeout` exists on
  every supported major; the connection check before the deploy (see the
  Migration Plan) catches a rejected parameter while the old code still runs;
  the kill switch removes the `-c` effect but not the parameter itself, so a
  pooler that rejects `options` needs a rollback of the release.
- [A false fire under extreme load] → loud and retried: ES skips and the
  sweeper redoes, the activity chunk is put back, a CWS request gets a 500, AWS
  says "Operation failed".
- [`drop_db` / `init_db` waiting behind a busy cluster now fail after 30 s] →
  accepted: a clear error beats an endless wait with CMS down.
- [A role-level `lock_timeout` set by an operator is now overridden] → use the
  URL or `PGOPTIONS`; documented in the sample config.

## Migration Plan

The owner decided on 2026-11-26 that no contest-free window can be assumed, so
this change does not get a release of its own: it ships together with the
previous, not yet deployed release (omi.18), in a low-traffic hour, with a
backup first.

**Before** deploying, run a read-only connection check against the real database
path: from the running application container, which still has the old code,
connect with the same `options` value this change sends and read
`SHOW lock_timeout`. It must print `30s`. If the check fails, do not deploy.

After the deploy, `SHOW lock_timeout` from the `cms` container must print
`30s`.

Rollback: the kill-switch suffix plus `./up.sh` choice 1. The suffix does not
help if the `options` parameter itself is rejected, because it still sends it;
then build the omi.18 commit `4d93b8b8`. Neither omi.18 nor this change changes
the database schema, so no database step is needed.
