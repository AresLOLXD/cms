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

"""Tests for the theme script shared by CWS and AWS (cms.theme).

The script runs under node against stubs of document, window and
localStorage; no browser is involved, so nothing here says how a page looks.

"""

import importlib.resources
import json
import shutil
import subprocess
import unittest
from unittest.mock import patch

from cms.io.web_service import StaticFileHasher
from cms.server.admin.server import AdminWebServer


THEME_JS = importlib.resources.files("cms.server").joinpath(
    "static", "theme.js")

# Runs argv[1] (the script) against stubs and prints what the page would show.
# Config (JSON, argv[2]): "stored" (value under cms.theme, or null),
# "read_throws"/"write_throws", "has_select", "choices" (values picked in the
# select, in order).
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const cfg = JSON.parse(process.argv[2]);
const store = {};
if (cfg.stored !== null) store["cms.theme"] = cfg.stored;
const attributes = {};
const listeners = {};
const select = {
    value: "system",
    listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; },
};
const document = {
    documentElement: {
        setAttribute(name, value) { attributes[name] = value; },
        removeAttribute(name) { delete attributes[name]; },
    },
    getElementById(id) { return id === "theme" && cfg.has_select ? select : null; },
    addEventListener(name, fn) { listeners[name] = fn; },
};
const localStorage = {
    getItem(key) {
        if (cfg.read_throws) throw new Error("blocked");
        return key in store ? store[key] : null;
    },
    setItem(key, value) {
        if (cfg.write_throws) throw new Error("blocked");
        store[key] = value;
    },
    removeItem(key) {
        if (cfg.write_throws) throw new Error("blocked");
        delete store[key];
    },
};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"),
                   { document, window: { localStorage } });
const out = { early: attributes["data-theme"] || null };
listeners.DOMContentLoaded();
out.select_value = select.value;
out.after_choices = cfg.choices.map((choice) => {
    select.value = choice;
    select.listeners.change();
    return { theme: attributes["data-theme"] || null,
             stored: store["cms.theme"] || null };
});
console.log(JSON.stringify(out));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestThemeScript(unittest.TestCase):

    def run_script(self, stored=None, read_throws=False, write_throws=False,
                   has_select=True, choices=()):
        config = {
            "stored": stored, "read_throws": read_throws,
            "write_throws": write_throws, "has_select": has_select,
            "choices": list(choices)}
        result = subprocess.run(
            ["node", "-e", HARNESS, str(THEME_JS), json.dumps(config)],
            capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_saved_dark_is_set_before_the_dom_is_ready(self):
        # Scenario: A saved dark choice with a light system
        # (the attribute is read before DOMContentLoaded is fired; the
        # system preference never enters the script)
        self.assertEqual(self.run_script(stored="dark")["early"], "dark")
        self.assertEqual(self.run_script(stored="light")["early"], "light")

    def test_no_saved_choice_leaves_the_media_query_to_decide(self):
        # Scenario: System prefers dark
        # Scenario: System prefers light or nothing
        for stored in (None, "system", "garbage"):
            self.assertIsNone(self.run_script(stored=stored)["early"])

    def test_the_select_shows_the_saved_choice(self):
        # Scenario: The switch is on every page (current choice selected)
        for stored, shown in ((None, "system"), ("system", "system"),
                              ("dark", "dark"), ("light", "light")):
            self.assertEqual(
                self.run_script(stored=stored)["select_value"], shown)

    def test_choosing_dark_light_and_system(self):
        # Scenario: Choosing Dark or Light
        # Scenario: Choosing System
        # Scenario: The choice survives a reload and other pages
        # (what is stored is what the early apply reads on the next page)
        out = self.run_script(choices=["dark", "light", "dark", "system"])
        self.assertEqual(out["after_choices"], [
            {"theme": "dark", "stored": "dark"},
            {"theme": "light", "stored": "light"},
            {"theme": "dark", "stored": "dark"},
            {"theme": None, "stored": None},
        ])

    def test_a_page_without_the_select_still_applies_the_theme(self):
        # Scenario: The choice survives a reload and other pages
        out = self.run_script(stored="dark", has_select=False)
        self.assertEqual(out["early"], "dark")

    def test_unavailable_storage_does_not_stop_the_page(self):
        # Scenario: Local storage is unavailable
        out = self.run_script(read_throws=True)
        self.assertIsNone(out["early"])
        self.assertEqual(out["select_value"], "system")
        out = self.run_script(write_throws=True, choices=["dark", "system"])
        self.assertEqual(out["after_choices"], [
            {"theme": "dark", "stored": None},
            {"theme": None, "stored": None},
        ])


class TestThemeScriptFile(unittest.TestCase):

    def test_the_choice_never_leaves_the_browser(self):
        # Scenario: Another server is independent
        # (one localStorage key, which the browser keeps per origin, and no
        # cookie or request that could send it anywhere)
        source = THEME_JS.read_text(encoding="utf-8")
        self.assertIn('"cms.theme"', source)
        for sender in ("cookie", "XMLHttpRequest", "fetch", "sendBeacon"):
            self.assertNotIn(sender, source)

    def test_rws_has_an_identical_copy(self):
        # RWS cannot reach cms/server/static, so it serves its own copy.
        copy = importlib.resources.files("cmsranking").joinpath(
            "static", "theme.js")
        self.assertEqual(copy.read_bytes(), THEME_JS.read_bytes())

    def test_aws_serves_the_script(self):
        # The templates of AWS refer to it as static_url("static",
        # "theme.js"): AWS must search cms/server/static, where it lives.
        class Stop(Exception):
            pass

        with patch("cms.server.admin.server.WebService.__init__",
                   side_effect=Stop) as web_service_init:
            with self.assertRaises(Stop):
                AdminWebServer(0)
        static_files = web_service_init.call_args.args[2]["static_files"]
        url = StaticFileHasher(static_files).make(lambda *p: "/".join(p))(
            "static", "theme.js")
        self.assertRegex(url, r"^static/theme\.js\?h=")


if __name__ == "__main__":
    unittest.main()
