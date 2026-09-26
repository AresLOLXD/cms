"""Tests for ProxyExecutor's per-group batching and resets."""

import json
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.parse import urljoin

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


if __name__ == "__main__":
    unittest.main()
