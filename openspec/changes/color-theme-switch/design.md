## Context

Observed (read-only recon, counts approximate):

- **CWS** (`cms/server/contest/`): `static/css/bootstrap.css` is Bootstrap 2.x with about 824 color occurrences and no variables; `static/cws_style.css` has about 39. `templates/base.html` loads both. The navbar and the language `<select>` live in `templates/contest.html` (the language select only renders with more than one translation). Only `icon-chevron-right` and `icon-chevron-down` of the glyphicons sprite are used (`task_submissions.html`). One inline color: `contest_list.html:11`, `#adadad` (about 2.3:1).
- **AWS** (`cms/server/admin/`): `templates/base.html` loads `aws_style.css` (about 71 colors, no media queries), `reset.css`, `prism.css` (light) and the jqPlot CSS. Inline colors: `users_bulk_remove.html`, `task.html:447`, the archive banner in `base.html`, `macro/activity.html:29`, and `<style>` blocks in `export.html` and `contest_users_import.html`. jqPlot (canvas) is used only in `resources.html`. The sidebar `login_notice` is where the switch fits.
- **RWS** (`cmsranking/static/`): `Ranking.css` has about 149 color occurrences, uses `var(--cols)` and `var(--rws-banner)`, has hard-coded score classes and 8 vendor-prefixed keyframes; `Ranking.html` has inline footer colors. Charts are a canvas helper (`Chart.js`) and Raphael SVG (`Overview.js`). `cmsranking/visibility.py` builds three pages with inline CSS (hidden notice, staff login, staff banner). RWS serves only `cmsranking/static`, and a test requires every top-level entry of that directory to be listed in `FROZEN_ROUTES`.
- Tests that must keep passing: the ones reading `Ranking.css` (maximum z-index below 1000; the first `#InnerFrame {`, `#SidePanel {` and `#UserDetail_bg {` blocks keep `bottom`).
- CWS and AWS share `cms/server/static/` through `static_url("static", ...)`; RWS does not.

## Goals / Non-Goals

**Goals:**
- Light, dark and system theme on all three servers, chosen by the person, kept per browser, applied without a flash.
- Contrast AA in both themes, checked without a browser.

**Non-Goals:**
- The Spanish/English work of #30.
- Updating Bootstrap or any other frontend library (see Follow-up).
- Forcing a theme per contest or per deployment; any server-side setting or cookie.
- Dark palettes or redraws for the charts.
- New icon assets beyond the few that break on a dark background.

## Decisions

All of these are my calls unless marked as the user's.

1. **Tokens per server, one pattern (mine).** Each server's stylesheet defines its colors as CSS custom properties: the light set on `:root`, the dark set twice, once under `@media (prefers-color-scheme: dark)` guarded by `:root:not([data-theme="light"])` and once under `:root[data-theme="dark"]`. A page sets `data-theme` only when the saved choice is light or dark; with System it sets nothing and the media query decides. `color-scheme: light dark` is declared so native controls and scrollbars follow.
2. **Storage and no flash (mine).** The choice is saved in `localStorage` under `cms.theme` with values `light`, `dark` or `system` (default system), always inside `try/catch`. A small blocking script in `<head>` reads it and sets `data-theme` before first paint. Alternative rejected: a cookie (the issue suggested one for CWS/AWS) needs server code and a request round trip for nothing, since the server never needs to know.
3. **One script, two copies (mine).** `cms/server/static/theme.js` serves CWS and AWS. RWS cannot reach that directory, so `cmsranking/static/theme.js` is a copy, kept identical by a unit test, and listed in `FROZEN_ROUTES`. The script has two entry points: the early apply (in `<head>`) and the switch wiring (a `<select>` with `id="theme"` handling `change`). Alternative rejected: making RWS serve `cms/server/static` (changes what a frozen/hidden ranking exposes).
4. **Switch placement (mine).** A `<select>` with the options «Sistema», «Claro», «Oscuro». CWS: in the navbar, outside the `{% if available_translations|length > 1 %}` block so it shows with one language. AWS: in the sidebar header next to the login notice. RWS: in the top bar of `Ranking.html`. The pages of `visibility.py` follow the saved theme by including the same early script.
5. **CWS overlay (the user's, with the follow-up).** `bootstrap.css` is untouched. A new `cws_dark.css`, loaded after `cws_style.css`, redefines the surfaces and text colors only under the dark selectors (page and navbar, tables, forms, buttons, alerts, wells, labels, task statement and submission views). `cws_style.css` moves its own colors to tokens. The user said that updating Bootstrap would be better, and agreed to keep the overlay meanwhile.
6. **Charts keep a light panel (the user's).** In dark theme, the chart containers get a light background; nothing in `Chart.js`, `Overview.js`, `UserDetail.js` or jqPlot changes.
7. **Icons (mine).** The two CWS chevrons use `filter: invert(1)` in dark theme. RWS `tick_*.png` and `close.png` are swapped per theme with CSS (`filter` first; if a PNG does not read well, the alternate asset is added then).
8. **RWS animations and score cells.** Colors inside the 8 keyframes and the score classes become tokens; the up/down colors are chosen so that each pair stays distinguishable from the other and from the background in both themes. The vendor-prefixed duplicates are kept as they are, only with `var()`.
9. **Contrast test (mine).** One pytest module reads the token block of each stylesheet (light set and dark set), takes a declared list of foreground/background pairs per server (text on page, link, status colors, score-cell colors, banner text), computes WCAG contrast and asserts 4.5:1. The pairs live in the test next to the names of the tokens, so adding a token without a pair is visible in review. Screenshots are not taken by tests; the person reviewing opens the pages.
10. **Inline colors move to the tokens.** The five inline colored `style=` attributes and the `<style>` blocks in AWS become classes using tokens; the `#adadad` link in CWS gets a token that passes AA.

## Risks / Trade-offs

- The CWS overlay will leave rough edges in rarely used Bootstrap components. Accepted until Bootstrap is upgraded; the covered surfaces are listed in the spec and each gets a test of its token pair, not of the pixels.
- Bootstrap's 824 colors cannot be verified by a unit test: unlisted components may be unreadable in dark theme. Reviewer and user open the pages; this is stated as not verified until they have.
- RWS has the most colors (about 190 between CSS and JS). Colors computed in JS that are not charts (if any are found while implementing) are listed as a deviation, not silently changed.
- Two copies of `theme.js` can drift; the identical-copy test prevents it.
- `localStorage` is per origin: the same server reached by two hostnames keeps two choices. Accepted.

## Follow-up (not part of this change)

- **Update the frontend libraries** (requested by the user while this change was planned): Bootstrap 2.x (CWS), jQuery 3.6.0 (CWS and AWS from `cms/server/static/jq`, and RWS from the Google CDN with a local fallback), jqPlot, Prism, Raphael, eventsource and explorercanvas (RWS). Bootstrap 2 to 5 renames markup classes and the JavaScript plugin API, so it is its own project; Bootstrap 5.3 has native dark mode (`data-bs-theme`), which would replace `cws_dark.css`. To be tracked as its own issue with an inventory of versions and known advisories.
