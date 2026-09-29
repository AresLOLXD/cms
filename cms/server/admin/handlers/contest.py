#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2016 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2015 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
# Copyright © 2016 Amir Keivan Mohtashami <akmohtashami97@gmail.com>
# Copyright © 2018 William Di Luigi <williamdiluigi@gmail.com>
# Copyright © 2026 Tobias Lenz <t_lenz94@web.de>
# Copyright © 2026 Chuyang Wang <mail@chuyang-wang.de>
# Copyright © 2026 Jonathan Baumann <Jonathan.Baumann@edu.ruhr-uni-bochum.de>
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

"""Contest-related handlers for AWS.

"""

import asyncio

from sqlalchemy import select

from cms import ServiceCoord, get_service_shards, get_service_address
from cms.db import Contest, Participation, Group, RankingGroup, Submission
from cmscommon.datetime import make_datetime

from .base import BaseHandler, SimpleContestHandler, SimpleHandler, \
    require_permission


class AddContestHandler(
        SimpleHandler("add_contest.html", permission_all=True)):
    """Adds a new contest.

    """
    def _post_sync(self):
        fallback_page = self.url("contests", "add")

        try:
            attrs = dict()

            self.get_string(attrs, "name", empty=None)
            assert attrs.get("name") is not None, "No contest name specified."
            attrs["description"] = attrs["name"]

            # Create the contest.
            contest = Contest(**attrs)

            # Add the default group
            group = Group(name="default")
            contest.groups.append(group)
            contest.main_group = group

            self.sql_session.add(contest)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            # Create the contest on RWS.
            self.schedule_rpc(self.service.proxy_service.reinitialize)
            self.redirect(self.url("contest", contest.id))
        else:
            self.redirect(fallback_page)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)


class ContestHandler(SimpleContestHandler("contest.html")):
    def _post_sync(self, contest_id: str):
        contest = self.safe_get_item(Contest, contest_id)

        try:
            attrs = contest.get_attrs()

            self.get_string(attrs, "name", empty=None)
            self.get_string(attrs, "description")

            assert attrs.get("name") is not None, "No contest name specified."

            allowed_localizations: str = self.get_argument("allowed_localizations", "")
            if allowed_localizations:
                attrs["allowed_localizations"] = \
                    [x.strip() for x in allowed_localizations.split(",")
                     if len(x) > 0 and not x.isspace()]
            else:
                attrs["allowed_localizations"] = []

            attrs["languages"] = self.get_arguments("languages")

            self.get_bool(attrs, "submissions_download_allowed")
            self.get_bool(attrs, "allow_questions")
            self.get_bool(attrs, "allow_user_tests")
            self.get_bool(attrs, "allow_unofficial_submission_before_analysis_mode")
            self.get_bool(attrs, "block_hidden_participations")
            self.get_bool(attrs, "allow_password_authentication")
            self.get_bool(attrs, "allow_registration")
            self.get_bool(attrs, "ip_restriction")
            self.get_bool(attrs, "ip_autologin")
            self.get_bool(attrs, "active")
            self.get_int(attrs, "ranking_group_id")
            if attrs["ranking_group_id"] is not None and \
                    RankingGroup.get_from_id(
                        attrs["ranking_group_id"], self.sql_session) is None:
                raise ValueError("Unknown ranking group.")

            self.get_string(attrs, "token_mode")
            self.get_int(attrs, "token_max_number")
            self.get_timedelta_sec(attrs, "token_min_interval")
            self.get_int(attrs, "token_gen_initial")
            self.get_int(attrs, "token_gen_number")
            self.get_timedelta_min(attrs, "token_gen_interval")
            self.get_int(attrs, "token_gen_max")

            self.get_int(attrs, "max_submission_number")
            self.get_int(attrs, "max_user_test_number")
            self.get_timedelta_sec(attrs, "min_submission_interval")
            self.get_timedelta_sec(attrs, "min_submission_interval_grace_period")
            self.get_timedelta_sec(attrs, "min_user_test_interval")

            self.get_string(attrs, "timezone", empty=None)
            self.get_int(attrs, "score_precision")

            self.get_group_settings(contest.main_group)
            # Update the contest.
            contest.set_attrs(attrs)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s).", repr(error))
            self.redirect(self.url("contest", contest_id))
            return

        if self.try_commit():
            # Update the contest on RWS.
            self.schedule_rpc(self.service.proxy_service.reinitialize)
        self.redirect(self.url("contest", contest_id))

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self, contest_id: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync, contest_id)


class OverviewHandler(BaseHandler):
    """Home page handler, with queue and workers statuses.

    """
    def _get_sync(self, contest_id: str | None = None):
        if contest_id is not None:
            self.contest = self.safe_get_item(Contest, contest_id)

        self.r_params = self.render_params()
        self.render("overview.html", **self.r_params)

    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self, contest_id: str | None = None):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, contest_id)


class ResourcesListHandler(BaseHandler):
    def _get_sync(self, contest_id: str | None = None):
        if contest_id is not None:
            self.contest = self.safe_get_item(Contest, contest_id)

        self.r_params = self.render_params()
        self.r_params["resource_addresses"] = {}
        services = get_service_shards("ResourceService")
        for i in range(services):
            self.r_params["resource_addresses"][i] = get_service_address(
                ServiceCoord("ResourceService", i)).ip
        self.render("resourceslist.html", **self.r_params)

    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self, contest_id: str | None = None):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, contest_id)


class ContestListHandler(SimpleHandler("contests.html")):
    """Get returns the list of all contests, post perform operations on
    a specific contest (removing them from CMS).

    """

    REMOVE = "Remove"

    def _post_sync(self):
        contest_id = self.get_argument("contest_id")
        operation = self.get_argument("operation")

        if operation == self.REMOVE:
            asking_page = self.url("contests", contest_id, "remove")
            # Open asking for remove page
            self.redirect(asking_page)
        else:
            self.service.add_notification(
                make_datetime(), "Invalid operation %s" % operation, "")
            self.redirect(self.url("contests"))

    @require_permission(BaseHandler.AUTHENTICATED)
    async def post(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)


class RemoveContestHandler(BaseHandler):
    """Get returns a page asking for confirmation, delete actually removes
    the contest from CMS.

    """

    def _get_sync(self, contest_id):
        contest = self.safe_get_item(Contest, contest_id)
        submission_query = select(Submission)\
            .join(Submission.participation)\
            .filter(Participation.contest == contest)

        self.contest = contest
        self.render_params_for_remove_confirmation(submission_query)
        self.render("contest_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self, contest_id):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, contest_id)

    def _delete_sync(self, contest_id):
        contest = self.safe_get_item(Contest, contest_id)

        self.sql_session.delete(contest)
        if self.try_commit():
            self.schedule_rpc(self.service.proxy_service.reinitialize)

        # Maybe they'll want to do this again (for another contest)
        self.write("../../contests")

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def delete(self, contest_id):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._delete_sync, contest_id)
