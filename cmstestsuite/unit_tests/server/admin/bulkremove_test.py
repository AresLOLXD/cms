#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <www.luisrodolfo@gmail.com>
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

"""Tests for the bulk removal of users and participations of AWS."""

import unittest

from cms.server.admin.bulkremove import MAX_LIST_BYTES, parse_usernames


class TestParseUsernames(unittest.TestCase):

    def test_one_username_per_line(self):
        self.assertEqual(parse_usernames(b"ana\nbeto\n"), ["ana", "beto"])

    def test_bom_crlf_blank_lines_and_spaces(self):
        data = "﻿ ana \r\n\r\n  \r\nbeto\r\n".encode("utf-8")
        self.assertEqual(parse_usernames(data), ["ana", "beto"])

    def test_first_column_of_a_csv(self):
        data = "ana,Ana,Pérez\nbeto,Beto,Ruiz\n".encode("utf-8")
        self.assertEqual(parse_usernames(data), ["ana", "beto"])

    def test_semicolon_and_tab_separators(self):
        data = "ana;Ana;Pérez\nbeto\tBeto\tRuiz\n".encode("utf-8")
        self.assertEqual(parse_usernames(data), ["ana", "beto"])

    def test_quoted_first_column(self):
        self.assertEqual(parse_usernames(b'"ana","Ana"\n'), ["ana"])

    def test_header_is_skipped_in_any_case(self):
        self.assertEqual(parse_usernames(b"Username,nombre\nana,Ana\n"),
                         ["ana"])
        self.assertEqual(parse_usernames(b"\n\nUSERNAME\nana\n"), ["ana"])

    def test_username_after_the_first_line_is_not_a_header(self):
        self.assertEqual(parse_usernames(b"ana\nusername\n"),
                         ["ana", "username"])

    def test_duplicates_keep_the_first_order(self):
        self.assertEqual(parse_usernames(b"beto\nana\nbeto\n"),
                         ["beto", "ana"])

    def test_sources_are_joined_each_with_its_own_header(self):
        self.assertEqual(
            parse_usernames(b"ana\nbeto\n", b"username\nbeto\ncarla\n"),
            ["ana", "beto", "carla"])

    def test_non_ascii_usernames(self):
        self.assertEqual(parse_usernames("josé\n".encode("utf-8")),
                         ["josé"])

    def test_nothing(self):
        self.assertEqual(parse_usernames(), [])
        self.assertEqual(parse_usernames(b"", b"\n\n"), [])

    def test_too_large(self):
        half = b"a" * (MAX_LIST_BYTES // 2 + 1)
        with self.assertRaises(ValueError) as caught:
            parse_usernames(half, half)
        self.assertEqual(str(caught.exception), "la lista pasa de 1 MB")

    def test_exactly_the_limit_is_accepted(self):
        self.assertEqual(parse_usernames(b"a" * MAX_LIST_BYTES),
                         ["a" * MAX_LIST_BYTES])

    def test_not_utf8(self):
        with self.assertRaises(ValueError) as caught:
            parse_usernames("josé\n".encode("latin-1"))
        self.assertEqual(str(caught.exception),
                         "la lista no está en UTF-8")
