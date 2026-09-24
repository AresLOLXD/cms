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

"""Regression test for the SQLAlchemy 2.0 migration dropping the implicit
backref save-update cascade: Submission.get_result_or_create() and
UserTest.get_result_or_create() used to rely on the cascade to get a
freshly-constructed SubmissionResult/UserTestResult into the session, but
2.0 removed cascade_backrefs for back_populates relationships. Without an
explicit session.add(), the first compilation of every new submission (or
user test) was silently dropped at commit and never actually persisted,
breaking the grading pipeline for all new work.

These tests call EvaluationService.write_results() -- the only place
results are first created in production -- for a submission/user test that
has NO existing result, then re-fetch from a fresh session/query to prove
the result row was actually written to the database.

"""

import gevent.monkey

gevent.monkey.patch_all()  # noqa

import unittest

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import Submission, UserTest
from cms.grading.Job import CompilationJob
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, Result


class TestWriteResultsCreatesNewResult(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(task=self.task, autojudge=True)
        self.task.active_dataset = self.dataset
        # write_results() opens its own DB session (SessionGen()), so the
        # fixture needs to be actually committed, not just flushed, to be
        # visible to it.
        self.session.commit()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    @staticmethod
    def _make_compilation_result(operation):
        job = CompilationJob(
            operation=operation,
            success=True,
            compilation_success=True,
            executables={},
            text=["Compilation succeeded"],
            sandboxes=[],
            sandbox_digests={},
            plus={
                "execution_time": 0.0,
                "execution_wall_clock_time": 0.0,
                "execution_memory": 0,
            })
        return Result(job, True)

    def test_submission_result_is_persisted_on_first_compilation(self):
        # No pre-existing SubmissionResult: get_result_or_create() has to
        # construct one from scratch inside write_results().
        submission = self.add_submission(self.task, self.participation)
        self.session.commit()

        operation = ESOperation(
            ESOperation.COMPILATION, submission.id, self.dataset.id)
        items = [(operation, self._make_compilation_result(operation))]

        service = EvaluationService(0)
        service.write_results(items)

        # Re-fetch from a fresh session/query to make sure the row was
        # actually committed to the database, not merely present on the
        # in-memory object graph.
        self.session.expire_all()
        fetched_submission = Submission.get_from_id(
            submission.id, self.session)
        result = fetched_submission.get_result(self.dataset)

        self.assertIsNotNone(result)
        self.assertTrue(result.compiled())
        self.assertTrue(result.compilation_succeeded())

    def test_user_test_result_is_persisted_on_first_compilation(self):
        # No pre-existing UserTestResult: get_result_or_create() has to
        # construct one from scratch inside write_results().
        user_test = self.add_user_test(self.task, self.participation)
        self.session.commit()

        operation = ESOperation(
            ESOperation.USER_TEST_COMPILATION, user_test.id, self.dataset.id)
        items = [(operation, self._make_compilation_result(operation))]

        service = EvaluationService(0)
        service.write_results(items)

        self.session.expire_all()
        fetched_user_test = UserTest.get_from_id(user_test.id, self.session)
        result = fetched_user_test.get_result(self.dataset)

        self.assertIsNotNone(result)
        self.assertTrue(result.compiled())
        self.assertTrue(result.compilation_succeeded())


if __name__ == "__main__":
    unittest.main()
