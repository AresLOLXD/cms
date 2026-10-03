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

"""Tests for the ActivityInterval model.

"""

import ipaddress
import unittest
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import ActivityInterval, Participation
from cms.db.activity import ACTIVITY_END_ACTIVE, ACTIVITY_END_INACTIVITY, \
    ACTIVITY_END_LOGOUT, activity_end_reason


NOW = datetime(2026, 10, 12, 12, 0, 0)
THRESHOLD = timedelta(minutes=30)


class TestActivityEndReason(unittest.TestCase):

    def test_logout_wins(self):
        self.assertEqual(
            activity_end_reason(NOW, NOW, NOW, THRESHOLD),
            ACTIVITY_END_LOGOUT)

    def test_old_last_request_is_inactivity(self):
        self.assertEqual(
            activity_end_reason(None, NOW - timedelta(minutes=31), NOW,
                                THRESHOLD),
            ACTIVITY_END_INACTIVITY)

    def test_recent_last_request_is_active(self):
        self.assertEqual(
            activity_end_reason(None, NOW - timedelta(minutes=29), NOW,
                                THRESHOLD),
            ACTIVITY_END_ACTIVE)


class TestActivityIntervalModel(DatabaseMixin, unittest.TestCase):

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def add_interval(self, participation: Participation) -> ActivityInterval:
        self.session.flush()
        interval = ActivityInterval(
            device_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
            ip="10.0.0.5",
            started_at=NOW,
            last_seen_at=NOW,
            started_by="login")
        # Base.__init__ does not take foreign key columns.
        interval.participation_id = participation.id
        self.session.add(interval)
        return interval

    def test_round_trip(self):
        participation = self.add_participation()
        interval = self.add_interval(participation)
        self.session.commit()
        self.session.expire_all()

        self.assertEqual(interval.ip, ipaddress.ip_interface("10.0.0.5"))
        self.assertEqual(interval.device_id,
                         uuid.UUID("00000000-0000-4000-8000-000000000001"))
        self.assertIsNone(interval.logged_out_at)

    def test_deleting_the_participation_deletes_its_intervals(self):
        participation = self.add_participation()
        self.add_interval(participation)
        self.session.commit()

        self.session.delete(participation)
        self.session.commit()

        self.assertEqual(
            self.session.execute(select(ActivityInterval)).all(), [])
