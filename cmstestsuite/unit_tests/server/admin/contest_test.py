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

"""Tests for ContestHandler's handling of the ranking group field.

The functional test harness edits a contest by POSTing to contest/<id>
without a "ranking_group_id" argument. That used to raise a KeyError
inside the handler, which was reported as an "Invalid field(s)"
notification and discarded every other field of the POST (nothing was
committed). An omitted "ranking_group_id" must instead leave the
contest's ranking group unchanged, like any other omitted field.

"""

import unittest
from unittest.mock import MagicMock

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import Contest, RankingGroup
from cms.server.admin.handlers.contest import ContestHandler


class TestContestHandlerRankingGroup(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.group = RankingGroup(name="olim", description="OLIM")
        self.other_group = RankingGroup(name="other", description="Other")
        self.session.add(self.group)
        self.session.add(self.other_group)
        self.contest = self.add_contest(
            name="before", description="before", languages=["C++17 / g++"],
            active=False, ranking_group=self.group)
        self.session.commit()
        self.contest_id = self.contest.id
        self.group_id = self.group.id
        self.other_group_id = self.other_group.id

    def tearDown(self):
        # A failed handler run may leave uncommitted changes in the
        # session, which would otherwise be flushed by delete_data().
        self.session.rollback()
        self.delete_data()
        super().tearDown()

    def _post(self, **extra_form):
        """Simulate a POST to contest/<id> and return the handler.

        The form always carries the fields the handler requires (name
        and the main group's start and stop) plus the given extras; a
        list value is sent as a repeated argument.

        """
        form = {
            "name": "after",
            "description": "after",
            "start": "2026-01-01 10:00:00",
            "stop": "2026-01-01 15:00:00",
            "active": "on",
            "languages": ["Python 3 / CPython", "C11 / gcc"],
        }
        form.update(extra_form)

        def get_argument(name, default=None):
            value = form.get(name, default)
            return value[0] if isinstance(value, list) else value

        def get_arguments(name):
            value = form.get(name, [])
            return value if isinstance(value, list) else [value]

        handler = ContestHandler.__new__(ContestHandler)
        handler.sql_session = self.session
        handler.application = MagicMock()
        handler.get_argument = get_argument
        handler.get_arguments = get_arguments
        handler.redirect = MagicMock()
        handler.url = lambda *args, **kwargs: "/contest"
        handler.schedule_rpc = MagicMock()
        handler._post_sync(str(self.contest_id))
        return handler

    def _reload_contest(self) -> Contest:
        """Return the contest as stored in the DB, dropping any
        uncommitted change made by the handler."""
        self.session.rollback()
        self.session.expire_all()
        return Contest.get_from_id(self.contest_id, self.session)

    def _notifications(self, handler):
        return [call.args[1]
                for call in handler.service.add_notification.call_args_list]

    def test_omitted_ranking_group_updates_other_fields_and_keeps_group(self):
        handler = self._post()

        self.assertEqual(self._notifications(handler), ["Operation successful."])
        handler.schedule_rpc.assert_called_once()
        contest = self._reload_contest()
        self.assertEqual(contest.name, "after")
        self.assertEqual(contest.description, "after")
        self.assertTrue(contest.active)
        self.assertEqual(
            contest.languages, ["Python 3 / CPython", "C11 / gcc"])
        self.assertEqual(contest.ranking_group_id, self.group_id)

    def test_omitted_ranking_group_keeps_unset_group(self):
        self.contest.ranking_group = None
        self.session.commit()

        self._post()

        contest = self._reload_contest()
        self.assertEqual(contest.name, "after")
        self.assertIsNone(contest.ranking_group_id)

    def test_valid_ranking_group_is_set(self):
        handler = self._post(ranking_group_id=str(self.other_group_id))

        self.assertEqual(self._notifications(handler), ["Operation successful."])
        contest = self._reload_contest()
        self.assertEqual(contest.name, "after")
        self.assertEqual(contest.ranking_group_id, self.other_group_id)

    def test_empty_ranking_group_clears_it(self):
        handler = self._post(ranking_group_id="")

        self.assertEqual(self._notifications(handler), ["Operation successful."])
        contest = self._reload_contest()
        self.assertEqual(contest.name, "after")
        self.assertIsNone(contest.ranking_group_id)

    def test_unknown_ranking_group_is_rejected_without_commit(self):
        unknown_id = self.other_group_id + 1000
        handler = self._post(ranking_group_id=str(unknown_id))

        self.assertEqual(self._notifications(handler), ["Invalid field(s)."])
        handler.schedule_rpc.assert_not_called()
        contest = self._reload_contest()
        self.assertEqual(contest.name, "before")
        self.assertFalse(contest.active)
        self.assertEqual(contest.ranking_group_id, self.group_id)

    def test_non_numeric_ranking_group_is_rejected_without_commit(self):
        handler = self._post(ranking_group_id="abc")

        self.assertEqual(self._notifications(handler), ["Invalid field(s)."])
        handler.schedule_rpc.assert_not_called()
        contest = self._reload_contest()
        self.assertEqual(contest.name, "before")
        self.assertEqual(contest.ranking_group_id, self.group_id)


if __name__ == "__main__":
    unittest.main()
