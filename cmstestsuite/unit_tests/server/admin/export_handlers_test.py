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

"""Tests for the pages that export contests as a dump.

They send real requests to a real AdminWebServer, as a browser would, with
a store of jobs that runs a fake exporter. They must never import
cmscontrib.DumpExporter (see exportjobs_test.py).

"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from urllib.parse import urlencode

from sqlalchemy import select
from tornado.httpclient import HTTPResponse
from tornado.httputil import ParseBodyConfig, set_parse_body_config

from cmstestsuite.unit_tests.server.admin.admin_session_test import \
    session_payload, sign
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH
from cmstestsuite.unit_tests.server.admin.exportjobs_test import \
    FAKE_EXPORTER
from cmstestsuite.unit_tests.server.admin.pool_exhaustion_test import \
    XSRF_COOKIE, _AwsServerMixin

from cms.db import Admin
from cms.locale import DEFAULT_TRANSLATION
from cms.server.admin import exportjobs, server
from cms.server.admin.exportjobs import ExportJobStore
from cms.server.admin.handlers import base
from cms.server.admin.handlers.base import BaseHandler
from cms.server.admin.server import AdminWebServer

MODULE = "cms.server.admin.handlers.export"
RUNNING_ERROR = "Tick the confirmation"


class ExportHandlersTest(_AwsServerMixin, unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # The admin of the mixin has all the permissions.
        self.admin_id = self.session.execute(
            select(Admin.id).where(Admin.name == self.admin_name)
        ).scalar_one()
        self.other = self.login(self.add_admin())
        plain = self.add_admin(permission_all=False)
        self.plain_id = plain.id
        self.plain = self.login(plain)
        # Two contests that ended, one running and one in analysis mode.
        ended = dict(start=datetime(2020, 1, 1), stop=datetime(2020, 1, 2))
        self.finished = self.add_contest(groups=[self.get_group(**ended)])
        self.finished2 = self.add_contest(groups=[self.get_group(**ended)])
        self.running = self.add_contest()
        self.analysis = self.add_contest(groups=[self.get_group(
            start=datetime(2020, 1, 1), stop=datetime(2020, 1, 2),
            analysis_enabled=True, analysis_start=datetime(2020, 1, 3),
            analysis_stop=datetime(2100, 1, 1))])
        self.session.commit()
        self.finished_id = self.finished.id
        self.finished2_id = self.finished2.id
        self.running_id = self.running.id
        self.analysis_id = self.analysis.id
        self.session.rollback()

        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        self.gate = os.path.join(directory, "gate")
        self.gated = False
        self.argv_calls = []
        self.exit_code = 0

        def build_argv(contest_ids, flags, target):
            self.argv_calls.append((list(contest_ids), tuple(flags)))
            return [sys.executable, "-c", FAKE_EXPORTER, target,
                    str(self.exit_code),
                    self.gate if self.gated else ""]

        self.store = ExportJobStore(os.path.join(directory, "aws-exports"),
                                    build_argv=build_argv)
        self.addAsyncCleanup(self.settle)
        for patcher in (
                mock.patch(MODULE + ".EXPORT_JOBS", self.store),
                mock.patch.object(self.aws, "translations", {
                    "en": DEFAULT_TRANSLATION, "es": SPANISH}),
                mock.patch.object(exportjobs, "MIN_FREE_BYTES", 0)):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def settle(self):
        """Let the running export end, so its child does not outlive the
        test."""
        job = self.store.running_job()
        if job is not None:
            open(self.gate, "w").close()
            await job.task

    def login(self, admin) -> str:
        cookie = sign(session_payload(admin.id))
        self.session.rollback()
        return "%s=%s" % (BaseHandler.COOKIE_NAME, cookie)

    async def fetch(self, path: str, cookie: str | None = None,
                    form: list[tuple[str, str]] | None = None,
                    xsrf: bool = True, language: str | None = None,
                    **kwargs) -> HTTPResponse:
        """Send a GET, or a POST if there is a form, as an admin, with the
        AWS language chosen if one is given."""
        cookies = [cookie or self.cookie_header, "_xsrf=" + XSRF_COOKIE]
        if language is not None:
            cookies.append("aws_language=" + language)
        body = None
        if form is not None:
            body = urlencode(form + ([("_xsrf", XSRF_COOKIE)] if xsrf
                                     else []))
        return await self.client.fetch(
            "http://127.0.0.1:%d%s" % (self.port, path),
            method="GET" if form is None else "POST", body=body,
            headers={"Cookie": "; ".join(cookies)},
            follow_redirects=False, raise_error=False, **kwargs)

    async def run_job(self, owner_id: int | None = None, ids=None):
        job = self.store.start(owner_id or self.admin_id,
                               ids or [self.finished_id], ())
        await job.task
        return job

    async def test_everything_needs_all_the_permissions(self):
        # The job is the plain admin's own: only the permission refuses.
        job = await self.run_job(owner_id=self.plain_id)
        self.assertEqual(self.store.get(job.id, self.plain_id), job)

        for path, form in (
                ("/export", None),
                ("/export", [("contest_id", str(self.finished_id))]),
                ("/export/%s/status" % job.id, None),
                ("/export/%s/download" % job.id, None)):
            with self.subTest(path=path, post=form is not None):
                response = await self.fetch(path, self.plain, form)
                self.assertEqual(response.code, 403)
        # Only the job started by the test above exists.
        self.assertEqual(len(self.argv_calls), 1)

    async def test_the_page_lists_the_contests(self):
        response = await self.fetch("/export")

        self.assertEqual(response.code, 200)
        body = response.body.decode()
        self.assertIn("Export contests", body)
        self.assertIn(self.finished.name, body)
        self.assertIn("(running or in analysis mode)", body)

    async def test_only_full_admins_see_the_menu_entry(self):
        entry = '<a class="menu_link" href="./export">Export</a>'
        for cookie, shown in ((None, True), (self.plain, False)):
            response = await self.fetch("/contests", cookie)
            self.assertEqual(response.code, 200)
            self.assertEqual(entry in response.body.decode(), shown)

    async def test_the_form_needs_the_xsrf_token(self):
        response = await self.fetch(
            "/export", form=[("contest_id", str(self.finished_id))],
            xsrf=False)

        self.assertEqual(response.code, 403)
        self.assertEqual(self.argv_calls, [])

    async def test_the_choices_of_the_form_become_flags(self):
        form = [("contest_id", str(self.finished2_id)),
                ("contest_id", str(self.finished_id)),
                ("contest_id", str(self.finished2_id)),
                ("content", "no_files"), ("no_users", "1"),
                ("no_generated", "1")]
        with self.assertLogs(MODULE, "INFO") as logs:
            response = await self.fetch("/export", form=form)

        self.assertEqual(response.code, 302)
        (job,) = self.store.jobs_of(self.admin_id)
        self.assertEqual(response.headers["Location"],
                         "./export?job=" + job.id)
        self.assertEqual(
            self.argv_calls,
            [([self.finished_id, self.finished2_id], ("-F", "-G", "-X"))])
        self.assertIn("admin %d" % self.admin_id, logs.output[0])
        self.assertIn("('-F', '-G', '-X')", logs.output[0])

    async def test_the_owner_follows_the_job_and_downloads_the_zip(self):
        job = await self.run_job()

        status = await self.fetch("/export/%s/status" % job.id)
        self.assertEqual(status.code, 200)
        self.assertEqual(json.loads(status.body)["status"], "done")
        self.assertEqual(status.headers["Cache-Control"], "no-store")
        page = await self.fetch("/export?job=" + job.id)
        self.assertIn(job.name, page.body.decode())

        # Big enough that the server is still sending when the client gets
        # the first chunk: the socket buffers cannot take it all.
        with open(job.path, "wb") as f:
            f.write(b"0123456789abcdef" * 2 * 1024 * 1024)
        chunks = []
        slot_was_locked = []

        def on_chunk(chunk):
            slot_was_locked.append(base._get_request_slots().locked())
            chunks.append(chunk)

        # With one slot, the download itself would hold it while streaming.
        with self.request_limit(1), self.assertLogs(MODULE, "INFO") as logs:
            download = await self.fetch("/export/%s/download" % job.id,
                                        streaming_callback=on_chunk)
            await self.assert_all_slots_free("after the download")

        self.assertEqual(download.code, 200)
        self.assertEqual(download.headers["Content-Type"], "application/zip")
        self.assertEqual(
            download.headers["Content-Disposition"],
            'attachment; filename="%s"' % job.filename)
        with open(job.path, "rb") as f:
            self.assertEqual(b"".join(chunks), f.read())
        self.assertEqual(slot_was_locked[0], False)
        self.assertIn("admin %d" % self.admin_id, logs.output[0])
        self.assertIn(job.id, logs.output[0])

    async def test_other_admins_find_neither_state_nor_file(self):
        job = await self.run_job()

        for path in ("/export/%s/status" % job.id,
                     "/export/%s/download" % job.id):
            response = await self.fetch(path, self.other)
            self.assertEqual(response.code, 404, path)
        page = await self.fetch("/export?job=" + job.id, self.other)
        self.assertIn("The export was not found", page.body.decode())
        self.assertNotIn(job.name, page.body.decode())
        unknown = await self.fetch("/export/nope/download")
        self.assertEqual(unknown.code, 404)

    async def test_a_running_contest_needs_a_confirmation(self):
        for contest_id in (self.running_id, self.analysis_id):
            response = await self.fetch(
                "/export", form=[("contest_id", str(contest_id))])
            self.assertEqual(response.code, 200)
            self.assertIn(RUNNING_ERROR, response.body.decode())
        self.assertEqual(self.argv_calls, [])

        confirmed = await self.fetch("/export", form=[
            ("contest_id", str(self.running_id)), ("confirm_running", "1")])
        # Only one export runs at a time.
        await self.store.running_job().task
        finished = await self.fetch(
            "/export", form=[("contest_id", str(self.finished_id))])

        # A contest that ended needs no confirmation.
        self.assertEqual((confirmed.code, finished.code), (302, 302))
        self.assertEqual([ids for ids, _ in self.argv_calls],
                         [[self.running_id], [self.finished_id]])

    async def test_the_form_is_checked(self):
        for form, message in (
                ([], "choose at least one contest"),
                ([("contest_id", str(self.finished_id + 1000))],
                 "no longer exists")):
            response = await self.fetch("/export", form=form)
            self.assertEqual(response.code, 200)
            self.assertIn(message, response.body.decode())
        for form in ([("contest_id", "x")],
                     [("contest_id", str(self.finished_id)),
                      ("content", "everything")]):
            response = await self.fetch("/export", form=form)
            self.assertEqual(response.code, 400)
        self.assertEqual(self.argv_calls, [])

    async def test_only_one_export_runs_at_a_time(self):
        self.gated = True
        first = await self.fetch(
            "/export", form=[("contest_id", str(self.finished_id))])
        self.assertEqual(first.code, 302)

        second = await self.fetch(
            "/export", self.other,
            form=[("contest_id", str(self.finished_id))])

        self.assertEqual(second.code, 200)
        self.assertIn("an export is already running",
                      second.body.decode())
        self.assertEqual(len(self.argv_calls), 1)

    # The page and the job in the two languages. The page follows the
    # aws_language cookie of the request.

    async def test_the_page_in_english_and_in_spanish(self):
        # Scenarios: A page in English / A page in Spanish
        english = await self.fetch("/export", language="en")
        spanish = await self.fetch("/export", language="es")

        self.assertIn("Export contests", english.body.decode())
        self.assertNotIn("Exportar concursos", english.body.decode())
        self.assertIn("Exportar concursos", spanish.body.decode())
        self.assertIn("(en curso o en modo de análisis)",
                      spanish.body.decode())
        self.assertNotIn("Export contests", spanish.body.decode())

    async def test_the_errors_of_the_form_in_spanish(self):
        # Scenario: A page in Spanish (the errors of the form)
        for form, message in (
                ([], "elige al menos un concurso"),
                ([("contest_id", str(self.finished_id + 1000))],
                 "alguno de los concursos elegidos ya no existe"),
                ([("contest_id", str(self.running_id)),
                  ("contest_id", str(self.analysis_id))],
                 "Estos concursos están en curso o en su modo de "
                 "análisis: %s, %s. El volcado se lee sin congelar la "
                 "base de datos: puede salir inconsistente y la carga "
                 "puede afectar al concurso. Marca la confirmación para "
                 "exportarlos de todos modos." % tuple(sorted(
                     (self.running.name, self.analysis.name))))):
            response = await self.fetch("/export", form=form, language="es")
            self.assertEqual(response.code, 200)
            self.assertIn("<li>%s</li>" % message, response.body.decode())

    async def test_the_not_found_notice_and_the_busy_error_in_spanish(self):
        # Scenario: A notification (the notice of an unknown job) and
        # Scenario: A page in Spanish (the error of a second export)
        page = await self.fetch("/export?job=nope", language="es")
        self.assertIn("No se encontró la exportación: no existe o ya "
                      "expiró.", page.body.decode())

        self.gated = True
        form = [("contest_id", str(self.finished_id))]
        await self.fetch("/export", form=form)
        second = await self.fetch("/export", self.other, form, language="es")
        self.assertIn("<li>ya hay una exportación en curso; espera a que "
                      "termine</li>", second.body.decode())

    async def test_a_job_started_in_spanish_is_polled_in_spanish_in_english(
            self):
        # Scenario: A job polled by another admin. The same owner, with
        # English chosen on another tab, reads the Spanish error.
        self.exit_code = 3
        response = await self.fetch(
            "/export", form=[("contest_id", str(self.finished_id))],
            language="es")
        self.assertEqual(response.code, 302)
        (job,) = self.store.jobs_of(self.admin_id)
        await job.task

        status = await self.fetch("/export/%s/status" % job.id,
                                  language="en")
        self.assertEqual(
            json.loads(status.body)["error"],
            "la exportación falló (código de salida 3); no se generó nada")
        page = await self.fetch("/export?job=" + job.id, language="en")
        self.assertIn('data-messages="', page.body.decode())
        self.assertIn("Checking the state", page.body.decode())

    async def test_the_list_of_exports_names_the_states_in_two_languages(
            self):
        # Scenarios: A page in English / A page in Spanish
        done = await self.run_job()
        self.exit_code = 3
        failed = await self.run_job()
        for language, words in (("en", ("ready", "failed")),
                                ("es", ("lista", "falló"))):
            body = (await self.fetch("/export", language=language)
                    ).body.decode()
            self.assertIn("Your exports" if language == "en"
                          else "Tus exportaciones", body)
            for job, word in zip((done, failed), words):
                self.assertRegex(
                    body, r"%s</a></td>\s*<td>%s</td>" % (job.name, word))


class TestWipeAtStart(unittest.TestCase):

    def test_aws_removes_the_dumps_of_a_previous_process(self):
        self.addCleanup(set_parse_body_config, ParseBodyConfig())

        with mock.patch.object(server.EXPORT_JOBS, "wipe") as wipe:
            AdminWebServer(0)

        wipe.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
