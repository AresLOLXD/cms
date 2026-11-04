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

"""Tests for the color theme of RWS: the page, the stylesheet and the pages
that visibility.py builds.

Nothing here renders a page in a browser; the tests read the files and the
HTML that the app answers. They belong to the gevent group of tests: run
them apart from the asyncio ones.

"""

import importlib.resources
import json
import re
import unittest
from unittest.mock import patch

from cmsranking.visibility import FROZEN_ROUTES
from cmstestsuite.unit_tests.cmsranking import test_visibility
from cmstestsuite.unit_tests.server.themecss import DARK_ATTRIBUTE, \
    DARK_MEDIA, LITERAL_COLOR, dark_set, light_set, read_block


STATIC = importlib.resources.files("cmsranking").joinpath("static")
CSS = STATIC.joinpath("Ranking.css")
HTML = STATIC.joinpath("Ranking.html")
THEME_JS = STATIC.joinpath("theme.js")


def without_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


class TestRwsPage(unittest.TestCase):

    def setUp(self):
        self.html = HTML.read_text(encoding="utf-8")

    def test_the_script_loads_in_head_before_the_stylesheet(self):
        # Scenario: A saved dark choice with a light system
        # (a blocking script in <head> before the stylesheet runs before the
        # body is painted)
        head = self.html[:self.html.index("</head>")]
        script = head.index('src="theme.js"')
        self.assertNotRegex(head[script:script + 40], r"defer|async")
        self.assertLess(script, head.index("Ranking.css"))
        self.assertNotIn("data-theme", self.html)

    def test_the_switch_is_in_the_top_bar(self):
        # Scenario: The switch is on every page
        panel = re.search(r'<div id="UpperPanel">(.*?)<div id="TimeView">',
                          self.html, re.S).group(1)
        select = re.findall(r'<select id="theme"[^>]*>(.*?)</select>',
                            panel, re.S)
        self.assertEqual(len(select), 1)
        self.assertEqual(
            re.findall(r'<option value="(\w+)">(\w+)</option>', select[0]),
            [("system", "Sistema"), ("light", "Claro"), ("dark", "Oscuro")])
        self.assertEqual(self.html.count('id="theme"'), 1)

    def test_the_footer_has_no_literal_color(self):
        # Scenario: The three servers cover their pages (RWS footer)
        styles = re.findall(r'style="([^"]*)"', self.html)
        self.assertEqual([c for s in styles for c in LITERAL_COLOR.findall(s)],
                         [])
        self.assertIn('class="powered_by"', self.html)


class TestRwsStylesheet(unittest.TestCase):

    def setUp(self):
        self.css = CSS.read_text(encoding="utf-8")

    def test_dark_set_is_declared_for_the_system_and_for_the_choice(self):
        # Scenario: System prefers dark
        # Scenario: Choosing Dark or Light
        # (the same tokens, with the same values, under both selectors)
        by_media = read_block(self.css, DARK_MEDIA)
        by_attribute = read_block(self.css, DARK_ATTRIBUTE)
        self.assertGreater(len(by_media), 0)
        self.assertEqual(by_media, by_attribute)
        self.assertNotEqual(by_media, light_set(self.css))
        self.assertEqual(set(light_set(self.css)), set(by_media))

    def test_dark_applies_by_media_query_only_without_a_light_choice(self):
        # Scenario: System prefers light or nothing
        # (the light values are the default, in a plain :root; dark comes
        # through the media query, and not while the choice is Light)
        self.assertRegex(
            self.css, r"@media \(prefers-color-scheme: dark\) \{\s*%s \{"
            % re.escape(DARK_MEDIA))
        self.assertEqual(self.css.count("--page-bg:"), 3)

    def test_native_controls_follow_the_theme(self):
        # Scenario: Dark native controls
        self.assertRegex(self.css, r"(?m)^:root \{\s*color-scheme: light dark;")
        self.assertRegex(
            self.css, r'(?s)\[data-theme="light"\] \{\s*color-scheme: light;')
        self.assertRegex(
            self.css, r'(?s)\[data-theme="dark"\] \{\s*color-scheme: dark;')
        self.assertRegex(self.css, r"(?s)%s \{\s*color-scheme: dark;" %
                         re.escape(DARK_MEDIA))

    def test_no_literal_color_is_left_outside_the_tokens(self):
        # Scenario: The three servers cover their pages (RWS)
        # (a color that is not a token would not change with the theme;
        # rgba(var(--up), a) takes its channels from a token)
        css = without_comments(self.css)
        css = re.sub(r"^\s*--[\w-]+:.*$", "", css, flags=re.M)
        css = css.replace("rgba(var(", "")
        # Allowed: the IE gradient filter, which is only the fallback of
        # the overlay for a browser without custom properties, and the
        # palette of the selection boxes (see the note on the token block).
        css = "\n".join(line for line in css.splitlines()
                        if "progid:" not in line)
        css = re.sub(r"tr\.color[1-8] td\.sel(?::hover|:active)? \{[^}]*\}",
                     "", css)
        self.assertEqual(LITERAL_COLOR.findall(css), [])

    def test_keyframes_take_their_colors_from_tokens(self):
        # Scenario: The three servers cover their pages (RWS animations)
        # (8 sets, each with the 3 prefixes of the browsers and the plain one)
        keyframes = re.findall(r"@(?:-\w+-)?keyframes \w+ \{(.*?)\n\}",
                               without_comments(self.css), flags=re.S)
        self.assertEqual(len(keyframes), 16)
        for body in keyframes:
            colors = re.findall(r"background-color:\s*([^;}]+)", body)
            self.assertEqual(len(colors), 3)
            for color in colors:
                self.assertRegex(
                    color, r"^rgba\(var\(--(?:up|down)\), [01](?:\.6)?\)$")

    def test_ticks_and_close_icons_stay_visible_in_dark(self):
        # Scenario: CWS chevrons and RWS ticks (RWS half)
        # (the tick of the unselected boxes is swapped for the white one; the
        # selected boxes always had the white one on a colored box; the close
        # icon, drawn for a light background, is inverted)
        light, dark = light_set(self.css), dark_set(self.css)
        self.assertIn("tick_black.png", light["tick-icon"])
        self.assertIn("tick_white.png", dark["tick-icon"])
        self.assertEqual(light["icon-filter"], "none")
        self.assertEqual(dark["icon-filter"], "invert(1)")
        self.assertEqual(self.css.count("var(--tick-icon)"), 2)
        self.assertNotIn("tick_black", without_comments(self.css).replace(
            light["tick-icon"], ""))
        for selector in ("#TeamSearch_close", "#UserDetail_close"):
            self.assertRegex(
                self.css, r"(?m)^%s \{[^}]*filter: var\(--icon-filter\);"
                % selector)
        for icon in ("tick_black.png", "tick_white.png", "close.png"):
            self.assertTrue(STATIC.joinpath("img", icon).is_file(), icon)

    def test_the_charts_keep_a_light_panel_in_both_themes(self):
        # Scenario: A chart in dark theme
        # (the panel and its text color are the same light tokens in both
        # sets; the overview and the canvases of the user detail use them)
        light, dark = light_set(self.css), dark_set(self.css)
        for token in ("chart-bg", "overview-bg", "chart-text"):
            self.assertEqual(light[token], dark[token], token)
        self.assertGreater(int(light["chart-bg"][1:3], 16), 0xe0)
        self.assertGreater(int(light["overview-bg"][1:3], 16), 0xe0)
        self.assertRegex(
            self.css, r"(?m)^#Overview \{[^}]*background-color: "
            r"var\(--overview-bg\);[^}]*color: var\(--chart-text\);")
        self.assertRegex(
            self.css, r"(?m)^#UserDetail_charts canvas \{[^}]*"
            r"background-color: var\(--chart-bg\);[^}]*"
            r"color: var\(--chart-text\);")


class TestRwsServesTheScript(test_visibility.VisibilityTestCase):

    def test_the_frozen_group_passes_the_script(self):
        # theme.js is a static file at the top level: a frozen group serves
        # it to the public as it does Ranking.css, and the live group too.
        self.assertEqual(FROZEN_ROUTES["theme.js"], "pass")
        self.put_contest("/olim")
        for state in ("live", "frozen"):
            if state == "frozen":
                self.client.put("/olim/visibility", data=json.dumps({
                    "hide_at": None, "show_at": None, "freeze_at": 200,
                    "unfreeze_at": 400,
                    "staff_password": test_visibility.STAFF_HASH}),
                    content_type="application/json",
                    headers=test_visibility.AUTH)
            with patch("cmsranking.visibility.time.time",
                       return_value=350 if state == "frozen" else 100):
                response = self.client.get("/olim/theme.js")
            self.assertEqual(response.status_code, 200, state)
            self.assertEqual(response.get_data(), THEME_JS.read_bytes(),
                             state)


if __name__ == "__main__":
    unittest.main()
