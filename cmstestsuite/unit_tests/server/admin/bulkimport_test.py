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

import logging
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
        self.assertIn(
            "falta asignar la columna para la contraseña (password)", errors)

    def test_unmapped_required_fields_read_well_in_spanish(self):
        for field, text in (
                ("username", "el usuario"), ("first_name", "el nombre"),
                ("last_name", "el apellido"), ("password", "la contraseña")):
            with self.subTest(field=field):
                _, errors = read_rows(
                    csv_bytes("usuario,nombre,apellido,contraseña,estado\n"),
                    dict(MAPPING, **{field: ""}))
                self.assertEqual(
                    errors,
                    ["falta asignar la columna para %s (%s)" % (text, field)])

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

    def test_field_over_the_csv_limit_is_an_error(self):
        # The csv module refuses fields over 131072 characters; that must
        # come back as an error, not as an exception.
        with self.assertLogs("cms.server.admin.bulkimport",
                             level="DEBUG") as logs:
            rows, errors = read_rows(csv_bytes(
                "usuario,nombre,apellido,contraseña,estado\n"
                "ana,A,L,%s,\n" % ("x" * 200000)), MAPPING)
        self.assertEqual(rows, [])
        # The page gets a fixed message; the reason of the csv module (a
        # developer text in English) only goes to the DEBUG log, and
        # neither of them echoes the content of a cell.
        self.assertEqual(errors, ["el archivo no es un CSV válido"])
        self.assertEqual([r.levelno for r in logs.records],
                         [logging.DEBUG])
        self.assertIn("field limit", logs.output[0])
        self.assertNotIn("x" * 100, logs.output[0])

    def test_row_errors_follow_the_field_order(self):
        # Every required cell but the team is empty. The errors must come
        # in the order of FIELDS, whatever the hash seed of the process.
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            ",,,,JAL\n"), MAPPING)
        self.assertEqual(errors, ["fila 2: el usuario está vacío",
                                  "fila 2: el nombre está vacío",
                                  "fila 2: el apellido está vacío",
                                  "fila 2: la contraseña está vacía"])

    def test_column_mapped_twice_is_an_error(self):
        # With the username and the password in the same column, two rows
        # with the same password would put it in the "repeated user"
        # error. The mapping is rejected before any row is checked.
        mapping = dict(MAPPING, username="contraseña")
        rows, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,SECRETPW,\n"
            "beto,Beto,Pérez,SECRETPW,\n"), mapping)
        self.assertEqual(rows, [])
        self.assertEqual(
            errors, ["la columna contraseña está asignada a más de un campo"])
        self.assertFalse(any("SECRETPW" in e for e in errors))

    def test_column_mapped_to_three_fields_is_reported_once(self):
        mapping = dict(MAPPING, last_name="nombre", team="nombre")
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,pw,\n"), mapping)
        self.assertEqual(
            errors, ["la columna nombre está asignada a más de un campo"])

    def test_whitespace_only_password_is_empty(self):
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,   ,\n"), MAPPING)
        self.assertEqual(errors, ["fila 2: la contraseña está vacía"])

    def test_blank_and_separator_only_rows_are_skipped(self):
        rows, errors = read_rows(csv_bytes(
            "usuario;nombre;apellido;contraseña;estado\n"
            "ana;Ana;López;pw1;JAL\n"
            "\n"
            ";;;;\n"
            "  ;  ;  ;  ;  \n"
            "beto;Beto;Pérez;pw2;\n"
            ";;;;\n"), MAPPING)
        self.assertEqual(errors, [])
        # The rows after the skipped ones keep their number in the file.
        self.assertEqual([(r.line, r.username) for r in rows],
                         [(2, "ana"), (6, "beto")])

    def test_max_rows_is_accepted_and_blank_rows_do_not_count(self):
        body = "usuario,nombre,apellido,contraseña,estado\n" + "".join(
            "u%d,A,L,p,\n\n" % i for i in range(MAX_ROWS))
        rows, errors = read_rows(csv_bytes(body), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), MAX_ROWS)

    def test_crlf_line_endings(self):
        rows, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\r\n"
            "ana,Ana,López,pw,JAL\r\n"
            "beto,Beto,Pérez,pw2,\r\n"), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual([(r.line, r.username, r.password, r.team)
                          for r in rows],
                         [(2, "ana", "pw", "JAL"), (3, "beto", "pw2", None)])

    def test_bom_with_comma_delimiter(self):
        rows, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,pw,JAL\n", bom=True), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual([r.username for r in rows], ["ana"])

    def test_row_shorter_than_the_header(self):
        rows, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,pw\n"
            "beto,Beto,Pérez\n"), MAPPING)
        # A missing optional cell is None; a missing required one is empty.
        self.assertEqual(rows[0].team, None)
        self.assertEqual(errors, ["fila 3: la contraseña está vacía"])

    def test_header_only_file(self):
        header = "usuario,nombre,apellido,contraseña,estado\n"
        for text in (header, header + "\n,,,,\n  ,  ,  ,  ,  \n"):
            rows, errors = read_rows(csv_bytes(text), MAPPING)
            self.assertEqual(rows, [])
            self.assertEqual(errors, ["el archivo no tiene filas de datos"])

    def test_empty_file(self):
        rows, errors = read_rows(b"", MAPPING)
        self.assertEqual(rows, [])
        self.assertEqual(errors, ["el archivo está vacío"])


if __name__ == "__main__":
    unittest.main()
