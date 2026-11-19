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

"""The messages that the form parsers of AWS raise follow the language."""

import unittest
from types import SimpleNamespace

from cms.locale import DEFAULT_TRANSLATION
from cms.server.admin.handlers.base import BaseHandler, parse_int
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH


def read_int(translation) -> str:
    """Read "abc" as an integer in a handler that uses the translation
    and return the message of the error."""
    handler = SimpleNamespace(get_argument=lambda name, default=None: "abc",
                              _=translation.gettext)
    with unittest.TestCase().assertRaises(ValueError) as caught:
        BaseHandler.get_int(handler, {}, "field")
    return str(caught.exception)


class TestParseErrors(unittest.TestCase):

    def test_invalid_field_in_spanish(self):
        # Scenario "Invalid field in Spanish" (aws-language)
        self.assertEqual(
            read_int(SPANISH), "No se puede convertir abc a entero.")

    def test_invalid_field_in_english(self):
        # Scenario "Invalid field in English" (aws-language): the
        # message is what it was, with the handler's translation or
        # when a parser is called without one.
        self.assertEqual(
            read_int(DEFAULT_TRANSLATION), "Can't cast abc to int.")
        with self.assertRaisesRegex(ValueError, "^Can't cast abc to int.$"):
            parse_int("abc")
