#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""Request bodies for the tests of Tornado's limits on form bodies.

These tests post forms with a given number of fields, or a given size of
part header, to a real web server and look at whether the body was
parsed or rejected.

"""

BOUNDARY = "cmsbodylimitsboundary"
MULTIPART_CONTENT_TYPE = "multipart/form-data; boundary=%s" % BOUNDARY
URLENCODED_CONTENT_TYPE = "application/x-www-form-urlencoded"

_DISPOSITION = 'Content-Disposition: form-data; name="field%d"'
_PADDING_HEADER = "\r\nX-Padding: "


def urlencoded_body(arguments: int) -> str:
    """Encode a form with that many arguments, all with other names.

    arguments: how many arguments the form has.

    return: the body.

    """
    return "&".join("field%d=1" % i for i in range(arguments))


def multipart_body(parts: int, header_size: int | None = None) -> str:
    """Encode a multipart/form-data form with that many parts.

    parts: how many parts the form has.
    header_size: the size in bytes of the header of the first part
        (the other parts have just the Content-Disposition header), or
        None for the shortest header.

    return: the body.

    """
    headers = [_DISPOSITION % i for i in range(parts)]
    if header_size is not None and parts > 0:
        padding = header_size - len(headers[0]) - len(_PADDING_HEADER)
        assert padding >= 0, "header_size is shorter than the header"
        headers[0] += _PADDING_HEADER + "a" * padding
        assert len(headers[0]) == header_size
    body = "".join("--%s\r\n%s\r\n\r\nv\r\n" % (BOUNDARY, header)
                   for header in headers)
    return body + "--%s--\r\n" % BOUNDARY
