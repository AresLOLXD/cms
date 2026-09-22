#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""Two-phase (fail-fast screening) evaluation gating.

Ported from COMIGuide's juez/pristine/twophase.py. First, only a group's
screening testcases (the sample case plus a case designed to catch a wrong
answer bug and one designed to catch a too-slow solution) are evaluated.
The rest of that group's testcases are only evaluated once its screening
has passed; if screening fails, the remaining testcases are synthesized as
skipped (outcome 0) instead of actually graded. This is per group
(subtask), so other groups keep their own partial scoring unaffected.

Groups and screening testcases are identified purely by a testcase codename
convention:

    <group>-<nn>-<tag>     e.g. s1-00-sample, s1-01-scr-wa, s1-02-scr-tle, s1-03-...

- screening = the tag is "sample" or contains "scr".
- group = the part before the first "-". A codename without "-" (old
  format, e.g. "000") falls into the single global group "".

Pass rule: pass = outcome > 0 (WA and TLE give "0.0").

"""

import re

from cms import config


_GROUP_RE = re.compile(r"^([^-]*)-")


def enabled() -> bool:
    """Return whether two-phase evaluation is enabled in the config."""
    return config.global_.two_phase_evaluation


def group_of(codename: str) -> str:
    """Return the group (subtask) a testcase belongs to, from its codename."""
    m = _GROUP_RE.match(codename)
    return m.group(1) if m else ""


def _tag_of(codename: str) -> str:
    """Return the tag part of a testcase codename (see module docstring).

    codename: a testcase codename, e.g. "s1-01-scr-wa".

    return: the tag, e.g. "scr-wa", or "" if the codename does not follow
        the "<group>-<nn>-<tag>" convention.

    """
    parts = codename.split("-", 2)
    return parts[2] if len(parts) == 3 else ""


def is_screening(codename: str) -> bool:
    """Return whether a testcase is a screening testcase."""
    tag = _tag_of(codename)
    return tag == "sample" or "scr" in tag


def _passed(outcome: str | None) -> bool:
    # Pass = outcome strictly positive. WA and TLE give "0.0".
    try:
        return float(outcome) > 0.0
    except (TypeError, ValueError):
        return False


def group_screening_status(
    dataset, outcome_by_codename: dict[str, str | None]
) -> dict[str, str]:
    """Return each group's screening status given evaluations written so far.

    dataset: the active dataset (needs a .testcases {codename: Testcase}
        mapping).
    outcome_by_codename: outcome of each testcase ALREADY evaluated.

    return: group -> "pending" | "passed" | "failed". A group with no
        screening testcases is absent (the caller treats that as "passed":
        nothing gates it).

    """
    screening: dict[str, list[str]] = {}
    for codename in dataset.testcases.keys():
        if is_screening(codename):
            screening.setdefault(group_of(codename), []).append(codename)

    status = {}
    for group, codenames in screening.items():
        done = [c for c in codenames if c in outcome_by_codename]
        if len(done) < len(codenames):
            status[group] = "pending"
        elif all(_passed(outcome_by_codename[c]) for c in done):
            status[group] = "passed"
        else:
            status[group] = "failed"
    return status
