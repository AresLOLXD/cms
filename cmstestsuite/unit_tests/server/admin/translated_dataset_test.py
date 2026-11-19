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

"""Tests for the Spanish dataset pages of AWS, the notifications of
their handlers and the messages of the zip import.

The pages are rendered from the real templates with the stand-ins of
aws_render and the Spanish of aws.po; nothing here says how a page
looks.

"""

import io
import re
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cms.db import Dataset, Task
from cms.server.admin.handlers.dataset import AddManagerHandler, \
    AddTestcasesHandler, ActivateDatasetHandler, CloneDatasetHandler, \
    DownloadTestcasesHandler, RenameDatasetHandler
from cmscommon.importers import import_testcases_from_zipfile
from cmstestsuite.unit_tests.server.admin import aws_render
from cmstestsuite.unit_tests.server.admin.aws_catalog import SPANISH


class English:
    """The identity translation, as AWS uses it for English."""
    gettext = staticmethod(lambda message: message)

    @staticmethod
    def ngettext(singular, plural, n):
        return singular if n == 1 else plural


def page(name, translation, mode="full") -> str:
    return aws_render.render(aws_render.environment(mode), name, translation)


def make_handler(cls, translation, **form):
    """Return a handler that reads the form and notifies, with no DB."""
    handler = cls.__new__(cls)
    handler._ = translation.gettext
    handler.n_ = translation.ngettext
    handler.application = MagicMock()
    handler.get_argument = lambda name, default=None: form.get(name, default)
    handler.redirect = MagicMock()
    handler.url = lambda *parts: "/" + "/".join(str(p) for p in parts)
    handler.sql_session = MagicMock()
    handler.try_commit = MagicMock(return_value=True)
    handler.schedule_rpc = MagicMock()
    return handler


def notifications(handler):
    return [call.args[1:]
            for call in handler.service.add_notification.call_args_list]


def serve(handler, dataset, task=None):
    """Make the handler find the dataset, and its task."""
    task = task or SimpleNamespace(id=3, name="sum", datasets=[dataset])
    dataset.task = task
    handler.safe_get_item = lambda cls, _id: \
        {Dataset: dataset, Task: task}.get(cls, SimpleNamespace(id=_id))


class TestDatasetPages(unittest.TestCase):

    def test_the_dataset_page_in_spanish(self):
        # Scenario "A page group in Spanish" (dataset pages)
        full = page("dataset.html", SPANISH)
        empty = page("dataset.html", SPANISH, "empty")

        for text in ("Dataset: ", "Dataset actual: «<strong>",
                     "Haz clic en uno de los elementos siguientes para ver "
                     "los resultados con otro dataset.",
                     "Reevaluar todos los envíos de este dataset (",
                     ">Envíos</h2>"):
            self.assertIn(text, full)
        self.assertIn("No hay datasets para este problema.", empty)
        for text in ("Current dataset", "Click on one", "No datasets exist",
                     "Reevaluate all"):
            self.assertNotIn(text, full + empty)

    def test_the_new_dataset_page_in_spanish(self):
        # Scenario "A page group in Spanish" (dataset pages): the limits
        # and the type blocks that task.html shares.
        html = page("add_dataset.html", SPANISH)

        for text in ("- Crear dataset", "Estás clonando el dataset «",
                     "Información del dataset", "Clonar resultados de "
                     "evaluación", "Clonar managers", "Límite de tiempo",
                     "Límite de memoria", "segundo(s)",
                     "Configuración del tipo de problema (<a href=\"http://",
                     "Tipo de puntaje", "Parámetros del puntaje",
                     'value="Crear"'):
            self.assertIn(text, html)
        for text in ("Create dataset", "You are cloning", "Time limit",
                     "Score Parameters", 'value="Create"'):
            self.assertNotIn(text, html)

    def test_the_activate_page_in_spanish(self):
        # Scenario "A page group in Spanish" (dataset pages)
        html = page("activate_dataset.html", SPANISH)

        for text in ("Vista previa del cambio de dataset para",
                     "Dataset actual:", "[Ver resultados]",
                     "<b>NOTA</b>: La vista previa de esta página",
                     "Cambiar a este dataset provocará los siguientes "
                     "cambios:", "Puntaje público:", "(con token)",
                     "[Todos los usuarios]", "Asunto:", "Texto:",
                     'value="Activar"', 'value="Cancelar"'):
            self.assertIn(text, html)
        self.assertNotIn("Preview dataset", html)

    def test_the_default_message_to_contestants_in_spanish(self):
        # Scenario "A page group in Spanish": the message that the admin
        # sends is written in the language of the admin. The textarea
        # keeps its raw quotes.
        html = page("activate_dataset.html", SPANISH)

        self.assertRegex(html, r'name="message_subject"[^>]*value="Se '
                         r'reevaluó el problema [^"]*"')
        self.assertRegex(html, r'<textarea name="message_text"[^>]*>Se '
                         r'reevaluó el problema «[^»]*» y se actualizó el '
                         r'puntaje de tus envíos\.</textarea>')

    def test_the_rename_and_delete_pages_in_spanish(self):
        # Scenario "A page group in Spanish" (dataset pages)
        rename = page("rename_dataset.html", SPANISH)
        delete = page("delete_dataset.html", SPANISH)

        for text in ("- Renombrar dataset", "Edita la descripción (cada "
                     "dataset debe tener una descripción única):",
                     'value="Renombrar"'):
            self.assertIn(text, rename)
        for text in ("- Borrar dataset", "¿Seguro que quieres borrar el "
                     "dataset «", "» y todos sus managers, casos de prueba "
                     "y resultados de envíos asociados?",
                     'value="Borrar"', 'value="Cancelar"'):
            self.assertIn(text, delete)
        self.assertNotIn("Are you sure", rename + delete)

    def test_the_testcase_pages_in_spanish(self):
        # Scenario "A page group in Spanish" (testcase pages)
        one = page("add_testcase.html", SPANISH)
        many = page("add_testcases.html", SPANISH)
        download = page("download_testcases.html", SPANISH)
        manager = page("add_manager.html", SPANISH)

        for text in ("- Subir caso de prueba", "Nombre clave:", "Entrada:",
                     "Salida:", ">Público</td>", 'value="Subir"'):
            self.assertIn(text, one)
        for text in ("- Subir casos de prueba", "Archivo comprimido (un "
                     ".zip que solo contiene archivos):", "Sobrescribir los casos de "
                     "prueba existentes", "Plantilla de nombres de los "
                     "archivos de entrada (usa * como nombre clave"):
            self.assertIn(text, many)
        for text in ("- Descargar todos los casos de prueba",
                     "Nombre del archivo zip", "Plantilla de nombres de "
                     "los archivos de salida", 'value="Descargar"'):
            self.assertIn(text, download)
        for text in ("- Subir manager", 'value="Subir"'):
            self.assertIn(text, manager)
        self.assertNotIn("Upload", one + many + manager)

    def test_the_posted_defaults_of_the_forms_stay(self):
        # Scenario "No translated posted values": the templates of the
        # file names are data of the form, not messages.
        html = page("add_testcases.html", SPANISH)

        self.assertIn('name="input_template" value="input.*"', html)
        self.assertIn('name="output_template" value="output.*"', html)


class TestDatasetNotifications(unittest.TestCase):

    def test_a_taken_dataset_name_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English":
        # renaming a dataset to the description of another one.
        for translation, subject, text in (
                (SPANISH, "El nombre de dataset «dos» ya está en uso.",
                 "Elige un nombre único para este dataset."),
                (English, 'Dataset name "dos" is already taken.',
                 "Please choose a unique name for this dataset.")):
            handler = make_handler(RenameDatasetHandler, translation,
                                   description="dos")
            dataset = SimpleNamespace(id=1, description="uno")
            other = SimpleNamespace(id=2, description="dos")
            serve(handler, dataset,
                  SimpleNamespace(id=3, datasets=[dataset, other]))

            handler._post_sync("1")

            self.assertEqual(notifications(handler), [(subject, text)])

    def test_a_taken_name_when_cloning_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        for translation, subject in (
                (SPANISH, "El nombre de dataset 'uno' ya está en uso."),
                (English, "Dataset name 'uno' is already taken.")):
            handler = make_handler(CloneDatasetHandler, translation)
            handler.get_string = lambda attrs, name: \
                attrs.__setitem__(name, "uno")
            serve(handler, SimpleNamespace(id=1, task_id=3,
                                           description="uno"))

            handler._post_sync("1")

            self.assertEqual(notifications(handler)[0][0], subject)

    def test_an_invalid_field_when_cloning_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English": the
        # detail of the error stays as it is.
        for translation, subject in ((SPANISH, "Campos no válidos"),
                                     (English, "Invalid field(s)")):
            handler = make_handler(CloneDatasetHandler, translation)
            handler.get_string = MagicMock(side_effect=ValueError("x"))
            serve(handler, SimpleNamespace(id=1, task_id=3,
                                           description="uno"))

            handler._post_sync("1")

            self.assertEqual(notifications(handler),
                             [(subject, "ValueError('x')")])

    def test_the_count_of_messages_sent_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        # (the notification of a count): one user and two.
        for translation, expected in (
                (SPANISH, ["Mensaje enviado a 1 usuario.",
                           "Mensajes enviados a 2 usuarios."]),
                (English, ["Messages sent to 1 users.",
                           "Messages sent to 2 users."])):
            for count, text in zip((1, 2), expected):
                handler = make_handler(ActivateDatasetHandler, translation)
                handler.request = SimpleNamespace(arguments={
                    "notify_%d" % i: [b"on"] for i in range(count)})
                dataset = SimpleNamespace(id=1)
                serve(handler, dataset, SimpleNamespace(
                    id=3, active_dataset=None))

                with patch("cms.server.admin.handlers.dataset.Message"):
                    handler._post_sync("1")

                self.assertEqual(notifications(handler), [(text, "")])

    def test_a_manager_without_file_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        for translation, subject, text in (
                (SPANISH, "Datos no válidos",
                 "Elige un archivo de manager."),
                (English, "Invalid data", "Please choose a manager file.")):
            handler = make_handler(AddManagerHandler, translation)
            handler.request = SimpleNamespace(files={})
            serve(handler, SimpleNamespace(id=1))

            handler._post_sync("1")

            self.assertEqual(notifications(handler), [(subject, text)])

    def test_a_download_template_without_star_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        for translation, subject, text in (
                (SPANISH, "Formato de plantilla no válido",
                 "Debes usar exactamente un '*' en la plantilla de "
                 "entrada y en la de salida."),
                (English, "Invalid template format",
                 "You must have exactly one '*' in input/output "
                 "template.")):
            handler = make_handler(DownloadTestcasesHandler, translation,
                                   input_template="input")
            serve(handler, SimpleNamespace(id=1))

            handler._post_sync("1")

            self.assertEqual(notifications(handler), [(subject, text)])


def zip_file(names=()) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "x")
    buffer.seek(0)
    return buffer


def import_zip(archive, translation=None, testcases=None):
    dataset = SimpleNamespace(task=SimpleNamespace(name="sum"),
                              testcases=testcases or {})
    args = (MagicMock(), MagicMock(), dataset, archive,
            re.compile(r"in\.(.*)$"), re.compile(r"out\.(.*)$"), False, True)
    if translation is None:
        return import_testcases_from_zipfile(*args)
    return import_testcases_from_zipfile(*args, translation.gettext)


class TestZipImportMessages(unittest.TestCase):

    def test_the_outcome_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English": the
        # subject and the text that the handler notifies. Without a
        # translation (cmsAddTestcases) the English is what it was.
        archive = ["in.a", "out.a", "in.b", "out.b"]
        skipped = {"a": object(), "b": object()}
        for translation, expected in (
                (SPANISH, ("Se agregaron 0 y se sobrescribieron 0 caso(s) de "
                           "prueba", "Agregados: ninguno; sobrescritos: "
                           "ninguno; omitidos: a, b")),
                (None, ("Successfully added 0 and overwritten 0 testcase(s)",
                        "Added: none; overwritten: none; skipped: a, b"))):
            self.assertEqual(
                import_zip(zip_file(archive), translation, skipped), expected)

    def test_an_empty_archive_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        self.assertEqual(import_zip(zip_file(), SPANISH), (
            "Se agregaron 0 y se sobrescribieron 0 caso(s) de prueba",
            "Agregados: ninguno; sobrescritos: ninguno; omitidos: ninguno"))
        self.assertEqual(import_zip(zip_file()), (
            "Successfully added 0 and overwritten 0 testcase(s)",
            "Added: none; overwritten: none; skipped: none"))

    def test_a_file_that_is_not_a_zip_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        with self.assertRaises(Exception) as spanish:
            import_zip(io.BytesIO(b"no zip"), SPANISH)
        with self.assertRaises(Exception) as english:
            import_zip(io.BytesIO(b"no zip"))

        self.assertEqual(
            str(spanish.exception), "El archivo seleccionado no es un .zip. "
            "Selecciona un archivo .zip válido.")
        self.assertEqual(
            str(english.exception), "The selected file is not a zip file. "
            "Please select a valid zip file.")

    def test_a_storage_failure_in_both_languages(self):
        # Scenarios "Invalid field in Spanish" and "... in English"
        for translation, message in (
                (SPANISH, "No se pudo guardar el caso de prueba"),
                (None, "Testcase storage failed")):
            dataset = SimpleNamespace(task=SimpleNamespace(name="sum"),
                                      testcases={})
            file_cacher = MagicMock()
            file_cacher.put_file_content.side_effect = OSError("disk")
            args = (MagicMock(), file_cacher, dataset,
                    zip_file(["in.a", "out.a"]), re.compile(r"in\.(.*)$"),
                    re.compile(r"out\.(.*)$"), False, True)
            if translation is not None:
                args += (translation.gettext,)

            with self.assertRaises(Exception) as raised:
                import_testcases_from_zipfile(*args)

            self.assertEqual(str(raised.exception), message)

    def test_the_handler_notifies_the_import_in_the_language(self):
        # Scenario "Invalid field in Spanish": the handler gives its own
        # translation to the helper, so a bad file is reported in
        # Spanish, with the repr of the error as its text.
        handler = make_handler(AddTestcasesHandler, SPANISH)
        handler.request = SimpleNamespace(
            files={"archive": [{"body": b"no zip"}]})
        serve(handler, SimpleNamespace(id=1, testcases={}))

        handler._post_sync("1")

        subject, text = notifications(handler)[0]
        self.assertEqual(subject, "El archivo seleccionado no es un .zip. "
                         "Selecciona un archivo .zip válido.")
        self.assertTrue(text.startswith("Exception('El archivo"))


if __name__ == "__main__":
    unittest.main()
