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

"""Bulk removal of users (from the platform) or of participations (from
a contest), for AWS.

The messages are meant to be shown as they are in the page.

"""

import dataclasses
import re
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from cms.db import Participation, Submission, User, UserTest
from cmscommon.datetime import make_datetime

MAX_LIST_BYTES = 1024 * 1024
# Why a value of the selection is not removed.
NOT_FOUND = "no existe"
NOT_IN_CONTEST = "no participa en el concurso"
# A list may be a CSV exported with any of these separators; the
# username is the first column.
_SEPARATORS = re.compile(r"[,;\t]")


def parse_usernames(*sources: bytes) -> list[str]:
    """Return the usernames listed in sources, in order, without repeats.

    Each source is the text of the textarea or the body of the uploaded
    file: one username per line, or a CSV (comma, semicolon or tab
    separated) whose first column is the username. Blank lines are
    skipped, and so is a first line whose first column is "username", in
    any case, as the header of a CSV.

    sources: the lists, UTF-8 with or without BOM.

    return: the usernames.

    raise (ValueError): if the sources pass MAX_LIST_BYTES in all, or
        one of them is not UTF-8.

    """
    if sum(len(source) for source in sources) > MAX_LIST_BYTES:
        raise ValueError("la lista pasa de 1 MB")
    usernames: list[str] = []
    seen: set[str] = set()
    for source in sources:
        try:
            text = source.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("la lista no está en UTF-8") from None
        first = True
        for line in text.splitlines():
            value = _SEPARATORS.split(line, maxsplit=1)[0]
            value = value.strip().strip('"').strip()
            if not value:
                continue
            if first:
                first = False
                if value.lower() == "username":
                    continue
            if value not in seen:
                seen.add(value)
                usernames.append(value)
    return usernames


@dataclasses.dataclass
class RemovalEntry:
    """A user that a removal takes, with what goes with them."""
    user_id: int
    username: str
    first_name: str
    last_name: str
    submissions: int
    user_tests: int
    contests: list[str]


@dataclasses.dataclass
class RemovalPlan:
    """What a removal takes, and what it leaves out and why."""
    users: list[RemovalEntry]
    ignored: list[tuple[str, str]]
    running: bool

    @property
    def user_ids(self) -> list[int]:
        return sorted(entry.user_id for entry in self.users)

    @property
    def submissions(self) -> int:
        return sum(entry.submissions for entry in self.users)

    @property
    def user_tests(self) -> int:
        return sum(entry.user_tests for entry in self.users)


def _count_by_user(session: Session, model, participation_ids: list[int]
                   ) -> dict[int, int]:
    """Return how many rows of model each user has in the participations.

    model: Submission or UserTest.
    participation_ids: the participations to count in.

    return: user id -> count, for the users with at least one row.

    """
    if not participation_ids:
        return {}
    rows = session.execute(
        select(Participation.user_id, func.count(model.id))
        .join(model, model.participation_id == Participation.id)
        .where(Participation.id.in_(participation_ids))
        .group_by(Participation.user_id))
    return {user_id: count for user_id, count in rows}


def plan_removal(session: Session, *, contest_id: int | None,
                 user_ids: Sequence[int] = (),
                 usernames: Sequence[str] = (),
                 now: datetime | None = None) -> RemovalPlan:
    """Return what removing the chosen users would take.

    session: the session to read with.
    contest_id: the contest to remove the participations from, or None to
        remove the users from the platform.
    user_ids: the users checked in the list.
    usernames: the users named in a list of usernames.
    now: the time to decide whether a contest is running (default: now).

    return: the plan.

    """
    now = make_datetime() if now is None else now
    # What was chosen, in input order: the value and its user, if any.
    chosen: list[tuple[str, User | None]] = []
    if user_ids:
        by_id = {user.id: user for user in session.execute(
            select(User).where(User.id.in_(user_ids))).scalars()}
        chosen.extend((str(user_id), by_id.get(user_id))
                      for user_id in user_ids)
    if usernames:
        by_name = {user.username: user for user in session.execute(
            select(User).where(User.username.in_(usernames))).scalars()}
        chosen.extend((username, by_name.get(username))
                      for username in usernames)
    found = {user.id: user for _, user in chosen if user is not None}

    participations: list[Participation] = []
    if found:
        query = select(Participation) \
            .where(Participation.user_id.in_(list(found))) \
            .options(joinedload(Participation.contest),
                     joinedload(Participation.group))
        if contest_id is not None:
            query = query.where(Participation.contest_id == contest_id)
        participations = list(session.execute(query).scalars())
    participating = {p.user_id for p in participations}

    ignored: list[tuple[str, str]] = []
    ignored_seen: set[tuple[str, str]] = set()
    for value, user in chosen:
        if user is None:
            entry = (value, NOT_FOUND)
        elif contest_id is not None and user.id not in participating:
            entry = (user.username, NOT_IN_CONTEST)
            found.pop(user.id, None)
        else:
            continue
        if entry not in ignored_seen:
            ignored_seen.add(entry)
            ignored.append(entry)

    participation_ids = [p.id for p in participations]
    submissions = _count_by_user(session, Submission, participation_ids)
    user_tests = _count_by_user(session, UserTest, participation_ids)
    contests: dict[int, set[str]] = {}
    for p in participations:
        contests.setdefault(p.user_id, set()).add(p.contest.name)

    users = [
        RemovalEntry(user_id=user.id, username=user.username,
                     first_name=user.first_name, last_name=user.last_name,
                     submissions=submissions.get(user.id, 0),
                     user_tests=user_tests.get(user.id, 0),
                     contests=sorted(contests.get(user.id, set())))
        for user in sorted(found.values(), key=lambda u: u.username)]
    running = any(p.group.start <= now < p.group.stop
                  for p in participations)
    return RemovalPlan(users=users, ignored=ignored, running=running)


def remove_users(session: Session, user_ids: Sequence[int]) -> int:
    """Remove users from the platform, with everything they own.

    The database removes the rest through ON DELETE CASCADE:
    participations, submissions, user tests, questions, messages and what
    hangs from them. The caller commits.

    session: the session to remove in.
    user_ids: the users to remove.

    return: how many users were removed.

    """
    if not user_ids:
        return 0
    result = session.execute(
        delete(User).where(User.id.in_(user_ids))
        .execution_options(synchronize_session=False))
    return result.rowcount


def remove_participations(session: Session, contest_id: int,
                          user_ids: Sequence[int]) -> int:
    """Remove the participations of users in a contest.

    The users stay, with their other participations; the database removes
    what hangs from these participations through ON DELETE CASCADE. The
    caller commits.

    session: the session to remove in.
    contest_id: the contest.
    user_ids: the users whose participation to remove.

    return: how many participations were removed.

    """
    if not user_ids:
        return 0
    result = session.execute(
        delete(Participation)
        .where(Participation.contest_id == contest_id)
        .where(Participation.user_id.in_(user_ids))
        .execution_options(synchronize_session=False))
    return result.rowcount
