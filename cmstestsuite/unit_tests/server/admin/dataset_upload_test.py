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

"""Tests for the form that adds a manager to a dataset.

A browser that submits the form without a chosen file sends no file
for it. The handler used to fail with a KeyError, and the admin got an
internal server error instead of a message. These tests send the form
to a real AdminWebServer, as a browser would.

"""

import unittest
from unittest import mock

from sqlalchemy import select
from tornado.httpclient import HTTPResponse

from cmstestsuite.unit_tests.server.admin.pool_exhaustion_test import \
    XSRF_COOKIE, _AwsServerMixin

from cms.db import Manager


BOUNDARY = "cmsmanagerboundary"

DIGEST = "0123456789abcdef0123456789abcdef01234567"


def multipart_body(file_part: str) -> bytes:
    """Encode a form with a text field and the part of the file field.

    file_part: the part of the file field, without the boundary, or an
        empty string for a form that has no such field.

    return: the body of the request.

    """
    parts = ['--%s\r\nContent-Disposition: form-data; name="note"'
             '\r\n\r\nx\r\n' % BOUNDARY]
    if file_part:
        parts.append("--%s\r\n%s\r\n" % (BOUNDARY, file_part))
    parts.append("--%s--\r\n" % BOUNDARY)
    return "".join(parts).encode()


# What a browser sends when no file is chosen in the file input: a part
# with an empty file name and no content.
NO_FILE_CHOSEN = ('Content-Disposition: form-data; name="manager"; '
                  'filename=""\r\nContent-Type: application/octet-stream'
                  '\r\n\r\n')
FILE_CHOSEN = ('Content-Disposition: form-data; name="manager"; '
               'filename="grader.cpp"\r\nContent-Type: text/plain'
               '\r\n\r\nint main() {}')


class AddManagerTest(_AwsServerMixin, unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        task = self.add_task()
        dataset = self.add_dataset(task=task)
        self.session.commit()
        self.task_id = task.id
        self.dataset_id = dataset.id
        self.session.rollback()
        # The server is shared by the tests: forget old notifications.
        self.aws.take_notifications()

    async def post_manager_form(self, file_part: str) -> HTTPResponse:
        return await self.client.fetch(
            "http://127.0.0.1:%d/dataset/%d/managers/add"
            % (self.port, self.dataset_id),
            method="POST", body=multipart_body(file_part),
            headers={
                "Content-Type":
                    "multipart/form-data; boundary=%s" % BOUNDARY,
                "Cookie": "%s; _xsrf=%s" % (self.cookie_header, XSRF_COOKIE),
                "X-XSRFToken": XSRF_COOKIE,
            },
            follow_redirects=False, raise_error=False)

    def managers(self) -> list[Manager]:
        self.session.rollback()
        return list(self.session.execute(select(Manager)).scalars())

    async def assert_asks_for_a_file(self, file_part: str):
        with mock.patch.object(
                self.aws.file_cacher, "put_file_content") as put_file:
            response = await self.post_manager_form(file_part)

        self.assertEqual(response.code, 302)
        self.assertTrue(response.headers["Location"].endswith(
            "/dataset/%d/managers/add" % self.dataset_id))
        notifications = self.aws.take_notifications()
        self.assertEqual(len(notifications), 1)
        _, subject, text = notifications[0]
        self.assertEqual(subject, "Invalid data")
        self.assertIn("choose a manager file", text)
        put_file.assert_not_called()
        self.assertEqual(self.managers(), [])

    async def test_form_with_no_file_chosen_asks_for_a_file(self):
        await self.assert_asks_for_a_file(NO_FILE_CHOSEN)

    async def test_form_without_the_file_field_asks_for_a_file(self):
        await self.assert_asks_for_a_file("")

    async def test_chosen_file_is_still_stored(self):
        with mock.patch.object(
                self.aws.file_cacher, "put_file_content",
                return_value=DIGEST) as put_file:
            response = await self.post_manager_form(FILE_CHOSEN)

        self.assertEqual(response.code, 302)
        self.assertTrue(response.headers["Location"].endswith(
            "/task/%d" % self.task_id))
        put_file.assert_called_once()
        self.assertEqual(put_file.call_args.args[0], b"int main() {}")
        manager, = self.managers()
        self.assertEqual(manager.filename, "grader.cpp")
        self.assertEqual(manager.digest, DIGEST)
        self.assertEqual(self.aws.take_notifications()[0][1],
                         "Operation successful.")


if __name__ == "__main__":
    unittest.main()
