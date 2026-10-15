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

"""Read the Worker and ES logs of a load-test run.

Shared by the scripts of this directory. A run directory is
out/<run>/ as run.sh leaves it: cmslog/ with one directory per service.
Only the timestamped log files are read (cmslog/<Service>-<n>/[0-9]*.log);
last.log is a copy of the newest one.

"""

import datetime
import glob
import os
import re
from collections.abc import Iterator

# "[evaluate submission 12 on testcase s1-00-sample] Starting job." and
# "[compile submission 12] Finished job."
JOB_RE = re.compile(r"\[(evaluate|compile) submission (\d+)"
                    r"(?: on testcase (\S+?))?\] (Starting|Finished) job\.")


def timestamp(line: str) -> float:
    """Return the Unix time of a CMS log line (its first 23 characters).

    line: a log line, "2026-10-07 01:27:17,123 - INFO ...". CMS logs in
        UTC inside the containers.

    return: seconds since the epoch.

    """
    return datetime.datetime.strptime(
        line[:23], "%Y-%m-%d %H:%M:%S,%f").replace(
            tzinfo=datetime.timezone.utc).timestamp()


def log_files(run: str, service: str) -> list[str]:
    """Return the timestamped log files of every shard of a service.

    run: the run directory.
    service: the service name, e.g. "Worker" or "EvaluationService".

    return: the paths, sorted.

    """
    return sorted(glob.glob(
        os.path.join(run, "cmslog", "%s-*" % service, "[0-9]*.log")))


def worker_jobs(run: str) -> Iterator[tuple[str, str, int, str | None,
                                            float, float]]:
    """Yield every job a Worker started and finished.

    run: the run directory.

    yield: (log file, operation, submission id, testcase codename or
        None, start time, end time), operation being "evaluate" or
        "compile", in the order of each Worker's log.

    """
    for path in log_files(run, "Worker"):
        started: dict[tuple, float] = {}
        with open(path, errors="replace") as f:
            for line in f:
                match = JOB_RE.search(line)
                if match is None:
                    continue
                key = (match.group(1), int(match.group(2)), match.group(3))
                if match.group(4) == "Starting":
                    started[key] = timestamp(line)
                elif key in started:
                    yield (path,) + key + (started.pop(key), timestamp(line))


def quantile(values: list[float], fraction: float) -> float:
    """Return the nearest-rank quantile of values (nan if empty)."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)]


def run_name(run: str) -> str:
    """Return the name of a run directory."""
    return os.path.basename(run.rstrip("/"))
