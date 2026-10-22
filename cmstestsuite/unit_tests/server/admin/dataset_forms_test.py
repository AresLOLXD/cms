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

"""Tests for the score parameters field of the dataset forms of AWS.

task.html needs a large rendering context, so these tests assert on the
text of the templates and of the stylesheets instead of rendering them.

"""

import re
import unittest
from pathlib import Path

import cms.server.admin

ADMIN_DIR = Path(cms.server.admin.__file__).parent
CONTEST_DIR = ADMIN_DIR.parent / "contest"

HELP = ("Please see the score type documentation. "
        'A subtask can list "depends_on": [subtask numbers]; '
        "see the subtask dependencies page of the documentation.")


def read_admin(*parts):
    return ADMIN_DIR.joinpath(*parts).read_text(encoding="utf-8")


class DatasetFormsTestCase(unittest.TestCase):

    def test_textarea_has_rows_and_the_help_names_depends_on(self):
        for name in ("add_dataset.html", "task.html"):
            html = read_admin("templates", name)
            self.assertRegex(
                html,
                r'<textarea name="score_type_parameters[^"]*" rows="12">',
                name)
            self.assertIn("<span class=\"info\" title='%s'></span>" % HELP,
                          html, name)

    def test_update_has_the_no_rescore_hint(self):
        html = read_admin("templates", "task.html")
        match = re.search(r'<span class="hint">(.*?)</span>', html, re.S)
        self.assertIsNotNone(match)
        hint = match.group(1)
        self.assertIn("Update does not rescore", hint)
        self.assertIn("press S or E", hint)
        self.assertIn("live task during a contest", hint)
        # The hint is part of the form of the Update button.
        self.assertLess(html.index('value="Update"'), match.start())
        self.assertLess(match.start(), html.index("</form>", match.start()))

    def test_note_and_hint_have_styles(self):
        aws = read_admin("static", "aws_style.css")
        cws = CONTEST_DIR.joinpath(
            "static", "cws_style.css").read_text(encoding="utf-8")
        for css, selector in ((aws, ".hint"),
                              (aws, ".subtask-dependency"),
                              (cws, "#submission_detail .subtask-dependency")):
            match = re.search(
                r"^%s \{(.*?)\}" % re.escape(selector), css, re.M | re.S)
            self.assertIsNotNone(match, selector)
            self.assertIn("font-size: 0.9em", match.group(1), selector)
            self.assertRegex(match.group(1), r"color: #[0-9A-F]+;", selector)


if __name__ == "__main__":
    unittest.main()
