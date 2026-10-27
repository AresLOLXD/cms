# RWS: a scoreboard that stays readable with many tasks, and on a phone (issue #55) — Design

Date: 2026-10-27. Decided by the owner (the three questions below) after two
design reviews and a tie-break; the rest are the author's calls, marked
**(call)**.

## Problem
`cmsranking/static/Scoreboard.js` gives every task its own column group
(`make_cols`: 3 `<col/>` per task, 10 per name column) in a fixed-layout table
whose width is proportional to the screen. With ~90 tasks in one ranking every
task column is a few pixels: headers become stubs, scores overflow, and the
First/Last Name columns shrink so a name wraps onto 3-4 lines (only ~10
contestants fit on screen). With about 10 tasks it looks fine. In production
the full name was loaded into First Name with Last Name empty (a dead column),
and usernames like `PREFIX-STATE-N` (13-14 chars) wrap in the 8em column. The
page has no viewport meta tag, so a phone lays it out at 980 px (readable only
after zoom).

## Decisions of the owner
1. **Layout:** above a task threshold the ranking starts collapsed to the
   contest totals, with a **button that expands the tasks** (min width per
   column, horizontal scroll, sticky Name column).
2. **Names:** one "Name" column (first + " " + last) and a wider Username
   column; this changes every ranking, also the small ones.
3. **Phones:** add `<meta name="viewport" content="width=device-width,
   initial-scale=1">` now, and make the page work at phone width.

## Goals / non-goals
Goals: a ranking with ~90 tasks is legible on a projector (collapsed) and on
demand per task (expanded); a name and username fit in one or two lines;
the 10-task look stays as it is except the name columns; the page is usable
at 412 px wide.
Non-goals: per-contest collapse; persisting the toggle; a date/zoom control;
sticky rank/username/total columns; changes to the data model, the event
stream, `visibility.py` or any server code; new static files (the
`FROZEN_ROUTES` allow-list in `visibility.py` is test-checked: do not add
files); animations.

## Design
### A. The table (`Scoreboard.js`, `Ranking.css`, `Ranking.html`)
- **Threshold (call):** one constant `COLLAPSE_TASKS = 20` in Scoreboard.js,
  compared with the TOTAL number of tasks of the ranking (all contests). At
  20 tasks or fewer nothing new is switched on (no button, no min widths).
  Check at the end that 20 is right at a projector width (1835 px): at 20 tasks
  the task columns are ~44 px each.
- **Collapsed:** the task columns are NOT generated (not hidden with
  `display:none`: each task makes 3 `<col/>` and only the first has class
  `task`, the other two would still take width). `make_cols`, `make_head` and
  `make_row` leave the task cells out; contest total columns stay. Fewer cells
  also means less work for `score_handler`. A `tasks-collapsed` state is kept
  in a variable and rebuilds the table when toggled (the rebuild path of
  `Scoreboard` already exists for task/contest changes: reuse it).
- **Button:** one `<button id="Scoreboard_tasks_toggle" hidden>` before the
  table in `#InnerFrame` (Ranking.html), shown only above the threshold, text
  "Show tasks" / "Totals only". Do not put a `display` rule on its id (it would
  defeat `[hidden]`).
- **Expanded:** `min-width` of the table = number of `<col>` × 15 px + 240 px
  (the JS sets a CSS variable `--cols`; border-spacing adds 2 px per col, so the
  floor is per column, not per cell), horizontal scroll inside `#InnerFrame`,
  sticky `th.name, td.name { position: sticky; left: 0 }` and sticky header row
  (`thead th { position: sticky; top: 0 }`) with OPAQUE backgrounds (the row
  hover colour is rgba: give it an opaque equivalent for the name cell); the
  corner header cell has the highest z-index of the table but stays below the
  `visibility.py` bar (z-index 1000). `#InnerFrame` must stay the scroller and
  keep its `bottom` (the staff bar sets `bottom` on `#InnerFrame`,
  `#UserDetail_bg` and `#TeamSearch_bg`). To make sticky work set
  `#InnerFrame { padding: 0 }` and `#Scoreboard { width: calc(100% - 6em);
  margin: 20px 3em }` (rendering at rest is the same; a sticky left of 0 sticks
  at the content edge and the 3em padding would let scrolled cells show through).
  Do not use `background-attachment: fixed` for the sticky backgrounds (iOS
  ignores it). When the view collapses and `sort_key` starts with `t_`, switch
  the sort to the last non-task header.
- **Names:** replace the First/Last Name columns by one `name` column of 20
  units (the same total width as the two today): `make_cols`, `make_head`,
  `make_row`, `update_user` (L~427-428), the click selector
  (`td.f_name, td.l_name, td.user_id` -> `td.name, td.user_id`) and the three
  CSS rules that style `f_name`/`l_name` (Ranking.css ~L450-452, 474-477,
  482-484: find them by grep). `overflow-wrap: break-word`. The sort tiebreak
  stays last-name then first-name (the data keeps both fields; only the
  display joins them). `Overview.js` and `UserDetail.js` already join them.
- **Username:** fixed `col.user_id { width: 13em }` (measured: 14 capitals fit
  in 12em; 8em wraps even 13 characters).
- **User detail:** `#UserDetail_navigator { max-height: 60vh; overflow-y: auto }`
  so ~90 task rows do not push the charts 2000 px down (10 tasks are unaffected).

### B. Phones (`Ranking.html`, `Ranking.css`) **(call: all of B)**
Add the viewport meta tag, and make the layout work below `max-width: 800px`
(one `@media` block at the end of Ranking.css; nothing changes above 800 px).
Today `#LogoPanel`, `#UpperPanel`, `#TimeView` and `#SidePanel` are absolutely
positioned (SidePanel 220 px wide) and `#InnerFrame` starts at `left: 220px`;
`#UserDetail` is 920 px with two `<canvas width="920">`; `#TeamSearch` is 940 px
and `#TeamSearch_input` sits at `left: 250px`. At phone width:
- the page becomes a single column: logo/status/time view/side panel stack
  above the table (`position: static`, full width, side panel without its fixed
  top/bottom, its content limited in height with scroll), `#InnerFrame` is
  `position: static` with the full width and its own horizontal scroll;
- `#UserDetail` and `#TeamSearch` overlays use the full viewport width (no
  fixed 920/940 px); the canvases scale with CSS (`max-width: 100%; height:
  auto`); the detail header stacks;
- `#TeamSearch_input` is reachable (not at `left: 250px`).
Keep the visibility notice/bar pages untouched (the notice template already has
a viewport tag; the staff bar must still work: check it at 412 px).
Do not redesign the desktop look and do not add JavaScript for this unless a
CSS-only approach cannot do it (say so).

## Verification (no JS test runner exists; ESLint is not installed)
- `node --check` on every changed JS file; `git diff` shows no change in
  non-RWS code.
- Existing Python tests of cmsranking pass (`cmsranking/` is the gevent group:
  run each test file alone). If `test_visibility.py` pins the static route
  list, it must still pass.
- A throwaway demo seeder, NOT committed, in the scratch dir: PUT to a local
  `cmsRankingWebServer` (default credentials `usern4me`/`passw0rd`, first
  authenticated PUT creates a group): contests, tasks (float `max_score`, int
  `score_precision`, `extra_headers: []`, `score_mode: "max"`), 130 users with
  long Spanish names and one with an empty last name, `PREFIX-STATE-N`
  usernames, teams = flag stems like `AGU`, submissions and subchanges. Keys go
  through `encode_id`. Make a 10-task and a 90-task ranking.
- Real-browser check with the flatpak Chromium (`org.chromium.Chromium`),
  headless with `--remote-debugging-port` and `--filesystem` for the output dir,
  driven by a ~40-line CDP script (system python has `websockets`), screenshots
  at 1835x900, 900x700 and 412x915 (`Emulation.setDeviceMetricsOverride` with
  `mobile: true`): 10 tasks (as today except names), 90 tasks collapsed,
  90 tasks expanded scrolled by 1200 px (sticky Name), a live update (PUT a
  subchange) while expanded, the user detail panel and the team search overlay
  at 412 px. Checks via `Runtime.evaluate`: a name is at most 2 lines, at least
  25 rows are visible at 1835x900 collapsed, the sticky cell's `left` equals the
  frame's `left` when scrolled, no horizontal page scroll at 412 px outside
  `#InnerFrame`. Attach the screenshots' paths in the report. Firefox and
  Safari are NOT checked (say so in the docs/report).

## Docs
`docs/multi-contest.md` or the RWS page that describes the scoreboard: one
short paragraph on the totals-only view above 20 tasks and its button, in
English with the Spanish `.po` by hand, only if such a page describes the
scoreboard columns; otherwise no docs.

## Tasks
1. Part A (table, names, username, navigator, button) + the seeder and the
   screenshots at 1835 px.
2. Part B (viewport and the `@media` block) + the screenshots at 900 and 412 px.
3. Docs (if any).
Review after each of 1 and 2 against the screenshots; a fresh final reviewer
for the whole branch.

## Risks
Opaque sticky backgrounds (`tr:hover` is rgba); the `bottom` set by
`visibility.py`; the 20-task threshold at the projector; a rebuild of the table
while a live update arrives (`score_handler` walks the cells); `FROZEN_ROUTES`.
