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

"""Compare the metrics.json of several load-test runs side by side.

Stdlib only. Each run directory (out/<run>) must hold the metrics.json
written by analyze.py. The output is a markdown table with one column per
run, or with --median one column per target (the median of its runs).
The per-container CPU and memory dicts are not compared: their keys are
container names, which differ between targets.
"""

import argparse
import json
import math
import os
import statistics
import sys

# The metrics.json keys of analyze.METRIC_KEYS, in the same order, without
# the labels of the run and the per-container dicts.
DEFAULT_KEYS = (
    "users", "submissions_sent", "submissions_rejected", "login_failures",
    "http_errors", "score_mismatches", "rws_pairs", "rws_mismatches",
    "login_p50", "login_p95", "submit_p50", "submit_p95", "submit_end_p95",
    "scored_p50", "scored_p95", "scored_max", "drain_after_stop_s",
    "peak_pg_connections")


def load(run_dirs: list[str]) -> list[dict]:
    """Read the metrics.json of each run directory.

    run_dirs: the run directories (out/<run>), in the order to compare.

    return: one metrics dict per run directory, in the same order.

    raise (FileNotFoundError): if a directory has no metrics.json (analyze.py
        has not been run on it).

    """
    runs = []
    for run_dir in run_dirs:
        path = os.path.join(run_dir, "metrics.json")
        try:
            with open(path) as f:
                runs.append(json.load(f))
        except FileNotFoundError:
            raise FileNotFoundError(
                "%s not found: run analyze.py on that run directory first"
                % path)
    return runs


def _is_number(value: object) -> bool:
    """Tell whether value is a real number that can be compared."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and not math.isnan(value))


def _cell(value: object) -> str:
    """Format one metric value for the table ("-" if there is none)."""
    if value is None:
        return "-"
    if isinstance(value, float):
        if math.isnan(value):
            return "-"
        text = "%.3g" % value
        # Keep 1234.5 as 1234 rather than 1.23e+03.
        return "%.0f" % value if "e+" in text else text
    return str(value)


def table(runs: list[dict], keys: list[str]) -> str:
    """Render the runs as a markdown table, one column per run.

    runs: the metrics dicts; the "run" key labels the column.
    keys: the metrics to show, one row each. A run without a key (a
        crashed run) shows "-".

    return: the markdown table.

    """
    rows = [["metric"] + [str(run.get("run", "?")) for run in runs]]
    rows += [[key] + [_cell(run.get(key)) for run in runs] for key in keys]
    lines = ["| " + " | ".join(row) + " |" for row in rows]
    lines.insert(1, "|" + "---|" * len(rows[0]))
    return "\n".join(lines)


def summarize(runs: list[dict], group_by: str = "target") -> list[dict]:
    """Take the median of every numeric metric over the runs of each group.

    runs: the metrics dicts.
    group_by: the key whose value groups the runs.

    return: one dict per group, in order of first appearance, with the
        group value under group_by, the number of runs under "runs" and
        the median of each numeric metric. A run without a value for a
        metric (a crashed run) counts in "runs" but not in that median;
        a metric with no value in a group is None.

    """
    groups: dict[object, list[dict]] = {}
    for run in runs:
        groups.setdefault(run.get(group_by), []).append(run)
    numeric_keys = list(dict.fromkeys(
        key for run in runs for key, value in run.items()
        if key != group_by and _is_number(value)))
    rows = []
    for value, members in groups.items():
        row = {group_by: value, "runs": len(members)}
        for key in numeric_keys:
            values = [m[key] for m in members if _is_number(m.get(key))]
            row[key] = statistics.median(values) if values else None
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> None:
    """Print the comparison of the runs given on the command line.

    argv: the command line arguments, without the program name (default:
        sys.argv).

    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run_dirs", nargs="+", metavar="run_dir",
                        help="a run directory (out/<run>) with metrics.json")
    parser.add_argument("--median", action="store_true",
                        help="one column per target, with the median of "
                        "its runs, instead of one column per run")
    cli = parser.parse_args(argv)
    try:
        runs = load(cli.run_dirs)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if cli.median:
        rows = summarize(runs)
        print(table([dict(row, run=row["target"]) for row in rows],
                    ["runs"] + list(DEFAULT_KEYS)))
    else:
        print(table(runs, list(DEFAULT_KEYS)))


if __name__ == "__main__":
    main(sys.argv[1:])
