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

"""Ranking group database interface for SQLAlchemy.

"""

from datetime import datetime

from sqlalchemy.schema import Column
from sqlalchemy.types import Boolean, DateTime, Integer, Unicode

from cmscommon.ranking_groups import window_is_open

from . import Base


class RankingGroup(Base):
    """Class to store a ranking group.

    A ranking group is an independent public scoreboard, served by RWS
    at /<name>/, that one or more contests are sent to. It deliberately
    has no relationship to its contests (see Contest.ranking_group):
    DumpExporter follows every relationship, and a backref would make
    exporting one contest also export every contest of its group.

    """
    __tablename__ = 'ranking_groups'

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    # URL slug, validated with cmscommon.ranking_groups.
    name: str = Column(
        Unicode,
        nullable=False,
        unique=True)

    # Human readable title.
    description: str = Column(
        Unicode,
        nullable=False)

    # Derived: AWS writes hide_pending_at() here, i.e. whether the group
    # is hidden now or has a hide scheduled. It is kept only so that a CMS
    # rolled back to MC-2 minimal, which reads it, does not publish the
    # group; the current code never reads it.
    hidden: bool = Column(
        Boolean,
        nullable=False,
        default=False)

    # Staff password as a cmscommon.crypto authentication string (e.g.
    # "bcrypt:..."), or None when nobody can log in to a hidden ranking.
    staff_password: str | None = Column(
        Unicode,
        nullable=True)

    # MC-2 phase 2: the public scoreboard is hidden during
    # [hide_at, show_at) and frozen during [freeze_at, unfreeze_at);
    # hidden wins. Naive UTC; None means the end is open.
    hide_at: datetime | None = Column(DateTime, nullable=True)
    show_at: datetime | None = Column(DateTime, nullable=True)
    freeze_at: datetime | None = Column(DateTime, nullable=True)
    unfreeze_at: datetime | None = Column(DateTime, nullable=True)

    def is_hidden_at(self, now: datetime) -> bool:
        """Tell whether the group is hidden at now (naive UTC)."""
        return window_is_open(self.hide_at, self.show_at, now)

    def is_frozen_at(self, now: datetime) -> bool:
        """Tell whether the group is frozen, and not hidden, at now."""
        return not self.is_hidden_at(now) and \
            window_is_open(self.freeze_at, self.unfreeze_at, now)

    def hide_pending_at(self, now: datetime) -> bool:
        """Tell whether the group is hidden or will be, as of now.

        This is what the hidden column keeps, so that a CMS rolled back
        to MC-2 minimal does not publish it.

        """
        return self.hide_at is not None and \
            (self.show_at is None or self.show_at > now)
