#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2018 Luca Wehrstedt <luca.wehrstedt@gmail.com>
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

"""Tests for tokening functions.

"""

import unittest
from datetime import timedelta
from unittest.mock import MagicMock, call, patch

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import TOKEN_MODE_INFINITE, TOKEN_MODE_DISABLED, TOKEN_MODE_FINITE
from cms.db import Session, Submission, Token
from cms.server.contest.handlers.tasksubmission import UseTokenHandler
from cms.server.contest.submission.check import CONCURRENT_REQUEST_TEXT, \
    TOKEN_LOCK_NAMESPACE
from cms.server.contest.tokening import accept_token, UnacceptableToken, \
    TokenAlreadyPlayed, tokens_available
from cmscommon.datetime import make_datetime


class TestTokensAvailable(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()

        self.timestamp = make_datetime()

        group = self.get_group(start=self.at(0))
        self.contest = self.add_contest(groups = [group])
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.other_task = self.add_task(contest=self.contest)

    def at(self, timestamp):
        return self.timestamp + timedelta(seconds=timestamp)

    def call(self, timestamp):
        return tokens_available(
            self.participation, self.task, self.at(timestamp))

    def add_token_to_contest(self, timestamp):
        submission = self.add_submission(participation=self.participation,
                                         task=self.other_task)
        self.add_token(timestamp=self.at(timestamp), submission=submission)

    def add_token_to_task(self, timestamp):
        submission = self.add_submission(participation=self.participation,
                                         task=self.task)
        self.add_token(timestamp=self.at(timestamp), submission=submission)

    def set_contest_token_infinite(self):
        self.contest.token_mode = TOKEN_MODE_INFINITE

    def set_contest_token_disabled(self):
        self.contest.token_mode = TOKEN_MODE_DISABLED

    def set_contest_token_finite(self, initial, number, interval, max_=None):
        self.contest.token_mode = TOKEN_MODE_FINITE
        self.contest.token_gen_initial = initial
        self.contest.token_gen_number = number
        self.contest.token_gen_interval = timedelta(seconds=interval)
        self.contest.token_gen_max = max_

    def set_contest_token_constraints(self, max_number=None, min_interval=0):
        self.contest.token_max_number = max_number
        self.contest.token_min_interval = timedelta(seconds=min_interval)

    def set_task_token_infinite(self):
        self.task.token_mode = TOKEN_MODE_INFINITE

    def set_task_token_disabled(self):
        self.task.token_mode = TOKEN_MODE_DISABLED

    def set_task_token_finite(self, initial, number, interval, max_=None):
        self.task.token_mode = TOKEN_MODE_FINITE
        self.task.token_gen_initial = initial
        self.task.token_gen_number = number
        self.task.token_gen_interval = timedelta(seconds=interval)
        self.task.token_gen_max = max_

    def set_task_token_constraints(self, max_number=None, min_interval=0):
        self.task.token_max_number = max_number
        self.task.token_min_interval = timedelta(seconds=min_interval)

    def test_infinite(self):
        self.set_contest_token_infinite()
        self.set_task_token_infinite()
        self.assertEqual(self.call(10), (-1, None, None))

    def test_disabled(self):
        self.set_contest_token_infinite()
        self.set_task_token_disabled()
        self.assertEqual(self.call(10), (0, None, None))

        self.set_contest_token_disabled()
        self.set_task_token_infinite()
        self.assertEqual(self.call(10), (0, None, None))

    def test_finite_on_contest(self):
        self.set_contest_token_finite(initial=3, number=2, interval=4, max_=6)
        self.set_task_token_infinite()

        # None generated yet.
        self.assertEqual(self.call(3), (3, self.at(4), None))
        # One period of generation passed.
        self.assertEqual(self.call(6), (5, self.at(8), None))
        # Cap hit.
        self.assertEqual(self.call(10), (6, None, None))
        # Once some used, regeneration resumes.
        self.add_token_to_contest(9)
        self.assertEqual(self.call(10), (5, self.at(12), None))

    def test_finite_on_task(self):
        self.set_contest_token_infinite()
        self.set_task_token_finite(initial=3, number=2, interval=4, max_=6)

        # None generated yet.
        self.assertEqual(self.call(3), (3, self.at(4), None))
        # One period of generation passed.
        self.assertEqual(self.call(6), (5, self.at(8), None))
        # Cap hit.
        self.assertEqual(self.call(10), (6, None, None))
        # Once some used, regeneration resumes.
        self.add_token_to_task(9)
        self.assertEqual(self.call(10), (5, self.at(12), None))

        # Tokens on other tasks have no effect.
        self.add_token_to_contest(9)
        self.assertEqual(self.call(10), (5, self.at(12), None))

    def test_finite_on_both(self):
        self.set_contest_token_finite(initial=1, number=2, interval=5)
        self.set_task_token_finite(initial=2, number=1, interval=4, max_=5)

        # 7 ║                             ┏━━━━━━━━━┛
        # 6 ║                             ┃
        # 5 ║                   ┏━━━┯━━━━━┹───────────
        # 4 ║               ┌───╂───┘
        # 3 ║ ↓task ┌─┲━━━━━┷━━━┛
        # 2 ║───────┘ ┃
        # 1 ║━━━━━━━━━┛ ←contest
        # 0 ╠═╦═╦═╦═╦═╦═╦═╦═╦═╦═╦═╦═╦═╦═╦═╦═╦═╦═╦═╦═╦═
        #   0   2   4   6   8  10  12  14  16  18  20

        # Capped at the contest level.
        self.assertEqual(self.call(3), (1, self.at(5), None))
        # Capped at both, with the contest unlocking later.
        self.assertEqual(self.call(7), (3, self.at(10), None))
        # Capped at the task level.
        self.assertEqual(self.call(11), (4, self.at(12), None))
        # Capped at both, but no more will be generated.
        self.assertEqual(self.call(13), (5, None, None))
        # Capped at the task, but no more will be generated.
        self.assertEqual(self.call(16), (5, None, None))

        # Generation stops when the maximum is hit, but resumes as soon
        # as some tokens are used.
        self.assertEqual(self.call(101), (5, None, None))
        self.add_token_to_task(102)
        self.assertEqual(self.call(103), (4, self.at(104), None))
        self.assertEqual(self.call(105), (5, None, None))

    def test_constraints(self):
        self.set_contest_token_finite(initial=3, number=1, interval=2, max_=6)
        self.set_task_token_finite(initial=1, number=2, interval=2)

        # 7 ║                       ┌───────┘
        # 6 ║                       ┢━━━━━━━━━━━━━━━
        # 5 ║ ↓contest      ┏━━━━━━━┛
        # 4 ║       ┏━━━━━━━┩
        # 3 ║━━━━━━━╃───────┘
        # 2 ║       │
        # 1 ║───────┘ ←task
        # 0 ╠═══╦═══╦═══╦═══╦═══╦═══╦═══╦═══╦═══╦═══╦═
        #   0   1   2   3   4   5   6   7   8   9  10

        self.set_contest_token_constraints(3, 2)
        self.set_task_token_constraints(None, 4)

        self.add_token_to_contest(2)
        # We have 4 - 1 = 3 contest tokens and 3 task tokens but we can
        # play only 2 more on the contest, hence the number is capped.
        self.assertEqual(self.call(3), (2, None, self.at(4)))

        self.add_token_to_task(1)
        # We need to wait 4 seconds to play a token.
        self.assertEqual(self.call(3), (1, None, self.at(5)))

        self.add_token_to_contest(6)
        # We played all the tokens we could on the contest.
        self.assertEqual(self.call(7), (0, None, None))

    def test_constraints_have_no_effect_if_infinite(self):
        self.set_contest_token_infinite()
        self.set_task_token_infinite()

        self.add_token_to_task(5)

        # Just contest.
        self.set_contest_token_constraints(1, 10)
        self.assertEqual(self.call(10), (-1, None, None))

        # Just task.
        self.set_contest_token_constraints()
        self.set_task_token_constraints(1, 10)
        self.assertEqual(self.call(10), (-1, None, None))

        # Both.
        self.set_contest_token_constraints(1, 10)
        self.assertEqual(self.call(10), (-1, None, None))

    def test_usaco_like(self):
        self.contest.main_group.per_user_time = timedelta(seconds=100)
        self.participation.starting_time = self.at(1000)

        self.set_contest_token_finite(initial=2, number=1, interval=10, max_=10)
        self.set_task_token_finite(initial=10, number=0, interval=1)

        # Generation didn't start before the user's starting_time.
        self.assertEqual(self.call(1000), (2, self.at(1010), None))
        # Then it proceeds normally.
        self.assertEqual(self.call(1011), (3, self.at(1020), None))
        # Until the cap is hit.
        self.assertEqual(self.call(1081), (10, None, None))
        # Played tokens are taken into account at their correct time.
        self.add_token_to_task(1050)
        self.assertEqual(self.call(1051), (6, self.at(1060), None))
        self.assertEqual(self.call(1081), (9, None, None))


class TestAcceptToken(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()

        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.submission = self.add_submission(participation=self.participation,
                                              task=self.task)

        patcher = patch("cms.server.contest.tokening.tokens_available")
        self.tokens_available = patcher.start()
        self.addCleanup(patcher.stop)

        patcher = patch("cms.server.contest.tokening.try_lock_limit_checks")
        self.try_lock_limit_checks = patcher.start()
        self.addCleanup(patcher.stop)
        self.try_lock_limit_checks.return_value = True

        self.timestamp = make_datetime()

    def call(self):
        return accept_token(self.session, self.submission, self.timestamp)

    def test_success_infinite_tokens(self):
        self.tokens_available.return_value = (-1, None, None)
        token = self.call()
        self.assertIsNotNone(token)
        self.assertIs(token.submission, self.submission)
        self.assertIs(token.timestamp, self.timestamp)

    def test_success_finite_tokens(self):
        self.tokens_available.return_value = (1, None, None)
        token = self.call()
        self.assertIsNotNone(token)
        self.assertIs(token.submission, self.submission)
        self.assertIs(token.timestamp, self.timestamp)

    def test_failure_none_available(self):
        self.tokens_available.return_value = (0, None, None)
        with self.assertRaises(UnacceptableToken):
            self.call()

    def test_failure_cooldown_in_effect(self):
        self.tokens_available.return_value = \
            (1, None, self.timestamp + timedelta(seconds=1))
        with self.assertRaises(UnacceptableToken):
            self.call()

    def test_double_use(self):
        # Infinite tokens.
        self.tokens_available.return_value = (-1, None, None)
        self.add_token(submission=self.submission,
                       timestamp=self.timestamp - timedelta(seconds=1))
        with self.assertRaises(TokenAlreadyPlayed):
            self.call()

    def test_checks_run_under_the_lock(self):
        self.tokens_available.return_value = (1, None, None)
        calls = MagicMock()
        calls.attach_mock(self.try_lock_limit_checks, "try_lock")
        calls.attach_mock(self.tokens_available, "tokens_available")

        self.call()

        self.assertEqual(calls.mock_calls, [
            call.try_lock(self.session, self.participation, "token"),
            call.tokens_available(
                self.participation, self.task, self.timestamp)])

    def test_failure_due_to_concurrent_request(self):
        self.tokens_available.return_value = (1, None, None)
        self.try_lock_limit_checks.return_value = False

        with self.assertRaises(UnacceptableToken) as raised:
            self.call()

        self.assertEqual(raised.exception.subject, "Token request discarded")
        self.assertEqual(raised.exception.text, CONCURRENT_REQUEST_TEXT)
        self.tokens_available.assert_not_called()
        self.assertIsNone(self.submission.token)


class TestAcceptTokenConcurrency(DatabaseMixin, unittest.TestCase):
    """Two token requests of one contestant at the same moment, as two
    CWS shards handle them: each one has its own session, and the real
    checks run.

    """

    def setUp(self):
        super().setUp()

        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(
            contest=self.contest, token_mode=TOKEN_MODE_INFINITE)
        self.submissions = [
            self.add_submission(participation=self.participation,
                                task=self.task)
            for _ in range(2)]
        self.session.commit()
        self.participation_id = self.participation.id
        self.submission_ids = [s.id for s in self.submissions]
        self.timestamp = make_datetime()

        # The session of the request handled by the other shard.
        self.other_session = Session()
        self.addCleanup(self.other_session.close)

    def play(self, session, submission_index, timestamp):
        """Play a token on one of the submissions in the given session."""
        return accept_token(
            session,
            session.get(Submission, self.submission_ids[submission_index]),
            timestamp)

    def count_tokens(self):
        """Return how many tokens of the contestant were committed."""
        with Session() as session:
            return session.execute(
                select(func.count(Token.id)).join(Submission)
                .where(Submission.participation_id == self.participation_id)
            ).scalar_one()

    def test_concurrent_token_over_max_number_is_rejected_busy(self):
        self.contest.token_mode = TOKEN_MODE_FINITE
        self.contest.token_max_number = 1
        self.session.commit()
        self.play(self.session, 0, self.timestamp)

        with self.assertRaises(UnacceptableToken) as raised, \
                self.assertLogs("cms.server.contest.submission.check",
                                "INFO") as logs:
            self.play(self.other_session, 1, self.timestamp)
        self.assertEqual(raised.exception.subject, "Token request discarded")
        self.assertEqual(raised.exception.text, CONCURRENT_REQUEST_TEXT)
        self.assertEqual(logs.output, [
            "INFO:cms.server.contest.submission.check:concurrent request "
            "rejected for %s (participation %d, token)" % (
                self.participation.user.username, self.participation_id)])
        self.other_session.rollback()
        self.session.commit()

        self.assertEqual(self.count_tokens(), 1)
        # Sent again, it reads the committed token: the limit refuses it.
        with self.assertRaisesRegex(UnacceptableToken, "no tokens available"):
            self.play(self.other_session, 1, self.timestamp)

    def test_concurrent_token_on_the_same_submission_is_rejected_busy(self):
        # Without any limit: the lock is always taken for tokens.
        self.play(self.session, 0, self.timestamp)

        with self.assertRaises(UnacceptableToken) as raised:
            self.play(self.other_session, 0, self.timestamp)
        self.assertEqual(raised.exception.text, CONCURRENT_REQUEST_TEXT)
        self.other_session.rollback()
        self.session.commit()

        self.assertEqual(self.count_tokens(), 1)
        with self.assertRaises(TokenAlreadyPlayed):
            self.play(self.other_session, 0, self.timestamp)

    def test_uncontended_token_is_accepted_under_the_lock(self):
        self.play(self.session, 0, self.timestamp)

        self.assertEqual(self.held_advisory_locks(self.session),
                         [(TOKEN_LOCK_NAMESPACE, self.participation_id)])
        self.session.commit()
        self.assertEqual(self.held_advisory_locks(self.session), [])
        self.assertEqual(self.count_tokens(), 1)


class DriverError(Exception):
    """A stand-in for a psycopg2 error, with its SQLSTATE."""

    def __init__(self, pgcode: str):
        super().__init__("SQLSTATE " + pgcode)
        self.pgcode = pgcode


class TestUseTokenHandler(unittest.TestCase):
    """The handler body runs for real; only the database and
    accept_token are replaced.

    """

    def setUp(self):
        super().setUp()
        handler = UseTokenHandler.__new__(UseTokenHandler)
        handler._current_user = MagicMock()
        handler._current_user.user.username = "ana"
        handler.impersonated_by_admin = False
        handler.is_multi_contest = lambda: False
        handler.r_params = {"actual_phase": 0}
        handler.timestamp = make_datetime()
        handler.application = MagicMock()
        task = MagicMock()
        task.name = "sum"
        handler.get_task = MagicMock(return_value=task)
        handler.get_submission = MagicMock()
        handler.contest_url = MagicMock()
        handler.redirect = MagicMock()
        handler.schedule_rpc = MagicMock()
        handler.notify_success = MagicMock()
        handler.notify_error = MagicMock()
        self.calls = MagicMock()
        handler.sql_session = self.calls.sql_session
        handler.notify_warning = self.calls.notify_warning
        self.handler = handler

    def post_with_commit_error(self, pgcode: str):
        """Play a token whose commit fails with the given SQLSTATE."""
        self.handler.sql_session.commit.side_effect = IntegrityError(
            "INSERT INTO tokens", {}, DriverError(pgcode))
        with patch("cms.server.contest.handlers.tasksubmission."
                   "accept_token"):
            self.handler.post("sum", "1")

    def test_duplicate_token_at_commit_is_shown_as_already_played(self):
        # A second token on the submission, refused by the unique
        # constraint at the commit (23505, unique_violation): a request
        # that the lock in accept_token did not serialize with this one
        # played it.
        with self.assertLogs("cms.server.contest.handlers.tasksubmission",
                             "WARNING") as logs:
            self.post_with_commit_error("23505")

        self.assertEqual(self.calls.mock_calls, [
            call.sql_session.commit(),
            call.sql_session.rollback(),
            call.notify_warning(
                "Token request discarded",
                "Your request has been discarded because you already used "
                "a token on that submission.")])
        self.assertEqual(logs.output, [
            "WARNING:cms.server.contest.handlers.tasksubmission:User ana "
            "played a token twice on a submission of task sum; the second "
            "one was refused at commit."])
        self.handler.notify_success.assert_not_called()
        self.handler.schedule_rpc.assert_not_called()
        self.handler.redirect.assert_called_once_with(
            self.handler.contest_url())

    def test_other_integrity_error_at_commit_is_raised(self):
        # 23503, foreign_key_violation: an admin deleted the
        # participation, the task or the contest meanwhile. That is no
        # token played twice: roll back and let it be an error.
        with self.assertNoLogs("cms.server.contest.handlers.tasksubmission",
                               "WARNING"), \
                self.assertRaises(IntegrityError):
            self.post_with_commit_error("23503")

        self.assertEqual(self.calls.mock_calls, [
            call.sql_session.commit(),
            call.sql_session.rollback()])
        self.handler.notify_success.assert_not_called()
        self.handler.schedule_rpc.assert_not_called()
        self.handler.redirect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
