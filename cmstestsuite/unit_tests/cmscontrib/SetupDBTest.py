#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""Tests for the SetupDB script."""

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import Admin, Contest, custom_psycopg2_connection
from cmscommon.crypto import validate_password
from cmscontrib.SetupDB import ensure_first_admin
from cmscontrib.updaters.fork_ddl import SchemaLockTimeout, apply_fork_sql
from cmscontrib.updaters.fork_submission_limits import \
    apply_fork_submission_limits_update


# The source tree, for scripts/cmsSetupDB; the tests run from it, in the
# CI image too.
REPO_ROOT = Path(__file__).resolve().parents[3]

LOCK_MESSAGE = (
    "cmsSetupDB could not lock %s within 5 s: another session is using "
    "them. That update changed nothing. Find that session with the "
    "holder query in the contest-day runbook (\"When the database "
    "misbehaves\"), wait for it to finish or end it, then run the setup "
    "again.")


class TestEnsureFirstAdmin(DatabaseMixin, unittest.TestCase):

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _admin_count(self):
        return self.session.execute(
            select(func.count()).select_from(Admin)
        ).scalar_one()

    def _get_admin(self, username):
        return self.session.execute(
            select(Admin).filter(Admin.username == username)
        ).scalar_one()

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_skips_if_admin_exists(self):
        """Returns True immediately when an admin already exists."""
        self.add_admin(username="existing")
        result = ensure_first_admin()
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 1)

    def test_creates_admin_from_env(self):
        """Creates admin from CMS_ADMIN_USER / CMS_ADMIN_PASSWORD env vars."""
        env = {"CMS_ADMIN_USER": "sysadmin", "CMS_ADMIN_PASSWORD": "s3cr3t"}
        with patch("sys.stdin.isatty", return_value=False), \
             patch.dict("os.environ", env, clear=False):
            result = ensure_first_admin()
        self.assertTrue(result)
        self.session.expire_all()
        a = self._get_admin("sysadmin")
        self.assertTrue(validate_password(a.authentication, "s3cr3t"))
        self.assertTrue(a.permission_all)

    def test_partial_env_only_user_returns_false(self):
        """Returns False when only CMS_ADMIN_USER is set."""
        saved_pass = os.environ.pop("CMS_ADMIN_PASSWORD", None)
        try:
            with patch("sys.stdin.isatty", return_value=False), \
                 patch.dict("os.environ", {"CMS_ADMIN_USER": "sysadmin"}, clear=False):
                result = ensure_first_admin()
        finally:
            if saved_pass is not None:
                os.environ["CMS_ADMIN_PASSWORD"] = saved_pass
        self.assertFalse(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 0)

    def test_partial_env_only_password_returns_false(self):
        """Returns False when only CMS_ADMIN_PASSWORD is set."""
        with patch("sys.stdin.isatty", return_value=False), \
             patch.dict("os.environ", {"CMS_ADMIN_PASSWORD": "s3cr3t"}, clear=False):
            os.environ.pop("CMS_ADMIN_USER", None)
            result = ensure_first_admin()
        self.assertFalse(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 0)

    def test_no_tty_no_env_warns_and_returns_true(self):
        """Returns True (non-fatal) when no TTY and no env vars."""
        with patch("sys.stdin.isatty", return_value=False), \
             patch.dict("os.environ", {}, clear=False):
            os.environ.pop("CMS_ADMIN_USER", None)
            os.environ.pop("CMS_ADMIN_PASSWORD", None)
            result = ensure_first_admin()
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 0)

    def test_interactive_creates_admin(self):
        """Creates admin from interactive TTY prompt."""
        with patch("sys.stdin.isatty", return_value=True), \
             patch.dict("os.environ", {}, clear=False), \
             patch("builtins.input", return_value="interadmin"), \
             patch("getpass.getpass", side_effect=["mypassword", "mypassword"]):
            os.environ.pop("CMS_ADMIN_USER", None)
            os.environ.pop("CMS_ADMIN_PASSWORD", None)
            result = ensure_first_admin()
        self.assertTrue(result)
        self.session.expire_all()
        a = self._get_admin("interadmin")
        self.assertTrue(validate_password(a.authentication, "mypassword"))
        self.assertTrue(a.permission_all)

    def test_interactive_mismatch_3_times_returns_false(self):
        """Returns False after 3 password confirmation mismatches."""
        with patch("sys.stdin.isatty", return_value=True), \
             patch.dict("os.environ", {}, clear=False), \
             patch("builtins.input", return_value="interadmin"), \
             patch("getpass.getpass",
                   side_effect=["pw1", "bad", "pw2", "bad", "pw3", "bad"]):
            os.environ.pop("CMS_ADMIN_USER", None)
            os.environ.pop("CMS_ADMIN_PASSWORD", None)
            result = ensure_first_admin()
        self.assertFalse(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 0)


class TestOfferSampleContest(DatabaseMixin, unittest.TestCase):

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def _contest_count(self):
        return self.session.execute(
            select(func.count()).select_from(Contest)
        ).scalar_one()

    def test_skips_when_no_tty(self):
        """Returns True immediately when there is no TTY (Docker/CI)."""
        from cmscontrib.SetupDB import offer_sample_contest
        with patch("sys.stdin.isatty", return_value=False):
            result = offer_sample_contest()
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._contest_count(), 0)

    def test_skips_if_contest_exists(self):
        """Returns True immediately when a contest already exists."""
        from cmscontrib.SetupDB import offer_sample_contest
        self.add_contest()
        # offer_sample_contest() queries via its own SessionGen(), a
        # separate DB connection that cannot see this session's pending,
        # uncommitted INSERT; commit so it's actually visible to it.
        self.session.commit()
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=AssertionError("should not prompt")):
            result = offer_sample_contest()
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._contest_count(), 1)

    def test_creates_sample_contest_when_confirmed(self):
        """Creates a sample contest when user answers 'y'."""
        from cmscontrib.SetupDB import offer_sample_contest
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="y"):
            result = offer_sample_contest()
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._contest_count(), 1)
        contest = self.session.execute(select(Contest)).scalar_one()
        self.assertEqual(contest.name, "sample")
        self.assertEqual(contest.description, "Sample Contest")

    def test_skips_when_declined(self):
        """Does not create a contest when user declines."""
        from cmscontrib.SetupDB import offer_sample_contest
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="N"):
            result = offer_sample_contest()
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._contest_count(), 0)


class TestSchemaLockTimeout(DatabaseMixin, unittest.TestCase):
    """The fork's schema updates give up on a locked table (#39)."""

    def setUp(self):
        super().setUp()
        # Another session; psycopg2 does not autocommit, so what it runs
        # stays in an open transaction.
        self.holder = custom_psycopg2_connection()
        self.addCleanup(self.holder.close)

    def _hold(self, statement: str) -> None:
        self.holder.cursor().execute(statement)

    def _apply_fork_sql_timed(self, sql: str, tables: str) -> float:
        """Run apply_fork_sql, which must give up; return its lock wait.

        The wait is timed from the moment the connection is open, so it
        leaves out the connect. The connection also gets a 30 s
        statement_timeout, so that a regression of the lock timeout fails
        the test in 30 s (with QueryCanceled) instead of hanging it.

        sql: the statements to apply.
        tables: the tables they lock, for the error message.

        return: the seconds from the open connection to the exception.

        """
        opened_at = []

        def connect_with_backstop():
            conn = custom_psycopg2_connection()
            # Session level: autocommit keeps this SET out of the
            # transaction that apply_fork_sql starts.
            conn.autocommit = True
            conn.cursor().execute("SET statement_timeout = '30s'")
            conn.autocommit = False
            opened_at.append(time.monotonic())
            return conn

        with patch("cmscontrib.updaters.fork_ddl.custom_psycopg2_connection",
                   connect_with_backstop):
            with self.assertRaises(SchemaLockTimeout) as caught:
                apply_fork_sql(sql, tables)
        wait = time.monotonic() - opened_at[0]
        self.assertEqual(caught.exception.tables, tables)
        self.assertEqual(str(caught.exception), LOCK_MESSAGE % tables)
        return wait

    @staticmethod
    def _run_setup_db() -> tuple[subprocess.CompletedProcess, float]:
        """Run cmsSetupDB as db-init does; return it and its duration."""
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            path for path in (str(REPO_ROOT), env.get("PYTHONPATH"))
            if path)
        # No first admin to create: ensure_first_admin() only warns.
        env.pop("CMS_ADMIN_USER", None)
        env.pop("CMS_ADMIN_PASSWORD", None)
        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "cmsSetupDB")],
            env=env, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=60)
        return completed, time.monotonic() - started

    def test_apply_fork_sql_gives_up_after_5_seconds(self):
        self._hold("SELECT 1 FROM contests LIMIT 1")
        wait = self._apply_fork_sql_timed(
            "ALTER TABLE public.contests ALTER COLUMN active DROP DEFAULT",
            "contests")
        self.assertGreaterEqual(wait, 5)
        self.assertLess(wait, 8)

    def test_apply_fork_sql_changes_nothing_when_it_gives_up(self):
        # "That update changed nothing" is what the operator is told: the
        # statement that ran before the blocked one is rolled back too.
        self._hold("SELECT 1 FROM contests LIMIT 1")
        self._apply_fork_sql_timed(
            "CREATE TABLE public.zz_probe (i integer); "
            "ALTER TABLE public.contests ALTER COLUMN active DROP DEFAULT",
            "contests")
        self.holder.rollback()
        cursor = self.holder.cursor()
        cursor.execute("SELECT to_regclass('public.zz_probe') IS NULL")
        self.assertTrue(cursor.fetchone()[0])

    def test_setup_db_exits_1_when_a_session_read_contests(self):
        # F4's holder: it read contests and stays idle in its transaction.
        self._hold("SELECT 1 FROM contests LIMIT 1")
        completed, elapsed = self._run_setup_db()
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn(LOCK_MESSAGE % "contests or ranking_groups",
                      completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertGreaterEqual(elapsed, 5)
        self.assertLess(elapsed, 15)

    def test_setup_db_exits_1_when_a_session_writes_activity(self):
        # A reader would not block it: CREATE INDEX IF NOT EXISTS takes
        # a SHARE lock, which only writers block (ROW EXCLUSIVE, as the
        # activity recorder's inserts take).
        self._hold("LOCK TABLE activity_intervals IN ROW EXCLUSIVE MODE")
        completed, elapsed = self._run_setup_db()
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn(LOCK_MESSAGE % "activity_intervals", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertGreaterEqual(elapsed, 5)
        self.assertLess(elapsed, 15)

    def test_setup_db_succeeds_again_once_the_session_ended(self):
        self._hold("SELECT 1 FROM contests LIMIT 1")
        self.holder.rollback()
        completed, _ = self._run_setup_db()
        self.assertEqual(completed.returncode, 0, completed.stderr)


class TestForkSubmissionLimits(DatabaseMixin, unittest.TestCase):
    """The fork adds the submission limit columns, and without a lock."""

    DROP_CONSTRAINTS = (
        "ALTER TABLE submissions "
        "DROP CONSTRAINT submissions_override_time_limit_check, "
        "DROP CONSTRAINT submissions_override_mem_limit_check, "
        "DROP CONSTRAINT submissions_override_mem_limit_check1")
    DROP_COLUMNS = (
        "ALTER TABLE submissions DROP COLUMN override_time_limit, "
        "DROP COLUMN override_mem_limit")

    def setUp(self):
        super().setUp()
        # What the other tests of this class break, put back at the end.
        self.addCleanup(apply_fork_submission_limits_update)

    @staticmethod
    def _run(statement: str) -> None:
        conn = custom_psycopg2_connection()
        try:
            conn.cursor().execute(statement)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _limits_schema() -> tuple[list, list]:
        """Return the two columns and the CHECKs of the limits, as stored."""
        conn = custom_psycopg2_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT attname, format_type(atttypid, atttypmod), "
                "attnotnull FROM pg_attribute "
                "WHERE attrelid = 'public.submissions'::regclass "
                "AND attname LIKE 'override\\_%' AND NOT attisdropped "
                "ORDER BY attname")
            columns = cursor.fetchall()
            cursor.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'public.submissions'::regclass "
                "AND conname LIKE 'submissions\\_override\\_%' "
                "ORDER BY conname")
            return columns, cursor.fetchall()
        finally:
            conn.close()

    def test_adds_what_init_db_creates_and_a_second_run_changes_nothing(self):
        expected = self._limits_schema()
        # Pins the shape that cmsInitDB gives them, so that the comparison
        # below cannot pass on two empty results.
        self.assertEqual(
            [column[:2] for column in expected[0]],
            [("override_mem_limit", "bigint"),
             ("override_time_limit", "double precision")])
        self.assertEqual(
            [name for name, _ in expected[1]],
            ["submissions_override_mem_limit_check",
             "submissions_override_mem_limit_check1",
             "submissions_override_time_limit_check"])

        for broken in (self.DROP_COLUMNS, self.DROP_CONSTRAINTS):
            with self.subTest(broken=broken):
                self._run(broken)
                self.assertNotEqual(self._limits_schema(), expected)
                apply_fork_submission_limits_update()
                self.assertEqual(self._limits_schema(), expected)
                apply_fork_submission_limits_update()
                self.assertEqual(self._limits_schema(), expected)

    def test_takes_no_lock_on_submissions_when_nothing_is_missing(self):
        holder = custom_psycopg2_connection()
        self.addCleanup(holder.close)
        # Any ALTER TABLE, even one that changes nothing, would wait for
        # this session and give up with SchemaLockTimeout after 5 s.
        holder.cursor().execute("LOCK TABLE submissions IN ACCESS SHARE MODE")
        apply_fork_submission_limits_update()

    def test_setup_db_adds_the_columns(self):
        expected = self._limits_schema()
        self._run(self.DROP_COLUMNS)
        completed, _ = TestSchemaLockTimeout._run_setup_db()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self._limits_schema(), expected)


if __name__ == "__main__":
    unittest.main()
