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

"""Regression test for RankingHandler's eager-loading query.

The Task 3 .query()->select() rewrite kept the original string-based
loader options (joinedload("participations"), etc.). SQLAlchemy 2.0
raises ArgumentError for string-based attribute names in loader
options, so /contest/N/ranking (and its csv/txt exports) 500'd on
every single request. This exercises the handler's real query path
against a real database to prove it no longer raises, and that the
expected eager-loaded structure (participations -> submissions ->
token / results) actually comes back populated.

"""

import unittest
from unittest.mock import MagicMock

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.server.admin.handlers.contestranking import RankingHandler


class TestRankingHandler(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.task = self.add_task(contest=self.contest)
        self.participation = self.add_participation(contest=self.contest)
        self.submission = self.add_submission(self.task, self.participation)
        self.token = self.add_token(self.submission)
        self.dataset = self.add_dataset(task=self.task)
        self.task.active_dataset = self.dataset
        self.submission_result = self.add_submission_result(
            self.submission, self.dataset)
        self.session.commit()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def _make_handler(self):
        handler = RankingHandler.__new__(RankingHandler)
        handler.sql_session = self.session
        handler.contest = None
        handler._current_user = None
        handler.request = MagicMock()
        handler.application = MagicMock()
        handler.static_url_helper = lambda *args, **kwargs: ""
        handler.url = lambda *args, **kwargs: ""
        handler.xsrf_form_html = lambda: ""
        handler.render = MagicMock()
        handler.set_header = MagicMock()
        handler.finish = MagicMock()
        return handler

    def test_get_does_not_raise_argument_error_and_eager_loads(self):
        handler = self._make_handler()

        # This is the crux of the regression: with string-based loader
        # options this raised sqlalchemy.exc.ArgumentError before ever
        # reaching render(). Going through .get() (rather than duplicating
        # the query) proves the actual handler code, not a copy of it.
        RankingHandler.get.__wrapped__(handler, self.contest.id, "csv")

        handler.finish.assert_called_once()
        csv_output = handler.finish.call_args[0][0]

        # The eager-loaded submission, its token and its result should
        # have come back correctly (proving the joinedload chain was
        # rewritten to the right attributes, not just that some
        # ArgumentError-free but empty/wrong query ran instead).
        self.assertEqual(len(handler.contest.participations), 1)
        [participation] = handler.contest.participations
        self.assertEqual(len(participation.submissions), 1)
        [submission] = participation.submissions
        self.assertEqual(submission.token, self.token)
        self.assertEqual(list(submission.results), [self.submission_result])

        self.assertIn(self.participation.user.username, csv_output)


if __name__ == "__main__":
    unittest.main()
