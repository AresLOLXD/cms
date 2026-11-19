"""PROTOTYPE of the pytest wiring (would live in
cmstestsuite/unit_tests/server/admin/aws_i18n_guard_test.py).

A file is IN SCOPE when its first 3 lines hold  aws-i18n: complete  (templates:
{# aws-i18n: complete #}; python/js: a comment), or when AWS_I18N_FILES lists it
(a lane before it marks the files). In-scope files must be clean; at the end
test_every_file_is_complete turns on for all.
"""
import os
import re

import pytest

import js_literals
import lint_python
from check_trans import dedupe, parse_all, python_findings, template_findings
from lint_templates import TEMPLATES, WT, lint_file
from pseudo_render import OPEN, env_for, english_left, render, translated_form_values

MARK = re.compile(r"aws-i18n:\s*complete")
EXTRA = set(filter(None, os.environ.get("AWS_I18N_FILES", "").split(",")))
ALL = sorted(str(f.relative_to(TEMPLATES)) for f in TEMPLATES.rglob("*.html"))
PAGES = [n for n in ALL if not n.startswith(("macro/", "fragments/"))]


def in_scope(name):
    return name in EXTRA or "".join((TEMPLATES / name).read_text().splitlines(True)[:3]).find("aws-i18n") >= 0 \
        and bool(MARK.search("".join((TEMPLATES / name).read_text().splitlines(True)[:3])))


@pytest.mark.parametrize("name", ALL)
def test_template_text_is_wrapped(name):
    if not in_scope(name):
        pytest.skip("not complete yet")
    assert lint_file(TEMPLATES / name) == []


@pytest.mark.parametrize("name", [n for n in ALL])
def test_script_literals_are_wrapped(name):
    if not in_scope(name):
        pytest.skip("not complete yet")
    assert [x for x in js_literals.scan() if str(x[0]) == name] == []


@pytest.mark.parametrize("name", PAGES)
def test_pseudo_render_leaves_no_english(name):
    if not in_scope(name):
        pytest.skip("not complete yet")
    outside = {t for n in ALL if not in_scope(n) for _, _, t in lint_file(TEMPLATES / n)} | {"español"}
    env = env_for(True)
    left = [t for _, _, t in english_left(render(env, name)) if t not in outside]
    assert left == []


def test_templates_compile_and_formats_are_valid():
    errors, trees = parse_all()
    assert errors == []
    assert template_findings(trees) == []
    assert python_findings() == []


def test_named_controls_post_untranslated_values():
    env = env_for(True)
    found = {n: v for n in PAGES if (v := translated_form_values(render(env, n)))}
    # reviewed: the handler accepts both the label and the English key
    assert set(found) <= {"ranking_group.html", "ranking_groups.html"}


def test_the_guard_catches_what_it_promises():
    from lint_templates import skeleton, Linter
    from check_trans import ENV
    lt = Linter(); lt.feed(skeleton('<p title="Hi there">Hello</p>{% trans %}ok{% endtrans %}'))
    assert [k for _, k, _ in lt.findings] == ["attr-title", "text"]


def test_every_file_is_complete():
    pytest.skip("switched on by the last integration commit: pending=%d" %
                sum(not in_scope(n) for n in ALL))
