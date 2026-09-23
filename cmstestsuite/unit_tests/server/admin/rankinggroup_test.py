"""Tests for the AWS ranking group handlers."""

import unittest
from unittest.mock import MagicMock

from cms.server.admin.handlers.rankinggroup import \
    RegenerateRankingHandler, read_ranking_group_attrs


def form_handler(form: dict) -> MagicMock:
    """Return a fake handler whose get_string reads from form."""
    handler = MagicMock()

    def get_string(dest, name, empty=""):
        if name in form:
            dest[name] = form[name] if form[name] != "" else empty
    handler.get_string.side_effect = get_string
    return handler


class TestReadRankingGroupAttrs(unittest.TestCase):

    def test_valid_group(self):
        attrs = dict()
        read_ranking_group_attrs(
            form_handler({"name": "olim", "description": "OLIM"}), attrs)
        self.assertEqual(attrs, {"name": "olim", "description": "OLIM"})

    def test_description_defaults_to_name(self):
        attrs = dict()
        read_ranking_group_attrs(
            form_handler({"name": "olim", "description": ""}), attrs)
        self.assertEqual(attrs["description"], "olim")

    def test_rejects_reserved_and_invalid_names(self):
        for name in ["events", "Olim", "", "a/b"]:
            with self.assertRaises(ValueError, msg=name):
                read_ranking_group_attrs(
                    form_handler({"name": name, "description": "x"}),
                    dict())


class TestRegenerateRankingHandler(unittest.TestCase):

    def run_post(self, group_arg: str) -> MagicMock:
        handler = RegenerateRankingHandler.__new__(RegenerateRankingHandler)
        # "service" is a read-only property (defined on
        # CommonRequestHandler) that returns self.application.service, so
        # it must be mocked through "application", not assigned directly.
        handler.application = MagicMock()
        handler.get_argument = MagicMock(return_value=group_arg)
        handler.redirect = MagicMock()
        handler.url = MagicMock(return_value="/ranking_groups")
        RegenerateRankingHandler.post.__wrapped__(handler)
        return handler.application.service.proxy_service.regenerate_ranking

    def test_group(self):
        self.run_post("olim").assert_called_once_with(group="olim")

    def test_root(self):
        self.run_post("").assert_called_once_with(group=None)


if __name__ == "__main__":
    unittest.main()
