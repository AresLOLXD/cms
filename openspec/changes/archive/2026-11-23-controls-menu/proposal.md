# Proposal

## Why

The owner looked at the deployed omi.16 and found the three personal controls of the AdminWebServer ugly: the log out button, the theme select and the language select are stacked at the top of the left sidebar, as controls of different widths that are left aligned inside a right aligned sidebar. They push the navigation down on every page. The contestant site and the ranking carry the same two selects in other places (a navbar and a top panel), so the controls look different on each server. Issue #82, requested as the most urgent item.

## What Changes

- AWS: the log out button, the theme select and the language select leave the sidebar header. They go into one menu opened by a small icon fixed at the top right of the page. The sidebar keeps only navigation. The login and error pages get the same menu without the log out button.
- CWS: the theme select, the language select and the log out button of the contest navbar move into the same kind of menu. The pages without the contest navbar keep their corner placement, now holding the menu. The «Logged in as …» text stays in the navbar.
- RWS: the theme select and the language select of the top panel move into the same kind of menu.
- One shared behavior for the three: a native `<details>` element, so the browser provides opening, keyboard access and the accessible role, plus a small shared script that closes the menu with Escape or a click outside. The script is one file copied into the ranking like `theme.js`.
- Nothing changes in how the choices work: the `aws_language` cookie, the contestant language cookie, the ranking language and theme in local storage, and the log out form with its XSRF field stay as they are.
- Known cosmetic defect to look at on the way, only if the fix is a few CSS lines: the last sidebar links overlap the fixed footer in a short window.

Not built: an icon library (three inline SVG glyphs are enough), arrow-key navigation inside the menu, a responsive redesign of the AWS sidebar, per-contest forced language or theme.

## Capabilities

### New Capabilities
- `controls-menu`: where the log out, theme and language controls of AWS, CWS and RWS live (one corner icon menu), how the menu opens and closes, and that every control keeps working from inside it.

### Modified Capabilities
- `ranking-language`: the requirement "A switch chooses the language" says the switch sits in the top bar next to the theme switch; it will say it is in the controls menu of the top bar.

## Impact

- AWS: `cms/server/admin/templates/base.html`, `fragments/theme_switch.html`, `fragments/lang_switch.html`, `static/aws_style.css`, the Spanish catalog `cms/locale/es/LC_MESSAGES/aws.po` (the menu's accessible name), and the AWS guard tests that look at the sidebar header.
- CWS: `cms/server/contest/templates/base.html`, `contest.html`, `macro/theme_switch.html`, `static/cws_style.css`, `cws_dark.css`, the contestant catalog `cms.po`.
- RWS: `cmsranking/static/Ranking.html`, `Ranking.css`, `i18n.js` (the menu's label in both languages), the copy of the shared script and its route.
- Shared: a new script in `cms/server/static` and its identical copy in `cmsranking/static`; the tests of the theme and language switches that find the controls by their container.
- No database, RPC or configuration change. Deploy: CMS and ranking images (up.sh options 3 and 4); the ranking caches its static files for 12 hours, so a hard reload is needed.
