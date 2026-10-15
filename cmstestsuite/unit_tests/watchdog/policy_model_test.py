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

"""Tests for the data model and the ledger of cmsWatchdog's policy."""

import unittest

from cms.watchdog.policy import ENFORCE, OBSERVE, RESTART, START, \
    SERVICES_POOL, WORKERS_POOL, Booking, Ledger, LedgerError, \
    PolicyState, ProgramFacts, age, hhmm_utc, is_number, p99, pool_of


WALL = 1_800_000_000.0


def booking(program: str, wall: float, pool: str = SERVICES_POOL,
            action: str = RESTART, would: bool = False,
            label: str = "") -> Booking:
    return Booking(wall, program, label or program, pool, action, "R1",
                   would, 4242 if action == RESTART else None)


class TestHelpers(unittest.TestCase):

    def test_program_facts(self):
        cws = ProgramFacts("cmscontestwebserver2", "ContestWebServer", 2,
                           "RUNNING", 10, WALL)
        self.assertTrue(cws.managed)
        self.assertEqual(cws.klass, "web")
        self.assertEqual(cws.label, "cmsContestWebServer 2")
        for kind, shard in (("TelegramBot", 0), (None, None),
                            ("Worker", None), ("Watchdog", 0)):
            self.assertFalse(
                ProgramFacts("x", kind, shard, "RUNNING", 1, WALL).managed)

    def test_pools(self):
        self.assertEqual(pool_of("workers"), WORKERS_POOL)
        for klass in ("web", "core", "infra"):
            self.assertEqual(pool_of(klass), SERVICES_POOL)

    def test_a_negative_age_is_zero(self):
        self.assertEqual(age(100.0, 160.0), 0.0)
        self.assertEqual(age(160.0, 100.0), 60.0)

    def test_hhmm_utc(self):
        self.assertEqual(hhmm_utc(WALL), "08:00 UTC")
        self.assertEqual(hhmm_utc(WALL + 3000), "08:50 UTC")

    def test_hhmm_utc_is_total(self):
        # An alert line can always be formatted, whatever the clock or a
        # file gave.
        for wall in (float("nan"), float("inf"), -float("inf"), 1e300,
                     1e18, 10 ** 400):
            with self.subTest(wall=wall):
                self.assertEqual(hhmm_utc(wall), "--:--")

    def test_p99(self):
        self.assertIsNone(p99([]))
        self.assertEqual(p99([0.5]), 0.5)
        self.assertEqual(p99([float(i) for i in range(1, 101)]), 99.0)

    def test_is_number(self):
        for value in (0, 1.5, -3):
            self.assertTrue(is_number(value))
        for value in (True, None, "1", float("nan"), float("inf"),
                      10 ** 400, -10 ** 400):
            self.assertFalse(is_number(value))

    def test_shadow_for(self):
        state = PolicyState(mode=ENFORCE,
                            restart_classes=frozenset({"web"}))
        self.assertFalse(state.shadow_for("web"))
        self.assertTrue(state.shadow_for("workers"))
        observe = PolicyState(mode=OBSERVE, restart_classes=frozenset())
        self.assertFalse(observe.shadow_for("workers"))


class TestBudgets(unittest.TestCase):

    def setUp(self):
        self.ledger = Ledger()

    def free_at(self, program: str, pool: str = SERVICES_POOL,
                action: str = RESTART, shadow: bool = False,
                wall: float = WALL) -> float | None:
        return self.ledger.free_at(program, pool, action, shadow, wall)

    def test_two_restarts_per_program(self):
        self.ledger.book(booking("cws1", WALL - 600), False)
        self.assertIsNone(self.free_at("cws1"))
        self.ledger.book(booking("cws1", WALL - 300), False)
        self.assertEqual(self.free_at("cws1"), WALL - 600 + 3600)
        self.assertIsNone(self.free_at("cws0"))

    def test_three_restarts_per_pool(self):
        for index, program in enumerate(("cws0", "es", "ss")):
            self.ledger.book(booking(program, WALL - 900 + index), False)
        self.assertEqual(self.free_at("aws"), WALL - 900 + 3600)

    def test_the_workers_have_a_pool_of_their_own(self):
        for program in ("cws0", "es", "ss"):
            self.ledger.book(booking(program, WALL - 60), False)
        self.assertIsNone(self.free_at("worker0", WORKERS_POOL))
        for program in ("worker0", "worker1", "worker2"):
            self.ledger.book(booking(program, WALL - 30, WORKERS_POOL),
                             False)
        self.assertEqual(self.free_at("worker3", WORKERS_POOL),
                         WALL - 30 + 3600)
        self.ledger.real = [b for b in self.ledger.real
                            if b.pool == WORKERS_POOL]
        self.assertIsNone(self.free_at("aws"))

    def test_two_restarts_per_worker_inside_the_workers_pool(self):
        self.ledger.book(booking("worker0", WALL - 600, WORKERS_POOL),
                         False)
        self.assertIsNone(self.free_at("worker0", WORKERS_POOL))
        self.ledger.book(booking("worker0", WALL - 300, WORKERS_POOL),
                         False)
        # 2 of the pool's 3: the other Workers may still restart.
        self.assertEqual(self.free_at("worker0", WORKERS_POOL),
                         WALL - 600 + 3600)
        self.assertIsNone(self.free_at("worker1", WORKERS_POOL))

    def test_fatal_starts_have_their_own_budget(self):
        self.ledger.book(booking("es", WALL - 100, action=START), False)
        self.ledger.book(booking("es", WALL - 50, action=START), False)
        self.assertEqual(self.free_at("es", action=START),
                         WALL - 100 + 3600)
        self.assertIsNone(self.free_at("es"))
        self.assertIsNone(self.free_at("ss", action=START))

    def test_the_shadow_ledger_is_separate(self):
        self.ledger.book(booking("worker0", WALL - 10, WORKERS_POOL,
                                 would=True), True)
        self.ledger.book(booking("worker0", WALL - 5, WORKERS_POOL,
                                 would=True), True)
        self.assertIsNone(self.free_at("worker0", WORKERS_POOL))
        self.assertEqual(
            self.free_at("worker0", WORKERS_POOL, shadow=True),
            WALL - 10 + 3600)
        self.assertEqual(self.ledger.count("worker0", RESTART, True, WALL),
                         2)
        self.assertEqual(self.ledger.count("worker0", RESTART, False, WALL),
                         0)

    def test_the_window_slides(self):
        self.ledger.book(booking("cws1", WALL - 3600), False)
        self.ledger.book(booking("cws1", WALL - 100), False)
        self.assertIsNone(self.free_at("cws1"))
        self.assertEqual(self.ledger.count("cws1", RESTART, False, WALL), 1)

    def test_a_booking_in_the_future_counts(self):
        # The wall clock stepped back: its age is 0, not negative.
        self.ledger.book(booking("cws1", WALL + 500), False)
        self.ledger.book(booking("cws1", WALL + 600), False)
        self.assertEqual(self.free_at("cws1"), WALL + 500 + 3600)

    def test_an_exhausted_ledger_blocks_everything_for_an_hour(self):
        self.ledger = Ledger.exhausted(WALL)
        for action in (RESTART, START):
            for shadow in (False, True):
                self.assertEqual(
                    self.free_at("cws1", action=action, shadow=shadow),
                    WALL + 3600)
        self.ledger.prune(WALL + 3599)
        self.assertEqual(self.ledger.exhausted_until, WALL + 3600)
        self.ledger.prune(WALL + 3600)
        self.assertIsNone(self.ledger.exhausted_until)
        self.assertIsNone(self.free_at("cws1", wall=WALL + 3600))

    def test_prune_keeps_what_the_last_hour_needs(self):
        self.ledger.book(booking("cws1", WALL - 4000), False)
        self.ledger.book(booking("cws1", WALL - 10), False)
        self.ledger.red = [[WALL - 9000, WALL - 4000],
                           [WALL - 300, WALL - 200], [WALL - 50, None]]
        self.ledger.prune(WALL)
        self.assertEqual([b.wall for b in self.ledger.real], [WALL - 10])
        self.assertEqual(self.ledger.red,
                         [[WALL - 300, WALL - 200], [WALL - 50, None]])


class TestRedIntervals(unittest.TestCase):

    def test_red_since(self):
        ledger = Ledger(red=[[WALL - 300, WALL - 200]])
        self.assertTrue(ledger.red_since(WALL - 250))
        self.assertFalse(ledger.red_since(WALL - 200))
        ledger.red.append([WALL - 10, None])
        self.assertTrue(ledger.red_since(WALL + 1000))

    def test_last_restart_of(self):
        ledger = Ledger()
        self.assertIsNone(ledger.last_restart_of("cmsEvaluationService 0"))
        ledger.book(booking("es", WALL - 50, would=True,
                            label="cmsEvaluationService 0"), False)
        self.assertIsNone(ledger.last_restart_of("cmsEvaluationService 0"))
        ledger.book(booking("es", WALL - 20,
                            label="cmsEvaluationService 0"), False)
        self.assertEqual(
            ledger.last_restart_of("cmsEvaluationService 0").wall,
            WALL - 20)


def ledger_text(wall: str = "1800000000", program: str = '"es"',
                pool: str = '"services"', action: str = '"restart"',
                would: str = "false", pid: str = "4242",
                red: str = "[]", incidents: str = "{}",
                exhausted: str = "null") -> str:
    """Return a ledger file with one booking, each field as JSON text."""
    return (
        '{"real": [{"wall": %s, "program": %s, "label": "cmsX 0",'
        ' "pool": %s, "action": %s, "rule": "R1", "would": %s,'
        ' "pid": %s}], "shadow": [], "red": %s, "incidents": %s,'
        ' "exhausted_until": %s}'
        % (wall, program, pool, action, would, pid, red, incidents,
           exhausted))


def incident_text(opened: str = "1800000000", passes: str = "1",
                  rule: str = '"R1"') -> str:
    """Return the incidents of a ledger file: one, by field."""
    return '{"es": {"rule": %s, "opened": %s, "passes": %s}}' % (
        rule, opened, passes)


class TestLedgerFile(unittest.TestCase):

    def test_a_round_trip(self):
        ledger = Ledger()
        ledger.book(booking("cws1", WALL - 10), False)
        ledger.book(booking("worker0", WALL - 5, WORKERS_POOL,
                            would=True), True)
        ledger.red = [[WALL - 300, WALL - 200], [WALL - 50, None]]
        ledger.incidents = {"cws1": {"rule": "R1", "opened": WALL - 90,
                                     "passes": 1}}
        ledger.exhausted_until = WALL + 100
        self.assertEqual(Ledger.from_json(ledger.to_json()), ledger)

    def test_the_text_the_bad_cases_start_from_is_a_ledger(self):
        ledger = Ledger.from_json(ledger_text(
            red="[[1800000000, null]]", incidents=incident_text(),
            exhausted="1800003600"))
        self.assertEqual(ledger.real[0].wall, WALL)
        self.assertEqual(ledger.real[0].pid, 4242)
        self.assertEqual(ledger.incidents["es"]["passes"], 1)
        self.assertEqual(ledger.exhausted_until, WALL + 3600)
        self.assertIsNone(Ledger.from_json(ledger_text(
            action='"start"', pid="null")).real[0].pid)

    def test_what_is_not_a_ledger(self):
        for text in ("", "{", "[]", "{}", '{"real": 3}',
                     '{"real": [{"wall": "x"}], "shadow": [], "red": [],'
                     ' "incidents": {}, "exhausted_until": null}',
                     '{"real": [], "shadow": [], "red": [[1]],'
                     ' "incidents": {}, "exhausted_until": null}',
                     # Not the right kind of container.
                     '{"real": "", "shadow": [], "red": [],'
                     ' "incidents": {}, "exhausted_until": null}',
                     '{"real": {}, "shadow": [], "red": [],'
                     ' "incidents": {}, "exhausted_until": null}',
                     '{"real": [], "shadow": [], "red": [],'
                     ' "incidents": [], "exhausted_until": null}',
                     '{"real": [3], "shadow": [], "red": [],'
                     ' "incidents": {}, "exhausted_until": null}',
                     '{"real": [], "shadow": [], "red": [{}],'
                     ' "incidents": {}, "exhausted_until": null}',
                     '{"real": [], "shadow": [], "red": [],'
                     ' "incidents": {"es": 3}, "exhausted_until": null}',
                     # Nested deeper than the parser goes.
                     "[" * 100_000,
                     # A time that is not a number, or not a time.
                     ledger_text(wall="NaN"),
                     ledger_text(wall='"nan"'),
                     ledger_text(wall='"1800000000"'),
                     ledger_text(wall="true"),
                     ledger_text(wall="null"),
                     ledger_text(wall="-1"),
                     ledger_text(wall="1e300"),
                     ledger_text(wall="Infinity"),
                     ledger_text(wall="1" + "0" * 399),
                     ledger_text(red="[[NaN, null]]"),
                     ledger_text(red="[[1800000000, Infinity]]"),
                     ledger_text(red="[[1800000000, 1800000000, 1]]"),
                     ledger_text(exhausted="Infinity"),
                     ledger_text(exhausted="1e300"),
                     ledger_text(exhausted="NaN"),
                     ledger_text(exhausted='"1800003600"'),
                     ledger_text(incidents=incident_text(opened="NaN")),
                     ledger_text(incidents=incident_text(opened='"x"')),
                     # A count or a pid that is not one.
                     ledger_text(pid="1e400"),
                     ledger_text(pid="4242.0"),
                     ledger_text(pid="0"),
                     ledger_text(pid="true"),
                     ledger_text(pid='"4242"'),
                     ledger_text(pid="1" + "0" * 399),
                     ledger_text(incidents=incident_text(
                         passes="Infinity")),
                     ledger_text(incidents=incident_text(passes="1.5")),
                     ledger_text(incidents=incident_text(passes="-1")),
                     ledger_text(incidents=incident_text(passes="true")),
                     ledger_text(incidents=incident_text(
                         passes="1" + "0" * 399)),
                     # A flag or a name that is not one.
                     ledger_text(would='"false"'),
                     ledger_text(would="0"),
                     ledger_text(would="null"),
                     ledger_text(program="null"),
                     ledger_text(program="3"),
                     ledger_text(pool="null"),
                     ledger_text(pool='"nowhere"'),
                     ledger_text(action="null"),
                     ledger_text(action='"explode"'),
                     ledger_text(incidents=incident_text(rule="null"))):
            with self.subTest(text=text[:100]):
                with self.assertRaises(LedgerError):
                    Ledger.from_json(text)
        self.assertTrue(issubclass(LedgerError, ValueError))

    def test_a_nan_is_never_written(self):
        for ledger in (self.ledger_with_wall(float("nan")),
                       self.ledger_with_wall(float("inf")),
                       Ledger(red=[[float("nan"), None]]),
                       Ledger(exhausted_until=float("inf")),
                       Ledger(incidents={"es": {"rule": "R1",
                                                "opened": float("nan"),
                                                "passes": 1}})):
            with self.subTest(ledger=ledger):
                with self.assertRaises(ValueError):
                    ledger.to_json()

    @staticmethod
    def ledger_with_wall(wall: float) -> Ledger:
        ledger = Ledger()
        ledger.book(booking("cws1", wall), False)
        return ledger


if __name__ == "__main__":
    unittest.main()
