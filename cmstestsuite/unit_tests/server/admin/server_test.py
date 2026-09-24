#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 The CMS development team
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

"""Tests for AdminWebServer.submissions_status.

"""

import unittest

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.server.admin.server import AdminWebServer


class TestSubmissionsStatus(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(task=self.task)
        self.task.active_dataset = self.dataset

        # A submission with no result at all: it hasn't started compiling.
        self.add_submission(self.task, self.participation)

        # A submission whose result exists but hasn't been compiled yet.
        submission = self.add_submission(self.task, self.participation)
        self.add_submission_result(submission, self.dataset)

        # A submission whose compilation failed.
        self.add_submission_with_results(
            self.task, self.participation, compilation_outcome=False)

        # A submission that compiled successfully but hasn't been
        # evaluated yet.
        self.add_submission_with_results(
            self.task, self.participation, compilation_outcome=True)

        self.session.commit()

    def test_counts_all_contests(self):
        stats = AdminWebServer.submissions_status(None)
        self.assertEqual(stats["total"], 4)
        # Both the submission with no result and the one with an
        # uncompiled result are bucketed as "compiling".
        self.assertEqual(stats["compiling"], 2)
        self.assertEqual(stats["compilation_fail"], 1)
        self.assertEqual(stats["evaluating"], 1)
        self.assertEqual(stats["max_compilations"], 0)
        self.assertEqual(stats["max_evaluations"], 0)
        self.assertEqual(stats["scoring"], 0)
        self.assertEqual(stats["scored"], 0)

    def test_counts_restricted_by_contest(self):
        other_contest = self.add_contest()
        other_task = self.add_task(contest=other_contest)
        other_dataset = self.add_dataset(task=other_task)
        other_task.active_dataset = other_dataset
        other_participation = self.add_participation(contest=other_contest)
        self.add_submission_with_results(
            other_task, other_participation, compilation_outcome=True)
        self.session.commit()

        stats = AdminWebServer.submissions_status(self.contest.id)
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["compiling"], 2)

        other_stats = AdminWebServer.submissions_status(other_contest.id)
        self.assertEqual(other_stats["total"], 1)
        self.assertEqual(other_stats["evaluating"], 1)
        self.assertEqual(other_stats["compiling"], 0)


if __name__ == "__main__":
    unittest.main()
