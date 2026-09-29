#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2017 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2015 William Di Luigi <williamdiluigi@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
# Copyright © 2016 Peyman Jabbarzade Ganje <peyman.jabarzade@gmail.com>
# Copyright © 2017 Valentin Rosca <rosca.valentin2012@gmail.com>
# Copyright © 2021 Manuel Gundlach <manuel.gundlach@gmail.com>
# Copyright © 2026 Tobias Lenz <t_lenz94@web.de>
# Copyright © 2026 Chuyang Wang <mail@chuyang-wang.de>
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

"""User-related handlers for AWS for a specific contest.

"""

import asyncio
import json
import logging

import collections
try:
    collections.MutableMapping
except:
    # Monkey-patch: Tornado 4.5.3 does not work on Python 3.11 by default
    collections.MutableMapping = collections.abc.MutableMapping

import tornado.web
from sqlalchemy import select

from cms.db import Contest, Group, Message, Participation, Submission, User, \
    Team
from cms.server.admin.bulkimport import FIELDS, plan_import, read_rows
from cms.server.admin.importjobs import IMPORT_JOBS
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


class ContestUsersHandler(BaseHandler):
    REMOVE_FROM_CONTEST = "Remove from contest"

    def _get_sync(self, contest_id):
        self.contest = self.safe_get_item(Contest, contest_id)

        self.r_params = self.render_params()
        self.r_params["contest"] = self.contest
        assigned_user_ids = self.sql_session.execute(
            select(Participation.user_id)
            .filter(Participation.contest == self.contest)
        ).scalars().all()
        self.r_params["unassigned_users"] = self.sql_session.execute(
            select(User).filter(User.id.notin_(assigned_user_ids))
        ).scalars().all()
        self.render("contest_users.html", **self.r_params)

    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self, contest_id):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, contest_id)

    def _post_sync(self, contest_id):
        fallback_page = self.url("contest", contest_id, "users")

        try:
            user_id = self.get_argument("user_id")
            operation = self.get_argument("operation")
            assert operation in (
                self.REMOVE_FROM_CONTEST,
            ), "Please select a valid operation"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if operation == self.REMOVE_FROM_CONTEST:
            asking_page = \
                self.url("contest", contest_id, "user", user_id, "remove")
            # Open asking for remove page
            self.redirect(asking_page)
            return

        self.redirect(fallback_page)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self, contest_id):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync, contest_id)


class RemoveParticipationHandler(BaseHandler):
    """Get returns a page asking for confirmation, delete actually removes
    the participation from the contest.

    """

    def _get_sync(self, contest_id, user_id):
        self.contest = self.safe_get_item(Contest, contest_id)
        user = self.safe_get_item(User, user_id)
        participation: Participation = self.sql_session.execute(
            select(Participation)
            .filter(Participation.contest_id == contest_id)
            .filter(Participation.user_id == user_id)
        ).scalars().first()
        # Check that the participation is valid.
        if participation is None:
            raise tornado.web.HTTPError(404)

        submission_query = select(Submission)\
            .filter(Submission.participation == participation)
        self.render_params_for_remove_confirmation(submission_query)

        self.r_params["user"] = user
        self.r_params["contest"] = self.contest
        self.render("participation_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self, contest_id, user_id):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, contest_id, user_id)

    def _delete_sync(self, contest_id, user_id):
        self.contest = self.safe_get_item(Contest, contest_id)
        user = self.safe_get_item(User, user_id)

        participation: Participation = self.sql_session.execute(
            select(Participation)
            .filter(Participation.user == user)
            .filter(Participation.contest == self.contest)
        ).scalars().first()

        # Unassign the user from the contest.
        self.sql_session.delete(participation)

        if self.try_commit():
            # Remove the participation on RWS.
            self.schedule_rpc(self.service.proxy_service.reinitialize)

        # Maybe they'll want to do this again (for another participation)
        self.write("../../users")

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def delete(self, contest_id, user_id):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._delete_sync, contest_id, user_id)


class AddContestUserHandler(BaseHandler):
    def _post_sync(self, contest_id):
        fallback_page = self.url("contest", contest_id, "users")

        self.contest = self.safe_get_item(Contest, contest_id)

        try:
            user_id: str = self.get_argument("user_id")
            assert user_id != "null", "Please select a valid user"
            group_id: str = self.get_argument("group_id")
            assert group_id != "null", "Please select a valid group"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        user = self.safe_get_item(User, user_id)
        group = self.safe_get_item(Group, group_id)

        # Create the participation.
        participation = Participation(contest=self.contest, user=user,
                                      group=group)
        self.sql_session.add(participation)

        if self.try_commit():
            # Create the user on RWS.
            self.schedule_rpc(self.service.proxy_service.reinitialize)

        # Maybe they'll want to do this again (for another user)
        self.redirect(fallback_page)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self, contest_id):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync, contest_id)


class ParticipationHandler(BaseHandler):
    """Shows the details of a single user in a contest: submissions,
    questions, messages (and allows to send the latters).

    """
    def _get_sync(self, contest_id, user_id):
        self.contest = self.safe_get_item(Contest, contest_id)
        participation: Participation = self.sql_session.execute(
            select(Participation)
            .filter(Participation.contest_id == contest_id)
            .filter(Participation.user_id == user_id)
        ).scalars().first()

        # Check that the participation is valid.
        if participation is None:
            raise tornado.web.HTTPError(404)

        submission_query = select(Submission)\
            .filter(Submission.participation == participation)
        page = int(self.get_query_argument("page", 0))
        self.render_params_for_submissions(submission_query, page)

        self.r_params["participation"] = participation
        self.r_params["selected_user"] = participation.user
        self.r_params["teams"] = self.sql_session.execute(
            select(Team)).scalars().all()
        self.render("participation.html", **self.r_params)

    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self, contest_id, user_id):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, contest_id, user_id)

    def _post_sync(self, contest_id, user_id):
        fallback_page = \
            self.url("contest", contest_id, "user", user_id, "edit")

        self.contest = self.safe_get_item(Contest, contest_id)
        participation: Participation = self.sql_session.execute(
            select(Participation)
            .filter(Participation.contest_id == contest_id)
            .filter(Participation.user_id == user_id)
        ).scalars().first()

        # Check that the participation is valid.
        if participation is None:
            raise tornado.web.HTTPError(404)

        try:
            attrs = participation.get_attrs()

            self.get_password(attrs, participation.password, True)

            self.get_ip_networks(attrs, "ip")
            self.get_datetime(attrs, "starting_time")
            self.get_timedelta_sec(attrs, "delay_time")
            self.get_timedelta_sec(attrs, "extra_time")
            self.get_bool(attrs, "hidden")
            self.get_bool(attrs, "unrestricted")

            # Update the participation.
            participation.set_attrs(attrs)

            # Update the group of the participant
            group_id = self.get_argument("group_id")
            participation.group = self.safe_get_item(Group, group_id)

            # Update the team
            self.get_string(attrs, "team")
            team_code = attrs["team"]

            if team_code:  # If a team code is provided
                team: Team | None = self.sql_session.execute(
                    select(Team).filter(Team.code == team_code)
                ).scalars().first()
                if team is None:
                    raise ValueError(f"Team with code '{team_code}' does not exist")
                participation.team = team
            else:  # If no team code is provided, set to None
                participation.team = None

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            # Update the user on RWS.
            self.schedule_rpc(self.service.proxy_service.reinitialize)
        self.redirect(fallback_page)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self, contest_id, user_id):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync, contest_id, user_id)


class MessageHandler(BaseHandler):
    """Called when a message is sent to a specific user.

    """

    def _post_sync(self, contest_id, user_id):
        user = self.safe_get_item(User, user_id)
        self.contest = self.safe_get_item(Contest, contest_id)
        participation: Participation | None = self.sql_session.execute(
            select(Participation)
            .filter(Participation.contest == self.contest)
            .filter(Participation.user == user)
        ).scalars().first()

        # check that the participation is valid
        if participation is None:
            raise tornado.web.HTTPError(404)

        message = Message(make_datetime(),
                          self.get_argument("message_subject", ""),
                          self.get_argument("message_text", ""),
                          participation=participation,
                          admin=self.current_user)
        self.sql_session.add(message)
        if self.try_commit():
            logger.info("Message submitted to user %s in contest %s.",
                        user.username, self.contest.name)

        self.redirect(self.url("contest", contest_id, "user", user_id, "edit"))

    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    async def post(self, contest_id, user_id):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync, contest_id, user_id)


class ImportUsersHandler(BaseHandler):
    """Bulk import of users and participations into a contest (CSV).

    GET shows the form, or the progress of the job given by ?job=. POST
    reads the file: "validate" only reports what an import would do,
    "import" starts the job and redirects to its progress page.

    """

    def _render_page(self, mapping: dict[str, str] | None = None,
                     errors: list[str] | None = None,
                     summary: dict[str, int] | None = None, job=None):
        """Render the page.

        The template gets every one of these parameters, as AWS renders
        with StrictUndefined.

        mapping: import field -> header the admin chose for it, so that
            the choice survives the page being shown again.
        errors: why nothing was imported, to list them.
        summary: what an import of the file would do, after "validate".
        job: the import job whose progress to show, instead of the form.

        """
        self.r_params = self.render_params()
        self.r_params["contest"] = self.contest
        self.r_params["fields"] = FIELDS
        self.r_params["mapping"] = mapping or {}
        self.r_params["errors"] = errors or []
        self.r_params["summary"] = summary
        self.r_params["job"] = job
        self.render("contest_users_import.html", **self.r_params)

    def _get_sync(self, contest_id):
        self.contest = self.safe_get_item(Contest, contest_id)
        job_id = self.get_argument("job", None)
        if job_id is None:
            self._render_page()
            return
        job = IMPORT_JOBS.get(job_id, self.current_user.id)
        if job is None or job.contest_id != self.contest.id:
            self._render_page(errors=["la importación no existe o ya expiró"])
            return
        self._render_page(job=job)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self, contest_id):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, contest_id)

    def _post_sync(self, contest_id):
        self.contest = self.safe_get_item(Contest, contest_id)
        mapping = {field: self.get_argument("map_" + field, "")
                   for field in FIELDS}
        files = self.request.files.get("file")
        if not files:
            self._render_page(mapping, errors=["elige un archivo CSV"])
            return
        rows, errors = read_rows(files[0]["body"], mapping)
        plan = None
        if not errors:
            plan, errors = plan_import(self.sql_session, self.contest.id,
                                       rows)
        if errors:
            self._render_page(mapping, errors=errors)
            return
        if self.get_argument("action", "validate") != "import":
            self._render_page(mapping, summary=plan.summary())
            return
        service = self.service

        def on_done():
            # Called from the job's thread, once the import is saved.
            self.schedule_rpc(service.proxy_service.reinitialize)

        try:
            job = IMPORT_JOBS.start(self.current_user.id, self.contest.id,
                                    rows, on_done)
        except ValueError as error:
            self._render_page(mapping, errors=[str(error)])
            return
        self.redirect(self.url("contest", self.contest.id, "users",
                               "import") + "?job=" + job.id)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self, contest_id):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync, contest_id)


class ImportJobStatusHandler(BaseHandler):
    """The progress of an import job, as JSON, for its owner only."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self, contest_id, job_id):
        job = IMPORT_JOBS.get(job_id, self.current_user.id)
        if job is None or str(job.contest_id) != contest_id:
            raise tornado.web.HTTPError(404)
        self.set_header("Content-Type", "application/json")
        self.set_header("Cache-Control", "no-store")
        self.write(json.dumps(job.as_json()))
