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

"""Wait in an asyncio test for something done by a background task.

A service built inside a test's running event loop processes its work in
background tasks, so a test must wait for their effects before asserting
on them. A wait that gives up silently lets the next assertion fail on
partial data, with no hint of what had arrived: wait_until fails at the
wait itself and says what it saw.

"""

import asyncio
import time
from collections.abc import Callable


async def wait_until(
    predicate: Callable[[], object],
    timeout: float = 5.0,
    describe: Callable[[], str] | None = None,
) -> None:
    """Let the event loop run until predicate() is true.

    predicate: a zero-argument callable to poll.
    timeout: how many seconds to poll for before failing.
    describe: a zero-argument callable returning what was observed, put
        in the failure message (e.g. the URLs received so far).

    raise (AssertionError): if predicate() is still false after timeout.

    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            message = "Condition not met after %ss." % timeout
            if describe is not None:
                message += " Observed: %s" % describe()
            raise AssertionError(message)
        await asyncio.sleep(0.01)
