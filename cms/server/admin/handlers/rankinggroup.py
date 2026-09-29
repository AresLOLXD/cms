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
from sqlalchemy import select, func

from cms.db import Contest, RankingGroup
from cmscommon.crypto import hash_password
from cmscommon.datetime import make_datetime
from cmscommon.ranking_groups import RESERVED_GROUP_NAMES, \
    is_valid_group_name

from .base import BaseHandler, SimpleHandler, require_permission


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


def read_ranking_group_visibility(handler: BaseHandler, attrs: dict):
    """Read the visibility fields of the form (MC-2) into attrs.

    An empty password field keeps attrs["staff_password"] as it is, so
    editing a group without retyping the password keeps it.

    handler: the handler whose request carries the form.
    attrs: where to store hidden and staff_password.

    raise (ValueError): if a new password and its removal are both
        requested, or if the new password is too long.

    """
    handler.get_bool(attrs, "hidden")
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


class RankingGroupListHandler(SimpleHandler("ranking_groups.html")):
    """Get returns the list of all ranking groups, post perform
    operations on a specific group (removing it).

    """

    REMOVE = "Remove"

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
            read_ranking_group_visibility(self, attrs)
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
            read_ranking_group_visibility(self, attrs)
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
