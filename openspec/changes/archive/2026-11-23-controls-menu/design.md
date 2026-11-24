# Design

## Context

Where the controls are today:

- **AWS** (`cms/server/admin/templates/base.html`, `#sidebar_header`): a `login_notice` block with «Hello, <name>.» and the log out form, then `fragments/theme_switch.html` and `fragments/lang_switch.html`, each a `div.theme_switch` holding one `<select>` (`#theme`, `#lang`). The sidebar is right aligned and the two selects are left aligned with a fixed margin and a smaller font (`aws_style.css`, `.theme_switch`), which is why they look out of place. The login page and the error page render the same selects without the greeting and the log out form.
- **CWS** (`cms/server/contest/templates/contest.html`): the navbar (`navbar-fixed-top`, Bootstrap 2) holds, as `pull-right` items, the theme select (`macro/theme_switch.html`), the language select (`#lang`, only when more than one translation is offered), the log out form and the «Logged in as …» text. Pages without the contest navbar (for example the list of contests) get `div.theme_switch_corner` (absolute, top 8 px, right 12 px) with the theme select only: they have no language selector today and this change does not add one.
- **RWS** (`cmsranking/static/Ranking.html`): `#UpperPanel` holds the connection status, then `<select id="theme">` and `<select id="language">`; `theme.js` wires `#theme`, `i18n.js` wires `#language`.
- `cms/server/static/theme.js` has an identical copy in `cmsranking/static` served through the ranking's frozen routes, and a test keeps both equal. The AWS language function is inline in `fragments/lang_switch.html` because the login page does not load `aws_utils.js`. Existing tests find the controls by their ids and, in places, by the `.theme_switch` container.
- AWS has guards that fail on any user-visible text outside gettext, so the icon's accessible name must be in the AWS catalog.

## Goals / Non-Goals

**Goals:**
- One look and one behavior for the personal controls on the three servers: a corner icon that opens a menu.
- The AWS sidebar holds only navigation.
- No change to what the controls do (cookies, local storage, the logout request).
- Accessible by keyboard, named in the page language, readable in both themes.

**Non-Goals:**
- An icon library or a font of icons.
- Arrow-key navigation inside the menu, animations, or a popover library.
- A responsive redesign of the AWS sidebar, or moving the AWS clock.
- Per-contest forced language or theme, or new languages.
- Changing the contents of the ranking top panel other than the two selects (the connection status stays).

## Decisions

**Approaches considered** (the owner picked A):

- **A. Native `<details>` plus a tiny shared script.** The browser gives the toggle, keyboard activation and the accessible role; the script only adds closing on Escape and on a click outside. CSS is per server because the three have different stylesheets. Chosen: the smallest thing that meets the request and it keeps working without scripts.
- **B. A hand-made ARIA menu** (`aria-expanded`, focus handling, arrow keys, animation). More control, much more code and tests multiplied by three, no gain for what was asked.
- **C. CSS only** (`:focus-within` or a checkbox). No script, but no Escape and no click outside, which feels broken.

**Decisions of the owner:** the controls go into one icon menu at the top right (not separate icons, not a bar of selects); the same design on AWS, CWS and the ranking.

**My calls** (the design is mine here; the owner can change them):

1. **The icon is a gear** drawn as an inline SVG with `currentColor`, so the theme tokens color it and no extra file or request is needed. A person glyph would suggest an account menu only, and the menu also holds theme and language.
2. **The structure is `<details class="controls_menu"><summary aria-label="…">icon</summary><div class="controls_panel">…</div></details>`.** The existing controls move into the panel unchanged, keeping `#theme`, `#lang`, `#language` and their wiring (`theme.js`, `switchLanguage`, `i18n.js`). Only the wrapper markup and CSS change.
3. **The shared script is `controls_menu.js`**, one file in `cms/server/static` and an identical copy in `cmsranking/static` (same mechanism and same equality test as `theme.js`). It does two things: close an open menu on Escape (returning focus to the summary) and on a click outside. It adds nothing else.
4. **Placement.** AWS has no top bar, so the menu is fixed at the top right of the viewport above the content (high `z-index`); the panel opens downwards and to the left, aligned to the icon. CWS puts it as the last `pull-right` item of the navbar; the pages without a navbar keep the existing corner wrapper. RWS puts it in `#UpperPanel` where the two selects are now.
5. **What stays visible.** On CWS the «Logged in as …» text stays in the navbar: a contestant should always see which account is open. The log out button moves into the menu on AWS and CWS, as the owner asked for the three controls.
6. **Choosing a theme keeps the menu open** (several tries in a row), choosing a language reloads the page as today.
7. **Labels.** One new message per site, «Settings» (accessible name of the icon), added to `aws.po`, `cms.po` and the ranking's `i18n.js` tables in both languages («Ajustes»); the other labels already exist. The log out button keeps its current label.
8. **The sidebar footer overlap** (the last sidebar links under the fixed notice in a short window) is checked on the way; it is fixed here only if the fix is a few CSS lines, otherwise it stays in the issue.
9. **Tests move with the markup.** Tests that find the controls through `.theme_switch` are changed to find them inside the menu; new tests cover: the menu is present and holds the right controls on a logged in AWS page, the AWS login page, a CWS contest page, a CWS page without the navbar, the ranking page; no control outside the menu; the icon's accessible name in both languages; the script (Escape, click outside, focus return) run with node as `theme.js` is; the two copies of the script are equal; the contrast of the icon in both themes through the existing token checks.

## Risks / Trade-offs

- **Overlap on AWS.** A fixed icon at the top right can cover the right end of wide tables or buttons at the top of a page. The icon is small and the content already scrolls under it; if a page shows a real clash, the content gets a little top padding there. To be checked in the screenshots of the main pages.
- **CWS navbar is Bootstrap 2.** A `<details>` inside `navbar-text`/`pull-right` may need a few overrides so the panel is not clipped by the navbar; checked with a screenshot in both themes.
- **Ranking static cache.** The ranking serves its static files with a 12 hour cache, so after the deploy a hard reload is needed to see the menu (same as the earlier ranking changes).
- **A native `<details>` closes only through the script on outside click**, so with scripts disabled the menu stays open until the icon is pressed again. Accepted: the controls need scripts anyway except logout.
- **Guards of AWS.** Any text in the new markup must be gettext; the existing guards will fail otherwise, which is the intent.
