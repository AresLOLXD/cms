#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
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

"""Tests for the deletion of old service log files at startup."""

import os
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cms import config
from cms.io.async_service import AsyncService
from cms.io.service import Service
from cms.log import prune_old_logs
from cms.service.LogService import LogService
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


DAY = 24 * 60 * 60


class TestPruneOldLogs(unittest.TestCase):

    def setUp(self):
        self.log_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.log_dir)

    def make(self, name: str, age_days: float) -> str:
        """Create a file in the log directory, modified that long ago."""
        path = os.path.join(self.log_dir, name)
        with open(path, "w") as f:
            f.write("log\n")
        modified = time.time() - age_days * DAY
        os.utime(path, (modified, modified))
        return path

    def test_prune_removes_old_log_files(self):
        old = self.make("old.log", 31)
        prune_old_logs(self.log_dir)
        self.assertFalse(os.path.exists(old))

    def test_prune_keeps_recent_files(self):
        recent = self.make("recent.log", 29)
        today = self.make("today.log", 0)
        prune_old_logs(self.log_dir)
        self.assertTrue(os.path.exists(recent))
        self.assertTrue(os.path.exists(today))

    def test_prune_keeps_last_log_symlink_other_files_and_subdirectories(
        self
    ):
        # Everything below is old enough to go, but is not a log file.
        target = self.make("target.log", 0)
        last = os.path.join(self.log_dir, "last.log")
        os.symlink(target, last)
        old_target = self.make("old-target.log", 31)
        old_link = os.path.join(self.log_dir, "old-link.log")
        os.symlink(old_target, old_link)
        os.remove(old_target)
        notes = self.make("notes.txt", 100)
        directory = os.path.join(self.log_dir, "archive.log")
        os.mkdir(directory)
        os.utime(directory, (time.time() - 100 * DAY,) * 2)
        inner = os.path.join(directory, "inner.log")
        with open(inner, "w") as f:
            f.write("log\n")
        os.utime(inner, (time.time() - 100 * DAY,) * 2)

        self.assertEqual(prune_old_logs(self.log_dir), 0)

        self.assertTrue(os.path.islink(last))
        self.assertTrue(os.path.islink(old_link))
        self.assertTrue(os.path.exists(notes))
        self.assertTrue(os.path.isdir(directory))
        self.assertTrue(os.path.exists(inner))

    def test_prune_ignores_a_file_that_disappears(self):
        first = self.make("first.log", 40)
        second = self.make("second.log", 40)
        third = self.make("third.log", 40)
        real_remove = os.remove

        def remove(path):
            if path == second:
                raise FileNotFoundError(path)
            real_remove(path)

        with patch("cms.log.os.remove", side_effect=remove):
            removed = prune_old_logs(self.log_dir)

        self.assertEqual(removed, 2)
        self.assertFalse(os.path.exists(first))
        self.assertTrue(os.path.exists(second))
        self.assertFalse(os.path.exists(third))

    def test_prune_returns_the_number_removed(self):
        self.make("a.log", 31)
        self.make("b.log", 45)
        self.make("c.log", 5)
        self.assertEqual(prune_old_logs(self.log_dir), 2)
        self.assertEqual(prune_old_logs(self.log_dir), 0)

    def test_prune_ignores_a_directory_it_cannot_list(self):
        missing = os.path.join(self.log_dir, "missing")
        self.assertEqual(prune_old_logs(missing), 0)


class TestServicesPruneTheirOwnDirectory(
    ServiceLoggingIsolationMixin, unittest.TestCase
):
    """Each place that opens a service log file prunes its directory."""

    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        patcher = patch.object(config.global_, "log_dir", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("cms.io.async_service.get_service_address")
    def test_async_service_prunes_its_directory(self, mock_get_address):
        class PruningAsyncService(AsyncService):
            pass

        with patch("cms.io.async_service.prune_old_logs") as prune:
            PruningAsyncService(shard=3)
        prune.assert_called_once_with(
            os.path.join(self.root, "PruningAsyncService-3"))

    def test_gevent_service_prunes_its_directory(self):
        # Service.__init__ also takes over the process signal handlers,
        # so only its logging setup runs, on a stand-in with what it uses.
        service = SimpleNamespace(
            name="PruningWorker", shard=2, connect_to=MagicMock())
        with patch("cms.io.service.prune_old_logs") as prune:
            Service.initialize_logging(service)
        prune.assert_called_once_with(
            os.path.join(self.root, "PruningWorker-2"))

    @patch("cms.io.async_service.get_service_address")
    def test_log_service_prunes_its_directory(self, mock_get_address):
        with patch("cms.service.LogService.prune_old_logs") as prune:
            LogService(0)
        prune.assert_called_once_with(os.path.join(self.root, "cms"))


if __name__ == "__main__":
    unittest.main()
