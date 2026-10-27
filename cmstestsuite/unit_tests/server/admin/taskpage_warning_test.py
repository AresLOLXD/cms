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

"""Tests for the invalid score parameters warning of the task page."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cms.db import Dataset, Task, Testcase
from cms.grading.scoretypes import SCORE_TYPES
from cms.grading.tasktypes import TASK_TYPES
from cms.server.admin.handlers.task import TaskHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT


class TestTaskPageScoreTypeWarning(unittest.TestCase):

    def setUp(self):
        self.task = Task(name="task", title="Task")
        # Datasets 1 and 3 name existing testcases; dataset 2 does not.
        self.datasets = {}
        for dataset_id, regex in ((1, "^a"), (2, "^<z&"), (3, "^b")):
            dataset = Dataset(
                task=self.task, description=str(dataset_id),
                task_type="Batch", task_type_parameters=[],
                score_type="GroupMin",
                score_type_parameters=[{"max_score": 100, "testcases": regex}])
            dataset.id = dataset_id
            for codename in ("a", "b"):
                Testcase(dataset=dataset, codename=codename, public=True,
                         input="input", output="output")
            self.datasets[dataset_id] = dataset

    def page_params(self):
        """Run the task page and return the parameters it renders with."""
        handler = TaskHandler.__new__(TaskHandler)
        handler.safe_get_item = MagicMock(return_value=self.task)
        handler.sql_session = MagicMock()
        handler.sql_session.execute.return_value.scalars.return_value \
            .all.return_value = []
        handler.render_params = MagicMock(return_value={})
        handler.render = MagicMock()
        handler._get_sync("5")
        handler.render.assert_called_once()
        self.assertEqual(handler.render.call_args.args, ("task.html",))
        return handler.render.call_args.kwargs

    def get(self):
        """Run the task page and return the warnings it renders with."""
        return self.page_params()["score_type_warnings"]

    def render_core(self):
        """Render the core block of the real template with the page params."""
        template = AWS_ENVIRONMENT.get_template("task.html")
        # The context that the base handler adds to every page.
        params = dict(
            self.page_params(), rtd_version="latest", timestamp=None,
            url=lambda *parts: "/" + "/".join(str(p) for p in parts),
            admin=SimpleNamespace(permission_all=True),
            SCORE_TYPES=SCORE_TYPES, TASK_TYPES=TASK_TYPES,
            xsrf_form_html="")
        return "".join(template.blocks["core"](template.new_context(params)))

    def test_only_the_invalid_dataset_gets_its_message(self):
        warnings = self.get()

        self.assertEqual(list(warnings), [2])
        self.assertIn("No testcase matches", warnings[2])

    def test_a_failing_validation_does_not_break_the_page(self):
        with patch.object(Dataset, "validate_score_type",
                          side_effect=RuntimeError("secret detail")):
            warnings = self.get()

        self.assertEqual(set(warnings), {1, 2, 3})
        self.assertNotIn("secret detail", warnings[1])
        self.assertIn("could not be checked", warnings[1])

    def test_the_template_shows_the_message_of_the_invalid_dataset_only(self):
        html = self.render_core()

        # Datasets 1 and 3 are valid; the message of dataset 2 is escaped.
        self.assertEqual(html.count('<p class="score-type-warning"'), 1)
        self.assertIn("Currently invalid: ", html)
        self.assertIn("the regexp &#39;^&lt;z&amp;&#39;.</strong></p>", html)

    def test_the_template_shows_nothing_for_valid_datasets(self):
        self.task.datasets.remove(self.datasets[2])

        self.assertNotIn("score-type-warning", self.render_core())

    def test_the_template_renders_a_task_without_datasets(self):
        self.task.datasets.clear()

        self.assertNotIn("score-type-warning", self.render_core())


if __name__ == "__main__":
    unittest.main()
