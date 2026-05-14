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

import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import Admin
from cmscommon.crypto import validate_password


class TestEnsureFirstAdmin(DatabaseMixin, unittest.TestCase):

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _admin_count(self):
        return self.session.query(Admin).count()

    def _get_admin(self, username):
        return self.session.query(Admin).filter(Admin.username == username).one()

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_skips_if_admin_exists(self):
        """Returns True immediately when an admin already exists."""
        self.add_admin(username="existing")
        from cmscontrib.SetupDB import ensure_first_admin
        result = ensure_first_admin()
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 1)

    def test_creates_admin_from_env(self):
        """Creates admin from CMS_ADMIN_USER / CMS_ADMIN_PASSWORD env vars."""
        from cmscontrib.SetupDB import ensure_first_admin
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
        from cmscontrib.SetupDB import ensure_first_admin
        env = {"CMS_ADMIN_USER": "sysadmin"}
        with patch("sys.stdin.isatty", return_value=False), \
             patch.dict("os.environ", env, clear=False), \
             patch.dict("os.environ", {"CMS_ADMIN_PASSWORD": ""}, clear=False):
            # ensure CMS_ADMIN_PASSWORD is absent
            import os
            os.environ.pop("CMS_ADMIN_PASSWORD", None)
            result = ensure_first_admin()
        self.assertFalse(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 0)

    def test_partial_env_only_password_returns_false(self):
        """Returns False when only CMS_ADMIN_PASSWORD is set."""
        from cmscontrib.SetupDB import ensure_first_admin
        import os
        saved_user = os.environ.pop("CMS_ADMIN_USER", None)
        try:
            with patch("sys.stdin.isatty", return_value=False), \
                 patch.dict("os.environ", {"CMS_ADMIN_PASSWORD": "s3cr3t"}, clear=False):
                result = ensure_first_admin()
        finally:
            if saved_user is not None:
                os.environ["CMS_ADMIN_USER"] = saved_user
        self.assertFalse(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 0)

    def test_no_tty_no_env_warns_and_returns_true(self):
        """Returns True (non-fatal) when no TTY and no env vars."""
        from cmscontrib.SetupDB import ensure_first_admin
        import os
        saved_user = os.environ.pop("CMS_ADMIN_USER", None)
        saved_pass = os.environ.pop("CMS_ADMIN_PASSWORD", None)
        try:
            with patch("sys.stdin.isatty", return_value=False):
                result = ensure_first_admin()
        finally:
            if saved_user is not None:
                os.environ["CMS_ADMIN_USER"] = saved_user
            if saved_pass is not None:
                os.environ["CMS_ADMIN_PASSWORD"] = saved_pass
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 0)

    def test_interactive_creates_admin(self):
        """Creates admin from interactive TTY prompt."""
        from cmscontrib.SetupDB import ensure_first_admin
        import os
        saved_user = os.environ.pop("CMS_ADMIN_USER", None)
        saved_pass = os.environ.pop("CMS_ADMIN_PASSWORD", None)
        try:
            with patch("sys.stdin.isatty", return_value=True), \
                 patch("builtins.input", return_value="interadmin"), \
                 patch("getpass.getpass", side_effect=["mypassword", "mypassword"]):
                result = ensure_first_admin()
        finally:
            if saved_user is not None:
                os.environ["CMS_ADMIN_USER"] = saved_user
            if saved_pass is not None:
                os.environ["CMS_ADMIN_PASSWORD"] = saved_pass
        self.assertTrue(result)
        self.session.expire_all()
        a = self._get_admin("interadmin")
        self.assertTrue(validate_password(a.authentication, "mypassword"))
        self.assertTrue(a.permission_all)

    def test_interactive_mismatch_3_times_returns_false(self):
        """Returns False after 3 password confirmation mismatches."""
        from cmscontrib.SetupDB import ensure_first_admin
        import os
        saved_user = os.environ.pop("CMS_ADMIN_USER", None)
        saved_pass = os.environ.pop("CMS_ADMIN_PASSWORD", None)
        try:
            with patch("sys.stdin.isatty", return_value=True), \
                 patch("builtins.input", return_value="interadmin"), \
                 patch("getpass.getpass",
                       side_effect=["pw1", "bad", "pw2", "bad", "pw3", "bad"]):
                result = ensure_first_admin()
        finally:
            if saved_user is not None:
                os.environ["CMS_ADMIN_USER"] = saved_user
            if saved_pass is not None:
                os.environ["CMS_ADMIN_PASSWORD"] = saved_pass
        self.assertFalse(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 0)


if __name__ == "__main__":
    unittest.main()
