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

"""Guards of the AWS translation work (change aws-language-rest).

They need no database. A file is checked strictly when it carries the
marker "aws-i18n: complete" (see aws_i18n_lint.py), or when the
environment variable AWS_I18N_FILES lists it. Regenerate the English snapshot with
"python -m cmstestsuite.unit_tests.server.admin.aws_render" only on a
tree whose English is the reference.

"""

import difflib
import json

import pytest
from jinja2 import nodes

from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.server.admin import aws_i18n_lint as lint
from cmstestsuite.unit_tests.server.admin import aws_render


def _diff(name, mode, kind, old, new):
    return [f"{name} ({mode}) {kind}: {line}" for line in difflib.unified_diff(
        [str(x) for x in old], [str(x) for x in new], "snapshot", "now",
        lineterm="", n=0)]


def test_english_render_unchanged():
    # Scenario "A page in English is unchanged" (aws-language): the
    # visible texts and the posted values of every page, in English,
    # are those of the snapshot taken before any template changed.
    snapshot = json.loads(aws_render.SNAPSHOT.read_text(encoding="utf-8"))
    assert sorted(snapshot) == aws_render.PAGES

    problems = []
    for name, modes in snapshot.items():
        for mode, old in modes.items():
            new = aws_render.snapshot_page(name, mode == "full")
            for kind in ("error", "texts", "posts"):
                if old.get(kind) != new.get(kind):
                    problems += _diff(name, mode, kind,
                                      old.get(kind, []), new.get(kind, []))
    assert not problems, "English changed:\n" + "\n".join(problems[:60])


def test_macro_imports_take_the_context():
    # A macro imported without the context gets no per-request gettext
    # and would show its texts in English whatever the language.
    bare = []
    for name in aws_render.ALL_TEMPLATES:
        source = (aws_render.TEMPLATES / name).read_text(encoding="utf-8")
        tree = AWS_ENVIRONMENT.parse(source)
        bare += [f"{name}:{node.lineno}" for node
                 in tree.find_all((nodes.Import, nodes.FromImport))
                 if not node.with_context]
    assert not bare, "imports without 'with context': " + ", ".join(bare)


def _strict(rel):
    if not lint.is_complete(rel):
        pytest.skip("not complete yet")


def _show(findings):
    return "\n".join(f"  line {line}: {kind}: {text[:90]!r}"
                     for line, kind, text in findings)


@pytest.mark.parametrize("rel", lint.TEMPLATE_FILES)
def test_template_text_is_wrapped(rel):
    # Scenario "A new untranslated text" (aws-language)
    _strict(rel)
    findings = lint.static_findings(rel)
    assert not findings, f"{rel} has texts outside gettext:\n" \
        + _show(findings)


@pytest.mark.parametrize("rel", lint.SCRIPT_FILES + lint.TEMPLATE_FILES)
def test_script_literals_are_wrapped(rel):
    # Scenario "A new untranslated text" (aws-language)
    _strict(rel)
    findings = lint.script_findings(rel)
    assert not findings, f"{rel} has script texts outside gettext:\n" \
        + _show(findings)


@pytest.mark.parametrize("rel", lint.PYTHON_FILES)
def test_python_texts_are_wrapped(rel):
    # Scenario "A new untranslated text" (aws-language)
    _strict(rel)
    findings = lint.static_findings(rel)
    assert not findings, f"{rel} has texts outside gettext:\n" \
        + _show(findings)


@pytest.mark.parametrize("page", aws_render.PAGES)
def test_pseudo_render_leaves_no_english(page):
    # Scenario "A page group in Spanish" (aws-language): with every
    # message marked as translated, a word that is left was never
    # wrapped (or belongs to a file that is not checked yet).
    _strict("templates/" + page)
    complete = {rel for rel in lint.TEMPLATE_FILES if lint.is_complete(rel)}
    left = lint.english_left(page, complete)
    assert not left, f"{page} keeps English text: {left}"


def test_templates_compile_and_formats_are_valid():
    problems = [problem for rel in lint.TEMPLATE_FILES
                for problem in lint.template_problems(rel)]
    problems += [problem for rel in lint.PYTHON_FILES
                 for problem in lint.python_problems(rel)]
    assert not problems, "\n".join(problems)


def test_named_controls_post_untranslated_values():
    # Scenario "No translated posted values" (aws-language): with every
    # message marked as translated, no named control posts a marked
    # value, because a handler compares it with an English key.
    env = aws_render.environment(True)
    found = {}
    for page in aws_render.PAGES:
        _, posts = aws_render.extract(
            aws_render.render(env, page, aws_render.PSEUDO))
        marked = [(n, v) for n, v in posts if aws_render.OPEN in v]
        if marked:
            found[page] = marked
    assert set(found) <= lint.NAMED_CONTROLS_ALLOW, found


def test_the_allow_lists_have_no_stale_entry():
    # An entry for a text that is no longer there hides a future one.
    stale = [(rel, text) for rel, text, _ in lint.ALLOW
             if text not in [t for _, _, t in lint.raw_findings(rel)]]
    assert not stale, f"entries that match nothing: {stale}"


def test_the_guard_catches_what_it_promises():
    # Scenario "A new untranslated text" (aws-language): the checks
    # find exactly the bare texts of a synthetic file, not the wrapped.
    template = (
        '<p title="Bare title">{% trans %}Wrapped{% endtrans %}</p>\n'
        "<p>Bare text</p>\n"
        '<input type="submit" value="{{ _("Wrapped") }}">\n'
        '<script>alert("Bare script text"); var key = "some_key";</script>\n')
    assert [(kind, text) for _, kind, text in lint.template_texts(template)] \
        == [("attr-title", "Bare title"), ("text", "Bare text")]
    scripts = [text for body in lint.inline_scripts(template)
               for _, _, text in lint.script_literals(body[1])]
    assert scripts == ["Bare script text"]

    python = (
        "def post(self):\n"
        "    self.service.add_notification(\n"
        '        make_datetime(), "Bare subject", self._("Wrapped"))\n'
        '    assert x, "Bare assert"\n')
    assert [(kind, text) for _, kind, text in lint.python_texts(python)] \
        == [("notification-subject", "Bare subject"),
            ("assert-text", "Bare assert")]


def test_every_file_is_complete():
    # Skips until the last commit of the change turns it on for all.
    pending = [rel for rel in lint.ALL_FILES if not lint.is_complete(rel)]
    pytest.skip(f"switched on by the last commit: pending={len(pending)}")
