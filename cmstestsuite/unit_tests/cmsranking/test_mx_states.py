#!/usr/bin/env python3

import unittest

from cmsranking.mx_states import MX_STATES


class TestMxStates(unittest.TestCase):

    def test_has_32_entries(self):
        self.assertEqual(len(MX_STATES), 32)

    def test_keys_are_three_uppercase_letters(self):
        for code in MX_STATES:
            self.assertEqual(len(code), 3)
            self.assertTrue(code.isupper())
            self.assertTrue(code.isalpha())

    def test_values_are_nonempty_strings(self):
        for name in MX_STATES.values():
            self.assertIsInstance(name, str)
            self.assertGreater(len(name), 0)

    def test_known_entries(self):
        self.assertEqual(MX_STATES["JAL"], "Jalisco")
        self.assertEqual(MX_STATES["CMX"], "Ciudad de México")
        self.assertEqual(MX_STATES["MEX"], "Estado de México")


if __name__ == "__main__":
    unittest.main()
