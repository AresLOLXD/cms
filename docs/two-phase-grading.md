# Two-phase grading

Two-phase grading saves worker time on wrong submissions. In each subtask, a
few **screening** testcases are graded first. The other testcases of the
subtask are graded only if the screening passes. If it fails, they are not run:
they are recorded as skipped, with outcome 0.

It is off by default. One setting, `CMS_TWO_PHASE_EVALUATION`, turns it on for
the whole deployment. The screening testcases and the groups are found from the
codenames of the testcases. There is no setting in the Admin Web Server (AWS)
and no database change.

## What it does

Two-phase grading works on **groups** of testcases, taken from their codenames
(see "Naming the testcases"). Each group should be the testcases of one
subtask.

1. A submission first grades the screening testcases of every group, and every
   testcase of the groups that have no screening testcase.
2. The screening of a group **passes** when all its screening testcases are
   graded and each one has an outcome above 0. A partial outcome passes. A wrong
   answer or a timeout gives 0 and fails. The decision waits for the last
   screening testcase of the group.
3. If the screening passes, the other testcases of the group are graded.
4. If it fails, the other testcases of the group are not run. Each one is
   recorded with outcome 0 and the text "Skipped after screening phase
   failure".
5. Each group is decided on its own. A failed screening in one group does not
   change how the other groups are graded.

For example, a task has the groups `s1` (subtask 0) and `s2` (subtask 1). A
submission gives a wrong answer on `s1-01-scr-wa`. The other testcases of `s1`
are skipped and subtask 0 scores 0. All the testcases of `s2` are graded as
usual.

It applies to submissions only. The tests that contestants run from the Contest
Web Server (user tests) are graded as before. It applies to every dataset that
is graded, the live one and the ones with background judging.

### Effect on the score

With a group score type (`GroupMin`, `GroupMul` or `GroupThreshold`) and groups
that are exactly the subtasks, two-phase grading does not change any score. A
failed screening testcase has outcome 0, and that already makes its subtask
worth 0. Only the grading time changes.

- `GroupThreshold`: a screening testcase above the threshold has an outcome
  above 0, so it passes the screening even though it fails the subtask. The
  rest of the group is graded and the subtask still scores 0. Nothing is saved
  in that case, but the score is right.
- `Sum`: each testcase scores on its own, so skipping changes the score. A
  submission loses the points of the skipped testcases it would have passed.
  Do not give screening codenames to the testcases of a `Sum` task.

## Turning it on

With Docker, set it in `.env`:

```bash
CMS_TWO_PHASE_EVALUATION=true
```

- The values are `true` and `false`, in any case (`TRUE` works too). Unset or
  empty means `false`.
- Any other value stops CMS. The `db-init` container exits with this error
  (here for `yes`), which `./logs.sh db-init` shows:

  ```
  ERROR: CMS_TWO_PHASE_EVALUATION must be 'true' or 'false', got 'yes'.
  ```

  The `cms` container does not start, and `./up.sh` fails with
  `service "db-init" didn't complete successfully: exit 1`.
- **A wrong value takes a running CMS down.** When `.env` changes, `./up.sh`
  recreates the `cms` container: it stops the running one and does not start
  the new one. Check the value before you run `./up.sh`, above all on the day
  of the contest.

- It applies to the whole deployment: every contest, task and dataset. It
  cannot be turned on for one contest or one task only.
- Changes to `.env` apply when the containers are recreated (`./up.sh`).

Without Docker, set it in the `[global]` section of `cms.toml`:

```toml
[global]
two_phase_evaluation = true
```

Then restart the Evaluation Service. It reads the setting when it starts, and
it is the only service that uses it.

### Tasks that do not follow the naming

A task with no screening testcase is graded as without two-phase grading: all
its testcases at once, none skipped. A codename with fewer than two `-`, such
as `000` (group `""`, empty), is never a screening testcase.

Other codenames can be screening testcases by accident. Any codename with at
least two `-` is read with the naming below, so `sub1-big-discrete` is a
screening testcase: its tag, `discrete`, contains `scr`. Before turning it on, check the
codenames of every task (step 2 of "Before the contest").

## Naming the testcases

A codename has the form `<group>-<nn>-<tag>`:

| Codename | Group | Screening |
|----------|-------|-----------|
| `s1-00-sample` | `s1` | yes: the tag is `sample` |
| `s1-01-scr-wa` | `s1` | yes: the tag contains `scr` |
| `s1-02-scr-tle` | `s1` | yes: the tag contains `scr` |
| `s1-03-big` | `s1` | no |
| `s2-00-sample` | `s2` | yes |
| `000` | `""` (empty) | no |

- The **group** is the text before the first `-`.
- The **tag** is everything after the second `-`. A codename with a single `-`
  has no tag, so it is never a screening testcase: `s1-sample` is not one.
- A testcase is a **screening** testcase when its tag is exactly `sample` or
  contains `scr` anywhere, even inside a word. `s2-05-scramble` is a screening
  testcase; `s1-00-sample2` is not.
- The middle part (`<nn>`) is not read. Use it to number the testcases.

A typical group has three screening testcases: the example, one built to catch
a wrong answer (`scr-wa`) and one built to catch a solution that is too slow
(`scr-tle`).

### Getting these codenames

The `italy_yaml` loader names the testcases `000`, `001`, …, so a task
imported with it has a single group `""` and no screening testcases.

AWS cannot rename a testcase. To give a dataset new codenames, use the **Test
cases** block of the dataset on the task page:

1. **Download all testcases**. With the default templates, the zip has
   `input.000`, `output.000`, `input.001`, ….
2. Rename the files to the new codenames (`input.s1-00-sample` and
   `output.s1-00-sample`, …) and zip them again, with no folder inside.
3. **Add multiple testcases** with the new zip and the default templates
   (`input.*` and `output.*`: the `*` is the codename). Its **Public** box
   applies to every testcase in the zip.
4. **Delete** each old testcase.

If the task already has submissions, do this on a clone of the dataset (see
"Changing it after there are submissions").

## Groups and subtasks

The score type decides the subtasks and the score. The codenames decide the
groups and the screening. **Each group must be exactly the testcases of one
subtask**, or the screening of one subtask can skip testcases of another.

Select the testcases of each subtask by regular expression in the score type
parameters (`"testcases": "^s1-"`). Do not use counts: they take the testcases
in the order of their codenames, and `s10-…` sorts before `s2-…`.

The exact condition, and an example, are in "Two-phase screening" in
[Subtask dependencies](subtask-dependencies.md). It matters most when the task
also uses `depends_on`.

## What contestants and admins see

In the Contest Web Server, each skipped testcase of a submission shows:

- Outcome: "Not correct" (in Spanish, "Incorrecto").
- Details: "Skipped after screening phase failure" (in Spanish, "No se probó:
  el envío falló los primeros casos de la subtarea").
- Time and memory, where shown: 0, since nothing ran.

The texts follow the language of the contestant in the Contest Web Server.
Which rows a contestant sees depends on the feedback level of the task and on
which testcases are public, as for any other testcase.

The **Documentation** page of the contest explains the message: "This testcase
was not run because the screening testcases for its subtask did not pass." In
Spanish: "Este caso no se probó porque el envío falló alguno de los primeros
casos de su subtarea. Primero se prueban unos pocos casos de cada subtarea y,
si alguno falla, los demás ya no se prueban."

In AWS, the submission page shows each skipped testcase with outcome `0.0` and
the details "Skipped after screening phase failure". AWS shows status texts in
English.

The Evaluation Service logs each submission that gets skipped testcases, here
7 testcases of submission 42 on dataset 3:

```
Two-phase: synthesized 7 skipped evaluation(s) for submission 42(3).
```

## Latency

A correct submission gets its full score later. Each group takes two rounds
instead of one: the rest of a group goes to the workers only after the results
of all its screening testcases are written. The groups do their rounds at the
same time, so a submission takes two rounds, not two per group.

In the first round a submission uses at most as many workers as it has
screening testcases (plus the testcases of the groups without screening). With
many idle workers, a correct submission takes longer than without two-phase
grading.

In return, a submission that fails a screening testcase skips the rest of that
group, and the workers are free for other submissions.

With `depends_on`, a subtask also waits for its dependencies (see "Latency" in
[Subtask dependencies](subtask-dependencies.md)).

## Before the contest

1. Set `CMS_TWO_PHASE_EVALUATION` in `.env` as it will be on the day, and
   recreate the containers (`./up.sh`).
2. For every task, open the task page and read the codenames in the **Test
   cases** block of each dataset. Every group has its screening testcases, and
   no other tag contains `scr`. A task that must not use screening has no
   screening codename.
3. Check that each group is exactly one subtask (see "Groups and subtasks").
4. Submit a correct solution. It gets the full score, and its page in AWS has
   no "Skipped after screening phase failure" row.
5. Submit a solution that fails a screening testcase of one subtask, for
   example a wrong answer on its `scr-wa` testcase. That subtask scores 0, its
   page in AWS shows "Skipped after screening phase failure" on the rest of the
   testcases of that subtask, and the other subtasks are scored as usual.
6. `./logs.sh --tail 2000 cms` shows "Two-phase: synthesized" for the second
   submission.

For a task with `depends_on`, also follow "Before the contest" in
[Subtask dependencies](subtask-dependencies.md).

## Changing it after there are submissions

**Turning it on or off.** Once the Evaluation Service runs with the new value:

- Results already written stay as they are. Nothing is graded again: skipped
  testcases stay skipped, and testcases graded with it off keep their results.
- A submission still being graded finishes with the new value. With it off,
  all its testcases left are graded.
- With a group score type and groups that are exactly the subtasks, the scores
  are the same either way, so there is no need to grade again. To grade the
  skipped testcases anyway (for example in a `Sum` task), turn it off and press
  **E** (Evaluation) for the dataset or the submission.

**Renaming testcases.** New codenames change the groups and the screening. Do
it before the contest. On a task with submissions, change the testcases on a
clone of the dataset (see {doc}`Task versioning`), grade the clone in the
background and make it live when it is done.

The steps, where the **E** buttons are, and the warning about pressing **E** on
a live task are in "Changing the dependencies after there are submissions" in
[Subtask dependencies](subtask-dependencies.md).

While two-phase grading is on, grade again the whole submission or the whole
dataset. The **Rerun and archive** button of one testcase does not clear the
skipped testcases: a skipped testcase that is rerun is skipped again, and if a
rerun screening testcase now passes, the rest of its group stays skipped.
