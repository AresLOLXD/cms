# Controls Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the log out, theme and language controls of AWS, CWS and the ranking into one corner icon menu built on a native `<details>`.

**Architecture:** A `details.controls_menu` wrapper (summary with a gear SVG and an accessible name, then a `.controls_panel` holding the existing controls unchanged) per server, one shared script that closes the open menu on Escape and on a click outside, CSS per server on its own theme tokens. Control ids (`#theme`, `#lang`, `#language`) and their wiring (`theme.js`, `switchLanguage`, `i18n.js`) do not change.

**Tech Stack:** Jinja2 templates, plain CSS with the existing custom properties, plain JS (ES5 style like `theme.js`), pytest with node-driven script tests, gettext (`aws.po`, `cms.po`), the ranking's `i18n.js` tables.

**Spec:** `openspec/changes/controls-menu/specs/controls-menu/spec.md`, `specs/ranking-language/spec.md`, design in `design.md` (same directory).

## Global Constraints

- Code, comments, commit messages in English; Spanish only inside `.po` files and the ranking's Spanish tables. Conventional Commits, each commit ends with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Control ids, the `aws_language` cookie, the contestant language cookie, the ranking `cms.lang` and `cms.theme` keys, and the log out form with its XSRF field do not change.
- The icon is an inline SVG using `currentColor`; no icon file, font or library.
- Every user-visible AWS text is gettext (the AWS guards fail otherwise); the new message is «Settings» / «Ajustes» in `aws.po`, `cms.po` and the ranking tables (both languages).
- English page text stays the same as today except the markup this plan moves.
- Never run a bare `pytest` over the suite: the ranking tests are the gevent group, the AWS/CWS tests the asyncio group (see CLAUDE.md "Testing"). DB for tests: a database whose name ends with `fortesting` in the podman container `cms-test-pg18` (port 55433, user and password `cmsuser`).

## Review Focus

- Other `<details>` on the same page (the collapsible section headings of the AWS overview use them): the script must only touch `.controls_menu`, never close them.
- A click inside the panel (on a select, on a label) must not close the menu, and a click on the summary must toggle once (no double toggle with the native behavior).
- Escape while a control inside the panel has the focus closes the menu and returns the focus to the summary.
- The ranking serves only the static files listed in `FROZEN_ROUTES`; the new script must be listed or it is refused (and the notice pages of a hidden group keep having no menu).
- The AWS sidebar header must still look right with the controls gone (top spacing, title alignment), and the panel must not be clipped by the CWS Bootstrap 2 navbar.

---

### Task 1: The shared script and the icon

**Files:**
- Create: `cms/server/static/controls_menu.js`, identical copy `cmsranking/static/controls_menu.js`
- Modify: `cmsranking/visibility.py:94` (add `"controls_menu.js": "pass"` to `FROZEN_ROUTES`)
- Test: `cmstestsuite/unit_tests/server/static_controls_menu_js_test.py` (new, same node-driven style as `static_theme_js_test.py`; skipped when node is missing)

**Interfaces:**
- Produces: the script, loaded with `<script src=… defer>` or at the end of the body; on `DOMContentLoaded` it attaches one `keydown` and one `click` listener on `document`. It acts only on `details.controls_menu[open]`. No global function.
- Produces: the markup contract every task below follows: `<details class="controls_menu"><summary aria-label="{{ _("Settings") }}"><svg …gear…></svg></summary><div class="controls_panel">…controls…</div></details>`.

- [ ] **Step 1: Write the failing tests** in `static_controls_menu_js_test.py`: `test_escape_closes_the_open_menu_and_focuses_the_summary`, `test_a_click_outside_closes_it`, `test_a_click_inside_the_panel_keeps_it_open`, `test_a_click_on_the_summary_is_left_to_the_browser` (the script does not change `open`), `test_other_details_are_never_closed` (a second `<details>` stays open after Escape and after an outside click), `test_a_page_without_the_menu_still_loads`, `test_the_ranking_has_an_identical_copy`.
- [ ] **Step 2: Run** `pytest cmstestsuite/unit_tests/server/static_controls_menu_js_test.py -v` → FAIL (script missing).
- [ ] **Step 3: Implement the script** with exactly two behaviors: Escape closes every open `.controls_menu` and focuses its `summary`; a click whose target is not inside an open `.controls_menu` closes it. Write the identical copy to the ranking and list it in `FROZEN_ROUTES`.
- [ ] **Step 4: Run** the new test plus `cmstestsuite/unit_tests/cmsranking/test_visibility.py` (the test that checks every static file is in `FROZEN_ROUTES`) → PASS (ranking tests run in their own gevent-group process).
- [ ] **Step 5: Commit** `feat(ui): add the shared script of the controls menu`.

### Task 2: AWS

**Files:**
- Modify: `cms/server/admin/templates/base.html` (the `#sidebar_header` blocks at lines ~108-123 and the `<head>` scripts), `templates/login.html` (head script), `templates/error.html` if it does not extend `base.html`, `cms/server/admin/static/aws_style.css`, `cms/locale/es/LC_MESSAGES/aws.po` and `aws.pot` (regenerate with the documented command in `docs/Localization.rst`)
- Modify tests: `cmstestsuite/unit_tests/server/admin/theme_aws_test.py`, `lang_switch_test.py`, `translated_chrome_test.py` (and `aws_i18n_lint.py` / `aws_render.py` only if they name `.theme_switch` or `login_notice`)
- Test: `cmstestsuite/unit_tests/server/admin/controls_menu_aws_test.py` (new)

**Interfaces:**
- Consumes: the markup contract and `controls_menu.js` of Task 1; `fragments/theme_switch.html` and `fragments/lang_switch.html` keep their content (the `<select id="theme">`, `<select id="lang">` and the inline `switchLanguage`) but drop their `div.theme_switch` wrapper if the panel styles them instead.

- [ ] **Step 1: Write the failing tests** in `controls_menu_aws_test.py`: `test_logged_in_page_has_the_menu_with_all_controls` (greeting, log out form with the xsrf field, `#theme`, `#lang` inside `details.controls_menu`), `test_sidebar_header_has_no_controls` (none of them inside `#sidebar_header`), `test_login_and_error_pages_have_the_menu_without_logout`, `test_the_icon_has_an_accessible_name_in_both_languages` («Settings» / «Ajustes», rendered through the project's AWS render helper), `test_the_controls_appear_only_once`, `test_the_script_is_loaded`. Update the existing tests that look for the controls through the old container so they look inside the menu.
- [ ] **Step 2: Run** `pytest cmstestsuite/unit_tests/server/admin/controls_menu_aws_test.py -v` → FAIL.
- [ ] **Step 3: Implement** the template change and the «Settings» message; CSS: the menu fixed at the top right with a high `z-index`, the panel opening downwards and to the left over the page (no layout shift), surface/border/text from the existing tokens in light and dark, the unused `.theme_switch` and `.login_notice` rules removed, and the `#sidebar_header` top spacing restored. Look at the sidebar footer overlap (`#sidebar::after`, `.cr_notice`): fix it only if a few CSS lines do it, otherwise report it.
- [ ] **Step 4: Run** `pytest cmstestsuite/unit_tests/locale cmstestsuite/unit_tests/server/admin -q` (one asyncio-group process) → PASS, with the AWS guards and `check.py` of the catalog tooling clean.
- [ ] **Step 5: Commit** `feat(aws): move the log out, theme and language controls into the corner menu`.

### Task 3: CWS

**Files:**
- Modify: `cms/server/contest/templates/contest.html` (navbar, lines ~50-85), `templates/base.html:26` (corner block), `templates/macro/theme_switch.html`, `static/cws_style.css`, `static/cws_dark.css`, `cms/locale/es/LC_MESSAGES/cms.po` and `cms.pot`
- Modify tests: `cmstestsuite/unit_tests/server/contest/theme_cws_test.py` and the tests that look for `#lang` or the log out form in the navbar
- Test: `cmstestsuite/unit_tests/server/contest/controls_menu_cws_test.py` (new)

**Interfaces:**
- Consumes: the markup contract and the script of Task 1; `utils.switch_lang()` (in `cws_utils.js`) stays the handler of `#lang`.

- [ ] **Step 1: Write the failing tests**: `test_contest_page_menu_holds_theme_language_and_logout`, `test_logged_in_as_stays_in_the_navbar`, `test_the_language_selector_is_absent_with_one_language`, `test_a_page_without_the_navbar_has_the_menu_with_only_the_theme`, `test_the_controls_appear_only_once`, `test_the_icon_has_an_accessible_name` (Spanish «Ajustes», English «Settings»), `test_the_script_is_loaded`.
- [ ] **Step 2: Run** the new test → FAIL.
- [ ] **Step 3: Implement**: the menu as the last `pull-right` navbar item holding the theme select, the language select (under its existing condition) and the log out form (same XSRF field); the corner wrapper of `base.html` holds the menu with the theme only; CSS so the panel is not clipped by the navbar (`overflow`, `position`, `z-index`) in both themes.
- [ ] **Step 4: Run** `pytest cmstestsuite/unit_tests/server/contest cmstestsuite/unit_tests/locale -q` (asyncio group) → PASS.
- [ ] **Step 5: Commit** `feat(cws): move the theme, language and log out controls into the corner menu`.

### Task 4: Ranking

**Files:**
- Modify: `cmsranking/static/Ranking.html:45-54`, `Ranking.css` (the block at ~1386 for `#theme`/`#language`), `cmsranking/static/i18n.js` (the «Settings» entry in both tables)
- Modify tests: `cmstestsuite/unit_tests/cmsranking/test_theme_rws.py`, `test_i18n_rws.py`
- Test: `cmstestsuite/unit_tests/cmsranking/test_controls_menu_rws.py` (new)

**Interfaces:**
- Consumes: the markup contract and the copied script of Task 1; `i18n.js` translates `data-i18n-attr` entries (as `alt.logo` does), so the summary label uses that mechanism.

- [ ] **Step 1: Write the failing tests**: `test_the_upper_panel_has_one_menu_with_both_selects` (and no other copy of them), `test_the_connection_status_is_unchanged`, `test_the_summary_label_is_translated_in_both_languages`, `test_the_script_loads_and_is_in_frozen_routes`, `test_the_icon_contrasts_in_both_themes` (use the token checks of `theme_contrast_test.py`/`themecss.py`).
- [ ] **Step 2: Run** the new test (gevent group process: `pytest cmstestsuite/unit_tests/cmsranking -q`) → FAIL.
- [ ] **Step 3: Implement** the markup, the CSS and the two table entries; update the existing tests that found the selects directly.
- [ ] **Step 4: Run** `pytest cmstestsuite/unit_tests/cmsranking -q` → PASS.
- [ ] **Step 5: Commit** `feat(ranking): move the theme and language switches into the corner menu`.

---

Group 5 of `tasks.md` (docs, verification, review, archive) is run by the lead after these tasks; it is not part of this plan.
