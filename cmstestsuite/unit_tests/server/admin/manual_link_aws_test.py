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

"""Tests for the Manual entry of the AWS sidebar (openspec aws-manual-link)."""

import re
import unittest
from unittest.mock import MagicMock, patch

from cms.conf import AWSConfig
from cms.server.admin.handlers.base import BaseHandler, manual_url
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH
from cmstestsuite.unit_tests.server.admin.lang_switch_test import \
    error_page, login_page
from cmstestsuite.unit_tests.server.admin.theme_aws_test import render_page
from cmstestsuite.unit_tests.server.admin.translated_chrome_test import \
    a_contest, sidebar, spanish


ADDRESS = "https://docs.example.org/es/"
LINK = re.compile(r'<a class="menu_link" href="([^"]*)" target="_blank" '
                  r'rel="noopener noreferrer"[^>]*>(.*?)</a>', re.S)


def the_links(html: str) -> list[tuple[str, str]]:
    """Return the (address, text) of the links that open a new tab."""
    return LINK.findall(html)


class TestAddress(unittest.TestCase):

    def test_default_address(self):
        # Scenario: Default address
        self.assertEqual(
            manual_url(AWSConfig().manual_url, "es"),
            "https://cms-omi.readthedocs.io/es/latest/")

    def test_language_of_the_page(self):
        # Scenario: Language of the page
        template = "https://x/{language}/"
        for identifier, expected in (("es", "es"), ("es-CL", "es"),
                                     ("en", "en"), ("fr", "en")):
            self.assertEqual(manual_url(template, identifier),
                             f"https://x/{expected}/", identifier)

    def test_own_address_and_other_braces(self):
        # Scenario: Own address
        # Scenario: Address without a placeholder
        self.assertEqual(manual_url("https://x/a{b}/", "es"), "https://x/a{b}/")
        self.assertEqual(manual_url("https://x/m.html", "en"),
                         "https://x/m.html")

    def test_empty_address_hides_the_entry(self):
        # Scenario: Hidden when empty
        self.assertEqual(manual_url("", "es"), "")


class TestHandler(unittest.TestCase):

    def test_the_handler_gives_the_page_its_address(self):
        # Scenario: Default address (the real handler, the real option)
        # Scenario: Language of the page
        handler = BaseHandler.__new__(BaseHandler)
        handler.application = MagicMock()
        handler.sql_session = MagicMock()
        handler._current_user = None
        handler.contest = None
        handler.url = handler.static_url_helper = MagicMock()
        for translation, language in ((SPANISH, "es"),
                                      (BaseHandler.translation, "en")):
            handler.translation = translation
            with patch.object(BaseHandler, "xsrf_form_html", return_value=""):
                params = handler.render_params()
            self.assertEqual(params["manual_url"],
                             f"https://cms-omi.readthedocs.io/{language}"
                             "/latest/")


class TestSidebar(unittest.TestCase):

    def test_home_page_and_contest_page(self):
        # Scenario: The home page
        # Scenario: A contest page
        # Scenario: The link is safe (rel is part of the pattern)
        for contest in (None, a_contest()):
            html = render_page("base.html", manual_url=ADDRESS, unanswered=0,
                               **sidebar(contest))
            self.assertEqual(
                [(address, text.split("<")[0])
                 for address, text in the_links(html)],
                [(ADDRESS, "Manual")])

    def test_no_entry_when_the_address_is_empty_or_missing(self):
        # Scenario: Hidden when empty
        for params in ({"manual_url": ""}, {}):
            html = render_page("base.html", **params, **sidebar())
            self.assertEqual(the_links(html), [])
            self.assertIn(">Resource usage</a>", html)

    def test_login_and_error_pages_have_no_entry(self):
        # Scenario: The login and error pages
        for html in (login_page(manual_url=ADDRESS),
                     error_page(manual_url=ADDRESS)):
            self.assertEqual(the_links(html), [])

    def test_entry_in_spanish(self):
        # Scenario: A Spanish page
        html = spanish("base.html", manual_url=ADDRESS, **sidebar())

        [(_, text)] = the_links(html)
        self.assertTrue(text.startswith("Manual"))
        self.assertIn('title="Se abre en una pestaña nueva"', html)
        self.assertNotIn("Opens in a new tab", html)


if __name__ == "__main__":
    unittest.main()
