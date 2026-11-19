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
prints as a symbol. There are three modes: "full" (the stand-in is true
and iterates over two items), "empty" (false, iterates over nothing)
and "admin" (an admin is logged in, the stand-in is true, has six items
and is ``none`` for the ``is none`` test, as a page without a contest
or a dataset). Four things are extracted from the rendered html: the
visible texts, the (name, value) pairs that the named controls would
post, the inline scripts and the event attributes.

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
from jinja2.utils import LRUCache
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
# The attributes that show text.
TEXT_ATTRS = ("title", "placeholder", "alt", "aria-label",
              "aria-description", "aria-placeholder", "label", "summary")
MODES = ("full", "empty", "admin")

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


def make_stand_ins(mode: str) -> tuple[type[Undefined], type]:
    """Return the classes of the stand-in for a datum that the page
    does not get (Undefined) and for one that it does (a plain object)."""
    items = {"full": 2, "empty": 0, "admin": 6}[mode]

    class Datum:
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
            return iter([self] * min(items, 2))

        def __str__(self):
            return SYMBOL

        def __bool__(self):
            return items > 0

        def __len__(self):
            return items

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

    class Anything(Datum, Undefined):
        __slots__ = ()

    class Defined(Datum):
        __slots__ = ()

    return Anything, Defined


def _total(fn: Callable) -> Callable:
    """Make a filter print a symbol instead of raising on a stand-in."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return SYMBOL
    return wrapper


@functools.cache
def environment(mode: str) -> Environment:
    """Return the AWS environment with the stand-ins of the mode."""
    anything, defined = make_stand_ins(mode)
    env = AWS_ENVIRONMENT.overlay(undefined=anything)
    # An overlay starts with a copy of its parent's cache, whose
    # templates keep the parent's environment and would ignore ours.
    env.cache = LRUCache(400)
    # An overlay shares the globals, filters and tests of its parent.
    env.globals = dict(env.globals)
    env.filters = dict(env.filters)
    env.tests = dict(env.tests)
    env.globals["clock_zones"] = lambda *args: []
    # The real one is random.
    env.globals["get_hex_random_key"] = lambda *args: "0000"
    if mode == "admin":
        # What a page has when an admin is logged in and no contest is
        # chosen: the stand-ins are "none", the lists are long.
        env.globals["admin"] = defined()
        env.globals["contest_list"] = defined()
        env.tests["none"] = lambda value: value is None \
            or isinstance(value, (anything, defined))
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

    A page that raises in a mode is skipped here (the stand-ins of that
    mode are not data the page can use); test_every_page_renders checks
    that each page renders in at least one mode.

    """
    for mode in MODES:
        try:
            yield render(environment(mode), page, translation)
        except Exception:
            continue


# Only these spaces count: a no-break space is text.
_SPACE = re.compile(r"[ \t\r\n\f]+")
# The elements that sit in a line of text: a space next to them shows.
INLINE = frozenset(
    "a abbr b bdi bdo big button cite code em font i img input kbd label "
    "mark q s samp select small span strike strong sub sup textarea tt u "
    "var".split())
# Stand in a static skeleton for a jinja tag (it splits the text).
TAG = "\ue002"


def _normalize(text: str) -> str:
    return _SPACE.sub(" ", text).strip(" ")


class _Extractor(HTMLParser):
    """Collect what a page shows and posts.

    The texts are (line, kind, text): a text node or the text of an
    attribute, a <br> and the raw content of <pre> and <textarea>. Runs
    of spaces collapse, but a space next to an inline tag stays.

    """

    VOID = ("meta", "link", "br", "hr", "input", "img", "col")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts: list[tuple[int, str, str]] = []
        self.posts: list[tuple[str, str]] = []
        self.scripts: list[tuple[int, str]] = []
        self.events: list[tuple[int, str, str]] = []
        self._stack: list[str] = []
        self._select: str | None = None
        self._option: list | None = None
        self._script_buf: list[str] | None = None
        self._open_text: tuple | None = None
        self._after_inline = False

    def _expand(self, text: str, keep_tag: bool = False) -> str:
        """Drop the stand-ins of the jinja tags (or keep them to split)."""
        return text if keep_tag else text.replace(TAG, "")

    def _boundary(self, before_inline: bool, after_inline: bool):
        """A tag: close the text before it and remember what follows."""
        self._flush(before_inline)
        self._after_inline = after_inline

    def _flush(self, before_inline: bool):
        if self._open_text is None:
            return
        line, kind, core, lead, trail, after_inline = self._open_text
        self._open_text = None
        if core:
            text = (" " if lead and after_inline else "") + core \
                + (" " if trail and before_inline else "")
        elif lead and after_inline and before_inline:
            text = " "
        else:
            return
        self.texts.append((line, kind, text))

    def handle_starttag(self, tag, attrs):
        a = {k: self._expand(v or "") for k, v in attrs}
        line = self.getpos()[0]
        # A space shows between a text and an inline tag that starts (or
        # after one that ends, or a void one), not inside the element.
        self._boundary(tag in INLINE, tag in INLINE and tag in self.VOID)
        if tag not in self.VOID:
            self._stack.append(tag)
        if tag == "br":
            self.texts.append((line, "br", ""))
        for kind in TEXT_ATTRS:
            if a.get(kind):
                self.texts.append((line, kind, a[kind]))
        for name, value in a.items():
            if name.startswith("on") and value:
                self.events.append((line, name, value))
        if tag == "script" and "src" not in a:
            self._script_buf = []
        if tag == "input":
            if a.get("type") in ("submit", "button", "reset") \
                    and a.get("value"):
                # The same kind as the text of a <button>.
                self.texts.append((line, "label", a["value"]))
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
        self._boundary(False, tag in INLINE)
        if tag == "script" and self._script_buf is not None:
            self.scripts.append(
                (self.getpos()[0], self._expand("".join(self._script_buf))))
            self._script_buf = None
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
        if self._script_buf is not None:
            self._script_buf.append(data)
            return
        if self._stack and self._stack[-1] == "style":
            return
        if self._option:
            self._option[2] += data
        line = self.getpos()[0]
        raw = [t for t in ("textarea", "pre") if t in self._stack]
        if raw:
            self.texts.append((line, raw[-1], self._expand(data)))
            return
        kind = "label" if "button" in self._stack else "text"
        pieces = self._expand(data, keep_tag=True).split(TAG)
        for number, piece in enumerate(pieces):
            if number:
                self._boundary(False, False)
            core = _normalize(piece)
            self._open_text = (
                line, kind, core, piece[:1] in " \t\r\n\f",
                piece[-1:] in " \t\r\n\f", self._after_inline)

    def finish(self):
        self.close()
        self._flush(False)


def extract_all(html: str) -> _Extractor:
    """Return the extractor that has read the html."""
    extractor = _Extractor()
    extractor.feed(html)
    extractor.finish()
    return extractor


def extract(html: str) -> tuple[list[tuple[int, str, str]],
                                list[tuple[str, str]]]:
    """Return the (line, kind, text) visible texts and the (name, value)
    pairs that the named controls of the html would post."""
    extractor = extract_all(html)
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
