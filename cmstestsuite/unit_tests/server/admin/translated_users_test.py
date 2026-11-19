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

"""Tests for the AWS pages of the users, the teams and the groups in Spanish.

The pages are rendered from the template with the Spanish translation
built from the committed catalog and fragments. Nothing here says how a
page looks.

"""

import json
import re
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from cms.server.admin.handlers.user import AddTeamHandler, \
    EditParticipationHandler, GroupListHandler, TeamListHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH
from cmstestsuite.unit_tests.server.admin.users_pages_test import render, \
    visible


def user(**fields):
    params = {"id": 7, "first_name": "Ana", "last_name": "Pérez",
              "username": "ana", "email": None, "timezone": None,
              "preferred_languages": [], "password": "plaintext:x"}
    params.update(fields)
    return SimpleNamespace(**params)


def page(name: str, translation=None, **params) -> str:
    """The text of the core block of the page, with the attributes."""
    text, attributes = visible(render(AWS_ENVIRONMENT, name, translation,
                                      **params))
    return " | ".join([text] + attributes)


def run_post(handler_class, form: dict, translation=None, **attributes):
    """Post the form to the handler, without a database, and return the
    handler (its service holds the notifications)."""
    handler = handler_class.__new__(handler_class)
    if translation is not None:
        handler._ = translation.gettext
        handler.n_ = translation.ngettext

    def get_argument(name, *default):
        if name in form:
            return form[name]
        if default:
            return default[0]
        raise KeyError(name)

    handler.application = MagicMock()
    handler.sql_session = MagicMock()
    handler.get_argument = get_argument
    handler.redirect = MagicMock()
    handler.url = lambda *parts: "/" + "/".join(str(p) for p in parts)
    handler.try_commit = MagicMock(return_value=True)
    handler.schedule_rpc = MagicMock()
    handler.safe_get_item = MagicMock()
    for name, value in attributes.items():
        setattr(handler, name, value)
    handler._post_sync(*attributes.get("args", ()))
    return handler


def notification(handler) -> tuple[str, str]:
    (_when, subject, text), _kwargs = \
        handler.service.add_notification.call_args
    return subject, text


def operation_buttons(html: str) -> list[tuple[str, str]]:
    """The (value posted, label shown) of the buttons that send an
    operation."""
    return re.findall(
        r'<button[^>]*name="operation"[^>]*value="([^"]*)"[^>]*>([^<]*)'
        r"</button>", html)


class TestUsersPagesInSpanish(unittest.TestCase):

    def test_the_user_page_in_spanish(self):
        # Scenario: A page group in Spanish (users)
        participation = SimpleNamespace(
            contest=SimpleNamespace(id=2, name="d1", description="Día 1"),
            user=user(), hidden=False, unrestricted=True,
            group=SimpleNamespace(name="main"))
        text = page("user.html", SPANISH, user=user(),
                    participations=[participation],
                    unassigned_contests=[])

        for expected in ("Participaciones", "Editar la participación "
                         "seleccionada:", "Descripción del concurso",
                         "Información general", "Idiomas preferidos",
                         "Zona horaria del concursante"):
            self.assertIn(expected, text)
        # hidden=False, unrestricted=True
        self.assertIn("No Sí", text)
        for english in ("True", "False", "Participations",
                        "General Information", "Preferred languages"):
            self.assertNotIn(english, text)

    def test_the_new_user_page_in_spanish(self):
        # Scenario: A page group in Spanish (users)
        text = page("add_user.html", SPANISH)

        for expected in ("Nuevo usuario", "Correo electrónico",
                         "Texto plano", "Con hash (bcrypt)", "Enviar",
                         "Restablecer"):
            self.assertIn(expected, text)
        self.assertNotIn("New user", text)

    def test_the_user_removal_page_in_spanish(self):
        # Scenario: A page group in Spanish (users)
        text = page("user_remove.html", SPANISH, user=user(),
                    participation_count=2, submission_count=5,
                    admin=SimpleNamespace(permission_all=True))

        for expected in (
                "Borrar usuario",
                "¿Seguro que quieres borrar el usuario ana, incluidos "
                "todos sus datos relacionados?",
                "Esto borrará 2 participación(es) y 5 envío(s).",
                "Sí, borrar"):
            self.assertIn(expected, text)

    def test_the_users_list_headers_in_spanish(self):
        # Scenario: A page group in Spanish (users)
        text = page("users.html", SPANISH, user_list=[user()])

        for expected in ("Lista de usuarios", "Usuario", "Nombre",
                         "Apellido"):
            self.assertIn(expected, text)
        self.assertNotIn("Users list", text)

    def test_the_hashed_password_placeholder_in_spanish(self):
        # Scenario: A page group in Spanish (the script text of the form)
        html = render(AWS_ENVIRONMENT, "user.html", SPANISH,
                      user=user(password="bcrypt:$2b$x"), participations=[],
                      unassigned_contests=[])

        # tojson writes the literal of the script with ASCII escapes.
        self.assertIn(
            json.dumps("La contraseña está guardada como hash; escribe una "
                       "nueva aquí para cambiarla"), html)
        self.assertNotIn("The password is hashed", html)

    def test_the_teams_pages_in_spanish(self):
        # Scenario: A page group in Spanish (teams)
        team = SimpleNamespace(id=3, code="mx", name="Equipo <1>")
        admin = SimpleNamespace(permission_all=True)

        text = page("teams.html", SPANISH, team_list=[team], admin=admin)
        for expected in ("Lista de equipos", "Editar el equipo seleccionado:",
                         "Código", "Nombre", "Quitar"):
            self.assertIn(expected, text)
        self.assertNotIn("Teams list", text)

        text = page("team.html", SPANISH, team=team, admin=admin)
        for expected in ("Información general", "Actualizar", "Restablecer"):
            self.assertIn(expected, text)

        text = page("add_team.html", SPANISH)
        self.assertIn("Nuevo equipo", text)
        self.assertNotIn("New team", text)

    def test_the_team_removal_page_in_spanish(self):
        # Scenario: A page group in Spanish (teams)
        params = {"team": SimpleNamespace(id=3, code="mx", name="Equipo"),
                  "admin": SimpleNamespace(permission_all=True)}

        text = page("team_remove.html", SPANISH, participation_count=4,
                    **params)
        for expected in (
                "Borrar equipo",
                "¿Seguro que quieres borrar el equipo mx (Equipo)?",
                "Esto quitará la asociación con el equipo de 4 "
                "participación(es)."):
            self.assertIn(expected, text)

        text = page("team_remove.html", SPANISH, participation_count=0,
                    **params)
        self.assertIn("Este equipo no tiene participaciones asociadas.",
                      text)

    def test_the_groups_pages_in_spanish(self):
        # Scenario: A page group in Spanish (groups)
        contest = SimpleNamespace(id=1, name="d1", main_group_id=5)
        group = SimpleNamespace(
            id=5, name="main", contest=contest, per_user_time=None,
            start=datetime(2026, 1, 1), stop=datetime(2026, 1, 2),
            analysis_enabled=False, analysis_start=datetime(2026, 1, 3),
            analysis_stop=datetime(2026, 1, 4), participations=[])
        contest.groups = [group]
        admin = SimpleNamespace(permission_all=True)

        text = page("groups.html", SPANISH, contest=contest, admin=admin)
        for expected in ("Lista de grupos", "Agrega un grupo nuevo llamado",
                         "Editar el grupo seleccionado:", "Inicio (en UTC)",
                         "Inicio de la fase de análisis", "(grupo principal)"):
            self.assertIn(expected, text)
        self.assertNotIn("Groups list", text)

        text = page("group.html", SPANISH, contest=contest, group=group,
                    admin=admin)
        for expected in ("Grupo main en", "No se encontraron usuarios.",
                         "Información del grupo",
                         "Nombre de este grupo de usuarios."):
            self.assertIn(expected, text)

    def test_the_operation_buttons_post_english_values(self):
        # Scenario: No translated posted values (the label is Spanish, the
        # value that the handler reads is what it was)
        admin = SimpleNamespace(permission_all=True)
        contest = SimpleNamespace(id=1, name="d1", main_group_id=5,
                                  groups=[])
        pages = {
            "groups.html": ({"contest": contest},
                            [("Make main group", "Hacer grupo principal"),
                             ("Delete selected group",
                              "Borrar el grupo seleccionado")]),
            "teams.html": ({"team_list": []}, [("Remove", "Quitar")]),
            "team.html": ({"team": SimpleNamespace(id=1, code="a", name="b")},
                          [("Remove", "Quitar")]),
            "user.html": ({"user": user(), "unassigned_contests": [],
                           "participations": [SimpleNamespace(
                               contest=SimpleNamespace(
                                   id=2, name="d1", description=""),
                               user=user(), hidden=False,
                               unrestricted=False,
                               group=SimpleNamespace(name="g"))]},
                          [("Remove", "Quitar")]),
        }
        for name, (params, expected) in pages.items():
            html = render(AWS_ENVIRONMENT, name, SPANISH, admin=admin,
                          **params)
            self.assertEqual(operation_buttons(html), expected, msg=name)

    def test_a_form_error_in_spanish(self):
        # Scenario: Invalid field in Spanish (the team has no code)
        handler = run_post(AddTeamHandler, {}, SPANISH)

        self.assertEqual(
            notification(handler),
            ("Campos no válidos",
             repr(AssertionError("No se indicó el código del equipo."))))

    def test_a_form_error_in_english(self):
        # Scenario: Invalid field in English (the same form, unchanged)
        handler = run_post(AddTeamHandler, {})

        self.assertEqual(
            notification(handler),
            ("Invalid field(s)",
             repr(AssertionError("No team code specified."))))

    def test_the_spanish_page_posts_the_remove_operation(self):
        # Scenario: An operation posted from a Spanish page (the teams page
        # and the user page post "Remove", which the handlers accept)
        team_page = render(AWS_ENVIRONMENT, "teams.html", SPANISH,
                           team_list=[], admin=SimpleNamespace(
                               permission_all=True))
        (value, label), = operation_buttons(team_page)
        self.assertEqual(label, "Quitar")

        handler = run_post(TeamListHandler,
                           {"team_id": "3", "operation": value}, SPANISH)
        handler.redirect.assert_called_once_with("/teams/3/remove")

        user_page = render(
            AWS_ENVIRONMENT, "user.html", SPANISH, user=user(),
            unassigned_contests=[], admin=SimpleNamespace(
                permission_all=True), participations=[SimpleNamespace(
                    contest=SimpleNamespace(id=2, name="d1", description=""),
                    user=user(), hidden=False, unrestricted=False,
                    group=SimpleNamespace(name="g"))])
        (value, label), = operation_buttons(user_page)
        self.assertEqual(label, "Quitar")

        handler = run_post(
            EditParticipationHandler,
            {"contest_id": "2", "operation": value}, SPANISH, args=(7,))
        handler.service.add_notification.assert_not_called()
        handler.sql_session.delete.assert_called_once()

    def test_the_spanish_page_posts_the_group_operations(self):
        # Scenario: An operation posted from a Spanish page (the groups
        # page)
        page_html = render(
            AWS_ENVIRONMENT, "groups.html", SPANISH,
            contest=SimpleNamespace(id=1, name="d1", groups=[]),
            admin=SimpleNamespace(permission_all=True))
        values = dict(operation_buttons(page_html))
        contest = SimpleNamespace(id=1, name="d1", main_group_id=None)
        group = SimpleNamespace(id=5, name="omi", participations=[1, 2])
        items = {"Contest": contest, "Group": group}

        def run(operation, **more):
            return run_post(
                GroupListHandler,
                {"group_id": "5", "operation": operation}, SPANISH,
                args=(1,),
                safe_get_item=lambda cls, _id: items[cls.__name__], **more)

        handler = run(next(v for v in values if v.startswith("Make")))
        self.assertEqual(contest.main_group_id, 5)
        handler.service.add_notification.assert_not_called()

        # A group with users is not deleted (the notification agrees in
        # number in Spanish, and is what it was in English).
        handler = run(next(v for v in values if v.startswith("Delete")))
        self.assertEqual(
            notification(handler),
            ("No se puede borrar un grupo que contiene usuarios",
             "El grupo «omi» contiene 2 usuarios"))
        group.participations = [1]
        handler = run(next(v for v in values if v.startswith("Delete")))
        self.assertEqual(notification(handler)[1],
                         "El grupo «omi» contiene 1 usuario")

    def test_a_group_with_users_in_english(self):
        # Scenario: Invalid field in English (the notification of the
        # group that cannot be deleted is what it was)
        contest = SimpleNamespace(id=1, name="d1", main_group_id=None)
        group = SimpleNamespace(id=5, name="omi", participations=[1])
        items = {"Contest": contest, "Group": group}
        handler = run_post(
            GroupListHandler,
            {"group_id": "5", "operation": "Delete selected group"},
            args=(1,), safe_get_item=lambda cls, _id: items[cls.__name__])

        self.assertEqual(
            notification(handler),
            ("Cannot delete group containing users",
             "The group `omi' contains 1 users"))


if __name__ == "__main__":
    unittest.main()
