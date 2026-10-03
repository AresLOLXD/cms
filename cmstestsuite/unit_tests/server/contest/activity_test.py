# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the buffered recording of the participants' activity.

"""

import asyncio
import ipaddress
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import select

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import ActivityInterval, Participation, Session
from cms.db.async_session import async_engine
from cms.server.contest.activity import ActivityRecorder, PendingSegment


THRESHOLD = timedelta(minutes=30)
T0 = datetime(2026, 10, 12, 10, 0, 0)
DEVICE = uuid.UUID("00000000-0000-4000-8000-000000000001")
OTHER_DEVICE = uuid.UUID("00000000-0000-4000-8000-000000000002")
IP = "10.0.0.5"


def minutes(n: float) -> datetime:
    return T0 + timedelta(minutes=n)


class TestRecorderBuffer(unittest.TestCase):
    """The in-memory buffer, without a database."""

    def setUp(self):
        self.recorder = ActivityRecorder(THRESHOLD)

    def test_requests_of_one_key_make_one_segment(self):
        for n in (0, 1, 2):
            self.recorder.record(1, DEVICE, IP, minutes(n))
        self.assertEqual(self.recorder.pending(), {
            (1, DEVICE, IP): [PendingSegment(minutes(0), minutes(2), False)],
        })

    def test_out_of_order_request_does_not_move_last_seen_back(self):
        self.recorder.record(1, DEVICE, IP, minutes(2))
        self.recorder.record(1, DEVICE, IP, minutes(1))
        self.assertEqual(
            self.recorder.pending()[(1, DEVICE, IP)][0].last_seen, minutes(2))

    def test_login_starts_a_new_segment(self):
        self.recorder.record(1, DEVICE, IP, minutes(0))
        self.recorder.record(1, DEVICE, IP, minutes(1), login=True)
        self.assertEqual(self.recorder.pending()[(1, DEVICE, IP)], [
            PendingSegment(minutes(0), minutes(0), False),
            PendingSegment(minutes(1), minutes(1), True),
        ])

    def test_logout_closes_the_segment_and_later_activity_opens_another(self):
        self.recorder.record(1, DEVICE, IP, minutes(0))
        self.recorder.record_logout(1, DEVICE, IP, minutes(1))
        self.recorder.record(1, DEVICE, IP, minutes(2))
        self.assertEqual(self.recorder.pending()[(1, DEVICE, IP)], [
            PendingSegment(minutes(0), minutes(1), False, minutes(1)),
            PendingSegment(minutes(2), minutes(2), False),
        ])

    def test_gap_over_the_threshold_starts_a_new_segment(self):
        # Only happens when flushes fail for longer than the threshold.
        self.recorder.record(1, DEVICE, IP, minutes(0))
        self.recorder.record(1, DEVICE, IP, minutes(31))
        self.assertEqual(len(self.recorder.pending()[(1, DEVICE, IP)]), 2)

    def test_devices_and_ips_are_separate_keys(self):
        self.recorder.record(1, DEVICE, IP, minutes(0))
        self.recorder.record(1, OTHER_DEVICE, IP, minutes(0))
        self.recorder.record(1, None, IP, minutes(0))
        self.recorder.record(1, DEVICE, "10.0.0.6", minutes(0))
        self.assertEqual(len(self.recorder.pending()), 4)


class FlushTestBase(DatabaseMixin, unittest.IsolatedAsyncioTestCase):
    """A participation committed to the database, and a recorder."""

    def setUp(self):
        super().setUp()
        self.delete_data()
        self.participation = self.add_participation()
        self.session.commit()
        self.participation_id = self.participation.id
        self.session.close()
        self.recorder = ActivityRecorder(THRESHOLD)

    async def asyncTearDown(self):
        # Each test runs on its own event loop; don't let pooled
        # connections outlive it.
        await async_engine.dispose()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def intervals(self) -> list[ActivityInterval]:
        with Session() as session:
            return session.execute(
                select(ActivityInterval)
                .order_by(ActivityInterval.started_at,
                          ActivityInterval.id)
            ).scalars().all()


class TestFlush(FlushTestBase):

    async def test_first_flush_opens_an_interval(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(1))
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.participation_id, self.participation_id)
        self.assertEqual(interval.device_id, DEVICE)
        # psycopg2 reads INET as an interface.
        self.assertEqual(interval.ip, ipaddress.ip_interface(IP))
        self.assertEqual(interval.started_at, minutes(0))
        self.assertEqual(interval.last_seen_at, minutes(1))
        self.assertIsNone(interval.logged_out_at)
        self.assertEqual(interval.started_by, "resumed")
        self.assertEqual(self.recorder.pending(), {})

    async def test_activity_within_the_threshold_extends_the_interval(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        await self.recorder.flush()
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(29))
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.started_at, minutes(0))
        self.assertEqual(interval.last_seen_at, minutes(29))

    async def test_activity_after_the_threshold_opens_a_new_interval(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        await self.recorder.flush()
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(31))
        await self.recorder.flush()

        first, second = self.intervals()
        self.assertEqual(first.last_seen_at, minutes(0))
        self.assertEqual(second.started_at, minutes(31))
        self.assertEqual(second.started_by, "resumed")

    async def test_login_opens_a_new_interval_marked_login(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        await self.recorder.flush()
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(1),
                             login=True)
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(2))
        await self.recorder.flush()

        first, second = self.intervals()
        self.assertEqual(first.started_by, "resumed")
        self.assertEqual(first.last_seen_at, minutes(0))
        self.assertEqual(second.started_by, "login")
        self.assertEqual(second.started_at, minutes(1))
        self.assertEqual(second.last_seen_at, minutes(2))

    async def test_logout_closes_the_interval(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        await self.recorder.flush()
        self.recorder.record_logout(
            self.participation_id, DEVICE, IP, minutes(5))
        await self.recorder.flush()
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(6))
        await self.recorder.flush()

        first, second = self.intervals()
        self.assertEqual(first.last_seen_at, minutes(5))
        self.assertEqual(first.logged_out_at, minutes(5))
        self.assertEqual(second.started_at, minutes(6))
        self.assertIsNone(second.logged_out_at)

    async def test_header_clients_are_stored_without_device(self):
        self.recorder.record(self.participation_id, None, IP, minutes(0))
        await self.recorder.flush()
        self.recorder.record(self.participation_id, None, IP, minutes(1))
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertIsNone(interval.device_id)
        self.assertEqual(interval.last_seen_at, minutes(1))

    async def test_ipv6_addresses_are_stored(self):
        self.recorder.record(self.participation_id, DEVICE, "2001:db8::1",
                             minutes(0))
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.ip, ipaddress.ip_interface("2001:db8::1"))

    async def test_deleted_participation_is_dropped_not_retried(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        with Session() as session:
            session.delete(session.get(Participation, self.participation_id))
            session.commit()

        await self.recorder.flush()

        self.assertEqual(self.intervals(), [])
        self.assertEqual(self.recorder.pending(), {})

    async def test_failed_flush_keeps_the_activity_for_the_next_one(self):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        with patch("cms.server.contest.activity.write_pending_activity",
                   side_effect=OSError("database unavailable")), \
                self.assertLogs("cms.server.contest.activity", "ERROR"):
            await self.recorder.flush()
        self.assertEqual(self.intervals(), [])
        # Activity buffered while the database was unavailable.
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(1))

        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.started_at, minutes(0))
        self.assertEqual(interval.last_seen_at, minutes(1))

    async def test_concurrent_flushes_of_two_shards_open_one_interval(self):
        other_shard = ActivityRecorder(THRESHOLD)
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        other_shard.record(self.participation_id, DEVICE, IP, minutes(1))

        await asyncio.gather(self.recorder.flush(), other_shard.flush())

        [interval] = self.intervals()
        self.assertEqual(interval.started_at, minutes(0))
        self.assertEqual(interval.last_seen_at, minutes(1))


if __name__ == "__main__":
    unittest.main()
