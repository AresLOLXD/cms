# Polish of the subtask dependencies (#15)

Date: 2026-10-21. Issue: AresLOLXD/cms#15 (Ola 2). Code: `cms/db/task.py` (`Dataset`),
`cms/server/admin/handlers/{task,dataset}.py`, `cmscontrib/{importing,ImportTask,ImportContest,ImportDataset}.py`,
`cms/service/EvaluationService.py`, the AWS templates and `aws_utils.js`, `docs/subtask-dependencies.md`.

## Intent

The subtask dependency DAG (`depends_on` in the score type parameters) is on main. Its reviews left a list of
small items. The owner chose: validation of the score parameters at clone and import, the test and the clearer
warning of the gate fallback, and the AWS polish. A wrong `depends_on` today enters a contest through a clone
or an import without a word, and shows only when somebody presses Update.

## Goals

1. A dataset whose score parameters are invalid is refused when it is **cloned** in AWS and when it is **imported**
   (ImportTask, ImportContest, ImportDataset, and DumpImporter if it is one call), with the message that Update
   shows today, instead of being stored.
2. N-3: a test that an aligned DAG with two-phase on never reaches the fallback of
   `submission_enqueue_operations` (the "gates are holding each other" branch).
3. N-2: the WARNING of that fallback says which of the missing testcases belong to no subtask (they are
   in no subtask, the documented harmless case) and which belong to one.
4. AWS: a larger score-parameters textarea; a hint that Update does not rescore; `depends_on` named in the help
   of the field; a style for the note `.subtask-dependency` (rendered by the score details, in AWS and CWS);
   the text typed in a score-parameters field survives a rejected save.

## Non-goals

- N-1 (enqueue only what the gates would release): it changes the hot path of EvaluationService for a case that only
  an invalid dataset reaches and that the docs already say not to trust; Goal 1 and the docs prevent it.
- The rounded-score rule for a dependency to pass: it changes scores, nobody asked.
- The neutral state of the skipped-by-dependency rows in CWS: same code as #16 (hide the dependency messages from
  contestants); it is designed there.
- The test and style debt of the issue (an `Evaluation` helper, typing of `zeroed`, comments): left listed in the issue.
- Validating a dataset **added** in AWS: it has no testcases, and `ScoreTypeGroup` raises "No testcase matches
  against the regexp" for a regular expression without testcases, so a full validation there would refuse every
  regex dataset. It stays validated on Update, after the testcases are added, as the docs say.

## Anti-goals

- Refusing a dataset that is valid today, or any change of the scores. The check is the one Update already runs
  (`dataset.score_type_object`), not a new rule.
- Making an import fail for datasets that were already invalid in the database and are not part of the import.

## Design

**Goal 1.** `Dataset.validate_score_type() -> None` in `cms/db/task.py`: touches `self.score_type_object` and
turns `AssertionError` and `ValueError` into a `ValueError` whose text is the one of the error (what the Update
handler catches today, `(AssertionError, ValueError)`). The Update handler uses it too instead of its own
`try/except`. AWS clone (`CloneDatasetHandler._post_sync`): call it after `dataset.clone_from(...)`, so the
testcases exist, and on `ValueError` notify "Invalid score type parameters" and redirect to the clone form as the
handler does for its other errors; nothing is committed. Importers: a function
`validate_datasets(task) -> None` in `cmscontrib/importing.py` that calls the method on each dataset of a loaded task
and raises `ImportDataError("Task <name>, dataset <description>: <message>")`; ImportTask, ImportContest (each
task) and ImportDataset call it right after the loader returns and before any database write, so a failed import
changes nothing. DumpImporter: add the call only if its objects are built in one place; otherwise say so in the
report and the docs ("a dump is trusted").

**Goal 2.** A test in `cmstestsuite/unit_tests/service/` (the gevent/asyncio group of the existing two-phase ES
tests; find the file with the existing fallback test) with an aligned dataset, two-phase on, and a submission
result whose operations are all done or skipped: `submission_enqueue_operations` finalizes the result and the
fallback branch (the WARNING and the extra enqueue) is not taken. It is a regression test of the alignment rule.

**Goal 3.** In the fallback, `missing` is split with the dataset's score type: the testcases that belong to no
subtask (the score type's `parameters` targets), and the rest. The line keeps its text for the rest and adds
"(in no subtask: <codenames>)" when there are some, so that "gates are holding each other" is not read as a
misalignment when the only missing testcases are orphans. The docs paragraph "The warning can also appear on a
dataset that follows the rule" says so. A stale "Not tested" evaluation cannot be told apart from a real
misalignment in this branch (it shows as the same missing testcase): the docs keep saying it can happen.

**Goal 4.** Templates: `add_dataset.html` and `task.html` get `rows` on the textarea of the score parameters (CSS
`min-height`), the help `title` of that field names `depends_on` and the page of the docs; near the Update button
of `task.html` a one-line hint "Update does not rescore the submissions; see the docs" (the sentence of
`subtask-dependencies.md`). `aws_style.css` and the CWS stylesheet get a rule for `.subtask-dependency` (muted,
small, italic), consistent with the neighboring notes. The typed text survives: a small block in `aws_utils.js`:
before a form that has a `textarea[name^="score_type_parameters"]` is submitted, the text of each is saved in
`sessionStorage` under the page path and the field name; on load, if a saved text exists and differs from the
field's, the field gets the saved text and a "Restored your unsaved text" line appears under it; a saved text equal
to the field's is dropped. It is not done on the server because the three handlers redirect on error, and
re-rendering `task.html` (every dataset, every field) is far more code.

## Calls I made (the owner can overrule)

- Add dataset is not validated (explained above); the docs sentence "press Update on every DAG task after an
  import" stays true for tasks created by hand, and changes for imported and cloned ones.
- The typed-text item is client-side (`sessionStorage`) for the reason above; if it is not wanted, the rest stands.
- Goal 3 only tells orphans apart; it does not try to classify stale skips.
- The importers validate the whole task before writing, and refuse the import (an `ImportDataError`) instead of
  warning: an invalid dataset would fail the same way at the first Update.

## Tests

One per behavior, in the group of each file: `Dataset.validate_score_type` (valid, invalid depends_on, a regex with
no testcase), the clone handler refuses an invalid clone and commits nothing, each importer refuses an invalid task
and writes nothing, the fallback WARNING lists orphans, the new regression test of Goal 2. The JS draft and the
CSS are not unit-tested: checked in headless Chromium once (a screenshot of the restored field).

## Not verified / risks

- The sessionStorage restore is checked in one browser only.
- Validation at import can refuse a task that used to import: an operator with a bad `depends_on` finds out at
  the import (the goal), but a dataset with a regex that matches no testcase also fails (it fails on Update today).
