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

"""Tests that keep the Spanish catalogs of CWS and AWS closed.

A message added to the code must reach the template (cms.pot, aws.pot)
and the es catalog, and the es catalog must have no untranslated or
fuzzy message. If one of these fails, run "./setup.py extract_messages"
and "./setup.py update_catalog -l es" (for AWS add "-F
babel_mapping_aws.cfg -o cms/locale/aws.pot" to the first and "-D aws -i
cms/locale/aws.pot" to the second, see docs/Localization.rst), then
translate what is missing.

"""

import io
from configparser import RawConfigParser
from pathlib import Path

import pytest
from babel.messages import Catalog
from babel.messages.extract import DEFAULT_KEYWORDS, extract_from_dir
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po


ROOT = Path(__file__).resolve().parents[3]
LOCALE_DIR = ROOT / "cms" / "locale"
# The domains whose catalogs are checked, and the mapping file that tells
# which files hold their messages.
MAPPING_FILES = {"cms": "babel_mapping.cfg", "aws": "babel_mapping_aws.cfg"}
DOMAINS = list(MAPPING_FILES)


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


def find_missing_messages(code_msgids, catalog):
    """Return the msgids found in the code that the catalog does not have."""
    known = {_first(message.id) for message in catalog}
    return sorted(code_msgids - known)


def extract_code_msgids(domain="cms"):
    """Return the msgids that "setup.py extract_messages" finds in the code.

    It uses the same mapping and keywords as setup.cfg.

    domain: the catalog whose messages are wanted, "cms" or "aws".

    """
    parser = RawConfigParser()
    parser.read(ROOT / MAPPING_FILES[domain], encoding="utf-8")
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
    # Scenario "Incomplete catalog" (aws-language): the check itself fails
    # and lists the messages.
    catalog = Catalog(locale="es")
    catalog.add("Done", "Hecho")
    catalog.add("Fuzzy one", "Dudoso", flags=["fuzzy"])
    catalog.add("Missing one", "")
    catalog.add(("One file", "%d files"), ("", ""))

    assert find_open_messages(catalog) == [
        "Fuzzy one", "Missing one", "One file"]


def test_checker_finds_messages_missing_from_the_catalog():
    # Scenario "A message missing from the catalog" (aws-language): the check
    # itself fails and lists the message.
    catalog = Catalog(locale="es")
    catalog.add("Done", "Hecho")
    catalog.add(("One file", "%d files"), ("Un archivo", "%d archivos"))

    assert find_missing_messages(
        {"Done", "One file", "New one"}, catalog) == ["New one"]


@pytest.mark.parametrize("domain", DOMAINS)
def test_es_catalog_has_no_fuzzy_or_untranslated_messages(domain):
    # Scenario "Incomplete catalog" (aws-language) for the real catalogs.
    open_messages = find_open_messages(
        _read_catalog(LOCALE_DIR / "es" / "LC_MESSAGES" / f"{domain}.po"))

    assert not open_messages, (
        f"es {domain} has fuzzy or untranslated messages:\n  "
        + "\n  ".join(open_messages))


@pytest.mark.parametrize("domain", DOMAINS)
def test_code_messages_are_in_pot_and_es(domain):
    # Scenario "A message missing from the catalog" (aws-language) for the
    # real code and catalogs.
    code_msgids = extract_code_msgids(domain)
    assert code_msgids, "no message was extracted from the code"

    for name, path in (
            (f"{domain}.pot", LOCALE_DIR / f"{domain}.pot"),
            (f"es {domain}",
             LOCALE_DIR / "es" / "LC_MESSAGES" / f"{domain}.po")):
        missing = find_missing_messages(code_msgids, _read_catalog(path))
        assert not missing, (
            f"{name} lacks messages found in the code:\n  "
            + "\n  ".join(missing))


def test_both_catalogs_compile():
    # Scenario "Both catalogs compile" (aws-language): the build compiles the
    # domains listed in setup.cfg; each of them must compile for Spanish.
    # shortcut: this compiles with Babel's API rather than running
    # "setup.py compile_catalog" (CI has no setuptools).
    parser = RawConfigParser()
    parser.read(ROOT / "setup.cfg", encoding="utf-8")
    built = parser.get("compile_catalog", "domain").split()

    assert sorted(built) == sorted(DOMAINS)
    for domain in built:
        catalog = _read_catalog(
            LOCALE_DIR / "es" / "LC_MESSAGES" / f"{domain}.po")
        mo = io.BytesIO()
        write_mo(mo, catalog)
        assert len(mo.getvalue()) > 0, f"{domain} did not compile"
