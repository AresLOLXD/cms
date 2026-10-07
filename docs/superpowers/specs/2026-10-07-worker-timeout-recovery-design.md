# Workers Disabled by the Job Timeout Come Back — Design Doc

**Date:** 2026-10-07
**Issue:** #40 (wave 1 of `2026-10-06-post-10-10-roadmap-design.md`)
**Status:** Designed and approved autonomously. The user asked for the
backlog to advance without stopping, always through brainstorming.
Decisions marked *(autonomous)* took the recommended option and can be
revisited.

## Problem

EvaluationService's `WorkerPool` disables a worker for good when one of its
job groups is not finished within `WORKER_TIMEOUT` (600 s).

- `check_timeouts` (`cms/service/workerpool.py:467-514`) runs every 300 s
  on the event loop. It does three things:
  - re-queues the worker's operations;
  - marks the worker `WORKER_DISABLED`;
  - sends it `quit`.

  Supervisord restarts the Worker process and ES reconnects to it
  (`on_worker_connected`), but the worker stays disabled. Only the AWS
  Overview **Enable** button calls `enable_worker`.
- **The clock stops only in `release_worker`.** That is called from
  `_action_finished_sync`, which needs `post_finish_lock`. So when ES itself
  stalls for 10 minutes or more (a DB stall, a long rejudge), every busy
  worker is disabled at once. That includes workers that answered long ago
  and whose results are only waiting for the lock.
- **A legitimate job group can exceed 600 s.**
  - A group holds up to 25 operations (`MAX_OPERATIONS_PER_BATCH`), each
    bounded only by its own sandbox limits:
    - user program: wall 2 × TL + 1 s;
    - checker or manager: wall 2 × 10 + 1 s;
    - compilation: wall 2 × 20 + 1 s since `1.6.dev0+omi.3`.
  - So 25 compilations that each run to the wall limit take 1025 s.
  - The 2026-10-07 review of the compile-limit hotfix flagged this as the
    one Important risk of that change.

**The effect in a contest:** a stall or one heavy batch silently removes
capacity for the rest of the contest. Nobody notices unless an operator
watches the Overview.

## Goal

1. A worker that answered is never disabled because ES is slow to process
   its answer.
2. A worker disabled by the timeout is used again as soon as it reconnects.
3. A job group within its own sandbox limits never hits the timeout.
4. A worker that really hangs is still caught.

## Approaches Considered

- **A (chosen): fix the three causes in `WorkerPool`.**
  - Stop the clock when the worker answers.
  - Re-enable a timeout-disabled worker on reconnect.
  - Derive the timeout from the job group's own limits.

  All of it is local to `WorkerPool`, and it keeps today's detection of a
  hung worker. *(autonomous)*
- **B (rejected): only make the 600 s configurable.** It is cheap, but an
  operator would need to know the worst case of every batch. It also does
  nothing for the ES stall or for the permanent disable.
- **C (rejected): a heartbeat from the Worker during a job group.** It is
  the most precise, but it changes the Worker RPC protocol and the gevent
  Worker, which is wave-3 work (#20, #21).

## Design

### 1. Stop the clock when the worker answers

- **New per-worker state in `WorkerPool`, guarded by `_operation_lock` like
  the rest:**
  - `_dispatch_id[shard]` (int): incremented by `acquire_worker` each time it
    gives the worker a job group;
  - `_answered_at[shard]` (`datetime | None`): None while the worker is
    working;
  - `_timeout[shard]` (`timedelta`): see section 3.
- **`_build_and_dispatch` and `_dispatch_to_worker` carry the dispatch id**
  they were spawned with.
- **When the `execute_job_group` RPC returns, with a result or an
  `RPCError`,** `_dispatch_to_worker` calls `_mark_answered(shard,
  dispatch_id)` on the event loop, before `action_finished`. That call sets
  `_answered_at[shard] = now` only if:
  - the worker is still busy, and
  - `_dispatch_id[shard]` still equals the id it carries.

  So a late answer from an earlier dispatch, for example the RPC error of a
  connection the worker dropped when it quit, never touches the current one.
- **`check_timeouts` skips an answered worker,** unless it has been waiting
  for more than `ANSWER_HANDLING_TIMEOUT` (30 min, a class constant). This
  backstop keeps a worker from being lost for good if handling its answer
  fails before `release_worker`. Such a worker is disabled and re-queued
  like a timed-out one, with its own log line ("answered ... but its result
  was not handled in ..."). *(autonomous)*
- **`release_worker` resets `_answered_at[shard]` to None.**
- **`_start_time` is unchanged,** so the Overview still shows how long the
  worker has been busy.

### 2. Re-enable a timeout-disabled worker on reconnect

- **New state `_disabled_by_timeout[shard]` (bool):**
  - `check_timeouts` sets it to True when it disables the worker, for both
    the job-timeout reason and the answer-handling reason;
  - `disable_worker` (the AWS **Disable** button) and `enable_worker` set it
    to False. So a worker an operator disabled stays disabled.
- **`on_worker_connected`, under `_operation_lock`,** checks whether the
  worker is `WORKER_DISABLED` and `_disabled_by_timeout` is set. If so, it
  enables the worker again through the same code as `enable_worker`, and
  logs a WARNING: "Worker %s reconnected after being disabled for not
  answering in time; enabling it again."
- **After a timeout, `quit` makes the Worker exit,** supervisord restarts it,
  and the reconnect enables it. A worker that ignores `quit` and keeps its
  connection stays disabled, as today, and the operator can press
  **Enable**.
- **There is no cap on repeated re-enables.** A worker that times out again
  is disabled again, and each cycle re-queues its operations. Repeated
  cycles show as repeated WARNING lines. A cap is left out until a real
  case needs it.

### 3. A timeout derived from the job group

- **A pure function** (in `workerpool.py`, no DB, no config import inside):

  ```python
  def job_group_timeout(
      job_group_dict: dict, compilation_time_limit_s: float,
      trusted_time_limit_s: float, minimum: timedelta,
  ) -> timedelta
  ```

  It returns `minimum` when it cannot bound the group, and otherwise
  `max(minimum, sum of the per-job bounds)`.
- **Per-job bound, in seconds.** Each sandbox run stops at its own wall
  limit, `wall(x) = 2x + 1`. The bound is therefore the sum of the walls of
  the sandboxes a job can run, plus `JOB_OVERHEAD_S = 10` for files, sandbox
  setup and cleanup.
  - **A compilation job:** `wall(compilation_time_limit_s) + 10`.
  - **An evaluation job with time limit TL:**
    `2 × wall(TL) + wall(trusted_time_limit_s) + 10`.
    - The two user walls cover TwoSteps, which has two user stages, and
      Communication with up to two processes.
    - The trusted wall covers the checker, or the manager when its limit is
      the trusted one.
  - **An evaluation job without a time limit** has no wall limit, so the
    group cannot be bounded and gets `minimum`, which is today's behaviour.
- **`minimum` is `WORKER_TIMEOUT` (600 s),** so no group gets less time than
  today. *(autonomous)*
- **Examples:**

  | Batch of 25 | Timeout |
  |---|---|
  | TL = 1 s | 25 × 37 = 925 s |
  | TL = 10 s | 25 × 73 = 1825 s |
  | Compilations | 25 × 51 = 1275 s |
  | A single evaluation | 600 s |

  A hung worker behind a full batch with a large TL is caught later than
  today: up to about 30 min plus the 300 s check interval. That is the price
  of never disabling a correct one. A worker that disconnects is still
  caught within `WORKER_CONNECTION_CHECK_TIME` (10 s).
- **Where it is set.** `acquire_worker` sets `_timeout[shard] =
  WORKER_TIMEOUT`, because the job group is not built yet. `_build_and_dispatch`
  then builds the dict, computes the timeout with the values from
  `config.sandbox`, and stores it under `_operation_lock` (only if the
  dispatch id still matches) before it sends the RPC. `check_timeouts`
  compares against `_timeout[shard]`, and its log line names the timeout it
  used.
- **Assumption, documented in a comment:** ES and the Workers read the same
  `[sandbox]` limits. That holds in the Docker deployment, which generates
  one `cms.toml`.

### 4. Documentation

- **`docs/contest-day.md`,** paragraph "A worker was disabled":
  - the timeout grows with the job group's time limits (at least 10
    minutes);
  - a worker the timeout disabled comes back by itself when it reconnects
    after its restart (`./logs.sh cms` shows the WARNING line);
  - only a worker disabled with the **Disable** button, or one that never
    restarts, needs **Enable**.
- **The matching `.po` entries** in `docs/locale/es/LC_MESSAGES`.

## Testing

- **`job_group_timeout`:**
  - compilation-only groups;
  - evaluation groups with TL 1 and 10;
  - a mix of compilations and evaluations;
  - a job without a TL gives `minimum`;
  - the result is never below `minimum`.
- **`WorkerPool` (`cmstestsuite/unit_tests/service/workerpool_test.py`,
  existing patterns, mocked service and worker proxies):**
  - an answered worker is not timed out, even past its timeout;
  - an answered worker is timed out after `ANSWER_HANDLING_TIMEOUT`;
  - a late answer from an earlier dispatch does not mark the current
    dispatch as answered;
  - `release_worker` clears the answer;
  - a worker past its derived timeout is disabled and its operations are
    returned; one within it is not;
  - a timeout-disabled worker is enabled on reconnect, with the WARNING;
  - a worker disabled with `disable_worker` stays disabled on reconnect;
  - `enable_worker` clears the flag;
  - the first connection of an idle worker changes nothing.
- **Existing ES tests** (`evaluationservice_*`, `EvaluationService_test`,
  the gevent two-phase files) still pass.
- **No load test.** The change only matters past a timeout, which the
  harness does not reach.

## Out of Scope

- Releasing the worker before `post_finish_lock` (#6): its premise was not
  confirmed by the #7 load tests.
- ES stalls themselves: #39 (database timeouts) and #38 (watchdog).
- A cap on re-enables, or a heartbeat from the Worker.
- Making the timeout configurable.
