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

"""Tests for the corner controls menu of AWS: the markup of the pages.

Nothing here renders a page in a browser; the tests read the rendered html.

"""

import re
import unittest
from unittest.mock import patch

from cms.server.admin.server import AdminWebServer
from cms.io.web_service import StaticFileHasher
from cmstestsuite.unit_tests.server.admin.lang_switch_test import \
    error_page, login_page
from cmstestsuite.unit_tests.server.admin.theme_aws_test import \
    render_page, sidebar_pages
from cmstestsuite.unit_tests.server.admin.translated_chrome_test import \
    sidebar, spanish
from cmstestsuite.unit_tests.server.themecss import AWS_CSS


MENU = re.compile(r'<details class="controls_menu">(.*?)</details>', re.S)


def the_menu(html: str) -> str:
    """Return the inside of the one menu of the page."""
    menus = MENU.findall(html)
    assert len(menus) == 1, len(menus)
    return menus[0]


def the_sidebar_header(html: str) -> str:
    start = html.index('id="sidebar_header"')
    return html[start:html.index("</div>", start)]


class TestMenuOnThePages(unittest.TestCase):

    def test_logged_in_page_has_the_menu_with_all_controls(self):
        # Scenario: A logged in AWS page
        menu = the_menu(render_page("base.html", **sidebar()))

        self.assertRegex(menu, r'<summary aria-label="Settings">\s*<svg ')
        self.assertIn('Hello, <a href="/admins">root</a>.', menu)
        form = re.search(r'<form[^>]*action="/logout"[^>]*>(.*?)</form>',
                         menu, re.S)
        self.assertIsNotNone(form)
        self.assertIn('value="Logout"', form.group(1))
        self.assertIn('<select id="theme"', menu)
        self.assertIn('<select id="lang"', menu)
        # the panel holds them
        self.assertRegex(menu, r'(?s)<div class="controls_panel">.*'
                         r'id="theme".*id="lang"')

    def test_the_xsrf_field_stays_in_the_logout_form(self):
        field = '<input type="hidden" name="_xsrf" value="tok">'
        menu = the_menu(render_page("base.html", xsrf_form_html=field,
                                    **sidebar()))

        form = re.search(r'<form[^>]*logout[^>]*>(.*?)</form>', menu, re.S)
        self.assertIn(field, form.group(1))

    def test_sidebar_header_has_no_controls(self):
        # Scenario: A logged in AWS page (the sidebar keeps navigation only)
        for name, html in (("logged in", render_page("base.html", **sidebar())),
                           ("logged out", sidebar_pages()["base logged out"])):
            header = the_sidebar_header(html)
            for control in ("<select", "<form", "<input", "Hello",
                            "theme_switch"):
                self.assertNotIn(control, header, name)
            self.assertIn("Administration", header, name)

    def test_login_and_error_pages_have_the_menu_without_logout(self):
        # Scenario: The AWS login page
        for name, html in (("login", login_page()), ("error", error_page())):
            menu = the_menu(html)
            self.assertIn('<select id="theme"', menu, name)
            self.assertIn('<select id="lang"', menu, name)
            self.assertNotIn("<form", menu, name)
            self.assertNotIn("Hello", menu, name)

    def test_the_icon_has_an_accessible_name_in_both_languages(self):
        # Scenario: The icon is named in the page language
        english = the_menu(render_page("base.html", **sidebar()))
        in_spanish = the_menu(spanish("base.html", **sidebar()))

        self.assertIn('<summary aria-label="Settings">', english)
        self.assertIn('<summary aria-label="Ajustes">', in_spanish)
        self.assertNotIn("Settings", in_spanish)
        # the icon itself is hidden from assistive technology
        self.assertRegex(english,
                         r'<svg[^>]*aria-hidden="true"[^>]*focusable="false"')

    def test_the_controls_appear_only_once(self):
        # Scenario: no other copy of the controls on the page
        pages = dict(sidebar_pages(), error=error_page())
        for name, html in pages.items():
            self.assertEqual(html.count('id="theme"'), 1, name)
            self.assertEqual(html.count('id="lang"'), 1, name)
            self.assertEqual(html.count('class="controls_menu"'), 1, name)
            self.assertEqual(html.count("function switchLanguage"), 1, name)
        self.assertEqual(
            sidebar_pages()["base logged in"].count('action="/logout"'), 1)

    def test_the_script_is_loaded(self):
        pages = dict(sidebar_pages(), error=error_page())
        for name, html in pages.items():
            head = html[:html.index("</head>")]
            self.assertIn('<script src="static/controls_menu.js" defer>',
                          head, name)

    def test_aws_serves_the_script(self):
        # The templates refer to static_url("static", "controls_menu.js"):
        # AWS must search cms/server/static, where it lives.
        class Stop(Exception):
            pass

        with patch("cms.server.admin.server.WebService.__init__",
                   side_effect=Stop) as web_service_init:
            with self.assertRaises(Stop):
                AdminWebServer(0)
        static_files = web_service_init.call_args.args[2]["static_files"]
        url = StaticFileHasher(static_files).make(lambda *p: "/".join(p))(
            "static", "controls_menu.js")
        self.assertRegex(url, r"^static/controls_menu\.js\?h=")


class TestMenuStylesheet(unittest.TestCase):

    def test_the_old_container_rules_are_gone(self):
        css = AWS_CSS.read_text(encoding="utf-8")

        self.assertIsNone(re.search(r"theme_switch", css))
        self.assertIsNone(re.search(r"login_notice", css))
        self.assertRegex(css, r"\.controls_menu\s*\{[^}]*position:\s*fixed")


if __name__ == "__main__":
    unittest.main()
