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

from sqlalchemy import select, func

from cms.db import Contest, RankingGroup
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


class RankingGroupListHandler(SimpleHandler("ranking_groups.html")):
    """Get returns the list of all ranking groups, post perform
    operations on a specific group (removing it).

    """

    REMOVE = "Remove"

    @require_permission(BaseHandler.AUTHENTICATED)
    def post(self):
        group_id: str = self.get_argument("ranking_group_id")
        operation: str = self.get_argument("operation")

        if operation == self.REMOVE:
            self.redirect(self.url("ranking_groups", group_id, "remove"))
        else:
            self.service.add_notification(
                make_datetime(), "Invalid operation %s" % operation, "")
            self.redirect(self.url("ranking_groups"))


class AddRankingGroupHandler(
        SimpleHandler("add_ranking_group.html", permission_all=True)):
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback_page = self.url("ranking_groups", "add")

        try:
            attrs = dict()
            read_ranking_group_attrs(self, attrs)
            self.sql_session.add(RankingGroup(**attrs))

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.service.proxy_service.reinitialize()
            self.redirect(self.url("ranking_groups"))
        else:
            self.redirect(fallback_page)


class RankingGroupHandler(BaseHandler):
    """Show and edit a single ranking group.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, group_id: str):
        group = self.safe_get_item(RankingGroup, group_id)

        self.r_params = self.render_params()
        self.r_params["ranking_group"] = group
        self.r_params["group_contests"] = self.sql_session.execute(
            select(Contest)
            .filter(Contest.ranking_group_id == group.id)
            .order_by(Contest.name)
        ).scalars().all()
        self.render("ranking_group.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, group_id: str):
        fallback_page = self.url("ranking_group", group_id)

        group = self.safe_get_item(RankingGroup, group_id)

        try:
            attrs = group.get_attrs()
            read_ranking_group_attrs(self, attrs)
            group.set_attrs(attrs)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            # A rename moves the ranking to a new namespace.
            self.service.proxy_service.reinitialize()
        self.redirect(fallback_page)


class RemoveRankingGroupHandler(BaseHandler):
    """Get returns a page asking for confirmation, delete actually
    removes the ranking group (its contests are kept, without group).

    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, group_id: str):
        group = self.safe_get_item(RankingGroup, group_id)

        self.r_params = self.render_params()
        self.r_params["ranking_group"] = group
        self.r_params["contest_count"] = self.sql_session.execute(
            select(func.count()).select_from(Contest)
            .filter(Contest.ranking_group_id == group.id)
        ).scalar_one()
        self.render("ranking_group_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, group_id: str):
        group = self.safe_get_item(RankingGroup, group_id)

        self.sql_session.delete(group)
        if self.try_commit():
            self.service.proxy_service.reinitialize()

        # Maybe they'll want to do this again (for another group)
        self.write("../../ranking_groups")


class RegenerateRankingHandler(BaseHandler):
    """Empty a ranking (a group, or the root) and send it again.

    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        group: str = self.get_argument("group", "")
        self.service.proxy_service.regenerate_ranking(group=group or None)
        self.service.add_notification(
            make_datetime(), "Ranking regeneration requested",
            "Ranking %s is being emptied and sent again."
            % (group or "root"))
        self.redirect(self.url("ranking_groups"))
