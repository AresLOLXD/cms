#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""Tests for the database reads of the Telegram bot.

Only the synchronous read is exercised: the bot's Application and its
event loop are never started here (this directory runs in the gevent
pytest group, where an asyncio loop can hang).

"""

import unittest

from sqlalchemy import event

from cms.db import engine
from cmscontrib.TelegramBot import TelegramBot
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin


class TestReadQuestionsAndAnnouncements(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    @staticmethod
    def make_bot(contest_id):
        """Return a bot that can only read the DB.

        TelegramBot.__init__ builds the telegram Application, which has
        nothing to do with these tests, so it is skipped.

        contest_id (int|None): the contest the bot serves, None for all.

        return (TelegramBot): a bot with just its contest_id set.

        """
        bot = TelegramBot.__new__(TelegramBot)
        bot.contest_id = contest_id
        return bot

    def add_askers(self, contest, count, questions_each=2):
        """Add count participations to contest, each with some questions.

        contest (Contest): the contest of the new participations.
        count (int): how many participations (and users) to add.
        questions_each (int): how many questions each one asks.

        return ([Question]): all the questions that were added.

        """
        questions = []
        for _ in range(count):
            participation = self.add_participation(contest=contest)
            for _ in range(questions_each):
                questions.append(self.add_question(
                    participation=participation))
        return questions

    @staticmethod
    def read_counting_selects(bot):
        """Call the bot's DB read and count the SELECTs it sends.

        bot (TelegramBot): the bot to read with.

        return (int, tuple): the number of SELECT statements, and what
            the read returned.

        """
        selects = []

        def record(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            result = bot._read_questions_and_announcements()
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(selects), result

    def test_select_count_does_not_depend_on_participations(self):
        # Every participation asks something, so a lazy load of the
        # participation (and of its user) costs two SELECTs for each.
        one = self.add_contest()
        many = self.add_contest()
        self.add_askers(one, 1)
        self.add_askers(many, 10)
        self.add_announcement(contest=one)
        self.add_announcement(contest=many)
        self.session.commit()

        selects_one, (questions_one, _) = self.read_counting_selects(
            self.make_bot(one.id))
        selects_many, (questions_many, _) = self.read_counting_selects(
            self.make_bot(many.id))

        # Make sure the comparison is not between two empty reads.
        self.assertEqual(len(questions_one), 2)
        self.assertEqual(len(questions_many), 20)
        self.assertGreater(selects_one, 0)
        self.assertEqual(selects_one, selects_many)

    def test_select_count_in_all_contests_mode(self):
        # Same as above, serving every contest (no announcements).
        contest = self.add_contest()
        self.add_askers(contest, 1)
        self.session.commit()
        selects_few, (questions_few, _) = self.read_counting_selects(
            self.make_bot(None))
        self.assertEqual(len(questions_few), 2)

        self.add_askers(contest, 10)
        self.session.commit()
        selects_more, (questions_more, _) = self.read_counting_selects(
            self.make_bot(None))
        self.assertEqual(len(questions_more), 22)

        self.assertGreater(selects_few, 0)
        self.assertEqual(selects_few, selects_more)

    def test_usernames(self):
        users = {name: self.add_user(username=name)
                 for name in ("alice", "bob", "carol")}
        asked = []
        for name, user in users.items():
            participation = self.add_participation(
                user=user, contest=self.contest)
            for _ in range(2):
                asked.append(
                    (self.add_question(participation=participation), name))
        self.session.commit()
        expected = {question.id: name for question, name in asked}

        questions, _ = self.make_bot(
            self.contest.id)._read_questions_and_announcements()

        self.assertEqual(
            {q["id"]: q["username"] for q in questions}, expected)

    def test_question_dicts_have_the_columns_and_the_username(self):
        participation = self.add_participation(contest=self.contest)
        question = self.add_question(
            participation=participation, reply_subject="Yes",
            reply_text="Read the statement", ignored=True)
        self.session.commit()

        questions, announcements = self.make_bot(
            self.contest.id)._read_questions_and_announcements()

        self.assertEqual(announcements, [])
        # get_attrs() leaves the primary key out.
        self.assertEqual(
            questions,
            [dict(question.get_attrs(), id=question.id,
                  username=participation.user.username)])

    def test_announcement_dicts_have_the_columns(self):
        announcement = self.add_announcement(contest=self.contest)
        self.session.commit()

        questions, announcements = self.make_bot(
            self.contest.id)._read_questions_and_announcements()

        self.assertEqual(questions, [])
        self.assertEqual(
            announcements,
            [dict(announcement.get_attrs(), id=announcement.id)])

    def test_only_the_bot_contest_is_read(self):
        other = self.add_contest()
        mine = self.add_askers(self.contest, 2, questions_each=1)
        self.add_askers(other, 2, questions_each=1)
        my_announcement = self.add_announcement(contest=self.contest)
        self.add_announcement(contest=other)
        self.session.commit()

        questions, announcements = self.make_bot(
            self.contest.id)._read_questions_and_announcements()

        self.assertCountEqual(
            [q["id"] for q in questions], [q.id for q in mine])
        self.assertEqual(
            [a["id"] for a in announcements], [my_announcement.id])

    def test_all_contests_mode_reads_every_question_and_no_announcement(self):
        other = self.add_contest()
        questions_here = self.add_askers(self.contest, 1, questions_each=1)
        questions_there = self.add_askers(other, 1, questions_each=1)
        self.add_announcement(contest=self.contest)
        self.add_announcement(contest=other)
        self.session.commit()

        questions, announcements = self.make_bot(
            None)._read_questions_and_announcements()

        self.assertCountEqual(
            [q["id"] for q in questions],
            [q.id for q in questions_here + questions_there])
        self.assertEqual(announcements, [])


if __name__ == "__main__":
    unittest.main()
