# Hidden Dependency Messages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a subtask hide its dependency messages from contestants (`"hide_dependency": true`), and draw a testcase skipped by a dependency in a neutral state instead of a red "Incorrecto" (issue #16, plus the CWS pill of #15).

**Architecture:** `ScoreTypeGroup` (cms/grading/scoretypes/abc.py) writes the full details (AWS) and the public details (contestants) at scoring time. The public subtask details become a copy that, for a hidden subtask zeroed by a dependency, has no note and shows its skipped testcases as `N/A` rows; every skipped testcase carries `"skipped": true` and the template draws it neutral.

**Tech Stack:** Python 3.12, Jinja2 (the details TEMPLATE inside abc.py), CSS, gettext catalogs, unittest.

**Spec:** `docs/superpowers/specs/2026-10-22-hide-dependency-messages-design.md`

## Global Constraints

- Code, comments, test names and commits in English; the Spanish catalogs by hand (never `sphinx-intl update`).
- Python 3.12, per-worktree `.venv`. Tests ONE file at a time: `CMS_CONFIG=/tmp/claude-1000/-var-home-areslolxd-Documentos-cms/dag-polish-scratch/cms-hide.toml .venv/bin/pytest -p no:cacheprovider <file> -q` (private DB `cmshidefortesting`, created and initialized by the controller; the score type tests in `cmstestsuite/unit_tests/grading/` do not need it). Never a directory or a bare pytest.
- `uvx -q pyflakes <changed .py files>` clean; lines at most 79 columns where the surrounding file keeps them; docstrings in the project's format.
- The scoring does not change: scores, ranking details and the stored FULL details (AWS) are untouched; only `public_subtasks` changes. Smallest complete change; a `shortcut: <limit>, <when to upgrade>` comment for a known limit.
- One commit per task, Conventional Commits, trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never push or merge.

## Review Focus

- The public details of an ALL-PUBLIC subtask are today the same object as the full details: hiding must not remove the note from the full details (AWS). (Task 2 test)
- A hidden subtask whose testcases were graded although it is zeroed keeps its real rows and loses only the note. (Task 2)
- `hide_dependency` that is not a boolean is refused with a clear message by `score_type_object` (so AWS Update/clone and the importers refuse it); on a subtask without `depends_on` it is accepted and does nothing. (Task 1)
- Details stored before this change (no `skipped` key) must still render with the old row. (Task 3)
- `get_json_details` at the RESTRICTED level keeps `skipped` only for testcases it shows, and the API (JSON) never gets a `skipped: false`. (Task 2)

---

### Task 1: Read and validate `hide_dependency`

**Files:**
- Modify: `cms/grading/subtaskdag.py` (new function next to `parse_dependencies`), `cms/grading/scoretypes/abc.py` (`ScoreTypeGroup.__init__`)
- Test: `cmstestsuite/unit_tests/grading/subtaskdag_test.py`, `cmstestsuite/unit_tests/grading/scoretypes/GroupDependenciesTest.py`

**Interfaces:**
- Produces: `HIDE_DEPENDENCY = "hide_dependency"` in subtaskdag.py; `subtaskdag.parse_hide_dependency(parameters: list) -> list[bool]`: one bool per subtask, `False` for a list-form or a dict without the key; raises `ValueError("Subtask %d: hide_dependency must be true or false." % index)` when the value is not a `bool` (an integer is not a bool). `ScoreTypeGroup.hide_dependency: list[bool]` set in `__init__` right after `self.dependencies`.

- [ ] **Step 1: Tests.** In subtaskdag_test.py: `TestParseHideDependency`: list form and plain dicts give all `False`; `{"hide_dependency": True}` gives `True` for that index; `1`, `"true"` and `None` raise `ValueError` whose text contains `hide_dependency must be true or false`. In GroupDependenciesTest.py: `test_hide_dependency_attribute` (GroupMin with the file's `self.parameters` plus the key on subtask 1: `st.hide_dependency == [False, True, False]`), `test_non_boolean_hide_dependency_raises_like_depends_on` (the same construction with `"yes"` raises `ValueError`), and `test_hide_dependency_without_depends_on_is_accepted`.
- [ ] **Step 2:** Run both files; the new tests fail.
- [ ] **Step 3:** Implement the function and the attribute.
- [ ] **Step 4:** Run both files and `cmstestsuite/unit_tests/grading/scoretypes/GroupPublicDetailsTest.py`; pass. Commit `feat(scoring): read hide_dependency from the subtask parameters`.

### Task 2: Public details: hide, copy, mark skipped

**Files:**
- Modify: `cms/grading/scoretypes/abc.py` (`ScoreTypeGroup.compute_score`, `get_json_details`)
- Test: `cmstestsuite/unit_tests/grading/scoretypes/GroupDependenciesTest.py`, `GroupPublicDetailsTest.py`

**Interfaces:**
- Consumes: `self.hide_dependency` (Task 1).
- Produces: every testcase detail dict (full and public) whose evaluation text is the `skipped_dependency` message (`EVALUATION_MESSAGES.get("skipped_dependency").message`, the first element of `evaluation.text`) gets `"skipped": True` (the key is absent otherwise). For a subtask `st_idx` with `self.hide_dependency[st_idx]` and `zeroed[st_idx] is not None`, the entry appended to `public_subtasks` is a COPY of the subtask dict without `zeroed_by_dependency`, whose `testcases` are those of the public list with each skipped one replaced by `{"idx": <idx>}`; the full `subtasks` list is unchanged. `get_json_details` adds `"skipped": True` to a shown testcase when `tc.get("skipped")`.

- [ ] **Step 1: Tests** (GroupDependenciesTest.py, reusing `get_submission_result` and `set_outcome`; read how `test_partially_private_zeroed_subtask_in_public_details` builds the public view): `test_skipped_testcase_is_marked` (a failed dependency: the dependent subtask's skipped testcases have `skipped` True in the full details and the others have no key); `test_hidden_subtask_public_details_have_no_note_and_na_rows` (flag on the dependent: its public subtask has no `zeroed_by_dependency` and its skipped testcases are `{"idx": n}`); `test_hiding_does_not_touch_the_full_details` (the same call: `details[1]["zeroed_by_dependency"] == 0` and the skipped rows still carry their text; for an ALL-PUBLIC subtask too); `test_hidden_subtask_that_graded_its_testcases_keeps_the_rows` (zeroed after grading: the real rows stay, the note goes); `test_not_hidden_public_details_are_unchanged` (parity with today's note and rows, plus the `skipped` marks); in GroupPublicDetailsTest.py `test_json_details_keep_skipped_only_when_true` (FULL and RESTRICTED levels; a testcase without the mark gets no `skipped` key).
- [ ] **Step 2:** Run both files; the new tests fail.
- [ ] **Step 3:** Implement in `compute_score` and `get_json_details`.
- [ ] **Step 4:** Run both files and `cmstestsuite/unit_tests/grading/scoretypes/SumTest.py`; pass. Commit `feat(scoring): hide the dependency messages of a subtask from the public details`.

### Task 3: Neutral skipped row (template, CSS, strings)

**Files:**
- Modify: `cms/grading/scoretypes/abc.py` (`TEMPLATE` and the `N_()` marker list), `cms/server/contest/static/cws_style.css`, `cms/server/admin/static/aws_style.css`, `cms/locale/cms.pot`, `cms/locale/es/LC_MESSAGES/cms.po`
- Test: `cmstestsuite/unit_tests/grading/scoretypes/GroupDependenciesTest.py`, `cmstestsuite/unit_tests/server/admin/dataset_forms_test.py` (add the CSS test there; it already asserts rules of both stylesheets)

**Interfaces:**
- A testcase row with `tc["skipped"]` renders `<tr class="skipped">` and its outcome cell shows the translated "Not tested" (new `N_("Not tested")` string; es "No se probó"); other columns as for a normal row. A row without the key renders as today. CSS: `tr.skipped` neutral grey rule in both stylesheets (text `#666666` or darker, as the contrast rule of #15; read the neighboring `tr.notcorrect` rules for the selector scope each stylesheet uses).

- [ ] **Step 1: Tests:** `test_skipped_row_is_neutral_in_the_html` (render with `get_html_details(details)` after a failed dependency: the skipped rows are `class="skipped"`, their outcome cell is "Not tested", the "Not tested: this subtask depends on subtask 0" detail text is still there, and the row of a graded failed testcase is still `class="notcorrect"`), `test_details_without_the_skipped_key_render_as_before` (hand-built details with a `Not correct` outcome and no key), and `test_skipped_rule_exists_in_both_stylesheets` (dataset_forms_test.py: both CSS files contain a `tr.skipped` rule).
- [ ] **Step 2:** Run; fail. **Step 3:** Implement the template branch, the marker, the CSS and the two catalog entries (find how "Worth 0 because it depends on subtask %(index)s" is entered in cms.pot and es cms.po and add `Not tested` the same way, es translation "No se probó"). **Step 4:** Run the two test files; pass. Commit `feat(cws): draw a testcase skipped by a dependency as not tested`.

### Task 4: Docs and Spanish catalog

**Files:**
- Modify: `docs/subtask-dependencies.md`, `docs/locale/es/LC_MESSAGES/subtask-dependencies.po`

- [ ] **Step 1:** Update "What the contestant sees" (the skipped rows are now grey "Not tested" and no longer a red "Incorrecto"), add a subsection that documents `"hide_dependency": true` (what it hides: the note and the "Not tested: …" text of that subtask, shown as grey `N/A` rows; what it does not: the zero itself, the ranking, AWS; the example `{"max_score": 50, "testcases": "^s3-", "depends_on": [0, 1], "hide_dependency": true}`; not boolean is refused with `Subtask N: hide_dependency must be true or false.`; add the message to the table of "Errors when saving"), and say in "Changing the dependencies after there are submissions" that the flag applies when a submission is scored again (**S** for old ones; stored details keep the old rows). Spanish: edit the `.po` entries by hand (copy each old msgid exactly, new msgid, msgstr; code spans verbatim; GLOSSARY terms; no fuzzy).
- [ ] **Step 2:** The controller runs the docs check (`docs-check-hide.sh`, must end `docs check rc=0`) and `msgfmt --statistics`; run them too if you can. Commit `docs(dag): document hide_dependency and the neutral skipped row`.
