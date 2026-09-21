# Two-phase / fail-fast grading — design spec

Date: 2026-09-21
Origin: [issue #2](https://github.com/AresLOLXD/cms/issues/2) — port the two-phase / fail-fast grading
patch from COMIGuide's `juez/pristine/` onto this fork.

## Problem

COMIGuide's judge stack (`juez/`) currently applies a custom patch to grading by `docker cp`-ing four
files into the running `cms-core`/`cms-worker` containers' installed egg, then restarting. It must be
re-applied after every `docker compose down`/recreate or grading silently reverts to stock CMS. The
patch implements fail-fast screening: a small set of testcases per subtask (sample + a case designed
to catch a wrong-answer bug + a case designed to catch a too-slow solution) is evaluated first; the
rest of that subtask's cases only run if screening passes. If screening fails, the remaining cases are
synthesized as "Saltado" (skipped, outcome 0) instead of actually graded, saving grading time on
submissions that are obviously wrong or too slow — without affecting other subtasks' partial scoring.

This spec covers porting that behavior into this fork's own source tree, baked into the Docker image
build, gated behind a flag that defaults to off.

## Why the patch can't be applied literally

The four pristine files (`conf.py`, `twophase.py`, `service/EvaluationService.py`,
`service/esoperations.py`) were written against an older/different base of CMS than what this fork
currently has:

- This fork's `cms/conf.py` uses `@dataclass`-based config sections parsed from TOML
  (`GlobalConfig`, `DatabaseConfig`, ...); the pristine file sets plain attributes on a config object.
- This fork's `submission_get_operations()` and `write_results()` already carry an `archive_sandbox`
  parameter/tuple element that didn't exist when the patch was written.
- This fork's `Evaluation` model stores `evaluation_sandbox_paths`/`evaluation_sandbox_digests`
  (both `list[str] | None`); the pristine code constructs an `Evaluation` with a single
  `evaluation_sandbox=""` field that no longer exists.

So this is a **reimplementation of the same behavior** on the current codebase, not a literal patch
apply. The core algorithm (`cms/grading/twophase.py`) is unchanged — it has no CMS-internal
dependencies beyond reading one config flag.

## Approaches considered

1. **Column-based screening flag on `Testcase`** — more explicit, but requires a schema migration and
   breaks the existing contract with COMIGuide's `exporta_cms.py`, which already renames imported
   testcase codenames to the `<group>-<nn>-<tag>` convention. Rejected: unnecessary schema change for
   a convention that's already established and working on the COMIGuide side.
2. **Implement at the `ScoreType` layer only** — would compute scores correctly but wouldn't actually
   skip running the testcases, defeating the purpose (saving grading time). Rejected.
3. **Port COMIGuide's design as-is** (codename convention, scheduling-level gate, synthesized skipped
   evaluations) — recommended and what this spec describes.

## Design

### 1. `cms/grading/twophase.py` (new file)

Same logic as the pristine module — `enabled()`, `group_of(codename)`, `is_screening(codename)`,
`group_screening_status(dataset, outcome_by_codename)`. Pure function, only dependency is
`from cms import config`. The only change from pristine: reads
`config.global_.two_phase_evaluation` instead of `getattr(config, "two_phase_evaluation", False)`.

Grouping/screening convention (unchanged from COMIGuide):

```
<group>-<nn>-<tag>     e.g. s1-00-sample, s1-01-scr-wa, s1-02-scr-tle, s1-03-...
```

- screening = tag is `sample` or contains `scr`.
- group = the part before the first `-`. A codename without `-` (old format, e.g. `000`) falls into
  the single global group `""`.
- pass = `outcome > 0` (WA/TLE give `0.0`).

### 2. `cms/conf.py`

Add one field to `GlobalConfig` (the section that already holds simple top-level flags like
`file_log_debug`, and maps to `[global]` in `cms.toml`):

```python
@dataclass()
class GlobalConfig:
    ...
    two_phase_evaluation: bool = False
```

### 3. `cms/service/esoperations.py` — `submission_get_operations()`

Insert the screening gate between computing `evaluated_testcase_ids` and the loop that yields
`ESOperation.EVALUATION` for each un-evaluated testcase:

```python
if twophase.enabled():
    outcome_by_codename = {
        evaluation.codename: evaluation.outcome
        for evaluation in submission_result.evaluations}
    screening_status = twophase.group_screening_status(dataset, outcome_by_codename)
else:
    screening_status = None

for testcase_codename in dataset.testcases.keys():
    testcase_id = dataset.testcases[testcase_codename].id
    if testcase_id in evaluated_testcase_ids:
        continue
    if screening_status is not None and not twophase.is_screening(testcase_codename):
        group = twophase.group_of(testcase_codename)
        if screening_status.get(group, "passed") != "passed":
            continue
    yield ESOperation(ESOperation.EVALUATION, submission.id, dataset.id,
                      testcase_codename, archive_sandbox=archive_sandbox), \
        priority, submission.timestamp
```

`archive_sandbox` (not present in pristine) is preserved as-is.

### 4. `cms/service/EvaluationService.py`

**New method** `_advance_two_phase(self, session, submission_result)`, adapted to this fork's
`Evaluation` model — synthesizes a skipped evaluation for every non-screening testcase of a failed
group:

```python
def _advance_two_phase(self, session, submission_result):
    dataset = submission_result.dataset
    outcome_by_codename = {e.codename: e.outcome for e in submission_result.evaluations}
    status = twophase.group_screening_status(dataset, outcome_by_codename)
    evaluated_ids = {e.testcase_id for e in submission_result.evaluations}

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
        logger.info("Two-phase: synthesized %d skipped evaluation(s) for "
                    "submission %d(%d).", created,
                    submission_result.submission_id, submission_result.dataset_id)
        session.commit()
```

Note the field-name adaptation from pristine: `evaluation_sandbox=""` → `evaluation_sandbox_paths=[]`,
`evaluation_sandbox_digests=[]` (this fork's model doesn't have a single `evaluation_sandbox` field).

**Call sites in `write_results()`**:

- After the first `session.commit()` (results just written), for every distinct
  `(object_id, dataset_id)` of type `EVALUATION`, if `twophase.enabled()`, load the
  `SubmissionResult` and call `_advance_two_phase()`. This synthesizes skips for any group whose
  screening just completed and failed.
- In the "ending operations" loop, when `type_ == ESOperation.EVALUATION` and
  `submission_result.evaluated()` is still `False` after the above (i.e. some group's screening just
  *passed*, so its remaining testcases are real work, not skips): call
  `self.submission_enqueue_operations(submission_result.submission, archive_sandbox)` to push the
  phase-2 operations, **but only when `twophase.enabled()`** — this is a deliberate change from
  pristine (which did this unconditionally). Gating it keeps flag-off behavior byte-for-byte
  identical to today, satisfying the acceptance criterion that the default behaves exactly like
  current grading. `submission_enqueue_operations` is the same primitive the periodic
  `_missing_operations` sweep already uses for pending submissions, so repeated/redundant calls are
  known-safe (`enqueue()` dedups against already-queued/in-flight operations).

### 5. `docker/generate_config.py` + `.env.example`

New env var `CMS_TWO_PHASE_EVALUATION` (default `false`), written into the `[global]` section of the
generated `cms.toml`, following the same pattern as `CMS_LOG_DEBUG`. Documented in `.env.example` next
to the other diagnostics/global flags.

### 6. Codename convention compatibility

No changes needed on the CMS side beyond the above — `dataset.testcases` is already a
codename-keyed dict (used elsewhere in `esoperations.py`), so COMIGuide's `exporta_cms.py` renaming
step (`s1-00-sample`, `s1-01-scr-wa`, ...) continues to work unmodified against this fork's schema.

## Testing

No existing unit tests cover `esoperations.py` or `EvaluationService.py` scheduling behavior in
`cmstestsuite/unit_tests/`. Plan:

- New unit test file for `cms/grading/twophase.py` (pure logic — `group_of`, `is_screening`,
  `group_screening_status`) under `cmstestsuite/unit_tests/grading/`, matching where the rest of that
  package's tests live.
- Manual verification of the scheduling gate and skip-synthesis: build a small dataset fixture with
  screening + non-screening codenames, flag on/off, confirm operations are withheld/released and
  skipped evaluations are synthesized only on the failed path.
- `pyflakes` clean on all touched files.

## Non-goals

- No DB schema migration.
- No changes to `docker-compose.prod.yml` or the Dockerfile — the flag flows through the existing
  `docker/generate_config.py` → `cms.toml` path, and the feature files are part of the normal source
  tree already copied into the image build.
