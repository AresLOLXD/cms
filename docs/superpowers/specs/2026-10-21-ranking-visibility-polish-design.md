# Polish of the ranking visibility and freeze pages of RWS (#14)

Date: 2026-10-21. Issue: AresLOLXD/cms#14 (Ola 2). Code: `cmsranking/visibility.py`,
`cmsranking/static/Ranking.css` (the banner), `cms/server/admin/handlers/rankinggroup.py`,
`docs/multi-contest.md` and its Spanish catalog.

## Intent

MC-2 (hiding a ranking group, with a staff view) and MC-2 phase 2 (scheduled hide and freeze
windows) are on main. Their final reviews left a list of rough edges, all on the same pages: the
hidden notice, the staff and freeze banners, the "Congelar ahora" button and the "next:" label of
AWS. The owner chose to do the whole list of the issue except its item 8 (staff login on a visible
group, a change of the MC-2 spec). The work is small things in one area, done together so that the
area gets one review and one deploy.

## Goals

Each item has the number it has in the issue.

1. Staff at a hidden group's `/Ranking.html` get the staff banner, as at `/`.
2. The public at a hidden group's `/Ranking.html` gets the notice page, not a bare 403.
3. The staff banner never stays stale: a page of the staff reloads when the public view of the
   group changes (hidden, shown, frozen, unfrozen), so the banner matches the view.
4. A manual change of the view (a button of AWS) reaches the open pages as soon as RWS receives the
   update from ProxyService, not at the next ping of the event stream (up to 15 s more). A
   scheduled change keeps the ping latency.
5. The hidden notice stops polling a URL that answers 403 on purpose.
6. Two simultaneous clicks on "Congelar ahora" (or any visibility button) change the group once.
7. The "next:" label of AWS names a time only if it changes what the summary says.
9. `#TeamSearch_bg` does not slide under the narrow banner.
10. A flood of anonymous staff logins cannot queue without limit in front of the bcrypt threads.
11. `HEAD /` answers like `GET /` as to `Content-Length`, if that is wrong today.

## Non-goals

- Item 8: staff login on a visible group.
- Scheduled boundaries pushing an event at the instant they pass: they keep the ping latency (at
  most 15 s), which the docs already state.
- Any change to what a hidden or frozen group serves, to the allow-list `FROZEN_ROUTES`, to the
  event cache or to the cookie format.
- Rate limiting by IP, CAPTCHA or lockout for the staff login: item 10 only bounds the queue.

## Anti-goals

- A public page that learns more than it does today. The one new public answer (item 5) says only
  "hidden", "frozen" or "live", which the notice and the banner already tell.
- A reload loop. The page already reloads at most once every 10 s (`RELOAD_INTERVAL` in
  `DataStore.js`); nothing here may send a reload in answer to a reload.

## Design

All of items 1 to 5, 10 and 11 are in `VisibilityGuard.__call__` and its helpers.

**Items 1 and 2.** The condition `path == "/"` in the staff branch and in the hidden-public branch
becomes `path in INDEX_PATHS`, the constant that already names both pages. The notice keeps its
relative links (`staff-login`, and the poll URL), which resolve the same from `/Ranking.html`.

**Item 3.** In `_guard_writes`, the check "the public view changed since the request came"
(`last_change(now) > opened_at`) also applies to a staff client: its stream is told to reload and is
closed at its next write, exactly as a public stream. The existing check for a public client that
lost its staff access stays as it is.

**Item 4.** When a visibility update changes the public view (`_update` compares `public_view(now)` before
and after `update_settings`, whose return value stays as it is), the guard asks the group's event
source to send a `reload` event. That wakes every
open stream; the write guard of items 3 and the existing code then replaces the event by the
guarded reload and closes the stream, for the public and the staff alike. It is only a wake-up, so
its content does not matter, and it is cached like any event (a client that reconnects with an older
`Last-Event-ID` gets a reload, which the 10 s limit of the page makes harmless). The guard reaches the event
source through the application it wraps (`RoutingHandler.event_handler`, a `DataWatcher`), and does
nothing if the application has none (the test doubles).

**Item 5.** The guard answers `GET /visibility` to anybody, with 200, `Cache-Control: no-store` and
a body of one word: `hidden`, `frozen` or `live`. The notice polls `visibility` instead of `config`
and reloads when the answer is not `hidden`. `PUT /visibility` is unchanged. Old notice pages
(open before the deploy) poll `config` and keep working until they reload.

**Item 10.** `_login` counts the staff-password checks that are waiting or running; past
`LOGIN_MAX_PENDING` it answers 503 with `Retry-After: 1` at once, without reading bcrypt, instead
of queuing. The count goes up before `apply` and down in a `finally`.

**Item 11.** Verify with a real server what `HEAD /` and `HEAD /Ranking.html` return, for the
notice and for the static page. If a header is missing for the notice (our response), fix it; if
it is missing for the static file handler (pre-existing, not ours), record it in the issue and do
not change that handler.

**Item 6 (AWS).** `RankingGroupHandler._post_sync` reads the group with a row lock
(`SELECT ... FOR UPDATE`), so that a second request waits for the first to commit, reads the new
state, and is refused by the checks that already exist ("The ranking is already frozen."). The
sequential double click is already refused today; only the simultaneous one is the race.

**Item 7 (AWS).** `visibility_view` names, as the next change, the first future time at which the
state that its summary shows (hidden or not, frozen or not, by the raw windows) differs from the
state now, instead of the first future time of any field. An end without a start, or an end that
the other window hides, no longer shows.

**Item 9.** Check on a real page, with the Chromium of the machine in headless mode, whether
`#TeamSearch_bg` and its panel reach under the banner on a 360 px wide screen. If they do, add it
to the rule of the banner's `<style>` that already lifts `#InnerFrame` and `#UserDetail_bg`. If the
browser cannot run here, add the rule anyway (it is the same one) and say it was not seen.

**Docs.** The paragraph of `docs/multi-contest.md` that says an open page reloads "within about 15
seconds" becomes: within about a second for a change made by hand, within about 15 seconds for a
scheduled one; the staff pages reload too; the notice checks every 15 seconds. Its Spanish catalog
follows. The `HEAD` result goes in a comment on the issue, not in the docs.

## Calls I made (the owner can overrule)

- The wake-up for item 4 is the `reload` event of the existing event source, not a new channel and
  not a shorter ping (a 5 s ping is three times the idle traffic of every open page).
- Item 5 adds a public `GET /visibility` instead of making `config` answer 200 while hidden
  (which would leak the data `config` guards).
- Item 10 answers 503 past `LOGIN_MAX_PENDING = 16`: with two threads and about 250 ms of bcrypt,
  16 waiting checks are about two seconds of queue; more than that is a flood, not a team of staff.
- Item 7 is computed from the state, not from a list of rules per field, so that it stays right
  when the windows change.
- Item 6 locks the row on every edit POST, not only on the button actions: one rule, and an edit
  of one group takes milliseconds.

## Tests

The tests of `cmstestsuite/unit_tests/cmsranking/test_visibility.py` run in the gevent group; the
AWS ones in `cmstestsuite/unit_tests/server/admin/rankinggroup_test.py` in the asyncio group. One
test per behavior: items 1 and 2 (the staff banner and the notice at `/Ranking.html`), 3 (a staff
stream gets the reload on a change, and a change of the staff password alone does not reload it), 4
(a stream opened before a manual change gets the reload before any ping, using the existing
real-event-stream test fixtures with a long ping), 5 (`GET /visibility` for the three views, and the
notice script polls it), 10 (past the limit, 503 without calling bcrypt; the count comes back down
after a failure), 7 (the unit cases of `visibility_view`), 6 (the handler reads with the lock; a
simulated second request sees the first's change).

## Not verified / risks

- The wake-up of item 4 and the staff reload of item 3 are tested against the repository's event
  stream fixtures, not in a browser.
- The size of `LOGIN_MAX_PENDING` is a judgement, not a measurement.
- Item 9 depends on whether a headless Chromium runs on the development machine.
