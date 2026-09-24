#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 The CMS development team
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

"""Tests for rpc_authorization_checker.

"""

import unittest

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.server.admin.rpc_authorization import rpc_authorization_checker


class TestRpcAuthorizationChecker(DatabaseMixin, unittest.TestCase):

    def test_no_admin_id(self):
        self.assertFalse(
            rpc_authorization_checker(
                None, "ResourceService", 0, "kill_service"))

    def test_admin_with_permission_all_is_allowed(self):
        admin = self.add_admin(permission_all=True)
        self.assertTrue(
            rpc_authorization_checker(
                admin.id, "ResourceService", 0, "kill_service"))

    def test_unknown_admin_is_denied(self):
        admin = self.add_admin(permission_all=True)
        self.assertFalse(
            rpc_authorization_checker(
                admin.id + 1000000, "ResourceService", 0, "kill_service"))

    def test_disabled_admin_is_denied(self):
        admin = self.add_admin(permission_all=True, enabled=False)
        self.assertFalse(
            rpc_authorization_checker(
                admin.id, "ResourceService", 0, "kill_service"))

    def test_permission_messaging_is_restricted_to_messaging_rpcs(self):
        admin = self.add_admin(permission_all=False, permission_messaging=True)
        self.assertTrue(
            rpc_authorization_checker(
                admin.id, "AdminWebServer", 0, "submissions_status"))
        self.assertFalse(
            rpc_authorization_checker(
                admin.id, "ResourceService", 0, "kill_service"))

    def test_authenticated_admin_is_restricted_to_authenticated_rpcs(self):
        admin = self.add_admin(permission_all=False, permission_messaging=False)
        self.assertTrue(
            rpc_authorization_checker(
                admin.id, "AdminWebServer", 0, "submissions_status"))
        self.assertFalse(
            rpc_authorization_checker(
                admin.id, "ResourceService", 0, "kill_service"))


if __name__ == "__main__":
    unittest.main()
