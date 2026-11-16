"""PROTOTYPE guard (4): pseudo-localization render of every AWS template.

Every msgid is "translated" to  ⟦msgid⟧  (mono-line, marks both ends). Pages are
rendered with permissive stand-ins for the data (no database): anything the
template reads that was not given is an `Anything` that prints as a symbol (no
letters), iterates over two items and is true (mode "full") or, in mode "empty",
is false and iterates over nothing. The visible text of the rendered page, minus
the marked spans, must contain no word: whatever is left is English the code
did not wrap (or data that must be in the allow-list).
Run: .venv/bin/python3 pseudo_render.py [--list]
"""
import functools
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from html.parser import HTMLParser

from jinja2 import Undefined

from cms.locale import DEFAULT_TRANSLATION, Translation
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from lint_templates import TEMPLATES, WORD

OPEN, CLOSE = "⟦", "⟧"


class Pseudo(Translation):
    def __init__(self):
        super().__init__("es")

    def gettext(self, msgid):
        return OPEN + msgid + CLOSE

    def ngettext(self, one, many, n):
        return OPEN + (one if n == 1 else many) + CLOSE


PSEUDO = Pseudo()
SYMBOL = "§"


def make_undefined(full):
    class Anything(Undefined):
        """Stands for any datum: attribute, item, call, iteration, arithmetic."""
        __slots__ = ()
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return self
        def __getitem__(self, key): return self
        def __call__(self, *a, **k): return self
        def __iter__(self):
            return iter([self, self] if full else [])
        def __str__(self): return SYMBOL
        def __bool__(self): return full
        def __len__(self): return 2 if full else 0
        def __int__(self): return 1
        def __float__(self): return 1.0
        def __index__(self): return 1
        def __html__(self): return SYMBOL
        def __lt__(self, o): return False
        __gt__ = __le__ = __ge__ = __lt__
        def __eq__(self, o): return False
        def __hash__(self): return 1
        def __abs__(self): return self
        __neg__ = __pos__ = __abs__
        def __add__(self, o): return self
        __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __truediv__ = __add__
        __floordiv__ = __mod__ = __rmod__ = __add__
    return Anything


def env_for(full):
    env = AWS_ENVIRONMENT.overlay(undefined=make_undefined(full))
    env.globals["clock_zones"] = lambda *a: []
    import json
    from markupsafe import Markup
    env.filters["tojson"] = lambda v, *a, **k: Markup(json.dumps(v, default=lambda o: SYMBOL))
    # a filter that chokes on a stand-in prints a symbol instead of raising
    import functools
    for name, fn in list(env.filters.items()):
        if name not in ("safe", "e", "escape", "tojson", "forceescape", "default", "d"):
            env.filters[name] = _total(fn)
    return env


def _total(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            return SYMBOL
    return wrapper


def render(env, name):
    params = dict(
        static_url=lambda *p: "/".join(p), url=lambda *p: "/" + "/".join(map(str, p)),
        xsrf_form_html="", timestamp=datetime(2026, 1, 1),
        translation=PSEUDO, gettext=PSEUDO.gettext, ngettext=PSEUDO.ngettext,
        available_translations={"es": PSEUDO}, cookie_translation=None,
        automatic_translation=PSEUDO)
    return env.get_template(name).render(**params)


class Visible(HTMLParser):
    """Collect the visible text (text nodes + title/placeholder/alt/aria-label/
    submit value) outside scripts and styles."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in ("meta", "link", "br", "hr", "input", "img", "col"):
            self.stack.append(tag)
        for k in ("title", "placeholder", "alt", "aria-label"):
            if a.get(k):
                self.texts.append((self.getpos()[0], k, a[k]))
        if tag == "input" and a.get("type") in ("submit", "button", "reset") and a.get("value"):
            self.texts.append((self.getpos()[0], "value", a["value"]))

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass

    def handle_data(self, data):
        if not self.stack or self.stack[-1] not in ("script", "style", "title_"):
            self.texts.append((self.getpos()[0], "text", data))


def english_left(html):
    # a msgid may hold markup (<code>..</code>): drop the whole span first
    html = re.sub(re.escape(OPEN) + ".*?" + re.escape(CLOSE), SYMBOL, html, flags=re.S)
    p = Visible()
    p.feed(html)
    out = []
    for line, kind, text in p.texts:
        rest = re.sub(re.escape(OPEN) + ".*?" + re.escape(CLOSE), " ", text, flags=re.S)
        rest = rest.replace(SYMBOL, " ")
        rest = " ".join(rest.split())
        if WORD.search(rest) and re.search(r"[a-z]", rest):   # ALL CAPS: UTC, CMS, YYYY
            out.append((line, kind, rest))
    return out


def run():
    results = {}
    for full in (True, False):
        env = env_for(full)
        for f in sorted(TEMPLATES.rglob("*.html")):
            name = str(f.relative_to(TEMPLATES))
            if name.startswith(("macro/", "fragments/")) or name == "ranking.txt":
                continue          # rendered through their pages
            try:
                html = render(env, name)
                results.setdefault(name, {})["full" if full else "empty"] = english_left(html)
            except Exception as e:
                results.setdefault(name, {})["full" if full else "empty"] = f"ERROR {type(e).__name__}: {str(e)[:90]}"
    return results


if __name__ == "__main__":
    res = run()
    ok = errs = 0
    errors = defaultdict(list)
    print(f"{'file':32} full  empty   (English words left in the rendered page)")
    for name, r in sorted(res.items()):
        cols = []
        for mode in ("full", "empty"):
            v = r[mode]
            if isinstance(v, str):
                cols.append("ERR"); errors[name].append((mode, v))
            else:
                cols.append(str(len(v)))
        print(f"{name:32} {cols[0]:>4} {cols[1]:>6}")
        if "--list" in sys.argv:
            for mode in ("full",):
                if not isinstance(r[mode], str):
                    for l, k, t in r[mode][:8]:
                        print(f"        {l}: {k}: {t[:80]}")
    print("\nrender errors:")
    for n, e in errors.items():
        for mode, msg in e:
            print(" ", n, mode, msg)
    total_templates = len(res)
    both_ok = sum(1 for r in res.values() if not isinstance(r["full"], str) and not isinstance(r["empty"], str))
    one_ok = sum(1 for r in res.values() if not isinstance(r["full"], str) or not isinstance(r["empty"], str))
    print(f"\npage templates: {total_templates}; render ok in both modes: {both_ok}; in at least one: {one_ok}")


# ---- guard 4b: a translated value on a NAMED control changes what the form posts
CONTROL = re.compile(r"<(input|button|option)\b[^>]*>", re.S)


def translated_form_values(html):
    """(name, value) of named input/button controls whose posted value was
    translated (it contains a pseudo marker): the handler would receive
    Spanish instead of the key it compares with."""
    out = []
    for m in CONTROL.finditer(html):
        tag = m.group(0)
        name = re.search(r'\bname="([^"]*)"', tag)
        value = re.search(r'\bvalue="([^"]*)"', tag)
        if name and value and OPEN in value.group(1):
            out.append((name.group(1), value.group(1)))
        elif m.group(1) == "option" and value and OPEN in value.group(1):
            out.append(("<option>", value.group(1)))
    return out
