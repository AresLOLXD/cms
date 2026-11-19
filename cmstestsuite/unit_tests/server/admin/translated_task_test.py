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

"""Tests for the Spanish task pages of AWS and the notifications of
their handlers (lane L2 of aws-language-rest).

The pages are rendered from the real templates with the Spanish of
aws.po plus the fragments; nothing here says how a page looks.

"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from cms.db import Dataset, Task
from cms.grading.scoretypes import SCORE_TYPES
from cms.grading.tasktypes import TASK_TYPES
from cms.server.admin.handlers.task import AddTaskHandler, TaskHandler, \
    TaskListHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH


class English:
    """The identity translation, as AWS uses it for English."""
    gettext = staticmethod(lambda message: message)

    @staticmethod
    def ngettext(singular, plural, n):
        return singular if n == 1 else plural


ENGLISH = English


def render_core(name, translation=None, **params):
    """Render the core block of a template, in English by default."""
    template = AWS_ENVIRONMENT.get_template(name)
    if translation is not None:
        params.update(gettext=translation.gettext,
                      ngettext=translation.ngettext)
    params.setdefault("url", lambda *parts: "/" + "/".join(
        str(part) for part in parts))
    params.setdefault("xsrf_form_html", "")
    params.setdefault("admin", SimpleNamespace(permission_all=True))
    return "".join(template.blocks["core"](template.new_context(params)))


def make_handler(cls, translation, **form):
    """Return a handler that reads the form and notifies, with no DB."""
    handler = cls.__new__(cls)
    handler._ = translation.gettext
    handler.n_ = translation.ngettext
    handler.application = MagicMock()
    handler.get_argument = lambda name, default=None: form.get(name, default)
    handler.redirect = MagicMock()
    handler.url = lambda *parts: "/" + "/".join(str(p) for p in parts)
    return handler


def notifications(handler):
    return [call.args[1:]
            for call in handler.service.add_notification.call_args_list]


TASK = SimpleNamespace(id=7, name="sum", title="Sum")


class TestTaskSmallPages(unittest.TestCase):

    def test_the_new_task_page_in_spanish(self):
        # Scenario "A page group in Spanish" (task pages)
        html = render_core("add_task.html", SPANISH)

        for text in ("Nuevo problema", "Nombre:",
                     'title="Un nombre corto para el problema, de '
                     'preferencia solo con letras, números y guiones '
                     'bajos."'):
            self.assertIn(text, html)
        self.assertNotIn("New task", html)

    def test_the_upload_pages_in_spanish(self):
        # Scenario "A page group in Spanish" (task pages)
        statement = render_core("add_statement.html", SPANISH, task=TASK)
        attachment = render_core("add_attachment.html", SPANISH, task=TASK)

        for text in ("- Subir enunciado", "Código de idioma:",
                     'value="Subir"'):
            self.assertIn(text, statement)
        for text in ("- Subir adjunto", 'value="Subir"'):
            self.assertIn(text, attachment)
        self.assertNotIn("Upload", statement + attachment)

    def test_the_remove_page_in_spanish(self):
        # Scenario "A page group in Spanish" (task pages)
        html = render_core("task_remove.html", SPANISH, task=TASK,
                           submission_count=3)

        for text in ("Borrar problema",
                     "¿Seguro que quieres borrar el problema sum de CMS, "
                     "con todos sus datos relacionados?",
                     "Se borrarán 3 envío(s).",
                     "Esta operación no se puede deshacer.",
                     "Sí, borrar", ">No</a>"):
            self.assertIn(text, html)
        self.assertNotIn("Are you sure", html)

    def test_the_task_list_posts_the_english_operation(self):
        # Scenario "No translated posted values": the label is Spanish,
        # the posted value stays what TaskListHandler compares with.
        html = render_core("tasks.html", SPANISH, task_list=[TASK])

        self.assertIn('value="Remove"', html)
        self.assertIn(">Quitar</button>", html)
        self.assertEqual(TaskListHandler.REMOVE, "Remove")
        for text in ("Lista de problemas", "Editar el problema seleccionado:",
                     "<th>Nombre</th>", "<th>Título</th>"):
            self.assertIn(text, html)


class TestTaskPage(unittest.TestCase):

    def setUp(self):
        self.task = Task(name="sum", title="Sum")
        self.dataset = Dataset(
            task=self.task, description="main", task_type="Batch",
            task_type_parameters=[], score_type="Sum",
            score_type_parameters=100)
        self.dataset.id = 1

    def page(self, translation=None, **extra):
        params = dict(
            task=self.task, submissions=[], primary_statements=[],
            score_type_warnings={}, rtd_version="latest", timestamp=None,
            SCORE_TYPES=SCORE_TYPES, TASK_TYPES=TASK_TYPES, LANGUAGES=[])
        params.update(extra)
        return render_core("task.html", translation, **params)

    def test_the_shared_blocks_in_spanish(self):
        # Scenario "A page group in Spanish": the token, limit and
        # score blocks that contest.html and add_dataset.html reuse.
        html = self.page(SPANISH)

        for text in (
                'Parámetros de tokens (<a href="http://cms.readthedocs.org'
                '/en/latest/Configuring%20a%20contest.html'
                '#feedback-to-contestants" target="_blank">documentación'
                '</a>)',
                "Número máximo de tokens", "Intervalo mínimo entre tokens",
                "Número máximo de user tests", ">Límites</h2>",
                "Decimales del puntaje", "Límite de tiempo",
                "Límite de memoria", "segundo(s)",
                "Configuración del tipo de problema (<a ",
                "Tipo de puntaje", "Parámetros del puntaje"):
            self.assertIn(text, html)
        for text in ("Maximum number", "Token mode", "Score Type",
                     "Time limit", "second(s)"):
            self.assertNotIn(text, html)

    def test_the_task_page_in_spanish(self):
        # Scenario "A page group in Spanish": the configuration, the
        # datasets and the test cases.
        html = self.page(SPANISH)

        for text in ("Configuración del problema", "Enunciados",
                     "Aún no hay envíos para este problema.",
                     "Dataset: main", "[Ver resultados]", "[Clonar]",
                     "Casos de prueba", "Aún no se ha subido ningún "
                     "enunciado", "Actualizar no recalcula el puntaje"):
            self.assertIn(text, html)
        self.assertNotIn("[Clone]", html)

    def test_the_submission_count_in_both_languages(self):
        # Scenario "A page in English is unchanged": "1 submission".
        for translation, one, many in ((None, "1 submission", "2 submissions"),
                                       (SPANISH, "1 envío", "2 envíos")):
            self.assertIn(one, self.page(
                translation, submissions=[object()]))
            self.assertIn(many, self.page(
                translation, submissions=[object(), object()]))

    def test_the_remove_button_posts_the_english_value(self):
        # Scenario "No translated posted values": the label is Spanish
        # and the value is what TaskListHandler compares with.
        html = self.page(SPANISH)

        self.assertIn('value="Remove"', html)
        self.assertIn(">Quitar</button>", html)

    def test_the_warning_of_an_invalid_dataset_in_spanish(self):
        # Scenario "A page group in Spanish": our prefix is translated,
        # the detail of cms.db stays English.
        html = self.page(SPANISH, score_type_warnings={1: "No testcase."})

        self.assertIn("Actualmente no válido: No testcase.</strong>", html)

    def test_an_unchecked_score_parameters_message_in_spanish(self):
        # Scenario "Invalid field in Spanish": the text that the handler
        # stores for a validation that failed.
        handler = TaskHandler.__new__(TaskHandler)
        handler._ = SPANISH.gettext
        handler.safe_get_item = MagicMock(return_value=self.task)
        handler.sql_session = MagicMock()
        handler.sql_session.execute.return_value.scalars.return_value \
            .all.return_value = []
        handler.render_params = MagicMock(return_value={})
        handler.render = MagicMock()
        self.dataset.validate_score_type = MagicMock(
            side_effect=RuntimeError("x"))

        handler._get_sync("5")

        self.assertEqual(
            handler.render.call_args.kwargs["score_type_warnings"],
            {1: "no se pudieron revisar los parámetros del puntaje."})


class TestTaskHandlerNotifications(unittest.TestCase):

    def test_a_task_without_name_in_spanish(self):
        # Scenario "Invalid field in Spanish"
        handler = make_handler(AddTaskHandler, SPANISH)

        handler._post_sync()

        self.assertEqual(notifications(handler), [
            ("Campos no válidos",
             "AssertionError('No se indicó el nombre del problema.')")])

    def test_a_task_without_name_in_english(self):
        # Scenario "Invalid field in English": the text of today.
        handler = make_handler(AddTaskHandler, ENGLISH)

        handler._post_sync()

        self.assertEqual(notifications(handler), [
            ("Invalid field(s)",
             "AssertionError('No task name specified.')")])

    def test_an_invalid_operation_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        for translation, subject in ((SPANISH, "Operación no válida: x"),
                                     (ENGLISH,
                                      "Invalid operation x")):
            handler = make_handler(TaskListHandler, translation,
                                   task_id="1", operation="x")

            handler._post_sync()

            self.assertEqual(notifications(handler), [(subject, "")])


if __name__ == "__main__":
    unittest.main()
