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

"""Tests for the RWS configuration defaults."""

import os
import tempfile
import unittest

from cmsranking.Config import load_config


class TestPublicConfigDefaults(unittest.TestCase):

    def test_username_column_is_shown_without_a_public_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = os.path.join(tmp, "cms_ranking.toml")
            with open(config_file, "w") as f:
                f.write('log_dir = "%s"\nlib_dir = "%s"\n'
                        % (os.path.join(tmp, "log"), os.path.join(tmp, "lib")))

            config = load_config(config_file)

        self.assertTrue(config.public.show_id_column)


if __name__ == "__main__":
    unittest.main()
