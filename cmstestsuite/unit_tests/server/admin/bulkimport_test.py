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

"""Tests for reading the bulk import CSV of AWS."""

import unittest

from cms.server.admin.bulkimport import MAX_ROWS, read_rows

MAPPING = {"username": "usuario", "first_name": "nombre",
           "last_name": "apellido", "password": "contraseña",
           "team": "estado", "group": ""}


def csv_bytes(text: str, bom: bool = False) -> bytes:
    data = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


class TestReadRows(unittest.TestCase):

    def test_semicolon_and_bom(self):
        rows, errors = read_rows(csv_bytes(
            "usuario;nombre;apellido;contraseña;estado\n"
            "ana;Ana;López; s3cr3t ;JAL\n", bom=True), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row.line, row.username, row.first_name,
                          row.last_name, row.team, row.group),
                         (2, "ana", "Ana", "López", "JAL", None))
        # The password is kept as typed.
        self.assertEqual(row.password, " s3cr3t ")

    def test_comma_and_quotes(self):
        rows, errors = read_rows(csv_bytes(
            'usuario,nombre,apellido,contraseña,estado\n'
            'beto,"Roberto, Jr.",Pérez,pw,\n'), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0].first_name, "Roberto, Jr.")
        self.assertIsNone(rows[0].team)

    def test_unmapped_required_field(self):
        mapping = dict(MAPPING, password="")
        _, errors = read_rows(csv_bytes("usuario,nombre,apellido\n"),
                              mapping)
        self.assertTrue(any("password" in e for e in errors))

    def test_mapping_to_a_missing_header(self):
        mapping = dict(MAPPING, team="equipo")
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña\nana,A,L,p\n"), mapping)
        self.assertTrue(any("equipo" in e for e in errors))

    def test_empty_required_cell_and_duplicates(self):
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,,\n"
            "ana,Ana,López,pw,\n"), MAPPING)
        self.assertIn("fila 2: la contraseña está vacía", errors)
        self.assertIn("fila 3: el usuario ana está repetido (fila 2)",
                      errors)

    def test_password_too_long_does_not_echo_it(self):
        secret = "ñ" * 37
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,A,L,%s,\n" % secret), MAPPING)
        self.assertEqual(len(errors), 1)
        self.assertNotIn(secret, errors[0])
        self.assertIn("fila 2", errors[0])

    def test_limits(self):
        _, errors = read_rows(b"x" * (2 * 1024 * 1024 + 1), MAPPING)
        self.assertTrue(errors)
        body = "usuario,nombre,apellido,contraseña,estado\n" + "".join(
            "u%d,A,L,p,\n" % i for i in range(MAX_ROWS + 1))
        _, errors = read_rows(csv_bytes(body), MAPPING)
        self.assertTrue(any(str(MAX_ROWS) in e for e in errors))

    def test_not_utf8(self):
        _, errors = read_rows("usuario\nñ\n".encode("latin-1"), MAPPING)
        self.assertTrue(any("UTF-8" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
