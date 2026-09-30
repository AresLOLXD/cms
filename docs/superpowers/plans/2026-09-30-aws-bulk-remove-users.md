# Bulk Removal of Users and Participations in AWS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an AWS admin remove several users from the platform, or
several participations from one contest, at once, chosen with checkboxes
or with a list of usernames, after a confirmation page.

**Architecture:** A pure module, `cms/server/admin/bulkremove.py`, reads
the username list, plans the removal (what goes, what is ignored and
why, whether a contest is running) and removes with one bulk `DELETE`,
relying on the database's `ON DELETE CASCADE`. A new handlers file,
`cms/server/admin/handlers/bulkremove.py`, runs the two-step flow:
preview, then confirm by typing the count. One new template renders the
confirmation, and the two existing list templates gain checkboxes and a
"por lista" form.

**Tech Stack:** Python 3.12, Tornado (AWS), SQLAlchemy 2.0 (`select`,
`delete`), Jinja2 (StrictUndefined), PostgreSQL, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-09-30-aws-bulk-remove-users-design.md`

## Global Constraints

- Work only in the worktree `/var/home/areslolxd/Documentos/cms/.worktrees/borrado-masivo` (branch `borrado-masivo`).
- Use only this worktree's venv: `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/pyflakes`.
- Every test command runs after `export CMS_CONFIG=/home/areslolxd/.claude/jobs/940cf102/tmp/bulkremove/cms.toml` (database `bulkremovefortesting`, already created). Never print credentials from that file.
- Never run Docker, and never stop or remove any container.
- Temporary files go in `/home/areslolxd/.claude/jobs/940cf102/tmp/bulkremove/`.
- Code, comments, identifiers and commit messages in English. UI text in Spanish, exactly as given in this plan.
- Commits: Conventional Commits; each ends with a trailer naming the committing model, e.g. `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never amend, rebase, reset or `git stash`. Do not push.
- PEP 8, PEP 484 annotations, pyflakes clean, the project docstring format (imperative first line; `name: description` argument lines; `return:`; `raise (Error):`), as in `cms/server/admin/bulkimport.py`.
- New files start with the AGPL header used by `cms/server/admin/bulkimport.py` (Copyright © 2026 Ares Ulises Juárez Martínez <www.luisrodolfo@gmail.com>).
- The single-user routes `/users/<id>/remove` and `/contest/<id>/user/<id>/remove`, and `UserListHandler.post` / `ContestUsersHandler.post`, stay unchanged.
- Both new handlers require `BaseHandler.PERMISSION_ALL` and run their body in the executor (`loop.run_in_executor(None, ...)`), like every AWS handler.
- The removal is one transaction; after a successful commit, exactly one `self.schedule_rpc(self.service.proxy_service.reinitialize)`.
- Size limit of a username list: `MAX_LIST_BYTES = 1024 * 1024` (1 MB), textarea and file together.

## Review Focus

1. A list exported from a Spanish-locale spreadsheet uses `;` (or a tab) as the separator: the first column must still be read as the username, not `ana;Ana;Pérez` (test in Task 1).
2. The same user checked in the table and also written in the list must appear once in the plan and be counted once (test in Task 2).
3. Pressing "Borrar seleccionados" with nothing checked and no list must show a notification and redirect back, never a 500 (test in Task 3).
4. A tampered `user_id` that is not an integer must produce a notification and a redirect, never a 500, and remove nothing (test in Task 3).
5. Usernames are matched exactly (case-sensitive, as CMS usernames are): `Ana` does not match `ana`, and is reported as "no existe" (test in Task 2).

---

### Task 1: Read a list of usernames

**Files:**
- Create: `cms/server/admin/bulkremove.py`
- Test: `cmstestsuite/unit_tests/server/admin/bulkremove_test.py`

**Interfaces:**
- Consumes: nothing.
- Produces (in `cms.server.admin.bulkremove`):
  - `MAX_LIST_BYTES: int = 1024 * 1024`
  - `NOT_FOUND: str = "no existe"`
  - `NOT_IN_CONTEST: str = "no participa en el concurso"`
  - `parse_usernames(*sources: bytes) -> list[str]`, raising `ValueError("la lista pasa de 1 MB")` or `ValueError("la lista no está en UTF-8")`.

Note: the spec names `parse_usernames(data: bytes)`. It takes several
sources instead, because the textarea and the file are parsed
separately (each may have its own header line) and then joined; with one
source it behaves as the spec says.

- [ ] **Step 1: Write the failing tests**

Create `cmstestsuite/unit_tests/server/admin/bulkremove_test.py` (AGPL header, then):

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkremove_test.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'cms.server.admin.bulkremove'`.

- [ ] **Step 3: Write the module**

Create `cms/server/admin/bulkremove.py` (AGPL header, then):

```python
"""Bulk removal of users (from the platform) or of participations (from
a contest), for AWS.

The messages are meant to be shown as they are in the page.

"""

import re

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkremove_test.py -v`
Expected: 14 passed.

Run: `.venv/bin/pyflakes cms/server/admin/bulkremove.py cmstestsuite/unit_tests/server/admin/bulkremove_test.py`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add cms/server/admin/bulkremove.py cmstestsuite/unit_tests/server/admin/bulkremove_test.py
git commit -m "feat(admin): read a list of usernames for bulk removal"
```
(with the model trailer from the Global Constraints)

---

### Task 2: Plan and run the removal

**Files:**
- Modify: `cms/server/admin/bulkremove.py`
- Create: `cmstestsuite/unit_tests/server/admin/bulkremove_db_test.py`

**Interfaces:**
- Consumes: `NOT_FOUND`, `NOT_IN_CONTEST` from Task 1.
- Produces (in `cms.server.admin.bulkremove`):
  - `RemovalEntry` dataclass: `user_id: int`, `username: str`, `first_name: str`, `last_name: str`, `submissions: int`, `user_tests: int`, `contests: list[str]` (sorted contest names).
  - `RemovalPlan` dataclass: `users: list[RemovalEntry]` (sorted by username), `ignored: list[tuple[str, str]]` (value, reason; in input order, ids first then usernames), `running: bool`; properties `user_ids -> list[int]` (sorted), `submissions -> int`, `user_tests -> int` (totals).
  - `plan_removal(session: Session, *, contest_id: int | None, user_ids: Sequence[int] = (), usernames: Sequence[str] = (), now: datetime | None = None) -> RemovalPlan`
  - `remove_users(session: Session, user_ids: Sequence[int]) -> int`
  - `remove_participations(session: Session, contest_id: int, user_ids: Sequence[int]) -> int`

Semantics (from the spec):
- `contest_id is None` is the platform scope; otherwise the contest scope.
- An id or username that matches no user goes to `ignored` with `NOT_FOUND` (an id as `str(id)`). In the contest scope, a user with no participation in that contest goes to `ignored` with `NOT_IN_CONTEST` (value: the username). Each value is ignored at most once.
- A user selected by id and by username is planned once.
- Counts: in the contest scope, the submissions and user tests of that contest's participation; in the platform scope, over all the user's participations.
- `contests`: in the contest scope, that contest's name; in the platform scope, the names of every contest the user participates in.
- `running` is true when any counted participation's group has `group.start <= now < group.stop`; `now` defaults to `make_datetime()` (naive UTC, as the columns).
- The removals issue one bulk `DELETE` with `synchronize_session=False`, return the row count, and do not commit. Empty `user_ids` returns 0 without a query.

Note for the tests: `DatabaseMixin.add_contest()` makes a main group from 2000-01-01 to 2100-01-01, which is "running" at any real `now`. Tests that need a contest that is not running set the group's `start`/`stop` in the past and pass `now`.

- [ ] **Step 1: Write the failing tests**

Create `cmstestsuite/unit_tests/server/admin/bulkremove_db_test.py` (AGPL header, then):

```python
"""Tests for the bulk removal of AWS run against the database."""

import unittest
from datetime import datetime

from sqlalchemy import func, select

from cms.db import Evaluation, Executable, File, Message, Participation, \
    Question, Submission, SubmissionResult, User, UserTest, UserTestFile, \
    UserTestResult
from cms.server.admin.bulkremove import NOT_FOUND, NOT_IN_CONTEST, \
    plan_removal, remove_participations, remove_users
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

NOW = datetime(2026, 10, 10, 12, 0)


class BulkRemoveDatabaseTestCase(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.addCleanup(self.delete_data)
        self.contest = self.add_contest(name="dia1")
        self.other_contest = self.add_contest(name="dia2")
        for contest in (self.contest, self.other_contest):
            # Not running at NOW unless a test says otherwise.
            contest.main_group.start = datetime(2026, 1, 1)
            contest.main_group.stop = datetime(2026, 1, 2)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(task=self.task)
        self.task.active_dataset = self.dataset
        self.testcase = self.add_testcase(self.dataset)

    def add_participant(self, username, contest=None):
        user = self.add_user(username=username)
        participation = self.add_participation(
            user=user, contest=contest or self.contest)
        return user, participation

    def add_full_history(self, participation):
        """Give participation one row in every table that hangs from it."""
        submission = self.add_submission(self.task, participation)
        result = self.add_submission_result(submission, self.dataset)
        self.add_evaluation(result, self.testcase)
        self.add_executable(result)
        self.add_file(submission)
        user_test = self.add_user_test(self.task, participation)
        self.add_user_test_file(user_test)
        self.add_user_test_result(user_test, self.dataset)
        self.add_question(participation=participation)
        self.add_message(participation=participation)

    def count(self, model, *where):
        return self.session.execute(
            select(func.count()).select_from(model).where(*where)
        ).scalar_one()


class TestPlanRemoval(BulkRemoveDatabaseTestCase):

    def test_platform_scope_by_ids_and_usernames(self):
        ana, ana_p = self.add_participant("ana")
        beto, _ = self.add_participant("beto", self.other_contest)
        self.add_participation(user=ana, contest=self.other_contest)
        self.add_full_history(ana_p)
        self.session.commit()

        plan = plan_removal(self.session, contest_id=None,
                            user_ids=[ana.id, 999999],
                            usernames=["beto", "nadie"], now=NOW)

        self.assertEqual([entry.username for entry in plan.users],
                         ["ana", "beto"])
        self.assertEqual(plan.user_ids, sorted([ana.id, beto.id]))
        self.assertEqual(plan.ignored,
                         [("999999", NOT_FOUND), ("nadie", NOT_FOUND)])
        ana_entry = plan.users[0]
        self.assertEqual((ana_entry.submissions, ana_entry.user_tests),
                         (1, 1))
        self.assertEqual(ana_entry.contests, ["dia1", "dia2"])
        self.assertEqual(plan.users[1].contests, ["dia2"])
        self.assertEqual((plan.submissions, plan.user_tests), (1, 1))
        self.assertFalse(plan.running)

    def test_a_user_chosen_twice_is_planned_once(self):
        ana, _ = self.add_participant("ana")
        self.session.commit()

        plan = plan_removal(self.session, contest_id=None,
                            user_ids=[ana.id, ana.id],
                            usernames=["ana", "ana"], now=NOW)

        self.assertEqual(plan.user_ids, [ana.id])
        self.assertEqual(plan.ignored, [])

    def test_usernames_match_exactly(self):
        self.add_participant("ana")
        self.session.commit()

        plan = plan_removal(self.session, contest_id=None,
                            usernames=["Ana"], now=NOW)

        self.assertEqual(plan.users, [])
        self.assertEqual(plan.ignored, [("Ana", NOT_FOUND)])

    def test_contest_scope_ignores_who_does_not_participate(self):
        _, ana_p = self.add_participant("ana")
        self.add_participant("beto", self.other_contest)
        self.add_full_history(ana_p)
        self.session.commit()

        plan = plan_removal(self.session, contest_id=self.contest.id,
                            usernames=["ana", "beto", "nadie"], now=NOW)

        self.assertEqual([entry.username for entry in plan.users], ["ana"])
        self.assertEqual(plan.users[0].contests, ["dia1"])
        self.assertEqual(plan.ignored, [("beto", NOT_IN_CONTEST),
                                        ("nadie", NOT_FOUND)])

    def test_contest_scope_counts_that_contest_only(self):
        ana, ana_p = self.add_participant("ana")
        other_task = self.add_task(contest=self.other_contest)
        other_p = self.add_participation(user=ana, contest=self.other_contest)
        self.add_full_history(ana_p)
        self.add_submission(other_task, other_p)
        self.session.commit()

        in_contest = plan_removal(self.session, contest_id=self.contest.id,
                                  user_ids=[ana.id], now=NOW)
        everywhere = plan_removal(self.session, contest_id=None,
                                  user_ids=[ana.id], now=NOW)

        self.assertEqual(in_contest.submissions, 1)
        self.assertEqual(everywhere.submissions, 2)

    def test_running_follows_the_groups_of_the_counted_participations(self):
        ana, _ = self.add_participant("ana")
        self.add_participation(user=ana, contest=self.other_contest)
        self.other_contest.main_group.start = datetime(2026, 10, 10, 9, 0)
        self.other_contest.main_group.stop = datetime(2026, 10, 10, 14, 0)
        self.session.commit()

        platform = plan_removal(self.session, contest_id=None,
                                user_ids=[ana.id], now=NOW)
        this_contest = plan_removal(self.session,
                                    contest_id=self.contest.id,
                                    user_ids=[ana.id], now=NOW)
        at_the_stop = plan_removal(
            self.session, contest_id=None, user_ids=[ana.id],
            now=datetime(2026, 10, 10, 14, 0))

        self.assertTrue(platform.running)
        self.assertFalse(this_contest.running)
        self.assertFalse(at_the_stop.running)

    def test_nothing_chosen(self):
        plan = plan_removal(self.session, contest_id=None, now=NOW)

        self.assertEqual((plan.users, plan.ignored, plan.running),
                         ([], [], False))


class TestRemove(BulkRemoveDatabaseTestCase):

    def test_remove_users_cascades_to_everything_they_own(self):
        ana, ana_p = self.add_participant("ana")
        ana_other_p = self.add_participation(user=ana,
                                             contest=self.other_contest)
        beto, beto_p = self.add_participant("beto")
        self.add_full_history(ana_p)
        self.add_full_history(beto_p)
        self.session.commit()
        ana_id, participation_ids = ana.id, [ana_p.id, ana_other_p.id]

        removed = remove_users(self.session, [ana_id])
        self.session.commit()

        self.assertEqual(removed, 1)
        self.session.expire_all()
        self.assertEqual(self.count(User, User.id == ana_id), 0)
        self.assertEqual(self.count(
            Participation, Participation.id.in_(participation_ids)), 0)
        for model in (Submission, UserTest, Question, Message):
            self.assertEqual(self.count(
                model, model.participation_id.in_(participation_ids)), 0,
                model.__name__)
        # One of each is left: beto's.
        for model in (Submission, SubmissionResult, Evaluation, Executable,
                      File, UserTest, UserTestFile, UserTestResult,
                      Question, Message):
            self.assertEqual(self.count(model), 1, model.__name__)
        self.assertEqual(self.count(User, User.id == beto.id), 1)

    def test_remove_participations_keeps_the_user(self):
        ana, ana_p = self.add_participant("ana")
        ana_other_p = self.add_participation(user=ana,
                                             contest=self.other_contest)
        self.add_full_history(ana_p)
        self.session.commit()
        ana_id, ana_p_id, other_id = ana.id, ana_p.id, ana_other_p.id

        removed = remove_participations(self.session, self.contest.id,
                                        [ana_id])
        self.session.commit()

        self.assertEqual(removed, 1)
        self.session.expire_all()
        self.assertEqual(self.count(User, User.id == ana_id), 1)
        self.assertEqual(self.count(Participation,
                                    Participation.id == ana_p_id), 0)
        self.assertEqual(self.count(Participation,
                                    Participation.id == other_id), 1)
        self.assertEqual(self.count(Submission), 0)
        self.assertEqual(self.count(Question), 0)

    def test_nothing_to_remove(self):
        self.assertEqual(remove_users(self.session, []), 0)
        self.assertEqual(
            remove_participations(self.session, self.contest.id, []), 0)

    def test_remove_does_not_commit(self):
        ana, _ = self.add_participant("ana")
        self.session.commit()
        ana_id = ana.id

        remove_users(self.session, [ana_id])
        self.session.rollback()

        self.assertEqual(self.count(User, User.id == ana_id), 1)
```

If a `DatabaseMixin` helper's signature differs from these calls (for
instance `add_user_test_file`), adapt the call, not the assertion, and
say so in the report.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `export CMS_CONFIG=/home/areslolxd/.claude/jobs/940cf102/tmp/bulkremove/cms.toml && .venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkremove_db_test.py -v`
Expected: `ImportError: cannot import name 'plan_removal'`.

- [ ] **Step 3: Implement**

Add to `cms/server/admin/bulkremove.py` (merge the imports with Task 1's, sorted as in `bulkimport.py`):

```python
import dataclasses
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from cms.db import Participation, Submission, User, UserTest
from cmscommon.datetime import make_datetime


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
    ignored: list[tuple[str, str]] = []
    found: dict[int, User] = {}

    def ignore(value: str, reason: str) -> None:
        if (value, reason) not in ignored:
            ignored.append((value, reason))

    if user_ids:
        by_id = {user.id: user for user in session.execute(
            select(User).where(User.id.in_(user_ids))).scalars()}
        for user_id in user_ids:
            if user_id in by_id:
                found.setdefault(user_id, by_id[user_id])
            else:
                ignore(str(user_id), NOT_FOUND)
    if usernames:
        by_name = {user.username: user for user in session.execute(
            select(User).where(User.username.in_(usernames))).scalars()}
        for username in usernames:
            user = by_name.get(username)
            if user is None:
                ignore(username, NOT_FOUND)
            else:
                found.setdefault(user.id, user)

    participations: list[Participation] = []
    if found:
        query = select(Participation) \
            .where(Participation.user_id.in_(list(found))) \
            .options(joinedload(Participation.contest),
                     joinedload(Participation.group))
        if contest_id is not None:
            query = query.where(Participation.contest_id == contest_id)
        participations = list(session.execute(query).scalars())

    if contest_id is not None:
        participating = {p.user_id for p in participations}
        for user_id, user in list(found.items()):
            if user_id not in participating:
                ignore(user.username, NOT_IN_CONTEST)
                del found[user_id]

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `export CMS_CONFIG=/home/areslolxd/.claude/jobs/940cf102/tmp/bulkremove/cms.toml && .venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkremove_db_test.py cmstestsuite/unit_tests/server/admin/bulkremove_test.py -v`
Expected: all pass (11 database tests, 14 parsing tests).

Run: `.venv/bin/pyflakes cms/server/admin/bulkremove.py cmstestsuite/unit_tests/server/admin/bulkremove_db_test.py`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add cms/server/admin/bulkremove.py cmstestsuite/unit_tests/server/admin/bulkremove_db_test.py
git commit -m "feat(admin): plan and run bulk removal of users and participations"
```
(with the model trailer)

---

### Task 3: Handlers, routes and the confirmation page

**Files:**
- Create: `cms/server/admin/handlers/bulkremove.py`
- Create: `cms/server/admin/templates/users_bulk_remove.html`
- Modify: `cms/server/admin/handlers/__init__.py` (import block near line 53; routes near lines 144 and 217)
- Test: `cmstestsuite/unit_tests/server/admin/bulkremove_handlers_test.py`

**Interfaces:**
- Consumes (Tasks 1-2): `parse_usernames(*sources: bytes) -> list[str]`, `plan_removal(session, *, contest_id, user_ids=(), usernames=(), now=None) -> RemovalPlan`, `remove_users(session, user_ids) -> int`, `remove_participations(session, contest_id, user_ids) -> int`, `RemovalPlan` (`users`, `ignored`, `running`, `user_ids`, `submissions`, `user_tests`), `RemovalEntry` fields.
- Produces:
  - `BulkRemoveUsersHandler` at `r"/users/remove"` (platform scope).
  - `BulkRemoveParticipationsHandler` at `r"/contest/([0-9]+)/users/remove"` (contest scope).
  - Form contract (what Task 4's list forms post): `action` = `preview` (default) or `confirm`; `user_id` (repeated, integers); `usernames` (textarea text); `file` (multipart upload); `expected_count` (confirm only).
  - Template `users_bulk_remove.html` with parameters `contest` (Contest or None), `plan` (RemovalPlan), `error` (str or None), `action_url` (str), `back_url` (str), plus `render_params()`.

UI strings (use verbatim):
- `NOTHING_CHOSEN = "No elegiste ningún usuario."`
- `WRONG_COUNT = "El número escrito no coincide con el número de usuarios. No se borró nada."`
- `PLAN_CHANGED = "La selección cambió desde la vista previa: revisa la lista y confirma otra vez. No se borró nada."`
- List read error: notification subject `"No se leyó la lista"`, text = the `ValueError` message.
- Bad `user_id`: notification subject `"Invalid field(s)"`, text `"user_id"`.
- Removal error: subject `"No se borró nada"`, text `repr(error)`.
- Success: subject `"Se borraron %d usuarios"` (platform) or `"Se quitaron %d participaciones"` (contest), text `""`.

- [ ] **Step 1: Write the failing tests**

Create `cmstestsuite/unit_tests/server/admin/bulkremove_handlers_test.py` (AGPL header, then). The handlers are built with `__new__` and mocked helpers, as in `contest_users_import_test.py`, so no database and no web server are needed:

```python
"""Tests for the AWS bulk removal pages: flow, permissions and template.

The handlers are built with __new__ and their helpers mocked, so these
tests need no database and no web server. The template is rendered from
its "core" block only, as the rest of base.html needs a whole request.

"""

import unittest
from types import SimpleNamespace
from unittest import mock

import tornado.web

from cms.server.admin.bulkremove import NOT_FOUND, RemovalEntry, \
    RemovalPlan
from cms.server.admin.handlers import HANDLERS
from cms.server.admin.handlers.bulkremove import NOTHING_CHOSEN, \
    PLAN_CHANGED, WRONG_COUNT, BulkRemoveParticipationsHandler, \
    BulkRemoveUsersHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT

MODULE = "cms.server.admin.handlers.bulkremove"
CONTEST_ID = 4


def entry(user_id, username, submissions=0, contests=("dia1",)):
    return RemovalEntry(user_id=user_id, username=username,
                        first_name="N" + username, last_name="A" + username,
                        submissions=submissions, user_tests=0,
                        contests=list(contests))


def make_plan(*entries, ignored=(), running=False):
    return RemovalPlan(users=list(entries), ignored=list(ignored),
                       running=running)


def fake_url(*parts):
    return "/" + "/".join(str(part) for part in parts)


def make_handler(handler_class=BulkRemoveUsersHandler, *, form=None,
                 user_ids=(), file=None, permission_all=True):
    """Build a handler without a request or a database.

    form: the single-valued arguments.
    user_ids: the values of the repeated user_id argument.
    file: the body of the uploaded file, or None for none.

    """
    form = dict(form or {})
    handler = handler_class.__new__(handler_class)
    handler.application = mock.MagicMock()
    handler.request = SimpleNamespace(
        files={} if file is None else {"file": [{"body": file}]})
    handler._current_user = SimpleNamespace(id=1,
                                            permission_all=permission_all)
    handler.contest = None
    handler.sql_session = mock.MagicMock()
    handler.safe_get_item = mock.MagicMock(
        return_value=SimpleNamespace(id=CONTEST_ID, name="dia1"))
    handler.get_argument = mock.MagicMock(
        side_effect=lambda name, default=None: form.get(name, default))
    handler.get_arguments = mock.MagicMock(
        side_effect=lambda name: list(user_ids) if name == "user_id" else [])
    handler.render_params = mock.MagicMock(side_effect=lambda: {
        "url": fake_url, "xsrf_form_html": "",
        "admin": SimpleNamespace(permission_all=True)})
    handler.render = mock.MagicMock()
    handler.redirect = mock.MagicMock()
    handler.url = mock.MagicMock(side_effect=fake_url)
    handler.schedule_rpc = mock.MagicMock()
    handler.try_commit = mock.MagicMock(return_value=True)
    return handler


def notifications(handler):
    return [call.args[1:] for call in
            handler.service.add_notification.call_args_list]


class TestPreview(unittest.TestCase):

    def test_checkboxes_render_the_plan(self):
        plan = make_plan(entry(7, "ana"))
        handler = make_handler(user_ids=["7"])
        with mock.patch(MODULE + ".plan_removal",
                        return_value=plan) as planner:
            handler._post_sync()

        planner.assert_called_once_with(
            handler.sql_session, contest_id=None, user_ids=[7],
            usernames=[])
        handler.render.assert_called_once()
        (template,), params = handler.render.call_args
        self.assertEqual(template, "users_bulk_remove.html")
        self.assertIs(params["plan"], plan)
        self.assertIsNone(params["error"])
        self.assertEqual(params["action_url"], "/users/remove")
        self.assertEqual(params["back_url"], "/users")

    def test_textarea_and_file_are_both_read(self):
        handler = make_handler(BulkRemoveParticipationsHandler,
                               form={"usernames": "ana\nbeto"},
                               file=b"username\nbeto\ncarla\n")
        with mock.patch(MODULE + ".plan_removal",
                        return_value=make_plan()) as planner:
            handler._post_sync(str(CONTEST_ID))

        planner.assert_called_once_with(
            handler.sql_session, contest_id=CONTEST_ID, user_ids=[],
            usernames=["ana", "beto", "carla"])
        params = handler.render.call_args.kwargs
        self.assertEqual(params["action_url"], "/contest/4/users/remove")
        self.assertEqual(params["back_url"], "/contest/4/users")

    def test_nothing_chosen_redirects_back(self):
        handler = make_handler(form={"usernames": "  \n "})
        with mock.patch(MODULE + ".plan_removal") as planner:
            handler._post_sync()

        planner.assert_not_called()
        handler.render.assert_not_called()
        handler.redirect.assert_called_once_with("/users")
        self.assertEqual(notifications(handler), [(NOTHING_CHOSEN, "")])

    def test_a_bad_list_redirects_back(self):
        handler = make_handler(file="josé".encode("latin-1"))
        with mock.patch(MODULE + ".plan_removal") as planner:
            handler._post_sync()

        planner.assert_not_called()
        handler.redirect.assert_called_once_with("/users")
        self.assertEqual(notifications(handler), [
            ("No se leyó la lista", "la lista no está en UTF-8")])

    def test_a_user_id_that_is_not_a_number_redirects_back(self):
        for action in ("preview", "confirm"):
            handler = make_handler(form={"action": action,
                                         "expected_count": "1"},
                                   user_ids=["7", "x"])
            with mock.patch(MODULE + ".plan_removal") as planner, \
                    mock.patch(MODULE + ".remove_users") as remover:
                handler._post_sync()

            planner.assert_not_called()
            remover.assert_not_called()
            handler.redirect.assert_called_once_with("/users")
            self.assertEqual(notifications(handler),
                             [("Invalid field(s)", "user_id")])


class TestConfirm(unittest.TestCase):

    def confirm(self, handler_class, plan, *, user_ids, expected_count,
                contest_id=None):
        form = {"action": "confirm", "expected_count": expected_count}
        handler = make_handler(handler_class, form=form, user_ids=user_ids)
        with mock.patch(MODULE + ".plan_removal", return_value=plan), \
                mock.patch(MODULE + ".remove_users",
                           return_value=len(plan.users)) as users, \
                mock.patch(MODULE + ".remove_participations",
                           return_value=len(plan.users)) as participations:
            if contest_id is None:
                handler._post_sync()
            else:
                handler._post_sync(str(contest_id))
        return handler, users, participations

    def test_platform_removal(self):
        plan = make_plan(entry(7, "ana"), entry(9, "beto"))
        handler, users, participations = self.confirm(
            BulkRemoveUsersHandler, plan, user_ids=["9", "7"],
            expected_count="2")

        users.assert_called_once_with(handler.sql_session, [7, 9])
        participations.assert_not_called()
        handler.try_commit.assert_called_once_with()
        handler.schedule_rpc.assert_called_once_with(
            handler.service.proxy_service.reinitialize)
        self.assertIn(("Se borraron 2 usuarios", ""), notifications(handler))
        handler.redirect.assert_called_once_with("/users")

    def test_contest_removal(self):
        plan = make_plan(entry(7, "ana"))
        handler, users, participations = self.confirm(
            BulkRemoveParticipationsHandler, plan, user_ids=["7"],
            expected_count="1", contest_id=CONTEST_ID)

        participations.assert_called_once_with(
            handler.sql_session, CONTEST_ID, [7])
        users.assert_not_called()
        handler.schedule_rpc.assert_called_once()
        self.assertIn(("Se quitaron 1 participaciones", ""),
                      notifications(handler))
        handler.redirect.assert_called_once_with("/contest/4/users")

    def test_a_wrong_count_removes_nothing(self):
        for typed in ("3", "", "dos"):
            plan = make_plan(entry(7, "ana"), entry(9, "beto"))
            handler, users, _ = self.confirm(
                BulkRemoveUsersHandler, plan, user_ids=["7", "9"],
                expected_count=typed)

            users.assert_not_called()
            handler.schedule_rpc.assert_not_called()
            params = handler.render.call_args.kwargs
            self.assertEqual(params["error"], WRONG_COUNT)
            self.assertIs(params["plan"], plan)

    def test_a_changed_plan_removes_nothing(self):
        # Beto was removed by someone else since the preview.
        plan = make_plan(entry(7, "ana"), ignored=[("9", NOT_FOUND)])
        handler, users, _ = self.confirm(
            BulkRemoveUsersHandler, plan, user_ids=["7", "9"],
            expected_count="2")

        users.assert_not_called()
        handler.schedule_rpc.assert_not_called()
        params = handler.render.call_args.kwargs
        self.assertEqual(params["error"], PLAN_CHANGED)
        self.assertIs(params["plan"], plan)

    def test_a_failed_commit_schedules_no_rpc(self):
        form = {"action": "confirm", "expected_count": "1"}
        handler = make_handler(form=form, user_ids=["7"])
        handler.try_commit.return_value = False
        with mock.patch(MODULE + ".plan_removal",
                        return_value=make_plan(entry(7, "ana"))), \
                mock.patch(MODULE + ".remove_users", return_value=1):
            handler._post_sync()

        handler.schedule_rpc.assert_not_called()
        handler.redirect.assert_called_once_with("/users")

    def test_a_failed_removal_rolls_back(self):
        form = {"action": "confirm", "expected_count": "1"}
        handler = make_handler(form=form, user_ids=["7"])
        with mock.patch(MODULE + ".plan_removal",
                        return_value=make_plan(entry(7, "ana"))), \
                mock.patch(MODULE + ".remove_users",
                           side_effect=RuntimeError("boom")):
            handler._post_sync()

        handler.sql_session.rollback.assert_called_once_with()
        handler.try_commit.assert_not_called()
        handler.schedule_rpc.assert_not_called()
        self.assertEqual(notifications(handler), [
            ("No se borró nada", repr(RuntimeError("boom")))])

    def test_confirm_without_users_redirects_back(self):
        form = {"action": "confirm", "expected_count": "0"}
        handler = make_handler(form=form)
        with mock.patch(MODULE + ".plan_removal") as planner:
            handler._post_sync()

        planner.assert_not_called()
        handler.redirect.assert_called_once_with("/users")
        self.assertEqual(notifications(handler), [(NOTHING_CHOSEN, "")])


class TestPermissionsAndRoutes(unittest.TestCase):

    def test_both_need_all_permissions(self):
        for handler_class, args in (
                (BulkRemoveUsersHandler, ()),
                (BulkRemoveParticipationsHandler, (str(CONTEST_ID),))):
            handler = make_handler(handler_class, permission_all=False)
            handler._post_sync = mock.MagicMock()
            with self.assertRaises(tornado.web.HTTPError) as caught:
                handler.post(*args)
            self.assertEqual(caught.exception.status_code, 403)
            handler._post_sync.assert_not_called()

    def route_of(self, handler_class):
        (pattern,) = [route[0] for route in HANDLERS
                      if route[1] is handler_class]
        return pattern

    def test_routes(self):
        self.assertRegex("/users/remove",
                         "^%s$" % self.route_of(BulkRemoveUsersHandler))
        pattern = self.route_of(BulkRemoveParticipationsHandler)
        self.assertRegex("/contest/12/users/remove", "^%s$" % pattern)
        self.assertNotRegex("/contest/x/users/remove", "^%s$" % pattern)


class TestConfirmationPage(unittest.TestCase):

    def render(self, plan, *, contest=None, error=None):
        template = AWS_ENVIRONMENT.get_template("users_bulk_remove.html")
        params = {"url": fake_url, "xsrf_form_html": "",
                  "admin": SimpleNamespace(permission_all=True),
                  "contest": contest, "plan": plan, "error": error,
                  "action_url": "/users/remove", "back_url": "/users"}
        return "".join(template.blocks["core"](template.new_context(params)))

    def test_platform_page(self):
        plan = make_plan(entry(7, "ana", submissions=3,
                               contests=("dia1", "dia2")),
                         entry(9, "beto"),
                         ignored=[("nadie", NOT_FOUND)])
        html = self.render(plan)

        self.assertIn("ana", html)
        self.assertIn("dia1, dia2", html)
        self.assertIn("Se borrarán 2 usuarios, 3 envíos y 0 user tests.",
                      html)
        self.assertIn("nadie", html)
        self.assertIn(NOT_FOUND, html)
        self.assertIn('name="user_id" value="7"', html)
        self.assertIn('name="user_id" value="9"', html)
        self.assertIn('name="action" value="confirm"', html)
        self.assertIn('name="expected_count"', html)
        self.assertIn("Escribe 2 para confirmar", html)
        self.assertNotIn("bulk_remove_running", html)

    def test_contest_page_and_running_warning(self):
        contest = SimpleNamespace(id=CONTEST_ID, name="dia1")
        html = self.render(make_plan(entry(7, "ana"), running=True),
                           contest=contest)

        self.assertIn("Se quitarán 1 participaciones de este concurso", html)
        self.assertIn("bulk_remove_running", html)

    def test_error_line(self):
        html = self.render(make_plan(entry(7, "ana")), error=WRONG_COUNT)

        self.assertIn(WRONG_COUNT, html)

    def test_nothing_to_remove_has_no_button(self):
        html = self.render(make_plan(ignored=[("nadie", NOT_FOUND)]))

        self.assertIn("No hay nada que borrar.", html)
        self.assertNotIn('name="expected_count"', html)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkremove_handlers_test.py -v`
Expected: `ModuleNotFoundError: No module named 'cms.server.admin.handlers.bulkremove'`.

- [ ] **Step 3: Write the handlers**

Create `cms/server/admin/handlers/bulkremove.py` (AGPL header, then):

```python
"""Handlers of AWS that remove several users, or participations, at once.

"""

import asyncio

from cms.db import Contest
from cms.server.admin.bulkremove import RemovalPlan, parse_usernames, \
    plan_removal, remove_participations, remove_users
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission

NOTHING_CHOSEN = "No elegiste ningún usuario."
WRONG_COUNT = ("El número escrito no coincide con el número de usuarios. "
               "No se borró nada.")
PLAN_CHANGED = ("La selección cambió desde la vista previa: revisa la "
                "lista y confirma otra vez. No se borró nada.")


class _BulkRemoveHandler(BaseHandler):
    """Remove several users from the platform, or from one contest.

    POST with action=preview (the default) shows what would be removed:
    the checked user_id values and the usernames of the "usernames"
    textarea and of the "file" upload. POST with action=confirm removes
    the posted user_id values, if they still resolve to the same users
    and expected_count is their number.

    """

    def _post_sync(self, contest_id: str | None = None) -> None:
        self.contest = None if contest_id is None \
            else self.safe_get_item(Contest, contest_id)
        if self.contest is None:
            self._back_url = self.url("users")
            self._action_url = self.url("users", "remove")
        else:
            self._back_url = self.url("contest", self.contest.id, "users")
            self._action_url = self.url("contest", self.contest.id,
                                        "users", "remove")
        try:
            user_ids = [int(value)
                        for value in self.get_arguments("user_id")]
        except ValueError:
            self._notify("Invalid field(s)", "user_id")
            self.redirect(self._back_url)
            return
        if self.get_argument("action", "preview") == "confirm":
            self._confirm(user_ids)
        else:
            self._preview(user_ids)

    def _scope_id(self) -> int | None:
        return None if self.contest is None else self.contest.id

    def _notify(self, subject: str, text: str) -> None:
        self.service.add_notification(make_datetime(), subject, text)

    def _preview(self, user_ids: list[int]) -> None:
        sources = []
        text = self.get_argument("usernames", "")
        if text.strip():
            sources.append(text.encode("utf-8"))
        files = self.request.files.get("file")
        if files and files[0]["body"]:
            sources.append(files[0]["body"])
        try:
            usernames = parse_usernames(*sources)
        except ValueError as error:
            self._notify("No se leyó la lista", str(error))
            self.redirect(self._back_url)
            return
        if not user_ids and not usernames:
            self._notify(NOTHING_CHOSEN, "")
            self.redirect(self._back_url)
            return
        plan = plan_removal(self.sql_session, contest_id=self._scope_id(),
                            user_ids=user_ids, usernames=usernames)
        self._render_plan(plan, None)

    def _confirm(self, user_ids: list[int]) -> None:
        if not user_ids:
            self._notify(NOTHING_CHOSEN, "")
            self.redirect(self._back_url)
            return
        plan = plan_removal(self.sql_session, contest_id=self._scope_id(),
                            user_ids=user_ids)
        if len(plan.users) != len(set(user_ids)):
            self._render_plan(plan, PLAN_CHANGED)
            return
        if self.get_argument("expected_count", "") != str(len(plan.users)):
            self._render_plan(plan, WRONG_COUNT)
            return
        try:
            if self.contest is None:
                count = remove_users(self.sql_session, plan.user_ids)
            else:
                count = remove_participations(
                    self.sql_session, self.contest.id, plan.user_ids)
        except Exception as error:
            self.sql_session.rollback()
            self._notify("No se borró nada", repr(error))
            self.redirect(self._back_url)
            return
        if self.try_commit():
            self.schedule_rpc(self.service.proxy_service.reinitialize)
            self._notify(("Se borraron %d usuarios" if self.contest is None
                          else "Se quitaron %d participaciones") % count, "")
        self.redirect(self._back_url)

    def _render_plan(self, plan: RemovalPlan, error: str | None) -> None:
        """Render the confirmation page.

        plan: what the removal would take.
        error: why the last confirmation removed nothing, or None.

        """
        self.r_params = self.render_params()
        self.r_params["contest"] = self.contest
        self.r_params["plan"] = plan
        self.r_params["error"] = error
        self.r_params["action_url"] = self._action_url
        self.r_params["back_url"] = self._back_url
        self.render("users_bulk_remove.html", **self.r_params)


class BulkRemoveUsersHandler(_BulkRemoveHandler):
    """Remove several users, and all their data, from the platform."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync)


class BulkRemoveParticipationsHandler(_BulkRemoveHandler):
    """Remove the participations of several users from a contest."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    async def post(self, contest_id: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post_sync, contest_id)
```

Note: `self.service` in the tests is `handler.application.service` through
`BaseHandler`; check how `BaseHandler.service` is defined and, if the
tests' `handler.service.add_notification` does not reach the same mock,
set `handler.service = mock.MagicMock()` in `make_handler` rather than
changing the handler.

Note: `test_both_need_all_permissions` calls `handler.post(...)` without
awaiting; this matches `contest_users_import_test.py`, where
`require_permission` raises the 403 before the coroutine body runs. If
the decorator in this repo returns a coroutine instead, drive it with
`asyncio.run(handler.post(*args))` inside the `assertRaises`.

- [ ] **Step 4: Register the routes**

In `cms/server/admin/handlers/__init__.py` add, after the `from .contestuser import ...` block:

```python
from .bulkremove import \
    BulkRemoveParticipationsHandler, \
    BulkRemoveUsersHandler
```

In `HANDLERS`, after `(r"/contest/([0-9]+)/users/add", AddContestUserHandler),` add:

```python
    (r"/contest/([0-9]+)/users/remove", BulkRemoveParticipationsHandler),
```

and after `(r"/users/([0-9]+)/remove", RemoveUserHandler),` add:

```python
    (r"/users/remove", BulkRemoveUsersHandler),
```

- [ ] **Step 5: Write the confirmation template**

Create `cms/server/admin/templates/users_bulk_remove.html`:

```html
{% extends "base.html" %}

{% block core %}
<div class="core_title">
{% if contest is none %}
  <h1>Borrar usuarios</h1>
{% else %}
  <h1>Quitar participaciones de {{ contest.name }}</h1>
{% endif %}
</div>

{% if error is not none %}
<p class="bulk_remove_error" style="color: red;"><strong>{{ error }}</strong></p>
{% endif %}

{% if plan.running %}
<p class="bulk_remove_running" style="color: red;"><strong>
  Atención: hay un concurso en curso entre los afectados. Se perderán
  envíos hechos durante el concurso y el ranking se recalculará.
</strong></p>
{% endif %}

{% if plan.users %}
<p>
{% if contest is none %}
  Se borrarán {{ plan.users|length }} usuarios, {{ plan.submissions }} envíos y {{ plan.user_tests }} user tests.
{% else %}
  Se quitarán {{ plan.users|length }} participaciones de este concurso, con {{ plan.submissions }} envíos y {{ plan.user_tests }} user tests.
{% endif %}
  Esta operación no se puede deshacer.
</p>
<table class="bordered">
  <thead>
    <tr>
      <th>Username</th>
      <th>Nombre</th>
      <th>Apellido</th>
      <th>Envíos</th>
      <th>User tests</th>
{% if contest is none %}
      <th>Concursos</th>
{% endif %}
    </tr>
  </thead>
  <tbody>
{% for u in plan.users %}
    <tr>
      <td>{{ u.username }}</td>
      <td>{{ u.first_name }}</td>
      <td>{{ u.last_name }}</td>
      <td>{{ u.submissions }}</td>
      <td>{{ u.user_tests }}</td>
{% if contest is none %}
      <td>{{ u.contests|join(", ") }}</td>
{% endif %}
    </tr>
{% endfor %}
  </tbody>
</table>
{% else %}
<p>No hay nada que borrar.</p>
{% endif %}

{% if plan.ignored %}
<p>Ignorados:</p>
<ul>
{% for value, reason in plan.ignored %}
  <li>{{ value }} — {{ reason }}</li>
{% endfor %}
</ul>
{% endif %}

{% if plan.users %}
<form action="{{ action_url }}" method="POST">
  {{ xsrf_form_html|safe }}
  <input type="hidden" name="action" value="confirm"/>
{% for user_id in plan.user_ids %}
  <input type="hidden" name="user_id" value="{{ user_id }}"/>
{% endfor %}
  <label>Escribe {{ plan.users|length }} para confirmar:
    <input type="text" name="expected_count" autocomplete="off"/>
  </label>
  <input type="submit"
         value="{% if contest is none %}Borrar{% else %}Quitar{% endif %}"/>
</form>
{% endif %}
<p><a href="{{ back_url }}">Cancelar</a></p>

{% endblock core %}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkremove_handlers_test.py -v`
Expected: all pass.

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/admin/ -q` (with the `CMS_CONFIG` export)
Expected: no failures (the routes table changed; the other AWS tests must still pass).

Run: `.venv/bin/pyflakes cms/server/admin/handlers/bulkremove.py cms/server/admin/handlers/__init__.py cmstestsuite/unit_tests/server/admin/bulkremove_handlers_test.py`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add cms/server/admin/handlers/bulkremove.py cms/server/admin/handlers/__init__.py cms/server/admin/templates/users_bulk_remove.html cmstestsuite/unit_tests/server/admin/bulkremove_handlers_test.py
git commit -m "feat(admin): confirm and run bulk removal of users and participations"
```
(with the model trailer)

---

### Task 4: Checkboxes and "por lista" forms on the two lists

**Files:**
- Modify: `cms/server/admin/templates/users.html`
- Modify: `cms/server/admin/templates/contest_users.html:39-73`
- Test: `cmstestsuite/unit_tests/server/admin/bulkremove_lists_test.py`

**Interfaces:**
- Consumes (Task 3): routes `url("users", "remove")` and `url("contest", contest.id, "users", "remove")`; form fields `action=preview`, `user_id` (repeated), `usernames`, `file`.
- Produces: the two list pages post to those routes.

Both pages keep every other element as it is (the "Add a new user"
form, the "Importar CSV" link, the table columns and links). Only the
selection form changes, and a second form is added below the table.
The submit buttons stay disabled for admins without `permission_all`,
as today.

- [ ] **Step 1: Write the failing tests**

Create `cmstestsuite/unit_tests/server/admin/bulkremove_lists_test.py` (AGPL header, then):

```python
"""Tests for the checkboxes and "por lista" forms of the AWS user lists.

The templates are rendered from their "core" block only.

"""

import unittest
from types import SimpleNamespace

from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT


def fake_url(*parts):
    return "/" + "/".join(str(part) for part in parts)


def user(user_id, username):
    return SimpleNamespace(id=user_id, username=username,
                           first_name="N", last_name="A")


class ListPageTestCase(unittest.TestCase):

    def render(self, name, permission_all=True, **params):
        template = AWS_ENVIRONMENT.get_template(name)
        params.update({"url": fake_url, "xsrf_form_html": "",
                       "admin": SimpleNamespace(
                           permission_all=permission_all)})
        return "".join(template.blocks["core"](template.new_context(params)))

    def assert_bulk_forms(self, html, action, button):
        self.assertNotIn('type="radio"', html)
        self.assertIn('action="%s" method="POST"' % action, html)
        self.assertIn('name="action" value="preview"', html)
        self.assertIn('id="select_all_users"', html)
        self.assertIn('value="%s"' % button, html)
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertIn('<textarea name="usernames"', html)
        self.assertIn('type="file" name="file"', html)
        self.assertIn('value="Revisar lista"', html)


class TestUsersPage(ListPageTestCase):

    def test_checkboxes_and_list_form(self):
        html = self.render("users.html",
                           user_list=[user(7, "ana"), user(9, "beto")])

        self.assert_bulk_forms(html, "/users/remove", "Borrar seleccionados")
        self.assertIn('type="checkbox" name="user_id" value="7"', html)
        self.assertIn('type="checkbox" name="user_id" value="9"', html)

    def test_buttons_disabled_without_all_permissions(self):
        html = self.render("users.html", permission_all=False,
                           user_list=[user(7, "ana")])

        self.assertEqual(html.count("disabled"), 2)


class TestContestUsersPage(ListPageTestCase):

    def contest(self):
        group = SimpleNamespace(id=1, name="main")
        participation = SimpleNamespace(user=user(7, "ana"), group=group)
        return SimpleNamespace(id=4, name="dia1", groups=[group],
                               main_group_id=1,
                               participations=[participation])

    def test_checkboxes_and_list_form(self):
        # The overload warning fragment reads the CWS ports.
        config = SimpleNamespace(
            contest_web_server=SimpleNamespace(listen_port=[8888]))
        html = self.render("contest_users.html", contest=self.contest(),
                           unassigned_users=[], config=config)

        self.assert_bulk_forms(html, "/contest/4/users/remove",
                               "Quitar seleccionados del concurso")
        self.assertIn('type="checkbox" name="user_id" value="7"', html)
        # The rest of the page is still there.
        self.assertIn('value="Add user"', html)
        self.assertIn("Importar CSV", html)
```

`contest_users.html` includes `fragments/overload_warning.html`, which
reads `config.contest_web_server.listen_port`; the test passes a fake
`config` for it. If rendering needs more parameters, add them to the
test's `render` call, not to the template.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkremove_lists_test.py -v`
Expected: FAIL (`'type="radio"'` found; no `/users/remove`).

- [ ] **Step 3: Change `users.html`**

Replace the whole `<form action="{{ url("users") }}" method="POST"> ... </form>` block with:

```html
<form action="{{ url("users", "remove") }}" method="POST">
  {{ xsrf_form_html|safe }}
  <input type="hidden" name="action" value="preview"/>
  Usuarios seleccionados:
  <input type="submit"
         value="Borrar seleccionados"
{% if not admin.permission_all %}
         disabled
{% endif %}
         />
  <table class="bordered">
    <thead>
      <tr>
        <th><input type="checkbox" id="select_all_users" title="Seleccionar todo"/></th>
        <th>Username</th>
        <th>First name</th>
        <th>Last name</th>
      </tr>
    </thead>
    <tbody>
      {% for u in user_list|sort(attribute="username") %}
      <tr>
        <td>
          <input type="checkbox" name="user_id" value="{{ u.id }}"/>
        </td>
        <td><a href="{{ url("user", u.id) }}">{{ u.username }}</a></td>
        <td>{{ u.first_name }}</td>
        <td>{{ u.last_name }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</form>

<h2>Borrar por lista</h2>
<form action="{{ url("users", "remove") }}" method="POST"
      enctype="multipart/form-data">
  {{ xsrf_form_html|safe }}
  <input type="hidden" name="action" value="preview"/>
  <p>Un username por línea, o un CSV cuya primera columna sea el username.</p>
  <textarea name="usernames" rows="6" cols="40"></textarea><br>
  <input type="file" name="file" accept=".csv,.txt,text/csv,text/plain"/><br>
  <input type="submit"
         value="Revisar lista"
{% if not admin.permission_all %}
         disabled
{% endif %}
         />
</form>

<script>
document.getElementById("select_all_users").addEventListener(
    "change", function () {
        var boxes = document.querySelectorAll('input[name="user_id"]');
        for (var i = 0; i < boxes.length; i++) {
            boxes[i].checked = this.checked;
        }
    });
</script>
```

- [ ] **Step 4: Change `contest_users.html`**

Replace the second form (the one starting `<form action="{{ url("contest", contest.id, "users") }}" method="POST">`, currently lines 39-73) with the same structure, using:
- form action `{{ url("contest", contest.id, "users", "remove") }}` for both forms;
- button value `Quitar seleccionados del concurso`;
- heading `<h2>Quitar por lista</h2>`;
- the same table columns as today (Username linking to `url("contest", contest.id, "user", u.user.id, "edit")`, First name, Last name, Group), iterating `contest.participations|sort(attribute="user.username")`, with the checkbox `<input type="checkbox" name="user_id" value="{{ u.user.id }}"/>`;
- the same `select_all_users` header checkbox and script.

Written out:

```html
<form action="{{ url("contest", contest.id, "users", "remove") }}" method="POST">
  {{ xsrf_form_html|safe }}
  <input type="hidden" name="action" value="preview"/>
  Usuarios seleccionados:
  <input type="submit"
         value="Quitar seleccionados del concurso"
{% if not admin.permission_all %}
         disabled
{% endif %}
         />
  <table class="bordered">
    <thead>
      <tr>
        <th><input type="checkbox" id="select_all_users" title="Seleccionar todo"/></th>
        <th>Username</th>
        <th>First name</th>
        <th>Last name</th>
        <th>Group</th>
      </tr>
    </thead>
    <tbody>
      {% for u in contest.participations|sort(attribute="user.username") %}
      <tr>
        <td>
          <input type="checkbox" name="user_id" value="{{ u.user.id }}"/>
        </td>
        <td><a href="{{ url("contest", contest.id, "user", u.user.id, "edit") }}">{{ u.user.username }}</a></td>
        <td>{{ u.user.first_name }}</td>
        <td>{{ u.user.last_name }}</td>
        <td>{{ u.group.name }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</form>

<h2>Quitar por lista</h2>
<form action="{{ url("contest", contest.id, "users", "remove") }}" method="POST"
      enctype="multipart/form-data">
  {{ xsrf_form_html|safe }}
  <input type="hidden" name="action" value="preview"/>
  <p>Un username por línea, o un CSV cuya primera columna sea el username.</p>
  <textarea name="usernames" rows="6" cols="40"></textarea><br>
  <input type="file" name="file" accept=".csv,.txt,text/csv,text/plain"/><br>
  <input type="submit"
         value="Revisar lista"
{% if not admin.permission_all %}
         disabled
{% endif %}
         />
</form>

<script>
document.getElementById("select_all_users").addEventListener(
    "change", function () {
        var boxes = document.querySelectorAll('input[name="user_id"]');
        for (var i = 0; i < boxes.length; i++) {
            boxes[i].checked = this.checked;
        }
    });
</script>
```

The "Add a new user" form above it has its own `disabled` for admins
without all permissions; `test_buttons_disabled_without_all_permissions`
only checks `users.html`, which has exactly the two new buttons.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest cmstestsuite/unit_tests/server/admin/bulkremove_lists_test.py -v`
Expected: all pass.

Run (with the `CMS_CONFIG` export): `.venv/bin/pytest cmstestsuite/unit_tests/server/admin/ -q`
Expected: no failures.

- [ ] **Step 6: Commit**

```bash
git add cms/server/admin/templates/users.html cms/server/admin/templates/contest_users.html cmstestsuite/unit_tests/server/admin/bulkremove_lists_test.py
git commit -m "feat(admin): select users with checkboxes or a list for bulk removal"
```
(with the model trailer)

---

## After the tasks (controller)

These are not implementer tasks; the controller runs them after the final
whole-branch review, in this order, one Docker stack at a time and never
while another stack (the load test, the `fix-auditoria` CI) is running:

1. The whole unit suite and pyflakes in the worktree.
2. The Chromium E2E of the spec (section 3, "Browser E2E") against a local
   Docker stack built from this branch, by a UI tester agent, with
   screenshots for the user.
3. The CI on noble, then on bookworm (`docker/docker-compose.test.yml`,
   rootful Docker, a unique project name).
4. Ask the user before merging into `main`; merge only if all of this
   passed before 2026-10-02.
