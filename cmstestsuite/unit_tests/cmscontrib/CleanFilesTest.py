#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juarez Martinez <aresulises8@hotmail.com>
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

"""Tests for cmsCleanFiles (clean_files)."""

import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.testidgenerator import unique_digest

from cmscontrib.CleanFiles import clean_files


class TestCleanFiles(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.delete_data()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def _run(self, store, dry_run):
        """Run clean_files on a fake store (a set of digests); return it."""
        with patch("cmscontrib.CleanFiles.FileCacher") as cacher_class:
            cacher = cacher_class.return_value
            cacher.list.side_effect = lambda: [(d, "") for d in store]
            cacher.get_size.return_value = 1
            cacher.delete.side_effect = store.discard
            clean_files(self.session, dry_run)
        return store

    def test_deletes_only_unreferenced_files(self):
        in_contest = self.add_statement(task=self.add_task(
            contest=self.add_contest())).digest
        # Task.contest_id is nullable: such a task is not an orphan.
        no_contest = self.add_statement(task=self.add_task()).digest
        testcase = self.add_testcase(dataset=self.add_dataset(
            task=self.add_task()))
        orphan = unique_digest()
        self.session.commit()
        kept = {in_contest, no_contest, testcase.input, testcase.output}

        store = self._run(kept | {orphan}, dry_run=True)
        self.assertEqual(store, kept | {orphan})

        store = self._run(store, dry_run=False)
        self.assertEqual(store, kept)

    def test_keeps_submission_files_of_a_task_without_contest(self):
        task = self.add_task(contest=self.add_contest())
        participation = self.add_participation(contest=task.contest)
        file_ = self.add_file(submission=self.add_submission(
            task=task, participation=participation))
        self.session.commit()
        task.contest = None
        self.session.commit()

        store = self._run({file_.digest, unique_digest()}, dry_run=False)

        self.assertEqual(store, {file_.digest})


if __name__ == "__main__":
    unittest.main()
