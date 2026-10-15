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

"""Give the duration of Worker jobs and the Worker processes' CPU per job.

For each run: percentiles of the evaluate and compile job durations
(Starting job -> Finished job in the Worker logs), of the gap between two
consecutive jobs on the same Worker, and the CPU the Worker processes
themselves used per evaluate job (from monitor.jsonl; the sandboxed
contestant code runs in isolate's own cgroups and is not included).

Usage: python3 adhoc/jobdur.py out/<run> [out/<run> ...]
Needs cmslog/ and monitor.jsonl of each run.

"""

import json
import os
import sys

from workerlog import quantile, run_name, worker_jobs


def main() -> None:
    """Print one markdown table row per run given on the command line."""
    print("| run | evaluate job p50 / p95 ms | compile job p50 / p95 ms "
          "| gap between jobs on a worker p50 / p95 ms "
          "| Worker python CPU ms / evaluate job |")
    print("|---|---|---|---|---|")
    for run in sys.argv[1:]:
        durations: dict[str, list[float]] = {"evaluate": [], "compile": []}
        gaps = []
        last_end: dict[str, float] = {}
        for path, operation, _, _, start, end in worker_jobs(run):
            if path in last_end:
                gaps.append(start - last_end[path])
            last_end[path] = end
            durations[operation].append(end - start)
        with open(os.path.join(run, "monitor.jsonl")) as f:
            samples = [json.loads(line) for line in f]
        first, last = samples[0], samples[-1]
        worker_cpu = sum(last["procs"][name]["cpu"]
                         - first["procs"][name]["cpu"]
                         for name in last["procs"]
                         if name.startswith("Worker"))
        evaluate, compile_ = durations["evaluate"], durations["compile"]
        print("| %s | %.0f / %.0f | %.0f / %.0f | %.0f / %.0f | %.1f |" % (
            run_name(run),
            1000 * quantile(evaluate, .5), 1000 * quantile(evaluate, .95),
            1000 * quantile(compile_, .5), 1000 * quantile(compile_, .95),
            1000 * quantile(gaps, .5), 1000 * quantile(gaps, .95),
            1000 * worker_cpu / len(evaluate)))


if __name__ == "__main__":
    main()
