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

"""Tests for the AWS bulk removal pages: flow, permissions and template.

The handlers are built with __new__ and their helpers mocked, so these
tests need no database and no web server. The template is rendered from
its "core" block only, as the rest of base.html needs a whole request.

"""

import unittest
from types import SimpleNamespace
from unittest import mock

import tornado.web

from cms.server.admin.bulkremove import NOT_FOUND, RemovalEntry, \
    RemovalPlan
from cms.server.admin.handlers import HANDLERS
from cms.server.admin.handlers.bulkremove import NOTHING_CHOSEN, \
    PLAN_CHANGED, WRONG_COUNT, BulkRemoveParticipationsHandler, \
    BulkRemoveUsersHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT

MODULE = "cms.server.admin.handlers.bulkremove"
CONTEST_ID = 4


def entry(user_id, username, submissions=0, contests=("dia1",)):
    return RemovalEntry(user_id=user_id, username=username,
                        first_name="N" + username, last_name="A" + username,
                        submissions=submissions, user_tests=0,
                        contests=list(contests))


def make_plan(*entries, ignored=(), running=False):
    return RemovalPlan(users=list(entries), ignored=list(ignored),
                       running=running)


def fake_url(*parts):
    return "/" + "/".join(str(part) for part in parts)


def make_handler(handler_class=BulkRemoveUsersHandler, *, form=None,
                 user_ids=(), file=None, permission_all=True):
    """Build a handler without a request or a database.

    form: the single-valued arguments.
    user_ids: the values of the repeated user_id argument.
    file: the body of the uploaded file, or None for none.

    """
    form = dict(form or {})
    handler = handler_class.__new__(handler_class)
    handler.application = mock.MagicMock()
    handler.request = SimpleNamespace(
        files={} if file is None else {"file": [{"body": file}]})
    handler._current_user = SimpleNamespace(id=1,
                                            permission_all=permission_all)
    handler.contest = None
    handler.sql_session = mock.MagicMock()
    handler.safe_get_item = mock.MagicMock(
        return_value=SimpleNamespace(id=CONTEST_ID, name="dia1"))
    handler.get_argument = mock.MagicMock(
        side_effect=lambda name, default=None: form.get(name, default))
    handler.get_arguments = mock.MagicMock(
        side_effect=lambda name: list(user_ids) if name == "user_id" else [])
    handler.render_params = mock.MagicMock(side_effect=lambda: {
        "url": fake_url, "xsrf_form_html": "",
        "admin": SimpleNamespace(permission_all=True)})
    handler.render = mock.MagicMock()
    handler.redirect = mock.MagicMock()
    handler.url = mock.MagicMock(side_effect=fake_url)
    handler.schedule_rpc = mock.MagicMock()
    handler.try_commit = mock.MagicMock(return_value=True)
    return handler


def notifications(handler):
    return [call.args[1:] for call in
            handler.service.add_notification.call_args_list]


class TestPreview(unittest.TestCase):

    def test_checkboxes_render_the_plan(self):
        plan = make_plan(entry(7, "ana"))
        handler = make_handler(user_ids=["7"])
        with mock.patch(MODULE + ".plan_removal",
                        return_value=plan) as planner:
            handler._post_sync()

        planner.assert_called_once_with(
            handler.sql_session, contest_id=None, user_ids=[7],
            usernames=[])
        handler.render.assert_called_once()
        (template,), params = handler.render.call_args
        self.assertEqual(template, "users_bulk_remove.html")
        self.assertIs(params["plan"], plan)
        self.assertIsNone(params["error"])
        self.assertEqual(params["action_url"], "/users/remove")
        self.assertEqual(params["back_url"], "/users")

    def test_textarea_and_file_are_both_read(self):
        handler = make_handler(BulkRemoveParticipationsHandler,
                               form={"usernames": "ana\nbeto"},
                               file=b"username\nbeto\ncarla\n")
        with mock.patch(MODULE + ".plan_removal",
                        return_value=make_plan()) as planner:
            handler._post_sync(str(CONTEST_ID))

        planner.assert_called_once_with(
            handler.sql_session, contest_id=CONTEST_ID, user_ids=[],
            usernames=["ana", "beto", "carla"])
        params = handler.render.call_args.kwargs
        self.assertEqual(params["action_url"], "/contest/4/users/remove")
        self.assertEqual(params["back_url"], "/contest/4/users")

    def test_nothing_chosen_redirects_back(self):
        handler = make_handler(form={"usernames": "  \n "})
        with mock.patch(MODULE + ".plan_removal") as planner:
            handler._post_sync()

        planner.assert_not_called()
        handler.render.assert_not_called()
        handler.redirect.assert_called_once_with("/users")
        self.assertEqual(notifications(handler), [(NOTHING_CHOSEN, "")])

    def test_a_bad_list_redirects_back(self):
        handler = make_handler(file="josé".encode("latin-1"))
        with mock.patch(MODULE + ".plan_removal") as planner:
            handler._post_sync()

        planner.assert_not_called()
        handler.redirect.assert_called_once_with("/users")
        self.assertEqual(notifications(handler), [
            ("No se leyó la lista", "la lista no está en UTF-8")])

    def test_a_user_id_that_is_not_a_number_redirects_back(self):
        for action in ("preview", "confirm"):
            handler = make_handler(form={"action": action,
                                         "expected_count": "1"},
                                   user_ids=["7", "x"])
            with mock.patch(MODULE + ".plan_removal") as planner, \
                    mock.patch(MODULE + ".remove_users") as remover:
                handler._post_sync()

            planner.assert_not_called()
            remover.assert_not_called()
            handler.redirect.assert_called_once_with("/users")
            self.assertEqual(notifications(handler),
                             [("Invalid field(s)", "user_id")])


class TestConfirm(unittest.TestCase):

    def confirm(self, handler_class, plan, *, user_ids, expected_count,
                contest_id=None):
        form = {"action": "confirm", "expected_count": expected_count}
        handler = make_handler(handler_class, form=form, user_ids=user_ids)
        with mock.patch(MODULE + ".plan_removal", return_value=plan), \
                mock.patch(MODULE + ".remove_users",
                           return_value=len(plan.users)) as users, \
                mock.patch(MODULE + ".remove_participations",
                           return_value=len(plan.users)) as participations:
            if contest_id is None:
                handler._post_sync()
            else:
                handler._post_sync(str(contest_id))
        return handler, users, participations

    def test_platform_removal(self):
        plan = make_plan(entry(7, "ana"), entry(9, "beto"))
        handler, users, participations = self.confirm(
            BulkRemoveUsersHandler, plan, user_ids=["9", "7"],
            expected_count="2")

        users.assert_called_once_with(handler.sql_session, [7, 9])
        participations.assert_not_called()
        handler.try_commit.assert_called_once_with()
        handler.schedule_rpc.assert_called_once_with(
            handler.service.proxy_service.reinitialize)
        self.assertIn(("Se borraron 2 usuarios", ""), notifications(handler))
        handler.redirect.assert_called_once_with("/users")

    def test_contest_removal(self):
        plan = make_plan(entry(7, "ana"))
        handler, users, participations = self.confirm(
            BulkRemoveParticipationsHandler, plan, user_ids=["7"],
            expected_count="1", contest_id=CONTEST_ID)

        participations.assert_called_once_with(
            handler.sql_session, CONTEST_ID, [7])
        users.assert_not_called()
        handler.schedule_rpc.assert_called_once()
        self.assertIn(("Se quitaron 1 participaciones", ""),
                      notifications(handler))
        handler.redirect.assert_called_once_with("/contest/4/users")

    def test_a_wrong_count_removes_nothing(self):
        for typed in ("3", "", "dos"):
            plan = make_plan(entry(7, "ana"), entry(9, "beto"))
            handler, users, _ = self.confirm(
                BulkRemoveUsersHandler, plan, user_ids=["7", "9"],
                expected_count=typed)

            users.assert_not_called()
            handler.schedule_rpc.assert_not_called()
            params = handler.render.call_args.kwargs
            self.assertEqual(params["error"], WRONG_COUNT)
            self.assertIs(params["plan"], plan)

    def test_a_changed_plan_removes_nothing(self):
        # Beto was removed by someone else since the preview.
        plan = make_plan(entry(7, "ana"), ignored=[("9", NOT_FOUND)])
        handler, users, _ = self.confirm(
            BulkRemoveUsersHandler, plan, user_ids=["7", "9"],
            expected_count="2")

        users.assert_not_called()
        handler.schedule_rpc.assert_not_called()
        params = handler.render.call_args.kwargs
        self.assertEqual(params["error"], PLAN_CHANGED)
        self.assertIs(params["plan"], plan)

    def test_a_failed_commit_schedules_no_rpc(self):
        form = {"action": "confirm", "expected_count": "1"}
        handler = make_handler(form=form, user_ids=["7"])
        handler.try_commit.return_value = False
        with mock.patch(MODULE + ".plan_removal",
                        return_value=make_plan(entry(7, "ana"))), \
                mock.patch(MODULE + ".remove_users", return_value=1):
            handler._post_sync()

        handler.schedule_rpc.assert_not_called()
        handler.redirect.assert_called_once_with("/users")

    def test_a_failed_removal_rolls_back(self):
        form = {"action": "confirm", "expected_count": "1"}
        handler = make_handler(form=form, user_ids=["7"])
        with mock.patch(MODULE + ".plan_removal",
                        return_value=make_plan(entry(7, "ana"))), \
                mock.patch(MODULE + ".remove_users",
                           side_effect=RuntimeError("boom")):
            handler._post_sync()

        handler.sql_session.rollback.assert_called_once_with()
        handler.try_commit.assert_not_called()
        handler.schedule_rpc.assert_not_called()
        self.assertEqual(notifications(handler), [
            ("No se borró nada", repr(RuntimeError("boom")))])

    def test_confirm_without_users_redirects_back(self):
        form = {"action": "confirm", "expected_count": "0"}
        handler = make_handler(form=form)
        with mock.patch(MODULE + ".plan_removal") as planner:
            handler._post_sync()

        planner.assert_not_called()
        handler.redirect.assert_called_once_with("/users")
        self.assertEqual(notifications(handler), [(NOTHING_CHOSEN, "")])


class TestPermissionsAndRoutes(unittest.TestCase):

    def test_both_need_all_permissions(self):
        for handler_class, args in (
                (BulkRemoveUsersHandler, ()),
                (BulkRemoveParticipationsHandler, (str(CONTEST_ID),))):
            handler = make_handler(handler_class, permission_all=False)
            handler._post_sync = mock.MagicMock()
            with self.assertRaises(tornado.web.HTTPError) as caught:
                handler.post(*args)
            self.assertEqual(caught.exception.status_code, 403)
            handler._post_sync.assert_not_called()

    def route_of(self, handler_class):
        (pattern,) = [route[0] for route in HANDLERS
                      if route[1] is handler_class]
        return pattern

    def test_routes(self):
        self.assertRegex("/users/remove",
                         "^%s$" % self.route_of(BulkRemoveUsersHandler))
        pattern = self.route_of(BulkRemoveParticipationsHandler)
        self.assertRegex("/contest/12/users/remove", "^%s$" % pattern)
        self.assertNotRegex("/contest/x/users/remove", "^%s$" % pattern)


class TestConfirmationPage(unittest.TestCase):

    def render(self, plan, *, contest=None, error=None):
        template = AWS_ENVIRONMENT.get_template("users_bulk_remove.html")
        params = {"url": fake_url, "xsrf_form_html": "",
                  "admin": SimpleNamespace(permission_all=True),
                  "contest": contest, "plan": plan, "error": error,
                  "action_url": "/users/remove", "back_url": "/users"}
        return "".join(template.blocks["core"](template.new_context(params)))

    def test_platform_page(self):
        plan = make_plan(entry(7, "ana", submissions=3,
                               contests=("dia1", "dia2")),
                         entry(9, "beto"),
                         ignored=[("nadie", NOT_FOUND)])
        html = self.render(plan)

        self.assertIn("ana", html)
        self.assertIn("dia1, dia2", html)
        self.assertIn("Se borrarán 2 usuarios, 3 envíos y 0 user tests.",
                      html)
        self.assertIn("nadie", html)
        self.assertIn(NOT_FOUND, html)
        self.assertIn('name="user_id" value="7"', html)
        self.assertIn('name="user_id" value="9"', html)
        self.assertIn('name="action" value="confirm"', html)
        self.assertIn('name="expected_count"', html)
        self.assertIn("Escribe 2 para confirmar", html)
        self.assertNotIn("bulk_remove_running", html)

    def test_contest_page_and_running_warning(self):
        contest = SimpleNamespace(id=CONTEST_ID, name="dia1")
        html = self.render(make_plan(entry(7, "ana"), running=True),
                           contest=contest)

        self.assertIn("Se quitarán 1 participaciones de este concurso", html)
        self.assertIn("bulk_remove_running", html)

    def test_error_line(self):
        html = self.render(make_plan(entry(7, "ana")), error=WRONG_COUNT)

        self.assertIn(WRONG_COUNT, html)

    def test_nothing_to_remove_has_no_button(self):
        html = self.render(make_plan(ignored=[("nadie", NOT_FOUND)]))

        self.assertIn("No hay nada que borrar.", html)
        self.assertNotIn('name="expected_count"', html)
