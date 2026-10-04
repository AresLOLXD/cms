# Participant activity log: sessions, devices and IPs

Date: 2026-10-02. Branch: `feat/participant-activity-log` (from main
250c3ef0). Not for the 2026-10-10 contest: cms-live is frozen until then, so
this ships afterwards.

Line numbers refer to commit 250c3ef0.

## 1. Goal

Store when each participant starts and stops participating, and from which
devices and IP addresses, for two equal-weight uses:

- **Audit / anti-cheating:** spot an account used by two people, and keep
  evidence if a result is disputed.
- **Attendance:** who took part, when they started and when they stopped,
  including exams left open for a week.

Success: an admin opens a contest in AWS and sees, per participation, the
intervals of activity with device, IP, start, last activity and how each one
ended; the "simultaneous activity" alert lists accounts active from two
devices at once; the whole log exports as CSV.

### 1.1 Constraints that shaped the design

- **Logout is rare.** `LogoutHandler` (`cms/server/contest/handlers/main.py:287`)
  only clears the cookie. Most sessions end when the cookie expires or the
  browser closes; with `ip_autologin` there is no login at all. So "when it
  ends" means **explicit logout, or last activity** before a period of
  inactivity.
- **Dynamic IPs and CGNAT.** In Mexico most home connections get dynamic IPs
  and many ISPs use CGNAT. A participant changing IP, or two strangers
  sharing one, is normal. IPs are recorded as evidence but are **not** an
  alert signal on their own. The device is what tells "same person, new IP"
  from "someone else on the account".
- **Scale.** The fork targets contests of up to 100,000 participants (#29).
  CWS handlers already do synchronous DB work on the event loop (#22); this
  feature must not add per-request DB writes.

### 1.2 Out of scope

- Failed login attempts (they stay in the service log only).
- User-agent or browser fingerprinting.
- Requests made while an admin impersonates a participant.
- Including the log in `DumpExporter` dumps (the CSV is the export).
- Automatic purging (rows go away with their participation).
- A dedicated load test (measure it with #7's load test once that exists).

## 2. Data model

New model `ActivityInterval` in `cms/db/activity.py`, table
`activity_intervals`, exported from `cms/db/__init__.py`. One row is one
continuous interval of activity of a participation **from one device and one
IP**.

| Column | Type | Meaning |
|---|---|---|
| `id` | integer PK | |
| `participation_id` | FK `participations.id`, `ON DELETE CASCADE`, not null | |
| `device_id` | `UUID`, nullable | Anonymous device id from the `cms_device` cookie; `NULL` when the request presented no valid device cookie: a client authenticated by the `X-CMS-Authorization` header, or a browser without the cookie (first request after an IP autologin, cleared cookies, a cookie-less client). A `NULL` device is not evidence of an API script |
| `ip` | `INET`, not null | Real client IP (already resolved behind the proxy) |
| `started_at` | timestamp, not null | First request of the interval |
| `last_seen_at` | timestamp, not null | Last flushed activity |
| `logged_out_at` | timestamp, nullable | Set only on explicit logout |
| `started_by` | enum `login` / `resumed`, not null | `login`: opened by a username/password login. `resumed`: opened by an existing session (cookie, header or IP autologin) after inactivity or from a new device/IP |

Indexes: `(participation_id, last_seen_at)` for the flush lookup and the
overlap query; `(ip)` for the IP filter.

No backref on `Participation` (same choice as `RankingGroup`), so
`DumpExporter`, which follows every relationship, does not pick the table up.

**End state is derived on read, never stored:**

- `logged_out_at` is set → `logout`, ended at `logged_out_at`;
- else `last_seen_at < now - activity_inactivity_threshold` → `inactivity`,
  ended at `last_seen_at`;
- else → `active`.

Nothing has to sweep and close stale intervals.

**When a new interval starts:** the device changes, the IP changes, a
login happens, the previous interval has a logout, or more than
`activity_inactivity_threshold` passed since its `last_seen_at`. A dynamic IP
change on the same device therefore shows up as consecutive intervals with
the same `device_id`.

### 2.1 Migration

`cmscontrib/updaters/fork_activity_intervals.py`, shaped like
`fork_multi_contest.py`: the SQL is a Python constant (Docker installs CMS
non-editable and does not package `.sql` files) and every statement is
idempotent. PostgreSQL has no `CREATE TYPE IF NOT EXISTS`, so the
`activity_started_by` enum is created inside a `DO $$ ... IF NOT EXISTS
(SELECT 1 FROM pg_type WHERE typname = ...) ... $$` block, the same guard
`fork_multi_contest.py` uses for its foreign key; then
`CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`.
`setup_db()` in `cmscontrib/SetupDB.py` calls the new
`apply_fork_activity_intervals_update()` next to
`apply_fork_multi_contest_update()` (line 161), so existing Docker
deployments upgrade on start. `cmstestsuite/unit_tests/schema_diff_test.py`
is updated for the new table.

## 3. Capture and flush (ContestWebServer)

### 3.1 Device cookie

- Name `cms_device`, **global** (not per contest), so one computer is
  recognized across all contests of a multi-contest CWS.
- Value: a random UUID, written with `set_secure_cookie` so arbitrary values
  cannot be injected; lifetime one year.
- Read in `get_current_user`. A missing or badly signed cookie is treated as
  absent: that request is recorded with device `None`, and a new UUID is
  issued in the cookie for the next ones (so the first request after an IP
  autologin, after cleared cookies, or from a cookie-less client has no
  device). A login records its request with the device directly, issuing it
  if needed.
- When authentication came from the `X-CMS-Authorization` header, the device
  is `None` and no cookie is set, so header-only API clients that keep no
  cookies do not create a new device per request.

### 3.2 `ActivityRecorder`

New module `cms/server/contest/activity.py`. One instance per CWS process,
owned by `ContestWebServer` (`cms/server/contest/server.py`). It is the only
code that writes `activity_intervals`. Handlers call two methods that never
touch the DB:

- `record(participation_id, device_id, ip, timestamp, login=False)`
- `record_logout(participation_id, device_id, ip, timestamp)`

In-memory buffer: a dict keyed by `(participation_id, device_id, ip)` whose
value is a short list of pending segments
`{first_seen, last_seen, login: bool, logged_out_at}`. A plain request updates
`last_seen` of the last segment. `login=True`, or any activity after a
segment with `logged_out_at`, appends a new segment. The buffer is bounded by
the number of active participants.

The IP is stored as PostgreSQL's `inet` accepts it: the scope of an IPv6
address (`fe80::1%eth0`, which Python 3.12's `ipaddress` accepts and `inet`
rejects) is dropped and the address is kept in canonical form. A request whose
IP is still not a valid address is logged as a WARNING and not recorded, so
one bad value cannot make every flush of its chunk fail.

### 3.3 Hook points

1. **`get_current_user`** (`cms/server/contest/handlers/contest.py`, around
   line 150): after `authenticate_request` returns a participation and
   `impersonated` is false, call `record(...)`. This covers cookie, header and
   IP-autologin authentication, and every handler including the polling ones
   (`refresh_cookie = False`).
2. **Successful login:** `LoginHandler.post` (`handlers/main.py:221`) and the
   API login (`handlers/api.py`) call `record(..., login=True)` when
   `validate_login_async` returns a participation. The API login skips it when
   an `admin_token` was used (that is an impersonated login,
   `cms/server/contest/authentication.py:227`).
3. **`LogoutHandler.post`** (`handlers/main.py:287`): resolve the current
   participation first; if there is one and it is not impersonated, call
   `record_logout(...)`, then clear the cookie as today.

### 3.4 Flush

- An asyncio task flushes every `activity_flush_interval` seconds (default 60)
  with the async session (`cms/db/async_session.py`), one transaction per
  batch, so the event loop never blocks on it. A batch is a chunk of up to
  `FLUSH_CHUNK_SIZE = 500` participations (with all their keys), by ascending
  participation id. Each key is written at most once per interval: about
  1,700 rows/s in batches with 100,000 active participants.
- The buffer is swapped out at the start of a flush, so requests keep writing
  into a fresh one. Flushes of one process run one at a time (an
  `asyncio.Lock`).
- For each chunk, in its transaction: take the advisory locks of all its
  participations in one statement, in ascending order (avoids deadlocks;
  this stops two CWS shards from opening the same interval twice), with the
  two-key form and a fixed namespace, so the log does not claim the whole
  lock key space and a transaction holds at most 500 of them:
  `SELECT pg_advisory_xact_lock(ACTIVITY_LOCK_NAMESPACE, id) FROM unnest(:ids)
  AS id ORDER BY id`. Then drop the participations deleted meanwhile and load
  the latest interval of every key of the chunk in one query (`DISTINCT ON
  (participation_id, device_id, ip) ... ORDER BY ..., last_seen_at DESC`).
  For each pending segment of a key, in order:
  - if that interval exists, has no `logged_out_at`, the segment is not a
    login, and `segment.first_seen - interval.last_seen_at <=
    activity_inactivity_threshold`: set `started_at = min(...)`,
    `last_seen_at = max(...)` (another shard may have written later activity
    first) and `logged_out_at` if the segment has one, in which case
    `last_seen_at = logged_out_at`;
  - otherwise insert a new interval with `started_by = login` if the segment
    is a login, else `resumed`.
- **Failure:** if a chunk fails (e.g. DB unavailable), log it and merge that
  chunk's keys back in front of the current buffer; the other chunks are
  still written, and the next tick retries. Nothing is lost while the
  process lives. A cancelled flush also merges back everything not committed
  yet.
- **Shutdown:** one last flush when the service stops, after any flush in
  flight, bounded by `SHUTDOWN_FLUSH_TIMEOUT = 30` s (on timeout, log ERROR
  with how many participations' activity is lost). A hard crash loses at
  most one interval of `last_seen` (about 60 s).

### 3.5 Configuration

Under `[contest_web_server]` in `cms.toml`, in `cms/conf.py` and
`config/cms.sample.toml`:

- `activity_flush_interval = 60` (seconds)
- `activity_inactivity_threshold = 1800` (seconds)

## 4. AdminWebServer

All views are read-only and available to any authenticated admin, like the
ranking. Times are shown in the contest timezone (`get_timezone(None,
contest)`; the rest of AWS prints raw UTC).

### 4.1 Contest activity page

`/contest/<id>/activity`, linked from the contest sidebar. New handler module
`cms/server/admin/handlers/contestactivity.py`, template
`contest_activity.html`.

**Alerts** at the top, computed by SQL on demand:

- **Simultaneous activity:** pairs of intervals of the same participation
  with different `device_id` (or different `ip` when either device is `NULL`)
  whose time ranges overlap by more than `2 * activity_flush_interval`. The
  margin absorbs flush granularity. An interval's range is
  `[started_at, last_seen_at]`: on logout `last_seen_at` equals
  `logged_out_at`, and while active it lags at most one flush. Each row:
  user, the two devices/IPs, the overlap window, link to the participation.
  The overlap test is written without `least()`/`greatest()` (each
  `last_seen_at` is greater than each `started_at` plus the margin), with
  `last_seen_at` bare so the `(participation_id, last_seen_at)` index can
  bound a lookup. This page shows at most `SIMULTANEOUS_LIMIT = 200` pairs,
  the most recent overlap first, with the true total in the heading (a
  `count(*) OVER ()` of the same query); when capped it says so and points to
  the participation pages (uncapped) and the CSV.
- **More than one device:** participations with more than one distinct
  non-null `device_id`, and the count. Informational only.

**Intervals table** below: user, device (first 8 characters of the UUID),
IP, start, last activity, end reason (`logout` / `inactivity` / `active`),
`started_by`. Newest first, 100 rows per page, filters by username and IP via
GET parameters (the IP filter is mainly for on-site venues).

### 4.2 Participation page

A new "Activity" section in `participation.html`: all intervals of that
participation (not paginated) with its alerts highlighted. This is the view
for a dispute.

### 4.3 CSV export

`/contest/<id>/activity/csv`, a link on the activity page, like the
ranking's `/ranking/csv` (`cms/server/admin/handlers/contestranking.py:85`).

- Honors the active filters.
- Columns: `username, first_name, last_name, device_id, ip, started_at,
  last_seen_at, ended_at, end_reason, started_by`, where `ended_at` is
  `logged_out_at`, or `last_seen_at` for `inactivity`, empty for `active`.
- Times in ISO 8601 with an explicit UTC offset.
- Streamed: keyset batches (by `id`) are read in the executor and written
  with `write()` + `await flush()` on the loop, releasing the DB connection
  between batches, so millions of rows never sit in memory. (`yield_per`
  would not stream: in AWS a `write()` from an executor thread only
  buffers.)

### 4.4 Retention

No automatic purge. Intervals are deleted with their participation (cascade),
so bulk removal and contest deletion take them too.

## 5. Testing

New tests are asyncio or plain synchronous and import nothing from
`cmscontrib/` that monkey-patches gevent (`cmscontrib.updaters.*` is safe),
so they all belong to the asyncio group of `docker/_cms-test-internal.sh`.

1. **Recorder buffer** (no DB): repeated requests of one key keep one
   segment; a login appends a segment; activity after a logout appends a
   segment; header authentication gives device `None`.
2. **Flush against the DB:** extend vs. open a new interval across the
   threshold; `logged_out_at` is written and later activity opens a new
   interval; `started_by` is right; two recorders flushing the same key
   concurrently produce one interval (advisory lock); a failed flush keeps the
   buffer and the next tick writes it.
3. **CWS handlers:** an authenticated request is recorded; an impersonated one
   is not (cookie and API `admin_token`); `cms_device` is created when missing,
   reused when present, replaced when badly signed; web and API logins record
   `login`; logout records `logged_out_at`.
4. **AWS:** simultaneous activity for a real overlap; no alert for
   consecutive dynamic-IP intervals on one device; no alert for an overlap
   under the margin; more than one device; filters and pagination; CSV
   columns, `end_reason`, UTC offset and filters.
5. **Migration:** `schema_diff_test` updated; `fork_activity_intervals` run
   twice in a row without error.

## 6. Files touched

- New: `cms/db/activity.py`, `cms/server/contest/activity.py`,
  `cms/server/admin/handlers/contestactivity.py`,
  `cms/server/admin/templates/contest_activity.html`,
  `cmscontrib/updaters/fork_activity_intervals.py`, and the tests above.
- Changed: `cms/db/__init__.py`, `cms/conf.py`, `config/cms.sample.toml`,
  `cms/server/contest/server.py`, `cms/server/contest/handlers/contest.py`,
  `cms/server/contest/handlers/main.py`, `cms/server/contest/handlers/api.py`,
  `cms/server/admin/handlers/__init__.py`, the contest sidebar template,
  `cms/server/admin/templates/participation.html`, `cmscontrib/SetupDB.py`,
  `cmstestsuite/unit_tests/schema_diff_test.py`, `cms/db/base.py`
  (`_TYPE_MAP` gains `INET` and `UUID`), `cmscommon/datetime.py`
  (`get_timezone` accepts no user).
