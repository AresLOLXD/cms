# cmsWatchdog: detect its own crash loop, and map the ssl parameters of CMS_DB_URL (issues #68, #69) — Design

**Date:** 2026-10-24
**Status:** Decisions by a read-only panel (sre-engineer); the owner approved
the consolidated design.

## #68 — crash loop after startsecs

### Facts
supervisord keeps no restart count: `getProcessInfo` has only start, now,
exitstatus, spawnerr; the `startretries` counter resets once a run passes
`startsecs=1`; a crash after that goes RUNNING → EXITED → STARTING with no
backoff and never reaches FATAL. So the watchdog must count its own starts.

### Decisions
- Count starts in the run directory (tmpfs): a container restart is a
  legitimate start and the wipe resets the count, which is honest; a
  persistent data_dir would count every `./up.sh` on contest day. The ledger
  already lives there for the same reason.
- Count at start (not in the crash handler `run_forever`): it also catches
  self-guard exits (`faulthandler exit=True`) and OOM kills.
- `Files.record_start(wall) -> int`: a JSON list in
  `run_dir/watchdog-starts.json`; keep entries of the last 3600 s, drop
  future ones, append `wall`, cap at 50, write with the module's
  `write_atomic`; any OSError/ValueError while reading or writing counts as an
  empty history and never raises. Separate from the ledger (a corrupt ledger
  exhausts every budget).
- Call it in `serve()` right after `load_ledger` (~`main.py:1275`), before
  the first cycle. If the count is exactly `START_LOOP_LIMIT + 1` (constant 5
  in `policy.py`), `alerter.emit("ERROR", ...)` once (at the crossing; no
  repeats, no state).
- Alert text: "Watchdog: it has started N times in the last hour, so it is
  crash-looping (each run ends in an error or at its self-guard). Read the
  error: `./logs.sh cms | grep -B3 -A30 'cycle failed unexpectedly'`. To stop
  the loop, stop `cmswatchdog` (see 'How to turn it off') or set
  `CMS_WATCHDOG=off` and run `./up.sh`. Keep the traceback for the
  developers."
- Non-goals: anything in data_dir, supervisord event listeners or XML-RPC
  history, a `startsecs` bump (it turns the loop into a silent FATAL;
  `test_generate_config.py` asserts there is none), a policy.Decision or
  open-alert entry, repeat state, a `--status` change, self-disable.

## #69 — ssl* parameters of CMS_DB_URL

### Decisions
- An allow-list tuple in the psql environment builder (`cms/watchdog/probes.py`),
  not "forward every query key" (that would let `options`,
  `application_name`, `connect_timeout` override the gate's own settings).
  Env name = `"PG" + key.replace("_", "").upper()`, value = last value
  (`value[-1]`):
  sslmode, sslrootcert, sslcert, sslkey, sslcrl, sslcrldir, sslcertmode,
  sslsni, sslnegotiation (libpq 17+; older ignores it),
  ssl_min_protocol_version, ssl_max_protocol_version, target_session_attrs
  (so a multi-host gate does not land on a standby).
- `sslpassword` has no environment variable in libpq: leave it unmapped (argv
  is forbidden by the module's own rule); the gate stays red with libpq's
  text; document the limit.
- Multi-host: map it, don't fail. Mirror SQLAlchemy 2.0's
  `_split_multihost_from_url` (`dialects/postgresql/base.py`): split every
  `host` token on `:` and set `PGHOST="a,b"`; set `PGPORT="5432,"` style
  (empty entry = default) when any port is present; honour a query `port` for
  the `host=a,b&port=1,2` form. The same code fixes a single
  `?host=db:5433`.
- Decoding: `parse_qs` already percent-decodes and turns `+` into a space,
  like SQLAlchemy's `parse_qsl`.
- Non-goals: sslpassword via a service file, other non-ssl keywords,
  a multi-host failure alert, IPv6 inside repeated host tokens, the mixed
  `host:port` + `port` error case, a TLS integration test in CI.

## Tests
- #68 `TestFiles`: the count accumulates across `Files` instances; old and
  future entries are dropped; a corrupt or non-list file counts as empty; an
  unwritable run dir does not raise. `TestServe` (`main_cycle_test.py` ~951):
  patch `Watchdog.cycle` to raise, run `serve()` 6 times, the ERROR line
  appears on the 6th run only.
- #69 `TestEnvironment` (`probes_gate_test.py` ~104): a table of keyword →
  env var → value with subTests (including `%2F` and `%20` in a path);
  `application_name`, `options`, `connect_timeout` in the URL leave the gate's
  PGAPPNAME/PGOPTIONS/PGCONNECT_TIMEOUT unchanged; a `sslpassword` secret
  appears in no env value; multi-host forms (repeated `host=a:5432&host=b`,
  comma form with `port`, single `host=db:5433`) checked against
  `PGDialect_psycopg2().create_connect_args(make_url(url))` as an oracle.
  One manual check against a postgres:18 container with ssl=on if cheap; not
  in CI.

## Docs (en + es .po by hand)
A row in the contest-day table next to "its cycle failed unexpectedly"
(`docs/contest-day.md` ~341); the CMS_DB_URL paragraph of "The watchdog" in
`docs/docker-deployment.md` (~308-313): the mapped parameters and the
sslpassword limit.

## Tasks
1. #68 + tests. 2. #69 + tests. 3. Docs + docs check.
