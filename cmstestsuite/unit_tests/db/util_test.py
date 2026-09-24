#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""Tests for cms.db.util.enumerate_files.

These exercise the SQLAlchemy 2.0 select()/.with_only_columns() rewrite
of enumerate_files: since it has no coverage elsewhere in the suite, we
check that its explicit joins survive .with_only_columns() and that the
right digests come back (and only those), across a few of its branches.

"""

import unittest

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.testidgenerator import unique_digest, \
    unique_unicode_id

from cms.db import Digest, UserTestExecutable
from cms.db.util import enumerate_files


class TestEnumerateFiles(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        # enumerate_files(), called without a contest filter, looks at
        # every contest in the DB: start each test from a clean slate so
        # tests don't see each other's committed data.
        self.delete_data()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def _populate_contest(self, contest=None):
        """Build a contest with one task exercising every kind of file
        enumerate_files() looks at, and return (contest, digests).

        digests is the set of every digest that should be returned by
        enumerate_files() for this contest (assuming skip_generated is
        not set).

        """
        contest = contest if contest is not None else self.add_contest()
        task = self.add_task(contest=contest)
        statement = self.add_statement(task=task)
        dataset = self.add_dataset(task=task)
        manager = self.add_manager(dataset=dataset)
        testcase = self.add_testcase(dataset=dataset)

        participation = self.add_participation(contest=contest)
        submission = self.add_submission(task=task, participation=participation)
        file_ = self.add_file(submission=submission)
        submission_result = self.add_submission_result(
            submission=submission, dataset=dataset)
        executable = self.add_executable(submission_result=submission_result)

        user_test = self.add_user_test(task=task, participation=participation)
        user_test_file = self.add_user_test_file(user_test=user_test)
        user_test_manager = self.add_user_test_manager(user_test=user_test)
        user_test_result = self.add_user_test_result(
            user_test=user_test, dataset=dataset, output=unique_digest())
        user_test_executable = UserTestExecutable(
            user_test_result=user_test_result,
            filename=unique_unicode_id(),
            digest=unique_digest())
        self.session.add(user_test_executable)

        self.session.commit()

        digests = {
            statement.digest, manager.digest,
            testcase.input, testcase.output,
            file_.digest, executable.digest,
            user_test.input, user_test_file.digest, user_test_manager.digest,
            user_test_executable.digest, user_test_result.output,
        }
        # skip_generated also skips the UserTestResult.output query branch
        # (it's gated by the same "if not skip_generated" as the two
        # executable branches), not just the two executable branches.
        generated_digests = {
            executable.digest, user_test_executable.digest,
            user_test_result.output,
        }
        return contest, digests, generated_digests

    def test_returns_all_digests_with_explicit_joins_preserved(self):
        contest, digests, _ = self._populate_contest()

        result = enumerate_files(self.session)

        # If .with_only_columns() had dropped any of the explicit joins
        # (e.g. because it recomputed the FROM clause from the selected
        # column alone), this would either raise or come back with a
        # cartesian-product / wrong-table result instead of exactly the
        # digests we created.
        self.assertEqual(result, digests)

    def test_filters_by_contest(self):
        contest1, digests1, _ = self._populate_contest()
        contest2, digests2, _ = self._populate_contest()
        self.assertTrue(digests1.isdisjoint(digests2))

        result = enumerate_files(self.session, contest=contest1)

        self.assertEqual(result, digests1)

    def test_skip_generated_excludes_executables_and_output(self):
        contest, digests, generated_digests = self._populate_contest()

        result = enumerate_files(self.session, skip_generated=True)

        self.assertEqual(result, digests - generated_digests)
        # Sanity check: skip_generated actually excluded something.
        self.assertTrue(generated_digests)

    def test_discards_tombstone_digest(self):
        contest, digests, _ = self._populate_contest()
        task = self.add_task(contest=contest)
        dataset = self.add_dataset(task=task)
        manager = self.add_manager(dataset=dataset, digest=Digest.TOMBSTONE)
        self.session.commit()

        result = enumerate_files(self.session, contest=contest)

        self.assertNotIn(Digest.TOMBSTONE, result)
        self.assertEqual(result, digests | {manager.digest} - {Digest.TOMBSTONE})


if __name__ == "__main__":
    unittest.main()
