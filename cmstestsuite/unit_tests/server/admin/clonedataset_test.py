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

"""Tests for the clone of a dataset in AWS."""

import json
import unittest
from unittest.mock import MagicMock, PropertyMock, patch

from cms.db import Dataset, Task, Testcase
from cms.server.admin.handlers.dataset import CloneDatasetHandler

CYCLE = [{"max_score": 50, "testcases": 1, "depends_on": [1]},
         {"max_score": 50, "testcases": 1, "depends_on": [0]}]
VALID = [{"max_score": 20, "testcases": 1},
         {"max_score": 80, "testcases": 1, "depends_on": [0]}]


class TestCloneDatasetValidation(unittest.TestCase):

    def setUp(self):
        self.task = Task(name="task", title="Task")
        self.original = Dataset(
            task=self.task, description="original", task_type="OutputOnly",
            task_type_parameters=["diff"], score_type="GroupMin",
            score_type_parameters=[{"max_score": 100, "testcases": 2}])
        for codename in ("a", "b"):
            Testcase(dataset=self.original, codename=codename, public=True,
                     input="input", output="output")
        # clone_from() flushes the session of the dataset, which has none.
        patcher = patch.object(Dataset, "sa_session",
                               new_callable=PropertyMock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, score_type_parameters):
        """Run the clone form with the given score type parameters."""
        form = {
            "description": "clone",
            "time_limit": "1.0",
            "memory_limit": "256",
            "task_type": "OutputOnly",
            "TaskTypeOptions_OutputOnly_output_eval": "diff",
            "score_type": "GroupMin",
            "score_type_parameters": json.dumps(score_type_parameters),
        }
        handler = CloneDatasetHandler.__new__(CloneDatasetHandler)
        # "service" is a read-only property of the request handler that
        # returns self.application.service.
        handler.application = MagicMock()
        handler.sql_session = MagicMock()
        handler.safe_get_item = MagicMock(
            side_effect=lambda cls, _id: (
                self.original if cls is Dataset else self.task))
        handler.get_argument = MagicMock(
            side_effect=lambda name, default=None: form.get(name, default))
        handler.try_commit = MagicMock(return_value=True)
        handler.redirect = MagicMock()
        handler.url = MagicMock(
            side_effect=lambda *parts: "/" + "/".join(map(str, parts)))
        handler._post_sync("7")
        return handler

    def test_an_invalid_clone_is_refused_and_not_committed(self):
        handler = self.post(CYCLE)

        handler.try_commit.assert_not_called()
        notification = handler.application.service.add_notification
        notification.assert_called_once()
        self.assertEqual(notification.call_args.args[1],
                         "Invalid score type parameters")
        self.assertIn("cycle", notification.call_args.args[2])
        handler.redirect.assert_called_once_with("/dataset/7/clone")

    def test_a_valid_clone_is_committed(self):
        handler = self.post(VALID)

        handler.try_commit.assert_called_once_with()
        handler.application.service.add_notification.assert_not_called()
        handler.redirect.assert_called_once_with("/task/None")


if __name__ == "__main__":
    unittest.main()
