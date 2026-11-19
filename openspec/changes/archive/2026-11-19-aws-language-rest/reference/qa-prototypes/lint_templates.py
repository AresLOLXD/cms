"""PROTOTYPE guard (1): user-visible text in AWS templates outside gettext.

Run: CMS_CONFIG=config/cms.sample.toml .venv/bin/python3 lint_templates.py [--all] [files]
Findings are (file, line, kind, text). Allow-list lives in ALLOW (file, regex, reason).
"""
import re
import sys
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path

from jinja2 import nodes

from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT as ENV

ROOT = Path(__file__).resolve().parents[0]
TEMPLATES = Path(sys.argv[0]).resolve()  # placeholder, replaced below
import os
WT = Path(os.environ.get("WT", "/var/home/areslolxd/Documentos/cms/.worktrees/aws-es"))
TEMPLATES = WT / "cms/server/admin/templates"

WRAPPED, EXPR, TAG = "", "", ""
WORD = re.compile(r"[^\W\d_]{2,}")          # a word of 2+ letters
TEXT_ATTRS = {"title", "placeholder", "alt", "aria-label", "aria-description",
              "aria-placeholder", "label", "summary"}
GETTEXT = {"_", "gettext", "ngettext", "N_", "n_"}
NON_TEXT_CALLS = {"url", "static_url", "xsrf_form_html", "make_timestamp"}


# ---------- Jinja side: skeleton of the HTML + AST literals ----------
def skeleton(src):
    """Return the html with each jinja construct replaced by a sentinel
    (same number of newlines, so line numbers stay right)."""
    out = []
    toks = list(ENV.lex(src))
    in_js = False
    i = 0
    while i < len(toks):
        _, kind, val = toks[i]
        if kind == "data":
            out.append(val)
        elif kind in ("variable_begin", "block_begin", "comment_begin"):
            end = {"variable_begin": "variable_end", "block_begin": "block_end",
                   "comment_begin": "comment_end"}[kind]
            j, text = i, ""
            while toks[j][1] != end:
                text += toks[j][2]
                j += 1
            first = [t[2] for t in toks[i + 1:j] if t[1] == "name"][:1]
            if kind == "block_begin" and first == ["trans"]:
                # swallow until the block that holds 'endtrans'
                k = j
                while not (toks[k][1] == "block_begin" and [
                        t[2] for t in toks[k + 1:k + 6] if t[1] == "name"][:1]
                        == ["endtrans"]):
                    text += toks[k][2]
                    k += 1
                while toks[k][1] != "block_end":
                    text += toks[k][2]
                    k += 1
                j = k
                sentinel = WRAPPED
            elif kind == "block_begin" and first == ["block"] and \
                    [t[2] for t in toks[i + 1:j] if t[1] == "name"][1:2] in (["js"], ["js_init"]):
                sentinel, in_js = "<script>", True      # child templates have raw js here
            elif kind == "block_begin" and first == ["endblock"] and in_js:
                sentinel, in_js = "</script>", False
            else:
                sentinel = {"variable_begin": EXPR, "block_begin": TAG,
                            "comment_begin": ""}[kind]
            out.append(sentinel + "\n" * text.count("\n"))
            i = j
        i += 1
    return "".join(out)


def _consts(node):
    """Yield (Const, in_wrapped_call) string constants whose value can reach the
    page: through conditionals, concatenation, filters, call args, containers."""
    if isinstance(node, nodes.Const):
        if isinstance(node.value, str):
            yield node
    elif isinstance(node, nodes.CondExpr):
        yield from _consts(node.expr1)
        if node.expr2 is not None:
            yield from _consts(node.expr2)
    elif isinstance(node, (nodes.Concat, nodes.Add)):
        for n in node.iter_child_nodes():
            yield from _consts(n)
    elif isinstance(node, nodes.Mod):
        yield from _consts(node.left)
    elif isinstance(node, nodes.Filter):
        if node.node is not None:
            yield from _consts(node.node)
    elif isinstance(node, nodes.Call):
        callee = node.node
        if isinstance(callee, nodes.Name) and (callee.name in GETTEXT
                                               or callee.name in NON_TEXT_CALLS):
            return                                   # wrapped or a path
        if isinstance(callee, nodes.Getattr) and callee.attr in {"get", "format"}:
            return
        for a in node.args:
            yield from _consts(a)
    elif isinstance(node, (nodes.List, nodes.Tuple)):
        for n in node.items:
            yield from _consts(n)
    elif isinstance(node, nodes.Dict):
        for p in node.items:
            yield from _consts(p.value)
    elif isinstance(node, nodes.MarkSafeIfAutoescape):
        yield from _consts(node.expr)


def expr_literals(src):
    """String literals that would be printed by {{ }}, set by {% set %}, or
    passed to macros, and are not under _()/gettext()/ngettext()."""
    tree = ENV.parse(src)
    for out in tree.find_all(nodes.Output):
        for child in out.nodes:
            if not isinstance(child, nodes.TemplateData):
                for c in _consts(child):
                    yield c.lineno, "expr-literal", c.value
    for a in tree.find_all(nodes.Assign):
        for c in _consts(a.node):
            yield c.lineno, "set-literal", c.value
    for call in tree.find_all(nodes.CallBlock):
        for a in call.call.args:
            for c in _consts(a):
                yield c.lineno, "macro-arg-literal", c.value
    for m in tree.find_all(nodes.Macro):
        for d in m.defaults:
            for c in _consts(d):
                yield c.lineno, "macro-default-literal", c.value


# ---------- HTML side ----------
class Linter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.findings, self.stack = [], []

    def add(self, kind, text):
        t = text.replace(WRAPPED, "").replace(EXPR, "").replace(TAG, "")
        t = " ".join(t.split())
        if WORD.search(t):
            self.findings.append((self.getpos()[0], kind, t))

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
        a = dict(attrs)
        for name, val in attrs:
            if val is None or WRAPPED in val and not WORD.search(
                    val.replace(WRAPPED, "").replace(EXPR, "").replace(TAG, "")):
                continue
            if name in TEXT_ATTRS:
                # an attribute that is wholly a {{ }} is data or already handled
                self.add("attr-" + name, val)
            elif name == "value" and (
                    tag == "input" and a.get("type") in ("submit", "button", "reset")):
                self.add("attr-value", val)
            elif name.startswith("on"):
                for m in re.finditer(r"""(confirm|alert|prompt)\(\s*(['"])(.*?)\2""", val):
                    self.add("js-attr-" + m.group(1), m.group(3))
        self.last_open = tag

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.stack.pop()

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass

    def handle_data(self, data):
        if self.stack and self.stack[-1] in ("script", "style"):
            return                                    # scripts: see js check
        self.add("text", data)


SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)


def script_bodies(path):
    """Yield (line, body) for every inline script (incl. block js / js_init)."""
    sk = skeleton(path.read_text(encoding="utf-8"))
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", sk, re.S):
        yield sk.count("\n", 0, m.start(1)) + 1, m.group(1)


def lint_file(path):
    src = path.read_text(encoding="utf-8")
    res = []
    sk = skeleton(src)
    lt = Linter()
    lt.feed(sk)
    res += lt.findings
    res += list(expr_literals(src))
    return [(l, k, t) for l, k, t in res if WORD.search(t)
            and not any(re.search(r, t) for _, r, _ in ALLOW)]


# ---------- allow-list: (file glob suffix, regex that must match whole text, reason)
ALLOW = [
    (r".*", r"^(\W|\d)*$", "no letters"),
    (r".*", r"^\s*(selected|checked|disabled|readonly|multiple)\s*$", "html boolean attribute"),
    (r".*", r"^[^a-z]*$", "only capitals and symbols: UTC, YYYY-MM-DD, KB"),
]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    files = [Path(a).resolve() for a in args] or sorted(TEMPLATES.rglob("*.html"))
    per_file, kinds = {}, Counter()
    for f in files:
        res = lint_file(f)
        per_file[f.relative_to(TEMPLATES)] = res
        kinds.update(k for _, k, _ in res)
    if "--list" in sys.argv:
        for f, res in per_file.items():
            for l, k, t in res:
                print(f"{f}:{l}: {k}: {t[:90]}")
    print("\nFINDINGS PER FILE")
    for f, res in sorted(per_file.items(), key=lambda kv: -len(kv[1])):
        print(f"{len(res):5d}  {f}")
    print("TOTAL", sum(len(r) for r in per_file.values()), dict(kinds))


if __name__ == "__main__":
    main()
