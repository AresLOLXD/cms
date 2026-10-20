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

"""Check the pass criteria of a watchdog run (run.sh --watchdog, #38).

Usage: watchdogstats.py out/<run> --cws N --workers N

It reads watchdog_status.jsonl (one status per cycle, written by
cmsWatchdog --status-history), watchdog.log (its stdout), the driver's
requests.jsonl and, after a fault run, faults.json. It prints one
markdown row per criterion of "Harness L" in
docs/superpowers/specs/2026-10-10-watchdog-design.md, then the probe
latencies, and exits 1 if a criterion fails. A fault run (one with
faults.json) is judged by the criteria of L3: its latency and near-miss
rows, the criteria of L1 and L2, are only recorded. Stdlib only: the
constants below copy those of cms/watchdog/policy.py, and a test keeps
them equal.

"""

import argparse
import calendar
import json
import math
import os
import re
import sys
import time


HTTP_TIMEOUT_S = 5.0
RPC_TIMEOUT_S = 3.0
# Half of each trip: the near-miss gates.
HALF_MISSES = 2
HALF_SECTION_S = 90.0
HALF_ES_IDLE_S = 30.0
INVALIDATION = "post_finish_lock:_invalidate_submission_sync"
# The probes every program needs latency samples of: favicon for the
# kinds judged on it, health() for every kind but the Worker.
FAVICON_KINDS = frozenset({"ContestWebServer", "AdminWebServer"})
HEALTH_KINDS = FAVICON_KINDS | {"LogService", "ResourceService",
                                "EvaluationService", "ScoringService",
                                "ProxyService"}
# Three cycles of policy.CYCLE_S (15 s): the longest the status history
# may go without a cycle. Past it the watchdog stopped or stalled (its
# self-guard exits after 60 s, and nothing restarts it in the harness).
MAX_CYCLE_GAP_S = 45.0
# A fault shows in the gate from up to one gate probe (7 s) before it
# to one cycle and one probe (22 s) after it.
FAULT_BEFORE_S = 7.0
FAULT_AFTER_S = 22.0
WOULD = re.compile(r"\bWOULD (restart|start)\b")
HELD = re.compile(r"\bheld by H\d\b")
LINE_TIME = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC ")

Row = tuple[str, str, str, bool]


def load_jsonl(path: str) -> list[dict]:
    """Read a JSON-lines file; [] if it is missing."""
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


def p_quantile(values: list[float], q: float) -> float | None:
    """Return the q quantile of values (nearest rank), or None."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def line_time(line: str) -> float | None:
    """Return the epoch time of a watchdog.log line, or None."""
    match = LINE_TIME.match(line)
    if match is None:
        return None
    return float(calendar.timegm(time.strptime(match.group(1),
                                               "%Y-%m-%d %H:%M:%S")))


def fault_windows(run_dir: str) -> list[tuple[float, float]]:
    """Return the windows of F2 and F3, widened by the gate's slack."""
    try:
        with open(os.path.join(run_dir, "faults.json")) as f:
            records = json.load(f)
    except FileNotFoundError:
        return []
    return [(record["start"] - FAULT_BEFORE_S,
             record["end"] + FAULT_AFTER_S)
            for record in records
            if record.get("fault") in ("F2", "F3") and "end" in record]


def inside(moment: float, windows: list[tuple[float, float]]) -> bool:
    return any(start <= moment <= end for start, end in windows)


def expected_labels(cws: int, workers: int) -> list[str]:
    """Return the labels of the programs every cycle must show."""
    return (["cmsLogService 0", "cmsEvaluationService 0",
             "cmsScoringService 0", "cmsProxyService 0",
             "cmsAdminWebServer 0"]
            + ["cmsContestWebServer %d" % i for i in range(cws)]
            + ["cmsWorker %d" % i for i in range(workers)])


def check(run_dir: str, cws: int, workers: int) -> tuple[list[Row],
                                                         list[str]]:
    """Return the rows of the criteria, and the latency lines."""
    statuses = load_jsonl(os.path.join(run_dir, "watchdog_status.jsonl"))
    try:
        with open(os.path.join(run_dir, "watchdog.log")) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    requests = load_jsonl(os.path.join(run_dir, "requests.jsonl"))
    start = min((request["t"] for request in requests), default=None)
    windows = fault_windows(run_dir)
    fault_run = os.path.exists(os.path.join(run_dir, "faults.json"))
    rows: list[Row] = []

    woulds = [line for line in lines if WOULD.search(line)]
    rows.append(("no WOULD line", str(len(woulds)), "0", not woulds))

    held = [line for line in lines if HELD.search(line)]
    outside = [line for line in held
               if not inside(line_time(line) or 0.0, windows)]
    rows.append(("holds only inside F2 and F3", "%d of %d"
                 % (len(outside), len(held)), "0 outside", not outside))

    red = [item["time"] for item in statuses if item.get("gate") != "green"]
    red_outside = [moment for moment in red if not inside(moment, windows)]
    rows.append(("gate red only inside F2 and F3", "%d of %d cycles"
                 % (len(red_outside), len(red)), "0 outside",
                 not red_outside))

    problems = []
    seen: set[str] = set()
    cycles = [item for item in statuses
              if start is not None and item["time"] >= start]
    for item in cycles:
        running = {program["label"] for program in item["programs"]
                   if program.get("state") == "RUNNING"}
        for label in expected_labels(cws, workers):
            if label in running:
                seen.add(label)
            elif label in seen or label != "cmsProxyService 0":
                problems.append("%s missing at %d" % (label, item["time"]))
    never = [label for label in expected_labels(cws, workers)
             if label not in seen]
    rows.append(("every program in every cycle",
                 "; ".join((["never: " + ", ".join(never)] if never else [])
                           + problems[:3]) or "all",
                 "all, from the driver's start",
                 bool(cycles) and not never and not problems))

    # The rows above judge only the cycles there are: the cycles must
    # also cover the run, and every F2 and F3 window.
    moments = sorted(item["time"] for item in statuses)
    end = max((request["t"] for request in requests), default=None)
    covered = moments[-1] if moments and end is not None else None
    rows.append(("last cycle at most %g s before the driver's last request"
                 % MAX_CYCLE_GAP_S,
                 "none" if covered is None else "%.0f s" % (end - covered),
                 "%g s" % MAX_CYCLE_GAP_S,
                 covered is not None and end - covered <= MAX_CYCLE_GAP_S))
    if start is not None:
        # From the driver's start on: the last cycle before it (the
        # start itself if there is none) and every cycle after it.
        moments = ([moment for moment in moments if moment < start][-1:]
                   or [start]) + [moment for moment in moments
                                  if moment >= start]
    gaps = ["%.0f s after %d" % (later - earlier, earlier)
            for earlier, later in zip(moments, moments[1:])
            if later - earlier > MAX_CYCLE_GAP_S]
    rows.append(("no gap over %g s between cycles" % MAX_CYCLE_GAP_S,
                 ", ".join(gaps[:3]) or "none", "none", not gaps))
    empty = ["%d-%d" % window for window in windows
             if not any(inside(item["time"], [window]) for item in statuses)]
    rows.append(("a cycle inside every F2 and F3 window",
                 ", ".join(empty) or "all", "all", not empty))

    http: dict[str, list[float]] = {}
    rpc: dict[str, list[float]] = {}
    max_misses = 0
    es_bad: list[str] = []
    es_idle = 0.0
    ss_bad: list[str] = []
    ps_bad: list[str] = []
    for item in statuses:
        invalidating = any(
            section.get("what") == INVALIDATION
            for program in item["programs"]
            if program.get("kind") == "EvaluationService"
            for section in program.get("sections") or [])
        for program in item["programs"]:
            label = program.get("label") or program["name"]
            for key, table in (("http", http), ("rpc", rpc)):
                if program.get(key + "_latency_s") is not None:
                    table.setdefault(label, []).append(
                        program[key + "_latency_s"])
            max_misses = max(max_misses, program.get("misses") or 0)
            for section in program.get("sections") or []:
                what = section.get("what")
                age_s = section.get("age_s") or 0.0
                idle_s = section.get("idle_s") or 0.0
                kind = program.get("kind")
                if kind == "EvaluationService" and what != INVALIDATION:
                    es_idle = max(es_idle, idle_s)
                    if age_s > HALF_SECTION_S and idle_s >= HALF_ES_IDLE_S:
                        es_bad.append("%s %d s" % (what, age_s))
                if kind == "ScoringService" and not invalidating \
                        and age_s > HALF_SECTION_S:
                    ss_bad.append("%d s" % age_s)
                if kind == "ProxyService" and idle_s > HALF_SECTION_S:
                    ps_bad.append("%d s" % idle_s)

    # Expected from each program's kind ("cms<Kind> <shard>"), not from
    # the probes the status history shows.
    missing = ["%s %s" % (label, name)
               for label in expected_labels(cws, workers)
               for name, table, kinds in (("favicon", http, FAVICON_KINDS),
                                          ("health()", rpc, HEALTH_KINDS))
               if label.split()[0][3:] in kinds and not table.get(label)]
    rows.append(("probe latencies for every probed program",
                 ", ".join(missing) or "all", "all", not missing))
    # The criteria of L1 and L2. A fault run (L3) is judged by the rows
    # above: F2 and F3 push these past half their trips by design, so
    # there they are only recorded.
    load_rows: list[Row] = []
    for name, table, timeout in (("favicon", http, HTTP_TIMEOUT_S),
                                 ("health()", rpc, RPC_TIMEOUT_S)):
        worst = max((p_quantile(values, 0.99) or 0.0
                     for values in table.values()), default=None)
        load_rows.append(("%s p99 under 1/3 of %g s" % (name, timeout),
                          "no samples" if worst is None
                          else "%.2f s" % worst, "%.2f s" % (timeout / 3),
                          worst is not None and worst < timeout / 3))
    load_rows.append(("at most 2 misses in 6", str(max_misses),
                      str(HALF_MISSES), max_misses <= HALF_MISSES))
    load_rows.append(("ES sections under half their trip",
                      ", ".join(es_bad[:3]) or "none", "none", not es_bad))
    load_rows.append(("SS executions under 90 s", ", ".join(ss_bad[:3])
                      or "none", "none", not ss_bad))
    load_rows.append(("PS sends idle under 90 s", ", ".join(ps_bad[:3])
                      or "none", "none", not ps_bad))
    if fault_run:
        load_rows = [(criterion, value, "(recorded: a fault run)", True)
                     for criterion, value, _, _ in load_rows]
    rows.extend(load_rows)
    rows.append(("longest ES idle_s in a section (margin)",
                 "%.1f s" % es_idle, "(recorded)", True))

    latency = ["| program | probe | p50 | p99 |", "|---|---|---|---|"]
    for name, table in (("favicon", http), ("health()", rpc)):
        for label in sorted(table):
            latency.append("| %s | %s | %.3f s | %.3f s |" % (
                label, name, p_quantile(table[label], 0.5),
                p_quantile(table[label], 0.99)))
    return rows, latency


def main(argv: list[str]) -> int:
    """Print the table of a run; return 1 if a criterion failed."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir")
    parser.add_argument("--cws", type=int, required=True)
    parser.add_argument("--workers", type=int, required=True)
    args = parser.parse_args(argv[1:])
    rows, latency = check(args.run_dir, args.cws, args.workers)
    print("| criterion | value | limit | result |")
    print("|---|---|---|---|")
    for criterion, value, limit, passed in rows:
        print("| %s | %s | %s | %s |" % (criterion, value, limit,
                                         "pass" if passed else "FAIL"))
    print()
    print("\n".join(latency))
    return 0 if all(row[3] for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
