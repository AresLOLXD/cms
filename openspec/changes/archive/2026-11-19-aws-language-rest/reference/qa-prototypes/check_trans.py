"""PROTOTYPE guard (3): gettext usage that breaks at runtime (or silently) and
msgid dedupe.

 a. every AWS template compiles (a {{ expr }} inside {% trans %} is a
    TemplateSyntaxError, but only when the page is first loaded).
 b. {% trans %} blocks that reference variables must not contain printf specs
    (%d, %s, %(x)s typed by hand): Jinja doubles them -> shown literally.
 c. msgid % args must be a valid format string (an unescaped % raises at render);
    a msgid with %% that is NOT %-formatted shows two percent signs.
 d. the .po translations keep the placeholders of the msgid (babel checkers).
 e. dedupe: msgids that differ only by case / trailing punctuation / spacing.
"""
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

from jinja2 import nodes

from lint_templates import ENV, TEMPLATES, WT

SPEC = re.compile(r"%(?:\(\w+\)[sdif]|[sdifr])")
GETTEXT = {"_", "gettext", "N_", "n_", "ngettext"}


def parse_all():
    """Compile every template; return (errors, trees)."""
    errors, trees = [], {}
    for f in sorted(TEMPLATES.rglob("*.html")):
        rel = f.relative_to(TEMPLATES)
        try:
            ENV.get_template(str(rel))                      # compile
            trees[rel] = ENV.parse(f.read_text(encoding="utf-8"))
        except Exception as e:                              # TemplateSyntaxError
            errors.append((rel, type(e).__name__, str(e)))
    return errors, trees


def dummy_args(msgid):
    """Arguments of the right type for every spec in msgid (1 for %d, 'x' else)."""
    plain = msgid.replace("%%", "")
    named = re.findall(r"%\((\w+)\)([sdif])", plain)
    if named:
        return {n: (1 if t in "dif" else "x") for n, t in named}
    return tuple(1 if t in "dif" else "x" for t in re.findall(r"%([sdifr])", plain))


def format_problem(msgid, has_mod):
    """Why msgid is wrong for the way it is used, or None."""
    if has_mod:
        try:
            msgid % dummy_args(msgid)
        except Exception as e:
            return f"invalid format: {e}"
    elif "%%" in msgid:
        return "has %% but is never %-formatted: shows two percent signs"
    return None


def unwrap(node):
    """A trans block with variables is Mod(MarkSafeIfAutoescape(Call), Dict)."""
    return node.expr if isinstance(node, nodes.MarkSafeIfAutoescape) else node


def template_findings(trees):
    out = []
    for rel, tree in trees.items():
        # trans blocks: Mod(Call(gettext, Const), Dict) when they reference variables
        for mod in tree.find_all(nodes.Mod):
            call = unwrap(mod.left)
            if isinstance(call, nodes.Call) and isinstance(call.node, nodes.Name) \
                    and call.node.name in GETTEXT and call.args \
                    and isinstance(call.args[0], nodes.Const):
                msgid = call.args[0].value
                # the hand-typed printf in a trans block with variables was doubled
                if re.search(r"%%(?:\(\w+\))?[sdif]", msgid):
                    out.append((rel, call.lineno, "printf typed inside a {% trans %} that has variables", msgid))
                p = format_problem(msgid, True)
                if p:
                    out.append((rel, call.lineno, p, msgid))
        # gettext calls that are not the left side of a Mod
        mods = {id(unwrap(m.left)) for m in tree.find_all(nodes.Mod)}
        for call in tree.find_all(nodes.Call):
            if isinstance(call.node, nodes.Name) and call.node.name in GETTEXT \
                    and call.args and isinstance(call.args[0], nodes.Const) \
                    and isinstance(call.args[0].value, str) and id(call) not in mods:
                p = format_problem(call.args[0].value, False)
                if p:
                    out.append((rel, call.lineno, p, call.args[0].value))
    return out


def python_findings():
    out = []
    for f in sorted((WT / "cms/server/admin").rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod):
                mods.add(id(n.left))
                c = n.left
                if isinstance(c, ast.Call) and getattr(c.func, "attr", getattr(c.func, "id", None)) in GETTEXT \
                        and c.args and isinstance(c.args[0], ast.Constant):
                    msgid = c.args[0].value
                    try:
                        args = dummy_args(msgid)
                        right = n.right      # arity of the literal tuple must match
                        if isinstance(right, ast.Tuple) and isinstance(args, tuple) \
                                and len(right.elts) != len(args):
                            raise TypeError("%d specs, %d arguments" % (len(args), len(right.elts)))
                        msgid % args
                    except Exception as e:
                        out.append((f.relative_to(WT), c.lineno, f"invalid format: {e}", msgid))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", None)) in GETTEXT \
                    and n.args and isinstance(n.args[0], ast.Constant) \
                    and isinstance(n.args[0].value, str) and id(n) not in mods:
                p = format_problem(n.args[0].value, False)
                if p:
                    out.append((f.relative_to(WT), n.lineno, p, n.args[0].value))
    return out


def norm(msgid):
    m = re.sub(r"%\(\w+\)s|%[sdi]", "#", msgid)
    return re.sub(r"[\W_]+", " ", m.casefold()).strip()


def dedupe(msgids):
    groups = defaultdict(set)
    for m in msgids:
        groups[norm(m)].add(m)
    return {k: sorted(v) for k, v in groups.items() if len(v) > 1}


def catalog_problems():
    from babel.messages.catalog import TranslationError
    from babel.messages.pofile import read_po
    with open(WT / "cms/locale/es/LC_MESSAGES/aws.po", "rb") as f:
        cat = read_po(f)
    bad = []
    for m in cat:
        if m.id:
            try:
                m.check(cat)
            except TranslationError as e:
                bad.append((m.id if isinstance(m.id, str) else m.id[0], str(e)))
    return bad


if __name__ == "__main__":
    errors, trees = parse_all()
    print("a. templates that do not compile:", errors or "none", f"({len(trees)} compiled)")
    t = template_findings(trees)
    p = python_findings()
    print("b/c. template findings:", t or "none")
    print("b/c. python findings:", p or "none")
    print("d. catalog placeholder problems:", catalog_problems() or "none")
    import os
    sys.path.insert(0, str(WT))
    from cmstestsuite.unit_tests.locale.catalog_test import extract_code_msgids
    ids = extract_code_msgids("aws")
    print(f"e. msgids in the code: {len(ids)}; near-duplicate groups:", dedupe(ids) or "none")
