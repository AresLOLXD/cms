"""PROTOTYPE guard (2): English texts that reach the admin from Python code of
cms/server/admin/ without gettext (self._, self.n_, N_, gettext, ngettext).

Sinks: add_notification(ts, subject, text), HTTPError(code, text), raise
ValueError/KeyError/AssertionError/Exception(text) in handlers (shown via
repr(error) in notifications), assert ..., text, self.write(text with spaces),
and prose module/class constants (LABELS = ...) not wrapped in N_.
Run: .venv/bin/python3 lint_python.py [--list]
"""
import ast
import os
import re
import sys
from collections import Counter
from pathlib import Path

WT = Path(os.environ.get("WT", "/var/home/areslolxd/Documentos/cms/.worktrees/aws-es"))
ADMIN = WT / "cms/server/admin"
WRAPPERS = {"_", "n_", "gettext", "ngettext", "N_", "Nn_"}
RAISED = {"ValueError", "KeyError", "AssertionError", "Exception", "RuntimeError"}
WORD = re.compile(r"[^\W\d_]{2,}")


def callee(node):
    f = node.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None


def classify(node):
    """'wrapped' | 'literal' | 'dynamic' for the expression that becomes text."""
    if isinstance(node, ast.Call):
        name = callee(node)
        if name in WRAPPERS:
            return "wrapped", None
        if name == "format" and isinstance(node.func, ast.Attribute):
            return classify(node.func.value)             # "..".format(...)
        if name in {"repr", "str"} and node.args:
            return "dynamic", None
        return "dynamic", None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return ("literal", node.value) if WORD.search(node.value) else ("empty", None)
    if isinstance(node, ast.JoinedStr):
        txt = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
        return ("literal", txt) if WORD.search(txt) else ("dynamic", None)
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Mod):
            k, t = classify(node.left)
            return (k, t)                               # wrapped % args is fine
        if isinstance(node.op, ast.Add):
            for side in (node.left, node.right):
                k, t = classify(side)
                if k in ("literal", "wrapped"):
                    return k, t
    if isinstance(node, ast.IfExp):
        for side in (node.body, node.orelse):
            k, t = classify(side)
            if k == "literal":
                return k, t
    return "dynamic", None


def unwrapped_constants(node):
    """Yield the string constants under node, not looking inside N_(...)."""
    if isinstance(node, ast.Call) and callee(node) in WRAPPERS:
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    for child in ast.iter_child_nodes(node):
        yield from unwrapped_constants(child)


class Visitor(ast.NodeVisitor):
    def __init__(self):
        self.found, self.wrapped, self.dynamic = [], 0, []

    def check(self, node, kind, expr):
        k, text = classify(expr)
        if k == "literal":
            self.found.append((node.lineno, kind, text))
        elif k == "wrapped":
            self.wrapped += 1
        elif k == "dynamic":
            self.dynamic.append((node.lineno, kind, ast.unparse(expr)[:70]))

    def visit_Call(self, node):
        name = callee(node)
        if name == "add_notification" and len(node.args) >= 3:
            self.check(node, "notification-subject", node.args[1])
            self.check(node, "notification-text", node.args[2])
        elif name == "HTTPError":
            for a in node.args[1:2]:
                self.check(node, "http-error", a)
            for kw in node.keywords:
                if kw.arg in ("log_message", "reason"):
                    self.check(node, "http-error", kw.value)
        elif name in RAISED and node.args and self.in_handler:
            self.check(node, "exception-text", node.args[0])
        elif name == "write" and node.args:
            k, t = classify(node.args[0])
            if k == "literal" and " " in t.strip():
                self.found.append((node.lineno, "write", t))
        self.generic_visit(node)

    def visit_Assert(self, node):
        if node.msg is not None and self.in_handler:
            self.check(node, "assert-text", node.msg)
        self.generic_visit(node)

    def visit_Assign(self, node):
        # module/class-level prose constants that were not wrapped in N_
        if self.depth == 0 or self.in_class_body:
            for v in unwrapped_constants(node.value):
                if " " in v.strip() and v[:1].isupper():
                    self.found.append((node.lineno, "constant", v))
        self.generic_visit(node)

    depth = 0
    in_class_body = False
    in_handler = False

    def visit_FunctionDef(self, node):
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1
    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        old, self.in_class_body = self.in_class_body, True
        self.generic_visit(node)
        self.in_class_body = old


def lint(path):
    v = Visitor()
    v.in_handler = "handlers" in path.parts
    v.visit(ast.parse(path.read_text(encoding="utf-8")))
    return v


def main():
    per, kinds, wrapped, dyn = {}, Counter(), 0, 0
    for f in sorted(ADMIN.rglob("*.py")):
        if f.name == "__init__.py":
            continue
        v = lint(f)
        per[f.relative_to(ADMIN)] = v
        kinds.update(k for _, k, _ in v.found)
        wrapped += v.wrapped
        dyn += len(v.dynamic)
    if "--list" in sys.argv:
        for f, v in per.items():
            for l, k, t in v.found:
                print(f"{f}:{l}: {k}: {t[:90]!r}")
    if "--dynamic" in sys.argv:
        for f, v in per.items():
            for l, k, t in v.dynamic:
                print(f"{f}:{l}: DYNAMIC {k}: {t}")
    print("\nFINDINGS PER FILE")
    for f, v in sorted(per.items(), key=lambda kv: -len(kv[1].found)):
        if v.found or v.wrapped:
            print(f"{len(v.found):5d}  {f}   (already wrapped sinks: {v.wrapped}, dynamic: {len(v.dynamic)})")
    print("TOTAL", sum(len(v.found) for v in per.values()), dict(kinds),
          "wrapped sinks", wrapped, "dynamic sinks", dyn)


if __name__ == "__main__":
    main()
