# Two-phase / fail-fast grading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port COMIGuide's two-phase fail-fast grading behavior (screen a few testcases per subtask first; skip the rest of that subtask if screening fails) into this fork's own source tree, gated behind a flag that defaults to off, with zero behavior change when the flag is off.

**Architecture:** A new pure-logic module (`cms/grading/twophase.py`) identifies screening testcases and per-group pass/fail status from testcase codenames (`<group>-<nn>-<tag>` convention). `cms/service/esoperations.py`'s `submission_get_operations()` consults it to withhold non-screening evaluation operations until their group's screening passes. `cms/service/EvaluationService.py`'s `write_results()` consults it after committing new results to (a) synthesize skipped evaluations for testcases in a group whose screening failed, and (b) release (re-enqueue) a group's real operations once its screening passes. A new `two_phase_evaluation` config flag (default `false`) wires through `cms/conf.py` and `docker/generate_config.py`/`.env.example`.

**Tech Stack:** Python 3.11+, SQLAlchemy (CMS's DB layer), gevent (CMS's service runtime), pytest/unittest (`cmstestsuite/unit_tests/`).

**Spec:** `docs/superpowers/specs/2026-09-21-two-phase-grading-design.md`

## Global Constraints

- Default behavior (flag off) must be byte-for-byte identical to current grading — no new DB queries, commits, or enqueue calls on the flag-off path.
- No DB schema migration.
- No changes to `docker-compose.prod.yml` or the `Dockerfile`.
- Screening/group identification is entirely by testcase codename convention (`<group>-<nn>-<tag>`; screening = tag is `sample` or contains `scr`); no new DB column.
- `pyflakes` clean on every touched file.

---

### Task 1: `cms/grading/twophase.py` — screening logic module

**Files:**
- Create: `cms/grading/twophase.py`
- Test: `cmstestsuite/unit_tests/grading/twophase_test.py`

**Interfaces:**
- Produces: `twophase.enabled() -> bool`, `twophase.group_of(codename: str) -> str`, `twophase.is_screening(codename: str) -> bool`, `twophase.group_screening_status(dataset, outcome_by_codename: dict[str, str | None]) -> dict[str, str]` (values are `"pending"`, `"passed"`, or `"failed"`; a group with no screening testcases is absent from the result). `dataset` only needs a `.testcases` attribute that is a `{codename: object}` mapping (matches `cms.db.Dataset.testcases`).
- Consumes: `cms.config.global_.two_phase_evaluation: bool` (added in Task 2 — until Task 2 lands, `enabled()`'s test must patch this attribute directly on the `GlobalConfig` instance, which already exists with today's fields; add the new field in Task 2 before merging this module's behavior end-to-end, but the module itself only reads `config.global_.two_phase_evaluation` via `getattr`-free direct attribute access, so Task 1's test patches it in with `unittest.mock.patch.object`, which works whether or not the dataclass field exists yet — MonkeyPatching an attribute onto a dataclass instance is legal even before Task 2 adds the field, so Task 1 can be implemented and tested independently of Task 2's ordering. To avoid any ordering ambiguity, implement Task 2 first if working sequentially).

- [ ] **Step 1: Write the failing test**

Create `cmstestsuite/unit_tests/grading/twophase_test.py`:

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

"""Tests for the two-phase (fail-fast screening) grading gate."""

import unittest
from unittest.mock import patch

from cms import config
from cms.grading import twophase


class FakeDataset:
    """Minimal stand-in for cms.db.Dataset: only needs .testcases."""

    def __init__(self, codenames):
        self.testcases = {codename: object() for codename in codenames}


class TestEnabled(unittest.TestCase):

    def test_default_is_false(self):
        self.assertFalse(twophase.enabled())

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_true_when_configured(self):
        self.assertTrue(twophase.enabled())


class TestGroupOf(unittest.TestCase):

    def test_group_before_first_dash(self):
        self.assertEqual(twophase.group_of("s1-00-sample"), "s1")
        self.assertEqual(twophase.group_of("s1-01-scr-wa"), "s1")

    def test_no_dash_falls_in_global_group(self):
        self.assertEqual(twophase.group_of("000"), "")


class TestIsScreening(unittest.TestCase):

    def test_sample_is_screening(self):
        self.assertTrue(twophase.is_screening("s1-00-sample"))

    def test_scr_tag_is_screening(self):
        self.assertTrue(twophase.is_screening("s1-01-scr-wa"))
        self.assertTrue(twophase.is_screening("s1-02-scr-tle"))

    def test_normal_testcase_is_not_screening(self):
        self.assertFalse(twophase.is_screening("s1-03-normal"))
        self.assertFalse(twophase.is_screening("000"))


class TestGroupScreeningStatus(unittest.TestCase):

    def setUp(self):
        self.dataset = FakeDataset([
            "s1-00-sample", "s1-01-scr-wa", "s1-02-normal",
            "s2-00-sample", "s2-01-normal",
        ])

    def test_group_with_no_evaluations_yet_is_pending(self):
        status = twophase.group_screening_status(self.dataset, {})
        self.assertEqual(status, {"s1": "pending", "s2": "pending"})

    def test_group_pending_until_all_screening_done(self):
        status = twophase.group_screening_status(
            self.dataset, {"s1-00-sample": "1.0"})
        self.assertEqual(status["s1"], "pending")

    def test_group_passed_when_all_screening_positive(self):
        status = twophase.group_screening_status(
            self.dataset,
            {"s1-00-sample": "1.0", "s1-01-scr-wa": "1.0",
             "s2-00-sample": "1.0"})
        self.assertEqual(status, {"s1": "passed", "s2": "passed"})

    def test_group_failed_when_any_screening_non_positive(self):
        status = twophase.group_screening_status(
            self.dataset,
            {"s1-00-sample": "1.0", "s1-01-scr-wa": "0.0",
             "s2-00-sample": "1.0"})
        self.assertEqual(status, {"s1": "failed", "s2": "passed"})

    def test_group_without_screening_testcases_is_absent(self):
        dataset = FakeDataset(["000", "001"])
        status = twophase.group_screening_status(dataset, {})
        self.assertEqual(status, {})

    def test_non_numeric_outcome_counts_as_not_passed(self):
        status = twophase.group_screening_status(
            self.dataset,
            {"s1-00-sample": None, "s1-01-scr-wa": "1.0",
             "s2-00-sample": "1.0"})
        self.assertEqual(status["s1"], "failed")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/grading/twophase_test.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'cms.grading.twophase'`

- [ ] **Step 3: Write minimal implementation**

Create `cms/grading/twophase.py`:

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


def is_screening(codename: str) -> bool:
    """Return whether a testcase is a screening testcase."""
    return "sample" in codename or "scr" in codename


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/grading/twophase_test.py -v`
Expected: PASS (all tests). Note: `test_default_is_false` requires `config.global_.two_phase_evaluation` to already exist as an attribute — if Task 2 hasn't landed yet, this specific test fails with `AttributeError`. Do Task 2 first, or accept that ordering — see Note below.

- [ ] **Step 5: Run pyflakes**

Run: `.venv/bin/pyflakes cms/grading/twophase.py cmstestsuite/unit_tests/grading/twophase_test.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add cms/grading/twophase.py cmstestsuite/unit_tests/grading/twophase_test.py
git commit -m "feat(grading): add two-phase fail-fast screening logic module"
```

**Note on task order:** `enabled()` reads `config.global_.two_phase_evaluation`, which doesn't exist until Task 2 adds it to `GlobalConfig`. Do Task 2 before Task 1 (or before running Task 1's tests) so `config.global_.two_phase_evaluation` exists as a real dataclass field with its documented default, not just a mock-patched attribute. The step order above is written task-by-task per the plan's numbering, but the executor should do Task 2 first in practice.

---

### Task 2: `two_phase_evaluation` config flag — `cms/conf.py`, `docker/generate_config.py`, `.env.example`

**Files:**
- Modify: `cms/conf.py:67-77` (`GlobalConfig` dataclass)
- Modify: `docker/generate_config.py` (add env var parsing + write to `[global]` TOML section)
- Modify: `.env.example` (document the new var)

**Interfaces:**
- Produces: `config.global_.two_phase_evaluation: bool` (default `False`), consumed by `cms/grading/twophase.py` (Task 1).
- Consumes: nothing from other tasks.

- [ ] **Step 1: Add the field to `GlobalConfig`**

In `cms/conf.py`, current lines 67-77:

```python
@dataclass()
class GlobalConfig:
    temp_dir: str = "/tmp"
    backdoor: bool = False
    file_log_debug: bool = False
    stream_log_detailed: bool = False
    log_dir: str = default_path("log")
    cache_dir: str = default_path("cache")
    data_dir: str = default_path("lib")
    run_dir: str = default_path("run")
```

Change to:

```python
@dataclass()
class GlobalConfig:
    temp_dir: str = "/tmp"
    backdoor: bool = False
    file_log_debug: bool = False
    stream_log_detailed: bool = False
    two_phase_evaluation: bool = False
    log_dir: str = default_path("log")
    cache_dir: str = default_path("cache")
    data_dir: str = default_path("lib")
    run_dir: str = default_path("run")
```

- [ ] **Step 2: Verify existing config tests still pass**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/ -k conf -v`
Expected: PASS (no test currently asserts the exact field set of `GlobalConfig`; if one does and fails, extend its expected fields to include `two_phase_evaluation`, don't drop the new field).

- [ ] **Step 3: Wire the env var into `docker/generate_config.py`**

In `docker/generate_config.py`, inside `generate_cms_toml()`, current lines 56-63:

```python
    raw_log = _get("CMS_LOG_DEBUG", "false").lower()
    if raw_log not in ("true", "false"):
        print(
            f"ERROR: CMS_LOG_DEBUG must be 'true' or 'false', got {raw_log!r}.",
            file=sys.stderr,
        )
        sys.exit(1)
    log_debug = raw_log
```

Add immediately after (still inside `generate_cms_toml()`):

```python

    raw_two_phase = _get("CMS_TWO_PHASE_EVALUATION", "false").lower()
    if raw_two_phase not in ("true", "false"):
        print(
            f"ERROR: CMS_TWO_PHASE_EVALUATION must be 'true' or 'false', "
            f"got {raw_two_phase!r}.",
            file=sys.stderr,
        )
        sys.exit(1)
    two_phase_evaluation = raw_two_phase
```

Then, current lines 98-101:

```python
    toml = f"""\
[global]
file_log_debug = {log_debug}
stream_log_detailed = false
```

Change to:

```python
    toml = f"""\
[global]
file_log_debug = {log_debug}
stream_log_detailed = false
two_phase_evaluation = {two_phase_evaluation}
```

- [ ] **Step 4: Document the var in `.env.example`**

In `.env.example`, current lines 201-206:

```
# -----------------------------------------------------------
# DIAGNOSTICS (optional)
# -----------------------------------------------------------

# Set to true to write debug-level entries to the service log files.
CMS_LOG_DEBUG=false
```

Change to:

```
# -----------------------------------------------------------
# DIAGNOSTICS (optional)
# -----------------------------------------------------------

# Set to true to write debug-level entries to the service log files.
CMS_LOG_DEBUG=false

# -----------------------------------------------------------
# GRADING (optional)
# -----------------------------------------------------------

# Set to true to enable two-phase fail-fast evaluation: for each subtask,
# a small set of "screening" testcases (identified by codename — sample or
# "scr" in the tag, e.g. s1-00-sample, s1-01-scr-wa) is evaluated first.
# The rest of that subtask's testcases only run if screening passes; if it
# fails, they are recorded as skipped (outcome 0) without running, saving
# grading time. Off by default; safe to leave off unless your task
# testcases follow the <group>-<nn>-<tag> codename convention.
CMS_TWO_PHASE_EVALUATION=false
```

- [ ] **Step 5: Verify by running the generator script manually**

```bash
.venv/bin/python3 -c "
import os
os.environ['CMS_DB_URL']='postgresql+psycopg2://a:b@db/c'
os.environ['CMS_SECRET_KEY']='x'*32
import sys
sys.path.insert(0,'docker')
import generate_config as g
print([l for l in g.generate_cms_toml().splitlines() if 'two_phase' in l])
os.environ['CMS_TWO_PHASE_EVALUATION']='true'
import importlib; importlib.reload(g)
print([l for l in g.generate_cms_toml().splitlines() if 'two_phase' in l])
"
```

Expected output:
```
['two_phase_evaluation = false']
['two_phase_evaluation = true']
```

- [ ] **Step 6: Run pyflakes**

Run: `.venv/bin/pyflakes cms/conf.py docker/generate_config.py`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add cms/conf.py docker/generate_config.py .env.example
git commit -m "feat(config): add two_phase_evaluation flag (CMS_TWO_PHASE_EVALUATION)"
```

---

### Task 3: Gate evaluation operations on screening status — `cms/service/esoperations.py`

**Files:**
- Modify: `cms/service/esoperations.py:35-38` (imports), `:191-211` (`submission_get_operations`)
- Test: `cmstestsuite/unit_tests/service/twophase_esoperations_test.py`

**Interfaces:**
- Consumes: `twophase.enabled()`, `twophase.group_of()`, `twophase.is_screening()`, `twophase.group_screening_status()` from Task 1's `cms.grading.twophase`; `config.global_.two_phase_evaluation` from Task 2.
- Produces: no new public interface — `submission_get_operations()`'s existing signature and yield type are unchanged; only its withheld/released set of operations changes when the flag is on.

- [ ] **Step 1: Write the failing test**

Create `cmstestsuite/unit_tests/service/twophase_esoperations_test.py`:

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

"""Tests for the two-phase screening gate in submission_get_operations()."""

import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.service.esoperations import ESOperation, submission_get_operations


class TestSubmissionGetOperationsTwoPhase(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(task=self.task, autojudge=True)
        self.task.active_dataset = self.dataset
        self.testcases = {
            codename: self.add_testcase(self.dataset, codename=codename)
            for codename in [
                "s1-00-sample", "s1-01-scr-wa", "s1-02-normal",
                "s2-00-sample", "s2-01-normal",
            ]
        }
        self.session.flush()

    def _evaluation_codenames(self, submission_result, submission):
        return set(
            op.testcase_codename
            for op, _, _ in submission_get_operations(
                submission_result, submission, self.dataset)
            if op.type_ == ESOperation.EVALUATION)

    def test_disabled_yields_all_unevaluated_testcases(self):
        submission, results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.session.flush()

        self.assertEqual(
            self._evaluation_codenames(results[0], submission),
            set(self.testcases.keys()))

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_enabled_withholds_non_screening_until_screening_passes(self):
        submission, results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.session.flush()

        self.assertEqual(
            self._evaluation_codenames(results[0], submission),
            {"s1-00-sample", "s1-01-scr-wa", "s2-00-sample"})

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_enabled_releases_group_once_its_screening_passes(self):
        submission, results = self.add_submission_with_results(
            self.task, self.participation, True)
        result = results[0]
        self.add_evaluation(
            result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            result, self.testcases["s1-01-scr-wa"], outcome="1.0")
        self.session.flush()

        self.assertEqual(
            self._evaluation_codenames(result, submission),
            {"s1-02-normal", "s2-00-sample"})

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_enabled_withholds_group_whose_screening_failed(self):
        submission, results = self.add_submission_with_results(
            self.task, self.participation, True)
        result = results[0]
        self.add_evaluation(
            result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            result, self.testcases["s1-01-scr-wa"], outcome="0.0")
        self.session.flush()

        self.assertEqual(
            self._evaluation_codenames(result, submission),
            {"s2-00-sample"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/service/twophase_esoperations_test.py -v`
Expected: `test_disabled_yields_all_unevaluated_testcases` PASSES already (current behavior); the three `two_phase` tests FAIL because nothing gates operations yet (they'll get the full testcase set instead of the filtered one).

- [ ] **Step 3: Implement the gate**

In `cms/service/esoperations.py`, current lines 35-38:

```python
from cms.db import Dataset, Evaluation, Submission, SubmissionResult, \
    Task, Testcase, UserTest, UserTestResult
from cms.db.session import Session
from cms.io import PriorityQueue, QueueItem
```

Change to:

```python
from cms.db import Dataset, Evaluation, Submission, SubmissionResult, \
    Task, Testcase, UserTest, UserTestResult
from cms.db.session import Session
from cms.grading import twophase
from cms.io import PriorityQueue, QueueItem
```

Then, in `submission_get_operations()`, current lines 191-211:

```python
    elif submission_to_evaluate(submission_result):
        if not dataset.active:
            priority = PriorityQueue.PRIORITY_EXTRA_LOW
        elif submission_result.evaluation_tries == 0:
            priority = PriorityQueue.PRIORITY_MEDIUM
        else:
            priority = PriorityQueue.PRIORITY_LOW

        evaluated_testcase_ids = set(
            evaluation.testcase_id
            for evaluation in submission_result.evaluations)
        for testcase_codename in dataset.testcases.keys():
            testcase_id = dataset.testcases[testcase_codename].id
            if testcase_id not in evaluated_testcase_ids:
                yield ESOperation(ESOperation.EVALUATION,
                                  submission.id,
                                  dataset.id,
                                  testcase_codename,
                                  archive_sandbox=archive_sandbox), \
                    priority, \
                    submission.timestamp
```

Change to:

```python
    elif submission_to_evaluate(submission_result):
        if not dataset.active:
            priority = PriorityQueue.PRIORITY_EXTRA_LOW
        elif submission_result.evaluation_tries == 0:
            priority = PriorityQueue.PRIORITY_MEDIUM
        else:
            priority = PriorityQueue.PRIORITY_LOW

        evaluated_testcase_ids = set(
            evaluation.testcase_id
            for evaluation in submission_result.evaluations)

        # Two-phase fail-fast: withhold a group's non-screening testcases
        # until that group's screening testcases have all been evaluated
        # and passed. See cms/grading/twophase.py.
        if twophase.enabled():
            outcome_by_codename = {
                evaluation.codename: evaluation.outcome
                for evaluation in submission_result.evaluations}
            screening_status = twophase.group_screening_status(
                dataset, outcome_by_codename)
        else:
            screening_status = None

        for testcase_codename in dataset.testcases.keys():
            testcase_id = dataset.testcases[testcase_codename].id
            if testcase_id in evaluated_testcase_ids:
                continue
            if screening_status is not None \
                    and not twophase.is_screening(testcase_codename):
                group = twophase.group_of(testcase_codename)
                if screening_status.get(group, "passed") != "passed":
                    continue
            yield ESOperation(ESOperation.EVALUATION,
                              submission.id,
                              dataset.id,
                              testcase_codename,
                              archive_sandbox=archive_sandbox), \
                priority, \
                submission.timestamp
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/service/twophase_esoperations_test.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 5: Run the full esoperations test file to check for regressions**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/service/esoperations_test.py -v`
Expected: PASS (unchanged — flag defaults off, so `submission_get_operations` behaves exactly as before for these tests).

- [ ] **Step 6: Run pyflakes**

Run: `.venv/bin/pyflakes cms/service/esoperations.py cmstestsuite/unit_tests/service/twophase_esoperations_test.py`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add cms/service/esoperations.py cmstestsuite/unit_tests/service/twophase_esoperations_test.py
git commit -m "feat(es): gate evaluation operations on two-phase screening status"
```

---

### Task 4: Synthesize skipped evaluations for failed groups — `EvaluationService._advance_two_phase`

**Files:**
- Modify: `cms/service/EvaluationService.py:42-56` (imports), add new method after `write_results_one_object_and_type` (currently ends around line 617, i.e. right before `write_results_one_row` at line 620 in the pre-Task-5 file)
- Test: `cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py`

**Interfaces:**
- Consumes: `twophase.enabled()`, `twophase.group_of()`, `twophase.is_screening()`, `twophase.group_screening_status()` from Task 1.
- Produces: `EvaluationService._advance_two_phase(self, session, submission_result) -> None` — for Task 5's `write_results()` call sites. Synthesizes an `Evaluation(text=["Saltado tras fallo en la fase de tamizaje"], outcome="0.0", execution_time=0.0, execution_wall_clock_time=0.0, execution_memory=0, evaluation_shard=None, evaluation_sandbox_paths=[], evaluation_sandbox_digests=[], testcase=testcase)` for every non-screening, not-yet-evaluated testcase of a group whose screening failed, and commits if it created any.

- [ ] **Step 1: Write the failing test**

Create `cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py`:

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

"""Tests for EvaluationService's two-phase skip synthesis."""

# We enable monkey patching to make many libraries gevent-friendly.
import gevent.monkey

gevent.monkey.patch_all()  # noqa

import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.service.EvaluationService import EvaluationService


class TestAdvanceTwoPhase(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(task=self.task, autojudge=True)
        self.task.active_dataset = self.dataset
        self.testcases = {
            codename: self.add_testcase(self.dataset, codename=codename)
            for codename in [
                "s1-00-sample", "s1-01-scr-wa", "s1-02-normal", "s1-03-normal",
            ]
        }
        self.submission, self.results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.result = self.results[0]
        self.session.flush()

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_synthesizes_skipped_evaluations_for_failed_group(self):
        self.add_evaluation(
            self.result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            self.result, self.testcases["s1-01-scr-wa"], outcome="0.0")
        self.session.flush()

        service = EvaluationService(0)
        service._advance_two_phase(self.session, self.result)

        codenames = {e.codename for e in self.result.evaluations}
        self.assertEqual(
            codenames,
            {"s1-00-sample", "s1-01-scr-wa", "s1-02-normal", "s1-03-normal"})
        for codename in ("s1-02-normal", "s1-03-normal"):
            evaluation = next(
                e for e in self.result.evaluations if e.codename == codename)
            self.assertEqual(evaluation.outcome, "0.0")
            self.assertEqual(evaluation.evaluation_sandbox_paths, [])
            self.assertEqual(evaluation.evaluation_sandbox_digests, [])

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_no_op_while_screening_still_pending(self):
        self.add_evaluation(
            self.result, self.testcases["s1-00-sample"], outcome="1.0")
        self.session.flush()

        service = EvaluationService(0)
        service._advance_two_phase(self.session, self.result)

        codenames = {e.codename for e in self.result.evaluations}
        self.assertEqual(codenames, {"s1-00-sample"})

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_no_op_when_screening_passed(self):
        self.add_evaluation(
            self.result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            self.result, self.testcases["s1-01-scr-wa"], outcome="1.0")
        self.session.flush()

        service = EvaluationService(0)
        service._advance_two_phase(self.session, self.result)

        codenames = {e.codename for e in self.result.evaluations}
        self.assertEqual(codenames, {"s1-00-sample", "s1-01-scr-wa"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py -v`
Expected: FAIL with `AttributeError: 'EvaluationService' object has no attribute '_advance_two_phase'`.

- [ ] **Step 3: Add the import**

In `cms/service/EvaluationService.py`, current lines 49-50:

```python
from cms.grading.Job import Job, JobGroup
from cms.io import Executor, TriggeredService, rpc_method
```

Change to:

```python
from cms.grading import twophase
from cms.grading.Job import Job, JobGroup
from cms.io import Executor, TriggeredService, rpc_method
```

- [ ] **Step 4: Add the `_advance_two_phase` method**

In `cms/service/EvaluationService.py`, insert this new method immediately after `write_results_one_object_and_type` ends (right before the `def write_results_one_row(...)` method, currently at line 620 in the file as it stands before this task's edit — locate it by searching for `def write_results_one_row`):

```python
    def _advance_two_phase(self, session, submission_result):
        """Two-phase fail-fast bookkeeping for one submission result.

        For every group whose screening testcases are all evaluated and at
        least one did not pass, synthesize a skipped evaluation (outcome 0)
        for each of that group's remaining, non-screening testcases. This
        lets the submission reach a complete evaluation (and hence be
        scored) with the score type naturally scoring the failed group at
        0, without spending CPU on the skipped testcases. No-op unless
        two-phase is enabled, and no-op for groups still pending or already
        passed screening.

        session: the DB session to use.
        submission_result: the submission result to advance.

        """
        dataset = submission_result.dataset
        outcome_by_codename = {
            e.codename: e.outcome for e in submission_result.evaluations}
        status = twophase.group_screening_status(dataset, outcome_by_codename)
        evaluated_ids = {
            e.testcase_id for e in submission_result.evaluations}

        created = 0
        for codename, testcase in dataset.testcases.items():
            if testcase.id in evaluated_ids or twophase.is_screening(codename):
                continue
            if status.get(twophase.group_of(codename), "passed") == "failed":
                submission_result.evaluations += [Evaluation(
                    text=["Saltado tras fallo en la fase de tamizaje"],
                    outcome="0.0",
                    execution_time=0.0,
                    execution_wall_clock_time=0.0,
                    execution_memory=0,
                    evaluation_shard=None,
                    evaluation_sandbox_paths=[],
                    evaluation_sandbox_digests=[],
                    testcase=testcase)]
                created += 1

        if created:
            logger.info(
                "Two-phase: synthesized %d skipped evaluation(s) for "
                "submission %d(%d).", created,
                submission_result.submission_id, submission_result.dataset_id)
            session.commit()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py -v`
Expected: PASS (all 3 tests).

- [ ] **Step 6: Run pyflakes**

Run: `.venv/bin/pyflakes cms/service/EvaluationService.py cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add cms/service/EvaluationService.py cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py
git commit -m "feat(es): add EvaluationService._advance_two_phase skip synthesis"
```

---

### Task 5: Wire `_advance_two_phase` and phase-2 release into `write_results()`

**Files:**
- Modify: `cms/service/EvaluationService.py:499-582` (`write_results`)
- Test: `cmstestsuite/unit_tests/service/twophase_reenqueue_test.py`

**Interfaces:**
- Consumes: `EvaluationService._advance_two_phase` (Task 4), `twophase.enabled()` (Task 1), `EvaluationService.submission_enqueue_operations` (pre-existing, `cms/service/EvaluationService.py:305`).
- Produces: no new public interface — `write_results()`'s signature is unchanged; this task only changes what happens inside it when the flag is on.

- [ ] **Step 1: Write the failing test**

Create `cmstestsuite/unit_tests/service/twophase_reenqueue_test.py`:

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

"""Tests that a group's phase-2 operations are released once its screening
passes, via EvaluationService.submission_enqueue_operations (the same call
write_results() makes for two-phase submissions).

"""

import gevent.monkey

gevent.monkey.patch_all()  # noqa

import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService


class TestTwoPhaseReEnqueue(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(task=self.task, autojudge=True)
        self.task.active_dataset = self.dataset
        self.testcases = {
            codename: self.add_testcase(self.dataset, codename=codename)
            for codename in ["s1-00-sample", "s1-01-scr-wa", "s1-02-normal"]
        }
        self.submission, self.results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.result = self.results[0]
        self.session.flush()

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_phase_two_operation_released_after_screening_passes(self):
        self.add_evaluation(
            self.result, self.testcases["s1-00-sample"], outcome="1.0")
        self.add_evaluation(
            self.result, self.testcases["s1-01-scr-wa"], outcome="1.0")
        self.session.flush()

        service = EvaluationService(0)
        service.submission_enqueue_operations(self.submission)

        expected = ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            "s1-02-normal")
        self.assertIn(expected, service.get_executor())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it passes already (sanity check on Task 3)**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/service/twophase_reenqueue_test.py -v`
Expected: PASS already — Task 3's gate already makes `submission_get_operations` (and hence `submission_enqueue_operations`, which just calls it) yield `s1-02-normal` once screening has passed. This test doesn't depend on this task's `write_results()` change; it exists to lock in the release behavior at the level `write_results()` will call. Confirm it passes before continuing, so the next step's `write_results()` wiring is validated against something already green.

- [ ] **Step 3: Wire `_advance_two_phase` and the phase-2 release into `write_results()`**

In `cms/service/EvaluationService.py`, current lines 541-543:

```python
            logger.info("Committing evaluations...")
            session.commit()

            num_testcases_per_dataset = dict()
```

Change to:

```python
            logger.info("Committing evaluations...")
            session.commit()

            # Two-phase fail-fast: for any group whose screening just
            # finished and failed, synthesize skipped evaluations for its
            # remaining testcases so the submission can complete without
            # running them. See cms/grading/twophase.py.
            if twophase.enabled():
                for type_, object_id, dataset_id, _ in by_object_and_type.keys():
                    if type_ == ESOperation.EVALUATION:
                        submission_result = SubmissionResult.get_from_id(
                            (object_id, dataset_id), session)
                        if submission_result is not None:
                            self._advance_two_phase(session, submission_result)

            num_testcases_per_dataset = dict()
```

Then, current lines 570-574:

```python
                elif type_ == ESOperation.EVALUATION:
                    submission_result = SubmissionResult.get_from_id(
                        (object_id, dataset_id), session)
                    if submission_result.evaluated():
                        self.evaluation_ended(submission_result, archive_sandbox)
```

Change to:

```python
                elif type_ == ESOperation.EVALUATION:
                    submission_result = SubmissionResult.get_from_id(
                        (object_id, dataset_id), session)
                    if submission_result.evaluated():
                        self.evaluation_ended(submission_result, archive_sandbox)
                    elif twophase.enabled():
                        # Two-phase: some group's screening just passed;
                        # push its now-unblocked operations.
                        self.submission_enqueue_operations(
                            submission_result.submission, archive_sandbox)
```

- [ ] **Step 4: Run the full EvaluationService-related test suite**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/service/ -v`
Expected: PASS — all pre-existing service tests plus Tasks 3-5's new tests. The `elif twophase.enabled():` branch is unreachable on the default flag-off path, so no pre-existing test's behavior changes.

- [ ] **Step 5: Run pyflakes**

Run: `.venv/bin/pyflakes cms/service/EvaluationService.py cmstestsuite/unit_tests/service/twophase_reenqueue_test.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add cms/service/EvaluationService.py cmstestsuite/unit_tests/service/twophase_reenqueue_test.py
git commit -m "feat(es): wire two-phase skip synthesis and phase-2 release into write_results"
```

---

### Task 6: Full verification and close out issue #2

**Files:** none created/modified — verification only.

**Interfaces:** none.

- [ ] **Step 1: Run pyflakes across every touched file**

```bash
.venv/bin/pyflakes cms/grading/twophase.py cms/conf.py docker/generate_config.py \
  cms/service/esoperations.py cms/service/EvaluationService.py \
  cmstestsuite/unit_tests/grading/twophase_test.py \
  cmstestsuite/unit_tests/service/twophase_esoperations_test.py \
  cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py \
  cmstestsuite/unit_tests/service/twophase_reenqueue_test.py
```

Expected: no output.

- [ ] **Step 2: Run the full grading and service unit test directories**

```bash
.venv/bin/pytest cmstestsuite/unit_tests/grading/ cmstestsuite/unit_tests/service/ -v
```

Expected: PASS, no failures, no errors.

- [ ] **Step 3: Run the full unit test suite to check for unrelated regressions**

```bash
.venv/bin/pytest cmstestsuite/unit_tests/ -q
```

Expected: same pass/fail counts as on `main` before this branch (no new failures introduced).

- [ ] **Step 4: Manual end-to-end sanity check of the generated config**

```bash
CMS_DB_URL="postgresql+psycopg2://a:b@db/c" CMS_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(16))')" \
  CMS_TWO_PHASE_EVALUATION=true .venv/bin/python3 docker/generate_config.py 2>&1 | tail -5
```

Expected: script exits 0, prints `Generated ...` lines (adjust `CMS_CONFIG`/output paths as needed if running outside a container — the goal is just confirming the script doesn't crash with the new var set).

- [ ] **Step 5: Push and comment on issue #2**

```bash
git push origin main
gh issue comment 2 --repo AresLOLXD/cms --body "$(cat <<'EOF'
Portado en las tareas de docs/superpowers/plans/2026-09-21-two-phase-grading.md (commits en esta rama).

- cms/grading/twophase.py: misma lógica que el pristine de COMIGuide (convención de codename, group_screening_status), adaptada a config.global_.two_phase_evaluation.
- cms/conf.py + docker/generate_config.py + .env.example: nueva flag CMS_TWO_PHASE_EVALUATION (default false, sin cambio de comportamiento).
- cms/service/esoperations.py: compuerta de tamizaje en submission_get_operations().
- cms/service/EvaluationService.py: _advance_two_phase() sintetiza evaluaciones "Saltado" para grupos con tamizaje fallido; write_results() libera las operaciones de fase 2 cuando el tamizaje pasa.

Sin migración de esquema, sin cambios a docker-compose.prod.yml/Dockerfile — el patch queda horneado en la imagen vía el árbol de fuente normal. Cobertura de tests nueva en cmstestsuite/unit_tests/grading/twophase_test.py y cmstestsuite/unit_tests/service/twophase_*.py; pyflakes limpio en todos los archivos tocados.

Cierro este issue.
EOF
)"
gh issue close 2 --repo AresLOLXD/cms --reason completed
```

---

## Plan self-review notes

- **Spec coverage:** all 5 files from the spec's "Design" section have a task (Tasks 1-5); config wiring (§5) is Task 2; codename convention compatibility (§6) needed no code change, confirmed by Task 3's tests using the exact convention; testing plan (spec's "Testing" section) is covered by Tasks 1, 3, 4, 5's unit tests plus Task 6's manual check.
- **Type/signature consistency:** `_advance_two_phase(self, session, submission_result)` (Task 4) is called identically in Task 5's `write_results()` wiring. `twophase.group_screening_status(dataset, outcome_by_codename)` signature is identical everywhere it's called (Tasks 3 and 4). `Evaluation(...)` field names (`evaluation_sandbox_paths`, `evaluation_sandbox_digests`) match the current model (verified against `cms/db/submission.py`), not the pristine's now-removed `evaluation_sandbox` field.
- **No placeholders:** every step has real code, real commands, real expected output.
