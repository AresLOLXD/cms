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
import os
import re
import threading
from collections.abc import Callable, Iterator
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from cms.db import Contest, Group, Participation, Team, User
from cmscommon.crypto import generate_random_password, hash_password, \
    validate_password

logger = logging.getLogger(__name__)

FIELDS: tuple[str, ...] = ("username", "first_name", "last_name",
                           "password", "team", "group")
# Without a password column, the participations get none of their own and
# log in with the account password.
REQUIRED: frozenset[str] = frozenset({"username", "first_name",
                                      "last_name"})
# The global import of users: accounts with their own password.
USER_FIELDS: tuple[str, ...] = ("username", "first_name", "last_name",
                                "password")
USER_REQUIRED: frozenset[str] = frozenset(USER_FIELDS)
LABELS = {"username": "el usuario", "first_name": "el nombre",
          "last_name": "el apellido", "password": "la contraseña",
          "team": "el equipo", "group": "el grupo"}
EMPTY_MESSAGES = {"username": "el usuario está vacío",
                  "first_name": "el nombre está vacío",
                  "last_name": "el apellido está vacío",
                  "password": "la contraseña está vacía"}
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_PASSWORD_BYTES = 72
HASH_THREADS = 4
# Control characters, which a password cannot have: a login field cannot
# produce a tab or a line break, and Tornado turns most of the others into
# spaces when CWS reads the login form, so such a password could never
# match.
PASSWORD_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
T = TypeVar("T")


@dataclasses.dataclass(frozen=True)
class ImportRow:
    """One data row of the file, already mapped to the import fields.

    line: the number of the row in the CSV, with the header as row 1. It
        is not the physical line: a quoted cell with a line break inside
        is still one row.
    password: None when the file has no password column (a contest import
        that leaves the participation passwords alone).

    """
    line: int
    username: str
    first_name: str
    last_name: str
    password: str | None
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


def read_rows(data: bytes, mapping: dict[str, str],
              fields: tuple[str, ...] = FIELDS,
              required: frozenset[str] = REQUIRED
              ) -> tuple[list[ImportRow], list[str]]:
    """Parse the CSV and map its columns to the import fields.

    data: the raw file.
    mapping: import field -> header of the column that holds it ("" or
        missing for an unmapped optional field).
    fields: the fields of the import; they include username, first_name,
        last_name and password. A field out of them reads as None.
    required: the fields that must be mapped and filled in every row. A
        mapped password must be filled in every row too.

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

    def report(message: str) -> None:
        # A column shared by several fields is reported once.
        if message not in errors:
            errors.append(message)

    # Without a header row, the "header" is the first contestant's row, so
    # the header chosen for the password is a real password: the messages
    # name the field instead of it, whichever field it comes up in.
    password_name = (mapping.get("password") or "").strip()
    for field in fields:
        name = (mapping.get(field) or "").strip()
        if not name:
            if field in required:
                errors.append("falta asignar la columna para %s (%s)"
                              % (LABELS[field], field))
            continue
        if name not in header:
            report(
                "la columna asignada a la contraseña no está en el archivo"
                if name == password_name
                else "la columna %s no está en el archivo" % name)
        # Any column may feed several fields, except the password column:
        # a cell of it read by another field could reach an error message,
        # for example as a repeated user.
        if name == password_name and field != "password":
            report("la columna de la contraseña está asignada a más de un "
                   "campo")
        if name in header:
            columns[field] = header.index(name)
    if errors:
        return [], errors

    # A mapped password must be in every row, even where it is optional:
    # an empty cell would be an empty password.
    must_fill = required | ({"password"} & columns.keys())

    rows: list[ImportRow] = []
    seen: dict[str, int] = {}
    for line, cells in records:
        def cell(field: str) -> str:
            index = columns.get(field)
            if index is None or index >= len(cells):
                return ""
            # The password too: CWS strips what the contestant types
            # (Tornado's get_argument), with this same str.strip().
            return cells[index].strip()

        values = {field: cell(field) for field in fields}
        for field in fields:
            if field in must_fill and not values[field]:
                errors.append("fila %d: %s" % (line, EMPTY_MESSAGES[field]))
            elif field != "password" and "\x00" in values[field]:
                # The database refuses NUL in a text, so the job would
                # fail at the commit. The password has a check of its own
                # below. Spanish contracts "de el" into "del".
                of_label = ("de " + LABELS[field]).replace("de el ", "del ")
                errors.append("fila %d: la celda %s tiene caracteres no "
                              "permitidos" % (line, of_label))
        if len(values["password"].encode("utf-8")) > MAX_PASSWORD_BYTES:
            errors.append("fila %d: la contraseña pasa de %d bytes"
                          % (line, MAX_PASSWORD_BYTES))
        if PASSWORD_CONTROL_CHARACTERS.search(values["password"]):
            errors.append("fila %d: la contraseña tiene caracteres no "
                          "permitidos" % line)
        username = values["username"]
        # A NUL is reported above; the checks below would echo it.
        if username and "\x00" not in username:
            # The Codename domain of the database. Not \w: it would also
            # accept the accented letters.
            if not re.fullmatch(r"[A-Za-z0-9_-]+", username):
                errors.append(
                    "fila %d: el usuario %s tiene caracteres no permitidos "
                    "(solo letras sin acentos, números, _ y -)"
                    % (line, username))
            if username in seen:
                errors.append("fila %d: el usuario %s está repetido "
                              "(fila %d)" % (line, username, seen[username]))
            else:
                seen[username] = line
        rows.append(ImportRow(
            line=line, username=username,
            first_name=values["first_name"],
            last_name=values["last_name"],
            password=values["password"] if "password" in columns else None,
            team=values.get("team") or None,
            group=values.get("group") or None))
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
    removed_teams: how many existing participations have a team that the
        import clears, because their row has no team.
    moved_to_main_group: how many existing participations are in a group
        other than the main one and go to it, because their row has no
        group.
    stored_passwords: username -> participation password stored in the
        contest (an authentication string), for the existing
        participations that have one. It is never shown or logged, so it
        is out of summary() and of the representation of the plan.

    """
    new_users: list[str]
    updated_users: list[str]
    new_participations: list[str]
    updated_participations: list[str]
    teams: dict[str, int]
    groups: dict[str, int]
    main_group_id: int
    removed_teams: int = 0
    moved_to_main_group: int = 0
    stored_passwords: dict[str, str] = dataclasses.field(
        default_factory=dict, repr=False)

    def summary(self) -> dict[str, int]:
        """Count what the import would do.

        return: the number of new and updated users and participations,
            and of the participations that lose their team or go to the
            main group.

        """
        return {"usuarios_nuevos": len(self.new_users),
                "usuarios_actualizados": len(self.updated_users),
                "participaciones_nuevas": len(self.new_participations),
                "participaciones_actualizadas":
                    len(self.updated_participations),
                "equipos_quitados": self.removed_teams,
                "movidas_al_grupo_principal": self.moved_to_main_group}


def plan_import(session: Session, contest_id: int, rows: list[ImportRow]
                ) -> tuple[ImportPlan | None, list[str]]:
    """Check the rows against the database and plan the import.

    session: a read-only use of a session; nothing is added or flushed,
        not even what the caller has left pending in it.
    contest_id: the contest the participations go to.
    rows: the rows from read_rows, without errors.

    return: the plan, or None and the errors (the contest does not exist
        or has no main group, or a team or a group is unknown, or a user
        does not exist and the rows have no password).

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
        usernames = [row.username for row in rows]
        existing = set(session.execute(
            select(User.username).filter(User.username.in_(usernames))
        ).scalars())
        errors: list[str] = []
        for row in rows:
            if row.team is not None and row.team not in teams:
                errors.append("fila %d: el equipo %s no existe"
                              % (row.line, row.team))
            if row.group is not None and row.group not in groups:
                errors.append("fila %d: el grupo %s no existe en este "
                              "concurso" % (row.line, row.group))
            # A new user would get a random account password, and with no
            # participation password nothing could log in.
            if row.password is None and row.username not in existing:
                errors.append(
                    "fila %d: el usuario %s no existe; sin columna de "
                    "contraseña solo se pueden inscribir usuarios que ya "
                    "existen" % (row.line, row.username))
        if errors:
            return None, errors
        current = session.execute(
            select(User.username, Participation.team_id,
                   Participation.group_id, Participation.password)
            .select_from(User).join(Participation)
            .filter(Participation.contest_id == contest_id,
                    User.username.in_(usernames))).all()
    # username -> (team id, group id) of the existing participations.
    participating = {username: (team_id, group_id)
                     for username, team_id, group_id, _ in current}
    stored_passwords = {username: password
                        for username, _, _, password in current
                        if password is not None}
    # An empty cell replaces: it clears the team and puts the participation
    # in the main group, so the page has to say how many that is.
    removed_teams = moved_to_main_group = 0
    for row in rows:
        if row.username not in participating:
            continue
        team_id, group_id = participating[row.username]
        if row.team is None and team_id is not None:
            removed_teams += 1
        if row.group is None and group_id != contest.main_group_id:
            moved_to_main_group += 1
    return ImportPlan(
        new_users=[u for u in usernames if u not in existing],
        updated_users=[u for u in usernames if u in existing],
        new_participations=[u for u in usernames if u not in participating],
        updated_participations=[u for u in usernames if u in participating],
        teams=teams, groups=groups, main_group_id=contest.main_group_id,
        removed_teams=removed_teams,
        moved_to_main_group=moved_to_main_group,
        stored_passwords=stored_passwords), []


def _lower_thread_priority() -> None:
    """Lower the priority of the calling hash thread, if it can be done.

    The hashing keeps HASH_THREADS cores busy for a while, on a host that
    may also run the Workers and CWS. Best effort and Linux only, where a
    thread id is a process id for setpriority: otherwise, or if it is
    refused, the thread keeps its priority.

    """
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except (AttributeError, OSError):
        pass


def _hash_or_keep(password: str, stored: str | None) -> str:
    """Hash a password, unless the stored bcrypt hash still matches it.

    The CWS cookie holds the stored string, and a new hash of the same
    password is another string (bcrypt salts it), so replacing it would
    log the contestant out. Checking costs about as much as hashing.

    password: the password of the row.
    stored: the authentication string stored now, if any.

    return: the authentication string to store.

    """
    if stored is not None and stored.startswith("bcrypt:") \
            and validate_password(stored, password):
        return stored
    return hash_password(password, "bcrypt")


def _hash_in_pool(rows: list[ImportRow],
                  work: Callable[[ImportRow], tuple[str, T]],
                  progress: Callable[[], None]) -> dict[str, T]:
    """Run the hashing work of every row, a few rows at a time.

    bcrypt costs about 0.2 s per password and releases the GIL, so a
    small pool divides the wait.

    rows: the rows to import.
    work: hashes one row; returns its username and what it computed.
    progress: called once each time a row is done.

    return: username -> what work computed for it.

    """
    result: dict[str, T] = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=HASH_THREADS,
            thread_name_prefix="aws-import-hash",
            initializer=_lower_thread_priority) as pool:
        try:
            for future in concurrent.futures.as_completed(
                    [pool.submit(work, row) for row in rows]):
                username, value = future.result()
                result[username] = value
                progress()
        except BaseException:
            # Do not hash the rows still queued for a result nobody will
            # use; only the ones already running are waited for.
            pool.shutdown(wait=True, cancel_futures=True)
            raise
    return result


def hash_passwords(rows: list[ImportRow], new_users: set[str],
                   stored_passwords: dict[str, str],
                   progress: Callable[[], None]
                   ) -> dict[str, tuple[str | None, str | None]]:
    """Hash every password of a contest import, a few at a time.

    A participation keeps its stored bcrypt password when the row's
    password still matches it (see _hash_or_keep).

    rows: the rows to import.
    new_users: the usernames that do not exist yet.
    stored_passwords: username -> participation password stored in the
        contest, from the plan.
    progress: called once each time a row is done.

    return: username -> (participation password, or None for a row
        without password; account password, or None for an existing
        user), both as authentication strings.

    """
    def work(row: ImportRow
             ) -> tuple[str, tuple[str | None, str | None]]:
        participation = None if row.password is None else _hash_or_keep(
            row.password, stored_passwords.get(row.username))
        account = hash_password(generate_random_password(), "bcrypt") \
            if row.username in new_users else None
        return row.username, (participation, account)

    return _hash_in_pool(rows, work, progress)


def apply_import(session: Session, contest_id: int, rows: list[ImportRow],
                 plan: ImportPlan,
                 hashes: dict[str, tuple[str | None, str | None]]
                 ) -> None:
    """Write the import into the session; the caller commits.

    session: the session of the transaction.
    contest_id: the contest of the participations.
    rows: the rows to import.
    plan: from plan_import, made in this same process just before.
    hashes: from hash_passwords; a None participation password leaves the
        participation's alone.

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
        # Without a password column, an existing participation keeps its
        # password and a new one has none, so the account password logs
        # in.
        if participation_hash is not None:
            participation.password = participation_hash
        participation.team_id = team_id
        participation.group_id = group_id
