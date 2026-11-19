"""PROTOTYPE guard (6): prose string literals in AWS javascript (static files
and inline/block-js scripts of the templates).

Run: CMS_CONFIG=... .venv/bin/python3 js_literals.py [--list]
A literal is PROSE when, after stripping html tags and sentinels, it has a word of
2+ letters and is not code-looking (selector, url, css value, identifier, event name).
"""
import os
import re
import sys
from collections import Counter
from pathlib import Path

from lint_templates import TEMPLATES, WT, WRAPPED, EXPR, TAG, script_bodies

TOKEN = re.compile(
    r"""//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`""",
    re.S)
SENT = re.compile("[%s%s%s]" % (WRAPPED, EXPR, TAG))
WORD = re.compile(r"[^\W\d_]{2,}")
URLISH = re.compile(r"^(?:[./#]|https?:|data:|mailto:)")


# reviewed: literal -> reason (the real guard keys these by file too)
JS_ALLOW = {"use strict": "pragma", "Notification": "browser API name",
            "Escape": "key name", "Download": "see aws_utils.js:231 (decide)"}
JS_ALLOW.pop("Download")      # Download is real prose: keep it a finding


def literals(js):
    for m in TOKEN.finditer(js):
        t = m.group(0)
        if t.startswith(("//", "/*")):
            continue
        yield js.count("\n", 0, m.start()) + 1, t[1:-1]


def is_prose(lit):
    text = re.sub(r"<[^>]*>", " ", lit)           # html snippets: keep their text
    text = SENT.sub(" ", text)
    text = re.sub(r"&\w+;|\\[nrt]", " ", text)
    text = " ".join(text.split())
    if not WORD.search(text) or URLISH.match(text):
        return None
    tokens = text.split()
    if all(re.search(r"[-_.\d]", t) for t in tokens):     # css classes, ids, files
        return None
    if text in JS_ALLOW or not re.search(r"[a-z]", text):  # reviewed / ALL CAPS
        return None
    if len(tokens) == 1 and re.search(r"[a-z][A-Z]|[A-Z]{2}", text):
        return None                                        # CamelCase identifier
    if " " not in text and not text[0].isupper():
        return None                               # identifier, key, event, extension
    if "_" in text and " " not in text:
        return None
    return text


def scan():
    res = []
    statics = [WT / "cms/server/admin/static/aws_utils.js",
               WT / "cms/server/admin/static/web_rpc.js"]
    for f in statics:
        for ln, lit in literals(f.read_text(encoding="utf-8")):
            p = is_prose(lit)
            if p:
                res.append((f.relative_to(WT / "cms/server/admin"), ln, p))
    for f in sorted(TEMPLATES.rglob("*.html")):
        for base, body in script_bodies(f):
            for ln, lit in literals(body):
                p = is_prose(lit)
                if p:
                    res.append((f.relative_to(TEMPLATES), base + ln, p))
    return res


if __name__ == "__main__":
    res = scan()
    per = Counter(str(f) for f, _, _ in res)
    if "--list" in sys.argv:
        for f, ln, p in res:
            print(f"{f}:{ln}: {p[:100]}")
    for f, n in per.most_common():
        print(f"{n:5d}  {f}")
    print("TOTAL", len(res))
