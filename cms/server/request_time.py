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

"""When a request reached the deployment, as early as can be trusted.

Under load a request can wait seconds before ContestWebServer builds its
handler, so the handler's own time can turn an in-time submission into a
late one. Only a time that a trusted front proxy wrote in a header can
move the arrival time: it is never later than the handler time and never
earlier than MAX_SKEW before it.

"""

import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# How far before the handler time the arrival time can go. Bounds the
# effect of a skewed proxy clock or a forged header that slipped through.
MAX_SKEW = timedelta(seconds=60)

# A header value at or above this is in milliseconds, below it in seconds.
_MILLISECONDS_THRESHOLD = 10 ** 11

_HEADER_VALUE = re.compile(r"^\s*(?:t=)?([0-9]+(?:\.[0-9]+)?)\s*$")


def parse_request_time_header(value: str | None) -> datetime | None:
    """Parse a proxy's request time header.

    value (str|None): the header value: Unix time in seconds (with or
        without a fraction) or milliseconds, optionally prefixed by "t=".

    return (datetime|None): the time as a naive UTC datetime, or None if
        the value is missing or not understood.

    """
    if not value:
        return None
    match = _HEADER_VALUE.match(value)
    if match is None:
        logger.debug("Ignoring request time header %r.", value)
        return None
    number = float(match.group(1))
    if number >= _MILLISECONDS_THRESHOLD:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, timezone.utc).replace(
            tzinfo=None)
    except (OverflowError, OSError, ValueError):
        logger.debug("Ignoring request time header %r.", value)
        return None


def request_arrival_time(
    handler_time: datetime,
    header_time: datetime | None,
    max_skew: timedelta = MAX_SKEW,
) -> datetime:
    """Return when a request arrived, as early as can be trusted.

    handler_time (datetime): when the handler was built.
    header_time (datetime|None): the time a trusted proxy wrote, or None.
    max_skew (timedelta): how far before handler_time the result can go.

    return (datetime): header_time, unless it is missing or after
        handler_time (then handler_time) or more than max_skew before
        handler_time (then handler_time minus max_skew).

    """
    if header_time is None or header_time > handler_time:
        return handler_time
    return max(header_time, handler_time - max_skew)
