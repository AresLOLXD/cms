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

"""Tests for the notifications of refused submissions."""

import unittest
from unittest.mock import MagicMock

from cms.server.contest.handlers.api import ApiSubmitHandler
from cms.server.contest.handlers.tasksubmission import SubmitHandler
from cms.server.contest.handlers.taskusertest import UserTestHandler
from cms.server.contest.phase_management import phase_refusal_text


def make_handler(cls, actual_phase, api=False, allow_unofficial=False):
    handler = cls.__new__(cls)
    handler._current_user = MagicMock(unrestricted=False)
    handler.impersonated_by_admin = False
    handler.api_request = api
    handler.is_multi_contest = lambda: False
    handler.r_params = {"actual_phase": actual_phase,
                        "testing_enabled": True}
    handler.request = MagicMock(method="POST", arguments={}, files={})
    handler.contest = MagicMock(
        allow_unofficial_submission_before_analysis_mode=allow_unofficial)
    handler.contest_url = MagicMock(return_value="/")
    handler.redirect = MagicMock()
    handler.notify_error = MagicMock()
    handler.json = MagicMock()
    return handler


class PhaseRefusalTextTest(unittest.TestCase):

    def test_texts(self):
        for phase in (-2, -1):
            self.assertEqual(phase_refusal_text(phase),
                             "The contest hasn't started yet.")
        for phase in (1, 2, 3, 4):
            self.assertEqual(phase_refusal_text(phase),
                             "The contest has already ended.")


class SubmitRefusalTest(unittest.TestCase):

    def test_after_the_contest(self):
        handler = make_handler(SubmitHandler, 4)
        handler.post("task")
        handler.notify_error.assert_called_once_with(
            "Submission not accepted", "The contest has already ended.")
        handler.redirect.assert_called_once_with("/")

    def test_before_the_contest(self):
        handler = make_handler(SubmitHandler, -2)
        handler.post("task")
        handler.notify_error.assert_called_once_with(
            "Submission not accepted", "The contest hasn't started yet.")

    def test_in_phase_refusal_without_unofficial_submissions(self):
        handler = make_handler(SubmitHandler, 2, allow_unofficial=False)
        handler.post("task")
        handler.notify_error.assert_called_once_with(
            "Submission not accepted", "The contest has already ended.")
        handler.redirect.assert_called_once_with("/")

    def test_api_still_gets_json(self):
        handler = make_handler(ApiSubmitHandler, 4, api=True)
        handler.post("task")
        handler.json.assert_called_once_with(
            {"error": "The contest is not open"}, 403)
        handler.notify_error.assert_not_called()


class UserTestRefusalTest(unittest.TestCase):

    def test_after_the_contest(self):
        handler = make_handler(UserTestHandler, 4)
        handler.post("task")
        handler.notify_error.assert_called_once_with(
            "Test not accepted", "The contest has already ended.")
