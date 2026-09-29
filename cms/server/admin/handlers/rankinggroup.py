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

"""Ranking group handlers for AWS.

"""

import asyncio
from datetime import datetime

from sqlalchemy import select, func

from cms.db import Contest, RankingGroup
from cmscommon.crypto import hash_password
from cmscommon.datetime import local_tz, make_datetime, utc
from cmscommon.ranking_groups import RESERVED_GROUP_NAMES, \
    check_window, is_valid_group_name, window_is_open

from .base import BaseHandler, SimpleHandler, parse_datetime, \
    require_permission


def read_ranking_group_attrs(handler: BaseHandler, attrs: dict):
    """Read and validate the ranking group fields of the form.

    handler: the handler whose request carries the form.
    attrs: where to store the name and the description.

    raise (ValueError): if the name is not a valid group name.

    """
    handler.get_string(attrs, "name")
    handler.get_string(attrs, "description")
    name = attrs.get("name")
    if name is None or not is_valid_group_name(name):
        raise ValueError(
            "Invalid ranking group name %r: use only lowercase letters, "
            "digits, '-' and '_', and none of: %s."
            % (name, ", ".join(sorted(RESERVED_GROUP_NAMES))))
    if not attrs.get("description"):
        attrs["description"] = name


# bcrypt only uses the first 72 bytes of a password.
MAX_STAFF_PASSWORD_BYTES = 72


# The time fields of a ranking group: the column and its label.
WINDOW_FIELDS = (
    ("hide_at", "Hide from (UTC)"),
    ("show_at", "Show again at (UTC)"),
    ("freeze_at", "Freeze at (UTC)"),
    ("unfreeze_at", "Unfreeze at (UTC)"),
)
FORM_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_utc(value: datetime | None) -> str:
    """Render a naive UTC datetime as the form shows it.

    value: the time, or None.

    return: the time as YYYY-MM-DD HH:MM:SS, or "" for None.

    """
    return "" if value is None else value.strftime(FORM_TIME_FORMAT)


def read_ranking_group_visibility(handler: BaseHandler, attrs: dict,
                                  now: datetime):
    """Read the visibility fields of the form into attrs.

    Each window time comes with <name>_shown, the value the page was
    rendered with; a time only counts when the organizer changed it, so
    that a stale page does not undo what somebody else scheduled. The
    add form has no _shown fields, and attrs has no current time there:
    its times always count. The visibility_action buttons set a time to
    now. The hidden column is always derived from the windows again,
    whatever the form says, for rollbacks to MC-2 minimal.

    An empty password field keeps attrs["staff_password"] as it is, so
    editing a group without retyping the password keeps it.

    handler: the handler whose request carries the form.
    attrs: the group's current attributes, updated in place.
    now: the current time, naive UTC.

    raise (ValueError): on a malformed time, an invalid window, an
        action that does not apply, or an invalid staff password.

    """
    for name, _label in WINDOW_FIELDS:
        raw = handler.get_argument(name, None)
        if raw is None:
            continue
        raw = raw.strip()
        shown = handler.get_argument(name + "_shown", None)
        if shown is not None and raw == shown.strip() and name in attrs:
            continue
        attrs[name] = parse_datetime(raw) if raw else None

    action = handler.get_argument("visibility_action", None)
    if action == "hide_now":
        attrs["hide_at"] = now
        if attrs.get("show_at") is not None and attrs["show_at"] <= now:
            attrs["show_at"] = None
    elif action == "show_now":
        if not window_is_open(attrs.get("hide_at"), attrs.get("show_at"),
                              now):
            raise ValueError("The ranking is not hidden.")
        attrs["show_at"] = now
    elif action == "freeze_now":
        # The raw window, also while the group is hidden: freezing then
        # would move freeze_at to now, and publish the scores of the
        # window that is open as soon as the group is shown.
        if window_is_open(attrs.get("freeze_at"), attrs.get("unfreeze_at"),
                          now):
            raise ValueError("The ranking is already frozen.")
        attrs["freeze_at"] = now
        if attrs.get("unfreeze_at") is not None \
                and attrs["unfreeze_at"] <= now:
            attrs["unfreeze_at"] = None
    elif action == "unfreeze_now":
        if not window_is_open(attrs.get("freeze_at"),
                              attrs.get("unfreeze_at"), now):
            raise ValueError("The ranking is not frozen.")
        attrs["unfreeze_at"] = now
    elif action is not None:
        raise ValueError("Unknown action %r." % (action,))

    check_window(attrs.get("hide_at"), attrs.get("show_at"), "hide")
    check_window(attrs.get("freeze_at"), attrs.get("unfreeze_at"),
                 "freeze")
    # The column is rewritten on every save, also when hide_at is
    # cleared: the migration hides a row with hidden set and no hide_at,
    # so a stale value would bring the hide back. The group is only there
    # to reuse hide_pending_at, and is never added to a session.
    attrs["hidden"] = RankingGroup(
        name="", description="",
        **{name: attrs.get(name) for name, _label in WINDOW_FIELDS}
    ).hide_pending_at(now)

    new_password = handler.get_argument("staff_password", "", strip=False)
    remove = handler.get_argument(
        "remove_staff_password", None) is not None
    if new_password and remove:
        raise ValueError(
            "Set a new staff password or remove it, not both.")
    if remove:
        attrs["staff_password"] = None
    elif new_password:
        if len(new_password.encode("utf-8")) > MAX_STAFF_PASSWORD_BYTES:
            raise ValueError(
                "The staff password is too long (at most %d bytes)."
                % MAX_STAFF_PASSWORD_BYTES)
        attrs["staff_password"] = hash_password(new_password, "bcrypt")


def visibility_view(group: RankingGroup, now: datetime) -> dict:
    """Prepare what the group page shows about its visibility.

    group: the ranking group.
    now: the current time, naive UTC.

    return: the fields (name, label, UTC value, local value), whether it
        is hidden or frozen now, a one-line summary and the next change.
        Frozen follows the freeze window alone, also while the group is
        hidden, unlike RankingGroup.is_frozen_at.

    """
    def local(value: datetime | None) -> str:
        if value is None:
            return ""
        return value.replace(tzinfo=utc).astimezone(local_tz).strftime(
            "%Y-%m-%d %H:%M %Z")

    fields = [{"name": name, "label": label,
               "utc": format_utc(getattr(group, name)),
               "local": local(getattr(group, name))}
              for name, label in WINDOW_FIELDS]
    hidden = group.is_hidden_at(now)
    frozen = window_is_open(group.freeze_at, group.unfreeze_at, now)
    if hidden:
        summary = "oculto (congelado)" if frozen else "oculto"
    else:
        summary = "congelado" if frozen else "visible"
    upcoming = sorted((getattr(group, name), label)
                      for name, label in WINDOW_FIELDS
                      if getattr(group, name) is not None
                      and getattr(group, name) > now)
    next_change = "" if not upcoming else "%s: %s" % (
        upcoming[0][1], local(upcoming[0][0]))
    return {"fields": fields, "hidden_now": hidden, "frozen_now": frozen,
            "summary": summary, "next_change": next_change}


class RankingGroupListHandler(SimpleHandler("ranking_groups.html")):
    """Get returns the list of all ranking groups, post perform
    operations on a specific group (removing it).

    """

    REMOVE = "Remove"

    def _get_sync(self):
        self.r_params = self.render_params()
        now = make_datetime()
        self.r_params["views"] = {
            group.id: visibility_view(group, now)
            for group in self.r_params["ranking_group_list"]}
        self.render("ranking_groups.html", **self.r_params)

    def _post_sync(self):
        group_id: str = self.get_argument("ranking_group_id")
        operation: str = self.get_argument("operation")

        if operation == self.REMOVE:
            self.redirect(self.url("ranking_groups", group_id, "remove"))
        else:
            self.service.add_notification(
                make_datetime(), "Invalid operation %s" % operation, "")
            self.redirect(self.url("ranking_groups"))

    @require_permission(BaseHandler.AUTHENTICATED)
    async def post(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)


class AddRankingGroupHandler(
        SimpleHandler("add_ranking_group.html", permission_all=True)):
    def _post_sync(self):
        fallback_page = self.url("ranking_groups", "add")

        try:
            attrs = dict()
            read_ranking_group_attrs(self, attrs)
            read_ranking_group_visibility(self, attrs, make_datetime())
            self.sql_session.add(RankingGroup(**attrs))

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.schedule_rpc(self.service.proxy_service.reinitialize)
            self.redirect(self.url("ranking_groups"))
        else:
            self.redirect(fallback_page)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)


class RankingGroupHandler(BaseHandler):
    """Show and edit a single ranking group.

    """
    def _get_sync(self, group_id: str):
        group = self.safe_get_item(RankingGroup, group_id)

        self.r_params = self.render_params()
        self.r_params["ranking_group"] = group
        self.r_params["view"] = visibility_view(group, make_datetime())
        self.r_params["group_contests"] = self.sql_session.execute(
            select(Contest)
            .filter(Contest.ranking_group_id == group.id)
            .order_by(Contest.name)
        ).scalars().all()
        self.render("ranking_group.html", **self.r_params)

    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self, group_id: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, group_id)

    def _post_sync(self, group_id: str):
        fallback_page = self.url("ranking_group", group_id)

        group = self.safe_get_item(RankingGroup, group_id)

        try:
            attrs = group.get_attrs()
            read_ranking_group_attrs(self, attrs)
            read_ranking_group_visibility(self, attrs, make_datetime())
            group.set_attrs(attrs)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            # A rename moves the ranking to a new namespace.
            self.schedule_rpc(self.service.proxy_service.reinitialize)
        self.redirect(fallback_page)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self, group_id: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync, group_id)


class RemoveRankingGroupHandler(BaseHandler):
    """Get returns a page asking for confirmation, delete actually
    removes the ranking group (its contests are kept, without group).

    """

    def _get_sync(self, group_id: str):
        group = self.safe_get_item(RankingGroup, group_id)

        self.r_params = self.render_params()
        self.r_params["ranking_group"] = group
        self.r_params["contest_count"] = self.sql_session.execute(
            select(func.count()).select_from(Contest)
            .filter(Contest.ranking_group_id == group.id)
        ).scalar_one()
        self.render("ranking_group_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self, group_id: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, group_id)

    def _delete_sync(self, group_id: str):
        group = self.safe_get_item(RankingGroup, group_id)

        self.sql_session.delete(group)
        if self.try_commit():
            self.schedule_rpc(self.service.proxy_service.reinitialize)

        # Maybe they'll want to do this again (for another group)
        self.write("../../ranking_groups")

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def delete(self, group_id: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._delete_sync, group_id)


class RegenerateRankingHandler(BaseHandler):
    """Empty a ranking (a group, or the root) and send it again.

    """

    def _post_sync(self):
        group: str = self.get_argument("group", "")
        self.schedule_rpc(self.service.proxy_service.regenerate_ranking,
                          group=group or None)
        self.service.add_notification(
            make_datetime(), "Ranking regeneration requested",
            "Ranking %s is being emptied and sent again."
            % (group or "root"))
        self.redirect(self.url("ranking_groups"))

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)
