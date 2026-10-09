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

"""Check the pass criteria of a fault run (run.sh --faults, #39).

Usage: faultstats.py out/<run>

It reads faults.json (written by faults.py), the CMS logs in cmslog/,
the driver's requests.jsonl, db_export.json and processes_end.txt,
prints one markdown row per criterion of the fault table of
docs/superpowers/specs/2026-10-08-db-transport-timeouts-design.md
("Validation runs"), and exits 1 if one fails, a fault that did not run
included. Stdlib only.

"""

import datetime
import glob
import json
import os
import re
import sys


# F2: each database call fails within this many seconds of its start.
CALL_LIMIT_S = 75
# After F2 or F3: pages answer within a minute, ES commits again within
# about 2 minutes (its sweeper runs every 117 s), and the scores arrive
# within about 6 minutes (the SS and PS sweepers run every 347 s), plus
# the time to do the work.
PAGES_LIMIT_S = 60
ES_LIMIT_S = 150
SCORES_LIMIT_S = 420
# F1: the logs name the lost connection within this many seconds.
F1_WINDOW_S = 120
# F3: a new connection fails at the connect deadline of 10 s. The probe
# of faults.py must fail with "timeout expired" within these bounds; a
# CWS 500 answer lasts at most F3_CONNECT_LIMIT_S; the slack is how long
# after the pause its effects may still be logged.
F3_PROBE_MIN_S = 9
F3_PROBE_MAX_S = 13
F3_CONNECT_LIMIT_S = 12
F3_SLACK_S = 15
# F4: cmsSetupDB exits within 15 s, and CWS p99 stays at most 6 s from
# its start to 5 s after it exits.
F4_EXIT_LIMIT_S = 15
F4_P99_LIMIT_S = 6
F4_AFTER_S = 5

DISCONNECT = re.compile(
    r"Lost the database connection"
    r"|server closed the connection unexpectedly"
    r"|terminating connection due to administrator command"
    r"|could not receive data from server|Connection timed out")
ES_LOST = "Lost the database connection while writing worker results"
# Tornado's request log: "] 500 GET /path (ip) 61000.00ms".
ACCESS = re.compile(r"\] (\d{3}) [A-Z]+ \S+ \(.*\) ([0-9.]+)ms$")
SS_START = re.compile(r"Executing operations? `(.+?)'")
SS_ERROR = re.compile(r"Unexpected error when executing operations? `(.+?)'")

Row = tuple[str, str, str, str, bool]


def log_time(line: str) -> float:
    """Return the time of a CMS log line, written in UTC.

    line: the log line.

    return: its time, in epoch seconds.

    raise (ValueError): if the line does not start with a time, as the
        lines of a traceback.

    """
    return datetime.datetime.strptime(
        line[:23], "%Y-%m-%d %H:%M:%S,%f").replace(
            tzinfo=datetime.timezone.utc).timestamp()


def read_logs(run_dir: str, service: str) -> list[tuple[float, str]]:
    """Return the timed records of a service's logs, in time order.

    The lines with no time of their own (the traceback of a record) are
    joined, with newlines, to the timed line before them: the error of a
    failed request is often only there.

    run_dir: the run directory.
    service: how the log directories' names start, as
        "EvaluationService"; "" for every service (the directory "cms",
        LogService's merged copy of every record, is skipped then, or
        each record would count twice).

    return: (time, record) pairs.

    """
    entries = []
    pattern = os.path.join(run_dir, "cmslog", service + "*", "[0-9]*.log")
    for path in glob.glob(pattern):
        if service == "" and os.path.basename(
                os.path.dirname(path)) == "cms":
            continue
        last = None
        with open(path, errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                try:
                    entries.append((log_time(line), line))
                    last = len(entries) - 1
                except ValueError:
                    if last is not None:
                        moment, record = entries[last]
                        entries[last] = (moment, record + "\n" + line)
    entries.sort(key=lambda entry: entry[0])
    return entries


def load_jsonl(path: str) -> list[dict]:
    """Return the objects of a JSON-lines file; none if it is missing."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _load_json(path: str, default):
    """Return the content of a JSON file, or default if it is missing."""
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def _read_text(path: str) -> str | None:
    """Return the content of a text file, or None if it is missing."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def pct(values: list[float], fraction: float) -> float | None:
    """Return a percentile as analyze.py computes it, or None."""
    values = sorted(values)
    return values[int((len(values) - 1) * fraction)] if values else None


def fmt(seconds: float | None) -> str:
    """Format a duration for the table."""
    return "-" if seconds is None else "%.1f s" % seconds


def first_after(times: list[float], moment: float) -> float | None:
    """Return how long after moment the first of times comes, or None."""
    later = [t for t in times if t >= moment]
    return min(later) - moment if later else None


def es_failed_flushes(es_logs: list[tuple[float, str]], start: float,
                      end: float) -> list[float]:
    """Return how long each ES flush that lost the connection took.

    A flush runs from "Starting commit process..." to its ERROR line;
    only the flushes that started between start and end count.

    """
    durations = []
    began = None
    for t, line in es_logs:
        if "Starting commit process" in line:
            began = t
        elif ES_LOST in line and began is not None:
            if start <= began <= end:
                durations.append(t - began)
            began = None
        elif line.endswith("] Done"):
            began = None
    return durations


def cws_500s(cws_logs: list[tuple[float, str]], start: float,
             end: float, after: float = 0.0) -> list[float]:
    """Return the request times of the CWS 500 answers to the requests
    that started between start and end and were logged at or after
    after.

    """
    times = []
    for t, line in cws_logs:
        match = ACCESS.search(line)
        if match is not None and match.group(1) == "500" and t >= after:
            took = float(match.group(2)) / 1000
            if start <= t - took <= end:
                times.append(took)
    return times


def ss_failed_operations(ss_logs: list[tuple[float, str]], start: float,
                         end: float) -> list[float]:
    """Return how long each SS operation that failed took; only those
    that started between start and end count.

    """
    began: dict[str, float] = {}
    durations = []
    for t, line in ss_logs:
        failed = SS_ERROR.search(line)
        if failed is not None:
            first = began.pop(failed.group(1), None)
            if first is not None and start <= first <= end:
                durations.append(t - first)
            continue
        started = SS_START.search(line)
        if started is not None:
            began[started.group(1)] = t
    return durations


def _longest(fault: str, what: str, durations: list[float],
             limit: float) -> Row:
    """Return the row of the longest of some durations."""
    worst = max(durations, default=None)
    return (fault, "longest " + what,
            "%s of %d" % (fmt(worst), len(durations)),
            "<= %g s" % limit, worst is None or worst <= limit)


def _within(fault: str, what: str, seconds: float | None,
            limit: float) -> Row:
    """Return the row of a delay that must exist and stay in a limit."""
    return (fault, what, fmt(seconds), "<= %g s" % limit,
            seconds is not None and seconds <= limit)


def probe_row(probe: dict | None, start: float, end: float) -> Row:
    """Return the row of F3's probe: a new connection during the pause.

    The connect must begin inside the pause (otherwise the run proves
    nothing), the pause must stall it, and the connect deadline must end
    it with "timeout expired" at about 10 s.

    probe: the probe's record in faults.json, or None if it has none.
    start, end: the pause, in epoch seconds.

    return: the row; its value says what went wrong, if anything.

    """
    what = "a new connection during the pause fails with 'timeout expired'"
    limit = "timeout expired at %g-%g s" % (F3_PROBE_MIN_S, F3_PROBE_MAX_S)
    if probe is None:
        return ("F3", what, "no probe was recorded", limit, False)
    outcome = probe.get("outcome")
    elapsed = probe.get("elapsed_s")
    error = probe.get("error") or ""
    began = probe.get("connect_started_at")
    if outcome == "hung":
        # It printed nothing, so it has no connect time either.
        value = "hung, no answer in time: the deadline did not fire"
    elif outcome not in ("ok", "error") or elapsed is None:
        value = "unusable probe record: %s" % json.dumps(probe)
    elif began is None:
        value = "probe missing connect time"
    elif not start <= began < end:
        value = ("inconclusive: the probe connected outside the pause "
                 "(%.1f s after its start)" % (began - start))
    elif outcome == "ok":
        value = ("connected after %s: the pause did not stall connects, "
                 "so the fault did not apply" % fmt(elapsed))
    elif "timeout expired" not in error:
        value = "failed after %s with another error: %s" % (
            fmt(elapsed), error)
    elif elapsed > F3_PROBE_MAX_S:
        value = ("timeout expired after %s: the deadline did not fire "
                 "at about 10 s" % fmt(elapsed))
    elif elapsed < F3_PROBE_MIN_S:
        value = ("timeout expired after %s: earlier than the 10 s "
                 "deadline" % fmt(elapsed))
    else:
        return ("F3", what, "timeout expired after %s" % fmt(elapsed),
                limit, True)
    return ("F3", what, value, limit, False)


def check(run_dir: str) -> list[Row]:
    """Return one row per criterion of a fault run.

    run_dir: the run directory (out/<run>).

    return: (fault, criterion, value, limit, passed) tuples.

    """
    rows: list[Row] = []
    by_fault: dict[str, list[dict]] = {}
    for record in _load_json(os.path.join(run_dir, "faults.json"), []):
        by_fault.setdefault(record["fault"], []).append(record)
    every = read_logs(run_dir, "")
    es = read_logs(run_dir, "EvaluationService")
    cws = read_logs(run_dir, "ContestWebServer")
    ss = read_logs(run_dir, "ScoringService")
    requests = [request for request
                in load_jsonl(os.path.join(run_dir, "requests.jsonl"))
                if request["shard"] != "rws"]
    # When the answers arrived: the request time plus its duration.
    pages = [request["t"] + request["dur"] for request in requests
             if 200 <= request["status"] < 400]
    es_done = [t for t, line in es if line.endswith("] Done")]
    export = _load_json(os.path.join(run_dir, "db_export.json"), None)
    submissions = export["submissions"] if export is not None else []

    f1 = by_fault.get("F1", [])
    rows.append(("F1", "injections", str(len(f1)), "2", len(f1) == 2))
    for index, record in enumerate(f1, 1):
        start = record["start"]
        # A window ends at the next injection, whose effects are its own.
        end = start + F1_WINDOW_S
        if index < len(f1):
            end = min(end, f1[index]["start"])
        lines = sum(1 for t, line in every
                    if start <= t < end and DISCONNECT.search(line))
        rows.append(("F1", "backends ended (#%d)" % index,
                     str(record["terminated"]), "> 0",
                     record["terminated"] > 0))
        rows.append(("F1", "lost-connection log lines within %d s (#%d)"
                     % (F1_WINDOW_S, index), str(lines), "> 0", lines > 0))

    f2 = by_fault.get("F2", [{}])[0]
    if "partition_step" not in f2:
        rows.append(("F2", "ran", "no", "yes", False))
    else:
        window = (f2["start"] - CALL_LIMIT_S, f2["end"])
        answers = cws_500s(cws, *window)
        # Answered after the drop began; one logged before it is not it.
        during = cws_500s(cws, *window, after=f2["start"])
        rows.append(_longest("F2", "ES flush that lost the connection",
                             es_failed_flushes(es, *window), CALL_LIMIT_S))
        rows.append(_longest("F2", "CWS 500 answer", answers, CALL_LIMIT_S))
        rows.append(_longest("F2", "SS operation that failed",
                             ss_failed_operations(ss, *window),
                             CALL_LIMIT_S))
        rows.append(("F2", "CWS 500 answers during the drop",
                     str(len(during)), "> 0", bool(during)))
        rows.append(("F2", "backends ended by the partition step",
                     str(f2["partition_step_terminated"]), "recorded",
                     True))
        recovered = max(f2["end"], f2["partition_step"])
        rows.append(_within("F2", "pages answer again after",
                            first_after(pages, recovered), PAGES_LIMIT_S))
        rows.append(_within("F2", "ES commits again after",
                            first_after(es_done, recovered), ES_LIMIT_S))
        backlog = [submission for submission in submissions
                   if submission["timestamp"] <= recovered
                   and (submission["scored_at"] is None
                        or submission["scored_at"] >= f2["start"])]
        if any(submission["scored_at"] is None for submission in backlog):
            scored_after = None
        else:
            scored_after = max([submission["scored_at"] - recovered
                                for submission in backlog] + [0.0])
        rows.append(_within("F2", "earlier submissions scored after",
                            scored_after, SCORES_LIMIT_S))

    f3 = by_fault.get("F3", [{}])[0]
    if "end" not in f3:
        rows.append(("F3", "ran", "no", "yes", False))
    else:
        rows.append(probe_row(f3.get("probe"), f3["start"], f3["end"]))
        # Shown for the reader, with no effect on the result: the probe
        # decides. Requests may be served from pooled connections, which
        # wait for the resume, and a connect may not have been needed.
        expired = sum(1 for t, line in every
                      if f3["start"] <= t <= f3["end"] + F3_SLACK_S
                      and "timeout expired" in line)
        rows.append(("F3", "'timeout expired' log lines", str(expired),
                     "informational", True))
        answers = cws_500s(cws, f3["start"], f3["end"])
        if answers:
            rows.append(_longest("F3", "CWS 500 answer", answers,
                                 F3_CONNECT_LIMIT_S))
        else:
            rows.append(("F3", "longest CWS 500 answer", "no 500 answers",
                         "<= %g s" % F3_CONNECT_LIMIT_S, True))
        lasted = [request["dur"] for request in requests
                  if f3["start"] <= request["t"] <= f3["end"]]
        rows.append(("F3", "longest non-RWS request that started during "
                     "the pause",
                     "%s of %d" % (fmt(max(lasted)), len(lasted)) if lasted
                     else "no request started during the pause",
                     "informational", True))
        rows.append(_within("F3", "pages answer again after",
                            first_after(pages, f3["end"]), PAGES_LIMIT_S))

    f4 = by_fault.get("F4", [{}])[0]
    if "exit_status" not in f4:
        rows.append(("F4", "ran", "no", "yes", False))
    else:
        ready = bool(f4.get("holder_ready"))
        rows.append(("F4", "holder idle in its transaction before "
                     "cmsSetupDB",
                     "yes" if ready else
                     "no (the holder was not idle in its transaction "
                     "after 30 s, so the lock may not have been held)",
                     "yes", ready))
        rows.append(("F4", "cmsSetupDB exit status",
                     str(f4["exit_status"]), "1", f4["exit_status"] == 1))
        rows.append(_within("F4", "cmsSetupDB duration", f4["duration"],
                            F4_EXIT_LIMIT_S))
        rows.append(("F4", "lock message printed",
                     "yes" if f4["message"] else "no", "yes",
                     bool(f4["message"])))
        p99 = pct([request["dur"] for request in requests
                   if f4["start"] <= request["t"]
                   <= f4["end"] + F4_AFTER_S], 0.99)
        rows.append(_within(
            "F4", "CWS p99 from its start to %d s after its exit"
            % F4_AFTER_S, p99, F4_P99_LIMIT_S))

    census = _read_text(os.path.join(run_dir, "processes_end.txt"))
    alive = census is not None and "SHORT" not in census
    rows.append(("all", "CMS processes still running at the end",
                 "yes" if alive else "no", "yes", alive))
    if export is None:
        rows.append(("all", "submissions never scored",
                     "no db_export.json", "0", False))
    else:
        stuck = sum(1 for submission in submissions
                    if submission["scored_at"] is None)
        rows.append(("all", "submissions never scored", str(stuck), "0",
                     stuck == 0))
    return rows


def main(argv: list[str]) -> int:
    """Print the table of a run; return 1 if a criterion failed."""
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    rows = check(argv[1])
    print("| fault | criterion | value | limit | result |")
    print("|---|---|---|---|---|")
    for fault, criterion, value, limit, passed in rows:
        print("| %s | %s | %s | %s | %s |" % (
            fault, criterion, value, limit, "pass" if passed else "FAIL"))
    return 0 if all(row[4] for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
