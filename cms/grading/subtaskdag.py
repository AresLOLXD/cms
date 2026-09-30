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

import heapq
import logging

logger = logging.getLogger(__name__)

DEPENDS_ON = "depends_on"


def declares_dependencies(parameters: object) -> bool:
    """Tell whether score type parameters declare any dependency.

    A cheap check on the raw parameters: it builds and validates nothing,
    so that datasets without dependencies never pay for, nor are affected
    by, the rest of this module. A falsy malformed value (such as null)
    is not "declared" here, but parse_dependencies, which every group
    score type runs when built, still rejects it.

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
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        current = heapq.heappop(ready)
        order.append(current)
        for dependent in dependents[current]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                heapq.heappush(ready, dependent)
    if len(order) != len(dependencies):
        in_cycle = [str(index) for index, count in enumerate(remaining)
                    if count > 0]
        raise ValueError(
            "The subtask dependencies form a cycle; subtasks in it or "
            "depending on it: %s." % ", ".join(in_cycle))
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


def _outcome(value: str | None) -> float:
    """Read an evaluation outcome; a missing or bad one counts as 0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class SubtaskGate:
    """What a submission may run next, and what it may skip, on a dataset.

    Built from a group score type whose parameters declare dependencies.
    A subtask is "failed" as soon as the testcases evaluated so far make
    its score 0 (this relies on reduce() never going back up as more
    outcomes arrive, true for min, product and threshold), or when one of
    its dependencies failed; "passed" when all its testcases are evaluated,
    its score is above 0 and all its dependencies passed; "pending"
    otherwise.

    """

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"

    def __init__(self, score_type) -> None:
        """Initializer.

        score_type: a ScoreTypeGroup (needs dependencies, parameters,
            reduce() and retrieve_target_testcases()).

        """
        self._score_type = score_type
        self.targets: list[list[str]] = \
            score_type.retrieve_target_testcases()
        self.dependencies: list[list[int]] = score_type.dependencies
        self._order = topological_order(self.dependencies)
        self._subtasks_of: dict[str, list[int]] = {}
        for index, target in enumerate(self.targets):
            for codename in target:
                self._subtasks_of.setdefault(codename, []).append(index)

    def statuses(
        self, outcome_by_codename: dict[str, str | None]
    ) -> tuple[list[str], list[int | None]]:
        """Compute each subtask's status from the evaluations so far.

        outcome_by_codename: the outcome of each testcase already
            evaluated.

        return: for each subtask, its status, and the lowest-numbered
            dependency that failed (None if none did).

        """
        status = [self.PENDING] * len(self.targets)
        blocked_by: list[int | None] = [None] * len(self.targets)
        for index in self._order:
            deps = self.dependencies[index]
            failed = [number for number in deps
                      if status[number] == self.FAILED]
            if failed:
                status[index] = self.FAILED
                blocked_by[index] = min(failed)
                continue
            target = self.targets[index]
            done = [_outcome(outcome_by_codename[codename])
                    for codename in target
                    if codename in outcome_by_codename]
            parameter = self._score_type.parameters[index]
            if done and self._score_type.reduce(done, parameter) <= 0.0:
                status[index] = self.FAILED
            elif len(done) == len(target) and all(
                    status[number] == self.PASSED for number in deps):
                status[index] = self.PASSED
        return status, blocked_by

    def releasable(self, codename: str, status: list[str]) -> bool:
        """Tell whether a testcase may be evaluated now.

        A testcase in no subtask is never held. Otherwise it is released
        when at least one subtask containing it has all its dependencies
        passed.

        codename: the testcase.
        status: from statuses().

        return: whether the testcase may be evaluated now.

        """
        subtasks = self._subtasks_of.get(codename)
        if not subtasks:
            return True
        return any(
            all(status[number] == self.PASSED
                for number in self.dependencies[index])
            for index in subtasks)

    def skippable(
        self,
        codenames_left: list[str],
        status: list[str],
        blocked_by: list[int | None],
    ) -> dict[str, int]:
        """Find the testcases that no subtask still needs.

        A testcase is skipped when every subtask containing it failed
        because of a dependency.

        codenames_left: the testcases not evaluated yet.
        status: from statuses() (kept for symmetry with releasable()).
        blocked_by: from statuses().

        return: codename -> the dependency to name in its message (that
            of the lowest-numbered subtask containing it).

        """
        skip: dict[str, int] = {}
        for codename in codenames_left:
            subtasks = self._subtasks_of.get(codename)
            if subtasks and all(blocked_by[index] is not None
                                for index in subtasks):
                skip[codename] = blocked_by[min(subtasks)]
        return skip


_warned_datasets: set[object] = set()


def gate_for_dataset(dataset) -> SubtaskGate | None:
    """Return the dependency gate of a dataset, or None if it has none.

    Returns None without building anything when the dataset declares no
    dependency: such datasets are graded exactly as before. Also returns
    None, logging a warning once per dataset, when the dependencies can't
    be used (invalid parameters, or not a group score type), so that a bad
    dataset is graded without them instead of being left stuck.

    dataset: the dataset (needs score_type_parameters, score_type_object
        and id).

    return: the gate, or None if the dataset declares no dependency or
        they can't be used.

    """
    if not declares_dependencies(dataset.score_type_parameters):
        return None
    try:
        return SubtaskGate(dataset.score_type_object)
    except Exception:
        if dataset.id not in _warned_datasets:
            _warned_datasets.add(dataset.id)
            logger.warning(
                "Dataset %s declares subtask dependencies that cannot be "
                "used; grading it without them.", dataset.id, exc_info=True)
        return None
