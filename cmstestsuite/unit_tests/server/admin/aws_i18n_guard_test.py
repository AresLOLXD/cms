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

"""Guards of the AWS translation work (change aws-language-rest).

They need no database. Regenerate the English snapshot with
"python -m cmstestsuite.unit_tests.server.admin.aws_render" only on a
tree whose English is the reference.

"""

import difflib
import json

from cmstestsuite.unit_tests.server.admin import aws_render


def _diff(name, mode, kind, old, new):
    return [f"{name} ({mode}) {kind}: {line}" for line in difflib.unified_diff(
        [str(x) for x in old], [str(x) for x in new], "snapshot", "now",
        lineterm="", n=0)]


def test_english_render_unchanged():
    # Scenario "A page in English is unchanged" (aws-language): the
    # visible texts and the posted values of every page, in English,
    # are those of the snapshot taken before any template changed.
    snapshot = json.loads(aws_render.SNAPSHOT.read_text(encoding="utf-8"))
    assert sorted(snapshot) == aws_render.PAGES

    problems = []
    for name, modes in snapshot.items():
        for mode, old in modes.items():
            new = aws_render.snapshot_page(name, mode == "full")
            for kind in ("error", "texts", "posts"):
                if old.get(kind) != new.get(kind):
                    problems += _diff(name, mode, kind,
                                      old.get(kind, []), new.get(kind, []))
    assert not problems, "English changed:\n" + "\n".join(problems[:60])
