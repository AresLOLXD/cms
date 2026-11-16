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
"python -m cmstestsuite.unit_tests.server.admin.aws_snapshot" only on a
tree whose English is the reference.

"""

import difflib
import json

import pytest
from jinja2 import nodes

from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.server.admin import aws_i18n_lint as lint
from cmstestsuite.unit_tests.server.admin import aws_render, aws_snapshot


# The pages that do not render in a mode: the stand-ins of the mode are
# not data that the page can use. Pinned, so that a regenerated snapshot
# cannot enshrine a new error silently.
KNOWN_RENDER_ERRORS = {("submission.html", "empty"): "ValueError"}


def _diff(name, kind, old, new):
    return [f"{name} {kind}: {line}" for line in difflib.unified_diff(
        [str(x) for x in old], [str(x) for x in new], "snapshot", "now",
        lineterm="", n=0)]


def _snapshot():
    return json.loads(aws_snapshot.SNAPSHOT.read_text(encoding="utf-8"))


def test_english_render_unchanged():
    # Scenario "A page in English is unchanged" (aws-language): the
    # visible texts, scripts and posted values of every page, in
    # English, are those of the snapshot taken before any template
    # changed (main 676a397b), in each mode.
    snapshot = _snapshot()["rendered"]
    unknown = sorted(set(aws_render.PAGES) - set(snapshot))
    assert not unknown, (
        f"pages {unknown} are not in the snapshot: a lane may not add a "
        "page template (ask the coordinator)")
    assert sorted(snapshot) == aws_render.PAGES

    problems, errors = [], {}
    for name, modes in snapshot.items():
        assert sorted(modes) == sorted(aws_render.MODES)
        for mode, old in modes.items():
            new = aws_snapshot.rendered_page(name, mode)
            if "error" in new:
                errors[(name, mode)] = new["error"]
            for kind in ("error", "texts", "posts"):
                if old.get(kind) != new.get(kind):
                    problems += _diff(f"{name} ({mode})", kind,
                                      old.get(kind, []), new.get(kind, []))
    assert errors == KNOWN_RENDER_ERRORS
    assert not problems, "English changed:\n" + "\n".join(problems[:60])


def test_english_static_unchanged():
    # Scenario "A page in English is unchanged" (aws-language): the
    # texts of every template and static script as written, with a
    # trans block or a gettext call as its message, are those of the
    # snapshot, whichever branch a rendering reaches.
    snapshot = _snapshot()["static"]
    unknown = sorted(set(lint.TEMPLATE_FILES + lint.SCRIPT_FILES)
                     - set(snapshot))
    assert not unknown, (
        f"files {unknown} are not in the snapshot: a lane may not add a "
        "template (ask the coordinator)")
    problems = []
    for rel, old in snapshot.items():
        new = lint.static_records(rel)
        if old != new:
            problems += _diff(rel, "static", old, new)
    assert not problems, "English changed:\n" + "\n".join(problems[:60])


def test_the_snapshot_sees_what_it_promises():
    # Scenario "A page in English is unchanged" (aws-language): wrapping
    # a text leaves its record as it was; changing it, losing a space
    # next to a tag, a <br> or the kind of a text does not.
    plain = (
        'Download as <a href="x">csv</a>.<br>\n'
        '<p title="Tip">Hello,   {{ name }}!</p>\n'
        '<input type="submit" value="Send">\n'
        '<button>Go</button>\n'
        '<textarea>  Raw\n text </textarea>\n'
        '<a onclick="return confirm(\'Really?\');">x</a>\n'
        '<script>var m = "Done here.";</script>\n'
        '{{ "Remove" if a else "Add" }}')
    wrapped = (
        '{% trans %}Download as{% endtrans %} <a href="x">csv</a>.<br>\n'
        '<p title="{{ _("Tip") }}">{% trans name=name %}Hello, {{ name }}!'
        '{% endtrans %}</p>\n'
        '<input type="submit" value="{{ _("Send") }}">\n'
        '<button>{% trans %}Go{% endtrans %}</button>\n'
        '<textarea>  Raw\n text </textarea>\n'
        '<a onclick="return confirm({{ _("Really?")|tojson|forceescape }});">'
        'x</a>\n'
        '<script>var m = {{ _("Done here.")|tojson }};</script>\n'
        '{{ _("Remove") if a else _("Add") }}')
    record = lint.source_records(plain)
    assert record == lint.source_records(wrapped)
    assert "js: Done here." in record and "js-attr: Really?" in record
    assert "label: Send" in record and "label: Go" in record
    assert "textarea:   Raw\n text " in record
    for change in (
            ("Download as <a", "Download as<a"),
            ("</a>.<br>", "</a>."),
            ("Hello,", "Hi,"),
            ('title="Tip"', 'placeholder="Tip"'),
            ("Download as", "Download\u00a0as"),
            ("Raw\n text", "Raw text"),
            ("Really?", "Sure?"),
            ("Done here.", "Done there."),
            ('"Add"', '"Plus"')):
        assert lint.source_records(plain.replace(*change)) != record, change
    # What a person reflowing the html does not change.
    assert lint.source_records(plain.replace(
        "Hello,   ", "Hello,\n ")) == record


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
    found = {}
    for page in aws_render.PAGES:
        for html in aws_render.renders(page, aws_render.PSEUDO):
            _, posts = aws_render.extract(html)
            marked = [(n, v) for n, v in posts if aws_render.OPEN in v]
            if marked:
                found.setdefault(page, []).extend(marked)
    assert set(found) <= lint.NAMED_CONTROLS_ALLOW, found


def test_the_allow_lists_have_no_stale_entry():
    # An entry for a text that is no longer there hides a future one.
    # The texts of the inline scripts count too.
    stale = lint.stale_entries(lint.ALLOW)
    assert not stale, f"entries that match nothing: {stale}"


def test_the_stale_check_sees_script_literals():
    # An entry for a literal of an inline script is not stale; one that
    # matches nothing still is.
    source = ('<p>Bare text</p>\n<script>document.cookie = "a="'
              ' + "; path=/";</script>')

    def known(rel):
        return ({t for _, _, t in lint.template_texts(source)}
                | {t for body in lint.inline_scripts(source)
                   for _, _, t in lint.script_literals(body[1])})

    allow = [("x.html", "; path=/", "cookie attribute"),
             ("x.html", "Bare text", "template text"),
             ("x.html", "gone", "nothing")]
    assert "; path=/" in known("x.html")
    assert lint.stale_entries(allow, known) == [("x.html", "gone")]


def test_the_guard_catches_what_it_promises():
    # Scenario "A new untranslated text" (aws-language): the checks
    # find exactly the bare texts of a synthetic file, not the wrapped.
    template = (
        '<p title="Bare title">{% trans %}Wrapped{% endtrans %}</p>\n'
        "<p>Bare text</p>\n"
        '<input type="submit" value="{{ _("Wrapped") }}">\n'
        '<script>alert("Bare script text"); var key = "some_key";</script>\n'
        '<p>{{ fmt(text="Bare keyword text") }}</p>\n'
        '<p>{{ ["Bare first", "Bare second"][i] }}</p>\n'
        '{% for x in ["Bare loop one", "Bare loop two"] %}{% endfor %}\n'
        '<p>NOTE</p><p>OK</p><p>UTC</p><p>YYYY-MM-DD</p>\n'
        '<button onclick="alert(message)">{{ _("Wrapped") }}</button>\n'
        '<button onclick="showMessage(\'Bare event text\')">x</button>\n'
        '<button onclick="return confirm({{ _(\'Wrapped\')|tojson }});">'
        'x</button>\n'
        '<input type="hidden" name="a" value="Task {{ n }} was rejudged">\n'
        '<input type="hidden" name="op" value="validate">\n')
    assert [(kind, text) for _, kind, text in lint.template_texts(template)] \
        == [("attr-title", "Bare title"), ("text", "Bare text"),
            ("text", "NOTE"), ("text", "OK"),
            ("js-attr-alert", "message"), ("js-attr", "Bare event text"),
            ("attr-value-prose", "Task was rejudged"),
            ("expr-literal", "Bare keyword text"),
            ("expr-literal", "Bare first"), ("expr-literal", "Bare second"),
            ("for-literal", "Bare loop one"),
            ("for-literal", "Bare loop two")]
    scripts = [text for body in lint.inline_scripts(template)
               for _, _, text in lint.script_literals(body[1])]
    assert scripts == ["Bare script text"]
    # a single word is a text where the script shows it
    assert [text for _, _, text in lint.script_literals(
        "el.textContent = 'done'; var kind = 'done';"
        "$(x).html('x' + 'loading'); say('Done.'); say('Compiling...');"
        "var f = 'a.b'")] == ["done", "loading", "Done.", "Compiling..."]

    python = (
        "from cmscommon.importers import import_testcases_from_zipfile\n"
        "def post(self):\n"
        "    self.service.add_notification(\n"
        '        make_datetime(), "Bare subject", self._("Wrapped"))\n'
        '    assert x, "Bare assert"\n'
        '    self.service.add_notification(\n'
        '        subject="Bare keyword subject", text=self._("Wrapped"),\n'
        '        timestamp=1)\n'
        '    message = "Bare assigned text"\n'
        '    self.service.add_notification(1, self._("Wrapped"), message)\n'
        '    subject, text = import_testcases_from_zipfile(1)\n'
        '    self.service.add_notification(1, subject, text)\n'
        '    self.write({"error": "Bare dict text", "code": 3})\n'
        '    self.r_params["a"][1] = "Bare params text"\n'
        '    self.r_params["b"] = self._("Wrapped params text")\n'
        '    r_params["c"] = "Bare %s params" % 3\n')
    assert [(kind, text) for _, kind, text in lint.python_texts(python)] \
        == [("notification-subject", "Bare subject"),
            ("assert-text", "Bare assert"),
            ("notification-subject", "Bare keyword subject"),
            ("notification-text", "Bare assigned text"),
            ("helper-return", "Successfully added %d and overwritten %d "
             "testcase(s)"),
            ("helper-return", "Added: %s; overwritten: %s; skipped: %s"),
            ("dict-text", "Bare dict text"),
            ("r_params-text", "Bare params text"),
            ("r_params-text", "Bare %s params")]


def test_every_file_is_complete():
    # Skips until the last commit of the change turns it on for all.
    pending = [rel for rel in lint.ALL_FILES if not lint.is_complete(rel)]
    pytest.skip(f"switched on by the last commit: pending={len(pending)}")
