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

"""Regression tests for the RPCs fired by CWS handlers.

The remote-service client methods are coroutine functions, and the CWS
handler bodies are synchronous and run on the event loop thread itself.
Calling a client method without awaiting it creates a coroutine that
never runs, so the RPC is never sent: submissions and user tests then
wait for EvaluationService's sweeper, and played tokens for
ProxyService's. The handlers must hand the call to schedule_rpc(),
which submits it to the event loop.

"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from cms.server.contest.handlers.api import ApiSubmitHandler
from cms.server.contest.handlers.tasksubmission import \
    SubmitHandler, UseTokenHandler
from cms.server.contest.handlers.taskusertest import UserTestHandler


async def wait_until(condition):
    """Let the event loop run until condition() holds, for at most 1 s.

    schedule_rpc() only queues the coroutine: the loop starts it on a
    later iteration.

    """
    for _ in range(100):
        if condition():
            return
        await asyncio.sleep(0.01)


class TestHandlersAwaitTheirRpcs(unittest.IsolatedAsyncioTestCase):
    """The handler bodies run for real, synchronously inside the running
    loop as they do in production. Only the database and the checks
    accepting a submission, a user test or a token are replaced.

    """

    def make_handler(self, handler_cls):
        """Return a handler of the given class for a logged-in
        contestant in the middle of the contest, as prepare() leaves it.

        """
        handler = handler_cls.__new__(handler_cls)
        # The loop that CommonRequestHandler.prepare() captures.
        handler._loop = asyncio.get_running_loop()
        handler._current_user = MagicMock()
        handler.impersonated_by_admin = False
        handler.is_multi_contest = lambda: False
        handler.r_params = {"actual_phase": 0, "testing_enabled": True}
        handler.timestamp = MagicMock()
        handler.application = MagicMock()
        handler.request = MagicMock()
        handler.request.arguments = {}
        handler.request.files = {}
        handler.sql_session = MagicMock()
        handler.get_task = MagicMock()
        handler.notify_success = MagicMock()
        handler.contest_url = MagicMock()
        handler.redirect = MagicMock()
        handler.json = MagicMock()
        return handler

    async def test_submit_handler_sends_new_submission(self):
        handler = self.make_handler(SubmitHandler)
        new_submission = AsyncMock()
        handler.service.evaluation_service.new_submission = new_submission

        with patch("cms.server.contest.handlers.tasksubmission."
                   "accept_submission", return_value=MagicMock(id=42)):
            handler.post("task")
        await wait_until(lambda: new_submission.await_count)

        new_submission.assert_awaited_once_with(submission_id=42)

    async def test_use_token_handler_sends_submission_tokened(self):
        handler = self.make_handler(UseTokenHandler)
        handler.get_submission = MagicMock(return_value=MagicMock(id=42))
        submission_tokened = AsyncMock()
        handler.service.proxy_service.submission_tokened = submission_tokened

        with patch("cms.server.contest.handlers.tasksubmission."
                   "accept_token"):
            handler.post("task", "1")
        await wait_until(lambda: submission_tokened.await_count)

        submission_tokened.assert_awaited_once_with(submission_id=42)

    async def test_user_test_handler_sends_new_user_test(self):
        handler = self.make_handler(UserTestHandler)
        new_user_test = AsyncMock()
        handler.service.evaluation_service.new_user_test = new_user_test

        with patch("cms.server.contest.handlers.taskusertest."
                   "accept_user_test", return_value=MagicMock(id=7)):
            handler.post("task")
        await wait_until(lambda: new_user_test.await_count)

        new_user_test.assert_awaited_once_with(user_test_id=7)

    async def test_api_submit_handler_sends_new_submission(self):
        handler = self.make_handler(ApiSubmitHandler)
        new_submission = AsyncMock()
        handler.service.evaluation_service.new_submission = new_submission

        with patch("cms.server.contest.handlers.api.accept_submission",
                   return_value=MagicMock(id=42)):
            handler.post("task")
        await wait_until(lambda: new_submission.await_count)

        new_submission.assert_awaited_once_with(submission_id=42)


class TestScheduleRpcOnContestHandlers(unittest.IsolatedAsyncioTestCase):
    """schedule_rpc() must work on CWS handlers, from the loop thread.

    That needs the handler's prepare() to reach
    CommonRequestHandler.prepare() through the super().prepare() chain,
    which is where the event loop is captured.

    """

    async def prepared_handler(self, handler_cls):
        """Return a handler that went through its real prepare().

        Only the steps needing the database, the translations or a real
        request are replaced; the rest of the chain, down to
        CommonRequestHandler.prepare(), is the real code.

        """
        handler = handler_cls.__new__(handler_cls)
        handler.application = MagicMock()
        handler.application.service = MagicMock(
            auth_handler=None, num_proxies_used=0, contest_id=1)
        handler.request = MagicMock(
            headers={}, remote_ip="127.0.0.1", path="/")
        # Set by __init__, which __new__ skips.
        handler.url = MagicMock()
        handler.contest = MagicMock(allowed_localizations=[])
        handler.choose_contest = AsyncMock()
        handler.setup_locale = MagicMock()
        handler.set_header = MagicMock()
        handler.render_params = MagicMock(return_value={})
        await handler.prepare()
        return handler

    async def assert_rpc_is_awaited(self, handler_cls):
        handler = await self.prepared_handler(handler_cls)
        remote_method = AsyncMock()

        handler.schedule_rpc(remote_method, x=1)
        await wait_until(lambda: remote_method.await_count)

        remote_method.assert_awaited_once_with(x=1)

    async def test_submit_handler(self):
        await self.assert_rpc_is_awaited(SubmitHandler)

    async def test_use_token_handler(self):
        await self.assert_rpc_is_awaited(UseTokenHandler)

    async def test_user_test_handler(self):
        await self.assert_rpc_is_awaited(UserTestHandler)

    async def test_api_submit_handler(self):
        await self.assert_rpc_is_awaited(ApiSubmitHandler)


if __name__ == "__main__":
    unittest.main()
