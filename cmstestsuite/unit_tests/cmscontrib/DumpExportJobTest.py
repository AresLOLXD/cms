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

"""Round trip of the dump that AWS offers: real exporter, zip, importer.

The AWS job runs cmscontrib.DumpExporter in a child process and packs its
directory with pack_zip. This test runs the same command (export_argv) and
the same packing, then imports the zip with the real cmsDumpImporter,
which only accepts an archive with exactly one root directory. It lives
with the other cmscontrib tests because importing DumpImporter patches
threading and socket with gevent, which hangs the asyncio tests that run
after it in the same process (see docker/_cms-test-internal.sh).

"""

import os
import subprocess
import unittest
from datetime import date

from sqlalchemy import select

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.filesystemmixin import FileSystemMixin

from cms.db import Contest, Statement, User
from cms.db.filecacher import FileCacher
from cmscommon.digest import bytes_digest
from cmscontrib.DumpImporter import DumpImporter
from cms.server.admin.exportjobs import dump_name, export_argv, pack_zip


class TestDumpExportJob(DatabaseMixin, FileSystemMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.content = b"statement"
        self.digest = bytes_digest(self.content)
        self.add_fsobject(self.digest, self.content)

        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.add_statement(task=self.task, digest=self.digest)
        # A contest that is not chosen must stay out of the dump.
        self.add_contest()
        self.session.commit()
        self.contest_name = self.contest.name
        self.username = self.participation.user.username

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def test_the_zip_of_the_real_exporter_imports_back(self):
        name = dump_name([self.contest.id], date.today())
        # As the job does, with the environment of this process (the
        # configuration of the database included).
        subprocess.run(
            export_argv([self.contest.id], ("-G",),
                        os.path.join(self.base_dir, name)),
            check=True, stdin=subprocess.DEVNULL)
        archive = pack_zip(self.base_dir, name)
        self.delete_data()

        self.assertTrue(DumpImporter(
            False, archive, load_files=True, load_model=True,
            skip_generated=False, skip_submissions=False,
            skip_user_tests=False, skip_users=False).do_import())

        self.session.expire_all()
        imported = self.session.execute(
            select(Contest).filter(Contest.name == self.contest_name)
        ).scalar_one()
        self.assertEqual(
            [s.digest for s in self.session.execute(
                select(Statement)).scalars()], [self.digest])
        self.assertEqual(
            self.session.execute(select(User.username)).scalars().all(),
            [self.username])
        self.assertEqual(len(imported.tasks), 1)
        self.assertEqual(self.session.execute(
            select(Contest.name)).scalars().all(), [self.contest_name])
        with FileCacher().get_file(self.digest) as stored:
            self.assertEqual(stored.read(), self.content)


if __name__ == "__main__":
    unittest.main()
