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

"""Tests for the buffered recording of the participants' activity.

"""

import asyncio
import ipaddress
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import func, select, text

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import ActivityInterval, Participation, Session
from cms.db.async_session import async_engine
from cms.server.contest.activity import (ACTIVITY_LOCK_NAMESPACE,
                                         ActivityRecorder, PendingSegment,
                                         write_pending_activity)


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

    def test_ipv4_address_is_kept_as_is(self):
        self.recorder.record(1, DEVICE, IP, minutes(0))
        self.assertEqual(list(self.recorder.pending()), [(1, DEVICE, IP)])

    def test_scoped_ipv6_address_is_recorded_without_its_scope(self):
        # PostgreSQL's inet rejects the scope of a link-local address.
        self.recorder.record(1, DEVICE, "fe80::1%eth0", minutes(0))
        self.recorder.record_logout(1, DEVICE, "fe80::1%eth0", minutes(1))
        self.assertEqual(self.recorder.pending(), {
            (1, DEVICE, "fe80::1"):
                [PendingSegment(minutes(0), minutes(1), False, minutes(1))],
        })

    def test_ipv6_address_is_recorded_in_canonical_form(self):
        self.recorder.record(1, DEVICE, "2001:DB8:0:0::1", minutes(0))
        self.recorder.record(1, DEVICE, "2001:db8::1", minutes(1))
        self.assertEqual(list(self.recorder.pending()),
                         [(1, DEVICE, "2001:db8::1")])

    def test_invalid_ip_address_is_not_recorded(self):
        with self.assertLogs("cms.server.contest.activity", "WARNING"):
            self.recorder.record(1, DEVICE, "not-an-ip", minutes(0))
        with self.assertLogs("cms.server.contest.activity", "WARNING"):
            self.recorder.record_logout(1, DEVICE, "not-an-ip%eth0",
                                        minutes(1))
        self.assertEqual(self.recorder.pending(), {})


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

    async def test_scoped_ipv6_address_is_stored_without_its_scope(self):
        self.recorder.record(self.participation_id, DEVICE, "fe80::1%eth0",
                             minutes(0))
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.ip, ipaddress.ip_interface("fe80::1"))
        self.assertEqual(self.recorder.pending(), {})

    async def test_ipv4_mapped_address_extends_its_interval(self):
        # PostgreSQL and Python spell this address differently.
        self.recorder.record(self.participation_id, DEVICE,
                             "::ffff:10.0.0.5", minutes(0))
        await self.recorder.flush()
        self.recorder.record(self.participation_id, DEVICE,
                             "::ffff:10.0.0.5", minutes(1))
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.last_seen_at, minutes(1))

    async def test_each_key_extends_its_own_latest_interval(self):
        keys = [(DEVICE, IP), (OTHER_DEVICE, IP), (None, IP),
                (DEVICE, "10.0.0.6")]
        # An older interval of the first key, which must not be picked.
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        await self.recorder.flush()
        for n in (40, 41):
            for device_id, ip in keys:
                self.recorder.record(self.participation_id, device_id, ip,
                                     minutes(n))
            await self.recorder.flush()

        intervals = self.intervals()
        self.assertEqual(
            sorted((i.started_at, i.last_seen_at) for i in intervals),
            [(minutes(0), minutes(0))] + [(minutes(40), minutes(41))] * 4)

    async def test_logout_sets_last_seen_even_after_another_shards_activity(
        self,
    ):
        other_shard = ActivityRecorder(THRESHOLD)
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        await self.recorder.flush()
        other_shard.record(self.participation_id, DEVICE, IP, minutes(10))
        await other_shard.flush()
        self.recorder.record_logout(
            self.participation_id, DEVICE, IP, minutes(5))
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.logged_out_at, minutes(5))
        self.assertEqual(interval.last_seen_at, minutes(5))

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

    async def test_activity_recorded_during_a_flush_waits_for_the_next_one(
        self,
    ):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))

        async def patched_write(session, pending, inactivity_threshold):
            # Record activity while the flush is in flight.
            self.recorder.record(
                self.participation_id, DEVICE, IP, minutes(1))
            # Now do the actual flush.
            return await write_pending_activity(
                session, pending, inactivity_threshold)

        with patch("cms.server.contest.activity.write_pending_activity",
                   side_effect=patched_write):
            await self.recorder.flush()

        # After the flush, pending() should have only the minutes(1) segment
        # (the minutes(0) was written).
        pending = self.recorder.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(len(pending[(self.participation_id, DEVICE, IP)]), 1)
        self.assertEqual(
            pending[(self.participation_id, DEVICE, IP)][0].first_seen,
            minutes(1))

        # DB has one interval ending at minutes(0).
        [interval] = self.intervals()
        self.assertEqual(interval.started_at, minutes(0))
        self.assertEqual(interval.last_seen_at, minutes(0))

        # A second flush extends that same interval to minutes(1).
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.started_at, minutes(0))
        self.assertEqual(interval.last_seen_at, minutes(1))

    async def test_activity_recorded_during_a_failed_flush_follows_the_old_one(
        self,
    ):
        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))

        async def failing_write(session, pending, inactivity_threshold):
            # Record activity while the flush is in flight.
            self.recorder.record(
                self.participation_id, DEVICE, IP, minutes(1))
            # Then fail the flush.
            raise OSError("database unavailable")

        with patch("cms.server.contest.activity.write_pending_activity",
                   side_effect=failing_write), \
                self.assertLogs("cms.server.contest.activity", "ERROR"):
            await self.recorder.flush()

        # pending() should have both segments in order (minutes(0) from the
        # failed flush, then minutes(1) from during the failed flush).
        pending = self.recorder.pending()
        segments = pending[(self.participation_id, DEVICE, IP)]
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].first_seen, minutes(0))
        self.assertEqual(segments[0].last_seen, minutes(0))
        self.assertEqual(segments[1].first_seen, minutes(1))
        self.assertEqual(segments[1].last_seen, minutes(1))

        # The DB is still empty.
        self.assertEqual(self.intervals(), [])

        # The next real flush writes one interval minutes(0)..minutes(1).
        await self.recorder.flush()

        [interval] = self.intervals()
        self.assertEqual(interval.started_at, minutes(0))
        self.assertEqual(interval.last_seen_at, minutes(1))

    async def test_concurrent_flushes_of_one_recorder_do_not_overlap(self):
        events = []
        first_write_started = asyncio.Event()

        async def slow_write(session, pending, inactivity_threshold):
            events.append("enter")
            first_write_started.set()
            await asyncio.sleep(0.05)
            await write_pending_activity(
                session, pending, inactivity_threshold)
            events.append("exit")

        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        with patch("cms.server.contest.activity.write_pending_activity",
                   side_effect=slow_write):
            first = asyncio.create_task(self.recorder.flush())
            await first_write_started.wait()
            self.recorder.record(
                self.participation_id, DEVICE, IP, minutes(1))
            await asyncio.gather(first, self.recorder.flush())

        self.assertEqual(events, ["enter", "exit", "enter", "exit"])
        [interval] = self.intervals()
        self.assertEqual(interval.last_seen_at, minutes(1))
        self.assertEqual(self.recorder.pending(), {})

    async def test_cancelled_flush_keeps_its_activity(self):
        write_started = asyncio.Event()

        async def hanging_write(session, pending, inactivity_threshold):
            write_started.set()
            await asyncio.Event().wait()

        self.recorder.record(self.participation_id, DEVICE, IP, minutes(0))
        with patch("cms.server.contest.activity.write_pending_activity",
                   side_effect=hanging_write):
            flush = asyncio.create_task(self.recorder.flush())
            await write_started.wait()
            flush.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await flush

        self.assertEqual(self.recorder.pending(), {
            (self.participation_id, DEVICE, IP):
                [PendingSegment(minutes(0), minutes(0), False)],
        })
        await self.recorder.flush()
        [interval] = self.intervals()
        self.assertEqual(interval.started_at, minutes(0))


class TestChunkedFlush(FlushTestBase):
    """A flush writes chunks of participations, each in its own
    transaction."""

    def setUp(self):
        super().setUp()
        participations = [self.add_participation() for _ in range(2)]
        self.session.commit()
        self.participation_ids = sorted(
            [self.participation_id] + [p.id for p in participations])
        self.session.close()
        for participation_id in self.participation_ids:
            self.recorder.record(participation_id, DEVICE, IP, minutes(0))

    def written_participation_ids(self) -> list[int]:
        return sorted(i.participation_id for i in self.intervals())

    async def test_chunks_are_written_in_separate_transactions(self):
        chunks = []

        async def tracking_write(session, pending, inactivity_threshold):
            await write_pending_activity(
                session, pending, inactivity_threshold)
            chunks.append((
                sorted(key[0] for key in pending),
                (await session.execute(select(func.txid_current())))
                .scalar_one()))

        with patch("cms.server.contest.activity.FLUSH_CHUNK_SIZE", 2), \
                patch("cms.server.contest.activity.write_pending_activity",
                      side_effect=tracking_write):
            await self.recorder.flush()

        self.assertEqual([ids for ids, _ in chunks],
                         [self.participation_ids[:2],
                          self.participation_ids[2:]])
        self.assertNotEqual(chunks[0][1], chunks[1][1])
        self.assertEqual(self.written_participation_ids(),
                         self.participation_ids)
        self.assertEqual(self.recorder.pending(), {})

    async def test_failed_chunk_keeps_only_its_activity(self):
        failing_id = self.participation_ids[0]

        async def failing_write(session, pending, inactivity_threshold):
            if any(key[0] == failing_id for key in pending):
                raise OSError("database unavailable")
            await write_pending_activity(
                session, pending, inactivity_threshold)

        with patch("cms.server.contest.activity.FLUSH_CHUNK_SIZE", 2), \
                patch("cms.server.contest.activity.write_pending_activity",
                      side_effect=failing_write), \
                self.assertLogs("cms.server.contest.activity", "ERROR"):
            await self.recorder.flush()

        # The chunk of the first two failed, the third one was written.
        self.assertEqual(self.written_participation_ids(),
                         self.participation_ids[2:])
        self.assertEqual(
            sorted(self.recorder.pending()),
            [(participation_id, DEVICE, IP)
             for participation_id in self.participation_ids[:2]])

        await self.recorder.flush()

        self.assertEqual(self.written_participation_ids(),
                         self.participation_ids)

    async def test_each_participation_is_locked_once_in_the_namespace(self):
        locks = []

        async def lock_reading_write(session, pending, inactivity_threshold):
            await write_pending_activity(
                session, pending, inactivity_threshold)
            locks.extend((await session.execute(text(
                "SELECT classid, objid, objsubid FROM pg_locks "
                "WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
            ))).all())

        with patch("cms.server.contest.activity.write_pending_activity",
                   side_effect=lock_reading_write):
            await self.recorder.flush()

        # objsubid 2 marks the two-key form (namespace, participation).
        self.assertEqual(
            sorted(tuple(lock) for lock in locks),
            [(ACTIVITY_LOCK_NAMESPACE, participation_id, 2)
             for participation_id in self.participation_ids])


if __name__ == "__main__":
    unittest.main()
