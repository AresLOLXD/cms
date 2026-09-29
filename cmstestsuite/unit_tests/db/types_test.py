#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""Tests for the custom column types in cms.db.types.

SQLAlchemy 2.0 builds no statement cache key, and warns about it, for
any statement that involves a TypeDecorator not declaring cache_ok. That
silently disables the statement cache for every query touching such a
column (contest, task, user and team names, digests, filenames...), so
we check that queries on a column of each of our types stay cacheable.

No database is needed: a cache key is built from the statement alone.

"""

import unittest
import warnings

from sqlalchemy import select
from sqlalchemy.exc import SAWarning

# The models are used as db.<Name> because importing Testcase by name
# would make pytest try to collect it as a test class.
from cms import db


class TestStatementCaching(unittest.TestCase):

    @staticmethod
    def make_statements():
        """Build a query filtering on a column of each custom type.

        return: the statements, indexed by a label naming the column and
            its type.

        """
        return {
            "Contest.name (Codename)":
                select(db.Contest).where(db.Contest.name == "x"),
            "Testcase.codename (Codename)":
                select(db.Testcase).where(db.Testcase.codename == "x"),
            "File.digest (Digest)":
                select(db.File).where(db.File.digest == "a" * 40),
            "File.filename (FilenameSchema)":
                select(db.File).where(db.File.filename == "sol.%l"),
            "Executable.filename (Filename)":
                select(db.Executable).where(db.Executable.filename == "sol"),
            "Task.submission_format (FilenameSchemaArray)":
                select(db.Task).where(
                    db.Task.submission_format == ["sol.%l"]),
        }

    def test_statements_have_a_cache_key(self):
        for label, statement in self.make_statements().items():
            with self.subTest(label):
                self.assertIsNotNone(statement._generate_cache_key())

    def test_no_sqlalchemy_warning_is_emitted(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for statement in self.make_statements().values():
                statement._generate_cache_key()

        self.assertEqual(
            [str(w.message) for w in caught
             if issubclass(w.category, SAWarning)],
            [])


if __name__ == "__main__":
    unittest.main()
