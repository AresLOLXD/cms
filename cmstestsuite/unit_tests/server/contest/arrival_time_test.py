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

"""Tests for the arrival time of a request in a real CWS.

The phase of the contest, and the timestamp of a submission, are those
of when the request arrived, not of when its handler was built: under
load a request can wait seconds in between. A trusted front proxy can
tell CWS when it received the request in a header. The first class sends
real submissions, just after the end of the contest, through a real
ContestWebServer; the second builds handlers directly to check the
handler's times.

"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import tornado.web
from tornado.httpclient import HTTPResponse
from tornado.httputil import HTTPHeaders, HTTPServerRequest

from cmstestsuite.unit_tests.server.contest.xsrf_error_page_test import \
    CwsTestBase

from cms import config
from cms.db import Contest, Submission
from cms.server.contest.handlers.base import BaseHandler
from cms.server.util import CommonRequestHandler
from cmscommon.datetime import make_datetime


EPOCH = datetime(1970, 1, 1)


def unix(dt: datetime) -> float:
    return (dt - EPOCH).total_seconds()


class ArrivalTimeTest(CwsTestBase):
    """Submissions in the last seconds, to a contest that just ended."""

    multi_contest = False

    def setUp(self):
        super().setUp()
        # The submissions are only stored in the DB.
        local_copy = patch.object(
            config.contest_web_server, "submit_local_copy", False)
        local_copy.start()
        self.addCleanup(local_copy.stop)

        self.now = make_datetime()
        self.stop = self.now - timedelta(seconds=30)
        contest = self.session.get(Contest, self.contest_id)
        contest.languages = ["C++17 / g++"]
        contest.main_group.start = self.now - timedelta(hours=1)
        contest.main_group.stop = self.stop
        task = self.add_task(
            name="sum", contest=contest, submission_format=["sol.%l"])
        task.active_dataset = self.add_dataset(task=task)
        self.session.commit()
        self.session.close()

    async def submit(self, headers: dict[str, str]) -> HTTPResponse:
        """Log in and submit a solution to the task, with extra headers.

        headers: the headers to add to the request of the submission.

        return: the response of the submission, not following redirects.

        """
        token = await self.fetch_xsrf_token()
        login = await self.post(
            self.contest_path + "/login",
            {"username": "myuser", "password": "mypass", "_xsrf": token},
            cookies={"_xsrf": token})
        self.assertEqual(login.code, 302)
        cookies = self.set_cookies(login)
        cookies["_xsrf"] = token

        boundary = "arrival-time-test-boundary"
        parts = []
        for name, value in (("_xsrf", token), ("language", "C++17 / g++")):
            parts.append(
                '--%s\r\nContent-Disposition: form-data; name="%s"'
                '\r\n\r\n%s\r\n' % (boundary, name, value))
        parts.append(
            '--%s\r\nContent-Disposition: form-data; name="sol.%%l"; '
            'filename="sol.cpp"\r\nContent-Type: text/x-c++src\r\n\r\n'
            'int main() { return 0; }\r\n' % boundary)
        parts.append("--%s--\r\n" % boundary)

        request_headers = {
            "Accept-Language": "en",
            "Content-Type": "multipart/form-data; boundary=%s" % boundary,
            "Cookie": "; ".join("%s=%s" % item for item in cookies.items()),
            **headers,
        }
        return await self.client.fetch(
            "http://127.0.0.1:%d%s/tasks/sum/submit"
            % (self.port, self.contest_path),
            method="POST", body="".join(parts), headers=request_headers,
            follow_redirects=False, raise_error=False)

    async def test_header_moves_submission_before_the_stop(self):
        self.cws.request_time_header = "X-Request-Start"
        arrival = self.stop - timedelta(seconds=5)
        resp = await self.submit(headers={
            "X-Request-Start": "t=%d" % (unix(arrival) * 1000)})
        self.assertIn("submission_id=", resp.headers["Location"])
        stored = self.session.query(Submission).one()
        self.assertAlmostEqual(
            (stored.timestamp - arrival).total_seconds(), 0, delta=0.01)

    async def test_header_ignored_when_not_configured(self):
        self.cws.request_time_header = ""
        arrival = self.stop - timedelta(seconds=5)
        resp = await self.submit(headers={
            "X-Request-Start": "t=%d" % (unix(arrival) * 1000)})
        self.assertNotIn("submission_id=", resp.headers["Location"])
        self.assertEqual(self.session.query(Submission).count(), 0)

    async def test_header_clamped_to_max_skew(self):
        # The stop was 30 s ago: a header 10 min old is clamped to
        # handler time - 60 s, which is still before the stop, so the
        # submission is accepted with a timestamp about 60 s ago.
        self.cws.request_time_header = "X-Request-Start"
        long_ago = self.now - timedelta(minutes=10)
        resp = await self.submit(headers={
            "X-Request-Start": "%.3f" % unix(long_ago)})
        self.assertIn("submission_id=", resp.headers["Location"])
        stored = self.session.query(Submission).one()
        self.assertLess(
            abs((self.now - stored.timestamp).total_seconds() - 60), 5)


# Tests of the handler, on its own. A request that was read 3 s before
# its handler was built, as Tornado reports it, and the time at which
# the handler was built.
ELAPSED = 3.0
HANDLER_TIME = datetime(2026, 10, 10, 20, 0, 0)


class HandlerTimesTest(unittest.TestCase):

    def build_handler(self, handler_class, service, headers=None):
        """Build a handler with its real __init__, with no server.

        handler_class (type): the class of the handler.
        service (object): the service of the application.
        headers (dict|None): the headers of the request.

        return (CommonRequestHandler): the handler, built at
            HANDLER_TIME, for a request Tornado read ELAPSED seconds ago.

        """
        application = tornado.web.Application([])
        application.service = service
        request = HTTPServerRequest(
            method="GET", uri="/", headers=HTTPHeaders(headers or {}),
            connection=MagicMock())
        request.request_time = MagicMock(return_value=ELAPSED)
        with patch("cms.server.util.make_datetime",
                   return_value=HANDLER_TIME):
            handler = handler_class(application, request)
        self.addCleanup(handler.sql_session.close)
        return handler

    def test_now_is_handler_time(self):
        # The arrival time is what the phase and the timestamps use, but
        # the clock and the countdown of the page show the real time.
        service = MagicMock(request_time_header="")
        handler = self.build_handler(BaseHandler, service)

        self.assertEqual(handler.handler_time, HANDLER_TIME)
        self.assertEqual(handler.timestamp,
                         HANDLER_TIME - timedelta(seconds=ELAPSED))
        self.assertEqual(handler.render_params()["now"], HANDLER_TIME)

    def test_service_without_header_attribute(self):
        # A service with no request_time_header, or one that is a
        # MagicMock: __init__ does not fail, and the header is not read.
        header = {"X-Request-Start":
                  "%.3f" % unix(HANDLER_TIME - timedelta(seconds=30))}
        no_attribute = MagicMock(spec=["static_file_hasher"])
        any_attribute = MagicMock()
        for name, service in (("no attribute", no_attribute),
                              ("MagicMock attribute", any_attribute)):
            with self.subTest(service=name):
                handler = self.build_handler(
                    CommonRequestHandler, service, header)

                self.assertEqual(handler.handler_time, HANDLER_TIME)
                self.assertEqual(handler.timestamp,
                                 HANDLER_TIME - timedelta(seconds=ELAPSED))


del CwsTestBase


if __name__ == "__main__":
    unittest.main()
