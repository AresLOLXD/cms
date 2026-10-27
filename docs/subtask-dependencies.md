# Subtask dependencies

A subtask can depend on other subtasks. If a subtask it depends on scored 0,
the subtask is worth 0 and its testcases are not graded. Subtasks that depend on
nothing keep their own score.

This is useful when subtasks build on each other. For example, subtask 0 has
N ≤ 10, subtask 1 has N ≤ 1000 and subtask 2 has no limit. A submission that
fails subtask 0 cannot score in the other two, so grading them is a waste of
worker time.

Dependencies are declared in the score type parameters of a dataset. There is
no new page in the Admin Web Server (AWS) and no database change. A dataset
without dependencies is graded exactly as before (see "Parity" below).

## What it does

- A subtask **passes** when its score fraction is above 0. A partial score
  passes. A subtask worth 0 points (for example one with only the examples)
  passes when its testcases pass, because it is the fraction that counts, not
  the points. So a dependency passes even if its displayed score rounds to 0
  points, which is only possible with partial outcomes (a checker that gives
  part of the points).
- A subtask that depends on several subtasks needs **all** of them to pass.
- The rule is **transitive**. If subtask 3 depends on 1 and subtask 4 depends
  on 3, and 1 fails, then 3 is worth 0, and so is 4.
- Subtasks with no dependency keep their own score, whatever the others do.
- The rule is part of the score. It applies to the score the contestant sees,
  to the public score and to the per-subtask scores sent to the ranking. The
  maximum score of the task does not change.
- The rule applies even if the testcases of the subtask were graded (for
  example after you changed the dependencies). The results are kept as they
  are, and only the subtask score is 0.

The rule applies within each submission. This matters with the score mode
that sums the best result of each subtask across all submissions (IOI 2017-),
so put it in the contest rules, for example:

> A subtask that depends on other subtasks scores only in a submission that
> also passes those subtasks. Your best score in each subtask is taken over
> your submissions with this rule applied to each one: you cannot combine a
> subtask passed in one submission with a subtask that depends on it solved in
> another.

It also changes the order of grading:

1. A submission first grades the subtasks with no dependencies.
2. The testcases of a subtask are graded only once all its dependencies have
   passed: all their testcases are graded, their score is above 0 and their own
   dependencies passed.
3. A subtask counts as failed as soon as the testcases already graded make its
   score 0. Its dependents do not wait for its other testcases.
4. A subtask with a failed dependency is never graded. Its testcases that no
   other subtask needs are recorded as "not tested", with outcome 0, without
   running anything. The same happens down the chain to the subtasks that depend
   on it.
5. A testcase that is in two subtasks is graded as soon as one of them has all
   its dependencies passed. A testcase that is in no subtask is never held.

This works with or without two-phase screening (`CMS_TWO_PHASE_EVALUATION`).
The grading order follows the data: it is active whenever the dataset declares
a dependency.

## How to declare it

Use the **dict form** of the score type parameters, and add `depends_on` to
each subtask that needs it: a list of subtask numbers.

The numbers count **from 0**, exactly as CWS shows the subtasks ("Subtarea 0",
"Subtarea 1", …): the position of the subtask in the list. Tools that export
their own ids must convert them. For example, COMIGuide's subtask id 1 becomes
0.

```json
[{"max_score": 20, "testcases": 3},
 {"max_score": 30, "testcases": 5},
 {"max_score": 50, "testcases": 8, "depends_on": [0, 1]}]
```

Here subtasks 0 and 1 are independent, and subtask 2 is worth 0 if either of
them scores 0.

- `depends_on` is optional. A subtask without it, or with `[]`, has no
  dependencies.
- The **list form** (`[[20, 3], [30, 5], ...]`) cannot carry dependencies. Use
  the dict form.
- `threshold` and `always_show_testcases` keep working next to it.
- It works with the group score types `GroupMin`, `GroupMul` and
  `GroupThreshold`. For `GroupThreshold`, every subtask needs its `"threshold"`
  in the dict form.

### In AWS

Open the task page and go to **Datasets**. In the block of the dataset, under
"Score type settings", write the JSON in **Score Parameters**, and press
**Update** at the bottom of the page. The score type must be one of the group
score types above.

**Press Update after creating a dataset and adding its testcases.** A new
dataset is not checked when you create it, because it has no testcases yet, and
a regular expression that matches no testcase is an error. A clone and an
import are checked when you make them (see "Errors when saving").

The **Score Parameters** field is 12 lines tall, and its help text names
`depends_on`. Next to **Update**, a hint says that Update does not rescore the
submissions (see "Changing the dependencies after there are submissions").

### In `task.yaml`

The `italy_yaml` loader only reads the score type from `task.yaml` when the file
declares `score_type`, `score_type_parameters` **and** `n_input` (the number of
testcases). Otherwise it ignores them and detects the subtasks from `gen/GEN`,
which cannot carry dependencies. It warns only when `score_type` or
`score_type_parameters` is there without the other two; `n_input` alone gives
no warning.

This is an excerpt of a `task.yaml`, with the three keys. `n_input` must equal
the sum of the `testcases` counts (16 = 3 + 5 + 8), and nothing checks it. The
loader names the testcases `000`, `001`, …: see "Two-phase screening" if you
need other codenames.

```yaml
score_type: GroupMin
score_type_parameters:
  - {max_score: 20, testcases: 3}
  - {max_score: 30, testcases: 5}
  - {max_score: 50, testcases: 8, depends_on: [0, 1]}
n_input: 16
```

### A working example

The functional test task `cmstestsuite/tasks/batch_dag/__init__.py` has three
subtasks with one testcase each. Subtask 1 depends on subtask 0:

```json
[{"max_score": 20, "testcases": 1},
 {"max_score": 30, "testcases": 1, "depends_on": [0]},
 {"max_score": 50, "testcases": 1}]
```

A solution that fails subtask 0 but is right in subtask 1 and subtask 2 scores
50, not 80: subtask 1 is worth 0 because of the dependency, and subtask 2 keeps
its own score.

## Errors when saving

AWS and the import commands run the same checks on the score type parameters of
a dataset:

- **Update** on the task page checks every dataset of the task.
- **Cloning** a dataset checks the clone once its testcases are copied, so a
  dataset with no testcases that selects them by regular expression is refused
  with "No testcase matches against the regexp ...".
- `cmsImportTask` checks the datasets of the task it stores: a new task, or an
  existing one with `--update` whose data has changed. With `--update` and no
  change it stores nothing and checks nothing.
- `cmsImportContest` checks a new task (`--import-tasks`) and a task it updates
  (`--update-tasks`).
- `cmsImportDataset` checks the dataset it stores.

**Creating** a dataset in AWS does not check it, because it has no testcases
yet: after adding them, open the task page and press **Update**.
`cmsDumpImporter` does not check the datasets it loads: it restores a dump, and
refusing the dump would block restoring a database that already holds an older
invalid dataset.

A wrong `depends_on` or `hide_dependency` is rejected like any other invalid
score type parameter: a notification titled **"Invalid score type parameters"** shows one of the
messages below. Nothing you changed on the task page is saved, not only the
dataset.

A refused clone shows the same notification and saves nothing. A refused import
logs `Task <task>, dataset <description>: <message>` (the message that Update
would show) and "Error while importing, no changes were made.", and stops
without writing anything.

If the text is not valid JSON, the notification is titled **"Invalid field(s)"**
and reads `ValueError('Score type parameters are invalid JSON.')`.

| Message | What to do |
|---------|------------|
| `Unknown score type "GroupMn".` | Write the name exactly as CMS has it; for dependencies, one of `GroupMin`, `GroupMul` and `GroupThreshold`. AWS offers only the score types CMS has, so you meet this message in an import, for example with a misspelled `score_type` in `task.yaml`. |
| "Subtask 2: depends_on must be a list of subtask numbers." | Write `depends_on` as a list of integers, for example `[0, 1]`. Not a number alone, not text, not `null`, not `true`. |
| "Subtask 1: depends on subtask 5, which does not exist (subtasks are numbered from 0 to 2)." | Use a number from 0 to the number of subtasks minus 1. |
| "Subtask 1: a subtask cannot depend on itself." | Remove its own number from the list. |
| "Subtask 2: depends_on repeats a subtask." | Write each number once. |
| "The subtask dependencies form a cycle; subtasks in it or depending on it: 1, 2, 3." | Break the cycle. The subtasks named are the ones in the cycle and the ones that depend on it. |
| "Subtask 2: hide_dependency must be true or false." | Write `hide_dependency` as `true` or `false`, without quotes. Not a number, not text, not `null`. |

The numbers and the score type name in the table are only examples: the
messages show those of your own parameters. The cycle check runs after the other
`depends_on` checks, and the `hide_dependency` check after it, so fix the other
errors first.

When AWS rejects a save, the page puts back the text you typed in **Score
Parameters**, once, with the line "Restored your unsaved text." under the field.
The text is kept only in that browser tab, and it comes back only if the page
loads again within 30 seconds and the text differs from the stored one; spaces,
line breaks and the order of the keys alone do not count.

## What the contestant sees

A subtask worth 0 because of the rule shows 0 in CWS.

The texts follow the language of the contestant in CWS: contestants see the
Spanish texts below only when their CWS language is Spanish. AWS shows status
texts untranslated, so an admin sees the English text, for example "Not tested:
this subtask depends on subtask 0, which scored no points."

**Testcases that were not graded** are a grey row, not a red one: the outcome
cell says "Not tested" (in Spanish, "No se probó") and the details show this
text:

- English: "Not tested: this subtask depends on subtask %s, which scored no
  points."
- Spanish: "No se probó: esta subtarea depende de la subtarea %s, que no obtuvo
  puntos."

The `%s` is replaced by the number of the dependency, counted from 0. When
several dependencies had failed, the text names the lowest-numbered one that had
failed when the row was written.

In CWS the row is the grey pill. In AWS only the label changes, because the AWS
table has no colors. Details stored before this change keep the old red "Not
correct" row ("Incorrecto" in Spanish) until the submission is scored again.

**Every subtask worth 0 by the rule** also shows a note in the subtask, whether
its testcases were "Not tested" or were graded, unless the subtask hides it (see
"Hiding the dependency messages"). A subtask whose testcases were graded shows
its real testcase results next to the note.

- English: "Worth 0 because it depends on subtask %(index)s, which scored no
  points."
- Spanish: "Vale 0 porque depende de la subtarea %(index)s, que no obtuvo
  puntos."

Here `%(index)s` is the number of the dependency: the lowest-numbered one that
failed when the submission was scored. It can differ from the number in the
"Not tested" rows, which name the dependency that had failed when they were
written.

The ranking receives the subtask scores with the rule applied.

### Hiding the dependency messages

A subtask can hide why it is worth 0. Add `"hide_dependency": true` to the
**dependent** subtask, next to `depends_on`:

```json
[{"max_score": 20, "testcases": "^s1-"},
 {"max_score": 30, "testcases": "^s2-"},
 {"max_score": 50, "testcases": "^s3-", "depends_on": [0, 1], "hide_dependency": true}]
```

- A hidden subtask that is worth 0 because of a dependency shows the 0 without
  the note "Worth 0 because it depends on subtask N, ...".
- Its testcases that the dependency skipped show as the rows of a private
  testcase: grey, `N/A`, no text. The number of rows does not change, except
  at the OI-restricted feedback level, where private rows are not shown at all.
- A hidden subtask that graded its testcases although it is zeroed keeps its
  real rows and loses only the note.
- `hide_dependency` must be `true` or `false`; anything else is refused like a
  wrong `depends_on` (see "Errors when saving"). On a subtask without
  `depends_on` it is accepted and does nothing.
- The full "Evaluation" section of AWS shows everything, to admins; the box
  "Evaluation (as seen by the user)" shows what the contestant sees.

**Hiding the messages does not hide the zeros.** The subtask still shows 0, the
public score still drops, and the ranking and the scores are the same, so a
contestant can still see that something it depends on failed (see "Public and
private testcases").

The public details a contestant sees are written when a submission is scored, so
a change of the flag applies to submissions scored afterwards: press **S** to
apply it to old ones (see "Changing the dependencies after there are
submissions"). The full details that a contestant sees for a submission with a
token and in analysis mode are filtered when they are shown, so they follow the
flag at once, without rescoring. This also holds for submissions scored before
this version: their skipped rows are recognized by their text.

### Public and private testcases

The contestant sees a subtask as 0, with the "Not tested: …" text (in Spanish,
"No se probó…") in its testcases, when one of its dependencies failed. So if a
subtask has only public testcases but depends on a subtask with **private**
testcases, the contestant can learn that the hidden dependency failed: the
subtask shows 0 and the public score drops. This comes with the rule and cannot
be avoided.

If it matters, give the dependent subtask at least one private testcase, or do
not make a subtask with only public testcases depend on one with private
testcases.

## Two-phase screening

[Two-phase grading](two-phase-grading.md) explains how two-phase screening
(`CMS_TWO_PHASE_EVALUATION`) works. It is declared by testcase codenames
(`sN-nn-sample`, `sN-nn-scr-wa`, …), and the group of a testcase is the part of
its codename before the first `-`.

- A subtask that its dependencies release runs its screening testcases first,
  and the rest of its testcases only if the screening passes.
- A subtask whose screening fails gets its other testcases marked as skipped, so
  its score is 0, and the subtasks that depend on it are skipped too.

**The codename groups must agree with the subtasks of the score type.** The
score type decides the score and the dependencies. The codenames decide the
screening. If a group holds other testcases than its subtask, screening and
dependencies can disagree, for example screening one subtask with the
testcases of another.

The exact condition is the **alignment rule**: for every codename group G that
has screening testcases, every subtask that contains a non-screening testcase
of G also contains all of G's screening testcases. The layout below follows it.

With codename groups numbered from 1 (`s1`, `s2`, …), every testcase whose
codename starts with `sN-` must be exactly the testcases of subtask N-1 (`s1-`
is subtask 0). The regex form guarantees it. For the groups `s1`, `s2` and `s3`
(subtasks 0, 1 and 2), the score type selects the testcases by regular
expression:

```json
[{"max_score": 20, "testcases": "^s1-"},
 {"max_score": 30, "testcases": "^s2-"},
 {"max_score": 50, "testcases": "^s3-", "depends_on": [0, 1]}]
```

The `italy_yaml` loader names the testcases `000`, `001`, …, so an import with
these regular expressions, or with lists that name codenames the loader did not
create, is refused (for a regular expression: "No testcase matches against the
regexp ..."; for a list, the message names the missing codename). Import with
the count form (`"testcases": 3`), rename the testcases to the `sN-nn-…`
codenames (see "Getting these codenames" in
[Two-phase grading](two-phase-grading.md)), then write the regular expressions
in **Score Parameters** and press **Update**.

### When a dataset breaks the alignment rule

Grading still completes, but the two gates can hold each other: two-phase holds
the other testcases of a group until its screening is done, and the
dependencies hold that screening. The Evaluation Service then releases the held
testcases of the submission without the gates, so some testcases run that could
have been skipped. It writes this warning to its log, here for submission 42 on
dataset 7:

```
Submission 42, dataset 7: no operations left but no evaluation for testcase(s) s1-01-scr-wa, s1-02-scr-tle, s1-03-x. The subtask dependency and two-phase screening gates are holding each other (see the alignment rule in docs/subtask-dependencies.md); releasing these testcases without the gates.
```

Search the log for "gates are holding each other". The warning can repeat for
the same submission until the released testcases are graded. A screening
testcase marked "Not tested" because of a dependency does not count as a failed
screening, so a subtask that does not depend on the failed one is still graded.
Fix the codenames or the subtasks of the dataset so that it follows the rule.

Until it is fixed, **do not trust the scores of that dataset**: the released
testcases all run at once, so whether a failed screening skips a testcase
already at a worker depends on which result arrives first, and the same
submission can get different scores. Fix the dataset, then reevaluate its
submissions (see "Changing the dependencies after there are submissions",
including its warning about a live task).

The warning can also appear on a dataset that follows the rule: when a testcase
is in no subtask, or after a "Rerun" of a single testcase or a change of
`depends_on` without **E** left an old "Not tested" evaluation behind. Grading
still completes with the right scores.

When some of the testcases listed belong to no subtask, the line adds
`(in no subtask: <codenames>)` right after the list:

```
Submission 42, dataset 7: no operations left but no evaluation for testcase(s) s1-01-scr-wa, s9-01-x (in no subtask: s9-01-x). The subtask dependency and two-phase screening gates are holding each other (see the alignment rule in docs/subtask-dependencies.md); releasing these testcases without the gates.
```

If the only testcases listed are in that note, the cause is a testcase in no
subtask, not a dataset that breaks the alignment rule. If other testcases are
listed too, check the dataset against the rule: the line cannot tell a dataset
that breaks it from an old "Not tested" evaluation.

## Latency

A correct submission gets its full score later. A dependent subtask starts only
when all its dependencies have finished, so each level of dependencies waits
for the previous one. The longer the chain of dependencies, the longer a correct
submission takes.

In return, a submission that fails an early subtask skips all the subtasks
that depend on it.

## Changing the dependencies after there are submissions

- **Update does not rescore.** It only tells the ranking that the dataset
  changed. Submissions scored before keep the rule they were scored with, and
  new submissions get the new rule, until you press **S** or **E** (below).
- Testcases already marked "Not tested: …" (shown as "No se probó…" to
  Spanish-language contestants) stay that way until they are graded again.
- **`hide_dependency` added or removed:** the public details are written when a
  submission is scored, so the flag applies to submissions scored afterwards.
  Press **S** to apply it to old ones; **E** is not needed. The full details of
  a submission with a token and in analysis mode follow the flag at once, also
  for submissions scored before this version (their skipped rows are
  recognized by their text).
- **Dependencies added or tightened:** **S** (Score) is enough. The rule applies
  to testcases that were already graded, so the scores follow the new rule.
- **Dependencies removed or loosened:** press **E** (Evaluation). The testcases
  marked "Not tested" must be graded now, and only a new evaluation does that.
- Where: open the dataset with its **[View results]** link (in the block of the
  dataset under **Datasets** on the task page, and on the **[Make Live ...]**
  page). Use the buttons next to "Reevaluate all N submissions for this
  dataset". On a submission page, the dataset selector shows that one
  submission under another dataset; it does not open the dataset's page.
- Always re-evaluate the whole submission or the whole task. The "Rerun and
  archive" button of one testcase (in the submission page) does not clear the
  "Not tested: …" rows. If you rerun a failed root testcase and it now passes,
  its dependents stay at 0 until the whole submission is evaluated again. The
  same is true for the testcases skipped by two-phase screening.

> **Warning: do not press E or S on a live task during a contest.** **E** on
> the whole dataset blanks every score and grades every submission again, and
> **S** blanks every score until it is computed again, so scores are missing
> meanwhile (and with parameters the score type cannot use, S leaves them all
> unscored). Change the dependencies before the contest starts. For a live
> task, use a clone instead: **[Clone]** the dataset (leave "Clone evaluation
> results" unticked), write the new dependencies in the clone and press
> **Update**, then press **[Enable background judging]** on the clone. That
> alone grades and scores every submission on the clone, at the lowest
> priority; press **E** on the clone only to redo it after changing its
> parameters again. It is done when every submission on the clone's
> **[View results]** page has a score and the **[Make Live ...]** summary looks
> right; only then use **[Make Live ...]** on the clone. Making a half-graded
> clone live hides scores from the contestants.

## When the dependencies cannot be used

AWS rejects wrong `depends_on` values (see "Errors when saving"). But a dataset
can also have parameters that the score type cannot use even though `depends_on`
is fine, for example a `GroupThreshold` subtask in dict form without
`"threshold"`.

The Evaluation Service then ignores the dependencies of that dataset: it grades
every testcase as if none were declared, and writes this warning to its log,
once per dataset each time it starts, followed by a traceback:

```
Dataset 7 declares subtask dependencies that cannot be used; grading it without them.
```

The number is the id of the dataset. Fix the parameters of the dataset.

The same happens with a wrong `depends_on` in a dataset created by hand that
Update has not checked yet, or loaded from a dump (see "Errors when saving").

> **Warning: unusable parameters stop the scoring.** "Grading without the
> dependencies" is about the grading only. Parameters that the score type cannot
> read, such as a `GroupThreshold` subtask without `"threshold"`, also make
> scoring fail: the score cannot be computed, and the submissions stay unscored.
> The Scoring Service logs "Unexpected error when executing operation" and tries
> again every 347 seconds, until the parameters are corrected. Then the
> submissions are scored by that periodic sweep, or at once with the **S**
> button of the dataset (see "Changing the dependencies after there are
> submissions"). A wrong `depends_on` in a dataset created by hand that Update
> has not checked yet, or loaded from a dump, ends the same way.

## Before the contest

For every task that uses `depends_on`:

1. Import it: the import checks the score type parameters of its datasets. For
   a dataset you created by hand, or restored from a dump, open the task page
   and press **Update** once its testcases are there (see "Errors when
   saving"). Press **Update** once on it in any case: that also covers a task
   that a reimport left alone and testcases that were added or deleted after
   the import.
2. Check that its datasets follow the alignment rule (see "Two-phase
   screening").
3. With `CMS_TWO_PHASE_EVALUATION` set as it will be during the contest,
   submit one correct solution and one that fails a subtask with no
   dependencies. Both must finish grading: the first with the full score, the
   second with the subtasks that depend on the failed one at 0.
4. Search the Evaluation Service log for "cannot be used" (see "When the
   dependencies cannot be used") and for "gates are holding each other" (see
   "When a dataset breaks the alignment rule"). Neither should appear.

## Custom score types

The dependencies are implemented once, in the base class of the group score
types (`ScoreTypeGroup`), so they work with any group score type. A score
type used with dependencies must have a `reduce` that:

- **never goes back up as more outcomes arrive**: adding an outcome to the list
  can keep the result or lower it, never raise it. The Evaluation Service
  decides that a subtask failed as soon as `reduce` over the outcomes so far
  gives 0, before its other testcases are graded. `min`, a product and a
  threshold test do it, and so do the built-in `GroupMin`, `GroupMul` and
  `GroupThreshold`;
- accepts a **single outcome of 1.0** (the Evaluation Service calls it that way,
  for each subtask, to check that the parameters can be read).

A `reduce` that can go up (for example an average) can skip testcases of a
subtask that would have scored.

## Parity

A dataset with no `depends_on` (or only empty ones) is graded exactly as before,
in scoring and in scheduling: the results are identical. Only two cheap things
run. Every group score type validates `depends_on` when it is built, and a check
of the parameters looks for dependencies. In a contest where some dataset
declares dependencies, the Evaluation Service's periodic sweep for missing
operations also takes the dependency-aware path for the datasets of all its
tasks, with the same results.
