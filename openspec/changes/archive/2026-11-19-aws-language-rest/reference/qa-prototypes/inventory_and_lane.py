"""PROTOTYPE of guard (5): the per-file ratchet baseline and the lane check.

  inventory_and_lane.py inventory          -> writes baseline.json {file: n_findings}
  inventory_and_lane.py lane F1 F2 ...     -> exit 1 if a lane file still has findings
                                              (static + js + python) or owns a leftover
                                              English text in the pseudo render
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from jinja2 import nodes
import js_literals, lint_python
from lint_templates import TEMPLATES, WT, lint_file
from pseudo_render import run

HERE = Path(__file__).parent


def counts():
    c = defaultdict(int)
    for f in sorted(TEMPLATES.rglob("*.html")):
        n = len(lint_file(f))
        if n:
            c["templates/" + str(f.relative_to(TEMPLATES))] += n
    for rel, ln, _ in js_literals.scan():
        c[("templates/" if str(rel).endswith(".html") else "") + str(rel) + "#js"] += 1
    for f in sorted((lint_python.ADMIN).rglob("*.py")):
        n = len(lint_python.lint(f).found)
        if n:
            c[str(f.relative_to(lint_python.ADMIN))] += n
    return dict(c)


def lane(files):
    bad = []
    base = counts()
    for f in files:
        for key, n in base.items():
            if key.split("#")[0] == f or key.split("#")[0] == "templates/" + f:
                bad.append((key, n))
    # pseudo render of the lane's pages: whatever English is left must belong
    # to a template that is not in the lane (base chrome, shared macros) -> the
    # shared lane (base.html, macro/, fragments/) must merge first
    res = run()
    outside = {t for f in TEMPLATES.rglob("*.html")
               if str(f.relative_to(TEMPLATES)) not in files
               for _, _, t in lint_file(f)} | {"español"}
    for page in files:
        r = res.get(page)
        if r is None or isinstance(r["full"], str):
            continue
        for _, kind, text in r["full"] + r["empty"]:
            if text not in outside:
                bad.append((f"pseudo:{page}", text[:60]))
    return bad


if __name__ == "__main__":
    if sys.argv[1] == "inventory":
        b = counts()
        (HERE / "baseline.json").write_text(json.dumps(dict(sorted(b.items())), indent=1))
        print(len(b), "entries,", sum(b.values()), "findings")
    else:
        bad = lane(sys.argv[2:])
        for b in bad[:25]:
            print(b)
        print("lane clean" if not bad else f"{len(bad)} problems")
        sys.exit(1 if bad else 0)
