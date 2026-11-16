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

import re
import unittest
from types import SimpleNamespace
from unittest import mock

import tornado.web

from cms.locale import DEFAULT_TRANSLATION
from cms.server.admin.bulkremove import MAX_PLAN_USERS, NOT_FOUND, \
    NOT_IN_CONTEST, RemovalEntry, RemovalPlan
from cms.server.admin.handlers import HANDLERS
from cms.server.admin.handlers.bulkremove import NOTHING_CHOSEN, \
    PLAN_CHANGED, PLAN_TOO_LARGE, PLAN_TOO_LARGE_TEXT, WRONG_COUNT, \
    BulkRemoveParticipationsHandler, BulkRemoveUsersHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cms.server.admin.server import MAX_FORM_FIELDS
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH

MODULE = "cms.server.admin.handlers.bulkremove"
CONTEST_ID = 4


def entry(user_id, username, submissions=0, contests=("dia1",)):
    return RemovalEntry(user_id=user_id, username=username,
                        first_name="N" + username, last_name="A" + username,
                        submissions=submissions, user_tests=0,
                        contests=list(contests))


def make_plan(*entries, ignored=(), running=False, groups=()):
    return RemovalPlan(users=list(entries), ignored=list(ignored),
                       running=running, groups=list(groups))


def confirm_form(plan):
    """The form of the confirmation page of plan, filled in right."""
    return {"action": "confirm", "expected_count": str(len(plan.users)),
            "fingerprint": plan.fingerprint}


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
    handler._current_user = SimpleNamespace(id=1, name="root",
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
            ("The list could not be read", "the list is not in UTF-8")])

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

    def preview_of_users(self, handler_class, count, *args):
        """Preview a plan with count users and return the handler."""
        plan = make_plan(*(entry(i, "user%d" % i) for i in range(count)))
        handler = make_handler(handler_class, user_ids=["7"])
        with mock.patch(MODULE + ".plan_removal", return_value=plan):
            handler._post_sync(*args)
        return handler

    def test_a_plan_at_the_cap_is_rendered(self):
        for handler_class, args in (
                (BulkRemoveUsersHandler, ()),
                (BulkRemoveParticipationsHandler, (str(CONTEST_ID),))):
            handler = self.preview_of_users(
                handler_class, MAX_PLAN_USERS, *args)

            handler.render.assert_called_once()
            handler.redirect.assert_not_called()

    def test_a_plan_over_the_cap_redirects_back_with_a_clear_error(self):
        for handler_class, args, back in (
                (BulkRemoveUsersHandler, (), "/users"),
                (BulkRemoveParticipationsHandler, (str(CONTEST_ID),),
                 "/contest/4/users")):
            handler = self.preview_of_users(
                handler_class, MAX_PLAN_USERS + 1, *args)

            handler.render.assert_not_called()
            handler.redirect.assert_called_once_with(back)
            self.assertEqual(notifications(handler), [(
                PLAN_TOO_LARGE,
                PLAN_TOO_LARGE_TEXT % (MAX_PLAN_USERS + 1, MAX_PLAN_USERS))])


class TestConfirm(unittest.TestCase):

    def confirm(self, handler_class, plan, *, user_ids, expected_count,
                contest_id=None, fingerprint=None):
        """Confirm plan, whose preview had fingerprint (default: its own)."""
        form = dict(confirm_form(plan), expected_count=expected_count)
        if fingerprint is not None:
            form["fingerprint"] = fingerprint
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
        plan = make_plan(entry(7, "ana"), entry(9, "beto"),
                         groups=[None, "omi"])
        handler, users, participations = self.confirm(
            BulkRemoveUsersHandler, plan, user_ids=["9", "7"],
            expected_count="2")

        users.assert_called_once_with(handler.sql_session, [7, 9])
        participations.assert_not_called()
        handler.sql_session.commit.assert_called_once_with()
        # Each group that lost users is emptied and sent again (a
        # reinitialize would not drop the removed users from RWS).
        regenerate = handler.service.proxy_service.regenerate_ranking
        self.assertEqual(handler.schedule_rpc.call_args_list, [
            mock.call(regenerate, group=None),
            mock.call(regenerate, group="omi")])
        # Only ours: try_commit would add an "Operation successful."
        self.assertEqual(notifications(handler),
                         [("2 users were deleted", "")])
        handler.redirect.assert_called_once_with("/users")

    def test_contest_removal(self):
        plan = make_plan(entry(7, "ana"), groups=["omi"])
        handler, users, participations = self.confirm(
            BulkRemoveParticipationsHandler, plan, user_ids=["7"],
            expected_count="1", contest_id=CONTEST_ID)

        participations.assert_called_once_with(
            handler.sql_session, CONTEST_ID, [7])
        users.assert_not_called()
        handler.schedule_rpc.assert_called_once_with(
            handler.service.proxy_service.regenerate_ranking, group="omi")
        self.assertIn(("1 participation was removed", ""),
                      notifications(handler))
        handler.redirect.assert_called_once_with("/contest/4/users")

    def test_one_user_is_notified_in_the_singular(self):
        handler, _, _ = self.confirm(
            BulkRemoveUsersHandler, make_plan(entry(7, "ana")),
            user_ids=["7"], expected_count="1")

        self.assertIn(("1 user was deleted", ""), notifications(handler))

    def test_several_participations_are_notified_in_the_plural(self):
        handler, _, _ = self.confirm(
            BulkRemoveParticipationsHandler,
            make_plan(entry(7, "ana"), entry(9, "beto")),
            user_ids=["7", "9"], expected_count="2", contest_id=CONTEST_ID)

        self.assertIn(("2 participations were removed", ""),
                      notifications(handler))

    def test_a_platform_removal_is_logged(self):
        plan = make_plan(entry(7, "ana"), entry(9, "beto"))
        with self.assertLogs(MODULE, level="INFO") as logged:
            self.confirm(BulkRemoveUsersHandler, plan, user_ids=["7", "9"],
                         expected_count="2")

        (record,) = logged.records
        message = record.getMessage()
        for part in ("root", "2", "platform", "ana", "beto"):
            self.assertIn(part, message)

    def test_a_long_list_of_usernames_is_cut_in_the_log(self):
        plan = make_plan(*(entry(i, "user%d" % i) for i in range(250)))
        with self.assertLogs(MODULE, level="INFO") as logged:
            self.confirm(BulkRemoveUsersHandler, plan,
                         user_ids=[str(i) for i in range(250)],
                         expected_count="250")

        (record,) = logged.records
        message = record.getMessage()
        self.assertIn("user199", message)
        self.assertNotIn("user200", message)
        self.assertTrue(message.endswith("and 50 more."), message[-30:])

    def test_a_contest_removal_is_logged_with_the_contest(self):
        plan = make_plan(entry(7, "ana"))
        with self.assertLogs(MODULE, level="INFO") as logged:
            self.confirm(BulkRemoveParticipationsHandler, plan,
                         user_ids=["7"], expected_count="1",
                         contest_id=CONTEST_ID)

        (record,) = logged.records
        message = record.getMessage()
        for part in ("root", "1", "dia1", str(CONTEST_ID), "ana"):
            self.assertIn(part, message)

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
        # Beto was removed by someone else since the preview, and ana got
        # a submission.
        previewed = make_plan(entry(7, "ana"), entry(9, "beto"))
        for current, fingerprint in (
                (make_plan(entry(7, "ana")), previewed.fingerprint),
                (make_plan(entry(7, "ana", submissions=1)),
                 make_plan(entry(7, "ana")).fingerprint),
                (make_plan(entry(7, "ana")), ""),
                (make_plan(entry(7, "ana")), None)):
            form = confirm_form(current)
            form.pop("fingerprint")
            if fingerprint is not None:
                form["fingerprint"] = fingerprint
            handler = make_handler(form=form, user_ids=["7"])
            with mock.patch(MODULE + ".plan_removal", return_value=current), \
                    mock.patch(MODULE + ".remove_users") as users:
                handler._post_sync()

            users.assert_not_called()
            handler.sql_session.commit.assert_not_called()
            handler.schedule_rpc.assert_not_called()
            params = handler.render.call_args.kwargs
            self.assertEqual(params["error"], PLAN_CHANGED)
            self.assertIs(params["plan"], current)

    def failed_commit(self):
        """Confirm a removal whose commit fails; return the handler."""
        plan = make_plan(entry(7, "ana"))
        handler = make_handler(form=confirm_form(plan), user_ids=["7"])
        handler.sql_session.commit.side_effect = RuntimeError("deadlock")
        with mock.patch(MODULE + ".plan_removal", return_value=plan), \
                mock.patch(MODULE + ".remove_users", return_value=1), \
                self.assertNoLogs(MODULE, level="INFO"):
            handler._post_sync()
        return handler

    def test_a_failed_commit_removes_nothing_and_says_so_once(self):
        handler = self.failed_commit()

        handler.sql_session.rollback.assert_called_once_with()
        handler.schedule_rpc.assert_not_called()
        handler.redirect.assert_called_once_with("/users")
        self.assertEqual(notifications(handler), [
            ("Nothing was deleted", repr(RuntimeError("deadlock")))])

    def test_a_failed_removal_rolls_back(self):
        plan = make_plan(entry(7, "ana"))
        handler = make_handler(form=confirm_form(plan), user_ids=["7"])
        with mock.patch(MODULE + ".plan_removal", return_value=plan), \
                mock.patch(MODULE + ".remove_users",
                           side_effect=RuntimeError("boom")):
            handler._post_sync()

        handler.sql_session.rollback.assert_called_once_with()
        handler.sql_session.commit.assert_not_called()
        handler.schedule_rpc.assert_not_called()
        self.assertEqual(notifications(handler), [
            ("Nothing was deleted", repr(RuntimeError("boom")))])

    def failed_removal_text(self, error):
        plan = make_plan(entry(7, "ana"))
        handler = make_handler(form=confirm_form(plan), user_ids=["7"])
        with mock.patch(MODULE + ".plan_removal", return_value=plan), \
                mock.patch(MODULE + ".remove_users", side_effect=error):
            handler._post_sync()
        ((subject, text),) = notifications(handler)
        self.assertEqual(subject, "Nothing was deleted")
        return text

    def test_a_long_database_error_is_shortened(self):
        error = RuntimeError("wrapper with the whole SQL")
        error.orig = "x" * 1000
        text = self.failed_removal_text(error)

        self.assertEqual(len(text), 300)
        self.assertTrue(text.startswith("x" * 300))

    def test_a_short_database_error_shows_only_the_driver_message(self):
        error = RuntimeError("wrapper with the whole SQL")
        error.orig = ValueError("violates foreign key")

        self.assertEqual(self.failed_removal_text(error),
                         "violates foreign key")

    def test_an_error_without_orig_uses_repr_cut_to_300(self):
        error = RuntimeError("y" * 1000)
        text = self.failed_removal_text(error)

        self.assertEqual(text, repr(error)[:300])
        self.assertEqual(len(text), 300)

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
                         ignored=[("nadie", NOT_FOUND)],
                         groups=[None, "omi"])
        html = self.render(plan)

        self.assertIn("ana", html)
        self.assertIn("dia1, dia2", html)
        self.assertIn(
            "2 users will be deleted, 3 submissions and 0 user tests.", html)
        self.assertIn("Rankings that will be emptied and sent again: "
                      "root (/), omi.", html)
        self.assertIn("nadie", html)
        self.assertIn(NOT_FOUND, html)
        self.assertIn('name="user_id" value="7"', html)
        self.assertIn('name="user_id" value="9"', html)
        self.assertIn('name="action" value="confirm"', html)
        self.assertIn('name="fingerprint" value="%s"' % plan.fingerprint,
                      html)
        self.assertIn('name="expected_count"', html)
        self.assertIn("Type 2 to confirm", html)
        self.assertNotIn("bulk_remove_running", html)

    def test_contest_page_and_running_warning(self):
        contest = SimpleNamespace(id=CONTEST_ID, name="dia1")
        html = self.render(make_plan(entry(7, "ana"), running=True),
                           contest=contest)

        self.assertIn(
            "1 participation will be removed from this contest, with", html)
        self.assertIn("bulk_remove_running", html)
        self.assertNotIn("Rankings that will be emptied", html)

    def test_one_user_is_told_in_the_singular(self):
        html = self.render(make_plan(entry(7, "ana")))

        self.assertIn("1 user will be deleted, 0 submissions", html)

    def test_several_participations_are_told_in_the_plural(self):
        contest = SimpleNamespace(id=CONTEST_ID, name="dia1")
        html = self.render(make_plan(entry(7, "ana"), entry(9, "beto")),
                           contest=contest)

        self.assertIn(
            "2 participations will be removed from this contest", html)

    def test_error_line(self):
        html = self.render(make_plan(entry(7, "ana")), error=WRONG_COUNT)

        self.assertIn(WRONG_COUNT, html)

    def test_nothing_to_remove_has_no_button(self):
        html = self.render(make_plan(ignored=[("nadie", NOT_FOUND)]))

        self.assertIn("There is nothing to delete.", html)
        self.assertNotIn('name="expected_count"', html)

    def test_the_largest_plan_fits_the_form_limit(self):
        # The confirmation page posts one user_id per planned user, and
        # AWS rejects a form with more arguments than MAX_FORM_FIELDS.
        # The cap on the plan leaves room for the page's own fields.
        template = AWS_ENVIRONMENT.get_template("users_bulk_remove.html")
        plan = make_plan(*(entry(i, "user%d" % i)
                           for i in range(MAX_PLAN_USERS)))
        params = {"url": fake_url, "admin": SimpleNamespace(
                      permission_all=True),
                  "xsrf_form_html": '<input type="hidden" name="_xsrf"'
                                    ' value="token"/>',
                  "contest": None, "plan": plan, "error": None,
                  "action_url": "/users/remove", "back_url": "/users"}
        html = "".join(template.blocks["core"](template.new_context(params)))

        # Every named control is posted. The submit button has no name,
        # so it is not.
        arguments = len(re.findall(
            r'<(?:input|select|textarea|button)\b[^>]*\sname="', html))

        self.assertEqual(arguments, MAX_PLAN_USERS + 4)
        self.assertLessEqual(arguments, MAX_FORM_FIELDS - 50)


class TestUserPageRemoveButton(unittest.TestCase):

    def test_it_opens_the_bulk_preview_of_that_user(self):
        template = AWS_ENVIRONMENT.get_template("user.html")
        user = SimpleNamespace(
            id=7, first_name="Ana", last_name="Perez", username="ana",
            password="plaintext:x", email="", preferred_languages=[],
            timezone="")
        params = {"url": fake_url, "xsrf_form_html": "",
                  "admin": SimpleNamespace(permission_all=True),
                  "user": user, "participations": [],
                  "unassigned_contests": []}
        html = "".join(template.blocks["core"](template.new_context(params)))

        (form,) = re.findall(
            r'<form action="/users/remove".*?</form>', html, re.DOTALL)
        self.assertNotIn('name="operation"', form)
        # What the button posts gives the preview of that user alone.
        posted = dict(re.findall(
            r'<input type="hidden" name="(\w+)" value="([^"]*)"', form))
        handler = make_handler(form={"action": posted["action"]},
                               user_ids=[posted["user_id"]])
        plan = make_plan(entry(7, "ana"))
        with mock.patch(MODULE + ".plan_removal",
                        return_value=plan) as planner:
            handler._post_sync()

        planner.assert_called_once_with(
            handler.sql_session, contest_id=None, user_ids=[7],
            usernames=[])
        self.assertIs(handler.render.call_args.kwargs["plan"], plan)


class TestBulkRemovalInTwoLanguages(unittest.TestCase):
    """The bulk removal page and notifications in English and in Spanish.

    The Spanish texts are those that the page and the handler showed
    before they were translated, word for word (the "pinned" ones).
    Three of the page lines were wrapped in the template source and are
    now one line; the HTML shows them the same.

    """

    def render(self, translation, plan, *, contest=None, error=None):
        template = AWS_ENVIRONMENT.get_template("users_bulk_remove.html")
        params = {"url": fake_url, "xsrf_form_html": "",
                  "admin": SimpleNamespace(permission_all=True),
                  "contest": contest, "plan": plan, "error": error,
                  "action_url": "/users/remove", "back_url": "/users",
                  "gettext": translation.gettext,
                  "ngettext": translation.ngettext}
        return "".join(template.blocks["core"](template.new_context(params)))

    def handler_in(self, translation, handler_class=BulkRemoveUsersHandler,
                   **kwargs):
        handler = make_handler(handler_class, **kwargs)
        handler._ = translation.gettext
        handler.n_ = translation.ngettext
        return handler

    def removal_notification(self, translation, handler_class, removed, *,
                             contest_id=None):
        """The notification of a confirmed removal of removed users."""
        users = [entry(i, "user%d" % i) for i in range(2)]
        plan = make_plan(*users)
        handler = self.handler_in(
            translation, handler_class, form=confirm_form(plan),
            user_ids=["0", "1"])
        with mock.patch(MODULE + ".plan_removal", return_value=plan), \
                mock.patch(MODULE + ".remove_users", return_value=removed), \
                mock.patch(MODULE + ".remove_participations",
                           return_value=removed):
            if contest_id is None:
                handler._post_sync()
            else:
                handler._post_sync(str(contest_id))
        ((subject, text),) = notifications(handler)
        self.assertEqual(text, "")
        return subject

    def test_a_page_in_english(self):
        # Scenario: A page in English
        plan = make_plan(entry(7, "ana", submissions=3),
                         entry(9, "beto"),
                         ignored=[("nadie", NOT_FOUND),
                                  ("carla", NOT_IN_CONTEST)],
                         groups=[None, "omi"])
        html = self.render(DEFAULT_TRANSLATION, plan)

        for text in ("Delete users", "First name", "Last name",
                     "Submissions", "Contests", "Ignored:",
                     "nadie \u2014 does not exist",
                     "carla \u2014 does not take part in the contest",
                     "This operation cannot be undone.", "Cancel",
                     'value="Delete"'):
            self.assertIn(text, html)
        for spanish in ("Borrar", "Nombre", "Apellido", "Envíos",
                        "Concursos", "Ignorados", "no existe", "Cancelar",
                        "operación"):
            self.assertNotIn(spanish, html)

    def test_a_page_in_spanish(self):
        # Scenario: A page in Spanish
        plan = make_plan(entry(7, "ana", submissions=3,
                               contests=("dia1", "dia2")),
                         entry(9, "beto"),
                         ignored=[("nadie", NOT_FOUND),
                                  ("carla", NOT_IN_CONTEST)],
                         groups=[None, "omi"], running=True)
        html = self.render(SPANISH, plan)

        for text in (
                "Borrar usuarios",
                "Atención: hay un concurso en curso entre los afectados. "
                "Se perderán envíos hechos durante el concurso, y vaciar y "
                "volver a enviar un ranking en pleno concurso es pesado.",
                "Se borrarán 2 usuarios, 3 envíos y 0 user tests.",
                "Esta operación no se puede deshacer.",
                "Rankings que se vaciarán y se enviarán otra vez: "
                "raíz (/), omi.",
                "<th>Username</th>", "<th>Nombre</th>",
                "<th>Apellido</th>", "<th>Envíos</th>",
                "<th>User tests</th>", "<th>Concursos</th>",
                "Ignorados:", "nadie \u2014 no existe",
                "carla \u2014 no participa en el concurso",
                "Escribe 2 para confirmar:", 'value="Borrar"',
                ">Cancelar</a>"):
            self.assertIn(text, html)

    def test_the_contest_page_in_spanish(self):
        # Scenario: A page in Spanish
        contest = SimpleNamespace(id=CONTEST_ID, name="dia1")
        html = self.render(SPANISH, make_plan(entry(7, "ana")),
                           contest=contest)

        self.assertIn("Quitar participaciones de dia1", html)
        self.assertIn("Se quitará 1 participación de este concurso, con "
                      "0 envíos y 0 user tests.", html)
        self.assertIn('value="Quitar"', html)
        self.assertNotIn("<th>Concursos</th>", html)

    def test_the_contest_page_in_english(self):
        # Scenario: A page in English
        contest = SimpleNamespace(id=CONTEST_ID, name="dia1")
        html = self.render(DEFAULT_TRANSLATION, make_plan(entry(7, "ana")),
                           contest=contest)

        self.assertIn("Remove participations from dia1", html)
        self.assertIn('value="Remove"', html)

    def test_one_and_several_in_the_page(self):
        # Scenario: One and several (the page; zero users shows the
        # "nothing to delete" line instead of a count)
        contest = SimpleNamespace(id=CONTEST_ID, name="dia1")
        one, two = make_plan(entry(7, "ana")), \
            make_plan(entry(7, "ana"), entry(9, "beto"))
        for translation, platform, in_contest in (
                (DEFAULT_TRANSLATION,
                 ("1 user will be deleted,", "2 users will be deleted,"),
                 ("1 participation will be removed from this contest",
                  "2 participations will be removed from this contest")),
                (SPANISH,
                 ("Se borrará 1 usuario,", "Se borrarán 2 usuarios,"),
                 ("Se quitará 1 participación de este concurso",
                  "Se quitarán 2 participaciones de este concurso"))):
            self.assertIn(platform[0], self.render(translation, one))
            self.assertIn(platform[1], self.render(translation, two))
            self.assertIn(
                in_contest[0], self.render(translation, one, contest=contest))
            self.assertIn(
                in_contest[1], self.render(translation, two, contest=contest))

    def test_zero_in_the_page(self):
        # Scenario: Zero (nothing planned, so no count and no form)
        for translation, text in ((DEFAULT_TRANSLATION,
                                   "There is nothing to delete."),
                                  (SPANISH, "No hay nada que borrar.")):
            html = self.render(translation,
                               make_plan(ignored=[("nadie", NOT_FOUND)]))
            self.assertIn(text, html)
            self.assertNotIn('name="expected_count"', html)

    def test_group_names_are_escaped_once(self):
        plan = make_plan(entry(7, "ana"), groups=["<b>"])
        html = self.render(SPANISH, plan)

        self.assertIn("otra vez: &lt;b&gt;.", html)

    def test_a_notification_in_spanish(self):
        # Scenario: A notification (one, several and zero removed)
        users = (1, "Se borró 1 usuario"), (2, "Se borraron 2 usuarios"), \
            (0, "Se borraron 0 usuarios")
        for removed, spanish in users:
            self.assertEqual(self.removal_notification(
                SPANISH, BulkRemoveUsersHandler, removed), spanish)
        participations = (1, "Se quitó 1 participación"), \
            (2, "Se quitaron 2 participaciones"), \
            (0, "Se quitaron 0 participaciones")
        for removed, spanish in participations:
            self.assertEqual(self.removal_notification(
                SPANISH, BulkRemoveParticipationsHandler, removed,
                contest_id=CONTEST_ID), spanish)

    def test_a_notification_in_english(self):
        # Scenario: A notification (one, several and zero removed)
        for removed, english in ((1, "1 user was deleted"),
                                 (2, "2 users were deleted"),
                                 (0, "0 users were deleted")):
            self.assertEqual(self.removal_notification(
                DEFAULT_TRANSLATION, BulkRemoveUsersHandler, removed),
                english)
        for removed, english in ((1, "1 participation was removed"),
                                 (2, "2 participations were removed"),
                                 (0, "0 participations were removed")):
            self.assertEqual(self.removal_notification(
                DEFAULT_TRANSLATION, BulkRemoveParticipationsHandler,
                removed, contest_id=CONTEST_ID), english)

    def test_the_other_notifications_in_spanish(self):
        # Scenario: A notification
        bad_id = self.handler_in(SPANISH, user_ids=["7", "x"])
        bad_id._post_sync()
        too_many = self.handler_in(SPANISH, user_ids=["7"])
        big = make_plan(*(entry(i, "u%d" % i)
                          for i in range(MAX_PLAN_USERS + 1)))
        with mock.patch(MODULE + ".plan_removal", return_value=big):
            too_many._post_sync()
        plan = make_plan(entry(7, "ana"))
        failed = self.handler_in(SPANISH, form=confirm_form(plan),
                                 user_ids=["7"])
        with mock.patch(MODULE + ".plan_removal", return_value=plan), \
                mock.patch(MODULE + ".remove_users",
                           side_effect=RuntimeError("boom")):
            failed._post_sync()

        self.assertEqual(notifications(bad_id),
                         [("Campos no válidos", "user_id")])
        self.assertEqual(notifications(too_many), [
            ("La selección es demasiado grande",
             "Son 9901 usuarios y el máximo por vez es 9900. "
             "Divídela en listas más pequeñas.")])
        self.assertEqual(notifications(failed), [
            ("No se borró nada", repr(RuntimeError("boom")))])

    def test_the_first_notifications_in_spanish(self):
        # Scenario: A notification
        nothing = self.handler_in(SPANISH, form={"usernames": "  "})
        nothing._post_sync()
        bad_list = self.handler_in(SPANISH, file="josé".encode("latin-1"))
        bad_list._post_sync()
        too_big = self.handler_in(SPANISH, file=b"a" * (1024 * 1024 + 1))
        too_big._post_sync()

        self.assertEqual(notifications(nothing),
                         [("No elegiste ningún usuario.", "")])
        self.assertEqual(notifications(bad_list), [
            ("No se leyó la lista", "la lista no está en UTF-8")])
        self.assertEqual(notifications(too_big), [
            ("No se leyó la lista", "la lista pasa de 1 MB")])

    def test_the_confirmation_errors_in_spanish(self):
        # Scenario: A page in Spanish (the error line of the page)
        plan = make_plan(entry(7, "ana"), entry(9, "beto"))
        errors = {}
        for name, form in (
                ("count", dict(confirm_form(plan), expected_count="3")),
                ("changed", dict(confirm_form(plan), fingerprint="x"))):
            handler = self.handler_in(SPANISH, form=form,
                                      user_ids=["7", "9"])
            with mock.patch(MODULE + ".plan_removal", return_value=plan):
                handler._post_sync()
            errors[name] = handler.render.call_args.kwargs["error"]

        self.assertEqual(errors, {
            "count": "El número escrito no coincide con el número de "
                     "usuarios. No se borró nada.",
            "changed": "La selección cambió desde la vista previa (los "
                       "usuarios, sus concursos o envíos, o si hay un "
                       "concurso en curso): revisa la lista y confirma "
                       "otra vez. No se borró nada."})
