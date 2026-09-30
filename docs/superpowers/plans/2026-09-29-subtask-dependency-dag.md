# Subtask Dependency DAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A subtask of a group score type can declare `depends_on`. A subtask
whose dependency scored 0 is worth 0 and is not graded. Datasets without
dependencies behave exactly as today.

**Architecture:**
- **`cms/grading/subtaskdag.py`, a new module.** It holds the pure logic:
  - validation and topological order;
  - the scoring rule helper;
  - a `SubtaskGate` that tells, for one submission, which testcases may run
    and which can be skipped.
- **`ScoreTypeGroup`.** It validates `depends_on` and applies the scoring
  rule.
- **Scheduling.** The two-phase hooks already present in `esoperations.py`
  and `EvaluationService.py` are extended so that they also run for
  datasets that declare dependencies.
- **Parity.** Everything is driven by data: a cheap check on the raw
  parameters returns early for datasets without `depends_on`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Jinja2 templates, Babel
(`cms.po`), `pytest` (asyncio group for everything here), and the
functional test suite (`cmstestsuite/Tests.py`).

**Spec:** `docs/superpowers/specs/2026-09-29-subtask-dependency-dag-design.md`

## Global Constraints

- **Branch:** `dag-subtareas`, in its own worktree. It merges into `beta`
  only if everything passes before 2026-10-02: the task reviews, the final
  review, and the full CI alone on `ubuntu:noble` and `debian:bookworm`.
- **Format:** `depends_on` is an optional key in the **dict form** of
  `score_type_parameters`. It is a list of subtask numbers **counted from
  0**, as CMS shows them ("Subtask 0", "Subtask 1", …). The list form
  cannot carry dependencies.
- **Validation errors:**
  - `depends_on` is not a list of integers (a `bool` is not an integer);
  - a number is out of range: below 0, or at least the number of subtasks;
  - a subtask depends on itself;
  - a number is repeated;
  - the dependencies form a cycle.

  Each one raises `ValueError` from the `ScoreTypeGroup` constructor.
- **Rule:**
  - A subtask is worth 0 when any dependency's score fraction is ≤ 0.
  - The rule is transitive: a subtask worth 0 by the rule counts as failed
    for its dependents.
  - A dependency passes when its score **fraction** is > 0. This matches
    "score > 0" for subtasks with points, and lets a 0-point examples group
    pass.
  - Several dependencies: all must pass.
- **Texts**, English source and Spanish in `cms/locale/es/LC_MESSAGES/cms.po`:
  - Skipped testcase: "Not tested: this subtask depends on subtask %s, which
    scored no points." / "No se probó: esta subtarea depende de la subtarea
    %s, que no obtuvo puntos."
  - Subtask note: "Worth 0 because it depends on subtask %(index)s, which
    scored no points." / "Vale 0 porque depende de la subtarea %(index)s,
    que no obtuvo puntos."
  - When several dependencies failed, the lowest-numbered one is named.
- **Parity:** a dataset whose raw `score_type_parameters` contain no
  non-empty `depends_on` must produce exactly the same operations,
  evaluations, scores and details as today. None of the new code may run
  for it, beyond `subtaskdag.declares_dependencies()` returning `False`.
- **Robustness:**
  - A dataset that declares dependencies but whose score type cannot be
    built is graded **without** the DAG. A warning is logged once per
    dataset id.
  - Nothing may leave a submission stuck.
- **Code style** (`CLAUDE.md`):
  - PEP 8, PEP 484, and the project docstring format (an imperative first
    line, then args/return/raise).
  - Code and comments in English.
  - Conventional Commits. Never amend, rebase or reset.
- **Tests:**
  - Use `.venv/bin/pytest`, wrapped in `timeout --foreground --signal=ABRT 900`.
  - Every file in this plan is in the **asyncio group**. Never run it in the
    same process as `cmstestsuite/unit_tests/cmscontrib`, `cmsranking`,
    `db/rankinggroup_test.py`, `service/twophase_evaluationservice_test.py`
    or `service/twophase_reenqueue_test.py`.
  - DB tests use a private config copy with a unique DB name, created and
    then dropped.
- **Docker:** never run two CI projects (`cmsci-*`) at once.
  `cgroup: host` makes their isolate boxes clash.

## Review Focus

1. **A dependency on a 0-point examples subtask.**
   - A dependent is zeroed only if the examples fail, never just because
     their points are 0.
   - Tested in Task 2 (`test_zero_point_dependency_passes_on_fraction`) and
     Task 3 (`test_zero_point_subtask_passes`).
2. **Evaluations already present when dependencies are added or changed.**
   This covers a re-evaluation, or a dataset edited after submissions.
   - Scoring applies the rule to evaluated subtasks.
   - The gate uses the existing outcomes.
   - Tested in Task 2 (`test_rule_applies_to_evaluated_subtasks`) and
     Task 3 (`test_statuses_use_existing_outcomes`).
3. **A dataset saved with a cycle while submissions are pending.**
   - The gate turns off, and the submissions are still graded and complete.
   - Tested in Task 3 (`test_gate_for_invalid_dataset_is_none_and_warns_once`)
     and Task 5 (`test_invalid_dependencies_do_not_block_grading`).
4. **A failed screening of a root subtask.** With two-phase on, it must also
   skip the root's dependents in the same `write_results` batch, in cascade.
   - Tested in Task 5
     (`test_screening_failure_of_root_skips_dependents_same_batch`).
5. **Regex or codename-list parameters where a testcase is in two subtasks,
   or in none.**
   - A shared testcase runs if any subtask needs it.
   - A testcase in no subtask is never held.
   - Tested in Task 3 (`test_shared_testcase_released_if_any_subtask_needs_it`,
     `test_testcase_in_no_subtask_is_never_held`) and Task 4
     (`test_shared_and_orphan_testcases`).

---

## File Structure

| File | Responsibility |
|---|---|
| `cms/grading/subtaskdag.py` (new) | The pure dependency logic: `declares_dependencies`, `parse_dependencies`, `topological_order`, `zeroed_by`, `SubtaskGate` and `gate_for_dataset`. It imports nothing from `cms.db`. |
| `cms/grading/scoretypes/abc.py` | `ScoreTypeGroup`: validates in `__init__`, sets `self.dependencies`, applies the rule in `compute_score`, passes the key through in `get_json_details`, and adds the template note. |
| `cms/grading/steps/evaluation.py` | The new `HumanMessage("skipped_dependency", …)`. |
| `cms/service/esoperations.py` | The gate in `submission_get_operations`, and the new `any_dataset_declares_dependencies`. |
| `cms/service/EvaluationService.py` | `_advance_dependencies`, the three write/enqueue hooks, and the sweeper condition. |
| `cms/locale/cms.pot`, `cms/locale/es/LC_MESSAGES/cms.po` | The three new msgids and their Spanish. |
| `cmstestsuite/unit_tests/grading/subtaskdag_test.py` (new) | Tests for Tasks 1 and 3. |
| `cmstestsuite/unit_tests/grading/scoretypes/GroupDependenciesTest.py` (new) | Tests for Task 2. |
| `cmstestsuite/unit_tests/service/subtaskdag_esoperations_test.py` (new) | Tests for Task 4. |
| `cmstestsuite/unit_tests/service/subtaskdag_write_results_test.py` (new) | Tests for Task 5. |
| `cmstestsuite/tasks/batch_dag/` (new), `cmstestsuite/Tests.py` | The functional task and its tests (Task 6). |
| `docs/subtask-dependencies.md` (new), `README.md` | The operator guide (Task 7). |

---

### Task 1: Dependency parsing, order and the scoring helper

**Files:**
- Create: `cms/grading/subtaskdag.py`
- Test (create): `cmstestsuite/unit_tests/grading/subtaskdag_test.py`

**Interfaces:**
- Produces:
  - `DEPENDS_ON = "depends_on"`
  - `declares_dependencies(parameters: object) -> bool`
  - `parse_dependencies(parameters: list) -> list[list[int]]`, which raises `ValueError`
  - `topological_order(dependencies: list[list[int]]) -> list[int]`, which raises `ValueError` on a cycle
  - `zeroed_by(fractions: list[float], dependencies: list[list[int]]) -> list[int | None]`

- [ ] **Step 1: Write the failing tests.** Put the following in `cmstestsuite/unit_tests/grading/subtaskdag_test.py`:

```python
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

"""Tests for cms.grading.subtaskdag."""

import unittest

from cms.grading import subtaskdag


class TestDeclaresDependencies(unittest.TestCase):

    def test_list_form_and_plain_dicts_declare_none(self):
        self.assertFalse(subtaskdag.declares_dependencies([[20, 3]]))
        self.assertFalse(subtaskdag.declares_dependencies(
            [{"max_score": 20, "testcases": 3}]))
        self.assertFalse(subtaskdag.declares_dependencies(
            [{"max_score": 20, "testcases": 3, "depends_on": []}]))
        self.assertFalse(subtaskdag.declares_dependencies(100))
        self.assertFalse(subtaskdag.declares_dependencies({}))

    def test_non_empty_depends_on_declares(self):
        self.assertTrue(subtaskdag.declares_dependencies([
            {"max_score": 20, "testcases": 3},
            {"max_score": 80, "testcases": 3, "depends_on": [0]}]))


class TestParseDependencies(unittest.TestCase):

    def test_valid(self):
        self.assertEqual(
            subtaskdag.parse_dependencies([
                [20, 3],
                {"max_score": 30, "testcases": 3},
                {"max_score": 50, "testcases": 3, "depends_on": [1, 0]}]),
            [[], [], [0, 1]])

    def test_not_a_list_of_integers(self):
        for bad in ["0", [0.0], [True], [None], {"0": 1}]:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "Subtask 1"):
                    subtaskdag.parse_dependencies([
                        [20, 3],
                        {"max_score": 80, "testcases": 3,
                         "depends_on": bad}])

    def test_out_of_range(self):
        for bad in [-1, 2]:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "does not exist"):
                    subtaskdag.parse_dependencies([
                        [20, 3],
                        {"max_score": 80, "testcases": 3,
                         "depends_on": [bad]}])

    def test_self_dependency(self):
        with self.assertRaisesRegex(ValueError, "itself"):
            subtaskdag.parse_dependencies([
                {"max_score": 100, "testcases": 3, "depends_on": [0]}])

    def test_repeated(self):
        with self.assertRaisesRegex(ValueError, "repeats"):
            subtaskdag.parse_dependencies([
                [20, 3],
                {"max_score": 80, "testcases": 3, "depends_on": [0, 0]}])

    def test_cycle(self):
        with self.assertRaisesRegex(ValueError, "cycle.*0, 1"):
            subtaskdag.parse_dependencies([
                {"max_score": 50, "testcases": 3, "depends_on": [1]},
                {"max_score": 50, "testcases": 3, "depends_on": [0]}])


class TestTopologicalOrder(unittest.TestCase):

    def test_dependencies_come_first_lowest_number_first(self):
        self.assertEqual(
            subtaskdag.topological_order([[2], [], [], [0, 1]]),
            [1, 2, 0, 3])

    def test_no_dependencies_keeps_order(self):
        self.assertEqual(subtaskdag.topological_order([[], [], []]),
                         [0, 1, 2])


class TestZeroedBy(unittest.TestCase):

    def test_independent_subtasks_keep_their_score(self):
        self.assertEqual(
            subtaskdag.zeroed_by([0.0, 1.0, 1.0], [[], [], []]),
            [None, None, None])

    def test_failed_dependency_zeroes_the_dependent(self):
        self.assertEqual(
            subtaskdag.zeroed_by([0.0, 1.0, 1.0], [[], [0], []]),
            [None, 0, None])

    def test_transitive(self):
        self.assertEqual(
            subtaskdag.zeroed_by([0.0, 1.0, 1.0], [[], [0], [1]]),
            [None, 0, 1])

    def test_partial_score_passes(self):
        self.assertEqual(
            subtaskdag.zeroed_by([0.5, 1.0], [[], [0]]),
            [None, None])

    def test_lowest_numbered_failed_dependency_is_named(self):
        self.assertEqual(
            subtaskdag.zeroed_by([1.0, 0.0, 0.0, 1.0], [[], [], [], [2, 1, 0]]),
            [None, None, None, 1])


if __name__ == "__main__":
    unittest.main()
```

Note: `test_lowest_numbered_failed_dependency_is_named` passes unsorted `[2, 1, 0]` on purpose, to prove that `zeroed_by` does not rely on sorted input.

- [ ] **Step 2: Run the tests to see them fail.**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/grading/subtaskdag_test.py -q`
Expected: FAIL with `ImportError` (`cannot import name 'subtaskdag'`).

- [ ] **Step 3: Implement.** Create `cms/grading/subtaskdag.py` with the project's AGPL license header, as in the other files of `cms/grading/`:

```python
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
```

- [ ] **Step 4: Run the tests to see them pass**, with the same command. Expected: all pass. Run `pyflakes` on both files.

- [ ] **Step 5: Commit.**

```bash
git add cms/grading/subtaskdag.py cmstestsuite/unit_tests/grading/subtaskdag_test.py
git commit -m "feat(grading): parse and order subtask dependencies"
```

---

### Task 2: The scoring rule in ScoreTypeGroup

**Files:**
- Modify: `cms/grading/scoretypes/abc.py`:
  - `ScoreTypeGroupParametersDict`, around line 226;
  - the `N_` markers in `ScoreTypeGroup`, around line 265;
  - `TEMPLATE`, where the subtask body starts;
  - `get_json_details`, around line 376;
  - a new `__init__`;
  - `compute_score`, around line 546.
- Modify: `cms/locale/cms.pot` and `cms/locale/es/LC_MESSAGES/cms.po`.
- Test (create): `cmstestsuite/unit_tests/grading/scoretypes/GroupDependenciesTest.py`

**Interfaces:**
- Consumes (Task 1): `subtaskdag.parse_dependencies` and `subtaskdag.zeroed_by`.
- Produces:
  - `ScoreTypeGroup.dependencies: list[list[int]]`, which is empty lists when none are declared;
  - the subtask details key `"zeroed_by_dependency": int`, present only for zeroed subtasks;
  - the template note.

- [ ] **Step 1: Write the failing tests.** Put the following in `cmstestsuite/unit_tests/grading/scoretypes/GroupDependenciesTest.py`, with the same license header as Task 1:

```python
"""Tests for subtask dependencies in the group score types."""

import unittest

from cms.grading.scoretypes.GroupMin import GroupMin
from cms.grading.scoretypes.GroupMul import GroupMul
from cms.grading.scoretypes.GroupThreshold import GroupThreshold
from cmstestsuite.unit_tests.grading.scoretypes.scoretypetestutils import \
    ScoreTypeTestMixin


PUBLIC = {"0": True, "1": True, "2": True, "3": True}


def params(*subtasks):
    """Build dict-form parameters, one testcase per subtask by default."""
    return [dict(max_score=s[0], testcases=s[1], **s[2]) for s in subtasks]


class TestGroupDependencies(ScoreTypeTestMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        # Subtask 0 = "0", subtask 1 = "1" (depends on 0), subtask 2 = "2".
        self.parameters = params(
            (20, 1, {}), (30, 1, {"depends_on": [0]}), (50, 1, {}))
        self.public = {"0": True, "1": True, "2": True}

    def test_validation_errors_raise(self):
        for bad in [[1], [5], ["0"], [0, 0]]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    GroupMin(params((20, 1, {}), (80, 1, {"depends_on": bad})),
                             {"0": True, "1": True}, 2)

    def test_dependencies_attribute(self):
        st = GroupMin(self.parameters, self.public, 2)
        self.assertEqual(st.dependencies, [[], [0], []])
        self.assertEqual(GroupMin([[20, 1], [80, 2]], PUBLIC, 2).dependencies,
                         [[], []])

    def test_failed_dependency_zeroes_the_dependent(self):
        st = GroupMin(self.parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        score, details, public_score, _, ranking = st.compute_score(sr)
        self.assertAlmostEqual(score, 50.0)
        self.assertAlmostEqual(public_score, 50.0)
        self.assertEqual(ranking, ["0", "0", "50"])
        self.assertNotIn("zeroed_by_dependency", details[0])
        self.assertEqual(details[1]["zeroed_by_dependency"], 0)
        self.assertEqual(details[1]["score"], 0.0)
        self.assertEqual(details[1]["score_fraction"], 0.0)
        self.assertNotIn("zeroed_by_dependency", details[2])

    def test_passed_dependency_keeps_scores_and_details(self):
        st = GroupMin(self.parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        score, details, _, _, ranking = st.compute_score(sr)
        self.assertAlmostEqual(score, 100.0)
        self.assertEqual(ranking, ["20", "30", "50"])
        for subtask in details:
            self.assertNotIn("zeroed_by_dependency", subtask)

    def test_rule_applies_to_evaluated_subtasks(self):
        # Subtask 1's own testcase is correct, but its dependency failed.
        st = GroupMin(self.parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        _, details, _, _, _ = st.compute_score(sr)
        self.assertEqual(details[1]["testcases"][0]["outcome"], "Correct")
        self.assertEqual(details[1]["score"], 0.0)

    def test_transitive(self):
        parameters = params((20, 1, {}), (30, 1, {"depends_on": [0]}),
                            (50, 1, {"depends_on": [1]}))
        st = GroupMin(parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        score, details, _, _, _ = st.compute_score(sr)
        self.assertAlmostEqual(score, 0.0)
        self.assertEqual(details[2]["zeroed_by_dependency"], 1)

    def test_partial_dependency_passes(self):
        parameters = params((20, 2, {}), (80, 1, {"depends_on": [0]}))
        public = {"0": True, "1": True, "2": True}
        st = GroupMin(parameters, public, 2)
        sr = self.get_submission_result(public)
        self.set_outcome(sr, "0", 0.5)
        score, _, _, _, ranking = st.compute_score(sr)
        self.assertAlmostEqual(score, 90.0)
        self.assertEqual(ranking, ["10", "80"])

    def test_zero_point_dependency_passes_on_fraction(self):
        parameters = params((0, 1, {}), (100, 1, {"depends_on": [0]}))
        public = {"0": True, "1": True}
        st = GroupMin(parameters, public, 2)
        sr = self.get_submission_result(public)
        self.assertAlmostEqual(st.compute_score(sr)[0], 100.0)
        self.set_outcome(sr, "0", 0.0)
        self.assertAlmostEqual(st.compute_score(sr)[0], 0.0)

    def test_group_mul_and_threshold(self):
        mul = GroupMul(self.parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        self.assertAlmostEqual(mul.compute_score(sr)[0], 50.0)

        # GroupThreshold: an outcome passes when 0 < outcome <= threshold.
        thr = GroupThreshold(
            params((20, 1, {"threshold": 1.0}),
                   (30, 1, {"threshold": 1.0, "depends_on": [0]}),
                   (50, 1, {"threshold": 1.0})),
            self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 2.0)
        self.assertAlmostEqual(thr.compute_score(sr)[0], 50.0)

    def test_parity_without_dependencies(self):
        list_form = GroupMin([[20, 1], [30, 1], [50, 1]], self.public, 2)
        dict_form = GroupMin(params((20, 1, {}), (30, 1, {}), (50, 1, {})),
                             self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        self.assertEqual(list_form.compute_score(sr),
                         dict_form.compute_score(sr))

    def test_json_details_and_html_note(self):
        st = GroupMin(self.parameters, self.public, 2)
        sr = self.get_submission_result(self.public)
        self.set_outcome(sr, "0", 0.0)
        _, details, _, _, _ = st.compute_score(sr)
        json_details = st.get_json_details(details)
        self.assertEqual(json_details[1]["zeroed_by_dependency"], 0)
        self.assertNotIn("zeroed_by_dependency", json_details[0])
        html = st.get_html_details(details)
        self.assertIn("Worth 0 because it depends on subtask 0, which scored "
                      "no points.", html)
        self.assertEqual(html.count("Worth 0 because"), 1)


if __name__ == "__main__":
    unittest.main()
```

Notes for the implementer:
- `get_submission_result` in `scoretypetestutils.py` builds evaluations with outcome 1.0 and `text = "Nothing to report"`. The HTML test only checks the note.
- If `get_html_details` needs `text` to be a list for rendering, set it with a local helper in this test file, for example set every evaluation's `text` to `["Output is correct"]`. Don't change `scoretypetestutils.py`.

- [ ] **Step 2: Run the tests to see them fail.**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/grading/scoretypes/GroupDependenciesTest.py -q`
Expected: FAIL. `dependencies` does not exist, and no subtask is zeroed.

- [ ] **Step 3: Implement.** Make these changes in `cms/grading/scoretypes/abc.py`.

1. Import the module next to the other `cms.grading` imports: `from cms.grading import subtaskdag`. If that creates an import cycle, import it inside the two methods instead, and say so in the report.
2. Add to `ScoreTypeGroupParametersDict`: `depends_on: NotRequired[list[int]]`.
3. In the `N_` marker block of `ScoreTypeGroup`, add
   `N_("Worth 0 because it depends on subtask %(index)s, which scored no points.")`.
4. Add a constructor to `ScoreTypeGroup`, right after the `parameters` annotation:

```python
    def __init__(
        self,
        parameters: object,
        public_testcases: dict[str, bool],
        score_precision: int,
    ):
        """Initializer; see ScoreType.

        raise (ValueError): also if a subtask's "depends_on" is invalid
            (see cms.grading.subtaskdag), so that AWS reports it like any
            other invalid parameter.

        """
        super().__init__(parameters, public_testcases, score_precision)
        self.dependencies: list[list[int]] = \
            subtaskdag.parse_dependencies(self.parameters)
```

5. In `TEMPLATE`, right after `<div class="subtask-body">`, insert:

```jinja
    {% if "zeroed_by_dependency" in st %}
        <p class="subtask-dependency">
            {% trans index=st["zeroed_by_dependency"] %}Worth 0 because it depends on subtask {{ index }}, which scored no points.{% endtrans %}
        </p>
    {% endif %}
```

6. In `get_json_details`, after `filtered_st` is built, add:

```python
            if "zeroed_by_dependency" in st:
                filtered_st["zeroed_by_dependency"] = st["zeroed_by_dependency"]
```

7. In `compute_score`, right after `score_precision = …`, add:

```python
        # A subtask whose dependency failed is worth 0 (see
        # cms.grading.subtaskdag); computed up front from each subtask's
        # own fraction so that the rule is transitive.
        zeroed = [None] * len(self.parameters)
        if any(self.dependencies):
            zeroed = subtaskdag.zeroed_by(
                [self.reduce([float(evaluations[tc_idx].outcome)
                              for tc_idx in target], parameter)
                 for target, parameter in zip(targets, self.parameters)],
                self.dependencies)
```

   Then, in the per-subtask loop, replace `st_score_fraction = self.reduce(...)` with the two lines below. Change the restricted-feedback condition, `st_score_fraction < 1.0`, to `own_fraction < 1.0`. That way the feedback reflects the subtask's own testcases, which is identical when the subtask is not zeroed.

```python
            own_fraction = self.reduce(
                [float(evaluations[tc_idx].outcome) for tc_idx in target],
                parameter)
            st_score_fraction = 0.0 if zeroed[st_idx] is not None \
                else own_fraction
```

   Build the subtask dict in a variable and add the key only when the subtask is zeroed, so that details without dependencies are unchanged:

```python
            subtask = {
                "idx": st_idx,
                "score_fraction": st_score_fraction,
                "score": rounded_score,
                "max_score": self.get_max_score(parameter),
                "testcases": testcases,
            }
            if zeroed[st_idx] is not None:
                subtask["zeroed_by_dependency"] = zeroed[st_idx]
            subtasks.append(subtask)
```

   Keep the two existing comments about `score_fraction` in that dict.

8. Translations. Add this entry to `cms/locale/cms.pot`, next to `"Subtask %(index)s"`:

```
msgid "Worth 0 because it depends on subtask %(index)s, which scored no points."
msgstr ""
```

   Add this entry to `cms/locale/es/LC_MESSAGES/cms.po`:

```
msgid "Worth 0 because it depends on subtask %(index)s, which scored no points."
msgstr "Vale 0 porque depende de la subtarea %(index)s, que no obtuvo puntos."
```

- [ ] **Step 4: Run the tests to see them pass.** Run the new file, then the existing score type tests (`GroupMinTest.py`, `GroupMulTest.py`, `GroupThresholdTest.py`, `SumTest.py`). All must pass unchanged. Also run `pyflakes cms/grading/scoretypes/abc.py`.

- [ ] **Step 5: Commit.**

```bash
git add cms/grading/scoretypes/abc.py cms/locale/cms.pot cms/locale/es/LC_MESSAGES/cms.po cmstestsuite/unit_tests/grading/scoretypes/GroupDependenciesTest.py
git commit -m "feat(grading): make a subtask with a failed dependency worth 0"
```

---

### Task 3: SubtaskGate — what may run and what may be skipped

**Files:**
- Modify: `cms/grading/subtaskdag.py`, appending to it.
- Test: `cmstestsuite/unit_tests/grading/subtaskdag_test.py`, adding classes.

**Interfaces:**
- Consumes: `topological_order` and `declares_dependencies` (Task 1); `ScoreTypeGroup.dependencies`, `.parameters`, `.reduce()` and `.retrieve_target_testcases()` (Task 2).
- Produces:
  - `SubtaskGate(score_type)`, with the constants `PENDING = "pending"`, `PASSED = "passed"` and `FAILED = "failed"`, and these methods:
    - `statuses(outcome_by_codename: dict[str, str | None]) -> tuple[list[str], list[int | None]]`
    - `releasable(codename: str, status: list[str]) -> bool`
    - `skippable(codenames_left: list[str], status: list[str], blocked_by: list[int | None]) -> dict[str, int]`
  - `gate_for_dataset(dataset) -> SubtaskGate | None`

- [ ] **Step 1: Write the failing tests.** Append to `subtaskdag_test.py`, and add `from unittest.mock import Mock, PropertyMock` and `from cms.grading.scoretypes.GroupMin import GroupMin` to the imports:

```python
def _gate(parameters, codenames):
    public = {codename: True for codename in codenames}
    return subtaskdag.SubtaskGate(GroupMin(parameters, public, 2))


class TestSubtaskGate(unittest.TestCase):

    def setUp(self):
        # Count-based: subtask 0 = a0, a1; subtask 1 = b0 (depends on 0);
        # subtask 2 = c0.
        self.gate = _gate(
            [{"max_score": 20, "testcases": 2},
             {"max_score": 30, "testcases": 1, "depends_on": [0]},
             {"max_score": 50, "testcases": 1}],
            ["a0", "a1", "b0", "c0"])

    def test_nothing_evaluated(self):
        status, blocked_by = self.gate.statuses({})
        self.assertEqual(status, ["pending", "pending", "pending"])
        self.assertEqual(blocked_by, [None, None, None])
        self.assertTrue(self.gate.releasable("a0", status))
        self.assertFalse(self.gate.releasable("b0", status))
        self.assertTrue(self.gate.releasable("c0", status))

    def test_dependency_passed_releases_dependent(self):
        status, _ = self.gate.statuses({"a0": "1.0", "a1": "1.0"})
        self.assertEqual(status[0], "passed")
        self.assertTrue(self.gate.releasable("b0", status))

    def test_statuses_use_existing_outcomes(self):
        status, blocked_by = self.gate.statuses(
            {"a0": "0.0", "b0": "1.0", "c0": "1.0"})
        self.assertEqual(status, ["failed", "failed", "passed"])
        self.assertEqual(blocked_by, [None, 0, None])

    def test_failure_is_known_before_all_testcases_run(self):
        status, blocked_by = self.gate.statuses({"a0": "0.0"})
        self.assertEqual(status[:2], ["failed", "failed"])
        self.assertEqual(blocked_by[1], 0)
        self.assertEqual(
            self.gate.skippable(["a1", "b0", "c0"], status, blocked_by),
            {"b0": 0})

    def test_partial_score_passes(self):
        status, _ = self.gate.statuses({"a0": "0.5", "a1": "1.0"})
        self.assertEqual(status[0], "passed")

    def test_zero_point_subtask_passes(self):
        gate = _gate([{"max_score": 0, "testcases": 1},
                      {"max_score": 100, "testcases": 1, "depends_on": [0]}],
                     ["a0", "b0"])
        status, _ = gate.statuses({"a0": "1.0"})
        self.assertEqual(status[0], "passed")

    def test_transitive_skip(self):
        gate = _gate([{"max_score": 20, "testcases": 1},
                      {"max_score": 30, "testcases": 1, "depends_on": [0]},
                      {"max_score": 50, "testcases": 1, "depends_on": [1]}],
                     ["a0", "b0", "c0"])
        status, blocked_by = gate.statuses({"a0": "0.0"})
        self.assertEqual(blocked_by, [None, 0, 1])
        self.assertEqual(gate.skippable(["b0", "c0"], status, blocked_by),
                         {"b0": 0, "c0": 1})

    def test_shared_testcase_released_if_any_subtask_needs_it(self):
        # Regex params: "x" is in subtask 0 and in subtask 1.
        gate = _gate([{"max_score": 50, "testcases": "^(a|x)"},
                      {"max_score": 50, "testcases": "^(b|x)",
                       "depends_on": [0]}],
                     ["a", "b", "x"])
        status, blocked_by = gate.statuses({"a": "0.0"})
        self.assertTrue(gate.releasable("x", status))
        self.assertEqual(gate.skippable(["b", "x"], status, blocked_by),
                         {"b": 0})

    def test_testcase_in_no_subtask_is_never_held(self):
        gate = _gate([{"max_score": 50, "testcases": "^a"},
                      {"max_score": 50, "testcases": "^b",
                       "depends_on": [0]}],
                     ["a", "b", "z"])
        status, blocked_by = gate.statuses({"a": "0.0"})
        self.assertTrue(gate.releasable("z", status))
        self.assertNotIn("z", gate.skippable(["b", "z"], status, blocked_by))


class TestGateForDataset(unittest.TestCase):

    def test_no_dependencies_builds_nothing(self):
        dataset = Mock()
        dataset.score_type_parameters = [[20, 1], [80, 1]]
        type(dataset).score_type_object = PropertyMock(
            side_effect=AssertionError("must not be built"))
        self.assertIsNone(subtaskdag.gate_for_dataset(dataset))

    def test_dependencies_build_a_gate(self):
        dataset = Mock()
        dataset.score_type_parameters = [
            {"max_score": 20, "testcases": 1},
            {"max_score": 80, "testcases": 1, "depends_on": [0]}]
        dataset.score_type_object = GroupMin(
            dataset.score_type_parameters, {"a": True, "b": True}, 2)
        gate = subtaskdag.gate_for_dataset(dataset)
        self.assertIsInstance(gate, subtaskdag.SubtaskGate)

    def test_gate_for_invalid_dataset_is_none_and_warns_once(self):
        dataset = Mock()
        dataset.id = 424242
        dataset.score_type_parameters = [
            {"max_score": 50, "testcases": 1, "depends_on": [1]},
            {"max_score": 50, "testcases": 1, "depends_on": [0]}]
        type(dataset).score_type_object = PropertyMock(
            side_effect=ValueError("cycle"))
        with self.assertLogs("cms.grading.subtaskdag", "WARNING") as logs:
            self.assertIsNone(subtaskdag.gate_for_dataset(dataset))
            self.assertIsNone(subtaskdag.gate_for_dataset(dataset))
        self.assertEqual(len(logs.records), 1)
```

- [ ] **Step 2: Run the tests to see them fail.** Run the same file. Expected: an `AttributeError` for `SubtaskGate`.

- [ ] **Step 3: Implement.** Append to `cms/grading/subtaskdag.py`:

```python
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
```

Note: the value of `status` passed to `skippable` is unused by design. Keep it: it keeps the three calls parallel for callers. If `pyflakes` flags it, rename the parameter to `_status` and update the tests.

- [ ] **Step 4: Run the tests to see them pass** (the whole `subtaskdag_test.py`), then run `pyflakes`.

- [ ] **Step 5: Commit.**

```bash
git add cms/grading/subtaskdag.py cmstestsuite/unit_tests/grading/subtaskdag_test.py
git commit -m "feat(grading): tell which testcases a submission may run or skip by dependencies"
```

---

### Task 4: The gate in esoperations and the new evaluation message

**Files:**
- Modify: `cms/grading/steps/evaluation.py`, the `EVALUATION_MESSAGES` collection around line 80.
- Modify: `cms/service/esoperations.py`:
  - `submission_get_operations`, lines ~157-231;
  - a new function after `get_submission_results_to_evaluate`, ~line 545.
- Modify: `cms/locale/cms.pot` and `cms/locale/es/LC_MESSAGES/cms.po`.
- Test (create): `cmstestsuite/unit_tests/service/subtaskdag_esoperations_test.py`

**Interfaces:**
- Consumes: `subtaskdag.gate_for_dataset` and `subtaskdag.declares_dependencies` (Tasks 1 and 3).
- Produces:
  - `EVALUATION_MESSAGES.get("skipped_dependency")`, whose message has one `%s`, the dependency number;
  - `any_dataset_declares_dependencies(session: Session, contest_id: int | None) -> bool` in `cms.service.esoperations`.

- [ ] **Step 1: Write the failing tests.** Put the following in `cmstestsuite/unit_tests/service/subtaskdag_esoperations_test.py`, with the license header:

```python
"""Tests for the subtask dependency gate in submission_get_operations()."""

import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.grading.steps import EVALUATION_MESSAGES
from cms.service.esoperations import ESOperation, \
    any_dataset_declares_dependencies, submission_get_operations


DAG_PARAMETERS = [
    {"max_score": 20, "testcases": 3},
    {"max_score": 30, "testcases": 2, "depends_on": [0]},
    {"max_score": 50, "testcases": 1},
]
CODENAMES = [
    "s0-00-sample", "s0-01-scr-wa", "s0-02-normal",
    "s1-00-sample", "s1-01-normal",
    "s2-00-sample",
]


class TestSubtaskDependencyGate(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(
            task=self.task, autojudge=True, score_type="GroupMin",
            score_type_parameters=DAG_PARAMETERS)
        self.task.active_dataset = self.dataset
        self.testcases = {
            codename: self.add_testcase(self.dataset, codename=codename)
            for codename in CODENAMES}
        self.submission, results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.result = results[0]
        self.session.flush()

    def _released(self):
        return set(
            op.testcase_codename
            for op, _, _ in submission_get_operations(
                self.result, self.submission, self.dataset)
            if op.type_ == ESOperation.EVALUATION)

    def _evaluate(self, **outcomes):
        for codename, outcome in outcomes.items():
            self.add_evaluation(
                self.result, self.testcases[codename], outcome=outcome)
        self.session.flush()

    def test_message_is_registered(self):
        message = EVALUATION_MESSAGES.get("skipped_dependency").message
        self.assertIn("%s", message)

    def test_dependents_wait_for_their_dependencies(self):
        self.assertEqual(self._released(), {
            "s0-00-sample", "s0-01-scr-wa", "s0-02-normal", "s2-00-sample"})

    def test_passed_dependency_releases_the_dependent(self):
        self._evaluate(**{"s0-00-sample": "1.0", "s0-01-scr-wa": "1.0",
                          "s0-02-normal": "1.0"})
        self.assertEqual(self._released(),
                         {"s1-00-sample", "s1-01-normal", "s2-00-sample"})

    def test_failed_dependency_never_releases_the_dependent(self):
        self._evaluate(**{"s0-00-sample": "1.0", "s0-01-scr-wa": "0.0"})
        self.assertEqual(self._released(), {"s0-02-normal", "s2-00-sample"})

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_with_two_phase_the_released_subtask_screens_first(self):
        self.assertEqual(self._released(), {
            "s0-00-sample", "s0-01-scr-wa", "s2-00-sample"})
        self._evaluate(**{"s0-00-sample": "1.0", "s0-01-scr-wa": "1.0",
                          "s0-02-normal": "1.0"})
        self.assertEqual(self._released(), {"s1-00-sample", "s2-00-sample"})


class TestParityWithoutDependencies(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(
            task=self.task, autojudge=True, score_type="GroupMin",
            score_type_parameters=[[20, 3], [30, 2], [50, 1]])
        self.task.active_dataset = self.dataset
        for codename in CODENAMES:
            self.add_testcase(self.dataset, codename=codename)
        self.submission, results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.result = results[0]
        self.session.flush()

    def _released(self):
        return set(
            op.testcase_codename
            for op, _, _ in submission_get_operations(
                self.result, self.submission, self.dataset)
            if op.type_ == ESOperation.EVALUATION)

    def test_two_phase_off_releases_everything(self):
        self.assertEqual(self._released(), set(CODENAMES))

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_two_phase_on_releases_the_screening(self):
        self.assertEqual(self._released(), {
            "s0-00-sample", "s0-01-scr-wa", "s1-00-sample", "s2-00-sample"})


class TestSharedAndOrphanTestcases(DatabaseMixin, unittest.TestCase):

    def test_shared_and_orphan_testcases(self):
        contest = self.add_contest()
        participation = self.add_participation(contest=contest)
        task = self.add_task(contest=contest)
        dataset = self.add_dataset(
            task=task, autojudge=True, score_type="GroupMin",
            score_type_parameters=[
                {"max_score": 50, "testcases": "^(a|x)"},
                {"max_score": 50, "testcases": "^(b|x)", "depends_on": [0]}])
        task.active_dataset = dataset
        for codename in ["a", "b", "x", "z"]:
            self.add_testcase(dataset, codename=codename)
        submission, results = self.add_submission_with_results(
            task, participation, True)
        self.session.flush()
        released = set(
            op.testcase_codename
            for op, _, _ in submission_get_operations(
                results[0], submission, dataset)
            if op.type_ == ESOperation.EVALUATION)
        self.assertEqual(released, {"a", "x", "z"})


class TestAnyDatasetDeclaresDependencies(DatabaseMixin, unittest.TestCase):

    def test_detects_per_contest(self):
        with_deps = self.add_contest()
        without = self.add_contest()
        task = self.add_task(contest=with_deps)
        self.add_dataset(task=task, score_type="GroupMin",
                         score_type_parameters=DAG_PARAMETERS)
        other = self.add_task(contest=without)
        self.add_dataset(task=other, score_type="GroupMin",
                         score_type_parameters=[[100, 1]])
        self.session.flush()
        self.assertTrue(
            any_dataset_declares_dependencies(self.session, with_deps.id))
        self.assertFalse(
            any_dataset_declares_dependencies(self.session, without.id))
        self.assertTrue(any_dataset_declares_dependencies(self.session, None))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to see them fail.**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/service/subtaskdag_esoperations_test.py -q`, with `CMS_CONFIG` set to your private config.
Expected: an `ImportError` for `any_dataset_declares_dependencies`.

- [ ] **Step 3: Implement.**

1. In `cms/grading/steps/evaluation.py`, add to the `EVALUATION_MESSAGES` `MessageCollection`, after `"skipped"`:

```python
    HumanMessage("skipped_dependency",
                 N_("Not tested: this subtask depends on subtask %s, "
                    "which scored no points."),
                 N_("This testcase was not run because its subtask depends "
                    "on another subtask that scored no points, so it is "
                    "worth 0 anyway.")),
```

2. In `cms/service/esoperations.py`, add `from cms.grading import subtaskdag` next to the `twophase` import. In `submission_get_operations`, right before `for testcase_codename in dataset.testcases.keys():`, add:

```python
        # Subtask dependencies: hold a subtask's testcases until all its
        # dependencies passed; a dependent of a failed subtask is never
        # released (EvaluationService skips it). No-op for datasets that
        # declare none. See cms/grading/subtaskdag.py.
        gate = subtaskdag.gate_for_dataset(dataset)
        if gate is not None:
            dag_status, _ = gate.statuses({
                evaluation.codename: evaluation.outcome
                for evaluation in submission_result.evaluations})
```

   Inside the loop, right after the `evaluated_testcase_ids` check and before the two-phase check, add:

```python
            if gate is not None \
                    and not gate.releasable(testcase_codename, dag_status):
                continue
```

3. In `cms/service/esoperations.py`, after `get_submission_results_to_evaluate`, add the function below. Import `Dataset` from `cms.db` if it isn't imported yet.

```python
def any_dataset_declares_dependencies(
    session: Session, contest_id: int | None = None
) -> bool:
    """Tell whether any dataset of the contest declares subtask dependencies.

    The sweeper uses this to choose the dependency-aware path, which
    otherwise it only takes when two-phase grading is on.

    session: the database session to use.
    contest_id: the contest to look at; if None, look at every contest.

    return: whether some dataset's parameters have a "depends_on".

    """
    if contest_id is None:
        contest_filter = literal(True)
    else:
        contest_filter = Task.contest_id == contest_id
    parameters = session.execute(
        select(Dataset.score_type_parameters)
        .join(Dataset.task)
        .filter(contest_filter)).scalars()
    return any(subtaskdag.declares_dependencies(p) for p in parameters)
```

   If `Dataset.task` is not a relationship usable in `.join`, use `.join(Task, Dataset.task_id == Task.id)`.

4. Translations. Add both msgids, the message and its help text, to `cms/locale/cms.pot` next to the "Skipped after screening phase failure" entries. Add them to `cms/locale/es/LC_MESSAGES/cms.po`:

```
msgid "Not tested: this subtask depends on subtask %s, which scored no points."
msgstr "No se probó: esta subtarea depende de la subtarea %s, que no obtuvo puntos."

msgid "This testcase was not run because its subtask depends on another subtask that scored no points, so it is worth 0 anyway."
msgstr "Este caso no se probó porque su subtarea depende de otra subtarea que no obtuvo puntos, así que vale 0 de todos modos."
```

- [ ] **Step 4: Run the tests to see them pass.** Run the new file. Then run the existing `twophase_esoperations_test.py` and `twophase_gate_consistency_test.py` in the same asyncio process: they must pass unchanged. Run `pyflakes` on the changed files.

- [ ] **Step 5: Commit.**

```bash
git add cms/grading/steps/evaluation.py cms/service/esoperations.py cms/locale/cms.pot cms/locale/es/LC_MESSAGES/cms.po cmstestsuite/unit_tests/service/subtaskdag_esoperations_test.py
git commit -m "feat(evaluation): hold the testcases of a subtask until its dependencies pass"
```

---

### Task 5: EvaluationService — skip in cascade, release, and the sweeper

**Files:**
- Modify: `cms/service/EvaluationService.py`:
  - `submission_enqueue_operations`, ~340-383;
  - `_missing_operations_sync`, ~424;
  - `write_results`, ~830-842 and ~892;
  - a new `_advance_dependencies` next to `_advance_two_phase`, ~955.
- Test (create): `cmstestsuite/unit_tests/service/subtaskdag_write_results_test.py`

**Interfaces:**
- Consumes: `subtaskdag.gate_for_dataset`, `SubtaskGate.statuses`, `SubtaskGate.skippable`, `EVALUATION_MESSAGES.get("skipped_dependency")` and `any_dataset_declares_dependencies` (Tasks 3 and 4).
- Produces: `EvaluationService._advance_dependencies(session, submission_result) -> None`

- [ ] **Step 1: Write the failing tests.** Create `subtaskdag_write_results_test.py` by copying the fixture of `cmstestsuite/unit_tests/service/twophase_write_results_test.py`: `asyncSetUp` with its three patches, `tearDown`, `_build_service` and `_make_evaluation_result`. Change the dataset to `score_type="GroupMin"` with the parameters and testcases below. Then add these tests:

```python
DAG_PARAMETERS = [
    {"max_score": 20, "testcases": 2},
    {"max_score": 30, "testcases": 1, "depends_on": [0]},
    {"max_score": 50, "testcases": 1, "depends_on": [1]},
]
CODENAMES = ["s0-00-sample", "s0-01-scr-wa", "s1-00-normal", "s2-00-normal"]

    # (the dataset in asyncSetUp: self.add_dataset(task=self.task,
    #  autojudge=True, score_type="GroupMin",
    #  score_type_parameters=DAG_PARAMETERS); testcases from CODENAMES)

    def _op(self, codename):
        return ESOperation(ESOperation.EVALUATION, self.submission.id,
                           self.dataset.id, codename)

    async def _write(self, **outcomes):
        items = [(self._op(c), self._make_evaluation_result(
                    self._op(c), o, ["Output is correct"]))
                 for c, o in outcomes.items()]
        service = self._build_service()
        await service.write_results(items)
        self.session.expire_all()
        return Submission.get_from_id(
            self.submission.id, self.session).get_result(self.dataset)

    async def test_failed_root_skips_dependents_in_cascade(self):
        result = await self._write(
            **{"s0-00-sample": "1.0", "s0-01-scr-wa": "0.0"})
        texts = {e.codename: e.text for e in result.evaluations}
        message = EVALUATION_MESSAGES.get("skipped_dependency").message
        self.assertEqual(texts["s1-00-normal"], [message, "0"])
        self.assertEqual(texts["s2-00-normal"], [message, "1"])
        self.assertTrue(result.evaluated())

    async def test_passed_root_skips_nothing(self):
        result = await self._write(
            **{"s0-00-sample": "1.0", "s0-01-scr-wa": "1.0"})
        self.assertEqual({e.codename for e in result.evaluations},
                         {"s0-00-sample", "s0-01-scr-wa"})
        self.assertFalse(result.evaluated())

    @patch.object(config.global_, "two_phase_evaluation", True)
    async def test_screening_failure_of_root_skips_dependents_same_batch(
        self,
    ):
        # With two-phase on, the root's screening fails. Its own
        # remaining testcases have none left here (both are screening), and
        # its dependents are skipped by the dependency rule in the same call.
        result = await self._write(
            **{"s0-00-sample": "0.0", "s0-01-scr-wa": "1.0"})
        codenames = {e.codename for e in result.evaluations}
        self.assertEqual(codenames, set(CODENAMES))
        self.assertTrue(result.evaluated())

    async def test_invalid_dependencies_do_not_block_grading(self):
        # A cycle: the gate is off, so nothing is held nor skipped.
        self.dataset.score_type_parameters = [
            {"max_score": 20, "testcases": 2, "depends_on": [1]},
            {"max_score": 30, "testcases": 1, "depends_on": [0]},
            {"max_score": 50, "testcases": 1}]
        self.session.commit()
        result = await self._write(**{c: "1.0" for c in CODENAMES})
        self.assertTrue(result.evaluated())

    async def test_sweeper_takes_the_gated_path_when_dependencies_exist(
        self,
    ):
        service = self._build_service()
        with patch("cms.service.EvaluationService.get_submissions_operations"
                   ) as plain, \
                patch.object(service, "submission_enqueue_operations",
                             return_value=0) as gated:
            service._missing_operations_sync()
        plain.assert_not_called()
        gated.assert_called()
```

Also add a parity test in a second class. It uses the same fixture, but with list parameters `[[20, 2], [30, 1], [50, 1]]`. Writing `"s0-01-scr-wa": "0.0"` with two-phase off must synthesize nothing: the evaluations are exactly the two written. The sweeper, with two-phase off, must call `get_submissions_operations`, the plain path, and not the gated one.

- [ ] **Step 2: Run the tests to see them fail.** Run the file alone, with `CMS_CONFIG` pointing at your private config. Expected: the skip and sweeper tests fail.

- [ ] **Step 3: Implement.** Make these changes in `cms/service/EvaluationService.py`.

1. Add the imports: `from cms.grading import subtaskdag`, and `any_dataset_declares_dependencies` in the `cms.service.esoperations` import list. Also import `Dataset` from `cms.db` if it isn't imported yet.

2. Add `_advance_dependencies` right after `_advance_two_phase`:

```python
    def _advance_dependencies(
        self, session: Session, submission_result: SubmissionResult
    ) -> None:
        """Skip the testcases that only subtasks with a failed dependency need.

        For every subtask that failed because one of its dependencies
        failed (transitively), synthesize a skipped evaluation (outcome 0)
        for each of its testcases not evaluated yet and not needed by
        another subtask, so the submission can complete without running
        them. No-op for datasets that declare no dependency. See
        cms/grading/subtaskdag.py.

        session: the DB session to use.
        submission_result: the submission result to advance.

        """
        dataset = submission_result.dataset
        gate = subtaskdag.gate_for_dataset(dataset)
        if gate is None:
            return
        outcome_by_codename = {
            e.codename: e.outcome for e in submission_result.evaluations}
        status, blocked_by = gate.statuses(outcome_by_codename)
        left = [codename for codename in dataset.testcases
                if codename not in outcome_by_codename]
        skip = gate.skippable(left, status, blocked_by)
        message = EVALUATION_MESSAGES.get("skipped_dependency").message
        for codename, dependency in skip.items():
            submission_result.evaluations += [Evaluation(
                text=[message, str(dependency)],
                outcome="0.0",
                execution_time=0.0,
                execution_wall_clock_time=0.0,
                execution_memory=0,
                evaluation_shard=None,
                evaluation_sandbox_paths=[],
                evaluation_sandbox_digests=[],
                testcase=dataset.testcases[codename])]
        if skip:
            logger.info(
                "Subtask dependencies: synthesized %d skipped evaluation(s) "
                "for submission %d(%d).", len(skip),
                submission_result.submission_id, submission_result.dataset_id)
            session.commit()
```

3. In `submission_enqueue_operations`, after the two-phase block (`if number_of_operations == 0 and twophase.enabled(): …`), add:

```python
            # Subtask dependencies: likewise, the only testcases left may
            # belong to subtasks whose dependency failed and whose skips
            # are not synthesized yet. No-op without dependencies. See
            # cms/grading/subtaskdag.py.
            if number_of_operations == 0 and submission_result is not None:
                self._advance_dependencies(
                    submission_result.sa_session, submission_result)
```

4. In `write_results`, replace the block `if twophase.enabled(): for … self._advance_two_phase(…)` with the code below. It is identical when two-phase is off and no dataset declares dependencies:

```python
            # Two-phase fail-fast and subtask dependencies: synthesize the
            # skipped evaluations that the results just written make
            # certain, two-phase first (a failed screening can make a
            # dependency fail). See cms/grading/twophase.py and
            # cms/grading/subtaskdag.py.
            gated_datasets = set()
            for type_, _, dataset_id, _ in by_object_and_type.keys():
                if type_ == ESOperation.EVALUATION \
                        and dataset_id not in gated_datasets:
                    dataset = Dataset.get_from_id(dataset_id, session)
                    if dataset is not None and \
                            subtaskdag.gate_for_dataset(dataset) is not None:
                        gated_datasets.add(dataset_id)
            if twophase.enabled() or gated_datasets:
                for type_, object_id, dataset_id, _ in \
                        by_object_and_type.keys():
                    if type_ == ESOperation.EVALUATION:
                        submission_result = SubmissionResult.get_from_id(
                            (object_id, dataset_id), session)
                        if submission_result is not None:
                            if twophase.enabled():
                                self._advance_two_phase(
                                    session, submission_result)
                            if dataset_id in gated_datasets:
                                self._advance_dependencies(
                                    session, submission_result)
```

5. In the "Ending operations" loop, change `elif twophase.enabled():` to the code below, and extend the comment to mention dependencies:

```python
                    elif twophase.enabled() or subtaskdag.gate_for_dataset(
                            submission_result.dataset) is not None:
```

6. In `_missing_operations_sync`, change `if twophase.enabled():` to the code below, and add one sentence to the comment. The plain SQL path would also bypass the dependency gate, so the gated path is used whenever a dataset of the contest declares dependencies.

```python
            if twophase.enabled() or any_dataset_declares_dependencies(
                    session, self.contest_id):
```

- [ ] **Step 4: Run the tests to see them pass.** Run the new file. Then run, together in one asyncio-group process:
  - `twophase_write_results_test.py`
  - `twophase_e2e_test.py`
  - `twophase_gate_consistency_test.py`
  - `evaluationservice_failures_test.py`
  - `EvaluationService_test.py`
  - `write_results_creates_result_test.py`

  List the files first, so you only run ones that exist. All must pass unchanged. Run `pyflakes`.

- [ ] **Step 5: Commit.**

```bash
git add cms/service/EvaluationService.py cmstestsuite/unit_tests/service/subtaskdag_write_results_test.py
git commit -m "feat(evaluation): skip the subtasks whose dependency failed, in cascade"
```

---

### Task 6: Functional test with dependencies

**Files:**
- Create: `cmstestsuite/tasks/batch_dag/__init__.py` and `cmstestsuite/tasks/batch_dag/data/{1,2,3}.{in,out}`
- Modify: `cmstestsuite/Tests.py`, adding the import and three `Test(...)` entries next to the `batch_stdio` ones.

**Interfaces:**
- Consumes: everything above. It reuses the existing `correct-stdio.%l`, `half-correct-stdio.%l` and `incorrect-stdio.%l` sources. The half-correct source answers wrongly on even inputs.

- [ ] **Step 1: Create the task.** `cmstestsuite/tasks/batch_dag/__init__.py` gets the license header plus:

```python
task_info = {
    "name": "batchdag",
    "title": "Test Batch Task with subtask dependencies",
    "official_language": "",
    "submission_format_choice": "other",
    "submission_format": "batchdag.%l",
    "time_limit_{{dataset_id}}": "0.5",
    "memory_limit_{{dataset_id}}": "128",
    "task_type_{{dataset_id}}": "Batch",
    "TaskTypeOptions_{{dataset_id}}_Batch_compilation": "alone",
    "TaskTypeOptions_{{dataset_id}}_Batch_io_0_inputfile": "",
    "TaskTypeOptions_{{dataset_id}}_Batch_io_1_outputfile": "",
    "TaskTypeOptions_{{dataset_id}}_Batch_output_eval": "diff",
    "score_type_{{dataset_id}}": "GroupMin",
    # Subtask 0 = testcase 000 (input 2), subtask 1 = 001 (input 1,
    # depends on 0), subtask 2 = 002 (input 3). half-correct fails only
    # even inputs: 0 in subtask 0, so subtask 1 is worth 0 by the
    # dependency (80 without it), 50 from subtask 2.
    "score_type_parameters_{{dataset_id}}":
        '[{"max_score": 20, "testcases": 1},'
        ' {"max_score": 30, "testcases": 1, "depends_on": [0]},'
        ' {"max_score": 50, "testcases": 1}]',
}

test_cases = [
    ("1.in", "1.out", True),
    ("2.in", "2.out", True),
    ("3.in", "3.out", True),
]
```

The data files contain:
- `1.in`: `2`, and `1.out`: `correct 2`;
- `2.in`: `1`, and `2.out`: `correct 1`;
- `3.in`: `3`, and `3.out`: `correct 3`.

Each file ends with a newline, like `cmstestsuite/tasks/batch_stdio/data/`. The framework names testcases `%03d` by position (`functionaltestframework.py:389`), so the files map to `000`, `001` and `002`.

- [ ] **Step 2: Add the tests** in `cmstestsuite/Tests.py`. Import `cmstestsuite.tasks.batch_dag as batch_dag` next to `batch_stdio`, and add the entries after the `batch_stdio` block:

```python
    # Subtask dependencies (GroupMin with depends_on).

    Test('correct-dag',
         task=batch_dag, filenames=['correct-stdio.%l'],
         languages=(LANG_PYTHON3,),
         checks=[CheckOverallScore(100, 100)]),

    Test('half-correct-dag',
         task=batch_dag, filenames=['half-correct-stdio.%l'],
         languages=(LANG_PYTHON3,),
         checks=[CheckOverallScore(50, 100)]),

    Test('incorrect-dag',
         task=batch_dag, filenames=['incorrect-stdio.%l'],
         languages=(LANG_PYTHON3,),
         checks=[CheckOverallScore(0, 100)]),
```

Match the tuple or list style used by the surrounding `languages=` arguments.

- [ ] **Step 3: Validate** with one full local CI run on rootful Docker, **alone**: no other `cmsci-*` project may be running. Use a `git archive` snapshot of the branch, the `docker-compose.test.yml` `testcms` service with `BASE_IMAGE=ubuntu:noble`, a unique compose project name, and teardown with `down -v`. The functional run must report every test passed, including the three new ones.
  - **Negative check.** In a scratch copy only, remove the `depends_on`. `half-correct-dag` must then fail with 80.

- [ ] **Step 4: Commit.**

```bash
git add cmstestsuite/tasks/batch_dag cmstestsuite/Tests.py
git commit -m "test(functional): grade a task with subtask dependencies"
```

---

### Task 7: Operator documentation

**Files:**
- Create: `docs/subtask-dependencies.md`
- Modify: `README.md`, adding one row in the feature table next to "AWS user import".

- [ ] **Step 1: Write `docs/subtask-dependencies.md`** in English, in the style of `docs/importing-users.md`. It covers:
  - **What it does.** A subtask whose dependency scored 0 is worth 0 and is not graded. Independent subtasks keep their own score. All dependencies must pass. A partial score passes, and so does a 0-point examples subtask whose testcases pass. The rule is transitive.
  - **How to declare it.** Use the dict form of the score type parameters in AWS (dataset page → score type parameters) or in `task.yaml`. Give the exact JSON example from the spec, with numbers from 0 as CWS shows them ("Subtarea 0"). The list form cannot carry dependencies.
  - **The errors AWS shows when saving:** the 5 validation cases, with the text of `parse_dependencies`.
  - **What the contestant sees.** Quote both Spanish texts verbatim from `cms.po`.
  - **How it combines with two-phase screening.** Screening is still declared by testcase codenames (`sN-nn-sample`, `-scr-`…). A subtask released by its dependencies screens first. Codename groups and score type subtasks must agree.
  - **Latency.** A correct submission's full score arrives later on deep dependency chains.
  - **Changing dependencies after submissions.** Scores follow the new rule once rescored. Testcases already marked "No se probó" need a re-evaluation of the task from AWS.
  - **Parity.** Tasks without `depends_on` are graded exactly as before.
  - **Custom score types.** A group score type used with dependencies must have a `reduce` that never goes back up as more outcomes arrive.

  Keep every quoted string verbatim with the code.

- [ ] **Step 2:** Add the README row: `| Subtask dependencies | A subtask whose prerequisite scored 0 is worth 0 and is not graded | [docs/subtask-dependencies.md](docs/subtask-dependencies.md) |`, with the same columns as its neighbours.

- [ ] **Step 3: Commit.**

```bash
git add docs/subtask-dependencies.md README.md
git commit -m "docs: explain subtask dependencies"
```

---

### Task 8: End-to-end check in Chromium (no code)

Run it on the local `mc2-e2e` stack, rebuilt from the branch tip in deploy order: `ranking` first, then `cms` plus `db-init`. The procedure and the CDP driver are in the beta worktree's `.superpowers/scratch/e2e-stack/` and `e2e-browser/`. Record the results in the ledger.

1. **Setup.** In AWS, give a contest task the dict-form parameters with `depends_on`. Use codenames that follow the screening convention, and add a contestant.
2. **Wrong root.** Submit a solution that fails subtask 0.
   - CWS shows subtask 1 at 0.
   - Its testcases show "No se probó: esta subtarea depende de la subtarea 0, que no obtuvo puntos.".
   - The subtask shows the note "Vale 0 porque depende de la subtarea 0, que no obtuvo puntos." only if it was evaluated.
   - An independent subtask keeps its score.
3. **Correct solution.** It gets the full score.
4. **Ranking.** The contest's ranking group shows the per-subtask scores with the rule applied.
5. **AWS validation.** Saving parameters with a cycle shows "Invalid score type parameters" with the cycle message, and the dataset keeps its previous parameters.
6. **Logs.** No JS errors, no 5xx, and no ERROR in the ES, Worker or AWS logs. The "Subtask dependencies: synthesized …" INFO line appears.
7. **Shutdown.** Stop the stack and the test Chromium afterwards.
