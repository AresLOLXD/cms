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
import shutil
import subprocess
import unittest
from unittest.mock import patch

from cmsranking.visibility import FROZEN_ROUTES, STAFF_BANNER, \
    STAFF_FROZEN_BANNER, public_frozen_banner
from cmstestsuite.unit_tests.cmsranking import test_visibility
from cmstestsuite.unit_tests.server.theme_contrast_test import contrast
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


# Runs the early-apply snippet of a notice page (argv[1]) against stubs and
# prints the data-theme it sets. Config (JSON, argv[2]): "stored" (the value
# of cms.theme, or null), "throws" (reading local storage fails).
EARLY_HARNESS = r"""
const vm = require("vm");
const cfg = JSON.parse(process.argv[2]);
const attributes = {};
const localStorage = {
    getItem() {
        if (cfg.throws) throw new Error("blocked");
        return cfg.stored;
    },
};
const document = {
    documentElement: { setAttribute(name, value) { attributes[name] = value; } },
};
vm.runInNewContext(process.argv[1], { document, localStorage });
console.log(JSON.stringify(attributes["data-theme"] || null));
"""


class TestRwsBuiltInPages(test_visibility.VisibilityTestCase):
    """The notice and the staff login, as a hidden and a frozen group serve
    them."""

    def setUp(self):
        super().setUp()
        self.put_contest("/olim")

    def hide(self):
        self.put_visibility("olim", True)

    def freeze(self):
        self.client.put("/olim/visibility", data=json.dumps({
            "hide_at": None, "show_at": None, "freeze_at": 200,
            "unfreeze_at": 400,
            "staff_password": test_visibility.STAFF_HASH}),
            content_type="application/json", headers=test_visibility.AUTH)

    def pages(self) -> dict[str, str]:
        """Return the html of the hidden notice, the staff login of a
        frozen group and the notice that answers a failed login."""
        self.hide()
        pages = {"hidden notice": self.client.get("/olim/")}
        with patch("cmsranking.visibility.gevent.sleep"):
            pages["failed login"] = self.client.post(
                "/olim/staff-login", data={"password": "wrong"})
        self.put_visibility("olim", False)
        self.freeze()
        with patch("cmsranking.visibility.time.time", return_value=350):
            pages["frozen login"] = self.client.get("/olim/staff-login")
        for name, response in pages.items():
            self.assertEqual(response.status_code,
                             401 if name == "failed login" else 200, name)
        return {name: r.get_data(as_text=True) for name, r in pages.items()}

    def test_a_hidden_group_refuses_the_script(self):
        # Why the notice pages inline the early apply: a hidden group
        # answers 403 to everything but the notice, the login and the staff
        # (and a frozen group's login page is the same page).
        self.hide()
        self.assertEqual(self.client.get("/olim/theme.js").status_code, 403)
        for name, html in self.pages().items():
            self.assertNotIn("theme.js", html, name)
            self.assertNotIn("<script src", html, name)

    def test_the_pages_have_the_dark_set_under_both_selectors(self):
        # Scenario: The ranking built-in pages
        # (the same tokens and values under both selectors, and different
        # from the light set, so the pages do not stay light in dark)
        for name, html in self.pages().items():
            style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
            by_media = read_block(style, DARK_MEDIA)
            self.assertGreater(len(by_media), 0, name)
            self.assertEqual(by_media, read_block(style, DARK_ATTRIBUTE), name)
            self.assertEqual(set(by_media), set(light_set(style)), name)
            for token, value in by_media.items():
                self.assertNotEqual(value, light_set(style)[token],
                                    "%s %s" % (name, token))
            self.assertRegex(style, r"(?m)^:root \{\s*color-scheme: light dark;")
            self.assertRegex(style, r"(?s)%s \{\s*color-scheme: dark;" %
                             re.escape(DARK_MEDIA))
            self.assertRegex(
                style, r'(?s)\[data-theme="dark"\] \{\s*color-scheme: dark;')
            # Outside the token declarations, no literal color.
            self.assertEqual(LITERAL_COLOR.findall(
                re.sub(r"--[\w-]+:[^;]*;", "", style)), [], name)

    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
    def test_the_pages_apply_the_saved_choice(self):
        # Scenario: A saved dark choice with a light system
        # Scenario: Local storage is unavailable
        # (the snippet does what the early apply of theme.js does)
        for name, html in self.pages().items():
            script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
            self.assertIn("cms.theme", script)
            for stored, throws, expected in [
                    ("dark", False, "dark"), ("light", False, "light"),
                    (None, False, None), ("system", False, None),
                    ("garbage", False, None), (None, True, None)]:
                result = subprocess.run(
                    ["node", "-e", EARLY_HARNESS, script, json.dumps(
                        {"stored": stored, "throws": throws})],
                    capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout), expected,
                                 (name, stored, throws))

    def test_the_banners_use_tokens_not_literal_colors(self):
        # Scenario: The ranking built-in pages
        # (the banner is inserted into Ranking.html, whose stylesheet has
        # banner-bg and banner-text, with a dark value of its own for the bar)
        css = CSS.read_text(encoding="utf-8")
        banners = [STAFF_BANNER, STAFF_FROZEN_BANNER,
                   public_frozen_banner(1_700_000_000)]
        for banner in banners:
            text = banner.decode("utf-8")
            self.assertEqual(LITERAL_COLOR.findall(text), [])
            self.assertIn("background:var(--banner-bg)", text)
            self.assertIn("color:var(--banner-text)", text)
        self.assertNotEqual(light_set(css)["banner-bg"],
                            dark_set(css)["banner-bg"])


def to_hex(channels: str) -> str:
    """Return the #rrggbb of "r, g, b" (as the up and down tokens are)."""
    return "#%02x%02x%02x" % tuple(int(c) for c in channels.split(","))


class TestScoreCellsKeepTheirMeaning(unittest.TestCase):

    # Scenario: Score cells keep their meaning
    # The colors that tell the states of a score apart are zero (the text
    # color of a score of 0), improved (up), worsened (down) and selected
    # (the palette of the selection boxes, which is the same in both sets and
    # is only checked in the dark set: in the light one it is the old
    # palette, unchanged). Each must be told from the others (all different;
    # up and down also by luminance, not only by hue) and from the page.
    # 1.5:1 is the threshold: WCAG asks 3:1 only for the parts of a control,
    # and these are transient or secondary marks next to text that has its
    # own check (see theme_contrast_test); 1.5 keeps them from melting into
    # the background.
    MIN_RATIO = 1.5

    def setUp(self):
        self.css = CSS.read_text(encoding="utf-8")

    def states(self, tokens: dict[str, str]) -> dict[str, str]:
        return {"zero": tokens["score-zero"], "up": to_hex(tokens["up"]),
                "down": to_hex(tokens["down"])}

    def test_states_differ_from_each_other_and_from_the_page(self):
        palette = [
            "#%02x%02x%02x" % tuple(int(c) for c in match)
            for match in re.findall(
                r"tr\.color[1-8] td\.sel \{\s*background-color: "
                r"rgb\((\d+),(\d+),(\d+)\)", self.css)]
        self.assertEqual(len(palette), 8)
        for theme, tokens in (("light", light_set(self.css)),
                              ("dark", dark_set(self.css))):
            states = self.states(tokens)
            if theme == "dark":
                states.update(
                    ("selected %d" % n, c) for n, c in enumerate(palette, 1))
            self.assertEqual(len(set(states.values())), len(states), theme)
            for name, color in states.items():
                for page in ("page-grad-top", "page-grad-bottom"):
                    self.assertGreaterEqual(
                        contrast(color, tokens[page]), self.MIN_RATIO,
                        "%s: %s on %s" % (theme, name, page))
            self.assertGreaterEqual(
                contrast(states["up"], states["down"]), self.MIN_RATIO, theme)


if __name__ == "__main__":
    unittest.main()
