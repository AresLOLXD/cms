"""Tests for ProxyExecutor's per-group batching, resets and failures."""

import asyncio
import json
import threading
import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from urllib.parse import urljoin

import requests.exceptions

from cms.io.priorityqueue import QueueEntry
from cms.service.ProxyService import ProxyExecutor, ProxyOperation, \
    RefusedEntity


RANKING = "http://rws:secret@localhost:8890/"


def entries(*operations):
    return [QueueEntry(op, 0, datetime.now(), i)
            for i, op in enumerate(operations)]


class TestProxyExecutorGroups(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        put_patcher = patch("cms.service.ProxyService.requests.put")
        self.requests_put = put_patcher.start()
        self.addCleanup(put_patcher.stop)
        self.requests_put.return_value.status_code = 200

        delete_patcher = patch("cms.service.ProxyService.requests.delete")
        self.requests_delete = delete_patcher.start()
        self.addCleanup(delete_patcher.stop)
        self.requests_delete.return_value.status_code = 204

        self.executor = ProxyExecutor(RANKING)

    def put_calls(self):
        bodies = []
        for c in self.requests_put.call_args_list:
            bodies.append((c.args[0], json.loads(c.args[1])))
        return bodies

    def delete_urls(self):
        return [c.args[0] for c in self.requests_delete.call_args_list]

    async def test_root_operations_use_plain_paths(self):
        await self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}})))
        self.assertEqual(self.put_calls(), [(urljoin(RANKING, "contests/"),
                                             {"c": {}})])

    async def test_group_operations_use_prefixed_paths(self):
        await self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim")))
        self.assertEqual(
            self.put_calls(),
            [(urljoin(RANKING, "olim/contests/"), {"c": {}})])

    async def test_batch_is_split_by_group(self):
        await self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u1": {}}, "olim"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u2": {}}, "omips"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u3": {}}, "olim")))
        self.assertCountEqual(self.put_calls(), [
            (urljoin(RANKING, "olim/users/"), {"u1": {}, "u3": {}}),
            (urljoin(RANKING, "omips/users/"), {"u2": {}})])

    async def test_reset_discards_earlier_data_and_runs_before_puts(self):
        await self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.USER_TYPE, {"stale": {}}, "olim"),
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim"),
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim")))
        self.assertEqual(
            self.delete_urls(),
            [urljoin(RANKING, "olim/contests/"),
             urljoin(RANKING, "olim/users/")])
        self.assertEqual(
            self.put_calls(),
            [(urljoin(RANKING, "olim/contests/"), {"c": {}})])

    async def test_reset_of_root_namespace(self):
        await self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, None)))
        self.assertEqual(
            self.delete_urls(),
            [urljoin(RANKING, "contests/"), urljoin(RANKING, "users/")])

    async def test_put_gives_up_on_a_ranking_that_stops_answering(self):
        # Without a timeout, a hung ranking would block the thread
        # sending to it forever: connect and read timeouts, in seconds.
        await self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}})))
        self.assertEqual(self.requests_put.call_count, 1)
        self.assertEqual(
            self.requests_put.call_args.kwargs.get("timeout"), (5, 120))

    async def test_delete_gives_up_on_a_ranking_that_stops_answering(self):
        await self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim")))
        self.assertEqual(self.requests_delete.call_count, 2)
        for call in self.requests_delete.call_args_list:
            self.assertEqual(call.kwargs.get("timeout"), (5, 120))


def url(resource: str) -> str:
    return urljoin(RANKING, resource)


def stamped_entries(*operations):
    """Wrap operations like the queue hands them out, in sorted order.

    Unlike entries(), each one has its own priority and timestamp, to
    check that they survive being put back in the queue.

    """
    return [QueueEntry(op, 1 + i // 2, datetime(2020, 1, 1, 9, 0, i),
                       40 + i)
            for i, op in enumerate(operations)]


def stamps(queue_entries):
    """Return what decides the place of each entry in the queue."""
    return [(e.item, e.priority, e.timestamp) for e in queue_entries]


class FakeRetryWaits:
    """Stand in for asyncio inside ProxyService, to skip the retry waits.

    After a failure an executor waits, with asyncio.sleep, before
    trying again. Swapping the asyncio module that ProxyService sees
    (and only that one, so the sleeps of the tests themselves are left
    alone) records those waits and returns at once.

    The executor also reads the clock, with monotonic, to know which
    groups are due. Patch it with this object's monotonic too: that
    clock moves forward only by the waits, as if they took place.

    """

    def __init__(self):
        self.waits: list[float] = []
        # Once this many waits happened, the next ones never end.
        self.block_after: int | None = None
        # The time on the fake clock, in seconds.
        self.now = 0.0

    async def sleep(self, delay: float) -> None:
        self.waits.append(delay)
        if self.block_after is not None \
                and len(self.waits) >= self.block_after:
            await asyncio.Event().wait()
        self.now += delay
        await asyncio.sleep(0)

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, name):
        return getattr(asyncio, name)


class TestProxyExecutorFailures(unittest.IsolatedAsyncioTestCase):
    """What an executor does when a ranking cannot take some data."""

    async def asyncSetUp(self):
        # The requests made, in order, as (method, URL, JSON payload).
        self.calls: list[tuple[str, str, dict | None]] = []
        # How the ranking answers, by (method, URL): an exception to
        # raise, or the HTTP status to answer with.
        self.outcomes: dict[tuple[str, str], Exception | int] = {}
        # When each URL was requested, on the fake clock.
        self.request_times: dict[str, list[float]] = {}

        def fake_request(method, default_status):
            def send(target, body=None, **kwargs):
                payload = None if body is None else json.loads(body)
                self.calls.append((method, target, payload))
                self.request_times.setdefault(target, []).append(
                    self.retry_waits.now)
                outcome = self.outcomes.get((method, target), default_status)
                if isinstance(outcome, Exception):
                    raise outcome
                response = MagicMock()
                response.status_code = outcome
                return response
            return send

        for method, default_status in (("put", 200), ("delete", 204)):
            patcher = patch("cms.service.ProxyService.requests." + method,
                            fake_request(method, default_status))
            patcher.start()
            self.addCleanup(patcher.stop)

        self.retry_waits = FakeRetryWaits()
        for name, fake in (("asyncio", self.retry_waits),
                           ("monotonic", self.retry_waits.monotonic)):
            patcher = patch("cms.service.ProxyService." + name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.executor = ProxyExecutor(RANKING)

    def urls(self) -> list[str]:
        return [target for method, target, payload in self.calls]

    async def drain(self, executor=None) -> list[QueueEntry]:
        """Take out everything waiting in the queue, like run() does."""
        queue = (executor or self.executor)._operation_queue
        drained = []
        while not queue.empty():
            drained.append(await queue.pop())
        return drained

    async def next_round(self):
        """Do what run() does for one round."""
        await self.executor.execute(await self.drain())

    async def rounds_until(self, until: float, before_each=None):
        """Do what run() does, until the fake clock gets to until.

        until: the time (on the fake clock) to stop at.
        before_each: a function to call before each round, if any.

        """
        # An executor that stops waiting would never get there.
        for _ in range(1000):
            if self.retry_waits.now >= until:
                return
            if before_each is not None:
                before_each()
            await self.next_round()
        self.fail("The fake clock is still at %s after 1000 rounds."
                  % self.retry_waits.now)

    def start_running(self, executor: ProxyExecutor) -> asyncio.Task:
        task = asyncio.create_task(executor.run())

        async def stop():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(stop)
        return task

    async def wait_until(self, condition, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail("Gave up waiting after %s seconds." % timeout)
            await asyncio.sleep(0.005)

    async def test_communication_errors_are_retried_without_losing_data(
        self,
    ):
        for error in (requests.exceptions.ConnectionError("refused"),
                      requests.exceptions.ReadTimeout("timed out"),
                      requests.exceptions.ConnectTimeout("no route")):
            with self.subTest(error=type(error).__name__):
                self.executor = ProxyExecutor(RANKING)
                self.calls.clear()
                self.outcomes = {("put", url("olim/contests/")): error}
                batch = stamped_entries(
                    ProxyOperation(ProxyExecutor.CONTEST_TYPE,
                                   {"c": {}}, "olim"),
                    ProxyOperation(ProxyExecutor.USER_TYPE,
                                   {"u": {}}, "olim"),
                    ProxyOperation(ProxyExecutor.TASK_TYPE,
                                   {"t": {}}, "olim"))

                await self.executor.execute(batch)

                # A task refers to its contest: nothing after the
                # failed request is sent, or the ranking would reject it.
                self.assertEqual(self.urls(), [url("olim/contests/")])
                # Everything is put back as it was, in the same place.
                requeued = await self.drain()
                self.assertEqual(stamps(requeued), stamps(batch))

                # The ranking is back: the next round sends it all.
                self.outcomes.clear()
                self.calls.clear()
                await self.executor.execute(requeued)
                self.assertEqual(self.calls, [
                    ("put", url("olim/contests/"), {"c": {}}),
                    ("put", url("olim/tasks/"), {"t": {}}),
                    ("put", url("olim/users/"), {"u": {}})])
                self.assertEqual(await self.drain(), [])

    async def test_same_stamp_entries_keep_their_order_when_put_back(self):
        # The order of entries decides which data wins when they carry
        # the same entity, and the stamp does not tell them apart.
        self.outcomes = {("put", url("contests/")):
                         requests.exceptions.ConnectionError("refused")}
        stamp = datetime(2020, 1, 1, 9, 0, 0)
        first = ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {"v": 1}})
        second = ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {"v": 2}})
        batch = [QueueEntry(first, 2, stamp, 7),
                 QueueEntry(second, 2, stamp, 8)]

        await self.executor.execute(batch)

        self.assertEqual(
            [entry.item for entry in await self.drain()], [first, second])

    async def test_server_errors_are_retried_without_losing_data(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                self.executor = ProxyExecutor(RANKING)
                self.calls.clear()
                self.outcomes = {("put", url("olim/contests/")): status}
                batch = stamped_entries(
                    ProxyOperation(ProxyExecutor.CONTEST_TYPE,
                                   {"c": {}}, "olim"),
                    ProxyOperation(ProxyExecutor.USER_TYPE,
                                   {"u": {}}, "olim"))

                await self.executor.execute(batch)

                self.assertEqual(stamps(await self.drain()), stamps(batch))
                self.assertEqual(self.retry_waits.waits[-1:], [1])

    async def test_rejected_data_is_dropped_and_the_rest_is_sent(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                self.executor = ProxyExecutor(RANKING)
                self.calls.clear()
                self.retry_waits.waits.clear()
                # RWS refuses the submissions of olim, e.g. because one
                # of them names an unknown user: it takes all or nothing.
                self.outcomes = {
                    ("put", url("olim/submissions/")): status}
                batch = entries(
                    ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                                   {"1": {}}, "olim"),
                    ProxyOperation(ProxyExecutor.SUBCHANGE_TYPE,
                                   {"1s": {}}, "olim"),
                    ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                                   {"2": {}}, "omips"),
                    ProxyOperation(ProxyExecutor.SUBCHANGE_TYPE,
                                   {"2s": {}}, "omips"))

                with self.assertLogs(
                        "cms.service.ProxyService", "WARNING") as logs:
                    await self.executor.execute(batch)

                # Only the refused (group, type) pair is lost.
                self.assertEqual(self.urls(), [
                    url("olim/submissions/"), url("olim/subchanges/"),
                    url("omips/submissions/"), url("omips/subchanges/")])
                # Sending it again would fail again: it is not retried.
                self.assertEqual(await self.drain(), [])
                self.assertEqual(self.retry_waits.waits, [])
                # The operator is told what became of it in that group,
                # and only that one. Only data refused as invalid (400)
                # is sent again, once ProxyService fixed what it names.
                hints = [line for line in logs.output
                         if " refused " in line or "Regenerate" in line]
                self.assertEqual(len(hints), 1)
                self.assertIn("submissions of group olim", hints[0])
                self.assertIn(
                    "sent again after the contest data" if status == 400
                    else "It will not be sent again: use Regenerate",
                    hints[0])
                self.assertNotIn("omips", hints[0])

    async def test_failed_group_waits_while_the_others_are_sent(self):
        self.outcomes = {("put", url("olim/submissions/")):
                         requests.exceptions.ConnectionError("reset")}
        batch = stamped_entries(
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u": {}}, "olim"),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE, {"1": {}}, "olim"),
            ProxyOperation(ProxyExecutor.SUBCHANGE_TYPE, {"1s": {}}, "olim"),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE, {"2": {}}, "omips"),
            ProxyOperation(ProxyExecutor.SUBCHANGE_TYPE, {"2s": {}}, "omips"))

        await self.executor.execute(batch)

        # The subchange of olim is not tried: it needs its submission.
        self.assertEqual(self.urls(), [
            url("olim/users/"), url("olim/submissions/"),
            url("omips/submissions/"), url("omips/subchanges/")])
        # What olim did not get is put back, what it got is not.
        self.assertEqual(stamps(await self.drain()), stamps(batch[1:3]))

    async def test_failed_reset_is_repeated_before_the_data(self):
        self.outcomes = {("delete", url("olim/contests/")):
                         requests.exceptions.ConnectionError("reset")}
        batch = stamped_entries(
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim"),
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"),
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "omips"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u": {}}, "omips"))

        await self.executor.execute(batch)

        # omips is emptied and filled, olim waits for its reset.
        self.assertEqual(self.calls, [
            ("delete", url("olim/contests/"), None),
            ("delete", url("omips/contests/"), None),
            ("delete", url("omips/users/"), None),
            ("put", url("omips/users/"), {"u": {}})])
        requeued = await self.drain()
        self.assertEqual(stamps(requeued), stamps(batch[:2]))

        self.outcomes.clear()
        self.calls.clear()
        await self.executor.execute(requeued)
        self.assertEqual(self.calls, [
            ("delete", url("olim/contests/"), None),
            ("delete", url("olim/users/"), None),
            ("put", url("olim/contests/"), {"c": {}})])

    async def test_completed_reset_is_not_repeated_with_the_data(self):
        self.outcomes = {("put", url("olim/users/")):
                         requests.exceptions.ConnectionError("reset")}
        batch = stamped_entries(
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim"),
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u": {}}, "olim"))

        await self.executor.execute(batch)

        # olim was emptied and got its contest: only what failed is put
        # back, or emptying it again would lose the contest.
        requeued = await self.drain()
        self.assertEqual(stamps(requeued), stamps(batch[2:]))

        self.outcomes.clear()
        self.calls.clear()
        await self.executor.execute(requeued)
        self.assertEqual(
            self.calls, [("put", url("olim/users/"), {"u": {}})])

    async def test_rejected_reset_is_dropped_and_the_data_is_sent(self):
        self.outcomes = {("delete", url("olim/contests/")): 404}
        batch = entries(
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim"),
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"))

        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            await self.executor.execute(batch)

        self.assertEqual(self.urls(), [
            url("olim/contests/"), url("olim/contests/")])
        self.assertEqual(self.calls[-1][0], "put")
        self.assertEqual(await self.drain(), [])
        self.assertTrue(any("Regenerate" in line and "olim" in line
                            for line in logs.output))

    async def test_unexpected_error_keeps_the_data_for_a_retry(self):
        # E.g. a certificate file that requests cannot open (an OSError
        # rather than a RequestException).
        self.outcomes = {("put", url("olim/contests/")):
                         OSError("Could not find a CA bundle")}
        batch = stamped_entries(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"),
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"d": {}}, "omips"))

        with self.assertLogs("cms.service.ProxyService", "ERROR") as logs:
            await self.executor.execute(batch)

        self.assertIsNotNone(logs.records[0].exc_info)
        # Nothing is lost, and omips is not held back by olim.
        self.assertEqual(self.urls(), [
            url("olim/contests/"), url("omips/contests/")])
        self.assertEqual(stamps(await self.drain()), stamps(batch[:1]))
        self.assertEqual(self.retry_waits.waits, [1])

    async def test_data_that_cannot_be_encoded_is_dropped(self):
        # Sending it again would fail the same way: it must not keep
        # the group waiting forever.
        circular: dict = {}
        circular["itself"] = circular
        for error, user in (("TypeError", {"f_name": object()}),
                            ("ValueError", circular)):
            with self.subTest(error=error):
                self.executor = ProxyExecutor(RANKING)
                self.calls.clear()
                batch = entries(
                    ProxyOperation(ProxyExecutor.CONTEST_TYPE,
                                   {"c": {}}, "olim"),
                    ProxyOperation(ProxyExecutor.USER_TYPE,
                                   {"u": user}, "olim"),
                    ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                                   {"1": {}}, "olim"))

                with self.assertLogs(
                        "cms.service.ProxyService", "WARNING") as logs:
                    await self.executor.execute(batch)

                # Only the users are dropped: what comes after them in
                # the group is sent, and nothing is tried again.
                self.assertEqual(self.urls(), [
                    url("olim/contests/"), url("olim/submissions/")])
                self.assertEqual(await self.drain(), [])
                self.assertEqual(self.retry_waits.waits, [])
                hints = [line for line in logs.output
                         if "Regenerate" in line]
                self.assertEqual(len(hints), 1)
                self.assertIn("users of group olim cannot be encoded",
                              hints[0])
                # Regenerate alone would build the same data again.
                self.assertIn("Fix the data, then use Regenerate", hints[0])
                # Not an unexpected error: no traceback.
                self.assertFalse(any(r.exc_info for r in logs.records))

    async def test_wait_doubles_up_to_a_cap_and_restarts_after_success(self):
        contests = ("put", url("contests/"))
        self.outcomes = {
            contests: requests.exceptions.ConnectionError("down")}
        self.executor.enqueue(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}))

        for _ in range(9):
            await self.next_round()
        self.assertEqual(self.retry_waits.waits,
                         [1, 2, 4, 8, 16, 32, 60, 60, 60])

        # The ranking is back: sent, and no wait.
        del self.outcomes[contests]
        await self.next_round()
        self.assertEqual(len(self.retry_waits.waits), 9)
        self.assertEqual(await self.drain(), [])

        # The next failure starts again from the shortest wait.
        self.outcomes[contests] = requests.exceptions.ConnectionError("down")
        self.executor.enqueue(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c2": {}}))
        await self.next_round()
        self.assertEqual(self.retry_waits.waits[9:], [1])

    async def test_running_executor_waits_between_attempts(self):
        self.outcomes = {("put", url("contests/")):
                         requests.exceptions.ConnectionError("down")}
        # Let the executor try five times, then stop it from going on.
        self.retry_waits.block_after = 5
        self.start_running(self.executor)
        self.executor.enqueue(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}))

        await self.wait_until(lambda: len(self.retry_waits.waits) >= 5)

        # It waited, for longer each time, before each new attempt.
        self.assertEqual(self.retry_waits.waits, [1, 2, 4, 8, 16])
        self.assertEqual(len(self.calls), 5)

    async def test_backing_off_does_not_hold_back_another_ranking(self):
        other_ranking = "http://rws:secret@otherhost:8890/"
        self.outcomes = {("put", url("contests/")):
                         requests.exceptions.ConnectionError("down")}
        # The first wait, the one of the failing executor, never ends.
        self.retry_waits.block_after = 1
        operation = ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}})
        failing = ProxyExecutor(RANKING)
        working = ProxyExecutor(other_ranking)
        for executor in (failing, working):
            self.start_running(executor)
            executor.enqueue(operation)

        sent_to_other = ("put", urljoin(other_ranking, "contests/"),
                         {"c": {}})
        await self.wait_until(lambda: sent_to_other in self.calls)
        await self.wait_until(lambda: self.retry_waits.waits)

        self.assertEqual(await self.drain(working), [])
        self.assertEqual(
            [e.item for e in await self.drain(failing)], [operation])

    async def test_visibility_goes_after_the_reset_and_before_the_data(self):
        settings = {"hide_at": None, "show_at": None, "freeze_at": None,
                    "unfreeze_at": None, "staff_password": "bcrypt:hash"}
        batch = entries(
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim"),
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"),
            ProxyOperation(ProxyExecutor.VISIBILITY_TYPE, settings, "olim"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u": {}}, "olim"))

        await self.executor.execute(batch)

        # The ranking learns whether to hide the group before it gets
        # any of its data, even the data queued before the settings.
        self.assertEqual(self.calls, [
            ("delete", url("olim/contests/"), None),
            ("delete", url("olim/users/"), None),
            ("put", url("olim/visibility"), settings),
            ("put", url("olim/contests/"), {"c": {}}),
            ("put", url("olim/users/"), {"u": {}})])
        self.assertEqual(await self.drain(), [])

    async def test_failed_visibility_holds_back_the_data_of_its_group(self):
        hidden = {"hide_at": 1791658800, "show_at": None, "freeze_at": None,
                  "unfreeze_at": None, "staff_password": None}
        visible = {"hide_at": None, "show_at": None, "freeze_at": None,
                   "unfreeze_at": None, "staff_password": None}
        for failure in (requests.exceptions.ConnectionError("refused"), 503):
            with self.subTest(failure=failure):
                self.executor = ProxyExecutor(RANKING)
                self.calls.clear()
                self.outcomes = {("put", url("olim/visibility")): failure}
                batch = stamped_entries(
                    ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim"),
                    ProxyOperation(ProxyExecutor.VISIBILITY_TYPE,
                                   hidden, "olim"),
                    ProxyOperation(ProxyExecutor.CONTEST_TYPE,
                                   {"c": {}}, "olim"),
                    ProxyOperation(ProxyExecutor.VISIBILITY_TYPE,
                                   visible, "omips"),
                    ProxyOperation(ProxyExecutor.CONTEST_TYPE,
                                   {"d": {}}, "omips"))

                await self.executor.execute(batch)

                # The data of olim waits for its settings, omips goes on.
                self.assertEqual(self.calls, [
                    ("delete", url("olim/contests/"), None),
                    ("delete", url("olim/users/"), None),
                    ("put", url("olim/visibility"), hidden),
                    ("put", url("omips/visibility"), visible),
                    ("put", url("omips/contests/"), {"d": {}})])
                # The reset got there: only the settings and the data of
                # olim are put back, as they were.
                requeued = await self.drain()
                self.assertEqual(stamps(requeued), stamps(batch[1:3]))

                # The ranking is back: the settings go first.
                self.outcomes.clear()
                self.calls.clear()
                await self.executor.execute(requeued)
                self.assertEqual(self.calls, [
                    ("put", url("olim/visibility"), hidden),
                    ("put", url("olim/contests/"), {"c": {}})])
                self.assertEqual(await self.drain(), [])

    async def test_rejected_visibility_drops_the_data_of_its_group(self):
        hidden = {"hide_at": 1791658800, "show_at": None, "freeze_at": None,
                  "unfreeze_at": None, "staff_password": None}
        visible = {"hide_at": None, "show_at": None, "freeze_at": None,
                   "unfreeze_at": None, "staff_password": None}
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                self.executor = ProxyExecutor(RANKING)
                self.calls.clear()
                self.retry_waits.waits.clear()
                self.outcomes = {("put", url("olim/visibility")): status}
                batch = entries(
                    ProxyOperation(ProxyExecutor.VISIBILITY_TYPE,
                                   hidden, "olim"),
                    ProxyOperation(ProxyExecutor.CONTEST_TYPE,
                                   {"c": {}}, "olim"),
                    ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                                   {"1": {}}, "olim"),
                    ProxyOperation(ProxyExecutor.VISIBILITY_TYPE,
                                   visible, "omips"),
                    ProxyOperation(ProxyExecutor.CONTEST_TYPE,
                                   {"d": {}}, "omips"))

                with self.assertLogs(
                        "cms.service.ProxyService", "WARNING") as logs:
                    await self.executor.execute(batch)

                # The ranking may show olim while it should be hidden:
                # none of its data is sent. omips is not affected.
                self.assertEqual(self.urls(), [
                    url("olim/visibility"), url("omips/visibility"),
                    url("omips/contests/")])
                # Sending it again would fail again: nothing is retried.
                self.assertEqual(await self.drain(), [])
                self.assertEqual(self.retry_waits.waits, [])
                # The operator is told how to fix olim, and only olim.
                # Not with Regenerate: it would empty the namespace and
                # send nothing back while the settings are refused.
                hints = [line for line in logs.output
                         if "rejected the visibility" in line]
                self.assertEqual(len(hints), 1)
                self.assertIn("of group olim, so its data is held back",
                              hints[0])
                self.assertIn("/olim/visibility", hints[0])
                self.assertIn("save the group again in AWS", hints[0])
                self.assertNotIn("omips", hints[0])
                self.assertFalse(
                    any("Regenerate" in line for line in logs.output))

    async def test_last_visibility_of_a_group_wins(self):
        self.outcomes = {("put", url("olim/visibility")):
                         requests.exceptions.ConnectionError("reset")}
        old = {"hide_at": None, "show_at": None, "freeze_at": None,
               "unfreeze_at": None, "staff_password": "bcrypt:old"}
        new = {"hide_at": None, "show_at": None, "freeze_at": None,
               "unfreeze_at": None, "staff_password": "bcrypt:new"}
        other = {"hide_at": None, "show_at": None, "freeze_at": None,
                 "unfreeze_at": None, "staff_password": None}
        batch = stamped_entries(
            ProxyOperation(ProxyExecutor.VISIBILITY_TYPE, old, "olim"),
            ProxyOperation(ProxyExecutor.VISIBILITY_TYPE, other, "omips"),
            ProxyOperation(ProxyExecutor.VISIBILITY_TYPE, new, "olim"))

        await self.executor.execute(batch)

        # One request for each group, with the settings queued last.
        self.assertEqual(self.calls, [
            ("put", url("olim/visibility"), new),
            ("put", url("omips/visibility"), other)])
        # Only those settings are kept to be sent again.
        self.assertEqual(stamps(await self.drain()), stamps(batch[2:]))

    async def test_failed_reset_holds_back_the_visibility(self):
        self.outcomes = {("delete", url("olim/contests/")):
                         requests.exceptions.ConnectionError("reset")}
        batch = stamped_entries(
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim"),
            ProxyOperation(ProxyExecutor.VISIBILITY_TYPE,
                           {"hide_at": None, "show_at": None,
                            "freeze_at": None, "unfreeze_at": None,
                            "staff_password": None}, "olim"),
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"))

        await self.executor.execute(batch)

        # Nothing of olim goes after a reset that did not get there.
        self.assertEqual(self.urls(), [url("olim/contests/")])
        self.assertEqual(stamps(await self.drain()), stamps(batch))

    async def test_rejected_visibility_keeps_holding_back_the_group_data(
        self,
    ):
        hidden = {"hide_at": None, "show_at": None, "freeze_at": None,
                  "unfreeze_at": None, "staff_password": None}
        self.outcomes = {("put", url("olim/visibility")): 400}

        with self.assertLogs("cms.service.ProxyService", "DEBUG") as logs:
            await self.executor.execute(entries(
                ProxyOperation(ProxyExecutor.VISIBILITY_TYPE,
                               hidden, "olim")))
            # The data of olim comes in later batches, as it does when
            # the service enqueues it from another thread.
            await self.executor.execute(entries(
                ProxyOperation(ProxyExecutor.CONTEST_TYPE,
                               {"c": {}}, "olim"),
                ProxyOperation(ProxyExecutor.USER_TYPE,
                               {"u": {}}, "olim"),
                ProxyOperation(ProxyExecutor.CONTEST_TYPE,
                               {"d": {}}, "omips")))
            # A reset only deletes: it still goes through.
            await self.executor.execute(entries(
                ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim"),
                ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                               {"1": {}}, "olim")))
            # The same settings, refused again.
            await self.executor.execute(entries(
                ProxyOperation(ProxyExecutor.VISIBILITY_TYPE,
                               hidden, "olim"),
                ProxyOperation(ProxyExecutor.TASK_TYPE,
                               {"t": {}}, "olim")))

        self.assertEqual(self.calls, [
            ("put", url("olim/visibility"), hidden),
            ("put", url("omips/contests/"), {"d": {}}),
            ("delete", url("olim/contests/"), None),
            ("delete", url("olim/users/"), None),
            ("put", url("olim/visibility"), hidden)])
        # What is dropped is not kept to be sent again, and nobody waits.
        self.assertEqual(await self.drain(), [])
        self.assertEqual(self.retry_waits.waits, [])
        # One warning when olim got refused, not one for each batch: the
        # data dropped meanwhile only goes to the debug log.
        messages = [record.getMessage() for record in logs.records]
        self.assertEqual(
            len([m for m in messages if "rejected the visibility" in m]), 1)
        drops = [record.getMessage().split(":")[0]
                 for record in logs.records if record.levelname == "DEBUG"
                 and record.getMessage().startswith("Dropping")]
        self.assertEqual(drops, [
            "Dropping 2 operation(s) of group olim",
            "Dropping 1 operation(s) of group olim",
            "Dropping 1 operation(s) of group olim"])

    async def test_group_data_flows_again_once_its_visibility_is_sent(self):
        hidden = {"hide_at": None, "show_at": None, "freeze_at": None,
                  "unfreeze_at": None, "staff_password": None}
        self.outcomes = {("put", url("olim/visibility")): 400}
        await self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.VISIBILITY_TYPE, hidden, "olim")))

        # New settings do not get there at first (RWS is restarting): the
        # data queued with them waits for them, instead of being dropped.
        self.outcomes = {("put", url("olim/visibility")):
                         requests.exceptions.ConnectionError("refused")}
        self.calls.clear()
        batch = stamped_entries(
            ProxyOperation(ProxyExecutor.VISIBILITY_TYPE, hidden, "olim"),
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"))
        await self.executor.execute(batch)
        self.assertEqual(self.urls(), [url("olim/visibility")])
        requeued = await self.drain()
        self.assertEqual(stamps(requeued), stamps(batch))

        # The ranking takes them: that data goes after them, and so does
        # the data that comes later.
        self.outcomes.clear()
        self.calls.clear()
        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            await self.executor.execute(requeued)
            await self.executor.execute(entries(
                ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                               {"1": {}}, "olim")))
        self.assertEqual(self.calls, [
            ("put", url("olim/visibility"), hidden),
            ("put", url("olim/contests/"), {"c": {}}),
            ("put", url("olim/submissions/"), {"1": {}})])
        # The data dropped meanwhile is not sent again by itself: now
        # that the settings get there, Regenerate can send it.
        self.assertEqual(len(logs.output), 1)
        self.assertIn("accepted the visibility of group olim",
                      logs.output[0])
        self.assertIn("Regenerate", logs.output[0])

    async def test_failing_group_does_not_slow_down_the_others(self):
        # olim keeps failing, omips works and gets new data all the time.
        self.outcomes = {("put", url("olim/contests/")): 503}
        stamp = datetime(2020, 1, 1, 9, 0, 0)
        self.executor.enqueue(ProxyOperation(
            ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"), 3, stamp)
        scores = iter(range(1000))

        def new_score():
            self.executor.enqueue(ProxyOperation(
                ProxyExecutor.SUBMISSION_TYPE, {"%d" % next(scores): {}},
                "omips"))

        await self.rounds_until(184, before_each=new_score)

        # olim is tried again later and later...
        olim = self.request_times[url("olim/contests/")]
        self.assertEqual([later - earlier
                          for earlier, later in zip(olim, olim[1:])],
                         [1, 2, 4, 8, 16, 32, 60, 60])
        # ... while omips gets its data at every round, which comes a
        # second after the previous one at most.
        self.assertEqual(self.request_times[url("omips/submissions/")],
                         list(range(184)))
        self.assertLessEqual(max(self.retry_waits.waits),
                             ProxyExecutor.MIN_RETRY_WAIT)
        # The data of olim was put back as it was, every time.
        self.assertEqual(
            [(e.item.data, e.priority, e.timestamp)
             for e in await self.drain()],
            [({"c": {}}, 3, stamp)])

    async def test_group_that_recovers_starts_its_wait_over(self):
        olim = ("put", url("olim/contests/"))
        omips = ("put", url("omips/contests/"))
        self.outcomes = {olim: 503, omips: 503}
        for group in ("olim", "omips"):
            self.executor.enqueue(ProxyOperation(
                ProxyExecutor.CONTEST_TYPE, {"c": {}}, group))
        await self.rounds_until(7)
        # olim takes its data again, omips still fails.
        del self.outcomes[olim]
        await self.rounds_until(8)
        # olim fails again, with new data.
        self.outcomes[olim] = 503
        self.executor.enqueue(ProxyOperation(
            ProxyExecutor.CONTEST_TYPE, {"c2": {}}, "olim"))
        await self.rounds_until(16)

        # olim waits 1, 2 and 4 seconds, takes its data at 7, and
        # starts over from 1 second; omips keeps doubling.
        self.assertEqual(self.request_times[url("olim/contests/")],
                         [0, 1, 3, 7, 8, 9, 11, 15])
        self.assertEqual(self.request_times[url("omips/contests/")],
                         [0, 1, 3, 7, 15])

    async def test_new_data_does_not_wait_for_a_group_backing_off(self):
        self.outcomes = {("put", url("olim/contests/")):
                         requests.exceptions.ConnectionError("down")}
        # The wait after the failure of olim never ends by itself.
        self.retry_waits.block_after = 1
        self.start_running(self.executor)
        self.executor.enqueue(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"))
        await self.wait_until(lambda: self.retry_waits.waits)

        # A score of omips comes while the executor waits to try olim.
        self.executor.enqueue(
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE, {"1": {}}, "omips"))

        sent = ("put", url("omips/submissions/"), {"1": {}})
        await self.wait_until(lambda: sent in self.calls)
        # olim was not tried again: its wait is not over.
        self.assertEqual(self.urls().count(url("olim/contests/")), 1)

    async def test_data_of_a_group_backing_off_does_not_start_rounds(self):
        # Each round takes the whole queue out and puts back what waits:
        # a round for every score queued for a group that cannot be
        # tried yet would take time quadratic in its backlog.
        self.outcomes = {("put", url("olim/contests/")):
                         requests.exceptions.ConnectionError("down")}
        # The wait after the failure of olim never ends by itself.
        self.retry_waits.block_after = 1
        rounds: list[int] = []
        real_execute = self.executor.execute

        async def counting_execute(batch):
            rounds.append(len(batch))
            await real_execute(batch)

        self.executor.execute = counting_execute
        self.start_running(self.executor)
        self.executor.enqueue(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"))
        await self.wait_until(lambda: self.retry_waits.waits)

        # Scores of olim keep coming while it waits.
        for i in range(20):
            self.executor.enqueue(ProxyOperation(
                ProxyExecutor.SUBMISSION_TYPE, {"%d" % i: {}}, "olim"))
            await asyncio.sleep(0.001)
        # A score of omips does start a round.
        self.executor.enqueue(ProxyOperation(
            ProxyExecutor.SUBMISSION_TYPE, {"x": {}}, "omips"))
        await self.wait_until(
            lambda: url("omips/submissions/") in self.urls())

        # The first round, and the one of omips, with all that waits.
        self.assertEqual(rounds, [1, 22])
        self.assertEqual(self.urls().count(url("olim/contests/")), 1)

    async def test_data_of_other_groups_is_batched_while_one_backs_off(self):
        # A round costs time proportional to what waits in the queue: a
        # round for each score of a group that can be sent, while
        # another group backs off with a backlog, would cost that much
        # for each of them.
        self.outcomes = {("put", url("olim/contests/")):
                         requests.exceptions.ConnectionError("down")}
        # The wait after the failure of olim never ends by itself.
        self.retry_waits.block_after = 1
        rounds: list[int] = []
        real_execute = self.executor.execute

        async def counting_execute(batch):
            rounds.append(len(batch))
            await real_execute(batch)

        self.executor.execute = counting_execute
        self.start_running(self.executor)
        self.executor.enqueue(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"))
        await self.wait_until(lambda: self.retry_waits.waits)

        # A burst of scores of omips, a millisecond apart.
        scores = {"%d" % i for i in range(20)}
        for score in sorted(scores):
            self.executor.enqueue(ProxyOperation(
                ProxyExecutor.SUBMISSION_TYPE, {score: {}}, "omips"))
            await asyncio.sleep(0.001)

        def sent_scores():
            return {score for method, target, payload in self.calls
                    if target == url("omips/submissions/")
                    for score in payload}

        await self.wait_until(lambda: sent_scores() == scores)
        # They go out together, in a round or two after the first one.
        self.assertLessEqual(len(rounds), 3, rounds)
        self.assertEqual(self.urls().count(url("olim/contests/")), 1)


def score(user: str) -> dict:
    """Return the data of a submission of user, as the service builds it."""
    return {"user": user, "task": "t", "time": 1000}


class TestProxyExecutorRefusals(unittest.IsolatedAsyncioTestCase):
    """What an executor does when a ranking refuses part of a list.

    RWS takes the entities of a PUT of a list all or none (see
    cmstestsuite/unit_tests/cmsranking/test_put_list.py): one entity it
    refuses gets the whole list refused.

    """

    async def asyncSetUp(self):
        # The requests made, in order, as (method, URL, JSON payload).
        self.calls: list[tuple[str, str, dict | None]] = []
        # The ranking answers the requests to the URLs of url_statuses
        # with their status, whatever they hold. Else it refuses as
        # invalid (400) a request holding one of refused_ids, else
        # forbids (403) one holding one of forbidden_ids, else fails
        # (503) on one holding one of failing_ids, else takes it.
        self.url_statuses: dict[str, int] = {}
        self.refused_ids: set[str] = set()
        self.forbidden_ids: set[str] = set()
        self.failing_ids: set[str] = set()

        def fake_request(method, ok_status):
            def send(target, body=None, **kwargs):
                payload = None if body is None else json.loads(body)
                self.calls.append((method, target, payload))
                ids = set(payload or ())
                response = MagicMock()
                if target in self.url_statuses:
                    response.status_code = self.url_statuses[target]
                elif ids & self.refused_ids:
                    response.status_code = 400
                elif ids & self.forbidden_ids:
                    response.status_code = 403
                elif ids & self.failing_ids:
                    response.status_code = 503
                else:
                    response.status_code = ok_status
                return response
            return send

        for method, ok_status in (("put", 200), ("delete", 204)):
            patcher = patch("cms.service.ProxyService.requests." + method,
                            fake_request(method, ok_status))
            patcher.start()
            self.addCleanup(patcher.stop)

        self.executor = ProxyExecutor(RANKING)

    def refusal_warnings(self, logs) -> list[str]:
        return [record.getMessage() for record in logs.records
                if " refused " in record.getMessage()]

    def test_a_refused_batch_is_split_and_only_refused_entities_are_dropped(
        self,
    ):
        # Bob is not known to the ranking.
        self.refused_ids = {"2"}
        batch = entries(
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"1": score("alice")}),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"2": score("bob")}),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"3": score("carol")}))

        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            unsent = self.executor._execute_sync(batch)

        # The list is refused, then each submission is sent on its own.
        self.assertEqual(self.calls, [
            ("put", url("submissions/"), {"1": score("alice"),
                                          "2": score("bob"),
                                          "3": score("carol")}),
            ("put", url("submissions/"), {"1": score("alice")}),
            ("put", url("submissions/"), {"2": score("bob")}),
            ("put", url("submissions/"), {"3": score("carol")})])
        # What was refused on its own is not sent again by the executor,
        # but it is reported.
        self.assertEqual(unsent, [])
        self.assertEqual(self.executor._refused_in_round, [
            RefusedEntity(None, ProxyExecutor.SUBMISSION_TYPE, "2",
                          score("bob"))])
        self.assertEqual(self.refusal_warnings(logs), [
            "Ranking http://localhost:8890/ refused 1 of 3 submissions of "
            "group (root) (2); they will be sent again after the contest "
            "data of the group."])

    def test_a_refusal_while_splitting_is_only_logged_at_debug(self):
        # The warning of the round names it: one warning per entity on
        # top of that would flood the log with each refused round.
        self.refused_ids = {"2"}
        batch = entries(
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"1": score("alice")}),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"2": score("bob")}),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"3": score("carol")}))

        with self.assertLogs("cms.service.ProxyService", "DEBUG") as logs:
            self.executor._execute_sync(batch)

        status = "Status 400 while sending submissions to ranking " \
            "http://localhost:8890/."
        self.assertEqual(
            [record.getMessage() for record in logs.records
             if record.levelname == "WARNING"],
            [status,
             "Ranking http://localhost:8890/ refused 1 of 3 submissions of "
             "group (root) (2); they will be sent again after the contest "
             "data of the group."])
        # The merged request, then the refused one alone.
        self.assertEqual(
            [record.levelname for record in logs.records
             if record.getMessage() == status],
            ["WARNING", "DEBUG"])

    def test_the_warning_names_at_most_ten_refused_ids(self):
        # One entry, as the sweep queues the scores of a contest.
        self.refused_ids = {"%d" % i for i in range(1, 13)}
        batch = entries(ProxyOperation(
            ProxyExecutor.SUBMISSION_TYPE,
            {"%d" % i: score("u%d" % i) for i in range(1, 14)}, "olim"))

        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            self.executor._execute_sync(batch)

        self.assertEqual(len(self.executor._refused_in_round), 12)
        # The first REFUSED_IDS_SHOWN ids, sorted as strings.
        self.assertEqual(self.refusal_warnings(logs), [
            "Ranking http://localhost:8890/ refused 12 of 13 submissions of "
            "group olim (1, 10, 11, 12, 2, 3, 4, 5, 6, 7, ...); they will "
            "be sent again after the contest data of the group."])

    def test_a_single_refused_entity_is_not_split(self):
        self.refused_ids = {"2"}
        batch = entries(
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"2": score("bob")}, "olim"))

        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            unsent = self.executor._execute_sync(batch)

        self.assertEqual(self.calls, [
            ("put", url("olim/submissions/"), {"2": score("bob")})])
        self.assertEqual(unsent, [])
        self.assertEqual(self.executor._refused_in_round, [
            RefusedEntity("olim", ProxyExecutor.SUBMISSION_TYPE, "2",
                          score("bob"))])
        warnings = self.refusal_warnings(logs)
        self.assertEqual(len(warnings), 1)
        self.assertIn("refused 1 of 1 submissions of group olim (2)",
                      warnings[0])
        self.assertIn("they will be sent again after the contest data",
                      warnings[0])

    def test_a_batch_refused_for_another_reason_is_not_split(self):
        # E.g. wrong credentials (401), or an RWS without the namespace
        # (404): each entity alone would be refused the same way.
        for status in (401, 404):
            with self.subTest(status=status):
                self.executor = ProxyExecutor(RANKING)
                self.calls.clear()
                self.url_statuses = {url("olim/submissions/"): status}
                batch = entries(
                    ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                                   {"1": score("alice")}, "olim"),
                    ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                                   {"2": score("bob")}, "olim"),
                    ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                                   {"3": score("carol")}, "olim"))

                with self.assertLogs(
                        "cms.service.ProxyService", "WARNING") as logs:
                    unsent = self.executor._execute_sync(batch)

                # One request, dropped whole as before, and not reported.
                self.assertEqual([(target, list(payload))
                                  for method, target, payload in self.calls],
                                 [(url("olim/submissions/"),
                                   ["1", "2", "3"])])
                self.assertEqual(unsent, [])
                self.assertEqual(self.executor._refused_in_round, [])
                self.assertEqual(self.refusal_warnings(logs), [])
                hints = [line for line in logs.output
                         if "Regenerate" in line]
                self.assertEqual(len(hints), 1)
                self.assertIn(
                    "Ranking http://localhost:8890/ rejected the "
                    "submissions of group olim. It will not be sent again: "
                    "use Regenerate for this group in AWS (Ranking groups) "
                    "to send its data again.", hints[0])

    def test_an_entity_refused_alone_for_another_reason_is_not_reported(
        self,
    ):
        # The list is refused as invalid because of "2", and then the
        # ranking forbids "3" alone (its credentials changed meanwhile).
        self.refused_ids = {"2"}
        self.forbidden_ids = {"3"}
        batch = entries(
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"1": score("alice")}, "olim"),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"2": score("bob")}, "olim"),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"3": score("carol")}, "olim"))

        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            unsent = self.executor._execute_sync(batch)

        self.assertEqual(
            [list(payload) for method, target, payload in self.calls],
            [["1", "2", "3"], ["1"], ["2"], ["3"]])
        self.assertEqual(unsent, [])
        # Only "2" was refused as invalid: "3" is dropped, as a batch
        # refused for another reason is.
        self.assertEqual(self.executor._refused_in_round, [
            RefusedEntity("olim", ProxyExecutor.SUBMISSION_TYPE, "2",
                          score("bob"))])
        warnings = self.refusal_warnings(logs)
        self.assertEqual(len(warnings), 1)
        self.assertIn("refused 1 of 3 submissions of group olim (2)",
                      warnings[0])
        hints = [line for line in logs.output if "Regenerate" in line]
        self.assertEqual(len(hints), 1)
        self.assertIn("rejected the submissions of group olim", hints[0])

    def test_an_unsent_answer_while_splitting_puts_the_rest_back(self):
        # The ranking refuses the list because of "3", takes "1", and
        # fails on "2", e.g. while it restarts.
        self.refused_ids = {"3"}
        self.failing_ids = {"2"}
        batch = entries(
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"1": score("alice")}, "olim"),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"2": score("bob")}, "olim"),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"3": score("carol")}, "olim"),
            ProxyOperation(ProxyExecutor.SUBCHANGE_TYPE,
                           {"1s": {"submission": "1"}}, "olim"),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"4": score("dave")}, "omips"))

        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            unsent = self.executor._execute_sync(batch)

        # A failure while splitting is a warning, as any failure is.
        self.assertIn(
            "Status 503 while sending submissions to ranking "
            "http://localhost:8890/olim/.",
            [record.getMessage() for record in logs.records])
        # The split stops at "2", and the group stalls: its subchanges
        # are not sent. omips is not held back.
        self.assertEqual([(target, list(payload))
                          for method, target, payload in self.calls], [
            (url("olim/submissions/"), ["1", "2", "3"]),
            (url("olim/submissions/"), ["1"]),
            (url("olim/submissions/"), ["2"]),
            (url("omips/submissions/"), ["4"])])
        # What did not go in alone goes back, in order: "1" went in.
        self.assertEqual(unsent, batch[1:4])
        # "3" was not refused on its own (yet).
        self.assertEqual(self.executor._refused_in_round, [])

    def test_an_entry_is_sent_only_if_all_its_entities_went_in(self):
        with self.subTest("refused"):
            self.refused_ids = {"b"}
            batch = entries(
                ProxyOperation(ProxyExecutor.USER_TYPE,
                               {"a": {"f_name": "A"}, "b": {"f_name": "B"}},
                               "olim"),
                ProxyOperation(ProxyExecutor.USER_TYPE,
                               {"c": {"f_name": "C"}}, "olim"))

            with self.assertLogs(
                    "cms.service.ProxyService", "WARNING") as logs:
                unsent = self.executor._execute_sync(batch)

            # "b" is dropped: both entries are done with.
            self.assertEqual(
                [list(payload) for method, target, payload in self.calls],
                [["a", "b", "c"], ["a"], ["b"], ["c"]])
            self.assertEqual(unsent, [])
            self.assertEqual(self.executor._refused_in_round, [
                RefusedEntity("olim", ProxyExecutor.USER_TYPE, "b",
                              {"f_name": "B"})])
            # ProxyService sends the contest data again at the next
            # sweep; if that does not help, the data needs fixing.
            self.assertEqual(self.refusal_warnings(logs), [
                "Ranking http://localhost:8890/ refused 1 of 3 users of "
                "group olim (b); the contest data of the group will be "
                "sent again at the next sweep. If this warning repeats, "
                "fix the data, then use Regenerate for this group in AWS "
                "(Ranking groups)."])

        with self.subTest("unsent"):
            self.executor = ProxyExecutor(RANKING)
            self.calls.clear()
            # "a" goes in and "b" is refused, but "c" does not get there.
            self.refused_ids = {"b"}
            self.failing_ids = {"c"}

            unsent = self.executor._execute_sync(batch)

            self.assertEqual(
                [list(payload) for method, target, payload in self.calls],
                [["a", "b", "c"], ["a"], ["b"], ["c"]])
            # The first entry is done with, the second one is not.
            self.assertEqual(unsent, batch[1:])
            self.assertEqual(self.executor._refused_in_round, [
                RefusedEntity("olim", ProxyExecutor.USER_TYPE, "b",
                              {"f_name": "B"})])

        with self.subTest("not all in"):
            self.executor = ProxyExecutor(RANKING)
            self.calls.clear()
            # "a" goes in, but "b" does not get there.
            self.refused_ids = {"c"}
            self.failing_ids = {"b"}

            unsent = self.executor._execute_sync(batch)

            self.assertEqual(
                [list(payload) for method, target, payload in self.calls],
                [["a", "b", "c"], ["a"], ["b"]])
            # Both entries go back: the first one although "a" went in.
            self.assertEqual(unsent, batch)
            self.assertEqual(self.executor._refused_in_round, [])

    def test_an_entity_that_cannot_be_encoded_does_not_drop_the_others(
        self,
    ):
        batch = entries(
            ProxyOperation(ProxyExecutor.USER_TYPE,
                           {"a": {"f_name": "A"}}, "olim"),
            ProxyOperation(ProxyExecutor.USER_TYPE,
                           {"u": {"f_name": object()}}, "olim"))

        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            unsent = self.executor._execute_sync(batch)

        # Neither the list nor "u" can be encoded: only "a" is sent.
        self.assertEqual(self.calls, [
            ("put", url("olim/users/"), {"a": {"f_name": "A"}})])
        self.assertEqual(unsent, [])
        # Fixing what it refers to would not help: it is not handed over,
        # and the operator is told to fix the data, once.
        self.assertEqual(self.executor._refused_in_round, [])
        self.assertEqual(self.refusal_warnings(logs), [])
        hints = [line for line in logs.output if "Regenerate" in line]
        self.assertEqual(len(hints), 1)
        self.assertIn("users of group olim cannot be encoded", hints[0])

    def test_reset_and_visibility_are_never_split(self):
        settings = {"hide_at": None, "show_at": None, "freeze_at": None,
                    "unfreeze_at": None, "staff_password": None}
        self.url_statuses = {url("olim/contests/"): 400,
                             url("omips/visibility"): 400}
        batch = entries(
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u": {}}, "olim"),
            ProxyOperation(ProxyExecutor.VISIBILITY_TYPE, settings, "omips"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"v": {}}, "omips"))

        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            unsent = self.executor._execute_sync(batch)

        # The refused reset is dropped and the data of olim is sent; the
        # refused visibility drops the data of omips. One request each.
        self.assertEqual(self.calls, [
            ("delete", url("olim/contests/"), None),
            ("put", url("omips/visibility"), settings),
            ("put", url("olim/users/"), {"u": {}})])
        self.assertEqual(unsent, [])
        self.assertEqual(self.executor._refused_in_round, [])
        self.assertEqual(self.refusal_warnings(logs), [])
        messages = [record.getMessage() for record in logs.records]
        self.assertTrue(any("rejected the reset of group olim" in m
                            and "Regenerate" in m for m in messages))
        self.assertTrue(any("rejected the visibility of group omips" in m
                            for m in messages))

    def test_no_refusal_sends_one_put_per_type(self):
        batch = entries(
            ProxyOperation(ProxyExecutor.USER_TYPE, {"a": {}}, "olim"),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"1": score("a")}, "olim"),
            ProxyOperation(ProxyExecutor.SUBCHANGE_TYPE,
                           {"1s": {"submission": "1"}}, "olim"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"b": {}}, "olim"),
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"2": score("b")}, "olim"),
            ProxyOperation(ProxyExecutor.SUBCHANGE_TYPE,
                           {"2s": {"submission": "2"}}, "olim"))

        with self.assertNoLogs("cms.service.ProxyService", "WARNING"):
            unsent = self.executor._execute_sync(batch)

        self.assertEqual([(target, list(payload))
                          for method, target, payload in self.calls], [
            (url("olim/users/"), ["a", "b"]),
            (url("olim/submissions/"), ["1", "2"]),
            (url("olim/subchanges/"), ["1s", "2s"])])
        self.assertEqual(unsent, [])
        self.assertEqual(self.executor._refused_in_round, [])

    async def test_execute_hands_refused_entities_to_the_callback(self):
        threads: list[int] = []
        on_refused = MagicMock(
            side_effect=lambda refused: threads.append(threading.get_ident()))
        self.executor = ProxyExecutor(RANKING, on_refused=on_refused)

        # Nothing refused: the callback is not called.
        await self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {"1": score("alice")}, "olim")))
        on_refused.assert_not_called()

        # Bob is not known to the ranking, nor then his submission.
        self.refused_ids = {"2", "2s"}
        with self.assertLogs("cms.service.ProxyService", "WARNING") as logs:
            await self.executor.execute(entries(
                ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                               {"2": score("bob")}, "olim"),
                ProxyOperation(ProxyExecutor.SUBCHANGE_TYPE,
                               {"2s": {"submission": "2"}}, "olim"),
                ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                               {"3": score("carol")}, "olim")))

        # Once, with everything refused in the round, on the event loop
        # (the requests are made in another thread).
        on_refused.assert_called_once_with([
            RefusedEntity("olim", ProxyExecutor.SUBMISSION_TYPE, "2",
                          score("bob")),
            RefusedEntity("olim", ProxyExecutor.SUBCHANGE_TYPE, "2s",
                          {"submission": "2"})])
        self.assertEqual(threads, [threading.get_ident()])
        # The round is over: nothing is kept for the next one.
        self.assertEqual(self.executor._refused_in_round, [])
        # Scores and tokens are sent again, as submissions are.
        self.assertEqual(self.refusal_warnings(logs), [
            "Ranking http://localhost:8890/ refused 1 of 2 submissions of "
            "group olim (2); they will be sent again after the contest "
            "data of the group.",
            "Ranking http://localhost:8890/ refused 1 of 1 subchanges of "
            "group olim (2s); they will be sent again after the contest "
            "data of the group."])


if __name__ == "__main__":
    unittest.main()
