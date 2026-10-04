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

"""Buffered recording of the participants' activity for CWS.

Handlers report each authenticated request, login and logout to an
ActivityRecorder, which only updates an in-memory buffer. A periodic
flush writes the buffer to the activity_intervals table, on the async
session, in chunks of participations with one transaction each, so
that no request ever waits for the database because of this log.

"""

import asyncio
import dataclasses
import ipaddress
import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cms.db import ActivityInterval, AsyncSessionGen, Participation
from cms.db.activity import ACTIVITY_STARTED_BY_LOGIN, \
    ACTIVITY_STARTED_BY_RESUMED


logger = logging.getLogger(__name__)


# Key space of this log's advisory locks: the first key of the
# two-key form of pg_advisory_xact_lock (an int32), the participation
# id being the second, so that the log does not claim every lock key.
ACTIVITY_LOCK_NAMESPACE = 0x41435456  # "ACTV" in ASCII.

# How many participations a flush writes per transaction. It bounds
# the advisory locks a transaction holds (they live in PostgreSQL's
# shared lock table, of about max_locks_per_transaction *
# max_connections entries) and how long it holds them.
FLUSH_CHUNK_SIZE = 500

# Lock the participations :ids, in ascending order to avoid deadlocks.
# pg_advisory_xact_lock is volatile, so PostgreSQL evaluates it after
# the sort, one row at a time.
LOCK_PARTICIPATIONS = text(
    "SELECT pg_advisory_xact_lock(CAST(:namespace AS integer), id) "
    "FROM unnest(CAST(:ids AS integer[])) AS id ORDER BY id")


# (participation id, device id, IP address) of a stream of requests.
ActivityKey = tuple[int, UUID | None, str]


@dataclasses.dataclass
class PendingSegment:
    """Activity of one key not written to the database yet.

    first_seen and last_seen are the first and the last request of the
    segment; login tells whether a login started it; logged_out_at is
    the time of the logout that ended it, if any.

    """
    first_seen: datetime
    last_seen: datetime
    login: bool
    logged_out_at: datetime | None = None


PendingActivity = dict[ActivityKey, list[PendingSegment]]


def normalize_ip(ip: str) -> str | None:
    """Return an IP address in the form stored in the database.

    Since Python 3.12 ipaddress accepts the scope of an IPv6 address
    (fe80::1%eth0), which PostgreSQL's inet rejects, so the scope is
    dropped; the address is then written in its canonical form.

    ip: the address the request came from.

    return: the address to store, or None if ip is not an address.

    """
    try:
        return str(ipaddress.ip_address(ip.partition("%")[0]))
    except ValueError:
        return None


def split_into_chunks(pending: PendingActivity) -> list[PendingActivity]:
    """Split buffered activity by participation, for a flush.

    pending: the buffered activity, by key.

    return: the activity of up to FLUSH_CHUNK_SIZE participations per
        chunk, by ascending participation id.

    """
    by_participation: dict[int, PendingActivity] = {}
    for key, segments in pending.items():
        by_participation.setdefault(key[0], {})[key] = segments
    participation_ids = sorted(by_participation)
    chunks: list[PendingActivity] = []
    for start in range(0, len(participation_ids), FLUSH_CHUNK_SIZE):
        chunk: PendingActivity = {}
        for participation_id in \
                participation_ids[start:start + FLUSH_CHUNK_SIZE]:
            chunk.update(by_participation[participation_id])
        chunks.append(chunk)
    return chunks


class ActivityRecorder:
    """Buffer the participants' activity and write it periodically.

    One instance per CWS process. record() and record_logout() never
    touch the database; flush() writes what they buffered.

    """

    def __init__(self, inactivity_threshold: timedelta):
        """Create an empty recorder.

        inactivity_threshold: how long without requests ends an
            interval of activity.

        """
        self.inactivity_threshold = inactivity_threshold
        self._pending: PendingActivity = {}
        # Built before the service's event loop runs: since Python
        # 3.10 a Lock binds to the running loop only when a flush first
        # has to wait for it, not at construction.
        self._flush_lock = asyncio.Lock()

    def record(
        self,
        participation_id: int,
        device_id: UUID | None,
        ip: str,
        timestamp: datetime,
        login: bool = False,
    ):
        """Buffer one authenticated request (or a login).

        A request from something that is not an IP address is logged
        and not recorded.

        participation_id: the participation that made the request.
        device_id: the device it came from, or None if unknown.
        ip: the real IP address it came from.
        timestamp: the time of the request.
        login: whether the request is a successful login, which always
            starts a new interval.

        """
        self._record(participation_id, device_id, ip, timestamp, login)

    def record_logout(
        self,
        participation_id: int,
        device_id: UUID | None,
        ip: str,
        timestamp: datetime,
    ):
        """Buffer an explicit logout.

        See record() for the arguments.

        """
        segment = self._record(
            participation_id, device_id, ip, timestamp, login=False)
        if segment is not None:
            segment.logged_out_at = timestamp

    def _record(
        self,
        participation_id: int,
        device_id: UUID | None,
        ip: str,
        timestamp: datetime,
        login: bool,
    ) -> PendingSegment | None:
        """Buffer a request, see record().

        return: the segment the request went to, or None if it was not
            recorded.

        """
        stored_ip = normalize_ip(ip)
        if stored_ip is None:
            logger.warning("Activity of participation %d not recorded: "
                           "%r is not an IP address.", participation_id, ip)
            return None
        segments = self._pending.setdefault(
            (participation_id, device_id, stored_ip), [])
        last = segments[-1] if segments else None
        if (login or last is None or last.logged_out_at is not None
                or timestamp - last.last_seen > self.inactivity_threshold):
            segments.append(PendingSegment(timestamp, timestamp, login))
        else:
            last.last_seen = max(last.last_seen, timestamp)
        return segments[-1]

    def pending(self) -> PendingActivity:
        """Return the buffered activity (for tests and diagnostics)."""
        return self._pending

    async def flush(self):
        """Write the buffered activity to the database.

        The buffer is swapped out first, so requests served while the
        flush waits on the database go to a fresh one. It is written in
        chunks of FLUSH_CHUNK_SIZE participations, one transaction
        each. If a chunk fails, it is logged and its activity is put
        back in front of the new one, for the next flush to retry; the
        other chunks are still written. If the flush is cancelled,
        everything not committed yet is put back the same way.

        Flushes run one at a time, so the one at shutdown waits for a
        periodic one in flight.

        """
        async with self._flush_lock:
            pending, self._pending = self._pending, {}
            chunks = split_into_chunks(pending)
            unwritten: list[PendingActivity] = []
            index, committed = 0, False
            try:
                for index, chunk in enumerate(chunks):
                    committed = False
                    try:
                        async with AsyncSessionGen() as session:
                            await write_pending_activity(
                                session, chunk, self.inactivity_threshold)
                            await session.commit()
                            committed = True
                    except Exception:
                        logger.error(
                            "Could not store the activity of %d "
                            "participation(s); the next flush retries it.",
                            len({key[0] for key in chunk}), exc_info=True)
                        if not committed:
                            unwritten.append(chunk)
            except BaseException:
                # Cancelled (e.g. at shutdown): keep what is not
                # committed yet.
                unwritten.extend(chunks[index + committed:])
                raise
            finally:
                self._put_back(unwritten)

    def _put_back(self, chunks: list[PendingActivity]):
        """Put unwritten activity back in front of the newer one.

        chunks: activity swapped out by a flush and not written.

        """
        if not chunks:
            return
        restored: PendingActivity = {}
        for chunk in chunks:
            restored.update(chunk)
        for key, segments in self._pending.items():
            restored.setdefault(key, []).extend(segments)
        self._pending = restored


async def write_pending_activity(
    session: AsyncSession,
    pending: PendingActivity,
    inactivity_threshold: timedelta,
):
    """Merge buffered activity into the activity_intervals table.

    Meant for one chunk of a flush. Its participations are locked
    first, in one statement, with transaction-level advisory locks in
    ACTIVITY_LOCK_NAMESPACE taken in ascending order (to avoid
    deadlocks), so that two CWS shards flushing the same participation
    at the same time do not open the same interval twice. Activity of
    participations deleted in the meantime is dropped.

    session: the session to write with; the caller commits.
    pending: the buffered activity, by key.
    inactivity_threshold: the longest gap that still extends an
        interval.

    """
    participation_ids = sorted({key[0] for key in pending})
    await session.execute(LOCK_PARTICIPATIONS, {
        "namespace": ACTIVITY_LOCK_NAMESPACE, "ids": participation_ids})
    existing_ids = set((await session.execute(
        select(Participation.id)
        .where(Participation.id.in_(participation_ids))
    )).scalars().all())

    # The latest interval of every key of these participations. The IP
    # is compared as an address: PostgreSQL and Python do not always
    # spell it the same way (e.g. ::ffff:10.0.0.5).
    latest: dict[tuple[int, UUID | None,
                       ipaddress.IPv4Address | ipaddress.IPv6Address],
                 ActivityInterval] = {}
    for interval, host in (await session.execute(
        select(ActivityInterval, func.host(ActivityInterval.ip))
        .where(ActivityInterval.participation_id.in_(existing_ids))
        .distinct(ActivityInterval.participation_id,
                  ActivityInterval.device_id,
                  ActivityInterval.ip)
        .order_by(ActivityInterval.participation_id,
                  ActivityInterval.device_id,
                  ActivityInterval.ip,
                  ActivityInterval.last_seen_at.desc())
    )).all():
        latest[(interval.participation_id, interval.device_id,
                ipaddress.ip_address(host))] = interval

    for (participation_id, device_id, ip), segments in pending.items():
        if participation_id not in existing_ids:
            continue
        interval: ActivityInterval | None = latest.get(
            (participation_id, device_id, ipaddress.ip_address(ip)))
        for segment in segments:
            if (interval is not None
                    and interval.logged_out_at is None
                    and not segment.login
                    and segment.first_seen - interval.last_seen_at
                    <= inactivity_threshold):
                # Another shard may have written later activity first;
                # a logout ends the interval all the same.
                interval.started_at = min(
                    interval.started_at, segment.first_seen)
                interval.last_seen_at = (
                    segment.logged_out_at
                    if segment.logged_out_at is not None
                    else max(interval.last_seen_at, segment.last_seen))
                interval.logged_out_at = segment.logged_out_at
            else:
                interval = ActivityInterval(
                    device_id=device_id,
                    ip=ip,
                    started_at=segment.first_seen,
                    last_seen_at=segment.last_seen,
                    logged_out_at=segment.logged_out_at,
                    started_by=(ACTIVITY_STARTED_BY_LOGIN if segment.login
                                else ACTIVITY_STARTED_BY_RESUMED))
                # Base.__init__ does not take foreign key columns.
                interval.participation_id = participation_id
                session.add(interval)
