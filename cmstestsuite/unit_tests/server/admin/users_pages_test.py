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

"""Tests for the Spanish pages of the users and of the bulk removal.

Each page was Spanish before it was translatable (the pages were written in
Spanish by hand). The pages are rendered in Spanish from the template of
today and from a frozen copy of the template of main at that time
(data/*_before_i18n.html), and the visible text (with the attributes that
carry text) is compared. No browser is involved, so nothing here says how
the pages look.

"""

import re
import unittest
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

from jinja2 import ChoiceLoader, DictLoader

from cms.server.admin.bulkremove import NOT_FOUND, NOT_IN_CONTEST
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH, \
    with_new_terms

DATA = Path(__file__).parent / "data"

# What main said where the new template takes a message id.
REASONS_BEFORE = {NOT_FOUND: "no existe",
                  NOT_IN_CONTEST: "no participa en el concurso"}


def before_environment(name: str):
    path = DATA / (name.replace(".html", "") + "_before_i18n.html")
    return AWS_ENVIRONMENT.overlay(loader=ChoiceLoader([
        DictLoader({name: path.read_text("utf-8")}),
        AWS_ENVIRONMENT.loader]))


def fake_url(*parts: object) -> str:
    return "/" + "/".join(str(part) for part in parts)


def render(environment, name: str, translation=None, **params) -> str:
    template = environment.get_template(name)
    if translation is not None:
        params.update({"gettext": translation.gettext,
                       "ngettext": translation.ngettext})
    params.setdefault("url", fake_url)
    params.setdefault("xsrf_form_html", "")
    params.setdefault("admin", SimpleNamespace(permission_all=True))
    # The overload warning of the contest users page reads the config.
    params.setdefault("config", SimpleNamespace(
        contest_web_server=SimpleNamespace(listen_port=[8888])))
    return "".join(template.blocks["core"](template.new_context(params)))


class Attributes(HTMLParser):
    """Collects the values of the attributes of every tag, in order."""

    def __init__(self):
        super().__init__()
        self.values: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.values += [value for _, value in attrs if value]


def visible(html: str) -> tuple[str, list[str]]:
    """The text a reader gets (scripts and styles apart, whitespace
    normalized) and the values of the attributes."""
    attributes = Attributes()
    attributes.feed(html)
    html = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)
    text = " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())
    return text, [" ".join(v.split()) for v in attributes.values]


def new_terms(page: tuple[str, list[str]]) -> tuple[str, list[str]]:
    """The page of main with the terms of the style guide."""
    return with_new_terms(page[0]), [with_new_terms(v) for v in page[1]]


# The headers that main left in English and that the pages of today
# translate (the users and the bulk removal pages only).
HEADERS_OF_MAIN = [("Users list", "Lista de usuarios"),
                   ("Username", "Usuario"), ("First name", "Nombre"),
                   ("Last name", "Apellido")]


# The English that main left on the contest users page, which lane L6
# translated.
ENGLISH_OF_CONTEST_USERS = [
    ("Users list", "Lista de usuarios"),
    ("Add a new user:", "Agrega un usuario nuevo:"),
    ("Select a new user", "Selecciona un usuario nuevo"),
    ("Add user", "Agregar usuario"), ("Username", "Usuario"),
    ("First name", "Nombre"), ("Last name", "Apellido"),
    ("Group", "Grupo")]


def with_translated_headers(page: tuple[str, list[str]],
                            headers=HEADERS_OF_MAIN) \
        -> tuple[str, list[str]]:
    """The page of main with the English texts in Spanish."""
    def translate(text: str) -> str:
        for old, new in headers:
            text = text.replace(old, new)
        return text
    return translate(page[0]), [translate(v) for v in page[1]]


EVIL = 'Día "1" <b>&</b> 100%'


def entry(number: int, **fields):
    params = {"username": "u%d<" % number, "first_name": "Ana \"%d\"" % number,
              "last_name": "Pérez &", "submissions": number, "user_tests": 1,
              "contests": ["c<1>", "dia2"]}
    params.update(fields)
    return SimpleNamespace(**params)


def plan(count: int, groups=(), ignored=(), running=False):
    users = [entry(n) for n in range(count)]
    return SimpleNamespace(
        users=users, submissions=sum(u.submissions for u in users),
        user_tests=count, groups=list(groups), ignored=list(ignored),
        running=running, fingerprint="f0", user_ids=list(range(count)))


CONTEST = SimpleNamespace(id=1, name=EVIL, groups=[], main_group_id=None,
                          participations=[])
IGNORED = [("bob<", NOT_FOUND), ("ana", NOT_IN_CONTEST)]
REMOVAL_PLANS = {
    "one user": plan(1),
    "several users": plan(3),
    "many users": plan(101),
    "groups": plan(2, groups=[None, "g<1>", "omi"]),
    "running": plan(2, running=True),
    "ignored": plan(2, ignored=IGNORED),
    "only ignored": plan(0, ignored=IGNORED),
    "nothing": plan(0),
}


class TestBulkRemovalPageInSpanish(unittest.TestCase):

    def test_the_page_is_the_page_of_main(self):
        # Scenario: A page in Spanish is the page of main
        before = before_environment("users_bulk_remove.html")
        for contest in (None, CONTEST):
            for error in (None, "Error <b> 5%"):
                for label, removal in REMOVAL_PLANS.items():
                    params = {"contest": contest, "error": error,
                              "action_url": "/a", "back_url": "/b"}
                    old = SimpleNamespace(**vars(removal))
                    old.ignored = [(v, REASONS_BEFORE[r])
                                   for v, r in removal.ignored]
                    self.assertEqual(
                        visible(render(AWS_ENVIRONMENT,
                                       "users_bulk_remove.html", SPANISH,
                                       plan=removal, **params)),
                        with_translated_headers(visible(render(
                            before, "users_bulk_remove.html", plan=old,
                            **params))),
                        msg="%s, contest=%s, error=%s" % (
                            label, contest is not None, error))

    def test_the_sentence_that_follows_the_count_keeps_its_space(self):
        # Scenario: A page in Spanish is the page of main (the space between
        # the count and "Esta operación" was lost once)
        text, _ = visible(render(
            AWS_ENVIRONMENT, "users_bulk_remove.html", SPANISH,
            contest=None, error=None, plan=plan(1), action_url="/a",
            back_url="/b"))
        self.assertIn("1 user tests. Esta operación no se puede deshacer.",
                      text)


class TestUsersPagesInSpanish(unittest.TestCase):

    def test_the_users_page_is_the_page_of_main(self):
        # Scenario: A page in Spanish is the page of main
        before = before_environment("users.html")
        users = [SimpleNamespace(id=n, username="u%d<" % n,
                                 first_name="Ana", last_name="Pérez")
                 for n in range(3)]
        for permission_all in (True, False):
            params = {"user_list": users,
                      "admin": SimpleNamespace(permission_all=permission_all)}
            self.assertEqual(
                visible(render(AWS_ENVIRONMENT, "users.html", SPANISH,
                               **params)),
                with_translated_headers(new_terms(
                    visible(render(before, "users.html", **params)))))

    def test_the_contest_users_page_is_the_page_of_main(self):
        # Scenario: A page in Spanish is the page of main
        before = before_environment("contest_users.html")
        users = [SimpleNamespace(id=n, username="u%d<" % n)
                 for n in range(3)]
        for permission_all in (True, False):
            params = {"contest": CONTEST, "unassigned_users": users,
                      "admin": SimpleNamespace(permission_all=permission_all)}
            self.assertEqual(
                visible(render(AWS_ENVIRONMENT, "contest_users.html",
                               SPANISH, **params)),
                with_translated_headers(new_terms(
                    visible(render(before, "contest_users.html", **params))),
                    ENGLISH_OF_CONTEST_USERS))


if __name__ == "__main__":
    unittest.main()
