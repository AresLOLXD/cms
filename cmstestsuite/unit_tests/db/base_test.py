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

"""Targeted tests for cms.db.base.Base.get_from_id().

These cover two Review Focus gaps left undertested by the SQLAlchemy 2.0
migration: fetching by a composite primary key (independent of two-phase
evaluation, which was previously the only path exercising it) and the
ObjectDeletedError-to-None handling for an object that got expired and
then removed from the database out from under the session.

"""

import unittest

from sqlalchemy import text

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import Submission, SubmissionResult


class TestGetFromId(DatabaseMixin, unittest.TestCase):

    def test_composite_key(self):
        submission = self.add_submission()
        dataset = self.add_dataset(task=submission.task)
        submission_result = self.add_submission_result(submission, dataset)
        self.session.commit()

        # Make sure we're not just handed back the identity-mapped object
        # without a real lookup.
        self.session.expire_all()

        fetched = SubmissionResult.get_from_id(
            (submission.id, dataset.id), self.session)

        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.submission_id, submission.id)
        self.assertEqual(fetched.dataset_id, dataset.id)
        self.assertEqual(fetched.submission, submission_result.submission)

    def test_composite_key_missing_returns_none(self):
        submission = self.add_submission()
        dataset = self.add_dataset(task=submission.task)
        self.session.commit()

        fetched = SubmissionResult.get_from_id(
            (submission.id, dataset.id), self.session)

        self.assertIsNone(fetched)

    def test_object_deleted_error_returns_none(self):
        submission = self.add_submission()
        self.session.commit()
        submission_id = submission.id

        # Expire the object so the next access needs to refresh it from
        # the database, then remove the row out from under the session
        # (bypassing the ORM, so the session's identity map doesn't know
        # about the deletion).
        self.session.expire(submission)
        self.session.execute(
            text("DELETE FROM submissions WHERE id = :id"),
            {"id": submission_id})
        self.session.commit()

        # get_from_id() must catch the resulting ObjectDeletedError and
        # return None, rather than letting it propagate.
        fetched = Submission.get_from_id(submission_id, self.session)

        self.assertIsNone(fetched)


if __name__ == "__main__":
    unittest.main()
