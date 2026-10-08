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


class PollTest(unittest.TestCase):

    def assertDelays(self, delays, expected):
        self.assertEqual(len(delays), len(expected))
        for got, want in zip(delays, expected):
            self.assertAlmostEqual(got, want)

    def run_poll(self, poll_cap, opaque_id, polls_until_final):
        """Run Driver.poll against a fake server.

        poll_cap: the --poll-cap value (None for no cap).
        opaque_id: the submission id, which sets the stagger of the backoff.
        polls_until_final: the number of the poll that sees a final status.

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
            polls.append(path)
            status = SCORED if len(polls) == polls_until_final else EXECUTED
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
        self.assertEqual(len(polls), polls_until_final)
        poll_driver.rec.submission.assert_called_once_with(**entry)
        return delays, entry

    def test_without_a_cap_the_backoff_grows_like_upstream(self):
        # Opaque id 100 has no stagger: the factor is 1.4.
        delays, entry = self.run_poll(None, 100, 9)
        self.assertDelays(delays, [1.4 ** k for k in range(9)])
        self.assertEqual(entry["polls"], 9)
        self.assertEqual(entry["final_status"], SCORED)

    def test_the_cap_limits_every_delay_after_the_multiplication(self):
        delays, entry = self.run_poll(5.0, 100, 9)
        self.assertDelays(delays, [1.4 ** k for k in range(5)] + [5.0] * 4)
        self.assertEqual(entry["polls"], 9)

    def test_a_cap_above_the_backoff_changes_nothing(self):
        capped, _ = self.run_poll(1000.0, 100, 9)
        uncapped, _ = self.run_poll(None, 100, 9)
        self.assertDelays(capped, uncapped)

    def test_the_cap_keeps_the_stagger_of_the_submission(self):
        # Opaque id 99 has hash 0.63: the factor is 1.4 + 0.63 * 0.2.
        factor = 1.526
        delays, _ = self.run_poll(10.0, 99, 12)
        self.assertEqual(delays[0], 1.0)
        self.assertAlmostEqual(delays[1], factor)
        self.assertAlmostEqual(delays[2], factor ** 2)
        self.assertEqual(max(delays), 10.0)
        self.assertEqual(delays[-3:], [10.0] * 3)

    def test_a_cap_below_the_first_delay_still_waits_one_second_first(self):
        # Like the page: the first delay is 1 s, the cap bites from the
        # second one.
        delays, _ = self.run_poll(0.5, 100, 4)
        self.assertDelays(delays, [1.0, 0.5, 0.5, 0.5])


class PollCapOptionTest(unittest.TestCase):

    def parse(self, *extra):
        argv = ["driver.py", "--users-file", "/nonexistent/users.json",
                "--cws", "http://cms:8888", "--rws", "http://ranking:8890",
                "--out", "/nonexistent/out"] + list(extra)
        with mock.patch.object(sys, "argv", argv):
            driver.main()

    def test_a_cap_that_is_not_positive_is_rejected(self):
        for value in ("0", "-3"):
            with self.subTest(value=value), \
                    mock.patch("sys.stderr"), \
                    self.assertRaises(SystemExit) as cm:
                self.parse("--poll-cap", value)
            self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
