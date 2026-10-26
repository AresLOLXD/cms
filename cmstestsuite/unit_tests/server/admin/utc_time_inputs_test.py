#!/usr/bin/env python3

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

"""Tests that the AWS inputs for a UTC time carry the data-utc-time mark.

The page script writes the time of the server zone next to every marked
input, so an unmarked UTC input gets no hint.

"""

import re
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from cms.db import RankingGroup
from cms.server.admin.handlers.rankinggroup import WINDOW_FIELDS, \
    visibility_view
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT


NOW = datetime(2026, 10, 10, 20, 30, 15)


def render_core(page: str, **params) -> str:
    template = AWS_ENVIRONMENT.get_template(page)
    params.setdefault(
        "url", lambda *parts: "/" + "/".join(str(p) for p in parts))
    params.setdefault("xsrf_form_html", "")
    params.setdefault("admin", SimpleNamespace(permission_all=True))
    return "".join(template.blocks["core"](template.new_context(params)))


def render_fragment(page: str, **params) -> str:
    return AWS_ENVIRONMENT.get_template(page).render(**params)


class TestUtcTimeInputs(unittest.TestCase):

    def pages(self):
        """Return (page, UTC input names, its html) for each template."""
        group = RankingGroup(name="olim", description="OLIM")
        settings = SimpleNamespace(
            start=NOW, stop=NOW, analysis_enabled=False, analysis_start=NOW,
            analysis_stop=NOW, per_user_time=timedelta(0))
        participation = MagicMock(
            starting_time=None, team=None, ip=None, password=None,
            delay_time=timedelta(0), extra_time=timedelta(0),
            questions=[], messages=[])
        names = [name for name, _label in WINDOW_FIELDS]
        yield ("ranking_group.html", names, render_core(
            "ranking_group.html", ranking_group=group, group_contests=[],
            view=visibility_view(group, NOW)))
        yield ("add_ranking_group.html", names, render_core(
            "add_ranking_group.html", window_fields=WINDOW_FIELDS))
        yield ("fragments/group_settings.html",
               ["start", "stop", "analysis_start", "analysis_stop"],
               render_fragment("fragments/group_settings.html",
                               group=settings))
        yield ("participation.html", ["starting_time"], render_core(
            "participation.html", participation=participation,
            # The imports of the template are outside of its core block,
            # and it indexes url.
            url=MagicMock(return_value="/x"),
            macro_reevaluation_buttons=MagicMock(),
            macro_submission=MagicMock(),
            macro_question=MagicMock(), macro_markdown=MagicMock(),
            macro_activity=MagicMock(),
            selected_user=SimpleNamespace(id=1, username="u"),
            contest=SimpleNamespace(id=1, name="c", groups=[]),
            submissions=[], submission_count=0, submission_page=1,
            submission_pages=1, teams=[], timezone="UTC",
            activity_simultaneous=None, activity_intervals=[],
            activity_end_reason={}, activity_flagged_ids=set()))

    def test_every_utc_input_is_marked(self):
        for page, names, html in self.pages():
            with self.subTest(page=page):
                for name in names:
                    (tag,) = re.findall(
                        r'<input[^>]*name="%s"[^>]*>' % name, html)
                    self.assertIn("data-utc-time", tag)
                self.assertEqual(html.count("data-utc-time"), len(names))


if __name__ == "__main__":
    unittest.main()
