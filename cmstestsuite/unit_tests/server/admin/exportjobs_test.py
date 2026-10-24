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

"""Tests for the contest dump export jobs of AWS.

The exporter is replaced by a small Python script that writes a fake
dump, so these tests need no database. They must never import
cmscontrib.DumpExporter: it patches threading and socket with gevent,
which hangs every asyncio test that runs after it in the same process.

"""

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from unittest import mock

from cms.server.admin import exportjobs
from cms.server.admin.exportjobs import JOB_TTL, ExportJobStore, \
    dump_name, export_argv, pack_zip

OWNER_ID = 3
# Writes a dump like the exporter's in the directory it is given, then
# waits for the gate file (if any) and exits with the given code.
FAKE_EXPORTER = """
import os, sys, time
target, code, gate = sys.argv[1], int(sys.argv[2]), sys.argv[3]
os.mkdir(target)
os.mkdir(os.path.join(target, "files"))
with open(os.path.join(target, "contest.json"), "w") as f:
    f.write("{}")
with open(os.path.join(target, "files", "abc"), "w") as f:
    f.write("blob")
with open(os.path.join(target, "tmpdir"), "w") as f:
    f.write(os.environ["TMPDIR"])
while gate and not os.path.exists(gate):
    time.sleep(0.01)
sys.exit(code)
"""


def fake_argv(code: int = 0, gate: str = ""):
    """Return an argv builder that runs the fake exporter."""
    def build(contest_ids, flags, target):
        return [sys.executable, "-c", FAKE_EXPORTER, target, str(code), gate]
    return build


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestPureFunctions(unittest.TestCase):

    def test_flags_and_contests_become_the_argv_of_the_exporter(self):
        argv = export_argv([3, 4], ("-F", "-G", "-X"), "/data/dump_3-4")

        self.assertEqual(argv, [
            "nice", "-n", "19", sys.executable, "-m",
            "cmscontrib.DumpExporter", "-c", "3", "4", "-F", "-G", "-X",
            "/data/dump_3-4"])

    def test_the_name_lists_the_contests_until_there_are_too_many(self):
        today = date(2026, 10, 24)

        self.assertEqual(dump_name([4, 3], today), "dump_3-4_2026-10-24")
        self.assertEqual(dump_name(list(range(1, 40)), today),
                         "dump_39-contests_2026-10-24")

    def test_the_zip_has_exactly_one_root_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            name = "dump_1_2026-10-24"
            os.makedirs(os.path.join(directory, name, "files"))
            for path in ("contest.json", "files/abc"):
                with open(os.path.join(directory, name, path), "w") as f:
                    f.write("x")

            archive = pack_zip(directory, name)

            self.assertEqual(archive, os.path.join(directory, name + ".zip"))
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
            self.assertIn(name + "/contest.json", names)
            self.assertIn(name + "/files/abc", names)
            self.assertTrue(all(n.startswith(name + "/") for n in names))
            # The staging directory is gone.
            self.assertEqual(os.listdir(directory), [name + ".zip"])


class TestExportJobStore(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.directory = os.path.join(tempfile.mkdtemp(), "aws-exports")
        self.addCleanup(shutil.rmtree, os.path.dirname(self.directory),
                        ignore_errors=True)
        self.clock = FakeClock()
        # The disk of the machine that runs the tests is not the subject.
        patcher = mock.patch.object(exportjobs, "MIN_FREE_BYTES", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_store(self, **kwargs) -> ExportJobStore:
        return ExportJobStore(self.directory, self.clock, **kwargs)

    async def test_a_successful_export_leaves_a_zip_to_download(self):
        store = self.make_store(build_argv=fake_argv())

        job = store.start(OWNER_ID, [1, 2], ("-G",))
        self.assertEqual(job.status, "running")
        await job.task

        self.assertEqual(job.status, "done")
        self.assertIsNone(job.error)
        self.assertEqual(job.size, os.path.getsize(job.path))
        self.assertEqual(os.listdir(job.directory), [job.filename])
        with zipfile.ZipFile(job.path) as zf:
            # The child gets the job directory as its TMPDIR.
            self.assertEqual(zf.read(job.name + "/tmpdir").decode(),
                             job.directory)
        # The paths never reach the page.
        self.assertNotIn(self.directory, str(job.as_json()))
        self.assertIs(store.get(job.id, OWNER_ID), job)

    async def test_a_failed_export_removes_its_directory(self):
        store = self.make_store(build_argv=fake_argv(code=3))

        job = store.start(OWNER_ID, [1], ())
        await job.task

        self.assertEqual(job.status, "error")
        self.assertIn("3", job.error)
        self.assertEqual(os.listdir(self.directory), [])
        # The admin can still ask why it failed.
        self.assertIs(store.get(job.id, OWNER_ID), job)

    async def test_a_second_export_is_refused_until_the_first_ends(self):
        gate = os.path.join(os.path.dirname(self.directory), "gate")
        store = self.make_store(build_argv=fake_argv(gate=gate))
        first = store.start(OWNER_ID, [1], ())

        with self.assertRaises(ValueError):
            store.start(OWNER_ID + 1, [2], ())
        self.assertIs(store.running_job(), first)

        open(gate, "w").close()
        await first.task
        self.assertIsNone(store.running_job())
        second = store.start(OWNER_ID, [2], ())
        await second.task
        self.assertEqual(second.status, "done")

    async def test_the_file_is_deleted_when_the_job_expires(self):
        store = self.make_store(build_argv=fake_argv())
        job = store.start(OWNER_ID, [1], ())
        await job.task
        self.clock.now += JOB_TTL - 1
        self.assertIs(store.get(job.id, OWNER_ID), job)
        self.assertTrue(os.path.exists(job.path))

        self.clock.now += 2

        self.assertIsNone(store.get(job.id, OWNER_ID))
        self.assertFalse(os.path.exists(job.directory))

    async def test_the_ttl_counts_from_the_end_not_from_the_start(self):
        gate = os.path.join(os.path.dirname(self.directory), "gate")
        store = self.make_store(build_argv=fake_argv(gate=gate))
        job = store.start(OWNER_ID, [1], ())
        # It runs for longer than the TTL: it must not be forgotten.
        self.clock.now += JOB_TTL + 10
        self.assertIs(store.get(job.id, OWNER_ID), job)

        open(gate, "w").close()
        await job.task

        self.assertIs(store.get(job.id, OWNER_ID), job)

    async def test_only_the_owner_finds_a_job(self):
        store = self.make_store(build_argv=fake_argv())
        job = store.start(OWNER_ID, [1], ())
        await job.task

        self.assertIsNone(store.get(job.id, OWNER_ID + 1))
        self.assertIsNone(store.get("nope", OWNER_ID))
        self.assertEqual(store.jobs_of(OWNER_ID), [job])
        self.assertEqual(store.jobs_of(OWNER_ID + 1), [])

    async def test_too_little_free_disk_refuses_before_starting(self):
        store = self.make_store(build_argv=fake_argv())

        with mock.patch.object(exportjobs, "MIN_FREE_BYTES", 10 ** 30), \
                self.assertRaises(ValueError) as caught:
            store.start(OWNER_ID, [1], ())

        self.assertIn("espacio", str(caught.exception))
        self.assertEqual(os.listdir(self.directory), [])
        self.assertIsNone(store.running_job())

    async def test_cancelling_kills_the_exporter_and_removes_the_files(self):
        gate = os.path.join(os.path.dirname(self.directory), "never")
        store = self.make_store(build_argv=fake_argv(gate=gate))
        job = store.start(OWNER_ID, [1], ())
        # Let the child start and write its files.
        for _ in range(500):
            await asyncio.sleep(0.01)
            if os.path.exists(os.path.join(job.directory, job.name,
                                           "tmpdir")):
                break

        job.task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await job.task

        self.assertEqual(os.listdir(self.directory), [])

    def test_wipe_removes_what_a_previous_process_left(self):
        os.makedirs(os.path.join(self.directory, "left-over"))

        self.make_store().wipe()

        self.assertFalse(os.path.exists(self.directory))
        # A directory that is already gone is fine.
        self.make_store().wipe()


if __name__ == "__main__":
    unittest.main()
