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

"""Static and rendered checks that the texts of AWS go through gettext.

The helper of aws_i18n_guard_test.py (change aws-language-rest). Four
kinds of check:

- the template lint: text nodes, the attributes that show text and the
  string literals of {{ }}, {% set %} and macro arguments that are not
  under gettext;
- the script scan: prose string literals in the scripts (the static
  files and the inline scripts of the templates);
- the Python lint: texts that reach the admin from the handlers
  without self._ (notifications, raised errors, asserts, writes and
  module constants);
- the pseudo-localization render of aws_render: every English word
  left on a page is a text nobody wrapped.

A file is checked strictly when it carries the marker "aws-i18n:
complete" (the first lines of a template or script, anywhere in a
Python file) or when AWS_I18N_FILES lists it (comma separated, names
relative to cms/server/admin, or to its templates directory for a
template). The names in this module are relative to cms/server/admin.

"""

import ast
import os
import re
from html.parser import HTMLParser

from jinja2 import nodes

from cms.grading.languagemanager import LANGUAGES
from cms.grading.scoretypes import SCORE_TYPES
from cms.grading.tasktypes import TASK_TYPES
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT as ENV
from cmstestsuite.unit_tests.server.admin import aws_render
from cmstestsuite.unit_tests.server.admin.aws_render import WORD


ADMIN = aws_render.TEMPLATES.parent
MARKER = re.compile(r"aws-i18n:\s*complete")
EXTRA = {name if "/" in name or not name.endswith(".html")
         else "templates/" + name
         for name in filter(None, os.environ.get(
             "AWS_I18N_FILES", "").split(","))}

TEMPLATE_FILES = ["templates/" + name for name in aws_render.ALL_TEMPLATES]
PYTHON_FILES = sorted(
    str(f.relative_to(ADMIN)) for f in ADMIN.rglob("*.py")
    if f.name != "__init__.py")
# The vendored scripts (jq, prism) are not ours.
SCRIPT_FILES = ["static/aws_utils.js", "static/web_rpc.js"]
ALL_FILES = TEMPLATE_FILES + PYTHON_FILES + SCRIPT_FILES

# The forms of text that are not prose.
NOT_PROSE = [
    (r"^(\W|\d)*$", "no letters"),
    (r"^\s*(selected|checked|disabled|readonly|multiple)\s*$",
     "html boolean attribute"),
    (r"^[^a-z]*$", "only capitals and symbols: UTC, YYYY-MM-DD, KB"),
]

# Intentional English that the checks would flag: (file, exact text,
# reason). A lane asks the coordinator for an entry in its report. An
# entry that matches nothing in a checked file fails the guard.
ALLOW = [
    ("handlers/base.py", "Invalid permission level %s.",
     "programmer error, never reaches an admin"),
    ("handlers/base.py", "A critical error has occurred :-(",
     "written when the locale itself failed"),
    ("handlers/contesttask.py", "Remove from contest",
     "operation identifier that the page posts"),
    ("handlers/user.py", "Delete selected group",
     "operation identifier that the page posts"),
    ("handlers/user.py", "Make main group",
     "operation identifier that the page posts"),
    ("handlers/dataset.py", "Copy of %s",
     "stored description of the new dataset"),
    ("handlers/dataset.py", "Task manager for %s",
     "description of a file stored in the database"),
    ("handlers/dataset.py", "Testcase input for task %s",
     "description of a file stored in the database"),
    ("handlers/dataset.py", "Testcase output for task %s",
     "description of a file stored in the database"),
    ("handlers/task.py", "Statement for task %s (lang: %s)",
     "description of a file stored in the database"),
    ("handlers/task.py", "Task attachment for %s",
     "description of a file stored in the database"),
]

# Pages whose named controls may post a translated value. Empty: the
# ranking group pages already post a fixed value.
NAMED_CONTROLS_ALLOW: set[str] = set()

# English that a rendered page may keep: (page, exact text, reason);
# the page "*" stands for every page.
PSEUDO_ALLOW = [
    ("*", "español", "name of the language, in that language"),
]


def read(rel: str) -> str:
    return (ADMIN / rel).read_text(encoding="utf-8")


def is_complete(rel: str) -> bool:
    """Tell whether the file is checked strictly."""
    if rel in EXTRA:
        return True
    text = read(rel)
    if rel.endswith(".py"):
        return bool(MARKER.search(text))
    return bool(MARKER.search("".join(text.splitlines(True)[:3])))


def allowed(rel: str, text: str) -> bool:
    return any(f == rel and t == text for f, t, _ in ALLOW)


# ---------------------------------------------------------------- templates
# Private-use characters that stand for the jinja constructs.
WRAPPED, EXPR, TAG = "\ue000", "\ue001", "\ue002"
TEXT_ATTRS = {"title", "placeholder", "alt", "aria-label", "aria-description",
              "aria-placeholder", "label", "summary"}
GETTEXT = {"_", "gettext", "ngettext", "N_", "n_"}
NON_TEXT_CALLS = {"url", "static_url", "xsrf_form_html", "make_timestamp"}


# The lexer without trim_blocks and lstrip_blocks, which eat newlines
# and would make the line numbers of the findings drift.
_LEXER = ENV.overlay(trim_blocks=False, lstrip_blocks=False)


def skeleton(src: str) -> str:
    """Return the html with each jinja construct replaced by a sentinel
    (WRAPPED for a trans block, EXPR for {{ }}, TAG for {% %}), keeping
    the line numbers."""
    out = []
    toks = list(_LEXER.lex(src))
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
            names = [t[2] for t in toks[i + 1:j] if t[1] == "name"]
            first = names[:1]
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
            elif kind == "block_begin" and first == ["block"] \
                    and names[1:2] in (["js"], ["js_init"]):
                # child templates have raw js in these blocks
                sentinel, in_js = "<script>", True
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
    """Yield the string constants whose value can reach the page:
    through conditionals, concatenation, filters, call args, containers."""
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
        if isinstance(callee, nodes.Name) and (
                callee.name in GETTEXT or callee.name in NON_TEXT_CALLS):
            return                                   # wrapped, or a path
        if isinstance(callee, nodes.Getattr) and callee.attr in {
                "get", "format"}:
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


def expr_literals(src: str):
    """Yield the string literals that {{ }}, {% set %} or a macro call
    print and that are not under gettext."""
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


class _Linter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.findings, self.stack = [], []

    def add(self, kind, text):
        for sentinel in (WRAPPED, EXPR, TAG):
            text = text.replace(sentinel, "")
        text = " ".join(text.split())
        if WORD.search(text):
            self.findings.append((self.getpos()[0], kind, text))

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
        a = dict(attrs)
        for name, val in attrs:
            if val is None:
                continue
            if name in TEXT_ATTRS:
                self.add("attr-" + name, val)
            elif name == "value" and tag == "input" \
                    and a.get("type") in ("submit", "button", "reset"):
                self.add("attr-value", val)
            elif name.startswith("on"):
                for m in re.finditer(
                        r"""(confirm|alert|prompt)\(\s*(['"])(.*?)\2""", val):
                    self.add("js-attr-" + m.group(1), m.group(3))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.stack.pop()

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass

    def handle_data(self, data):
        # scripts have their own scan
        if not (self.stack and self.stack[-1] in ("script", "style")):
            self.add("text", data)


def template_texts(src: str) -> list[tuple[int, str, str]]:
    """Return the (line, kind, text) that the template shows outside
    gettext."""
    linter = _Linter()
    linter.feed(skeleton(src))
    found = linter.findings + list(expr_literals(src))
    return [(line, kind, text) for line, kind, text in found
            if WORD.search(text)
            and not any(re.search(rule, text) for rule, _ in NOT_PROSE)]


# ------------------------------------------------------------------ scripts
_TOKEN = re.compile(
    r"""//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`""",
    re.S)
_SENTINELS = re.compile("[%s%s%s]" % (WRAPPED, EXPR, TAG))
_URLISH = re.compile(r"^(?:[./#]|https?:|data:|mailto:)")
# Script literals that are not prose although they look like it.
JS_NOT_PROSE = {"use strict": "pragma", "Notification": "browser API name",
                "Escape": "key name"}


def _is_prose(literal: str) -> str | None:
    """Return the text of the literal if it is prose, else None."""
    text = re.sub(r"<[^>]*>", " ", literal)       # keep the text of html
    text = _SENTINELS.sub(" ", text)
    text = re.sub(r"&\w+;|\\[nrt]", " ", text)
    text = " ".join(text.split())
    if not WORD.search(text) or _URLISH.match(text):
        return None
    tokens = text.split()
    # css classes, ids, files
    if all(re.search(r"[-_.\d]", t) for t in tokens):
        return None
    if text in JS_NOT_PROSE or not re.search(r"[a-z]", text):
        return None
    # CamelCase identifier
    if len(tokens) == 1 and re.search(r"[a-z][A-Z]|[A-Z]{2}", text):
        return None
    # identifier, key, event, extension
    if " " not in text and not text[0].isupper():
        return None
    if "_" in text and " " not in text:
        return None
    return text


def script_literals(js: str, first_line: int = 0) -> list[tuple[int, str, str]]:
    """Return the (line, "literal", text) prose literals of a script."""
    found = []
    for m in _TOKEN.finditer(js):
        token = m.group(0)
        if token.startswith(("//", "/*")):
            continue
        prose = _is_prose(token[1:-1])
        if prose:
            found.append(
                (first_line + js.count("\n", 0, m.start()) + 1, "literal",
                 prose))
    return found


def inline_scripts(src: str):
    """Yield (line, body) of every inline script of a template, with
    the block js and js_init of the child templates."""
    sk = skeleton(src)
    for m in re.finditer(
            r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", sk, re.S):
        yield sk.count("\n", 0, m.start(1)), m.group(1)


def script_texts(rel: str) -> list[tuple[int, str, str]]:
    """Return the prose literals of a static script or of the inline
    scripts of a template."""
    if rel.endswith(".js"):
        return script_literals(read(rel))
    return [found for base, body in inline_scripts(read(rel))
            for found in script_literals(body, base)]


# ------------------------------------------------------------------- python
WRAPPERS = {"_", "n_", "gettext", "ngettext", "N_", "Nn_"}
RAISED = {"ValueError", "KeyError", "AssertionError", "Exception",
          "RuntimeError"}


def _callee(node):
    f = node.func
    return f.id if isinstance(f, ast.Name) \
        else f.attr if isinstance(f, ast.Attribute) else None


def _classify(node):
    """Return ("wrapped" | "literal" | "empty" | "dynamic", text) for
    the expression that becomes text."""
    if isinstance(node, ast.Call):
        name = _callee(node)
        if name in WRAPPERS:
            return "wrapped", None
        if name == "format" and isinstance(node.func, ast.Attribute):
            return _classify(node.func.value)
        return "dynamic", None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return ("literal", node.value) if WORD.search(node.value) \
            else ("empty", None)
    if isinstance(node, ast.JoinedStr):
        txt = "".join(v.value for v in node.values
                      if isinstance(v, ast.Constant))
        return ("literal", txt) if WORD.search(txt) else ("dynamic", None)
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Mod):
            return _classify(node.left)
        if isinstance(node.op, ast.Add):
            for side in (node.left, node.right):
                kind, text = _classify(side)
                if kind in ("literal", "wrapped"):
                    return kind, text
    if isinstance(node, ast.IfExp):
        for side in (node.body, node.orelse):
            kind, text = _classify(side)
            if kind == "literal":
                return kind, text
    return "dynamic", None


def _unwrapped_constants(node):
    """Yield the string constants under node, not looking inside N_()."""
    if isinstance(node, ast.Call) and _callee(node) in WRAPPERS:
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    for child in ast.iter_child_nodes(node):
        yield from _unwrapped_constants(child)


class _PythonVisitor(ast.NodeVisitor):
    """Collect the texts that reach the admin without gettext.

    Sinks: add_notification(ts, subject, text), raise ValueError and
    the like, and assert ..., text (in handlers); self.write(text with
    spaces); prose module or class constants not wrapped in N_. HTTP
    error reasons are English on purpose and are not sinks.

    shortcut: only literal texts are seen; a text built from variables
    or returned by a helper is not. Upgrade if one slips through.

    """

    def __init__(self, in_handler):
        self.found = []
        self.in_handler = in_handler
        self.depth = 0
        self.in_class_body = False

    def check(self, node, kind, expr):
        kind_of, text = _classify(expr)
        if kind_of == "literal":
            self.found.append((node.lineno, kind, text))

    def visit_Call(self, node):
        name = _callee(node)
        if name == "add_notification" and len(node.args) >= 3:
            self.check(node, "notification-subject", node.args[1])
            self.check(node, "notification-text", node.args[2])
        elif name in RAISED and node.args and self.in_handler:
            self.check(node, "exception-text", node.args[0])
        elif name == "write" and node.args:
            kind, text = _classify(node.args[0])
            if kind == "literal" and " " in text.strip():
                self.found.append((node.lineno, "write", text))
        self.generic_visit(node)

    def visit_Assert(self, node):
        if node.msg is not None and self.in_handler:
            self.check(node, "assert-text", node.msg)
        self.generic_visit(node)

    def visit_Assign(self, node):
        if self.depth == 0 or self.in_class_body:
            for v in _unwrapped_constants(node.value):
                if " " in v.strip() and v[:1].isupper():
                    self.found.append((node.lineno, "constant", v))
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        old, self.in_class_body = self.in_class_body, True
        self.generic_visit(node)
        self.in_class_body = old


def python_texts(src: str, in_handler: bool = True):
    """Return the (line, kind, text) of the Python source that reach
    the admin without gettext."""
    visitor = _PythonVisitor(in_handler)
    visitor.visit(ast.parse(src))
    return visitor.found


# ------------------------------------------------------ formats and syntax
_SPEC = re.compile(r"%(?:\(\w+\)[sdif]|[sdifr])")


def _dummy_args(msgid):
    """Arguments of the right type for every spec in msgid."""
    plain = msgid.replace("%%", "")
    named = re.findall(r"%\((\w+)\)([sdif])", plain)
    if named:
        return {n: (1 if t in "dif" else "x") for n, t in named}
    return tuple(1 if t in "dif" else "x"
                 for t in re.findall(r"%([sdifr])", plain))


def _format_problem(msgid, has_mod):
    """Why msgid is wrong for the way it is used, or None."""
    if has_mod:
        try:
            msgid % _dummy_args(msgid)
        except Exception as e:
            return f"invalid format: {e}"
    elif "%%" in msgid:
        return "has %% but is never %-formatted: shows two percent signs"
    return None


def _unwrap(node):
    return node.expr if isinstance(node, nodes.MarkSafeIfAutoescape) else node


def template_problems(rel: str) -> list[str]:
    """Return what is wrong with the template: it does not compile, or
    a gettext msgid is not a valid format for its use."""
    try:
        ENV.get_template(rel.removeprefix("templates/"))
        tree = ENV.parse(read(rel))
    except Exception as e:
        return [f"{rel}: {type(e).__name__}: {e}"]
    out = []
    mods = set()
    # a trans block with variables is Mod(MarkSafeIfAutoescape(Call), Dict)
    for mod in tree.find_all(nodes.Mod):
        call = _unwrap(mod.left)
        if isinstance(call, nodes.Call) and isinstance(call.node, nodes.Name) \
                and call.node.name in GETTEXT and call.args \
                and isinstance(call.args[0], nodes.Const):
            mods.add(id(call))
            msgid = call.args[0].value
            if re.search(r"%%(?:\(\w+\))?[sdif]", msgid):
                out.append((call.lineno, "printf typed inside a trans "
                            "block that has variables", msgid))
            problem = _format_problem(msgid, True)
            if problem:
                out.append((call.lineno, problem, msgid))
    for call in tree.find_all(nodes.Call):
        if isinstance(call.node, nodes.Name) and call.node.name in GETTEXT \
                and call.args and isinstance(call.args[0], nodes.Const) \
                and isinstance(call.args[0].value, str) \
                and id(call) not in mods:
            problem = _format_problem(call.args[0].value, False)
            if problem:
                out.append((call.lineno, problem, call.args[0].value))
    return [f"{rel}:{line}: {problem}: {msgid!r}"
            for line, problem, msgid in out]


def python_problems(rel: str) -> list[str]:
    """Return the gettext msgids of a Python file that are not a valid
    format for their use."""
    tree = ast.parse(read(rel))
    out, mods = [], set()
    for n in ast.walk(tree):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod):
            call = n.left
            if isinstance(call, ast.Call) and _callee(call) in GETTEXT \
                    and call.args and isinstance(call.args[0], ast.Constant):
                mods.add(id(call))
                msgid = call.args[0].value
                try:
                    args = _dummy_args(msgid)
                    if isinstance(n.right, ast.Tuple) \
                            and isinstance(args, tuple) \
                            and len(n.right.elts) != len(args):
                        raise TypeError("%d specs, %d arguments" % (
                            len(args), len(n.right.elts)))
                    msgid % args
                except Exception as e:
                    out.append((call.lineno, f"invalid format: {e}", msgid))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and _callee(n) in GETTEXT and n.args \
                and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str) and id(n) not in mods:
            problem = _format_problem(n.args[0].value, False)
            if problem:
                out.append((n.lineno, problem, n.args[0].value))
    return [f"{rel}:{line}: {problem}: {msgid!r}"
            for line, problem, msgid in out]


# --------------------------------------------------------------- the files
def raw_findings(rel: str) -> list[tuple[int, str, str]]:
    """Return the findings of the static checks for one file, before
    the allow-list."""
    if rel.endswith(".py"):
        return python_texts(read(rel), rel.startswith("handlers/"))
    if rel.endswith(".js"):
        return script_texts(rel)
    return template_texts(read(rel))


def static_findings(rel: str) -> list[tuple[int, str, str]]:
    """Return the findings of the static checks for one file, after the
    allow-list."""
    return [f for f in raw_findings(rel) if not allowed(rel, f[2])]


def script_findings(rel: str) -> list[tuple[int, str, str]]:
    """Return the findings of the script scan for a template or a
    static script, after the allow-list."""
    return [f for f in script_texts(rel) if not allowed(rel, f[2])]


# ------------------------------------------------------------- pseudo render
def _key(text: str) -> str:
    """Return text without spaces, digits and the stand-in symbol, so a
    text of the lint (a sentinel in place of the datum) and the same
    text rendered (a number or a symbol) compare equal."""
    return re.sub(r"[\s\d" + aws_render.SYMBOL + "]+", "", text)


def _strings(value, seen=None):
    """Yield the strings of a grading object: names, descriptions and
    choices of a parameter, however nested."""
    seen = set() if seen is None else seen
    if isinstance(value, str):
        yield value
    elif id(value) not in seen:
        seen.add(id(value))
        if isinstance(value, dict):
            value = [*value.keys(), *value.values()]
        elif hasattr(value, "__dict__"):
            value = vars(value).values()
        if isinstance(value, (list, tuple)) or hasattr(value, "__iter__") \
                and not isinstance(value, (bytes, type)):
            for item in value:
                yield from _strings(item, seen)


def grading_texts() -> set[str]:
    """Return the texts that come from cms/grading and are not
    translated: names of the task types, score types and languages, and
    the names, descriptions and choices of their parameters."""
    texts = set(TASK_TYPES) | set(SCORE_TYPES) \
        | {language.name for language in LANGUAGES}
    for task_type in TASK_TYPES.values():
        texts |= set(_strings(task_type.ACCEPTED_PARAMETERS))
    return {_key(text) for text in texts if text}


def english_left(page: str, translated_pages: set[str]) -> list[str]:
    """Return the English texts left on the page rendered in the
    pseudo-locale.

    Not counted: the texts that the lint finds in a template that is
    not checked yet (they belong to it), the texts of cms/grading and
    the allow-list.

    page: the template of the page.
    translated_pages: the templates (names relative to the admin
        directory) that are checked.

    """
    outside = {_key(text) for rel in TEMPLATE_FILES
               if rel not in translated_pages
               for _, _, text in template_texts(read(rel))}
    allowed_here = {_key(t) for p, t, _ in PSEUDO_ALLOW if p in ("*", page)}
    skip = outside | allowed_here | grading_texts()
    left = []
    for html in aws_render.renders(page, aws_render.PSEUDO):
        left += [text for _, _, text in aws_render.english_left(html)
                 if _key(text) not in skip and text not in left]
    return left
