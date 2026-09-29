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

    """

    def __init__(self):
        self.waits: list[float] = []
        # Once this many waits happened, the next ones never end.
        self.block_after: int | None = None

    async def sleep(self, delay: float) -> None:
        self.waits.append(delay)
        if self.block_after is not None \
                and len(self.waits) >= self.block_after:
            await asyncio.Event().wait()
        await asyncio.sleep(0)

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

        def fake_request(method, default_status):
            def send(target, body=None, **kwargs):
                payload = None if body is None else json.loads(body)
                self.calls.append((method, target, payload))
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
        patcher = patch("cms.service.ProxyService.asyncio", self.retry_waits)
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
        settings = {"hidden": True, "staff_password": "bcrypt:hash"}
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
        hidden = {"hidden": True, "staff_password": None}
        visible = {"hidden": False, "staff_password": None}
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
        hidden = {"hidden": True, "staff_password": None}
        visible = {"hidden": False, "staff_password": None}
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
                hints = [line for line in logs.output
                         if "Regenerate" in line]
                self.assertEqual(len(hints), 1)
                self.assertIn("olim", hints[0])
                self.assertIn("visibility", hints[0])
                self.assertNotIn("omips", hints[0])

    async def test_last_visibility_of_a_group_wins(self):
        self.outcomes = {("put", url("olim/visibility")):
                         requests.exceptions.ConnectionError("reset")}
        old = {"hidden": True, "staff_password": "bcrypt:old"}
        new = {"hidden": False, "staff_password": "bcrypt:new"}
        other = {"hidden": False, "staff_password": None}
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
                           {"hidden": False, "staff_password": None}, "olim"),
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim"))

        await self.executor.execute(batch)

        # Nothing of olim goes after a reset that did not get there.
        self.assertEqual(self.urls(), [url("olim/contests/")])
        self.assertEqual(stamps(await self.drain()), stamps(batch))


if __name__ == "__main__":
    unittest.main()
