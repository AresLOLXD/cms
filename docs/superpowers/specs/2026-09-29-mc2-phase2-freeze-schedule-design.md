# Ranking Freeze and Scheduled Visibility per Group (MC-2 phase 2) — Design Spec

**Date:** 2026-09-29
**Status:** Approved (design, sections 1-5); pending written-spec review
**Builds on:** `2026-09-29-mc2-ranking-visibility-design.md` (MC-2 minimal)
**When:** implemented after the 2026-10-10 contest, on `beta`

## Problem

MC-2 minimal lets an organizer hide a ranking group's public scoreboard by
hand while the staff watch it live. The original sub-project 2 also asked
for the two remaining pieces:

- **Freeze:** near the end of a contest, the public keeps seeing the
  scoreboard as it was at a given time, while the staff keep seeing it
  live; then it is published all at once.
- **Schedule:** hiding, revealing, freezing and unfreezing happen by
  themselves at a configured time, per group, without anyone pressing a
  button.

## Goals

- Per ranking group, an organizer configures in AWS, with fixed dates and
  times: when to hide, when to show, when to freeze and when to unfreeze.
- While frozen, the public sees the scoreboard **as it was at the freeze
  time** (a snapshot); the staff see it live.
- The changes happen by themselves at the configured times, and stay
  correct across restarts of any service.
- Manual control remains: "… now" buttons.
- Groups without any scheduled time behave exactly as today.

## Non-Goals

- Times relative to the contest end (for example "60 minutes before the
  end"). Times are absolute; an extension is adjusted by hand.
- More than one hide window and one freeze window per group.
- ICPC-style "pending" cells during a freeze, or showing only positions.
- Freeze or visibility for the legacy root ranking (single-contest mode).
- Live public updates of late evaluations of pre-freeze submissions: the
  public sees them on reload.

## Decisions Taken in Brainstorming

| Question | Decision |
|---|---|
| What does the public see while frozen? | A snapshot at the freeze time |
| What can be scheduled? | Hide, show, freeze and unfreeze |
| How are times given? | Fixed date and time (no relative times) |
| Where is the freeze applied? | In RWS, filtering by submission time (approach A) |
| Who triggers scheduled changes? | Nobody: RWS evaluates the state from the clock on every request |
| How are manual and scheduled control combined? | Each state is a time window; "… now" buttons fill in the current time |
| Which wins when both apply? | Hidden wins over frozen |

Rejected alternatives: holding back post-freeze data in ProxyService with
a separate `<group>--staff` namespace (double traffic, a different staff
URL, a resend on unfreeze); copying the stores in RWS when the freeze time
arrives (wrong snapshot if RWS is down at that moment); timers in
ProxyService or AWS (a restart or outage at the scheduled time breaks it).

## Design

### 1. Behaviour

**Windows.** Each group has two windows, each with optional ends:

- Hidden from `hide_at` until `show_at`.
- Frozen from `freeze_at` until `unfreeze_at`.

A missing start means the window never opens; a missing end means it
never closes. With `now` the current time:

    hidden(now) = hide_at is not None and hide_at <= now
                  and (show_at is None or now < show_at)
    frozen(now) = freeze_at is not None and freeze_at <= now
                  and (unfreeze_at is None or now < unfreeze_at)

Hidden wins: a group that is hidden and frozen behaves as hidden.

**"… now" buttons.** "Ocultar ahora", "Mostrar ahora", "Congelar ahora" and
"Descongelar ahora" set their field to the current time. They also clear
the other end of the same window when it lies in the past, so the window
stays valid. For example, "Ocultar ahora" after an earlier "Mostrar
ahora" sets `hide_at` to now and clears the old `show_at`. Today's Hide
checkbox is replaced by "Ocultar ahora" and "Mostrar ahora".

**Public, while frozen.**

- The scoreboard as it was at `freeze_at`. The submissions made before
  `freeze_at` count, with their result even if they are evaluated later,
  because every score change carries the submission's time.
  Submissions made after `freeze_at` don't count. Token uses after
  `freeze_at` don't count either, since they carry the token's time.
- A banner at the top: "Ranking congelado desde las HH:MM (<time zone name>)", with a
  discreet "Acceso staff" link.
- No live score updates. A late evaluation of a pre-freeze submission
  appears on reload.

**Staff.** The same password and the same cookie as MC-2 minimal. With a
valid cookie the staff see everything live. On a frozen group, the page
carries the banner "Vista staff: ranking congelado para el público".
While hidden, the staff log in from the notice, as today; while frozen,
from "Acceso staff".

**Transitions.** They happen at the configured times with no timer: RWS
evaluates the state on every request. At each transition RWS closes the
public live streams, and the browser reconnects to the right view (see
§3). If any service restarts, the state is still correct, and the freeze
snapshot does not change: it depends on `freeze_at`, not on when it is
computed.

### 2. Data, protocol and compatibility

**Database.** `ranking_groups` gains `hide_at`, `show_at`, `freeze_at` and
`unfreeze_at`: nullable `DateTime` in UTC, like the other CMS datetimes.
They are added by the idempotent fork SQL in
`cmscontrib/updaters/fork_multi_contest.py`, which `cmsSetupDB` (Docker
`db-init`) applies on every run.

**Migration of `hidden`.** The existing `hidden` column is kept for
rollback safety.

- The fork SQL sets `hide_at` to the current time for every row with
  `hidden = true` and `hide_at IS NULL`. It is idempotent: after the first
  run no such row is left, unless an older AWS (after a rollback) hid the
  group, in which case the next run converts it again.
- On every save, AWS writes `hidden` derived from the windows: true when the
  group is hidden now **or** a hide is pending (`hide_at` set and `show_at`
  empty or in the future). An older CMS container after a rollback
  therefore never publishes a group that the new model has hidden or
  will hide: it fails closed.

**Wire format.** The same `PUT /<group>/visibility` with the proxy's write
credentials, now with this body:

    {"hide_at": <int|null>, "show_at": <int|null>,
     "freeze_at": <int|null>, "unfreeze_at": <int|null>,
     "staff_password": <hash string|null>}

The times are Unix seconds.

- **Old format.** RWS also accepts MC-2 minimal's body
  `{"hidden": <bool>, "staff_password": ...}`, sent by a CMS container
  after a rollback. `true` means `hide_at = 0` with the other times null
  (hidden since always); `false` means all times null.
- **Validation.** RWS answers 400 when an end is not after its start (both
  set), when a time is not an integer, or when the body mixes the two
  formats.

**State file.** `visibility.json` stores the same four times, the hash and
the secret, with atomic writes as today. A file written by MC-2 minimal,
with `hidden` and no times, is read with the same conversion as the old
wire format. An unreadable or malformed file still fails closed: hidden,
with no staff password.

**Time zone.** AWS asks for every time in UTC, like the rest of AWS ("(en
UTC)"), and shows next to each field its equivalent in the server's time
zone (`CMS_TIMEZONE`), for example "= 13:00 hora de México". The public
banner shows the freeze time in that zone. RWS formats it with the
container's local time zone (`TZ`, set from `CMS_TIMEZONE` in the compose
file; add it to the `ranking` service if it is missing).

**Deployment order.** RWS first, then the CMS container, as today. An RWS
of MC-2 minimal rejects the new format with 400, and ProxyService then
holds the group's data back: nothing is exposed.

### 3. RankingWebServer

**State.** `VisibilityState` keeps the four times and computes `hidden`
and `frozen` from `time.time()` on every request. The hidden path is
unchanged.

**Public requests while frozen (and not hidden):**

| Path | Response |
|---|---|
| `/scores` | Scores at `freeze_at`: for each user and task, the last entry of the score history `(time, score)` with time ≤ `freeze_at`. RWS already keeps that history per user and task. |
| `/history` | The global history cut to time ≤ `freeze_at`. |
| `/sublist/<user>` | Only the submissions with time ≤ `freeze_at`, with the result computed from the changes with time ≤ `freeze_at`. |
| `/events` | Contest, task, team and user events pass. Score, submission and subchange events are dropped, both live and when cached events are replayed on reconnect. |
| `/submissions`, `/subchanges` | 403, as for a hidden group. The page doesn't use them. |
| `/`, `/Ranking.html`, static files, `/contests`, `/tasks`, `/teams`, `/users`, faces, flags, logo, config | Unchanged. The public `/` gets the freeze banner. |

Filtered responses carry `Cache-Control: no-store`.

**Staff access while frozen.** A new `GET /<group>/staff-login` renders
the same form as the notice; the `POST`, the cookie and the logout are
MC-2 minimal's. With a valid cookie everything passes through live, and
`/` gets the staff freeze banner.

**Transitions.** The guard remembers the last effective state of each
group. When it sees a change (hidden or frozen, either way), it closes the
open public streams, generalizing the mechanism that already cuts them
when a group gets hidden. At worst this happens at the next write, the
15 s ping. When the browser reconnects:

- after a freeze, the event filter keeps post-freeze data out;
- after an unfreeze, the event cache replays what was missed, or RWS
  sends `reload` when the cache no longer has it, and the page shows
  everything.

**Performance.** `/scores` at a time walks users × tasks × history, which
takes milliseconds for about 300 contestants. If needed it can be cached
per data version.

### 4. AdminWebServer and ProxyService

**Group page (AWS).**

- The current state at the top, for example "Ahora: congelado desde 13:00
  (hora de México) hasta 16:00" or "Ahora: visible; se congela hoy a las
  13:00".
- Four datetime fields (UTC, with the local equivalent next to each) in two
  windows, each field optional.
- "Ocultar ahora", "Mostrar ahora", "Congelar ahora" and "Descongelar
  ahora" buttons, as in §1.
- **Stale-form protection**, generalizing MC-2 minimal's `hidden_shown`:
  each field carries the value the page was rendered with, and it is
  applied only when the organizer changed it. Saving an old page never
  undoes what another organizer scheduled.
- **Validation:** an end must be after its start when both are set. It is
  rejected with a notification and nothing is saved. A time in the past is
  allowed and applies at once.
- The staff password works as in MC-2 minimal.
- The group list shows the current state and the next scheduled change.
- On save, the derived `hidden` column is written (§2).

**ProxyService.** It stays a messenger. It sends the four times and the
hash in the visibility `PUT` at the same moments as today: at startup, on
reinitialize, on every AWS save (through reinitialize) and at every
sweep. It has no scheduling logic. The authoritative clock is RWS's; AWS,
ProxyService and RWS run on the same host.

### 5. Security Considerations

- The freeze filter is an allow-list like the hidden one: anything not
  listed as passing or filtered in §3 answers 403 for the public while
  frozen.
- `/sublist/<user>` exposes nothing after `freeze_at`: submissions,
  scores and token uses after it are all filtered.
- No new secret is added; the staff cookie is MC-2 minimal's.

## Error Handling

| Situation | Behaviour |
|---|---|
| An MC-2 minimal RWS receives the new format | 400; ProxyService marks the group rejected and holds its data back (existing behaviour); deploy RWS first |
| A CMS container after a rollback sends `{"hidden": …}` | The new RWS accepts it (`true` = hidden since always, `false` = no windows); the schedule is lost until the new CMS resends it at the next sweep |
| `visibility.json` unreadable or corrupt | Fail closed: hidden, no staff password (as today) |
| Invalid window | AWS rejects with a notification; RWS answers 400 |
| RWS or ProxyService down at a scheduled time | No effect: the state comes from the clock on every request; the snapshot depends only on `freeze_at` |
| A pre-freeze submission evaluated after `freeze_at` | Counted in the snapshot (its submission time is before `freeze_at`); the public sees it on reload |

## Testing

- **RWS (unit, gevent group):**
  - window evaluation at the edges (before, exactly at and after each
    time), and hidden winning over frozen;
  - frozen `/scores` equal to the scores computed from the changes with
    time ≤ `freeze_at`, including a late evaluation of a pre-freeze
    submission;
  - `/history` and `/sublist` cut, including a token used after the
    freeze;
  - the `/events` filter, live and on replay;
  - the stream cut at each transition;
  - staff live, and `GET staff-login`;
  - the old `PUT` format and an old `visibility.json`;
  - 400 on invalid bodies.
- **ProxyService:** the payload format, and that it is sent at startup, on
  save and at every sweep.
- **AWS:**
  - the fields, the "… now" buttons (including clearing a past end), the
    per-field stale-form protection, the validation and the derived
    `hidden`;
  - the fork SQL, idempotent, including the `hidden` to `hide_at`
    migration, with `schema_diff_test` green.
- **Functional:** extend the functional visibility scenario (added before
  2026-10-10) with a freeze. Score before and after `freeze_at`, check the
  public snapshot and the staff view, then unfreeze.
- **Chromium end to end, on a local stack:**
  - short windows (1-2 minutes) that switch by themselves;
  - submissions before and after the freeze;
  - the staff live view;
  - unfreeze showing everything;
  - a restart of RWS during the freeze, after which the snapshot is
    unchanged.

## Operations

- Deploy: RWS first, then CMS (`./up.sh`: **Ranking only**, then **CMS
  only**), as for MC-2 minimal.
- Before the contest, per group: set the hide or freeze windows, then
  check the public URL and the staff login.
- Rollback: roll back only the CMS container, as for MC-2 minimal. The new
  RWS accepts the old format, and the derived `hidden` column keeps hidden
  or soon-hidden groups hidden.
- Update `docs/multi-contest.md` with the windows, the buttons, the time
  zone and the freeze behaviour.
