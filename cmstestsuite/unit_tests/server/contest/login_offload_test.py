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

"""Tests for the login's password check running off the event loop.

The bcrypt check of POST /<contest>/login costs about 200 ms of CPU.
It has to run in a dedicated thread pool, so that the event loop keeps
serving other requests, and without a database connection checked out
while it is pending (hundreds of logins can queue behind a small pool).

"""

import asyncio
import ipaddress
import json
import os
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.db import Contest, Participation, Session, engine
from cms.server import Url
from cms.server.contest import authentication
from cms.server.contest.authentication import validate_login
from cms.server.contest.handlers.main import LoginHandler
from cmscommon.crypto import build_password, hash_password, validate_password
from cmscommon.datetime import make_datetime, make_timestamp


VALIDATE_PASSWORD = "cms.server.contest.authentication.validate_password"

# How long a fake password check waits to be released before giving up;
# it is only reached if the test fails.
CHECK_TIMEOUT = 2.0


class BlockingCheck:
    """A fake validate_password that blocks until released."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, authentication_string, password):
        self.entered.set()
        self.release.wait(timeout=CHECK_TIMEOUT)
        return True


class LoginTestBase(DatabaseMixin, unittest.IsolatedAsyncioTestCase):
    """A contest with one participant, committed to the database."""

    def setUp(self):
        super().setUp()
        self.delete_data()
        self.timestamp = make_datetime()
        self.contest = self.add_contest(allow_password_authentication=True)
        self.user = self.add_user(
            username="myuser", password=build_password("mypass"))
        self.participation = self.add_participation(
            contest=self.contest, user=self.user)
        # Read what the tests need before the commit expires it.
        self.contest_name = self.contest.name
        self.session.commit()
        self.contest_id = self.contest.id
        self.participation_id = self.participation.id
        self.session.close()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def new_session(self) -> Session:
        session = Session()
        self.addCleanup(session.close)
        return session

    def change(self, **changes):
        """Commit changes to the contest, the participation or the user.

        Each keyword is "<object>__<attribute>", with object one of
        contest, participation and user.

        """
        session = self.new_session()
        contest = session.get(Contest, self.contest_id)
        participation = session.get(Participation, self.participation_id)
        objects = {"contest": contest, "participation": participation,
                   "user": participation.user}
        for key, value in changes.items():
            target, attribute = key.split("__")
            setattr(objects[target], attribute, value)
        session.commit()


class TestLoginHandlerOffload(LoginTestBase):
    """LoginHandler.post, driven the way Tornado drives it."""

    def make_handler(self, username="myuser", password="mypass"):
        arguments = {"username": username, "password": password}
        session = self.new_session()

        handler = LoginHandler.__new__(LoginHandler)
        handler.sql_session = session
        # As in prepare(): the contest is loaded through the request's
        # session, which is then left inside a transaction.
        handler.contest = session.get(Contest, self.contest_id)
        handler.timestamp = self.timestamp
        handler.url = Url(".")
        handler.contest_url = Url(".")
        handler.request = MagicMock()
        handler.request.remote_ip = "127.0.0.1"
        handler.is_multi_contest = lambda: False
        handler.get_argument = \
            lambda name, default=None: arguments.get(name, default)
        handler.set_secure_cookie = MagicMock()
        handler.clear_cookie = MagicMock()
        handler.redirect = MagicMock()
        return handler

    @staticmethod
    async def run_post(handler):
        # This is what Tornado's RequestHandler._execute does.
        result = handler.post()
        if result is not None:
            await result

    async def wait_until(self, predicate):
        deadline = time.monotonic() + 5.0
        while not predicate():
            self.assertLess(time.monotonic(), deadline, "timed out")
            await asyncio.sleep(0.005)

    async def test_successful_login_sets_the_cookie_and_redirects(self):
        handler = self.make_handler()

        await self.run_post(handler)

        handler.set_secure_cookie.assert_called_once_with(
            self.contest_name + "_login",
            json.dumps(["myuser", build_password("mypass"),
                        make_timestamp(self.timestamp), False]).encode(),
            expires_days=None,
            max_age=config.contest_web_server.cookie_duration)
        handler.clear_cookie.assert_not_called()
        handler.redirect.assert_called_once_with(handler.contest_url())

    async def test_failed_login_clears_the_cookie_and_redirects(self):
        handler = self.make_handler(password="wrong")

        await self.run_post(handler)

        handler.set_secure_cookie.assert_not_called()
        handler.clear_cookie.assert_called_once_with(
            self.contest_name + "_login")
        handler.redirect.assert_called_once_with(
            handler.contest_url(login_error="true"))

    async def test_check_does_not_block_the_event_loop(self):
        handler = self.make_handler()
        check = BlockingCheck()
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        with patch(VALIDATE_PASSWORD, check):
            login_task = asyncio.create_task(self.run_post(handler))
            try:
                await self.wait_until(check.entered.is_set)
                self.assertFalse(
                    login_task.done(),
                    "the login finished although its check was still "
                    "pending: the check ran on the event loop")
                ticks_at_entry = ticks
                await asyncio.sleep(0.1)
                self.assertGreaterEqual(ticks - ticks_at_entry, 5)
                self.assertFalse(login_task.done())
            finally:
                check.release.set()
                await login_task
                ticker_task.cancel()

        handler.redirect.assert_called_once_with(handler.contest_url())

    async def test_other_login_is_served_while_a_check_is_pending(self):
        pending_handler = self.make_handler()
        other_handler = self.make_handler(username="nobody")
        check = BlockingCheck()

        with patch(VALIDATE_PASSWORD, check):
            login_task = asyncio.create_task(self.run_post(pending_handler))
            try:
                await self.wait_until(check.entered.is_set)
                await asyncio.wait_for(self.run_post(other_handler), 1.0)
                other_handler.redirect.assert_called_once_with(
                    other_handler.contest_url(login_error="true"))
                self.assertFalse(login_task.done())
            finally:
                check.release.set()
                await login_task

    async def test_no_db_connection_is_held_while_the_check_runs(self):
        handler = self.make_handler()
        # The handler's own session holds one connection, as it would
        # after prepare(); the others are not ours.
        self.assertTrue(handler.sql_session.in_transaction())
        others = engine.pool.checkedout() - 1
        seen = {}

        def check(authentication_string, password):
            seen["in_transaction"] = handler.sql_session.in_transaction()
            seen["checked_out"] = engine.pool.checkedout()
            return True

        with patch(VALIDATE_PASSWORD, check):
            await self.run_post(handler)

        self.assertFalse(seen["in_transaction"])
        self.assertEqual(seen["checked_out"], others)

    async def test_no_db_connection_is_taken_back_after_the_check(self):
        handler = self.make_handler()
        others = engine.pool.checkedout() - 1

        await self.run_post(handler)

        # Neither the cookie nor the redirect may lazily reload the
        # (expired) contest, which would check a connection out again.
        self.assertFalse(handler.sql_session.in_transaction())
        self.assertEqual(engine.pool.checkedout(), others)
        handler.redirect.assert_called_once()

    async def test_bcrypt_check_runs_in_the_dedicated_pool(self):
        self.change(user__password=hash_password("mypass"))
        handler = self.make_handler()
        thread_names = []

        def recording_check(authentication_string, password):
            thread_names.append(threading.current_thread().name)
            return validate_password(authentication_string, password)

        with patch(VALIDATE_PASSWORD, recording_check):
            await self.run_post(handler)

        self.assertEqual(len(thread_names), 1)
        self.assertTrue(
            thread_names[0].startswith("cws-password-check"), thread_names)
        handler.redirect.assert_called_once_with(handler.contest_url())
        handler.set_secure_cookie.assert_called_once()


class TestValidateLoginAsync(LoginTestBase):
    """validate_login_async, against the synchronous validate_login."""

    def outcome(self, validate, username, password, ip_address, admin_token):
        """Run a login in a fresh session, collecting what it does."""
        session = self.new_session()
        contest = session.get(Contest, self.contest_id)
        with self.assertLogs(authentication.logger, "DEBUG") as logs:
            result = validate(
                session, contest, self.timestamp, username, password,
                ipaddress.ip_address(ip_address), admin_token)
        return result, [(record.levelname, record.getMessage())
                        for record in logs.records]

    async def assertSameOutcome(
            self, username="myuser", password="mypass",
            ip_address="127.0.0.1", admin_token="", success=False):
        """Check the async variant against the sync one, and the result."""
        (sync_participation, sync_cookie), sync_logs = self.outcome(
            validate_login, username, password, ip_address, admin_token)

        session = self.new_session()
        contest = session.get(Contest, self.contest_id)
        with self.assertLogs(authentication.logger, "DEBUG") as logs:
            async_participation, async_cookie = \
                await authentication.validate_login_async(
                    session, contest, self.timestamp, username, password,
                    ipaddress.ip_address(ip_address), admin_token)
        async_logs = [(record.levelname, record.getMessage())
                      for record in logs.records]

        self.assertEqual(async_cookie, sync_cookie)
        self.assertEqual(async_logs, sync_logs)
        self.assertEqual(async_participation is not None, success)
        self.assertEqual(sync_participation is not None, success)
        if success:
            self.assertEqual(async_participation.id, self.participation_id)
        return async_logs

    async def login(self, username="myuser", password="mypass",
                    ip_address="127.0.0.1", admin_token=""):
        session = self.new_session()
        contest = session.get(Contest, self.contest_id)
        return await authentication.validate_login_async(
            session, contest, self.timestamp, username, password,
            ipaddress.ip_address(ip_address), admin_token)

    async def test_successful_login(self):
        logs = await self.assertSameOutcome(success=True)
        self.assertEqual(logs[-1][0], "INFO")
        self.assertIn("Successful login attempt", logs[-1][1])

    async def test_cookie_carries_the_stored_password(self):
        _, cookie = await self.login()
        self.assertEqual(
            json.loads(cookie),
            ["myuser", build_password("mypass"),
             make_timestamp(self.timestamp), False])

    async def test_wrong_password(self):
        logs = await self.assertSameOutcome(password="wrong")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][0], "INFO")
        self.assertTrue(logs[0][1].endswith("wrong password"), logs)

    async def test_unknown_user(self):
        logs = await self.assertSameOutcome(username="nobody")
        self.assertTrue(logs[0][1].endswith("user not registered to contest"))

    async def test_password_authentication_disabled(self):
        self.change(contest__allow_password_authentication=False)
        logs = await self.assertSameOutcome()
        self.assertTrue(logs[0][1].endswith("password authentication not allowed"))

    async def test_participation_specific_password(self):
        self.change(participation__password=build_password("other"))
        await self.assertSameOutcome(password="mypass")
        await self.assertSameOutcome(password="other", success=True)

    async def test_invalid_hash_stored_in_user(self):
        self.change(user__password="mypass")
        logs = await self.assertSameOutcome()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][0], "WARNING")
        self.assertIn(
            "Invalid password stored in database for user myuser in "
            "contest %s" % self.contest_name, logs[0][1])

    async def test_invalid_hash_stored_in_participation(self):
        self.change(participation__password="other")
        logs = await self.assertSameOutcome()
        self.assertEqual(logs[0][0], "WARNING")

    async def test_ip_restriction(self):
        self.change(contest__ip_restriction=True,
                    participation__ip=[ipaddress.ip_network("10.0.0.0/24")])
        logs = await self.assertSameOutcome(ip_address="10.0.1.1")
        self.assertTrue(logs[0][1].endswith("unauthorized IP address"))
        await self.assertSameOutcome(ip_address="10.0.0.1", success=True)

    async def test_ip_restriction_without_participation_ip(self):
        self.change(contest__ip_restriction=True)
        await self.assertSameOutcome(ip_address="10.0.1.1", success=True)

    async def test_hidden_participation(self):
        self.change(contest__block_hidden_participations=True,
                    participation__hidden=True)
        logs = await self.assertSameOutcome()
        self.assertTrue(
            logs[0][1].endswith("participation is hidden and unauthorized"))

    async def test_hidden_participation_allowed(self):
        self.change(participation__hidden=True)
        await self.assertSameOutcome(success=True)

    async def test_admin_token_login_needs_no_password_check(self):
        with patch.object(config.contest_web_server,
                          "contest_admin_token", "admin-token"), \
                patch(VALIDATE_PASSWORD) as check:
            await self.assertSameOutcome(
                password="", admin_token="admin-token", success=True)
            await self.assertSameOutcome(
                password="", admin_token="not-the-token")
        check.assert_not_called()

    async def test_admin_token_cookie_is_impersonated(self):
        with patch.object(config.contest_web_server,
                          "contest_admin_token", "admin-token"):
            _, cookie = await self.login(password="", admin_token="admin-token")
        self.assertEqual(
            json.loads(cookie),
            ["myuser", "", make_timestamp(self.timestamp), True])

    async def test_other_errors_of_the_check_propagate(self):
        with patch(VALIDATE_PASSWORD, side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                await self.login()

    async def test_pool_is_dedicated_and_bounded(self):
        pool = authentication._PASSWORD_CHECK_POOL
        self.assertEqual(pool._max_workers, min(4, os.cpu_count() or 1))
        self.assertTrue(pool._thread_name_prefix.startswith("cws-password"))


if __name__ == "__main__":
    unittest.main()
