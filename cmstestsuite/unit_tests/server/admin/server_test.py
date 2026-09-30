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

import threading
import unittest

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.server.admin.server import AdminWebServer
from cmscommon.datetime import make_datetime


class TestConstruction(unittest.TestCase):
    """AdminWebServer must be constructible without raising.

    Regression test for a TypeError that used to be raised at startup
    (WebService.__init__ tried to construct the old, WSGI-shaped
    AWSAuthMiddleware(app) as auth_middleware(), which doesn't accept
    zero arguments) -- see this sub-project's task 7 fix round 1.

    """

    def test_constructs_without_raising(self):
        server = AdminWebServer(0)
        self.assertIsNone(server.auth_handler)


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


class TestNotifications(unittest.TestCase):
    """add_notification/take_notifications from concurrent threads."""

    def setUp(self):
        self.server = AdminWebServer(0)

    def test_take_returns_all_and_empties(self):
        now = make_datetime()
        self.server.add_notification(now, "a", "1")
        self.server.add_notification(now, "b", "2")
        self.assertEqual(self.server.take_notifications(),
                         [(now, "a", "1"), (now, "b", "2")])
        self.assertEqual(self.server.notifications, [])
        self.assertEqual(self.server.take_notifications(), [])

    def test_concurrent_add_and_take_delivers_each_exactly_once(self):
        writers, per_writer = 4, 2000
        now = make_datetime()
        taken = []
        writers_done = threading.Event()

        def write(writer):
            for i in range(per_writer):
                self.server.add_notification(now, str(writer), str(i))

        def take():
            while not writers_done.is_set():
                taken.extend(self.server.take_notifications())
            taken.extend(self.server.take_notifications())

        taker = threading.Thread(target=take)
        taker.start()
        threads = [threading.Thread(target=write, args=(w,))
                   for w in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        writers_done.set()
        taker.join()

        self.assertEqual(len(taken), writers * per_writer)
        self.assertEqual(
            sorted((s, t) for _, s, t in taken),
            sorted((str(w), str(i))
                   for w in range(writers) for i in range(per_writer)))
        self.assertEqual(self.server.notifications, [])


if __name__ == "__main__":
    unittest.main()
