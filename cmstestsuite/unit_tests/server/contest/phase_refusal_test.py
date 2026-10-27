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

import tornado.web

from cms.server.contest.handlers.api import ApiSubmitHandler
from cms.server.contest.handlers.tasksubmission import SubmitHandler
from cms.server.contest.handlers.taskusertest import UserTestHandler
from cms.server.contest.phase_management import (
    actual_phase_required, phase_refusal_text)


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


class PhaseGateTest(unittest.TestCase):

    def test_no_refusal_subject_redirects_without_a_notification(self):
        # About 15 handlers rely on this default.
        handler = make_handler(SubmitHandler, 4)
        actual_phase_required(0)(MagicMock())(handler)
        handler.redirect.assert_called_once_with("/")
        handler.notify_error.assert_not_called()

    def test_phases_that_reach_the_handler(self):
        # A submit that gets past the gate asks for its task: a 404 here.
        for phase, unrestricted, refused in (
                (1, False, True), (2, False, True),
                (3, False, False), (4, True, False)):
            with self.subTest(phase=phase, unrestricted=unrestricted):
                handler = make_handler(SubmitHandler, phase)
                handler._current_user.unrestricted = unrestricted
                handler.get_task = MagicMock(return_value=None)
                if refused:
                    handler.post("task")
                    handler.redirect.assert_called_once_with("/")
                    handler.get_task.assert_not_called()
                else:
                    with self.assertRaises(tornado.web.HTTPError) as raised:
                        handler.post("task")
                    self.assertEqual(raised.exception.status_code, 404)


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
