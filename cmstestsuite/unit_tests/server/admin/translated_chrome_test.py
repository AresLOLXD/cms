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

"""Tests for the shared chrome of AWS in Spanish (change aws-language-rest,
lane L0): the sidebar and footer, the logged-in login page, the notifications
and form errors of the base handler, the dataset attributes, and the shared
script aws_utils.js, which runs under node against stubs.

Nothing here renders a page in a browser.

"""

import html
import importlib.resources
import json
import re
import shutil
import subprocess
import threading
import unittest
from datetime import datetime
from functools import partial
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cms.server.admin import formatting
from cms.server.admin.handlers import base
from cms.server.admin.handlers.base import BaseHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cms.server.admin.server import AdminWebServer
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH
from cmstestsuite.unit_tests.server.admin.theme_aws_test import render_page


STATIC = importlib.resources.files("cms.server.admin").joinpath("static")
AWS_UTILS = STATIC.joinpath("aws_utils.js").read_text(encoding="utf-8")


def spanish(page: str, **params) -> str:
    """Render the page as AWS does when Spanish is chosen."""
    return render_page(page, translation=SPANISH, gettext=SPANISH.gettext,
                       ngettext=SPANISH.ngettext, **params)


def sidebar(contest=None, **params) -> dict:
    """Return the parameters of a logged-in base page."""
    config = SimpleNamespace(web_server=SimpleNamespace(
        secret_key="key", DEFAULT_SECRET_KEY="default"))
    named = [SimpleNamespace(id=i, name=f"n{i}", title=f"n{i}",
                             description=f"d{i}") for i in (1, 2)]
    return dict(
        admin=SimpleNamespace(name="root", permission_all=True),
        contest=contest, timestamp=datetime(2026, 1, 1), config=config,
        contest_list=named, task_list=named, user_list=named,
        team_list=named[:1], ranking_group_list=named[:1], **params)


def a_contest() -> SimpleNamespace:
    group = SimpleNamespace(
        start=datetime(2026, 1, 1), stop=datetime(2026, 1, 2),
        analysis_start=datetime(2026, 1, 3),
        analysis_stop=datetime(2026, 1, 4),
        analysis_enabled=False)
    return SimpleNamespace(
        id=3, name="c", description="d", main_group=group, timezone=None,
        allow_user_tests=True)


class TestSidebar(unittest.TestCase):

    def test_the_sidebar_in_spanish(self):
        # Scenario "A page group in Spanish" (aws-language): the chrome
        html_ = spanish("base.html", **sidebar())

        for text in ("Administración", 'Hola, <a href="/admins">root</a>.',
                     'value="Cerrar sesión"', "Resumen", "Uso de recursos",
                     "Exportar", "Concursos", "(crear concurso nuevo…)",
                     "Problemas",
                     "(crear problema nuevo…)", "Usuarios", "Equipos",
                     "(crear equipo nuevo…)",
                     "(crear grupo de ranking nuevo…)"):
            self.assertIn(text, html_)
        # plurals: 2 users and 1 team, and the ranking group list of one
        for text in ("(mostrar los 2 usuarios…)", "(mostrar el único equipo…)",
                     "(mostrar 1 grupo de ranking…)"):
            self.assertIn(text, html_)
        self.assertNotIn("Administration", html_)
        self.assertNotIn("(show", html_)
        self.assertIn("<title>Admin</title>", html_)

    def test_the_contest_sidebar_and_footer_in_spanish(self):
        # Scenario "A page group in Spanish" (aws-language): the chrome of
        # a contest page, the title with the description, the footer
        html_ = spanish("base.html", **sidebar(a_contest(), unanswered=0))

        for text in ("<title>Admin: d</title>", ">General</a>", ">Ranking</a>",
                     ">Actividad</a>", ">Envíos</a>", ">User tests</a>",
                     ">Preguntas</a>", ">Anuncios</a>",
                     ">código</a> de CMS se publica bajo la<br/>"):
            self.assertIn(text, html_)

    def test_the_archive_banner_and_secret_notice_in_spanish(self):
        # Scenario "A page group in Spanish" (aws-language)
        config = SimpleNamespace(web_server=SimpleNamespace(
            secret_key="default", DEFAULT_SECRET_KEY="default"))
        params = sidebar(archived_group=SimpleNamespace(id=9, name="G"))
        params["config"] = config

        html_ = spanish("base.html", **params)

        self.assertIn("¡Cambia secret_key en cms.toml!", html_)
        self.assertIn("Por ejemplo,", html_)
        self.assertIn('El grupo de ranking <a href="/ranking_group/9">G</a> '
                      "está archivado", html_)

    def test_the_sidebar_in_english_is_unchanged(self):
        # Scenario "A page in English is unchanged" (aws-language)
        html_ = render_page("base.html", **sidebar())

        for text in ("Administration", 'value="Logout"',
                     "(show the 2 users...)",
                     "(show the only team...)",
                     "(show the 1 ranking groups...)",
                     "(create new ranking group...)", "<title>Admin</title>",
                     'Hello, <a href="/admins">root</a>.'):
            self.assertIn(text, html_)
        contest_page = render_page(
            "base.html", **sidebar(a_contest(), unanswered=0))
        self.assertIn("<title>Admin: d</title>", contest_page)

    def test_the_logged_in_login_page_in_spanish(self):
        # Scenario "A page group in Spanish" (aws-language): the login page
        # of someone who already logged in
        handler = MagicMock()
        handler.get_argument.return_value = ""
        admin = SimpleNamespace(name="root")

        html_ = spanish("login.html", handler=handler, admin=admin)
        english = render_page("login.html", handler=handler, admin=admin)

        self.assertIn('Ya iniciaste sesión. <a href="/logout">Cierra la '
                      'sesión</a> o ve al <a href="/">resumen</a>.', html_)
        self.assertIn('You are already logged in. <a href="/logout">Logout'
                      '</a> or go to the <a href="/">overview</a>.', english)

    def test_the_overload_warning_in_spanish(self):
        # Scenario "A page group in Spanish" (aws-language)
        config = SimpleNamespace(contest_web_server=SimpleNamespace(
            listen_port=[1]))
        contest = SimpleNamespace(participations=[0] * 150)
        template = AWS_ENVIRONMENT.get_template(
            "fragments/overload_warning.html")

        spanish_html = template.render(
            config=config, contest=contest, gettext=SPANISH.gettext,
            ngettext=SPANISH.ngettext)
        english_html = template.render(config=config, contest=contest)

        self.assertIn("Por ahora tienes 150.0 usuarios por cada "
                      "ContestWebServer, en promedio.<br/>", spanish_html)
        self.assertIn("At the moment, you have 150.0 users for each "
                      "ContestWebServer, on average.<br/>One CWS",
                      english_html)


class TestBaseHandlerTexts(unittest.TestCase):

    def setUp(self):
        self.handler = BaseHandler.__new__(BaseHandler)
        self.handler.application = MagicMock()
        self.handler.sql_session = MagicMock()
        self.handler.request = MagicMock(method="POST", path="/x")
        self.notification = self.handler.application.service.add_notification
        patch.object(base, "logger").start()
        self.addCleanup(patch.stopall)

    def speak_spanish(self):
        self.handler._ = SPANISH.gettext
        self.handler.n_ = SPANISH.ngettext

    def arguments(self, **values):
        self.handler.get_argument = \
            lambda name, default=None: values.get(name, default)

    def test_the_notifications_of_a_commit_in_spanish(self):
        # Scenario "Invalid field in Spanish" family (aws-language): the
        # subject and the text of the notification are in Spanish
        self.speak_spanish()

        self.assertTrue(self.handler.try_commit())
        self.assertEqual(self.notification.call_args.args[1:],
                         ("La operación se completó.", ""))

        from sqlalchemy.exc import OperationalError
        self.handler.sql_session.commit.side_effect = OperationalError(
            "SELECT 1", {}, Exception("gone"), connection_invalidated=True)
        self.assertFalse(self.handler.try_commit())
        self.assertEqual(
            self.notification.call_args.args[1:],
            ("La operación falló.",
             "La base de datos no respondió. Inténtalo de nuevo."))

    def test_the_notifications_of_a_commit_in_english_are_unchanged(self):
        # Scenario "Invalid field in English" (aws-language)
        self.assertTrue(self.handler.try_commit())
        self.assertEqual(self.notification.call_args.args[1:],
                         ("Operation successful.", ""))

        from sqlalchemy.exc import OperationalError
        self.handler.sql_session.commit.side_effect = OperationalError(
            "SELECT 1", {}, Exception("gone"), connection_invalidated=True)
        self.assertFalse(self.handler.try_commit())
        self.assertEqual(
            self.notification.call_args.args[1:],
            ("Operation failed.", "The database did not answer, try again."))

    def test_a_notification_keeps_the_language_of_who_raised_it(self):
        # Scenario "A notification read by another admin" (aws-language)
        service = SimpleNamespace(notifications=[],
                                  notifications_lock=threading.Lock())
        service.add_notification = partial(
            AdminWebServer.add_notification, service)
        service.take_notifications = partial(
            AdminWebServer.take_notifications, service)
        self.handler.application.service = service
        self.speak_spanish()
        self.handler.try_commit()

        # An admin with English chosen polls first: the text is Spanish.
        [(_, subject, text)] = service.take_notifications()

        self.assertEqual((subject, text), ("La operación se completó.", ""))

    def test_a_bad_number_in_spanish(self):
        # Scenario "Invalid field in Spanish" (aws-language)
        self.speak_spanish()
        self.arguments(time_limit="abc", memory_limit="abc", flag="1")
        attrs: dict = {}

        with self.assertRaises(ValueError) as caught:
            self.handler.get_time_limit(attrs, "time_limit")
        self.assertEqual(repr(caught.exception), repr(ValueError(
            "No se puede convertir abc a número decimal.")))
        with self.assertRaises(ValueError) as caught:
            self.handler.get_memory_limit(attrs, "memory_limit")
        self.assertEqual(str(caught.exception),
                         "No se puede convertir abc a entero.")
        self.arguments(time_limit="-1", memory_limit="0")
        with self.assertRaises(ValueError) as caught:
            self.handler.get_time_limit(attrs, "time_limit")
        self.assertEqual(str(caught.exception),
                         "El límite de tiempo está fuera de rango.")
        with self.assertRaises(ValueError) as caught:
            self.handler.get_memory_limit(attrs, "memory_limit")
        self.assertEqual(str(caught.exception), "Límite de memoria no válido.")

    def test_the_form_errors_in_english_are_unchanged(self):
        # Scenario "Invalid field in English" (aws-language), repr included
        self.arguments(time_limit="abc", memory_limit="0",
                       submission_format_choice="x")
        for method, field, expected in (
                (self.handler.get_time_limit, "time_limit",
                 "Can't cast abc to float."),
                (self.handler.get_memory_limit, "memory_limit",
                 "Invalid memory limit.")):
            with self.assertRaises(ValueError) as caught:
                method({}, field)
            self.assertEqual(repr(caught.exception),
                             repr(ValueError(expected)))
        with self.assertRaises(ValueError) as caught:
            self.handler.get_submission_format({"name": "n"})
        self.assertEqual(str(caught.exception),
                         "Submission format not recognized.")

    def test_the_task_and_score_type_errors_in_spanish(self):
        # Scenario "Invalid field in Spanish" (aws-language)
        self.speak_spanish()
        self.arguments(score_params="{not json", score="Sum",
                       task_type="Nope")
        for call, text in (
                (lambda: self.handler.get_task_type({}, "task_type", "p"),
                 "No se reconoce el tipo de problema: Nope."),
                (lambda: self.handler.get_task_type({}, "missing", "p"),
                 "No se encontró el tipo de problema."),
                (lambda: self.handler.get_score_type(
                    {}, "score", "score_params"),
                 "Los parámetros del tipo de puntaje no son un JSON válido."),
                (lambda: self.handler.get_score_type({}, "missing", "p"),
                 "No se encontró el tipo de puntaje.")):
            with self.assertRaises(ValueError) as caught:
                call()
            self.assertEqual(str(caught.exception), text)

    def test_the_main_group_times_in_spanish(self):
        # Scenario "Invalid field in Spanish" (aws-language): the asserts
        self.speak_spanish()
        self.arguments()

        with self.assertRaises(AssertionError) as caught:
            self.handler.get_group_settings(MagicMock())

        self.assertEqual(str(caught.exception),
                         "No se indicó la hora de inicio del grupo principal.")


class TestFormatDatasetAttrs(unittest.TestCase):

    def dataset(self, live: bool, autojudge: bool):
        dataset = SimpleNamespace(autojudge=autojudge)
        dataset.task = SimpleNamespace(
            active_dataset=dataset if live else None)
        return dataset

    def test_the_default_is_english(self):
        # Scenario "A page in English is unchanged" (aws-language)
        self.assertEqual(
            formatting.format_dataset_attrs(self.dataset(True, False)),
            " (Live)")
        self.assertEqual(
            formatting.format_dataset_attrs(self.dataset(False, True)),
            " (Background judging)")
        self.assertEqual(
            formatting.format_dataset_attrs(self.dataset(False, False)), "")

    def test_the_attributes_in_spanish(self):
        # Scenario "A page group in Spanish" (aws-language)
        self.assertEqual(
            formatting.format_dataset_attrs(
                self.dataset(True, False), SPANISH.gettext), " (En vivo)")
        self.assertEqual(
            formatting.format_dataset_attrs(
                self.dataset(False, True), SPANISH.gettext),
            " (Evaluación en segundo plano)")

    def test_the_filter_follows_the_language_of_the_page(self):
        # Scenario "A page group in Spanish" (aws-language): the filter
        # takes the gettext of the request from the template context
        template = AWS_ENVIRONMENT.from_string(
            "{{ dataset|format_dataset_attrs }}")
        dataset = self.dataset(True, False)

        self.assertEqual(template.render(dataset=dataset), " (Live)")
        self.assertEqual(
            template.render(dataset=dataset, gettext=SPANISH.gettext),
            " (En vivo)")


# ----------------------------------------------------------------- scripts
# Runs the script and then the scenario against stubs; the scenario's last
# value is printed as json. Date is fixed, so the times are exact.
HARNESS = r"""
const vm = require("vm");
const [source, messages, scenario] = process.argv.slice(1);
const fixed = Date.UTC(2026, 0, 1, 12, 0, 0);
class FixedDate extends Date {
    constructor(...args) {
        if (args.length) { super(...args); } else { super(fixed); }
    }
    static now() { return fixed; }
}
const context = {
    Date: FixedDate,
    window: { addEventListener() {} },
    document: {
        addEventListener() {},
        // messages === "" stands for a page whose body does not exist yet.
        body: messages === "" ? undefined : {
            getAttribute(name) {
                return name === "data-messages" ? messages : null;
            },
        },
    },
};
vm.createContext(context);
vm.runInContext(source, context);
console.log(JSON.stringify(vm.runInContext(scenario, context)));
"""

# What the shared script shows, with the stubs of the harness.
SCENARIO = r"""
var out = {};
$ = function(sel) { return {text: function(v) { out[sel] = v; }}; };
var alerts = [];
alert = function(m) { alerts.push(m); };
var u = new CMS.AWSUtils("/aws/", 1000, 2000, 3000, 4000, 5000, 1, 7);
var texts = {};
texts.na = u.repr_job(null);
texts.disabled = u.repr_job("disabled");
texts.compile = u.repr_job({type: "compile", object_id: 5, dataset_id: 2});
texts.evaluate = u.repr_job({type: "evaluate", object_id: 5, dataset_id: 2,
    multiplicity: 3, testcase_codename: "t<1>&"});
texts.evaluate_plain = u.repr_job(
    {type: "evaluate", object_id: 5, dataset_id: 2});
texts.compile_test = u.repr_job(
    {type: "compile_test", object_id: 6, dataset_id: 2});
texts.evaluate_test = u.repr_job(
    {type: "evaluate_test", object_id: 6, dataset_id: 2});
var now = new Date().getTime() / 1000;
texts.ago_s = u.repr_time_ago(now - 5);
texts.ago_ms = u.repr_time_ago(now - 125);
texts.ago_hms = u.repr_time_ago(now - 3725);
texts.ago_none = u.repr_time_ago(null);
texts.std_ok = u.standard_response({status: "ok"});
texts.std_unconnected = u.standard_response({status: "unconnected"});
texts.std_unauthorized = u.standard_response({status: "not authorized"});
texts.std_fail = u.standard_response({status: "fail"});
texts.std_other = u.standard_response({status: "<b>x"});
u.redirect_if_ok("/next", {status: "fail"});
u.file_received(null, "boom");
u.timestamp = 1000; u.first_date = new Date();
function remaining(a, b, c, d) {
    u.contest_start = a; u.contest_stop = b;
    u.analysis_start = c; u.analysis_stop = d;
    u.update_remaining_time();
    return out["#remaining_text"] + out["#remaining_value"];
}
texts.remaining_before = remaining(1100, 1200, 1300, 1400);
texts.remaining_during = remaining(900, 1200, 1300, 1400);
texts.analysis_before = remaining(800, 900, 1100, 1400);
texts.analysis_during = remaining(800, 900, 1000, 1400);
var box = {style: {display: "none"}};
var invoker = {innerHTML: "", parentElement: {parentElement: {
    querySelector: function() { return box; }}}};
var event = {preventDefault: function() {}};
u.question_reply_toggle(event, invoker); texts.reply_open = invoker.innerHTML;
u.question_reply_toggle(event, invoker);
texts.reply_closed = invoker.innerHTML;
texts.alerts = alerts;
texts;
"""

URLS = {
    "result": '<a href="/aws/submission/5/2">',
    "submission": '<a href="/aws/submission/5">',
    "dataset": '<a href="/aws/dataset/2">',
}

ENGLISH = {
    "na": "N/A",
    "disabled": "Worker disabled",
    "compile": 'Compiling the <a href="/aws/submission/5/2">result</a> of '
               '<a href="/aws/submission/5">submission 5</a> on '
               '<a href="/aws/dataset/2">dataset 2</a>',
    # The codename is escaped now: it was put in the html as it came.
    "evaluate": 'Evaluating the <a href="/aws/submission/5/2">result</a> of '
                '<a href="/aws/submission/5">submission 5</a> on '
                '<a href="/aws/dataset/2">dataset 2</a> '
                "[3 time(s) in queue] [testcase: `t&lt;1&gt;&amp;']",
    "evaluate_plain": 'Evaluating the <a href="/aws/submission/5/2">result'
                      '</a> of <a href="/aws/submission/5">submission 5</a>'
                      ' on <a href="/aws/dataset/2">dataset 2</a>',
    "compile_test": "Compiling the result of user_test 6 on "
                    '<a href="/aws/dataset/2">dataset 2</a>',
    "evaluate_test": "Evaluating the result of user_test 6 on "
                     '<a href="/aws/dataset/2">dataset 2</a>',
    "ago_s": "5 second(s)",
    "ago_ms": "2 minute(s), 5 second(s)",
    "ago_hms": "1 hour(s), 2 minute(s), 5 second(s)",
    "ago_none": "N/A",
    "std_ok": "",
    "std_unconnected": "Service not connected.",
    "std_unauthorized": "You are not authorized to call this method.",
    "std_fail": "Call to service failed.",
    # Escaped, like the codename.
    "std_other": "Unexpected reply `&lt;b&gt;x'. This should not happen.",
    "remaining_before": "To start of contest: 0:01:40",
    "remaining_during": "To end of contest: 0:03:20",
    "analysis_before": "To start of analysis: 0:01:40",
    "analysis_during": "To end of analysis: 0:06:40",
    "reply_open": "Hide reply",
    "reply_closed": "Reply",
    "alerts": ["Unable to invalidate (Call to service failed.).",
               "File request failed."],
}

SPANISH_PINS = {
    "disabled": "Worker desactivado",
    "compile": 'Compilando el <a href="/aws/submission/5/2">resultado</a> del'
               ' <a href="/aws/submission/5">envío 5</a> en el '
               '<a href="/aws/dataset/2">dataset 2</a>',
    "evaluate": 'Evaluando el <a href="/aws/submission/5/2">resultado</a> del'
                ' <a href="/aws/submission/5">envío 5</a> en el '
                '<a href="/aws/dataset/2">dataset 2</a> '
                "[3 vez/veces en la cola] [caso de prueba: «t&lt;1&gt;&amp;»]",
    "evaluate_test": "Evaluando el resultado de user_test 6 en el "
                     '<a href="/aws/dataset/2">dataset 2</a>',
    "ago_hms": "1 hora(s), 2 minuto(s), 5 segundo(s)",
    "std_unauthorized": "No tienes autorización para llamar a este método.",
    "std_other": "Respuesta inesperada «&lt;b&gt;x». "
                 "Esto no debería ocurrir.",
    "remaining_before": "Para el inicio del concurso: 0:01:40",
    "analysis_during": "Para el fin de la fase de análisis: 0:06:40",
    "reply_open": "Ocultar respuesta",
    "reply_closed": "Responder",
    "alerts": ["No se pudo invalidar (Falló la llamada al servicio.).",
               "Falló la solicitud del archivo."],
}


def page_messages(translated: bool) -> dict:
    """Return the messages that base.html puts in the body, parsed as the
    browser would."""
    page = spanish if translated else render_page
    found = re.search(
        r'<body[^>]*data-messages="([^"]*)"',
        page("base.html", **sidebar()))
    assert found is not None
    return json.loads(html.unescape(found.group(1)))


def run_script(messages: str, scenario: str):
    result = subprocess.run(
        ["node", "-e", HARNESS, AWS_UTILS, messages, scenario],
        capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestSharedScript(unittest.TestCase):

    def test_the_english_texts_are_the_ones_it_had(self):
        # Scenario "A page in English is unchanged" (aws-language)
        shown = run_script(json.dumps(page_messages(False)), SCENARIO)

        self.assertEqual(shown, ENGLISH)

    def test_the_texts_in_spanish(self):
        # Scenario "A shared script message in Spanish" (aws-language)
        shown = run_script(json.dumps(page_messages(True)), SCENARIO)

        for key, text in SPANISH_PINS.items():
            self.assertEqual(shown[key], text, key)
        # Same sentences, same links: only the words changed.
        for key in ("compile", "evaluate", "compile_test"):
            self.assertEqual(re.findall(r'href="[^"]*"', shown[key]),
                             re.findall(r'href="[^"]*"', ENGLISH[key]), key)

    def test_a_message_is_read_when_the_body_exists(self):
        # Scenario "A shared script message in Spanish" (aws-language): the
        # script loads in <head>, so t() must not read the body at load
        # time, and must work at DOMContentLoaded
        scenario = (
            'CMS.AWSUtils.t("pages");'
        )
        # No body yet: the key comes back and nothing is cached.
        self.assertEqual(run_script("", scenario), "pages")
        lazy = (
            "var before = CMS.AWSUtils.t('pages');"
            "document.body = {getAttribute: function() {"
            "return '{\"pages\": \"Páginas:\"}';}};"
            "[before, CMS.AWSUtils.t('pages')];")
        self.assertEqual(run_script("", lazy), ["pages", "Páginas:"])

    def test_values_are_filled_in_one_pass(self):
        # Scenario "A shared script message in Spanish" (aws-language)
        messages = json.dumps({"k": "%(a)s and %(b)s, %(c)s"})
        scenario = (
            'CMS.AWSUtils.t("k", {a: "%(b)s", b: "$&", c: 7});')

        # A value that looks like a placeholder is not filled again, "$&"
        # is not a replacement pattern, a missing value stays as it is.
        self.assertEqual(run_script(messages, scenario),
                         "%(b)s and $&, 7")
        self.assertEqual(
            run_script(messages, 'CMS.AWSUtils.t("k", {a: 1});'),
            "1 and %(b)s, %(c)s")
        self.assertEqual(run_script(messages, 'CMS.AWSUtils.t("nope");'),
                         "nope")


class TestScriptKeys(unittest.TestCase):

    @staticmethod
    def used_keys() -> set[str]:
        """Return the keys that aws_utils.js asks for."""
        return set(re.findall(r'\bt\(\s*"([^"]+)"', AWS_UTILS))

    def test_every_key_asked_is_in_the_page_and_the_reverse(self):
        # Scenario "A shared script message in Spanish" (aws-language): the
        # ratchet between aws_utils.js and base.html
        provided = set(page_messages(False))

        self.assertEqual(self.used_keys() - provided, set())
        self.assertEqual(provided - self.used_keys(), set())

    def test_every_message_is_translated(self):
        # Scenario "A shared script message in Spanish" (aws-language)
        english, translated = page_messages(False), page_messages(True)

        self.assertEqual(set(english), set(translated))
        # Only these stay as they are in Spanish.
        same = {key for key in english if english[key] == translated[key]}
        self.assertEqual(same, {"not_available", "error"})

    def test_no_script_shows_a_literal_text(self):
        # Scenario "A new untranslated text" (aws-language): no alert,
        # confirm or prompt with a literal in a static script
        found = []
        for script in sorted(STATIC.iterdir(), key=lambda p: p.name):
            if script.name.endswith(".js") and script.name not in (
                    "prism.js", "jquery.min.js"):
                for number, line in enumerate(
                        script.read_text(encoding="utf-8").splitlines(), 1):
                    if re.search(r"\b(alert|confirm|prompt)\(\s*[\"']", line):
                        found.append(f"{script.name}:{number}: {line.strip()}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
