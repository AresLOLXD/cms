#!/usr/bin/env python3

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

"""The checker of the Spanish catalog finds what it promises."""

import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[4] / "openspec" / "changes" \
    / "aws-language-rest" / "translation" / "check.py"


def test_the_checker_passes_its_selftest():
    # shortcut: the checker lives in the change, so the test skips once
    # the change is archived (the file moves); move both then if kept.
    if not CHECKER.is_file():
        pytest.skip("the change aws-language-rest is archived")

    result = subprocess.run(
        [sys.executable, "-I", str(CHECKER), "--selftest"],
        capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "selftest ok" in result.stdout
