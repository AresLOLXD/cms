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

"""Participant activity handlers for AWS for a specific contest.

"""

import asyncio
import csv
import functools
import io
import ipaddress
import logging
from datetime import datetime, timedelta, timezone

import tornado.web
from sqlalchemy import Select, and_, distinct, func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session, aliased
from sqlalchemy.types import DateTime

from cms import config
from cms.db import ActivityInterval, Contest, Participation, User
from cms.db.activity import ACTIVITY_END_INACTIVITY, ACTIVITY_END_LOGOUT, \
    activity_end_reason
from cmscommon.datetime import get_timezone, make_datetime
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


ACTIVITY_PAGE_SIZE = 100
# Most pairs of simultaneous activity shown on the contest page.
SIMULTANEOUS_LIMIT = 200
CSV_BATCH_SIZE = 1000
CSV_HEADER = ["username", "first_name", "last_name", "device_id", "ip",
              "started_at", "last_seen_at", "ended_at", "end_reason",
              "started_by"]

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def inactivity_threshold() -> timedelta:
    """Return how long without requests ends an interval."""
    return timedelta(
        seconds=config.contest_web_server.activity_inactivity_threshold)


def simultaneous_margin() -> timedelta:
    """Return the shortest overlap reported as simultaneous activity.

    It is twice the flush interval, which bounds how stale the
    last_seen_at of an active interval can be.

    """
    return timedelta(
        seconds=2 * config.contest_web_server.activity_flush_interval)


def parse_activity_filters(
    username: str, ip: str
) -> tuple[str, IPNetwork | None]:
    """Parse the filters of the activity page and of its CSV.

    username: the username to show only, or empty for all.
    ip: an IP address or network to show only, or empty for all.

    return: the stripped username and the network (None for all).

    raise (tornado.web.HTTPError): 400 if ip is not an address or a
        network.

    """
    username = username.strip()
    ip = ip.strip()
    if ip == "":
        return username, None
    try:
        return username, ipaddress.ip_network(ip, strict=False)
    except ValueError:
        raise tornado.web.HTTPError(
            400, "Invalid IP address or network: %s", ip)


def select_intervals(
    contest_id: int,
    username: str = "",
    network: IPNetwork | None = None,
) -> Select:
    """Return the query of the intervals of a contest, with their user.

    The IP address is selected as plain text (host()), since psycopg2
    reads INET values as ipaddress interfaces.

    contest_id: the contest.
    username: only this user's intervals, if not empty.
    network: only the intervals from this network, if not None.

    return: the query; callers add ordering and limits.

    """
    query = (
        select(ActivityInterval.id,
               ActivityInterval.participation_id,
               Participation.user_id,
               User.username,
               User.first_name,
               User.last_name,
               ActivityInterval.device_id,
               func.host(ActivityInterval.ip).label("ip"),
               ActivityInterval.started_at,
               ActivityInterval.last_seen_at,
               ActivityInterval.logged_out_at,
               ActivityInterval.started_by)
        .join(Participation,
              Participation.id == ActivityInterval.participation_id)
        .join(User, User.id == Participation.user_id)
        .where(Participation.contest_id == contest_id))
    if username != "":
        query = query.where(User.username == username)
    if network is not None:
        query = query.where(ActivityInterval.ip.op("<<=")(str(network)))
    return query


def find_simultaneous_activity(
    session: Session,
    contest_id: int,
    participation_id: int | None = None,
    limit: int | None = None,
) -> list[Row]:
    """Return the pairs of intervals active at the same time.

    Two intervals of one participation count when they come from
    different devices (or, if either has no device, from different
    IP addresses) and overlap by more than simultaneous_margin(). A
    dynamic IP change on one device gives consecutive intervals of the
    same device, which never count.

    session: the session to query with.
    contest_id: the contest.
    participation_id: only this participation's pairs, if not None.
    limit: return at most this many pairs, if not None.

    return: one row per pair, most recent overlap first; each row's
        total is the number of pairs without the limit (computed in
        the same scan, as the sort reads them all anyway).

    """
    first = aliased(ActivityInterval)
    second = aliased(ActivityInterval)
    margin = simultaneous_margin()
    overlap_start = func.greatest(
        first.started_at, second.started_at, type_=DateTime)
    overlap_end = func.least(
        first.last_seen_at, second.last_seen_at, type_=DateTime)
    query = (
        select(first.id.label("first_id"),
               second.id.label("second_id"),
               Participation.user_id,
               User.username,
               first.device_id.label("first_device_id"),
               func.host(first.ip).label("first_ip"),
               second.device_id.label("second_device_id"),
               func.host(second.ip).label("second_ip"),
               overlap_start.label("overlap_start"),
               overlap_end.label("overlap_end"),
               func.count().over().label("total"))
        .select_from(first)
        .join(second, and_(second.participation_id == first.participation_id,
                           second.id > first.id))
        .join(Participation, Participation.id == first.participation_id)
        .join(User, User.id == Participation.user_id)
        .where(Participation.contest_id == contest_id)
        .where(or_(first.device_id != second.device_id,
                   and_(or_(first.device_id.is_(None),
                            second.device_id.is_(None)),
                        first.ip != second.ip)))
        # overlap_end - overlap_start > margin, i.e. each end minus each
        # start is over the margin, spelled with last_seen_at bare so
        # that ix_activity_intervals_participation_id_last_seen_at can
        # bound a lookup of either interval.
        .where(first.last_seen_at > second.started_at + margin)
        .where(second.last_seen_at > first.started_at + margin)
        .where(first.last_seen_at > first.started_at + margin)
        .where(second.last_seen_at > second.started_at + margin)
        .order_by(overlap_start.desc()))
    if participation_id is not None:
        query = query.where(first.participation_id == participation_id)
    if limit is not None:
        query = query.limit(limit)
    return session.execute(query).all()


def find_multiple_devices(session: Session, contest_id: int) -> list[Row]:
    """Return the participations seen on more than one device.

    session: the session to query with.
    contest_id: the contest.

    return: rows with user_id, username and the number of devices,
        most devices first.

    """
    devices = func.count(distinct(ActivityInterval.device_id))
    query = (
        select(Participation.user_id, User.username,
               devices.label("devices"))
        .select_from(ActivityInterval)
        .join(Participation,
              Participation.id == ActivityInterval.participation_id)
        .join(User, User.id == Participation.user_id)
        .where(Participation.contest_id == contest_id)
        .group_by(Participation.id, Participation.user_id, User.username)
        .having(devices > 1)
        .order_by(devices.desc(), User.username))
    return session.execute(query).all()


def activity_render_params(
    session: Session,
    contest: Contest,
    participation_id: int | None = None,
) -> dict:
    """Return the render parameters shared by the activity views.

    The contest-wide simultaneous activity is capped at
    SIMULTANEOUS_LIMIT pairs; a participation's is not.

    session: the session to query with.
    contest: the contest.
    participation_id: only this participation's alerts, if not None.

    return: the simultaneous activity (most recent first) and its
        total, the ids of the intervals in it, the end reason function
        for the templates and the timezone.

    """
    simultaneous = find_simultaneous_activity(
        session, contest.id, participation_id,
        SIMULTANEOUS_LIMIT if participation_id is None else None)
    return {
        "activity_simultaneous": simultaneous,
        "activity_simultaneous_count":
            simultaneous[0].total if simultaneous else 0,
        "activity_flagged_ids":
            {row.first_id for row in simultaneous}
            | {row.second_id for row in simultaneous},
        "activity_end_reason": functools.partial(
            activity_end_reason, now=make_datetime(),
            inactivity_threshold=inactivity_threshold()),
        "timezone": get_timezone(None, contest),
    }


def iso_utc(dt: datetime | None) -> str:
    """Return a stored (naive UTC) time in ISO 8601 with its offset.

    dt: the time, or None.

    return: the formatted time, or an empty string for None.

    """
    return "" if dt is None else dt.replace(tzinfo=timezone.utc).isoformat()


def csv_row(row: Row, now: datetime) -> list[str]:
    """Return the CSV fields of an interval from select_intervals.

    row: the interval.
    now: the current time, to tell active intervals from ended ones.

    return: the fields, in the order of CSV_HEADER.

    """
    reason = activity_end_reason(
        row.logged_out_at, row.last_seen_at, now, inactivity_threshold())
    ended_at = {ACTIVITY_END_LOGOUT: row.logged_out_at,
                ACTIVITY_END_INACTIVITY: row.last_seen_at}.get(reason)
    return [row.username, row.first_name, row.last_name,
            "" if row.device_id is None else str(row.device_id),
            row.ip, iso_utc(row.started_at), iso_utc(row.last_seen_at),
            iso_utc(ended_at), reason, row.started_by]


def format_csv_rows(rows) -> str:
    """Return rows of fields as CSV text.

    rows (iterable of list of str): the rows.

    return: the CSV text.

    """
    output = io.StringIO()
    csv.writer(output).writerows(rows)
    return output.getvalue()


class ContestActivityHandler(BaseHandler):
    """Shows the participants' activity in a contest, with its alerts.

    """
    def _get_sync(self, contest_id: str):
        self.contest = self.safe_get_item(Contest, contest_id)
        ip_text = self.get_query_argument("ip", "")
        username, network = parse_activity_filters(
            self.get_query_argument("username", ""), ip_text)
        page = int(self.get_query_argument("page", 0))

        query = select_intervals(self.contest.id, username, network)
        count = self.sql_session.execute(
            select(func.count()).select_from(query.subquery())
        ).scalar_one()
        filters = {}
        if username != "":
            filters["username"] = username
        if network is not None:
            filters["ip"] = ip_text.strip()

        self.r_params = self.render_params()
        self.r_params.update(
            activity_render_params(self.sql_session, self.contest))
        self.r_params.update({
            "activity_intervals": self.sql_session.execute(
                query.order_by(ActivityInterval.last_seen_at.desc(),
                               ActivityInterval.id.desc())
                .offset(page * ACTIVITY_PAGE_SIZE)
                .limit(ACTIVITY_PAGE_SIZE)).all(),
            "activity_count": count,
            "activity_page": page,
            "activity_pages":
                (count + ACTIVITY_PAGE_SIZE - 1) // ACTIVITY_PAGE_SIZE,
            "activity_page_url": functools.partial(
                self.url, "contest", self.contest.id, "activity", **filters),
            "activity_csv_url": self.url(
                "contest", self.contest.id, "activity", "csv", **filters),
            "activity_filters": {"username": username,
                                 "ip": filters.get("ip", "")},
            "multiple_devices":
                find_multiple_devices(self.sql_session, self.contest.id),
            "simultaneous_margin": simultaneous_margin(),
        })
        self.render("contest_activity.html", **self.r_params)

    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self, contest_id: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._get_sync, contest_id)


class ContestActivityCsvHandler(BaseHandler):
    """Downloads the participants' activity in a contest as CSV.

    The rows are streamed in batches of CSV_BATCH_SIZE, read by
    increasing id, so that the whole log never sits in memory and the
    database connection is given back between batches.

    Once the first batch is sent the status and headers are gone, so a
    later failure cannot become an error page: the connection is
    closed instead, so that the client sees an incomplete download
    rather than a complete-looking, truncated file.

    """
    def _read_filters_sync(
        self, contest_id: str
    ) -> tuple[str, IPNetwork | None]:
        try:
            self.safe_get_item(Contest, contest_id)
            return parse_activity_filters(
                self.get_query_argument("username", ""),
                self.get_query_argument("ip", ""))
        finally:
            self.sql_session.rollback()

    def _fetch_batch_sync(
        self,
        contest_id: str,
        username: str,
        network: IPNetwork | None,
        after_id: int,
    ) -> list[Row]:
        try:
            return self.sql_session.execute(
                select_intervals(int(contest_id), username, network)
                .where(ActivityInterval.id > after_id)
                .order_by(ActivityInterval.id)
                .limit(CSV_BATCH_SIZE)).all()
        finally:
            # Give the connection back while the batch is sent.
            self.sql_session.rollback()

    async def _get_csv(self, contest_id: str):
        """Write the CSV, one batch at a time.

        contest_id: the contest, as in the URL.

        raise (tornado.web.HTTPError): 404 for an unknown contest, 400
            for an invalid filter; like any failure to read the first
            batch, they happen before anything is sent and get the
            usual error response.
        raise (Exception): whatever failed after the first batch was
            sent, once the connection has been closed and the failure
            logged.

        """
        loop = asyncio.get_running_loop()
        username, network = await loop.run_in_executor(
            None, self._read_filters_sync, contest_id)
        now = make_datetime()

        def fetch_batch(after_id: int):
            return loop.run_in_executor(
                None, self._fetch_batch_sync, contest_id, username,
                network, after_id)

        self.set_header("Content-Type", "text/csv")
        self.set_header("Content-Disposition",
                        "attachment; filename=\"activity.csv\"")
        self.write(format_csv_rows([CSV_HEADER]))
        # Nothing is flushed before the first batch is read, so a
        # failure there is still answered with a normal error page.
        rows = await fetch_batch(0)
        try:
            while rows:
                self.write(
                    format_csv_rows(csv_row(row, now) for row in rows))
                await self.flush()
                rows = await fetch_batch(rows[-1].id)
        except Exception:
            # The headers are already sent, so Tornado would end the
            # response normally, as if the file were complete.
            logger.error(
                "Activity export of contest %s aborted after sending "
                "part of the file.", contest_id, exc_info=True)
            self.request.connection.stream.close()
            raise

    @require_permission(BaseHandler.AUTHENTICATED)
    async def get(self, contest_id: str):
        await self._get_csv(contest_id)
