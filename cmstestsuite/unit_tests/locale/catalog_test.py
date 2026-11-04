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

"""Tests that keep the Spanish catalog of CWS closed.

A message added to the code must reach cms.pot and the es catalog, and
the es catalog must have no untranslated or fuzzy message. If one of
these fails, run "./setup.py extract_messages" and "./setup.py
update_catalog -l es", then translate what is missing.

"""

from configparser import RawConfigParser
from pathlib import Path

from babel.messages import Catalog
from babel.messages.extract import DEFAULT_KEYWORDS, extract_from_dir
from babel.messages.pofile import read_po


ROOT = Path(__file__).resolve().parents[3]
LOCALE_DIR = ROOT / "cms" / "locale"


def _first(msgid):
    """Return the singular form of a msgid (a tuple when it has a plural)."""
    return msgid[0] if isinstance(msgid, tuple) else msgid


def _read_catalog(path):
    with open(path, "rb") as f:
        return read_po(f)


def _strings(message):
    """Return the translations of a message as a tuple."""
    string = message.string
    return string if isinstance(string, tuple) else (string,)


def find_open_messages(catalog):
    """Return the msgids of the messages that are fuzzy or untranslated.

    The header (empty msgid) is left out.

    """
    return sorted(
        _first(message.id) for message in catalog
        if message.id and (message.fuzzy or not any(_strings(message))))


def extract_code_msgids():
    """Return the msgids that "setup.py extract_messages" finds in the code.

    It uses the same mapping and keywords as setup.cfg.

    """
    parser = RawConfigParser()
    parser.read(ROOT / "babel_mapping.cfg", encoding="utf-8")
    method_map = []
    options_map = {}
    for section in parser.sections():
        method, pattern = (part.strip() for part in section.split(":", 1))
        # shortcut: the name "jinja2" is resolved by babel through
        # pkg_resources, which not every environment has; we name the
        # extractor directly.
        if method == "jinja2":
            method = "jinja2.ext:babel_extract"
        method_map.append((pattern, method))
        options_map[pattern] = dict(parser.items(section))
    keywords = dict(DEFAULT_KEYWORDS)
    keywords.update({"n_": (1, 2), "Nn_": (1, 2)})
    return {
        _first(message)
        for _, _, message, _, _ in extract_from_dir(
            ROOT / "cms", method_map, options_map, keywords=keywords)
    }


def test_checker_finds_fuzzy_and_untranslated_messages():
    catalog = Catalog(locale="es")
    catalog.add("Done", "Hecho")
    catalog.add("Fuzzy one", "Dudoso", flags=["fuzzy"])
    catalog.add("Missing one", "")
    catalog.add(("One file", "%d files"), ("", ""))

    assert find_open_messages(catalog) == [
        "Fuzzy one", "Missing one", "One file"]


def test_es_catalog_has_no_fuzzy_or_untranslated_messages():
    open_messages = find_open_messages(
        _read_catalog(LOCALE_DIR / "es" / "LC_MESSAGES" / "cms.po"))

    assert not open_messages, (
        "es has fuzzy or untranslated messages:\n  "
        + "\n  ".join(open_messages))


def test_code_messages_are_in_pot_and_es():
    code_msgids = extract_code_msgids()
    assert code_msgids, "no message was extracted from the code"

    for name, path in (
            ("cms.pot", LOCALE_DIR / "cms.pot"),
            ("es", LOCALE_DIR / "es" / "LC_MESSAGES" / "cms.po")):
        known = {_first(message.id) for message in _read_catalog(path)}
        missing = sorted(code_msgids - known)
        assert not missing, (
            f"{name} lacks messages found in the code:\n  "
            + "\n  ".join(missing))
