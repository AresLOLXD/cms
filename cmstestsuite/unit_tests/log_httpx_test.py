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

"""Tests that httpx request lines (which carry the bot token) are not logged."""

import logging
import unittest

import cms.log


class ListHandler(logging.Handler):
    """Collect the records that reach it."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class TestHttpxLogging(unittest.TestCase):

    def setUp(self):
        self.handler = ListHandler()
        cms.log.root_logger.addHandler(self.handler)
        self.addCleanup(cms.log.root_logger.removeHandler, self.handler)

    def test_info_is_dropped_but_warning_is_kept(self):
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).info("POST https://x/bot<TOKEN>/getUpdates")
            logging.getLogger(name).warning("kept")
        self.assertEqual([r.getMessage() for r in self.handler.records],
                         ["kept", "kept"])


if __name__ == "__main__":
    unittest.main()
