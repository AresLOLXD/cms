"""Tests for ranking group name validation."""

import unittest
from datetime import datetime
from importlib.resources import files

from cmscommon.ranking_groups import GROUP_NAME_RE, RESERVED_GROUP_NAMES, \
    is_valid_group_name, check_window, window_is_open


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


class TestWindows(unittest.TestCase):

    def test_window_is_half_open(self):
        self.assertFalse(window_is_open(10, 20, 9))
        self.assertTrue(window_is_open(10, 20, 10))
        self.assertTrue(window_is_open(10, 20, 19))
        self.assertFalse(window_is_open(10, 20, 20))

    def test_missing_start_never_opens(self):
        self.assertFalse(window_is_open(None, 20, 15))
        self.assertFalse(window_is_open(None, None, 15))

    def test_missing_end_never_closes(self):
        self.assertTrue(window_is_open(10, None, 10 ** 12))

    def test_works_with_datetimes(self):
        start = datetime(2026, 10, 10, 13, 0)
        end = datetime(2026, 10, 10, 16, 0)
        self.assertTrue(window_is_open(start, end,
                                       datetime(2026, 10, 10, 15, 59)))
        self.assertFalse(window_is_open(start, end, end))

    def test_check_window(self):
        check_window(None, None, "freeze")
        check_window(10, None, "freeze")
        check_window(None, 20, "freeze")
        check_window(10, 20, "freeze")
        for end in (10, 9):
            with self.assertRaisesRegex(ValueError, "freeze"):
                check_window(10, end, "freeze")


if __name__ == "__main__":
    unittest.main()
