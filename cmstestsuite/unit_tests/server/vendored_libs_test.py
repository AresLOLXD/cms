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

"""Tests for the third-party libraries vendored in the static directories.

The table below is the inventory of those libraries: updating one is a
one-line change here, and a page that loads a script, a stylesheet or an
image from another host (a CDN) fails the second test.

"""

import importlib.util
import re
import unittest
from pathlib import Path


# Found without importing cmsranking: it is a gevent package and this test
# runs in the asyncio group. cms and cmsranking sit side by side both in the
# source tree and in site-packages.
ROOT = Path(importlib.util.find_spec("cms").origin).parent.parent

# path -> (version, license, text of the file that proves the version).
# shortcut: the jqPlot stylesheet is not listed (it has no header), and
# bootstrap.js carries no version, so its entry checks only the 2012
# Twitter copyright of the 2.0.x line; update both by hand with the others.
VENDORED = {
    "cms/server/static/jq/jquery-3.7.1.min.js":
        ("3.7.1", "MIT", "jQuery v3.7.1"),
    "cmsranking/static/lib/jquery.js":
        ("3.7.1", "MIT", "jQuery v3.7.1"),
    "cms/server/static/jq/jquery-migrate-3.3.2.min.js":
        ("3.3.2", "MIT", "jQuery Migrate v3.3.2"),
    "cms/server/contest/static/css/bootstrap.css":
        ("2.0.4", "Apache-2.0", "Bootstrap v2.0.4"),
    "cms/server/contest/static/js/bootstrap.js":
        ("2.0.4", "Apache-2.0", "Copyright 2012 Twitter"),
    "cms/server/admin/static/jq/jquery.jqplot.min.js":
        ("1.0.9", "MIT or GPL-2.0", "jqplot 1.0.9"),
    "cms/server/admin/static/jq/jqplot.dateAxisRenderer.min.js":
        ("1.0.9", "MIT or GPL-2.0", "jqplot 1.0.9"),
    "cms/server/admin/static/jq/jqplot.enhancedLegendRenderer.min.js":
        ("1.0.9", "MIT or GPL-2.0", "jqplot 1.0.9"),
    "cms/server/admin/static/prism.js":
        ("1.30.0", "MIT", "PrismJS 1.30.0"),
    "cms/server/admin/static/prism.css":
        ("1.30.0", "MIT", "PrismJS 1.30.0"),
    "cmsranking/static/lib/raphael.js":
        ("2.3.0", "MIT", 'e.version="2.3.0"'),
}

# Every page the three web servers can serve.
PAGE_FILES = (
    list((ROOT / "cms/server/admin/templates").rglob("*.html"))
    + list((ROOT / "cms/server/contest/templates").rglob("*.html"))
    + [ROOT / "cmsranking/static/Ranking.html"])

# A tag that fetches a resource, whose URL has a host (absolute or "//").
REMOTE_RESOURCE = re.compile(
    r"""<(?:script|link|img|iframe|source|embed)\b[^>]*?"""
    r"""\b(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", re.I | re.S)


class VendoredLibsTest(unittest.TestCase):

    def test_versions(self):
        for path, (version, _license, marker) in VENDORED.items():
            with self.subTest(path=path):
                text = (ROOT / path).read_text(encoding="utf-8")
                # Not assertIn: it would print the whole library on failure.
                self.assertTrue(marker in text, "%s is not version %s" % (
                    path, version))

    def test_pages_load_nothing_from_another_host(self):
        self.assertGreater(len(PAGE_FILES), 10)
        for page in PAGE_FILES:
            with self.subTest(page=str(page.relative_to(ROOT))):
                self.assertIsNone(REMOTE_RESOURCE.search(
                    page.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
