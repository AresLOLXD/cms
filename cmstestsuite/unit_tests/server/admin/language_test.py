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

"""Tests for the language AWS chooses for a request.

The handlers are built by hand, with a fake request and the Spanish
catalog made in memory, so no .mo file is needed.

"""

import unittest
from http.cookies import SimpleCookie
from io import BytesIO
from types import SimpleNamespace

from babel.messages import Catalog
from babel.messages.mofile import write_mo
from tornado.httputil import HTTPHeaders

from cms.locale import DEFAULT_TRANSLATION, Translation
from cms.server.admin.handlers.base import BaseHandler


def _spanish() -> Translation:
    catalog = Catalog(locale="es")
    catalog.add("Login", "Entrar")
    mo = BytesIO()
    write_mo(mo, catalog)
    mo.seek(0)
    return Translation("es", mo, "aws")


TRANSLATIONS = {"en": DEFAULT_TRANSLATION, "es": _spanish()}


def make_handler(accept: str | None = None, cookie: str | None = None,
                 setup: bool = True):
    """Return a handler whose request has these headers and cookie."""
    handler = BaseHandler.__new__(BaseHandler)
    headers = {} if accept is None else {"Accept-Language": accept}
    cookies = SimpleCookie()
    if cookie is not None:
        cookies["aws_language"] = cookie
    handler.request = SimpleNamespace(headers=headers, cookies=cookies)
    handler.application = SimpleNamespace(
        service=SimpleNamespace(translations=TRANSLATIONS))
    handler._headers = HTTPHeaders()
    handler.url = handler.static_url_helper = None
    handler.xsrf_form_html = lambda: ""
    if setup:
        handler.setup_locale()
    return handler


class TestLanguageChoice(unittest.TestCase):

    def assert_language(self, handler, code: str, text: str):
        self.assertEqual(handler.translation.identifier, code)
        self.assertEqual(handler._headers["Content-Language"], code)
        self.assertEqual(handler._("Login"), text)
        params = handler._base_render_params(None)
        self.assertEqual(params["gettext"]("Login"), text)
        self.assertIs(params["translation"], handler.translation)
        self.assertEqual(list(params["available_translations"]), ["en", "es"])

    def test_browser_in_spanish(self):
        # Scenario: Browser in Spanish
        self.assert_language(make_handler("es-MX"), "es", "Entrar")

    def test_browser_in_english_or_another_language(self):
        # Scenario: Browser in English or another language
        for accept in ("en-US", "fr", None):
            with self.subTest(accept=accept):
                self.assert_language(make_handler(accept), "en", "Login")

    def test_upper_case_accept_language_does_not_fail(self):
        for accept, code, text in (("ES", "es", "Entrar"),
                                   ("EN-US", "en", "Login"),
                                   ("Es-MX", "es", "Entrar")):
            with self.subTest(accept=accept):
                self.assert_language(make_handler(accept), code, text)

    def test_the_cookie_wins(self):
        # Scenario: The cookie wins
        self.assert_language(make_handler("es-MX", "en"), "en", "Login")
        self.assert_language(make_handler("en-US", "es"), "es", "Entrar")

    def test_a_bad_cookie_is_ignored(self):
        # Scenario: A bad cookie is ignored
        for cookie in ("fr", "xx", ""):
            with self.subTest(cookie=cookie):
                handler = make_handler("es-MX", cookie)
                self.assert_language(handler, "es", "Entrar")
                self.assertIsNone(handler.cookie_translation)

    def test_a_handler_that_never_ran_prepare_speaks_english(self):
        # Handlers built with __new__ by other tests keep working.
        handler = BaseHandler.__new__(BaseHandler)
        self.assertEqual(handler._("Login"), "Login")
        self.assertEqual(handler.n_("one", "many", 2), "many")
        self.assertIs(handler.translation, DEFAULT_TRANSLATION)

    def test_the_error_page_chooses_the_language_without_prepare(self):
        # prepare() may not have run (a slot timeout, the XSRF check).
        handler = make_handler("es-MX", setup=False)
        rendered = {}
        handler.render = lambda name, **params: rendered.update(params)

        handler.write_error(503)

        self.assertEqual(rendered["gettext"]("Login"), "Entrar")
        self.assertEqual(handler._headers["Content-Language"], "es")


if __name__ == "__main__":
    unittest.main()
