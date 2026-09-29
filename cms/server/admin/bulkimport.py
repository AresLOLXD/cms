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

"""Bulk import of users and participations from a CSV (AWS).

One CSV per contest: each row creates the user if missing and registers
its participation in the contest, with the day's password.

"""

import csv
import dataclasses
import io

FIELDS: tuple[str, ...] = ("username", "first_name", "last_name",
                           "password", "team", "group")
REQUIRED: frozenset[str] = frozenset({"username", "first_name",
                                      "last_name", "password"})
LABELS = {"username": "el usuario", "first_name": "el nombre",
          "last_name": "el apellido", "password": "la contraseña",
          "team": "el equipo", "group": "el grupo"}
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_PASSWORD_BYTES = 72


@dataclasses.dataclass(frozen=True)
class ImportRow:
    """One data row of the file, already mapped to the import fields."""
    line: int
    username: str
    first_name: str
    last_name: str
    password: str
    team: str | None
    group: str | None


def read_rows(data: bytes, mapping: dict[str, str]
              ) -> tuple[list[ImportRow], list[str]]:
    """Parse the CSV and map its columns to the import fields.

    data: the raw file.
    mapping: import field -> header of the column that holds it ("" or
        missing for an unmapped optional field).

    return: the rows and the list of errors; if there is any error the
        rows must not be used. Errors never contain a password.

    """
    if len(data) > MAX_BYTES:
        return [], ["el archivo pasa de 2 MB"]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], ["el archivo no está en UTF-8"]
    first_line = text.split("\n", 1)[0]
    delimiter = ";" if first_line.count(";") > first_line.count(",") \
        else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        return [], ["el archivo está vacío"]

    errors: list[str] = []
    columns: dict[str, int] = {}
    for field in FIELDS:
        name = (mapping.get(field) or "").strip()
        if not name:
            if field in REQUIRED:
                errors.append("falta asignar la columna de %s (%s)"
                              % (LABELS[field], field))
            continue
        if name not in header:
            errors.append("la columna %s no está en el archivo" % name)
            continue
        columns[field] = header.index(name)
    if errors:
        return [], errors

    rows: list[ImportRow] = []
    seen: dict[str, int] = {}
    for line, cells in enumerate(reader, start=2):
        if not any(cell.strip() for cell in cells):
            continue
        if len(rows) >= MAX_ROWS:
            return [], ["el archivo pasa de %d filas" % MAX_ROWS]

        def cell(field: str) -> str:
            index = columns.get(field)
            if index is None or index >= len(cells):
                return ""
            value = cells[index]
            return value if field == "password" else value.strip()

        values = {field: cell(field) for field in FIELDS}
        for field in REQUIRED:
            if values[field] == "":
                errors.append("fila %d: %s está vacío" % (
                    line, LABELS[field]) if field != "password" else
                    "fila %d: la contraseña está vacía" % line)
        if len(values["password"].encode("utf-8")) > MAX_PASSWORD_BYTES:
            errors.append("fila %d: la contraseña pasa de %d bytes"
                          % (line, MAX_PASSWORD_BYTES))
        username = values["username"]
        if username:
            if username in seen:
                errors.append("fila %d: el usuario %s está repetido "
                              "(fila %d)" % (line, username, seen[username]))
            else:
                seen[username] = line
        rows.append(ImportRow(
            line=line, username=username,
            first_name=values["first_name"],
            last_name=values["last_name"], password=values["password"],
            team=values["team"] or None, group=values["group"] or None))
    return rows, errors
