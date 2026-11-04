"""Tests for the AWS ranking group handlers."""

import re
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from cms.db import RankingGroup
from cms.server.admin.handlers.rankinggroup import \
    WINDOW_FIELDS, AddRankingGroupHandler, RankingGroupHandler, \
    RankingGroupListHandler, RegenerateRankingHandler, \
    read_ranking_group_attrs, read_ranking_group_visibility, \
    visibility_view
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


NOW = datetime(2026, 10, 10, 18, 0)


def visibility_handler(form: dict) -> MagicMock:
    """Return a fake handler reading the visibility fields from form."""
    handler = MagicMock()

    def get_argument(name, default=None, strip=True):
        value = form.get(name, default)
        if strip and isinstance(value, str):
            value = value.strip()
        return value
    handler.get_argument.side_effect = get_argument
    return handler


def make_group(group_id: int, name: str, **kwargs) -> RankingGroup:
    """Return a group with an id, which the constructor does not take."""
    group = RankingGroup(name=name, description=name.upper(), **kwargs)
    group.id = group_id
    return group


def make_form_handler(handler_class, form: dict):
    """Return a handler of handler_class reading its fields from form."""
    handler = handler_class.__new__(handler_class)
    handler.application = MagicMock()
    handler.sql_session = MagicMock()

    def get_string(dest, name, empty=""):
        if name in form:
            dest[name] = form[name] if form[name] != "" else empty
    fake = visibility_handler(form)
    handler.get_string = MagicMock(side_effect=get_string)
    handler.get_argument = fake.get_argument
    handler.try_commit = MagicMock(return_value=True)
    handler.schedule_rpc = MagicMock()
    handler.redirect = MagicMock()
    handler.url = MagicMock(return_value="/ranking_groups")
    return handler


class FormFields(HTMLParser):
    """Collect what a browser posts for the first form of a page.

    Unchecked boxes and buttons that were not pressed are not posted.

    """

    def __init__(self):
        super().__init__()
        self.fields = dict()
        self.done = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.done or tag != "input" or "name" not in attrs:
            return
        if attrs.get("type", "text") in (
                "submit", "reset", "button", "checkbox", "radio"):
            return
        self.fields[attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.done = True


class TestReadWindows(unittest.TestCase):

    def read(self, form, attrs=None):
        attrs = dict() if attrs is None else attrs
        read_ranking_group_visibility(visibility_handler(form), attrs, NOW)
        return attrs

    def test_fields_are_parsed_as_utc(self):
        attrs = self.read({"freeze_at": "2026-10-10 19:00:00",
                           "unfreeze_at": ""})
        self.assertEqual(attrs["freeze_at"], datetime(2026, 10, 10, 19, 0))
        self.assertIsNone(attrs["unfreeze_at"])

    def test_malformed_time_is_rejected(self):
        with self.assertRaises(ValueError):
            self.read({"freeze_at": "tomorrow"})

    def test_stale_field_keeps_the_stored_value(self):
        stored = datetime(2026, 10, 10, 20, 0)
        attrs = self.read({"freeze_at": "2026-10-10 19:00:00",
                           "freeze_at_shown": "2026-10-10 19:00:00"},
                          {"freeze_at": stored})
        self.assertEqual(attrs["freeze_at"], stored)

    def test_changed_field_is_applied(self):
        attrs = self.read({"freeze_at": "2026-10-10 21:00:00",
                           "freeze_at_shown": "2026-10-10 19:00:00"},
                          {"freeze_at": datetime(2026, 10, 10, 20, 0)})
        self.assertEqual(attrs["freeze_at"], datetime(2026, 10, 10, 21, 0))

    def test_field_without_a_stored_value_always_counts(self):
        # The add form has no current value to keep.
        attrs = self.read({"freeze_at": "2026-10-10 19:00:00",
                           "freeze_at_shown": "2026-10-10 19:00:00"})
        self.assertEqual(attrs["freeze_at"], datetime(2026, 10, 10, 19, 0))

    def test_hide_now_keeps_a_future_show_and_clears_a_past_one(self):
        future = NOW + timedelta(hours=2)
        attrs = self.read({"visibility_action": "hide_now"},
                          {"show_at": future})
        self.assertEqual((attrs["hide_at"], attrs["show_at"]), (NOW, future))
        attrs = self.read({"visibility_action": "hide_now"},
                          {"show_at": NOW - timedelta(hours=1)})
        self.assertEqual((attrs["hide_at"], attrs["show_at"]), (NOW, None))

    def test_show_now_only_when_hidden(self):
        attrs = self.read({"visibility_action": "show_now"},
                          {"hide_at": NOW - timedelta(hours=1)})
        self.assertEqual(attrs["show_at"], NOW)
        with self.assertRaises(ValueError):
            self.read({"visibility_action": "show_now"}, {})

    def test_freeze_and_unfreeze_now(self):
        attrs = self.read({"visibility_action": "freeze_now"}, {})
        self.assertEqual(attrs["freeze_at"], NOW)
        attrs = self.read({"visibility_action": "unfreeze_now"},
                          {"freeze_at": NOW - timedelta(hours=1)})
        self.assertEqual(attrs["unfreeze_at"], NOW)

    def test_freeze_now_keeps_a_future_unfreeze_and_clears_a_past_one(self):
        future = NOW + timedelta(hours=2)
        attrs = self.read({"visibility_action": "freeze_now"},
                          {"unfreeze_at": future})
        self.assertEqual((attrs["freeze_at"], attrs["unfreeze_at"]),
                         (NOW, future))
        attrs = self.read({"visibility_action": "freeze_now"},
                          {"freeze_at": NOW - timedelta(hours=2),
                           "unfreeze_at": NOW - timedelta(hours=1)})
        self.assertEqual((attrs["freeze_at"], attrs["unfreeze_at"]),
                         (NOW, None))

    def test_freeze_now_is_rejected_while_the_freeze_window_is_open(self):
        with self.assertRaisesRegex(ValueError, "already frozen"):
            self.read({"visibility_action": "freeze_now"},
                      {"freeze_at": NOW - timedelta(hours=1)})

    def test_freeze_now_follows_the_raw_window_while_hidden(self):
        # A hidden group is not "frozen" for the public, but pressing
        # "freeze now" would move freeze_at to now and publish the scores
        # of the whole window as soon as the group is shown.
        stored = {"hide_at": NOW - timedelta(hours=1),
                  "freeze_at": NOW - timedelta(hours=1)}
        with self.assertRaisesRegex(ValueError, "already frozen"):
            self.read({"visibility_action": "freeze_now"}, dict(stored))

    def test_unfreeze_now_works_while_hidden(self):
        attrs = self.read({"visibility_action": "unfreeze_now"},
                          {"hide_at": NOW - timedelta(hours=1),
                           "freeze_at": NOW - timedelta(hours=1)})
        self.assertEqual(attrs["unfreeze_at"], NOW)

    def test_unfreeze_now_is_rejected_when_not_frozen(self):
        with self.assertRaises(ValueError):
            self.read({"visibility_action": "unfreeze_now"}, {})

    def test_invalid_window_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "freeze"):
            self.read({"freeze_at": "2026-10-10 19:00:00",
                       "unfreeze_at": "2026-10-10 18:00:00"})

    def test_hidden_column_is_derived(self):
        attrs = self.read({"hide_at": "2026-10-10 20:00:00"})
        self.assertIs(attrs["hidden"], True)       # a pending hide
        attrs = self.read({"hide_at": "2026-10-10 10:00:00",
                           "show_at": "2026-10-10 12:00:00"})
        self.assertIs(attrs["hidden"], False)

    def test_hidden_column_is_rewritten_when_hide_at_is_cleared(self):
        # The migration re-hides the rows with hidden set and no hide_at:
        # a stale hidden must not survive a save that clears the hide.
        attrs = self.read(
            {"hide_at": "", "hide_at_shown": "2026-10-10 17:00:00"},
            {"hide_at": NOW - timedelta(hours=1), "hidden": True})
        self.assertIsNone(attrs["hide_at"])
        self.assertIs(attrs["hidden"], False)

    def test_hidden_column_is_rewritten_when_the_form_has_no_times(self):
        attrs = self.read({}, {"hidden": True})
        self.assertIs(attrs["hidden"], False)

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(ValueError):
            self.read({"visibility_action": "explode"})


class TestReadArchive(unittest.TestCase):
    """The archive_at field and the archive_now / unarchive buttons."""

    HOUR = timedelta(hours=1)

    def read(self, form, attrs=None):
        attrs = dict() if attrs is None else attrs
        read_ranking_group_visibility(visibility_handler(form), attrs, NOW)
        return attrs

    def test_setting_archive_at(self):
        # Scenario: Setting archive_at
        attrs = self.read({"archive_at": "2026-10-12 06:00:00",
                           "archive_at_shown": ""},
                          {"archive_at": None})
        self.assertEqual(attrs["archive_at"], datetime(2026, 10, 12, 6, 0))

    def test_clearing_archive_at(self):
        # Scenario: Clearing archive_at
        attrs = self.read({"archive_at": "",
                           "archive_at_shown": "2026-10-12 06:00:00"},
                          {"archive_at": datetime(2026, 10, 12, 6, 0)})
        self.assertIsNone(attrs["archive_at"])

    def test_a_stale_page_does_not_undo_a_change(self):
        # Scenario: A stale page does not undo a change
        stored = datetime(2026, 10, 12, 6, 0)
        attrs = self.read({"archive_at": "2026-10-11 06:00:00",
                           "archive_at_shown": "2026-10-11 06:00:00"},
                          {"archive_at": stored})
        self.assertEqual(attrs["archive_at"], stored)

    def test_a_malformed_archive_at(self):
        # Scenario: A malformed time
        with self.assertRaises(ValueError):
            self.read({"archive_at": "soon"}, {"archive_at": None})

    def test_archive_now(self):
        # Scenario: Archiving now
        attrs = self.read({"visibility_action": "archive_now"},
                          {"archive_at": NOW + self.HOUR})
        self.assertEqual(attrs["archive_at"], NOW)

    def test_archive_now_on_an_archived_group(self):
        # Scenario: Archive now on an archived group
        with self.assertRaisesRegex(ValueError, "already archived"):
            self.read({"visibility_action": "archive_now"},
                      {"archive_at": NOW - self.HOUR})

    def test_unarchive_button(self):
        # Scenario: Unarchive button
        attrs = self.read({"visibility_action": "unarchive"},
                          {"archive_at": NOW - self.HOUR})
        self.assertEqual(attrs["archive_at"], NOW + timedelta(hours=24))

    def test_unarchive_on_a_group_that_is_not_archived(self):
        # Scenario: Unarchive on a group that is not archived
        for archive_at in (None, NOW + self.HOUR):
            with self.assertRaisesRegex(ValueError, "not archived"):
                self.read({"visibility_action": "unarchive"},
                          {"archive_at": archive_at})


class TestReadStaffPassword(unittest.TestCase):

    def test_new_password_is_hashed_with_spaces_kept(self):
        attrs = dict()
        read_ranking_group_visibility(
            visibility_handler({"staff_password": " contraseña "}), attrs,
            NOW)
        self.assertTrue(attrs["staff_password"].startswith("bcrypt:"))
        self.assertTrue(validate_password(attrs["staff_password"],
                                          " contraseña "))

    def test_empty_password_keeps_the_current_one(self):
        attrs = {"staff_password": "bcrypt:old"}
        read_ranking_group_visibility(
            visibility_handler({"staff_password": ""}), attrs, NOW)
        self.assertEqual(attrs["staff_password"], "bcrypt:old")

    def test_remove_password(self):
        attrs = {"staff_password": "bcrypt:old"}
        read_ranking_group_visibility(
            visibility_handler({"remove_staff_password": "on"}), attrs, NOW)
        self.assertIsNone(attrs["staff_password"])

    def test_new_and_remove_conflict(self):
        with self.assertRaises(ValueError):
            read_ranking_group_visibility(
                visibility_handler({"staff_password": "x",
                                    "remove_staff_password": "on"}),
                dict(), NOW)

    def test_password_over_72_bytes_is_rejected(self):
        with self.assertRaises(ValueError):
            read_ranking_group_visibility(
                visibility_handler({"staff_password": "ñ" * 37}), dict(),
                NOW)

    def test_password_over_72_bytes_is_rejected_before_hashing(self):
        # bcrypt 4.x, the pinned version, silently cuts a longer password
        # at 72 bytes instead of failing.
        with patch("cms.server.admin.handlers.rankinggroup.hash_password") \
                as hasher:
            with self.assertRaises(ValueError):
                read_ranking_group_visibility(
                    visibility_handler({"staff_password": "ñ" * 37}),
                    dict(), NOW)
        hasher.assert_not_called()

    def test_password_of_exactly_72_bytes_is_accepted(self):
        attrs = dict()
        read_ranking_group_visibility(
            visibility_handler({"staff_password": "ñ" * 36}), attrs, NOW)
        self.assertTrue(validate_password(attrs["staff_password"],
                                          "ñ" * 36))


class TestVisibilityView(unittest.TestCase):

    HOUR = timedelta(hours=1)

    def view(self, **windows):
        return visibility_view(
            RankingGroup(name="olim", description="OLIM", **windows), NOW)

    def test_summary_tells_visible_hidden_and_frozen_apart(self):
        visible = self.view()
        self.assertEqual(
            (visible["summary"], visible["hidden_now"],
             visible["frozen_now"]), ("visible", False, False))
        hidden = self.view(hide_at=NOW - self.HOUR)
        self.assertEqual(
            (hidden["summary"], hidden["hidden_now"], hidden["frozen_now"]),
            ("oculto", True, False))
        frozen = self.view(freeze_at=NOW - self.HOUR)
        self.assertEqual(
            (frozen["summary"], frozen["hidden_now"], frozen["frozen_now"]),
            ("congelado", False, True))

    def test_windows_that_are_over_or_not_started_do_not_count(self):
        view = self.view(hide_at=NOW - 2 * self.HOUR, show_at=NOW - self.HOUR,
                         freeze_at=NOW + self.HOUR)
        self.assertEqual(view["summary"], "visible")

    def test_hidden_group_with_an_open_freeze_window_is_still_frozen(self):
        # The freeze button follows the raw window, not is_frozen_at().
        view = self.view(hide_at=NOW - self.HOUR, freeze_at=NOW - self.HOUR)
        self.assertEqual(view["summary"], "oculto (congelado)")
        self.assertIs(view["hidden_now"], True)
        self.assertIs(view["frozen_now"], True)

    def test_hidden_group_with_a_closed_freeze_window_is_not_frozen(self):
        view = self.view(hide_at=NOW - self.HOUR,
                         freeze_at=NOW - 2 * self.HOUR,
                         unfreeze_at=NOW - self.HOUR)
        self.assertEqual(view["summary"], "oculto")
        self.assertIs(view["frozen_now"], False)

    def test_fields_carry_the_utc_times(self):
        view = self.view(hide_at=datetime(2026, 10, 10, 20, 30, 15))
        fields = {f["name"]: f for f in view["fields"]}
        self.assertEqual(
            list(fields), ["hide_at", "show_at", "freeze_at", "unfreeze_at"])
        self.assertEqual(fields["hide_at"]["label"], "Hide from")
        self.assertEqual(fields["hide_at"]["utc"], "2026-10-10 20:30:15")
        self.assertEqual(fields["show_at"]["utc"], "")

    def test_next_change_is_the_earliest_future_time(self):
        zone = timezone(timedelta(hours=-6), "XYZ")
        with patch("cms.server.admin.handlers.rankinggroup.local_tz", zone):
            view = self.view(
                hide_at=NOW - self.HOUR,
                show_at=NOW + 3 * self.HOUR,
                freeze_at=NOW + self.HOUR)
        # Both the UTC time and the local one, each marked as such.
        self.assertEqual(
            view["next_change"],
            "Freeze at: 2026-10-10 19:00:00 UTC (2026-10-10 13:00 XYZ)")

    def test_next_change_is_empty_without_future_times(self):
        self.assertEqual(self.view()["next_change"], "")
        self.assertEqual(
            self.view(hide_at=NOW - self.HOUR)["next_change"], "")

    def test_next_change_skips_an_end_without_a_start(self):
        # Nothing is frozen or hidden by an end alone.
        self.assertEqual(
            self.view(unfreeze_at=NOW + self.HOUR)["next_change"], "")
        self.assertEqual(
            self.view(show_at=NOW + self.HOUR)["next_change"], "")

    def test_next_change_skips_a_time_that_changes_nothing_shown(self):
        view = self.view(unfreeze_at=NOW + self.HOUR,
                         hide_at=NOW + 2 * self.HOUR)
        self.assertTrue(view["next_change"].startswith(
            "Hide from: 2026-10-10 20:00:00 UTC"))
        # Hidden now: the freeze window counts, also while hidden.
        view = self.view(
            hide_at=NOW - self.HOUR, show_at=NOW + 3 * self.HOUR,
            freeze_at=NOW + self.HOUR, unfreeze_at=NOW + 2 * self.HOUR)
        self.assertTrue(view["next_change"].startswith(
            "Freeze at: 2026-10-10 19:00:00 UTC"))
        # A freeze that is over leaves nothing to name.
        view = self.view(hide_at=NOW - self.HOUR,
                         freeze_at=NOW - 2 * self.HOUR,
                         unfreeze_at=NOW - self.HOUR)
        self.assertEqual(view["next_change"], "")

    def test_next_change_with_two_times_at_the_same_instant(self):
        view = self.view(hide_at=NOW - self.HOUR, freeze_at=NOW + self.HOUR,
                         show_at=NOW + self.HOUR)
        label, _, rest = view["next_change"].partition(": ")
        self.assertIn(label, ("Freeze at", "Show again at"))
        self.assertTrue(rest.startswith("2026-10-10 19:00:00 UTC"))


class TestArchiveView(unittest.TestCase):

    def view(self, **kwargs):
        return visibility_view(
            RankingGroup(name="olim", description="OLIM", **kwargs), NOW)

    def test_an_archived_group(self):
        # Scenario: An archived group
        view = self.view(archive_at=datetime(2026, 10, 10, 17, 0))
        self.assertTrue(view["archived_now"])
        self.assertTrue(view["archive_summary"].startswith("archivado desde"))
        self.assertIn("2026-10-10 17:00:00 UTC", view["archive_summary"])
        self.assertEqual(view["archive_field"],
                         {"name": "archive_at", "label": "Archive at",
                          "utc": "2026-10-10 17:00:00"})

    def test_a_group_that_will_be_archived(self):
        # Scenario: A group that will be archived
        view = self.view(archive_at=datetime(2026, 10, 11, 17, 0))
        self.assertFalse(view["archived_now"])
        self.assertTrue(view["archive_summary"].startswith("se archiva el"))
        self.assertIn("2026-10-11 17:00:00 UTC", view["archive_summary"])

    def test_a_group_with_no_archive_at(self):
        # Scenario: A group with no archive_at
        view = self.view()
        self.assertFalse(view["archived_now"])
        self.assertEqual(view["archive_summary"], "nunca se archiva")
        self.assertEqual(view["archive_field"]["utc"], "")


class TestRankingGroupHandlersUseTheView(unittest.TestCase):

    def setUp(self):
        patcher = patch(
            "cms.server.admin.handlers.rankinggroup.make_datetime",
            return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_group_page_gets_the_view(self):
        group = make_group(1, "olim", hide_at=NOW - timedelta(hours=1))
        handler = RankingGroupHandler.__new__(RankingGroupHandler)
        handler.sql_session = MagicMock()
        handler.safe_get_item = MagicMock(return_value=group)
        handler.render_params = MagicMock(return_value=dict())
        handler.render = MagicMock()

        handler._get_sync("1")

        (page,), params = handler.render.call_args
        self.assertEqual(page, "ranking_group.html")
        self.assertEqual(params["view"]["summary"], "oculto")

    def test_add_page_gets_the_time_fields(self):
        handler = AddRankingGroupHandler.__new__(AddRankingGroupHandler)
        handler.render_params = MagicMock(return_value=dict())
        handler.render = MagicMock()

        handler._get_sync()

        (page,), params = handler.render.call_args
        self.assertEqual(page, "add_ranking_group.html")
        self.assertEqual(params["window_fields"], WINDOW_FIELDS)

    def test_list_page_gets_a_view_per_group(self):
        hidden = make_group(1, "olim", hide_at=NOW - timedelta(hours=1))
        visible = make_group(2, "omips")
        handler = RankingGroupListHandler.__new__(RankingGroupListHandler)
        handler.render_params = MagicMock(
            return_value={"ranking_group_list": [hidden, visible]})
        handler.render = MagicMock()

        handler._get_sync()

        (page,), params = handler.render.call_args
        self.assertEqual(page, "ranking_groups.html")
        self.assertEqual(
            {group_id: view["summary"]
             for group_id, view in params["views"].items()},
            {1: "oculto", 2: "visible"})


class TestRankingGroupHandlerSavesVisibility(unittest.TestCase):

    def setUp(self):
        patcher = patch(
            "cms.server.admin.handlers.rankinggroup.make_datetime",
            return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_edit_hides_and_sets_password(self):
        group = RankingGroup(name="olim", description="OLIM",
                             hidden=False, staff_password=None)
        handler = RankingGroupHandler.__new__(RankingGroupHandler)
        handler.application = MagicMock()
        handler.sql_session = MagicMock()
        handler.safe_get_item = MagicMock(return_value=group)
        form = {"name": "olim", "description": "OLIM",
                "visibility_action": "hide_now", "staff_password": "pw"}

        def get_string(dest, name, empty=""):
            if name in form:
                dest[name] = form[name] if form[name] != "" else empty
        fake = visibility_handler(form)
        handler.get_string = MagicMock(side_effect=get_string)
        handler.get_argument = fake.get_argument
        handler.try_commit = MagicMock(return_value=True)
        handler.schedule_rpc = MagicMock()
        handler.redirect = MagicMock()
        handler.url = MagicMock(return_value="/ranking_group/1")

        handler._post_sync("1")

        # The row is locked, so that a second request waits for this one.
        handler.sql_session.refresh.assert_called_once_with(
            group, with_for_update=True)
        self.assertEqual(group.hide_at, NOW)
        self.assertIs(group.hidden, True)
        self.assertTrue(validate_password(group.staff_password, "pw"))
        handler.schedule_rpc.assert_called_once_with(
            handler.service.proxy_service.reinitialize)

    def test_add_stores_windows_and_hashed_password(self):
        handler = make_form_handler(
            AddRankingGroupHandler,
            {"name": "olim", "description": "OLIM",
             "hide_at": "2026-10-10 20:00:00", "staff_password": "pw"})

        handler._post_sync()

        (group,) = handler.sql_session.add.call_args.args
        self.assertEqual(group.hide_at, datetime(2026, 10, 10, 20, 0))
        self.assertIs(group.hidden, True)     # a pending hide
        self.assertTrue(validate_password(group.staff_password, "pw"))
        handler.schedule_rpc.assert_called_once_with(
            handler.service.proxy_service.reinitialize)

    def test_add_without_visibility_fields_is_visible_without_password(self):
        handler = make_form_handler(
            AddRankingGroupHandler, {"name": "olim", "description": "OLIM"})

        handler._post_sync()

        (group,) = handler.sql_session.add.call_args.args
        self.assertIs(group.hidden, False)
        self.assertIsNone(group.hide_at)
        self.assertIsNone(group.staff_password)

    def test_add_applies_the_times_even_if_shown_matches(self):
        # A new group has no current value to keep: the times rule.
        handler = make_form_handler(
            AddRankingGroupHandler,
            {"name": "olim", "description": "OLIM",
             "freeze_at": "2026-10-10 19:00:00",
             "freeze_at_shown": "2026-10-10 19:00:00"})

        handler._post_sync()

        (group,) = handler.sql_session.add.call_args.args
        self.assertEqual(group.freeze_at, datetime(2026, 10, 10, 19, 0))

    def test_add_rejects_an_invalid_window(self):
        handler = make_form_handler(
            AddRankingGroupHandler,
            {"name": "olim", "description": "OLIM",
             "hide_at": "2026-10-10 20:00:00",
             "show_at": "2026-10-10 19:00:00"})

        handler._post_sync()

        handler.sql_session.add.assert_not_called()
        handler.try_commit.assert_not_called()
        handler.service.add_notification.assert_called_once()

    def test_edit_keeps_the_password_when_the_field_is_empty(self):
        group = RankingGroup(name="olim", description="OLIM", hidden=True,
                             staff_password="bcrypt:old")
        handler = make_form_handler(
            RankingGroupHandler,
            {"name": "olim", "description": "OLIM", "staff_password": ""})
        handler.safe_get_item = MagicMock(return_value=group)

        handler._post_sync("1")

        self.assertIs(group.hidden, False)
        self.assertEqual(group.staff_password, "bcrypt:old")

    def test_edit_rejects_conflicting_password_fields(self):
        group = RankingGroup(name="olim", description="OLIM", hidden=False,
                             staff_password="bcrypt:old")
        handler = make_form_handler(
            RankingGroupHandler,
            {"name": "olim", "description": "OLIM",
             "visibility_action": "hide_now",
             "staff_password": "new", "remove_staff_password": "on"})
        handler.safe_get_item = MagicMock(return_value=group)

        handler._post_sync("1")

        self.assertIs(group.hidden, False)
        self.assertIsNone(group.hide_at)
        self.assertEqual(group.staff_password, "bcrypt:old")
        handler.try_commit.assert_not_called()
        handler.schedule_rpc.assert_not_called()
        handler.service.add_notification.assert_called_once()

    def edit(self, form: dict, **stored) -> RankingGroup:
        """Save form over a group stored with stored and return it."""
        group = RankingGroup(name="olim", description="OLIM",
                             staff_password=None, **stored)
        handler = make_form_handler(
            RankingGroupHandler,
            {"name": "olim", "description": "OLIM", **form})
        handler.safe_get_item = MagicMock(return_value=group)

        handler._post_sync("1")

        handler.service.add_notification.assert_not_called()
        return group

    def test_edit_clearing_hide_at_rewrites_a_stale_hidden(self):
        # The migration re-hides the rows with hidden set and no hide_at,
        # so a save that clears the hide must not leave hidden behind.
        group = self.edit(
            {"hide_at": "", "hide_at_shown": "2026-10-10 17:00:00"},
            hidden=True, hide_at=datetime(2026, 10, 10, 17, 0))

        self.assertIsNone(group.hide_at)
        self.assertIs(group.hidden, False)

    def test_edit_rewrites_hidden_even_without_time_fields(self):
        group = self.edit({}, hidden=True)

        self.assertIs(group.hidden, False)

    def test_edit_hidden_follows_the_windows(self):
        group = self.edit(
            {"visibility_action": "hide_now"}, hidden=False)
        self.assertIs(group.hidden, True)

        group = self.edit(
            {"visibility_action": "show_now"}, hidden=True,
            hide_at=NOW - timedelta(hours=1))
        self.assertEqual(group.show_at, NOW)
        self.assertIs(group.hidden, False)

    def test_stale_page_keeps_a_window_scheduled_meanwhile(self):
        # Rendered with no hide, then somebody else scheduled one.
        scheduled = NOW + timedelta(hours=1)
        group = self.edit(
            {"hide_at": "", "hide_at_shown": ""}, hidden=True,
            hide_at=scheduled)

        self.assertEqual(group.hide_at, scheduled)
        self.assertIs(group.hidden, True)

    def test_stale_page_still_saves_the_staff_password(self):
        scheduled = NOW + timedelta(hours=1)
        group = self.edit(
            {"hide_at": "", "hide_at_shown": "", "staff_password": "pw"},
            hidden=True, hide_at=scheduled)

        self.assertEqual(group.hide_at, scheduled)
        self.assertTrue(validate_password(group.staff_password, "pw"))

    def test_changed_time_is_applied(self):
        group = self.edit(
            {"hide_at": "2026-10-10 22:00:00",
             "hide_at_shown": "2026-10-10 20:00:00"},
            hidden=True, hide_at=datetime(2026, 10, 10, 20, 0))

        self.assertEqual(group.hide_at, datetime(2026, 10, 10, 22, 0))

    def rejected(self, form: dict, **stored) -> RankingGroup:
        """Save form over a group and check that the save is refused."""
        group = RankingGroup(name="olim", description="OLIM",
                             staff_password=None, **stored)
        handler = make_form_handler(
            RankingGroupHandler,
            {"name": "olim", "description": "OLIM", **form})
        handler.safe_get_item = MagicMock(return_value=group)

        handler._post_sync("1")

        handler.try_commit.assert_not_called()
        handler.service.add_notification.assert_called_once()
        return group

    def test_edit_rejects_an_invalid_window(self):
        group = self.rejected(
            {"freeze_at": "2026-10-10 19:00:00",
             "unfreeze_at": "2026-10-10 18:00:00"}, hidden=False)

        self.assertIsNone(group.freeze_at)

    def test_edit_rejects_an_unknown_action(self):
        group = self.rejected({"visibility_action": "explode"}, hidden=False)

        self.assertIs(group.hidden, False)

    def test_edit_rejects_freeze_now_on_a_hidden_frozen_group(self):
        stored = NOW - timedelta(hours=1)
        group = self.rejected({"visibility_action": "freeze_now"},
                              hidden=True, hide_at=stored, freeze_at=stored)

        self.assertEqual(group.freeze_at, stored)

    def test_edit_unfreezes_a_hidden_frozen_group(self):
        stored = NOW - timedelta(hours=1)
        group = self.edit({"visibility_action": "unfreeze_now"},
                          hidden=True, hide_at=stored, freeze_at=stored)

        self.assertEqual(group.unfreeze_at, NOW)


class TestUnarchiveRegenerates(unittest.TestCase):

    HOUR = timedelta(hours=1)

    def setUp(self):
        patcher = patch(
            "cms.server.admin.handlers.rankinggroup.make_datetime",
            return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, form: dict, **stored) -> MagicMock:
        group = RankingGroup(name="olim", description="OLIM",
                             staff_password=None, **stored)
        handler = make_form_handler(
            RankingGroupHandler,
            {"name": "olim", "description": "OLIM", **form})
        handler.safe_get_item = MagicMock(return_value=group)
        handler.group = group

        handler._post_sync("1")
        return handler

    def assert_regenerated_once(self, handler):
        proxy = handler.service.proxy_service
        self.assertEqual(
            handler.schedule_rpc.call_args_list,
            [call(proxy.reinitialize),
             call(proxy.regenerate_ranking, group="olim")])

    def assert_not_regenerated(self, handler):
        handler.schedule_rpc.assert_called_once_with(
            handler.service.proxy_service.reinitialize)

    def test_unarchive_button_regenerates(self):
        # Scenario: Unarchive button regenerates
        handler = self.save({"visibility_action": "unarchive"},
                            archive_at=NOW - self.HOUR)
        self.assert_regenerated_once(handler)
        self.assertEqual(handler.group.archive_at, NOW + timedelta(hours=24))

    def test_unarchiving_by_editing_archive_at(self):
        # Scenario: Unarchiving by editing archive_at (a future time, then
        # an empty one)
        stored = NOW - self.HOUR
        for typed in ("2026-10-11 18:00:00", ""):
            with self.subTest(typed):
                handler = self.save(
                    {"archive_at": typed, "archive_at_shown":
                     stored.strftime("%Y-%m-%d %H:%M:%S")},
                    archive_at=stored)
                self.assertFalse(handler.group.is_archived_at(NOW))
                self.assert_regenerated_once(handler)

    def test_a_group_that_stays_as_it_is_is_not_regenerated(self):
        # Scenario: Saving a group that stays as it is
        for stored in (NOW - self.HOUR, NOW + self.HOUR, None):
            with self.subTest(stored):
                handler = self.save({}, archive_at=stored)
                self.assert_not_regenerated(handler)

    def test_archiving_is_not_regenerated(self):
        # Scenario: Saving a group that stays as it is (not archived to
        # archived is not an unarchive either)
        handler = self.save({"visibility_action": "archive_now"},
                            archive_at=NOW + self.HOUR)
        self.assert_not_regenerated(handler)

    def test_a_refused_unarchive_regenerates_nothing(self):
        # Scenario: Unarchive on a group that is not archived
        handler = self.save({"visibility_action": "unarchive"},
                            archive_at=None)
        handler.try_commit.assert_not_called()
        handler.schedule_rpc.assert_not_called()
        handler.service.add_notification.assert_called_once()

    def test_no_regenerate_when_the_commit_fails(self):
        # Scenario: Unarchive button regenerates (only after the commit)
        group = RankingGroup(name="olim", description="OLIM",
                             archive_at=NOW - self.HOUR)
        handler = make_form_handler(
            RankingGroupHandler,
            {"name": "olim", "description": "OLIM",
             "visibility_action": "unarchive"})
        handler.safe_get_item = MagicMock(return_value=group)
        handler.try_commit.return_value = False

        handler._post_sync("1")

        handler.schedule_rpc.assert_not_called()


class TestRenameArchivedGroup(unittest.TestCase):

    def setUp(self):
        patcher = patch(
            "cms.server.admin.handlers.rankinggroup.make_datetime",
            return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rename(self, **stored) -> tuple[RankingGroup, MagicMock]:
        group = RankingGroup(name="olim", description="OLIM",
                             staff_password=None, **stored)
        handler = make_form_handler(
            RankingGroupHandler, {"name": "new", "description": "OLIM"})
        handler.safe_get_item = MagicMock(return_value=group)
        handler._post_sync("1")
        return group, handler

    def test_renaming_an_archived_group_is_refused(self):
        # Scenario: Renaming an archived group
        group, handler = self.rename(archive_at=NOW - timedelta(hours=1))
        self.assertEqual(group.name, "olim")
        handler.try_commit.assert_not_called()
        handler.service.add_notification.assert_called_once()
        self.assertIn("unarchive it before renaming",
                      handler.service.add_notification.call_args.args[2])

    def test_renaming_a_group_that_is_not_archived(self):
        # Scenario: Renaming a group that is not archived
        for archive_at in (None, NOW + timedelta(hours=1)):
            group, handler = self.rename(archive_at=archive_at)
            self.assertEqual(group.name, "new")
            handler.try_commit.assert_called_once()

    def test_unarchiving_and_renaming_in_one_save_is_refused(self):
        # Design decision 6: the state is the stored one, before the edit.
        group = RankingGroup(name="olim", description="OLIM",
                             staff_password=None,
                             archive_at=NOW - timedelta(hours=1))
        handler = make_form_handler(
            RankingGroupHandler,
            {"name": "new", "description": "OLIM",
             "visibility_action": "unarchive"})
        handler.safe_get_item = MagicMock(return_value=group)
        handler._post_sync("1")
        self.assertEqual(group.name, "olim")
        handler.try_commit.assert_not_called()


class TestRankingGroupTemplates(unittest.TestCase):
    """The visibility fields of the pages, and that the password stays out.

    Only the "core" block of each page is rendered: the visibility fields
    live there, and the rest of base.html needs a whole request context.

    """

    # What the database holds for a group that has a staff password.
    STORED = "bcrypt:$2b$04$only-the-stored-hash-marker"

    HOUR = timedelta(hours=1)

    def render_core(self, page: str, **params) -> str:
        template = AWS_ENVIRONMENT.get_template(page)
        params.setdefault(
            "url", lambda *parts: "/" + "/".join(str(p) for p in parts))
        params.setdefault("xsrf_form_html", "")
        params.setdefault("admin", SimpleNamespace(permission_all=True))
        return "".join(template.blocks["core"](template.new_context(params)))

    def render_group(self, **windows) -> str:
        """Render the group page of a group with the given windows."""
        group = RankingGroup(name="olim", description="OLIM", **windows)
        return self.render_core(
            "ranking_group.html", ranking_group=group, group_contests=[],
            view=visibility_view(group, NOW))

    def assert_no_stored_password(self, html: str):
        for leak in ("stored-hash-marker", "$2b$", "bcrypt:"):
            self.assertNotIn(leak, html)
        self.assertNotRegex(html, r'name="staff_password"[^>]*value=')

    def input_tag(self, html: str, name: str) -> str:
        (tag,) = re.findall(r'<input[^>]*name="%s"[^>]*>' % name, html)
        return tag

    def test_group_page_says_whether_a_password_is_set(self):
        group = RankingGroup(name="olim", description="OLIM",
                             hide_at=NOW - self.HOUR,
                             staff_password=self.STORED)

        html = self.render_core(
            "ranking_group.html", ranking_group=group, group_contests=[],
            view=visibility_view(group, NOW))

        self.assertIn("(set)", html)
        self.assertNotIn("(not set)", html)
        self.assertIn('name="remove_staff_password"', html)
        self.assert_no_stored_password(html)

    def test_group_page_of_a_visible_group_without_password(self):
        html = self.render_group()

        self.assertIn("(not set)", html)
        self.assertNotIn("(set)", html)
        self.assertIn("<strong>visible</strong>", html)

    def test_group_page_has_no_hidden_checkbox_any_more(self):
        for html in (self.render_group(),
                     self.render_group(hide_at=NOW - self.HOUR)):
            self.assertNotIn('name="hidden"', html)
            self.assertNotIn('name="hidden_shown"', html)

    def test_group_page_carries_each_time_and_the_value_it_was_rendered_with(
            self):
        windows = {"hide_at": datetime(2026, 10, 10, 12, 0),
                   "show_at": datetime(2026, 10, 10, 20, 0),
                   "freeze_at": datetime(2026, 10, 10, 14, 30, 5),
                   "unfreeze_at": datetime(2026, 10, 10, 15, 0)}

        html = self.render_group(**windows)

        for name, value in windows.items():
            rendered = value.strftime("%Y-%m-%d %H:%M:%S")
            for tag in (self.input_tag(html, name),
                        self.input_tag(html, name + "_shown")):
                self.assertIn('value="%s"' % rendered, tag)
                # A soft reload must not bring back an old value next to a
                # fresh _shown: it would look like a change.
                self.assertIn('autocomplete="off"', tag)
            self.assertIn('type="hidden"',
                          self.input_tag(html, name + "_shown"))

    def test_group_page_carries_empty_times_for_a_group_without_windows(self):
        html = self.render_group()

        for name in ("hide_at", "show_at", "freeze_at", "unfreeze_at"):
            for tag in (self.input_tag(html, name),
                        self.input_tag(html, name + "_shown")):
                self.assertIn('value=""', tag)
                self.assertIn('autocomplete="off"', tag)

    def test_group_page_leaves_the_local_time_to_the_browser(self):
        html = self.render_group(freeze_at=datetime(2026, 10, 10, 20, 0))

        # Only the five typed fields, not their _shown twins, are marked.
        self.assertEqual(html.count("data-utc-time"), 5)
        self.assertNotIn("= 2026-", html)

    def test_group_page_offers_hide_or_show_now(self):
        html = self.render_group()
        self.assertIn("Ocultar ahora", html)
        self.assertIn('name="visibility_action" value="hide_now"', html)
        self.assertNotIn("Mostrar ahora", html)
        self.assertNotIn('value="show_now"', html)

        html = self.render_group(hide_at=NOW - self.HOUR)
        self.assertIn("Mostrar ahora", html)
        self.assertIn('name="visibility_action" value="show_now"', html)
        self.assertNotIn("Ocultar ahora", html)
        self.assertNotIn('value="hide_now"', html)

    def test_enter_in_a_field_submits_update_not_a_now_button(self):
        # Implicit submission uses the first submit button of the form.
        html = self.render_group()

        first = re.search(r'<(?:input type="submit"|button)[^>]*>', html)
        self.assertIn('value="Update"', first.group(0))
        self.assertNotIn("visibility_action", first.group(0))

    def test_group_page_offers_freeze_or_unfreeze_now(self):
        html = self.render_group()
        self.assertIn("Congelar ahora", html)
        self.assertIn('name="visibility_action" value="freeze_now"', html)
        self.assertNotIn("Descongelar ahora", html)

        html = self.render_group(freeze_at=NOW - self.HOUR)
        self.assertIn("Descongelar ahora", html)
        self.assertIn('name="visibility_action" value="unfreeze_now"', html)
        self.assertNotIn("Congelar ahora", html)
        self.assertNotIn('value="freeze_now"', html)

    def test_freeze_button_of_a_hidden_group_follows_the_freeze_window(self):
        # is_frozen_at() is False while hidden, but "Congelar ahora" would
        # move freeze_at to now and publish the scores of the open window
        # once the group is shown.
        html = self.render_group(hide_at=NOW - self.HOUR,
                                 freeze_at=NOW - self.HOUR)

        self.assertIn("Descongelar ahora", html)
        self.assertNotIn("Congelar ahora", html)
        self.assertNotIn('value="freeze_now"', html)
        self.assertIn("Mostrar ahora", html)
        self.assertIn("<strong>oculto (congelado)</strong>", html)

    def test_group_page_marks_the_time_labels_as_utc(self):
        html = self.render_group()

        for _name, label in WINDOW_FIELDS:
            self.assertIn("<td>%s (UTC)</td>" % label, html)

    def test_group_page_shows_the_archive_state_and_field(self):
        # Scenario: An archived group (the page says it and shows archive_at)
        html = self.render_group(archive_at=NOW - self.HOUR)
        self.assertIn("<strong>archivado desde", html)
        for tag in (self.input_tag(html, "archive_at"),
                    self.input_tag(html, "archive_at_shown")):
            self.assertIn('value="2026-10-10 17:00:00"', tag)
            self.assertIn('autocomplete="off"', tag)
        self.assertIn("<td>Archive at (UTC)</td>", html)

        html = self.render_group()
        self.assertIn("<strong>nunca se archiva</strong>", html)

    def test_group_page_offers_archive_now_or_unarchive(self):
        html = self.render_group()
        self.assertIn("Archivar ahora", html)
        self.assertIn('name="visibility_action" value="archive_now"', html)
        self.assertNotIn("Desarchivar", html)

        html = self.render_group(archive_at=NOW - self.HOUR)
        self.assertIn("Desarchivar", html)
        self.assertIn('name="visibility_action" value="unarchive"', html)
        self.assertNotIn("Archivar ahora", html)
        # It sends the whole group again, so it asks first.
        self.assertRegex(
            html, r'value="unarchive"[^>]*onclick="return confirm\(')

    def test_group_page_explains_what_a_now_button_does(self):
        self.assertIn(
            "Un botón «… ahora» reemplaza la hora escrita en ese campo.",
            self.render_group())

    def test_saving_the_page_untouched_changes_nothing(self):
        # What the page posts back, as a browser would, must leave the
        # group as it is: the times keep their microseconds (make_datetime
        # produces them, the page shows whole seconds), and hidden is
        # derived the same way.
        windows_of = {
            "hidden now, frozen later": {
                "hide_at": datetime(2026, 10, 10, 12, 0, 0, 123456),
                "show_at": datetime(2026, 10, 10, 20, 0, 0, 500000),
                "freeze_at": datetime(2026, 10, 10, 18, 30, 5, 999999),
                "unfreeze_at": datetime(2026, 10, 10, 19, 0, 0, 1),
                "archive_at": datetime(2026, 10, 11, 18, 0, 0, 777777)},
            "shown again, frozen now": {
                "hide_at": datetime(2026, 10, 10, 10, 0, 0, 250000),
                "show_at": datetime(2026, 10, 10, 12, 0, 0, 750000),
                "freeze_at": datetime(2026, 10, 10, 13, 0, 0, 500000)},
            "no windows": {},
        }
        for case, windows in windows_of.items():
            with self.subTest(case):
                group = RankingGroup(name="olim", description="OLIM Ω",
                                     staff_password=self.STORED, **windows)
                group.hidden = group.hide_pending_at(NOW)
                expected = group.get_attrs()
                html = self.render_core(
                    "ranking_group.html", ranking_group=group,
                    group_contests=[], view=visibility_view(group, NOW))
                parser = FormFields()
                parser.feed(html)
                self.assertEqual(
                    set(parser.fields),
                    {"name", "description", "staff_password"}
                    | {n for n, _l in WINDOW_FIELDS}
                    | {n + "_shown" for n, _l in WINDOW_FIELDS}
                    | {"archive_at", "archive_at_shown"})
                handler = make_form_handler(RankingGroupHandler,
                                            parser.fields)
                handler.safe_get_item = MagicMock(return_value=group)

                with patch("cms.server.admin.handlers.rankinggroup."
                           "make_datetime", return_value=NOW):
                    handler._post_sync("1")

                handler.service.add_notification.assert_not_called()
                handler.try_commit.assert_called_once()
                self.assertEqual(group.get_attrs(), expected)

    def test_group_page_says_what_changes_next(self):
        html = self.render_group(hide_at=NOW + self.HOUR)

        self.assertIn("<strong>visible</strong> &middot; next: "
                      "Hide from: 2026-10-10 19:00:00 UTC (", html)
        self.assertNotIn("&middot; next:", self.render_group())

    def test_group_page_stops_the_browser_restoring_the_password(self):
        # The password field keeps its own setting.
        self.assertIn(
            'type="password" name="staff_password" '
            'autocomplete="new-password"', self.render_group())

    def test_add_page_has_the_four_empty_times_and_no_actions(self):
        html = self.render_core("add_ranking_group.html",
                                window_fields=WINDOW_FIELDS)

        for name, label in WINDOW_FIELDS:
            tag = self.input_tag(html, name)
            self.assertIn('value=""', tag)
            self.assertIn("%s (UTC)" % label, html)
        self.assertNotIn("_shown", html)
        self.assertNotIn("visibility_action", html)
        self.assertNotIn("<button", html)
        self.assertNotIn('name="hidden"', html)

    def test_add_page_takes_its_time_fields_from_the_handler(self):
        # No list of its own that could drift from WINDOW_FIELDS.
        html = self.render_core(
            "add_ranking_group.html",
            window_fields=(("thaw_at", "Thaw at"),))

        self.assertIn('name="thaw_at"', html)
        self.assertIn("Thaw at (UTC)", html)
        for name, _label in WINDOW_FIELDS:
            self.assertNotIn('name="%s"' % name, html)

    def test_add_page_starts_without_password(self):
        html = self.render_core("add_ranking_group.html",
                                window_fields=WINDOW_FIELDS)

        self.assertIn('type="password" name="staff_password"', html)
        self.assertIn('autocomplete="new-password"', html)
        self.assertNotIn("remove_staff_password", html)
        self.assertNotIn("(set)", html)
        self.assertNotIn("(not set)", html)

    def test_list_shows_the_state_of_each_group_and_the_passwords(self):
        groups = [
            make_group(1, "open"),
            make_group(2, "olim", hide_at=NOW - self.HOUR,
                       staff_password=self.STORED),
            make_group(3, "omips", hide_at=NOW - self.HOUR),
            make_group(4, "frozen", freeze_at=NOW - self.HOUR),
            make_group(5, "both", hide_at=NOW - self.HOUR,
                       freeze_at=NOW - self.HOUR,
                       staff_password=self.STORED),
            make_group(6, "soon", hide_at=NOW + self.HOUR),
        ]

        html = self.render_core(
            "ranking_groups.html", ranking_group_list=groups,
            contest_list=[],
            views={g.id: visibility_view(g, NOW) for g in groups})

        self.assertIn("<th>State</th>", html)
        self.assertIn("<th>Staff password</th>", html)
        self.assertEqual(html.count("<td>visible</td>"), 1)
        self.assertEqual(html.count("<td>oculto</td>"), 1)
        self.assertEqual(html.count(
            "<td>oculto (nobody can see it: no staff password)</td>"), 1)
        self.assertEqual(html.count("<td>congelado</td>"), 1)
        self.assertEqual(html.count("<td>oculto (congelado)</td>"), 1)
        self.assertEqual(html.count(
            "<td>visible &middot; next: Hide from: "
            "2026-10-10 19:00:00 UTC ("), 1)
        self.assertEqual(html.count("<td>set</td>"), 2)
        self.assertEqual(html.count("<td>not set</td>"), 4)
        self.assert_no_stored_password(html)


if __name__ == "__main__":
    unittest.main()
