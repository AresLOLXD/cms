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

"""Guards that every text of AWS goes through gettext.

They need no database. Every template, script and Python file of
cms/server/admin is checked (see aws_i18n_lint.py). An English text
that a change adds without gettext makes one of these tests fail:
wrap it with _() or N_() and update the catalog, see
docs/Localization.rst.

"""

import pytest
from jinja2 import nodes

from cms.grading.ParameterTypes import ParameterTypeChoice
from cms.grading.tasktypes import TASK_TYPES
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.server.admin import aws_i18n_lint as lint
from cmstestsuite.unit_tests.server.admin import aws_render


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


def _show(findings):
    return "\n".join(f"  line {line}: {kind}: {text[:90]!r}"
                     for line, kind, text in findings)


@pytest.mark.parametrize("rel", lint.TEMPLATE_FILES)
def test_template_text_is_wrapped(rel):
    # Scenario "A new untranslated text" (aws-language)
    findings = lint.static_findings(rel)
    assert not findings, f"{rel} has texts outside gettext:\n" \
        + _show(findings)


@pytest.mark.parametrize("rel", lint.SCRIPT_FILES + lint.TEMPLATE_FILES)
def test_script_literals_are_wrapped(rel):
    # Scenario "A new untranslated text" (aws-language)
    findings = lint.script_findings(rel)
    assert not findings, f"{rel} has script texts outside gettext:\n" \
        + _show(findings)


@pytest.mark.parametrize("rel", lint.PYTHON_FILES)
def test_python_texts_are_wrapped(rel):
    # Scenario "A new untranslated text" (aws-language)
    findings = lint.static_findings(rel)
    assert not findings, f"{rel} has texts outside gettext:\n" \
        + _show(findings)


@pytest.mark.parametrize("page", aws_render.PAGES)
def test_pseudo_render_leaves_no_english(page):
    # Scenario "A page group in Spanish" (aws-language): with every
    # message marked as translated, a word that is left was never
    # wrapped.
    left = lint.english_left(page)
    assert not left, f"{page} keeps English text: {left}"


@pytest.mark.parametrize("page", aws_render.PAGES)
def test_every_page_renders(page):
    # A page that raises in every mode would escape the render guards.
    assert list(aws_render.renders(page, aws_render.PSEUDO)), page


def test_pseudo_render_ignores_installed_task_types(monkeypatch):
    # A plugin such as cms_rekarel adds task types whose English
    # parameter names are not ours: the guard must pass with them.
    class PluginTask:
        ACCEPTED_PARAMETERS = [ParameterTypeChoice(
            "Strange grader mode", "mode", "A strange description.",
            {"zz": "Zeta option"})]

    monkeypatch.setitem(TASK_TYPES, "PluginTask", PluginTask)
    shown = list(aws_render.renders("add_dataset.html", aws_render.PSEUDO))
    assert any("Strange grader mode" in html for html in shown)
    assert not lint.english_left("add_dataset.html")


def test_templates_compile_and_formats_are_valid():
    problems = [problem for rel in lint.TEMPLATE_FILES
                for problem in lint.template_problems(rel)]
    problems += [problem for rel in lint.PYTHON_FILES
                 for problem in lint.python_problems(rel)]
    assert not problems, "\n".join(problems)


def _marked_controls(page, htmls):
    """Return the (page, name) of the controls that post a marked value."""
    return {(page, name) for html in htmls
            for name, value in aws_render.extract(html)[1]
            if aws_render.OPEN in value}


def test_named_controls_post_untranslated_values():
    # Scenario "No translated posted values" (aws-language): with every
    # message marked as translated, no named control posts a marked
    # value, because a handler compares it with an English key.
    found = set()
    for page in aws_render.PAGES:
        found |= _marked_controls(
            page, aws_render.renders(page, aws_render.PSEUDO))
    assert found <= lint.NAMED_CONTROLS_ALLOW, found


def test_named_controls_allow_is_per_control():
    # Mutant: a translated operation button on the exempt page is not
    # covered by the exemption of its subject field.
    env = aws_render.environment("full")
    html = env.from_string(
        '<input name="message_subject" value="{{ _("Subject") }}">'
        '<button name="operation" value="{{ _("Activate!") }}">x</button>'
    ).render(gettext=aws_render.PSEUDO.gettext)
    found = _marked_controls("activate_dataset.html", [html])
    assert found == {("activate_dataset.html", "message_subject"),
                     ("activate_dataset.html", "operation")}
    assert not found <= lint.NAMED_CONTROLS_ALLOW


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


def test_the_guard_catches_what_it_promises(tmp_path, monkeypatch):
    # Scenario "A new untranslated text" (aws-language): the checks
    # find exactly the bare texts of a synthetic file, not the wrapped.
    # A synthetic helper module returns bare prose, as the importer did
    # before it was wrapped.
    (tmp_path / "bare_prose_helper.py").write_text(
        "def import_things(zip_file):\n"
        "    if zip_file:\n"
        '        return "Bare helper success %d", 1\n'
        '    return "Bare helper failure", None\n',
        encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
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
        "from bare_prose_helper import import_things\n"
        "def post(self):\n"
        "    self.service.add_notification(\n"
        '        make_datetime(), "Bare subject", self._("Wrapped"))\n'
        '    assert x, "Bare assert"\n'
        '    self.service.add_notification(\n'
        '        subject="Bare keyword subject", text=self._("Wrapped"),\n'
        '        timestamp=1)\n'
        '    message = "Bare assigned text"\n'
        '    self.service.add_notification(1, self._("Wrapped"), message)\n'
        '    subject, text = import_things(1)\n'
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
            ("helper-return", "Bare helper failure"),
            ("helper-return", "Bare helper success %d"),
            ("dict-text", "Bare dict text"),
            ("r_params-text", "Bare params text"),
            ("r_params-text", "Bare %s params")]


def test_raised_texts_are_checked_outside_the_handlers(monkeypatch):
    # Scenario "A new untranslated text" (aws-language): the jobs and
    # helpers raise the texts that the handlers show, so their bare
    # raised and asserted texts are found like those of a handler.
    source = ('def f(x):\n    assert x, "Bare assert text"\n'
              '    raise ValueError("Bare raised text")\n')
    monkeypatch.setattr(lint, "read", lambda rel: source)

    for rel in ("exportjobs.py", "handlers/base.py"):
        assert [(kind, text) for _, kind, text in lint.raw_findings(rel)] \
            == [("assert-text", "Bare assert text"),
                ("exception-text", "Bare raised text")]


def test_every_script_is_checked():
    # The templates and Python files are found on disk; the scripts are
    # listed, so a new one must be added to SCRIPT_FILES. The vendored
    # ones (jq, prism) are not ours.
    on_disk = {str(f.relative_to(lint.ADMIN))
               for f in (lint.ADMIN / "static").glob("*.js")
               if f.name != "prism.js"}
    assert on_disk == set(lint.SCRIPT_FILES)
