#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""Tests for the system pages of AWS in Spanish (lane L7 of
aws-language-rest): the admins pages and their handlers, the ranking
group removal page, the overview and the resources pages.

The pages are rendered from the real templates with the Spanish of
aws.po plus the fragments; nothing here says how a page looks.

"""

import html
import json
import re
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from cms.server.admin.handlers.admin import AddAdminHandler, AdminHandler
from cms.server.admin.handlers.rankinggroup import RankingGroupListHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH
from cmstestsuite.unit_tests.server.admin.theme_aws_test import render_page
from cmstestsuite.unit_tests.server.admin.translated_chrome_test import \
    a_contest, sidebar, spanish


class English:
    """The identity translation, as AWS uses it for English."""
    gettext = staticmethod(lambda message: message)

    @staticmethod
    def ngettext(singular, plural, n):
        return singular if n == 1 else plural


def render_core(name, translation=None, **params):
    """Render the core block of a template, in English by default."""
    template = AWS_ENVIRONMENT.get_template(name)
    if translation is not None:
        params.update(gettext=translation.gettext,
                      ngettext=translation.ngettext)
    params.setdefault("url", lambda *parts: "/" + "/".join(
        str(part) for part in parts))
    params.setdefault("xsrf_form_html", "")
    params.setdefault("admin", SimpleNamespace(permission_all=True))
    return "".join(template.blocks["core"](template.new_context(params)))


def make_handler(cls, translation, **form):
    """Return a handler that reads the form and notifies, with no DB."""
    handler = cls.__new__(cls)
    handler._ = translation.gettext
    handler.n_ = translation.ngettext
    handler.application = MagicMock()
    handler.get_argument = lambda name, default=None: form.get(name, default)
    handler.redirect = MagicMock()
    handler.url = lambda *parts: "/" + "/".join(str(p) for p in parts)
    return handler


def notifications(handler):
    return [call.args[1:]
            for call in handler.service.add_notification.call_args_list]


ADMIN = SimpleNamespace(id=4, username="ana", name="Ana", enabled=True,
                        permission_all=False, permission_messaging=True)


class TestAdminPages(unittest.TestCase):

    def test_the_admins_list_in_spanish(self):
        # Scenario "A page group in Spanish" (system and admins)
        html = render_core("admins.html", SPANISH, admins=[ADMIN])

        for text in ("Lista de admins", "<th>Activado</th>",
                     "<th>Usuario</th>", "<th>Todos los permisos</th>",
                     "<th>Permisos de mensajería</th>",
                     "Agregar un admin nuevo",
                     "Los admins desactivados no pueden iniciar sesión.",
                     "Los permisos de «Mensajería» permiten"):
            self.assertIn(text, html)
        self.assertNotIn("Admins list", html)

    def test_the_admin_forms_in_spanish(self):
        # Scenario "A page group in Spanish" (system and admins)
        new = render_core("add_admin.html", SPANISH)
        edit = render_core("admin.html", SPANISH, admin_being_edited=ADMIN)

        for text in ("Nuevo admin", "Información del admin",
                     'value="Crear"', 'value="Limpiar"', "<td>Contraseña</td>",
                     "<td>Activado</td>", "<td>Permisos de mensajería</td>"):
            self.assertIn(text, new)
        for text in ('value="Actualizar"', "Borrar este admin",
                     "Si dejas vacío el campo de contraseña, se conserva "
                     "la contraseña anterior."):
            self.assertIn(text, edit)
        self.assertNotIn("Create", new)

    def test_the_admins_pages_in_english_are_unchanged(self):
        # Scenario "A page in English is unchanged"
        html = render_core("admin.html", admin_being_edited=ADMIN)

        for text in ('value="Update"', 'value="Reset"', "Delete this admin",
                     "Leaving the password field empty will keep the "
                     "previous password."):
            self.assertIn(text, html)


class TestAdminHandlerNotifications(unittest.TestCase):

    def test_an_admin_without_name_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        for translation, subject, error in (
                (SPANISH, "Campos no válidos",
                 "AssertionError('No se indicó el nombre del admin.')"),
                (English, "Invalid field(s)",
                 "AssertionError('No admin name specified.')")):
            handler = make_handler(AddAdminHandler, translation,
                                   username="ana")

            handler._post_sync()

            self.assertEqual(notifications(handler), [(subject, error)])

    def test_an_admin_without_user_or_password_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        for translation, user, password in (
                (SPANISH, "No se indicó el usuario.",
                 "No se permite una contraseña vacía."),
                (English, "No username specified.",
                 "Empty password not permitted.")):
            handler = make_handler(AddAdminHandler, translation)
            handler._post_sync()
            self.assertEqual(notifications(handler)[0][1],
                             f"AssertionError({user!r})")

            handler = make_handler(AddAdminHandler, translation,
                                   username="ana", name="Ana", password="")
            handler._post_sync()
            self.assertEqual(notifications(handler)[0][1],
                             f"AssertionError({password!r})")

    def test_the_edit_handler_notifies_in_spanish(self):
        # Scenario "Invalid field in Spanish"
        handler = make_handler(AdminHandler, SPANISH, name="Ana")
        handler.safe_get_item = MagicMock()

        handler._post_sync("4")

        self.assertEqual(notifications(handler), [
            ("Campos no válidos",
             "AssertionError('No se indicó el usuario.')")])


class TestRankingGroupRemovePage(unittest.TestCase):

    GROUP = SimpleNamespace(id=2, name="olim", description="OLIM",
                           staff_password=None)

    def page(self, translation=None, count=3, **extra):
        return render_core("ranking_group_remove.html", translation,
                           ranking_group=self.GROUP, contest_count=count,
                           **extra)

    def test_the_remove_page_in_spanish(self):
        # Scenario "A page group in Spanish" (system and admins)
        html = self.page(SPANISH)

        for text in ("Borrar grupo de ranking",
                     "¿Seguro que quieres borrar el grupo de ranking olim "
                     "(OLIM)?",
                     "3 concurso(s) dejarán de enviarse a cualquier ranking.",
                     "Su marcador se vaciará. Esta operación no se puede "
                     "deshacer.", "Sí, borrar", ">No</a>"):
            self.assertIn(text, html)
        self.assertIn("Ningún concurso se envía a este ranking.",
                      self.page(SPANISH, count=0))
        self.assertIn("No tienes permiso para borrar un grupo de ranking.",
                      self.page(SPANISH, admin=SimpleNamespace(
                          permission_all=False)))
        self.assertNotIn("Are you sure", html)

    def test_the_remove_page_in_english_is_unchanged(self):
        # Scenario "A page in English is unchanged"
        html = self.page()

        for text in ("Remove ranking group",
                     "Are you sure you want to remove ranking group olim "
                     "(OLIM)?",
                     "3 contest(s) will no longer be sent to any ranking. "
                     "The contests themselves are not removed.",
                     "Its scoreboard will be emptied. This operation cannot "
                     "be undone."):
            self.assertIn(text, " ".join(html.split()))

    def test_the_remove_buttons_post_the_english_value(self):
        # Scenario "No translated posted values": the list and the group
        # page post what RankingGroupListHandler compares with.
        self.assertEqual(RankingGroupListHandler.REMOVE, "Remove")
        list_page = render_core(
            "ranking_groups.html", SPANISH, ranking_group_list=[self.GROUP],
            contest_list=[], views={2: SimpleNamespace(
                hidden_now=False, summary="visible", next_change=None)})

        self.assertIn('value="Remove"', list_page)
        self.assertIn(">Borrar grupo</button>", list_page)


class TestMachineList(unittest.TestCase):

    def test_the_machine_list_in_spanish(self):
        # Scenario "A page group in Spanish" (system and admins)
        html = render_core("resourceslist.html", SPANISH, contest=None,
                           resource_addresses={0: "localhost"})

        for text in ("Lista de máquinas",
                     "Consulta el uso de recursos de las máquinas:",
                     ">localhost</a> (0)", ">Todas</a>"):
            self.assertIn(text, html)
        self.assertNotIn("Machine list", html)


# The scripts of the overview and the resources pages run under node
# against stubs of jQuery, of the shared script and of the browser. The
# page messages are read from the data-messages attribute of the page
# title, as the browser does.
HARNESS = r"""
const vm = require("vm");
const [scriptText, messages, scenario] = process.argv.slice(1);
const recorded = {};
const box = (key) => ({
    html: (value) => { recorded[key] = value; },
    text: (value) => { recorded[key] = value; },
    attr: () => messages,
    ready: () => {},
    click: () => {},
    delegate: () => {},
});
const context = {
    JSON, Object, Array, parseInt, console,
    document: {},
    recorded,
    $: Object.assign((selector) => box(selector), {
        isArray: Array.isArray,
        jqplot: Object.assign(() => ({
            series: [{data: []}, {data: []}, {data: []}],
            axes: {xaxis: {}, yaxis: {}}, replot: () => {},
        }), {DateAxisRenderer: {}, EnhancedLegendRenderer: {}}),
    }),
    utils: {
        standard_response: (r) => r.status === "ok" ? "" : "unreachable",
        repr_job: (job) => "job:" + job,
        repr_time_ago: () => "ago",
        repr_time_ago_short: () => "short",
    },
    cmsrpc_request: () => {},
    setInterval: () => 0,
    clearInterval: () => {},
    asked: [],
};
context.confirm = (question) => { context.asked.push(question); return false; };
vm.createContext(context);
vm.runInContext(scriptText, context);
process.stdout.write(JSON.stringify(vm.runInContext(scenario, context)));
"""

OVERVIEW_SCENARIO = r"""
update_submissions_status({status: "ok", data: {
    scored: 1, scoring: 2, compilation_fail: 3, compiling: 4, evaluating: 5,
    max_compilations: 6, max_evaluations: 7}});
update_queue_status({status: "ok", data: []});
update_workers_status({status: "ok", data: [
    {operations: ["a", "b", "c"], connected: true, start_time: 1},
    {operations: "disabled", connected: false, start_time: 2}]});
var workers = [recorded["#workers_status_table > tbody"],
               recorded["#title_workers_status"]];
update_workers_status({status: "ok", data: []});
update_logs({status: "ok", data: []});
enable_worker(3);
disable_worker(4);
({submissions: recorded["#submissions_status_table > tbody"],
  queue: recorded["#queue_status_table > tbody"],
  workers: workers[0], title: workers[1],
  no_workers: recorded["#workers_status_table > tbody"],
  logs: recorded["#logs_table > tbody"], asked: asked});
"""

RESOURCES_SCENARIO = r"""
var getter = new RSgetter("0", plotOptions);
window = {getters: [getter]};
getter.update_resources_cb({status: "ok", data: [[1000, {
    cpu: {num_cpu: 2, user: 1, system: 2, iowait: 3},
    memory: {ram_total: 1000, swap_total: 500, ram_used: 1, ram_buffers: 2,
             ram_cached: 3, swap_used: 4},
    services: {
        "Worker,0": {running: true, since: 1, threads: 2, resident: 3,
                     virtual: 4, user: 5, sys: 6, autorestart: true},
        "Evaluation,0": {running: true, since: 1, threads: 2, resident: 3,
                         virtual: 4, user: 5, sys: 6, autorestart: false},
        "Stopped,0": {running: false}}}]]});
var link = {parentNode: {}};
getter.kill_service("Worker,0", link);
({services: recorded["#services_0_table > tbody"],
  labels: [getter.cpu_graph.series.map(s => s.label),
           getter.memory_graph.series.map(s => s.label),
           [getter.swap_graph.series[0].label]],
  killing: link.parentNode.innerHTML});
"""


def page_script(page: str, translated: bool, admin_all=True, **params):
    """Return the script of the page, and its page messages, as the
    browser gets them."""
    params.update(sidebar(None))
    params["admin"] = SimpleNamespace(name="root", permission_all=admin_all)
    if page == "resources.html":
        params["resource_addresses"] = {"0": "localhost"}
    text = (spanish if translated else render_page)(page, **params)
    script = next(body for body in re.findall(
        r"<script>(.*?)</script>", text, re.S) if "function init" in body)
    title = re.search(r'<h1 id="\w+" data-messages="([^"]*)"', text)
    assert title is not None
    return script, json.loads(html.unescape(title.group(1)))


def run_page(page: str, scenario: str, translated=False, admin_all=True):
    script, messages = page_script(page, translated, admin_all)
    result = subprocess.run(
        ["node", "-e", HARNESS, script, json.dumps(messages), scenario],
        capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]*>", "", text)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestOverviewScript(unittest.TestCase):

    def test_the_texts_in_english_are_the_ones_it_had(self):
        # Scenario "A page in English is unchanged"
        shown = run_page("overview.html", OVERVIEW_SCENARIO)

        self.assertEqual(shown["submissions"], "".join(
            f"<tr><td>{label}</td><td>{n}</td></tr>" for label, n in (
                ("Scored", 1), ("Scoring...", 2), ("Compilation failed", 3),
                ("Compiling...", 4), ("Evaluating...", 5),
                ("Cannot compile (please check)", 6),
                ("Cannot evaluate (please check)", 7))))
        self.assertEqual(
            shown["queue"], '<tr><td colspan="100">Queue empty.</td></tr>')
        self.assertEqual(
            shown["no_workers"], '<tr><td colspan="5">No workers found.</td>')
        self.assertEqual(
            shown["logs"], '<tr><td colspan="5">No log entries.</td>')
        self.assertEqual(shown["title"],
                         "Workers status (1 connected, 2 configured)")
        self.assertEqual(shown["workers"], (
            '<tr><td style="text-align: center;">0</td>'
            '<td style="text-align: center;">Yes</td>'
            "<td>job:a and 2 more</td><td>ago</td>"
            '<td><button onclick="javascript:disable_worker(0); '
            'return true;">Disable</button></td></tr>'
            '<tr><td style="text-align: center;">1</td>'
            '<td style="text-align: center;">No</td>'
            "<td>job:disabled</td><td>ago</td>"
            '<td><button onclick="javascript:enable_worker(1); '
            'return true;">Enable</button></td></tr>'))
        self.assertEqual(shown["asked"], [
            "Do you really want to enable worker 3?",
            "Do you really want to disable worker 4?"])

    def test_the_texts_in_spanish(self):
        # Scenario "A page group in Spanish" (overview) and Scenario "A
        # confirmation in Spanish"
        shown = run_page("overview.html", OVERVIEW_SCENARIO, translated=True)

        for text in ("Puntaje asignado", "Calculando puntaje…",
                     "Compilación fallida", "No se puede compilar (revisa)"):
            self.assertIn(f"<td>{text}</td>", shown["submissions"])
        self.assertIn("La cola está vacía.", shown["queue"])
        self.assertIn("No se encontraron workers.", shown["no_workers"])
        self.assertIn("No hay entradas de registro.", shown["logs"])
        self.assertEqual(shown["title"],
                         "Estado de los workers (1 conectados, 2 configurados)")
        self.assertIn("<td>job:a y 2 más</td>", shown["workers"])
        self.assertIn(">Sí</td>", shown["workers"])
        self.assertIn(">Desactivar</button>", shown["workers"])
        self.assertIn(">Activar</button>", shown["workers"])
        self.assertEqual(shown["asked"], [
            "¿Seguro que quieres activar el worker 3?",
            "¿Seguro que quieres desactivar el worker 4?"])

    def test_a_worker_button_without_permission_is_disabled(self):
        # Scenario "A page in English is unchanged": the button keeps its
        # disabled attribute whatever the language.
        for translated in (False, True):
            shown = run_page("overview.html", OVERVIEW_SCENARIO,
                             translated=translated, admin_all=False)

            self.assertEqual(shown["workers"].count(
                'return true;" disabled>'), 2)

    def test_every_message_is_used_and_translated(self):
        # Scenario "A page group in Spanish": a key without a use, or a
        # key the script asks for and the page lacks, is a mistake.
        script, english = page_script("overview.html", False)
        _, translated = page_script("overview.html", True)

        asked = set(re.findall(r'page_message\("(\w+)"', script))
        self.assertEqual(asked, set(english))
        self.assertEqual(set(english), set(translated))
        self.assertEqual({k for k in english if english[k] == translated[k]},
                         {"no"})


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestResourcesScript(unittest.TestCase):

    def test_the_texts_in_english_are_the_ones_it_had(self):
        # Scenario "A page in English is unchanged"
        shown = run_page("resources.html", RESOURCES_SCENARIO)

        self.assertEqual(shown["labels"], [
            ["User", "System", "I/O"], ["Used", "Buffers", "Cache"],
            ["Used"]])
        self.assertIn(
            '<tr><td>Stopped,0</td><td colspan="6">Not running</td>',
            shown["services"])
        self.assertIn(
            "<tr><td>Worker,0</td><td>short"
            " <a onclick='window.getters[0].kill_service(\"Worker,0\", "
            "this);'>[Kill]</a></td>", shown["services"])
        self.assertIn(
            " <input type='checkbox' checked onchange='window.getters[0]"
            ".toggle_autorestart(\"Worker,0\", this);' />", shown["services"])
        self.assertIn(
            " <input type='checkbox' onchange='window.getters[0]"
            ".toggle_autorestart(\"Evaluation,0\", this);' />",
            shown["services"])
        self.assertIn('alt="loading..." />', shown["killing"])

    def test_the_texts_in_spanish(self):
        # Scenario "A page group in Spanish" (resources)
        shown = run_page("resources.html", RESOURCES_SCENARIO, translated=True)

        self.assertEqual(shown["labels"], [
            ["Usuario", "Sistema", "I/O"], ["Usado", "Búferes", "Caché"],
            ["Usado"]])
        for text in ("No se está ejecutando", ">[Matar]</a>"):
            self.assertIn(text, shown["services"])
        self.assertIn('alt="cargando…" />', shown["killing"])

    def test_a_checkbox_without_permission_is_disabled(self):
        # Scenario "A page in English is unchanged"
        for translated in (False, True):
            shown = run_page("resources.html", RESOURCES_SCENARIO,
                             translated=translated, admin_all=False)

            self.assertEqual(shown["services"].count(" disabled />"), 2)
            self.assertNotIn("kill_service", shown["services"])

    def test_every_message_is_used_and_translated(self):
        # Scenario "A page group in Spanish"
        script, english = page_script("resources.html", False)
        _, translated = page_script("resources.html", True)

        self.assertEqual(set(re.findall(r'page_message\("(\w+)"', script)),
                         set(english))
        self.assertEqual({k for k in english if english[k] == translated[k]},
                         set())


class TestSystemPages(unittest.TestCase):

    def test_the_overview_and_resources_pages_in_spanish(self):
        # Scenario "A page group in Spanish" (overview and resources)
        overview = spanish("overview.html", **sidebar(None))
        resources = spanish("resources.html", **sidebar(None),
                            resource_addresses={"0": "localhost"})

        for text in ("<h1 id=\"overview_title\"", ">Resumen</h1>",
                     ">Estado de la cola</h2>", ">Estado de los workers</h2>",
                     "<th>Prioridad</th>", "<th>Severidad</th>",
                     'alt="cargando…"'):
            self.assertIn(text, overview)
        for text in (">Uso de recursos</h1>", ">Máquina 0 (localhost)</h2>",
                     "<th>Tiempo activo (s)</th>", "<th>Usuario (%)</th>",
                     "<th>Reinicio automático</th>", ">Memoria</td>"):
            self.assertIn(text, resources)
        self.assertIn(">Estado de los envíos</h2>", spanish(
            "overview.html", **sidebar(a_contest(), unanswered=0)))
        self.assertNotIn(">Queue status<", overview)
        self.assertNotIn(">Resource usage<", resources)

    def test_the_pages_in_english_are_unchanged(self):
        # Scenario "A page in English is unchanged"
        overview = render_page("overview.html", **sidebar(None))
        resources = render_page("resources.html", **sidebar(None),
                                resource_addresses={"0": "localhost"})

        for text in (">Overview</h1>", ">Queue status<",
                     "<th>Priority</th>", 'alt="loading..."'):
            self.assertIn(text, " ".join(overview.split()))
        for text in (">Resource usage</h1>", ">Machine 0 (localhost)</h2>",
                     "<th>Uptime (s)</th>", "<th>User (%)</th>"):
            self.assertIn(text, resources)


if __name__ == "__main__":
    unittest.main()
