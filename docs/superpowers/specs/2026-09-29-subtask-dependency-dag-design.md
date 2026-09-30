# Subtask Dependency DAG for Grading — Design Spec

**Date:** 2026-09-29
**Status:** Approved in conversation (sections 1-5); pending written-spec review
**When:**
- Built now on the branch `dag-subtareas`, in its own worktree.
- Merged into `beta` only if it passes every task review, the final branch
  review, and the full CI, run alone on `ubuntu:noble` and `debian:bookworm`,
  before the freeze on Friday 2026-10-02. Otherwise the 2026-10-10 contest
  runs without it.

## Problem

Today every subtask of a submission is graded on its own and in parallel.
Two-phase grading (`cms/grading/twophase.py`) already saves time inside a
subtask: its screening testcases run first, and if one of them fails, the
rest of that subtask is marked as not tested, with 0 points. Across
subtasks, though, nothing is shared: a subtask whose prerequisite already
failed is still fully graded.

OMI problems often have subtasks that build on each other. One example:
subtask 1 has N ≤ 10, subtask 2 has N ≤ 1000, and subtask 3 has no limit.
The organizers want two things:
- **A scoring rule.** A subtask that depends on a subtask that scored 0 is
  worth 0.
- **A grading optimization.** Because such a subtask is worth 0, it is not
  graded at all.

## Goals

- A problem can declare, for each subtask, the subtasks it depends on.
- **Scoring.** A subtask is worth 0 when any of its dependencies, direct or
  transitive, scored 0. Subtasks with no dependency keep their own score.
- **Grading.** A submission first grades the subtasks with no dependencies,
  each with its screening and extra phases as today. It then walks the DAG:
  - a subtask is graded only once all its dependencies have passed;
  - a subtask with a failed dependency is never graded, and its testcases
    are marked as not tested.
- **Parity.** A dataset without any dependency behaves exactly as today, in
  scoring and in scheduling.

## Non-Goals

- A new screen in the Admin Web Server (AWS). The dependencies are edited
  in the dataset's score type parameters, which AWS already edits.
- A DB schema change.
- Changes to COMIGuide or OMI-Box. Issues describe what each needs (see
  §4).
- An optimistic mode that starts a dependent subtask before its
  dependencies finish.
- Changing how two-phase screening is declared or decided.

## Decisions Taken in Brainstorming

| Question | Decision |
|---|---|
| Purpose | Both: a scoring rule, and therefore skipping the grading |
| Independent subtasks | Keep their own score |
| When a dependency passes | Its subtask score is > 0 (a partial score passes) |
| Several dependencies | All must pass |
| Transitivity | Yes: a subtask worth 0 by the rule counts as failed for its own dependents |
| Where dependencies are declared | In the problem package, exported in `task.yaml` |
| Storage in CMS | `depends_on` in the dict form of `score_type_parameters` (approach A) |
| When | Before the 2026-10-10 contest, only if everything passes before 2026-10-02 |
| Scope | CMS only. Issues for OMI-Box now and for COMIGuide after 2026-10-10 |
| Two-phase screening | Unchanged: still declared by testcase codenames |

Rejected alternatives:
- **B, a new `Dataset` column.** It is cleaner, but needs a migration and
  changes to the loader, the dump import and export, and an AWS form, which
  is too much before the freeze.
- **C, scheduling only, with the score type unaware.** The rule would be
  lost on re-evaluation or when dependencies change. The rule must live in
  scoring.

## Design

### 1. Format and validation

**Format.** Every subtask in the dict form of `score_type_parameters` may
carry `depends_on`, a list of subtask numbers. The numbers are the ones CMS
already shows to contestants in CWS and in the ranking: "Subtask 0",
"Subtask 1", …, which is the subtask's position in the list, **starting at
0** (user decision, 2026-09-29):

```json
[{"max_score": 20, "testcases": 3},
 {"max_score": 30, "testcases": 5},
 {"max_score": 50, "testcases": 8, "depends_on": [0, 1]}]
```

Exporters convert their own ids. For example, COMIGuide's subtask ids start
at 1, so its id 1 becomes 0.

- `depends_on` is optional. A subtask without it has no dependencies.
- The list form (`[[20, 3], ...]`) is unchanged and cannot carry
  dependencies. Declaring dependencies requires the dict form, which
  `ScoreTypeGroup` already accepts.
- The existing keys `threshold` and `always_show_testcases` keep working
  next to it.

**Validation.** `ScoreTypeGroup` validates the parameters when it is built,
like the rest of its parameters. An invalid value raises `ValueError`, and
the dataset is reported as invalid, as any invalid parameter is today. The
errors are:
- `depends_on` is not a list of integers;
- a number is out of range (below 0, or at least the number of subtasks);
- a subtask depends on itself;
- a number is repeated in the same list;
- the dependencies form a cycle.

**Semantics.**
- All dependencies must pass.
- The rule is transitive. Say 3 depends on 1 and 4 depends on 3. If 1
  fails, 3 is worth 0, and so is 4.

**Parity.** When no subtask declares `depends_on`, every code path is the
one of today. The new logic is not entered.

### 2. Scoring rule

The rule is implemented once, in `ScoreTypeGroup`, so it applies to
`GroupMin`, `GroupMul` and `GroupThreshold`, and to any other group score
type.

- Each subtask's score is computed as today.
- The subtasks are then visited in topological order. A subtask with any
  dependency whose score is 0 gets a score of 0. The dependency's score
  already includes this rule, which is what makes it transitive.
- "Failed dependency" means that the dependency's subtask score is 0. Any
  score > 0 passes.
- The same rule applies to the public score (feedback and tokens) and to
  the per-subtask scores sent to the ranking.
- The task's maximum score does not change.
- The rule always applies, even when the subtask's testcases were
  evaluated (for example after the dependencies changed, or after a
  re-evaluation). The evaluations are kept as they are; only the subtask
  score is 0.

**What the contestant sees** for a subtask worth 0 by the rule:
- The subtask shows 0.
- **Testcases skipped because of a dependency** show a new evaluation
  message:
  - English source: "Not tested: this subtask depends on subtask %s, which
    scored no points."
  - Spanish (`cms.po`): "No se probó: esta subtarea depende de la subtarea
    %s, que no obtuvo puntos."
  - The subtask number is a format argument of the evaluation text. When
    several dependencies failed, the lowest-numbered one is shown.
- **A subtask whose testcases were evaluated** but that is worth 0 by the
  rule shows its real testcase results. The subtask header gets a note:
  - English: "Worth 0 because it depends on subtask %s, which scored no
    points."
  - Spanish: "Vale 0 porque depende de la subtarea %s, que no obtuvo
    puntos."

### 3. Scheduling

**What a subtask is.** Scheduling uses the scoring definition: the
testcases that the score type assigns to each subtask
(`retrieve_target_testcases`). Two-phase screening keeps using testcase
codenames inside those testcases, as today.

**Subtask status in a submission**, computed from the evaluations written
so far:
- **Failed:** as soon as the evaluated testcases already make its score 0.
  The test is the score type's own `reduce` over the evaluated outcomes
  being 0. That is valid for min, product and threshold, which can only
  go down as more outcomes arrive. The spec requires this monotonicity of
  any group score type used with dependencies. A subtask whose dependency
  failed or was skipped is also failed.
- **Passed:** all its testcases are evaluated, its score is > 0, and all its
  dependencies passed.
- **Pending:** otherwise.

**Gating**, in `submission_get_operations` in `cms/service/esoperations.py`:
- A testcase is released only if at least one subtask that contains it has
  all its dependencies passed.
- Within a released subtask, the two-phase screening gate applies
  unchanged.
- A testcase that belongs to no subtask is not gated.

**Skipping.** This extends the two-phase bookkeeping in `EvaluationService`
(`_advance_two_phase`, or a sibling called next to it).
- For a subtask that is failed because of a dependency, every testcase not
  yet evaluated, and not needed by any subtask that is not failed, gets a
  synthesized evaluation:
  - outcome "0.0";
  - the new "skipped because of a dependency" message, with the number of
    the dependency;
  - no execution.
- The same happens in cascade to the subtasks that depend on it.
- The submission then reaches a complete evaluation and is scored.

**Triggers.** The points that today run only when two-phase is enabled
also run when the dataset declares any dependency:
- releasing withheld operations after results are written;
- the skip synthesis;
- the sweeper's advance for submissions with no pending operation.

The DAG is driven by data. It is active whenever the dataset declares a
dependency, with or without two-phase grading.

**Robustness.**
- A dataset whose parameters cannot be parsed (for example a cycle) is
  graded without the DAG, and a warning is logged with the dataset id.
  Scoring reports the parameter error, as it does today for invalid
  parameters.
- Nothing may leave a submission stuck.
- User tests are not affected.

**Latency.** A correct submission reaches its full score later. A
dependent subtask starts only when all its dependencies have finished, so
the total time grows with the depth of the DAG. This is the accepted cost
of grading the roots first. In return, a submission that fails a root
subtask skips all of its dependents.

### 4. Administration, contestants, docs and issues

**AWS.**
- There is no new page. Dependencies are written in the dataset's score
  type parameters JSON.
- A validation error is shown on save, as for any invalid parameter.

**Changing dependencies after submissions.**
- Scores follow the new rule once the task is rescored.
- Testcases already marked as not tested stay that way until they are
  re-evaluated.
- The guide says to re-evaluate the task from AWS after changing its
  dependencies.

**Contestant Web Server and ranking.**
- The texts of §2 appear in CWS, translated in `cms/locale/es/LC_MESSAGES/cms.po`.
- The ranking receives the per-subtask scores with the rule applied.

**Documentation.**
- A new guide, `docs/subtask-dependencies.md`. It covers the format, an
  example, how dependencies combine with screening, the latency cost, the
  re-evaluation note, and the monotonicity requirement for custom score
  types.
- One README line that links to it.

**Issues**, filed on 2026-09-29: COMI-Guide/OMI-Box#28 and
COMI-Guide/COMIGuide#46.
- **OMI-Box (now).**
  1. Capture the dependencies of each subtask in the editor.
  2. Export `score_type_parameters` in dict form with `depends_on` in
     `task.yaml`. `task.yaml` takes precedence over `gen/GEN` in the
     `italy_yaml` loader.
  3. Export testcases with the screening codename convention:
     `sN-NN-sample`, `sN-NN-scr-wa`, `sN-NN-scr-tle`, then the extra
     testcases. The loader numbers testcases (`000`, `001`, …), so the
     issue proposes renaming them after the import, as COMIGuide's
     `exporta_cms.py` does.
- **COMIGuide (after 2026-10-10).**
  1. Problem metadata already declares `depende_de: [ids]` on each
     subtask (5 problems use it today), but no tool validates or exports
     it. Validate it in `verifica.py`.
  2. Export it as `depends_on` in dict-form `score_type_parameters` from
     `exporta_cms.py`. Convert each id to its CMS subtask number, which is
     its position in id order starting at 0, so id 1 becomes 0.

### 5. Testing

**Unit tests, score types:**
- `GroupMin`, `GroupMul` and `GroupThreshold` with dependencies;
- transitivity;
- a partial score that passes a dependency;
- several dependencies;
- the public score and the ranking details;
- the subtask note;
- validation: a cycle, a self-dependency, an out-of-range number, a
  repeated number, and a value that is not a list;
- the list form unchanged.

**Unit tests, scheduling:**
- gating with two-phase on and off;
- release after a dependency passes;
- a skip in cascade, with the message and the dependency number;
- a testcase shared by several subtasks;
- a testcase in no subtask;
- the sweeper advancing a submission with no pending operation;
- an invalid dataset graded without the DAG.

**Parity.** A dataset without `depends_on` yields exactly the same
operations and scores as the current code, checked against fixtures shared
with the existing two-phase tests.

**Functional.** A task with dependencies in the functional suite:
- a submission that fails the root subtask leaves its dependents at 0 and
  not graded;
- a correct submission gets the full score;
- independent subtasks keep their own score.

**Chromium.** A short run on the local stack checks the texts in CWS and the
score in the ranking.

**CI.** A full CI run, alone, on `ubuntu:noble` and `debian:bookworm`.

### 6. Rollout and risks

**Rollout.** The branch `dag-subtareas` goes through the usual process:
- a review after each task;
- a final whole-branch review;
- the full CI;
- a merge into `beta` only if everything passes before 2026-10-02.

**Risks:**
- **Grading of the contest.** Mitigated by the parity guarantee for tasks
  without dependencies. The 2026-10-10 tasks only get the DAG if their
  datasets declare `depends_on`.
- **Latency on deep DAGs.** This is the accepted cost, described in §3.
- **Two definitions of a subtask.** Codenames define the screening groups,
  and the score type defines the scoring subtasks. The guide explains that
  they must agree, and the rehearsal's codename lint query checks it.

## Error Handling

| Situation | Behaviour |
|---|---|
| Invalid `depends_on` (a cycle, out of range, …) | `ValueError` when the score type is built. The dataset is invalid, AWS shows the error, and the dataset is graded without the DAG. |
| A dependency fails | Dependents are worth 0. Their pending testcases are marked as not tested, in cascade. |
| Dependencies changed after grading | Scores follow the new rule on rescoring. Skipped testcases need a re-evaluation. |
| A dataset without dependencies | It behaves exactly as today. |
