#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check that a lane of aws-language-rest touched only what it owns.

Usage:
    python3 -I check_lane.py LANE [--base BRANCH]   # git diff BASE...HEAD
    python3 -I check_lane.py --pending LANE         # files without the marker
    python3 -I check_lane.py --selftest

The owned files are in lanes.json next to this file. A lane may change
its files, the existing tests that lanes.json lists for it, its fragment,
its test file and its plan, and nothing else (the shared files are
named in lanes.json). Exit status: 0 when it is clean, 1 otherwise.
Standard library only.
"""
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
MARKER = re.compile(r"aws-i18n:\s*complete")


def load():
    return json.loads((HERE / "lanes.json").read_text(encoding="utf-8"))


def allowed_paths(lane_data):
    """Return the patterns of the paths that the lane may change."""
    return (lane_data["files"] + lane_data["tests"]
            + [lane_data["fragment"], lane_data["test"], lane_data["plan"]])


def matches(path, pattern):
    return fnmatch.fnmatchcase(path, pattern)


def check(changed, lane, data):
    """Return the problems of the changed paths for the lane."""
    lane_data = data["lanes"][lane]
    allowed = allowed_paths(lane_data)
    owner = {}
    for other, other_data in data["lanes"].items():
        for path in other_data["files"]:
            owner[path] = other
    problems = []
    for path in changed:
        if any(matches(path, pattern) for pattern in allowed):
            continue
        if any(matches(path, pattern) for pattern in data["shared"]):
            problems.append(f"{path}: shared file, only the coordinator edits it")
        elif path in owner:
            problems.append(f"{path}: belongs to lane {owner[path]}")
        else:
            problems.append(f"{path}: not in the file list of {lane}")
    return problems


def has_marker(path):
    """Tell whether the file carries the marker "aws-i18n: complete"
    (the first 3 lines of a template or script, anywhere in Python)."""
    file = ROOT / path
    if not file.is_file():
        return False
    text = file.read_text(encoding="utf-8")
    if path.endswith(".py"):
        return bool(MARKER.search(text))
    return bool(MARKER.search("".join(text.splitlines(True)[:3])))


def pending(lane, data):
    """Return the files of the lane that lack the marker."""
    return [path for path in data["lanes"][lane]["files"]
            if not has_marker(path)]


def selftest():
    data = {
        "shared": ["cms/locale/es/LC_MESSAGES/aws.po", "docs/*"],
        "lanes": {
            "L1": {"files": ["a/one.html"], "tests": ["t/one_test.py"],
                   "fragment": "f/aws-L1.po", "test": "t/translated_one_test.py",
                   "plan": "p/*-L1.md"},
            "L2": {"files": ["a/two.html"], "tests": [], "fragment": "f/aws-L2.po",
                   "test": "t/translated_two_test.py", "plan": "p/*-L2.md"},
        }}
    assert check([], "L1", data) == []
    assert check(["a/one.html", "t/one_test.py", "f/aws-L1.po",
                  "t/translated_one_test.py", "p/2026-11-16-L1.md"],
                 "L1", data) == []
    assert len(check(["a/two.html"], "L1", data)) == 1
    assert "belongs to lane L2" in check(["a/two.html"], "L1", data)[0]
    assert "shared file" in check(["cms/locale/es/LC_MESSAGES/aws.po"], "L1", data)[0]
    assert "shared file" in check(["docs/x.rst"], "L1", data)[0]
    assert "not in the file list" in check(["a/three.html"], "L1", data)[0]
    assert len(check(["f/aws-L2.po", "p/x-L2.md"], "L1", data)) == 2
    print("selftest ok")
    return 0


def main(argv):
    if argv == ["--selftest"]:
        return selftest()
    data = load()
    if len(argv) == 2 and argv[0] == "--pending" and argv[1] in data["lanes"]:
        files = pending(argv[1], data)
        print(f"{argv[1]}: {len(files)} of "
              f"{len(data['lanes'][argv[1]]['files'])} files lack the marker")
        for path in files:
            print(f"  {path}")
        return 0
    base = data["base"]
    if len(argv) == 3 and argv[1] == "--base":
        base = argv[2]
        argv = argv[:1]
    if len(argv) != 1 or argv[0] not in data["lanes"]:
        print(__doc__)
        return 2
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"], cwd=ROOT,
        capture_output=True, text=True)
    if out.returncode:
        print(out.stderr.strip())
        return 2
    problems = check(out.stdout.split(), argv[0], data)
    for problem in problems:
        print(problem)
    print(f"{argv[0]}: {len(problems)} paths outside its list")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
