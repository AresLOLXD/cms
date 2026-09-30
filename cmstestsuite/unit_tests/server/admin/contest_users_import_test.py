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

"""Tests for the AWS bulk import page, its status endpoint and its link.

The handlers are built with __new__ and their helpers mocked, so these
tests need no database and no web server. The templates are rendered from
their "core" block only, as the rest of base.html needs a whole request.

"""

import asyncio
import json
import re
import secrets
import unittest
from types import SimpleNamespace
from unittest import mock

import tornado.web

from cms.server.admin.bulkimport import FIELDS, MAX_BYTES, ImportPlan, \
    read_rows
from cms.server.admin.handlers.contestuser import ImportJobStatusHandler, \
    ImportUsersHandler
from cms.server.admin.handlers import HANDLERS
from cms.server.admin.importjobs import ImportJob, ImportJobStore
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cms.server.util import Url

MODULE = "cms.server.admin.handlers.contestuser"
CONTEST_ID = 1
ADMIN_ID = 5
PASSWORD = "pw-secret-cell"
CSV = ("usuario,nombre,apellido,contraseña\n"
       "ana,Ana,Pérez,%s\n" % PASSWORD).encode("utf-8")
MAPPING = {"username": "usuario", "first_name": "nombre",
           "last_name": "apellido", "password": "contraseña"}
# What the handler sends back to the page: every field, "" if unassigned.
POSTED_MAPPING = {**{field: "" for field in FIELDS}, **MAPPING}
# Said when a job is unknown: it may have been saved before it expired, so
# the page must not say that nothing was applied.
NOT_FOUND = ("No se encontró la importación: no existe o ya expiró. "
             "Revisa la lista de usuarios para ver si se aplicó.")
NOT_APPLIED = "No se aplicó nada"
# Said with the progress of the admin's own running import, when a second
# file was sent while it ran: that file was refused, not imported.
REPEATED_NOTICE = ("Ya tenías una importación en curso en este concurso; "
                   "este es su progreso. El archivo que acabas de enviar no "
                   "se importó.")
# Said when the contest already has an import running: the file is fine, so
# it is not one of the errors that say that nothing was applied.
RUNNING_NOTICE = ("Hay una importación en curso para este concurso; "
                  "espera a que termine.")


def make_plan() -> ImportPlan:
    return ImportPlan(new_users=["ana"], updated_users=[],
                      new_participations=["ana"], updated_participations=[],
                      teams={}, groups={}, main_group_id=1)


def fake_url(*parts: object) -> str:
    return "/" + "/".join(str(part) for part in parts)


def make_handler(handler_class=ImportUsersHandler, *, data: bytes | None = CSV,
                 form: dict[str, str] | None = None, admin_id: int = ADMIN_ID,
                 permission_all: bool = True):
    """Build a handler of handler_class without a request or a database.

    data: the body of the uploaded file, or None for no file at all.
    form: the arguments of the request.

    """
    form = dict(form or {})
    handler = handler_class.__new__(handler_class)
    handler.application = mock.MagicMock()
    handler.request = SimpleNamespace(
        files={} if data is None else {"file": [{"body": data}]})
    handler._current_user = SimpleNamespace(
        id=admin_id, permission_all=permission_all)
    handler.contest = None
    handler.sql_session = mock.MagicMock()
    handler.safe_get_item = mock.MagicMock(
        return_value=SimpleNamespace(id=CONTEST_ID, name="Día 1"))
    handler.get_argument = mock.MagicMock(
        side_effect=lambda name, default=None: form.get(name, default))
    handler.render_params = mock.MagicMock(side_effect=lambda: {
        "url": fake_url, "xsrf_form_html": "",
        "admin": SimpleNamespace(permission_all=True)})
    handler.render = mock.MagicMock()
    handler.redirect = mock.MagicMock()
    handler.url = mock.MagicMock(side_effect=fake_url)
    handler.schedule_rpc = mock.MagicMock()
    handler.write = mock.MagicMock()
    handler.set_header = mock.MagicMock()
    return handler


def import_form(action: str | None, mapping: dict[str, str] | None = None
                ) -> dict[str, str]:
    """Return the arguments of a submitted form.

    action: the button pressed, or None if the request has none.

    """
    mapping = MAPPING if mapping is None else mapping
    form = {"map_" + field: header for field, header in mapping.items()}
    if action is not None:
        form["action"] = action
    return form


def rendered_params(handler) -> dict:
    """Return the parameters the handler rendered its page with."""
    handler.render.assert_called_once()
    template, = handler.render.call_args.args
    assert template == "contest_users_import.html"
    return handler.render.call_args.kwargs


class TestImportUsersPost(unittest.TestCase):

    def test_validate_shows_the_summary_and_starts_nothing(self):
        handler = make_handler(form=import_form("validate"))
        with mock.patch(MODULE + ".plan_import",
                        return_value=(make_plan(), [])) as plan_import, \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            handler._post_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertEqual(params["summary"], make_plan().summary())
        self.assertEqual(params["errors"], [])
        self.assertIsNone(params["job"])
        self.assertEqual(params["contest"].id, CONTEST_ID)
        self.assertEqual(params["fields"], FIELDS)
        plan_import.assert_called_once_with(
            handler.sql_session, CONTEST_ID, read_rows(CSV, MAPPING)[0])
        jobs.start.assert_not_called()
        handler.redirect.assert_not_called()

    def test_validate_is_the_default_action(self):
        handler = make_handler(form=import_form(None))
        with mock.patch(MODULE + ".plan_import",
                        return_value=(make_plan(), [])), \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            handler._post_sync(str(CONTEST_ID))

        self.assertIsNotNone(rendered_params(handler)["summary"])
        jobs.start.assert_not_called()

    def test_validate_keeps_the_columns_the_admin_chose(self):
        handler = make_handler(form=import_form("validate"))
        with mock.patch(MODULE + ".plan_import",
                        return_value=(make_plan(), [])):
            handler._post_sync(str(CONTEST_ID))

        self.assertEqual(rendered_params(handler)["mapping"], POSTED_MAPPING)

    def test_import_starts_a_job_and_redirects_to_its_page(self):
        handler = make_handler(form=import_form("import"))
        with mock.patch(MODULE + ".plan_import",
                        return_value=(make_plan(), [])), \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.start.return_value = SimpleNamespace(id="job-1")
            handler._post_sync(str(CONTEST_ID))

        jobs.start.assert_called_once_with(
            ADMIN_ID, CONTEST_ID, read_rows(CSV, MAPPING)[0], mock.ANY)
        handler.redirect.assert_called_once_with(
            "/contest/1/users/import?job=job-1")
        handler.render.assert_not_called()

    def test_finishing_the_job_reinitializes_the_proxy_service(self):
        handler = make_handler(form=import_form("import"))
        with mock.patch(MODULE + ".plan_import",
                        return_value=(make_plan(), [])), \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.start.return_value = SimpleNamespace(id="job-1")
            handler._post_sync(str(CONTEST_ID))
        on_done = jobs.start.call_args.args[3]
        handler.schedule_rpc.assert_not_called()

        on_done()

        handler.schedule_rpc.assert_called_once_with(
            handler.application.service.proxy_service.reinitialize)

    def test_errors_of_the_file_are_shown_and_nothing_starts(self):
        # The password column is not assigned to any header.
        mapping = {**MAPPING, "password": ""}
        handler = make_handler(form=import_form("import", mapping))
        with mock.patch(MODULE + ".plan_import") as plan_import, \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            handler._post_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertEqual(len(params["errors"]), 1)
        self.assertIn("password", params["errors"][0])
        self.assertIsNone(params["summary"])
        plan_import.assert_not_called()
        jobs.start.assert_not_called()
        handler.redirect.assert_not_called()

    def test_errors_of_the_plan_are_shown_and_nothing_starts(self):
        handler = make_handler(form=import_form("import"))
        with mock.patch(MODULE + ".plan_import",
                        return_value=(None, ["el equipo X no existe"])), \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            handler._post_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertEqual(params["errors"], ["el equipo X no existe"])
        self.assertIsNone(params["summary"])
        jobs.start.assert_not_called()
        handler.redirect.assert_not_called()

    def test_errors_keep_the_columns_the_admin_chose(self):
        handler = make_handler(form=import_form("import"))
        with mock.patch(MODULE + ".plan_import",
                        return_value=(None, ["el equipo X no existe"])):
            handler._post_sync(str(CONTEST_ID))

        self.assertEqual(rendered_params(handler)["mapping"], POSTED_MAPPING)

    def test_a_mapping_with_nothing_assigned_still_counts_as_submitted(self):
        # The page tells "no previous mapping" from "the admin left every
        # field unassigned" by the mapping being empty or not.
        handler = make_handler(form=import_form("import", {}))
        with mock.patch(MODULE + ".IMPORT_JOBS"):
            handler._post_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertTrue(params["errors"])
        self.assertEqual(params["mapping"], {field: "" for field in FIELDS})
        self.assertTrue(params["mapping"])

    def refuse_import(self, running_job, form=None):
        """Post an import that the store refuses; return the handler."""
        handler = make_handler(form=form or import_form("import"))
        with mock.patch(MODULE + ".plan_import",
                        return_value=(make_plan(), [])), \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.start.side_effect = ValueError(
                "ya hay una importación en curso para este concurso")
            jobs.running_job.return_value = running_job
            handler._post_sync(str(CONTEST_ID))
        self.jobs = jobs
        return handler

    def test_a_running_job_of_the_admin_redirects_to_its_progress(self):
        # A second request while the admin's own import runs: the admin
        # must not lose its page. The redirect says that the page is the
        # progress of an earlier import, as the file just sent may be a
        # different one and was not imported.
        handler = self.refuse_import(
            SimpleNamespace(id="job-1", owner_id=ADMIN_ID))

        handler.redirect.assert_called_once_with(
            "/contest/1/users/import?job=job-1&repetido=1")
        handler.render.assert_not_called()
        self.jobs.running_job.assert_called_once_with(CONTEST_ID)

    def test_a_running_job_of_another_admin_is_a_notice_not_an_error(self):
        handler = self.refuse_import(
            SimpleNamespace(id="job-1", owner_id=ADMIN_ID + 1))

        params = rendered_params(handler)
        self.assertEqual(params["notice"], RUNNING_NOTICE)
        # Not in the errors: they say that nothing was applied, and the
        # file has nothing to correct.
        self.assertEqual(params["errors"], [])
        self.assertIsNone(params["summary"])
        self.assertIsNone(params["job"])
        # The columns survive, as the file has to be chosen again.
        self.assertEqual(params["mapping"], POSTED_MAPPING)
        handler.redirect.assert_not_called()

    def test_a_job_that_ended_meanwhile_is_still_a_notice(self):
        # The store refused, but the job was over before it was asked for.
        handler = self.refuse_import(None)

        self.assertEqual(rendered_params(handler)["notice"], RUNNING_NOTICE)
        handler.redirect.assert_not_called()

    def test_a_second_request_ends_in_the_page_of_the_first_job(self):
        # The store is the real one: the first request starts the job, the
        # second one is refused and follows the first, marked as repeated.
        store = ImportJobStore()
        redirects = []
        for _ in range(2):
            handler = make_handler(form=import_form("import"))
            with mock.patch(MODULE + ".plan_import",
                            return_value=(make_plan(), [])), \
                    mock.patch(MODULE + ".IMPORT_JOBS", store), \
                    mock.patch.object(ImportJobStore, "_run"):
                handler._post_sync(str(CONTEST_ID))
            handler.render.assert_not_called()
            redirects.append(handler.redirect.call_args.args[0])

        (job_id,) = store._jobs
        self.assertEqual(redirects, [
            "/contest/1/users/import?job=" + job_id,
            "/contest/1/users/import?job=" + job_id + "&repetido=1"])

    def test_a_missing_file_is_reported(self):
        handler = make_handler(data=None, form=import_form("import"))
        with mock.patch(MODULE + ".plan_import") as plan_import, \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            handler._post_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertEqual(params["errors"], ["elige un archivo CSV"])
        self.assertEqual(params["mapping"], POSTED_MAPPING)
        plan_import.assert_not_called()
        jobs.start.assert_not_called()

    def test_a_file_over_the_limit_is_reported(self):
        handler = make_handler(data=b"x" * (MAX_BYTES + 1),
                               form=import_form("import"))
        with mock.patch(MODULE + ".plan_import") as plan_import, \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            handler._post_sync(str(CONTEST_ID))

        self.assertEqual(rendered_params(handler)["errors"],
                         ["el archivo pasa de 2 MB"])
        plan_import.assert_not_called()
        jobs.start.assert_not_called()

    def test_no_password_reaches_the_page(self):
        # A row with an empty username: the error points at the row,
        # never at the cell of the password column.
        data = ("usuario,nombre,apellido,contraseña\n"
                ",Ana,Pérez,%s\n" % PASSWORD).encode("utf-8")
        outcomes = [
            make_handler(data=data, form=import_form("import")),
            make_handler(form=import_form("validate")),
        ]
        for handler in outcomes:
            with mock.patch(MODULE + ".plan_import",
                            return_value=(make_plan(), [])), \
                    mock.patch(MODULE + ".IMPORT_JOBS"):
                handler._post_sync(str(CONTEST_ID))
            self.assertNotIn(PASSWORD, repr(rendered_params(handler)))


class TestImportUsersGet(unittest.TestCase):

    def test_the_page_without_a_job_is_the_form(self):
        handler = make_handler()
        with mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            handler._get_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertIsNone(params["job"])
        self.assertEqual(params["errors"], [])
        self.assertEqual(params["fields"], FIELDS)
        self.assertEqual(params["mapping"], {})
        self.assertIsNone(params["notice"])
        jobs.get.assert_not_called()

    def test_the_page_of_a_job_of_the_admin_shows_its_progress(self):
        handler = make_handler(form={"job": "job-1"})
        job = SimpleNamespace(id="job-1", contest_id=CONTEST_ID)
        with mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.get.return_value = job
            handler._get_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertIs(params["job"], job)
        self.assertEqual(params["errors"], [])
        self.assertIsNone(params["notice"])
        jobs.get.assert_called_once_with("job-1", ADMIN_ID)

    def test_a_repeated_import_shows_the_progress_with_a_notice(self):
        # The second file was refused while the first import runs, so the
        # progress must not look like the progress of the second one.
        handler = make_handler(form={"job": "job-1", "repetido": "1"})
        job = SimpleNamespace(id="job-1", contest_id=CONTEST_ID)
        with mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.get.return_value = job
            handler._get_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertIs(params["job"], job)
        self.assertEqual(params["notice"], REPEATED_NOTICE)
        # Not an error: the first import is fine.
        self.assertEqual(params["errors"], [])

    def test_a_repeated_import_of_an_unknown_job_is_only_not_found(self):
        handler = make_handler(form={"job": "gone", "repetido": "1"})
        with mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.get.return_value = None
            handler._get_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertEqual(params["notice"], NOT_FOUND)
        self.assertIsNone(params["job"])

    def test_an_unknown_job_is_a_notice_not_a_silent_form_or_an_error(self):
        handler = make_handler(form={"job": "gone"})
        with mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.get.return_value = None
            handler._get_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertEqual(params["notice"], NOT_FOUND)
        # Not in the errors: they say that nothing was applied, which is
        # not known of a job that expired after it was saved.
        self.assertEqual(params["errors"], [])
        self.assertIsNone(params["job"])

    def test_a_job_of_another_contest_is_a_notice_too(self):
        handler = make_handler(form={"job": "job-1"})
        job = SimpleNamespace(id="job-1", contest_id=CONTEST_ID + 1)
        with mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.get.return_value = job
            handler._get_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertEqual(params["notice"], NOT_FOUND)
        self.assertEqual(params["errors"], [])
        self.assertIsNone(params["job"])


class TestPageRendersWhatTheHandlerPasses(unittest.TestCase):
    """AWS renders with StrictUndefined: a parameter the handler forgets
    to pass fails the page, whichever path led to it."""

    def render_last_page(self, handler) -> str:
        params = dict(rendered_params(handler))
        template = AWS_ENVIRONMENT.get_template(
            "contest_users_import.html")
        return "".join(template.blocks["core"](template.new_context(params)))

    def test_the_form_and_the_unknown_job(self):
        for form in ({}, {"job": "gone"}):
            handler = make_handler(form=form)
            with mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
                jobs.get.return_value = None
                handler._get_sync(str(CONTEST_ID))

            html = self.render_last_page(handler)

            self.assertIn('<input type="file" name="file"', html)
            self.assertEqual(NOT_FOUND in html, bool(form))
            self.assertNotIn(NOT_APPLIED, html)

    def test_a_job_of_another_contest_shows_nothing_of_it(self):
        # The job exists, but it is not of this contest: the page must not
        # reveal its id, its status URL or its progress.
        handler = make_handler(form={"job": "job-1", "repetido": "1"})
        job = SimpleNamespace(id="job-1", contest_id=CONTEST_ID + 1,
                              processed=2, total=4)
        with mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.get.return_value = job
            handler._get_sync(str(CONTEST_ID))

        html = self.render_last_page(handler)

        self.assertIn(NOT_FOUND, html)
        self.assertNotIn("job-1", html)
        # The script of the page names the attribute, so look for the
        # element that carries it.
        self.assertNotIn('id="import_job"', html)
        self.assertNotIn("<progress", html)
        self.assertNotIn(REPEATED_NOTICE, html)

    def test_the_notice_of_a_running_import(self):
        handler = make_handler(form=import_form("import"))
        with mock.patch(MODULE + ".plan_import",
                        return_value=(make_plan(), [])), \
                mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.start.side_effect = ValueError("running")
            jobs.running_job.return_value = SimpleNamespace(
                id="job-1", owner_id=ADMIN_ID + 1)
            handler._post_sync(str(CONTEST_ID))

        html = self.render_last_page(handler)

        self.assertIn(RUNNING_NOTICE, html)
        self.assertNotIn(NOT_APPLIED, html)
        self.assertNotIn("Corrige estos errores", html)
        self.assertIn('data-selected="usuario"', html)

    def test_the_notice_of_a_repeated_import_comes_with_the_progress(self):
        for form, shown in (({"job": "job-1", "repetido": "1"}, True),
                            ({"job": "job-1"}, False)):
            handler = make_handler(form=form)
            job = SimpleNamespace(id="job-1", contest_id=CONTEST_ID,
                                  processed=2, total=4)
            with mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
                jobs.get.return_value = job
                handler._get_sync(str(CONTEST_ID))

            html = self.render_last_page(handler)

            self.assertIn("Procesando 2 de 4", html)
            self.assertEqual(REPEATED_NOTICE in html, shown, msg=form)
            self.assertNotIn(NOT_APPLIED, html)

    def test_the_progress_of_a_job(self):
        handler = make_handler(form={"job": "job-1"})
        job = SimpleNamespace(id="job-1", contest_id=CONTEST_ID,
                              processed=2, total=4)
        with mock.patch(MODULE + ".IMPORT_JOBS") as jobs:
            jobs.get.return_value = job
            handler._get_sync(str(CONTEST_ID))

        html = self.render_last_page(handler)

        self.assertIn("Procesando 2 de 4", html)

    def test_the_summary_and_the_errors_of_an_upload(self):
        for plan in ((make_plan(), []), (None, ["el equipo X no existe"])):
            handler = make_handler(form=import_form("validate"))
            with mock.patch(MODULE + ".plan_import", return_value=plan):
                handler._post_sync(str(CONTEST_ID))

            html = self.render_last_page(handler)

            self.assertIn("Usuarios nuevos: 1" if plan[0] else
                          "<li>el equipo X no existe</li>", html)
            self.assertEqual(NOT_APPLIED in html, plan[0] is None)
            # The columns chosen are still selected after either outcome.
            self.assertIn('data-selected="usuario"', html)
            self.assertNotIn(PASSWORD, html)

    def test_the_header_chosen_for_the_password_is_never_echoed(self):
        # A file without a header row: its first row is the header the
        # admin picks from, and it holds a real password.
        headerless = ("ana,Ana,Pérez,%s\nbob,Bob,Ruiz,pw2\n"
                      % PASSWORD).encode("utf-8")
        mapping = {"username": "ana", "first_name": "Ana",
                   "last_name": "Pérez", "password": PASSWORD}
        for plan in ((make_plan(), []), (None, ["el equipo X no existe"])):
            handler = make_handler(data=headerless,
                                   form=import_form("validate", mapping))
            with mock.patch(MODULE + ".plan_import", return_value=plan):
                handler._post_sync(str(CONTEST_ID))

            html = self.render_last_page(handler)

            self.assertIn('data-selected="ana"', html)
            self.assertNotIn(PASSWORD, html)


class TestLossesReachThePage(unittest.TestCase):
    """The counts of plan_import are what the warnings of the page show."""

    def test_the_validation_of_a_plan_that_clears_things(self):
        plan = ImportPlan(new_users=[], updated_users=["ana"],
                          new_participations=[],
                          updated_participations=["ana"], teams={},
                          groups={}, main_group_id=1, removed_teams=7,
                          moved_to_main_group=2)
        handler = make_handler(form=import_form("validate"))
        with mock.patch(MODULE + ".plan_import", return_value=(plan, [])):
            handler._post_sync(str(CONTEST_ID))

        params = rendered_params(handler)
        self.assertEqual(params["summary"]["equipos_quitados"], 7)
        self.assertEqual(params["summary"]["movidas_al_grupo_principal"], 2)
        template = AWS_ENVIRONMENT.get_template("contest_users_import.html")
        html = "".join(template.blocks["core"](
            template.new_context(dict(params))))
        self.assertIn("7 participaciones perderán su equipo", html)
        self.assertIn("2 participaciones pasarán al grupo principal", html)


class TestImportJobStatus(unittest.TestCase):

    def setUp(self):
        self.store = ImportJobStore()
        patcher = mock.patch(MODULE + ".IMPORT_JOBS", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start_job(self, owner_id: int = ADMIN_ID) -> ImportJob:
        """Start a job whose rows hold PASSWORD, without running it."""
        rows = read_rows(CSV, MAPPING)[0]
        with mock.patch.object(ImportJobStore, "_run"):
            return self.store.start(owner_id, CONTEST_ID, rows, lambda: None)

    def get_status(self, handler, job_id: str, contest_id: int = CONTEST_ID):
        asyncio.run(handler.get(str(contest_id), job_id))

    def test_the_owner_gets_the_json_of_the_job(self):
        job = self.start_job()
        job.processed = 1
        handler = make_handler(ImportJobStatusHandler)

        self.get_status(handler, job.id)

        handler.write.assert_called_once()
        body = handler.write.call_args.args[0]
        self.assertEqual(json.loads(body), job.as_json())
        self.assertEqual(json.loads(body)["processed"], 1)
        handler.set_header.assert_any_call("Content-Type",
                                           "application/json")
        handler.set_header.assert_any_call("Cache-Control", "no-store")

    def test_the_json_has_no_rows_and_no_password(self):
        job = self.start_job()
        job.status = "done"
        job.summary = make_plan().summary()
        handler = make_handler(ImportJobStatusHandler)

        self.get_status(handler, job.id)

        body = handler.write.call_args.args[0]
        self.assertNotIn("rows", json.loads(body))
        self.assertNotIn(PASSWORD, body)

    def assert_not_found(self, handler, job_id: str,
                         contest_id: int = CONTEST_ID):
        with self.assertRaises(tornado.web.HTTPError) as caught:
            self.get_status(handler, job_id, contest_id)
        self.assertEqual(caught.exception.status_code, 404)
        handler.write.assert_not_called()

    def test_another_admin_gets_a_404(self):
        job = self.start_job(owner_id=ADMIN_ID)
        handler = make_handler(ImportJobStatusHandler, admin_id=ADMIN_ID + 1)

        self.assert_not_found(handler, job.id)

    def test_an_unknown_job_gets_a_404(self):
        self.assert_not_found(make_handler(ImportJobStatusHandler), "nope")

    def test_a_job_of_another_contest_gets_a_404(self):
        job = self.start_job()

        self.assert_not_found(make_handler(ImportJobStatusHandler), job.id,
                              CONTEST_ID + 1)


class TestRoutes(unittest.TestCase):

    def route_of(self, handler_class) -> str:
        (pattern,) = [route[0] for route in HANDLERS
                      if route[1] is handler_class]
        return pattern

    def test_the_page_route(self):
        pattern = self.route_of(ImportUsersHandler)

        self.assertRegex("/contest/12/users/import", "^%s$" % pattern)
        self.assertNotRegex("/contest/x/users/import", "^%s$" % pattern)

    def test_the_status_route_takes_the_urls_of_any_job_id(self):
        pattern = self.route_of(ImportJobStatusHandler)

        # token_urlsafe uses "-" and "_": enough ids to have seen both.
        for _ in range(200):
            job_id = secrets.token_urlsafe(16)
            url = Url("")("contest", 12, "users", "import", job_id, "status")
            match = re.fullmatch(pattern, url)
            self.assertIsNotNone(match, msg=url)
            self.assertEqual(match.groups(), ("12", job_id))


class TestPermissions(unittest.TestCase):
    """Only the admins with all the permissions may import."""

    def setUp(self):
        patcher = mock.patch(MODULE + ".IMPORT_JOBS")
        self.jobs = patcher.start()
        self.addCleanup(patcher.stop)

    def assert_forbidden(self, handler, method: str, *args: str):
        with self.assertRaises(tornado.web.HTTPError) as caught:
            getattr(handler, method)(*args)
        self.assertEqual(caught.exception.status_code, 403)

    def test_the_page_needs_all_permissions(self):
        handler = make_handler(permission_all=False)
        handler._get_sync = mock.MagicMock()

        self.assert_forbidden(handler, "get", str(CONTEST_ID))

        handler._get_sync.assert_not_called()

    def test_the_upload_needs_all_permissions(self):
        handler = make_handler(form=import_form("import"),
                               permission_all=False)
        handler._post_sync = mock.MagicMock()

        self.assert_forbidden(handler, "post", str(CONTEST_ID))

        handler._post_sync.assert_not_called()
        self.jobs.start.assert_not_called()

    def test_the_status_needs_all_permissions(self):
        handler = make_handler(ImportJobStatusHandler, permission_all=False)

        self.assert_forbidden(handler, "get", str(CONTEST_ID), "job-1")

        self.jobs.get.assert_not_called()


class TestImportTemplates(unittest.TestCase):

    def render_core(self, page: str, **params) -> str:
        template = AWS_ENVIRONMENT.get_template(page)
        params.setdefault("url", fake_url)
        params.setdefault("xsrf_form_html", "")
        params.setdefault("admin", SimpleNamespace(permission_all=True))
        return "".join(template.blocks["core"](template.new_context(params)))

    def render_import(self, **params) -> str:
        """Render the page with what the handler passes when it has nothing
        else to say."""
        params.setdefault("contest", SimpleNamespace(id=CONTEST_ID,
                                                     name="Día 1"))
        params.setdefault("fields", FIELDS)
        params.setdefault("mapping", {})
        params.setdefault("errors", [])
        params.setdefault("summary", None)
        params.setdefault("job", None)
        params.setdefault("notice", None)
        return self.render_core("contest_users_import.html", **params)

    def test_the_form_uploads_a_file_with_one_select_per_field(self):
        html = self.render_import()

        self.assertRegex(html, r'<form enctype="multipart/form-data"'
                               r'\s+method="POST"')
        self.assertIn('action="/contest/1/users/import"', html)
        self.assertIn('<input type="file" name="file"', html)
        for field in FIELDS:
            self.assertEqual(
                len(re.findall(r'<select name="map_%s"' % field, html)), 1,
                msg=field)
        self.assertRegex(html, r'<button type="submit" name="action" '
                               r'value="validate">Solo validar</button>')
        self.assertRegex(html, r'<button type="submit" name="action" '
                               r'value="import">Importar</button>')
        self.assertNotIn("<progress", html)

    def test_the_form_carries_the_xsrf_field(self):
        html = self.render_import(
            xsrf_form_html='<input type="hidden" name="_xsrf" value="t"/>')

        self.assertIn('<input type="hidden" name="_xsrf" value="t"/>', html)

    def test_the_script_knows_the_aliases_of_the_columns(self):
        html = self.render_import()

        for alias in ('"contraseña"', '"contrasena"', '"estado"',
                      '"usuario"', '"apellidos"', '"grupo"'):
            self.assertIn(alias, html)

    def test_the_script_uses_escapes_and_no_invisible_characters(self):
        html = self.render_import()

        self.assertIn(r"/[\u0300-\u036f]/g", html)
        self.assertIn(r"/^\ufeff/", html)
        for character in ("\u0300", "\u036f", "\ufeff"):
            self.assertNotIn(character, html)

    def test_the_selects_carry_the_columns_chosen_before(self):
        html = self.render_import(mapping={
            "username": "usuario", "first_name": "nombre",
            "last_name": "apellido", "password": "contraseña"})

        self.assertRegex(
            html, r'<select name="map_username" data-field="username" '
                  r'data-selected="usuario" id="map_username">')
        # The column of the password is the one thing never echoed.
        self.assertRegex(
            html, r'<select name="map_password" data-field="password" '
                  r'data-selected="" id="map_password">')
        # An optional field left unassigned has nothing to select.
        self.assertRegex(
            html, r'<select name="map_team" data-field="team" '
                  r'data-selected="" id="map_team">')

    def test_the_columns_chosen_before_are_escaped(self):
        html = self.render_import(mapping={"username": '"><b>x</b>'})

        self.assertNotIn("<b>x</b>", html)
        self.assertRegex(
            html, r'data-field="username" data-selected="(&#34;|&quot;)'
                  r'&gt;&lt;b&gt;x&lt;/b&gt;"')

    def test_the_script_reads_the_columns_chosen_before(self):
        html = self.render_import()

        self.assertIn('getAttribute("data-selected")', html)
        self.assertIn("table[data-has-mapping]", html)

    def test_the_table_says_whether_there_is_a_previous_mapping(self):
        first_visit = self.render_import()
        self.assertNotIn('data-has-mapping="1"', first_visit)
        self.assertIn("<table>", first_visit)

        for mapping in ({"username": "usuario"},
                        {field: "" for field in FIELDS}):
            html = self.render_import(mapping=mapping)
            self.assertEqual(html.count('data-has-mapping="1"'), 1,
                             msg=mapping)
            self.assertIn('<table data-has-mapping="1">', html)

    def test_the_form_asks_to_pick_the_file_again_only_with_a_mapping(self):
        hint = ("Vuelve a elegir el archivo para importarlo. Se conservan "
                "las columnas asignadas, salvo la de la contraseña: "
                "revísala.")

        self.assertNotIn(hint, self.render_import())
        html = self.render_import(mapping={"username": "usuario"})

        self.assertIn("<p>%s</p>" % hint, html)
        # The hint is above the file input.
        self.assertLess(html.index(hint),
                        html.index('<input type="file" name="file"'))

    def test_every_field_has_a_label_in_spanish(self):
        html = self.render_import()

        labels = {
            "import_file": "Archivo CSV *",
            "map_username": "Usuario (username) *",
            "map_first_name": "Nombre (first_name) *",
            "map_last_name": "Apellidos (last_name) *",
            "map_password": "Contraseña del día (password) *",
            "map_team": "Equipo (team)",
            "map_group": "Grupo (group)"}
        for control, text in labels.items():
            self.assertIn('<label for="%s">%s</label>' % (control, text),
                          html, msg=control)
            # The label points at a control that exists.
            self.assertIn('id="%s"' % control, html, msg=control)
        self.assertEqual(html.count("<label "), len(labels))
        # The selects keep what the script and the server read.
        for field in FIELDS:
            self.assertRegex(html, r'<select name="map_%s" data-field="%s" '
                                   % (field, field))

    def test_the_required_marks_are_explained(self):
        html = self.render_import()

        self.assertIn("* obligatorio", html)
        # The optional fields are not marked.
        self.assertNotIn("(team) *", html)
        self.assertNotIn("(group) *", html)

    def test_the_progress_is_announced_and_the_bar_has_a_name(self):
        job = SimpleNamespace(id="job-1", processed=3, total=10)

        html = self.render_import(job=job)

        self.assertRegex(html, r'<p id="import_text" aria-live="polite">'
                               r'Procesando 3 de 10</p>')
        self.assertRegex(html, r'<progress id="import_bar" value="3" '
                               r'max="10" aria-label="[^"]+"')

    def test_the_result_of_the_import_is_announced_too(self):
        # "Listo.", what the import cleared and "Error: ..." are written in
        # this container by the script, so it is a live region as well.
        job = SimpleNamespace(id="job-1", processed=3, total=10)

        html = self.render_import(job=job)

        self.assertIn('<div id="import_result" aria-live="polite"></div>',
                      html)

    def test_the_form_gives_the_time_of_an_import(self):
        html = self.render_import()

        self.assertIn("de 20 a 40 s por cada 300 concursantes", html)
        self.assertNotIn("~30 s", html)

    def test_the_form_disables_its_buttons_after_the_first_submit(self):
        # A double click would post the file twice; the second post is
        # refused while the first import runs.
        html = self.render_import()

        self.assertIn('getElementById("import_form")', html)
        self.assertIn('addEventListener("submit"', html)
        self.assertIn(".disabled = true", html)
        self.assertRegex(html, r'<form [^>]*id="import_form"')

    def test_the_script_keeps_the_action_of_the_clicked_button(self):
        # A disabled submit button is not sent, so its name and value are
        # copied into a hidden input before disabling it.
        html = self.render_import()

        self.assertIn("event.submitter", html)
        self.assertIn('input.type = "hidden"', html)
        self.assertIn("input.name = event.submitter.name", html)
        self.assertIn("input.value = event.submitter.value", html)
        # The copy is made before either button is disabled.
        self.assertLess(html.index("input.value = event.submitter.value"),
                        html.index(".disabled = true"))

    def test_the_script_enables_the_buttons_again_when_coming_back(self):
        # The Back button may restore the page with its buttons disabled.
        html = self.render_import()

        self.assertIn('addEventListener("pageshow"', html)
        self.assertIn(".disabled = false", html)
        # Not only from the back/forward cache: Firefox can restore the
        # disabled state of a form without it, so every pageshow re-enables.
        self.assertNotIn("persisted", html)

    def test_a_job_shows_a_progress_bar_and_its_status_url(self):
        job = SimpleNamespace(id="job-1", processed=3, total=10)

        html = self.render_import(job=job)

        self.assertIn(
            'data-status-url="/contest/1/users/import/job-1/status"', html)
        self.assertIn("Procesando 3 de 10", html)
        self.assertRegex(html, r'<progress id="import_bar" value="3" '
                               r'max="10"')
        self.assertNotIn("<form", html)

    def test_errors_are_listed_and_escaped(self):
        html = self.render_import(
            errors=["fila 2: el usuario está vacío", "<script>x</script>"])

        self.assertIn("<li>fila 2: el usuario está vacío</li>", html)
        self.assertIn("<li>&lt;script&gt;x&lt;/script&gt;</li>", html)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("<form", html)

    def test_the_errors_have_a_count_as_a_heading(self):
        html = self.render_import(errors=["fila 2: a", "fila 3: b",
                                          "fila 4: c"])

        self.assertIn("<h3>3 errores</h3>", html)
        self.assertEqual(html.count("<li>fila"), 3)
        self.assertNotIn("más", html)

    def test_one_error_is_not_plural(self):
        html = self.render_import(errors=["fila 2: a"])

        self.assertIn("<h3>1 error</h3>", html)
        self.assertNotIn("1 errores", html)

    def test_the_error_list_is_capped_at_50(self):
        errors = ["fila %d: mal" % line for line in range(2, 302)]

        html = self.render_import(errors=errors)

        self.assertIn("<h3>300 errores</h3>", html)
        self.assertEqual(html.count("<li>fila"), 50)
        # The first 50 are the ones shown, in order, and the rest is only
        # counted.
        self.assertIn("<li>fila 2: mal</li>", html)
        self.assertIn("<li>fila 51: mal</li>", html)
        self.assertNotIn("fila 52: mal", html)
        self.assertNotIn("fila 301: mal", html)
        self.assertIn("y 250 más", html)

    def test_the_errors_at_the_cap_show_all_without_a_rest(self):
        for count, rest in ((50, None), (51, "y 1 más")):
            errors = ["fila %d: mal" % line for line in range(2, 2 + count)]

            html = self.render_import(errors=errors)

            self.assertIn("<h3>%d errores</h3>" % count, html)
            self.assertEqual(html.count("<li>fila"), 50, msg=count)
            if rest is None:
                self.assertNotIn(" más", html)
            else:
                self.assertIn(rest, html)

    def test_a_notice_is_shown_without_saying_that_nothing_was_applied(self):
        html = self.render_import(notice=NOT_FOUND)

        self.assertIn(NOT_FOUND, html)
        self.assertNotIn(NOT_APPLIED, html)
        self.assertNotIn("Corrige estos errores", html)
        self.assertIn("<form", html)

    def test_the_errors_say_that_nothing_was_applied(self):
        html = self.render_import(errors=["fila 2: el usuario está vacío"])

        self.assertIn(NOT_APPLIED, html)
        self.assertNotIn("No se encontró la importación", html)

    def test_no_notice_on_the_first_visit(self):
        html = self.render_import()

        self.assertNotIn("No se encontró la importación", html)
        self.assertNotIn(NOT_APPLIED, html)

    def test_the_column_chosen_for_the_password_is_never_echoed(self):
        # Without a header row that "header" is a contestant's password.
        html = self.render_import(mapping={
            "username": "usuario", "password": "s3cret"})

        self.assertNotIn("s3cret", html)
        self.assertRegex(
            html, r'<select name="map_password" data-field="password" '
                  r'data-selected="" id="map_password">')
        self.assertIn('data-selected="usuario"', html)

    def test_the_summary_of_a_validation_is_shown(self):
        html = self.render_import(summary=make_plan().summary())

        self.assertIn("Usuarios nuevos: 1", html)
        self.assertIn("Participaciones nuevas: 1", html)
        self.assertIn("Usuarios actualizados: 0", html)

    def test_the_validation_warns_about_what_an_import_clears(self):
        summary = {**make_plan().summary(), "equipos_quitados": 3,
                   "movidas_al_grupo_principal": 2}

        html = self.render_import(summary=summary)

        self.assertIn("3 participaciones perderán su equipo", html)
        self.assertIn("2 participaciones pasarán al grupo principal", html)
        # The warnings come after the counts of the summary.
        self.assertLess(html.index("Participaciones actualizadas"),
                        html.index("perderán su equipo"))

    def test_the_warnings_of_the_validation_have_a_singular(self):
        summary = {**make_plan().summary(), "equipos_quitados": 1,
                   "movidas_al_grupo_principal": 1}

        html = self.render_import(summary=summary)

        self.assertIn("1 participación perderá su equipo", html)
        self.assertIn("1 participación pasará al grupo principal", html)
        self.assertNotIn("1 participaciones", html)

    def test_each_warning_of_the_validation_shows_only_when_it_applies(self):
        html = self.render_import(summary={
            **make_plan().summary(), "equipos_quitados": 4})
        self.assertIn("4 participaciones perderán su equipo", html)
        self.assertNotIn("pasarán al grupo principal", html)

        html = self.render_import(summary={
            **make_plan().summary(), "movidas_al_grupo_principal": 5})
        self.assertIn("5 participaciones pasarán al grupo principal", html)
        self.assertNotIn("perderán su equipo", html)

    def test_no_warning_when_the_import_clears_nothing(self):
        html = self.render_import(summary=make_plan().summary())

        self.assertIn("Usuarios nuevos: 1", html)
        self.assertNotIn("perderá", html)
        self.assertNotIn("pasará", html)
        self.assertNotIn('class="import-box import-warning"', html)

    def test_the_form_warns_that_empty_team_and_group_cells_replace(self):
        note = ("Vacío o sin asignar: la participación se queda sin equipo "
                "/ en el grupo principal.")

        html = self.render_import()

        self.assertEqual(html.count(note), 1)
        # It sits in the table of the selectors, after the last of them.
        self.assertGreater(html.index(note), html.index('name="map_team"'))
        self.assertLess(html.index(note), html.index("</table>"))

    def test_the_script_reports_what_the_import_cleared(self):
        html = self.render_import()

        self.assertIn("summary.equipos_quitados", html)
        self.assertIn("summary.movidas_al_grupo_principal", html)
        # The import is over by then, so the lines are in the past.
        self.assertIn("perdieron su equipo", html)
        self.assertIn("pasaron al grupo principal", html)

    def test_the_job_carries_the_links_to_show_when_it_is_done(self):
        job = SimpleNamespace(id="job-1", processed=3, total=10)

        html = self.render_import(job=job)

        self.assertIn('data-users-url="/contest/1/users"', html)
        # "Importar otro archivo" is the page without ?job=.
        self.assertIn('data-import-url="/contest/1/users/import"', html)
        self.assertNotIn("import?job", html)

    def test_the_script_shows_the_links_and_a_bold_listo_when_done(self):
        html = self.render_import()

        self.assertIn('getAttribute("data-users-url")', html)
        self.assertIn('getAttribute("data-import-url")', html)
        self.assertIn("Lista de usuarios", html)
        self.assertIn("Importar otro archivo", html)
        self.assertIn('createElement("strong")', html)
        self.assertIn('strong.textContent = "Listo."', html)
        self.assertIn('createElement("a")', html)

    def test_the_script_shows_the_links_after_an_error_too(self):
        # "Error: ..." must not be a dead end either.
        html = self.render_import()
        script = html[html.index("<script>"):]
        done = script.index('s.status === "done"')
        error = script.index('s.status === "error"')
        error_end = script.index("} else {", error)

        self.assertEqual(script.count("function addLinks(parent)"), 1)
        links = script[script.index("function addLinks(parent)"):]
        links = links[:links.index("\n    }\n")]
        self.assertIn('"Lista de usuarios"', links)
        self.assertIn('"Importar otro archivo"', links)
        self.assertIn("addLinks(out);", script[done:error])
        self.assertIn('"Error: " + s.error', script[error:error_end])
        self.assertIn("addLinks(out);", script[error:error_end])

    def test_the_blocks_have_their_own_classes_not_the_toast_ones(self):
        # AWS styles ".notification" only inside its "#notifications"
        # container, so on this page it would render as plain text.
        summary = {**make_plan().summary(), "equipos_quitados": 1}
        html = self.render_import(errors=["fila 2: mal"], notice="Aviso",
                                  summary=summary)

        self.assertNotIn('class="notification', html)
        self.assertIn('<div class="import-box import-error">', html)
        self.assertIn('<div class="import-box import-notice">', html)
        self.assertIn('<div class="import-box import-summary">', html)
        self.assertIn('<div class="import-box import-warning">', html)

    def test_the_page_brings_the_styles_of_its_blocks(self):
        html = self.render_import()

        # Inside the core block, ahead of the script.
        self.assertIn("<style>", html)
        self.assertLess(html.index("<style>"), html.index("<script>"))
        css = html[html.index("<style>"):html.index("</style>")]
        # The notice and the summary keep the neutral look of the box.
        for name in ("import-box", "import-error", "import-warning",
                     "import-done", "import-note"):
            self.assertIn("." + name, css)
        # AWS resets the bullets of the lists.
        self.assertRegex(css, r"\.import-box ul\s*\{[^}]*list-style: disc")

    def test_the_script_styles_what_it_writes(self):
        html = self.render_import()

        # The result of the job, its failure and the lost contact.
        self.assertIn('"import-box import-done"', html)
        self.assertIn('"import-box import-warning"', html)
        self.assertIn('"import-box import-error"', html)
        self.assertIn('"import-box import-notice"', html)

    def test_the_users_page_links_to_the_import_for_admins_with_all(self):
        contest = SimpleNamespace(id=CONTEST_ID, participations=[],
                                  groups=[], main_group_id=None)
        config = SimpleNamespace(
            contest_web_server=SimpleNamespace(listen_port=[]))

        html = self.render_core(
            "contest_users.html", contest=contest, config=config,
            unassigned_users=[])
        self.assertIn(
            '<a href="/contest/1/users/import">Importar CSV</a>', html)

        html = self.render_core(
            "contest_users.html", contest=contest, config=config,
            unassigned_users=[],
            admin=SimpleNamespace(permission_all=False))
        self.assertNotIn("Importar CSV", html)


if __name__ == "__main__":
    unittest.main()
