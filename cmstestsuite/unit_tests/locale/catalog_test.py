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

import ast
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


def test_importer_messages_are_in_the_aws_catalog():
    # cmscommon/importers.py is outside the aws extraction; the N_ list
    # of handlers/dataset.py carries its messages. Renaming a message in
    # the importer without updating that list must fail here.
    tree = ast.parse((ROOT / "cmscommon" / "importers.py").read_text("utf-8"))
    msgids = {
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "gettext" and node.args
        and isinstance(node.args[0], ast.Constant)}
    assert msgids, "no gettext message found in the importer"

    missing = find_missing_messages(
        msgids, _read_catalog(LOCALE_DIR / "aws.pot"))
    assert not missing, (
        "the importer has messages that are not in aws.pot; declare them "
        "with N_ in cms/server/admin/handlers/dataset.py:\n  "
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
_PLACEHOLDER = re.compile(
    r"%%|%(?:\(\w+\))?[-+#0]*\d*(?:\.\d+)?[sdifrxXeEgGc]|\{\w*\}")
_TAG = re.compile(r"</?\w+[^>]*>")
_ENTITY = re.compile(r"&(?:\w+|#\d+|#[xX][0-9a-fA-F]+);")
# A ? or ! that ends a sentence: before a space, a closing mark, a tag
# or the end.
_QUESTION_END = re.compile(r"\?(?=\s|$|<|[)\]»\"'])")
_EXCLAMATION_END = re.compile(r"!(?=\s|$|<|[)\]»\"'])")
# Imperatives in the "usted" form (the register is "tú"). The list is the
# forms that cannot be anything else; the shape catches the -ar verbs
# whose usted form ends in -que, -gue, -güe or -ice, and a word that has
# that shape and is something else is allowed by name.
_USTED_WORDS = (
    "Usted usted Ustedes ustedes Ingrese ingrese Seleccione seleccione "
    "Presione presione Pulse pulse Escriba escriba Elija elija Haga haga "
    "Confirme confirme Espere espere Vuelva vuelva Marque marque "
    "Descargue descargue Cargue cargue Corrija corrija Revise revise "
    "Deje deje Quite quite Inténtelo inténtelo Avise avise Envíe envíe "
    # Only at the start of a sentence: in the middle the same form is a
    # subjunctive ("hasta que guarde"), which is right.
    "Introduzca Guarde Utilice Asegúrese Agregue Borre Cierre Verifique "
    "Compruebe Proporcione Complete Vea Añada Elimine Cambie Escoja "
    "Pruebe Intente Permita Rellene Suba Abra Tenga Recuerde Hágalo "
    "Vuélvalo Active Desactive Siga Use").split()
_USTED = re.compile(r"\b(?:%s)\b" % "|".join(_USTED_WORDS))
_USTED_SHAPE = re.compile(r"\b[A-ZÁÉÍÓÚ][a-záéíóúñü]*(?:que|gue|güe|ice)\b")
# Words of that shape that are not an imperative of usted: nouns, and
# the "tú" imperatives of -guir verbs.
NOT_USTED = {"Bloque", "Parque", "Cheque", "Clique", "Sigue", "Consigue",
             "Persigue", "Prosigue", "Distingue", "Averigua", "Dice"}


# A name between < > that is not html but a pattern of a URL, which the
# translation may translate: (English, Spanish).
TRANSLATED_ANGLES = {"<name>": "<nombre>", "<unknown>": "<desconocido>"}


def usted_words(text):
    """Return the words of the text that are a usted imperative."""
    return _USTED.findall(text) + [
        word for word in _USTED_SHAPE.findall(text)
        if word not in NOT_USTED]


def inverted_problems(text):
    """Return what is wrong with the ¿ ¡ of the text: each ? or ! that
    ends a sentence needs its own opening mark (the tags that close
    the text do not count)."""
    text = _TAG.sub("", text)
    found = []
    for name, end, opening in (("?", _QUESTION_END, "¿"),
                               ("!", _EXCLAMATION_END, "¡")):
        closing = len(end.findall(text))
        if closing > text.count(opening):
            found.append(f"{name} without {opening}")
        elif closing < text.count(opening):
            found.append(f"{opening} without {name}")
    return found


def find_style_problems(catalog):
    """Return the messages that break a mechanical rule of the style.

    A translation must not use "usted" or "...", must open with ¿ or ¡
    each sentence that ends with ? or !, and must keep the placeholders,
    HTML tags and entities of its msgid.

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
            if usted_words(target):
                found.append("usted")
            if "..." in target:
                found.append("three dots instead of …")
            found += inverted_problems(target)
            target_tags = target
            for english, spanish in TRANSLATED_ANGLES.items():
                target_tags = target_tags.replace(spanish, english)
            for name, pattern in (("placeholders", _PLACEHOLDER),
                                  ("tags", _TAG), ("entities", _ENTITY)):
                compared = target_tags if name == "tags" else target
                if Counter(pattern.findall(source)) \
                        != Counter(pattern.findall(compared)):
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


def _header(text, name):
    """Return the value of a header of the text of a .po file, or None."""
    found = re.search(rf'^"{name}: (.*?)\\n"$', text, re.M)
    return found.group(1) if found else None


def find_fragment_problems(text, reference):
    """Return what is wrong with the text of a fragment of a lane: its
    header differs from the one of aws.po, it repeats a msgid, it has
    an obsolete entry, or a plural lacks one of its two forms.

    text: the content of the .po file.
    reference: the content of aws.po.

    """
    catalog = read_po(io.BytesIO(text.encode("utf-8")))
    problems = []
    if _header(text, "Language") != "es":
        problems.append(
            f"Language is {_header(text, 'Language')!r}, not 'es'")
    if _header(text, "Plural-Forms") != _header(reference, "Plural-Forms"):
        problems.append(f"Plural-Forms is {_header(text, 'Plural-Forms')!r}, "
                        f"aws.po has {_header(reference, 'Plural-Forms')!r}")
    # A msgid may go over several lines.
    msgids = re.findall(r'^msgid ("(?:.*)"(?:\n".*")*)', text, re.M)
    problems += [f"msgid {msgid} is twice" for msgid, times
                 in Counter(msgids).items() if times > 1 and msgid != '""']
    if re.search(r"^#~", text, re.M):
        problems.append("has obsolete entries (#~)")
    for message in catalog:
        if message.id and isinstance(message.id, tuple) \
                and (len(message.string) != 2 or not all(message.string)):
            problems.append(f"plural {message.id[0]!r} needs msgstr[0] and "
                            "msgstr[1]")
    return problems


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


@pytest.mark.parametrize("source, target, expected", [
    # usted, by name and by shape
    ("Save it", "Guarde los cambios", ["usted"]),
    ("Use it", "Utilice la clave", ["usted"]),
    ("Check it", "Verifique el archivo", ["usted"]),
    ("Add it", "Agregue un usuario", ["usted"]),
    ("Make sure", "Asegúrese de guardar", ["usted"]),
    ("Enter it", "Introduzca el valor", ["usted"]),
    ("Close it", "Cierre la sesión", ["usted"]),
    ("Until it saves", "Hasta que guarde", []),
    # the "tú" imperatives and the nouns that look the same
    ("Define it", "Define una clave", []),
    ("Fix it", "Corrige esto", []),
    ("Type it", "Escribe el nombre", []),
    ("A block", "Bloque de datos", []),
    ("Name", "Nombre", []),
    # three dots are always a finding, English or not
    ("Wait...", "Espera...", ["three dots instead of …"]),
    ("Wait…", "Espera…", []),
    # every sentence that ends with ? or ! needs its mark
    ("<b>Sure?</b>", "<b>Seguro?</b>", ["? without ¿"]),
    ("<b>Sure?</b>", "<b>¿Seguro?</b>", []),
    ("Stop. Sure?", "Para. Seguro?", ["? without ¿"]),
    ("Sure? Yes!", "¿Seguro? Sí!", ["! without ¡"]),
    ("Sure? Yes!", "¿Seguro? ¡Sí!", []),
    ("Sure", "¿Seguro", ["¿ without ?"]),
    # a ? inside a url is not the end of a sentence
    ("See /a?b=1 now.", "Mira /a?b=1 ahora.", []),
    # placeholders, entities and tags: any of them
    ("%.2f s", "%.1f s", ["placeholders differ"]),
    ("%.2f s", "%.2f s", []),
    ("%5d of %i", "%5d de", ["placeholders differ"]),
    ("It&#39;s %(a)s", "Es %(a)s", ["entities differ"]),
    ("It&#39;s", "Es &#39;", []),
    ("A <u>b</u>", "A b", ["tags differ"]),
    ("A <u>b</u>", "A <u>b</u>", []),
    ("A <u>b</u>", "A <u>b</u> <i>", ["tags differ"]),
    ("Used in (/<name>/)", "Se usa en (/<nombre>/)", []),
    # the name shown for a deleted admin is translated too
    ("<unknown>", "<desconocido>", []),
    ("By: <unknown>", "Por: <desconocido>", []),
    ("<unknown>", "<otro>", ["tags differ"]),
])
def test_style_rules(source, target, expected):
    catalog = Catalog(locale="es")
    catalog.add(source, target)

    assert [problem.split(": ", 1)[1]
            for problem in find_style_problems(catalog)] == expected


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


_FRAGMENT_HEADER = (
    'msgid ""\nmsgstr ""\n"Language: es\\n"\n"MIME-Version: 1.0\\n"\n'
    '"Content-Type: text/plain; charset=UTF-8\\n"\n'
    '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n')


def test_fragment_problems_are_found():
    reference = _FRAGMENT_HEADER

    def problems(body, header=_FRAGMENT_HEADER):
        return find_fragment_problems(header + body, reference)

    assert problems('msgid "A"\nmsgstr "B"\n') == []
    assert problems('msgid "A %d"\nmsgid_plural "A %d s"\n'
                    'msgstr[0] "B"\nmsgstr[1] "Bs"\n') == []
    assert problems('msgid "A"\nmsgstr "B"\n\nmsgid "A"\nmsgstr "C"\n') \
        == ['msgid "A" is twice']
    assert problems('#~ msgid "A"\n#~ msgstr "B"\n') \
        == ["has obsolete entries (#~)"]
    assert len(problems('msgid "A %d"\nmsgid_plural "A %d s"\n'
                        'msgstr[0] "B"\nmsgstr[1] ""\n')) == 1
    assert len(problems('msgid "A %d"\nmsgid_plural "A %d s"\n'
                        'msgstr[0] "B"\n')) == 1
    assert len(problems('msgid "A"\nmsgstr "B"\n',
                        _FRAGMENT_HEADER.replace('"Language: es\\n"\n', ""))) == 1
    assert len(problems('msgid "A"\nmsgstr "B"\n',
                        _FRAGMENT_HEADER.replace("n != 1", "n > 1"))) == 1
    assert len(problems('msgid "A"\nmsgstr "B"\n', "")) == 2


def test_fragments_are_well_formed():
    reference = (LOCALE_DIR / "es" / "LC_MESSAGES" / "aws.po").read_text(
        encoding="utf-8")
    problems = {fragment.name: find_fragment_problems(
        fragment.read_text(encoding="utf-8"), reference)
        for fragment in _fragment_paths()}

    assert not any(problems.values()), problems


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
