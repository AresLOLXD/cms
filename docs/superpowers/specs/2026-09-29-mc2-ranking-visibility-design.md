# Ranking Visibility per Group (MC-2, minimal) — Design Spec

**Date:** 2026-09-29
**Status:** Approved (design); pending written-spec review
**Builds on:** `2026-09-23-multi-contest-rankings-design.md` (MC-1)

## Problem

On 2026-10-10 a real contest day runs on `beta` in multi-contest mode
(`CMS_CONTEST_ID=ALL`, one ranking group per olympiad). Organizers need a
group's public scoreboard to stay hidden during the contest while the staff
(judges, organizers) keep watching the live ranking, and to reveal it at the
end. Today every read endpoint of RankingWebServer (RWS) is public: the page,
the JSON stores, `/scores`, `/history`, the live `/events` stream, faces and
flags. Only ProxyService's writes are authenticated.

## Goals

- Per ranking group, an organizer can **hide** the public scoreboard from the
  group's page in AdminWebServer (AWS).
- While hidden, the public sees a notice at the group's ranking URL and **no
  ranking data leaks** through any endpoint.
- The staff open **the same URL**, enter the group's **staff password**
  (configured in AWS) and see the live ranking.
- Revealing is un-hiding the group in AWS; it takes effect within seconds.
- Nothing changes for visible groups, for legacy single-contest mode, or for
  deployments that never touch these settings.

## Non-Goals

- Freezing the ranking at a given time, and scheduled hide/freeze/reveal
  (the rest of the original sub-project 2). They can be added later as extra
  columns without changing this design.
- Visibility for the legacy root ranking (no groups).
- Per-domain CWS (MC-3), per-contest admin permissions, Telegram bot.
- Account-based staff access, rate limiting beyond a failed-login delay.

## Decisions Taken in Brainstorming

| Question | Decision |
|---|---|
| What does the public see when hidden? | A notice page; data endpoints closed |
| Where are settings managed? | AWS, per ranking group |
| How do settings reach RWS? | ProxyService pushes them (approach A) |
| How do staff get in? | Same URL, password form, signed cookie |
| One password or one per group? | One per group |

Deviation from MC-1's "Forward compatibility" note: that note sketched the
staff view as a second namespace `<group>--staff` and a visibility *mode*
column. The staff view is instead a login on the same namespace, as decided
above, and visibility is a single `hidden` boolean because freeze and
scheduling are out of scope. Adding them later means new columns, not a
change to these.

## Design

### 1. Data model and migration

`ranking_groups` gains two columns (`cms/db/rankinggroup.py`):

- `hidden`: `Boolean`, not null, Python-side default `False`.
- `staff_password`: `Unicode`, nullable. Stores the output of
  `cmscommon.crypto.hash_password(password, method="bcrypt")`, the same
  format CMS uses for other passwords. `NULL` means no staff password: a
  hidden group is then hidden from everyone.

Existing databases get the columns from the idempotent fork SQL in
`cmscontrib/updaters/fork_multi_contest.py`, which `cmsSetupDB` already
applies on every run (the Docker `db-init` service runs it). Follow the
pattern used for `contests.active`: `ADD COLUMN IF NOT EXISTS` with a default
to fill existing rows, then drop the server default so the schema matches
the model. `schema_diff_test` must stay green. Ranking groups are not part of
dumps, so export and import are unaffected.

### 2. AdminWebServer

Pages: `add_ranking_group.html`, `ranking_group.html`, `ranking_groups.html`;
handlers in `cms/server/admin/handlers/rankinggroup.py`.

- Checkbox **"Hide ranking from the public"** bound to `hidden`.
- Password input **"Staff password"** (`type="password"`,
  `autocomplete="new-password"`). Empty keeps the current value; a separate
  checkbox **"Remove staff password"** sets it to `NULL`. A non-empty value
  is hashed with `hash_password(..., "bcrypt")` before it is stored.
- The password or its hash is never rendered back to the browser. The page
  only says whether a password is set.
- The group list shows a "hidden" and a "password set" marker per group.
- Saving keeps scheduling `proxy_service.reinitialize`, as today.
- Form parsing follows the existing ranking-group handlers. Invalid input
  (both a new password and "remove" ticked) is rejected with a notification
  and nothing is saved.

### 3. ProxyService

Group mode only (`contest_id is None`). Legacy mode is unchanged.

- New operation type `VISIBILITY`, handled like `RESET` (a special type, not
  one of the store types). For each group, `initialize()` enqueues it with a
  priority higher than any data operation of that group, so RWS learns the
  visibility **before** it receives any data of a new or re-created
  namespace. `reinitialize` and `regenerate_ranking` go through
  `initialize()` and so resend it. Each sweep (every ~347 s) also resends
  every group's current visibility, so a lost `reinitialize`, an RWS volume
  restored without its state, or a rejection that RWS no longer makes
  corrects itself within one sweep.
- Request: `PUT <ranking>/<group>/visibility` with the proxy's write
  credentials and the JSON body
  `{"hidden": <bool>, "staff_password": <hash string or null>}`. The hash,
  never the password, travels on the wire.
- Failures follow the SP1 policy: transport errors and 5xx are retried with
  backoff; a 4xx is dropped for that group with a WARNING telling the
  operator to use Regenerate.
- Coordination: SP1 is changing `ProxyService.py` in parallel. This part is
  implemented after SP1 lands, on top of its executor changes.

**Note (post-review):** A rejected visibility (4xx from RWS) is sticky. The
group stays rejected until a later visibility PUT is accepted. Meanwhile, the
group's data is dropped (held back by ProxyService, not sent to RWS). A
transient failure of the group's new settings requeues its data together
with them, so the data still never reaches RWS before the settings.

### 4. RankingWebServer

A visibility guard wraps each group namespace app (built by `make_app` /
`build_ranking_app`, dispatched by `NamespaceDispatcher`).

**State.** `<group_dir>/visibility.json` holds `hidden`, `staff_password`
(the hash) and `secret` (32 random bytes, hex, created when the file is first
written and kept across updates). Writes are atomic (temporary file, then
`os.replace`). The file is loaded when the namespace app is created, so the
state survives RWS restarts. A group without the file behaves exactly as
today (visible). An unreadable or malformed file **fails closed**: the group
is treated as hidden with no staff password, and an ERROR is logged.

**Update endpoint.** `PUT /<group>/visibility` requires the same Basic
credentials as the other writes. It validates the body (`hidden` must be a
bool; `staff_password` must be null or a string that
`cmscommon.crypto.parse_authentication` accepts) and answers 400 otherwise.
Like other writes, it creates the namespace if missing. The secret is
created on first write and never changes.

Paths below are relative to the namespace, i.e. `/staff-login` means
`/<group>/staff-login`.

**Hidden group, request without a valid staff cookie.** Allow-list:

- `GET /` → the notice page (200).
- `POST /staff-login` → the login check.
- `PUT` / `DELETE` from the proxy → pass through; the store handlers keep
  enforcing their credentials.
- Everything else → 403 with a short body. That covers the JSON stores,
  `/sublist`, `/scores`, `/history`, `/events`, `/faces`, `/flags`, `/logo`,
  `/config` and static files. The allow-list is what guarantees no leak
  through a forgotten endpoint.

**Notice page.** Self-contained HTML in Spanish: inline CSS, no JavaScript,
no external assets. It shows the group name, says the ranking is hidden for
now, and has a password field for the staff. After a failed login it shows an
error message.

**Login.** `POST /staff-login` with a form field `password`:

- The group must have a staff password, and
  `cmscommon.crypto.validate_password(stored_hash, password)` must succeed.
- Success sets the cookie `rws_staff` and redirects (303) to `./`, i.e.
  the group's page.
  - Path: omitted. The browser then scopes the cookie to the directory of
    the login URL, which is the group's namespace even when a reverse
    proxy serves RWS under an extra path prefix. For the same reason every
    redirect and form action is relative.
  - Flags: `HttpOnly`, `SameSite=Lax`, plus `Secure` when the request
    arrived over HTTPS (`X-Forwarded-Proto: https` or the WSGI scheme).
  - Lifetime: session.
  - Value: `HMAC-SHA256(key=secret, msg=<group name> + "\n" + <stored hash>)`,
    hex encoded.
  - Changing or removing the password therefore invalidates every session.
- Failure waits 1 s (cooperative sleep; RWS runs on gevent), then renders
  the notice with an error (401).
- `GET /staff-logout` clears the cookie and redirects to `./`.
- The notice, the 403 responses and the login/logout responses carry
  `Cache-Control: no-store`, so no cache serves a stale page across a
  visibility change or a login.

**Connections opened while visible.** A response the guard let through
while the group was visible (notably the long-lived `/events` stream)
stops as soon as the group becomes hidden, unless the request carried a
valid staff cookie. The guard checks the state before yielding each chunk
of such responses. The browser's reconnection then gets 403.

**Hidden group, valid staff cookie.** Requests pass through unchanged, so the
live scoreboard, including `/events`, works as today. The HTML of `GET /` is
served with a small banner, "Vista staff: este ranking está oculto al
público", with a "Salir" (logout) link. It is inserted right after `<body>`
and fixed to the bottom of the window, and the page's scrolling areas stop
above it, so it covers no row.

**Visible group.** The guard answers `PUT /visibility` and `GET
/staff-logout`, which only clears the cookie. The index page (`GET` and
`HEAD` of `/` and `/Ranking.html`) gets `Cache-Control: no-cache`, so a
returning browser asks again and gets the notice as soon as the group is
hidden. Every other request passes through untouched. A staff cookie is
ignored.

### 5. Security Considerations

- The allow-list fails closed. The state file fails closed.
- Cookie comparison uses `hmac.compare_digest`.
- Only the bcrypt hash leaves the CMS DB; RWS never sees the password except
  in the login form.
- Login CSRF only logs the victim into the staff view, so it has low impact
  and needs no token.
- Brute force is slowed by the 1 s failure delay. Strong passwords are the
  organizers' responsibility.
- HTTPS is terminated by the host reverse proxy, as today.

### 6. Operations (for the 2026-10-10 contest day)

- Deploy: update RWS first, then run `cmsSetupDB` (Docker `db-init` does
  it) and restart AWS and ProxyService. If the new ProxyService reaches the
  old RWS, the old RWS rejects every group's visibility and every ranking
  freezes until each group is saved again and regenerated.
- Deploy before the public first opens the rankings. An older RWS served the
  index page without `Cache-Control`, so a browser that cached it then may
  keep showing that copy (a scoreboard with no data) after the group is
  hidden, until the copy expires. A reload fixes it; ask the staff to
  reload once after the deploy.
- Before the contest: tick "Hide" and set the staff password on each group
  page. Check the public URL shows the notice and the staff can log in.
- To reveal: untick "Hide". Staff sessions keep working.
- **Rollback:** roll back only the CMS side (AWS and ProxyService). **Never
  roll back RWS while a group is hidden.**
  - An RWS without the guard ignores `visibility.json`: it serves every
    group's stored data to everyone, including what was gathered while the
    group was hidden. (Checked in the final review against the pre-MC-2
    RWS: `GET /<group>/contests/` answers 200 with the data, and
    `PUT /<group>/visibility` answers 404.)
  - The new RWS works with an older CMS: `visibility.json` keeps hidden
    groups hidden, the staff can still log in, and data still arrives, since
    the guard accepts the proxy's authenticated writes. Hiding and revealing
    from AWS stop working; an operator can PUT the visibility straight to
    RWS with the proxy's credentials instead (see `docs/multi-contest.md`).
  - An older AWS cannot create ranking groups: `hidden` is NOT NULL with no
    server default.
- A short operator guide goes into `docs/multi-contest.md`.

## Error Handling

| Situation | Behaviour |
|---|---|
| RWS down when AWS saves | ProxyService retries with backoff (SP1); the old state holds until delivered |
| RWS rejects the visibility PUT (4xx) | WARNING logged; the group's new data is held back until RWS accepts new settings for it, so the ranking stops updating. An outdated RWS (the usual cause) keeps serving the data it already has to everyone. Fix RWS, save the group again (or wait for the next sweep), then press Regenerate |
| Malformed `visibility.json` | Group treated as hidden without password (fail closed), ERROR logged |
| Hidden group without a staff password | Everyone sees the notice; login always fails |
| Wrong password | 1 s delay, notice with error, 401 |

## Testing

- **RWS** (unit tests with Werkzeug's test client, gevent test group):
  - Hidden: every data endpoint, `/events` included, returns 403; `/`
    returns the notice.
  - Login succeeds and fails as specified; the cookie grants access.
  - Changing the password invalidates the cookie.
  - The state persists across a re-created dispatcher.
  - Visible groups are unchanged.
  - `PUT /visibility` without credentials gets 401, and a bad body gets 400.
  - A malformed state file fails closed.
- **ProxyService:** `VISIBILITY` is sent in group mode, before the group's
  data, with the hash payload; it is never sent in legacy mode; it is resent
  on reinitialize.
- **AWS:**
  - Hide and password fields: the password is stored as a hash, kept when
    the field is empty, and removed via the checkbox.
  - Conflicting input is rejected.
  - The password is never rendered back.
- **Migration:** the fork SQL is idempotent (applied twice) and
  `schema_diff_test` stays green.
- **End to end, in Chromium,** with the UI-testing agent against a local
  stack:
  1. In AWS, create a group, hide it and set a password.
  2. The public URL shows the notice, and a data URL returns 403.
  3. The staff log in and see the live ranking with the banner.
  4. Un-hide the group.
  5. The public sees the ranking.
- The full CI run (unit + functional, isolate) stays green.
