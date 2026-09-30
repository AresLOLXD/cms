#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <www.luisrodolfo@gmail.com>
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

"""Bulk removal of users (from the platform) or of participations (from
a contest), for AWS.

The messages are meant to be shown as they are in the page.

"""

import re

MAX_LIST_BYTES = 1024 * 1024
# Why a value of the selection is not removed.
NOT_FOUND = "no existe"
NOT_IN_CONTEST = "no participa en el concurso"
# A list may be a CSV exported with any of these separators; the
# username is the first column.
_SEPARATORS = re.compile(r"[,;\t]")


def parse_usernames(*sources: bytes) -> list[str]:
    """Return the usernames listed in sources, in order, without repeats.

    Each source is the text of the textarea or the body of the uploaded
    file: one username per line, or a CSV (comma, semicolon or tab
    separated) whose first column is the username. Blank lines are
    skipped, and so is a first line whose first column is "username", in
    any case, as the header of a CSV.

    sources: the lists, UTF-8 with or without BOM.

    return: the usernames.

    raise (ValueError): if the sources pass MAX_LIST_BYTES in all, or
        one of them is not UTF-8.

    """
    if sum(len(source) for source in sources) > MAX_LIST_BYTES:
        raise ValueError("la lista pasa de 1 MB")
    usernames: list[str] = []
    seen: set[str] = set()
    for source in sources:
        try:
            text = source.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("la lista no está en UTF-8") from None
        first = True
        for line in text.splitlines():
            value = _SEPARATORS.split(line, maxsplit=1)[0]
            value = value.strip().strip('"').strip()
            if not value:
                continue
            if first:
                first = False
                if value.lower() == "username":
                    continue
            if value not in seen:
                seen.add(value)
                usernames.append(value)
    return usernames
