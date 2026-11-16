#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""Tests for the Spanish contest pages of AWS (the settings, the list,
the announcements and the questions) and the notifications of their
handlers (lane L1 of aws-language-rest).

The pages are rendered from the real templates with the Spanish of
aws.po plus the fragments; nothing here says how a page looks.

"""

import re
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from cms.db import Question
from cms.server.admin.handlers.contest import AddContestHandler, \
    ContestHandler, ContestListHandler
from cms.server.admin.handlers.contestannouncement import \
    AddAnnouncementHandler
from cms.server.admin.handlers.contestquestion import QuestionReplyHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH
from cmstestsuite.unit_tests.server.admin.users_pages_test import render, \
    visible


class English:
    """The identity translation, as AWS uses it for English."""
    gettext = staticmethod(lambda message: message)

    @staticmethod
    def ngettext(singular, plural, n):
        return singular if n == 1 else plural


def page(name, translation=None, **params):
    """The core block of the page, English by default."""
    context = {"url": lambda *parts: "/" + "/".join(str(p) for p in parts),
               "xsrf_form_html": "",
               "admin": SimpleNamespace(permission_all=True), **params}
    if translation is not None:
        context.update(gettext=translation.gettext,
                       ngettext=translation.ngettext)
    # The macros that the pages import "with context", as they would.
    for alias, macro in (("macro_question", "question"),
                         ("macro_markdown", "markdown_input")):
        context[alias] = AWS_ENVIRONMENT.get_template(
            "macro/%s.html" % macro).make_module(context)
    return render(AWS_ENVIRONMENT, name, **context)


def text_of(html):
    """The text a reader gets, with the values of the attributes."""
    text, attributes = visible(html)
    return " | ".join([text] + attributes)


def make_handler(cls, translation, form=None, **attributes):
    """Return a handler that reads the form and notifies, with no DB."""
    form = form or {}
    handler = cls.__new__(cls)
    handler._ = translation.gettext
    handler.n_ = translation.ngettext
    handler.application = MagicMock()
    handler.sql_session = MagicMock()
    handler.get_argument = lambda name, default=None: form.get(name, default)
    handler.redirect = MagicMock()
    handler.url = lambda *parts: "/" + "/".join(str(p) for p in parts)
    handler.try_commit = MagicMock(return_value=False)
    for name, value in attributes.items():
        setattr(handler, name, value)
    return handler


def notification(handler):
    (_when, subject, text), _kwargs = \
        handler.service.add_notification.call_args
    return subject, text


def operation_buttons(html):
    """The (value posted, label shown) of the buttons that send an
    operation."""
    return re.findall(
        r'<button[^>]*name="operation"[^>]*value="([^"]*)"[^>]*>([^<]*)'
        r"</button>", html)


GROUP = SimpleNamespace(
    id=4, name="default", start=datetime(2026, 1, 1, 10),
    stop=datetime(2026, 1, 1, 15), per_user_time=None,
    analysis_enabled=False, analysis_start=datetime(2026, 1, 2),
    analysis_stop=datetime(2026, 1, 3))

CONTEST = SimpleNamespace(
    id=3, name="d1", description="Day 1", active=True,
    ranking_group_id=None, allowed_localizations=[], languages=[],
    submissions_download_allowed=True, allow_questions=True,
    allow_user_tests=True, score_precision=2,
    allow_unofficial_submission_before_analysis_mode=False,
    timezone=None, block_hidden_participations=False,
    allow_password_authentication=True, allow_registration=False,
    ip_restriction=False, ip_autologin=False, token_mode="disabled",
    token_max_number=None, token_min_interval=timedelta(0),
    token_gen_initial=0, token_gen_number=0,
    token_gen_interval=timedelta(minutes=30), token_gen_max=None,
    max_submission_number=None, max_user_test_number=None,
    min_submission_interval=None, min_submission_interval_grace_period=None,
    min_user_test_interval=None, main_group_id=4, main_group=GROUP,
    participations=[], tasks=[], announcements=[])


class TestContestPagesInSpanish(unittest.TestCase):

    def settings(self, translation=None):
        return page("contest.html", translation, contest=CONTEST,
                    ranking_group_list=[], rtd_version="latest")

    def test_the_contest_settings_page_in_spanish(self):
        # Scenario "A page group in Spanish" (contest settings)
        text = text_of(self.settings(SPANISH))

        for expected in (
                "Configuración del concurso", "Información del concurso",
                "Localizaciones permitidas", "Inicio de sesión",
                "Horario del concurso", "Fase de análisis",
                "Periodo de gracia del intervalo mínimo entre envíos",
                "Ejemplo: 'en,ja' (sin comillas).",
                "Ejemplo: 'Europe/Rome', 'America/New_York', …"):
            self.assertIn(expected, text)
        for english in ("Contest information", "Logging in", "Analysis mode",
                        "Allowed localizations"):
            self.assertNotIn(english, text)

    def test_the_shared_token_block_matches_the_task_page(self):
        # Scenario "A page group in Spanish": the token heading, the
        # labels and the second tooltip line are the msgids of the task
        # page, with the same documentation link.
        html = self.settings(SPANISH)

        for expected in (
                'Parámetros de tokens (<a href="http://cms.readthedocs.org'
                '/en/latest/Configuring%20a%20contest.html'
                '#feedback-to-contestants" target="_blank">documentación'
                '</a>)',
                "Modo de token", "Número máximo de tokens", ">Límites</h2>",
                "Decimales del puntaje", "Se usa solo si los tokens son "
                "limitados."):
            self.assertIn(expected, html)

    def test_the_groups_link_keeps_its_markup(self):
        # Scenario "A page group in Spanish": the link inside the sentence
        html = self.settings(SPANISH)

        self.assertIn("usa la sección de <a href='/contest/3/groups'>grupos"
                      "</a>.", html)

    def test_the_settings_page_in_english_is_unchanged(self):
        # Scenario "A page in English is unchanged": the title lines keep
        # their line break, the link its quotes.
        html = self.settings()

        self.assertIn("The list of languages that contestants can use, or "
                      "empty for allowing any language.\n", html)
        self.assertIn("Example: &#39;en,ja&#39; (without quotes).\"></span>",
                      html)
        self.assertIn("please use the <a href='/contest/3/groups'>groups</a> "
                      "section.", html)
        self.assertIn('value="Update"', html)
        self.assertIn(">Disabled</option>", html)

    def test_the_contest_list_and_the_small_pages_in_spanish(self):
        # Scenario "A page group in Spanish" (contest list and removal)
        listing = text_of(page(
            "contests.html", SPANISH, contest_list=[SimpleNamespace(
                id=3, name="d1", description="Day 1", active=True,
                ranking_group=None)]))
        new = text_of(page("add_contest.html", SPANISH))
        removal = text_of(page("contest_remove.html", SPANISH,
                               contest=CONTEST, submission_count=5))

        for expected in ("Lista de concursos",
                         "Editar el concurso seleccionado:", "Activo",
                         "Grupo de ranking"):
            self.assertIn(expected, listing)
        self.assertIn("Nuevo concurso", new)
        self.assertIn("Un nombre corto para el concurso, de preferencia "
                      "solo con letras, números y guiones bajos.", new)
        for expected in (
                "Borrar concurso",
                "¿Seguro que quieres borrar el concurso d1 de CMS, con "
                "todos sus datos relacionados?",
                "Esto borrará 0 participación(es) y 5 envío(s).",
                "Esta operación no se puede deshacer.", "Sí, borrar"):
            self.assertIn(expected, removal)
        self.assertNotIn("Are you sure", removal)

    def test_the_active_column_in_both_languages(self):
        # Scenario "A page in English is unchanged": "yes" and "no".
        params = dict(contest_list=[
            SimpleNamespace(id=1, name="a", description="", active=True,
                            ranking_group=None),
            SimpleNamespace(id=2, name="b", description="", active=False,
                            ranking_group=None)])

        english = text_of(page("contests.html", **params))
        spanish = text_of(page("contests.html", SPANISH, **params))

        self.assertIn("a yes b no", english)
        self.assertIn("a sí b no", spanish)

    def test_the_announcements_page_in_spanish(self):
        # Scenario "A page group in Spanish" (announcements)
        announcement = SimpleNamespace(
            id=9, timestamp=datetime(2026, 1, 1, 10), subject="Hello",
            text="Body", admin=None)
        contest = SimpleNamespace(**{**vars(CONTEST),
                                     "announcements": [announcement]})
        params = dict(contest=contest, admin=SimpleNamespace(
            permission_all=True, permission_messaging=True))

        text = text_of(page("announcements.html", SPANISH, **params))

        for expected in ("Anuncios", "Asunto", "Texto", "Agregar anuncio",
                         "Por: <desconocido>", "Quitar",
                         "Vista previa de Markdown",
                         "Aquí puedes usar Markdown."):
            self.assertIn(expected, text)
        self.assertNotIn("By:", text)
        english = text_of(page("announcements.html", **params))
        self.assertIn("By: <unknown>", english)
        self.assertIn("Preview Markdown", english)

    def test_an_empty_announcements_and_questions_pages_in_spanish(self):
        # Scenario "A page group in Spanish" (communication)
        announcements = text_of(page(
            "announcements.html", SPANISH, contest=CONTEST,
            admin=SimpleNamespace(permission_all=False,
                                  permission_messaging=False)))
        questions = text_of(page("questions.html", SPANISH, contest=CONTEST,
                                 questions=[]))

        self.assertIn("No hay anuncios.", announcements)
        self.assertIn("Preguntas", questions)
        self.assertIn("No hay preguntas.", questions)
        self.assertEqual(
            "No questions.",
            re.search(r"No questions\.", text_of(page(
                "questions.html", contest=CONTEST, questions=[]))).group())


def question(**fields):
    values = dict(
        id=11, subject="Help", text="Please", question_timestamp=
        datetime(2026, 1, 1, 10), reply_timestamp=None, reply_subject=None,
        reply_text=None, ignored=False, admin=None,
        participation=SimpleNamespace(user=SimpleNamespace(
            id=7, username="ana", first_name="Ana", last_name="Pérez")))
    values.update(fields)
    return SimpleNamespace(**values)


class TestQuestionsInSpanish(unittest.TestCase):

    def questions(self, translation=None, *msgs):
        return page("questions.html", translation, contest=CONTEST,
                    questions=list(msgs),
                    question_quick_answers=Question.QUICK_ANSWERS,
                    admin=SimpleNamespace(permission_all=True,
                                          permission_messaging=True))

    def test_the_question_macro_in_spanish(self):
        # Scenario "A page group in Spanish" (questions)
        mine = SimpleNamespace(name="root")
        text = text_of(self.questions(
            SPANISH, question(),
            question(id=12, admin=mine),
            question(id=13, ignored=True, admin=mine),
            question(id=14, reply_timestamp=datetime(2026, 1, 1, 11),
                     reply_subject="Yes", reply_text="", admin=mine)))

        for expected in ("Aún sin respuesta.", "Tomar", "Soltar",
                         "Ignorar", "Dejar de ignorar", "Responder",
                         "Editar respuesta", "Respuesta rápida:",
                         "Respuesta alternativa:", "Otra",
                         "Tomada por: root", "Ignorada por: root",
                         "Respuesta de: root", "Enviar"):
            self.assertIn(expected, text)
        for english in ("Not yet replied", "Unclaim", "Precompiled",
                        "Alternative answer"):
            self.assertNotIn(english, text)

    def test_the_question_macro_in_english_is_unchanged(self):
        # Scenario "A page in English is unchanged"
        mine = SimpleNamespace(name="root")
        text = text_of(self.questions(
            None, question(id=12, admin=mine),
            question(id=13, ignored=True, admin=mine)))

        for expected in ("Claimed by: root", "Ignored by: root",
                         "Not yet replied.", "Precompiled answer:",
                         "Alternative answer:"):
            self.assertIn(expected, text)

    def test_the_quick_answers_are_labeled_in_spanish(self):
        # Scenario "A quick answer from a Spanish page" (the labels): the
        # keys that the form posts do not change.
        html = self.questions(SPANISH, question())

        options = re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>',
                             html)

        self.assertEqual(options, [
            ("yes", "Sí"), ("no", "No"),
            ("invalid", "Pregunta no válida (no es una pregunta de sí o no)"),
            ("nocomment", "Sin comentarios/Consulta el enunciado del "
             "problema"),
            ("other", "Otra")])

    def test_the_quick_answers_in_english_are_unchanged(self):
        # Scenario "A page in English is unchanged"
        options = re.findall(
            r'<option value="([^"]*)"[^>]*>([^<]*)</option>',
            self.questions(None, question()))

        self.assertEqual(options, [
            ("yes", "Yes"), ("no", "No"),
            ("invalid", "Invalid Question (not a Yes/No Question)"),
            ("nocomment", "No Comment/Please refer to task statement"),
            ("other", "Other")])

    def test_a_quick_answer_from_a_spanish_page_is_stored_in_english(self):
        # Scenario "A quick answer from a Spanish page": the admin picks the
        # option labeled «Sí» (value "yes"); the stored subject is "Yes".
        html = self.questions(SPANISH, question())
        (value, label), = [
            (v, t) for v, t in re.findall(
                r'<option value="([^"]*)"[^>]*>([^<]*)</option>', html)
            if t == "Sí"]
        stored = question(admin=None)
        handler = make_handler(
            QuestionReplyHandler, SPANISH,
            {"reply_question_quick_answer": value,
             "reply_question_text": "ignored long answer"},
            current_user=SimpleNamespace(name="root"))

        handler.process_question(stored)

        self.assertEqual(value, "yes")
        self.assertEqual(stored.reply_subject, "Yes")
        self.assertEqual(stored.reply_text, "")
        self.assertIsNotNone(stored.reply_timestamp)

    def test_an_invalid_quick_answer_stores_an_empty_subject(self):
        # Scenario "A quick answer from a Spanish page": a key that is not
        # in QUICK_ANSWERS (as a translated label would be) is refused.
        stored = question()
        handler = make_handler(
            QuestionReplyHandler, SPANISH,
            {"reply_question_quick_answer": "Sí",
             "reply_question_text": "long"},
            current_user=SimpleNamespace(name="root"))

        handler.process_question(stored)

        self.assertEqual(stored.reply_subject, "")
        self.assertEqual(stored.reply_text, "long")


class TestContestHandlersInBothLanguages(unittest.TestCase):

    def test_a_contest_without_name_in_spanish(self):
        # Scenario "Invalid field in Spanish"
        handler = make_handler(AddContestHandler, SPANISH)

        handler._post_sync()

        self.assertEqual(notification(handler), (
            "Campos no válidos",
            "AssertionError('No se indicó el nombre del concurso.')"))

    def test_a_contest_without_name_in_english(self):
        # Scenario "Invalid field in English": the text of today.
        handler = make_handler(AddContestHandler, English)

        handler._post_sync()

        self.assertEqual(notification(handler), (
            "Invalid field(s)",
            "AssertionError('No contest name specified.')"))

    def test_an_edited_contest_with_a_bad_number_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English": the
        # message that parse_int raises is translated, repr() kept.
        for translation, subject, detail in (
                (SPANISH, "Campos no válidos.",
                 "ValueError('No se puede convertir abc a entero.')"),
                (English, "Invalid field(s).",
                 "ValueError(\"Can't cast abc to int.\")")):
            contest = SimpleNamespace(
                get_attrs=lambda: {}, main_group=GROUP)
            handler = make_handler(
                ContestHandler, translation,
                {"name": "d1", "score_precision": "abc"},
                safe_get_item=MagicMock(return_value=contest),
                get_arguments=lambda name: [])

            handler._post_sync("3")

            self.assertEqual(notification(handler), (subject, detail))

    def test_an_invalid_operation_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        for translation, subject in ((SPANISH, "Operación no válida x"),
                                     (English, "Invalid operation x")):
            handler = make_handler(ContestListHandler, translation,
                                   {"contest_id": "3", "operation": "x"})

            handler._post_sync()

            self.assertEqual(notification(handler), (subject, ""))

    def test_an_announcement_without_subject_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        for translation, subject in ((SPANISH, "El asunto es obligatorio."),
                                     (English, "Subject is mandatory.")):
            handler = make_handler(
                AddAnnouncementHandler, translation, {"subject": ""},
                safe_get_item=MagicMock())

            handler._post_sync("3")

            self.assertEqual(notification(handler), (subject, ""))


class TestContestOperationsFromASpanishPage(unittest.TestCase):

    def test_the_contest_list_posts_the_english_remove(self):
        # Scenario "An operation posted from a Spanish page": the label is
        # Spanish, the value is what ContestListHandler compares with, and
        # the handler opens the page that asks for confirmation.
        html = page("contests.html", SPANISH, contest_list=[])

        (value, label), = operation_buttons(html)
        handler = make_handler(ContestListHandler, SPANISH,
                               {"contest_id": "3", "operation": value})
        handler._post_sync()

        self.assertEqual((value, label), ("Remove", "Quitar"))
        self.assertEqual(ContestListHandler.REMOVE, "Remove")
        handler.redirect.assert_called_once_with("/contests/3/remove")
        handler.service.add_notification.assert_not_called()

    def test_the_contest_page_posts_the_english_remove(self):
        # Scenario "No translated posted values": the Remove button of the
        # settings page, also posted to ContestListHandler.
        html = page("contest.html", SPANISH, contest=CONTEST,
                    ranking_group_list=[], rtd_version="latest")

        self.assertEqual(operation_buttons(html), [("Remove", "Quitar")])


if __name__ == "__main__":
    unittest.main()
