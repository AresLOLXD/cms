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

"""Tests for the CSV export of the participants' activity.

"""

import csv
import io
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import tornado.web

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import ActivityInterval
from cms.server.admin.handlers import contestactivity
from cms.server.admin.handlers.contestactivity import \
    CSV_HEADER, ContestActivityCsvHandler, iso_utc


LAPTOP = uuid.UUID("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 10, 12, 12, 0, 0)


class TestIsoUtc(unittest.TestCase):

    def test_explicit_offset(self):
        self.assertEqual(iso_utc(datetime(2026, 10, 12, 10, 0, 0)),
                         "2026-10-12T10:00:00+00:00")
        self.assertEqual(iso_utc(None), "")


class TestActivityCsv(DatabaseMixin, unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.session.flush()
        self.username = self.participation.user.username

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def add_interval(self, ip, start, end, logged_out=False, device=LAPTOP):
        interval = ActivityInterval(
            device_id=device, ip=ip,
            started_at=NOW - timedelta(minutes=start),
            last_seen_at=NOW - timedelta(minutes=end),
            logged_out_at=NOW - timedelta(minutes=end) if logged_out else None,
            started_by="login")
        interval.participation_id = self.participation.id
        self.session.add(interval)

    async def download(self, **arguments) -> list[list[str]]:
        self.session.commit()
        handler = ContestActivityCsvHandler.__new__(ContestActivityCsvHandler)
        handler.sql_session = self.session
        handler.get_query_argument = \
            lambda name, default=None: arguments.get(name, default)
        handler.set_header = MagicMock()
        chunks = []
        handler.write = chunks.append
        handler.flush = AsyncMock()
        with patch.object(contestactivity, "make_datetime",
                          return_value=NOW):
            await handler._get_csv(str(self.contest.id))
        return list(csv.reader(io.StringIO("".join(chunks))))

    async def test_columns_and_end_reasons(self):
        self.add_interval("10.0.0.5", 120, 100, logged_out=True)
        self.add_interval("10.0.0.6", 90, 60)            # inactivity
        self.add_interval("2001:db8::1", 10, 1, device=None)  # active

        header, logout, inactivity, active = await self.download()

        self.assertEqual(header, CSV_HEADER)
        self.assertEqual(logout[0], self.username)
        self.assertEqual(logout[3], str(LAPTOP))
        self.assertEqual(logout[4], "10.0.0.5")
        self.assertEqual(logout[5], "2026-10-12T10:00:00+00:00")
        self.assertEqual(logout[7], "2026-10-12T10:20:00+00:00")
        self.assertEqual(logout[8:], ["logout", "login"])
        self.assertEqual(inactivity[7], "2026-10-12T11:00:00+00:00")
        self.assertEqual(inactivity[8], "inactivity")
        self.assertEqual(active[3], "")
        self.assertEqual(active[4], "2001:db8::1")
        self.assertEqual(active[7:9], ["", "active"])

    async def test_filters_and_batches(self):
        for n in range(5):
            self.add_interval("10.0.0.%d" % (n + 1), 50, 40)
        self.add_interval("172.16.0.1", 50, 40)

        with patch.object(contestactivity, "CSV_BATCH_SIZE", 2):
            rows = await self.download(ip="10.0.0.0/24")

        self.assertEqual(len(rows), 1 + 5)
        self.assertNotIn("172.16.0.1", [row[4] for row in rows])

    async def test_empty_log_is_only_the_header(self):
        self.assertEqual(await self.download(), [CSV_HEADER])

    async def test_invalid_filter_is_a_400(self):
        with self.assertRaises(tornado.web.HTTPError) as error:
            await self.download(ip="abc")
        self.assertEqual(error.exception.status_code, 400)
