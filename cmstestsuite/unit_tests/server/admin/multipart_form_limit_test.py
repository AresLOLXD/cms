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

"""Tests that AWS accepts multipart forms with many fields.

The task page is a multipart/form-data form with one field per public
testcase, on top of the task's and every dataset's own fields. Tornado
(since 6.5.5) rejects a multipart body with more than 100 parts with a
400 before any handler runs, so saving a task with a few dozen public
testcases failed. These tests send such a form through a real
AdminWebServer and check that its body gets parsed.

"""

import unittest

from tornado.httpclient import AsyncHTTPClient, HTTPResponse
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_sockets

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.server.admin.server import AdminWebServer


BOUNDARY = "cmsmultipartboundary"


def multipart_body(fields: list[tuple[str, str]]) -> str:
    """Encode fields as a multipart/form-data body."""
    parts = [
        "--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
        % (BOUNDARY, name, value)
        for name, value in fields
    ]
    return "".join(parts) + "--%s--\r\n" % BOUNDARY


class MultipartFormLimitTest(DatabaseMixin, unittest.IsolatedAsyncioTestCase):
    """Serve the real AdminWebServer application."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aws = AdminWebServer(0)

    async def asyncSetUp(self):
        sockets = bind_sockets(0, "127.0.0.1")
        self.port = sockets[0].getsockname()[1]
        self.server = HTTPServer(self.aws.application)
        self.server.add_sockets(sockets)
        self.client = AsyncHTTPClient()

    async def asyncTearDown(self):
        self.client.close()
        self.server.stop()
        await self.server.close_all_connections()

    async def post_multipart(
        self, path: str, fields: list[tuple[str, str]]
    ) -> HTTPResponse:
        return await self.client.fetch(
            "http://127.0.0.1:%d%s" % (self.port, path),
            method="POST", body=multipart_body(fields),
            headers={"Content-Type":
                     "multipart/form-data; boundary=%s" % BOUNDARY},
            raise_error=False)

    async def test_form_with_many_fields_is_parsed(self):
        # Like the form of a task with a few hundred public testcases.
        fields = [("testcase_%d_public" % i, "on") for i in range(500)]
        response = await self.post_multipart("/login", fields)
        # The body was parsed, so the request reached the XSRF check
        # (this form has no token), which comes after parsing.
        self.assertEqual(response.code, 403)


if __name__ == "__main__":
    unittest.main()
