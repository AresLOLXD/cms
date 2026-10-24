#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2015 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2016 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
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

"""Non-categorized handlers for AWS.

"""

import asyncio
import json
import logging

from sqlalchemy import or_, select

from cms import ServiceCoord, get_service_shards, get_service_address
from cms.db import Admin, Contest, Participation, Question
from cms.server.jinja2_toolbox import markdown_filter
from cms.server.util import normalize_login_next_page
from cmscommon.crypto import validate_password
from cmscommon.datetime import make_datetime, make_timestamp
from .base import BaseHandler, SimpleHandler, require_permission


logger = logging.getLogger(__name__)


class LoginHandler(SimpleHandler("login.html", authenticated=False)):
    """Login handler.

    """
    def render_params(self) -> dict:
        """Return the render params of the login page.

        The page doesn't use the lists of the sidebar, and the health
        check of the container requests it every few seconds, so they
        are not loaded.

        return: the render params of the login page.

        """
        params = self._base_render_params(None)
        if self.current_user is not None:
            params["admin"] = self.current_user
        return params

    async def post(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)

    def _post_sync(self):
        error_args = {"login_error": "true"}
        next_page: str = self.get_argument("next", None)
        if next_page is not None:
            error_args["next"] = next_page
        next_page = normalize_login_next_page(next_page, self.url, self.url())
        error_page = self.url("login", **error_args)

        username: str = self.get_argument("username", "")
        password: str = self.get_argument("password", "")
        admin: Admin | None = self.sql_session.execute(
            select(Admin).filter(Admin.username == username)
        ).scalars().first()

        if admin is None:
            logger.warning("Nonexistent admin account: %s", username)
            self.redirect(error_page)
            return

        try:
            allowed = validate_password(admin.authentication, password)
        except ValueError:
            logger.warning("Unable to validate password for admin %r", username,
                           exc_info=True)
            allowed = False

        if not allowed or not admin.enabled:
            if not allowed:
                logger.info("Login error for admin %r from IP %s.", username,
                            self.request.remote_ip)
            elif not admin.enabled:
                logger.info("Login successful for admin %r from IP %s, but "
                            "account is disabled.", username,
                            self.request.remote_ip)
            self.redirect(error_page)
            return

        logger.info("Admin logged in: %r from IP %s.", username,
                    self.request.remote_ip)
        self._set_admin_session(admin.id)
        self.redirect(next_page)


class LogoutHandler(BaseHandler):
    """Logout handler.

    """
    async def post(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)

    def _post_sync(self):
        self.clear_cookie(self.COOKIE_NAME)
        self.redirect(self.url())


class ResourcesHandler(BaseHandler):
    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self, shard=None, contest_id=None):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, shard, contest_id)

    def _get_sync(self, shard=None, contest_id=None):
        if contest_id is not None:
            self.contest = self.safe_get_item(Contest, contest_id)
            contest_address = [contest_id]
        else:
            contest_address = []

        if shard is None:
            shard = "all"

        self.r_params = self.render_params()
        self.r_params["resource_shards"] = \
            get_service_shards("ResourceService")
        self.r_params["resource_addresses"] = {}
        if shard == "all":
            for i in range(self.r_params["resource_shards"]):
                self.r_params["resource_addresses"][i] = get_service_address(
                    ServiceCoord("ResourceService", i)).ip
        else:
            shard = int(shard)
            try:
                address = get_service_address(
                    ServiceCoord("ResourceService", shard))
            except KeyError:
                self.redirect(self.url(*(["resourceslist"] + contest_address)))
                return
            self.r_params["resource_addresses"][shard] = address.ip

        self.render("resources.html", **self.r_params)


class NotificationsHandler(BaseHandler):
    """Displays notifications.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync)

    def _get_sync(self):
        res = []
        last_notification = make_datetime(
            float(self.get_argument("last_notification", "0")))

        query = (
            select(Question, Participation.contest_id)
            .join(Participation, Question.participation_id == Participation.id)
            .filter(Question.reply_timestamp.is_(None))
            .filter(Question.question_timestamp > last_notification))

        # A contest page asks for its own questions and those of the
        # contests sent to the same ranking group; a page without contest
        # asks for all. An id that is not a number or not a contest gets an
        # empty list, not a 404: a stale tab polls forever.
        contest_id = self.get_argument("contest_id", None)
        if contest_id is not None:
            try:
                contest_id = int(contest_id)
            except ValueError:
                contest_id = -1  # No contest has this id.
            # The NULL group of an ungrouped contest stays in SQL, where it
            # matches nothing; compared in Python, it would match every
            # ungrouped contest.
            group_id = select(Contest.ranking_group_id) \
                .where(Contest.id == contest_id).scalar_subquery()
            query = query.filter(Participation.contest_id.in_(
                select(Contest.id).where(or_(
                    Contest.id == contest_id,
                    Contest.ranking_group_id == group_id))))

        for question, question_contest_id in \
                self.sql_session.execute(query).all():
            res.append({
                "type": "new_question",
                "timestamp": make_timestamp(question.question_timestamp),
                "subject": question.subject,
                "text": question.text,
                "contest_id": question_contest_id
            })

        # Simple notifications
        for notification in self.service.take_notifications():
            res.append({"type": "notification",
                        "timestamp": make_timestamp(notification[0]),
                        "subject": notification[1],
                        "text": notification[2]})

        self.write(json.dumps(res))

class MarkdownRenderHandler(BaseHandler):
    """Renders Markdown for AWS message previews."""

    @require_permission(BaseHandler.AUTHENTICATED)
    async def post(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)

    def _post_sync(self):
        data = self.get_argument("input")
        rendered = markdown_filter(data)
        self.write(rendered)

