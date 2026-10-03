#!/usr/bin/env python3

"""Check that a translation of the manual is complete.

Usage: check_translations.py POT_DIR PO_DIR

POT_DIR holds the templates written by `sphinx-build -b gettext`; PO_DIR
holds one language's catalogs (docs/locale/<lang>/LC_MESSAGES). Print every
problem and exit 1 if a page has no catalog, a catalog has no page, a
message is missing, untranslated or fuzzy, a catalog header is fuzzy (Sphinx
then ignores the whole catalog without a warning), or a translation lost an
inline code span of its source.

"""

import re
import sys
from pathlib import Path

from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po

# Roles (:doc:`x`, {doc}`x`) and reST links (`text <url>`_): their text
# may be translated, so they are removed before looking for code. A link's
# text never starts with a space, which keeps "`-` or `_`" two code spans.
ROLE_OR_LINK = re.compile(
    r"(?::[\w:+.-]+:|\{[\w:+.-]+\})`[^`]*`|(?<!`)`[^`\s][^`]*`__?")
# Inline code in reStructuredText (``x``) and Markdown (`x`).
CODE_SPAN = re.compile(r"``[^`]+``|`[^`]+`")


def read_catalog(path: Path) -> Catalog:
    """Read a .po or .pot file."""
    with path.open("rb") as f:
        return read_po(f)


def short(text: str, width: int = 60) -> str:
    """Return the start of a message, for error lines."""
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width - 1] + "…"


def catalogs(directory: Path, suffix: str) -> dict[Path, Path]:
    """Map each catalog's path without suffix to its file."""
    return {path.relative_to(directory).with_suffix(""): path
            for path in directory.rglob("*" + suffix)}


def check(pot_dir: Path, po_dir: Path) -> list[str]:
    """Return the problems of the translation in po_dir.

    pot_dir: the templates of the current English sources.
    po_dir: the catalogs of one language.

    return: one line per problem; empty when the translation is complete.

    """
    problems = []
    templates = catalogs(pot_dir, ".pot")
    translations = catalogs(po_dir, ".po")
    for name in sorted(templates.keys() - translations.keys()):
        problems.append(f"{name}.po: no .po file (run sphinx-intl update)")
    for name in sorted(translations.keys() - templates.keys()):
        problems.append(f"{name}.po: no page produces it (delete it)")
    for name in sorted(templates.keys() & translations.keys()):
        catalog = read_catalog(translations[name])
        if catalog.fuzzy:
            problems.append(f"{name}.po: the header is marked fuzzy, so "
                            f"Sphinx ignores the whole catalog")
        for source in read_catalog(templates[name]):
            if not source.id:
                continue
            label = f"{name}.po: {short(source.id)!r}"
            message = catalog.get(source.id, source.context)
            if message is None:
                problems.append(f"{label}: missing (run sphinx-intl update)")
            elif not message.string:
                problems.append(f"{label}: not translated")
            elif message.fuzzy:
                problems.append(f"{label}: fuzzy")
            else:
                code = CODE_SPAN.findall(ROLE_OR_LINK.sub(" ", source.id))
                for span in code:
                    if span not in message.string:
                        problems.append(
                            f"{label}: the translation lacks {span}")
    return problems


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    problems = check(Path(sys.argv[1]), Path(sys.argv[2]))
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
