#!/usr/bin/env python3

"""Tests for cms.server.util's module-level setup."""

import unittest


class TestNoLegacyTornadoWorkarounds(unittest.TestCase):

    def test_no_mutablemapping_monkeypatch_needed(self):
        # cms.server.util used to monkey-patch collections.MutableMapping
        # back onto the collections module for Tornado 4.5.3's benefit.
        # Tornado 6.x doesn't need it -- importing this module shouldn't
        # touch collections.MutableMapping at all.
        import collections
        had_attr_before = hasattr(collections, "MutableMapping")
        import cms.server.util  # noqa: F401 (import side effect is the test)
        has_attr_after = hasattr(collections, "MutableMapping")
        self.assertEqual(had_attr_before, has_attr_after)

    def test_tornado_version_is_6x(self):
        import tornado
        self.assertTrue(
            tornado.version.startswith("6."),
            "Expected Tornado 6.x, got %r -- the rest of this migration "
            "assumes Tornado 6's native asyncio IOLoop integration." %
            tornado.version)
