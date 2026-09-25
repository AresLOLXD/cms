#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2013 Luca Wehrstedt <luca.wehrstedt@gmail.com>
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

"""Utilities related to SQLAlchemy async sessions.

Asyncio counterpart to cms.db.session: provides AsyncSession and
AsyncSessionGen on top of an async engine, for services migrated to
cms.io.async_service (sub-project 2.4) to use. No existing (synchronous)
call site is affected by this module -- see
docs/superpowers/specs/2026-09-25-async-db-access-design.md.

"""

import logging

from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession as _AsyncSession, async_sessionmaker, create_async_engine)

from cms.conf import config


logger = logging.getLogger(__name__)


def _async_database_url():
    """Derive the async engine's connection URL from config.database.url.

    Both cms.db's sync Session and this module's AsyncSession read
    connection parameters from the same cms.conf [database] section --
    only the driver component of the URL differs (postgresql+psycopg2
    for the sync engine, postgresql+psycopg for the async one; both
    route through the psycopg (v3) package, which supports both modes
    under the same dialect name). Returning the URL object itself
    (rather than str(url)) matters: str() on a SQLAlchemy URL masks the
    password with "***" by default, which would silently break every
    connection made with the swapped URL.

    return (sqlalchemy.engine.url.URL): the async engine's connection
        URL, with the real password intact.

    raise (AssertionError): if config.database.url isn't using the
        postgresql+psycopg2 driver this function expects to swap out.

    """
    sync_url = make_url(config.database.url)
    if sync_url.drivername != "postgresql+psycopg2":
        raise AssertionError(
            "cms.db.async_session expects config.database.url to use "
            "the postgresql+psycopg2 driver (got %r); it derives the "
            "async engine's URL by swapping that driver for "
            "postgresql+psycopg, the async mode of the same psycopg "
            "(v3) package." % sync_url.drivername)
    return sync_url.set(drivername="postgresql+psycopg")


# Mirrors cms/db/__init__.py's sync `engine = create_engine(...)` call:
# created once at import time, connects lazily on first actual use.
async_engine = create_async_engine(
    _async_database_url(), echo=config.database.debug,
    pool_timeout=60, pool_recycle=120)

AsyncSession = async_sessionmaker(async_engine)


class AsyncSessionGen:
    """Asyncio counterpart to cms.db.session.SessionGen.

    async with AsyncSessionGen() as session:
        await session.do_something()

    and at the end the session is automatically rolled back and
    closed. If one wants to commit the session, they have to call
    await session.commit() explicitly.

    Two-phase commit is not supported: AsyncSession has no
    begin_twophase()/prepare() equivalent -- SQLAlchemy's AsyncSession
    wraps a sync Session via a greenlet bridge, and while that inner
    sync Session would accept a twophase=True constructor argument,
    AsyncSession's own async transaction API (AsyncSession.begin())
    takes no such argument and exposes no two-phase methods at all. If
    config.database.twophase_commit is enabled, using this class would
    otherwise silently run without two-phase guarantees rather than
    failing outright -- __aenter__ raises instead, so this is caught
    the moment async DB access is actually attempted, not discovered
    later as a silent correctness gap.

    """

    def __init__(self):
        self.session: _AsyncSession | None = None

    async def __aenter__(self) -> _AsyncSession:
        if config.database.twophase_commit:
            raise NotImplementedError(
                "AsyncSessionGen does not support two-phase commit "
                "(config.database.twophase_commit is enabled, but "
                "SQLAlchemy's AsyncSession has no two-phase transaction "
                "API). Use the synchronous SessionGen for two-phase-"
                "commit code paths until this is resolved.")
        self.session = AsyncSession()
        return self.session

    async def __aexit__(self, unused1, unused2, unused3):
        await self.session.rollback()
        await self.session.close()
