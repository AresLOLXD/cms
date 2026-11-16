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

"""The partition of the files among the lanes of aws-language-rest."""

import glob
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cmstestsuite.unit_tests.server.admin import aws_i18n_lint as lint

ROOT = Path(__file__).resolve().parents[4]
TOOLS = ROOT / "openspec" / "changes" / "aws-language-rest" / "tools"

# shortcut: the tools live in the change, so the tests skip once the
# change is archived (the files move); move both then if kept.
pytestmark = pytest.mark.skipif(
    not (TOOLS / "lanes.json").is_file(),
    reason="the change aws-language-rest is archived")


def _data():
    return json.loads((TOOLS / "lanes.json").read_text(encoding="utf-8"))


def test_every_file_of_the_partition_exists():
    data = _data()
    missing = [path for lane in data["lanes"].values()
               for path in lane["files"] if not (ROOT / path).is_file()]
    missing += [path for path in data["no_lane"]
                if not (ROOT / path).is_file()]
    # The existing tests that a lane may edit, and a plan, may be a pattern.
    missing += [pattern for lane in data["lanes"].values()
                for pattern in lane["tests"]
                if not glob.glob(str(ROOT / pattern))]

    assert not missing, f"files of lanes.json that do not exist: {missing}"


def test_every_file_with_texts_has_exactly_one_owner():
    data = _data()
    owners = {}
    for name, lane in data["lanes"].items():
        for path in lane["files"]:
            owners.setdefault(path, []).append(name)
    twice = {path: lanes for path, lanes in owners.items() if len(lanes) > 1}
    no_lane = set(data["no_lane"])
    unowned = [
        rel for rel in lint.ALL_FILES
        if "cms/server/admin/" + rel not in owners
        and "cms/server/admin/" + rel not in no_lane
        and not rel.endswith("/__init__.py") and rel != "templates/ranking.txt"
        and not rel.startswith("static/jq")]

    assert not twice, f"files in two lanes: {twice}"
    assert not unowned, f"files that no lane owns: {unowned}"


def test_the_lane_check_passes_its_selftest():
    result = subprocess.run(
        [sys.executable, "-I", str(TOOLS / "check_lane.py"), "--selftest"],
        capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "selftest ok" in result.stdout


def test_the_lane_test_files_are_the_real_ones():
    names = {lane: Path(data["test"]).name
             for lane, data in _data()["lanes"].items()}

    assert names == {
        "L0": "translated_chrome_test.py", "L1": "translated_contest_test.py",
        "L2": "translated_task_test.py", "L3": "translated_dataset_test.py",
        "L4": "translated_submissions_test.py",
        "L5": "translated_users_test.py",
        "L6": "translated_participations_test.py",
        "L7": "translated_system_test.py"}
