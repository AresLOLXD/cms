# AWS: show the current UTC time and the time zones, and label every time (issue #50) — Design

**Date:** 2026-10-24
**Status:** Decisions by a read-only panel (ui-designer); the owner approved the
consolidated design.

## Facts (the issue overstates the "server local zone" part)
In AWS, `local_tz` is used in ONE place: `rankinggroup.py:191` (the "= local"
text next to freeze/hide times). The shared `format_datetime` filter has 4 AWS
call sites (`macro/activity.html:33,34,69,70`) and they always get the
contest's zone (`contestactivity.py:265` sets `timezone`). Everything else is
raw naive UTC from the database (6 `.timestamp` prints and 4 group cells in
`groups.html`). The browser's zone appears only in the JS formatters. So AWS is
effectively "UTC, except the activity pages and the JS times": fix by labelling
and unifying, not converting.

## Non-goals
A zone converter or zone-aware inputs for group times; per-value tooltips with
a second zone; browser-zone detection; changes to the shared babel filters
(they serve contestant pages); Intl/IANA handling in JS; a live preview while
typing; a server-time sync poll; a per-admin time zone; `repr_time_ago`;
changing the zone of any input field (riskiest thing for an operator entering
group times on contest day).

## Decisions

### 1. A ticking clock in the sidebar
- One `<div id="clock">` at the top of `#sidebar` in `base.html`, outside the
  `admin is undefined` branch so it shows on every page (login and error too).
- JS `update_clock()` reuses the pattern of `update_remaining_time`
  (`aws_utils.js` ~587): server time at page load plus elapsed client time, so a
  wrong client clock does not matter (~12 lines). `base.html` registers the
  remaining-time interval only inside the contest branch: register the clock
  tick outside it.
- Rows: UTC; the server zone; the contest zone only if `contest.timezone` is
  set, resolvable and different from the server's. Example:
  `America/Mexico_City (CST, UTC-06:00) 08:05:12`.
- Data: a Jinja global `clock_zones(timestamp, contest)` registered in
  `cms/server/admin/jinja2_toolbox.py`, implemented in
  `cms/server/admin/formatting.py`, returning `[{label, offset_seconds}]` from
  the `timestamp` and `contest` params base.html already has (no handler
  changes, no StrictUndefined risk on error pages). Server zone name from
  `get_system_timezone()` (`cmscommon/datetime.py`); abbreviation/offset from
  `now.replace(tzinfo=utc).astimezone(local_tz)` with `%Z`/`utcoffset()` as
  `rankinggroup.local()` does. JS renders labels with `.text()`
  (contest.timezone is admin-typed). `# shortcut: a DST change while the page
  is open is ignored` (offset fixed at load).

### 2. Labels: an AWS-only filter
- `format_utc_time` in `admin/formatting.py` (registered in the AWS toolbox):
  `YYYY-MM-DD HH:MM:SS UTC`, no microseconds, "" for None. Applied at:
  `macro/submission.html:87`, `submission.html:32`,
  `fragments/user_test_row.html:17`, `macro/question.html:20`,
  `participation.html:193`, `announcements.html:45`, `groups.html:56,57,64,65`
  (re-locate the lines).
- Leave input fields alone (`group_settings.html`, `participation.html:111`):
  their labels already say UTC and the values must stay parseable.
- Activity pages keep the contest zone, and name it: the notes at
  `contest_activity.html:9` and `participation.html:139` become "Times are in
  the contest's timezone ({{ timezone }})".
- JS: `format_time`, `format_datetime` and the "today" check of
  `format_time_or_date` (`aws_utils.js` ~770-820) use `getUTC*` and append
  " UTC" (covers notifications and the overview log).
- Fix the tooltip at `contest.html:118` ("Timezone of the server" is really the
  zone contestants see).

## Tests
New `cmstestsuite/unit_tests/server/admin/formatting_test.py`:
`format_utc_time` with microseconds and with None; `clock_zones` with
`local_tz` patched to `timezone(timedelta(hours=-6), "XYZ")` (pattern in
`rankinggroup_test.py` ~371), contest row present/absent/unresolvable. Extend a
real-`AWS_ENVIRONMENT` render test (e.g. `contestactivity_test.py`
`PageTestBase`) to assert `id="clock"` and "contest's timezone (". JS: a
throwaway node probe of the formatters and the clock (report, don't commit);
`node --check`.

## Docs (en + es .po by hand)
One sentence in the Clock item of `docs/contest-day.md` (~35); half a sentence
in the "Enter the times in UTC" paragraph of `docs/multi-contest.md` (~297).
AWS strings stay English.

## Out of scope for this branch
Another lane (#74/#59) edits `aws_utils.js` (the notification URL and
`CMS.AWSUtils` constructor) and `base.html` (the constructor call): keep your
edits there local (the clock and the formatters) so the merge is trivial.

## Tasks
1. formatting.py + filter + global + tests. 2. Templates + labels + render
test. 3. Clock + JS formatters + probe. 4. Docs + docs check.
