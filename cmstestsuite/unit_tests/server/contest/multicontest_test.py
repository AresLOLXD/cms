"""Tests for serving only active contests in multi-contest mode."""

import unittest
from unittest.mock import MagicMock, patch

import tornado.web

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.server.contest.handlers.base import BaseHandler, ContestListHandler
from cms.server.contest.handlers.contest import ContestHandler


class TestActiveContests(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.delete_data()
        self.active = self.add_contest(active=True)
        self.inactive = self.add_contest(active=False)
        self.session.commit()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def make_contest_handler(self, contest_id, name) -> ContestHandler:
        handler = ContestHandler.__new__(ContestHandler)
        handler.application = MagicMock()
        handler.application.service = MagicMock(contest_id=contest_id)
        handler.path_args = [name]
        handler.sql_session = self.session
        handler.is_multi_contest = lambda: contest_id is None
        return handler

    def test_list_shows_only_active_contests(self):
        handler = ContestListHandler.__new__(ContestListHandler)
        handler.sql_session = self.session
        handler.render_params = MagicMock(return_value={})
        handler.render = MagicMock()
        handler.get()
        contest_list = handler.render.call_args.kwargs["contest_list"]
        self.assertEqual(set(contest_list), {self.active.name})

    def test_active_contest_is_served(self):
        handler = self.make_contest_handler(None, self.active.name)
        handler.choose_contest()
        self.assertEqual(handler.contest.id, self.active.id)

    @patch.object(BaseHandler, "render_params", return_value={})
    @patch.object(BaseHandler, "prepare")
    def test_inactive_contest_is_404(self, *_):
        handler = self.make_contest_handler(None, self.inactive.name)
        with self.assertRaises(tornado.web.HTTPError) as cm:
            handler.choose_contest()
        self.assertEqual(cm.exception.status_code, 404)

    def test_single_contest_mode_ignores_active(self):
        handler = self.make_contest_handler(self.inactive.id, "ignored")
        handler.choose_contest()
        self.assertEqual(handler.contest.id, self.inactive.id)


if __name__ == "__main__":
    unittest.main()
