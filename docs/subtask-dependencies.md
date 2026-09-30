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
  the points.
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

### In `task.yaml`

The `italy_yaml` loader only reads the score type from `task.yaml` when the file
declares `score_type`, `score_type_parameters` **and** `n_input` (the number of
testcases). Otherwise it ignores them (with a warning if only some of the three
are there) and detects the subtasks from `gen/GEN`, which cannot carry
dependencies.

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

AWS checks the dependencies when you save the dataset. A wrong value is
rejected like any other invalid score type parameter: a notification titled
**"Invalid score type parameters"** shows one of the messages below, and the
dataset is not saved. If the text is not valid JSON, AWS says "Score type
parameters are invalid JSON." instead.

| Message | What to do |
|---------|------------|
| "Subtask 2: depends_on must be a list of subtask numbers." | Write `depends_on` as a list of integers, for example `[0, 1]`. Not a number alone, not text, not `null`, not `true`. |
| "Subtask 1: depends on subtask 5, which does not exist (subtasks are numbered from 0 to 2)." | Use a number from 0 to the number of subtasks minus 1. |
| "Subtask 1: a subtask cannot depend on itself." | Remove its own number from the list. |
| "Subtask 2: depends_on repeats a subtask." | Write each number once. |
| "The subtask dependencies form a cycle; subtasks in it or depending on it: 1, 2, 3." | Break the cycle. The subtasks named are the ones in the cycle and the ones that depend on it. |

The numbers in the table are only examples: the messages show the numbers of
your own parameters. The cycle check runs last, so fix the other errors first.

## What the contestant sees

A subtask worth 0 because of the rule shows 0 in CWS. When several of its
dependencies failed, the messages name the lowest-numbered one.

**Testcases that were not graded** show this text in their details:

- English: "Not tested: this subtask depends on subtask %s, which scored no
  points."
- Spanish: "No se probó: esta subtarea depende de la subtarea %s, que no obtuvo
  puntos."

The `%s` is replaced by the number of the dependency, counted from 0.

**A subtask whose testcases were graded but that is worth 0 by the rule** shows
its real testcase results, and a note in the subtask:

- English: "Worth 0 because it depends on subtask %(index)s, which scored no
  points."
- Spanish: "Vale 0 porque depende de la subtarea %(index)s, que no obtuvo
  puntos."

Here `%(index)s` is the number of the dependency.

The ranking receives the subtask scores with the rule applied.

### Public and private testcases

The contestant sees a subtask as 0, with "No se probó…" in its testcases, when
one of its dependencies failed. So if a subtask has only public testcases but
depends on a subtask with **private** testcases, the contestant can learn that
the hidden dependency failed. This comes with the rule and cannot be avoided.

If it matters, keep a subtask and the subtasks it depends on equally public.

## Two-phase screening

Two-phase screening (`CMS_TWO_PHASE_EVALUATION`) is unchanged. It is still
declared by testcase codenames (`sN-nn-sample`, `sN-nn-scr-wa`, …), and the
group of a testcase is the part of its codename before the first `-`.

- A subtask that its dependencies release runs its screening testcases first,
  and the rest of its testcases only if the screening passes.
- A subtask whose screening fails gets its other testcases marked as skipped, so
  its score is 0, and the subtasks that depend on it are skipped too.

**The codename groups must agree with the subtasks of the score type.** The
score type decides the score and the dependencies. The codenames decide the
screening. If a group holds other testcases than its subtask, screening and
dependencies can disagree, for example screening one subtask with the
testcases of another. Check it with the codename lint of the rehearsal.

For example, with the groups `s1`, `s2` and `s3` (subtasks 0, 1 and 2), the
score type can select the testcases by regular expression:

```json
[{"max_score": 20, "testcases": "^s1-"},
 {"max_score": 30, "testcases": "^s2-"},
 {"max_score": 50, "testcases": "^s3-", "depends_on": [0, 1]}]
```

## Latency

A correct submission gets its full score later. A dependent subtask starts only
when all its dependencies have finished, so each level of dependencies waits
for the previous one. The longer the chain of dependencies, the longer a correct
submission takes.

In return, a submission that fails an early subtask skips all the subtasks
that depend on it.

## Changing the dependencies after there are submissions

- Scores follow the new rule when the task is rescored.
- Testcases already marked "No se probó…" stay that way until they are
  graded again. After you change the dependencies of a task with submissions,
  **re-evaluate the task from AWS**: open the dataset (its "Submissions" list) and
  use the buttons next to "Reevaluate all N submissions for this dataset". **E**
  (Evaluation) grades the submissions again, and **S** (Score) only scores them
  again.
- Re-evaluate the whole submission or the whole task. The "Rerun and archive"
  button of one testcase (in the submission page) does not clear the "No se
  probó…" rows. If you rerun a failed root testcase and it now passes, its
  dependents stay at 0 until the whole submission is evaluated again. The same
  is true for the testcases skipped by two-phase screening.

## When the dependencies cannot be used

AWS rejects wrong `depends_on` values (see "Errors when saving"). But a dataset
can also have parameters that the score type cannot use even though `depends_on`
is fine, for example a `GroupThreshold` subtask in dict form without
`"threshold"`.

The Evaluation Service then ignores the dependencies of that dataset: it grades
every testcase as if none were declared, and writes this warning to its log,
once per dataset each time it starts:

```
Dataset 7 declares subtask dependencies that cannot be used; grading it without them.
```

The number is the id of the dataset. Fix the parameters of the dataset.

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
in scoring and in scheduling. None of the code for dependencies runs.
