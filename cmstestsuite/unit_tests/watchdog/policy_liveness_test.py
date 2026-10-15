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

"""Tests for cmsWatchdog's policy: liveness rules, holds, incidents."""

import unittest

from cms.watchdog.policy import ALERT_REPEAT_S, CYCLE_S, OBSERVE, \
    RESTART, SERVICES_POOL, Backend, Booking, Gate, Ledger, Probe, \
    hhmm_utc
from cmstestsuite.unit_tests.watchdog.scenario import CWS0, CWS1, \
    DEFAULT_PROGRAMS, ES, LS, RED, Scenario, actions, alerts, held, \
    lock_wait_snapshot, program


def book(s: Scenario, name: str, label: str, wall: float,
         pid: int = 4242) -> None:
    """Book a real restart of name in the scenario's ledger."""
    s.state.ledger.book(Booking(wall, name, label, SERVICES_POOL, RESTART,
                                "R1", False, pid), False)


def trip(s: Scenario, **kwargs) -> list:
    """Fail CWS1 five times; the last cycle gets kwargs."""
    for _ in range(4):
        s.cycle(fail=[CWS1])
    return s.cycle(fail=[CWS1], **kwargs)


class TestLivenessRules(unittest.TestCase):

    def test_r1_trips_at_the_fifth_miss_and_not_before(self):
        s = Scenario()
        s.warm_up()
        for _ in range(4):
            self.assertEqual(actions(s.cycle(fail=[CWS1])), [])
        [restart] = actions(s.cycle(fail=[CWS1]))
        self.assertEqual(
            (restart.action, restart.program, restart.rule, restart.would,
             restart.shadow),
            (RESTART, CWS1, "R1", False, False))
        self.assertEqual(restart.text,
                         "5 of its last 6 favicon probes failed")

    def test_r2_trips_at_the_fifth_miss_and_not_before(self):
        s = Scenario()
        s.warm_up()
        for _ in range(4):
            self.assertEqual(actions(s.cycle(fail=[ES])), [])
        [restart] = actions(s.cycle(fail=[ES]))
        self.assertEqual((restart.program, restart.rule), (ES, "R2"))
        self.assertEqual(restart.text,
                         "5 of its last 6 health() calls failed")

    def test_the_sliding_window_trips_on_a_livelock(self):
        s = Scenario()
        s.warm_up()
        for failing in ([CWS1], [CWS1], [], [CWS1], [CWS1]):
            self.assertEqual(actions(s.cycle(fail=failing)), [])
        [restart] = actions(s.cycle(fail=[CWS1]))
        self.assertEqual(restart.program, CWS1)

    def test_a_program_that_answers_is_not_restarted_as_h3_lifts(self):
        s = Scenario()
        s.warm_up()
        gate = Gate(True, backends=lock_wait_snapshot())
        for _ in range(6):
            self.assertEqual(
                actions(s.cycle(fail=[CWS1], gate=gate)), [])
        # The lock wait ends and CWS1 answers: 5 of the last 6 results
        # are still misses, but the latest one is a pass.
        self.assertEqual(actions(s.cycle()), [])
        self.assertEqual(list(s.state.programs[CWS1].window),
                         [True] * 5 + [False])
        for _ in range(3):
            self.assertEqual(actions(s.cycle()), [])

    def test_a_program_that_answers_is_not_restarted_as_h5_lifts(self):
        s = Scenario()
        s.warm_up()
        for _ in range(6):
            self.assertEqual(
                actions(s.cycle(fail=[CWS1, ES, LS])), [])
        # The stall ends: every probe passes.
        for _ in range(3):
            self.assertEqual(actions(s.cycle()), [])

    def test_a_program_that_still_fails_is_restarted_as_h3_lifts(self):
        s = Scenario()
        s.warm_up()
        gate = Gate(True, backends=lock_wait_snapshot())
        for _ in range(6):
            s.cycle(fail=[CWS1], gate=gate)
        [restart] = actions(s.cycle(fail=[CWS1]))
        self.assertEqual((restart.program, restart.rule), (CWS1, "R1"))

    def test_three_misses_give_an_early_warning(self):
        s = Scenario()
        s.warm_up()
        s.cycle(fail=[CWS1])
        s.cycle(fail=[CWS1])
        decisions = s.cycle(fail=[CWS1])
        self.assertEqual(actions(decisions), [])
        [warning] = alerts(decisions)
        self.assertEqual(warning.key, (CWS1, "R1 warning"))
        self.assertIn("early warning: 3 of the last 6 favicon probes of "
                      "cmsContestWebServer 1 failed", warning.text)

    def test_starved_cycles_are_unknown_results(self):
        s = Scenario()
        s.warm_up()
        for _ in range(4):
            self.assertEqual(s.cycle(fail=[CWS1], lag_s=6.0), [])
        self.assertEqual(actions(s.cycle(fail=[CWS1])), [])
        self.assertEqual(sum(s.state.programs[CWS1].window), 1)

    def test_a_starved_cycle_decides_nothing(self):
        s = Scenario()
        s.warm_up()
        for _ in range(4):
            s.cycle(fail=[CWS1])
        self.assertEqual(s.cycle(fail=[CWS1], lag_s=6.0), [])
        [restart] = actions(s.cycle(fail=[CWS1]))
        self.assertEqual(restart.program, CWS1)

    def test_a_pause_clears_the_windows(self):
        s = Scenario()
        s.warm_up()
        for _ in range(4):
            s.cycle(fail=[CWS1])
        decisions = s.cycle(fail=[CWS1], since=45.0, advance=45.0,
                            lag_s=30.0)
        [pause] = [d for d in decisions if d.key == ("-", "pause")]
        self.assertIn("paused for 45 s", pause.text)
        self.assertEqual(actions(decisions), [])
        self.assertEqual(list(s.state.programs[CWS1].window), [])
        self.assertEqual(actions(s.cycle(fail=[CWS1])), [])

    def test_a_new_pid_clears_the_window(self):
        s = Scenario()
        s.warm_up()
        for _ in range(4):
            s.cycle(fail=[CWS1])
        s.set(CWS1, pid=999)
        self.assertEqual(actions(s.cycle(fail=[CWS1])), [])
        self.assertEqual(list(s.state.programs[CWS1].window), [True])

    def test_only_running_managed_programs_are_judged(self):
        s = Scenario(programs=DEFAULT_PROGRAMS + (
            program("cmstelegrambot", "TelegramBot", 0, 111),))
        s.warm_up()
        failing = Probe(False, None, "timed out")
        for _ in range(6):
            self.assertEqual(
                actions(s.cycle(probes={"cmstelegrambot": failing})), [])
        for state in ("STOPPED", "STARTING", "BACKOFF", "EXITED",
                      "UNKNOWN"):
            s.set(CWS1, state=state, pid=None)
            for _ in range(6):
                self.assertEqual(
                    actions(s.cycle(probes={CWS1: failing})), [])


class TestHoldsAlone(unittest.TestCase):

    def assertHeldBy(self, decisions: list, code: str):
        self.assertEqual(actions(decisions), [])
        [alert] = held(decisions, CWS1)
        self.assertEqual(alert.holds[0], code)
        self.assertIn("held by %s" % code, alert.text)
        return alert

    def test_h1_supervisord_did_not_answer(self):
        s = Scenario()
        s.warm_up()
        alert = self.assertHeldBy(trip(s, supervisor_ok=False), "H1")
        self.assertEqual(alert.holds, ("H1",))

    def test_h2_the_gate_is_red(self):
        s = Scenario()
        s.warm_up()
        decisions = trip(s, gate=RED)
        alert = self.assertHeldBy(decisions, "H2")
        self.assertEqual(alert.holds, ("H2",))
        self.assertIn("the database gate is red (refused)", alert.text)
        [gate] = [d for d in alerts(decisions) if d.key == ("-", "gate")]
        self.assertEqual(gate.level, "ERROR")

    def test_h2_waits_for_120_s_of_green_and_clears_the_windows(self):
        s = Scenario()
        s.warm_up()
        trip(s, gate=RED)
        decisions = s.cycle(fail=[CWS1])
        self.assertTrue(any(
            d.text.startswith("Resolved: the database gate is green again")
            for d in decisions))
        self.assertEqual(list(s.state.programs[CWS1].window), [True])
        for _ in range(4):
            self.assertEqual(actions(s.cycle(fail=[CWS1])), [])
        # It tripped again, held by H2 as during the outage: the same
        # hold gives no new line, but the open alert says why.
        alert = s.state.open_alerts[(CWS1, "R1")]
        self.assertEqual(alert.holds[0], "H2")
        self.assertIn("back for only 60 s", alert.text)
        for _ in range(3):
            self.assertEqual(actions(s.cycle(fail=[CWS1])), [])
        [restart] = actions(s.cycle(fail=[CWS1]))
        self.assertEqual(restart.program, CWS1)

    def test_h3_a_lock_wait(self):
        s = Scenario()
        s.warm_up()
        decisions = trip(s, gate=Gate(True, backends=lock_wait_snapshot()))
        alert = self.assertHeldBy(decisions, "H3")
        self.assertEqual(alert.holds, ("H3",))
        self.assertIn(
            "'cmsEvaluationService 0' (pid 500) has waited 45 s on a lock "
            "held by pid 900 ('psql', idle in transaction, transaction "
            "open 300 s)", alert.text)
        self.assertIn("Ending a blocker is a human decision", alert.text)
        self.assertNotIn("orphan", alert.text)

    def test_h3_shows_hidden_fields_as_unknown(self):
        s = Scenario()
        s.warm_up()
        snapshot = (
            Backend(pid=500, application_name="cmsContestWebServer 0",
                    state="active", wait_event_type="Lock",
                    query_age_s=31.0, blocked_by=(901,)),
            Backend(pid=901))
        alert = self.assertHeldBy(
            trip(s, gate=Gate(True, backends=snapshot)), "H3")
        self.assertIn(
            "pid 901 ('unknown', unknown, transaction open unknown)",
            alert.text)

    def test_h3_ignores_a_short_wait_and_a_session_of_another_program(
            self):
        s = Scenario()
        s.warm_up()
        snapshot = (
            Backend(pid=500, application_name="cmsEvaluationService 0",
                    wait_event_type="Lock", query_age_s=30.0,
                    blocked_by=(900,)),
            Backend(pid=501, application_name="psql",
                    wait_event_type="Lock", query_age_s=500.0,
                    blocked_by=(900,)))
        [restart] = actions(trip(s, gate=Gate(True, backends=snapshot)))
        self.assertEqual(restart.program, CWS1)

    def test_h4_two_programs_of_the_class_fail(self):
        s = Scenario()
        s.warm_up()
        for _ in range(4):
            s.cycle(fail=[CWS0, CWS1])
        decisions = s.cycle(fail=[CWS0, CWS1])
        self.assertEqual(actions(decisions), [])
        for name in (CWS0, CWS1):
            [alert] = held(decisions, name)
            self.assertEqual(alert.holds, ("H4",))
            self.assertIn(
                "2 programs of class web fail together "
                "(cmsContestWebServer 0, cmsContestWebServer 1)",
                alert.text)

    def test_h4_does_not_count_a_program_over_its_budget(self):
        s = Scenario()
        s.warm_up()
        for wall in (s.wall - 600, s.wall - 300):
            book(s, CWS0, "cmsContestWebServer 0", wall, 108)
        for _ in range(4):
            s.cycle(fail=[CWS0, CWS1])
        decisions = s.cycle(fail=[CWS0, CWS1])
        [restart] = actions(decisions)
        self.assertEqual(restart.program, CWS1)
        [alert] = held(decisions, CWS0)
        self.assertEqual(alert.holds, ("H7",))

    def test_h5_a_third_of_the_programs_fail(self):
        s = Scenario()
        s.warm_up()
        for _ in range(4):
            s.cycle(fail=[CWS1, ES, LS])
        decisions = s.cycle(fail=[CWS1, ES, LS])
        self.assertEqual(actions(decisions), [])
        for name in (CWS1, ES, LS):
            [alert] = held(decisions, name)
            self.assertEqual(alert.holds, ("H5",))
            self.assertIn("3 of the 8 programs probed failed a probe",
                          alert.text)

    def test_h6_the_program_just_started(self):
        s = Scenario()
        s.warm_up()
        for _ in range(4):
            s.cycle(fail=[CWS1])
        s.set(CWS1, start_wall=s.wall + CYCLE_S - 60)
        alert = self.assertHeldBy(s.cycle(fail=[CWS1]), "H6")
        self.assertIn("cmsContestWebServer 1 started 60 s ago", alert.text)

    def test_h6_the_watchdog_just_started(self):
        s = Scenario()
        # The gate was green long before: only H6 holds.
        s.state.green_since_mono = s.mono - 1000
        alert = self.assertHeldBy(trip(s), "H6")
        self.assertEqual(alert.holds, ("H6",))
        self.assertIn("the watchdog itself started 60 s ago", alert.text)

    def test_h6_the_last_would_action(self):
        s = Scenario(mode=OBSERVE)
        s.warm_up()
        [would] = actions(trip(s))
        self.assertTrue(would.would)
        alert = self.assertHeldBy(s.cycle(fail=[CWS1]), "H6")
        self.assertIn("its last action or would-action was 15 s ago",
                      alert.text)

    def test_h7_the_budget(self):
        s = Scenario()
        s.warm_up()
        first = s.wall - 600
        for wall in (first, s.wall - 300):
            book(s, CWS1, "cmsContestWebServer 1", wall, 109)
        alert = self.assertHeldBy(trip(s), "H7")
        self.assertIn("will not restart cmsContestWebServer 1 before %s"
                      % hhmm_utc(first + 3600), alert.text)

    def test_h7_a_ledger_that_did_not_parse(self):
        s = Scenario()
        s.warm_up()
        s.state.ledger = Ledger.exhausted(s.wall)
        alert = self.assertHeldBy(trip(s), "H7")
        self.assertIn("its ledger did not parse", alert.text)

    def test_h8_another_action_in_flight(self):
        s = Scenario()
        s.warm_up()
        self.assertHeldBy(trip(s, in_flight=True), "H8")

    def test_h9_a_class_enforce_may_not_act_on(self):
        s = Scenario(restart=("core", "infra"))
        s.warm_up()
        [would] = actions(trip(s))
        self.assertEqual((would.program, would.would, would.shadow),
                         (CWS1, True, True))
        self.assertEqual(s.state.ledger.real, [])
        self.assertEqual([b.program for b in s.state.ledger.shadow],
                         [CWS1])


class TestHoldOrder(unittest.TestCase):

    def test_a_lock_wait_heads_a_correlated_failure(self):
        s = Scenario()
        s.warm_up()
        gate = Gate(True, backends=lock_wait_snapshot())
        for _ in range(4):
            s.cycle(fail=[CWS0, CWS1], gate=gate)
        decisions = s.cycle(fail=[CWS0, CWS1], gate=gate)
        self.assertEqual(actions(decisions), [])
        [alert] = held(decisions, CWS1)
        self.assertEqual(alert.holds, ("H3", "H4"))
        self.assertIn("held by H3", alert.text)
        self.assertIn("pid 900 ('psql'", alert.text)
        self.assertIn("Also: H4.", alert.text)

    def test_the_blocker_follows_another_headline(self):
        s = Scenario()
        s.warm_up()
        decisions = trip(s, supervisor_ok=False,
                         gate=Gate(True, backends=lock_wait_snapshot()))
        [alert] = held(decisions, CWS1)
        self.assertEqual(alert.holds, ("H1", "H3"))
        self.assertIn(" H3: a database lock wait: ", alert.text)


class TestOrphanMark(unittest.TestCase):

    def blocker_text(self, backend_age_s: float) -> str:
        s = Scenario()
        s.warm_up()
        # The watchdog restarted ES (pid 99); the new ES started 50 s
        # before the cycle that trips.
        book(s, ES, "cmsEvaluationService 0", s.wall - 10, 99)
        s.set(ES, pid=204, start_wall=s.wall + 5 * CYCLE_S - 50)
        snapshot = (
            Backend(pid=500, application_name="cmsContestWebServer 0",
                    state="active", wait_event_type="Lock",
                    query_age_s=40.0, blocked_by=(900,)),
            Backend(pid=900, application_name="cmsEvaluationService 0",
                    state="idle in transaction", xact_age_s=120.0,
                    backend_age_s=backend_age_s))
        [alert] = held(trip(s, gate=Gate(True, backends=snapshot)), CWS1)
        return alert.text

    def test_a_backend_older_than_the_new_process_is_an_orphan(self):
        self.assertIn("transaction open 120 s), orphan of pid 99",
                      self.blocker_text(100.0))

    def test_a_younger_backend_may_be_the_new_process_own(self):
        self.assertNotIn("orphan", self.blocker_text(10.0))


class TestBudgets(unittest.TestCase):

    def test_exhaustion_repeats_and_recovery(self):
        s = Scenario()
        s.warm_up()
        first = s.wall - 10
        book(s, CWS1, "cmsContestWebServer 1", first, 109)
        book(s, CWS1, "cmsContestWebServer 1", s.wall - 5, 109)
        self.assertEqual(held(trip(s), CWS1)[0].holds[0], "H7")
        # The held alert repeats every 5 minutes, not every cycle.
        emitted = [bool(held(s.cycle(fail=[CWS1]), CWS1))
                   for _ in range(int(ALERT_REPEAT_S / CYCLE_S))]
        self.assertEqual(emitted, [False] * 19 + [True])
        # An hour after the first restart, the budget allows one again.
        decisions = s.cycle(fail=[CWS1], since=CYCLE_S,
                            advance=first + 3600 - s.wall)
        [restart] = actions(decisions)
        self.assertEqual(restart.program, CWS1)

    def test_the_shadow_ledger_has_the_same_limits(self):
        s = Scenario(restart=("core", "infra"))
        s.warm_up()
        trip(s)
        history = [s.cycle(fail=[CWS1]) for _ in range(16)]
        self.assertEqual([i for i, d in enumerate(history) if actions(d)],
                         [7])
        [would] = actions(history[7])
        self.assertTrue(would.would and would.shadow)
        [alert] = held(history[15], CWS1)
        self.assertEqual(alert.holds[0], "H7")
        self.assertIn("(in the shadow ledger)", alert.text)
        self.assertEqual(s.state.ledger.real, [])
        self.assertEqual(s.state.ledger.count(CWS1, RESTART, True, s.wall),
                         2)

    def test_observe_mode_re_trips_after_each_grace(self):
        s = Scenario(mode=OBSERVE)
        s.warm_up()
        self.assertTrue(actions(trip(s))[0].would)
        history = [s.cycle(fail=[CWS1]) for _ in range(16)]
        self.assertEqual([i for i, d in enumerate(history) if actions(d)],
                         [7])
        self.assertEqual(held(history[15], CWS1)[0].holds[0], "H7")
        self.assertEqual(len(s.state.ledger.real), 2)


class TestIncidents(unittest.TestCase):

    def restarted(self) -> Scenario:
        s = Scenario()
        s.warm_up()
        self.assertEqual(actions(trip(s))[0].program, CWS1)
        self.assertIn(CWS1, s.state.ledger.incidents)
        # The restart worked: a new pid.
        s.set(CWS1, pid=999, start_wall=s.wall + CYCLE_S)
        return s

    @staticmethod
    def resolved(decisions: list) -> list:
        return [d for d in decisions if d.text.startswith("Resolved:")]

    def test_an_incident_closes_after_two_passes(self):
        s = self.restarted()
        self.assertEqual(self.resolved(s.cycle()), [])
        [line] = self.resolved(s.cycle())
        self.assertIn("cmsContestWebServer 1 passes its checks again",
                      line.text)
        self.assertNotIn(CWS1, s.state.ledger.incidents)

    def test_a_failure_resets_the_passes(self):
        s = self.restarted()
        for failing in ([], [CWS1], []):
            self.assertEqual(self.resolved(s.cycle(fail=failing)), [])
        self.assertEqual(len(self.resolved(s.cycle())), 1)


class TestClocks(unittest.TestCase):

    def test_a_wall_clock_step_back_gives_ages_of_zero(self):
        s = Scenario()
        s.warm_up()
        s.set(CWS1, start_wall=s.wall + 10_000)
        [alert] = held(trip(s), CWS1)
        self.assertEqual(alert.holds, ("H6",))
        self.assertIn("cmsContestWebServer 1 started 0 s ago", alert.text)


if __name__ == "__main__":
    unittest.main()
