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

"""Read the stat files of /proc, for cmsWatchdog (#38).

A private helper of cms.watchdog: probes.py (the /proc facts) and
control.py (the process sources and the actuators) both read these
files, and neither may import the other. It imports only the standard
library.

"""

import os


# The unit of the times in /proc/<pid>/stat (clock ticks per second).
CLOCK_TICKS = os.sysconf("SC_CLK_TCK")


def stat_fields(path: str) -> list[str]:
    """Return the fields of a /proc stat file after the command name.

    The command name is in parentheses and may hold spaces and ")", so
    the fields start after the last ")". The first one is the state
    (field 3 of proc(5)), the twelfth and thirteenth utime and stime.

    path: a /proc/<pid>/stat or /proc/<pid>/task/<tid>/stat file.

    return: the fields, as text.

    raise (OSError): if the file cannot be read.
    raise (IndexError): if it has no ")".

    """
    with open(path) as f:
        return f.read().rsplit(")", 1)[1].split()
