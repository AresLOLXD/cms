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


def run(
    tmp_path: Path,
    po_text: str | None,
    pot_text: str = TEMPLATE,
) -> list[str]:
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


def test_file_role_content_change_is_reported(tmp_path):
    pot = HEADER + 'msgid "See :file:`/etc/cms.toml` for config."\nmsgstr ""\n'
    po = (HEADER + 'msgid "See :file:`/etc/cms.toml` for config."\n'
          'msgstr "Ver :file:`/etc/cms-es.toml` para configurar."\n')
    problems = run(tmp_path, po, pot)
    assert len(problems) == 1 and "/etc/cms.toml" in problems[0]


def test_literal_brace_equals_colon_is_not_a_role(tmp_path):
    pot = HEADER + 'msgid "Set ``{a}={b}:{C}`` in the config."\nmsgstr ""\n'
    po = (HEADER + 'msgid "Set ``{a}={b}:{C}`` in the config."\n'
          'msgstr "Define ``{a}={b}:{C}`` en la configuración."\n')
    assert run(tmp_path, po, pot) == []
    po_changed = po.replace("Define ``{a}={b}:{C}``", "Define ``{a}={b}:{D}``")
    problems = run(tmp_path, po_changed, pot)
    assert len(problems) == 1 and "``{a}={b}:{C}``" in problems[0]


def test_gh_download_text_may_be_translated(tmp_path):
    pot = (HEADER + 'msgid "Download :gh_download:`CMS release` now."\n'
           'msgstr ""\n')
    po = (HEADER + 'msgid "Download :gh_download:`CMS release` now."\n'
          'msgstr "Descarga :gh_download:`la versión de CMS` ahora."\n')
    assert run(tmp_path, po, pot) == []


def test_gh_blob_path_change_is_reported(tmp_path):
    pot = (HEADER + 'msgid "See :gh_blob:`docker/cms-dev.sh` first."\n'
           'msgstr ""\n')
    po = (HEADER + 'msgid "See :gh_blob:`docker/cms-dev.sh` first."\n'
          'msgstr "Ver :gh_blob:`docker/cms-desarrollo.sh` primero."\n')
    problems = run(tmp_path, po, pot)
    assert len(problems) == 1 and "`docker/cms-dev.sh`" in problems[0]


def test_translation_starting_like_a_list_is_reported(tmp_path):
    # Sphinx parses "1. Respalda" as a list, not as the heading it
    # translates, and keeps the English without a warning. This holds even
    # though the English heading itself starts with "1. ".
    pot = HEADER + 'msgid "1. Back up everything"\nmsgstr ""\n'
    po = (HEADER + 'msgid "1. Back up everything"\n'
          'msgstr "1. Respalda todo"\n')
    problems = run(tmp_path, po, pot)
    assert len(problems) == 1 and "list/heading/quote" in problems[0]
    po_escaped = po.replace('msgstr "1. ', 'msgstr "1\\\\. ')
    assert run(tmp_path, po_escaped, pot) == []


def test_translation_line_starting_like_a_list_is_reported(tmp_path):
    pot = HEADER + 'msgid "Run it."\nmsgstr ""\n'
    for start in ("- ", "* ", "+ ", "> ", "# ", "2) "):
        po = (HEADER + 'msgid "Run it."\n'
              f'msgstr "Ejecútalo\\n{start}ahora."\n')
        problems = run(tmp_path, po, pot)
        assert len(problems) == 1 and "list/heading/quote" in problems[0]


def test_myst_doc_role_text_may_be_translated(tmp_path):
    pot = HEADER + 'msgid "See {doc}`text <multi-contest>` here."\nmsgstr ""\n'
    po = (HEADER + 'msgid "See {doc}`text <multi-contest>` here."\n'
          'msgstr "Ver {doc}`texto <multi-contest>` aquí."\n')
    assert run(tmp_path, po, pot) == []


def test_main_exit_code_0_on_complete_translation(tmp_path, monkeypatch):
    from check_translations import main

    pot_dir, po_dir = tmp_path / "pot", tmp_path / "po"
    write(pot_dir, "page.pot", GOOD)
    write(po_dir, "page.po", GOOD)
    monkeypatch.setattr(sys, "argv",
                        ["check_translations.py", str(pot_dir), str(po_dir)])
    assert main() == 0


def test_main_exit_code_1_on_problem(tmp_path, monkeypatch):
    from check_translations import main

    pot_dir, po_dir = tmp_path / "pot", tmp_path / "po"
    write(pot_dir, "page.pot", TEMPLATE)
    po = GOOD.replace('msgstr "Cómo funciona"', 'msgstr ""')
    write(po_dir, "page.po", po)
    monkeypatch.setattr(sys, "argv",
                        ["check_translations.py", str(pot_dir), str(po_dir)])
    assert main() == 1


def test_main_exit_code_2_on_missing_po_dir(tmp_path, monkeypatch):
    from check_translations import main

    pot_dir = tmp_path / "pot"
    po_dir = tmp_path / "nonexistent"
    write(pot_dir, "page.pot", TEMPLATE)
    monkeypatch.setattr(sys, "argv",
                        ["check_translations.py", str(pot_dir), str(po_dir)])
    assert main() == 2


def test_main_finds_templates_in_subdirectories(tmp_path, monkeypatch):
    # check() reads the templates recursively, so main() must too.
    from check_translations import main

    pot_dir, po_dir = tmp_path / "pot", tmp_path / "po"
    write(pot_dir / "sub", "page.pot", TEMPLATE)
    write(po_dir / "sub", "page.po", GOOD)
    monkeypatch.setattr(sys, "argv",
                        ["check_translations.py", str(pot_dir), str(po_dir)])
    assert main() == 0


def test_main_exit_code_2_on_empty_pot_dir(tmp_path, monkeypatch):
    from check_translations import main

    pot_dir = tmp_path / "pot"
    po_dir = tmp_path / "po"
    pot_dir.mkdir()
    po_dir.mkdir()
    monkeypatch.setattr(sys, "argv",
                        ["check_translations.py", str(pot_dir), str(po_dir)])
    assert main() == 2
