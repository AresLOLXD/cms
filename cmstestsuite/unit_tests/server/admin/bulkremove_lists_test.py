#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <www.luisrodolfo@gmail.com>
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

"""Tests for the checkboxes and "por lista" forms of the AWS user lists.

The templates are rendered from their "core" block only.

"""

import unittest
from types import SimpleNamespace

from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT


def fake_url(*parts):
    return "/" + "/".join(str(part) for part in parts)


def user(user_id, username):
    return SimpleNamespace(id=user_id, username=username,
                           first_name="N", last_name="A")


class ListPageTestCase(unittest.TestCase):

    def render(self, name, permission_all=True, **params):
        template = AWS_ENVIRONMENT.get_template(name)
        params.update({"url": fake_url, "xsrf_form_html": "",
                       "admin": SimpleNamespace(
                           permission_all=permission_all)})
        return "".join(template.blocks["core"](template.new_context(params)))

    def assert_bulk_forms(self, html, action, button):
        self.assertNotIn('type="radio"', html)
        self.assertIn('action="%s" method="POST"' % action, html)
        self.assertIn('name="action" value="preview"', html)
        self.assertIn('id="select_all_users"', html)
        self.assertIn('value="%s"' % button, html)
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertIn('<textarea name="usernames"', html)
        self.assertIn('type="file" name="file"', html)
        self.assertIn('value="Revisar lista"', html)


class TestUsersPage(ListPageTestCase):

    def test_checkboxes_and_list_form(self):
        html = self.render("users.html",
                           user_list=[user(7, "ana"), user(9, "beto")])

        self.assert_bulk_forms(html, "/users/remove", "Borrar seleccionados")
        self.assertIn('type="checkbox" name="user_id" value="7"', html)
        self.assertIn('type="checkbox" name="user_id" value="9"', html)

    def test_buttons_disabled_without_all_permissions(self):
        html = self.render("users.html", permission_all=False,
                           user_list=[user(7, "ana")])

        self.assertEqual(html.count("disabled"), 2)


class TestContestUsersPage(ListPageTestCase):

    def contest(self):
        group = SimpleNamespace(id=1, name="main")
        participation = SimpleNamespace(user=user(7, "ana"), group=group)
        return SimpleNamespace(id=4, name="dia1", groups=[group],
                               main_group_id=1,
                               participations=[participation])

    def test_checkboxes_and_list_form(self):
        # The overload warning fragment reads the CWS ports.
        config = SimpleNamespace(
            contest_web_server=SimpleNamespace(listen_port=[8888]))
        html = self.render("contest_users.html", contest=self.contest(),
                           unassigned_users=[], config=config)

        self.assert_bulk_forms(html, "/contest/4/users/remove",
                               "Quitar seleccionados del concurso")
        self.assertIn('type="checkbox" name="user_id" value="7"', html)
        # The rest of the page is still there.
        self.assertIn('value="Add user"', html)
        self.assertIn("Importar CSV", html)
