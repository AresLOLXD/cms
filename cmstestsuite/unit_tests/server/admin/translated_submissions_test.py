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

"""Tests for the translated AWS pages of submissions and user tests.

Pages are rendered with the stand-ins of aws_render, so they need no
database and say nothing about how a page looks. A page is asserted
only on the texts of this lane's files, because the pages of other
lanes may include them.

"""

import json
import unittest
from datetime import datetime
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest import mock

from cms.db import Submission, SubmissionResult, UserTestResult
from cms.locale import DEFAULT_TRANSLATION
from cms.server.admin.handlers.submission import (
    SubmissionCommentHandler, SubmissionDiffHandler)
from cmstestsuite.unit_tests.server.admin import aws_render
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH


def page(name, translation, mode="full") -> str:
    return aws_render.render(aws_render.environment(mode), name, translation)


def snippet(source, translation, mode="full", **params) -> str:
    """Render a few lines of template, as a minimal page that imports
    the macros with the context."""
    template = aws_render.environment(mode).from_string(source)
    return template.render(
        url=lambda *p: "/" + "/".join(map(str, p)),
        translation=translation, gettext=translation.gettext,
        ngettext=translation.ngettext, **params)


def submission_row(status, translation, score_type=None) -> str:
    """Render the row of a submission whose result has that status."""
    dataset = SimpleNamespace(id=2)
    result = SimpleNamespace(
        get_status=lambda: status, score=12.0, score_details=None,
        scored_at=None, evaluations=[], compilation_text=[],
        evaluated=lambda: True)
    user = SimpleNamespace(id=4, username="ana")
    submission = SimpleNamespace(
        id=1, timestamp=datetime(2026, 1, 1), files={}, token=None,
        official=True, short_comment="",
        task=SimpleNamespace(id=3, name="sum", active_dataset=dataset),
        participation=SimpleNamespace(
            user=user, contest=SimpleNamespace(id=5)),
        get_result=lambda dataset: result)
    return snippet(
        '{% import "macro/submission.html" as m with context %}'
        "{{ m.row(admin, url, s, dataset) }}", translation,
        admin=SimpleNamespace(permission_all=True), s=submission,
        dataset=dataset, get_score_type=lambda dataset: score_type)


class _Events(HTMLParser):
    """Collect the attributes (name, value) of every button."""

    def __init__(self):
        super().__init__()
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        if tag == "button":
            self.buttons.append(dict(attrs))


def buttons(html):
    parser = _Events()
    parser.feed(html)
    return parser.buttons


def page_url(page=0):
    return "/list?page=%d" % page


REEVALUATE = (
    '{% import "macro/reevaluation_buttons.html" as m with context %}'
    "{{ m.reevaluation_buttons(true, '/next', submission_id=1, "
    "dataset_id=2) }}")


class TestSubmissionPages(unittest.TestCase):

    def test_the_contest_submissions_page_in_spanish(self):
        # Scenario "A page group in Spanish"
        html = page("contest_submissions.html", SPANISH)
        for text in ("Todos los envíos", "Reevaluar todos los envíos de "
                     "este concurso", "para todos los datasets"):
            self.assertIn(text, html)
        for text in ("All Submissions", "Reevaluate all"):
            self.assertNotIn(text, html)

    def test_the_submission_table_in_spanish(self):
        # Scenario "A page group in Spanish": the macro rows of
        # macro/submission.html, inside the page of the contest.
        html = page("contest_submissions.html", SPANISH)
        for text in ("Comparar envíos…", "<th>Hora</th>", "<th>Problema</th>",
                     "<th>Latencia</th>", "<th>Oficial</th>",
                     "[Estado desconocido]", "Salida de la compilación"):
            self.assertIn(text, html)
        for text in ("Diff submissions", "<th>Latency</th>",
                     "[Unknown status]", "Compilation output"):
            self.assertNotIn(text, html)
        # An empty table shows its own sentence.
        html = snippet(
            '{% import "macro/submission.html" as m with context %}'
            "{{ m.rows(none, url, none, [], 0, 0) }}", SPANISH)
        self.assertIn("No se encontraron envíos.", html)

    def test_the_contest_submissions_page_in_english(self):
        # Scenario "A page in English is unchanged"
        html = page("contest_submissions.html", DEFAULT_TRANSLATION)
        for text in ("All Submissions", "Reevaluate all",
                     "submissions in this contest (for all datasets)"):
            self.assertIn(text, html)

    def test_the_contest_user_tests_page_in_spanish(self):
        # Scenario "A page group in Spanish"
        html = page("contest_user_tests.html", SPANISH)
        for text in ("Todos los user tests", "en este concurso",
                     "<th>Entrada del usuario</th>", "<th>Archivos</th>",
                     "Ver entrada", "[Estado desconocido]"):
            self.assertIn(text, html)
        for text in ("All user tests", "User input", "See input"):
            self.assertNotIn(text, html)
        html = snippet('{% include "fragments/user_test_rows.html" %}',
                       SPANISH, user_test_count=0)
        self.assertIn("No se encontraron user tests.", html)

    def test_the_submission_page_in_spanish(self):
        # Scenario "A page group in Spanish"
        html = page("submission.html", SPANISH)
        for text in ("Detalles del envío", "Hora del envío",
                     "Latencia vista por el usuario", "Volver a ejecutar y "
                     "archivar", "Evaluación (vista por el administrador)",
                     "Salida estándar", 'value="Hacer no oficial"',
                     "Fallas durante la evaluación"):
            self.assertIn(text, html)
        for text in ("Submission details", "Submission time",
                     "Rerun and archive", "Standard output",
                     "Make unofficial", "Make official"):
            self.assertNotIn(text, html)

    def test_the_submission_page_in_english(self):
        # Scenario "A page in English is unchanged": the same sentences
        # as before, and the heading with its link.
        html = page("submission.html", DEFAULT_TRANSLATION)
        for text in ("Submission details", "Submission time",
                     "Rerun and archive", "Standard output",
                     "Failures during evaluation", "(Task: <a href="):
            self.assertIn(text, html)

    def test_the_user_test_page_in_spanish(self):
        # Scenario "A page group in Spanish"
        html = page("user_test.html", SPANISH)
        for text in ("Detalles del user test", "Propiedad", "Valor",
                     "Sandbox de compilación", "(Problema: <a href=",
                     "Fallas durante la compilación"):
            self.assertIn(text, html)
        for text in ("User test details", "Property", "Compilation sandbox",
                     "Failures during compilation"):
            self.assertNotIn(text, html)
        # The English page keeps its words (Scenario "A page in English
        # is unchanged").
        html = page("user_test.html", DEFAULT_TRANSLATION)
        for text in ("User test details", "Property", "(Task: <a href="):
            self.assertIn(text, html)

    def test_the_status_of_a_submission_in_spanish(self):
        # Scenario "A page group in Spanish": the texts of a row (the
        # outcomes of the grading are not ours and are not asserted).
        scored = submission_row(SubmissionResult.SCORED, SPANISH,
                                SimpleNamespace(
                                    max_score=100.0, get_html_details=
                                    lambda *args: ""))
        for text in ("Puntaje asignado (12.0 / 100.0)", "Sí"):
            self.assertIn(text, scored)
        self.assertNotIn("Scored", scored)
        for status, text in ((SubmissionResult.COMPILING, "Compilando…"),
                             (SubmissionResult.COMPILATION_FAILED,
                              "Compilación fallida"),
                             (SubmissionResult.EVALUATING, "Evaluando…"),
                             (SubmissionResult.SCORING,
                              "Calculando puntaje…")):
            self.assertIn(text, submission_row(status, SPANISH))
        # Without a score type the row says so, in the language.
        self.assertIn(
            "[No se pudo obtener el tipo de puntaje; consulta los "
            "registros]",
            submission_row(SubmissionResult.SCORED, SPANISH,
                           aws_render.environment("full").undefined()))

    def test_the_status_of_a_submission_in_english(self):
        # Scenario "A page in English is unchanged": the functional
        # tests read these words.
        scored = submission_row(SubmissionResult.SCORED, DEFAULT_TRANSLATION,
                                SimpleNamespace(
                                    max_score=100.0, get_html_details=
                                    lambda *args: ""))
        self.assertIn("Scored (12.0 / 100.0)", scored)
        self.assertIn("Compiling...", submission_row(
            SubmissionResult.COMPILING, DEFAULT_TRANSLATION))

    def test_the_status_of_a_user_test_in_spanish(self):
        # Scenario "A page group in Spanish": a row of fragments/
        # user_test_row.html.
        dataset = SimpleNamespace(id=2)
        result = SimpleNamespace(
            get_status=lambda: UserTestResult.EVALUATED, compilation_text=[])
        user_test = SimpleNamespace(
            id=1, timestamp=datetime(2026, 1, 1), files={}, input="abc",
            task=SimpleNamespace(id=3, name="sum", active_dataset=dataset),
            participation=SimpleNamespace(
                user=SimpleNamespace(id=4, username="ana"),
                contest=SimpleNamespace(id=5)),
            get_result=lambda dataset: result)
        render = lambda translation: snippet(
            '{% include "fragments/user_test_row.html" %}', translation,
            ut=user_test)
        html = render(SPANISH)
        for text in ("Evaluado", "Ver entrada", "Salida de la compilación"):
            self.assertIn(text, html)
        html = render(DEFAULT_TRANSLATION)
        for text in ("Evaluated", "See input", "Compilation output"):
            self.assertIn(text, html)

    def test_the_page_selector_in_spanish(self):
        # Scenario "A page group in Spanish": the macro of pages.html in
        # a minimal page.
        source = ('{% import "macro/pages.html" as m with context %}'
                  "{{ m.selector(page_url, 3, 30) }}")
        html = snippet(source, SPANISH, page_url=page_url)
        self.assertIn("Páginas:", html)
        self.assertIn("Primera</a>", html)
        self.assertIn("Última</a>", html)
        self.assertNotIn("Pages:", html)
        self.assertIn("Pages:", snippet(source, DEFAULT_TRANSLATION, page_url=page_url))

    def test_the_reevaluate_confirmation_in_spanish(self):
        # Scenario "A confirmation in Spanish": the three buttons ask
        # the same question, in a script that is still valid.
        question = json.dumps(SPANISH.gettext(
            "Do you really want to reevaluate?"))
        self.assertIn("reevaluar", SPANISH.gettext(
            "Do you really want to reevaluate?"))
        found = buttons(snippet(REEVALUATE, SPANISH))
        self.assertEqual(
            [b["title"] for b in found],
            ["Compilación", "Evaluación", "Puntaje"])
        for button in found:
            self.assertTrue(
                button["onclick"].startswith("confirm(%s) && " % question),
                button["onclick"])

    def test_the_reevaluate_confirmation_in_english(self):
        # Scenario "A page in English is unchanged"
        found = buttons(snippet(REEVALUATE, DEFAULT_TRANSLATION))
        self.assertEqual(
            [b["title"] for b in found],
            ["Compilation", "Evaluation", "Score"])
        for button in found:
            self.assertTrue(button["onclick"].startswith(
                'confirm("Do you really want to reevaluate?") && '))


def handler_in(handler_class, translation):
    """Build a handler without a request or a database."""
    handler = handler_class.__new__(handler_class)
    handler.application = mock.MagicMock()
    handler.sql_session = mock.MagicMock()
    handler.set_header = mock.MagicMock()
    handler.write = mock.MagicMock()
    handler.redirect = mock.MagicMock()
    handler.url = mock.MagicMock(return_value="/")
    handler._ = translation.gettext
    handler.n_ = translation.ngettext
    return handler


def fake_submission(task_id=1, files=("a.cpp",), language="C++17"):
    task = SimpleNamespace(
        id=task_id, submission_format=["a.cpp"])
    submission = SimpleNamespace(
        task_id=task_id, task=task, language=language,
        files={name: SimpleNamespace(digest=name) for name in files})
    return submission


class TestSubmissionDiff(unittest.TestCase):
    """The messages of the diff of two submissions, one test per branch.
    Each is asserted in English (as it was before) and in Spanish."""

    def diff(self, translation, old, new, contents=None):
        handler = handler_in(SubmissionDiffHandler, translation)
        # The content of a file is its name, unless the test says so.
        handler.service.file_cacher.get_file_content.side_effect = \
            lambda digest: (contents or {}).get(digest, b"same")
        found = {1: old, 2: new}
        with mock.patch.object(
                Submission, "get_from_id",
                side_effect=lambda id, session: found.get(int(id))):
            handler._get_sync("1", "2")
        ((body,), _) = handler.write.call_args
        return json.loads(body)

    def check(self, old, new, english, spanish, contents=None, key="status"):
        for translation, expected in ((DEFAULT_TRANSLATION, english),
                                      (SPANISH, spanish)):
            answer = self.diff(translation, old, new, contents)
            found = answer["message"] if key == "message" \
                else answer["files"][0][key]
            self.assertEqual(found, expected)

    def test_a_submission_that_does_not_exist(self):
        # Scenario "A page group in Spanish" (the messages of a handler)
        self.check(None, fake_submission(),
                   "Submission ID 1 not found.",
                   "No se encontró el envío con ID 1.", key="message")

    def test_two_tasks_with_several_files(self):
        # Scenario "A page group in Spanish"
        self.check(
            fake_submission(1, ("a.cpp", "b.cpp")),
            fake_submission(2, ("a.cpp", "b.cpp")),
            "Cannot compare submissions: they are for different tasks and "
            "have more than 1 file.",
            "No se pueden comparar los envíos: son de problemas distintos y "
            "tienen más de un archivo.", key="message")

    def test_a_file_missing_from_a_submission(self):
        # Scenario "A page group in Spanish"
        self.check(fake_submission(files=()), fake_submission(),
                   "File not present in old submission",
                   "El archivo no está en el envío anterior")
        self.check(fake_submission(), fake_submission(files=()),
                   "File not present in new submission",
                   "El archivo no está en el envío nuevo")

    def test_a_file_that_is_too_big(self):
        # Scenario "A page group in Spanish"
        big = {"a.cpp": b"x" * 1000001}
        self.check(fake_submission(), fake_submission(),
                   "Old file is too big to diff",
                   "El archivo anterior es demasiado grande para "
                   "compararlo", contents=big)
        # Only the new submission is big: its digest is not the old one.
        old = fake_submission()
        old.files["a.cpp"].digest = "small"
        self.check(old, fake_submission(), "New file is too big to diff",
                   "El archivo nuevo es demasiado grande para compararlo",
                   contents=big)

    def test_a_file_with_too_many_lines(self):
        # Scenario "A page group in Spanish"
        many = {"a.cpp": b"\n" * 5001}
        self.check(fake_submission(), fake_submission(),
                   "Old file has too many lines to diff",
                   "El archivo anterior tiene demasiadas líneas para "
                   "compararlo", contents=many)
        old = fake_submission()
        old.files["a.cpp"].digest = "small"
        self.check(old, fake_submission(),
                   "New file has too many lines to diff",
                   "El archivo nuevo tiene demasiadas líneas para "
                   "compararlo", contents=many)

    def test_two_equal_files(self):
        # Scenario "A page group in Spanish"
        self.check(fake_submission(), fake_submission(),
                   "No changes", "Sin cambios")


class TestSubmissionComment(unittest.TestCase):

    def notification(self, translation):
        handler = handler_in(SubmissionCommentHandler, translation)
        handler.safe_get_item = mock.MagicMock(
            return_value=SimpleNamespace(comment=""))
        error = ValueError("boom")
        handler.get_string = mock.MagicMock(side_effect=error)
        handler._post_sync("1")
        return handler.service.add_notification.call_args.args[1:], \
            repr(error)

    def test_the_subject_of_an_invalid_comment(self):
        # Scenario "Invalid field in English" and "Invalid field in
        # Spanish": the subject changes, the repr of the error does not.
        (subject, text), error = self.notification(DEFAULT_TRANSLATION)
        self.assertEqual((subject, text), ("Invalid field(s)", error))
        (subject, text), error = self.notification(SPANISH)
        self.assertEqual((subject, text), ("Campos no válidos", error))


if __name__ == "__main__":
    unittest.main()
