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
import tempfile
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from werkzeug.test import Client

from cmsranking.visibility import FROZEN_ROUTES, NOTICE_THEME, STAFF_BANNER, \
    STAFF_FROZEN_BANNER, VisibilityGuard, VisibilitySettings, \
    public_frozen_banner
from cmstestsuite.unit_tests.cmsranking import test_visibility


STATIC = importlib.resources.files("cmsranking").joinpath("static")
I18N_JS = STATIC.joinpath("i18n.js")
HTML = STATIC.joinpath("Ranking.html")
CSS = STATIC.joinpath("Ranking.css")
RANKING_THEME_JS = STATIC.joinpath("theme.js")
CMS_THEME_JS = importlib.resources.files("cms.server").joinpath(
    "static", "theme.js")

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
                       {"languages": ["ca-ES"]}, {"languages": ["eu-ES"]},
                       {"languages": ["gl-ES"]},
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
        self.assertEqual(out["after_choices"],
                         [{"stored": None, "reloads": 1}])

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
        # ... and the theme scripts never touch the language
        for theme_js in (RANKING_THEME_JS, CMS_THEME_JS):
            self.assertNotIn("cms.lang", theme_js.read_text(encoding="utf-8"))

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
            re.findall(r'<option value="(\w+)"(?: lang="\w+")?>(\w+)</option>',
                       select[0]),
            [("es", "Español"), ("en", "English")])
        self.assertEqual(self.html.count('id="language"'), 1)
        self.assertLess(panel.index('id="theme"'),
                        panel.index('id="language"'))
        # the page is lang="en" and the labels are Spanish
        self.assertRegex(self.html, r'<select id="language"[^>]* lang="es"')
        # the English label is read in English by screen readers
        self.assertIn('<option value="en" lang="en">English</option>',
                      self.html)

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
                self.assertEqual(attrs[attribute], self.catalog["en"][key],
                                 key)
        self.assertGreaterEqual(marked, 15)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestScriptTexts(unittest.TestCase):

    def setUp(self):
        self.catalog = run_script()["catalog"]
        self.sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in STATIC.iterdir()
            if path.name.endswith(".js") and path.name != "i18n.js"}

    def test_both_catalogs_are_complete(self):
        # Scenario: Both catalogs are complete
        self.assertEqual(set(self.catalog["en"]), set(self.catalog["es"]))
        for lang, texts in self.catalog.items():
            for key, text in texts.items():
                self.assertTrue(text.strip(), (lang, key))
        used = set()
        for name, source in self.sources.items():
            # the argument of every call is a literal, never a data value
            self.assertNotRegex(source, r"I18n\.t\((?!\")", name)
            used.update(re.findall(r'I18n\.t\("([^"]+)"\)', source))
        self.assertGreater(len(used), 20)
        self.assertEqual(used - set(self.catalog["en"]), set())

    def test_no_user_text_is_left_in_the_scripts(self):
        # Scenario: Both catalogs are complete (a text moved to the catalog
        # is not also left in the scripts)
        # (only the keys that the scripts use: the alt texts and some labels
        # are static text of Ranking.html)
        used = {key for source in self.sources.values()
                for key in re.findall(r'I18n\.t\("([^"]+)"\)', source)}
        for key in used:
            text = self.catalog["en"][key]
            if key.startswith("conn.") or "<" in text:
                continue
            for name, source in self.sources.items():
                self.assertNotIn('"%s"' % text, source, (name, key))
                self.assertNotIn(">%s<" % text, source, (name, key))

    def test_connection_messages_keep_their_links(self):
        # Scenario: Connection messages keep their links
        # (the same onclick code in both languages, and DataStore.js
        # inserts them as markup)
        links = 0
        for key, text in self.catalog["en"].items():
            if "<a" not in text:
                continue
            links += 1
            onclick = re.findall(r'<a onclick="([^"]+)">', text)
            self.assertEqual(len(onclick), 1, key)
            self.assertEqual(
                re.findall(r'<a onclick="([^"]+)">', self.catalog["es"][key]),
                onclick, key)
            self.assertIn('.html(I18n.t("%s"))' % key,
                          self.sources["DataStore.js"])
        self.assertEqual(links, 3)

    def test_data_names_are_untouched(self):
        # Scenario: Data names are untouched
        # (the user detail still builds its title from the names of the
        # data, after the catalog prefix, and no data goes through t())
        source = self.sources["UserDetail.js"]
        self.assertIn(
            'I18n.t("title.user_prefix") + self.user["f_name"] + " " '
            '+ self.user["l_name"]', source)
        self.assertIn("contest['name']", source)
        self.assertIn("task['name']", source)
        for name, source in self.sources.items():
            self.assertNotRegex(source, r"I18n\.t\([^\")]*\[", name)


# Runs the early snippet of a notice page (argv[1]) against stubs and prints
# what it sets. Config (JSON, argv[2]): "stored" (cms.lang or null),
# "languages", "language" (as in HARNESS), "throws" (local storage fails).
SNIPPET_HARNESS = r"""
const vm = require("vm");
const cfg = JSON.parse(process.argv[2]);
const attributes = {};
const title = { textContent: "Ranking oculto",
                getAttribute(name) { return "Hidden ranking"; } };
const localStorage = {
    getItem(key) {
        if (cfg.throws) throw new Error("blocked");
        return key === "cms.lang" ? cfg.stored : null;
    },
};
const navigator = {};
if (cfg.languages !== null) navigator.languages = cfg.languages;
if (cfg.language !== null) navigator.language = cfg.language;
const document = {
    documentElement: { setAttribute(name, value) { attributes[name] = value; } },
    querySelector(selector) { return selector === "title" ? title : null; },
};
vm.runInNewContext(process.argv[1], { document, localStorage, navigator });
console.log(JSON.stringify({ lang: attributes["lang"] || null,
                             data_lang: attributes["data-lang"] || null,
                             title: title.textContent }));
"""

# Inputs shared by the parity test: (stored, languages, language).
PICKS = [
    (None, ["es-MX"], None), (None, ["ES"], None), (None, ["es", "en"], None),
    (None, None, "es"), (None, ["en-US"], None), (None, ["fr", "es"], None),
    (None, [], None), (None, None, "pt-BR"), (None, None, None),
    # contains "es" without starting with it: English
    (None, ["ca-ES"], None), (None, ["eu-ES"], None),
    (None, ["gl-ES"], None),
    ("en", ["es-MX"], None), ("es", ["en-US"], None),
    ("garbage", ["es"], None), ("garbage", ["fr"], None), ("", ["es"], None),
]


def run_snippet(script, stored, languages, language, throws=False):
    config = {"stored": stored, "languages": languages,
              "language": language, "throws": throws}
    result = subprocess.run(
        ["node", "-e", SNIPPET_HARNESS, script, json.dumps(config)],
        capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def notice_snippet():
    return re.search(r"<script>(.*?)</script>", NOTICE_THEME, re.S).group(1)


LANG_ES_HIDDEN = r':root\[data-lang="en"\] \.lang-es \{\s*display:\s*none;'
LANG_EN_HIDDEN = (r':root:not\(\[data-lang="en"\]\) \.lang-en \{\s*'
                  r'display:\s*none;')


class TestRwsBuiltInPages(test_visibility.VisibilityTestCase):
    """The notice and the staff login, as a hidden and a frozen group serve
    them."""

    def setUp(self):
        super().setUp()
        self.put_contest("/olim")

    def freeze(self):
        self.client.put("/olim/visibility", data=json.dumps({
            "hide_at": None, "show_at": None, "freeze_at": 200,
            "unfreeze_at": 400,
            "staff_password": test_visibility.STAFF_HASH}),
            content_type="application/json", headers=test_visibility.AUTH)

    def pages(self) -> dict[str, str]:
        """Return the html of the hidden notice, the notice that answers a
        failed login and the staff login of a frozen group."""
        self.put_visibility("olim", True)
        pages = {"hidden notice": self.client.get("/olim/")}
        with patch("cmsranking.visibility.gevent.sleep"):
            pages["failed login"] = self.client.post(
                "/olim/staff-login", data={"password": "wrong"})
        self.put_visibility("olim", False)
        self.freeze()
        with patch("cmsranking.visibility.time.time", return_value=350):
            pages["frozen login"] = self.client.get("/olim/staff-login")
        return {name: r.get_data(as_text=True) for name, r in pages.items()}

    def test_the_hidden_notice_in_english(self):
        # Scenario: The hidden notice in English
        # (the page carries the English text, the rules that show it when
        # the page language is English, and a snippet that picks that
        # language; the plain-text answers carry both languages)
        pages = self.pages()
        notice = pages["hidden notice"]
        self.assertIn('<span class="lang-en">This ranking is hidden for now.'
                      '</span>', notice)
        self.assertIn('<title data-en="Hidden ranking">Ranking oculto</title>',
                      notice)
        self.assertIn('<span class="lang-en">Staff password</span>', notice)
        self.assertIn('<span class="lang-en">Sign in</span>', notice)
        self.assertIn('<span class="lang-en">Wrong password.</span>',
                      pages["failed login"])
        self.assertIn('<title data-en="Staff login">Acceso staff</title>',
                      pages["frozen login"])
        self.assertIn('<span class="lang-en">Staff access to the live '
                      'ranking.</span>', pages["frozen login"])
        for name, html in pages.items():
            style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
            self.assertRegex(style, LANG_ES_HIDDEN, name)
            self.assertIn(notice_snippet(), html, name)
        self.put_visibility("olim", True)
        self.assertEqual(
            self.client.get("/olim/users/").get_data(as_text=True),
            "Este ranking está oculto. / This ranking is hidden.")
        self.put_visibility("olim", False)
        self.freeze()
        with patch("cmsranking.visibility.time.time", return_value=350):
            self.assertEqual(
                self.client.get("/olim/submissions/").get_data(as_text=True),
                "Este ranking está congelado. / This ranking is frozen.")

    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
    def test_the_snippet_shows_english_for_an_english_choice(self):
        # Scenario: The hidden notice in English
        # (the snippet sets the page language and the English title)
        script = notice_snippet()
        out = run_snippet(script, "en", ["es-MX"], None)
        self.assertEqual(out, {"lang": "en", "data_lang": "en",
                               "title": "Hidden ranking"})
        out = run_snippet(script, "es", ["en-US"], None)
        self.assertEqual(out, {"lang": "es", "data_lang": "es",
                               "title": "Ranking oculto"})
        # storage that fails: the browser language decides
        out = run_snippet(script, None, ["en-US"], None, throws=True)
        self.assertEqual(out["data_lang"], "en")
        out = run_snippet(script, None, ["es-MX"], None, throws=True)
        self.assertEqual(out["data_lang"], "es")

    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
    def test_the_snippet_picks_the_language_as_i18n_js_does(self):
        # Scenario: The hidden notice in English (one rule in two places)
        script = notice_snippet()
        for stored, languages, language in PICKS:
            early = run_script(stored=stored, languages=languages,
                               language=language)["early"]
            snippet = run_snippet(script, stored, languages, language)
            self.assertEqual(
                (snippet["lang"], snippet["data_lang"]),
                (early["lang"], early["data_lang"]),
                (stored, languages, language))

    def test_the_hidden_notice_without_javascript(self):
        # Scenario: The hidden notice without JavaScript
        # (no script runs, so no data-lang is set: nothing hides the Spanish
        # text, the English one is hidden by default, and the page is es)
        for name, html in self.pages().items():
            self.assertIn('<html lang="es">', html, name)
            style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
            self.assertRegex(style, LANG_EN_HIDDEN, name)
            hiding = re.findall(r"([^{}]*\.lang-es[^{}]*)\{", style)
            self.assertEqual([h.strip() for h in hiding],
                             [':root[data-lang="en"] .lang-es'], name)
            self.assertIn('<span class="lang-es">', html, name)
        notice = self.pages()["hidden notice"]
        self.assertIn('<span class="lang-es">Este ranking está oculto por '
                      'ahora.</span>', notice)
        self.assertIn('<span class="lang-es">Contraseña del staff</span>',
                      notice)

    def test_a_group_name_with_markup(self):
        # Scenario: A group name with markup
        # (the name is cut to [a-z0-9_-] as before and is shown once: it is
        # a name, not a text with two languages; no markup character of it
        # reaches the page, and nothing of it reaches the inline script)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        guard = VisibilityGuard(lambda environ, start_response: [], tmp,
                                '<script>alert(1)</script>&"x', "u", "p",
                                "Scoreboard")
        guard.state.update_settings(VisibilitySettings(hide_at=0))
        text = Client(guard).get("/").get_data(as_text=True)
        self.assertIn("<h1>scriptalert1scriptx</h1>", text)
        self.assertNotIn("alert(1)", text)
        self.assertNotIn("&amp;", text)
        self.assertEqual(text.count("scriptalert1scriptx"), 1)
        script = re.search(r"<script>(.*?)</script>", text, re.S).group(1)
        self.assertEqual(script, notice_snippet())


class TestRwsBanners(unittest.TestCase):

    def test_the_banners_follow_the_language(self):
        # Scenario: The banners follow the language
        # (both texts in spans, the Spanish one as it was, and the rules of
        # Ranking.css that show one of them from data-lang)
        for banner, es, en in [
                (STAFF_BANNER,
                 "Vista staff: este ranking está oculto al público",
                 "Staff view: this ranking is hidden from the public"),
                (STAFF_FROZEN_BANNER,
                 "Vista staff: ranking congelado para el público",
                 "Staff view: ranking frozen for the public"),
                (public_frozen_banner(1_700_000_000),
                 "Ranking congelado desde las ",
                 "Ranking frozen since ")]:
            text = banner.decode("utf-8")
            self.assertIn('<span class="lang-es">' + es, text)
            self.assertIn('<span class="lang-en">' + en, text)
        for banner in (STAFF_BANNER, STAFF_FROZEN_BANNER):
            text = banner.decode("utf-8")
            self.assertIn('href="staff-logout">Salir</a>', text)
            self.assertIn('href="staff-logout">Sign out</a>', text)
        text = public_frozen_banner(1_700_000_000).decode("utf-8")
        self.assertIn('href="staff-login">Acceso staff</a>', text)
        self.assertIn('href="staff-login">Staff login</a>', text)
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, LANG_ES_HIDDEN)
        self.assertRegex(css, LANG_EN_HIDDEN)


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
