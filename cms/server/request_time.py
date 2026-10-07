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
late one. The arrival time is the earliest of the handler time, the
handler time minus Tornado's elapsed time for the request, and a time a
trusted front proxy wrote in a header. It is never earlier than
MAX_SKEW before the handler time.

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

_HEADER_VALUE = re.compile(r"^\s*(?:t=)?(\d+(?:\.\d+)?)\s*$")


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
    elapsed: float | None,
    header_time: datetime | None,
    max_skew: timedelta = MAX_SKEW,
) -> tuple[datetime, str]:
    """Return when a request arrived, as early as can be trusted.

    handler_time (datetime): when the handler was built.
    elapsed (float|None): seconds Tornado reports since it read the
        request headers, or None.
    header_time (datetime|None): the time a trusted proxy wrote, or None.
    max_skew (timedelta): how far before handler_time the result can go.

    return ((datetime, str)): the arrival time and its source, one of
        "handler", "tornado" and "header".

    """
    candidates = [(handler_time, "handler")]
    if elapsed is not None and elapsed > 0:
        candidates.append(
            (handler_time - timedelta(seconds=elapsed), "tornado"))
    if header_time is not None and header_time <= handler_time:
        candidates.append((header_time, "header"))
    earliest, source = min(candidates, key=lambda candidate: candidate[0])
    return max(earliest, handler_time - max_skew), source
