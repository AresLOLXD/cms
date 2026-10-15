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

"""Count the jobs the Workers really ran, their busy time, and the load.

For each run: the submissions in the DB, the compile and evaluate jobs
the Workers started and finished (from their logs, so testcases that ES
skipped and wrote as synthetic evaluations are not counted), the time
spent in those jobs, and how many submissions each user made.

Usage: python3 adhoc/jobs.py out/<run> [out/<run> ...]
Needs cmslog/, db_export.json and users.json of each run.

"""

import json
import os
import statistics
import sys

from workerlog import run_name, worker_jobs


def main() -> None:
    """Print one markdown table row per run given on the command line."""
    print("| run | submissions (DB) | compile jobs | evaluate jobs "
          "| evaluate jobs / submission "
          "| evaluate jobs / compiled-OK submission "
          "| job busy time (worker-s) | busy s / submission "
          "| per-user submissions mean / median / min-max |")
    print("|---|---|---|---|---|---|---|---|---|")
    for run in sys.argv[1:]:
        jobs = {"compile": 0, "evaluate": 0}
        busy = 0.0
        evaluated = set()
        for _, operation, submission, _, start, end in worker_jobs(run):
            jobs[operation] += 1
            busy += end - start
            if operation == "evaluate":
                evaluated.add(submission)
        with open(os.path.join(run, "db_export.json")) as f:
            submissions = json.load(f)["submissions"]
        per_user: dict[str, int] = {}
        for s in submissions:
            per_user[s["user"]] = per_user.get(s["user"], 0) + 1
        with open(os.path.join(run, "users.json")) as f:
            users = json.load(f)["users"]
        counts = [per_user.get(u["username"], 0) for u in users]
        count = len(submissions)
        print("| %s | %d | %d | %d | %.1f | %.1f | %.0f | %.2f "
              "| %.2f / %g / %d-%d |" % (
                  run_name(run), count, jobs["compile"], jobs["evaluate"],
                  jobs["evaluate"] / count,
                  jobs["evaluate"] / max(1, len(evaluated)), busy,
                  busy / count, statistics.mean(counts),
                  statistics.median(counts), min(counts), max(counts)))


if __name__ == "__main__":
    main()
