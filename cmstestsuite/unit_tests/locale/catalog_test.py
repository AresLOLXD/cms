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

While a change translates the AWS pages in parallel, each lane keeps
its new messages in cms/locale/_fragments/aws-<lane>.po. For these
tests the AWS catalog is aws.po plus the fragments (and the template
is aws.pot plus their msgids); the integration merges them into
aws.po and aws.pot and deletes the directory.

"""

import fnmatch
import io
import re
import sys
from collections import Counter, defaultdict
from configparser import RawConfigParser
from pathlib import Path

import pytest
from babel.messages import Catalog
from babel.messages.extract import DEFAULT_KEYWORDS, extract_from_dir
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po, write_po


ROOT = Path(__file__).resolve().parents[3]
LOCALE_DIR = ROOT / "cms" / "locale"
# The domains whose catalogs are checked, and the mapping file that tells
# which files hold their messages.
MAPPING_FILES = {"cms": "babel_mapping.cfg", "aws": "babel_mapping_aws.cfg"}
DOMAINS = list(MAPPING_FILES)
FRAGMENTS_DIR = LOCALE_DIR / "_fragments"


def _first(msgid):
    """Return the singular form of a msgid (a tuple when it has a plural)."""
    return msgid[0] if isinstance(msgid, tuple) else msgid


def _read_catalog(path):
    with open(path, "rb") as f:
        return read_po(f)


def _fragment_paths():
    """Return the paths of the AWS fragments (none outside a change)."""
    return sorted(FRAGMENTS_DIR.glob("aws-*.po"))


def _read_with_fragments(path, domain):
    """Read a catalog; for AWS, with the messages of the fragments too."""
    catalog = _read_catalog(path)
    if domain == "aws":
        for fragment in _fragment_paths():
            for message in _read_catalog(fragment):
                if message.id:
                    catalog[message.id] = message
    return catalog


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
    open_messages = find_open_messages(_read_with_fragments(
        LOCALE_DIR / "es" / "LC_MESSAGES" / f"{domain}.po", domain))

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
        missing = find_missing_messages(
            code_msgids, _read_with_fragments(path, domain))
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


# The mechanical rules of the Spanish style guide (the glossary is left
# to translation/check.py and a human: it has false positives).
_PLACEHOLDER = re.compile(r"%\(\w+\)[sdrf]|%[sdrf]|\{\w*\}")
_TAG = re.compile(
    r"</?(?:code|b|i|strong|em|br|a|small|p|span|ul|li|pre|kbd|tt)\b[^>]*>",
    re.IGNORECASE)
_ENTITY = re.compile(r"&\w+;")
# Imperatives in the "usted" form (the register is "tú").
_USTED = re.compile(
    r"\b(?:Usted|usted|Ustedes|ustedes|Ingrese|ingrese|Seleccione|seleccione|"
    r"Presione|presione|Pulse|pulse|Escriba|escriba|Elija|elija|Haga|haga|"
    r"Confirme|confirme|Espere|espere|Vuelva|vuelva|Marque|marque|"
    r"Descargue|descargue|Cargue|cargue|Corrija|corrija|Revise|revise|"
    r"Deje|deje|Quite|quite|Inténtelo|inténtelo|Avise|avise|Envíe|envíe)\b")


def find_style_problems(catalog):
    """Return the messages that break a mechanical rule of the style.

    A translation must not use "usted" or "...", must open with ¿ or ¡
    what ends with ? or !, and must keep the placeholders, HTML tags
    and entities of its msgid.

    """
    problems = []
    for message in catalog:
        if not message.id:
            continue
        sources = message.id if isinstance(message.id, tuple) \
            else (message.id, message.id)
        for source, target in zip(sources, _strings(message)):
            if not target:
                continue
            found = []
            if _USTED.search(target):
                found.append("usted")
            if "..." in target and "..." not in source:
                found.append("three dots instead of …")
            if target.rstrip().endswith("?") and "¿" not in target:
                found.append("? without ¿")
            if target.rstrip().endswith("!") and "¡" not in target:
                found.append("! without ¡")
            for name, pattern in (("placeholders", _PLACEHOLDER),
                                  ("tags", _TAG), ("entities", _ENTITY)):
                if Counter(pattern.findall(source)) \
                        != Counter(pattern.findall(target)):
                    found.append(f"{name} differ")
            problems += [f"{source!r}: {what}" for what in found]
    return problems


def find_repeated_msgids(fragment, merged):
    """Return the msgids of a fragment that the merged catalog has."""
    known = {_first(message.id) for message in merged if message.id}
    return sorted(_first(message.id) for message in fragment
                  if message.id and _first(message.id) in known)


def find_conflicting_msgids(fragments):
    """Return the msgids that two fragments translate differently.

    fragments: the catalogs, by name.

    """
    seen = defaultdict(set)
    for catalog in fragments.values():
        for message in catalog:
            if message.id:
                seen[_first(message.id)].add(_strings(message))
    return sorted(msgid for msgid, forms in seen.items() if len(forms) > 1)


def find_check_errors(catalog):
    """Return what Babel's check says about the placeholders."""
    return [f"{_first(message.id)!r}: {error}"
            for message, errors in catalog.check() for error in errors]


def _es_aws_catalog():
    return _read_with_fragments(
        LOCALE_DIR / "es" / "LC_MESSAGES" / "aws.po", "aws")


def test_style_check_finds_what_it_promises():
    # Scenario "Spanish register" (aws-language): the check itself fails
    # and lists each kind of message.
    catalog = Catalog(locale="es")
    catalog.add("Type it", "Escriba esto")
    catalog.add("Wait", "Espere...")
    catalog.add("Continue?", "Continuar?")
    catalog.add("Done!", "Listo!")
    catalog.add("Count %d", "Cuenta")
    catalog.add("Use <code>x</code>", "Usa x")
    catalog.add("A &amp; B", "A y B")
    catalog.add(("%d file", "%d files"), ("%d archivo", "archivos"))
    catalog.add("Fine: %s", "Bien: %s")

    assert sorted(find_style_problems(catalog)) == sorted([
        "'Type it': usted",
        "'Wait': usted",
        "'Wait': three dots instead of …",
        "'Continue?': ? without ¿",
        "'Done!': ! without ¡",
        "'Count %d': placeholders differ",
        "'Use <code>x</code>': tags differ",
        "'A &amp; B': entities differ",
        "'%d files': placeholders differ",
    ])


def test_fragment_checks_find_what_they_promise():
    merged = Catalog(locale="es")
    merged.add("Done", "Hecho")
    first = Catalog(locale="es")
    first.add("Done", "Hecho")
    first.add("Send", "Enviar")
    second = Catalog(locale="es")
    second.add("Send", "Mandar")

    assert find_repeated_msgids(first, merged) == ["Done"]
    assert find_conflicting_msgids({"a": first, "b": second}) == ["Send"]
    assert find_conflicting_msgids({"a": first, "b": first}) == []


def test_babel_check_finds_a_lost_placeholder():
    catalog = Catalog(locale="es")
    catalog.add("Count %d", "Cuenta")

    assert find_check_errors(catalog)


def test_fragments_do_not_repeat_merged_msgids():
    # A fragment holds only what aws.po does not have yet.
    merged = _read_catalog(LOCALE_DIR / "es" / "LC_MESSAGES" / "aws.po")
    repeated = {fragment.name: find_repeated_msgids(
        _read_catalog(fragment), merged) for fragment in _fragment_paths()}

    assert not any(repeated.values()), (
        f"fragments repeat messages of aws.po: {repeated}")


def test_fragments_agree_with_each_other():
    fragments = {fragment.name: _read_catalog(fragment)
                 for fragment in _fragment_paths()}

    conflicts = find_conflicting_msgids(fragments)

    assert not conflicts, (
        "fragments translate these messages differently:\n  "
        + "\n  ".join(conflicts))


@pytest.mark.parametrize("domain", DOMAINS)
def test_es_catalogs_pass_babel_checks(domain):
    errors = find_check_errors(_read_with_fragments(
        LOCALE_DIR / "es" / "LC_MESSAGES" / f"{domain}.po", domain))

    assert not errors, "\n".join(errors)


def test_aws_es_style():
    # Scenario "Spanish register" (aws-language) for the real catalog.
    problems = find_style_problems(_es_aws_catalog())

    assert not problems, "\n".join(problems)


def test_fragments_are_not_shipped():
    # setup.py ships what matches the package_data pattern of cms.locale;
    # a fragment is a file of an underscore directory, with no LC_MESSAGES.
    pattern = re.search(r'"(\*/LC_MESSAGES/[^"]*)"',
                        (ROOT / "setup.py").read_text(encoding="utf-8"))

    assert pattern, "the package_data pattern of the catalogs changed"
    assert not fnmatch.fnmatch("_fragments/aws-L3.po", pattern.group(1))


def test_the_aws_catalog_includes_the_fragments(tmp_path, monkeypatch):
    fragment = Catalog(locale="es")
    fragment.add("From a fragment", "De un fragmento")
    with open(tmp_path / "aws-L9.po", "wb") as f:
        write_po(f, fragment)
    monkeypatch.setattr(sys.modules[__name__], "FRAGMENTS_DIR", tmp_path)

    merged = _es_aws_catalog()

    assert merged["From a fragment"].string == "De un fragmento"
    assert merged["Invalid field(s)"].string
