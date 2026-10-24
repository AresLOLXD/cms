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

"""Handlers of AWS to export contests as a dump, downloaded as a zip.

The dump has the password of every user and all the submissions, so every
handler needs all the permissions, and the state and the file of a job are
only for the admin who started it. The job itself is in exportjobs.py.

"""

import asyncio
import logging
import os
from datetime import datetime
from collections.abc import Collection

import tornado.web
from sqlalchemy import select
from sqlalchemy.orm import Session
from tornado.iostream import StreamClosedError

from cms.db import Contest, Group
from cms.server.admin.exportjobs import EXPORT_JOBS, ExportJob
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission
from .contestuser import _write_job_status

logger = logging.getLogger(__name__)

# The flags of cmsDumpExporter that each choice of the form adds.
CONTENT_FLAGS = {"all": (), "no_files": ("-F",), "only_files": ("-f",)}
OPTION_FLAGS = {"no_generated": "-G", "no_submissions": "-S",
                "no_user_tests": "-U", "no_users": "-X"}
CHUNK_SIZE = 1024 * 1024
NOT_FOUND = "No se encontró la exportación: no existe o ya expiró."


def running_contests(session: Session, contest_ids: Collection[int],
                     now: datetime) -> dict[int, str]:
    """Find the contests that are running or in their analysis mode.

    A contest is, if any of its groups is.

    session: the database session.
    contest_ids: the contests to look at.
    now: the moment to look at.

    return: the id and name of each of them.

    """
    rows = session.execute(
        select(Contest.id, Contest.name, Group)
        .join(Group, Group.contest_id == Contest.id)
        .where(Contest.id.in_(contest_ids))).all()
    # shortcut: the extra time or delay of one participation is not
    # considered, so a participant who works past the end of the group is
    # not noticed; upgrade it if exports are asked for at that moment.
    return {contest_id: name for contest_id, name, group in rows
            if group.phase(now) in (0, 2)}


class ExportHandler(BaseHandler):
    """The page to export contests, and the form that starts the job.

    GET shows the form and the exports of the admin, or the state of the
    job given by ?job=. POST checks the form, starts the job and redirects
    to its state.

    """

    def _render_page(self, form: dict | None = None,
                     errors: list[str] | None = None,
                     job: ExportJob | None = None,
                     notice: str | None = None,
                     jobs: list[ExportJob] | None = None) -> None:
        """Render the page, in an executor thread.

        The template gets every one of these parameters, as AWS renders
        with StrictUndefined.

        form: what the admin chose, to show it again; by default, the
            usual choices.
        errors: why nothing was started.
        job: the job whose state to show, instead of the form.
        notice: something to tell the admin that is not an error.
        jobs: the exports of the admin.

        """
        self.r_params = self.render_params()
        self.r_params["form"] = form or {
            "contest_ids": [], "content": "all",
            "options": {"no_generated"}}
        self.r_params["errors"] = errors or []
        self.r_params["job"] = job
        self.r_params["notice"] = notice
        self.r_params["jobs"] = jobs or []
        self.r_params["running"] = running_contests(
            self.sql_session, [c.id for c in self.r_params["contest_list"]],
            make_datetime())
        self.render("export.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self) -> None:
        admin_id = self.current_user.id
        job_id = self.get_argument("job", None)
        job = None if job_id is None else EXPORT_JOBS.get(job_id, admin_id)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._render_page, None, None, job,
            NOT_FOUND if job_id is not None and job is None else None,
            EXPORT_JOBS.jobs_of(admin_id))

    def _read_form(self) -> dict:
        """Read what the admin chose; a value out of the form is a 400.

        return: the chosen contests ("contest_ids"), the kind of content
            ("content"), the options ("options") and whether the admin
            confirmed to export running contests ("confirmed").

        """
        try:
            contest_ids = sorted(
                {int(i) for i in self.get_arguments("contest_id")})
        except ValueError:
            raise tornado.web.HTTPError(400)
        content = self.get_argument("content", "all")
        if content not in CONTENT_FLAGS:
            raise tornado.web.HTTPError(400)
        return {
            "contest_ids": contest_ids, "content": content,
            "options": {o for o in OPTION_FLAGS
                        if self.get_argument(o, None)},
            "confirmed": bool(self.get_argument("confirm_running", None))}

    def _check_form(self, form: dict) -> list[str]:
        """Check the choices against the database, in an executor thread.

        form: what _read_form returned.

        return: why the export must not start, or an empty list.

        """
        ids = form["contest_ids"]
        if not ids:
            return ["elige al menos un concurso"]
        found = self.sql_session.execute(
            select(Contest.id).where(Contest.id.in_(ids))).scalars().all()
        if len(found) != len(ids):
            return ["alguno de los concursos elegidos ya no existe"]
        running = running_contests(self.sql_session, ids, make_datetime())
        if running and not form["confirmed"]:
            return ["Estos concursos están en curso o en su modo de "
                    "análisis: %s. El volcado se lee sin congelar la base "
                    "de datos: puede salir inconsistente y la carga puede "
                    "afectar al concurso. Marca la confirmación para "
                    "exportarlos de todos modos." %
                    ", ".join(sorted(running.values()))]
        return []

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self) -> None:
        admin = self.current_user
        form = self._read_form()
        loop = asyncio.get_running_loop()
        errors = await loop.run_in_executor(None, self._check_form, form)
        if not errors:
            flags = CONTENT_FLAGS[form["content"]] + tuple(
                flag for option, flag in OPTION_FLAGS.items()
                if option in form["options"])
            try:
                job = EXPORT_JOBS.start(admin.id, form["contest_ids"], flags)
            except ValueError as exc:
                errors = [str(exc)]
            else:
                logger.info("Dump export %s started by admin %d (%r): "
                            "contests %s, flags %s, running contests "
                            "confirmed: %s.", job.name, admin.id,
                            admin.name, job.contest_ids, job.flags,
                            form["confirmed"])
                self.redirect(self.url("export") + "?job=" + job.id)
                return
        await loop.run_in_executor(
            None, self._render_page, form, errors, None, None,
            EXPORT_JOBS.jobs_of(admin.id))


class ExportJobStatusHandler(BaseHandler):
    """The state of an export job, as JSON, for its owner only."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self, job_id: str) -> None:
        job = EXPORT_JOBS.get(job_id, self.current_user.id)
        if job is None:
            raise tornado.web.HTTPError(404)
        _write_job_status(self, job)


class ExportDownloadHandler(BaseHandler):
    """The zip of a finished export job, for its owner only."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def get(self, job_id: str) -> None:
        admin_id = self.current_user.id
        job = EXPORT_JOBS.get(job_id, admin_id)
        if job is None or job.status != "done":
            raise tornado.web.HTTPError(404)
        try:
            file = open(job.path, "rb")
        except FileNotFoundError:
            raise tornado.web.HTTPError(404)
        logger.info("Dump export %s (job %s) downloaded by admin %d.",
                    job.name, job.id, admin_id)
        # Streaming needs no database connection, so it must not hold a
        # request slot either: a client that stops reading would keep it
        # (see FileHandler.fetch). If the job expires meanwhile, the open
        # file stays readable.
        self.sql_session.close()
        self._release_request_slot()
        loop = asyncio.get_running_loop()
        with file:
            self.set_header("Content-Type", "application/zip")
            self.set_header("Content-Length",
                            str(os.fstat(file.fileno()).st_size))
            self.set_header("Content-Disposition",
                            'attachment; filename="%s"' % job.filename)
            self.set_header("Cache-Control", "no-store")
            try:
                while chunk := await loop.run_in_executor(
                        None, file.read, CHUNK_SIZE):
                    self.write(chunk)
                    await self.flush()
            except StreamClosedError:
                # The client disconnected mid-download.
                return
