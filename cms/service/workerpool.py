#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2014 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2016 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2013-2015 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2013 Bernard Blackham <bernard@largestprime.net>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Manager for the set of workers.

"""

import asyncio
import logging
import random
import threading
from datetime import datetime, timedelta
import typing

from cms import config
from cms.conf import ServiceCoord
from cms.db import SessionGen
from cms.grading.Job import JobGroup
from cms.io.async_rpc import FIRE_AND_FORGET_TIMEOUT
from cms.io.rpc import RPCError
from cmscommon.datetime import make_datetime, make_timestamp
from cms.service.esoperations import ESOperation

if typing.TYPE_CHECKING:
    from cms.service.EvaluationService import EvaluationService


logger = logging.getLogger(__name__)

# Seconds a job needs besides its sandbox runs: fetching its files,
# creating and cleaning up its sandboxes.
JOB_OVERHEAD_S = 10


# The 2x+1 wall limits are set in cms/grading/steps/evaluation.py,
# compilation.py and trusted.py.
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
    takes about the sum of the wall limits of the sandboxes its jobs
    run, plus JOB_OVERHEAD_S per job. An evaluation job is taken to run
    two user stages (TwoSteps; the processes of Communication run side
    by side) and one trusted program (the checker, or the manager).
    That covers the usual task types: Batch, OutputOnly, TwoSteps and
    Communication with up to two processes. It does not cover
    Interactive (its controller_wall_limit is a task parameter),
    Communication with three or more processes (the manager gets
    2 * max(N * (time limit + 1), trusted limit) + 1) and steps with
    several commands. The minimum (600 s for a worker) and the
    overhead per job absorb the excess of small groups only.

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


class WorkerPool:
    """This class keeps the state of the workers attached to ES, and
    allow the ES to get a usable worker when it needs it.

    """

    WORKER_INACTIVE = None
    WORKER_DISABLED = "disabled"

    # The least time a worker may take on a job group (see
    # job_group_timeout).
    WORKER_TIMEOUT = timedelta(seconds=600)

    # How long an answer may wait for ES to handle it before the worker
    # counts as lost anyway (see check_timeouts).
    ANSWER_HANDLING_TIMEOUT = timedelta(minutes=30)

    def __init__(self, service: "EvaluationService"):
        """
        service: the EvaluationService using this WorkerPool.

        """
        self._service = service
        self._worker = {}
        # These dictionary stores data about the workers (identified
        # by their shard number). Schedule disabling to True means
        # that we are going to disable the worker as soon as possible
        # (when it finishes the current operations). The current
        # operations are also discarded because we already re-assigned
        # it. Ignore is true if the next results coming from the
        # worker should be discarded. Operations is the list of
        # operations currently executing. Operations to ignore is the
        # list of operations to ignore in the next batch of results.
        self._operations: dict[int, list[ESOperation]] = {}
        self._operations_to_ignore: dict[int, list[ESOperation]] = {}
        self._start_time: dict[int, datetime | None] = {}
        self._schedule_disabling: dict[int, bool] = {}
        self._ignore: dict[int, bool] = {}
        # The id of the job group each worker is working on, None while
        # it works on none. acquire_worker takes ids from
        # _last_dispatch_id, and every release clears the worker's. An
        # answer that carries another id is stale (see release_worker).
        self._current_dispatch: dict[int, int | None] = {}
        self._last_dispatch_id = 0
        # When each worker answered its current job group (None while it
        # works on it), and how long it may take on it.
        self._answered_at: dict[int, datetime | None] = {}
        self._timeout: dict[int, timedelta] = {}
        # Whether each disabled worker was disabled by check_timeouts
        # (then it is enabled again when it reconnects) rather than by
        # disable_worker.
        self._disabled_by_timeout: dict[int, bool] = {}

        # TODO: given the number of pieces data associated to each
        # worker, this class could be simplified by creating a new
        # WorkerPoolItem class.

        # TODO: at the moment race conditions during the periodic
        # checks cannot be excluded. A refactoring of this class
        # should take that into account.

        # A reverse lookup dictionary mapping operations to shards.
        self._operations_reverse: dict[ESOperation, int] = {}

        # A lock to ensure that the reverse lookup stays in sync with
        # the operations lists.
        self._operation_lock = threading.RLock()

        # Event set when there are workers available to take jobs. It
        # is only guaranteed that if a worker is available, then this
        # event is set. In other words, the fact that this event is
        # set does not mean that there is a worker available.
        self._workers_available_event = asyncio.Event()

    def __len__(self):
        return len(self._worker)

    def __contains__(self, operation):
        return operation in self._operations_reverse

    def _remove_operations(self, shard: int, new_operation: str | None):
        """Safely remove operations from a worker, assigning a new status.

        shard: the worker from which to remove operations.
        new_operations: the new operation, which can be
            WORKER_INACTIVE or WORKER_DISABLED.

        """
        with self._operation_lock:
            operations = self._operations[shard]
            self._operations[shard] = new_operation
            if isinstance(operations, list):
                for operation in operations:
                    # If the same operation was later assigned to
                    # another worker too, the entry is that worker's.
                    if self._operations_reverse.get(operation) == shard:
                        del self._operations_reverse[operation]

    def _add_operations(self, shard: int, operations: list[ESOperation]):
        """Assigns new operations to a currently inactive worker.

        shard: shard of the worker.
        operations: operations to assign to the worker.

        """
        if self._operations[shard] != WorkerPool.WORKER_INACTIVE:
            raise ValueError("Shard %s is already doing an operation.", shard)
        with self._operation_lock:
            self._operations[shard] = operations
            for operation in operations:
                self._operations_reverse[operation] = shard

    def _threadsafe_set_workers_available(self):
        """Set self._workers_available_event, safely from any thread.

        Touching an asyncio.Event directly from a thread other than
        the one running the event loop is not safe (the event loop
        might not wake up promptly, or internal state could race).
        self._service._loop is None only at __init__ time, before the
        loop starts and before anything could be waiting on this event
        -- a direct call is safe then, mirroring the dual-mode dispatch
        already established in ProxyService's _threadsafe_enqueue.

        """
        if self._service._loop is None:
            self._workers_available_event.set()
        else:
            self._service._loop.call_soon_threadsafe(
                self._workers_available_event.set)

    async def wait_for_workers(self):
        """Wait until a worker might be available."""
        await self._workers_available_event.wait()

    def add_worker(self, worker_coord: ServiceCoord):
        """Add a new worker to the worker pool.

        worker_coord: the coordinates of the worker.

        """
        shard = worker_coord.shard
        # Instruct GeventLibrary to connect ES to the Worker.
        self._worker[shard] = self._service.connect_to(
            worker_coord,
            on_connect=self.on_worker_connected)

        # And we fill all data.
        self._operations[shard] = WorkerPool.WORKER_INACTIVE
        self._operations_to_ignore[shard] = []
        self._start_time[shard] = None
        self._current_dispatch[shard] = None
        self._answered_at[shard] = None
        self._timeout[shard] = WorkerPool.WORKER_TIMEOUT
        self._schedule_disabling[shard] = False
        self._ignore[shard] = False
        self._disabled_by_timeout[shard] = False
        self._threadsafe_set_workers_available()
        logger.debug("Worker %s added.", shard)

    def on_worker_connected(self, worker_coord: ServiceCoord):
        """To be called when a worker comes alive after being
        offline. We use this callback to instruct the worker to
        precache all files concerning the contest.

        A worker that check_timeouts disabled is enabled again: it was
        told to quit, and reconnecting means it restarted.

        worker_coord: the coordinates of the worker
                      that came online.

        """
        shard = worker_coord.shard
        logger.info("Worker %s online again.", shard)
        with self._operation_lock:
            if self._operations[shard] == WorkerPool.WORKER_DISABLED and \
                    self._disabled_by_timeout[shard]:
                logger.warning("Worker %s reconnected after being disabled "
                               "for not answering in time; enabling it "
                               "again.", shard)
                self.enable_worker(shard)
        if self._service.contest_id is not None:
            self._service._spawn(
                self._fire_and_forget(
                    self._worker[shard].precache_files(
                        contest_id=self._service.contest_id)))
        # We don't requeue the operation, because a connection lost
        # does not invalidate a potential result given by the worker
        # (as the problem was the connection and not the machine on
        # which the worker is). But the worker could have been idling,
        # so we wake up the consumers.
        self._threadsafe_set_workers_available()

    @staticmethod
    async def _fire_and_forget(coro: typing.Coroutine):
        """Await coro, swallowing RPCError -- mirrors the old RPC proxy's
        silent-drop-on-failure behavior for calls with no callback.

        Nobody needs the answer, so it is not waited for longer than
        FIRE_AND_FORGET_TIMEOUT seconds: a worker that is connected but
        stuck would otherwise leave the call pending forever. The call
        is then dropped, with a warning.

        """
        try:
            await asyncio.wait_for(coro, FIRE_AND_FORGET_TIMEOUT)
        except RPCError:
            pass
        except asyncio.TimeoutError:
            logger.warning("RPC %s got no answer in %s seconds, "
                           "giving up on it.",
                           getattr(coro, "__qualname__", coro),
                           FIRE_AND_FORGET_TIMEOUT)

    def acquire_worker(self, operations: list[ESOperation]) -> int | None:
        """Tries to assign an operation to an available worker. If no workers
        are available then this returns None, otherwise this returns
        the chosen worker.

        Synchronous on purpose: EvaluationExecutor.execute() calls this
        while holding _current_execution_lock (a threading.RLock), so
        nothing here may await. Building the job group (a DB read, via
        run_in_executor) and the RPC to the worker happen afterwards in
        a separately spawned task, see _build_and_dispatch.

        operations: the operations to assign to a worker.

        return: None if no workers are available, the worker
            assigned to the operation otherwise.

        """
        with self._operation_lock:
            # We look for an available worker.
            try:
                shard = self.find_worker(WorkerPool.WORKER_INACTIVE,
                                         require_connection=True,
                                         random_worker=True)
            except LookupError:
                self._workers_available_event.clear()
                return None

            # Then we fill the info for future memory.
            self._add_operations(shard, operations)

            logger.debug("Worker %s acquired.", shard)
            self._start_time[shard] = make_datetime()
            self._answered_at[shard] = None
            self._timeout[shard] = WorkerPool.WORKER_TIMEOUT
            self._last_dispatch_id += 1
            dispatch_id = self._last_dispatch_id
            self._current_dispatch[shard] = dispatch_id

        logger.info("Asking worker %s to %s.", shard,
                    ", ".join("`%s'" % operation for operation in operations))

        self._service._spawn(
            self._build_and_dispatch(shard, operations, dispatch_id))
        return shard

    async def _build_and_dispatch(
        self, shard: int, operations: list[ESOperation],
        dispatch_id: int | None = None
    ):
        """Build the job group, dispatch it to the worker, report back.

        Runs as a spawned background task on the event loop: nobody
        (in particular not execute(), holding _current_execution_lock)
        awaits it, so awaiting run_in_executor here cannot deadlock on
        a lock held across the await.

        If building the job group fails, release the worker via
        action_finished(None, shard, error) instead of leaving it
        stuck until WORKER_TIMEOUT -- the same outcome a worker-side
        error would produce.

        shard: the worker the operations were assigned to.
        operations: the operations to send to the worker.
        dispatch_id: the id acquire_worker gave the job group.

        """
        loop = asyncio.get_running_loop()
        try:
            job_group_dict = await loop.run_in_executor(
                None, self._build_job_group_dict, operations)
        except Exception as build_error:
            logger.error("Failed to build job group for worker %s.", shard,
                         exc_info=True)
            await self._report_action_finished(
                None, shard, str(build_error), dispatch_id)
            return
        self._set_timeout(shard, dispatch_id, job_group_dict)
        await self._dispatch_to_worker(shard, job_group_dict, dispatch_id)

    def _build_job_group_dict(self, operations: list[ESOperation]) -> dict:
        """Build the JobGroup dict to send to a worker, synchronously.

        Runs inside loop.run_in_executor.

        """
        with SessionGen() as session:
            return JobGroup.from_operations(operations, session).export_to_dict()

    async def _dispatch_to_worker(
        self, shard: int, job_group_dict: dict,
        dispatch_id: int | None = None
    ):
        """Send a job group to a worker and forward its result to ES.

        Fire-and-forget from acquire_worker's perspective (mirrors the
        old callback-based execute_job_group(..., callback=..., plus=...)
        call, which also never blocked the caller) -- awaited by
        _build_and_dispatch, itself spawned as a background task via
        self._service._spawn.

        shard: the worker to send the job group to.
        job_group_dict: the job group, exported to dict.
        dispatch_id: the id acquire_worker gave the job group.

        """
        try:
            data = await self._worker[shard].execute_job_group(
                job_group_dict=job_group_dict)
            error = None
        except RPCError as rpc_error:
            data = None
            error = str(rpc_error)
        self._mark_answered(shard, dispatch_id)
        await self._report_action_finished(data, shard, error, dispatch_id)

    async def _report_action_finished(
        self, data: dict | None, shard: int, error: str | None,
        dispatch_id: int | None = None
    ):
        """Forward a worker's outcome to ES, logging any failure.

        This runs in a spawned task, so an exception escaping from
        action_finished (e.g. an error while ES handles the result)
        would otherwise be lost without a trace.

        data: the JobGroup exported to dict, or None on error.
        shard: the worker that finished.
        error: the error message, or None on success.
        dispatch_id: the id acquire_worker gave the job group.

        """
        try:
            await self._service.action_finished(
                data, shard, error, dispatch_id=dispatch_id)
        except Exception:
            logger.error("Unexpected error in action_finished for worker %s.",
                         shard, exc_info=True)

    def _mark_answered(self, shard: int, dispatch_id: int | None) -> None:
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
                     job_group_dict: dict) -> None:
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

    def release_worker(
        self, shard: int, dispatch_id: int | None = None
    ) -> bool | list[ESOperation]:
        """To be called by ES when it receives a notification that an
        operation finished.

        Note: if the worker is scheduled to be disabled, then we
        disable it, and notify the ES to discard the outcome obtained
        by the worker.

        shard: the worker to release.
        dispatch_id: the job group the answer belongs to, or None if
            unknown. An answer for another job group than the worker's
            current one is stale: it is ignored, and the worker is left
            as it is.

        return: if boolean, whether the result is to be ignored; if a list,
            the list of operation for which the results should be ignored.

        """
        with self._operation_lock:
            if dispatch_id is not None and \
                    dispatch_id != self._current_dispatch.get(shard):
                logger.info("Ignoring a stale answer from worker %s "
                            "(job group %s; current: %s).", shard,
                            dispatch_id, self._current_dispatch.get(shard))
                return True

            if self._operations[shard] == WorkerPool.WORKER_INACTIVE:
                err_msg = "Trying to release worker while it's inactive."
                logger.error(err_msg)
                raise ValueError(err_msg)

            # If the worker has already been disabled, ignore the result
            # and keep the worker disabled.
            if self._operations[shard] == WorkerPool.WORKER_DISABLED:
                return True

            ret = self._ignore[shard]
            to_ignore = self._operations_to_ignore[shard]
            self._operations_to_ignore[shard] = []
            self._start_time[shard] = None
            self._current_dispatch[shard] = None
            self._answered_at[shard] = None
            self._ignore[shard] = False
            if self._schedule_disabling[shard]:
                self._remove_operations(shard, WorkerPool.WORKER_DISABLED)
                self._schedule_disabling[shard] = False
                logger.info("Worker %s released and disabled.", shard)
            else:
                self._remove_operations(shard, WorkerPool.WORKER_INACTIVE)
                self._threadsafe_set_workers_available()
                logger.debug("Worker %s released.", shard)
            if ret is False and to_ignore != []:
                return to_ignore
            else:
                return ret

    def find_worker(
        self,
        operation: ESOperation | str | None,
        require_connection: bool = False,
        random_worker: bool = False,
    ) -> int:
        """Return a worker whose assigned operation is operation.

        Remember that there is a placeholder operation to signal that the
        worker is not doing anything (or disabled).

        operation: the operation we are
            looking for, or WorkerPool.WORKER_*.
        require_connection: True if we want to find a worker
            doing the operation and that is actually connected to us
            (i.e., did not die).
        random_worker: if True, choose uniformly amongst all
            workers doing the operation.

        returns: the shard of a worker working on operation.

        raise (LookupError): if nothing has been found.

        """
        pool = []
        for shard, worker_operation in self._operations.items():
            if worker_operation == operation:
                if not require_connection or self._worker[shard].connected:
                    pool.append(shard)
                    if not random_worker:
                        return shard
        if pool == []:
            raise LookupError("No such operation.")
        else:
            return random.choice(pool)

    def ignore_operation(self, operation: ESOperation):
        """Mark the operation to be ignored.

        operation: the operation to ignore.

        raise (LookupError): if operation is not found.

        """
        try:
            with self._operation_lock:
                shard = self._operations_reverse[operation]
                self._operations_to_ignore[shard].append(operation)
        except LookupError:
            logger.debug("Asked to ignore operation `%s' "
                         "that cannot be found.", operation)
            raise

    def get_status(self) -> dict:
        """Returns a dict with info about the current status of all
        workers.

        return: dict of info: current operation, starting time,
            number of errors, and additional data specified in the
            operation.

        """
        result = dict()
        for shard in self._worker.keys():
            s_time = self._start_time[shard]
            s_time = make_timestamp(s_time) if s_time is not None else None

            result["%d" % shard] = {
                'connected': self._worker[shard].connected,
                'operations': [operation.to_dict()
                               for operation in self._operations[shard]]
                if isinstance(self._operations[shard], list)
                else self._operations[shard],
                'start_time': s_time}
        return result

    def check_timeouts(self) -> list[ESOperation]:
        """Check if some worker is not responding in too much time. If
        this is the case, the worker is scheduled for disabling, and
        we send it a message trying to shut it down.

        The time a worker may take is the one of its job group (see
        _set_timeout). A worker that answered gets
        ANSWER_HANDLING_TIMEOUT instead: its answer only waits for ES,
        and the worker is not to blame for that.

        return: list of operations assigned to the worker
            that timed out.

        """
        with self._operation_lock:
            now = make_datetime()
            lost_operations = []
            for shard in self._worker:
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
                is_busy = (self._operations[shard] !=
                           WorkerPool.WORKER_INACTIVE and
                           self._operations[shard] !=
                           WorkerPool.WORKER_DISABLED)
                assert is_busy

                # We return the operation so ES can do what it needs.
                if not self._ignore[shard] and \
                        isinstance(self._operations[shard], list):
                    for operation in self._operations[shard]:
                        if operation not in \
                                self._operations_to_ignore[shard]:
                            lost_operations.append(operation)

                # Also, we are not trusting it, so we are not
                # assigning it new operations even if it comes
                # back to life.
                self._schedule_disabling[shard] = True
                self._ignore[shard] = True
                self._disabled_by_timeout[shard] = True
                self.release_worker(shard)
                self._service._spawn(
                    self._fire_and_forget(
                        self._worker[shard].quit(reason=reason)))

            return lost_operations

    def disable_worker(self, shard: int) -> list[ESOperation]:
        """Disable a worker.

        shard: which worker to disable.

        return: list of non-ignored operations
            assigned to the worker.

        raise (ValueError): if worker is already disabled.

        """
        with self._operation_lock:
            if self._operations[shard] == WorkerPool.WORKER_DISABLED:
                err_msg = \
                    "Trying to disable already disabled worker %s." % shard
                logger.warning(err_msg)
                raise ValueError(err_msg)

            self._disabled_by_timeout[shard] = False
            lost_operations = []
            if self._operations[shard] == WorkerPool.WORKER_INACTIVE:
                self._operations[shard] = WorkerPool.WORKER_DISABLED

            else:
                # We return all non-ignored operations so ES can do what
                # it needs.
                if not self._ignore[shard]:
                    to_ignore = self._operations_to_ignore[shard]
                    if isinstance(self._operations[shard], list):
                        for operation in self._operations[shard]:
                            if operation not in to_ignore:
                                lost_operations.append(operation)

                # And we mark the worker as disabled (until another action
                # is taken).
                self._schedule_disabling[shard] = True
                self._operations_to_ignore[shard] = []
                self._ignore[shard] = True
                self.release_worker(shard)

            logger.info("Worker %s disabled.", shard)
            return lost_operations

    def enable_worker(self, shard: int):
        """Enable a worker that previously was disabled.

        shard: which worker to enable.

        raise (ValueError): if worker is not disabled.

        """
        with self._operation_lock:
            if self._operations[shard] != WorkerPool.WORKER_DISABLED:
                err_msg = \
                    "Trying to enable worker %s which is not disabled." % shard
                logger.error(err_msg)
                raise ValueError(err_msg)

            self._operations[shard] = WorkerPool.WORKER_INACTIVE
            self._operations_to_ignore[shard] = []
            self._disabled_by_timeout[shard] = False
            self._threadsafe_set_workers_available()
            logger.info("Worker %s enabled.", shard)

    def check_connections(self) -> list[ESOperation]:
        """Check if a worker we assigned an operation to disconnects. In this
        case, requeue the operation.

        return: list of operations assigned to worker
            that disconnected.

        """
        with self._operation_lock:
            lost_operations = []
            for shard in self._worker:
                if not self._worker[shard].connected and \
                        self._operations[shard] not in [
                            WorkerPool.WORKER_DISABLED,
                            WorkerPool.WORKER_INACTIVE]:
                    if not self._ignore[shard]:
                        lost_operations += self._operations[shard]
                    self.release_worker(shard)

            return lost_operations
