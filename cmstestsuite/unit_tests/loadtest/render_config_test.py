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

"""Tests for the load-test config renderer."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import render_config  # noqa: E402


class RenderTest(unittest.TestCase):

    def test_replaces_every_marker(self):
        self.assertEqual(
            render_config.render("a=@A@ b=@B@", {"A": "1", "B": "2"}),
            "a=1 b=2")

    def test_missing_value_names_the_marker(self):
        with self.assertRaises(KeyError) as raised:
            render_config.render("x=@MISSING@", {})
        self.assertIn("MISSING", str(raised.exception))

    def test_worker_lines(self):
        self.assertEqual(render_config.worker_lines(2),
                         '    ["localhost", 26000],\n'
                         '    ["localhost", 26001],')

    def test_full_template_renders_and_parses(self):
        import tomllib
        here = os.path.join(os.path.dirname(__file__), "..", "..",
                            "loadtest", "config")
        values = render_config.values(
            db_url="postgresql+psycopg2://cms:pw@db:5432/cmsdb",
            secret_key="0" * 32, rws_password="pw", workers=3, cws=2,
            two_phase=False)
        text = render_config.render(
            open(os.path.join(here, "cms.toml.tmpl")).read(), values)
        conf = tomllib.loads(text)
        self.assertEqual(len(conf["services"]["Worker"]), 3)
        self.assertEqual(conf["contest_web_server"]["listen_port"],
                         [8888, 8889])
        self.assertEqual(len(conf["services"]["ContestWebServer"]), 2)
        self.assertFalse(conf["global"]["two_phase_evaluation"])
        tomllib.loads(render_config.render(
            open(os.path.join(here, "cms_ranking.toml.tmpl")).read(),
            values))


    def test_request_time_header_rendered_only_when_given(self):
        import tomllib
        here = os.path.join(os.path.dirname(__file__), "..", "..",
                            "loadtest", "config")
        template = open(os.path.join(here, "cms.toml.tmpl")).read()
        base = dict(db_url="postgresql+psycopg2://cms:pw@db:5432/cmsdb",
                    secret_key="0" * 32, rws_password="pw", workers=2,
                    cws=2, two_phase=False)
        off = tomllib.loads(render_config.render(
            template, render_config.values(**base)))
        self.assertNotIn("request_time_header", off["contest_web_server"])
        on = tomllib.loads(render_config.render(
            template, render_config.values(
                **base, request_time_header="X-Request-Start")))
        self.assertEqual(on["contest_web_server"]["request_time_header"],
                         "X-Request-Start")

    def test_request_time_header_name_is_validated(self):
        for bad in ('X"Evil', "X Y", "X\nY", "a=b"):
            with self.assertRaises(ValueError):
                render_config.values(
                    db_url="u", secret_key="k", rws_password="p", workers=1,
                    cws=1, two_phase=False, request_time_header=bad)


if __name__ == "__main__":
    unittest.main()
