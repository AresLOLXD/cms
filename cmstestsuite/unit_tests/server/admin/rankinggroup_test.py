"""Tests for the AWS ranking group handlers."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cms.db import RankingGroup
from cms.server.admin.handlers.rankinggroup import \
    AddRankingGroupHandler, RankingGroupHandler, \
    RegenerateRankingHandler, read_ranking_group_attrs, \
    read_ranking_group_visibility
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmscommon.crypto import validate_password


def form_handler(form: dict) -> MagicMock:
    """Return a fake handler whose get_string reads from form."""
    handler = MagicMock()

    def get_string(dest, name, empty=""):
        if name in form:
            dest[name] = form[name] if form[name] != "" else empty
    handler.get_string.side_effect = get_string
    return handler


class TestReadRankingGroupAttrs(unittest.TestCase):

    def test_valid_group(self):
        attrs = dict()
        read_ranking_group_attrs(
            form_handler({"name": "olim", "description": "OLIM"}), attrs)
        self.assertEqual(attrs, {"name": "olim", "description": "OLIM"})

    def test_description_defaults_to_name(self):
        attrs = dict()
        read_ranking_group_attrs(
            form_handler({"name": "olim", "description": ""}), attrs)
        self.assertEqual(attrs["description"], "olim")

    def test_rejects_reserved_and_invalid_names(self):
        for name in ["events", "Olim", "", "a/b"]:
            with self.assertRaises(ValueError, msg=name):
                read_ranking_group_attrs(
                    form_handler({"name": name, "description": "x"}),
                    dict())


class TestRegenerateRankingHandler(unittest.TestCase):

    def run_post(self, group_arg: str) -> MagicMock:
        handler = RegenerateRankingHandler.__new__(RegenerateRankingHandler)
        # "service" is a read-only property (defined on
        # CommonRequestHandler) that returns self.application.service, so
        # it must be mocked through "application", not assigned directly.
        handler.application = MagicMock()
        handler.get_argument = MagicMock(return_value=group_arg)
        handler.redirect = MagicMock()
        handler.url = MagicMock(return_value="/ranking_groups")
        handler.schedule_rpc = MagicMock()
        handler._post_sync()
        return handler

    def test_group(self):
        handler = self.run_post("olim")
        handler.schedule_rpc.assert_called_once_with(
            handler.application.service.proxy_service.regenerate_ranking,
            group="olim")

    def test_root(self):
        handler = self.run_post("")
        handler.schedule_rpc.assert_called_once_with(
            handler.application.service.proxy_service.regenerate_ranking,
            group=None)


def visibility_handler(form: dict) -> MagicMock:
    """Return a fake handler reading hidden/password fields from form."""
    handler = MagicMock()

    def get_bool(dest, name):
        dest[name] = bool(form.get(name, False))

    def get_argument(name, default=None, strip=True):
        value = form.get(name, default)
        if strip and isinstance(value, str):
            value = value.strip()
        return value
    handler.get_bool.side_effect = get_bool
    handler.get_argument.side_effect = get_argument
    return handler


class TestReadRankingGroupVisibility(unittest.TestCase):

    def test_hidden_checkbox(self):
        attrs = dict()
        read_ranking_group_visibility(
            visibility_handler({"hidden": "on"}), attrs)
        self.assertIs(attrs["hidden"], True)
        attrs = dict()
        read_ranking_group_visibility(visibility_handler({}), attrs)
        self.assertIs(attrs["hidden"], False)

    def test_new_password_is_hashed_with_spaces_kept(self):
        attrs = dict()
        read_ranking_group_visibility(
            visibility_handler({"staff_password": " contraseña "}), attrs)
        self.assertTrue(attrs["staff_password"].startswith("bcrypt:"))
        self.assertTrue(validate_password(attrs["staff_password"],
                                          " contraseña "))

    def test_empty_password_keeps_the_current_one(self):
        attrs = {"staff_password": "bcrypt:old"}
        read_ranking_group_visibility(
            visibility_handler({"staff_password": ""}), attrs)
        self.assertEqual(attrs["staff_password"], "bcrypt:old")

    def test_remove_password(self):
        attrs = {"staff_password": "bcrypt:old"}
        read_ranking_group_visibility(
            visibility_handler({"remove_staff_password": "on"}), attrs)
        self.assertIsNone(attrs["staff_password"])

    def test_new_and_remove_conflict(self):
        with self.assertRaises(ValueError):
            read_ranking_group_visibility(
                visibility_handler({"staff_password": "x",
                                    "remove_staff_password": "on"}),
                dict())

    def test_password_over_72_bytes_is_rejected(self):
        with self.assertRaises(ValueError):
            read_ranking_group_visibility(
                visibility_handler({"staff_password": "ñ" * 37}), dict())

    def test_password_over_72_bytes_is_rejected_before_hashing(self):
        # bcrypt 4.x, the pinned version, silently cuts a longer password
        # at 72 bytes instead of failing.
        with patch("cms.server.admin.handlers.rankinggroup.hash_password") \
                as hasher:
            with self.assertRaises(ValueError):
                read_ranking_group_visibility(
                    visibility_handler({"staff_password": "ñ" * 37}), dict())
        hasher.assert_not_called()

    def test_password_of_exactly_72_bytes_is_accepted(self):
        attrs = dict()
        read_ranking_group_visibility(
            visibility_handler({"staff_password": "ñ" * 36}), attrs)
        self.assertTrue(validate_password(attrs["staff_password"],
                                          "ñ" * 36))


class TestRankingGroupHandlerSavesVisibility(unittest.TestCase):

    def test_edit_hides_and_sets_password(self):
        group = RankingGroup(name="olim", description="OLIM",
                             hidden=False, staff_password=None)
        handler = RankingGroupHandler.__new__(RankingGroupHandler)
        handler.application = MagicMock()
        handler.safe_get_item = MagicMock(return_value=group)
        form = {"name": "olim", "description": "OLIM", "hidden": "on",
                "staff_password": "pw"}

        def get_string(dest, name, empty=""):
            if name in form:
                dest[name] = form[name] if form[name] != "" else empty
        fake = visibility_handler(form)
        handler.get_string = MagicMock(side_effect=get_string)
        handler.get_bool = fake.get_bool
        handler.get_argument = fake.get_argument
        handler.try_commit = MagicMock(return_value=True)
        handler.schedule_rpc = MagicMock()
        handler.redirect = MagicMock()
        handler.url = MagicMock(return_value="/ranking_group/1")

        handler._post_sync("1")

        self.assertIs(group.hidden, True)
        self.assertTrue(validate_password(group.staff_password, "pw"))
        handler.schedule_rpc.assert_called_once_with(
            handler.service.proxy_service.reinitialize)

    def make_handler(self, handler_class, form: dict):
        """Return a handler of handler_class reading its fields from form."""
        handler = handler_class.__new__(handler_class)
        handler.application = MagicMock()
        handler.sql_session = MagicMock()

        def get_string(dest, name, empty=""):
            if name in form:
                dest[name] = form[name] if form[name] != "" else empty
        fake = visibility_handler(form)
        handler.get_string = MagicMock(side_effect=get_string)
        handler.get_bool = fake.get_bool
        handler.get_argument = fake.get_argument
        handler.try_commit = MagicMock(return_value=True)
        handler.schedule_rpc = MagicMock()
        handler.redirect = MagicMock()
        handler.url = MagicMock(return_value="/ranking_groups")
        return handler

    def test_add_stores_hidden_and_hashed_password(self):
        handler = self.make_handler(
            AddRankingGroupHandler,
            {"name": "olim", "description": "OLIM", "hidden": "on",
             "staff_password": "pw"})

        handler._post_sync()

        (group,) = handler.sql_session.add.call_args.args
        self.assertIs(group.hidden, True)
        self.assertTrue(validate_password(group.staff_password, "pw"))
        handler.schedule_rpc.assert_called_once_with(
            handler.service.proxy_service.reinitialize)

    def test_add_without_visibility_fields_is_visible_without_password(self):
        handler = self.make_handler(
            AddRankingGroupHandler, {"name": "olim", "description": "OLIM"})

        handler._post_sync()

        (group,) = handler.sql_session.add.call_args.args
        self.assertIs(group.hidden, False)
        self.assertIsNone(group.staff_password)

    def test_edit_keeps_the_password_when_the_field_is_empty(self):
        group = RankingGroup(name="olim", description="OLIM", hidden=True,
                             staff_password="bcrypt:old")
        handler = self.make_handler(
            RankingGroupHandler,
            {"name": "olim", "description": "OLIM", "staff_password": ""})
        handler.safe_get_item = MagicMock(return_value=group)

        handler._post_sync("1")

        self.assertIs(group.hidden, False)
        self.assertEqual(group.staff_password, "bcrypt:old")

    def test_edit_rejects_conflicting_password_fields(self):
        group = RankingGroup(name="olim", description="OLIM", hidden=False,
                             staff_password="bcrypt:old")
        handler = self.make_handler(
            RankingGroupHandler,
            {"name": "olim", "description": "OLIM", "hidden": "on",
             "staff_password": "new", "remove_staff_password": "on"})
        handler.safe_get_item = MagicMock(return_value=group)

        handler._post_sync("1")

        self.assertIs(group.hidden, False)
        self.assertEqual(group.staff_password, "bcrypt:old")
        handler.try_commit.assert_not_called()
        handler.schedule_rpc.assert_not_called()
        handler.service.add_notification.assert_called_once()


class TestRankingGroupTemplates(unittest.TestCase):
    """The visibility fields of the pages, and that the password stays out.

    Only the "core" block of each page is rendered: the visibility fields
    live there, and the rest of base.html needs a whole request context.

    """

    # What the database holds for a group that has a staff password.
    STORED = "bcrypt:$2b$04$only-the-stored-hash-marker"

    def render_core(self, page: str, **params) -> str:
        template = AWS_ENVIRONMENT.get_template(page)
        params.setdefault(
            "url", lambda *parts: "/" + "/".join(str(p) for p in parts))
        params.setdefault("xsrf_form_html", "")
        params.setdefault("admin", SimpleNamespace(permission_all=True))
        return "".join(template.blocks["core"](template.new_context(params)))

    def assert_no_stored_password(self, html: str):
        for leak in ("stored-hash-marker", "$2b$", "bcrypt:"):
            self.assertNotIn(leak, html)
        self.assertNotRegex(html, r'name="staff_password"[^>]*value=')

    def test_group_page_says_whether_a_password_is_set(self):
        group = RankingGroup(name="olim", description="OLIM", hidden=True,
                             staff_password=self.STORED)

        html = self.render_core(
            "ranking_group.html", ranking_group=group, group_contests=[])

        self.assertRegex(html, r'name="hidden"\s+checked')
        self.assertIn("(set)", html)
        self.assertNotIn("(not set)", html)
        self.assertIn('name="remove_staff_password"', html)
        self.assert_no_stored_password(html)

    def test_group_page_of_a_visible_group_without_password(self):
        group = RankingGroup(name="olim", description="OLIM")

        html = self.render_core(
            "ranking_group.html", ranking_group=group, group_contests=[])

        self.assertIn('name="hidden"', html)
        self.assertNotRegex(html, r'name="hidden"\s+checked')
        self.assertIn("(not set)", html)
        self.assertNotIn("(set)", html)

    def test_add_page_starts_visible_and_without_password(self):
        html = self.render_core("add_ranking_group.html")

        self.assertIn('name="hidden"', html)
        self.assertNotRegex(html, r'name="hidden"\s+checked')
        self.assertIn('type="password" name="staff_password"', html)
        self.assertIn('autocomplete="new-password"', html)
        self.assertNotIn("remove_staff_password", html)
        self.assertNotIn("(set)", html)
        self.assertNotIn("(not set)", html)

    def test_list_marks_hidden_groups_and_passwords(self):
        groups = [
            RankingGroup(name="open", description="Open"),
            RankingGroup(name="olim", description="OLIM", hidden=True,
                         staff_password=self.STORED),
            RankingGroup(name="omips", description="OMIPS", hidden=True),
        ]

        html = self.render_core(
            "ranking_groups.html", ranking_group_list=groups,
            contest_list=[])

        self.assertIn("<th>Hidden</th>", html)
        self.assertIn("<th>Staff password</th>", html)
        self.assertEqual(html.count("<td>no</td>"), 1)
        self.assertEqual(html.count("<td>yes</td>"), 1)
        self.assertEqual(html.count(
            "<td>yes (nobody can see it: no staff password)</td>"), 1)
        self.assertEqual(html.count("<td>set</td>"), 1)
        self.assertEqual(html.count("<td>not set</td>"), 2)
        self.assert_no_stored_password(html)


if __name__ == "__main__":
    unittest.main()
