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

"""Split the Workers' time in the saturated window of a run.

The window runs from the start of the end burst (stop - 300 s) to the
last monitor sample with a non-empty ES queue. For every Worker log it
adds up the time spent in jobs, the gaps between two jobs of the same
job group, and the idle time between groups ("Finished job group" ->
the next "Starting job group"). The last one includes the wait for ES
to take the results and send new work. It also gives the jobs per
second, the evaluate job median, the Worker processes' own CPU per job,
and the host's busy logical CPUs from hostsample.log if there is one.

Usage: python3 adhoc/satwindow.py [--host-log FILE] out/<run> [...]
Needs cmslog/, users.json and monitor.jsonl of each run. --host-log
defaults to hostsample.log next to the run directories (the output of
adhoc/hostsample.sh).

"""

import argparse
import json
import os

from workerlog import JOB_RE, log_files, run_name, timestamp

END_BURST_S = 300


def host_busy(path: str, start: float, end: float) -> list[float]:
    """Return the host's busy logical CPUs sampled between start and end.

    path: a hostsample.log; a missing file gives no samples.
    start: window start (Unix time).
    end: window end (Unix time).

    return: the busy_cores values of the samples in the window.

    """
    if not os.path.exists(path):
        return []
    values = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            fields = dict(p.split("=", 1) for p in parts[1:])
            if "busy_cores" in fields and start <= float(parts[0]) <= end:
                values.append(float(fields["busy_cores"]))
    return values


def main() -> None:
    """Print one markdown table row per run given on the command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host-log", help="hostsample.log to read")
    parser.add_argument("runs", nargs="+", metavar="run")
    args = parser.parse_args()
    print("| run | window s | jobs done | jobs/s | in jobs % "
          "| gaps inside groups % | idle between groups % | groups "
          "| mean group size | idle per group ms | evaluate p50 ms "
          "| Worker python CPU ms/job | host busy cores mean |")
    print("|" + "---|" * 13)
    for run in args.runs:
        with open(os.path.join(run, "users.json")) as f:
            stop = json.load(f)["stop"]
        with open(os.path.join(run, "monitor.jsonl")) as f:
            samples = [json.loads(line) for line in f]
        start = stop - END_BURST_S
        end = max(s["t"] for s in samples if (s.get("queue_ops") or 0) > 0)
        in_jobs = in_gaps = idle = 0.0
        jobs = groups = 0
        evaluate = []
        paths = log_files(run, "Worker")
        for path in paths:
            started: dict[tuple, float] = {}
            last_end = group_end = None
            with open(path, errors="replace") as f:
                for line in f:
                    if "Starting job group" in line:
                        t = timestamp(line)
                        if group_end is not None and start <= t <= end:
                            idle += t - max(group_end, start)
                        last_end = None
                        continue
                    if "Finished job group" in line:
                        group_end = timestamp(line)
                        if start <= group_end <= end:
                            groups += 1
                        continue
                    match = JOB_RE.search(line)
                    if match is None:
                        continue
                    t = timestamp(line)
                    key = match.group(1, 2, 3)
                    if match.group(4) == "Starting":
                        started[key] = t
                        if last_end is not None and start <= t <= end:
                            in_gaps += t - last_end
                    elif key in started:
                        begin = started.pop(key)
                        last_end = t
                        if start <= t <= end:
                            in_jobs += t - max(begin, start)
                            jobs += 1
                            if match.group(1) == "evaluate":
                                evaluate.append(t - begin)
        window = [s for s in samples if start <= s["t"] <= end]
        worker_cpu = sum(window[-1]["procs"][name]["cpu"]
                         - window[0]["procs"][name]["cpu"]
                         for name in window[-1]["procs"]
                         if name.startswith("Worker"))
        host_log = args.host_log or os.path.join(
            os.path.dirname(os.path.abspath(run.rstrip("/"))),
            "hostsample.log")
        host = host_busy(host_log, start, end)
        total = len(paths) * (end - start)
        print("| %s | %.0f | %d | %.1f | %.1f | %.1f | %.1f | %d | %.1f "
              "| %.0f | %.0f | %.1f | %s |" % (
                  run_name(run), end - start, jobs, jobs / (end - start),
                  100 * in_jobs / total, 100 * in_gaps / total,
                  100 * idle / total, groups, jobs / max(1, groups),
                  1000 * idle / max(1, groups),
                  1000 * sorted(evaluate)[len(evaluate) // 2],
                  1000 * worker_cpu / jobs,
                  "%.1f" % (sum(host) / len(host)) if host else "n/a"))


if __name__ == "__main__":
    main()
