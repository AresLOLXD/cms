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

"""Tests for the color theme of AWS: the pages and the stylesheets.

Nothing here renders a page in a browser; the tests read the templates and
the CSS text.

"""

import importlib.resources
import re
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.server.themecss import AWS_CSS, DARK_ATTRIBUTE, \
    DARK_MEDIA, LITERAL_COLOR, light_set, read_block


ADMIN_DIR = importlib.resources.files("cms.server.admin")
PRISM_CSS = ADMIN_DIR.joinpath("static", "prism.css")


def render_page(page: str, **params) -> str:
    params.setdefault("static_url", lambda *parts: "/".join(parts))
    params.setdefault("url", lambda *parts: "/" + "/".join(map(str, parts)))
    params.setdefault("xsrf_form_html", "")
    params.setdefault("timestamp", None)
    return AWS_ENVIRONMENT.get_template(page).render(**params)


def sidebar_pages() -> dict[str, str]:
    """Return the html of the pages that have a header: the base page logged
    out and logged in, and the standalone login page."""
    admin = SimpleNamespace(name="root", permission_all=True)
    config = SimpleNamespace(web_server=SimpleNamespace(
        secret_key="key", DEFAULT_SECRET_KEY="default"))
    handler = MagicMock()
    handler.get_argument.return_value = ""
    base = dict(contest=None, timestamp=datetime(2026, 1, 1), config=config)
    return {
        "base logged out": render_page("base.html", **base),
        "base logged in": render_page("base.html", admin=admin, **base),
        "login": render_page("login.html", handler=handler),
    }


class TestAwsPages(unittest.TestCase):

    def test_the_switch_is_on_every_page(self):
        # Scenario: The switch is on every page
        for name, html in sidebar_pages().items():
            select = re.search(r'<select id="theme"[^>]*>(.*?)</select>',
                               html, re.S)
            self.assertIsNotNone(select, name)
            # the pages are lang="en" and the labels are Spanish
            self.assertRegex(html, r'<select id="theme"[^>]* lang="es"', name)
            self.assertEqual(
                re.findall(r'<option value="(\w+)">(\w+)</option>',
                           select.group(1)),
                [("system", "Sistema"), ("light", "Claro"),
                 ("dark", "Oscuro")], name)

    def test_the_script_loads_in_head_before_the_stylesheets(self):
        # Scenario: A saved dark choice with a light system
        # (a blocking script in <head> before any stylesheet runs before the
        # body is painted)
        for name, html in sidebar_pages().items():
            head = html[:html.index("</head>")]
            script = head.index('src="static/theme.js"')
            self.assertNotRegex(head[script:script + 40], r"defer|async")
            self.assertLess(script, head.index("<link rel=\"stylesheet\""),
                            name)
            self.assertNotIn("data-theme", html, name)


class TestAwsStylesheet(unittest.TestCase):

    def setUp(self):
        self.css = AWS_CSS.read_text(encoding="utf-8")

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
        self.assertEqual(self.css.count("--page-bg:"), 3)

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
        # Scenario: The three servers cover their pages (AWS)
        # (a color that is not a token would not change with the theme)
        css = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        css = re.sub(r"^\s*--[\w-]+:.*$", "", css, flags=re.M)
        self.assertEqual(LITERAL_COLOR.findall(css), [])

    def test_templates_have_no_literal_color(self):
        # Scenario: The three servers cover their pages (AWS)
        # (inline style attributes and <style> blocks)
        found = []
        for template in ADMIN_DIR.joinpath("templates").rglob("*.html"):
            html = template.read_text(encoding="utf-8")
            styles = re.findall(r'style="([^"]*)"', html) \
                + re.findall(r"<style>(.*?)</style>", html, re.S)
            for style in styles:
                found += [(template.name, c)
                          for c in LITERAL_COLOR.findall(style)]
        self.assertEqual(found, [])

    def test_charts_stay_on_a_light_panel_in_both_themes(self):
        # Scenario: A chart in dark theme
        resources = ADMIN_DIR.joinpath("templates", "resources.html") \
            .read_text(encoding="utf-8")
        for chart in ("cpu", "memory", "swap"):
            self.assertRegex(
                resources, r'<div id="%s_\{\{ i \}\}" class="chart_panel"'
                % chart)
        panel = re.search(r"\.chart_panel \{(.*?)\}", self.css, re.S).group(1)
        self.assertIn("var(--chart-bg)", panel)
        light, dark = light_set(self.css), read_block(self.css, DARK_ATTRIBUTE)
        for token in ("chart-bg", "chart-text"):
            self.assertEqual(light[token], dark[token])

    def test_code_highlighting_reads_the_theme_tokens(self):
        # Scenario: The three servers cover their pages (AWS code highlighting)
        prism = PRISM_CSS.read_text(encoding="utf-8")
        for token in ("code-bg", "code-text", "code-keyword", "code-string"):
            self.assertIn("var(--%s)" % token, prism)


if __name__ == "__main__":
    unittest.main()
