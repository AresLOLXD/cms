## Why

CWS, AWS and RWS have fixed light colors, and nothing follows the system preference. The organizers asked (issue #30) for a light or dark theme across the whole system, ranking included, that each person can pick. Contests run for hours at night and in dim rooms, and the ranking is shown on screens of every kind.

## What Changes

- Every page of CWS, AWS and RWS follows the system theme by default (`prefers-color-scheme`), with a manual switch **Sistema / Claro / Oscuro** on each server. The choice is kept in the browser, per server, and applies before the first paint (no flash of the wrong theme).
- CWS: the main surfaces (page, navbar, tables, forms, buttons, alerts, task statements) get a dark look from a new overlay stylesheet; Bootstrap itself is not touched. The contest-list link color that does not pass contrast is fixed.
- AWS: the page shell, tables, forms, banners and the code highlighter (Prism) have light and dark sets; the inline colors in templates move to the tokens.
- RWS: the scoreboard, score cells (including the up/down animations), side panel, user detail frame, banners, the notice and staff-login pages built by `cmsranking/visibility.py`, and the icons that assume a light background follow the theme.
- Charts (AWS resources page, RWS user detail chart and overview) stay on a light panel in dark theme, exactly as drawn today.
- Text/background pairs of both themes in the three servers are checked by a unit test for WCAG AA contrast.
- Out of scope, stays open in #30: Spanish/English work (AWS gettext, RWS catalog, CWS catalog gaps). Out of scope here: updating Bootstrap and the other frontend libraries (separate follow-up, see design.md).

## Capabilities

### New Capabilities
- `color-theme`: the theme choice (system, light, dark), where it is kept, how it applies to each server's pages, and the contrast it must meet.

### Modified Capabilities

## Impact

- New: `cms/server/static/theme.js` (served to CWS and AWS), a copy in `cmsranking/static/` (listed in `FROZEN_ROUTES`), a dark overlay stylesheet for CWS, and token blocks in `aws_style.css` and `Ranking.css`.
- Edited: `cms/server/contest/templates/contest.html`, `base.html`; `cms/server/admin/templates/base.html` and the templates with inline colors; `cmsranking/static/Ranking.html`, `Ranking.css`; `cmsranking/visibility.py` (its inline-CSS pages).
- No database change, no new Python dependency, no server-side setting.
- Tests: contrast test per server, copy-identical test for `theme.js`, `FROZEN_ROUTES` test stays green; existing Ranking.css tests (z-index, `bottom` blocks) must keep passing.
