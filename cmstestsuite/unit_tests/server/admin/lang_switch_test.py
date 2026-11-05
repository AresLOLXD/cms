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

"""Tests for the language selector of AWS.

The pages are rendered from the templates and the inline function runs
under node against stubs; no browser is involved, so nothing here says how
a page looks.

"""

import json
import re
import shutil
import subprocess
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from cms.locale import DEFAULT_TRANSLATION, Translation
from cmstestsuite.unit_tests.server.admin.theme_aws_test import render_page


SPANISH = Translation("es")
OFFERED = {"en": DEFAULT_TRANSLATION, "es": SPANISH}


def login_page(**params) -> str:
    handler = MagicMock()
    handler.get_argument.return_value = ""
    return render_page("login.html", handler=handler,
                       available_translations=OFFERED, **params)


def error_page(**params) -> str:
    config = SimpleNamespace(web_server=SimpleNamespace(
        secret_key="key", DEFAULT_SECRET_KEY="default"))
    return render_page("error.html", status_code=500, contest=None,
                       timestamp=datetime(2026, 1, 1), config=config,
                       available_translations=OFFERED, **params)


def selector(html: str) -> str:
    match = re.search(r'<select id="lang".*?</select>', html, re.S)
    assert match is not None
    return match.group(0)


class TestSelectorOnThePages(unittest.TestCase):

    def test_the_selector_is_on_the_login_and_error_pages(self):
        # Scenario: The selector is on the login and error pages
        for name, html in (("login", login_page()), ("error", error_page())):
            self.assertEqual(
                re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>',
                           selector(html)),
                [("", "Automatic (English)"), ("en", "English"),
                 ("es", "español")], name)

    def test_the_selected_option_is_the_cookie_language(self):
        # Scenario: The cookie wins (the selector shows what was chosen)
        automatic = login_page()
        self.assertRegex(selector(automatic), r'<option value="" selected>')
        chosen = login_page(cookie_translation=SPANISH)
        self.assertRegex(selector(chosen), r'<option value="es" selected>')
        self.assertNotIn('value="" selected', selector(chosen))

    def test_the_page_declares_its_language(self):
        # Scenario: Browser in Spanish / Browser in English or another
        # language (the lang attribute; Content-Language is in language_test)
        for page in (login_page, error_page):
            self.assertIn('<html lang="en">', page())
            self.assertIn('<html lang="es">', page(translation=SPANISH))


# Runs the inline function against stubs; argv[2] is the chosen value.
HARNESS = r"""
const vm = require("vm");
const cookies = [];
let reloads = 0;
const context = {
    Date,
    document: { set cookie(value) { cookies.push(value); } },
    location: { reload() { reloads += 1; } },
};
vm.runInNewContext(process.argv[1] + "\nswitchLanguage(process.argv[2]);",
                   { ...context, process });
console.log(JSON.stringify({ cookies, reloads }));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestSwitchLanguage(unittest.TestCase):

    def choose(self, value: str) -> dict:
        script = re.search(r"<script>(.*?)</script>", login_page(), re.S)
        result = subprocess.run(
            ["node", "-e", HARNESS, script.group(1), value],
            capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_choosing_a_language(self):
        # Scenario: Choosing a language
        result = self.choose("es")

        self.assertEqual(result["reloads"], 1)
        [cookie] = result["cookies"]
        self.assertTrue(cookie.startswith("aws_language=es; expires="))
        self.assertTrue(cookie.endswith("; path=/"))
        expires = re.search(r"expires=([^;]+);", cookie).group(1)
        days = (datetime.strptime(expires, "%a, %d %b %Y %H:%M:%S GMT")
                .replace(tzinfo=timezone.utc)
                - datetime.now(timezone.utc)).days
        self.assertIn(days, (364, 365, 366))

    def test_choosing_automatic(self):
        # Scenario: Choosing Automatic
        result = self.choose("")

        self.assertEqual(result["reloads"], 1)
        self.assertEqual(
            result["cookies"],
            ["aws_language=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/"])


if __name__ == "__main__":
    unittest.main()
