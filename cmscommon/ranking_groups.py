#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2018 Stefano Maggiolo <s.maggiolo@gmail.com>
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

"""Validation of ranking group names.

A ranking group name is the first path segment of an RWS URL
(``/<group>/``), so it must be URL-safe and must not shadow any path
the root RWS app already serves.

"""

import re

__all__ = ["GROUP_NAME_RE", "RESERVED_GROUP_NAMES", "is_valid_group_name",
           "window_is_open", "check_window"]


GROUP_NAME_RE = re.compile(r"[a-z0-9_-]+")

# First path segments served by the root RWS app: store handlers,
# routed handlers and top-level entries of cmsranking/static/, plus
# "groups", the directory holding the namespaces.
RESERVED_GROUP_NAMES = frozenset({
    "contests", "tasks", "teams", "users", "submissions", "subchanges",
    "faces", "flags", "sublist", "history", "scores", "events", "logo",
    "config", "lib", "img", "groups",
})


def is_valid_group_name(name: str) -> bool:
    """Return whether name can be used as a ranking group name.

    name: the candidate name.

    return: True if it is a URL-safe slug that is not reserved.

    """
    return GROUP_NAME_RE.fullmatch(name) is not None \
        and name not in RESERVED_GROUP_NAMES


def window_is_open(start, end, now) -> bool:
    """Tell whether now falls in the time window [start, end).

    start: when the window opens, or None if it never does.
    end: when it closes, or None if it never does.
    now: the time to check; comparable with start and end (Unix
        seconds, or naive UTC datetimes).

    return: True if start <= now < end, a missing end being +infinity.

    """
    return start is not None and start <= now \
        and (end is None or now < end)


def check_window(start, end, what: str) -> None:
    """Check that a time window ends after it starts.

    start: when the window opens, or None.
    end: when it closes, or None.
    what: the name of the window, for the error message.

    raise (ValueError): if both ends are set and end <= start.

    """
    if start is not None and end is not None and end <= start:
        raise ValueError("The %s window must end after it starts." % what)
