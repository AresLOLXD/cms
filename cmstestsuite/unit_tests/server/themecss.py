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

"""Helpers to read the theme tokens of the stylesheets (not a test module)."""

import importlib.resources
import re


# Resolved from the "cms" package: naming cms.server.admin here would import
# it (and tornado, asyncio...) into the gevent process of the ranking tests.
AWS_CSS = importlib.resources.files("cms").joinpath(
    "server", "admin", "static", "aws_style.css")
CWS_STATIC = importlib.resources.files("cms").joinpath(
    "server", "contest", "static")
CWS_CSS = CWS_STATIC.joinpath("cws_style.css")
RWS_CSS = importlib.resources.files("cmsranking").joinpath(
    "static", "Ranking.css")

DARK_MEDIA = ':root:not([data-theme="light"])'
DARK_ATTRIBUTE = ':root[data-theme="dark"]'

# A color written as a literal: hex, a color name, or a color function.
LITERAL_COLOR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b"
    r"|\b(?:white|black|red|green|blue|grey|gray|yellow|orange|purple)"
    r"(?![-\w])"
    r"|\b(?:rgb|rgba|hsl|hsla)\(")


def read_block(css: str, selector: str) -> dict[str, str]:
    """Return the custom properties declared in the blocks of a selector.

    css: the stylesheet text.
    selector: the exact selector text that opens the block (the block
        inside a media query is found by its own, indented, selector).
    return: token name (without "--") -> value, for every block found.

    """
    tokens: dict[str, str] = {}
    for match in re.finditer(
            r"^\s*%s \{(.*?)\}" % re.escape(selector), css, re.M | re.S):
        tokens.update(re.findall(r"--([\w-]+):\s*([^;]+);", match.group(1)))
    return tokens


def light_set(css: str) -> dict[str, str]:
    return read_block(css, ":root")


def dark_set(css: str) -> dict[str, str]:
    return read_block(css, ':root[data-theme="dark"]')


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
