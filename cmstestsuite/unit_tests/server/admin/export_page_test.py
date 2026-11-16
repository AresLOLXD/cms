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

"""Tests for the language of the AWS contests export page and its script.

The page is rendered from its template in English (no translation) and in
Spanish (the aws catalog), and compared with the page as it was before it
was translatable (data/export_before_i18n.html, a frozen copy of the
template of main at that time). The inline script runs under node against
stubs of the document and of fetch. No browser is involved, so nothing here
says how the page looks.

"""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

from jinja2 import ChoiceLoader, DictLoader

from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH

BEFORE = Path(__file__).parent / "data" / "export_before_i18n.html"
BEFORE_ENVIRONMENT = AWS_ENVIRONMENT.overlay(loader=ChoiceLoader([
    DictLoader({"export.html": BEFORE.read_text("utf-8")}),
    AWS_ENVIRONMENT.loader]))
TEMPLATE = "export.html"


def fake_url(*parts: object) -> str:
    return "/" + "/".join(str(part) for part in parts)


def render(environment, translation=None, **params) -> str:
    """Render the core block of the page, in English if translation is None."""
    template = environment.get_template(TEMPLATE)
    if translation is not None:
        params.update({"gettext": translation.gettext,
                       "ngettext": translation.ngettext})
    params.setdefault("url", fake_url)
    params.setdefault("xsrf_form_html", "")
    return "".join(template.blocks["core"](template.new_context(params)))


def contests(*names: str) -> list:
    return [SimpleNamespace(id=n, name=name)
            for n, name in enumerate(names, 1)]


def page(**params) -> dict:
    """The parameters of a page that has nothing else to say."""
    params.setdefault("contest_list", contests("Day 1", "Day 2"))
    params.setdefault("form", {"contest_ids": [], "content": "all",
                               "options": {"no_generated"}})
    params.setdefault("errors", [])
    for name in ("job", "notice"):
        params.setdefault(name, None)
    params.setdefault("jobs", [])
    params.setdefault("running", {})
    return params


JOBS = [SimpleNamespace(id="a", name="dump_1_2026-11-04", status=status)
        for status in ("running", "done", "error")]
# Every shape of the page that has text of its own.
PAGES = {
    "no contests": page(contest_list=[]),
    "several contests": page(form={
        "contest_ids": [2], "content": "no_files",
        "options": {"no_users", "no_submissions"}}),
    "running contests": page(running={2: "Day 2"}),
    "only files": page(form={"contest_ids": [], "content": "only_files",
                             "options": {"no_user_tests"}}),
    "an error": page(errors=["elige al menos un concurso"]),
    "two errors": page(errors=["uno", "dos"]),
    "a notice": page(notice="Aviso del servidor."),
    "the list of exports": page(jobs=JOBS),
    "a job": page(job=SimpleNamespace(id="j-1", name="dump_1_2026-11-04"),
                  jobs=JOBS[:1]),
}


def visible_text(html: str) -> str:
    """The text a reader gets from a page (scripts and styles apart), with
    the whitespace normalized."""
    html = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def inline_script(html: str) -> str:
    match = re.search(r"<script>(.*?)</script>", html, re.S)
    assert match is not None
    return match.group(1)


class JobContainer(HTMLParser):
    """Collects the attributes of the element with id export_job."""

    def __init__(self):
        super().__init__()
        self.attributes: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if dict(attrs).get("id") == "export_job":
            self.attributes = {k: v for k, v in attrs}


def job_attributes(html: str) -> dict[str, str]:
    parser = JobContainer()
    parser.feed(html)
    return parser.attributes


# Runs the script argv[1] with the config argv[2] (JSON): "attributes" of the
# #export_job container, "payloads" (what the status URL answers, in order;
# then it never answers) and "fail" (every poll fails). Prints what the
# page would show: the state text and the result tree.
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const cfg = JSON.parse(process.argv[2]);
const realSetTimeout = setTimeout;
function element(tag) {
    return {
        tag: tag, className: "", href: undefined, children: [], own: "",
        set textContent(value) {
            this.own = String(value); this.children = [];
        },
        get textContent() {
            return this.own + this.children.map(c => c.textContent).join("");
        },
        appendChild(child) { this.children.push(child); return child; },
    };
}
function dump(node) {
    return {tag: node.tag, cls: node.className, href: node.href,
            text: node.own, kids: node.children.map(dump)};
}
const job = {getAttribute(name) { return cfg.attributes[name]; }};
const text = element("p");
const result = element("div");
const byId = {export_job: job, export_text: text, export_result: result};
const document = {
    getElementById(id) { return byId[id] || null; },
    createElement: element,
};
const context = {
    document: document,
    fetch() {
        if (cfg.fail) { return Promise.reject(new Error("down")); }
        if (cfg.payloads.length === 0) { return new Promise(() => {}); }
        const payload = cfg.payloads.shift();
        return Promise.resolve(
            {ok: true, json: () => Promise.resolve(payload)});
    },
    setTimeout: (fn) => realSetTimeout(fn, 0),
    console: console, String: String, JSON: JSON, Error: Error,
};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), context);
realSetTimeout(() => {
    console.log(JSON.stringify({text: text.textContent,
                                result: dump(result)}));
}, 200);
"""

# What the status URL answers.
EXPORTING = {"status": "running", "phase": "exporting", "size": None,
             "error": None}
PACKING = {"status": "running", "phase": "packing", "size": None,
           "error": None}
DONE = {"status": "done", "phase": "packing", "size": 3 * 1048576 + 52429,
        "error": None}
FAILED = {"status": "error", "phase": "exporting", "size": None,
          "error": "la exportación falló (código de salida 3); no se "
                   "generó nada %(x)s"}
SCRIPT_CASES = {
    "exporting": [EXPORTING],
    "packing then done": [PACKING, DONE],
    "failed": [FAILED],
}


def run_script(html: str, payloads: list, fail: bool = False) -> dict:
    """Run the inline script of the rendered page against the stubs."""
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "export.js"
        script.write_text(inline_script(html), "utf-8")
        check = subprocess.run(["node", "--check", str(script)],
                               capture_output=True, text=True)
        assert check.returncode == 0, check.stderr
        config = {"attributes": job_attributes(html), "fail": fail,
                  "payloads": list(payloads)}
        result = subprocess.run(
            ["node", "-e", HARNESS, str(script), json.dumps(config)],
            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def shown(html: str, payloads: list, fail: bool = False) -> list[str]:
    """The texts of the state and the result block after polling, flattened."""
    out = run_script(html, payloads, fail)

    def flatten(node):
        own = [node["text"]] if node["text"] else []
        return own + [t for kid in node["kids"] for t in flatten(kid)]

    return [out["text"]] + flatten(out["result"])


def job_page(translation=None, environment=AWS_ENVIRONMENT) -> str:
    return render(environment, translation, **PAGES["a job"])


class TestPageInEnglish(unittest.TestCase):

    def test_a_page_in_english(self):
        # Scenario: A page in English
        form = visible_text(
            render(AWS_ENVIRONMENT, **PAGES["several contests"]))
        self.assertIn("Export contests", form)
        self.assertIn("Download a .zip file with the contests you choose",
                      form)
        self.assertIn("Contests * Day 1 Day 2", form)
        self.assertIn("Content Database and files Only the database, "
                      "without files ( -F )", form)
        self.assertIn("Options Do not export what can be regenerated: "
                      "results and executables ( -G , recommended)", form)
        self.assertIn("* required Export", form)
        self.assertIn("(no contests)", visible_text(
            render(AWS_ENVIRONMENT, **PAGES["no contests"])))

        warning = visible_text(
            render(AWS_ENVIRONMENT, **PAGES["running contests"]))
        self.assertIn("Day 2 (running or in analysis mode)", warning)
        self.assertIn("There are contests running or in analysis mode.",
                      warning)
        self.assertIn("I understand the risk and want to export anyway",
                      warning)

        errors = visible_text(render(AWS_ENVIRONMENT, **PAGES["an error"]))
        self.assertIn("The export was not started: "
                      "elige al menos un concurso", errors)

        exports = visible_text(
            render(AWS_ENVIRONMENT, **PAGES["the list of exports"]))
        self.assertIn("Your exports", exports)
        self.assertEqual(re.findall(
            r"dump_1_2026-11-04 (running|ready|failed)", exports),
            ["running", "ready", "failed"])

        job = visible_text(render(AWS_ENVIRONMENT, **PAGES["a job"]))
        self.assertIn("dump_1_2026-11-04 Checking the state…", job)

    def test_a_page_in_english_has_no_spanish_text(self):
        # Scenario: A page in English (the notice and the errors are the
        # server's, already in its language)
        spanish_word = re.compile(
            r"[áéíóúñ¿¡]|\b(el|la|los|las|de|del|que|con|sin|por|y)\b")
        for name, params in PAGES.items():
            if name in ("an error", "two errors", "a notice"):
                continue
            text = visible_text(render(AWS_ENVIRONMENT, **params))
            self.assertIsNone(spanish_word.search(text), msg=name)


class TestPageInSpanish(unittest.TestCase):

    def test_a_page_in_spanish_is_the_page_of_main(self):
        # Scenario: A page in Spanish. The visible text of every shape of
        # the page equals the one of the template before it was translated.
        for name, params in PAGES.items():
            before = visible_text(render(BEFORE_ENVIRONMENT, **params))
            after = visible_text(render(AWS_ENVIRONMENT, SPANISH, **params))
            self.assertEqual(after, before, msg=name)

    def test_the_spanish_pin_is_not_vacuous(self):
        # Scenario: A page in Spanish. The same comparison fails for the
        # English page and the text is the Spanish one.
        params = PAGES["running contests"]
        before = visible_text(render(BEFORE_ENVIRONMENT, **params))
        self.assertNotEqual(visible_text(render(AWS_ENVIRONMENT, **params)),
                            before)
        self.assertIn("Day 2 (en curso o en modo de análisis)", before)
        exports = visible_text(render(
            BEFORE_ENVIRONMENT, **PAGES["the list of exports"]))
        self.assertEqual(re.findall(
            r"dump_1_2026-11-04 (en curso|lista|falló)", exports),
            ["en curso", "lista", "falló"])


class TestScriptTexts(unittest.TestCase):

    def test_the_script_is_valid_javascript_in_both_languages(self):
        # Scenario: Script texts in English / in Spanish (run_script checks
        # the script with node --check before running it)
        for translation in (None, SPANISH):
            run_script(job_page(translation), [EXPORTING])

    def test_the_container_carries_the_messages_in_the_page_language(self):
        # Scenario: Script texts in English / in Spanish
        english = json.loads(job_attributes(job_page())["data-messages"])
        spanish = json.loads(
            job_attributes(job_page(SPANISH))["data-messages"])
        self.assertEqual(english.keys(), spanish.keys())
        self.assertEqual(english["done"], "Done.")
        self.assertEqual(spanish["done"], "Listo.")
        self.assertEqual(spanish["download"],
                         "Descargar el .zip (%(size)s MB)")

    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
    def test_script_texts_in_english(self):
        # Scenario: Script texts in English
        html = job_page()
        self.assertEqual(
            shown(html, SCRIPT_CASES["exporting"]),
            ["Exporting… it may take several minutes; you can close this "
             "page and come back from the list of exports."])
        self.assertEqual(
            shown(html, SCRIPT_CASES["packing then done"]),
            ["Done.", "Download the .zip (3.1 MB)",
             "The file will be available for one hour.",
             "Export other contests"])
        self.assertEqual(
            shown(html, SCRIPT_CASES["failed"]),
            ["The export failed.",
             "Error: la exportación falló (código de salida 3); no se "
             "generó nada %(x)s", "Export other contests"])
        self.assertEqual(shown(html, [PACKING])[0], "Packing the .zip…")
        self.assertEqual(
            shown(html, [], fail=True),
            ["", "Could not check the state; reload the page."])

    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
    def test_script_texts_in_spanish_are_those_of_main(self):
        # Scenario: Script texts in Spanish. The same payloads give the
        # same tree with the script of main and with the translated one.
        before = job_page(environment=BEFORE_ENVIRONMENT)
        after = job_page(SPANISH)
        for name, payloads in SCRIPT_CASES.items():
            self.assertEqual(run_script(after, payloads),
                             run_script(before, payloads), msg=name)
        self.assertEqual(run_script(after, [PACKING]),
                         run_script(before, [PACKING]))
        self.assertEqual(run_script(after, [], fail=True),
                         run_script(before, [], fail=True))

    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
    def test_script_texts_in_spanish(self):
        # Scenario: Script texts in Spanish (pinned, so the comparison with
        # main above cannot pass because both sides are empty)
        html = job_page(SPANISH)
        self.assertEqual(
            shown(html, SCRIPT_CASES["packing then done"]),
            ["Listo.", "Descargar el .zip (3.1 MB)",
             "El archivo estará disponible una hora.",
             "Exportar otros concursos"])
        self.assertEqual(shown(html, [PACKING])[0],
                         "Empaquetando el .zip…")
        self.assertEqual(
            shown(html, SCRIPT_CASES["failed"])[:2],
            ["La exportación falló.",
             "Error: la exportación falló (código de salida 3); no se "
             "generó nada %(x)s"])
        self.assertEqual(
            shown(html, [], fail=True)[1],
            "No se pudo consultar el estado; recarga la página.")


if __name__ == "__main__":
    unittest.main()
