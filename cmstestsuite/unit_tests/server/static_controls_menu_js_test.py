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

"""Tests for the script of the controls menu shared by AWS, CWS and RWS.

The script runs under node against stubs of document and of the elements;
no browser is involved, so nothing here says how the menu looks.

"""

import importlib.resources
import json
import shutil
import subprocess
import unittest


CONTROLS_MENU_JS = importlib.resources.files("cms.server").joinpath(
    "static", "controls_menu.js")

# Runs argv[1] (the script) against stubs and prints what the page would show.
# Config (JSON, argv[2]): "has_menu" (the page has a .controls_menu),
# "menu_open"/"other_open" (initial state of the menu and of an unrelated
# <details>), "events" (list of "escape", "click_outside", "click_panel",
# "click_summary", "click_other", fired in order).
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const cfg = JSON.parse(process.argv[2]);
const listeners = {};
const state = { focused: null };

function makeNode(parent) {
    return {
        parent, open: false,
        contains(node) {
            for (; node; node = node.parent) {
                if (node === this) return true;
            }
            return false;
        },
    };
}
const outside = makeNode(null);
const menu = makeNode(null);
const summary = makeNode(menu);
const panel = makeNode(menu);
const other = makeNode(null);
const otherChild = makeNode(other);
menu.open = cfg.menu_open;
other.open = cfg.other_open;
summary.focus = () => { state.focused = "summary"; };
menu.querySelector = (selector) => selector === "summary" ? summary : null;

// Only the selectors a correct script may use; a looser one such as
// "details[open]" also returns the unrelated <details>.
const document = {
    querySelectorAll(selector) {
        if (selector === "details.controls_menu[open]") {
            return cfg.has_menu && menu.open ? [menu] : [];
        }
        if (selector === "details[open]" || selector === "details") {
            return [menu, other].filter((node) => node.open);
        }
        return [];
    },
    addEventListener(name, fn) { listeners[name] = fn; },
};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), { document });
listeners.DOMContentLoaded();

const targets = { click_outside: outside, click_panel: panel,
                  click_summary: summary, click_other: otherChild };
cfg.events.forEach((event) => {
    if (event === "escape") {
        listeners.keydown({ key: "Escape" });
    } else {
        listeners.click({ target: targets[event] });
    }
});
console.log(JSON.stringify({
    menu_open: menu.open, other_open: other.open, focused: state.focused,
    listeners: Object.keys(listeners).sort(),
}));
"""


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestControlsMenuScript(unittest.TestCase):

    def run_script(self, events=(), has_menu=True, menu_open=True,
                   other_open=True):
        config = {
            "has_menu": has_menu, "menu_open": menu_open,
            "other_open": other_open, "events": list(events)}
        result = subprocess.run(
            ["node", "-e", HARNESS, str(CONTROLS_MENU_JS),
             json.dumps(config)],
            capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_escape_closes_the_open_menu_and_focuses_the_summary(self):
        # Scenario: Escape closes the menu
        out = self.run_script(events=["escape"])
        self.assertFalse(out["menu_open"])
        self.assertEqual(out["focused"], "summary")

    def test_escape_with_the_menu_closed_does_nothing(self):
        out = self.run_script(events=["escape"], menu_open=False)
        self.assertFalse(out["menu_open"])
        self.assertIsNone(out["focused"])

    def test_a_click_outside_closes_it(self):
        # Scenario: A click outside closes the menu
        out = self.run_script(events=["click_outside"])
        self.assertFalse(out["menu_open"])

    def test_a_click_inside_the_panel_keeps_it_open(self):
        # Scenario: Using a control keeps the menu open
        out = self.run_script(events=["click_panel"])
        self.assertTrue(out["menu_open"])

    def test_a_click_on_the_summary_is_left_to_the_browser(self):
        # Scenario: The icon opens and closes the menu
        # (the browser toggles `open`; the script must not touch it)
        out = self.run_script(events=["click_summary"])
        self.assertTrue(out["menu_open"])
        out = self.run_script(events=["click_summary"], menu_open=False)
        self.assertFalse(out["menu_open"])

    def test_other_details_are_never_closed(self):
        # Scenario: Other disclosure widgets are untouched
        out = self.run_script(events=["escape", "click_outside",
                                      "click_other"])
        self.assertFalse(out["menu_open"])
        self.assertTrue(out["other_open"])

    def test_a_page_without_the_menu_still_loads(self):
        out = self.run_script(events=["escape", "click_outside"],
                              has_menu=False)
        self.assertTrue(out["other_open"])
        self.assertEqual(out["listeners"],
                         ["DOMContentLoaded", "click", "keydown"])


class TestControlsMenuScriptFile(unittest.TestCase):

    def test_the_ranking_has_an_identical_copy(self):
        # RWS cannot reach cms/server/static, so it serves its own copy.
        copy = importlib.resources.files("cmsranking").joinpath(
            "static", "controls_menu.js")
        self.assertEqual(copy.read_bytes(), CONTROLS_MENU_JS.read_bytes())


if __name__ == "__main__":
    unittest.main()
