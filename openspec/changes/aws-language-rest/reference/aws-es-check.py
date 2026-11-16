#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check a Spanish .po catalog against the AWS glossary and style rules.

Prototype for the AWS Spanish translation guide. Standard library only.
The glossary is read from aws-es-glossary.json next to this file.

Usage:
    python3 -I aws-es-check.py PATH/TO/aws.po
    python3 -I aws-es-check.py --selftest

Exit status: 0 when there is no finding, 1 when there is at least one.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

GLOSSARY_PATH = Path(__file__).resolve().parent / "aws-es-glossary.json"

MSG_RE = re.compile(r'^(msgctxt|msgid_plural|msgid|msgstr)(?:\[(\d+)\])?\s+"(.*)"$')
CONT_RE = re.compile(r'^"(.*)"$')
PLACEHOLDER_RE = re.compile(r"%\(\w+\)[sdrf]|%[sdrf]|\{\w*\}")
TAG_RE = re.compile(
    r"</?(?:code|b|i|strong|em|br|a|small|p|span|ul|li|pre|kbd|tt)\b[^>]*>", re.IGNORECASE
)
ENTITY_RE = re.compile(r"&\w+;")
TERMINALS = ".:?!…"
ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
USTED_RE = re.compile(
    r"\b(?:Usted|usted|Ustedes|ustedes|Ingrese|ingrese|Seleccione|seleccione|"
    r"Presione|presione|Pulse|pulse|Escriba|escriba|Elija|elija|Haga|haga|"
    r"Confirme|confirme|Espere|espere|Vuelva|vuelva|Marque|marque|"
    r"Descargue|descargue|Cargue|cargue|Corrija|corrija|Revise|revise|"
    r"Deje|deje|Quite|quite|Inténtelo|inténtelo|Avise|avise|Envíe|envíe)\b"
)


def unescape(text):
    """Undo the C-style escapes of a .po string."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(ESCAPES.get(text[i + 1], text[i + 1]))
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def parse_po(text):
    """Return the catalog entries (header excluded) as dicts."""
    entries, current, field, flags = [], None, None, set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            if current is not None:
                entries.append(current)
            current, field, flags = None, None, set()
            continue
        if line.startswith("#,"):
            flags |= {f.strip() for f in line[2:].split(",") if f.strip()}
            continue
        if line.startswith("#"):
            continue
        match = MSG_RE.match(line)
        if match:
            keyword = match.group(1)
            index = int(match.group(2) or 0)
            value = unescape(match.group(3))
            if keyword in ("msgctxt", "msgid") and current is not None and current["msgid"] is not None:
                entries.append(current)
                current = None
            if current is None:
                current = {"line": number, "flags": flags, "msgctxt": None,
                           "msgid": None, "msgid_plural": None, "msgstr": {}}
                flags = set()
            if keyword == "msgstr":
                current["msgstr"][index] = value
            else:
                current[keyword] = value
            field = (keyword, index)
            continue
        cont = CONT_RE.match(line)
        if cont and current is not None and field is not None:
            value = unescape(cont.group(1))
            keyword, index = field
            if keyword == "msgstr":
                current["msgstr"][index] += value
            else:
                current[keyword] += value
    if current is not None:
        entries.append(current)
    return [e for e in entries if e["msgid"]]


def load_rules(path):
    """Read the glossary rules and the list of intentionally unchanged messages."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["rules"], set(data.get("unchanged_ok", []))


def visible(text, exempt=None):
    """Return the words of a message without placeholders and known markup."""
    text = TAG_RE.sub(" ", PLACEHOLDER_RE.sub(" ", text))
    if exempt:
        text = re.sub(exempt, " ", text, flags=re.IGNORECASE)
    return text


def terminal(text):
    """Return the final punctuation mark of a message ("…" for three dots)."""
    text = text.rstrip()
    if text.endswith("..."):
        return "…"
    return text[-1] if text and text[-1] in TERMINALS else ""


def starts_with_placeholder(text):
    return PLACEHOLDER_RE.match(text.lstrip("¿¡ ")) is not None


def first_letter(text):
    for char in visible(text).lstrip("¿¡ "):
        if char.isalpha():
            return char
    return ""


def positional(tokens):
    return [t for t in tokens if not t.startswith(("%(", "{"))]


def check_form(source, target):
    """Return (code, detail) problems of one translation against its source."""
    if not target:
        return [("EMPTY", "no translation")]
    problems = []
    source_tokens = PLACEHOLDER_RE.findall(source)
    target_tokens = PLACEHOLDER_RE.findall(target)
    if Counter(source_tokens) != Counter(target_tokens):
        problems.append(("PLACEHOLDER", f"source {source_tokens}, translation {target_tokens}"))
    elif positional(source_tokens) != positional(target_tokens):
        problems.append(("PLACEHOLDER-ORDER", "positional %s/%d/%r must keep their order"))
    if Counter(TAG_RE.findall(source)) != Counter(TAG_RE.findall(target)):
        problems.append(("MARKUP", "HTML tags differ"))
    if Counter(ENTITY_RE.findall(source)) != Counter(ENTITY_RE.findall(target)):
        problems.append(("MARKUP", "HTML entities differ"))
    if target != target.strip() or "  " in target:
        problems.append(("SPACE", "leading, trailing or double space"))
    if re.search(r"\s[,;:.!?]", target) and not re.search(r"\s[,;:.!?]", source):
        problems.append(("SPACE", "space before punctuation"))
    if terminal(source) != terminal(target):
        problems.append(("PUNCT-END", f"source ends {terminal(source)!r}, translation {terminal(target)!r}"))
    if target.rstrip().endswith("?") and "¿" not in target:
        problems.append(("INVERTED", "question without ¿"))
    if target.rstrip().endswith("!") and "¡" not in target:
        problems.append(("INVERTED", "exclamation without ¡"))
    if "..." in target and "..." not in source:
        problems.append(("ELLIPSIS", "use … (U+2026) instead of three dots"))
    if '"' in target and '"' not in source:
        problems.append(("QUOTES", "use «» instead of straight quotes"))
    usted = USTED_RE.search(target)
    if usted:
        problems.append(("REGISTER", f"{usted.group(0)!r} is usted; use tú"))
    if not starts_with_placeholder(source) and not starts_with_placeholder(target):
        first_source, first_target = first_letter(source), first_letter(target)
        if first_source and first_target and first_source.isupper() != first_target.isupper():
            problems.append(("CASE", f"source starts {first_source!r}, translation {first_target!r}"))
    return problems


def check_glossary(entry, rules):
    """Return (code, detail) problems of the glossary rules for one entry."""
    source = visible(entry["msgid"]) + " " + visible(entry["msgid_plural"] or "")
    target = " ".join(visible(value) for value in entry["msgstr"].values())
    problems = []
    for rule in rules:
        scope = visible(source, rule.get("exempt"))
        if not re.search(rule["en_re"], scope, re.IGNORECASE):
            continue
        bad = [p for p in rule.get("forbid", []) if re.search(p, target, re.IGNORECASE)]
        if bad:
            found = re.search(bad[0], target, re.IGNORECASE).group(0)
            problems.append(("GLOSSARY", f"'{rule['en']}' must be {rule['es']!r}, found {found!r}"))
        elif not any(re.search(p, target, re.IGNORECASE) for p in rule["accept"]):
            problems.append(("GLOSSARY", f"'{rule['en']}' should be {rule['es']!r}"))
    return problems


def check_entry(entry, rules, unchanged_ok):
    """Return (line, code, detail) findings for one catalog entry."""
    line, msgid, plural, forms = entry["line"], entry["msgid"], entry["msgid_plural"], entry["msgstr"]
    found = []
    if "fuzzy" in entry["flags"]:
        found.append((line, "FUZZY", "fuzzy flag still set; delete it after review"))
    wanted = {0, 1} if plural is not None else {0}
    if set(forms) != wanted:
        found.append((line, "PLURAL-FORMS", f"msgstr forms {sorted(forms)}, expected {sorted(wanted)}"))
    for index, target in sorted(forms.items()):
        source = plural if (plural is not None and index == 1) else msgid
        for code, detail in check_form(source, target):
            label = f"msgstr[{index}]: " if plural is not None else ""
            found.append((line, code, label + detail))
    if plural is None and forms.get(0) == msgid and msgid not in unchanged_ok and re.search(r"[A-Za-z]", msgid):
        found.append((line, "SAME-AS-SOURCE", "translation is the English text"))
    for code, detail in check_glossary(entry, rules):
        found.append((line, code, detail))
    return found


def check_catalog(path, rules, unchanged_ok):
    entries = parse_po(Path(path).read_text(encoding="utf-8"))
    findings = []
    for entry in entries:
        for line, code, detail in check_entry(entry, rules, unchanged_ok):
            findings.append((line, code, detail, entry["msgid"][:60]))
    return entries, sorted(findings)


SELFTEST_PO = '''msgid "One row per contestant: it creates users."
msgstr "Un renglón por concursante: crea usuarios."

msgid "Type %(count)s to confirm:"
msgstr "Escriba %(count)s para confirmar:"

msgid "Continue?"
msgstr "Continuar?"

msgid "Count %d"
msgstr "Cuenta %s"

msgid "Done."
msgstr "Listo."
'''


def selftest():
    rules, unchanged_ok = load_rules(GLOSSARY_PATH)
    entries = parse_po(SELFTEST_PO)
    assert len(entries) == 5, len(entries)
    found = {}
    for entry in entries:
        for _, code, _ in check_entry(entry, rules, unchanged_ok):
            found.setdefault(entry["msgid"], set()).add(code)
    assert "GLOSSARY" in found["One row per contestant: it creates users."], found
    assert "REGISTER" in found["Type %(count)s to confirm:"], found
    assert "INVERTED" in found["Continue?"], found
    assert "PLACEHOLDER" in found["Count %d"], found
    assert "Done." not in found, found
    print("selftest ok")
    return 0


def main(argv):
    if argv == ["--selftest"]:
        return selftest()
    if len(argv) != 1:
        print(__doc__)
        return 2
    rules, unchanged_ok = load_rules(GLOSSARY_PATH)
    entries, findings = check_catalog(argv[0], rules, unchanged_ok)
    for line, code, detail, excerpt in findings:
        print(f"{argv[0]}:{line}: [{code}] {detail} (msgid: {excerpt!r})")
    counts = Counter(code for _, code, _, _ in findings)
    summary = ", ".join(f"{code}={n}" for code, n in sorted(counts.items())) or "none"
    print(f"{len(entries)} entries checked; {len(findings)} findings ({summary})")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
