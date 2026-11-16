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

"""Tests for the shared chrome of AWS in Spanish (change aws-language-rest,
lane L0): the sidebar and footer, the logged-in login page, the notifications
and form errors of the base handler, the dataset attributes, and the shared
script aws_utils.js, which runs under node against stubs.

Nothing here renders a page in a browser.

"""

import threading
import unittest
from functools import partial
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cms.server.admin import formatting
from cms.server.admin.handlers import base
from cms.server.admin.handlers.base import BaseHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cms.server.admin.server import AdminWebServer
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH


class TestBaseHandlerTexts(unittest.TestCase):

    def setUp(self):
        self.handler = BaseHandler.__new__(BaseHandler)
        self.handler.application = MagicMock()
        self.handler.sql_session = MagicMock()
        self.handler.request = MagicMock(method="POST", path="/x")
        self.notification = self.handler.application.service.add_notification
        patch.object(base, "logger").start()
        self.addCleanup(patch.stopall)

    def speak_spanish(self):
        self.handler._ = SPANISH.gettext
        self.handler.n_ = SPANISH.ngettext

    def arguments(self, **values):
        self.handler.get_argument = \
            lambda name, default=None: values.get(name, default)

    def test_the_notifications_of_a_commit_in_spanish(self):
        # Scenario "Invalid field in Spanish" family (aws-language): the
        # subject and the text of the notification are in Spanish
        self.speak_spanish()

        self.assertTrue(self.handler.try_commit())
        self.assertEqual(self.notification.call_args.args[1:],
                         ("Operación correcta.", ""))

        from sqlalchemy.exc import OperationalError
        self.handler.sql_session.commit.side_effect = OperationalError(
            "SELECT 1", {}, Exception("gone"), connection_invalidated=True)
        self.assertFalse(self.handler.try_commit())
        self.assertEqual(
            self.notification.call_args.args[1:],
            ("La operación falló.",
             "La base de datos no respondió. Inténtalo de nuevo."))

    def test_the_notifications_of_a_commit_in_english_are_unchanged(self):
        # Scenario "Invalid field in English" (aws-language)
        self.assertTrue(self.handler.try_commit())
        self.assertEqual(self.notification.call_args.args[1:],
                         ("Operation successful.", ""))

        from sqlalchemy.exc import OperationalError
        self.handler.sql_session.commit.side_effect = OperationalError(
            "SELECT 1", {}, Exception("gone"), connection_invalidated=True)
        self.assertFalse(self.handler.try_commit())
        self.assertEqual(
            self.notification.call_args.args[1:],
            ("Operation failed.", "The database did not answer, try again."))

    def test_a_notification_keeps_the_language_of_who_raised_it(self):
        # Scenario "A notification read by another admin" (aws-language)
        service = SimpleNamespace(notifications=[],
                                  notifications_lock=threading.Lock())
        service.add_notification = partial(
            AdminWebServer.add_notification, service)
        service.take_notifications = partial(
            AdminWebServer.take_notifications, service)
        self.handler.application.service = service
        self.speak_spanish()
        self.handler.try_commit()

        # An admin with English chosen polls first: the text is Spanish.
        [(_, subject, text)] = service.take_notifications()

        self.assertEqual((subject, text), ("Operación correcta.", ""))

    def test_a_bad_number_in_spanish(self):
        # Scenario "Invalid field in Spanish" (aws-language)
        self.speak_spanish()
        self.arguments(time_limit="abc", memory_limit="abc", flag="1")
        attrs: dict = {}

        with self.assertRaises(ValueError) as caught:
            self.handler.get_time_limit(attrs, "time_limit")
        self.assertEqual(repr(caught.exception), repr(ValueError(
            "No se puede convertir abc a número decimal.")))
        with self.assertRaises(ValueError) as caught:
            self.handler.get_memory_limit(attrs, "memory_limit")
        self.assertEqual(str(caught.exception),
                         "No se puede convertir abc a entero.")
        self.arguments(time_limit="-1", memory_limit="0")
        with self.assertRaises(ValueError) as caught:
            self.handler.get_time_limit(attrs, "time_limit")
        self.assertEqual(str(caught.exception),
                         "El límite de tiempo está fuera de rango.")
        with self.assertRaises(ValueError) as caught:
            self.handler.get_memory_limit(attrs, "memory_limit")
        self.assertEqual(str(caught.exception), "Límite de memoria no válido.")

    def test_the_form_errors_in_english_are_unchanged(self):
        # Scenario "Invalid field in English" (aws-language), repr included
        self.arguments(time_limit="abc", memory_limit="0",
                       submission_format_choice="x")
        for method, field, expected in (
                (self.handler.get_time_limit, "time_limit",
                 "Can't cast abc to float."),
                (self.handler.get_memory_limit, "memory_limit",
                 "Invalid memory limit.")):
            with self.assertRaises(ValueError) as caught:
                method({}, field)
            self.assertEqual(repr(caught.exception),
                             repr(ValueError(expected)))
        with self.assertRaises(ValueError) as caught:
            self.handler.get_submission_format({"name": "n"})
        self.assertEqual(str(caught.exception),
                         "Submission format not recognized.")

    def test_the_task_and_score_type_errors_in_spanish(self):
        # Scenario "Invalid field in Spanish" (aws-language)
        self.speak_spanish()
        self.arguments(score_params="{not json", score="Sum",
                       task_type="Nope")
        for call, text in (
                (lambda: self.handler.get_task_type({}, "task_type", "p"),
                 "No se reconoce el tipo de problema: Nope."),
                (lambda: self.handler.get_task_type({}, "missing", "p"),
                 "No se encontró el tipo de problema."),
                (lambda: self.handler.get_score_type(
                    {}, "score", "score_params"),
                 "Los parámetros del tipo de puntaje no son un JSON válido."),
                (lambda: self.handler.get_score_type({}, "missing", "p"),
                 "No se encontró el tipo de puntaje.")):
            with self.assertRaises(ValueError) as caught:
                call()
            self.assertEqual(str(caught.exception), text)

    def test_the_main_group_times_in_spanish(self):
        # Scenario "Invalid field in Spanish" (aws-language): the asserts
        self.speak_spanish()
        self.arguments()

        with self.assertRaises(AssertionError) as caught:
            self.handler.get_group_settings(MagicMock())

        self.assertEqual(str(caught.exception),
                         "No se indicó la hora de inicio del grupo principal.")


class TestFormatDatasetAttrs(unittest.TestCase):

    def dataset(self, live: bool, autojudge: bool):
        dataset = SimpleNamespace(autojudge=autojudge)
        dataset.task = SimpleNamespace(
            active_dataset=dataset if live else None)
        return dataset

    def test_the_default_is_english(self):
        # Scenario "A page in English is unchanged" (aws-language)
        self.assertEqual(
            formatting.format_dataset_attrs(self.dataset(True, False)),
            " (Live)")
        self.assertEqual(
            formatting.format_dataset_attrs(self.dataset(False, True)),
            " (Background judging)")
        self.assertEqual(
            formatting.format_dataset_attrs(self.dataset(False, False)), "")

    def test_the_attributes_in_spanish(self):
        # Scenario "A page group in Spanish" (aws-language)
        self.assertEqual(
            formatting.format_dataset_attrs(
                self.dataset(True, False), SPANISH.gettext), " (En vivo)")
        self.assertEqual(
            formatting.format_dataset_attrs(
                self.dataset(False, True), SPANISH.gettext),
            " (Evaluación en segundo plano)")

    def test_the_filter_follows_the_language_of_the_page(self):
        # Scenario "A page group in Spanish" (aws-language): the filter
        # takes the gettext of the request from the template context
        template = AWS_ENVIRONMENT.from_string(
            "{{ dataset|format_dataset_attrs }}")
        dataset = self.dataset(True, False)

        self.assertEqual(template.render(dataset=dataset), " (Live)")
        self.assertEqual(
            template.render(dataset=dataset, gettext=SPANISH.gettext),
            " (En vivo)")


if __name__ == "__main__":
    unittest.main()
