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

"""Tests for the question notifications of AWS (NotificationsHandler).

A page of a contest asks only for the questions of that contest and of the
contests sent to the same ranking group; a page without contest asks for
all of them.

"""

import json
import unittest
from unittest.mock import MagicMock

from sqlalchemy import event

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import RankingGroup
from cms.server.admin.handlers.main import NotificationsHandler


class TestNotificationsHandler(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        group_g = RankingGroup(name="g", description="G")
        group_h = RankingGroup(name="h", description="H")
        self.session.add_all([group_g, group_h])
        # A and B share the group G, C is alone in H, D and E have none.
        self.contests = {
            name: self.add_contest(name=name, ranking_group=group)
            for name, group in (("a", group_g), ("b", group_g),
                                ("c", group_h), ("d", None), ("e", None))}
        self.questions = {
            name: self.add_question(
                participation=self.add_participation(contest=contest),
                subject=name)
            for name, contest in self.contests.items()}
        self.session.commit()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def notified(self, **arguments) -> list[str]:
        """Return the subjects of the new_question notifications."""
        handler = NotificationsHandler.__new__(NotificationsHandler)
        handler.sql_session = self.session
        # BaseHandler.service returns self.application.service.
        handler.application = MagicMock()
        handler.application.service.take_notifications.return_value = []
        handler.get_argument = \
            lambda name, default=None: arguments.get(name, default)
        handler.write = MagicMock()
        handler._get_sync()
        [response] = handler.write.call_args[0]
        return sorted(item["subject"] for item in json.loads(response))

    def test_a_contest_sees_the_contests_of_its_ranking_group(self):
        self.assertEqual(
            self.notified(contest_id=str(self.contests["a"].id)),
            ["a", "b"])

    def test_an_ungrouped_contest_sees_only_itself(self):
        self.assertEqual(
            self.notified(contest_id=str(self.contests["d"].id)), ["d"])

    def test_no_contest_sees_everything(self):
        self.assertEqual(self.notified(), ["a", "b", "c", "d", "e"])

    def test_an_unknown_or_bad_contest_id_sees_nothing(self):
        for contest_id in ("999999", "abc", ""):
            with self.subTest(contest_id=contest_id):
                self.assertEqual(self.notified(contest_id=contest_id), [])

    def test_the_query_count_does_not_grow_with_the_questions(self):
        def count_queries(contest_id):
            queries = []
            engine = self.session.get_bind()
            listener = lambda *args: queries.append(args[2])
            event.listen(engine, "before_cursor_execute", listener)
            try:
                self.notified(contest_id=str(contest_id))
            finally:
                event.remove(engine, "before_cursor_execute", listener)
            return len(queries)

        # Expire everything, as the handler gets a fresh session.
        self.session.expire_all()
        few = count_queries(self.contests["a"].id)
        for _ in range(5):
            self.add_question(participation=self.add_participation(
                contest=self.contests["a"]))
        self.session.commit()
        self.assertEqual(count_queries(self.contests["a"].id), few)


if __name__ == "__main__":
    unittest.main()
