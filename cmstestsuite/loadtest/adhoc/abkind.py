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

"""Break down the Workers' evaluate work per solution kind.

For each run and each solution kind of the scenario (ac, tle, wa_zero,
...): the submissions, the Evaluation rows in the DB, the evaluate jobs
the Workers really ran, the testcases ES skipped by two-phase screening
and by subtask dependencies (it writes a synthetic row for each), the
Worker time in evaluate jobs and the part of it in jobs over 0.5 s
(time-limit runs with the scenario's 1 s limit).

It joins db_export.json (submission id and opaque id),
submissions.jsonl (opaque id and kind), the Worker logs (jobs) and the
ES log ("synthesized N skipped evaluation(s) for submission S(D)", per
mechanism).

Usage: python3 adhoc/abkind.py out/<run> [out/<run> ...]

"""

import collections
import json
import os
import re
import sys

from workerlog import log_files, run_name, worker_jobs

SKIP_RE = re.compile(
    r"EvaluationService::(_advance_two_phase|_advance_dependencies)\] "
    r".*synthesized (\d+) skipped evaluation\(s\) for submission (\d+)\(")
SLOW_JOB_S = 0.5


def skipped(run: str) -> tuple[collections.Counter, collections.Counter]:
    """Return the testcases ES skipped per submission, per mechanism.

    run: the run directory.

    return: (by two-phase screening, by dependencies), submission id ->
        count.

    """
    two_phase: collections.Counter = collections.Counter()
    dependencies: collections.Counter = collections.Counter()
    for path in log_files(run, "EvaluationService"):
        with open(path, errors="replace") as f:
            for line in f:
                match = SKIP_RE.search(line)
                if match is not None:
                    target = (two_phase
                              if match.group(1) == "_advance_two_phase"
                              else dependencies)
                    target[int(match.group(3))] += int(match.group(2))
    return two_phase, dependencies


def main() -> None:
    """Print one markdown table per run given on the command line."""
    for run in sys.argv[1:]:
        with open(os.path.join(run, "db_export.json")) as f:
            submissions = json.load(f)["submissions"]
        kind_by_opaque = {}
        with open(os.path.join(run, "submissions.jsonl")) as f:
            for line in f:
                sent = json.loads(line)
                if sent.get("opaque_id") is not None:
                    kind_by_opaque[sent["opaque_id"]] = sent["kind"]
        kind = {s["id"]: kind_by_opaque.get(s["opaque_id"], "?")
                for s in submissions}
        rows = {s["id"]: s["evaluations"] for s in submissions}
        jobs: collections.Counter = collections.Counter()
        busy: collections.Counter = collections.Counter()
        slow: collections.Counter = collections.Counter()
        for _, operation, submission, _, start, end in worker_jobs(run):
            if operation != "evaluate":
                continue
            jobs[submission] += 1
            busy[submission] += end - start
            if end - start > SLOW_JOB_S:
                slow[submission] += end - start
        two_phase, dependencies = skipped(run)
        totals: dict[str, list[float]] = collections.defaultdict(
            lambda: [0, 0, 0.0, 0.0, 0, 0, 0])
        for submission, name in kind.items():
            values = (1, jobs[submission], busy[submission], slow[submission],
                      two_phase[submission], dependencies[submission],
                      rows[submission])
            totals[name] = [a + b for a, b in zip(totals[name], values)]
        print("\n%s" % run_name(run))
        print("| kind | subs | DB rows / sub | jobs run / sub "
              "| skipped by two-phase / sub | skipped by dependencies / sub "
              "| busy s / sub | of which jobs > 0.5 s |")
        print("|---|---|---|---|---|---|---|---|")
        everything = [0, 0, 0.0, 0.0, 0, 0, 0]
        for name in sorted(totals) + ["**all**"]:
            if name == "**all**":
                t = everything
            else:
                t = totals[name]
                everything = [a + b for a, b in zip(everything, t)]
            print("| %s | %d | %.1f | %.1f | %.1f | %.1f | %.2f | %.2f |" % (
                name, t[0], t[6] / t[0], t[1] / t[0], t[4] / t[0],
                t[5] / t[0], t[2] / t[0], t[3] / t[0]))


if __name__ == "__main__":
    main()
