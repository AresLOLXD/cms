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

The error messages are meant to be shown as they are in the page, so they
never contain a data cell of the column mapped to the password.

"""

import concurrent.futures
import csv
import dataclasses
import io
import itertools
import logging
from collections.abc import Callable, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from cms.db import Contest, Group, Participation, Team, User
from cmscommon.crypto import generate_random_password, hash_password

logger = logging.getLogger(__name__)

FIELDS: tuple[str, ...] = ("username", "first_name", "last_name",
                           "password", "team", "group")
REQUIRED: frozenset[str] = frozenset({"username", "first_name",
                                      "last_name", "password"})
LABELS = {"username": "el usuario", "first_name": "el nombre",
          "last_name": "el apellido", "password": "la contraseña"}
EMPTY_MESSAGES = {"username": "el usuario está vacío",
                  "first_name": "el nombre está vacío",
                  "last_name": "el apellido está vacío",
                  "password": "la contraseña está vacía"}
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_PASSWORD_BYTES = 72
HASH_THREADS = 4


@dataclasses.dataclass(frozen=True)
class ImportRow:
    """One data row of the file, already mapped to the import fields.

    line: the number of the row in the CSV, with the header as row 1. It
        is not the physical line: a quoted cell with a line break inside
        is still one row.

    """
    line: int
    username: str
    first_name: str
    last_name: str
    password: str
    team: str | None
    group: str | None


def _data_records(reader: Iterator[list[str]]
                  ) -> Iterator[tuple[int, list[str]]]:
    """Yield the non-blank rows of a CSV, one at a time.

    reader: the csv reader, with the header already read.

    return: pairs of the number of the row (the header is row 1) and its
        cells. Rows with nothing but empty cells are skipped, but they
        still count in the numbering.

    """
    for line, cells in enumerate(reader, start=2):
        if any(cell.strip() for cell in cells):
            yield line, cells


def read_rows(data: bytes, mapping: dict[str, str]
              ) -> tuple[list[ImportRow], list[str]]:
    """Parse the CSV and map its columns to the import fields.

    data: the raw file.
    mapping: import field -> header of the column that holds it ("" or
        missing for an unmapped optional field).

    return: the rows and the list of errors; if there is any error the
        rows must not be used. Errors never contain a data cell of the
        column mapped to password.

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
        header_cells = next(reader, None)
        # Only the non-blank rows are kept, and only up to one more than
        # the limit: a file of blank lines costs no memory.
        records = list(itertools.islice(_data_records(reader),
                                        MAX_ROWS + 1))
    except csv.Error as error:
        # The reason is a developer text in English, so it only goes to
        # the log; the page gets a fixed message.
        logger.debug("Bulk import file is not a valid CSV: %s", error)
        return [], ["el archivo no es un CSV válido"]
    if header_cells is None:
        return [], ["el archivo está vacío"]
    if len(records) > MAX_ROWS:
        return [], ["el archivo pasa de %d filas" % MAX_ROWS]
    header = [h.strip() for h in header_cells]

    errors: list[str] = []
    columns: dict[str, int] = {}
    assigned: set[str] = set()
    for field in FIELDS:
        name = (mapping.get(field) or "").strip()
        if not name:
            if field in REQUIRED:
                errors.append("falta asignar la columna para %s (%s)"
                              % (LABELS[field], field))
            continue
        # A column used for two fields would let a cell of the password
        # column reach an error message, for example as a repeated user.
        if name in assigned:
            message = "la columna %s está asignada a más de un campo" % name
            if message not in errors:
                errors.append(message)
            continue
        assigned.add(name)
        if name not in header:
            errors.append("la columna %s no está en el archivo" % name)
            continue
        columns[field] = header.index(name)
    if errors:
        return [], errors

    rows: list[ImportRow] = []
    seen: dict[str, int] = {}
    for line, cells in records:
        def cell(field: str) -> str:
            index = columns.get(field)
            if index is None or index >= len(cells):
                return ""
            value = cells[index]
            return value if field == "password" else value.strip()

        values = {field: cell(field) for field in FIELDS}
        for field in FIELDS:
            # A password of only whitespace is empty; any other password
            # is used as typed.
            if field in REQUIRED and not values[field].strip():
                errors.append("fila %d: %s" % (line, EMPTY_MESSAGES[field]))
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
    if not rows:
        return [], ["el archivo no tiene filas de datos"]
    return rows, errors


@dataclasses.dataclass
class ImportPlan:
    """What an import would do; built without writing anything.

    new_users: usernames of the users that do not exist yet.
    updated_users: usernames of the users that already exist.
    new_participations: usernames without a participation in the contest.
    updated_participations: usernames with a participation in the contest.
    teams: code -> id of every team.
    groups: name -> id of every group of the contest.
    main_group_id: the id of the main group of the contest, where a
        participation goes when its row has no group.

    """
    new_users: list[str]
    updated_users: list[str]
    new_participations: list[str]
    updated_participations: list[str]
    teams: dict[str, int]
    groups: dict[str, int]
    main_group_id: int

    def summary(self) -> dict[str, int]:
        """Count what the import would do.

        return: the number of new and updated users and participations.

        """
        return {"usuarios_nuevos": len(self.new_users),
                "usuarios_actualizados": len(self.updated_users),
                "participaciones_nuevas": len(self.new_participations),
                "participaciones_actualizadas":
                    len(self.updated_participations)}


def plan_import(session: Session, contest_id: int, rows: list[ImportRow]
                ) -> tuple[ImportPlan | None, list[str]]:
    """Check the rows against the database and plan the import.

    session: a read-only use of a session; nothing is added or flushed,
        not even what the caller has left pending in it.
    contest_id: the contest the participations go to.
    rows: the rows from read_rows, without errors.

    return: the plan, or None and the errors (the contest does not exist
        or has no main group, or a team or a group is unknown).

    """
    with session.no_autoflush:
        contest = session.get(Contest, contest_id)
        if contest is None:
            return None, ["el concurso no existe"]
        if contest.main_group_id is None:
            return None, ["el concurso no tiene grupo principal"]
        teams = dict(session.execute(select(Team.code, Team.id)).all())
        groups = dict(session.execute(
            select(Group.name, Group.id)
            .filter(Group.contest_id == contest_id)).all())
        errors: list[str] = []
        for row in rows:
            if row.team is not None and row.team not in teams:
                errors.append("fila %d: el equipo %s no existe"
                              % (row.line, row.team))
            if row.group is not None and row.group not in groups:
                errors.append("fila %d: el grupo %s no existe en este "
                              "concurso" % (row.line, row.group))
        if errors:
            return None, errors
        usernames = [row.username for row in rows]
        existing = set(session.execute(
            select(User.username).filter(User.username.in_(usernames))
        ).scalars())
        participating = set(session.execute(
            select(User.username).join(Participation)
            .filter(Participation.contest_id == contest_id,
                    User.username.in_(usernames))
        ).scalars())
    return ImportPlan(
        new_users=[u for u in usernames if u not in existing],
        updated_users=[u for u in usernames if u in existing],
        new_participations=[u for u in usernames if u not in participating],
        updated_participations=[u for u in usernames if u in participating],
        teams=teams, groups=groups,
        main_group_id=contest.main_group_id), []


def hash_passwords(rows: list[ImportRow], new_users: set[str],
                   progress: Callable[[], None]
                   ) -> dict[str, tuple[str, str | None]]:
    """Hash every password of the import, a few at a time.

    bcrypt costs about 0.2 s per password and releases the GIL, so a
    small pool divides the wait.

    rows: the rows to import.
    new_users: the usernames that do not exist yet.
    progress: called once each time a row is done.

    return: username -> (participation password, account password or
        None), both as authentication strings.

    """
    def work(row: ImportRow) -> tuple[str, tuple[str, str | None]]:
        participation = hash_password(row.password, "bcrypt")
        account = hash_password(generate_random_password(), "bcrypt") \
            if row.username in new_users else None
        return row.username, (participation, account)

    result: dict[str, tuple[str, str | None]] = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=HASH_THREADS,
            thread_name_prefix="aws-import-hash") as pool:
        for future in concurrent.futures.as_completed(
                [pool.submit(work, row) for row in rows]):
            username, hashes = future.result()
            result[username] = hashes
            progress()
    return result


def apply_import(session: Session, contest_id: int, rows: list[ImportRow],
                 plan: ImportPlan,
                 hashes: dict[str, tuple[str, str | None]]) -> None:
    """Write the import into the session; the caller commits.

    session: the session of the transaction.
    contest_id: the contest of the participations.
    rows: the rows to import.
    plan: from plan_import, made in this same process just before.
    hashes: from hash_passwords.

    """
    usernames = [row.username for row in rows]
    users = {u.username: u for u in session.execute(
        select(User).filter(User.username.in_(usernames))).scalars()}
    participations = {p.user.username: p for p in session.execute(
        select(Participation).join(User)
        .filter(Participation.contest_id == contest_id,
                User.username.in_(usernames))).scalars()}
    contest = session.get(Contest, contest_id)
    for row in rows:
        participation_hash, account_hash = hashes[row.username]
        user = users.get(row.username)
        if user is None:
            user = User(username=row.username, first_name=row.first_name,
                        last_name=row.last_name, password=account_hash)
            session.add(user)
        else:
            user.first_name = row.first_name
            user.last_name = row.last_name
        team_id = plan.teams[row.team] if row.team is not None else None
        group_id = plan.groups[row.group] if row.group is not None \
            else plan.main_group_id
        participation = participations.get(row.username)
        if participation is None:
            participation = Participation(contest=contest, user=user)
            session.add(participation)
        participation.password = participation_hash
        participation.team_id = team_id
        participation.group_id = group_id
