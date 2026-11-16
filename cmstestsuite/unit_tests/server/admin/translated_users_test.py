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
import unittest
from types import SimpleNamespace

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
        for english in ("Participations", "General Information",
                        "Preferred languages"):
            self.assertNotIn(english, text)

    def test_the_new_user_page_in_spanish(self):
        # Scenario: A page group in Spanish (users)
        text = page("add_user.html", SPANISH)

        for expected in ("Nuevo usuario", "Correo electrónico",
                         "Texto plano", "Cifrada (bcrypt)", "Limpiar"):
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
                "Sí, quitar"):
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
            json.dumps("La contraseña está cifrada, escribe aquí una nueva "
                       "para cambiarla"), html)
        self.assertNotIn("The password is hashed", html)


if __name__ == "__main__":
    unittest.main()
