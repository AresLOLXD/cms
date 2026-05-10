#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2014 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2020 Andrey Vihrov <andrey.vihrov@gmail.com>
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

"""Tests for general utility functions."""

import netifaces
import os
import sys
import tempfile
import unittest
import unittest.mock
from unittest.mock import Mock

import cms.util
from cms import Address, ServiceCoord, \
    get_safe_shard, get_service_address, get_service_shards, rmtree
from cms.util import contest_id_from_args


fake_async_config = {
    ServiceCoord("Service", 0): Address("0.0.0.0", 0),
    ServiceCoord("Service", 1): Address("0.0.0.1", 1),
}


def _set_up_async_config(restore=False):
    """Fake the async config."""
    if not restore:
        if not hasattr(_set_up_async_config, "original"):
            _set_up_async_config.original = cms.config.services
        cms.config.services = fake_async_config
    else:
        cms.config.services = _set_up_async_config.original


def _set_up_ip_addresses(addresses=None, restore=False):
    """Instruct the netifaces module to return the specific ips."""
    if not restore:
        if not hasattr(_set_up_ip_addresses, "original"):
            _set_up_ip_addresses.original = \
                (netifaces.interfaces, netifaces.ifaddresses)
        dict_addresses = {
            netifaces.AF_INET: [{"addr": address} for address in addresses]}
        netifaces.interfaces = Mock(return_value="eth0")
        netifaces.ifaddresses = Mock(return_value=dict_addresses)
    else:
        netifaces.interfaces, netifaces.ifaddresses = \
            _set_up_ip_addresses.original


class TestGetSafeShard(unittest.TestCase):
    """Test the function cms.util.get_safe_shard."""
    def setUp(self):
        """Set up the default mocks."""
        _set_up_async_config()
        _set_up_ip_addresses(["1.1.1.1", "0.0.0.1"])

    def tearDown(self):
        """Restore the mocks to ensure normal operations."""
        _set_up_async_config(restore=True)
        _set_up_ip_addresses(restore=True)

    def test_success(self):
        """Test success cases.

        This tests for both giving explicitly the shard number, and
        for autodetecting it.

        """
        self.assertEqual(get_safe_shard("Service", 0), 0)
        self.assertEqual(get_safe_shard("Service", 1), 1)
        self.assertEqual(get_safe_shard("Service", None), 1)

    def test_shard_not_present(self):
        """Test failure when the given shard is not in the config."""
        with self.assertRaises(ValueError):
            get_safe_shard("Service", 2)

    def test_service_not_present(self):
        """Test failure when the given service is not in the config."""
        with self.assertRaises(ValueError):
            get_safe_shard("ServiceNotPresent", 0)

    def test_no_autodetect(self):
        """Test failure when no shard is given and autodetect fails."""
        # Setting up non-matching IPs.
        _set_up_ip_addresses(["1.1.1.1", "0.0.0.2"])
        with self.assertRaises(ValueError):
            get_safe_shard("Service", None)


class TestGetServiceAddress(unittest.TestCase):
    """Test the function cms.util.get_service_address.

    """
    def setUp(self):
        """Set up the default mocks."""
        _set_up_async_config()

    def tearDown(self):
        """Restore the mocks to ensure normal operations."""
        _set_up_async_config(restore=True)

    def test_success(self):
        """Test success cases."""
        self.assertEqual(
            get_service_address(ServiceCoord("Service", 0)),
            Address("0.0.0.0", 0))
        self.assertEqual(
            get_service_address(ServiceCoord("Service", 1)),
            Address("0.0.0.1", 1))

    def test_shard_not_present(self):
        """Test failure when the shard of the service is invalid."""
        with self.assertRaises(KeyError):
            get_service_address(ServiceCoord("Service", 2))

    def test_service_not_present(self):
        """Test failure when the service is invalid."""
        with self.assertRaises(KeyError):
            get_service_address(ServiceCoord("ServiceNotPresent", 0))


class TestGetServiceShards(unittest.TestCase):
    """Test the function cms.util.get_service_shards.

    """
    def setUp(self):
        """Set up the default mocks."""
        _set_up_async_config()

    def tearDown(self):
        """Restore the mocks to ensure normal operations."""
        _set_up_async_config(restore=True)

    def test_success(self):
        """Test success cases."""
        self.assertEqual(get_service_shards("Service"), 2)
        self.assertEqual(get_service_shards("ServiceNotPresent"), 0)


class TestRmtree(unittest.TestCase):
    """Test the function cms.util.rmtree.

    """
    def setUp(self):
        """Set up temporary directory."""
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        """Remove temporary directory."""
        os.rmdir(self.tmpdir)

    def test_success(self):
        """Test success case."""
        testdir = os.path.join(self.tmpdir, "test")
        os.makedirs(os.path.join(testdir, "a"))
        os.makedirs(os.path.join(testdir, "b", "c"))
        open(os.path.join(testdir, "x"), "w").close()
        os.symlink("foo", os.path.join(testdir, "a", "y"))
        os.symlink(self.tmpdir, os.path.join(testdir, "b", "z"))

        rmtree(testdir)
        self.assertFalse(os.path.exists(testdir))
        self.assertTrue(os.path.isdir(self.tmpdir))

    def test_symlink(self):
        """Test failure on a symlink."""
        link = os.path.join(self.tmpdir, "link")
        os.symlink(self.tmpdir, link)

        with self.assertRaises(NotADirectoryError):
            rmtree(link)

        os.remove(link)

    def test_missing(self):
        """Test failure on a missing directory."""
        with self.assertRaises(FileNotFoundError):
            rmtree(os.path.join(self.tmpdir, "missing"))


class TestContestIdFromArgs(unittest.TestCase):
    """Test contest_id_from_args env-var fallback and non-TTY behavior."""

    def setUp(self):
        # Patch is_contest_id to always return True so we don't need a DB.
        patcher = unittest.mock.patch("cms.db.is_contest_id", return_value=True)
        self.mock_is_contest_id = patcher.start()
        self.addCleanup(patcher.stop)

    def _make_ask(self):
        """Return a mock ask_contest that must NOT be called."""
        m = unittest.mock.Mock(side_effect=AssertionError("ask_contest should not be called"))
        return m

    # ── existing -c flag behaviour (must not regress) ─────────────────────

    def test_explicit_flag_uses_flag_value(self):
        """When -c 23 is passed, return 23 without touching env or ask."""
        result = contest_id_from_args("23", self._make_ask())
        self.assertEqual(result, 23)

    def test_all_flag_returns_none(self):
        """When -c ALL is passed, return None (multi-contest mode)."""
        result = contest_id_from_args("ALL", self._make_ask())
        self.assertIsNone(result)

    def test_invalid_flag_exits(self):
        """When -c has a non-integer value, sys.exit(1) is called."""
        with self.assertRaises(SystemExit):
            contest_id_from_args("notanumber", self._make_ask())

    # ── new env-var fallback ──────────────────────────────────────────────

    def test_env_var_used_when_flag_absent(self):
        """When -c is absent but CMS_CONTEST_ID=23 is set, return 23."""
        with unittest.mock.patch.dict(os.environ, {"CMS_CONTEST_ID": "23"}):
            result = contest_id_from_args(None, self._make_ask())
        self.assertEqual(result, 23)

    def test_env_var_invalid_exits(self):
        """When CMS_CONTEST_ID is not an integer, sys.exit(1) is called."""
        with unittest.mock.patch.dict(os.environ, {"CMS_CONTEST_ID": "bad"}):
            with self.assertRaises(SystemExit):
                contest_id_from_args(None, self._make_ask())

    # ── non-TTY fail-fast ─────────────────────────────────────────────────

    def test_no_env_no_tty_exits(self):
        """When -c absent, no env var, and stdin is not a TTY, sys.exit(1)."""
        env = {k: v for k, v in os.environ.items() if k != "CMS_CONTEST_ID"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            with unittest.mock.patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                with self.assertRaises(SystemExit):
                    contest_id_from_args(None, self._make_ask())

    def test_no_env_with_tty_calls_ask(self):
        """When -c absent, no env var, but stdin IS a TTY, ask_contest is called."""
        ask = unittest.mock.Mock(return_value=5)
        env = {k: v for k, v in os.environ.items() if k != "CMS_CONTEST_ID"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            with unittest.mock.patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                result = contest_id_from_args(None, ask)
        self.assertEqual(result, 5)
        ask.assert_called_once()


if __name__ == "__main__":
    unittest.main()
