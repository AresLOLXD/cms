#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <www.luisrodolfo@gmail.com>
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

"""Tests for reading and planning the bulk import CSV of AWS."""

import concurrent.futures
import dataclasses
import ipaddress
import logging
import threading
import time
import unittest
from unittest import mock

from sqlalchemy import event, inspect, select

from cms.db import Participation, SessionGen, User
from cms.server.admin.bulkimport import HASH_THREADS, MAX_ROWS, ImportRow, \
    apply_import, hash_passwords, plan_import, read_rows
from cms.server.contest.authentication import authenticate_request, \
    validate_login
from cmscommon.datetime import make_datetime
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

MAPPING = {"username": "usuario", "first_name": "nombre",
           "last_name": "apellido", "password": "contraseña",
           "team": "estado", "group": ""}


def csv_bytes(text: str, bom: bool = False) -> bytes:
    data = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


class TestReadRows(unittest.TestCase):

    def test_semicolon_and_bom(self):
        rows, errors = read_rows(csv_bytes(
            "usuario;nombre;apellido;contraseña;estado\n"
            "ana;Ana;López; s3cr3t ;JAL\n", bom=True), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row.line, row.username, row.first_name,
                          row.last_name, row.team, row.group),
                         (2, "ana", "Ana", "López", "JAL", None))
        # The password is stripped like the other cells: CWS strips what
        # the contestant types, so " s3cr3t " could never log in.
        self.assertEqual(row.password, "s3cr3t")

    def test_comma_and_quotes(self):
        rows, errors = read_rows(csv_bytes(
            'usuario,nombre,apellido,contraseña,estado\n'
            'beto,"Roberto, Jr.",Pérez,pw,\n'), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0].first_name, "Roberto, Jr.")
        self.assertIsNone(rows[0].team)

    def test_unmapped_required_field(self):
        mapping = dict(MAPPING, password="")
        _, errors = read_rows(csv_bytes("usuario,nombre,apellido\n"),
                              mapping)
        self.assertIn(
            "falta asignar la columna para la contraseña (password)", errors)

    def test_unmapped_required_fields_read_well_in_spanish(self):
        for field, text in (
                ("username", "el usuario"), ("first_name", "el nombre"),
                ("last_name", "el apellido"), ("password", "la contraseña")):
            with self.subTest(field=field):
                _, errors = read_rows(
                    csv_bytes("usuario,nombre,apellido,contraseña,estado\n"),
                    dict(MAPPING, **{field: ""}))
                self.assertEqual(
                    errors,
                    ["falta asignar la columna para %s (%s)" % (text, field)])

    def test_mapping_to_a_missing_header(self):
        mapping = dict(MAPPING, team="equipo")
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña\nana,A,L,p\n"), mapping)
        self.assertTrue(any("equipo" in e for e in errors))

    def test_empty_required_cell_and_duplicates(self):
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,,\n"
            "ana,Ana,López,pw,\n"), MAPPING)
        self.assertIn("fila 2: la contraseña está vacía", errors)
        self.assertIn("fila 3: el usuario ana está repetido (fila 2)",
                      errors)

    def test_password_too_long_does_not_echo_it(self):
        secret = "ñ" * 37
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,A,L,%s,\n" % secret), MAPPING)
        self.assertEqual(len(errors), 1)
        self.assertNotIn(secret, errors[0])
        self.assertIn("fila 2", errors[0])

    def test_limits(self):
        _, errors = read_rows(b"x" * (2 * 1024 * 1024 + 1), MAPPING)
        self.assertTrue(errors)
        body = "usuario,nombre,apellido,contraseña,estado\n" + "".join(
            "u%d,A,L,p,\n" % i for i in range(MAX_ROWS + 1))
        _, errors = read_rows(csv_bytes(body), MAPPING)
        self.assertTrue(any(str(MAX_ROWS) in e for e in errors))

    def test_not_utf8(self):
        _, errors = read_rows("usuario\nñ\n".encode("latin-1"), MAPPING)
        self.assertTrue(any("UTF-8" in e for e in errors))

    def test_field_over_the_csv_limit_is_an_error(self):
        # The csv module refuses fields over 131072 characters; that must
        # come back as an error, not as an exception.
        with self.assertLogs("cms.server.admin.bulkimport",
                             level="DEBUG") as logs:
            rows, errors = read_rows(csv_bytes(
                "usuario,nombre,apellido,contraseña,estado\n"
                "ana,A,L,%s,\n" % ("x" * 200000)), MAPPING)
        self.assertEqual(rows, [])
        # The page gets a fixed message; the reason of the csv module (a
        # developer text in English) only goes to the DEBUG log, and
        # neither of them echoes the content of a cell.
        self.assertEqual(errors, ["el archivo no es un CSV válido"])
        self.assertEqual([r.levelno for r in logs.records],
                         [logging.DEBUG])
        self.assertIn("field limit", logs.output[0])
        self.assertNotIn("x" * 100, logs.output[0])

    def test_row_errors_follow_the_field_order(self):
        # Every required cell but the team is empty. The errors must come
        # in the order of FIELDS, whatever the hash seed of the process.
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            ",,,,JAL\n"), MAPPING)
        self.assertEqual(errors, ["fila 2: el usuario está vacío",
                                  "fila 2: el nombre está vacío",
                                  "fila 2: el apellido está vacío",
                                  "fila 2: la contraseña está vacía"])

    def test_column_mapped_twice_is_an_error(self):
        # With the username and the password in the same column, two rows
        # with the same password would put it in the "repeated user"
        # error. The mapping is rejected before any row is checked.
        mapping = dict(MAPPING, username="contraseña")
        rows, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,SECRETPW,\n"
            "beto,Beto,Pérez,SECRETPW,\n"), mapping)
        self.assertEqual(rows, [])
        self.assertEqual(
            errors,
            ["la columna de la contraseña está asignada a más de un campo"])
        self.assertFalse(any("SECRETPW" in e for e in errors))

    def test_column_mapped_to_three_fields_is_reported_once(self):
        mapping = dict(MAPPING, last_name="nombre", team="nombre")
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,pw,\n"), mapping)
        self.assertEqual(
            errors, ["la columna nombre está asignada a más de un campo"])

    def test_password_header_is_never_echoed_when_mapped_twice(self):
        # Without a header row, the "header" is the first contestant's row,
        # so the header chosen for the password is a real password.
        text = "beto;Roberto;Pérez;s3cret;JAL\nana;Ana;López;pw;\n"
        for other in ("username", "team"):
            with self.subTest(other=other):
                mapping = dict(MAPPING, username="beto",
                               first_name="Roberto", last_name="Pérez",
                               password="s3cret", team="JAL")
                mapping[other] = "s3cret"
                rows, errors = read_rows(csv_bytes(text), mapping)
                self.assertEqual(rows, [])
                self.assertEqual(
                    errors, ["la columna de la contraseña está asignada a "
                             "más de un campo"])
                self.assertFalse(any("s3cret" in e for e in errors))

    def test_password_header_missing_from_the_file_is_never_echoed(self):
        text = "usuario,nombre,apellido,estado\nana,Ana,López,\n"
        mapping = dict(MAPPING, password="s3cret")
        rows, errors = read_rows(csv_bytes(text), mapping)
        self.assertEqual(rows, [])
        self.assertEqual(
            errors,
            ["la columna asignada a la contraseña no está en el archivo"])
        self.assertFalse(any("s3cret" in e for e in errors))

    def test_password_header_is_never_echoed_when_another_field_has_it(self):
        # The username is checked before the password, so it is the
        # username that reaches the missing header first.
        text = "usuario,nombre,apellido,estado\nana,Ana,López,\n"
        mapping = dict(MAPPING, username="s3cret", password="s3cret")
        rows, errors = read_rows(csv_bytes(text), mapping)
        self.assertEqual(rows, [])
        self.assertEqual(
            errors,
            ["la columna asignada a la contraseña no está en el archivo",
             "la columna de la contraseña está asignada a más de un campo"])
        self.assertFalse(any("s3cret" in e for e in errors))

    def test_other_headers_are_still_echoed(self):
        text = "usuario,nombre,apellido,contraseña,estado\nana,A,L,pw,\n"
        _, errors = read_rows(csv_bytes(text), dict(
            MAPPING, team="equipo", group="equipo"))
        self.assertEqual(
            errors, ["la columna equipo no está en el archivo",
                     "la columna equipo está asignada a más de un campo"])

    def test_usernames_the_database_refuses(self):
        # The Codename domain of the database: letters without accents,
        # digits, _ and -. \w would let the accented letters through.
        for username in ("ana.perez", "josé", "ana perez", "ana@x"):
            with self.subTest(username=username):
                _, errors = read_rows(csv_bytes(
                    "usuario,nombre,apellido,contraseña,estado\n"
                    "ana,Ana,López,pw,\n"
                    '"%s",Ana,López,pw,\n' % username), MAPPING)
                self.assertEqual(
                    errors,
                    ["fila 3: el usuario %s tiene caracteres no permitidos "
                     "(solo letras sin acentos, números, _ y -)" % username])

    def test_username_with_every_allowed_character_passes(self):
        rows, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "Ana_01-b,Ana,López,pw,\n"), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual([r.username for r in rows], ["Ana_01-b"])

    def test_username_errors_keep_the_row_order(self):
        text = ("usuario,nombre,apellido,contraseña,estado\n"
                "ana.perez,Ana,López,pw,\n"
                ",Ana,López,pw,\n"
                "Ana_01-b,Ana,López,pw,\n"
                "josé,Ana,López,,\n"
                "ana.perez,Ana,López,pw,\n")
        message = ("fila %d: el usuario %s tiene caracteres no permitidos "
                   "(solo letras sin acentos, números, _ y -)")
        expected = [
            message % (2, "ana.perez"),
            # An empty username is only empty; it is not also refused.
            "fila 3: el usuario está vacío",
            "fila 5: la contraseña está vacía",
            message % (5, "josé"),
            message % (6, "ana.perez"),
            "fila 6: el usuario ana.perez está repetido (fila 2)"]
        for _ in range(3):
            _, errors = read_rows(csv_bytes(text), MAPPING)
            self.assertEqual(errors, expected)

    def test_whitespace_only_password_is_empty(self):
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,   ,\n"), MAPPING)
        self.assertEqual(errors, ["fila 2: la contraseña está vacía"])

    def test_the_password_is_stripped_as_cws_does(self):
        # The same str.strip() as Tornado's get_argument, which CWS uses
        # for the login: a tab or a no-break space from a pasted cell is
        # removed too, and the spaces inside are kept.
        for cell in ('" abc 12 "', '"\tabc 12"', '"abc 12 "'):
            with self.subTest(cell=cell):
                rows, errors = read_rows(csv_bytes(
                    "usuario,nombre,apellido,contraseña,estado\n"
                    "ana,Ana,López,%s,\n" % cell), MAPPING)
                self.assertEqual(errors, [])
                self.assertEqual(rows[0].password, "abc 12")

    def test_a_password_with_control_characters_is_refused(self):
        # Tornado turns most of them into spaces on the login, so such a
        # password could never match; a tab inside is kept by both.
        for character in ("\x00", "\x01", "\x08", "\x0b", "\x0c", "\x0e",
                          "\x1b", "\x1f", "\x7f"):
            secret = "s3c%sr3t" % character
            with self.subTest(character=repr(character)):
                rows, errors = read_rows(csv_bytes(
                    "usuario,nombre,apellido,contraseña,estado\n"
                    "ana,Ana,López,%s,\n" % secret), MAPPING)
                self.assertEqual(
                    errors,
                    ["fila 2: la contraseña tiene caracteres no permitidos"])
                self.assertFalse(any("s3c" in e for e in errors))
        rows, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,s3c\tr3t,\n"), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0].password, "s3c\tr3t")

    def test_a_nul_byte_in_any_cell_is_refused(self):
        # The database refuses NUL in a text, so the job would fail at the
        # commit, after all the hashing, on a file that "Solo validar"
        # found valid. The cell is named, never echoed.
        header = "usuario,nombre,apellido,contraseña,estado,grupo\n"
        mapping = dict(MAPPING, group="grupo")
        good = ["ana", "Ana", "López", "pw", "JAL", "tarde"]
        for index, name in ((0, "del usuario"), (1, "del nombre"),
                            (2, "del apellido"), (4, "del equipo"),
                            (5, "del grupo")):
            cells = list(good)
            cells[index] = "Sec\x00reto"
            with self.subTest(name=name):
                _, errors = read_rows(
                    csv_bytes(header + ",".join(cells) + "\n"), mapping)
                self.assertEqual(
                    errors,
                    ["fila 2: la celda %s tiene caracteres no permitidos"
                     % name])
                self.assertFalse(any("Sec" in e for e in errors))
        # The password has a message of its own, and only that one.
        cells = list(good)
        cells[3] = "Sec\x00reto"
        _, errors = read_rows(csv_bytes(header + ",".join(cells) + "\n"),
                              mapping)
        self.assertEqual(
            errors, ["fila 2: la contraseña tiene caracteres no permitidos"])

    def test_the_72_bytes_are_counted_after_stripping(self):
        rows, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,  %s  ,\n" % ("ñ" * 36)), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0].password, "ñ" * 36)
        _, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,  %sx  ,\n" % ("ñ" * 36)), MAPPING)
        self.assertEqual(errors, ["fila 2: la contraseña pasa de 72 bytes"])

    def test_blank_and_separator_only_rows_are_skipped(self):
        rows, errors = read_rows(csv_bytes(
            "usuario;nombre;apellido;contraseña;estado\n"
            "ana;Ana;López;pw1;JAL\n"
            "\n"
            ";;;;\n"
            "  ;  ;  ;  ;  \n"
            "beto;Beto;Pérez;pw2;\n"
            ";;;;\n"), MAPPING)
        self.assertEqual(errors, [])
        # The rows after the skipped ones keep their number in the file.
        self.assertEqual([(r.line, r.username) for r in rows],
                         [(2, "ana"), (6, "beto")])

    def test_max_rows_is_accepted_and_blank_rows_do_not_count(self):
        body = "usuario,nombre,apellido,contraseña,estado\n" + "".join(
            "u%d,A,L,p,\n\n" % i for i in range(MAX_ROWS))
        rows, errors = read_rows(csv_bytes(body), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), MAX_ROWS)

    def test_crlf_line_endings(self):
        rows, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\r\n"
            "ana,Ana,López,pw,JAL\r\n"
            "beto,Beto,Pérez,pw2,\r\n"), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual([(r.line, r.username, r.password, r.team)
                          for r in rows],
                         [(2, "ana", "pw", "JAL"), (3, "beto", "pw2", None)])

    def test_bom_with_comma_delimiter(self):
        rows, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,pw,JAL\n", bom=True), MAPPING)
        self.assertEqual(errors, [])
        self.assertEqual([r.username for r in rows], ["ana"])

    def test_row_shorter_than_the_header(self):
        rows, errors = read_rows(csv_bytes(
            "usuario,nombre,apellido,contraseña,estado\n"
            "ana,Ana,López,pw\n"
            "beto,Beto,Pérez\n"), MAPPING)
        # A missing optional cell is None; a missing required one is empty.
        self.assertEqual(rows[0].team, None)
        self.assertEqual(errors, ["fila 3: la contraseña está vacía"])

    def test_header_only_file(self):
        header = "usuario,nombre,apellido,contraseña,estado\n"
        for text in (header, header + "\n,,,,\n  ,  ,  ,  ,  \n"):
            rows, errors = read_rows(csv_bytes(text), MAPPING)
            self.assertEqual(rows, [])
            self.assertEqual(errors, ["el archivo no tiene filas de datos"])

    def test_empty_file(self):
        rows, errors = read_rows(b"", MAPPING)
        self.assertEqual(rows, [])
        self.assertEqual(errors, ["el archivo está vacío"])


def import_row(line: int, username: str, team: str | None = None,
               group: str | None = None, password: str = "pw") -> ImportRow:
    return ImportRow(line=line, username=username, first_name="Nombre",
                     last_name="Apellido", password=password, team=team,
                     group=group)


class ImportFixtureMixin(DatabaseMixin):
    """A contest with a team, and users ana and beto; only ana takes part."""

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.team = self.add_team(code="JAL", name="Jalisco")
        self.ana = self.add_user(username="ana")
        self.beto = self.add_user(username="beto")
        self.ana_participation = self.add_participation(
            user=self.ana, contest=self.contest)
        self.session.flush()


class TestPlanImport(ImportFixtureMixin, unittest.TestCase):

    def test_plan(self):
        plan, errors = plan_import(self.session, self.contest.id, [
            import_row(2, "ana", team="JAL"),
            import_row(3, "beto"),
            import_row(4, "carla")])
        self.assertEqual(errors, [])
        self.assertEqual(plan.new_users, ["carla"])
        self.assertEqual(plan.updated_users, ["ana", "beto"])
        self.assertEqual(plan.new_participations, ["beto", "carla"])
        self.assertEqual(plan.updated_participations, ["ana"])
        self.assertEqual(plan.summary(), {
            "usuarios_nuevos": 1, "usuarios_actualizados": 2,
            "participaciones_nuevas": 2, "participaciones_actualizadas": 1,
            "equipos_quitados": 0, "movidas_al_grupo_principal": 0})
        self.assertEqual(plan.main_group_id, self.contest.main_group_id)
        self.assertEqual(list(plan.teams), ["JAL"])

    def test_unknown_team_and_group(self):
        users_before = self.session.query(User).count()
        participations_before = self.session.query(Participation).count()
        result = plan_import(self.session, self.contest.id, [
            import_row(2, "carla", team="XYZ", group="tarde")])
        self.assertEqual(result, (None, [
            "fila 2: el equipo XYZ no existe",
            "fila 2: el grupo tarde no existe en este concurso"]))
        self.assertEqual(self.session.query(User).count(), users_before)
        self.assertEqual(self.session.query(Participation).count(),
                         participations_before)

    def test_nothing_is_added_or_flushed(self):
        # An object the caller left pending must stay pending: a flush
        # inside plan_import would write it, and would also hide an
        # object that plan_import itself added.
        pending = self.add_user(username="pending")
        flushes = []

        def record_flush(session, flush_context, instances):
            flushes.append(instances)

        event.listen(self.session, "before_flush", record_flush)
        self.addCleanup(event.remove, self.session, "before_flush",
                        record_flush)

        plan_import(self.session, self.contest.id, [
            import_row(2, "carla"), import_row(3, "ana")])
        plan_import(self.session, self.contest.id, [
            import_row(2, "carla", team="XYZ")])

        self.assertEqual(flushes, [])
        self.assertEqual(list(self.session.new), [pending])
        self.assertTrue(inspect(pending).pending)
        self.assertFalse(self.session.dirty)

    def test_participation_in_another_contest_is_not_a_participation(self):
        other_contest = self.add_contest()
        dora = self.add_user(username="dora")
        self.add_participation(user=dora, contest=other_contest)
        self.session.flush()

        plan, errors = plan_import(self.session, self.contest.id, [
            import_row(2, "dora")])
        self.assertEqual(errors, [])
        self.assertEqual(plan.new_users, [])
        self.assertEqual(plan.updated_users, ["dora"])
        self.assertEqual(plan.new_participations, ["dora"])
        self.assertEqual(plan.updated_participations, [])

    def test_unknown_contest(self):
        result = plan_import(self.session, -1, [import_row(2, "carla")])
        self.assertEqual(result, (None, ["el concurso no existe"]))

    def test_groups_are_those_of_the_contest(self):
        afternoon = self.get_group(name="tarde", contest=self.contest)
        self.session.add(afternoon)
        other_contest = self.add_contest()
        other_group = self.get_group(name="noche", contest=other_contest)
        self.session.add(other_group)
        self.session.flush()

        plan, errors = plan_import(self.session, self.contest.id, [
            import_row(2, "carla", group="tarde")])
        self.assertEqual(errors, [])
        self.assertEqual(plan.groups[afternoon.name], afternoon.id)
        self.assertNotIn("noche", plan.groups)

        result = plan_import(self.session, self.contest.id, [
            import_row(2, "carla", group="noche")])
        self.assertEqual(result, (None, [
            "fila 2: el grupo noche no existe en este concurso"]))

    def test_contest_without_main_group(self):
        contest = self.add_contest(main_group=None)
        self.session.flush()
        self.assertIsNone(contest.main_group_id)
        result = plan_import(self.session, contest.id, [
            import_row(2, "carla")])
        self.assertEqual(result,
                         (None, ["el concurso no tiene grupo principal"]))

    def test_the_stored_passwords_of_the_contest_are_kept_out_of_sight(self):
        # ana has a password in this contest; beto has one only in another
        # contest, and dora a participation here without a password.
        self.ana_participation.password = "bcrypt:stored-ana"
        other_contest = self.add_contest()
        self.add_participation(user=self.beto, contest=other_contest,
                               password="bcrypt:stored-beto")
        dora = self.add_user(username="dora")
        self.add_participation(user=dora, contest=self.contest)
        self.session.flush()

        plan, errors = plan_import(self.session, self.contest.id, [
            import_row(2, "ana"), import_row(3, "beto"),
            import_row(4, "dora"), import_row(5, "carla")])

        self.assertEqual(errors, [])
        self.assertEqual(plan.stored_passwords, {"ana": "bcrypt:stored-ana"})
        # Neither the counts nor the representation of the plan show it.
        self.assertNotIn("stored", str(plan.summary()))
        self.assertNotIn("bcrypt:stored-ana", repr(plan))


class TestPlanImportLosses(ImportFixtureMixin, unittest.TestCase):
    """The participations that an empty team or group cell changes."""

    def setUp(self):
        super().setUp()
        self.afternoon = self.get_group(name="tarde", contest=self.contest)
        self.session.add(self.afternoon)
        self.add_team(code="CDMX", name="Ciudad de México")
        # Every combination of a current team and a current group, in this
        # contest: ana has neither (main group, no team).
        self.add_participant("carla", team=self.team)
        self.add_participant("dora", group=self.afternoon)
        self.add_participant("eva", team=self.team, group=self.afternoon)
        self.session.flush()

    def add_participant(self, username, **kwargs):
        user = self.add_user(username=username)
        return self.add_participation(user=user, contest=self.contest,
                                      **kwargs)

    def plan(self, rows):
        plan, errors = plan_import(self.session, self.contest.id, rows)
        self.assertEqual(errors, [])
        return plan

    def test_empty_cells_lose_the_team_and_the_group(self):
        plan = self.plan([import_row(2, "ana"), import_row(3, "carla"),
                          import_row(4, "dora"), import_row(5, "eva")])

        # carla and eva lose their team; dora and eva go to the main group.
        self.assertEqual(plan.removed_teams, 2)
        self.assertEqual(plan.moved_to_main_group, 2)
        summary = plan.summary()
        self.assertEqual(summary["equipos_quitados"], 2)
        self.assertEqual(summary["movidas_al_grupo_principal"], 2)

    def test_the_two_counts_are_independent(self):
        plan = self.plan([import_row(2, "carla"), import_row(3, "dora")])

        # carla only loses her team (she is in the main group already) and
        # dora is only moved (she has no team).
        self.assertEqual((plan.removed_teams, plan.moved_to_main_group),
                         (1, 1))

    def test_a_cell_with_a_value_loses_nothing(self):
        # The row gives a team and a group, even if they are not the
        # current ones: that is a replacement, not a loss.
        plan = self.plan([
            import_row(2, "carla", team="JAL", group="tarde"),
            import_row(3, "dora", team="JAL", group="tarde"),
            import_row(4, "eva", team="JAL", group="tarde")])

        self.assertEqual((plan.removed_teams, plan.moved_to_main_group),
                         (0, 0))

    def test_a_row_that_changes_the_team_clears_nothing(self):
        # carla and eva are in JAL and the row gives CDMX: the team is
        # replaced, not lost. Counting any change of team as a loss would
        # give 2 here.
        plan = self.plan([import_row(2, "carla", team="CDMX"),
                          import_row(3, "eva", team="CDMX", group="tarde")])

        self.assertEqual(plan.removed_teams, 0)

    def test_a_row_that_gives_the_main_group_moves_nothing_to_it(self):
        # dora and eva are in "tarde" and the row names the main group: an
        # explicit choice, not the empty cell that this count warns about.
        # Counting any change of group would give 2 here.
        main_group = self.contest.main_group.name
        plan = self.plan([import_row(2, "dora", group=main_group),
                          import_row(3, "eva", team="JAL", group=main_group)])

        self.assertEqual(plan.moved_to_main_group, 0)

    def test_only_the_missing_cell_counts(self):
        plan = self.plan([import_row(2, "eva", team="JAL")])

        self.assertEqual((plan.removed_teams, plan.moved_to_main_group),
                         (0, 1))

        plan = self.plan([import_row(2, "eva", group="tarde")])

        self.assertEqual((plan.removed_teams, plan.moved_to_main_group),
                         (1, 0))

    def test_nothing_is_lost_by_a_participation_that_does_not_exist(self):
        # beto is a user without participation, and frida is a new user:
        # their empty cells have nothing to clear.
        plan = self.plan([import_row(2, "beto"), import_row(3, "frida")])

        self.assertEqual((plan.removed_teams, plan.moved_to_main_group),
                         (0, 0))

    def test_participations_of_other_contests_are_not_counted(self):
        other_contest = self.add_contest()
        gina = self.add_user(username="gina")
        other_group = self.get_group(name="noche", contest=other_contest)
        self.session.add(other_group)
        self.add_participation(user=gina, contest=other_contest,
                               group=other_group, team=self.team)
        self.session.flush()

        plan = self.plan([import_row(2, "gina")])

        self.assertEqual(plan.new_participations, ["gina"])
        self.assertEqual((plan.removed_teams, plan.moved_to_main_group),
                         (0, 0))

    def test_the_counts_are_zero_when_nothing_is_lost(self):
        plan = self.plan([import_row(2, "ana")])

        self.assertEqual(plan.summary()["equipos_quitados"], 0)
        self.assertEqual(plan.summary()["movidas_al_grupo_principal"], 0)


class TestHashPasswords(unittest.TestCase):

    def setUp(self):
        for name, replacement in (
                ("hash_password", lambda p, method="bcrypt": "fake:" + p),
                ("generate_random_password", lambda: "RANDOM")):
            patcher = mock.patch("cms.server.admin.bulkimport." + name,
                                 replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.rows = [import_row(2, "ana", password="pw-ana"),
                     import_row(3, "beto", password="pw-beto"),
                     import_row(4, "carla", password="pw-carla")]

    def test_hashes_and_progress(self):
        calls = []
        result = hash_passwords(self.rows, {"carla"}, {},
                                lambda: calls.append(None))
        self.assertEqual(result, {
            "ana": ("fake:pw-ana", None),
            "beto": ("fake:pw-beto", None),
            "carla": ("fake:pw-carla", "fake:RANDOM")})
        self.assertEqual(len(calls), 3)

    def test_a_stored_password_that_still_matches_is_kept(self):
        # A new hash of the same password would be another string, and the
        # CWS cookie holds the stored string: keeping it keeps the session.
        checked = []

        def fake_validate(stored, password):
            checked.append((stored, threading.current_thread().name))
            return stored.split(":", 1)[1] == password

        stored = {
            # Still the password of the row: kept.
            "ana": "bcrypt:pw-ana",
            # The row brings another password: hashed.
            "beto": "bcrypt:old-pw-beto",
            # Not a bcrypt hash, even if it matches: hashed.
            "carla": "plaintext:pw-carla"}
        rows = self.rows + [import_row(5, "dora", password="pw-dora")]
        calls = []
        with mock.patch("cms.server.admin.bulkimport.validate_password",
                        fake_validate):
            result = hash_passwords(rows, {"dora"}, stored,
                                    lambda: calls.append(None))

        self.assertEqual(result, {
            "ana": ("bcrypt:pw-ana", None),
            "beto": ("fake:pw-beto", None),
            "carla": ("fake:pw-carla", None),
            "dora": ("fake:pw-dora", "fake:RANDOM")})
        # One progress per row, whether it was checked or hashed.
        self.assertEqual(len(calls), len(rows))
        # The check costs as much as a hash, so it runs in the pool too.
        self.assertEqual(sorted(s for s, _ in checked),
                         ["bcrypt:old-pw-beto", "bcrypt:pw-ana"])
        for _, thread_name in checked:
            self.assertTrue(thread_name.startswith("aws-import-hash"),
                            msg=thread_name)

    def test_pool_size(self):
        created = []

        class RecordingExecutor(concurrent.futures.ThreadPoolExecutor):
            def __init__(self, max_workers=None, *args, **kwargs):
                created.append(max_workers)
                super().__init__(max_workers, *args, **kwargs)

        with mock.patch("concurrent.futures.ThreadPoolExecutor",
                        RecordingExecutor):
            hash_passwords(self.rows, set(), {}, lambda: None)
        self.assertEqual(created, [HASH_THREADS])

    def test_error_stops_the_queued_rows(self):
        rows = [import_row(line, "user%d" % line) for line in range(2, 12)]
        hashed = []

        def failing_hash(password, method="bcrypt"):
            hashed.append(password)
            if len(hashed) == 1:
                raise RuntimeError("first row")
            # Slow enough for the pool to be cancelled while this row is
            # the only one being hashed.
            time.sleep(0.1)
            return "fake:" + password

        with mock.patch("cms.server.admin.bulkimport.HASH_THREADS", 1), \
                mock.patch("cms.server.admin.bulkimport.hash_password",
                           failing_hash):
            with self.assertRaises(RuntimeError):
                hash_passwords(rows, set(), {}, lambda: None)
        # The failed row and, at most, the one the worker had already
        # taken; the other eight are never hashed.
        self.assertLessEqual(len(hashed), 2)

    def test_error_in_progress_stops_the_queued_rows(self):
        rows = [import_row(line, "user%d" % line) for line in range(2, 12)]
        hashed = []

        def slow_hash(password, method="bcrypt"):
            hashed.append(password)
            time.sleep(0.1)
            return "fake:" + password

        def failing_progress():
            raise RuntimeError("progress")

        with mock.patch("cms.server.admin.bulkimport.HASH_THREADS", 1), \
                mock.patch("cms.server.admin.bulkimport.hash_password",
                           slow_hash):
            with self.assertRaises(RuntimeError):
                hash_passwords(rows, set(), {}, failing_progress)
        self.assertLessEqual(len(hashed), 2)


class TestApplyImport(ImportFixtureMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        # Some of the tests commit, so the data is deleted at the end.
        self.addCleanup(self.delete_data)
        self.afternoon = self.get_group(name="tarde", contest=self.contest)
        self.session.add(self.afternoon)
        self.ana.password = "account:ana"
        self.beto.password = "account:beto"
        self.ana_participation.hidden = True
        self.session.flush()
        self.rows = [
            ImportRow(line=2, username="ana", first_name="Ana",
                      last_name="López", password="pw-ana", team="JAL",
                      group="tarde"),
            ImportRow(line=3, username="beto", first_name="Roberto",
                      last_name="Pérez", password="pw-beto", team=None,
                      group=None),
            ImportRow(line=4, username="carla", first_name="Carla",
                      last_name="Ruiz", password="pw", team=None,
                      group=None),
            ImportRow(line=5, username="dora", first_name="Dora",
                      last_name="Sanz", password="pw-dora", team=None,
                      group=None)]
        self.hashes = {
            "ana": ("fake:pw-ana", None),
            "beto": ("fake:pw-beto", None),
            "carla": ("fake:pw", "fake:RANDOM"),
            "dora": ("fake:pw-dora", "fake:RANDOM-dora")}

    def plan(self, session, rows=None):
        plan, errors = plan_import(session, self.contest.id,
                                   self.rows if rows is None else rows)
        self.assertEqual(errors, [])
        return plan

    def snapshot(self):
        """Read what is committed, in a session of its own."""
        with SessionGen() as session:
            users = session.execute(
                select(User.username, User.first_name, User.last_name,
                       User.password).order_by(User.username)).all()
            participations = session.execute(
                select(User.username, Participation.password,
                       Participation.team_id, Participation.group_id,
                       Participation.hidden)
                .join(Participation.user)
                .order_by(User.username)).all()
        return users, participations

    def test_apply(self):
        apply_import(self.session, self.contest.id, self.rows,
                     self.plan(self.session), self.hashes)
        self.session.commit()

        users = {u.username: u for u in self.session.query(User)}
        participations = {p.user.username: p for p in
                          self.session.query(Participation)}
        self.assertEqual(sorted(users), ["ana", "beto", "carla", "dora"])
        self.assertEqual(sorted(participations),
                         ["ana", "beto", "carla", "dora"])

        carla = users["carla"]
        self.assertEqual((carla.first_name, carla.last_name, carla.password),
                         ("Carla", "Ruiz", "fake:RANDOM"))
        participation = participations["carla"]
        self.assertEqual(participation.contest_id, self.contest.id)
        self.assertEqual(participation.password, "fake:pw")
        self.assertEqual(participation.group_id, self.contest.main_group_id)
        self.assertIsNone(participation.team_id)

        ana = users["ana"]
        self.assertEqual((ana.first_name, ana.last_name), ("Ana", "López"))
        participation = participations["ana"]
        self.assertIs(participation, self.ana_participation)
        self.assertEqual(participation.password, "fake:pw-ana")
        self.assertEqual(participation.team_id, self.team.id)
        self.assertEqual(participation.group_id, self.afternoon.id)
        self.assertTrue(participation.hidden)

        beto = users["beto"]
        self.assertEqual((beto.first_name, beto.last_name),
                         ("Roberto", "Pérez"))
        participation = participations["beto"]
        self.assertEqual(participation.contest_id, self.contest.id)
        self.assertEqual(participation.password, "fake:pw-beto")
        self.assertEqual(participation.group_id, self.contest.main_group_id)

        # The account password of an existing user is left alone.
        self.assertEqual(ana.password, "account:ana")
        self.assertEqual(beto.password, "account:beto")

    def test_row_replaces_team_and_group(self):
        # A row without team or group does not keep what the participation
        # had: it leaves it with no team and in the main group.
        self.ana_participation.team_id = self.team.id
        self.ana_participation.group_id = self.afternoon.id
        self.session.flush()
        rows = [import_row(2, "ana")]

        apply_import(self.session, self.contest.id, rows,
                     self.plan(self.session, rows),
                     {"ana": ("fake:pw", None)})
        self.session.commit()

        self.assertIsNone(self.ana_participation.team_id)
        self.assertEqual(self.ana_participation.group_id,
                         self.contest.main_group_id)
        self.assertNotEqual(self.contest.main_group_id, self.afternoon.id)

    def test_participations_of_another_contest_are_left_alone(self):
        # ana takes part in both contests and beto only in the other one.
        other_contest = self.add_contest()
        other_group = self.get_group(name="noche", contest=other_contest)
        self.session.add(other_group)
        self.session.flush()
        others = {user.username: self.add_participation(
            user=user, contest=other_contest, group=other_group,
            team=self.team, password="other:" + user.username, hidden=True)
            for user in (self.ana, self.beto)}
        self.session.flush()

        def state(participation):
            return (participation.contest_id, participation.password,
                    participation.team_id, participation.group_id,
                    participation.hidden)

        before = {name: state(p) for name, p in others.items()}

        apply_import(self.session, self.contest.id, self.rows,
                     self.plan(self.session), self.hashes)
        self.session.commit()

        self.assertEqual({name: state(p) for name, p in others.items()},
                         before)
        in_contest = {p.user.username: p.password for p in
                      self.session.query(Participation).filter(
                          Participation.contest_id == self.contest.id)}
        self.assertEqual(in_contest, {
            "ana": "fake:pw-ana", "beto": "fake:pw-beto",
            "carla": "fake:pw", "dora": "fake:pw-dora"})

    def test_nothing_is_committed(self):
        self.session.commit()
        before = self.snapshot()

        with SessionGen() as session:
            apply_import(session, self.contest.id, self.rows,
                         self.plan(session), self.hashes)
            # What the job does when its caller does not commit: the
            # block ends and rolls back.

        self.assertEqual(self.snapshot(), before)

    def test_failure_leaves_nothing(self):
        self.session.commit()
        before = self.snapshot()
        original_init = Participation.__init__
        created = []
        written = []

        def failing_init(participation, *args, **kwargs):
            created.append(participation)
            if len(created) == 2:
                # Send what has been added so far to the open transaction,
                # so the rollback has something to undo.
                session.flush()
                written.append((session.query(User).count(),
                                session.query(Participation).count()))
                raise RuntimeError("second new participation")
            original_init(participation, *args, **kwargs)

        # beto is the first new participation and carla the second, so
        # ana's update, beto's participation and carla's user have been
        # written to the transaction when this fails.
        with self.assertRaises(RuntimeError):
            with SessionGen() as session:
                plan = self.plan(session)
                with mock.patch.object(Participation, "__init__",
                                       failing_init):
                    apply_import(session, self.contest.id, self.rows, plan,
                                 self.hashes)

        self.assertEqual(len(created), 2)
        # carla's user and beto's participation did reach the transaction.
        self.assertEqual(written, [(len(before[0]) + 1,
                                    len(before[1]) + 1)])
        self.assertEqual(self.snapshot(), before)


class TestReimportAndTheContestCookie(ImportFixtureMixin, unittest.TestCase):
    """A re-import against the cookie check of CWS, with real bcrypt.

    The CWS cookie holds the stored password string of the participation,
    and bcrypt salts every hash, so rewriting an unchanged password with a
    new hash would log the contestant out.

    """

    def setUp(self):
        super().setUp()
        self.addCleanup(self.delete_data)
        patcher = mock.patch("cmscommon.crypto.BCRYPT_ROUNDS", 4)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The imports run in sessions of their own, as the job does.
        self.session.commit()
        self.timestamp = make_datetime()
        self.ip_address = ipaddress.ip_address("10.0.0.1")
        self.rows = [import_row(2, "ana", password="dia1-ana"),
                     import_row(3, "carla", password="dia1-carla")]

    def run_import(self, rows):
        """Import the rows and commit, the way the job does."""
        with SessionGen() as session:
            plan, errors = plan_import(session, self.contest.id, rows)
            self.assertEqual(errors, [])
            hashes = hash_passwords(rows, set(plan.new_users),
                                    plan.stored_passwords, lambda: None)
            apply_import(session, self.contest.id, rows, plan, hashes)
            session.commit()

    def stored_password(self, username):
        with SessionGen() as session:
            return session.execute(
                select(Participation.password).join(Participation.user)
                .filter(Participation.contest_id == self.contest.id,
                        User.username == username)).scalar_one()

    def log_in(self, username, password):
        """Log in to CWS and return the cookie it sets."""
        self.session.expire_all()
        participation, cookie = validate_login(
            self.session, self.contest, self.timestamp, username, password,
            self.ip_address)
        self.assertIsNotNone(participation)
        self.assertIsNotNone(cookie)
        return cookie

    def cookie_authenticates(self, cookie):
        self.session.expire_all()
        participation, _, _ = authenticate_request(
            self.session, self.contest, self.timestamp, cookie, None,
            self.ip_address)
        return participation is not None

    def test_an_unchanged_row_keeps_its_contestant_logged_in(self):
        self.run_import(self.rows)
        cookies = {row.username: self.log_in(row.username, row.password)
                   for row in self.rows}
        stored_before = self.stored_password("carla")

        # The same file again, only to fix the name of another contestant.
        self.rows[0] = dataclasses.replace(self.rows[0],
                                           first_name="Ana María")
        self.run_import(self.rows)

        self.assertEqual(self.stored_password("carla"), stored_before)
        self.assertTrue(self.cookie_authenticates(cookies["carla"]))
        self.assertTrue(self.cookie_authenticates(cookies["ana"]))

    def test_a_changed_password_logs_out_only_that_contestant(self):
        self.run_import(self.rows)
        cookies = {row.username: self.log_in(row.username, row.password)
                   for row in self.rows}

        self.rows[1] = dataclasses.replace(self.rows[1],
                                           password="dia1-carla-nueva")
        self.run_import(self.rows)

        self.assertFalse(self.cookie_authenticates(cookies["carla"]))
        self.assertTrue(self.cookie_authenticates(cookies["ana"]))
        # The new password is the one that logs in now.
        self.log_in("carla", "dia1-carla-nueva")


if __name__ == "__main__":
    unittest.main()
