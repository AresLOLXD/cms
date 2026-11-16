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

"""Render every AWS page template without a database, for the guards.

The data a page reads is replaced by a stand-in (``Anything``) that
prints as a symbol, is true and iterates over two items ("full" mode),
or is false and iterates over nothing ("empty" mode). Two things are
extracted from the rendered html: the visible texts and the (name,
value) pairs that the named controls would post.

"""

import functools
import json
import logging
import re
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, Undefined
from markupsafe import Markup

from cms.locale import DEFAULT_TRANSLATION, Translation
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT


TEMPLATES = Path(__file__).resolve().parents[4] \
    / "cms" / "server" / "admin" / "templates"
# Marks the two ends of a message in the pseudo-localization.
OPEN, CLOSE = "⟦", "⟧"
# What a datum that the page did not get prints as (no letters).
SYMBOL = "§"
# A word of two letters or more.
WORD = re.compile(r"[^\W\d_]{2,}")

# Pages are the templates that are not reached through another page.
ALL_TEMPLATES = sorted(
    str(f.relative_to(TEMPLATES)) for f in TEMPLATES.rglob("*.html"))
PAGES = [name for name in ALL_TEMPLATES
         if not name.startswith(("macro/", "fragments/"))]


class Pseudo(Translation):
    """A translation that wraps every message in the two end marks."""

    def __init__(self):
        super().__init__("es")

    def gettext(self, msgid):
        return OPEN + msgid + CLOSE

    def ngettext(self, singular, plural, n):
        return OPEN + (singular if n == 1 else plural) + CLOSE


PSEUDO = Pseudo()


def make_undefined(full: bool) -> type[Undefined]:
    """Return the stand-in class for the data a page does not get."""

    class Anything(Undefined):
        """Stands for any datum: attribute, item, call, iteration."""
        __slots__ = ()

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return self

        def __getitem__(self, key):
            return self

        def __call__(self, *args, **kwargs):
            return self

        def __iter__(self):
            return iter([self, self] if full else [])

        def __str__(self):
            return SYMBOL

        def __bool__(self):
            return full

        def __len__(self):
            return 2 if full else 0

        def __int__(self):
            return 1

        def __float__(self):
            return 1.0

        def __index__(self):
            return 1

        def __html__(self):
            return SYMBOL

        def __lt__(self, other):
            return False

        __gt__ = __le__ = __ge__ = __lt__

        def __eq__(self, other):
            return False

        def __hash__(self):
            return 1

        def __abs__(self):
            return self

        __neg__ = __pos__ = __abs__

        def __add__(self, other):
            return self

        __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __add__
        __truediv__ = __floordiv__ = __mod__ = __rmod__ = __add__

    return Anything


def _total(fn: Callable) -> Callable:
    """Make a filter print a symbol instead of raising on a stand-in."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return SYMBOL
    return wrapper


def environment(full: bool) -> Environment:
    """Return the AWS environment with the stand-ins for "full" or "empty"."""
    # No cache: an overlay starts with a copy of its parent's, whose
    # templates keep the parent's environment and would ignore ours.
    env = AWS_ENVIRONMENT.overlay(
        undefined=make_undefined(full), cache_size=0)
    # An overlay shares the globals and filters of its parent: copy them.
    env.globals = dict(env.globals)
    env.filters = dict(env.filters)
    env.globals["clock_zones"] = lambda *args: []
    # The real one is random.
    env.globals["get_hex_random_key"] = lambda *args: "0000"
    env.filters["tojson"] = lambda value, *args, **kwargs: Markup(
        json.dumps(value, default=lambda obj: SYMBOL))
    for name, fn in list(env.filters.items()):
        if name not in ("safe", "e", "escape", "tojson", "forceescape",
                        "default", "d"):
            env.filters[name] = _total(fn)
    return env


@contextmanager
def _quiet_grading_log():
    """Hide the errors that the stand-ins make format_status_text log."""
    logger = logging.getLogger("cms.grading")
    level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(level)


def render(env: Environment, name: str,
           translation: Translation = DEFAULT_TRANSLATION) -> str:
    """Render the template with the stand-ins and the given translation."""
    params = dict(
        static_url=lambda *p: "/".join(p),
        url=lambda *p: "/" + "/".join(map(str, p)),
        xsrf_form_html="", timestamp=datetime(2026, 1, 1),
        translation=translation, gettext=translation.gettext,
        ngettext=translation.ngettext,
        available_translations={"es": translation},
        cookie_translation=None, automatic_translation=translation)
    with _quiet_grading_log():
        return env.get_template(name).render(**params)


def renders(page: str, translation: Translation = DEFAULT_TRANSLATION):
    """Yield the html of the page in each mode that renders.

    A page that raises in a mode is skipped here: the snapshot test
    pins which ones do (the stand-ins of that mode are not data the
    page can use).

    """
    for full in (True, False):
        try:
            yield render(environment(full), page, translation)
        except Exception:
            continue


def _normalize(text: str) -> str:
    return " ".join(text.split())


class _Extractor(HTMLParser):
    """Collect the visible texts and the posted pairs of a page."""

    VOID = ("meta", "link", "br", "hr", "input", "img", "col")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts: list[tuple[int, str, str]] = []
        self.posts: list[tuple[str, str]] = []
        self._stack: list[str] = []
        self._select: str | None = None
        self._option: list | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        line = self.getpos()[0]
        if tag not in self.VOID:
            self._stack.append(tag)
        for kind in ("title", "placeholder", "alt", "aria-label"):
            if a.get(kind):
                self.texts.append((line, kind, a[kind]))
        if tag == "input":
            if a.get("type") in ("submit", "button", "reset") \
                    and a.get("value"):
                self.texts.append((line, "value", a["value"]))
            if a.get("name"):
                self.posts.append((a["name"], a.get("value", "")))
        elif tag == "button" and a.get("name"):
            self.posts.append((a["name"], a.get("value", "")))
        elif tag == "select":
            self._select = a.get("name")
        elif tag == "option" and self._select:
            # An option without value posts its text.
            self._option = [self._select, a.get("value"), ""]

    def handle_endtag(self, tag):
        if tag == "option" and self._option:
            name, value, text = self._option
            self.posts.append(
                (name, _normalize(text) if value is None else value))
            self._option = None
        if tag == "select":
            self._select = None
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data):
        if self._stack and self._stack[-1] in ("script", "style"):
            return
        if self._option:
            self._option[2] += data
        self.texts.append((self.getpos()[0], "text", data))


def extract(html: str) -> tuple[list[tuple[int, str, str]],
                                list[tuple[str, str]]]:
    """Return the (line, kind, text) visible texts and the (name, value)
    pairs that the named controls of the html would post."""
    extractor = _Extractor()
    extractor.feed(html)
    return extractor.texts, extractor.posts


def english_left(html: str) -> list[tuple[int, str, str]]:
    """Return the visible texts of a pseudo-rendered page that have words
    outside the marked messages: English that nobody wrapped."""
    marked = re.escape(OPEN) + ".*?" + re.escape(CLOSE)
    # A message may hold markup (<code>..</code>): drop the span first.
    html = re.sub(marked, SYMBOL, html, flags=re.S)
    texts, _ = extract(html)
    out = []
    for line, kind, text in texts:
        rest = _normalize(text.replace(SYMBOL, " "))
        # ALL CAPS is not prose: UTC, CMS, YYYY.
        if WORD.search(rest) and re.search(r"[a-z]", rest):
            out.append((line, kind, rest))
    return out


def snapshot_page(name: str, full: bool, translation=DEFAULT_TRANSLATION):
    """Return what the page shows and posts, as plain data for the
    English snapshot (or {"error": ...} when it does not render)."""
    try:
        html = render(environment(full), name, translation)
    except Exception as error:
        return {"error": type(error).__name__}
    texts, posts = extract(html)
    return {
        "texts": [t for t in (_normalize(x[2]) for x in texts) if t],
        "posts": sorted([n, v] for n, v in posts),
    }


SNAPSHOT = Path(__file__).parent / "data" / "aws_english_snapshot.json"


def build_snapshot() -> dict:
    """Return the English snapshot of every page, in both modes."""
    return {name: {mode: snapshot_page(name, mode == "full")
                   for mode in ("full", "empty")}
            for name in PAGES}


if __name__ == "__main__":
    # Regenerate the snapshot: only on a tree whose English is the
    # reference (it is temporary: the work of aws-language-rest).
    SNAPSHOT.write_text(
        json.dumps(build_snapshot(), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")
