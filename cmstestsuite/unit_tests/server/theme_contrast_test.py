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

import colorsys
import importlib.resources
import re
import unittest

from cmstestsuite.unit_tests.server.themecss import AWS_CSS, CWS_CSS, \
    RWS_CSS, dark_set, light_set


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


def blend(hsl_components: str, opacity: str, base: str) -> str:
    """Return the #rrggbb of a translucent hsla color over an opaque base.

    hsl_components: "H, S%, L%", as the score tokens are written.
    opacity: the alpha token value.

    """
    hue, saturation, lightness = (
        float(part.strip().rstrip("%")) for part in hsl_components.split(","))
    color = colorsys.hls_to_rgb(hue / 360, lightness / 100, saturation / 100)
    alpha = float(opacity)
    return "#" + "".join(
        "%02x" % round(255 * (alpha * c + (1 - alpha) * int(base[i:i + 2], 16)
                              / 255))
        for c, i in zip(color, (1, 3, 5)))


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


# CWS: pairs of the tokens of cws_style.css, checked in both sets. Bootstrap's
# own light colors are not tokens (about 800 of them) and are not checked.
CWS_PAIRS = [
    ("text", "surface", "page text, countdown and server time"),
    ("muted", "surface", "undefined scores, countdown labels"),
    ("hint", "surface", "subtask dependency note"),
    ("ended-link", "surface", "link of an ended contest"),
    ("text", "hover-bg", "hovered attachment"),
    ("announce-text", "announce-bg", "announcements, questions, messages"),
    ("outcome-undefined-text", "outcome-undefined-bg", "undefined outcome"),
    ("outcome-correct-text", "outcome-correct-bg", "correct outcome"),
    ("outcome-notcorrect-text", "outcome-notcorrect-bg",
     "not correct outcome"),
    ("outcome-partial-text", "outcome-partial-bg", "partially correct outcome"),
    ("outcome-skipped-text", "outcome-skipped-bg", "skipped testcase"),
    ("text", "subtask-bg", "subtask header"),
]

# CWS: pairs of the colors that only cws_dark.css uses (dark- tokens), so
# they are checked in the dark set only.
CWS_DARK_PAIRS = [
    ("dark-link", "surface", "links"),
    ("dark-link", "dark-panel", "links in wells and hero units"),
    ("dark-link", "dark-stripe", "links in striped rows"),
    ("dark-link", "hover-bg", "links in hovered rows and attachments"),
    ("dark-link-hover", "surface", "hovered links"),
    ("dark-link-hover", "hover-bg", "hovered links in hovered rows"),
    ("dark-navbar-text", "dark-navbar-bg", "navbar text and brand"),
    ("text", "dark-stripe", "striped table rows"),
    ("text", "hover-bg", "hovered table rows"),
    ("text", "dark-panel", "wells, hero units, code blocks, modal footers"),
    ("dark-code-text", "dark-panel", "inline code"),
    ("muted", "surface", "muted text, nav headers"),
    ("muted", "dark-stripe", "undefined scores in striped rows"),
    ("muted", "hover-bg", "undefined scores in hovered rows"),
    ("hint", "surface", "help blocks"),
    ("dark-field-text", "dark-field-bg", "form fields"),
    ("dark-field-text", "dark-field-off", "disabled form fields"),
    ("dark-placeholder", "dark-field-bg", "placeholders"),
    ("dark-error-text", "dark-field-bg", "fields with an error"),
    ("dark-error-text", "surface", "labels and help of fields with an error"),
    ("text", "dark-btn-bg", "plain button"),
    ("text", "dark-btn-hover", "plain button, hovered or disabled"),
    ("dark-btn-text", "dark-btn-bg", "labels"),
    ("dark-btn-text", "dark-primary", "primary button, active nav entry"),
    ("dark-btn-text", "dark-primary-hover", "primary button, hovered"),
    ("dark-btn-text", "dark-info", "info button"),
    ("dark-btn-text", "dark-info-hover", "info button, hovered"),
    ("dark-btn-text", "dark-warning", "warning button, unread counter"),
    ("dark-btn-text", "dark-warning-hover", "warning button, hovered"),
    ("dark-btn-text", "dark-danger", "danger button"),
    ("dark-btn-text", "dark-danger-hover", "danger button, hovered"),
    ("dark-btn-text", "dark-success", "success button"),
    ("dark-btn-text", "dark-success-hover", "success button, hovered"),
    ("dark-warn-text", "dark-warn-bg", "alert"),
    ("dark-error-text", "dark-error-bg", "error alert"),
    ("dark-success-text", "dark-success-bg", "success alert"),
]

# CWS: the translucent score cells are drawn over the page (and, in dark,
# over the striped rows).
# shortcut: in light the rows are Bootstrap's (#f9f9f9 stripes, #f5f5f5 hover,
# not tokens), so the cells are checked over the page only; add them if the
# light rows ever become tokens.
CWS_SCORE_HUES = ("score-0", "score-0-100", "score-100")
CWS_SCORE_OPACITIES = ("alpha-public", "alpha-public-hover", "alpha-total",
                       "alpha-total-hover")


class TestCwsContrast(unittest.TestCase):

    def setUp(self):
        css = CWS_CSS.read_text(encoding="utf-8")
        self.sets = {"light": light_set(css), "dark": dark_set(css)}

    def test_light_is_in_the_dark_set_and_the_rest_is_dark_only(self):
        # Not a Scenario of its own: a token missing from the dark set would
        # make the contrast check below meaningless for it.
        light, dark = set(self.sets["light"]), set(self.sets["dark"])
        self.assertGreater(len(light), 0)
        self.assertEqual({t for t in dark - light if not t.startswith("dark-")},
                         set())
        self.assertEqual(light - dark, set())

    def test_every_pair_passes_in_both_themes(self):
        # Scenario: Contrast in every theme (CWS)
        failures = []

        def check(theme, what, foreground, background):
            ratio = contrast(foreground, background)
            if ratio < MIN_RATIO:
                failures.append("%s: %s is %.2f" % (theme, what, ratio))

        for theme, tokens in self.sets.items():
            pairs = CWS_PAIRS + (CWS_DARK_PAIRS if theme == "dark" else [])
            for foreground, background, what in pairs:
                check(theme, "%s (%s on %s)" % (what, foreground, background),
                      tokens[foreground], tokens[background])
            bases = ["surface"] + (["dark-stripe"] if theme == "dark" else [])
            for hue in CWS_SCORE_HUES:
                for opacity in CWS_SCORE_OPACITIES:
                    for base in bases:
                        check(theme, "text on %s at %s over %s" % (
                            hue, opacity, base), tokens["score-text"],
                            blend(tokens[hue], tokens[opacity], tokens[base]))
        self.assertEqual(failures, [])


# RWS: foreground token, background token, what it is. "@page" is the body,
# a gradient from page-grad-top to page-grad-bottom over page-bg: the pair is
# checked against the three. The scoreboard text sits on the page, whose
# tints are not tokens of their own.
RWS_PAGE = ("page-bg", "page-grad-top", "page-grad-bottom")
RWS_PAIRS = [
    ("text", "@page", "body text, names, time"),
    ("text", "panel-bg", "upper panel, side panel, open time selector"),
    ("option-text", "panel-bg", "options of the time selector"),
    ("text", "option-selected-bg", "selected option"),
    ("text", "option-hover-bg", "hovered option"),
    ("field-text", "field-bg", "search box"),
    ("text", "dialog-bg", "team search and user detail"),
    ("text", "strong-bg", "team items, global rows, submissions header"),
    ("text", "mid-bg", "contest rows, even rows, buttons"),
    ("text", "odd-bg", "odd submission rows"),
    ("text", "button-active-bg", "pressed button"),
    ("text", "cell-bg", "scoreboard header, tasks toggle"),
    ("text", "cell-hover-bg", "hovered tasks toggle"),
    ("text", "name-hover-bg", "hovered name of a sticky column"),
    ("footer-text", "@page", "Powered by (it hangs under the side panel)"),
    ("link", "@page", "link of the footer"),
    ("status-ok-text", "status-ok-bg", "connected"),
    ("status-wait-text", "status-wait-bg", "reconnecting"),
    ("status-bad-text", "status-bad-bg", "disconnected, outdated, error"),
    ("chart-text", "chart-bg", "user detail charts"),
    ("chart-text", "overview-bg", "overview"),
    ("banner-text", "banner-bg", "staff and public banner"),
]
RWS_SCORE_CLASSES = ("score-0-10", "score-10-20", "score-20-30",
                     "score-30-40", "score-40-50", "score-50-60",
                     "score-60-70", "score-70-80", "score-80-90",
                     "score-90-100", "score-100")
# td.score has opacity 0.75, except hovered, in a selected row and the sort
# column. The animations draw on td.rank, up to 0.6 and, in a selected row, 1.
RWS_SCORE_OPACITY = 0.75
RWS_ANIMATION_PEAKS = (0.6, 1)
# The notice pages of visibility.py carry their own tokens.
RWS_NOTICE_PAIRS = [
    ("text", "page-bg", "text on the page"),
    ("text", "card-bg", "text on the card"),
    ("error", "card-bg", "wrong password"),
]
NOTICE_SOURCE = importlib.resources.files("cmsranking").joinpath(
    "visibility.py")


def composite(color: str, alpha: float, base: str) -> str:
    """Return the #rrggbb of a #rrggbb color at an opacity over a base."""
    return "#" + "".join(
        "%02x" % round(alpha * int(color[i:i + 2], 16)
                       + (1 - alpha) * int(base[i:i + 2], 16))
        for i in (1, 3, 5))


def rgba_over(value: str, base: str) -> str:
    """Return a rgba(r, g, b, a) token composited over a #rrggbb base."""
    red, green, blue, alpha = re.fullmatch(
        r"rgba\((\d+), (\d+), (\d+), ([\d.]+)\)", value).groups()
    return composite("#%02x%02x%02x" % (int(red), int(green), int(blue)),
                     float(alpha), base)


def hsl_hex(value: str) -> str:
    """Return the #rrggbb of an hsl(H, S%, L%) token."""
    return blend(re.fullmatch(r"hsl\((.*)\)", value).group(1), "1", "#000000")


def rgb_hex(channels: str) -> str:
    """Return the #rrggbb of the "r, g, b" of the up and down tokens."""
    return "#%02x%02x%02x" % tuple(int(c) for c in channels.split(","))


class TestRwsContrast(unittest.TestCase):

    def setUp(self):
        css = RWS_CSS.read_text(encoding="utf-8")
        self.sets = {"light": light_set(css), "dark": dark_set(css)}

    def test_light_and_dark_define_the_same_tokens(self):
        # Not a Scenario of its own: a token missing from one set would make
        # the contrast check below meaningless for it.
        self.assertEqual(set(self.sets["light"]), set(self.sets["dark"]))
        self.assertGreater(len(self.sets["light"]), 0)

    def test_every_pair_passes_in_both_themes(self):
        # Scenario: Contrast in every theme (RWS)
        failures = []

        def check(theme, what, foreground, background):
            ratio = contrast(foreground, background)
            if ratio < MIN_RATIO:
                failures.append("%s: %s is %.2f" % (theme, what, ratio))

        for theme, tokens in self.sets.items():
            pages = [tokens[name] for name in RWS_PAGE]
            for foreground, background, what in RWS_PAIRS:
                for name in (RWS_PAGE if background == "@page"
                             else (background,)):
                    check(theme, "%s (%s on %s)" % (what, foreground, name),
                          tokens[foreground], tokens[name])
            for page in pages:
                # Hovered rows tint the cells of the name and the user.
                check(theme, "text on a hovered row over %s" % page,
                      tokens["text"], rgba_over(tokens["row-hover"], page))
                # Zero scores and score cells, at the opacity of td.score.
                text = composite(tokens["text"], RWS_SCORE_OPACITY, page)
                check(theme, "zero score over %s" % page, composite(
                    tokens["score-zero"], RWS_SCORE_OPACITY, page),
                    composite(page, RWS_SCORE_OPACITY, page))
                check(theme, "zero score, full opacity, over %s" % page,
                      tokens["score-zero"], page)
                for name in RWS_SCORE_CLASSES:
                    cell = hsl_hex(tokens[name])
                    check(theme, "text on %s over %s" % (name, page), text,
                          composite(cell, RWS_SCORE_OPACITY, page))
                    check(theme, "text on %s, full opacity" % name,
                          tokens["text"], cell)
                for name in ("up", "down"):
                    color = rgb_hex(tokens[name])
                    for peak in RWS_ANIMATION_PEAKS:
                        check(theme, "text on the %s animation at %s over %s"
                              % (name, peak, page), tokens["text"],
                              composite(color, peak, page))
        self.assertEqual(failures, [])

    def test_the_notice_pages_pass_in_both_themes(self):
        # Scenario: Contrast in every theme (RWS notice pages)
        # (their tokens are in a constant of visibility.py: a hidden group
        # serves no stylesheet)
        style = re.search(r'(?s)NOTICE_THEME = """(.*?)"""',
                          NOTICE_SOURCE.read_text(encoding="utf-8")).group(1)
        sets = {"light": light_set(style), "dark": dark_set(style)}
        self.assertEqual(set(sets["light"]), set(sets["dark"]))
        failures = []
        for theme, tokens in sets.items():
            for foreground, background, what in RWS_NOTICE_PAIRS:
                ratio = contrast(tokens[foreground], tokens[background])
                if ratio < MIN_RATIO:
                    failures.append("%s: %s (%s on %s) is %.2f" % (
                        theme, what, foreground, background, ratio))
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
