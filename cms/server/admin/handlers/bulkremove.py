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

"""Handlers of AWS that remove several users, or participations, at once.

"""

import asyncio
import logging

from cms.db import Contest
from cms.server.admin.bulkremove import MAX_PLAN_USERS, RemovalPlan, \
    parse_usernames, plan_removal, remove_participations, remove_users
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission

NOTHING_CHOSEN = "No elegiste ningún usuario."
WRONG_COUNT = ("El número escrito no coincide con el número de usuarios. "
               "No se borró nada.")
PLAN_CHANGED = ("La selección cambió desde la vista previa (los usuarios, "
                "sus concursos o envíos, o si hay un concurso en curso): "
                "revisa la lista y confirma otra vez. No se borró nada.")
PLAN_TOO_LARGE = "La selección es demasiado grande"
PLAN_TOO_LARGE_TEXT = ("Son %d usuarios y el máximo por vez es %d. "
                       "Divídela en listas más pequeñas.")
MAX_ERROR_LENGTH = 300

logger = logging.getLogger(__name__)


class _BulkRemoveHandler(BaseHandler):
    """Remove several users from the platform, or from one contest.

    POST with action=preview (the default) shows what would be removed:
    the checked user_id values and the usernames of the "usernames"
    textarea and of the "file" upload. POST with action=confirm removes
    the posted user_id values, if the plan is still the one the preview
    showed (its fingerprint) and expected_count is the number of users.

    """

    def _post_sync(self, contest_id: str | None = None) -> None:
        self.contest = None if contest_id is None \
            else self.safe_get_item(Contest, contest_id)
        if self.contest is None:
            self._back_url = self.url("users")
            self._action_url = self.url("users", "remove")
        else:
            self._back_url = self.url("contest", self.contest.id, "users")
            self._action_url = self.url("contest", self.contest.id,
                                        "users", "remove")
        try:
            user_ids = [int(value)
                        for value in self.get_arguments("user_id")]
        except ValueError:
            self._notify("Invalid field(s)", "user_id")
            self.redirect(self._back_url)
            return
        if self.get_argument("action", "preview") == "confirm":
            self._confirm(user_ids)
        else:
            self._preview(user_ids)

    def _scope_id(self) -> int | None:
        return None if self.contest is None else self.contest.id

    def _notify(self, subject: str, text: str) -> None:
        self.service.add_notification(make_datetime(), subject, text)

    def _preview(self, user_ids: list[int]) -> None:
        sources = []
        text = self.get_argument("usernames", "")
        if text.strip():
            sources.append(text.encode("utf-8"))
        files = self.request.files.get("file")
        if files and files[0]["body"]:
            sources.append(files[0]["body"])
        try:
            usernames = parse_usernames(*sources)
        except ValueError as error:
            self._notify("No se leyó la lista", str(error))
            self.redirect(self._back_url)
            return
        if not user_ids and not usernames:
            self._notify(NOTHING_CHOSEN, "")
            self.redirect(self._back_url)
            return
        plan = plan_removal(self.sql_session, contest_id=self._scope_id(),
                            user_ids=user_ids, usernames=usernames)
        if len(plan.users) > MAX_PLAN_USERS:
            # The confirmation form would carry more arguments than AWS
            # parses, and the admin would get a bare 400 at confirm.
            self._notify(PLAN_TOO_LARGE, PLAN_TOO_LARGE_TEXT
                         % (len(plan.users), MAX_PLAN_USERS))
            self.redirect(self._back_url)
            return
        self._render_plan(plan, None)

    def _confirm(self, user_ids: list[int]) -> None:
        if not user_ids:
            self._notify(NOTHING_CHOSEN, "")
            self.redirect(self._back_url)
            return
        plan = plan_removal(self.sql_session, contest_id=self._scope_id(),
                            user_ids=user_ids)
        if self.get_argument("fingerprint", "") != plan.fingerprint:
            self._render_plan(plan, PLAN_CHANGED)
            return
        if self.get_argument("expected_count", "") != str(len(plan.users)):
            self._render_plan(plan, WRONG_COUNT)
            return
        try:
            if self.contest is None:
                count = remove_users(self.sql_session, plan.user_ids)
            else:
                count = remove_participations(
                    self.sql_session, self.contest.id, plan.user_ids)
            # Inside the try, so that a failed commit is told like a
            # failed DELETE; try_commit would add its own notification.
            self.sql_session.commit()
        except Exception as error:
            self.sql_session.rollback()
            # A failed DELETE repr() holds the SQL with one placeholder
            # per id, so show only the database driver's message.
            orig = getattr(error, "orig", None)
            text = repr(error) if orig is None else str(orig)
            self._notify("No se borró nada", text[:MAX_ERROR_LENGTH])
            self.redirect(self._back_url)
            return
        # reinitialize only resets the groups that lost contests, and RWS
        # only merges what it receives: the removed users would stay.
        for group in plan.groups:
            self.schedule_rpc(self.service.proxy_service.regenerate_ranking,
                              group=group)
        if self.contest is None:
            scope = "the platform"
            subject = "Se borró 1 usuario" if count == 1 \
                else "Se borraron %d usuarios" % count
        else:
            scope = "contest %s (id %s)" % (self.contest.name,
                                            self.contest.id)
            subject = "Se quitó 1 participación" if count == 1 \
                else "Se quitaron %d participaciones" % count
        logger.info("Admin %s removed %d user(s) from %s: %s.",
                    self.current_user.name, count, scope,
                    ", ".join(entry.username for entry in plan.users))
        self._notify(subject, "")
        self.redirect(self._back_url)

    def _render_plan(self, plan: RemovalPlan, error: str | None) -> None:
        """Render the confirmation page.

        plan: what the removal would take.
        error: why the last confirmation removed nothing, or None.

        """
        self.r_params = self.render_params()
        self.r_params["contest"] = self.contest
        self.r_params["plan"] = plan
        self.r_params["error"] = error
        self.r_params["action_url"] = self._action_url
        self.r_params["back_url"] = self._back_url
        self.render("users_bulk_remove.html", **self.r_params)


class BulkRemoveUsersHandler(_BulkRemoveHandler):
    """Remove several users, and all their data, from the platform."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)


class BulkRemoveParticipationsHandler(_BulkRemoveHandler):
    """Remove the participations of several users from a contest."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self, contest_id: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync, contest_id)
