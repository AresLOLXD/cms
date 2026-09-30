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

"""The functional-test harness must not wait forever for a service.

requests has no default timeout, so a request to a hung AWS or CWS used to
block the whole functional run (and the CI job with it) until the runner's
own limit. Browser now passes REQUEST_TIMEOUT to every call it makes on its
session, and a request that times out fails like any other request error
instead of hanging.

The first group of tests checks the argument with a mocked session, the
second one that a timeout ends up as the usual error outcome, and the last
one that the timeout is really enforced, against a server that accepts a
connection and never answers.

"""

import contextlib
import io
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

import requests

from cmstestsuite import web
from cmstestsuite.web import Browser, GenericRequest, REQUEST_TIMEOUT


URL = "http://cms.example/page"


class PageRequest(GenericRequest):
    """A plain GET of URL."""

    def __init__(self, browser):
        super().__init__(browser, base_url="http://cms.example")
        self.url = URL

    def describe(self):
        return "load the page"


class TestBrowserPassesTimeout(unittest.TestCase):
    """Every call Browser makes on its session carries the timeout."""

    def setUp(self):
        super().setUp()
        self.browser = Browser()
        self.browser.session = mock.MagicMock()
        self.browser.xsrf_token = "token"

    def test_timeout_is_finite(self):
        self.assertIsInstance(REQUEST_TIMEOUT, (int, float))
        self.assertGreater(REQUEST_TIMEOUT, 0)

    def test_read_xsrf_token(self):
        self.browser.read_xsrf_token(URL)
        self.browser.session.get.assert_called_once_with(
            URL, timeout=REQUEST_TIMEOUT)

    def test_get(self):
        self.browser.do_request(URL)
        self.browser.session.get.assert_called_once_with(
            URL, timeout=REQUEST_TIMEOUT)

    def test_post(self):
        self.browser.do_request(URL, {"field": "value"})
        self.browser.session.post.assert_called_once_with(
            URL, {"field": "value", "_xsrf": "token"},
            timeout=REQUEST_TIMEOUT)

    def test_post_with_files(self):
        with tempfile.NamedTemporaryFile() as upload:
            self.browser.do_request(
                URL, {"field": "value"}, [("attachment", upload.name)])
        args, kwargs = self.browser.session.post.call_args
        self.assertEqual(args[0], URL)
        self.assertEqual(list(kwargs["files"]), ["attachment"])
        self.assertEqual(kwargs["timeout"], REQUEST_TIMEOUT)


class TestTimeoutIsAnError(unittest.TestCase):
    """A timed-out request is reported as an error, not swallowed."""

    def test_request_outcome_is_error(self):
        browser = Browser()
        browser.session = mock.MagicMock()
        browser.session.get.side_effect = requests.exceptions.ReadTimeout(
            "Read timed out. (read timeout=%s)" % REQUEST_TIMEOUT)
        request = PageRequest(browser)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            request.execute()

        self.assertEqual(request.outcome, GenericRequest.OUTCOME_ERROR)
        self.assertIsInstance(request.exc_value, requests.exceptions.Timeout)
        self.assertIn("terminated with an exception", stderr.getvalue())
        self.assertIn("Read timed out", stderr.getvalue())

    def test_direct_call_raises(self):
        # The framework's admin_req() and Browser.read_xsrf_token() use the
        # browser outside GenericRequest, so there the exception reaches
        # the harness itself.
        browser = Browser()
        browser.session = mock.MagicMock()
        browser.session.post.side_effect = requests.exceptions.ReadTimeout()
        with self.assertRaises(requests.exceptions.Timeout):
            browser.do_request(URL, {"field": "value"})


class TestHungServer(unittest.TestCase):
    """The timeout really is enforced, with no mocks."""

    TIMEOUT = 0.3

    # If the timeout were ever lost again, drop the pending connection after
    # this long, so that the test fails instead of hanging.
    GIVE_UP_AFTER = 10

    def test_request_to_a_server_that_never_answers_times_out(self):
        # The kernel completes the TCP handshake of a listening socket even
        # if nobody calls accept(), so the request is sent and then never
        # answered, like by a hung service.
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        url = "http://127.0.0.1:%d/" % listener.getsockname()[1]
        watchdog = threading.Timer(self.GIVE_UP_AFTER, listener.close)
        watchdog.daemon = True
        watchdog.start()
        self.addCleanup(watchdog.cancel)

        browser = Browser()
        self.addCleanup(browser.session.close)
        # Don't let a proxy from the environment get in the way.
        browser.session.trust_env = False

        start = time.monotonic()
        with mock.patch.object(web, "REQUEST_TIMEOUT", self.TIMEOUT):
            with self.assertRaises(requests.exceptions.ReadTimeout):
                browser.do_request(url)

        self.assertLess(time.monotonic() - start, self.GIVE_UP_AFTER)


if __name__ == "__main__":
    unittest.main()
