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


"""Tests for the bulk removal of AWS run against the database."""

import unittest
from datetime import datetime

from sqlalchemy import func, select

from cms.db import Evaluation, Executable, File, Message, Participation, \
    Question, Submission, SubmissionResult, User, UserTest, UserTestFile, \
    UserTestResult
from cms.server.admin.bulkremove import NOT_FOUND, NOT_IN_CONTEST, \
    plan_removal, remove_participations, remove_users
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

NOW = datetime(2026, 10, 10, 12, 0)


class BulkRemoveDatabaseTestCase(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.addCleanup(self.delete_data)
        self.contest = self.add_contest(name="dia1")
        self.other_contest = self.add_contest(name="dia2")
        for contest in (self.contest, self.other_contest):
            # Not running at NOW unless a test says otherwise.
            contest.main_group.start = datetime(2026, 1, 1)
            contest.main_group.stop = datetime(2026, 1, 2)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(task=self.task)
        self.task.active_dataset = self.dataset
        self.testcase = self.add_testcase(self.dataset)

    def add_participant(self, username, contest=None):
        user = self.add_user(username=username)
        participation = self.add_participation(
            user=user, contest=contest or self.contest)
        return user, participation

    def add_full_history(self, participation):
        """Give participation one row in every table that hangs from it."""
        submission = self.add_submission(self.task, participation)
        result = self.add_submission_result(submission, self.dataset)
        self.add_evaluation(result, self.testcase)
        self.add_executable(result)
        self.add_file(submission)
        user_test = self.add_user_test(self.task, participation)
        self.add_user_test_file(user_test)
        self.add_user_test_result(user_test, self.dataset)
        self.add_question(participation=participation)
        self.add_message(participation=participation)

    def count(self, model, *where):
        return self.session.execute(
            select(func.count()).select_from(model).where(*where)
        ).scalar_one()


class TestPlanRemoval(BulkRemoveDatabaseTestCase):

    def test_platform_scope_by_ids_and_usernames(self):
        ana, ana_p = self.add_participant("ana")
        beto, _ = self.add_participant("beto", self.other_contest)
        self.add_participation(user=ana, contest=self.other_contest)
        self.add_full_history(ana_p)
        self.session.commit()

        plan = plan_removal(self.session, contest_id=None,
                            user_ids=[ana.id, 999999],
                            usernames=["beto", "nadie"], now=NOW)

        self.assertEqual([entry.username for entry in plan.users],
                         ["ana", "beto"])
        self.assertEqual(plan.user_ids, sorted([ana.id, beto.id]))
        self.assertEqual(plan.ignored,
                         [("999999", NOT_FOUND), ("nadie", NOT_FOUND)])
        ana_entry = plan.users[0]
        self.assertEqual((ana_entry.submissions, ana_entry.user_tests),
                         (1, 1))
        self.assertEqual(ana_entry.contests, ["dia1", "dia2"])
        self.assertEqual(plan.users[1].contests, ["dia2"])
        self.assertEqual((plan.submissions, plan.user_tests), (1, 1))
        self.assertFalse(plan.running)

    def test_a_user_chosen_twice_is_planned_once(self):
        ana, _ = self.add_participant("ana")
        self.session.commit()

        plan = plan_removal(self.session, contest_id=None,
                            user_ids=[ana.id, ana.id],
                            usernames=["ana", "ana"], now=NOW)

        self.assertEqual(plan.user_ids, [ana.id])
        self.assertEqual(plan.ignored, [])

    def test_usernames_match_exactly(self):
        self.add_participant("ana")
        self.session.commit()

        plan = plan_removal(self.session, contest_id=None,
                            usernames=["Ana"], now=NOW)

        self.assertEqual(plan.users, [])
        self.assertEqual(plan.ignored, [("Ana", NOT_FOUND)])

    def test_contest_scope_ignores_who_does_not_participate(self):
        _, ana_p = self.add_participant("ana")
        self.add_participant("beto", self.other_contest)
        self.add_full_history(ana_p)
        self.session.commit()

        plan = plan_removal(self.session, contest_id=self.contest.id,
                            usernames=["ana", "beto", "nadie"], now=NOW)

        self.assertEqual([entry.username for entry in plan.users], ["ana"])
        self.assertEqual(plan.users[0].contests, ["dia1"])
        self.assertEqual(plan.ignored, [("beto", NOT_IN_CONTEST),
                                        ("nadie", NOT_FOUND)])

    def test_contest_scope_counts_that_contest_only(self):
        ana, ana_p = self.add_participant("ana")
        other_task = self.add_task(contest=self.other_contest)
        other_p = self.add_participation(user=ana, contest=self.other_contest)
        self.add_full_history(ana_p)
        self.add_submission(other_task, other_p)
        self.session.commit()

        in_contest = plan_removal(self.session, contest_id=self.contest.id,
                                  user_ids=[ana.id], now=NOW)
        everywhere = plan_removal(self.session, contest_id=None,
                                  user_ids=[ana.id], now=NOW)

        self.assertEqual(in_contest.submissions, 1)
        self.assertEqual(everywhere.submissions, 2)

    def test_running_follows_the_groups_of_the_counted_participations(self):
        ana, _ = self.add_participant("ana")
        self.add_participation(user=ana, contest=self.other_contest)
        self.other_contest.main_group.start = datetime(2026, 10, 10, 9, 0)
        self.other_contest.main_group.stop = datetime(2026, 10, 10, 14, 0)
        self.session.commit()

        platform = plan_removal(self.session, contest_id=None,
                                user_ids=[ana.id], now=NOW)
        this_contest = plan_removal(self.session,
                                    contest_id=self.contest.id,
                                    user_ids=[ana.id], now=NOW)
        at_the_stop = plan_removal(
            self.session, contest_id=None, user_ids=[ana.id],
            now=datetime(2026, 10, 10, 14, 0))

        self.assertTrue(platform.running)
        self.assertFalse(this_contest.running)
        self.assertFalse(at_the_stop.running)

    def test_nothing_chosen(self):
        plan = plan_removal(self.session, contest_id=None, now=NOW)

        self.assertEqual((plan.users, plan.ignored, plan.running),
                         ([], [], False))


class TestRemove(BulkRemoveDatabaseTestCase):

    def test_remove_users_cascades_to_everything_they_own(self):
        ana, ana_p = self.add_participant("ana")
        ana_other_p = self.add_participation(user=ana,
                                             contest=self.other_contest)
        beto, beto_p = self.add_participant("beto")
        self.add_full_history(ana_p)
        self.add_full_history(beto_p)
        self.session.commit()
        ana_id, participation_ids = ana.id, [ana_p.id, ana_other_p.id]

        removed = remove_users(self.session, [ana_id])
        self.session.commit()

        self.assertEqual(removed, 1)
        self.session.expire_all()
        self.assertEqual(self.count(User, User.id == ana_id), 0)
        self.assertEqual(self.count(
            Participation, Participation.id.in_(participation_ids)), 0)
        for model in (Submission, UserTest, Question, Message):
            self.assertEqual(self.count(
                model, model.participation_id.in_(participation_ids)), 0,
                model.__name__)
        # One of each is left: beto's.
        for model in (Submission, SubmissionResult, Evaluation, Executable,
                      File, UserTest, UserTestFile, UserTestResult,
                      Question, Message):
            self.assertEqual(self.count(model), 1, model.__name__)
        self.assertEqual(self.count(User, User.id == beto.id), 1)

    def test_remove_participations_keeps_the_user(self):
        ana, ana_p = self.add_participant("ana")
        ana_other_p = self.add_participation(user=ana,
                                             contest=self.other_contest)
        self.add_full_history(ana_p)
        self.session.commit()
        ana_id, ana_p_id, other_id = ana.id, ana_p.id, ana_other_p.id

        removed = remove_participations(self.session, self.contest.id,
                                        [ana_id])
        self.session.commit()

        self.assertEqual(removed, 1)
        self.session.expire_all()
        self.assertEqual(self.count(User, User.id == ana_id), 1)
        self.assertEqual(self.count(Participation,
                                    Participation.id == ana_p_id), 0)
        self.assertEqual(self.count(Participation,
                                    Participation.id == other_id), 1)
        self.assertEqual(self.count(Submission), 0)
        self.assertEqual(self.count(Question), 0)

    def test_nothing_to_remove(self):
        self.assertEqual(remove_users(self.session, []), 0)
        self.assertEqual(
            remove_participations(self.session, self.contest.id, []), 0)

    def test_remove_does_not_commit(self):
        ana, _ = self.add_participant("ana")
        self.session.commit()
        ana_id = ana.id

        remove_users(self.session, [ana_id])
        self.session.rollback()

        self.assertEqual(self.count(User, User.id == ana_id), 1)
