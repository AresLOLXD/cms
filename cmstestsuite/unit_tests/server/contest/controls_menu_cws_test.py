#!/usr/bin/env python3

# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Tests for the corner controls menu of CWS: the markup of the pages.

Nothing here renders a page in a browser; the tests read the rendered html.

"""

import re
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from cmstestsuite.unit_tests.server.admin.aws_catalog import CMS_SPANISH
from cmstestsuite.unit_tests.server.contest.theme_cws_test import \
    contest_list_page, pages, render_page
from cmstestsuite.unit_tests.server.themecss import CWS_CSS


MENU = re.compile(r'<details class="controls_menu">(.*?)</details>', re.S)
XSRF = '<input type="hidden" name="_xsrf" value="tok">'


def the_menu(html: str) -> str:
    """Return the inside of the one menu of the page."""
    menus = MENU.findall(html)
    assert len(menus) == 1, len(menus)
    return menus[0]


def navbar(html: str) -> str:
    start = html.index('id="navigation_bar"')
    return html[start:html.index('id="notifications"', start)]


def logged_in_page(languages: int = 2, **params) -> str:
    """Render the contest page of a logged in contestant."""
    now = datetime(2026, 1, 1)
    params.setdefault("participation", SimpleNamespace(
        unrestricted=False, group=SimpleNamespace(per_user_time=None)))
    params.setdefault("contest", SimpleNamespace(
        description="Contest", name="c", languages=[], tasks=[],
        allow_registration=True))
    params.setdefault("user", SimpleNamespace(
        first_name="Ada", last_name="Lovelace", username="ada"))
    return render_page(
        "contest.html", languages=languages, now=now, utc=timezone.utc,
        timezone=timezone.utc, current_phase_begin=now,
        current_phase_end=now, actual_phase=0, page="overview",
        testing_enabled=False, xsrf_form_html=XSRF, **params)


class TestMenuOnThePages(unittest.TestCase):

    def test_contest_page_menu_holds_theme_language_and_logout(self):
        # Scenario: A logged in CWS page
        html = logged_in_page()
        menu = the_menu(html)

        self.assertRegex(menu, r'<summary aria-label="Settings">\s*<svg ')
        self.assertRegex(menu, r'(?s)<div class="controls_panel">.*'
                         r'id="theme".*id="lang".*action="/c/logout"')
        self.assertIn('onchange="utils.switch_lang()"', menu)
        form = re.search(r'<form[^>]*action="/c/logout"[^>]*>(.*?)</form>',
                         menu, re.S)
        self.assertIn(XSRF, form.group(1))
        self.assertIn(">Logout</button>", form.group(1))
        # the menu is the last pull-right item of the navbar, so the
        # rightmost one
        self.assertLess(navbar(html).index("controls_menu"),
                        navbar(html).index("Logged in as"))

    def test_logged_in_as_stays_in_the_navbar(self):
        # Scenario: A logged in CWS page
        html = logged_in_page()

        self.assertIn("Logged in as", navbar(html))
        self.assertNotIn("Logged in as", the_menu(html))
        self.assertIn("<strong>Ada Lovelace</strong>", navbar(html))

    def test_the_language_selector_is_absent_with_one_language(self):
        # Scenario: CWS with a single language
        menu = the_menu(logged_in_page(languages=1))

        self.assertNotIn('id="lang"', menu)
        self.assertIn('id="theme"', menu)
        self.assertIn('action="/c/logout"', menu)

    def test_the_login_page_menu_has_no_logout(self):
        # Scenario: The CWS login page
        html = render_page("contest.html", languages=2)
        menu = the_menu(html)

        self.assertIn('id="theme"', menu)
        self.assertIn('id="lang"', menu)
        self.assertNotIn("<form", menu)
        self.assertNotIn("Logout", html)

    def test_a_page_without_the_navbar_has_the_menu_with_only_the_theme(self):
        # Scenario: A page without the contest navbar
        # (it has no language selector nor log out today, so it gains none,
        # even when the contest has several languages)
        for name, html in pages().items():
            if name.startswith("login"):
                continue
            menu = the_menu(html)
            self.assertIn('id="theme"', menu, name)
            self.assertNotIn('id="lang"', menu, name)
            self.assertNotIn("<form", menu, name)
            self.assertNotIn("Logout", menu, name)
        self.assertNotIn("navigation_bar",
                         render_page("error.html", status_code=500,
                                     invalid_body=False, languages=2))
        self.assertNotIn(
            'id="lang"', render_page("error.html", status_code=500,
                                     invalid_body=False, languages=2))

    def test_the_controls_appear_only_once(self):
        # Scenario: no other copy of the controls on the page
        every_page = dict(pages(), contest=logged_in_page(),
                          list=contest_list_page())
        for name, html in every_page.items():
            self.assertEqual(html.count('id="theme"'), 1, name)
            self.assertEqual(html.count('class="controls_menu"'), 1, name)
            self.assertLessEqual(html.count('id="lang"'), 1, name)
        self.assertEqual(
            every_page["contest"].count('action="/c/logout"'), 1)
        self.assertEqual(every_page["contest"].count('id="lang"'), 1)

    def test_the_icon_has_an_accessible_name(self):
        # Scenario: The icon is named in the page language
        english = the_menu(logged_in_page())
        in_spanish = the_menu(logged_in_page(
            translation=CMS_SPANISH, gettext=CMS_SPANISH.gettext))

        self.assertIn('<summary aria-label="Settings">', english)
        self.assertIn('<summary aria-label="Ajustes">', in_spanish)
        self.assertNotIn("Settings", in_spanish)
        # the icon itself is hidden from assistive technology
        self.assertRegex(english,
                         r'<svg[^>]*aria-hidden="true"[^>]*focusable="false"')

    def test_the_script_is_loaded(self):
        for name, html in dict(pages(), contest=logged_in_page()).items():
            head = html[:html.index("</head>")]
            self.assertIn('<script src="static/controls_menu.js" defer>',
                          head, name)


class TestMenuStylesheet(unittest.TestCase):

    def test_the_old_container_rules_are_gone(self):
        css = CWS_CSS.read_text(encoding="utf-8")

        self.assertNotIn("theme_switch_corner", css)
        self.assertRegex(css, r"\.controls_menu\s*\{[^}]*position:\s*relative")
        # the panel floats over the page, not inside the navbar's flow
        self.assertRegex(css, r"\.controls_panel\s*\{[^}]*position:\s*absolute")


if __name__ == "__main__":
    unittest.main()
