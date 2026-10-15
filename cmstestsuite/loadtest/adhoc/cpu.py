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

"""Give the CPU of each CMS service over a run, and CWS CPU per request.

For each run: the CWS requests the driver sent (RWS polls excluded), the
CPU seconds of the CWS shards and that per request, of ES, of the Worker
processes (without the sandboxes, which run in isolate's own cgroups),
of SS, PS and LogService, from the first to the last monitor sample;
and the worst CWS RPC echo with the number of samples over 1 s.

Usage: python3 adhoc/cpu.py out/<run> [out/<run> ...]
Needs monitor.jsonl and requests.jsonl of each run.

"""

import json
import os
import sys

from workerlog import run_name


def group_cpu(first: dict, last: dict, prefix: str) -> float:
    """Return the CPU seconds the processes named prefix* used in between.

    first: the first monitor sample.
    last: the last monitor sample.
    prefix: the process name prefix, e.g. "ContestWebServer".

    """
    return sum(last["procs"][name]["cpu"]
               - first["procs"].get(name, {"cpu": 0})["cpu"]
               for name in last["procs"] if name.startswith(prefix))


def main() -> None:
    """Print one markdown table row per run given on the command line."""
    print("| run | CWS requests | CWS CPU s | CWS CPU ms / request | ES CPU s "
          "| Workers (python) CPU s | SS CPU s | PS CPU s | LogService CPU s "
          "| max CWS echo ms | CWS echo samples > 1 s |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for run in sys.argv[1:]:
        with open(os.path.join(run, "monitor.jsonl")) as f:
            samples = [json.loads(line) for line in f]
        first, last = samples[0], samples[-1]
        with open(os.path.join(run, "requests.jsonl")) as f:
            requests = sum(1 for line in f if '"shard": "rws"' not in line)
        echo = [v for s in samples for k, v in s["rpc_echo_ms"].items()
                if k.startswith("CWS") and v is not None]
        cws = group_cpu(first, last, "ContestWebServer")
        print("| %s | %d | %.0f | %.1f | %.0f | %.0f | %.0f | %.0f | %.0f "
              "| %.0f | %d |" % (
                  run_name(run), requests, cws, 1000 * cws / requests,
                  group_cpu(first, last, "EvaluationService"),
                  group_cpu(first, last, "Worker"),
                  group_cpu(first, last, "ScoringService"),
                  group_cpu(first, last, "ProxyService"),
                  group_cpu(first, last, "LogService"), max(echo),
                  sum(1 for e in echo if e > 1000)))


if __name__ == "__main__":
    main()
