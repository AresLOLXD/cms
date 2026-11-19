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

"""The Spanish AWS translation for the tests, built from the committed aws.po.

No compiled .mo file is needed, and the fragments of the lanes are
merged in. As in AWS, a message that the AWS catalog lacks is looked
up in the contestant-site one.

"""

import importlib.resources
from io import BytesIO

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

from cms.locale import Translation


def _spanish(domain: str) -> Translation:
    po = importlib.resources.files("cms.locale").joinpath(
        "es", "LC_MESSAGES", f"{domain}.po")
    with po.open("rb") as f:
        catalog = read_po(f)
    # While a change translates the pages in parallel, the new messages
    # of each lane are in a fragment (cms/locale/_fragments/aws-*.po).
    fragments = importlib.resources.files("cms.locale") / "_fragments"
    if domain == "aws" and fragments.is_dir():
        for fragment in sorted(fragments.iterdir(), key=lambda p: p.name):
            if fragment.name.startswith("aws-") \
                    and fragment.name.endswith(".po"):
                with fragment.open("rb") as f:
                    for message in read_po(f):
                        if message.id:
                            catalog[message.id] = message
    mo = BytesIO()
    write_mo(mo, catalog)
    mo.seek(0)
    return Translation("es", mo, domain)


# The contestant-site Spanish, which AWS falls back on (see server.py).
CMS_SPANISH = _spanish("cms")
SPANISH = _spanish("aws")
SPANISH.translation.add_fallback(CMS_SPANISH.translation)


# The Spanish of the pages as main had it, and the terms of the style
# guide that replaced it (the frozen "before" pages are main's).
MAIN_TERMS = [
    ("modo de análisis", "fase de análisis"),
    ("Un renglón por", "Una fila por"),
    ("pruebas de usuario", "user tests"),
    ("los participantes.", "los concursantes."),
    ("Un username por línea, o un CSV cuya primera columna sea el username.",
     "Un usuario por línea, o un CSV cuya primera columna sea el usuario."),
    ("La importación cifra las", "La importación calcula el hash de las"),
    ("(cifradas o en texto", "(con hash o en texto"),
]


def with_new_terms(text: str) -> str:
    """Return the text of a page of main with the terms of the style
    guide, to compare it with the page of today."""
    for old, new in MAIN_TERMS:
        text = text.replace(old, new)
    return text
