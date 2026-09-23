"""Tests for ProxyExecutor's per-group batching and resets."""

import gevent.monkey
gevent.monkey.patch_all()  # noqa

import unittest
from datetime import datetime
from unittest.mock import MagicMock, call, patch

from cms.io.priorityqueue import QueueEntry
from cms.service.ProxyService import ProxyExecutor, ProxyOperation


RANKING = "http://rws:secret@localhost:8890/"


def entries(*operations):
    return [QueueEntry(op, 0, datetime.now(), i)
            for i, op in enumerate(operations)]


class TestProxyExecutorGroups(unittest.TestCase):

    def setUp(self):
        self.calls = MagicMock()
        for name in ["safe_put_data", "safe_delete_data"]:
            patcher = patch("cms.service.ProxyService.%s" % name)
            self.calls.attach_mock(patcher.start(), name)
            self.addCleanup(patcher.stop)
        self.executor = ProxyExecutor(RANKING)

    def put_calls(self):
        return [(c.args[1], c.args[2]) for c in self.calls.mock_calls
                if c[0] == "safe_put_data"]

    def test_root_operations_use_plain_paths(self):
        self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}})))
        self.assertEqual(self.put_calls(), [("contests/", {"c": {}})])

    def test_group_operations_use_prefixed_paths(self):
        self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim")))
        self.assertEqual(self.put_calls(), [("olim/contests/", {"c": {}})])

    def test_batch_is_split_by_group(self):
        self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u1": {}}, "olim"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u2": {}}, "omips"),
            ProxyOperation(ProxyExecutor.USER_TYPE, {"u3": {}}, "olim")))
        self.assertCountEqual(self.put_calls(), [
            ("olim/users/", {"u1": {}, "u3": {}}),
            ("omips/users/", {"u2": {}})])

    def test_reset_discards_earlier_data_and_runs_before_puts(self):
        self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.USER_TYPE, {"stale": {}}, "olim"),
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, "olim"),
            ProxyOperation(ProxyExecutor.CONTEST_TYPE, {"c": {}}, "olim")))
        names = [c[0] for c in self.calls.mock_calls]
        self.assertEqual(names, ["safe_delete_data", "safe_delete_data",
                                 "safe_put_data"])
        self.assertEqual(
            [c.args[1] for c in self.calls.mock_calls[:2]],
            ["olim/contests/", "olim/users/"])
        self.assertEqual(self.put_calls(), [("olim/contests/", {"c": {}})])

    def test_reset_of_root_namespace(self):
        self.executor.execute(entries(
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, None)))
        self.assertEqual(self.calls.mock_calls, [
            call.safe_delete_data(RANKING, "contests/", unittest.mock.ANY),
            call.safe_delete_data(RANKING, "users/", unittest.mock.ANY)])


if __name__ == "__main__":
    unittest.main()
