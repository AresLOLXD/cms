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

No compiled .mo file is needed.

"""

import importlib.resources
from io import BytesIO

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

from cms.locale import Translation


def _spanish() -> Translation:
    po = importlib.resources.files("cms.locale").joinpath(
        "es", "LC_MESSAGES", "aws.po")
    with po.open("rb") as f:
        catalog = read_po(f)
    mo = BytesIO()
    write_mo(mo, catalog)
    mo.seek(0)
    return Translation("es", mo, "aws")


SPANISH = _spanish()
