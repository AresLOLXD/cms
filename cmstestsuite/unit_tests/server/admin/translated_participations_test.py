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

"""Tests for the AWS pages of the participations, the contest tasks, the
activity and the ranking in Spanish.

The pages are rendered from the template with the Spanish translation
built from the committed catalog and fragments. Nothing here says how a
page looks.

"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from cms.server.admin.handlers.contestuser import AddContestUserHandler, \
    ParticipationHandler
from cms.server.admin.handlers.contesttask import AddContestTaskHandler, \
    ContestTasksHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.server.admin import aws_render
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH
from cmstestsuite.unit_tests.server.admin.translated_users_test import \
    notification, operation_buttons, run_post
from cmstestsuite.unit_tests.server.admin.users_pages_test import fake_url, \
    render, visible


def page(name: str, translation=None, **params) -> str:
    """The text of the core block of the page, with the attributes."""
    text, attributes = visible(render(AWS_ENVIRONMENT, name, translation,
                                      **params))
    return " | ".join([text] + attributes)


def macros(translation, *names: str) -> dict:
    """The macro modules a page imports (render() runs only its core
    block, so the page's own imports do not run), as macro_<name>."""
    context = {"gettext": translation.gettext,
               "ngettext": translation.ngettext, "url": fake_url}
    return {"macro_" + name: AWS_ENVIRONMENT.get_template(
        f"macro/{name}.html").make_module(context) for name in names}


class TestContestTasksInSpanish(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.tasks = [self.add_task(contest=self.contest, num=n)
                      for n in range(3)]
        self.session.commit()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def post(self, translation, form: dict) -> ContestTasksHandler:
        """Post the form to the handler, on the real session."""
        handler = ContestTasksHandler.__new__(ContestTasksHandler)
        handler._ = translation.gettext
        handler.sql_session = self.session
        handler.get_argument = lambda name, *default: form[name]
        handler.url = lambda *parts: "/" + "/".join(map(str, parts))
        handler.redirect = MagicMock()
        handler.schedule_rpc = MagicMock()
        handler.application = MagicMock()
        handler._post_sync(str(self.contest.id))
        return handler

    def test_the_contest_tasks_page_in_spanish(self):
        # Scenario: A page group in Spanish (participations and contest tasks)
        self.session.refresh(self.contest)
        text = page("contest_tasks.html", SPANISH, contest=self.contest,
                    unassigned_tasks=[])

        for expected in (f"Lista de problemas de {self.contest.name}",
                         "Agrega un problema nuevo:", "Selecciona un problema "
                         "nuevo", "Mover el problema seleccionado:",
                         "Quitar del concurso", "Título"):
            self.assertIn(expected, text)
        self.assertNotIn("Tasks list of", text)

    def test_an_operation_posted_from_a_spanish_page(self):
        # Scenario: An operation posted from a Spanish page
        self.session.refresh(self.contest)
        html = render(AWS_ENVIRONMENT, "contest_tasks.html", SPANISH,
                      contest=self.contest, unassigned_tasks=[])
        buttons = dict((label, value)
                       for value, label in operation_buttons(html))
        self.assertEqual(
            buttons,
            {"Quitar del concurso": "Remove from contest",
             "al principio": "to the top", "subir 1": "up by 1",
             "bajar 1": "down by 1", "al final": "to the bottom"})

        # The value the Spanish "subir 1" button posts moves the task.
        self.post(SPANISH, {"task_id": str(self.tasks[2].id),
                            "operation": buttons["subir 1"]})

        self.session.expire_all()
        self.assertEqual([t.num for t in self.tasks], [0, 2, 1])


class TestContestTaskFormErrors(unittest.TestCase):

    def test_a_form_error_in_spanish(self):
        # Scenario: Invalid field in Spanish
        handler = run_post(AddContestTaskHandler, {"task_id": "null"},
                           SPANISH, args=("1",))

        self.assertEqual(
            notification(handler),
            ("Campos no válidos",
             repr(AssertionError("Selecciona un problema válido"))))

    def test_a_form_error_in_english(self):
        # Scenario: Invalid field in English (the same form, unchanged)
        handler = run_post(AddContestTaskHandler, {"task_id": "null"},
                           args=("1",))

        self.assertEqual(
            notification(handler),
            ("Invalid field(s)",
             repr(AssertionError("Please select a valid task"))))


class TestContestUsersInSpanish(unittest.TestCase):

    def test_the_contest_users_page_in_spanish(self):
        # Scenario: A page group in Spanish (participations)
        group = SimpleNamespace(id=5, name="main")
        contest = SimpleNamespace(id=1, name="d1", groups=[group],
                                  main_group_id=5, participations=[])
        text = page("contest_users.html", SPANISH, contest=contest,
                    unassigned_users=[])

        for expected in ("Lista de usuarios", "Agrega un usuario nuevo:",
                         "Selecciona un usuario nuevo", "Agregar usuario",
                         "Quitar seleccionados del concurso"):
            self.assertIn(expected, text)
        self.assertNotIn("Add a new user", text)

    def test_the_participation_removal_page_in_spanish(self):
        # Scenario: A page group in Spanish (participations)
        params = {"user": SimpleNamespace(id=7, username="ana"),
                  "contest": SimpleNamespace(id=1, name="d1"),
                  "submission_count": 5}

        text = page("participation_remove.html", SPANISH, **params)
        for expected in (
                "Quitar participación",
                "¿Seguro que quieres quitar al usuario ana del concurso "
                "d1, incluidos todos sus datos relacionados?",
                "Se borrarán 5 envío(s).", "Sí, borrar"):
            self.assertIn(expected, text)

        text = page("participation_remove.html", SPANISH,
                    admin=SimpleNamespace(permission_all=False), **params)
        self.assertIn("No tienes permiso para quitar una participación.",
                      text)

    def test_a_form_error_in_spanish_and_in_english(self):
        # Scenario: Invalid field in Spanish, Invalid field in English
        for translation, subject, text in (
                (SPANISH, "Campos no válidos", "Selecciona un usuario válido"),
                (None, "Invalid field(s)", "Please select a valid user")):
            handler = run_post(AddContestUserHandler, {"user_id": "null"},
                               translation, args=("1",))

            self.assertEqual(
                notification(handler),
                (subject, repr(AssertionError(text))))

    def test_an_unknown_team_in_spanish_and_in_english(self):
        # Scenario: Invalid field in Spanish, Invalid field in English
        participation = MagicMock()
        participation.get_attrs.return_value = {}
        session = MagicMock()
        session.execute.return_value.scalars.return_value.first \
            .side_effect = lambda: next(rows)
        for translation, subject, text in (
                (SPANISH, "Campos no válidos",
                 "No existe un equipo con el código «zz»"),
                (None, "Invalid field(s)",
                 "Team with code 'zz' does not exist")):
            rows = iter([participation, None])
            handler = run_post(
                ParticipationHandler, {"group_id": "1"}, translation,
                args=("1", "7"), sql_session=session,
                get_password=MagicMock(), get_ip_networks=MagicMock(),
                get_datetime=MagicMock(), get_timedelta_sec=MagicMock(),
                get_bool=MagicMock(),
                get_string=lambda attrs, name: attrs.update({name: "zz"}))

            self.assertEqual(
                notification(handler),
                (subject, repr(ValueError(text))))


def activity_params(**fields):
    """What the activity pages read, with one interval and one overlap."""
    row = SimpleNamespace(
        id=1, user_id=7, username="ana", device_id=None, ip="10.0.0.5",
        started_at=datetime(2026, 10, 12, 10), started_by="login",
        last_seen_at=datetime(2026, 10, 12, 11), logged_out_at=None)
    overlap = SimpleNamespace(
        user_id=7, username="ana", first_device_id=None, first_ip="10.0.0.5",
        second_device_id=None, second_ip="10.0.0.6",
        overlap_start=datetime(2026, 10, 12, 10),
        overlap_end=datetime(2026, 10, 12, 11))
    params = {
        "contest": SimpleNamespace(id=1, name="d1"), "timezone": "UTC",
        "activity_simultaneous": [overlap],
        "activity_simultaneous_count": 3,
        "simultaneous_margin": timedelta(seconds=120),
        "multiple_devices": [SimpleNamespace(
            user_id=7, username="ana", devices=2)],
        "activity_filters": {"username": "", "ip": ""},
        "activity_intervals": [row], "activity_count": 1,
        "activity_end_reason": lambda logged_out, last_seen: "active",
        "activity_flagged_ids": {1}, "activity_csv_url": "/csv",
        "activity_page_url": lambda **kwargs: "/page", "activity_page": 0,
        "activity_pages": 1}
    params.update(fields)
    return params


class TestActivityInSpanish(unittest.TestCase):

    def test_the_activity_page_in_spanish(self):
        # Scenario: A page group in Spanish (activity)
        text = page("contest_activity.html", SPANISH, **activity_params(),
                    **macros(SPANISH, "activity", "pages"))

        for expected in (
                "Las horas están en la zona horaria del concurso (UTC).",
                "Actividad simultánea (3)", "Más de un dispositivo (1)",
                "Intervalos (1)", "Solo se muestran los 1 más recientes",
                "durante más de 120 segundos", "Dirección IP o red:",
                "Filtrar", "Primer dispositivo / IP", "Coincidencia desde",
                "Última actividad", "Iniciado por",
                "Sin dispositivo: un cliente de API"):
            self.assertIn(expected, text)
        for english in ("Times are in", "Simultaneous", "Filter", "Overlap",
                        "No device"):
            self.assertNotIn(english, text)

    def test_the_activity_values_in_spanish(self):
        # Scenario: A page group in Spanish (activity): the starter and
        # the end of an interval are translated, an unknown value is not.
        for ended, started, expected in (
                ("logout", "login", "cierre de sesión inicio de sesión"),
                ("inactivity", "resumed", "inactividad reanudado"),
                ("active", "other", "en curso other")):
            params = activity_params(
                activity_end_reason=lambda logged_out, last_seen: ended)
            params["activity_intervals"][0].started_by = started
            text = page("contest_activity.html", SPANISH, **params,
                        **macros(SPANISH, "activity", "pages"))

            self.assertIn(expected, text)

    def test_the_empty_activity_page_in_spanish(self):
        # Scenario: A page group in Spanish (activity)
        text = page("contest_activity.html", SPANISH, **activity_params(
            activity_simultaneous=[], activity_simultaneous_count=0,
            multiple_devices=[], activity_intervals=[], activity_count=0),
                    **macros(SPANISH, "activity", "pages"))

        self.assertIn("No hay actividad registrada.", text)
        self.assertIn("Ninguno.", text)
        self.assertNotIn("None.", text)


class TestRankingInSpanish(unittest.TestCase):

    def test_the_ranking_page_in_spanish(self):
        # Scenario: A page group in Spanish (ranking)
        text = page("ranking.html", SPANISH,
                    contest=SimpleNamespace(id=1, tasks=[], participations=[]),
                    show_teams=True)

        for expected in ("Descarga en csv", "texto", "Nombre de usuario",
                         "Usuario", "Equipo", "Total"):
            self.assertIn(expected, text)
        self.assertNotIn("Download as", text)


class TestParticipationPageInSpanish(unittest.TestCase):

    def test_the_participation_page_in_spanish(self):
        # Scenario: A page group in Spanish (participations); the page is
        # rendered with the stand-ins of aws_render, which fill the data.
        html = aws_render.render(aws_render.environment("full"),
                                 "participation.html", SPANISH)
        text, attributes = visible(html)
        tooltips = " | ".join(attributes)

        for expected in (
                "Participación de", "Información de la participación",
                "Participación oculta", "Participación sin restricciones",
                "Hora del primer inicio de sesión (en UTC)",
                "Las horas están en la zona horaria del concurso",
                "El registro completo del concurso está en",
                "Mensajes", "Asunto:", "Por:"):
            self.assertIn(expected, text)
        for expected in (
                "El grupo al que pertenece el concursante.",
                "Contraseña del concursante para este concurso. Si está "
                "vacía, se usa la contraseña principal del concursante.",
                "Lista separada por comas de las direcciones IP",
                "Hora del primer inicio de sesión en este concurso",
                "Enviar", "Actualizar", "Restablecer"):
            self.assertIn(expected, tooltips)
        for english in ("Participation of", "Messages", "Subject:"):
            self.assertNotIn(english, text)
