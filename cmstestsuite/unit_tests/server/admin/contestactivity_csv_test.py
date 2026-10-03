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

    def make_handler(self, **arguments) -> ContestActivityCsvHandler:
        """Return a handler whose output is collected in self.chunks."""
        self.session.commit()
        handler = ContestActivityCsvHandler.__new__(ContestActivityCsvHandler)
        handler.sql_session = self.session
        handler.get_query_argument = \
            lambda name, default=None: arguments.get(name, default)
        handler.set_header = MagicMock()
        self.chunks = []
        handler.write = self.chunks.append
        handler.flush = AsyncMock()
        handler.request = MagicMock()

        fetch_batch = handler._fetch_batch_sync
        reads = []

        def capped_fetch_batch(*args):
            # A batch that restarts from the wrong id would loop forever.
            reads.append(args)
            self.assertLessEqual(len(reads), 20, "too many batches")
            return fetch_batch(*args)

        handler._fetch_batch_sync = capped_fetch_batch
        self.handler = handler
        return handler

    async def get_csv(self, handler: ContestActivityCsvHandler):
        with patch.object(contestactivity, "make_datetime",
                          return_value=NOW):
            await handler._get_csv(str(self.contest.id))

    async def download(self, **arguments) -> list[list[str]]:
        await self.get_csv(self.make_handler(**arguments))
        return list(csv.reader(io.StringIO("".join(self.chunks))))

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

    async def test_each_batch_is_flushed_on_its_own(self):
        for n in range(5):
            self.add_interval("10.0.0.%d" % (n + 1), 50, 40)

        with patch.object(contestactivity, "CSV_BATCH_SIZE", 2):
            await self.download()

        # The header, then batches of 2, 2 and 1 rows, each one flushed
        # before the next is read.
        self.assertEqual(
            [len(list(csv.reader(io.StringIO(chunk))))
             for chunk in self.chunks],
            [1, 2, 2, 1])
        self.assertEqual(self.handler.flush.await_count, 3)

    async def test_the_connection_is_released_around_each_read(self):
        for n in range(5):
            self.add_interval("10.0.0.%d" % (n + 1), 50, 40)

        with patch.object(self.session, "rollback",
                          wraps=self.session.rollback) as rollback, \
                patch.object(contestactivity, "CSV_BATCH_SIZE", 2):
            await self.download()

        # Once for the filters, then once per read: three batches and
        # the final, empty one.
        self.assertEqual(rollback.call_count, 1 + 4)

    async def test_batches_never_skip_or_repeat_a_row(self):
        # Matching and filtered-out rows alternate, so the ids a batch
        # ends on are never consecutive.
        for n in range(5):
            self.add_interval("10.0.0.%d" % (n + 1), 50, 40)
            self.session.flush()
            if n < 4:
                self.add_interval("172.16.0.%d" % (n + 1), 50, 40)
                self.session.flush()

        with patch.object(contestactivity, "CSV_BATCH_SIZE", 2):
            rows = await self.download(ip="10.0.0.0/24")

        self.assertEqual([row[4] for row in rows[1:]],
                         ["10.0.0.%d" % n for n in range(1, 6)])

    async def test_empty_log_is_only_the_header(self):
        self.assertEqual(await self.download(), [CSV_HEADER])

    async def test_failure_after_the_first_batch_aborts_the_download(self):
        for n in range(5):
            self.add_interval("10.0.0.%d" % (n + 1), 50, 40)
        handler = self.make_handler()
        fetch_batch = handler._fetch_batch_sync
        calls = []

        def fail_on_the_second_batch(*args):
            calls.append(args)
            if len(calls) == 2:
                raise RuntimeError("database gone")
            return fetch_batch(*args)

        with patch.object(handler, "_fetch_batch_sync",
                          side_effect=fail_on_the_second_batch), \
                patch.object(contestactivity, "CSV_BATCH_SIZE", 2), \
                self.assertLogs(contestactivity.logger, "ERROR"), \
                self.assertRaises(RuntimeError):
            await self.get_csv(handler)

        self.assertEqual(handler.flush.await_count, 1)
        handler.request.connection.stream.close.assert_called_once_with()

    async def test_failure_before_anything_is_sent_is_a_normal_error(self):
        self.add_interval("10.0.0.1", 50, 40)
        handler = self.make_handler()

        with patch.object(handler, "_fetch_batch_sync",
                          side_effect=RuntimeError("database gone")), \
                self.assertRaises(RuntimeError):
            await self.get_csv(handler)

        handler.flush.assert_not_awaited()
        handler.request.connection.stream.close.assert_not_called()

    async def test_invalid_filter_is_a_400(self):
        with self.assertRaises(tornado.web.HTTPError) as error:
            await self.download(ip="abc")
        self.assertEqual(error.exception.status_code, 400)
