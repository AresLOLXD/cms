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

"""Tests for the in-memory bulk import jobs of AWS.

The session, the planning, the hashing and the writing are all patched,
so these tests need no database: they check the life of a job (its
status, progress and eviction), not what an import writes.

"""

import threading
import time
import unittest
from unittest import mock

from cms.server.admin.bulkimport import ImportPlan, ImportRow
from cms.server.admin.importjobs import APPLY_FAILED, JOB_TTL, \
    ImportJobStore

MODULE = "cms.server.admin.importjobs"
CONTEST_ID = 7
OWNER_ID = 3
# What the tests must never find in a log: a username and a password,
# as the message of a database error would carry them. They are kept in
# constants so that the source line that raises them, which a traceback
# prints, does not contain them.
LEAK_USERNAME = "ana-leak-check"
LEAK_PASSWORD = "pw-secret-leak"
LEAK_MESSAGE = "%s %s" % (LEAK_USERNAME, LEAK_PASSWORD)
HASHES = {"user0": ("participation-hash", "account-hash")}


def make_rows(count: int = 3) -> list[ImportRow]:
    return [ImportRow(line=i + 2, username="user%d" % i, first_name="Name",
                      last_name="Last", password="pw%d" % i, team=None,
                      group=None)
            for i in range(count)]


def make_plan() -> ImportPlan:
    return ImportPlan(new_users=["user0"], updated_users=["user1", "user2"],
                      new_participations=["user0", "user1"],
                      updated_participations=["user2"], teams={}, groups={},
                      main_group_id=1)


class ImportJobsTestCase(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.rows = make_rows()
        self.plan = make_plan()
        self.session = mock.MagicMock()
        self.on_done = mock.Mock()
        for name in ("SessionGen", "plan_import", "hash_passwords",
                     "apply_import"):
            patcher = mock.patch("%s.%s" % (MODULE, name))
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.SessionGen.return_value.__enter__.return_value = self.session
        self.plan_import.return_value = (self.plan, [])
        self.hash_passwords.side_effect = self.hash_every_row
        self.apply_import.return_value = None

    @staticmethod
    def hash_every_row(rows, new_users, progress):
        for _ in rows:
            progress()
        return HASHES

    def wait_for_job(self, job) -> None:
        """Wait, at most 5 s, until the thread of the job is over."""
        name = "aws-import-%s" % job.id[:8]
        deadline = time.monotonic() + 5
        while any(t.name == name for t in threading.enumerate()):
            if time.monotonic() > deadline:
                self.fail("the thread of the import job did not finish")
            time.sleep(0.01)


class TestJobRuns(ImportJobsTestCase):

    def test_job_runs_to_done(self):
        events = []
        self.apply_import.side_effect = lambda *a: events.append("apply")
        self.session.commit.side_effect = lambda: events.append("commit")
        self.on_done.side_effect = lambda: events.append("on_done")
        store = ImportJobStore()

        job = store.start(OWNER_ID, CONTEST_ID, self.rows, self.on_done)
        self.wait_for_job(job)

        self.assertEqual(job.status, "done")
        self.assertEqual(job.processed, len(self.rows))
        self.assertEqual(job.total, len(self.rows))
        self.assertEqual(job.summary, self.plan.summary())
        self.assertIsNone(job.error)
        # The notification goes after the commit, once, and the commit
        # after the writing.
        self.assertEqual(events, ["apply", "commit", "on_done"])
        self.session.commit.assert_called_once_with()
        self.on_done.assert_called_once_with()
        self.plan_import.assert_called_once_with(
            self.session, CONTEST_ID, self.rows)
        self.hash_passwords.assert_called_once_with(
            self.rows, {"user0"}, mock.ANY)
        self.apply_import.assert_called_once_with(
            self.session, CONTEST_ID, self.rows, self.plan, HASHES)

    def test_apply_failure_is_an_error_without_row_data_in_the_log(self):
        def failing_apply(*args):
            raise RuntimeError(LEAK_MESSAGE)
        self.apply_import.side_effect = failing_apply
        store = ImportJobStore()

        with self.assertLogs(MODULE, level="ERROR") as logs:
            job = store.start(OWNER_ID, CONTEST_ID, self.rows, self.on_done)
            self.wait_for_job(job)

        self.assertEqual(job.status, "error")
        self.assertEqual(job.error, APPLY_FAILED)
        self.assertEqual(
            job.error, "no se pudo aplicar la importación; no se guardó nada")
        self.assertIsNone(job.summary)
        self.session.commit.assert_not_called()
        self.on_done.assert_not_called()
        # The log says which contest and which kind of error, and the
        # frames to find it, but nothing the exception carries.
        self.assertEqual(len(logs.records), 1)
        output = "\n".join(logs.output)
        self.assertIn(str(CONTEST_ID), output)
        self.assertIn("RuntimeError", output)
        self.assertIn("in failing_apply", output)
        for line in logs.output + [r.getMessage() for r in logs.records]:
            self.assertNotIn(LEAK_USERNAME, line)
            self.assertNotIn(LEAK_PASSWORD, line)
        # Another handler must not be able to format the exception either.
        self.assertIsNone(logs.records[0].exc_info)

    def test_plan_errors_at_run_time(self):
        # The database changed since the file was validated.
        self.plan_import.return_value = (
            None, ["fila 2: el equipo JAL no existe",
                   "fila 3: el grupo A no existe en este concurso"])
        store = ImportJobStore()

        job = store.start(OWNER_ID, CONTEST_ID, self.rows, self.on_done)
        self.wait_for_job(job)

        self.assertEqual(job.status, "error")
        self.assertEqual(job.error,
                         "fila 2: el equipo JAL no existe; "
                         "fila 3: el grupo A no existe en este concurso")
        self.hash_passwords.assert_not_called()
        self.apply_import.assert_not_called()
        self.session.commit.assert_not_called()
        self.on_done.assert_not_called()

    def test_on_done_failure_keeps_the_saved_import_done(self):
        self.on_done.side_effect = RuntimeError(LEAK_MESSAGE)
        store = ImportJobStore()

        with self.assertLogs(MODULE, level="WARNING") as logs:
            job = store.start(OWNER_ID, CONTEST_ID, self.rows, self.on_done)
            self.wait_for_job(job)

        # The import was committed, so the job is done, not an error.
        self.assertEqual(job.status, "done")
        self.assertEqual(job.summary, self.plan.summary())
        self.assertIsNone(job.error)
        self.session.commit.assert_called_once_with()
        self.on_done.assert_called_once_with()
        # The warning says the import was saved and which contest and
        # error class, but nothing the exception carries.
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].levelname, "WARNING")
        output = "\n".join(logs.output)
        self.assertIn(str(CONTEST_ID), output)
        self.assertIn("RuntimeError", output)
        self.assertIn("ProxyService", output)
        self.assertIsNone(logs.records[0].exc_info)
        self.assertNotIn(LEAK_USERNAME, output)
        self.assertNotIn(LEAK_PASSWORD, output)


class TestStore(ImportJobsTestCase):

    def test_one_running_job_per_contest(self):
        started = threading.Event()
        release = threading.Event()
        # Never leave a job thread blocked, even if the test fails.
        self.addCleanup(release.set)

        def blocking_hash(rows, new_users, progress):
            progress()
            started.set()
            release.wait(5)
            return HASHES
        self.hash_passwords.side_effect = blocking_hash
        store = ImportJobStore()

        job = store.start(OWNER_ID, CONTEST_ID, self.rows, self.on_done)
        self.assertTrue(started.wait(5))
        self.assertEqual((job.status, job.processed, job.total),
                         ("running", 1, len(self.rows)))
        with self.assertRaises(ValueError) as raised:
            store.start(OWNER_ID + 1, CONTEST_ID, self.rows, self.on_done)
        self.assertEqual(str(raised.exception),
                         "ya hay una importación en curso para este concurso")
        # Another contest is not blocked.
        other = store.start(OWNER_ID, CONTEST_ID + 1, self.rows,
                            self.on_done)

        release.set()
        self.wait_for_job(job)
        self.wait_for_job(other)
        self.assertEqual((job.status, other.status), ("done", "done"))
        # A finished job does not block its contest any more.
        again = store.start(OWNER_ID, CONTEST_ID, self.rows, self.on_done)
        self.wait_for_job(again)
        self.assertEqual(again.status, "done")

    def test_running_job_is_the_running_job_of_the_contest(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def blocking_hash(rows, new_users, progress):
            started.set()
            release.wait(5)
            return HASHES
        self.hash_passwords.side_effect = blocking_hash
        store = ImportJobStore()
        self.assertIsNone(store.running_job(CONTEST_ID))

        job = store.start(OWNER_ID, CONTEST_ID, self.rows, self.on_done)
        self.assertTrue(started.wait(5))

        # It is found whoever asks: the caller compares the owner.
        self.assertIs(store.running_job(CONTEST_ID), job)
        self.assertIsNone(store.running_job(CONTEST_ID + 1))

        release.set()
        self.wait_for_job(job)
        # A finished job is not a running one.
        self.assertEqual(job.status, "done")
        self.assertIsNone(store.running_job(CONTEST_ID))

    def test_running_job_forgets_an_expired_job(self):
        now = [1000.0]
        store = ImportJobStore(clock=lambda: now[0])
        # A job that never ends: its thread is not run.
        with mock.patch.object(ImportJobStore, "_run"):
            job = store.start(OWNER_ID, CONTEST_ID, self.rows, self.on_done)
        self.assertIs(store.running_job(CONTEST_ID), job)

        # start() would let a new job in at this point, so it is not
        # running any more.
        now[0] += JOB_TTL + 1
        self.assertIsNone(store.running_job(CONTEST_ID))

    def test_owner_check(self):
        store = ImportJobStore()
        job = store.start(OWNER_ID, CONTEST_ID, self.rows, self.on_done)
        self.wait_for_job(job)

        self.assertIsNone(store.get(job.id, OWNER_ID + 1))
        self.assertIsNone(store.get("unknown", OWNER_ID))
        self.assertIs(store.get(job.id, OWNER_ID), job)

    def test_jobs_expire_after_the_ttl(self):
        now = [1000.0]
        store = ImportJobStore(clock=lambda: now[0])
        job = store.start(OWNER_ID, CONTEST_ID, self.rows, self.on_done)
        self.wait_for_job(job)

        now[0] += JOB_TTL - 1
        self.assertIs(store.get(job.id, OWNER_ID), job)
        now[0] += 2
        self.assertIsNone(store.get(job.id, OWNER_ID))
        # It is gone from the store, not just hidden from get().
        self.assertNotIn(job.id, store._jobs)


class TestAsJson(ImportJobsTestCase):

    def test_as_json_has_only_the_progress(self):
        store = ImportJobStore()
        job = store.start(OWNER_ID, CONTEST_ID, self.rows, self.on_done)
        self.wait_for_job(job)

        self.assertEqual(job.as_json(), {
            "status": "done", "processed": len(self.rows),
            "total": len(self.rows), "summary": self.plan.summary(),
            "error": None})


if __name__ == "__main__":
    unittest.main()
