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

from sqlalchemy.schema import Column
from sqlalchemy.types import Integer, Unicode

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
