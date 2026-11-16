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

"""Tests for the language of the AWS users import page and its script.

The page is rendered from its template in English (no translation) and in
Spanish (the aws catalog), and compared with the page as it was before it
was translatable (data/contest_users_import_before_i18n.html, a frozen copy
of the template of main at that time). The inline script runs under node
against stubs of the document and of fetch. No browser is involved, so
nothing here says how the page looks.

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

from cms.server.admin.bulkimport import FIELDS, REQUIRED, USER_FIELDS, \
    USER_REQUIRED
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH

BEFORE = (Path(__file__).parent / "data"
          / "contest_users_import_before_i18n.html")
BEFORE_ENVIRONMENT = AWS_ENVIRONMENT.overlay(loader=ChoiceLoader([
    DictLoader({"contest_users_import.html": BEFORE.read_text("utf-8")}),
    AWS_ENVIRONMENT.loader]))
TEMPLATE = "contest_users_import.html"


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


def page(**params) -> dict:
    """The parameters of a page of a contest (contest=None for the global
    one) that has nothing else to say."""
    contest = params.setdefault("contest",
                                SimpleNamespace(id=1, name="Day 1"))
    params.setdefault("fields", FIELDS if contest else USER_FIELDS)
    params.setdefault("required", REQUIRED if contest else USER_REQUIRED)
    for name in ("summary", "job", "notice", "preview"):
        params.setdefault(name, None)
    params.setdefault("errors", [])
    params.setdefault("mapping", {})
    return params


def summary(new_users=1, updated_users=2, **losses) -> dict:
    return {"usuarios_nuevos": new_users,
            "usuarios_actualizados": updated_users, **losses}


def contest_summary(removed_teams=0, moved=0) -> dict:
    return summary(participaciones_nuevas=3, participaciones_actualizadas=4,
                   equipos_quitados=removed_teams,
                   movidas_al_grupo_principal=moved)


JOB = SimpleNamespace(id="job-1", processed=3, total=10)
# Every shape of the page that has text of its own.
PAGES = {
    "contest form": page(),
    "contest form with a mapping": page(mapping={"username": "usuario"}),
    "contest form with the password unassigned": page(
        mapping={"username": "usuario", "password": ""}),
    "global form": page(contest=None),
    "one error": page(errors=["fila 2: a"]),
    "three errors": page(errors=["fila 2: a", "fila 3: b", "fila 4: c"]),
    "sixty errors": page(errors=["fila %d: mal" % n for n in range(60)]),
    "notice": page(notice="Aviso del servidor."),
    "contest summary, nothing cleared": page(summary=contest_summary()),
    "contest summary, one of each": page(summary=contest_summary(1, 1)),
    "contest summary, several": page(summary=contest_summary(7, 2)),
    "global summary": page(contest=None, summary=summary()),
    "contest preview": page(preview={"username": "ana", "team": ""}),
    "global preview": page(contest=None, preview={"username": "ana",
                                                  "password": "x"}),
    "contest job": page(job=JOB),
    "global job": page(contest=None, job=JOB),
}


def visible_text(html: str) -> str:
    """The text a reader gets from a page (scripts and styles apart), with
    the accessible names and the whitespace normalized."""
    html = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)
    names = re.findall(r'aria-label="([^"]*)"', html)
    text = re.sub(r"<[^>]+>", " ", html)
    return " ".join(unescape(" ".join([text, *names])).split())


def inline_script(html: str) -> str:
    match = re.search(r"<script>(.*?)</script>", html, re.S)
    assert match is not None
    return match.group(1)


class JobContainer(HTMLParser):
    """Collects the attributes of the element with id import_job."""

    def __init__(self):
        super().__init__()
        self.attributes: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if dict(attrs).get("id") == "import_job":
            self.attributes = {k: v for k, v in attrs}


def job_attributes(html: str) -> dict[str, str]:
    parser = JobContainer()
    parser.feed(html)
    return parser.attributes


# Runs the script argv[1] with the config argv[2] (JSON): "attributes" of the
# #import_job container, "payloads" (what the status URL answers, in order;
# then it never answers) and "fail" (every poll fails). Prints what the
# page would show: the progress text and the result tree.
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
        get firstChild() { return this.children[0] || null; },
        appendChild(child) { this.children.push(child); return child; },
    };
}
function dump(node) {
    if (node.tag === "#text") { return {text: node.own}; }
    return {tag: node.tag, cls: node.className, href: node.href,
            text: node.own, kids: node.children.map(dump)};
}
const job = {getAttribute(name) { return cfg.attributes[name]; }};
const bar = element("progress");
const text = element("p");
const result = element("div");
const byId = {import_job: job, import_bar: bar, import_text: text,
              import_result: result};
const document = {
    getElementById(id) { return byId[id] || null; },
    createElement: element,
    createTextNode(value) {
        const n = element("#text"); n.own = value; return n;
    },
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
    console.log(JSON.stringify({text: text.textContent, max: bar.max,
                                value: bar.value, result: dump(result)}));
}, 200);
"""

# What the status URL answers. The data keys are the ones of the summary.
RUNNING = {"status": "running", "processed": 3, "total": 10}
DONE_CONTEST = {"status": "done", "processed": 10, "total": 10,
                "summary": {"usuarios_nuevos": 1, "usuarios_actualizados": 2,
                            "participaciones_nuevas": 3,
                            "participaciones_actualizadas": 4,
                            "equipos_quitados": 1,
                            "movidas_al_grupo_principal": 2}}
DONE_GLOBAL = {"status": "done", "processed": 5, "total": 5,
               "summary": {"usuarios_nuevos": 1, "usuarios_actualizados": 2}}
FAILED = {"status": "error", "processed": 4, "total": 10,
          "error": "fila 2: el usuario %(x)s está vacío"}
SCRIPT_CASES = {
    "running": [RUNNING],
    "done in a contest": [RUNNING, DONE_CONTEST],
    "done globally": [DONE_GLOBAL],
    "failed": [FAILED],
}


def run_script(html: str, payloads: list, fail: bool = False) -> dict:
    """Run the inline script of the rendered page against the stubs."""
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "import.js"
        script.write_text(inline_script(html), "utf-8")
        check = subprocess.run(["node", "--check", str(script)],
                               capture_output=True, text=True)
        assert check.returncode == 0, check.stderr
        config = {"attributes": job_attributes(html), "fail": fail,
                  "payloads": payloads}
        result = subprocess.run(
            ["node", "-e", HARNESS, str(script), json.dumps(config)],
            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def shown(html: str, payloads: list, fail: bool = False) -> list[str]:
    """The texts of the result block after polling, flattened, with the
    progress text first."""
    out = run_script(html, payloads, fail)

    def flatten(node):
        if "kids" not in node:
            return [node["text"]]
        own = [node["text"]] if node["text"] else []
        return own + [t for kid in node["kids"] for t in flatten(kid)]

    return [out["text"]] + flatten(out["result"])


def job_page(translation=None, environment=AWS_ENVIRONMENT) -> str:
    return render(environment, translation, **page(job=JOB))


class TestPageInEnglish(unittest.TestCase):

    def test_a_page_in_english(self):
        # Scenario: A page in English
        form = visible_text(render(AWS_ENVIRONMENT, **PAGES["contest form"]))
        self.assertIn("Import CSV \u2014 Day 1", form)
        self.assertIn("One row per contestant: it creates the users that "
                      "are missing", form)
        self.assertIn(
            "User (username) * (unassigned) First name (first_name)", form)
        self.assertIn("Password of the day (password)", form)
        self.assertIn("(unassigned)", form)
        self.assertIn("* required", form)
        self.assertIn("CSV file *", form)
        self.assertIn("Only validate Import", form)
        self.assertIn("20 to 40 s for every 300 contestants", form)

        global_form = visible_text(
            render(AWS_ENVIRONMENT, **PAGES["global form"]))
        self.assertIn("Import CSV \u2014 Users", global_form)
        self.assertIn("Password (password)", global_form)

        errors = visible_text(
            render(AWS_ENVIRONMENT, **PAGES["sixty errors"]))
        self.assertIn("Nothing was applied. Fix these errors and upload "
                      "the file again: 60 errors", errors)
        self.assertIn("and 10 more", errors)

        preview = visible_text(
            render(AWS_ENVIRONMENT, **PAGES["contest preview"]))
        self.assertIn("First row of the file; check that the columns are "
                      "the right ones:", preview)
        self.assertIn("User (username): ana Team (team): (empty)", preview)

        job = visible_text(render(AWS_ENVIRONMENT, **PAGES["contest job"]))
        self.assertIn("Processing 3 of 10 Import progress", job)

    def test_a_page_in_english_has_no_spanish_text(self):
        # Scenario: A page in English (the notice is the server's, already
        # in its language, and the errors are data)
        spanish_word = re.compile(
            r"[áéíóúñ¿¡]|\b(el|la|los|las|de|del|que|con|sin|por|y)\b")
        for name, params in PAGES.items():
            if name in ("notice", "sixty errors", "one error",
                        "three errors"):
                continue
            text = visible_text(render(AWS_ENVIRONMENT, **params))
            self.assertIsNone(spanish_word.search(text), msg=name)

    def test_one_and_several(self):
        # Scenario: One and several
        for count, heading in ((1, "1 error"), (2, "2 errors")):
            html = render(AWS_ENVIRONMENT, **page(
                errors=["fila %d: a" % n for n in range(count)]))
            self.assertIn("<h3>%s</h3>" % heading, html)
        for removed, moved, texts in (
                (1, 1, ("1 participation will lose its team",
                        "1 participation will move to the main group")),
                (7, 2, ("7 participations will lose their team",
                        "2 participations will move to the main group"))):
            text = visible_text(render(AWS_ENVIRONMENT, **page(
                summary=contest_summary(removed, moved))))
            for expected in texts:
                self.assertIn(expected, text)

    def test_the_import_summary_in_english(self):
        # Scenario: The import summary in English (same data fields, labels
        # in English)
        text = visible_text(render(AWS_ENVIRONMENT, **page(
            summary=contest_summary(7, 2))))
        for expected in ("The file is valid. When it is imported:",
                         "New users: 1", "Existing users (overwritten): 2",
                         "New participations: 3",
                         "Existing participations (overwritten): 4"):
            self.assertIn(expected, text)
        # The users import has no participations.
        text = visible_text(render(AWS_ENVIRONMENT, **PAGES["global summary"]))
        self.assertIn("New users: 1", text)
        self.assertNotIn("participations", text)

    def test_zero(self):
        # Scenario: Zero. A count of 0 is never shown by this page: the
        # warnings are left out when nothing is cleared, and the errors box
        # only exists with an error. The plural form is what 0 would use.
        text = visible_text(render(AWS_ENVIRONMENT, **PAGES[
            "contest summary, nothing cleared"]))
        self.assertNotIn("lose their team", text)
        self.assertNotIn("will move to the main group", text)
        self.assertEqual(SPANISH.ngettext("%(count)s error",
                                          "%(count)s errors", 0),
                         "%(count)s errores")


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
        form = PAGES["contest form"]
        before = visible_text(render(BEFORE_ENVIRONMENT, **form))
        self.assertNotEqual(visible_text(render(AWS_ENVIRONMENT, **form)),
                            before)
        self.assertIn("Solo validar Importar", before)
        self.assertIn("Contraseña del día (password)", before)

    def test_one_and_several_in_spanish(self):
        # Scenario: One and several (and Zero: the plural form)
        for count, heading in ((1, "1 error"), (2, "2 errores")):
            html = render(AWS_ENVIRONMENT, SPANISH, **page(
                errors=["fila %d: a" % n for n in range(count)]))
            self.assertIn("<h3>%s</h3>" % heading, html)
        text = visible_text(render(AWS_ENVIRONMENT, SPANISH, **page(
            summary=contest_summary(1, 2))))
        self.assertIn("1 participación perderá su equipo", text)
        self.assertIn("2 participaciones pasarán al grupo principal", text)


class TestScriptTexts(unittest.TestCase):

    def test_the_script_is_valid_javascript_in_both_languages(self):
        # Scenario: Script texts in English / in Spanish (run_script checks
        # the script with node --check before running it)
        for translation in (None, SPANISH):
            run_script(job_page(translation), [RUNNING])

    def test_the_container_carries_the_messages_in_the_page_language(self):
        # Scenario: Script texts in English / in Spanish
        english = json.loads(job_attributes(job_page())["data-messages"])
        spanish = json.loads(
            job_attributes(job_page(SPANISH))["data-messages"])
        self.assertEqual(english.keys(), spanish.keys())
        self.assertEqual(english["done"], "Done.")
        self.assertEqual(spanish["done"], "Listo.")
        self.assertEqual(english["teams_lost"][0],
                         "%(count)s participation lost its team")
        self.assertEqual(spanish["teams_lost"],
                         ["%(count)s participación perdió su equipo",
                          "%(count)s participaciones perdieron su equipo"])

    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
    def test_script_texts_in_english(self):
        # Scenario: Script texts in English
        html = job_page()
        self.assertEqual(shown(html, SCRIPT_CASES["running"]),
                         ["Processing 3 of 10"])
        self.assertEqual(
            shown(html, SCRIPT_CASES["done in a contest"]),
            ["Processing 10 of 10", "Done.",
             " New users: 1, existing (overwritten): 2. New participations: "
             "3, existing (overwritten): 4.",
             "1 participation lost its team",
             "2 participations were moved to the main group",
             "User list", " | ", "Import another file"])
        self.assertEqual(
            shown(html, SCRIPT_CASES["done globally"]),
            ["Processing 5 of 5", "Done.",
             " New users: 1, existing (overwritten): 2.",
             "User list", " | ", "Import another file"])
        self.assertEqual(
            shown(html, SCRIPT_CASES["failed"]),
            ["Processing 4 of 10",
             "Error: fila 2: el usuario %(x)s está vacío",
             "User list", " | ", "Import another file"])
        self.assertEqual(
            shown(html, [], fail=True),
            ["", "Could not check the progress; reload the page."])

    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
    def test_script_texts_in_spanish_are_those_of_main(self):
        # Scenario: Script texts in Spanish. The same payloads give the
        # same tree with the script of main and with the translated one.
        before = job_page(environment=BEFORE_ENVIRONMENT)
        after = job_page(SPANISH)
        for name, payloads in SCRIPT_CASES.items():
            self.assertEqual(run_script(after, payloads),
                             run_script(before, payloads), msg=name)
        self.assertEqual(run_script(after, [], fail=True)["result"],
                         run_script(before, [], fail=True)["result"])

    @unittest.skipIf(shutil.which("node") is None, "node is not installed")
    def test_script_texts_in_spanish(self):
        # Scenario: Script texts in Spanish (pinned, so the comparison with
        # main above cannot pass because both sides are empty)
        html = job_page(SPANISH)
        self.assertEqual(
            shown(html, SCRIPT_CASES["done in a contest"]),
            ["Procesando 10 de 10", "Listo.",
             " Usuarios nuevos: 1, ya existentes (se sobrescribieron): 2. "
             "Participaciones nuevas: 3, ya existentes (se sobrescribieron)"
             ": 4.",
             "1 participación perdió su equipo",
             "2 participaciones pasaron al grupo principal",
             "Lista de usuarios", " | ", "Importar otro archivo"])
        self.assertEqual(
            shown(html, SCRIPT_CASES["failed"])[1],
            "Error: fila 2: el usuario %(x)s está vacío")
        self.assertEqual(
            shown(html, [], fail=True)[1],
            "No se pudo consultar el progreso; recarga la página.")


if __name__ == "__main__":
    unittest.main()
