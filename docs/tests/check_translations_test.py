"""Tests for docs/check_translations.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_translations import check  # noqa: E402

HEADER = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=utf-8\\n"
"Language: es\\n"

'''
FUZZY_HEADER = "#, fuzzy\n" + HEADER

TEMPLATE = HEADER + '''msgid "How it works"
msgstr ""

msgid "Run `./up.sh` and set ``CMS_WORKER_COUNT``."
msgstr ""
'''

GOOD = HEADER + '''msgid "How it works"
msgstr "Cómo funciona"

msgid "Run `./up.sh` and set ``CMS_WORKER_COUNT``."
msgstr "Ejecuta `./up.sh` y define ``CMS_WORKER_COUNT``."
'''


def write(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


def run(tmp_path: Path, po_text: str | None, pot_text: str = TEMPLATE) -> list[str]:
    pot_dir, po_dir = tmp_path / "pot", tmp_path / "po"
    write(pot_dir, "page.pot", pot_text)
    po_dir.mkdir(parents=True, exist_ok=True)
    if po_text is not None:
        write(po_dir, "page.po", po_text)
    return check(pot_dir, po_dir)


def test_complete_translation_passes(tmp_path):
    assert run(tmp_path, GOOD) == []


def test_missing_po_file_is_reported(tmp_path):
    problems = run(tmp_path, None)
    assert len(problems) == 1 and "no .po file" in problems[0]


def test_po_without_page_is_reported(tmp_path):
    write(tmp_path / "po", "gone.po", GOOD)
    problems = run(tmp_path, GOOD)
    assert len(problems) == 1 and "gone.po" in problems[0]


def test_untranslated_message_is_reported(tmp_path):
    po = GOOD.replace('msgstr "Cómo funciona"', 'msgstr ""')
    problems = run(tmp_path, po)
    assert len(problems) == 1 and "not translated" in problems[0]


def test_fuzzy_message_is_reported(tmp_path):
    po = GOOD.replace('msgid "How it works"', '#, fuzzy\nmsgid "How it works"')
    problems = run(tmp_path, po)
    assert len(problems) == 1 and "fuzzy" in problems[0]


def test_fuzzy_header_is_reported(tmp_path):
    # Sphinx ignores a whole catalog whose header is fuzzy, silently.
    po = GOOD.replace(HEADER, FUZZY_HEADER, 1)
    problems = run(tmp_path, po)
    assert len(problems) == 1 and "header" in problems[0]


def test_message_missing_from_po_is_reported(tmp_path):
    pot = TEMPLATE + '\nmsgid "New paragraph"\nmsgstr ""\n'
    problems = run(tmp_path, GOOD, pot)
    assert len(problems) == 1 and "missing" in problems[0]


def test_dropped_code_span_is_reported(tmp_path):
    po = GOOD.replace("``CMS_WORKER_COUNT``", "``CMS_NUMERO_DE_WORKERS``")
    problems = run(tmp_path, po)
    assert len(problems) == 1 and "CMS_WORKER_COUNT" in problems[0]


def test_link_and_role_text_may_be_translated(tmp_path):
    pot = HEADER + (
        'msgid "See :doc:`task types <Task types>` and '
        '`USACO <http://usaco.org/>`_."\nmsgstr ""\n')
    po = HEADER + (
        'msgid "See :doc:`task types <Task types>` and '
        '`USACO <http://usaco.org/>`_."\n'
        'msgstr "Ver :doc:`tipos de problema <Task types>` y '
        '`USACO <http://usaco.org/>`_."\n')
    assert run(tmp_path, po, pot) == []


def test_code_spans_next_to_an_underscore_are_code(tmp_path):
    # "` or `_" must not be taken for a link: `-` and `_` are both code.
    pot = HEADER + 'msgid "Use `-` or `_` in `olim`."\nmsgstr ""\n'
    po = HEADER + ('msgid "Use `-` or `_` in `olim`."\n'
                   'msgstr "Usa `-` o `_` en `olim`."\n')
    assert run(tmp_path, po, pot) == []
    po_without = po.replace("`_`", "guion bajo")
    problems = run(tmp_path, po_without, pot)
    assert len(problems) == 1 and "`_`" in problems[0]
