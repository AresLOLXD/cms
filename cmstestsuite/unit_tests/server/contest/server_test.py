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

"""Tests for ContestWebServer construction.

"""

import unittest

from cms.server.contest.server import ContestWebServer


class TestConstruction(unittest.TestCase):
    """ContestWebServer must be constructible without raising.

    Regression test for an AttributeError that used to be raised at
    startup (ContestWebServer.__init__ tried to wrap self.wsgi_app,
    an attribute WebService no longer has since its native-Tornado
    rewrite) -- see this sub-project's final-review fix round 1.

    """

    def test_constructs_without_raising(self):
        server = ContestWebServer(0)
        self.assertIsNone(server.contest_id)


if __name__ == "__main__":
    unittest.main()
