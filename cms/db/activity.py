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

"""Participant activity database interface for SQLAlchemy.

"""

from datetime import datetime, timedelta
from uuid import UUID as PythonUUID

from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.schema import Column, ForeignKey, Index
from sqlalchemy.types import DateTime, Enum, Integer

from . import Base, Participation


ACTIVITY_STARTED_BY_LOGIN = "login"
ACTIVITY_STARTED_BY_RESUMED = "resumed"

ACTIVITY_END_LOGOUT = "logout"
ACTIVITY_END_INACTIVITY = "inactivity"
ACTIVITY_END_ACTIVE = "active"


class ActivityInterval(Base):
    """Class to store a continuous interval of activity of a participation.

    One row covers the requests of one participation from one device
    and one IP address, with no gap longer than the inactivity
    threshold between them. How the interval ended is not stored: see
    activity_end_reason(). There is deliberately no relationship to
    (or backref on) Participation: DumpExporter follows every
    relationship, and the log is exported as CSV from AWS instead.

    """
    __tablename__ = 'activity_intervals'
    __table_args__ = (
        Index("ix_activity_intervals_participation_id_last_seen_at",
              "participation_id", "last_seen_at"),
    )

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    # Participation (id) the activity belongs to.
    participation_id: int = Column(
        Integer,
        ForeignKey(Participation.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False)

    # Anonymous id of the browser (the cms_device cookie), or None when
    # the request presented no valid device cookie: an API client
    # authenticated by the X-CMS-Authorization header, or a browser
    # without the cookie (first request after an IP autologin, cleared
    # cookies, a cookie-less client), which gets it for its next
    # requests.
    device_id: PythonUUID | None = Column(
        UUID(as_uuid=True),
        nullable=True)

    # Real client IP address, as resolved behind the proxies. It is
    # written as a str; psycopg2 reads it back as an ipaddress
    # interface, so queries for display select host(ip) instead.
    ip: str = Column(
        INET,
        nullable=False,
        index=True)

    # First and last request of the interval. On an explicit logout
    # last_seen_at equals logged_out_at.
    started_at: datetime = Column(
        DateTime,
        nullable=False)
    last_seen_at: datetime = Column(
        DateTime,
        nullable=False)

    # Time of the explicit logout that closed the interval, if any.
    logged_out_at: datetime | None = Column(
        DateTime,
        nullable=True)

    # Whether a username/password login opened the interval, or an
    # existing session (cookie, header or IP autologin) did.
    started_by: str = Column(
        Enum(ACTIVITY_STARTED_BY_LOGIN, ACTIVITY_STARTED_BY_RESUMED,
             name="activity_started_by"),
        nullable=False)


def activity_end_reason(
    logged_out_at: datetime | None,
    last_seen_at: datetime,
    now: datetime,
    inactivity_threshold: timedelta,
) -> str:
    """Return how an interval ended, or that it is still going on.

    The end is not stored: an interval without logout whose last
    request is older than the inactivity threshold ended then.

    logged_out_at: the interval's logout time, if any.
    last_seen_at: the interval's last request.
    now: the current time.
    inactivity_threshold: how long without requests ends an
        interval.

    return: one of ACTIVITY_END_LOGOUT, ACTIVITY_END_INACTIVITY and
        ACTIVITY_END_ACTIVE.

    """
    if logged_out_at is not None:
        return ACTIVITY_END_LOGOUT
    if last_seen_at < now - inactivity_threshold:
        return ACTIVITY_END_INACTIVITY
    return ACTIVITY_END_ACTIVE
