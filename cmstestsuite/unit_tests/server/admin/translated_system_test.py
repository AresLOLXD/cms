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

"""Tests for the system pages of AWS in Spanish (lane L7 of
aws-language-rest): the admins pages and their handlers, the ranking
group removal page, the overview and the resources pages.

The pages are rendered from the real templates with the Spanish of
aws.po plus the fragments; nothing here says how a page looks.

"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from cms.server.admin.handlers.admin import AddAdminHandler, AdminHandler
from cms.server.admin.handlers.rankinggroup import RankingGroupListHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH


class English:
    """The identity translation, as AWS uses it for English."""
    gettext = staticmethod(lambda message: message)

    @staticmethod
    def ngettext(singular, plural, n):
        return singular if n == 1 else plural


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


ADMIN = SimpleNamespace(id=4, username="ana", name="Ana", enabled=True,
                        permission_all=False, permission_messaging=True)


class TestAdminPages(unittest.TestCase):

    def test_the_admins_list_in_spanish(self):
        # Scenario "A page group in Spanish" (system and admins)
        html = render_core("admins.html", SPANISH, admins=[ADMIN])

        for text in ("Lista de admins", "<th>Activado</th>",
                     "<th>Usuario</th>", "<th>Todos los permisos</th>",
                     "<th>Permisos de mensajería</th>",
                     "Agregar un admin nuevo",
                     "Los admins desactivados no pueden iniciar sesión.",
                     "Los permisos de «Mensajería» permiten"):
            self.assertIn(text, html)
        self.assertNotIn("Admins list", html)

    def test_the_admin_forms_in_spanish(self):
        # Scenario "A page group in Spanish" (system and admins)
        new = render_core("add_admin.html", SPANISH)
        edit = render_core("admin.html", SPANISH, admin_being_edited=ADMIN)

        for text in ("Nuevo admin", "Información del admin",
                     'value="Crear"', 'value="Limpiar"', "<td>Contraseña</td>",
                     "<td>Activado</td>", "<td>Permisos de mensajería</td>"):
            self.assertIn(text, new)
        for text in ('value="Actualizar"', "Borrar este admin",
                     "Si dejas vacío el campo de contraseña, se conserva "
                     "la contraseña anterior."):
            self.assertIn(text, edit)
        self.assertNotIn("Create", new)

    def test_the_admins_pages_in_english_are_unchanged(self):
        # Scenario "A page in English is unchanged"
        html = render_core("admin.html", admin_being_edited=ADMIN)

        for text in ('value="Update"', 'value="Reset"', "Delete this admin",
                     "Leaving the password field empty will keep the "
                     "previous password."):
            self.assertIn(text, html)


class TestAdminHandlerNotifications(unittest.TestCase):

    def test_an_admin_without_name_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        for translation, subject, error in (
                (SPANISH, "Campos no válidos",
                 "AssertionError('No se indicó el nombre del admin.')"),
                (English, "Invalid field(s)",
                 "AssertionError('No admin name specified.')")):
            handler = make_handler(AddAdminHandler, translation,
                                   username="ana")

            handler._post_sync()

            self.assertEqual(notifications(handler), [(subject, error)])

    def test_an_admin_without_user_or_password_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        for translation, user, password in (
                (SPANISH, "No se indicó el usuario.",
                 "No se permite una contraseña vacía."),
                (English, "No username specified.",
                 "Empty password not permitted.")):
            handler = make_handler(AddAdminHandler, translation)
            handler._post_sync()
            self.assertEqual(notifications(handler)[0][1],
                             f"AssertionError({user!r})")

            handler = make_handler(AddAdminHandler, translation,
                                   username="ana", name="Ana", password="")
            handler._post_sync()
            self.assertEqual(notifications(handler)[0][1],
                             f"AssertionError({password!r})")

    def test_the_edit_handler_notifies_in_spanish(self):
        # Scenario "Invalid field in Spanish"
        handler = make_handler(AdminHandler, SPANISH, name="Ana")
        handler.safe_get_item = MagicMock()

        handler._post_sync("4")

        self.assertEqual(notifications(handler), [
            ("Campos no válidos",
             "AssertionError('No se indicó el usuario.')")])


class TestRankingGroupRemovePage(unittest.TestCase):

    GROUP = SimpleNamespace(id=2, name="olim", description="OLIM",
                           staff_password=None)

    def page(self, translation=None, count=3, **extra):
        return render_core("ranking_group_remove.html", translation,
                           ranking_group=self.GROUP, contest_count=count,
                           **extra)

    def test_the_remove_page_in_spanish(self):
        # Scenario "A page group in Spanish" (system and admins)
        html = self.page(SPANISH)

        for text in ("Borrar grupo de ranking",
                     "¿Seguro que quieres borrar el grupo de ranking olim "
                     "(OLIM)?",
                     "3 concurso(s) dejarán de enviarse a cualquier ranking.",
                     "Su marcador se vaciará. Esta operación no se puede "
                     "deshacer.", "Sí, quitar", ">No</a>"):
            self.assertIn(text, html)
        self.assertIn("Ningún concurso se envía a este ranking.",
                      self.page(SPANISH, count=0))
        self.assertIn("No tienes permiso para borrar un grupo de ranking.",
                      self.page(SPANISH, admin=SimpleNamespace(
                          permission_all=False)))
        self.assertNotIn("Are you sure", html)

    def test_the_remove_page_in_english_is_unchanged(self):
        # Scenario "A page in English is unchanged"
        html = self.page()

        for text in ("Remove ranking group",
                     "Are you sure you want to remove ranking group olim "
                     "(OLIM)?",
                     "3 contest(s) will no longer be sent to any ranking. "
                     "The contests themselves are not removed.",
                     "Its scoreboard will be emptied. This operation cannot "
                     "be undone."):
            self.assertIn(text, " ".join(html.split()))

    def test_the_remove_buttons_post_the_english_value(self):
        # Scenario "No translated posted values": the list and the group
        # page post what RankingGroupListHandler compares with.
        self.assertEqual(RankingGroupListHandler.REMOVE, "Remove")
        list_page = render_core(
            "ranking_groups.html", SPANISH, ranking_group_list=[self.GROUP],
            contest_list=[], views={2: SimpleNamespace(
                hidden_now=False, summary="visible", next_change=None)})

        self.assertIn('value="Remove"', list_page)
        self.assertIn(">Borrar grupo</button>", list_page)


class TestMachineList(unittest.TestCase):

    def test_the_machine_list_in_spanish(self):
        # Scenario "A page group in Spanish" (system and admins)
        html = render_core("resourceslist.html", SPANISH, contest=None,
                           resource_addresses={0: "localhost"})

        for text in ("Lista de máquinas",
                     "Consulta el uso de recursos de las máquinas:",
                     ">localhost</a> (0)", ">Todas</a>"):
            self.assertIn(text, html)
        self.assertNotIn("Machine list", html)


if __name__ == "__main__":
    unittest.main()
