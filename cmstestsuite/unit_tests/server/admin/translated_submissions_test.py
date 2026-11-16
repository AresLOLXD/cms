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

"""Tests for the translated AWS pages of submissions and user tests.

Pages are rendered with the stand-ins of aws_render, so they need no
database and say nothing about how a page looks. A page is asserted
only on the texts of this lane's files, because the pages of other
lanes may include them.

"""

import json
import unittest
from html.parser import HTMLParser

from cms.locale import DEFAULT_TRANSLATION
from cmstestsuite.unit_tests.server.admin import aws_render
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH


def page(name, translation, mode="full") -> str:
    return aws_render.render(aws_render.environment(mode), name, translation)


def snippet(source, translation, mode="full", **params) -> str:
    """Render a few lines of template, as a minimal page that imports
    the macros with the context."""
    template = aws_render.environment(mode).from_string(source)
    return template.render(
        url=lambda *p: "/" + "/".join(map(str, p)),
        translation=translation, gettext=translation.gettext,
        ngettext=translation.ngettext, **params)


class _Events(HTMLParser):
    """Collect the attributes (name, value) of every button."""

    def __init__(self):
        super().__init__()
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        if tag == "button":
            self.buttons.append(dict(attrs))


def buttons(html):
    parser = _Events()
    parser.feed(html)
    return parser.buttons


def page_url(page=0):
    return "/list?page=%d" % page


REEVALUATE = (
    '{% import "macro/reevaluation_buttons.html" as m with context %}'
    "{{ m.reevaluation_buttons(true, '/next', submission_id=1, "
    "dataset_id=2) }}")


class TestSubmissionPages(unittest.TestCase):

    def test_the_contest_submissions_page_in_spanish(self):
        # Scenario "A page group in Spanish"
        html = page("contest_submissions.html", SPANISH)
        for text in ("Todos los envíos", "Reevaluar todos los envíos de "
                     "este concurso", "para todos los datasets"):
            self.assertIn(text, html)
        for text in ("All Submissions", "Reevaluate all"):
            self.assertNotIn(text, html)

    def test_the_contest_submissions_page_in_english(self):
        # Scenario "A page in English is unchanged"
        html = page("contest_submissions.html", DEFAULT_TRANSLATION)
        for text in ("All Submissions", "Reevaluate all",
                     "submissions in this contest (for all datasets)"):
            self.assertIn(text, html)

    def test_the_contest_user_tests_page_in_spanish(self):
        # Scenario "A page group in Spanish"
        html = page("contest_user_tests.html", SPANISH)
        for text in ("Todos los user tests", "en este concurso"):
            self.assertIn(text, html)
        self.assertNotIn("All user tests", html)

    def test_the_page_selector_in_spanish(self):
        # Scenario "A page group in Spanish": the macro of pages.html in
        # a minimal page.
        source = ('{% import "macro/pages.html" as m with context %}'
                  "{{ m.selector(page_url, 3, 30) }}")
        html = snippet(source, SPANISH, page_url=page_url)
        self.assertIn("Páginas:", html)
        self.assertIn("Primera</a>", html)
        self.assertIn("Última</a>", html)
        self.assertNotIn("Pages:", html)
        self.assertIn("Pages:", snippet(source, DEFAULT_TRANSLATION, page_url=page_url))

    def test_the_reevaluate_confirmation_in_spanish(self):
        # Scenario "A confirmation in Spanish": the three buttons ask
        # the same question, in a script that is still valid.
        question = json.dumps(SPANISH.gettext(
            "Do you really want to reevaluate?"))
        self.assertIn("reevaluar", SPANISH.gettext(
            "Do you really want to reevaluate?"))
        found = buttons(snippet(REEVALUATE, SPANISH))
        self.assertEqual(
            [b["title"] for b in found],
            ["Compilación", "Evaluación", "Puntaje"])
        for button in found:
            self.assertTrue(
                button["onclick"].startswith("confirm(%s) && " % question),
                button["onclick"])

    def test_the_reevaluate_confirmation_in_english(self):
        # Scenario "A page in English is unchanged"
        found = buttons(snippet(REEVALUATE, DEFAULT_TRANSLATION))
        self.assertEqual(
            [b["title"] for b in found],
            ["Compilation", "Evaluation", "Score"])
        for button in found:
            self.assertTrue(button["onclick"].startswith(
                'confirm("Do you really want to reevaluate?") && '))


if __name__ == "__main__":
    unittest.main()
