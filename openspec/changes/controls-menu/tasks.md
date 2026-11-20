# Tasks

## 1. The shared script

- [ ] 1.1 `controls_menu.js` in `cms/server/static` and its identical copy in `cmsranking/static` (with the route the ranking uses for `theme.js`): close an open menu on Escape (focus back to the summary) and on a click outside; tested with node like `theme.js` (Scenarios "Close with Escape", "Close by clicking outside"), plus the equality test of the two copies
- [ ] 1.2 The shared gear icon as inline SVG (`currentColor`) and the base menu CSS tokens (surface, border, text) added to the theme token sets of each server where they are needed; contrast checked through the existing token tests (Scenario "Contrast of the icon and the menu")

## 2. AWS

- [ ] 2.1 `base.html`: one `details.controls_menu` fixed at the top right holding the greeting, the log out form (same XSRF field), `fragments/theme_switch.html` and `fragments/lang_switch.html`; `#sidebar_header` keeps only the titles; the login and error pages get the menu without greeting and log out; the new «Settings» message in `aws.po` («Ajustes») (Scenarios "A logged in AWS page", "The AWS login page", "The icon has a name in the page language")
- [ ] 2.2 `aws_style.css`: menu and panel layout in both themes, the panel opens over the page without moving it, the old `.theme_switch` and `.login_notice` rules removed if unused (Scenario "The menu does not move the page"); look at the sidebar footer overlap and fix it only if a few CSS lines do it
- [ ] 2.3 AWS tests: update the tests that find the controls through the old container (`theme_aws_test.py`, `lang_switch_test.py`, `translated_chrome_test.py`, the guard helper if it names it), add the placement tests; the AWS guards and the pseudo-render stay green (Scenarios "Choosing a theme in the menu", "Choosing a language in the menu", "Logging out from the menu")

## 3. CWS

- [ ] 3.1 `contest.html`: the theme select, the language select (kept under its `available_translations|length > 1` condition) and the log out form go into one `details.controls_menu` as the last navbar item; «Logged in as …» stays in the navbar; `macro/theme_switch.html` and `base.html`'s corner block hold the menu with the theme only for pages without the navbar; «Settings» in `cms.po` (Scenarios "A contest page of CWS", "A CWS page without the contest navbar", "One language on CWS")
- [ ] 3.2 `cws_style.css` and `cws_dark.css`: the menu inside the Bootstrap 2 navbar and in the corner, panel not clipped, both themes; screenshot check
- [ ] 3.3 CWS tests: update `theme_cws_test.py` and the language selector tests, add the placement tests and the contestant log out test (Scenarios "Logging out from the menu", "Open and close without scripts")

## 4. Ranking

- [ ] 4.1 `Ranking.html`: the two selects go into one `details.controls_menu` in `#UpperPanel` (connection status unchanged), the script loaded; `Ranking.css`; «Settings» in both languages in `i18n.js` and its test of complete tables (Scenario "The ranking page")
- [ ] 4.2 Ranking tests: `test_theme_rws.py` and `test_i18n_rws.py` find the controls inside the menu; contrast of the icon in both themes; the delta of `ranking-language` has its test (Scenario "The switch is on the ranking page")

## 5. Docs and verification

- [ ] 5.1 Docs: mention the menu where the manual describes the language and theme choice (`docs/Localization.rst`, `docs/contest-day.md`) with the Spanish `.po`
- [ ] 5.2 Verification: the three pytest groups on PG 18 and the four docs checks (CI mirror), pyflakes, eslint on the touched JS compared with main, screenshots of AWS, CWS and the ranking in both themes and both languages with the menu closed and open, `openspec validate controls-menu`, the Scenario ↔ test check
- [ ] 5.3 Whole-branch review by an independent reviewer, fixes, then archive as its own commit and merge into local `main`
