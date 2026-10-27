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

## Revision 1 (builder check, 2026-10-27)
These points correct or complete the sections above; where they disagree,
this section wins.

### Corrections (facts in the code)
- **There is no rebuild path to reuse.** `Scoreboard.init` binds only user,
  score, rank and select events (no contest/task handlers) and `generate()`
  runs once: it re-binds the delegated clicks (a second run would make
  `td.sel` toggle twice), `make_body` pushes onto `user_list` without clearing
  it and it resets `sort_key`. The toggle needs a new `render()`: columns,
  head, `tbody.empty()`, `user_list = []`, `make_body`, then re-apply the
  `sort_key` classes; it is synchronous, so it cannot race `score_handler`.
  Live task/contest creation stays ignored, as today. `create_user` calls
  `make_row`, so `make_row` must read the same collapsed variable. Places that
  assume task columns: the `td.task` branch of `score_handler`, the `th.score`
  click, `sort_key` of the form `t_<id>`, `make_cols`, `make_head`, `make_row`,
  `create_user`. Rank, `move_user` and UserDetail do not depend on them.
- **Threshold:** use `DataStore.task_count` (the total; everything is loaded
  before `Scoreboard.init`, see Ranking.js).
- **Units:** the auto-width units are 20 + 3T + 4C + 5 (the last only if there
  is more than one contest); fixed: sel 2em, rank 48px, user_id, team
  2em+20px. Name = 20 units = 10 + 10, so the totals do not change; user_id
  from 8em to 13em takes about 68 px, so task columns get ~10 % narrower in
  every ranking. The `15 px per col + 240 px` min-width is approximate (about
  13 px per unit, ~43 px per task cell at 90 tasks): calibrate it with the
  screenshots.
- **Existing tests read Ranking.css** (cmstestsuite/unit_tests/cmsranking/
  test_visibility.py): the maximum z-index must stay below 1000; the first
  `#InnerFrame {`, `#SidePanel {` and `#UserDetail_bg {` blocks must keep their
  `bottom` (do not merge them into selector lists); sticky z-index values must
  stay below 400 (the overlays are 400/401/500; `#InnerFrame` creates no
  stacking context). `FROZEN_ROUTES` is checked against top-level static files
  only, so no new files means no problem.
- **Docs:** no page describes the scoreboard columns: task 3 is "no docs".

### Calls of the author (a builder may refine them)
- Scope the sticky and opaque rules to `#Scoreboard.expanded`, so the 10-task
  look stays and the UserDetail navigator (also `td.name`) is not hit.
- The name text is `trim(first + " " + last)` in one helper shared by
  `make_row` and `update_user`.
- Collapsing while `sort_key` is `t_*` switches to the global total if it is
  shown, else to the last contest.
- Sticky name cells get `z-index: 1` (`td.score` has opacity and would paint
  over them), a flat `#F6F6F6` background and about `#E6E6E6` on hover; the
  separate-borders table lets scrolled rows show through the sticky header and
  name: fix with a box-shadow or `border-spacing: 0` in the expanded state only.
- Toggle button: `aria-expanded` and `aria-controls`.
- Phone block additions: `#InnerFrame` and `#SidePanel` become `static` (the
  banner `bottom` rules shift `position: relative` elements); the fixed staff
  banner covers the end of the page, so `body { padding-bottom:
  var(--rws-banner, 0px) }` (find how the banner sets its height and use it);
  the overlays (`#UserDetail_bg`, `#TeamSearch_bg`) become `position: fixed`
  because the page now scrolls; `#SidePanel` needs an explicit `height`
  (`#Overview { height: 100% }` would collapse and Raphael would get 0); the
  close buttons (`right: -10px`) move inside so they do not cause horizontal
  page scroll; `.item { width: 25% }` becomes 50 % or 100 %;
  `#UserDetail_header { flex-direction: column }` and the navigator
  `width: auto`; `#UserDetail_submissions { overflow-x: auto }`;
  `#TeamSearch_input` is `fixed` inside the top bar with `font-size: 16px`
  (no iOS zoom). The sticky header does not work on a phone (the page scrolls,
  not the frame): accepted; `scroll_into_view` becomes a no-op there.
- Risks to cover in the plan: `generate()` is not idempotent; 20 tasks with
  `score_precision` 2 ("100.00") overflow ~43 px cells; the sticky stacking
  artifacts; a rebuild loses the scroll position, `selected` row classes and
  running animations (restore what is cheap, say what is not); the visible
  changes to rankings of 20 tasks or fewer are only the Name column, the 13em
  username and the `#InnerFrame`/`#Scoreboard` margin/padding (verify the
  right-edge gap is unchanged); the CSS rules for `f_name`/`l_name` (hover,
  bold) must all move to `name`.

### Open points for the owner (defaults used if not answered)
1. At 412 px the 3em margins plus the fixed columns leave ~80 px for the
   auto units, so even a 10-task table is unusable. Default: below 800 px every
   ranking gets the expanded min-width and horizontal scroll (this contradicts
   "20 tasks or fewer: no min widths" only on phones).
2. A 20-unit Name (at least 300 px) cannot be sticky at 412 px. Default: no
   sticky Name below 800 px.
3. Charts in the user detail scale by CSS (the 10 px labels become ~4 px).
   Default: CSS only; the alternative is redrawing with the real canvas width.
4. Collapsed with a single contest: Name takes 20 of 24 units (~830 px) beside
   a ~200 px total. Default: accept.
