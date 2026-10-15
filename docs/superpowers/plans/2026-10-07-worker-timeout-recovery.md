# Worker Timeout Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A worker that EvaluationService disabled because a job group timed
out comes back by itself. Answered workers and job groups within their own
sandbox limits never time out.

**Architecture:** All state lives in `cms/service/workerpool.py`, under
`_operation_lock`.
- Each job group gets a dispatch id. The id travels with the RPC and with
  `action_finished`, so a stale answer is ignored.
- The RPC's return marks the worker as answered.
- `check_timeouts` uses a per-group timeout derived from sandbox limits.
- A reconnect re-enables a worker that the timeout disabled.

**Tech Stack:** Python 3.12, asyncio, `unittest` with `IsolatedAsyncioTestCase`
and `MagicMock`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-07-worker-timeout-recovery-design.md`

## Global Constraints

- **Where to work.** Work only in
  `/var/home/areslolxd/Documentos/cms/.worktrees/worker-timeout` (branch
  `feat/worker-timeout-recovery`). Do not push, do not merge, do not use
  `git stash`.
- **Python style.** Code and comments in English, PEP 8, PEP 484 hints.
  Docstrings use the project format: an imperative first line, then
  `arg (type): ...` and `return (type): ...` lines (see `CONTRIBUTING.md`).
- **Lint.** `uvx -q pyflakes <touched .py files>` must show nothing, and
  `uvx -q pycodestyle --max-line-length=79 <files>` must show nothing new
  (`EvaluationService.py` has existing E501 lines).
- **Python environment.** Create the venv if it is missing:
  `uv venv -q --python 3.12 .venv && uv pip install -q --python .venv/bin/python -c constraints.txt -e ".[devel]"`.
  Run tests with `.venv/bin/python -m pytest -p no:cacheprovider <files>`.
- **Config for every test run.** Set
  `CMS_CONFIG=/home/areslolxd/.claude/jobs/4c257a1a/tmp/cms-test.toml`. It
  points at the Postgres container `cms-test-pg` on localhost:55432. Run one
  pytest process at a time.
- **Test groups.** Never run a bare `pytest` over the suite.
  `twophase_evaluationservice_test.py` and `twophase_reenqueue_test.py` are
  gevent files: run them in their own pytest process.
- **Commits.** Use Conventional Commits. End each message with a `Refs #40`
  line, a blank line, then
  `Co-Authored-By: <your model name> <noreply@anthropic.com>`.
- **Exact values:**

  | Name | Value |
  |---|---|
  | `WorkerPool.WORKER_TIMEOUT` | `timedelta(seconds=600)`, unchanged and the minimum |
  | `WorkerPool.ANSWER_HANDLING_TIMEOUT` | `timedelta(minutes=30)` |
  | module constant `JOB_OVERHEAD_S` | `10` |
  | wall limit of a CPU limit x | `2 * x + 1` |

- **Exact log texts:**
  - timeout: `"Disabling and shutting down worker %d because of no response in %s (timeout %s)."`
  - answer-handling backstop: `"Disabling and shutting down worker %d: it answered %s ago but its result was not handled in %s."`
  - stale answer (INFO): `"Ignoring a stale answer from worker %s (job group %s; current: %s)."`
  - re-enable (WARNING): `"Worker %s reconnected after being disabled for not answering in time; enabling it again."`

## Review Focus

1. **A job group dict that `job_group_timeout` cannot read.** For example,
   jobs are missing or a key is missing. The dispatch must still happen,
   with the default timeout and an ERROR log, never a crash before the RPC.
   Test: `test_a_timeout_that_cannot_be_computed_keeps_the_default`
   (Task 3).
2. **The answer of a worker that `check_connections` released.** It must be
   ignored quietly, not raise "Trying to release worker while it's
   inactive". Test: `test_an_answer_after_check_connections_released_the_worker_is_ignored`
   (Task 2).
3. **The old answer of a timed-out job group, arriving after the worker came
   back and took a new group.** It must not release the new group. Test:
   `test_a_stale_answer_does_not_release_the_current_job_group` (Task 2).
4. **A worker that an operator disabled with the AWS Disable button.** It
   must stay disabled when it reconnects. Test:
   `test_a_worker_disabled_by_an_operator_stays_disabled` (Task 4).
5. **The first connection of an idle worker at startup.** It must change
   nothing and log no WARNING. Test:
   `test_the_first_connection_changes_nothing` (Task 4).

---

### Task 1: The per-job-group timeout function

**Files:**
- Modify: `cms/service/workerpool.py` (module level, above `class WorkerPool`)
- Test: `cmstestsuite/unit_tests/service/workerpool_test.py` (new class
  `TestJobGroupTimeout`)

**Interfaces:**
- Produces:
  - `JOB_OVERHEAD_S: int = 10`;
  - `job_group_timeout(job_group_dict: dict, compilation_time_limit_s: float, trusted_time_limit_s: float, minimum: timedelta) -> timedelta`.

- [ ] **Step 1: Write the failing tests.** Add to `workerpool_test.py`.
  - Extend the imports: `from datetime import timedelta`, and change the
    workerpool import to
    `from cms.service.workerpool import JOB_OVERHEAD_S, WorkerPool, job_group_timeout`.
  - Then add:

```python
MINIMUM = timedelta(seconds=600)


def _compilation_job() -> dict:
    return {"type": "compilation"}


def _evaluation_job(time_limit: float | None) -> dict:
    return {"type": "evaluation", "time_limit": time_limit}


class TestJobGroupTimeout(unittest.TestCase):
    """How long a worker may take on a job group."""

    @staticmethod
    def _timeout(jobs: list[dict], minimum: timedelta = MINIMUM) -> timedelta:
        # Compilation limit 20 s and trusted limit 10 s, as in
        # config/cms.sample.toml.
        return job_group_timeout({"jobs": jobs}, 20.0, 10.0, minimum)

    def test_the_overhead_per_job(self):
        self.assertEqual(JOB_OVERHEAD_S, 10)

    def test_compilations_get_their_wall_limit_and_the_overhead(self):
        # 25 x (2 x 20 + 1 + 10) = 1275 s.
        self.assertEqual(self._timeout([_compilation_job()] * 25),
                         timedelta(seconds=1275))

    def test_evaluations_get_two_user_stages_and_a_trusted_program(self):
        # 25 x (2 x (2 x 10 + 1) + (2 x 10 + 1) + 10) = 25 x 73 s.
        self.assertEqual(self._timeout([_evaluation_job(10.0)] * 25),
                         timedelta(seconds=1825))
        # 25 x (2 x (2 x 1 + 1) + 21 + 10) = 25 x 37 s.
        self.assertEqual(self._timeout([_evaluation_job(1.0)] * 25),
                         timedelta(seconds=925))

    def test_a_mixed_group_adds_its_jobs_up(self):
        # 51 s for the compilation, 37 s for the evaluation.
        self.assertEqual(
            self._timeout([_compilation_job(), _evaluation_job(1.0)],
                          minimum=timedelta(0)),
            timedelta(seconds=88))

    def test_the_timeout_is_never_below_the_minimum(self):
        self.assertEqual(self._timeout([_evaluation_job(1.0)]), MINIMUM)
        self.assertEqual(self._timeout([]), MINIMUM)

    def test_a_job_without_time_limit_gets_the_minimum(self):
        # Its sandbox has no wall limit, so the group cannot be bounded.
        jobs = [_evaluation_job(10.0)] * 25 + [_evaluation_job(None)]
        self.assertEqual(self._timeout(jobs), MINIMUM)
```

- [ ] **Step 2: Run the tests and see them fail.**
  - Run: `CMS_CONFIG=... .venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/service/workerpool_test.py -k JobGroupTimeout -v`
  - Expected: an ImportError (`JOB_OVERHEAD_S`, `job_group_timeout`).

- [ ] **Step 3: Implement.** In `cms/service/workerpool.py`, after
  `logger = logging.getLogger(__name__)`, add:

```python
# Seconds a job needs besides its sandbox runs: fetching its files,
# creating and cleaning up its sandboxes.
JOB_OVERHEAD_S = 10


def _wall_limit(time_limit_s: float) -> float:
    """Return the wall-clock limit of a sandbox with a CPU time limit.

    time_limit_s (float): the CPU time limit, in seconds.

    return (float): the wall-clock limit the grading steps give it.

    """
    return 2 * time_limit_s + 1


def job_group_timeout(
    job_group_dict: dict,
    compilation_time_limit_s: float,
    trusted_time_limit_s: float,
    minimum: timedelta,
) -> timedelta:
    """Return how long a worker may take on a job group.

    Every sandbox run stops at its wall-clock limit, so a job group
    takes at most the sum of the wall limits of the sandboxes its jobs
    can run, plus JOB_OVERHEAD_S per job. An evaluation job runs at
    most two user stages (TwoSteps; the processes of Communication run
    side by side) and one trusted program (the checker, or the
    manager).

    job_group_dict (dict): the job group, as JobGroup.export_to_dict()
        returns it.
    compilation_time_limit_s (float): the CPU limit of a compilation.
    trusted_time_limit_s (float): the CPU limit of a trusted program.
    minimum (timedelta): the least time to allow; also the time for a
        group with a job without time limit, whose sandbox has no wall
        limit either.

    return (timedelta): the time after which the worker counts as not
        answering.

    """
    total = 0.0
    for job in job_group_dict["jobs"]:
        if job["type"] == "compilation":
            total += _wall_limit(compilation_time_limit_s)
        else:
            time_limit = job["time_limit"]
            if time_limit is None:
                return minimum
            total += (2 * _wall_limit(time_limit)
                      + _wall_limit(trusted_time_limit_s))
        total += JOB_OVERHEAD_S
    return max(minimum, timedelta(seconds=total))
```

- [ ] **Step 4: Run the tests and see them pass.** Use the same command as
  Step 2; expect 6 passed. Also run the whole `workerpool_test.py` and
  expect every test to pass.

- [ ] **Step 5: Lint and commit.**

```bash
uvx -q pyflakes cms/service/workerpool.py cmstestsuite/unit_tests/service/workerpool_test.py
git add cms/service/workerpool.py cmstestsuite/unit_tests/service/workerpool_test.py
git commit -m "feat(evaluation): bound a job group's run time by its sandbox limits" -m "Refs #40" -m "Co-Authored-By: <your model name> <noreply@anthropic.com>"
```

---

### Task 2: Dispatch ids, and stale answers ignored

**Files:**
- Modify: `cms/service/workerpool.py`:
  - `__init__`, `add_worker`, `acquire_worker`, `_build_and_dispatch`;
  - `_dispatch_to_worker`, `_report_action_finished`, `release_worker`.
- Modify: `cms/service/EvaluationService.py`: `action_finished` and
  `_action_finished_sync` (around lines 911-938).
- Modify: `cmstestsuite/unit_tests/service/evaluationservice_failures_test.py`,
  the two wrappers around `action_finished` (around lines 443-445 and
  827-829).
- Test: `cmstestsuite/unit_tests/service/workerpool_test.py` (new class
  `TestDispatchIds`, plus an update of the existing
  `test_job_group_is_awaited_however_long_it_takes`).
- Test: `cmstestsuite/unit_tests/service/EvaluationService_test.py` (one test
  in `EvaluationServiceTest`).

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `WorkerPool._current_dispatch: dict[int, int | None]`;
  - `WorkerPool._last_dispatch_id: int`;
  - `WorkerPool._build_and_dispatch(shard, operations, dispatch_id: int | None = None)`;
  - `WorkerPool._dispatch_to_worker(shard, job_group_dict, dispatch_id: int | None = None)`;
  - `WorkerPool._report_action_finished(data, shard, error, dispatch_id: int | None = None)`;
  - `WorkerPool.release_worker(shard, dispatch_id: int | None = None) -> bool | list[ESOperation]`;
  - `EvaluationService.action_finished(data, shard, error=None, dispatch_id: int | None = None)`;
  - `EvaluationService._action_finished_sync(data, shard, error=None, dispatch_id=None)`.

- [ ] **Step 1: Write the failing tests.**
  - In `workerpool_test.py`, change the last line of
    `test_job_group_is_awaited_however_long_it_takes` to
    `service.action_finished.assert_awaited_once_with({"jobs": []}, 0, None, dispatch_id=None)`.
  - Add the classes below (`TestDispatchToWorker` gets the second method):

```python
class TestDispatchIdOfTheAnswer(unittest.IsolatedAsyncioTestCase):

    async def test_dispatch_passes_its_id_to_action_finished(self):
        service = MagicMock()
        service.action_finished = AsyncMock()
        pool = WorkerPool(service)
        worker = MagicMock()
        worker.execute_job_group = AsyncMock(return_value={"jobs": []})
        pool._worker[0] = worker

        await pool._dispatch_to_worker(0, {"jobs": []}, 5)

        service.action_finished.assert_awaited_once_with(
            {"jobs": []}, 0, None, dispatch_id=5)


def _single_worker_pool() -> WorkerPool:
    """Return a pool with one connected worker and nothing dispatched."""
    service = MagicMock()
    service._loop = None
    service.contest_id = None
    # Nothing is dispatched for real: drop the spawned coroutines.
    service._spawn.side_effect = lambda coroutine: coroutine.close()
    service.connect_to.side_effect = \
        lambda coord, on_connect: MagicMock(connected=True)
    pool = WorkerPool(service)
    pool.add_worker(ServiceCoord("Worker", 0))
    return pool


def _operation(testcase_codename: str) -> ESOperation:
    return ESOperation(ESOperation.EVALUATION, 42, 7, testcase_codename)


class TestDispatchIds(unittest.TestCase):
    """Each job group has an id; an answer with another id is stale."""

    def setUp(self):
        self.pool = _single_worker_pool()

    def test_each_job_group_gets_a_new_id(self):
        shard = self.pool.acquire_worker([_operation("001")])
        first = self.pool._current_dispatch[shard]
        self.assertIsNotNone(first)
        self.pool.release_worker(shard, first)
        self.pool.acquire_worker([_operation("002")])
        self.assertNotEqual(self.pool._current_dispatch[shard], first)

    def test_the_current_answer_releases_the_worker(self):
        shard = self.pool.acquire_worker([_operation("001")])
        dispatch = self.pool._current_dispatch[shard]

        self.assertIs(self.pool.release_worker(shard, dispatch), False)

        self.assertIs(self.pool._operations[shard],
                      WorkerPool.WORKER_INACTIVE)
        self.assertIsNone(self.pool._current_dispatch[shard])

    def test_a_stale_answer_does_not_release_the_current_job_group(self):
        shard = self.pool.acquire_worker([_operation("001")])
        old = self.pool._current_dispatch[shard]
        # Something else released the worker (e.g. the timeout), and it
        # took a new job group before the old answer arrived.
        self.pool.release_worker(shard)
        self.assertEqual(self.pool.acquire_worker([_operation("002")]), shard)
        current = self.pool._current_dispatch[shard]

        with self.assertLogs("cms.service.workerpool", level="INFO") as logs:
            self.assertIs(self.pool.release_worker(shard, old), True)

        self.assertIn("stale answer", "\n".join(logs.output))
        self.assertEqual(self.pool._operations[shard], [_operation("002")])
        self.assertEqual(self.pool._current_dispatch[shard], current)

    def test_an_answer_after_check_connections_released_the_worker_is_ignored(
            self):
        operation = _operation("001")
        shard = self.pool.acquire_worker([operation])
        dispatch = self.pool._current_dispatch[shard]
        self.pool._worker[shard].connected = False
        self.assertEqual(self.pool.check_connections(), [operation])

        # The RPC error of the dropped connection arrives afterwards.
        self.assertIs(self.pool.release_worker(shard, dispatch), True)

        self.assertIs(self.pool._operations[shard],
                      WorkerPool.WORKER_INACTIVE)

    def test_a_release_without_id_behaves_as_before(self):
        shard = self.pool.acquire_worker([_operation("001")])
        self.assertIs(self.pool.release_worker(shard), False)
        with self.assertLogs("cms.service.workerpool", level="ERROR"):
            with self.assertRaises(ValueError):
                self.pool.release_worker(shard)
```

  - In `EvaluationService_test.py`, in class `EvaluationServiceTest`, add
    (`patch` comes from `unittest.mock`; import it if the file does not):

```python
    async def test_action_finished_passes_the_dispatch_id_to_the_pool(self):
        pool = self.service.get_executor().pool
        with patch.object(pool, "release_worker",
                          return_value=True) as release_worker:
            await self.service.action_finished(
                {"jobs": []}, 0, None, dispatch_id=7)
        release_worker.assert_called_once_with(0, 7)
```

- [ ] **Step 2: Run the tests and see them fail.**
  - Run: `.venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/service/workerpool_test.py cmstestsuite/unit_tests/service/EvaluationService_test.py -k "Dispatch or dispatch_id" -v`
  - Expected:
    - `KeyError` or `AttributeError` for `_current_dispatch`;
    - a `TypeError` for the `dispatch_id` keyword;
    - assertion failures.

- [ ] **Step 3: Implement in `workerpool.py`.**
  - **In `__init__`**, after `self._ignore: dict[int, bool] = {}`:

```python
        # The id of the job group each worker is working on, None while
        # it works on none. acquire_worker takes ids from
        # _last_dispatch_id, and every release clears the worker's. An
        # answer that carries another id is stale (see release_worker).
        self._current_dispatch: dict[int, int | None] = {}
        self._last_dispatch_id = 0
```

  - **In `add_worker`**, next to `self._start_time[shard] = None`, add
    `self._current_dispatch[shard] = None`.
  - **In `acquire_worker`**:
    - Inside the `with self._operation_lock:` block, replace
      `self._start_time[shard] = make_datetime()` with:

```python
            self._start_time[shard] = make_datetime()
            self._last_dispatch_id += 1
            dispatch_id = self._last_dispatch_id
            self._current_dispatch[shard] = dispatch_id
```

    - Replace the spawn line with
      `self._service._spawn(self._build_and_dispatch(shard, operations, dispatch_id))`,
      wrapped to fit 79 columns.
  - **`_build_and_dispatch`:**
    - Add a parameter `dispatch_id: int | None = None` and document it in
      the docstring: "dispatch_id: the id acquire_worker gave the job group."
    - The failure branch becomes
      `await self._report_action_finished(None, shard, str(build_error), dispatch_id)`.
    - The last line becomes
      `await self._dispatch_to_worker(shard, job_group_dict, dispatch_id)`.
  - **`_dispatch_to_worker`:** add the same parameter (default None) and
    docstring line. The last line becomes
    `await self._report_action_finished(data, shard, error, dispatch_id)`.
  - **`_report_action_finished`:** add `dispatch_id: int | None = None` and
    document it. Call
    `await self._service.action_finished(data, shard, error, dispatch_id=dispatch_id)`.
  - **`release_worker`:**
    - New signature: `def release_worker(self, shard: int, dispatch_id: int | None = None) -> bool | list[ESOperation]:`.
    - Add to the docstring: "dispatch_id: the job group the answer belongs
      to, or None if unknown. An answer for another job group than the
      worker's current one is stale: it is ignored, and the worker is left
      as it is."
    - Make this the first statement inside `with self._operation_lock:`:

```python
            if dispatch_id is not None and \
                    dispatch_id != self._current_dispatch.get(shard):
                logger.info("Ignoring a stale answer from worker %s "
                            "(job group %s; current: %s).", shard,
                            dispatch_id, self._current_dispatch.get(shard))
                return True
```

    - Next to `self._start_time[shard] = None` (the releasing branch), add
      `self._current_dispatch[shard] = None`.

- [ ] **Step 4: Implement in `EvaluationService.py`.**
  - **`action_finished`:**
    - Signature: `async def action_finished(self, data: dict, shard: int, error=None, dispatch_id: int | None = None):`.
    - Docstring line: "dispatch_id: the job group this answer belongs to
      (see WorkerPool.acquire_worker), or None if unknown."
    - Pass the id: `await loop.run_in_executor(None, self._action_finished_sync, data, shard, error, dispatch_id)`.
  - **`_action_finished_sync`:**
    - Signature: `def _action_finished_sync(self, data: dict, shard: int, error=None, dispatch_id: int | None = None):`.
    - Call `to_ignore = self.get_executor().pool.release_worker(shard, dispatch_id)`.

- [ ] **Step 5: Update the two test wrappers** in
  `evaluationservice_failures_test.py`, so they accept and forward the new
  keyword. The recorded tuple stays `(data, shard, error)`.

```python
        async def recording_action_finished(data, shard, error=None,
                                            dispatch_id=None):
            calls.append((data, shard, error))
            return await real_action_finished(data, shard, error,
                                              dispatch_id=dispatch_id)
```

```python
        async def signalling_action_finished(data, shard, error=None,
                                             dispatch_id=None):
            answer_arrived.set()
            await real_action_finished(data, shard, error,
                                       dispatch_id=dispatch_id)
```

  Then search the tests for any other replacement of `action_finished` that
  takes only `(data, shard, error)`, and update it the same way:
  `grep -rn "def .*action_finished" cmstestsuite`.

- [ ] **Step 6: Run the tests and see them pass.**
  - `workerpool_test.py`: everything passes.
  - The asyncio EvaluationService files must all pass:
    - `EvaluationService_test.py`;
    - `evaluationservice_failures_test.py`;
    - `evaluationservice_lock_order_test.py`;
    - `evaluationservice_loop_actions_test.py`;
    - `twophase_e2e_test.py`.
  - Then run the two gevent files in their own process.

- [ ] **Step 7: Lint and commit.**

```bash
git add cms/service/workerpool.py cms/service/EvaluationService.py cmstestsuite/unit_tests/service/
git commit -m "fix(evaluation): ignore a worker's answer for a job group it no longer has" -m "Refs #40" -m "Co-Authored-By: <your model name> <noreply@anthropic.com>"
```

---

### Task 3: Answered workers and the derived timeout in check_timeouts

**Files:**
- Modify: `cms/service/workerpool.py`:
  - imports: `from cms import config`;
  - `__init__`, `add_worker`, `acquire_worker`, `_build_and_dispatch`;
  - `_dispatch_to_worker`, `release_worker`, `check_timeouts`;
  - new methods `_mark_answered` and `_set_timeout`.
- Test: `cmstestsuite/unit_tests/service/workerpool_test.py` (new class
  `TestTimeouts`).

**Interfaces:**
- Consumes:
  - Task 1's `job_group_timeout`;
  - Task 2's `_current_dispatch`, the `dispatch_id` parameters and
    `_single_worker_pool()` and `_operation()` in the test file.
- Produces:
  - `WorkerPool.ANSWER_HANDLING_TIMEOUT = timedelta(minutes=30)`;
  - `WorkerPool._answered_at: dict[int, datetime | None]`;
  - `WorkerPool._timeout: dict[int, timedelta]`;
  - `WorkerPool._mark_answered(shard: int, dispatch_id: int | None) -> None`;
  - `WorkerPool._set_timeout(shard: int, dispatch_id: int | None, job_group_dict: dict) -> None`.

- [ ] **Step 1: Write the failing tests.**
  - Extend the imports: `from cmscommon.datetime import make_datetime`.
  - Then add:

```python
TEN_SECOND_EVALUATIONS = {
    "jobs": [{"type": "evaluation", "time_limit": 10.0}] * 25}


class TestTimeouts(unittest.TestCase):
    """When check_timeouts gives up on a worker."""

    def setUp(self):
        self.pool = _single_worker_pool()
        self.operation = _operation("001")
        self.shard = self.pool.acquire_worker([self.operation])
        self.dispatch = self.pool._current_dispatch[self.shard]

    def _busy_for(self, seconds: float):
        self.pool._start_time[self.shard] = \
            make_datetime() - timedelta(seconds=seconds)

    def _set_ten_second_timeout(self, dispatch_id: int | None):
        with patch("cms.service.workerpool.config") as config:
            config.sandbox.compilation_sandbox_max_time_s = 20.0
            config.sandbox.trusted_sandbox_max_time_s = 10.0
            self.pool._set_timeout(
                self.shard, dispatch_id, TEN_SECOND_EVALUATIONS)

    def test_a_worker_past_its_timeout_is_disabled(self):
        self._busy_for(601)

        with self.assertLogs("cms.service.workerpool", level="ERROR") as logs:
            lost = self.pool.check_timeouts()

        self.assertEqual(lost, [self.operation])
        self.assertEqual(self.pool._operations[self.shard],
                         WorkerPool.WORKER_DISABLED)
        self.assertIn("(timeout 0:10:00)", logs.output[0])

    def test_a_worker_within_its_derived_timeout_is_not_disabled(self):
        self._set_ten_second_timeout(self.dispatch)
        self.assertEqual(self.pool._timeout[self.shard],
                         timedelta(seconds=1825))

        self._busy_for(1000)
        self.assertEqual(self.pool.check_timeouts(), [])
        self.assertEqual(self.pool._operations[self.shard], [self.operation])

        self._busy_for(1826)
        with self.assertLogs("cms.service.workerpool", level="ERROR"):
            self.assertEqual(self.pool.check_timeouts(), [self.operation])

    def test_the_timeout_of_a_stale_dispatch_is_not_stored(self):
        self._set_ten_second_timeout(self.dispatch + 1)
        self.assertEqual(self.pool._timeout[self.shard],
                         WorkerPool.WORKER_TIMEOUT)

    def test_a_timeout_that_cannot_be_computed_keeps_the_default(self):
        with self.assertLogs("cms.service.workerpool", level="ERROR"):
            self.pool._set_timeout(self.shard, self.dispatch, {"no": "jobs"})
        self.assertEqual(self.pool._timeout[self.shard],
                         WorkerPool.WORKER_TIMEOUT)

    def test_an_answered_worker_is_not_timed_out(self):
        self._busy_for(10_000)
        self.pool._mark_answered(self.shard, self.dispatch)

        self.assertIsNotNone(self.pool._answered_at[self.shard])
        self.assertEqual(self.pool.check_timeouts(), [])
        self.assertEqual(self.pool._operations[self.shard], [self.operation])

    def test_an_answer_not_handled_in_time_disables_the_worker(self):
        self.pool._mark_answered(self.shard, self.dispatch)
        self.pool._answered_at[self.shard] = (
            make_datetime() - WorkerPool.ANSWER_HANDLING_TIMEOUT
            - timedelta(seconds=1))

        with self.assertLogs("cms.service.workerpool", level="ERROR") as logs:
            lost = self.pool.check_timeouts()

        self.assertEqual(lost, [self.operation])
        self.assertEqual(self.pool._operations[self.shard],
                         WorkerPool.WORKER_DISABLED)
        self.assertIn("was not handled", logs.output[0])

    def test_a_stale_answer_does_not_mark_the_current_dispatch(self):
        self.pool._mark_answered(self.shard, self.dispatch + 1)
        self.assertIsNone(self.pool._answered_at[self.shard])

    def test_the_release_clears_the_answer(self):
        self.pool._mark_answered(self.shard, self.dispatch)
        self.pool.release_worker(self.shard, self.dispatch)
        self.assertIsNone(self.pool._answered_at[self.shard])

    def test_marking_an_unknown_worker_does_nothing(self):
        self.pool._mark_answered(99, 1)
        self.pool._mark_answered(self.shard, None)
        self.assertIsNone(self.pool._answered_at[self.shard])
```

- [ ] **Step 2: Run the tests and see them fail.**
  - Run: `... -m pytest -p no:cacheprovider cmstestsuite/unit_tests/service/workerpool_test.py -k Timeouts -v`
  - Expected: `AttributeError` (`_set_timeout`, `_mark_answered`,
    `_answered_at`, `ANSWER_HANDLING_TIMEOUT`).

- [ ] **Step 3: Implement.**
  - **Imports and constants.** Add `from cms import config` to the imports.
    In `WorkerPool`, after `WORKER_TIMEOUT`, add:

```python
    # How long an answer may wait for ES to handle it before the worker
    # counts as lost anyway (see check_timeouts).
    ANSWER_HANDLING_TIMEOUT = timedelta(minutes=30)
```

    Change the comment of `WORKER_TIMEOUT` to: "The least time a worker
    may take on a job group (see job_group_timeout)."
  - **`__init__`**, after the Task 2 block:

```python
        # When each worker answered its current job group (None while it
        # works on it), and how long it may take on it.
        self._answered_at: dict[int, datetime | None] = {}
        self._timeout: dict[int, timedelta] = {}
```

  - **`add_worker` and `acquire_worker`** (inside the lock, next to the
    start time): set `self._answered_at[shard] = None` and
    `self._timeout[shard] = WorkerPool.WORKER_TIMEOUT`.
  - **`release_worker`**, releasing branch: add
    `self._answered_at[shard] = None`.
  - **New methods**, placed after `_report_action_finished`:

```python
    def _mark_answered(self, shard: int, dispatch_id: int | None):
        """Note that a worker answered a job group.

        From then on it cannot time out for being slow (see
        check_timeouts): its answer only waits for ES.

        shard (int): the worker that answered.
        dispatch_id (int|None): the job group it answered, or None if
            unknown (then nothing is noted).

        """
        if dispatch_id is None:
            return
        with self._operation_lock:
            if self._current_dispatch.get(shard) == dispatch_id:
                self._answered_at[shard] = make_datetime()

    def _set_timeout(self, shard: int, dispatch_id: int | None,
                     job_group_dict: dict):
        """Set how long a worker may take on the job group it was given.

        Assumes that ES and the workers read the same [sandbox] limits,
        as in the Docker deployment, which generates one cms.toml.

        shard (int): the worker.
        dispatch_id (int|None): the job group, or None if unknown (then
            the default stays).
        job_group_dict (dict): the job group, exported to dict.

        """
        if dispatch_id is None:
            return
        try:
            timeout = job_group_timeout(
                job_group_dict,
                config.sandbox.compilation_sandbox_max_time_s,
                config.sandbox.trusted_sandbox_max_time_s,
                WorkerPool.WORKER_TIMEOUT)
        except Exception:
            logger.error("Cannot compute the timeout of the job group of "
                         "worker %s; it gets %s.", shard,
                         WorkerPool.WORKER_TIMEOUT, exc_info=True)
            return
        with self._operation_lock:
            if self._current_dispatch.get(shard) == dispatch_id:
                self._timeout[shard] = timeout
```

  - **`_build_and_dispatch`:** right before the final
    `await self._dispatch_to_worker(...)`, add
    `self._set_timeout(shard, dispatch_id, job_group_dict)`.
  - **`_dispatch_to_worker`:** after the `try/except RPCError` block and
    before `await self._report_action_finished(...)`, add
    `self._mark_answered(shard, dispatch_id)`.
  - **`check_timeouts`:**
    - Replace the body of the `for shard in self._worker:` loop up to and
      including the `logger.error(...)` call with the block below.
    - Keep the rest as it is: the `is_busy` assert, the collection of
      `lost_operations`, `_schedule_disabling`, `_ignore` and
      `release_worker`.
    - The `quit` call becomes `self._worker[shard].quit(reason=reason)`.
    - Update the docstring to say that the timeout is the job group's own
      (see `_set_timeout`), and that an answered worker gets
      `ANSWER_HANDLING_TIMEOUT` instead.

```python
                if self._start_time[shard] is None:
                    continue
                answered_at = self._answered_at[shard]
                if answered_at is not None:
                    waiting_for = now - answered_at
                    if waiting_for <= WorkerPool.ANSWER_HANDLING_TIMEOUT:
                        continue
                    logger.error("Disabling and shutting down worker %d: "
                                 "it answered %s ago but its result was "
                                 "not handled in %s.", shard, waiting_for,
                                 WorkerPool.ANSWER_HANDLING_TIMEOUT)
                    reason = "Result not handled in %s." % waiting_for
                else:
                    active_for = now - self._start_time[shard]
                    if active_for <= self._timeout[shard]:
                        continue
                    # Here shard is a working worker with no sign of
                    # intelligent life for too much time.
                    logger.error("Disabling and shutting down worker %d "
                                 "because of no response in %s (timeout "
                                 "%s).", shard, active_for,
                                 self._timeout[shard])
                    reason = "No response in %s." % active_for
```

    This keeps one indentation level less than today's nested `if`s.
    Re-indent the kept code to match.

- [ ] **Step 4: Run the tests and see them pass.** Run the whole of
  `workerpool_test.py`, then the asyncio EvaluationService files and the
  two gevent files in their own process.
- [ ] **Step 5: Lint and commit.**

```bash
git add cms/service/workerpool.py cmstestsuite/unit_tests/service/workerpool_test.py
git commit -m "fix(evaluation): time out a worker by its job group, not once it answered" -m "Refs #40" -m "Co-Authored-By: <your model name> <noreply@anthropic.com>"
```

---

### Task 4: Re-enable a timeout-disabled worker on reconnect

**Files:**
- Modify: `cms/service/workerpool.py`: `__init__`, `add_worker`,
  `on_worker_connected`, `check_timeouts`, `disable_worker`,
  `enable_worker`.
- Test: `cmstestsuite/unit_tests/service/workerpool_test.py` (new class
  `TestReenableOnReconnect`).

**Interfaces:**
- Consumes:
  - Task 3's `check_timeouts` and `_mark_answered`;
  - the test helpers `_single_worker_pool()` and `_operation()`.
- Produces: `WorkerPool._disabled_by_timeout: dict[int, bool]`.

- [ ] **Step 1: Write the failing tests.**

```python
class TestReenableOnReconnect(unittest.TestCase):
    """A worker the timeout disabled comes back when it reconnects."""

    def setUp(self):
        self.pool = _single_worker_pool()
        self.coord = ServiceCoord("Worker", 0)

    def _time_out(self) -> int:
        shard = self.pool.acquire_worker([_operation("001")])
        self.pool._start_time[shard] = \
            make_datetime() - timedelta(seconds=601)
        with self.assertLogs("cms.service.workerpool", level="ERROR"):
            self.pool.check_timeouts()
        self.assertEqual(self.pool._operations[shard],
                         WorkerPool.WORKER_DISABLED)
        return shard

    def test_a_worker_disabled_by_the_timeout_is_enabled_on_reconnect(self):
        shard = self._time_out()
        self.assertTrue(self.pool._disabled_by_timeout[shard])

        with self.assertLogs("cms.service.workerpool", level="WARNING") as logs:
            self.pool.on_worker_connected(self.coord)

        self.assertIs(self.pool._operations[shard],
                      WorkerPool.WORKER_INACTIVE)
        self.assertFalse(self.pool._disabled_by_timeout[shard])
        self.assertIn("enabling it again", "\n".join(logs.output))

    def test_a_worker_disabled_after_its_answer_waited_comes_back_too(self):
        shard = self.pool.acquire_worker([_operation("001")])
        self.pool._mark_answered(shard, self.pool._current_dispatch[shard])
        self.pool._answered_at[shard] = (
            make_datetime() - WorkerPool.ANSWER_HANDLING_TIMEOUT
            - timedelta(seconds=1))
        with self.assertLogs("cms.service.workerpool", level="ERROR"):
            self.pool.check_timeouts()

        with self.assertLogs("cms.service.workerpool", level="WARNING"):
            self.pool.on_worker_connected(self.coord)

        self.assertIs(self.pool._operations[shard],
                      WorkerPool.WORKER_INACTIVE)

    def test_a_worker_disabled_by_an_operator_stays_disabled(self):
        self.pool.disable_worker(0)

        self.pool.on_worker_connected(self.coord)

        self.assertEqual(self.pool._operations[0], WorkerPool.WORKER_DISABLED)
        self.assertFalse(self.pool._disabled_by_timeout[0])

    def test_enabling_by_hand_clears_the_mark(self):
        shard = self._time_out()
        self.pool.enable_worker(shard)
        self.assertFalse(self.pool._disabled_by_timeout[shard])

    def test_the_first_connection_changes_nothing(self):
        with self.assertNoLogs("cms.service.workerpool", level="WARNING"):
            self.pool.on_worker_connected(self.coord)
        self.assertIs(self.pool._operations[0], WorkerPool.WORKER_INACTIVE)
```

- [ ] **Step 2: Run the tests and see them fail.** Run:
  `... -k ReenableOnReconnect -v`. Expected: `KeyError` or
  `AttributeError` for `_disabled_by_timeout`, and assertion failures.

- [ ] **Step 3: Implement.**
  - **`__init__`:**

```python
        # Whether each disabled worker was disabled by check_timeouts
        # (then it is enabled again when it reconnects) rather than by
        # disable_worker.
        self._disabled_by_timeout: dict[int, bool] = {}
```

  - **`add_worker`:** `self._disabled_by_timeout[shard] = False`.
  - **`check_timeouts`:** right after `self._ignore[shard] = True`, add
    `self._disabled_by_timeout[shard] = True`.
  - **`disable_worker`:** right after the "already disabled" check, add
    `self._disabled_by_timeout[shard] = False`.
  - **`enable_worker`:** next to `self._operations_to_ignore[shard] = []`,
    add `self._disabled_by_timeout[shard] = False`.
  - **`on_worker_connected`:** right after
    `logger.info("Worker %s online again.", shard)`, add:

```python
        with self._operation_lock:
            if self._operations[shard] == WorkerPool.WORKER_DISABLED and \
                    self._disabled_by_timeout[shard]:
                logger.warning("Worker %s reconnected after being disabled "
                               "for not answering in time; enabling it "
                               "again.", shard)
                self.enable_worker(shard)
```

    Also add one line to its docstring: "A worker check_timeouts disabled
    is enabled again: it was told to quit, and reconnecting means it
    restarted."

- [ ] **Step 4: Run the tests and see them pass.** Run the whole of
  `workerpool_test.py`, the asyncio EvaluationService files and the gevent
  files in their own process.
- [ ] **Step 5: Lint and commit.**

```bash
git add cms/service/workerpool.py cmstestsuite/unit_tests/service/workerpool_test.py
git commit -m "fix(evaluation): enable a worker again when it reconnects after a timeout" -m "Refs #40" -m "Co-Authored-By: <your model name> <noreply@anthropic.com>"
```

---

### Task 5: Contest-day runbook

**Files:**
- Modify: `docs/contest-day.md` (the paragraph "**A worker was disabled:**",
  around lines 127-132).
- Modify: `docs/locale/es/LC_MESSAGES/contest-day.po` (its msgid and
  msgstr, around line 337).

- [ ] **Step 1: Replace the English paragraph** with exactly:

```markdown
- **A worker was disabled:** a worker that does not finish its jobs in
  time is disabled and its jobs go back to the queue (`./logs.sh cms` says
  "Disabling and shutting down worker" and "put again in the queue because
  of worker timeout"; these messages are not in the Overview **Logs**
  table). The time allowed grows with the jobs: at least 10 minutes, about
  15 minutes for a full batch of evaluations with a 1 s time limit and
  about 30 minutes with a 10 s one. The worker is told to quit; when it has
  restarted and reconnects it is enabled again by itself (the log says
  "enabling it again"). Press **Enable** on its row of **Workers status**
  only for a worker that stays disabled, for example one disabled with the
  **Disable** button.
```

- [ ] **Step 2: Update the `.po` entry.**
  - Find the msgid that starts with `"**A worker was disabled:**`.
  - Replace it with the new English text, wrapped the way the file wraps
    msgids. The simplest way is to regenerate the entry with the docs
    tooling the repo uses. Read `.github/workflows/docs.yml` (or the docs
    job) and `docs/README` or `docs/Makefile` for the gettext and
    sphinx-intl steps. Do not regenerate the whole catalog by hand.
  - Write a natural Spanish msgstr with correct accents, using the terms in
    `GLOSSARY.md` (e.g. worker, cola). Remove any `fuzzy` flag.
  - Keep the inline code, the bold markers and the quoted log messages
    exactly as they are. Log messages are not translated.
- [ ] **Step 3: Run the docs checks** on a copy, so no `.mo` or `_build`
  files land in the worktree. For example, `git archive HEAD` plus the
  working changes, unpacked into `/home/areslolxd/.claude/jobs/4c257a1a/tmp/docs-check/`.
  - Run the commands of the docs CI job:
    - `sphinx-build -W` for English and Spanish;
    - `docs/tests`;
    - `docs/check_translations.py`.
  - Expected: all pass.
- [ ] **Step 4: Commit.**

```bash
git add docs/contest-day.md docs/locale/es/LC_MESSAGES/contest-day.po
git commit -m "docs(contest-day): say that a timed-out worker comes back by itself" -m "Refs #40" -m "Co-Authored-By: <your model name> <noreply@anthropic.com>"
```
