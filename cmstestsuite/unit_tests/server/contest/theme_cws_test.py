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

"""Tests for the color theme of CWS: the pages and the stylesheets.

Nothing here renders a page in a browser; the tests read the templates and
the CSS text.

"""

import importlib.resources
import re
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cms.io.web_service import StaticFileHasher
from cms.server.contest.jinja2_toolbox import CWS_ENVIRONMENT
from cms.server.contest.server import ContestWebServer
from cmstestsuite.unit_tests.server.themecss import CWS_CSS, CWS_STATIC, \
    DARK_ATTRIBUTE, DARK_MEDIA, LITERAL_COLOR, light_set, read_block


TEMPLATES = importlib.resources.files("cms.server.contest") \
    .joinpath("templates")
DARK_CSS = CWS_STATIC.joinpath("cws_dark.css")

# How cws_dark.css scopes its rules (:where() adds no specificity).
MEDIA_SCOPE = ":where(%s)" % DARK_MEDIA
ATTRIBUTE_SCOPE = ":where(%s)" % DARK_ATTRIBUTE


def render_page(template: str, languages: int = 1, **params) -> str:
    handler = MagicMock()
    handler.get_argument.side_effect = lambda name, default=None: default
    translation = SimpleNamespace(identifier="en", name="English")
    params.setdefault("static_url", lambda *parts: "/".join(parts))
    params.setdefault("url", lambda *parts: "/" + "/".join(map(str, parts)))
    params.setdefault("contest_url",
                      lambda *parts: "/c/" + "/".join(parts))
    params.setdefault("xsrf_form_html", "")
    params.setdefault("handler", handler)
    params.setdefault("translation", translation)
    params.setdefault("contest", SimpleNamespace(
        description="Contest", name="c", languages=[],
        allow_registration=True))
    params.setdefault("available_translations", {
        "l%d" % i: SimpleNamespace(name="Language %d" % i)
        for i in range(languages)})
    params.setdefault("cookie_translation", None)
    params.setdefault("automatic_translation", translation)
    params.setdefault("gettext", lambda text: text)
    params.setdefault("ngettext", lambda one, many, n: one if n == 1 else many)
    return CWS_ENVIRONMENT.get_template(template).render(**params)


def contest_list_page() -> str:
    now = datetime(2026, 1, 1)
    group = lambda stop: SimpleNamespace(stop=stop)  # noqa: E731
    contests = {name: SimpleNamespace(name=name, description=name,
                                      main_group=group(stop))
                for name, stop in (("open", now + timedelta(days=1)),
                                   ("ended", now - timedelta(days=1)))}
    return render_page("contest_list.html", contest_list=contests, now=now)


def pages() -> dict[str, str]:
    """Return the html of the pages that extend base.html directly, and of
    the login page (contest.html, which has the contest navbar)."""
    return {
        "login": render_page("contest.html"),
        "login with languages": render_page("contest.html", languages=2),
        "contest list": contest_list_page(),
        "error": render_page("error.html", status_code=500,
                             invalid_body=False),
        "register": render_page("register.html", MAX_INPUT_LENGTH=100, MIN_PASSWORD_LENGTH=6,
                                 teams=[]),
    }


def top_level_blocks(css: str) -> list[tuple[str, str]]:
    """Return (prelude, content) of each top-level block of a stylesheet,
    without comments."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\s+", " ", css)
    blocks, depth, start, prelude_start = [], 0, 0, 0
    for index, char in enumerate(css):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                blocks.append((css[prelude_start:start].strip(),
                               css[start + 1:index]))
                prelude_start = index + 1
    return blocks


def overlay_rules() -> tuple[list, list]:
    """Return the rules of cws_dark.css under the media query and under the
    data-theme attribute, with their selector scope replaced by "@"."""
    by_media, by_attribute = [], []
    for prelude, content in top_level_blocks(DARK_CSS.read_text("utf-8")):
        if prelude.startswith("@media"):
            assert prelude == "@media (prefers-color-scheme: dark)", prelude
            by_media = [(selector.replace(MEDIA_SCOPE, "@"), body)
                        for selector, body in top_level_blocks(content)]
        else:
            by_attribute.append((prelude.replace(ATTRIBUTE_SCOPE, "@"),
                                 content))
    return by_media, by_attribute


class TestCwsPages(unittest.TestCase):

    def test_the_switch_is_on_every_page(self):
        # Scenario: The switch is on every page
        for name, html in pages().items():
            select = re.findall(r'<select id="theme"[^>]*>(.*?)</select>',
                                html, re.S)
            self.assertEqual(len(select), 1, name)
            self.assertEqual(
                re.findall(r'<option value="(\w+)">(\w+)</option>',
                           select[0]),
                [("system", "Sistema"), ("light", "Claro"),
                 ("dark", "Oscuro")], name)

    def test_the_switch_is_present_with_a_single_language(self):
        # Scenario: CWS with a single language
        # (the language select only renders with several languages)
        pages_by_name = pages()
        self.assertNotIn('id="lang"', pages_by_name["login"])
        self.assertIn('id="theme"', pages_by_name["login"])
        self.assertIn('id="lang"', pages_by_name["login with languages"])
        self.assertIn('id="theme"', pages_by_name["login with languages"])

    def test_the_script_loads_in_head_before_the_stylesheets(self):
        # Scenario: A saved dark choice with a light system
        # (a blocking script in <head> before any stylesheet runs before the
        # body is painted)
        for name, html in pages().items():
            head = html[:html.index("</head>")]
            script = head.index('src="static/theme.js"')
            self.assertNotRegex(head[script:script + 40], r"defer|async")
            self.assertLess(script, head.index('<link rel="stylesheet"'),
                            name)
            self.assertNotIn("data-theme", html, name)

    def test_the_dark_overlay_loads_after_the_other_stylesheets(self):
        # Scenario: Choosing Dark or Light
        # (the overlay must come last to override Bootstrap and cws_style)
        for name, html in pages().items():
            order = [html.index(sheet) for sheet in (
                "css/bootstrap.css", "static/cws_style.css",
                "static/cws_dark.css")]
            self.assertEqual(order, sorted(order), name)

    def test_the_dark_overlay_recolors_the_focus_ring_in_both_halves(self):
        # Scenario: Contrast in every theme (bootstrap.css draws the focus
        # ring dotted #333, invisible on the dark surface)
        for half in overlay_rules():
            rules = [selector for selector, body in half
                     if "outline-color: var(--dark-link)" in body]
            self.assertEqual(len(rules), 1)
            for control in ("a", ".btn", "select", 'input[type="checkbox"]',
                            'input[type="radio"]', 'input[type="file"]'):
                self.assertIn("@ %s:focus" % control, rules[0])

    def test_cws_serves_the_script_and_the_overlay(self):
        # The templates refer to static_url("static", ...): CWS must search
        # cms/server/static (theme.js) and cms/server/contest/static.
        class Stop(Exception):
            pass

        config = MagicMock()
        config.web_server.secret_key = "00"
        with patch("cms.server.contest.server.config", config), \
                patch("cms.server.contest.server.WebService.__init__",
                      side_effect=Stop) as web_service_init:
            with self.assertRaises(Stop):
                ContestWebServer(0)
        static_files = web_service_init.call_args.args[2]["static_files"]
        make_url = StaticFileHasher(static_files).make(
            lambda *parts: "/".join(parts))
        for name in ("theme.js", "cws_dark.css"):
            self.assertRegex(make_url("static", name),
                             r"^static/%s\?h=" % re.escape(name))

    def test_ended_contest_link_uses_a_class_not_a_literal_color(self):
        # Scenario: Contrast in every theme (the ended-contest link)
        html = contest_list_page()
        self.assertEqual(html.count('class="ended"'), 1)
        found = []
        for template in TEMPLATES.rglob("*.html"):
            source = template.read_text(encoding="utf-8")
            styles = re.findall(r'style="([^"]*)"', source) \
                + re.findall(r"<style>(.*?)</style>", source, re.S)
            for style in styles:
                found += [(template.name, c)
                          for c in LITERAL_COLOR.findall(style)]
        self.assertEqual(found, [])


class TestCwsStylesheets(unittest.TestCase):

    def setUp(self):
        self.css = CWS_CSS.read_text(encoding="utf-8")

    def test_dark_set_is_declared_for_the_system_and_for_the_choice(self):
        # Scenario: System prefers dark
        # Scenario: Choosing Dark or Light
        # (the same tokens, with the same values, under both selectors)
        by_media = read_block(self.css, DARK_MEDIA)
        by_attribute = read_block(self.css, DARK_ATTRIBUTE)
        self.assertGreater(len(by_media), 0)
        self.assertEqual(by_media, by_attribute)
        self.assertNotEqual(by_media, light_set(self.css))

    def test_dark_applies_by_media_query_only_without_a_light_choice(self):
        # Scenario: System prefers light or nothing
        # (the light values are the default, in a plain :root; dark comes
        # through the media query, and not while the choice is Light)
        self.assertRegex(
            self.css, r"@media \(prefers-color-scheme: dark\) \{\s*%s \{"
            % re.escape(DARK_MEDIA))
        self.assertEqual(self.css.count("--surface:"), 3)

    def test_native_controls_follow_the_theme(self):
        # Scenario: Dark native controls
        self.assertRegex(self.css, r'(?m)^:root \{\s*color-scheme: light dark;')
        self.assertRegex(
            self.css, r'(?s)\[data-theme="light"\] \{\s*color-scheme: light;')
        self.assertRegex(
            self.css, r'(?s)\[data-theme="dark"\] \{\s*color-scheme: dark;')
        self.assertRegex(self.css, r"(?s)%s \{\s*color-scheme: dark;" %
                         re.escape(DARK_MEDIA))

    def test_no_literal_color_is_left_outside_the_tokens(self):
        # Scenario: The three servers cover their pages (CWS)
        # (a color that is not a token would not change with the theme;
        # hsla(var(...)) takes its hue and opacity from tokens)
        css = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        css = re.sub(r"^\s*--[\w-]+:.*$", "", css, flags=re.M)
        css = css.replace("hsla(var(", "")
        self.assertEqual(LITERAL_COLOR.findall(css), [])

    def test_overlay_has_no_rule_outside_the_dark_scopes(self):
        # Scenario: The three servers cover their pages (CWS)
        # (a rule in the overlay that is not scoped would also change the
        # light theme)
        by_media, by_attribute = overlay_rules()
        self.assertGreater(len(by_media), 0)
        for selector, _ in by_media + by_attribute:
            for part in selector.split(","):
                self.assertTrue(part.strip().startswith("@ "), part)

    def test_overlay_is_the_same_for_the_system_and_for_the_choice(self):
        # Scenario: System prefers dark
        # Scenario: Choosing Dark or Light
        by_media, by_attribute = overlay_rules()
        self.assertEqual(by_media, by_attribute)

    def test_overlay_takes_its_colors_from_the_tokens(self):
        # Scenario: The three servers cover their pages (CWS)
        overlay = re.sub(r"/\*.*?\*/", "", DARK_CSS.read_text("utf-8"),
                         flags=re.S)
        self.assertEqual(LITERAL_COLOR.findall(overlay), [])
        dark = read_block(self.css, DARK_ATTRIBUTE)
        for source in (overlay, self.css):
            for token in re.findall(r"var\(--([\w-]+)\)", source):
                self.assertIn(token, dark)

    def test_overlay_covers_the_surfaces_the_templates_use(self):
        # Scenario: The three servers cover their pages (CWS)
        selectors = {part.strip() for selector, _ in overlay_rules()[1]
                     for part in selector.split(",")}
        for covered in ("body", ".navbar-inner", ".table", ".btn-primary",
                        ".btn-warning", ".alert-error", ".alert-success",
                        ".well", ".hero-unit", ".modal", ".nav-tabs",
                        "pre"):
            self.assertTrue(
                any(s.startswith("@ %s" % covered) for s in selectors),
                covered)
        self.assertTrue(
            any("input[type=\"text\"]" in s for s in selectors))

    def test_chevrons_are_inverted_in_dark(self):
        # Scenario: CWS chevrons and RWS ticks
        # (the glyphicons sprite is dark; the white variant is not needed)
        rules = dict(overlay_rules()[1])
        body = rules["@ .icon-chevron-right, @ .icon-chevron-down"]
        self.assertIn("filter: invert(1)", body)


if __name__ == "__main__":
    unittest.main()
