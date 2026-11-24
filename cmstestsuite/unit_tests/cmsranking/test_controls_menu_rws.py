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

"""Tests for the corner controls menu of RWS: the page and the stylesheet.

Nothing here renders a page in a browser; the tests read the files. They
belong to the gevent group of tests: run them apart from the asyncio ones.

"""

import importlib.resources
import re
import shutil
import unittest

from cmsranking.visibility import FROZEN_ROUTES
from cmstestsuite.unit_tests.cmsranking.test_i18n_rws import run_script
from cmstestsuite.unit_tests.server.themecss import contrast, dark_set, \
    light_set


STATIC = importlib.resources.files("cmsranking").joinpath("static")
HTML = STATIC.joinpath("Ranking.html")
CSS = STATIC.joinpath("Ranking.css")

MENU = re.compile(r'<details class="controls_menu">(.*?)</details>', re.S)
STATUS_BOX = (
    '<div id="ConnectionStatus_box">\n'
    '                    <div id="ConnectionStatus_label" '
    'data-i18n="label.status">Status</div>\n'
    '                    <div id="ConnectionStatus_text"></div>\n'
    '                </div>')


def upper_panel(html: str) -> str:
    return re.search(r'<div id="UpperPanel">(.*?)<div id="TimeView">',
                     html, re.S).group(1)


class TestMenuOnThePage(unittest.TestCase):

    def setUp(self):
        self.html = HTML.read_text(encoding="utf-8")

    def test_the_upper_panel_has_one_menu_with_both_selects(self):
        # Scenario: The ranking page (the controls leave the top panel)
        panel = upper_panel(self.html)
        menus = MENU.findall(panel)
        self.assertEqual(len(menus), 1)
        self.assertEqual(self.html.count('class="controls_menu"'), 1)
        self.assertRegex(menus[0], r'(?s)<div class="controls_panel">.*'
                         r'<select id="theme".*<select id="language"')
        # no other copy: the selects are not left in the panel either
        self.assertNotIn("<select", MENU.sub("", panel))
        self.assertEqual(self.html.count('id="theme"'), 1)
        self.assertEqual(self.html.count('id="language"'), 1)

    def test_the_connection_status_is_unchanged(self):
        # Scenario: The ranking page (the status stays where it was)
        panel = upper_panel(self.html)
        self.assertTrue(panel.lstrip().startswith(STATUS_BOX), panel)
        self.assertNotIn("ConnectionStatus", MENU.search(panel).group(1))

    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
    def test_the_summary_label_is_translated_in_both_languages(self):
        # Scenario: The icon is named in the page language
        summary = re.search(r"<summary([^>]*)>", self.html).group(1)
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', summary))
        self.assertEqual(attrs["aria-label"], "Settings")
        element = {"attrs": {"data-i18n-attr": attrs["data-i18n-attr"],
                             "aria-label": attrs["aria-label"]}, "text": ""}
        for languages, label in ((["en-US"], "Settings"),
                                 (["es-MX"], "Ajustes")):
            out = run_script(languages=languages, elements=[element])
            self.assertEqual(out["filled"][0]["attrs"]["aria-label"], label)

    def test_the_script_loads_and_is_in_frozen_routes(self):
        # Scenario: The ranking page (the script closes the menu)
        head = self.html[:self.html.index("</head>")]
        self.assertIn('<script src="controls_menu.js" defer></script>', head)
        self.assertEqual(FROZEN_ROUTES["controls_menu.js"], "pass")
        self.assertTrue(STATIC.joinpath("controls_menu.js").is_file())


class TestMenuStylesheet(unittest.TestCase):

    def test_the_icon_contrasts_in_both_themes(self):
        # Scenario: The icon and the panel keep their contrast in both themes
        css = CSS.read_text(encoding="utf-8")
        sets = (light_set(css), dark_set(css))
        for selector in (r"\.controls_menu summary", r"\.controls_panel"):
            rule = re.search(r"(?m)^%s \{([^}]*)\}" % selector, css).group(1)
            foreground = re.search(r"(?<!-)color: var\(--([\w-]+)\)",
                                   rule).group(1)
            background = re.search(r"background-color: var\(--([\w-]+)\)",
                                   rule).group(1)
            for tokens in sets:
                self.assertGreaterEqual(
                    contrast(tokens[foreground], tokens[background]), 4.5,
                    selector)


if __name__ == "__main__":
    unittest.main()
