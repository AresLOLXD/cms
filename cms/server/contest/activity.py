# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Buffered recording of the participants' activity for CWS.

Handlers report each authenticated request, login and logout to an
ActivityRecorder, which only updates an in-memory buffer. A periodic
flush writes the buffer to the activity_intervals table in one
transaction, on the async session, so that no request ever waits for
the database because of this log.

"""

import dataclasses
import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cms.db import ActivityInterval, AsyncSessionGen, Participation
from cms.db.activity import ACTIVITY_STARTED_BY_LOGIN, \
    ACTIVITY_STARTED_BY_RESUMED


logger = logging.getLogger(__name__)


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
        self._pending: dict[ActivityKey, list[PendingSegment]] = {}

    def record(
        self,
        participation_id: int,
        device_id: UUID | None,
        ip: str,
        timestamp: datetime,
        login: bool = False,
    ):
        """Buffer one authenticated request (or a login).

        participation_id: the participation that made the request.
        device_id: the device it came from, or None if unknown.
        ip: the real IP address it came from.
        timestamp: the time of the request.
        login: whether the request is a successful login, which always
            starts a new interval.

        """
        segments = self._pending.setdefault(
            (participation_id, device_id, ip), [])
        last = segments[-1] if segments else None
        if (login or last is None or last.logged_out_at is not None
                or timestamp - last.last_seen > self.inactivity_threshold):
            segments.append(PendingSegment(timestamp, timestamp, login))
        else:
            last.last_seen = max(last.last_seen, timestamp)

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
        self.record(participation_id, device_id, ip, timestamp)
        self._pending[(participation_id, device_id, ip)][-1] \
            .logged_out_at = timestamp

    def pending(self) -> dict[ActivityKey, list[PendingSegment]]:
        """Return the buffered activity (for tests and diagnostics)."""
        return self._pending

    async def flush(self):
        """Write the buffered activity to the database.

        The buffer is swapped out first, so requests served while the
        flush waits on the database go to a fresh one. If writing
        fails, the swapped-out activity is put back in front of the
        new one and the next flush retries it.

        """
        pending, self._pending = self._pending, {}
        if not pending:
            return
        try:
            async with AsyncSessionGen() as session:
                await write_pending_activity(
                    session, pending, self.inactivity_threshold)
                await session.commit()
        except Exception:
            logger.error("Could not store the participants' activity; "
                         "the next flush retries it.", exc_info=True)
            for key, segments in self._pending.items():
                pending.setdefault(key, []).extend(segments)
            self._pending = pending


async def write_pending_activity(
    session: AsyncSession,
    pending: dict[ActivityKey, list[PendingSegment]],
    inactivity_threshold: timedelta,
):
    """Merge buffered activity into the activity_intervals table.

    Each participation is locked first (a transaction-level advisory
    lock, taken in ascending order to avoid deadlocks), so that two
    CWS shards flushing the same participation at the same time do not
    open the same interval twice. Activity of participations deleted in
    the meantime is dropped.

    session: the session to write with; the caller commits.
    pending: the buffered activity, by key.
    inactivity_threshold: the longest gap that still extends an
        interval.

    """
    participation_ids = sorted({key[0] for key in pending})
    for participation_id in participation_ids:
        await session.execute(
            select(func.pg_advisory_xact_lock(participation_id)))
    existing_ids = set((await session.execute(
        select(Participation.id)
        .where(Participation.id.in_(participation_ids))
    )).scalars().all())

    for (participation_id, device_id, ip), segments in pending.items():
        if participation_id not in existing_ids:
            continue
        interval: ActivityInterval | None = (await session.execute(
            select(ActivityInterval)
            .where(ActivityInterval.participation_id == participation_id)
            .where(ActivityInterval.device_id == device_id)
            .where(ActivityInterval.ip == ip)
            .order_by(ActivityInterval.last_seen_at.desc())
            .limit(1)
        )).scalars().first()
        for segment in segments:
            if (interval is not None
                    and interval.logged_out_at is None
                    and not segment.login
                    and segment.first_seen - interval.last_seen_at
                    <= inactivity_threshold):
                # Another shard may have written later activity first.
                interval.started_at = min(
                    interval.started_at, segment.first_seen)
                interval.last_seen_at = max(
                    interval.last_seen_at, segment.last_seen)
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
