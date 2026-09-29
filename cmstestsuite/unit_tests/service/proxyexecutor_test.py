"""Tests for ProxyExecutor's per-group batching, resets and failures."""

import asyncio
import json
import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from urllib.parse import urljoin

import requests.exceptions

from cms.io.priorityqueue import QueueEntry
from cms.service.ProxyService import ProxyExecutor, ProxyOperation


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
                # The operator is told how to fix that group, and only
                # that one.
                hints = [line for line in logs.output
                         if "Regenerate" in line]
                self.assertEqual(len(hints), 1)
                self.assertIn("olim", hints[0])
                self.assertIn("submissions", hints[0])
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
        hidden = {"hide_at": None, "show_at": None, "freeze_at": None,
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
        hidden = {"hide_at": None, "show_at": None, "freeze_at": None,
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


if __name__ == "__main__":
    unittest.main()
