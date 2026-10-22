# Subtask Dependencies Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refuse a dataset with invalid score parameters when it is cloned in AWS or imported, pin the ES gate fallback with a test and a clearer warning, and polish the AWS dataset forms (issue #15, without N-1, the rounding rule, the CWS pill and the style debt).

**Architecture:** One method, `Dataset.validate_score_type()`, is the single check (the one Update already runs through `score_type_object`). AWS clone and a helper of `cmscontrib/importing.py` call it; the ES fallback warning splits orphan testcases using the score type's subtask targets; the AWS forms get markup, CSS and a small `sessionStorage` draft in `aws_utils.js`.

**Tech Stack:** Python 3.12, SQLAlchemy, tornado/Jinja2 templates, plain JS, unittest/pytest, Sphinx docs with a hand-edited Spanish catalog.

**Spec:** `docs/superpowers/specs/2026-10-21-subtask-dag-polish-design.md`

## Global Constraints

- Code, comments, test names and commits in English; the UI strings of AWS stay in English as the existing ones, the Spanish catalog follows the English docs.
- Python 3.12, per-worktree `.venv`. Tests run ONE file at a time (never a directory or a bare pytest: gevent monkey-patching): `CMS_CONFIG=/tmp/claude-1000/-var-home-areslolxd-Documentos-cms/ea26d07b-cc2a-4e41-a2a9-aba4c85e00c4/scratchpad/cms-dag.toml .venv/bin/pytest -p no:cacheprovider <file> -q`. DB-backed tests use the private database `cmsdagfortesting` of that config (podman `cms-test-pg`, already created and initialized by the controller). Groups: `cmstestsuite/unit_tests/cmscontrib/*` run in the gevent group; `service/`, `server/`, `db/` (except `rankinggroup_test.py`) in the asyncio group.
- `uvx -q pyflakes <changed .py files>` clean; lines at most 79 columns (Python); JS follows the ESLint config (4-space indent, double quotes); docstrings in the project's format.
- No score changes: the check is the one Update runs. Smallest complete change, no option nobody asked for, a `shortcut: <limit>, <when to upgrade>` comment for a known limit.
- One commit per task, Conventional Commits, trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never push or merge.

## Review Focus

- A valid dataset must still clone and import (no false refusal): the tests reuse the valid DAG fixtures of `subtaskdag_*_test.py`. (Tasks 1, 2)
- A refused clone or import leaves nothing behind: no commit in the handler, no row in the database after `do_import()`. (Tasks 1, 2)
- The fallback warning keeps its old text when every missing testcase is in a subtask, and a score type without subtasks (`Sum`) does not crash it. (Task 3)
- The draft restore never replaces a field whose text equals the saved one, survives `sessionStorage` being unavailable (private window), and does not run on forms without a score-parameters textarea. (Task 5)
- The docs keep "press Update after adding testcases" true for datasets added by hand. (Task 6)

---

### Task 1: `Dataset.validate_score_type`, Update reuses it, clone validates

**Files:**
- Modify: `cms/db/task.py` (`Dataset`), `cms/server/admin/handlers/task.py` (the Update loop, about line 224), `cms/server/admin/handlers/dataset.py` (`CloneDatasetHandler._post_sync`, after `dataset.clone_from(...)`)
- Test: `cmstestsuite/unit_tests/db/dataset_validate_test.py` (new, plain `unittest`, no database: build in-memory `Dataset`/`Task`/`Testcase` objects the way `cmstestsuite/unit_tests/grading/subtaskdag_test.py` does), `cmstestsuite/unit_tests/server/admin/clonedataset_test.py` (new; mock the handler as `cmstestsuite/unit_tests/server/admin/rankinggroup_test.py` mocks `RankingGroupHandler`)

**Interfaces:**
- Produces: `Dataset.validate_score_type(self) -> None`: raises `ValueError(<message of the error>)` when `self.score_type_object` raises `AssertionError` or `ValueError`; returns `None` otherwise. Task 2 uses it.
- The Update loop calls it instead of its own `try/except (AssertionError, ValueError)` and keeps the same notification (`"Invalid score type parameters"`, `str(error)`) and redirect.
- `CloneDatasetHandler._post_sync`: after `dataset.clone_from(original_dataset, True, True, clone_results)` call `dataset.validate_score_type()`; on `ValueError` add the notification `("Invalid score type parameters", str(error))` through `self.service.add_notification(make_datetime(), ...)`, redirect to `fallback_page`, and return without `try_commit()`.

- [ ] **Step 1: Tests.** `dataset_validate_test.py`: `test_a_valid_dag_passes` (the valid parameters of `subtaskdag_test.py` with matching testcases: returns `None`), `test_a_cycle_is_refused` (`depends_on` cycle -> `ValueError` whose text contains "cycle" or the text `parse_dependencies` raises), `test_a_regex_without_testcases_is_refused` (a `GroupMin` target regex matching no testcase -> `ValueError` containing "No testcase matches"), `test_assertion_errors_become_value_errors` (parameters of the wrong shape that make the score type `assert` -> `ValueError`). `clonedataset_test.py`: `test_an_invalid_clone_is_refused_and_not_committed` (original dataset with testcases; the form carries invalid `score_type_parameters`: `try_commit` not called, the notification title is "Invalid score type parameters", `redirect` called with the clone page), `test_a_valid_clone_is_committed` (same with valid parameters: `try_commit` called once).
- [ ] **Step 2:** Run both files; the new tests fail (no method).
- [ ] **Step 3:** Implement the method and the two call sites; the Update loop keeps catching the `ValueError` it now gets.
- [ ] **Step 4:** Run both files, and `cmstestsuite/unit_tests/grading/subtaskdag_test.py` (it exercises the score types). All pass. Commit `feat(aws): refuse a cloned dataset whose score parameters are invalid`.

### Task 2: Importers refuse an invalid dataset

**Files:**
- Modify: `cmscontrib/importing.py` (new function), `cmscontrib/ImportTask.py`, `cmscontrib/ImportContest.py`, `cmscontrib/ImportDataset.py`, and `cmscontrib/DumpImporter.py` only if its datasets are built in one place (otherwise leave it and say so in the report)
- Test: `cmstestsuite/unit_tests/cmscontrib/ImportTaskTest.py`, `ImportContestTest.py`, `ImportDatasetTest.py` (gevent group, DatabaseMixin)

**Interfaces:**
- Consumes: `Dataset.validate_score_type()` of Task 1.
- Produces: `validate_datasets(task: Task) -> None` in `cmscontrib/importing.py`: for each dataset of `task.datasets` call `validate_score_type()`; on `ValueError` raise `ImportDataError("Task %s, dataset %s: %s" % (task.name, dataset.description, error))`. Each importer calls it right after its loader returns the task(s) and inside the code that already turns `ImportDataError` into `logger.error(...)` + "no changes were made" + `return False`, before any database write.

- [ ] **Step 1: Tests**, one per importer, reusing each file's `fake_loader_factory`: `test_an_invalid_dataset_is_refused_and_nothing_is_written` (a task whose dataset has `GroupMin` parameters with a cyclic `depends_on`: `do_import()` returns `False` and no `Task` row exists afterwards); in `ImportTaskTest.py` also `test_a_valid_dag_dataset_imports` (a valid `depends_on` dataset imports, `True`).
- [ ] **Step 2:** Run each file; the new tests fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the three files; pass. Commit `feat(import): refuse a dataset whose score parameters are invalid`.

### Task 3: ES gate fallback: regression test and a clearer warning

**Files:**
- Modify: `cms/service/EvaluationService.py` (the fallback of `submission_enqueue_operations`, the `logger.warning(...)` about "gates are holding each other")
- Test: `cmstestsuite/unit_tests/service/subtaskdag_write_results_test.py` (it has `WriteResultsFixtureMixin`, `GATES_HOLDING_EACH_OTHER`, `_write_with`, `_enqueued_evaluation_codenames`)

**Interfaces:**
- In the fallback, split `missing` into the codenames that belong to no subtask (not in any target of the dataset's score type; for a score type without subtask targets, such as `Sum`, none) and the rest. The warning text is unchanged except that, only when there are orphans, ` (in no subtask: <codename>, <codename>)` is added right after the list of missing codenames in the existing line. Use the targets the score type already computes (`ScoreTypeGroup`); do not parse the parameters again.

- [ ] **Step 1: Tests**, in the same file: `TestAlignedDagNeverHoldsItself` (aligned parameters in the layout of `docs/subtask-dependencies.md`, e.g. codenames `s1-00-sample`, `s1-01-scr-wa`, `s1-02-x`, `s2-...` with `^s1-`/`^s2-` targets and `depends_on`; two-phase on; write the sample result and the later results; assert with `assertNoLogs("cms.service.EvaluationService", "WARNING")` filtered on `GATES_HOLDING_EACH_OTHER` that the fallback is never taken and the result completes). `test_a_testcase_in_no_subtask_is_named` (parameters that leave one codename out of every target, two-phase on: the warning contains `(in no subtask: <that codename>)`, and `test_the_misaligned_warning_has_no_orphan_note` asserts the existing split-group case has no "in no subtask" text).
- [ ] **Step 2:** Run the file; the orphan test fails (the no-fallback test passes already).
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the file and `cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py` (gevent group, separate command). Pass. Commit `feat(es): tell testcases in no subtask apart in the gate fallback warning`.

### Task 4: AWS forms: textarea, help, hint, note style

**Files:**
- Modify: `cms/server/admin/templates/add_dataset.html` (line ~136), `cms/server/admin/templates/task.html` (the score-parameters row ~444 and the Update button ~496), `cms/server/admin/static/aws_style.css`, `cms/server/contest/static/cws_style.css`
- Test: `cmstestsuite/unit_tests/server/admin/dataset_forms_test.py` (new): render both templates with the same Jinja2 environment the AWS tests use (see how `rankinggroup_test.py` uses `AWS_ENVIRONMENT`) and assert the markup

**Interfaces:**
- The score-parameters `<textarea>` of both templates gets `rows="12"`. Its `title` help (the `<span class="info">` before "Score Parameters" in `task.html`; add the same span in `add_dataset.html` if it has none) says: `A subtask can list "depends_on": [subtask numbers]; see the subtask dependencies page of the documentation.` Near the Update button of `task.html` add `<span class="hint">Update does not rescore the submissions. Press S or E on the dataset page to rescore.</span>` (check that wording against the "Changing the dependencies after there are submissions" section of `docs/subtask-dependencies.md` and use its words if they differ). A rule for `.subtask-dependency` (muted color, `font-size: 0.9em`, italic, small margin) in `aws_style.css` and in `cws_style.css` next to the `#submission_detail .subtask-*` rules.

- [ ] **Step 1: Tests**: `test_textarea_has_rows_and_the_help_names_depends_on` (both templates), `test_update_has_the_no_rescore_hint` (`task.html`).
- [ ] **Step 2:** Run; fails. **Step 3:** Implement. **Step 4:** Run; passes; `uvx -q pyflakes`. Commit `feat(aws): size and explain the score parameters field and note that Update does not rescore`.

### Task 5: Keep the typed score parameters after a rejected save

**Files:**
- Modify: `cms/server/admin/static/aws_utils.js` (a block inside the existing `DOMContentLoaded` handler at line ~88, or a function it calls)

**Interfaces:**
- For every `form` that contains a `textarea[name^="score_type_parameters"]`: on `submit`, save each such textarea's value in `sessionStorage` under the key `aws_draft:<location.pathname>:<textarea name>`. On load, for each such textarea: if a saved text exists and differs from the textarea's current value, set the textarea to the saved text and insert after it a `<div class="hint">Restored your unsaved text.</div>`; if it equals the current value, remove the saved entry. Every `sessionStorage` access is inside `try/catch` and the page must work without it. No other script change.

- [ ] **Step 1:** Write the block. **Step 2:** `eslint cms/server/admin/static/aws_utils.js` clean (if eslint is not installed, `uvx`/`npx` is not needed: say so and read the file against the 4-space/double-quote rules).
- [ ] **Step 3:** Check it in headless Chromium once: a throwaway page in the scratchpad (outside the repo) that loads `aws_utils.js` with a form and a textarea, simulates a submit that stores the draft, then loads the page with a different textarea value and screenshots it (`flatpak run --filesystem=/tmp/claude-1000 org.chromium.Chromium --headless=new --no-sandbox --disable-gpu --screenshot=<file> --virtual-time-budget=5000 file://...`). State what you saw; if the harness cannot run the script, say "not seen". The saved-equals-current case and the no-storage case are checked by reading the code. Commit `feat(aws): restore the typed score parameters after a rejected save`.

### Task 6: Docs and Spanish catalog

**Files:**
- Modify: `docs/subtask-dependencies.md`, `docs/locale/es/LC_MESSAGES/subtask-dependencies.po`

- [ ] **Step 1:** Update the English text: (a) where the docs ask to press Update on every DAG task after an import: a dataset that is cloned in AWS or imported is now checked, and an invalid one is refused (the import stops with the task and dataset name and changes nothing; a clone shows "Invalid score type parameters"); a dataset added by hand has no testcases yet and is checked on Update, so pressing Update after adding the testcases stays; (b) the paragraph "The warning can also appear on a dataset that follows the rule" and the warning example: the line now says `(in no subtask: ...)` when testcases belong to no subtask, and that case is harmless; (c) one sentence for the AWS field: the larger field, the hint that Update does not rescore, and that a rejected save keeps what you typed (the page restores it, with "Restored your unsaved text"). Spanish: edit the `.po` entries by hand (never `sphinx-intl update`): same meaning, code spans verbatim, terms of `docs/locale/es/GLOSSARY.md`, no fuzzy.
- [ ] **Step 2:** The controller runs the docs check (`docs-check-dag.sh`, must end `docs check rc=0`) and `msgfmt --statistics` (0 fuzzy, 0 untranslated); if you can run commands, run them too. Commit `docs(dag): say that clones and imports are checked`.
