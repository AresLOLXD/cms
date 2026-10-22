# Hide the subtask dependency messages from contestants (#16), and a neutral state for skipped rows

Date: 2026-10-22. Issue: AresLOLXd/cms#16 (Ola 2), with the CWS pill item of #15 (see #73).
Code: `cms/grading/scoretypes/abc.py` (`ScoreTypeGroup`: `TEMPLATE`, `compute_score`, `get_json_details`),
`cms/grading/subtaskdag.py` (`parse_dependencies`), `cms/server/contest/static/cws_style.css`,
`cms/server/admin/static/aws_style.css`, `docs/subtask-dependencies.md` and its Spanish catalog.

## Intent

Contestants see two dependency messages in CWS: the note "Worth 0 because it depends on subtask N, which scored
no points." in a subtask, and the "Not tested: this subtask depends on subtask N, which scored no points." text
of the skipped testcases, drawn as a red "Incorrecto" row. Some organizers do not want to reveal the dependency
structure of a task. Separately, a skipped row looks like a failed one although it never ran.

## Goals

1. A subtask can carry `"hide_dependency": true` in its score type parameters, next to `depends_on`. For a
   contestant, a hidden subtask worth 0 because of a dependency shows its 0 **without the note**, and its
   testcases skipped by the dependency show as rows of a private testcase (grey, `N/A`, no text). The subtask
   still counts its rows (the table does not change size).
2. The scoring does not change: scores, the ranking, the stored full details and AWS (which shows everything) are
   untouched. Only the public details that contestants see change.
3. When the flag is not set, a testcase skipped by a dependency is drawn in a neutral state ("Not tested", grey)
   instead of the red "Incorrecto", in CWS and AWS, because it never ran.
4. `hide_dependency` is validated like `depends_on` (a boolean), by AWS on Update, clone and import (the check of
   #15), and documented.

## Non-goals

- Hiding the zeros in the ranking or in the public scores: a subtask worth 0 can still suggest a dependency; that
  would change the scoring.
- A per-contest or global switch (the owner chose the per-subtask key: no schema change, decided per task).
- Skips of two-phase screening ("the screening testcases did not pass"): they do not reveal dependencies.
- Rescoring existing submissions by itself: the public details are written when a submission is scored, so a
  change of the flag applies to new scorings; press **S** to apply it to old ones (the docs already say so for
  `depends_on`).

## Design

**Parameters.** `subtaskdag` gets a reader of `hide_dependency` (a bool per subtask, default false; a non-boolean
is a `ValueError("Subtask %d: hide_dependency must be true or false.")`), used by `ScoreTypeGroup.__init__` next
to `parse_dependencies`, so that `score_type_object` validation reports it in AWS and in the importers. A subtask
with `hide_dependency` and no `depends_on` is accepted and does nothing.

**Public details (hide).** In `ScoreTypeGroup.compute_score`, the subtask details written for contestants
(`public_subtasks`) are built from a COPY of the full subtask (today an all-public subtask puts the same object in
both lists), and for a subtask with `hide_dependency` that is zeroed by a dependency: the copy has no
`zeroed_by_dependency`, and each of its testcases that was skipped by the dependency (its evaluation text is the
`skipped_dependency` message) becomes `{"idx": <idx>}`, the form that a private testcase already has and that
`TEMPLATE` draws as `N/A`. A subtask that graded its testcases although it is zeroed (the rule applied at scoring
time) keeps showing its real rows; only the note is dropped.

**Neutral state (not hidden).** A testcase skipped by a dependency gets `"skipped": true` in its details dict
(both lists), set from the same text check; `TEMPLATE` draws such a row with class `skipped` and the outcome
cell "Not tested" (a new translatable string) instead of `Not correct`; `get_json_details` passes the key through;
`cws_style.css` and `aws_style.css` get a neutral rule for `tr.skipped` (grey, readable contrast). Details stored
before this change have no key and keep showing the old row until the submission is scored again.

**Docs.** "What the contestant sees" and "Changing the dependencies after there are submissions" say how to set
the flag, what a contestant sees with and without it, and that S applies it to old submissions. Spanish catalog by
hand.

## Calls I made (the owner can overrule)

- A hidden subtask keeps the grey `N/A` rows only for the testcases that were skipped; a testcase that was graded
  stays visible. The skipped testcases are recognized by their evaluation text (the `skipped_dependency`
  message), as the code already does elsewhere.
- The flag sits on the DEPENDENT subtask, because only it shows the messages.
- "Not tested" is a new string in the score details template (translations: es "No se probó").

## Tests

One per behavior: the parameter reader (valid, non-boolean, without `depends_on`), `compute_score` with and
without the flag (public details without the note and with `{"idx"}` rows for the skipped testcases; the full
details unchanged; an all-public subtask does not leak through the shared object), the template rendering of a
skipped row (neutral class and text) and of an `N/A` row, `get_json_details` keeping `skipped`, AWS validation of
the flag, the CSS rules exist.

## Not verified / risks

- CWS rendering is checked with the template and the CSS text, plus one screenshot in headless Chromium.
- Existing submissions show the old rows until they are scored again.
- The skipped rows are recognized by text: a different message wording would break the recognition (a test pins it).
