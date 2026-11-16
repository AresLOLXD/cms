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

"""Tests for loading the catalogs of one domain (cms or aws).

"""

import importlib.resources
import logging

import pytest
from babel.messages import Catalog
from babel.messages.mofile import write_mo

from cms.locale import get_translations


def _write_catalog(root, lang, domain, msgid, msgstr):
    path = root / lang / "LC_MESSAGES"
    path.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(locale=lang)
    catalog.add(msgid, msgstr)
    with open(path / f"{domain}.mo", "wb") as f:
        write_mo(f, catalog)


@pytest.fixture
def locale_tree(tmp_path, monkeypatch):
    """A locale tree: es has both catalogs, fr only the cms one."""
    _write_catalog(tmp_path, "es", "aws", "Login", "Entrar")
    _write_catalog(tmp_path, "es", "cms", "Login", "Acceder")
    _write_catalog(tmp_path, "fr", "cms", "Login", "Connexion")
    monkeypatch.setattr(importlib.resources, "files", lambda _: tmp_path)
    return tmp_path


def test_offered_languages(locale_tree):
    # Scenario "Offered languages" (aws-language)
    translations = get_translations("aws")

    assert sorted(translations) == ["en", "es"]
    assert translations["es"].gettext("Login") == "Entrar"


def test_a_quiet_start(locale_tree, caplog):
    # Scenario "A quiet start" (aws-language)
    with caplog.at_level(logging.WARNING):
        get_translations("aws")

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_cms_domain_is_unchanged(locale_tree, caplog):
    # Contestant-site loading keeps its behavior, and its warnings.
    (locale_tree / "de" / "LC_MESSAGES").mkdir(parents=True)

    with caplog.at_level(logging.WARNING):
        translations = get_translations()

    assert sorted(translations) == ["en", "es", "fr"]
    assert translations["es"].gettext("Login") == "Acceder"
    assert [r.getMessage() for r in caplog.records] == [
        "Failed to load translation for de"]


def test_underscore_directories_are_not_offered(locale_tree):
    # The AWS catalog fragments of a change live in cms/locale/_fragments.
    _write_catalog(locale_tree / "_fragments", "fr", "aws", "Login", "Entrer")

    assert sorted(get_translations("aws")) == ["en", "es"]
