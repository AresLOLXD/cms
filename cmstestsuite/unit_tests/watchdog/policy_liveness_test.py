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

from cms.watchdog.policy import ALERT_REPEAT_S, CYCLE_S, GATE_KILL_S, \
    OBSERVE, RESTART, SERVICES_POOL, Backend, Booking, Gate, Ledger, \
    Probe, busy_sections, hhmm_utc
from cmstestsuite.unit_tests.watchdog.scenario import CWS0, CWS1, \
    DEFAULT_PROGRAMS, ES, GREEN, LS, RED, W0, W1, Scenario, actions, \
    alerts, held, lock_wait_snapshot, program


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

    def warning(self, s: Scenario, **kwargs):
        """Fail CWS1 three times and return its early warning."""
        s.warm_up()
        s.cycle(fail=[CWS1])
        s.cycle(fail=[CWS1])
        [warning] = [d for d in alerts(s.cycle(fail=[CWS1], **kwargs))
                     if d.key == (CWS1, "R1 warning")]
        return warning

    def test_the_early_warning_says_what_happens_at_five(self):
        text = self.warning(Scenario()).text
        self.assertIn("At 5 misses, the latest included, the watchdog "
                      "restarts it.", text)
        self.assertNotIn("only logs", text)
        self.assertNotIn("applies now", text)
        self.assertIn("Check ./logs.sh for its log; cmsWatchdog "
                      "--snapshot cmscontestwebserver1 writes its stacks.",
                      text)

    def test_the_early_warning_in_observe_mode_promises_no_restart(self):
        text = self.warning(Scenario(mode=OBSERVE)).text
        self.assertIn("the watchdog only logs the restart it would do "
                      "(observe mode)", text)
        self.assertNotIn("restarts it", text)
        self.assertIn("Check ./logs.sh for its log; cmsWatchdog "
                      "--snapshot cmscontestwebserver1 writes its stacks.",
                      text)

    def test_the_early_warning_of_an_h9_class_promises_no_restart(self):
        text = self.warning(Scenario(restart=("core", "infra"))).text
        self.assertIn("only logs the restart it would do (class web is "
                      "not in CMS_WATCHDOG_RESTART)", text)
        self.assertNotIn("restarts it", text)

    def test_the_early_warning_names_the_hold_that_applies_now(self):
        text = self.warning(Scenario(), gate=RED).text
        # No promise: it would restart it, but a hold applies now.
        self.assertIn("the watchdog would restart it. But H2 applies "
                      "now: the database gate is red (refused).", text)
        self.assertNotIn("restarts it", text)
        self.assertNotIn("held by", text)

    def test_the_early_warning_lists_every_hold_that_applies(self):
        s = Scenario()
        s.warm_up()
        for wall in (s.wall - 600, s.wall - 300):
            book(s, CWS1, "cmsContestWebServer 1", wall, 109)
        # 60 s old at the third miss: H6 holds, and the budget (H7) will
        # go on holding after the grace.
        s.set(CWS1, start_wall=s.wall - CYCLE_S)
        s.cycle(fail=[CWS1])
        s.cycle(fail=[CWS1])
        [warning] = [d for d in alerts(s.cycle(fail=[CWS1]))
                     if d.key == (CWS1, "R1 warning")]
        self.assertIn("would restart it. But H6 applies now: "
                      "cmsContestWebServer 1 started 60 s ago. Also: H7.",
                      warning.text)

    def test_the_early_warning_carries_the_blockers_of_h3(self):
        lock = Gate(True, backends=lock_wait_snapshot())
        details = ("H3: a database lock wait: 'cmsEvaluationService 0' "
                   "(pid 500): its statement has been waiting 45 s on a "
                   "lock held by pid 900 ('psql', idle in transaction, "
                   "transaction open 300 s). Ending a blocker is a human "
                   "decision.")
        with self.subTest(headline="H1"):
            s = Scenario()
            text = self.warning(s, supervisor_ok=False, gate=lock).text
            self.assertIn("But H1 applies now: supervisord did not answer "
                          "in this cycle. Also: H3. " + details, text)
            # As the held line of the trip carries them.
            s.cycle(fail=[CWS1], supervisor_ok=False, gate=lock)
            [alert] = held(
                s.cycle(fail=[CWS1], supervisor_ok=False, gate=lock), CWS1)
            self.assertIn(" Also: H3. " + details, alert.text)
        with self.subTest(headline="H3"):
            text = self.warning(Scenario(), gate=lock).text
            self.assertIn("But " + details.replace(":", " applies now:", 1),
                          text)
            self.assertEqual(text.count("a database lock wait"), 1)

    def test_the_early_warning_in_observe_mode_names_the_hold_too(self):
        text = self.warning(Scenario(mode=OBSERVE), gate=RED).text
        self.assertIn("only logs the restart it would do (observe mode). "
                      "But H2 applies now: the database gate is red "
                      "(refused).", text)
        self.assertNotIn("restarts it", text)

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

    def test_a_starved_cycle_does_not_track_a_timed_out_gate(self):
        for reason in ("timeout", "other"):
            with self.subTest(reason=reason):
                s = Scenario()
                s.warm_up()
                for _ in range(4):
                    s.cycle(fail=[CWS1])
                green_cycles = s.state.green_cycles
                # Its gate may have failed because the watchdog was
                # starved: no line, no red interval, no cleared window,
                # no wait for H2. The near-miss statistics count it.
                gate = Gate(False, reason, "psql failed")
                self.assertEqual(s.cycle(fail=[CWS1], gate=gate, lag_s=6.0),
                                 [])
                self.assertEqual(s.state.ledger.red, [])
                self.assertEqual(s.state.near_miss.red_cycles, 1)
                self.assertEqual(s.state.green_cycles, green_cycles)
                self.assertEqual(sum(s.state.programs[CWS1].window), 4)
                [restart] = actions(s.cycle(fail=[CWS1]))
                self.assertEqual(restart.program, CWS1)

    def test_a_starved_green_gate_leaves_an_open_red_interval_open(self):
        # The reason of a green gate means nothing, whatever it holds.
        for gate in (GREEN, Gate(True, "refused")):
            with self.subTest(gate=gate):
                s = Scenario()
                s.warm_up()
                self.assertEqual(len(s.cycle(gate=RED)), 1)
                self.assertEqual(s.cycle(gate=gate, lag_s=6.0), [])
                self.assertEqual(len(s.state.ledger.red), 1)
                self.assertIsNone(s.state.ledger.red[0][1])
                self.assertEqual(s.state.green_cycles, 0)
                decisions = s.cycle()
                self.assertTrue(any(
                    d.text.startswith("Resolved: the database gate")
                    for d in decisions))
                self.assertIsNotNone(s.state.ledger.red[0][1])

    def test_a_starved_cycle_tracks_a_red_that_postgresql_answered(self):
        for reason in ("refused", "too many clients", "authentication"):
            with self.subTest(reason=reason):
                s = Scenario()
                s.warm_up()
                for _ in range(4):
                    s.cycle(fail=[CWS1])
                gate = Gate(False, reason, "PostgreSQL answered")
                decisions = s.cycle(fail=[CWS1], gate=gate, lag_s=6.0)
                [line] = decisions
                self.assertEqual((line.key, line.level),
                                 (("-", "gate"), "ERROR"))
                self.assertEqual(len(s.state.ledger.red), 1)
                self.assertIsNone(s.state.ledger.red[0][1])
                self.assertEqual(s.state.near_miss.red_cycles, 1)
                self.assertEqual(s.state.green_cycles, 0)
                self.assertIsNone(s.state.green_since_mono)
                # The database is back: the windows are cleared and
                # restarts wait for it.
                decisions = s.cycle(fail=[CWS1])
                self.assertTrue(any(
                    d.text.startswith("Resolved: the database gate")
                    for d in decisions))
                self.assertEqual(list(s.state.programs[CWS1].window),
                                 [True])

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
        # A kind that R1 judges, but without the shard that makes a
        # program managed (a command that is not "cmsX <shard>").
        unmanaged = "cmscontestwebserver"
        s = Scenario(programs=DEFAULT_PROGRAMS + (
            program(unmanaged, "ContestWebServer", None, 111),))
        s.warm_up()
        failing = Probe(False, None, "timed out")
        for _ in range(6):
            self.assertEqual(actions(s.cycle(fail=[unmanaged])), [])
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
            "'cmsEvaluationService 0' (pid 500): its statement has been "
            "waiting 45 s on a lock held by pid 900 ('psql', idle in "
            "transaction, transaction open 300 s)", alert.text)
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

    def test_h3_lists_at_most_3_blockers_of_a_waiter(self):
        s = Scenario()
        s.warm_up()
        snapshot = (
            Backend(pid=500, application_name="cmsContestWebServer 0",
                    wait_event_type="Lock", query_age_s=40.0,
                    blocked_by=(901, 902, 903, 904, 905)),
        ) + tuple(Backend(pid=pid, application_name="psql",
                          state="idle in transaction", xact_age_s=100.0)
                  for pid in range(901, 906))
        alert = self.assertHeldBy(
            trip(s, gate=Gate(True, backends=snapshot)), "H3")
        for pid in (901, 902, 903):
            self.assertIn("pid %d ('psql'" % pid, alert.text)
        for pid in (904, 905):
            self.assertNotIn("pid %d" % pid, alert.text)
        self.assertIn("; and 2 more blockers", alert.text)

    def test_h3_lists_at_most_3_waiters_longest_first(self):
        s = Scenario()
        s.warm_up()
        snapshot = tuple(
            Backend(pid=500 + i, application_name="cmsWorker %d" % i,
                    wait_event_type="Lock", query_age_s=40.0 + i,
                    blocked_by=(900,))
            for i in range(5)
        ) + (Backend(pid=900, application_name="psql",
                     state="idle in transaction", xact_age_s=100.0),)
        alert = self.assertHeldBy(
            trip(s, gate=Gate(True, backends=snapshot)), "H3")
        for pid in (504, 503, 502):
            self.assertIn("(pid %d)" % pid, alert.text)
        for pid in (501, 500):
            self.assertNotIn("(pid %d)" % pid, alert.text)
        self.assertIn("and 2 more sessions are waiting too", alert.text)

    def test_h3_treats_a_non_finite_age_as_unknown(self):
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value):
                s = Scenario()
                s.warm_up()
                # The orphan mark looks at the blocker's backend age too.
                book(s, ES, "cmsEvaluationService 0", s.wall - 10, 99)
                s.set(ES, pid=204, start_wall=s.wall + 5 * CYCLE_S - 50)
                snapshot = (
                    Backend(pid=500, application_name="cmsWorker 0",
                            wait_event_type="Lock", query_age_s=value,
                            blocked_by=(900,)),
                    Backend(pid=501, application_name="cmsWorker 1",
                            wait_event_type="Lock", query_age_s=45.0,
                            blocked_by=(900,)),
                    Backend(pid=900,
                            application_name="cmsEvaluationService 0",
                            state="idle in transaction", xact_age_s=value,
                            backend_age_s=value))
                alert = self.assertHeldBy(
                    trip(s, gate=Gate(True, backends=snapshot)), "H3")
                self.assertIn("(pid 501)", alert.text)
                self.assertNotIn("(pid 500)", alert.text)
                self.assertIn("transaction open unknown)", alert.text)
                self.assertNotIn("orphan", alert.text)

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

    def test_h5_does_not_count_workers(self):
        s = Scenario()
        s.warm_up()
        failing = Probe(False, None, "timed out")
        for _ in range(4):
            s.cycle(fail=[CWS1, ES], probes={W0: failing, W1: failing})
        decisions = s.cycle(fail=[CWS1, ES],
                            probes={W0: failing, W1: failing})
        # 2 of the 8 programs of the health kinds failed: no H5.
        [restart] = actions(decisions)
        self.assertEqual(restart.program, ES)
        [alert] = held(decisions, CWS1)
        self.assertEqual(alert.holds, ("H8",))

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
        # A fresh watchdog did not see the database turn green: it does
        # not assume it was red, so H2 does not hold; only H6 does.
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

    def test_h6_the_last_real_action(self):
        s = Scenario()
        s.warm_up()
        [restart] = actions(trip(s))
        self.assertFalse(restart.would)
        # The restart is not visible yet (the program keeps its start
        # time): the action itself gives the grace, as in observe mode.
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


class TestPidCheck(unittest.TestCase):

    @staticmethod
    def answer(pid: object) -> dict:
        return {CWS1: {"pid": pid, "uptime_s": 9_999.0, "busy": []}}

    @staticmethod
    def pid_alerts(decisions: list) -> list:
        return [d for d in alerts(decisions) if d.key == (CWS1, "pid")]

    def test_another_pid_on_the_port_is_an_alert_and_nothing_else(self):
        s = Scenario()
        s.warm_up()
        decisions = s.cycle(health=self.answer(777))
        [alert] = self.pid_alerts(decisions)
        self.assertEqual(alert.level, "WARNING")
        self.assertIn(
            "pid 777 answers on the RPC port of cmsContestWebServer 1, "
            "which supervisord started as pid 109", alert.text)
        self.assertIn("Check ./status.sh and ps.", alert.text)
        self.assertEqual(actions(decisions), [])

    def test_the_alert_repeats_every_5_minutes(self):
        s = Scenario()
        s.warm_up()
        emitted = [bool(self.pid_alerts(s.cycle(health=self.answer(777))))
                   for _ in range(int(ALERT_REPEAT_S / CYCLE_S) + 1)]
        self.assertEqual(emitted, [True] + [False] * 19 + [True])

    def test_the_alert_keeps_its_limit_when_the_pid_changes(self):
        # Another process holds ES's RPC port: supervisord's ES quits at
        # once and is started again, so each cycle sees a new pid, and
        # the other process answers health().
        s = Scenario()
        s.warm_up()
        answer = {ES: {"pid": 777, "uptime_s": 9_999.0, "busy": []}}
        emitted = []
        for cycle in range(int(ALERT_REPEAT_S / CYCLE_S) + 1):
            s.set(ES, pid=2000 + cycle, start_wall=s.wall + CYCLE_S - 1)
            decisions = s.cycle(health=answer)
            emitted.append(any(d.key == (ES, "pid")
                               for d in alerts(decisions)))
        self.assertEqual(emitted, [True] + [False] * 19 + [True])

    def test_the_same_pid_or_no_usable_pid_is_not_an_alert(self):
        s = Scenario()
        s.warm_up()
        # A bool, a float or a string is not a pid.
        for value in (109, None, "777", True, 777.0, [777]):
            with self.subTest(value=value):
                self.assertEqual(
                    self.pid_alerts(s.cycle(health=self.answer(value))),
                    [])
        self.assertEqual(
            self.pid_alerts(s.cycle(health={CWS1: [777]})), [])


class TestBusySections(unittest.TestCase):

    def test_an_answer_that_is_not_well_formed_has_no_section(self):
        for payload in (None, "busy", 3, [], {}, {"busy": None},
                        {"busy": "x"}, {"busy": {"what": "x"}},
                        [{"busy": []}]):
            with self.subTest(payload=payload):
                self.assertEqual(busy_sections(payload), [])

    def test_a_malformed_entry_is_skipped_and_the_others_are_kept(self):
        good = {"what": "score", "age_s": 5, "idle_s": 2}
        payload = {"busy": [
            1, "x", None, [good],
            {"what": 3, "age_s": 1, "idle_s": 1},
            {"what": "a", "age_s": "1", "idle_s": 1},
            {"what": "a", "age_s": float("nan"), "idle_s": 1},
            {"what": "a", "age_s": 1, "idle_s": float("inf")},
            {"what": "a", "age_s": True, "idle_s": 1},
            {"what": "a", "idle_s": 1},
            {"what": "a", "age_s": 1},
            good]}
        self.assertEqual(busy_sections(payload), [
            {"what": "score", "tid": None, "age_s": 5.0, "idle_s": 2.0,
             "statements": None}])

    def test_sections_come_oldest_first_with_checked_optional_fields(self):
        payload = {"busy": [
            {"what": "young", "age_s": 10, "idle_s": 1.5, "tid": 7,
             "statements": 3},
            {"what": "old", "age_s": 200.5, "idle_s": 4, "tid": True,
             "statements": "many"},
            {"what": "middle", "age_s": 50, "idle_s": 0, "tid": 7.0,
             "statements": None}]}
        self.assertEqual(busy_sections(payload), [
            {"what": "old", "tid": None, "age_s": 200.5, "idle_s": 4.0,
             "statements": None},
            {"what": "middle", "tid": None, "age_s": 50.0, "idle_s": 0.0,
             "statements": None},
            {"what": "young", "tid": 7, "age_s": 10.0, "idle_s": 1.5,
             "statements": 3}])


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


class TestHoldEpisodes(unittest.TestCase):

    def episode(self) -> Scenario:
        """Hold CWS1 by H1, then H3, then H1, then H3 again."""
        s = Scenario()
        s.warm_up()
        for _ in range(4):
            s.cycle(fail=[CWS1])
        lock = Gate(True, backends=lock_wait_snapshot())
        headlines = []
        emitted = []
        for changes in ({"supervisor_ok": False}, {"gate": lock},
                        {"supervisor_ok": False}, {"gate": lock}):
            decisions = s.cycle(fail=[CWS1], **changes)
            headlines.append(s.state.held_headline[(CWS1, "R1")])
            emitted.append(bool(held(decisions, CWS1)))
        self.assertEqual(headlines, ["H1", "H3", "H1", "H3"])
        # The first H3 of the episode goes out at once, though H1 headed
        # a line 15 s before; the second one, and H1 again, are limited.
        self.assertEqual(emitted, [True, True, False, False])
        return s

    def test_the_first_lock_wait_headline_goes_out_at_once(self):
        s = self.episode()
        # The episode ends with a restart: the next one starts afresh.
        [restart] = actions(s.cycle(fail=[CWS1]))
        self.assertEqual(restart.program, CWS1)
        self.assertNotIn((CWS1, "R1"), s.state.held_announced)

    def test_a_new_process_starts_a_new_hold_episode(self):
        s = Scenario()
        s.warm_up()
        book(s, CWS1, "cmsContestWebServer 1", s.wall - 600, 109)
        trip(s)
        for _ in range(7):
            s.cycle(fail=[CWS1])
        [budget] = held(s.cycle(fail=[CWS1]), CWS1)
        self.assertEqual(budget.holds[0], "H7")
        # The operator restarts it by hand; the new process hangs too.
        s.set(CWS1, pid=999, start_wall=s.wall + CYCLE_S)
        warned = []
        lines = []
        for cycle in range(9):
            decisions = s.cycle(fail=[CWS1])
            if cycle == 0:
                # The old process's episode is over, its incident is not.
                for table in (s.state.held_headline,
                              s.state.held_announced):
                    self.assertNotIn((CWS1, "R1"), table)
                self.assertIn(CWS1, s.state.ledger.incidents)
            warned.append(any(d.key == (CWS1, "R1 warning")
                              for d in alerts(decisions)))
            lines.append(held(decisions, CWS1))
        # The new process's first held line, at its trip, is entering a
        # hold: it does not wait for the 5 minutes of the old process's
        # last line. Its budget (H7) is announced again too, when the
        # grace of H6 ends. Its early warning keeps the rate limit of
        # the program's: the old process's went out 195 s before.
        self.assertEqual(warned, [False] * 9)
        self.assertEqual([bool(line) for line in lines],
                         [False] * 4 + [True] + [False] * 3 + [True])
        self.assertEqual(lines[4][0].holds[:2], ("H6", "H7"))
        self.assertEqual(lines[8][0].holds[0], "H7")

    def test_a_new_process_keeps_the_limit_of_the_early_warning(self):
        s = Scenario()
        s.warm_up()

        def warned(decisions: list) -> bool:
            return any(d.key == (CWS1, "R1 warning")
                       for d in alerts(decisions))

        s.cycle(fail=[CWS1])
        s.cycle(fail=[CWS1])
        self.assertTrue(warned(s.cycle(fail=[CWS1])))
        s.cycle(fail=[CWS1])
        self.assertTrue(
            held(s.cycle(fail=[CWS1], supervisor_ok=False), CWS1))
        # The operator restarts it by hand; the new process hangs too.
        s.set(CWS1, pid=999, start_wall=s.wall + CYCLE_S)
        history = [s.cycle(fail=[CWS1]) for _ in range(5)]
        # Its third miss comes 75 s after the old process's early
        # warning: that limit is the program's, whatever its process.
        self.assertEqual([warned(d) for d in history], [False] * 5)
        # Its trip is entering a hold: the first held line goes out at
        # once, though the old process's went out 75 s before.
        self.assertEqual([bool(held(d, CWS1)) for d in history],
                         [False] * 4 + [True])
        self.assertEqual(held(history[4], CWS1)[0].holds, ("H6",))

    def test_an_incident_that_resolves_ends_the_episode(self):
        s = self.episode()
        self.assertIn((CWS1, "R1"), s.state.held_announced)
        s.cycle()
        [resolved] = [d for d in s.cycle() if d.text.startswith("Resolved:")]
        self.assertIn("passes its checks again", resolved.text)
        self.assertEqual(s.state.held_announced, {})


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

    def test_a_backend_opened_within_the_margin_is_not_an_orphan(self):
        # The new process started 50 s before the cycle. A backend of
        # the process that opened up to GATE_KILL_S earlier cannot be
        # told from one the new process opened as it started: the
        # snapshot's ages are measured a little after the cycle starts.
        self.assertNotIn("orphan", self.blocker_text(50 + GATE_KILL_S - 1))

    def test_a_backend_opened_before_the_margin_is_an_orphan(self):
        self.assertIn("orphan of pid 99",
                      self.blocker_text(50 + GATE_KILL_S + 1))


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
        # The budget is exhausted: the first H7 headline of the hold
        # goes out at once, though H6 headed a line 105 s before.
        [alert] = held(history[15], CWS1)
        self.assertEqual(alert.holds[0], "H7")
        self.assertIn("(in the shadow ledger)", alert.text)
        self.assertEqual(s.state.ledger.real, [])
        self.assertEqual(s.state.ledger.count(CWS1, RESTART, True, s.wall),
                         2)

    def test_a_flapping_headline_is_rate_limited(self):
        s = Scenario()
        s.warm_up()
        for wall in (s.wall - 600, s.wall - 300):
            book(s, CWS1, "cmsContestWebServer 1", wall, 109)
        # ES and LogService fail every other cycle: CWS1 is held by H5
        # in those, and by H7 (its budget) in the others.
        headlines = []
        emitted = []
        for cycle in range(4 + 2 + int(ALERT_REPEAT_S / CYCLE_S)):
            failing = [CWS1, ES, LS] if cycle % 2 == 0 else [CWS1]
            decisions = s.cycle(fail=failing)
            if cycle >= 4:
                emitted.append(bool(held(decisions, CWS1)))
                headlines.append(
                    s.state.held_headline[(CWS1, "R1")])
        self.assertEqual(headlines[:3], ["H5", "H7", "H5"])
        # The first H7 goes out at once (the budget is exhausted); the
        # flapping after it gives one line per 5 minutes, not per cycle.
        self.assertEqual(emitted, [True, True] + [False] * 19 + [True])

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
