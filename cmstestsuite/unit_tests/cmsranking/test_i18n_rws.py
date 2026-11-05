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

"""Tests for the language of RWS (openspec change ranking-language): the
script i18n.js, the page that loads it and the pages that visibility.py
builds.

The script runs under node against stubs of document, window and
localStorage; nothing here renders a page in a browser, so nothing says how
a page looks. They belong to the gevent group of tests: run them apart from
the asyncio ones.

"""

import importlib.resources
import json
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from cmsranking.visibility import FROZEN_ROUTES
from cmstestsuite.unit_tests.cmsranking import test_visibility


STATIC = importlib.resources.files("cmsranking").joinpath("static")
I18N_JS = STATIC.joinpath("i18n.js")
HTML = STATIC.joinpath("Ranking.html")

# Runs argv[1] (the script) against stubs and prints what the page would
# show. Config (JSON, argv[2]): "stored" (value under cms.lang, or null),
# "theme" (value under cms.theme, or null), "languages" (navigator.languages,
# or null), "language" (navigator.language, or null), "read_throws" /
# "write_throws", "has_select", "choices" (values picked in the select, in
# order), "keys" (keys to look up with I18n.t), "drop_es" (keys removed from
# the Spanish catalog first), "elements" (the elements with data-i18n or
# data-i18n-attr that the page has, as {attrs, text}).
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const cfg = JSON.parse(process.argv[2]);
const store = {};
if (cfg.stored !== null) store["cms.lang"] = cfg.stored;
if (cfg.theme !== null) store["cms.theme"] = cfg.theme;
const attributes = {};
const listeners = {};
let reloads = 0;
const select = {
    value: "",
    listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; },
};
const elements = cfg.elements.map((e) => ({
    attrs: Object.assign({}, e.attrs), textContent: e.text,
    getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; },
    setAttribute(name, value) { this.attrs[name] = value; },
}));
const document = {
    documentElement: {
        setAttribute(name, value) { attributes[name] = value; },
        removeAttribute(name) { delete attributes[name]; },
    },
    getElementById(id) { return id === "language" && cfg.has_select ? select : null; },
    querySelectorAll(selector) {
        const name = selector.slice(1, -1);
        return elements.filter((e) => name in e.attrs);
    },
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
const navigator = {};
if (cfg.languages !== null) navigator.languages = cfg.languages;
if (cfg.language !== null) navigator.language = cfg.language;
const window = { localStorage, navigator, location: { reload() { reloads++; } } };
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"),
                   { document, window, navigator });
const out = { early: { lang: attributes["lang"] || null,
                       data_lang: attributes["data-lang"] || null },
              I18n_lang: window.I18n.lang };
for (const key of cfg.drop_es) delete window.I18n.catalog.es[key];
out.texts = cfg.keys.map((key) => window.I18n.t(key));
listeners.DOMContentLoaded();
out.select_value = select.value;
out.filled = elements.map((e) => ({ attrs: e.attrs, text: e.textContent }));
out.after_choices = cfg.choices.map((choice) => {
    select.value = choice;
    select.listeners.change();
    return { stored: store["cms.lang"] || null, reloads: reloads };
});
out.catalog = window.I18n.catalog;
out.theme = { stored: store["cms.theme"] || null,
              data_theme: attributes["data-theme"] || null };
console.log(JSON.stringify(out));
"""


def run_script(stored=None, theme=None, languages=None, language=None,
               read_throws=False, write_throws=False, has_select=True,
               choices=(), keys=(), drop_es=(), elements=()):
    config = {
        "stored": stored, "theme": theme, "languages": languages,
        "language": language, "read_throws": read_throws,
        "write_throws": write_throws, "has_select": has_select,
        "choices": list(choices), "keys": list(keys),
        "drop_es": list(drop_es), "elements": list(elements)}
    result = subprocess.run(
        ["node", "-e", HARNESS, str(I18N_JS), json.dumps(config)],
        capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestLanguageScript(unittest.TestCase):

    def test_browser_in_spanish(self):
        # Scenario: Browser in Spanish
        # (only the first preferred language counts)
        for kwargs in ({"languages": ["es-MX"]}, {"languages": ["ES"]},
                       {"languages": ["es-MX", "en-US"]},
                       {"language": "es"}):
            out = run_script(**kwargs)
            self.assertEqual(out["early"], {"lang": "es", "data_lang": "es"},
                             kwargs)
            self.assertEqual(out["I18n_lang"], "es", kwargs)

    def test_browser_in_english_or_another_language(self):
        # Scenario: Browser in English or another language
        for kwargs in ({"languages": ["en-US"]}, {"languages": ["fr"]},
                       {"languages": ["fr", "es"]}, {"languages": []},
                       {"language": "pt-BR"}, {}):
            out = run_script(**kwargs)
            self.assertEqual(out["early"], {"lang": "en", "data_lang": "en"},
                             kwargs)
            self.assertEqual(out["I18n_lang"], "en", kwargs)

    def test_the_saved_choice_beats_the_browser(self):
        # Scenario: Choosing a language
        # Scenario: The choice survives a reload
        # (the pick is made from what is stored, before the DOM is ready)
        self.assertEqual(
            run_script(stored="en", languages=["es-MX"])["early"]["lang"],
            "en")
        self.assertEqual(
            run_script(stored="es", languages=["en-US"])["early"]["lang"],
            "es")
        # any other stored value means automatic
        self.assertEqual(
            run_script(stored="garbage", languages=["es"])["early"]["lang"],
            "es")
        self.assertEqual(
            run_script(stored="garbage", languages=["fr"])["early"]["lang"],
            "en")

    def test_the_select_shows_the_resolved_language_and_saves_and_reloads(
            self):
        # Scenario: The switch is on the ranking page (current language)
        # Scenario: Choosing a language
        self.assertEqual(
            run_script(languages=["es"])["select_value"], "es")
        self.assertEqual(
            run_script(stored="en", languages=["es"])["select_value"], "en")
        out = run_script(languages=["en"], choices=["es", "en"])
        self.assertEqual(out["after_choices"], [
            {"stored": "es", "reloads": 1},
            {"stored": "en", "reloads": 2},
        ])

    def test_unavailable_storage_does_not_stop_the_page(self):
        # Scenario: Local storage is unavailable
        out = run_script(read_throws=True, languages=["es-MX"])
        self.assertEqual(out["early"]["lang"], "es")
        self.assertEqual(out["select_value"], "es")
        out = run_script(write_throws=True, languages=["en"], choices=["es"])
        self.assertEqual(out["after_choices"], [{"stored": None, "reloads": 1}])

    def test_a_page_without_the_select_still_picks_the_language(self):
        # Scenario: The choice survives a reload (the notice pages and any
        # page without the switch)
        out = run_script(stored="es", has_select=False)
        self.assertEqual(out["early"]["lang"], "es")

    def test_theme_and_language_are_independent(self):
        # Scenario: Theme and language are independent
        # (the script never reads or writes cms.theme or data-theme)
        out = run_script(theme="dark", choices=["es", "en"])
        self.assertEqual(out["theme"], {"stored": "dark", "data_theme": None})
        source = I18N_JS.read_text(encoding="utf-8")
        self.assertNotIn("cms.theme", source)
        self.assertNotIn("data-theme", source)

    def test_the_choice_never_leaves_the_browser(self):
        # Scenario: The choice survives a reload (one localStorage key, no
        # cookie and no request that could send it anywhere)
        source = I18N_JS.read_text(encoding="utf-8")
        self.assertIn('"cms.lang"', source)
        for sender in ("cookie", "XMLHttpRequest", "fetch", "sendBeacon"):
            self.assertNotIn(sender, source)

    def test_lookup_falls_back_to_english_then_to_the_key(self):
        # Scenario: Both catalogs are complete (what a missing entry shows)
        out = run_script(
            languages=["es"], keys=["label.status", "no.such.key"],
            drop_es=["label.status"])
        self.assertEqual(out["texts"], ["Status", "no.such.key"])
        out = run_script(languages=["es"], keys=["label.status"])
        self.assertEqual(out["texts"], ["Estado"])

    def test_static_texts_are_filled_from_the_markers(self):
        # Scenario: Browser in Spanish (the markers of Ranking.html)
        elements = [
            {"attrs": {"data-i18n": "label.status"}, "text": "Status"},
            {"attrs": {"data-i18n-attr": "alt:alt.close,title:alt.close",
                       "alt": "Close"}, "text": ""},
        ]
        out = run_script(languages=["es"], elements=elements)
        self.assertEqual(out["filled"][0]["text"], "Estado")
        self.assertEqual(out["filled"][1]["attrs"]["alt"], "Cerrar")
        self.assertEqual(out["filled"][1]["attrs"]["title"], "Cerrar")
        # in English the text stays what the file has
        out = run_script(languages=["en"], elements=elements)
        self.assertEqual(out["filled"][0]["text"], "Status")
        self.assertEqual(out["filled"][1]["attrs"]["alt"], "Close")


class ElementCollector(HTMLParser):
    """Collect the elements of a page with their attributes and own text."""

    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append({"tag": tag, "attrs": dict(attrs), "text": ""})

    def handle_data(self, data):
        if self.elements:
            self.elements[-1]["text"] += data


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestRankingPage(unittest.TestCase):

    def setUp(self):
        self.html = HTML.read_text(encoding="utf-8")
        collector = ElementCollector()
        collector.feed(self.html)
        self.elements = collector.elements
        self.catalog = run_script()["catalog"]

    def test_the_switch_is_in_the_top_bar_next_to_the_theme_switch(self):
        # Scenario: The switch is on the ranking page
        panel = re.search(r'<div id="UpperPanel">(.*?)<div id="TimeView">',
                          self.html, re.S).group(1)
        select = re.findall(r'<select id="language"[^>]*>(.*?)</select>',
                            panel, re.S)
        self.assertEqual(len(select), 1)
        self.assertEqual(
            re.findall(r'<option value="(\w+)">(\w+)</option>', select[0]),
            [("es", "Español"), ("en", "English")])
        self.assertEqual(self.html.count('id="language"'), 1)
        self.assertLess(panel.index('id="theme"'), panel.index('id="language"'))
        # the page is lang="en" and the labels are Spanish
        self.assertRegex(self.html, r'<select id="language"[^>]* lang="es"')

    def test_the_script_loads_in_head_right_after_the_theme(self):
        # Scenario: Browser in Spanish (the language is set before the
        # scripts that call I18n.t, and before the body is painted)
        head = self.html[:self.html.index("</head>")]
        script = head.index('src="i18n.js"')
        self.assertNotRegex(head[script:script + 40], r"defer|async")
        self.assertLess(head.index('src="theme.js"'), script)
        self.assertLess(script, head.index("Ranking.css"))
        self.assertLess(script, head.index('src="DataStore.js"'))

    def test_every_marker_has_both_texts_and_the_english_fallback(self):
        # Scenario: Both catalogs are complete (the markers of the page)
        # (the file keeps the English text, which shows without scripts)
        marked = 0
        for element in self.elements:
            attrs = element["attrs"]
            if "data-i18n" in attrs:
                marked += 1
                key = attrs["data-i18n"]
                for lang in ("en", "es"):
                    self.assertIn(key, self.catalog[lang])
                self.assertEqual(element["text"].strip(),
                                 self.catalog["en"][key], key)
            for pair in attrs.get("data-i18n-attr", "").split(","):
                if not pair:
                    continue
                marked += 1
                attribute, key = pair.split(":")
                for lang in ("en", "es"):
                    self.assertIn(key, self.catalog[lang])
                self.assertEqual(attrs[attribute], self.catalog["en"][key], key)
        self.assertGreaterEqual(marked, 15)


class TestRwsServesTheLanguageScript(test_visibility.VisibilityTestCase):

    def test_the_frozen_group_passes_the_script(self):
        # Scenario: Frozen ranking (and the live one, like theme.js)
        self.assertEqual(FROZEN_ROUTES["i18n.js"], "pass")
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
                response = self.client.get("/olim/i18n.js")
            self.assertEqual(response.status_code, 200, state)
            self.assertEqual(response.get_data(), I18N_JS.read_bytes(), state)


if __name__ == "__main__":
    unittest.main()
