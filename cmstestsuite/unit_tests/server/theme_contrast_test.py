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

"""WCAG AA contrast of the color pairs of the light and dark themes.

Each server declares its pairs (foreground token, background token, what it
is) next to the token names, so a token added without a pair is visible in
review. Only the tokens are checked: nothing here renders a page.

"""

import unittest

from cmstestsuite.unit_tests.server.themecss import AWS_CSS, dark_set, \
    light_set


MIN_RATIO = 4.5


def luminance(color: str) -> float:
    """Return the WCAG relative luminance of a #rrggbb color."""
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    red, green, blue = (
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first: str, second: str) -> float:
    lighter, darker = sorted((luminance(first), luminance(second)),
                             reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# AWS: (foreground token, background token, what it is). Borders and
# decorations are not text and are left out. Native form controls follow
# color-scheme and have no token to check.
AWS_PAIRS = [
    ("text", "page-bg", "body text"),
    ("text", "surface", "text on even rows and table headers"),
    ("text", "tint", "sidebar text, odd rows"),
    ("text", "note-bg", "notification text"),
    ("text", "note-strong", "close button of a notification"),
    ("text", "answered-bg", "answered question"),
    ("text", "flagged-bg", "flagged activity row"),
    ("text", "box-bg", "export and import box"),
    ("text", "error-bg", "export and import error box"),
    ("text", "warn-bg", "export and import warning box"),
    ("text", "done-bg", "export and import done box"),
    ("accent", "page-bg", "headings"),
    ("accent", "tint", "sidebar headings"),
    ("accent", "surface", "headings in white areas"),
    ("link", "page-bg", "links"),
    ("link", "tint", "sidebar links, small badges"),
    ("link", "surface", "hovered menu entry"),
    ("link", "note-bg", "links in notifications"),
    ("link", "answered-bg", "links in answered questions"),
    ("danger", "page-bg", "error and archive banners"),
    ("danger", "tint", "secret key notice in the sidebar"),
    ("hint", "page-bg", "hints"),
    ("hint", "surface", "hints in even rows"),
    ("hint", "tint", "hints in odd rows"),
    ("ignored-text", "surface", "ignored question"),
    ("badge-text", "badge-bg", "unread counter"),
    ("chart-text", "chart-bg", "light panel of the charts"),
    ("code-text", "code-bg", "code"),
    ("code-text", "code-selection", "selected code"),
    ("code-comment", "code-bg", "code comments"),
    ("code-punct", "code-bg", "code punctuation, line numbers"),
    ("code-number", "code-bg", "code numbers and tags"),
    ("code-string", "code-bg", "code strings"),
    ("code-operator", "code-bg", "code operators"),
    ("code-keyword", "code-bg", "code keywords"),
    ("code-function", "code-bg", "code functions"),
    ("code-important", "code-bg", "code regular expressions"),
]


class TestAwsContrast(unittest.TestCase):

    def setUp(self):
        css = AWS_CSS.read_text(encoding="utf-8")
        self.sets = {"light": light_set(css), "dark": dark_set(css)}

    def test_light_and_dark_define_the_same_tokens(self):
        # Not a Scenario of its own: a token missing from one set would make
        # the contrast check below meaningless for it.
        self.assertEqual(set(self.sets["light"]), set(self.sets["dark"]))
        self.assertGreater(len(self.sets["light"]), 0)

    def test_every_pair_passes_in_both_themes(self):
        # Scenario: Contrast in every theme (AWS)
        failures = []
        for theme, tokens in self.sets.items():
            for foreground, background, what in AWS_PAIRS:
                ratio = contrast(tokens[foreground], tokens[background])
                if ratio < MIN_RATIO:
                    failures.append("%s: %s (%s on %s) is %.2f" % (
                        theme, what, foreground, background, ratio))
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
