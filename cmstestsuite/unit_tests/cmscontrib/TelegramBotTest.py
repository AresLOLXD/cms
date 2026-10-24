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

"""Tests for the Telegram bot.

The bot's Application and an event loop are never started here (this
directory runs in the gevent pytest group, where an asyncio loop can
hang): the DB read is synchronous, and the coroutines are driven by hand
with run(), which only works for coroutines that never wait.

"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import event
from telegram.error import BadRequest, RetryAfter

from cms.db import Announcement, engine
from cmscommon.datetime import EPOCH, make_datetime
from cmscontrib.TelegramBot import (
    TelegramBot, load_cutoff, parse_announcement, running_contest_names)
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin


def run(coroutine):
    """Run a coroutine that never waits for anything, without a loop.

    coroutine (coroutine): the coroutine to run.

    return (object): what the coroutine returned.

    """
    try:
        coroutine.send(None)
    except StopIteration as stop:
        return stop.value
    raise AssertionError("the coroutine waited for something")


class TestReadQuestionsAndAnnouncements(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    @staticmethod
    def make_bot(contest_id, cutoff=EPOCH):
        """Return a bot that can only read the DB.

        TelegramBot.__init__ builds the telegram Application, which has
        nothing to do with these tests, so it is skipped.

        contest_id (int|None): the contest the bot serves, None for all.
        cutoff (datetime): the bot ignores the questions asked before it.

        return (TelegramBot): a bot with just its contest_id and its
            cutoff set.

        """
        bot = TelegramBot.__new__(TelegramBot)
        bot.contest_id = contest_id
        bot.cutoff = cutoff
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

    def test_questions_are_returned_in_id_order(self):
        # A restarted bot posts the new questions in the order it reads
        # them, which has to be the order they were asked in. Interleave
        # the participations, and update some rows: an UPDATE moves the
        # row to the end of the table, so a query without ORDER BY would
        # return it after the ones that were not touched.
        participations = [
            self.add_participation(contest=self.contest) for _ in range(3)]
        questions = [
            self.add_question(participation=participations[i % 3])
            for i in range(9)]
        self.session.commit()
        for question in questions[:4]:
            question.ignored = True
            self.session.commit()
        expected = [question.id for question in questions]
        self.assertEqual(expected, sorted(expected))

        for contest_id in (self.contest.id, None):
            with self.subTest(contest_id=contest_id):
                read, _ = self.make_bot(
                    contest_id)._read_questions_and_announcements()
                self.assertEqual([q["id"] for q in read], expected)

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

    def test_all_contests_mode_adds_the_contest_name(self):
        other = self.add_contest()
        here = self.add_askers(self.contest, 1, questions_each=1)
        there = self.add_askers(other, 1, questions_each=1)
        self.session.commit()

        questions, _ = self.make_bot(None)._read_questions_and_announcements()

        self.assertEqual(
            {q["id"]: q["contest_name"] for q in questions},
            {here[0].id: self.contest.name, there[0].id: other.name})

    def test_select_count_in_all_contests_mode_does_not_depend_on_contests(
            self):
        # The contest name of each question has to come with the question,
        # not from a lazy load for each contest.
        self.add_askers(self.add_contest(), 1)
        self.session.commit()
        selects_few, (questions_few, _) = self.read_counting_selects(
            self.make_bot(None))

        for _ in range(5):
            self.add_askers(self.add_contest(), 1)
        self.session.commit()
        selects_more, (questions_more, _) = self.read_counting_selects(
            self.make_bot(None))

        self.assertEqual(len(questions_few), 2)
        self.assertEqual(len(questions_more), 12)
        self.assertEqual(selects_few, selects_more)

    def test_questions_before_the_cutoff_are_not_read(self):
        participation = self.add_participation(contest=self.contest)
        asked_at = {
            day: self.add_question(
                participation=participation,
                question_timestamp=datetime(2026, 1, day))
            for day in (1, 2, 3)}
        self.session.commit()

        for contest_id in (self.contest.id, None):
            with self.subTest(contest_id=contest_id):
                read, _ = self.make_bot(
                    contest_id, datetime(2026, 1, 2)
                )._read_questions_and_announcements()
                # The question asked exactly at the cutoff is still read.
                self.assertEqual(
                    [q["id"] for q in read],
                    [asked_at[2].id, asked_at[3].id])


class TestLoadCutoff(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "cutoff")

    def test_first_start_writes_now(self):
        before = make_datetime()
        cutoff = load_cutoff(self.path, has_sent_questions=False)

        self.assertGreaterEqual(cutoff, before)
        # A second start reads the same moment back.
        self.assertEqual(load_cutoff(self.path, False), cutoff)

    def test_old_install_without_the_file_keeps_posting_everything(self):
        self.assertEqual(load_cutoff(self.path, has_sent_questions=True), EPOCH)
        self.assertFalse(os.path.exists(self.path))

    def test_the_file_wins_over_the_state(self):
        with open(self.path, "w") as f:
            f.write("2026-10-10T12:00:00")

        self.assertEqual(
            load_cutoff(self.path, has_sent_questions=True),
            datetime(2026, 10, 10, 12))


class TestQuestionText(unittest.TestCase):

    @staticmethod
    def post(**question):
        """Post a question and return the text that was sent.

        question: the columns of the question that differ from a pending
            question of alice, with no contest name.

        return (str): the text of the Telegram message.

        """
        bot = TelegramBot.__new__(TelegramBot)
        bot.chat_id = 1
        bot.msg_id_to_qid = {}
        bot.application = MagicMock()
        bot.application.bot.send_message = AsyncMock(
            return_value=SimpleNamespace(message_id=7))
        new = dict(
            id=3, username="alice", subject="Sub", text="Body",
            ignored=False, reply_text=None, reply_subject=None)
        new.update(question)
        run(bot.question_callback({}, new))
        return bot.application.bot.send_message.call_args.kwargs["text"]

    def test_single_contest_text_has_no_contest(self):
        self.assertEqual(
            self.post(),
            "#todo\n<b>New question from alice</b>\nSubject: <i>Sub</i>"
            "\n\nBody\n\nReply with <code>/answer your_reply_here</code> "
            "for a custom reply")

    def test_pending_question_has_todo_and_the_contest_hashtag(self):
        text = self.post(contest_name="omi-2026-day1")

        self.assertTrue(text.startswith("#todo #omi_2026_day1\n"))
        self.assertIn("Contest: <b>omi-2026-day1</b>\n", text)

    def test_answered_question_keeps_the_hashtag_but_not_todo(self):
        text = self.post(
            contest_name="omi-2026-day1", reply_text="", reply_subject="Yes")

        self.assertTrue(text.startswith("#omi_2026_day1\n"))
        self.assertNotIn("#todo", text)


class TestParseAnnouncement(unittest.TestCase):

    RUNNING = ["day1", "day2"]

    def test_contest_subject_and_body(self):
        self.assertEqual(
            parse_announcement(
                "/announcement day2  A subject\nFirst line\nSecond line\n",
                self.RUNNING),
            ("day2", "A subject", "First line\nSecond line"))

    def test_bad_messages_say_what_is_running(self):
        for text, problem in [
                ("/announcement\nBody", "Missing contest name"),
                ("/announcement day9 Subject\nBody", "day9 is not a running"),
                ("/announcement day1 Subject", "Missing announcement body"),
                ("/announcement day1\nBody", "Missing announcement subject")]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as caught:
                    parse_announcement(text, self.RUNNING)
                self.assertIn(problem, str(caught.exception))
        # The name is checked first, and the reply lists the running ones.
        with self.assertRaisesRegex(ValueError, "Running contests: day1, day2"):
            parse_announcement("/announcement day9 S\nB", self.RUNNING)

    def test_nothing_is_running(self):
        with self.assertRaisesRegex(ValueError, "Running contests: none"):
            parse_announcement("/announcement day1 S\nB", [])


class TestAnnouncements(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.now = make_datetime()
        hour = timedelta(hours=1)
        self.past = self.add_contest(name="past", groups=[self.get_group(
            start=self.now - 3 * hour, stop=self.now - 2 * hour)])
        self.running = self.add_contest(name="running", groups=[
            self.get_group(start=self.now - 3 * hour, stop=self.now - hour),
            self.get_group(start=self.now - hour, stop=self.now + hour),
            self.get_group(start=self.now - hour, stop=self.now + 2 * hour)])
        self.future = self.add_contest(name="future", groups=[self.get_group(
            start=self.now + hour, stop=self.now + 2 * hour)])
        self.session.commit()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def test_running_contest_names(self):
        # A contest with several running groups is listed once.
        self.assertEqual(
            running_contest_names(self.session, self.now), ["running"])

    def send(self, bot_contest_id, text):
        """Send an /announcement message to a bot and return what it did.

        bot_contest_id (int|None): the contest the bot serves.
        text (str): the text of the message.

        return (str): the text of the reply of the bot.

        """
        bot = TelegramBot.__new__(TelegramBot)
        bot.chat_id = 1
        bot.contest_id = bot_contest_id
        update = MagicMock()
        update.effective_chat.id = 1
        update.message.text = text
        context = MagicMock()
        context.bot.send_message = AsyncMock()
        run(bot.send_announcement(update, context))
        calls = context.bot.send_message.call_args_list
        return calls[0].kwargs["text"] if calls else None

    def announcements(self):
        self.session.expire_all()
        return [(a.contest.name, a.subject, a.text)
                for a in self.session.query(Announcement)]

    def test_all_mode_announces_in_the_named_running_contest(self):
        reply = self.send(None, "/announcement running Hello\nA body")

        self.assertEqual(self.announcements(), [("running", "Hello", "A body")])
        self.assertEqual(reply, "Announcement sent to #running")

    def test_all_mode_refuses_a_contest_that_is_not_running(self):
        reply = self.send(None, "/announcement past Hello\nA body")

        self.assertEqual(self.announcements(), [])
        self.assertIn("past is not a running contest", reply)
        self.assertIn("Running contests: running", reply)

    def test_single_contest_mode_is_unchanged(self):
        reply = self.send(self.past.id, "/announcement Hello there\nA body")

        self.assertEqual(
            self.announcements(), [("past", "Hello there", "A body")])
        self.assertIsNone(reply)


class StopLoop(Exception):
    """Raised by the patched sleep to end db_loop."""


class TestStoreRobustness(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.bot = TelegramBot.__new__(TelegramBot)
        self.bot.chat_id = 1
        self.bot.question_status = {}
        self.bot.question_storage_dir = directory.name
        self.bot.msg_id_to_qid = {}
        self.bot.application = MagicMock()
        self.send = self.bot.application.bot.send_message = AsyncMock(
            return_value=SimpleNamespace(message_id=5))
        self.edit = self.bot.application.bot.edit_message_text = AsyncMock()
        self.sleep = AsyncMock()
        patcher = patch("cmscontrib.TelegramBot.asyncio.sleep", self.sleep)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def question(qid, **columns):
        question = dict(
            id=qid, username="alice", subject="Sub", text="Body",
            ignored=False, reply_text=None, reply_subject=None,
            reply_timestamp=None)
        question.update(columns)
        return question

    def test_not_modified_edit_is_success(self):
        # Answering twice with the same button changes reply_timestamp
        # and not the text, so Telegram refuses the edit. If that failed
        # the stored question would stay different from the DB, and the
        # edit would be tried again at every poll and every restart.
        old = self.question(
            3, msg_id=10, reply_text="", reply_subject="Yes",
            reply_timestamp=datetime(2026, 1, 1))
        new = self.question(
            3, reply_text="", reply_subject="Yes",
            reply_timestamp=datetime(2026, 1, 2))
        self.bot.question_status[3] = old
        self.edit.side_effect = BadRequest(
            "Message is not modified: specified new message content and "
            "reply markup are exactly the same as a current content and "
            "reply markup of the message")

        run(self.bot.store_question(new, self.bot.question_callback))

        self.assertEqual(self.bot.question_status[3]["msg_id"], 10)
        self.assertEqual(
            self.bot.question_status[3]["reply_timestamp"],
            datetime(2026, 1, 2))
        self.assertTrue(os.path.exists(
            os.path.join(self.bot.question_storage_dir, "3.yaml")))

    def test_loop_goes_on_after_a_failed_question(self):
        self.send.side_effect = [
            RuntimeError("Telegram is down"),
            SimpleNamespace(message_id=6)]
        self.bot._read_questions_and_announcements = MagicMock(
            return_value=([self.question(1), self.question(2)], []))
        self.sleep.side_effect = StopLoop

        with self.assertLogs("cmscontrib.TelegramBot", "ERROR"):
            with self.assertRaises(StopLoop):
                run(self.bot.db_loop())

        # The second question was posted; the first is tried again at the
        # next poll.
        self.assertEqual(list(self.bot.question_status), [2])
        self.assertEqual(self.bot.question_status[2]["msg_id"], 6)

    def test_flood_control_waits_and_retries_the_same_question(self):
        self.send.side_effect = [
            RetryAfter(3), SimpleNamespace(message_id=6)]

        run(self.bot.store_question(
            self.question(1), self.bot.question_callback))

        self.sleep.assert_awaited_once_with(3)
        self.assertEqual(self.send.await_count, 2)
        self.assertEqual(self.bot.question_status[1]["msg_id"], 6)


if __name__ == "__main__":
    unittest.main()
