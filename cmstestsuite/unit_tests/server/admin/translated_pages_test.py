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

"""Tests for the AWS pages that are translated: login, error, switches.

The Spanish translation is built from the committed aws.po, so no
compiled .mo file is needed. Nothing here says how a page looks.

"""

import re
import unittest
from unittest.mock import MagicMock

from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH
from cmstestsuite.unit_tests.server.admin.lang_switch_test import \
    error_page, login_page


def spanish(page, **params) -> str:
    """Render the page as AWS does when Spanish is chosen."""
    return page(translation=SPANISH, gettext=SPANISH.gettext,
                ngettext=SPANISH.ngettext, **params)


def theme_labels(html: str) -> list[str]:
    select = re.search(r'<select id="theme".*?</select>', html, re.S)
    return re.findall(r">([^<]+)</option>", select.group(0))


class TestTranslatedPages(unittest.TestCase):

    def test_the_login_page_in_spanish(self):
        # Scenario: The login page in Spanish
        html = spanish(login_page)

        for text in ("Te damos la bienvenida", "Inicia sesión:",
                     "Nombre de usuario",
                     "Contraseña", "Iniciar sesión", "Restablecer",
                     "<title>Admin</title>",
                     'title="Tema"', 'title="Lenguaje"',
                     "Automático (English)"):
            self.assertIn(text, html)
        for text in ("Welcome", "Please log in", "Username", "Password",
                     "Reset", 'title="Theme"', 'title="Language"',
                     ">Automatic"):
            self.assertNotIn(text, html)

    def test_the_login_error_in_spanish(self):
        # Scenario: The login page in Spanish (the failed login message)
        handler = MagicMock()
        handler.get_argument.side_effect = \
            lambda name, default=None: "x" if name == "login_error" else ""

        html = spanish(login_page, handler=handler)

        self.assertIn("No se pudo iniciar sesión.", html)

    def test_the_error_page_in_spanish(self):
        # Scenario: The error page in Spanish
        html = spanish(error_page)

        self.assertIn("<h1>Error 500</h1>", html)
        self.assertIn(
            "Ocurrió un error mientras el servidor procesaba tu solicitud.",
            html)
        self.assertNotIn("An error occured", html)

    def test_the_aws_switch_follows_the_page_language(self):
        # Scenario: The AWS switch follows the page language
        for page in (login_page, error_page):
            self.assertEqual(theme_labels(page()),
                             ["System", "Light", "Dark"])
            self.assertEqual(theme_labels(spanish(page)),
                             ["Sistema", "Claro", "Oscuro"])


if __name__ == "__main__":
    unittest.main()
