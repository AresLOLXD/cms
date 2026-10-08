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

"""Tests for the status poll of the load-test driver."""

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

# The driver runs in its own image, which has aiohttp; the poll under test
# does not use it, so a stand-in is enough to import the module here.
with mock.patch.dict(sys.modules, {"aiohttp": mock.MagicMock()}):
    import driver  # noqa: E402

SCORED = 5
EXECUTED = 4
TEMPLATE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "cms", "server", "contest",
    "templates", "task_submissions.html")


class PollTest(unittest.TestCase):

    def assertDelays(self, delays, expected):
        self.assertEqual(len(delays), len(expected))
        for got, want in zip(delays, expected):
            self.assertAlmostEqual(got, want)

    def run_poll(self, poll_cap, opaque_id, responses):
        """Run Driver.poll against a fake server.

        poll_cap: the --poll-cap value (None for no cap).
        opaque_id: the submission id, which sets the hash of the stagger.
        responses: what the server answers to each status poll, in order:
            "pending" (200, not final), "final" (200, scored) or an int
            (the HTTP status of a failed poll, -1 for a connection error).
            The last one must end the poll.

        return: the delays slept before each status poll, and the entry the
            poll recorded.

        """
        delays = []
        polls = []

        async def fake_sleep(delay):
            delays.append(delay)

        async def fake_http(user, method, path, kind, **kwargs):
            if kind != "status_poll":
                return 200, b"", None
            response = responses[len(polls)]
            polls.append(path)
            if isinstance(response, int):
                return response, b"", None
            status = SCORED if response == "final" else EXECUTED
            return 200, json.dumps({"status": status}).encode(), None

        poll_driver = object.__new__(driver.Driver)
        poll_driver.args = argparse.Namespace(
            poll_cap=poll_cap, drain_timeout=10 ** 6)
        poll_driver.stop = time.time() + 10 ** 6
        poll_driver.rec = mock.Mock()
        poll_driver.terminal = 0
        poll_driver.rng = random.Random(1)
        poll_driver.http = fake_http
        entry = {"task": "suma", "opaque_id": opaque_id}
        with mock.patch.object(driver, "asyncio",
                               types.SimpleNamespace(sleep=fake_sleep)):
            asyncio.run(poll_driver.poll({}, entry))
        self.assertEqual(len(polls), len(responses))
        poll_driver.rec.submission.assert_called_once_with(**entry)
        return delays, entry

    @staticmethod
    def final_at(poll_number):
        return ["pending"] * (poll_number - 1) + ["final"]

    def test_without_a_cap_the_backoff_grows_like_upstream(self):
        # Opaque id 100 has hash 0: the factor is 1.4.
        delays, entry = self.run_poll(None, 100, self.final_at(9))
        self.assertDelays(delays, [1.4 ** k for k in range(9)])
        self.assertEqual(entry["polls"], 9)
        self.assertEqual(entry["final_status"], SCORED)

    def test_the_cap_limits_every_delay_after_the_multiplication(self):
        # Hash 0 caps at 0.9 times the option: 4.5 s.
        delays, entry = self.run_poll(5.0, 100, self.final_at(9))
        self.assertDelays(delays, [1.4 ** k for k in range(5)] + [4.5] * 4)
        self.assertEqual(entry["polls"], 9)

    def test_a_cap_above_the_backoff_changes_nothing(self):
        capped, _ = self.run_poll(1000.0, 100, self.final_at(9))
        uncapped, _ = self.run_poll(None, 100, self.final_at(9))
        self.assertDelays(capped, uncapped)

    def test_the_cap_is_jittered_by_the_hash_of_the_submission(self):
        # The cap is 10 * (0.9 + hash * 0.2) seconds: the submissions that
        # reach it do not poll with the same period.
        for opaque_id, hash_, cap in ((100, 0.0, 9.0), (99, 0.63, 10.26),
                                      (27, 0.99, 10.98)):
            with self.subTest(opaque_id=opaque_id, hash=hash_):
                delays, _ = self.run_poll(10.0, opaque_id, self.final_at(14))
                self.assertAlmostEqual(delays[1], 1.4 + hash_ * 0.2)
                self.assertAlmostEqual(max(delays), cap)
                self.assertDelays(delays[-3:], [cap] * 3)

    def test_a_cap_below_the_first_delay_still_waits_one_second_first(self):
        # Like the page: the first delay is 1 s, the cap bites from the
        # second one (0.9 * 0.5 s for hash 0).
        delays, _ = self.run_poll(0.5, 100, self.final_at(4))
        self.assertDelays(delays, [1.0, 0.45, 0.45, 0.45])

    def test_a_poll_that_fails_with_status_0_or_5xx_is_asked_again(self):
        # -1 is the driver's status for a connection error (the page's 0).
        # The backoff goes on growing through the failures.
        delays, entry = self.run_poll(
            None, 100, [502, -1, 500, 503, "pending", "final"])
        self.assertDelays(delays, [1.4 ** k for k in range(6)])
        self.assertEqual(entry["polls"], 6)
        self.assertEqual(entry["final_status"], SCORED)
        self.assertNotIn("poll_status", entry)

    def test_a_poll_that_fails_with_a_4xx_stops_the_poll(self):
        # The session expired or the submission is gone, as in the page.
        for status in (400, 403, 404, 429):
            with self.subTest(status=status):
                delays, entry = self.run_poll(
                    None, 100, ["pending", status])
                self.assertEqual(len(delays), 2)
                self.assertEqual(entry["final_status"], "poll_failed")
                self.assertEqual(entry["poll_status"], status)
                self.assertEqual(entry["polls"], 2)
                self.assertNotIn("t_terminal_seen", entry)


class PollCapOptionTest(unittest.TestCase):

    def parse(self, *extra):
        argv = ["driver.py", "--users-file", "/nonexistent/users.json",
                "--cws", "http://cms:8888", "--rws", "http://ranking:8890",
                "--out", "/nonexistent/out"] + list(extra)
        with mock.patch.object(sys, "argv", argv):
            driver.main()

    def test_a_cap_that_is_not_a_positive_number_is_rejected(self):
        for value in ("0", "-3", "nan", "inf"):
            with self.subTest(value=value), \
                    mock.patch("sys.stderr"), \
                    self.assertRaises(SystemExit) as cm:
                self.parse("--poll-cap", value)
            self.assertEqual(cm.exception.code, 2)


class PageDriftTest(unittest.TestCase):
    """Keep task_submissions.html and the driver's emulation of it in step.

    A change of the poll in one of them without the other fails here.
    """

    def setUp(self):
        with open(TEMPLATE) as f:
            self.page = f.read()

    def find(self, pattern):
        match = re.search(pattern, self.page)
        self.assertIsNotNone(match, "not in the page: %s" % pattern)
        return match

    def test_the_page_cap_is_the_one_the_driver_documents(self):
        match = self.find(r"var MAX_STATUS_POLL_DELAY_MS = (\d+);")
        self.assertEqual(int(match.group(1)),
                         round(driver.PAGE_POLL_CAP_S * 1000))

    def test_the_page_jitters_the_cap_like_the_driver(self):
        match = self.find(
            r"MAX_STATUS_POLL_DELAY_MS\s*\*\s*\(\s*([0-9.]+)\s*\+\s*hash"
            r"\s*\*\s*([0-9.]+)\s*\)")
        self.assertEqual(
            (float(match.group(1)), float(match.group(2))),
            (driver.POLL_CAP_JITTER_BASE, driver.POLL_CAP_JITTER_SPAN))

    def test_the_page_backoff_is_the_one_the_driver_hard_codes(self):
        self.find(r"var hash = \(37 \* parseInt\(submission_id\)\) "
                  r"% 100 / 100\.0;")
        self.find(r"\* \(1\.4 \+ hash \* 0\.2\)")
        self.find(r"delays\[submission_id\] = 1000\.0;")

    def test_the_page_retries_the_failures_the_driver_retries(self):
        self.find(r"xhr\.status === 0 \|\| xhr\.status >= 500")


if __name__ == "__main__":
    unittest.main()
