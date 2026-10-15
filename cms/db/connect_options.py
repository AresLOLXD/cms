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

"""The settings every CMS database connection gets, and the disconnect
rule.

Every connection to PostgreSQL (the sync engine, the async engine and
custom_psycopg2_connection) gets the libpq keywords of LIBPQ_DEFAULTS
and a fallback_application_name, merged under the query of the
configured URL, where the user's keys win. They bound how long a
connect, or a call on a dead connection, can wait (#39). No "options"
keyword is ever added, so a user's options and PGOPTIONS behave as
before.

This module imports only SQLAlchemy and the standard library: the
cms.db package imports it before it creates its engines.

"""

import logging
import os
import sys
from collections.abc import Sequence

from sqlalchemy.engine.url import URL
from sqlalchemy.exc import DBAPIError


logger = logging.getLogger(__name__)


# Libpq keywords, with string values as in a URL query.
LIBPQ_DEFAULTS: dict[str, str] = {
    # Seconds. Green psycopg2 enforces it in cms.io.PsycoGevent.
    "connect_timeout": "10",
    # Already libpq's default; kept explicit.
    "keepalives": "1",
    "keepalives_idle": "30",
    "keepalives_interval": "10",
    "keepalives_count": "3",
    # Milliseconds, unlike the others: "60" would mean 60 ms.
    "tcp_user_timeout": "60000",
}


def program_name(argv: Sequence[str]) -> str:
    """Return the name a program shows in pg_stat_activity.

    It is the basename of argv[0], followed by a space and argv[1] when
    that argument is a decimal integer (the shard of a service), for
    example "cmsContestWebServer 2". It is not cut: the server clips
    it to its own limit.

    argv: the program's command line, as in sys.argv.

    return: the name; empty when argv is.

    """
    if not argv:
        return ""
    name = os.path.basename(argv[0])
    if len(argv) > 1 and argv[1].isdecimal():
        name += " " + argv[1]
    return name


PROGRAM_NAME = program_name(sys.argv)


def with_connection_defaults(url: URL) -> URL:
    """Return url with the CMS connection defaults under its query.

    The defaults are LIBPQ_DEFAULTS and fallback_application_name set
    to PROGRAM_NAME. A key of url's query wins over its default, and
    the rest of the query (host=/socket/dir, sslmode, ...) passes
    through. It works on URL objects, because str(url) would mask the
    password.

    url: the configured database URL.

    return: the URL to connect with.

    """
    defaults = {**LIBPQ_DEFAULTS, "fallback_application_name": PROGRAM_NAME}
    return url.set(query={**defaults, **url.query})


def sqlstate(error: BaseException | None) -> str | None:
    """Return the SQLSTATE of a driver error.

    error: a psycopg2 error (its pgcode) or a psycopg 3 one (its
        sqlstate), usually the orig of a SQLAlchemy DBAPIError.

    return: the five-character code, or None when the error has none,
        as with a failed connect.

    """
    return getattr(error, "pgcode", None) or getattr(error, "sqlstate", None)


def is_disconnect(error: BaseException) -> bool:
    """Tell whether an error says that the database connection is gone.

    That is a SQLAlchemy error whose connection SQLAlchemy invalidated
    (which covers driver errors with no SQLSTATE, such as "server
    closed the connection unexpectedly", and psycopg 3's InternalError
    on an idle kill), or one whose SQLSTATE is of class 08, "connection
    exception". Deadlocks (40P01), serialization failures (40001) and
    lock timeouts (55P03) are OperationalErrors too, but not
    disconnects: react to a disconnect with this rule, never with an
    exception class.

    error: any exception.

    return: whether the connection is gone.

    """
    return isinstance(error, DBAPIError) and (
        error.connection_invalidated
        or (sqlstate(error.orig) or "").startswith("08"))


def describe_error(error: BaseException) -> str:
    """Return the message of an error's driver error, on one line.

    SQLAlchemy's own text adds the statement and a link on more lines;
    the driver's message is what names the failure ("server closed the
    connection unexpectedly", "timeout expired", ...).

    error: any exception, usually a SQLAlchemy DBAPIError.

    return: the lines of the driver error's message (of error's own
        message, if it has no driver error) joined by spaces, or the
        class name when the message is empty.

    """
    cause = getattr(error, "orig", None) or error
    text = " ".join(line.strip() for line in str(cause).splitlines()
                    if line.strip())
    return text or type(cause).__name__


def log_rollback_error(error: Exception, body_failed: bool) -> bool:
    """Log an error of the rollback that ends a session, if harmless.

    SessionGen and AsyncSessionGen roll back on exit. When their block
    raised, the block's own error is the one to report, so a rollback
    error is only logged at DEBUG. When the block succeeded and the
    rollback finds the connection gone, the server has already
    discarded the transaction: that is a WARNING. Any other rollback
    error must propagate, as before.

    error: the error that the rollback raised.
    body_failed: whether the session's block raised.

    return: True if the error was logged and must not propagate; False
        if the caller must re-raise it.

    """
    if body_failed:
        logger.debug("The rollback after a failed session block failed "
                     "too: %s", describe_error(error))
        return True
    if is_disconnect(error):
        logger.warning("Lost the database connection before the session's "
                       "rollback: %s", describe_error(error))
        return True
    return False
