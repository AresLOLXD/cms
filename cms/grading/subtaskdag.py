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

"""Dependencies between the subtasks of a group score type.

A subtask of a group score type (see cms.grading.scoretypes.ScoreTypeGroup)
may declare, in the dict form of its parameters, "depends_on": the numbers
of the subtasks it depends on, counted from 0 as CMS shows them. A subtask
whose dependency did not pass is worth 0 (see ScoreTypeGroup.compute_score)
and is not graded (see SubtaskGate). A dependency passes when its score
fraction is above 0; all dependencies must pass; the rule is transitive.

Datasets that declare no dependency never get past
declares_dependencies(), so they are graded exactly as before.

"""

import logging

logger = logging.getLogger(__name__)

DEPENDS_ON = "depends_on"


def declares_dependencies(parameters: object) -> bool:
    """Tell whether score type parameters declare any dependency.

    A cheap check on the raw parameters: it builds and validates nothing,
    so that datasets without dependencies never pay for, nor are affected
    by, the rest of this module.

    parameters: the score type parameters of a dataset.

    return: whether some subtask has a non-empty "depends_on".

    """
    return isinstance(parameters, list) and any(
        isinstance(parameter, dict) and bool(parameter.get(DEPENDS_ON))
        for parameter in parameters)


def parse_dependencies(parameters: list) -> list[list[int]]:
    """Read and validate the dependencies of every subtask.

    parameters: the score type parameters, one element per subtask; a
        list element, or a dict without "depends_on", has none.

    return: for each subtask, the sorted numbers of the subtasks it
        depends on.

    raise (ValueError): if a "depends_on" is not a list of integers, a
        number is out of range, a subtask depends on itself, a number is
        repeated, or the dependencies form a cycle.

    """
    count = len(parameters)
    dependencies: list[list[int]] = []
    for index, parameter in enumerate(parameters):
        if not isinstance(parameter, dict) or DEPENDS_ON not in parameter:
            dependencies.append([])
            continue
        raw = parameter[DEPENDS_ON]
        if not isinstance(raw, list) or any(
                isinstance(number, bool) or not isinstance(number, int)
                for number in raw):
            raise ValueError(
                "Subtask %d: depends_on must be a list of subtask numbers."
                % index)
        for number in raw:
            if not 0 <= number < count:
                raise ValueError(
                    "Subtask %d: depends on subtask %d, which does not exist "
                    "(subtasks are numbered from 0 to %d)."
                    % (index, number, count - 1))
            if number == index:
                raise ValueError(
                    "Subtask %d: a subtask cannot depend on itself." % index)
        if len(set(raw)) != len(raw):
            raise ValueError(
                "Subtask %d: depends_on repeats a subtask." % index)
        dependencies.append(sorted(raw))
    topological_order(dependencies)
    return dependencies


def topological_order(dependencies: list[list[int]]) -> list[int]:
    """Order the subtasks so that each one comes after its dependencies.

    Among the subtasks that are ready, the lowest number goes first, so
    the order is deterministic.

    dependencies: for each subtask, the subtasks it depends on.

    return: every subtask number, each after all of its dependencies.

    raise (ValueError): if the dependencies form a cycle.

    """
    remaining = [len(deps) for deps in dependencies]
    dependents: list[list[int]] = [[] for _ in dependencies]
    for index, deps in enumerate(dependencies):
        for number in deps:
            dependents[number].append(index)
    ready = [index for index, count in enumerate(remaining) if count == 0]
    order: list[int] = []
    while ready:
        ready.sort()
        current = ready.pop(0)
        order.append(current)
        for dependent in dependents[current]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                ready.append(dependent)
    if len(order) != len(dependencies):
        in_cycle = [str(index) for index, count in enumerate(remaining)
                    if count > 0]
        raise ValueError(
            "The subtask dependencies form a cycle among subtasks %s."
            % ", ".join(in_cycle))
    return order


def zeroed_by(
    fractions: list[float], dependencies: list[list[int]]
) -> list[int | None]:
    """Find, for each subtask, the dependency that makes it worth 0.

    A dependency fails when its score fraction is not above 0, counting a
    subtask that is itself worth 0 by this rule (transitivity).

    fractions: each subtask's own score fraction, before this rule.
    dependencies: for each subtask, the subtasks it depends on.

    return: for each subtask, the lowest-numbered dependency that failed,
        or None if none did.

    """
    effective = list(fractions)
    result: list[int | None] = [None] * len(fractions)
    for index in topological_order(dependencies):
        failed = [number for number in dependencies[index]
                  if effective[number] <= 0.0]
        if failed:
            result[index] = min(failed)
            effective[index] = 0.0
    return result
