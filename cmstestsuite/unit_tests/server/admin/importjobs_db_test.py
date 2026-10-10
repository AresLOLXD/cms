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

"""Tests for a bulk import job of AWS run against the database.

importjobs_test.py patches the session, the planning, the hashing and
the writing. These tests run a real job from end to end instead: the
run-time plan, bcrypt (at its lowest cost), the writing and the commit.

"""

import contextlib
import dataclasses
import json
import os
import re
import threading
import time
import unittest
from unittest import mock

from sqlalchemy import select

import cms.server.admin
from cms.db import Participation, SessionGen, User
from cms.server.admin.bulkimport import ImportRow
from cms.server.admin.importjobs import APPLY_FAILED, ImportJobStore
from cmscommon.crypto import build_password, validate_password
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

MODULE = "cms.server.admin.importjobs"
OWNER_ID = 3
# The rows import an existing participant and a new user. The values are
# in constants so that no source line a traceback prints contains them.
EXISTING_USERNAME = "ana-existing"
NEW_USERNAME = "carla-new"
EXISTING_PASSWORD = "dia1-ana-pw"
NEW_PASSWORD = "dia1-carla-pw"
# The keys of the summary that the script of the import page reads.
PAGE = os.path.join(os.path.dirname(cms.server.admin.__file__),
                    "templates", "contest_users_import.html")


class TestImportJobAgainstTheDatabase(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.addCleanup(self.delete_data)
        patcher = mock.patch("cmscommon.crypto.BCRYPT_ROUNDS", 4)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.contest = self.add_contest()
        self.team = self.add_team(code="JAL", name="Jalisco")
        existing = self.add_user(username=EXISTING_USERNAME,
                                 password=build_password("account-ana"))
        self.add_participation(user=existing, contest=self.contest,
                               password=build_password("old-ana"))
        # The job reads the database in sessions of its own.
        self.session.commit()
        self.rows = [
            ImportRow(line=2, username=EXISTING_USERNAME, first_name="Ana",
                      last_name="López", password=EXISTING_PASSWORD,
                      team="JAL", group=None),
            ImportRow(line=3, username=NEW_USERNAME, first_name="Carla",
                      last_name="Ruiz", password=NEW_PASSWORD, team=None,
                      group=None)]
        self.on_done = mock.Mock()

    def run_job(self):
        """Start a job for the rows and wait, at most 30 s, for its end."""
        job = ImportJobStore().start(OWNER_ID, self.contest.id, self.rows,
                                     self.on_done)
        self.wait(job)
        return job

    def wait(self, job) -> None:
        """Wait, at most 30 s, until the thread of the job is over."""
        name = "aws-import-%s" % job.id[:8]
        deadline = time.monotonic() + 30
        while any(t.name == name for t in threading.enumerate()):
            if time.monotonic() > deadline:
                self.fail("the thread of the import job did not finish")
            time.sleep(0.02)

    def snapshot(self):
        """Read what is committed, in a session of its own."""
        with SessionGen() as session:
            users = session.execute(
                select(User.username, User.first_name, User.last_name,
                       User.password).order_by(User.username)).all()
            participations = session.execute(
                select(User.username, Participation.contest_id,
                       Participation.password, Participation.team_id,
                       Participation.group_id)
                .join(Participation.user).order_by(User.username)).all()
        return users, participations

    def test_a_job_imports_the_rows(self):
        job = self.run_job()

        self.assertEqual((job.status, job.error), ("done", None))
        self.assertEqual((job.processed, job.total), (2, 2))
        self.assertEqual(job.summary, {
            "usuarios_nuevos": 1, "usuarios_actualizados": 1,
            "participaciones_nuevas": 1, "participaciones_actualizadas": 1,
            "equipos_quitados": 0, "movidas_al_grupo_principal": 0})
        # The page reads exactly these keys of the summary.
        with open(PAGE, encoding="utf-8") as page:
            read_by_the_page = set(re.findall(r"s\.summary\.(\w+)",
                                              page.read()))
        self.assertEqual(read_by_the_page, set(job.summary))
        self.on_done.assert_called_once_with()
        # No password reaches what the page gets.
        state = json.dumps(job.as_json())
        self.assertNotIn(EXISTING_PASSWORD, state)
        self.assertNotIn(NEW_PASSWORD, state)

        with SessionGen() as session:
            users = {u.username: u for u in session.execute(
                select(User)).scalars()}
            participations = {p.user.username: p for p in session.execute(
                select(Participation).filter(
                    Participation.contest_id == self.contest.id)).scalars()}
            # The new user has an account hash of its own, which the day
            # password does not open.
            new_user = users[NEW_USERNAME]
            self.assertEqual((new_user.first_name, new_user.last_name),
                             ("Carla", "Ruiz"))
            self.assertTrue(new_user.password.startswith("bcrypt:"))
            self.assertFalse(validate_password(new_user.password,
                                               NEW_PASSWORD))
            # Its participation has the day password, hashed.
            new_participation = participations[NEW_USERNAME]
            self.assertTrue(new_participation.password.startswith("bcrypt:"))
            self.assertTrue(validate_password(new_participation.password,
                                              NEW_PASSWORD))
            self.assertIsNone(new_participation.team_id)
            self.assertEqual(new_participation.group_id,
                             self.contest.main_group_id)
            # The existing participant keeps the account password and gets
            # the day password and the team of the row.
            existing_user = users[EXISTING_USERNAME]
            self.assertEqual(existing_user.password,
                             build_password("account-ana"))
            existing_participation = participations[EXISTING_USERNAME]
            self.assertTrue(validate_password(
                existing_participation.password, EXISTING_PASSWORD))
            self.assertEqual(existing_participation.team_id, self.team.id)

    def test_a_global_job_imports_the_accounts(self):
        rows = [dataclasses.replace(row, team=None) for row in self.rows]
        job = ImportJobStore().start(OWNER_ID, None, rows, self.on_done)
        self.wait(job)

        self.assertEqual((job.status, job.error), ("done", None))
        self.assertEqual(job.summary, {"usuarios_nuevos": 1,
                                       "usuarios_actualizados": 1})
        # The page reads these keys of the summary, among others.
        with open(PAGE, encoding="utf-8") as page:
            read_by_the_page = set(re.findall(r"s\.summary\.(\w+)",
                                              page.read()))
        self.assertLessEqual(set(job.summary), read_by_the_page)
        self.assertNotIn(NEW_PASSWORD, json.dumps(job.as_json()))
        with SessionGen() as session:
            users = {u.username: u for u in session.execute(
                select(User)).scalars()}
            self.assertTrue(validate_password(
                users[NEW_USERNAME].password, NEW_PASSWORD))
            self.assertTrue(validate_password(
                users[EXISTING_USERNAME].password, EXISTING_PASSWORD))
            # The participation and its day password are left alone.
            participation = session.execute(
                select(Participation).join(Participation.user)
                .filter(User.username == EXISTING_USERNAME)).scalar_one()
            self.assertTrue(validate_password(participation.password,
                                              "old-ana"))
            self.assertEqual(session.query(Participation).count(), 1)

    def test_a_commit_that_fails_leaves_nothing_written(self):
        before = self.snapshot()

        @contextlib.contextmanager
        def session_whose_commit_conflicts():
            # What an import of the same new user committed meanwhile by
            # something else would cause: a real unique violation.
            with SessionGen() as session:
                commit = session.commit

                def conflicting_commit():
                    session.add(User(username=NEW_USERNAME,
                                     first_name="Otra", last_name="Carla",
                                     password=build_password("x")))
                    commit()
                session.commit = conflicting_commit
                yield session

        with mock.patch("%s.SessionGen" % MODULE,
                        session_whose_commit_conflicts), \
                self.assertLogs(MODULE, level="ERROR") as logs:
            job = self.run_job()

        self.assertEqual((job.status, job.error), ("error", APPLY_FAILED))
        self.assertIsNone(job.summary)
        self.on_done.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        # The log names the class of the error, and nothing the rows or
        # the database error carry.
        output = "\n".join(logs.output)
        self.assertIn("IntegrityError", output)
        for value in (NEW_USERNAME, EXISTING_USERNAME, NEW_PASSWORD,
                      EXISTING_PASSWORD):
            self.assertNotIn(value, output)


if __name__ == "__main__":
    unittest.main()
