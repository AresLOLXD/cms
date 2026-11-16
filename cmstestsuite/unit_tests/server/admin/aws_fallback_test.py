#!/usr/bin/env python3

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

"""AWS falls back on the contestant-site catalog for what it lacks."""

import unittest
from unittest.mock import patch

from cms.grading import format_status_text
from cms.locale import DEFAULT_TRANSLATION, Translation
from cms.server.admin.server import AdminWebServer, add_contestant_fallback
from cmstestsuite.unit_tests.server.admin.aws_catalog import CMS_SPANISH, \
    SPANISH


class TestFallback(unittest.TestCase):

    def test_outcome_in_spanish(self):
        # Scenario "Outcome in Spanish" (aws-language): a message that
        # only the contestant-site catalog has.
        self.assertEqual(
            format_status_text(["Output is correct"], SPANISH),
            "Salida correcta")

    def test_outcome_in_english(self):
        # Scenario "Outcome in English" (aws-language)
        self.assertEqual(
            format_status_text(["Output is correct"], DEFAULT_TRANSLATION),
            "Output is correct")

    def test_aws_wins_over_the_fallback(self):
        # Both catalogs have "Username", with different Spanish.
        self.assertEqual(SPANISH.gettext("Username"), "Usuario")
        self.assertEqual(CMS_SPANISH.gettext("Username"), "Nombre de usuario")

    def test_unknown_messages_come_back_unchanged(self):
        self.assertEqual(SPANISH.gettext("No such message"), "No such message")

    def test_the_server_adds_it_to_every_language_but_the_default(self):
        aws_es = Translation("es")
        with patch("cms.server.admin.server.get_translations",
                   return_value={"en": DEFAULT_TRANSLATION,
                                 "es": CMS_SPANISH}):
            add_contestant_fallback({"en": DEFAULT_TRANSLATION, "es": aws_es})

        self.assertIs(aws_es.translation._fallback, CMS_SPANISH.translation)
        # The default translation is shared with the contestant site.
        self.assertIsNone(DEFAULT_TRANSLATION.translation._fallback)

    def test_the_server_constructor_wires_the_fallback(self):
        # Scenario "Outcome in Spanish" (aws-language): AWS itself, not
        # only the helper, adds the contestant-site catalog.
        aws_es = Translation("es")

        def translations(domain=None):
            return {"en": DEFAULT_TRANSLATION, "es": CMS_SPANISH} \
                if domain is None \
                else {"en": DEFAULT_TRANSLATION, "es": aws_es}

        with patch("cms.server.admin.server.WebService.__init__",
                   return_value=None), \
                patch.object(AdminWebServer, "connect_to"), \
                patch("cms.server.admin.server.get_translations",
                      side_effect=translations), \
                patch("cms.server.admin.server.get_service_shards",
                      return_value=0), \
                patch("cms.server.admin.server.set_parse_body_config"), \
                patch("cms.server.admin.server.EXPORT_JOBS"):
            server = AdminWebServer(0)

        self.assertIs(server.translations["es"], aws_es)
        self.assertIs(aws_es.translation._fallback, CMS_SPANISH.translation)
