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

"""The English snapshot of the AWS pages (change aws-language-rest).

Two layers, so that "English unchanged" does not depend on which
branches the stand-ins of aws_render reach:

- "rendered": what each page shows, posts and runs, in each mode;
- "static": the texts of each template (and static script) as written,
  where a trans block or a gettext call counts as its message, so that
  wrapping a text leaves the record as it was and changing it does not.

Regenerate it with "python -m
cmstestsuite.unit_tests.server.admin.aws_snapshot" only on a tree whose
English is the reference (main 676a397b); it is temporary, the work of
aws-language-rest.

"""

import json
from pathlib import Path

from cms.locale import DEFAULT_TRANSLATION, Translation
from cmstestsuite.unit_tests.server.admin import aws_i18n_lint as lint
from cmstestsuite.unit_tests.server.admin import aws_render


SNAPSHOT = Path(__file__).parent / "data" / "aws_english_snapshot.json"


def rendered_page(name: str, mode: str,
                  translation: Translation = DEFAULT_TRANSLATION) -> dict:
    """Return what the page shows and posts in the mode, as plain data
    (or {"error": ...} when it does not render)."""
    try:
        html = aws_render.render(
            aws_render.environment(mode), name, translation)
    except Exception as error:
        return {"error": type(error).__name__}
    extractor = aws_render.extract_all(html)
    return {
        "texts": lint.records(extractor),
        "posts": sorted([n, v] for n, v in extractor.posts),
    }


def build_snapshot() -> dict:
    """Return the English snapshot of every page, in both layers."""
    return {
        "rendered": {name: {mode: rendered_page(name, mode)
                            for mode in aws_render.MODES}
                     for name in aws_render.PAGES},
        "static": {rel: lint.static_records(rel)
                   for rel in lint.TEMPLATE_FILES + lint.SCRIPT_FILES},
    }


def dumps(snapshot: dict) -> str:
    """Return the snapshot as json with one record per line."""
    return json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    import sys
    SNAPSHOT_OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else SNAPSHOT
    SNAPSHOT_OUT.write_text(dumps(build_snapshot()), encoding="utf-8")
