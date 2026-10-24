#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2015 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2017 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
# Copyright © 2016 William Di Luigi <williamdiluigi@gmail.com>
# Copyright © 2016 Amir Keivan Mohtashami <akmohtashami97@gmail.com>
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

from datetime import datetime

import babel.dates

from cms.db import Contest
from cms.db.task import Dataset
from cmscommon.datetime import get_system_timezone, local_tz, utc


def format_dataset_attrs(dataset: Dataset) -> str:
    """Return a printable string for the attributes of a dataset.

    E.g.: live, autojudge enabled, etc.

    dataset: the dataset in question.

    return: printable string of relevant attributes.

    """
    if dataset is dataset.task.active_dataset:
        return " (Live)"
    elif dataset.autojudge:
        return " (Background judging)"
    else:
        return ""


def format_utc_time(value: datetime | None) -> str:
    """Return a stored (naive UTC) time with its zone named.

    value: the time, or None.

    return: the time as "YYYY-MM-DD HH:MM:SS UTC" (no microseconds),
        or an empty string for None.

    """
    return "" if value is None else value.strftime("%Y-%m-%d %H:%M:%S UTC")


def clock_zones(timestamp: datetime, contest: Contest | None) -> list[dict]:
    """Return the zones the sidebar clock shows.

    timestamp: the current time, naive UTC.
    contest: the contest the page is about, if any.

    return: UTC, the zone of the server and the zone of the contest (if
        set, valid and not already shown), each as a dict with the
        "label" to show and the "offset_seconds" from UTC at timestamp.

    """
    # get_system_timezone() gives the abbreviation ("CST") for a zoneinfo
    # zone, which is what Babel returns since 2.12: the key is the name.
    zones = [(getattr(local_tz, "key", None) or get_system_timezone(),
              local_tz)]
    if contest is not None and contest.timezone is not None:
        try:
            zones.append(
                (contest.timezone, babel.dates.get_timezone(contest.timezone)))
        except (LookupError, ValueError):
            # An unknown name, or one that is not a valid zone key.
            pass

    rows = [{"label": "UTC", "offset_seconds": 0}]
    for name, zone in zones:
        local = timestamp.replace(tzinfo=utc).astimezone(zone)
        # shortcut: the offset is fixed at page load, so a DST change
        # while the page is open is ignored; reload to refresh it.
        seconds = int(local.utcoffset().total_seconds())
        abbreviation = local.strftime("%Z")
        if seconds == 0 and abbreviation == "UTC":
            continue
        hours, minutes = divmod(abs(seconds) // 60, 60)
        offset = f"UTC{'-' if seconds < 0 else '+'}{hours:02d}:{minutes:02d}"
        row = {"label": f"{name} ({abbreviation}, {offset})",
               "offset_seconds": seconds}
        if row not in rows:
            rows.append(row)
    return rows
