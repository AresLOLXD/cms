"""Tests for ranking group name validation."""

import unittest
from importlib.resources import files

from cmscommon.ranking_groups import GROUP_NAME_RE, RESERVED_GROUP_NAMES, \
    is_valid_group_name


class TestIsValidGroupName(unittest.TestCase):

    def test_accepts_slugs(self):
        for name in ["olim", "omips", "day-1", "senior_2026", "a"]:
            self.assertTrue(is_valid_group_name(name), name)

    def test_rejects_invalid_characters(self):
        for name in ["", "Olim", "olim/", "ol im", "olím", "olim.",
                     "olim\n"]:
            self.assertFalse(is_valid_group_name(name), repr(name))

    def test_rejects_reserved_names(self):
        for name in RESERVED_GROUP_NAMES:
            self.assertFalse(is_valid_group_name(name), name)

    def test_static_entries_are_reserved(self):
        # Any top-level static entry that looks like a group name would be
        # shadowed by the namespace dispatcher.
        static = files("cmsranking") / "static"
        for entry in static.iterdir():
            if GROUP_NAME_RE.fullmatch(entry.name):
                self.assertIn(entry.name, RESERVED_GROUP_NAMES)


if __name__ == "__main__":
    unittest.main()
