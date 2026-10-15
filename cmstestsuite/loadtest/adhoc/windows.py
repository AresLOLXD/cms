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

"""Show a run's saturation signals window by window around the stop.

Per window: the host's busy logical CPUs (hostsample.log), the CPU of
the CWS shards (all, and the busiest one), the Workers, ES, SS, PS and
LogService processes, the worst RPC echo of CWS and ES, the ES queue,
the busy Workers, PostgreSQL's active backends and the longest
idle-in-transaction. All but the first come from monitor.jsonl.

The monitor takes a sample's time before its RPC echo calls, so inside
a stall of several seconds the per-process CPU rates are unreliable;
use docker_stats.log there.

Usage: python3 adhoc/windows.py [--host-log FILE] out/<run> [window_s
[from_s to_s]]
window_s defaults to 60; from_s and to_s are relative to the contest
stop and default to the whole contest and 1200 s after the stop.

"""

import argparse
import bisect
import json
import os

PROCESS_GROUPS = {"ES": "EvaluationService", "SS": "ScoringService",
                  "PS": "ProxyService", "Log": "LogService"}


def read_host_log(path: str) -> list[tuple[float, float]]:
    """Return (Unix time, busy logical CPUs) of every host sample.

    path: a hostsample.log; a missing file gives no samples.

    """
    if not os.path.exists(path):
        return []
    samples = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            fields = dict(p.split("=", 1) for p in parts[1:])
            if "busy_cores" in fields:
                samples.append((float(parts[0]), float(fields["busy_cores"])))
    return samples


def cpu_rate(first: dict, last: dict, prefix: str) -> tuple[float, float]:
    """Return the total and the largest CPU rate of a group of processes.

    first: the first monitor sample of the window.
    last: the last monitor sample of the window.
    prefix: the process name prefix, e.g. "ContestWebServer".

    return: (sum over the processes, largest one), in cores.

    """
    elapsed = last["t"] - first["t"]
    rates = [(last["procs"][name]["cpu"] - first["procs"][name]["cpu"])
             / elapsed
             for name in last["procs"]
             if name.startswith(prefix) and name in first["procs"]]
    return sum(rates), max(rates, default=0)


def main() -> None:
    """Print one markdown table row per window."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host-log", help="hostsample.log to read")
    parser.add_argument("run")
    parser.add_argument("window", nargs="?", type=float, default=60)
    parser.add_argument("bounds", nargs="*", type=float,
                        help="from and to, seconds relative to the stop")
    args = parser.parse_args()
    with open(os.path.join(args.run, "users.json")) as f:
        users = json.load(f)
    stop = users["stop"]
    low = stop + args.bounds[0] if args.bounds else users["start"]
    high = stop + args.bounds[1] if len(args.bounds) > 1 else stop + 1200
    with open(os.path.join(args.run, "monitor.jsonl")) as f:
        samples = [json.loads(line) for line in f]
    host = read_host_log(args.host_log or os.path.join(
        os.path.dirname(os.path.abspath(args.run.rstrip("/"))),
        "hostsample.log"))
    host_times = [h[0] for h in host]
    print("| t-stop (s) | host busy cores | CWS cores (max one) "
          "| Workers cores | ES | SS | PS | Log | max CWS echo ms "
          "| max ES echo ms | queue ops max | busy W mean | PG active max "
          "| idle-in-tx max s |")
    print("|" + "---|" * 14)
    t = low
    while t < high:
        window = [s for s in samples if t <= s["t"] < t + args.window]
        if len(window) >= 2:
            first, last = window[0], window[-1]
            cws, cws_max = cpu_rate(first, last, "ContestWebServer")
            workers, _ = cpu_rate(first, last, "Worker")
            other = {key: cpu_rate(first, last, prefix)[0]
                     for key, prefix in PROCESS_GROUPS.items()}
            i = bisect.bisect_left(host_times, t)
            j = bisect.bisect_left(host_times, t + args.window)
            host_busy = (sum(h[1] for h in host[i:j]) / (j - i)
                         if j > i else float("nan"))
            cws_echo = max((v for s in window
                            for k, v in s["rpc_echo_ms"].items()
                            if k.startswith("CWS") and v is not None),
                           default=0)
            es_echo = max((s["rpc_echo_ms"].get("ES") or 0 for s in window),
                          default=0)
            print("| %+.0f | %.1f | %.2f (%.2f) | %.2f | %.2f | %.2f | %.2f "
                  "| %.2f | %.0f | %.0f | %d | %.1f | %d | %.1f |" % (
                      t - stop, host_busy, cws, cws_max, workers,
                      other["ES"], other["SS"], other["PS"], other["Log"],
                      cws_echo, es_echo,
                      max(s.get("queue_ops") or 0 for s in window),
                      sum(s.get("workers_busy") or 0 for s in window)
                      / len(window),
                      max((s.get("pg_states") or {}).get("active", 0)
                          for s in window),
                      max(s.get("pg_max_idle_in_tx_s") or 0
                          for s in window)))
        t += args.window


if __name__ == "__main__":
    main()
