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

"""Tests for cmsWatchdog's policy: the section and Worker rules."""

import unittest

from cms.service.EvaluationService import EvaluationService
from cms.watchdog.policy import ALERT_REPEAT_S, BUDGET_WINDOW_S, CYCLE_S, \
    INVALIDATION_SECTION, OBSERVE, PASSES_TO_RESOLVE, RESTART, \
    SERVICES_POOL, WORKERS_POOL, Backend, Booking, Gate, Probe, hhmm_utc
from cmstestsuite.unit_tests.watchdog.scenario import DEFAULT_PROGRAMS, \
    ES, GREEN, HOLDER, PS, SS, W0, W1, Scenario, actions, alerts, busy, \
    held, lock_wait_snapshot, program, worker


RANKINGS_OK = {"http://localhost:8890/": Probe(True, 0.01)}
RANKINGS_DOWN = {"http://localhost:8890/": Probe(False, None, "refused")}


def es_busy(age_s: float, idle_s: float = 70.0,
            what: str = "post_finish_lock:_write_results_sync") -> dict:
    return busy(104, what, age_s, idle_s)


class TestEvaluationServiceSection(unittest.TestCase):

    def run_es(self, s: Scenario, ages: list[float], idle_s: float = 70.0,
               cpus: list[float] | None = None,
               advances: list[float] | None = None,
               gate: Gate = Gate(True)) -> list[list]:
        """Run one cycle per age; return the decisions of each."""
        history = []
        for index, age_s in enumerate(ages):
            history.append(s.cycle(
                health={ES: es_busy(age_s, idle_s)},
                holder_cpu=(HOLDER, cpus[index] if cpus else 5.0),
                advance=advances[index] if advances else 15.0, gate=gate))
        return history

    def test_it_trips_past_180_s_and_not_before(self):
        s = Scenario()
        s.warm_up()
        history = self.run_es(s, [105, 120, 135, 150, 165, 180, 195])
        self.assertEqual([bool(actions(d)) for d in history],
                         [False] * 6 + [True])
        [restart] = actions(history[6])
        self.assertEqual((restart.program, restart.rule, restart.action),
                         (ES, "R3", RESTART))
        self.assertIn("post_finish_lock:_write_results_sync",
                      restart.text)

    def test_a_section_that_issues_statements_never_trips(self):
        s = Scenario()
        s.warm_up()
        history = self.run_es(s, [200 + 15 * i for i in range(30)],
                              idle_s=5.0)
        self.assertEqual([d for d in history if actions(d)], [])

    def test_a_holder_that_uses_the_cpu_never_trips(self):
        s = Scenario()
        s.warm_up()
        history = self.run_es(s, [200 + 15 * i for i in range(10)],
                              cpus=[5.0 + 0.3 * i for i in range(10)])
        self.assertEqual([d for d in history if actions(d)], [])

    def test_an_active_statement_holds_the_trip_off(self):
        s = Scenario()
        s.warm_up()
        running = Gate(True, backends=(Backend(
            pid=500, application_name="cmsEvaluationService 0",
            state="active"),))
        history = self.run_es(s, [200 + 15 * i for i in range(8)],
                              gate=running)
        self.assertEqual([d for d in history if actions(d)], [])

    def test_a_write_to_a_client_that_does_not_read_does_not(self):
        s = Scenario()
        s.warm_up()
        writing = Gate(True, backends=(Backend(
            pid=500, application_name="cmsEvaluationService 0",
            state="active", wait_event_type="Client",
            wait_event="ClientWrite"),))
        history = self.run_es(s, [200 + 15 * i for i in range(5)],
                              gate=writing)
        self.assertEqual([bool(actions(d)) for d in history],
                         [False] * 4 + [True])

    def test_a_lock_wait_trips_and_is_held_by_h3(self):
        s = Scenario()
        s.warm_up()
        history = self.run_es(s, [200 + 15 * i for i in range(5)],
                              gate=Gate(True, backends=lock_wait_snapshot()))
        self.assertEqual([d for d in history if actions(d)], [])
        [alert] = held(history[4], ES)
        self.assertEqual(alert.holds[0], "H3")
        self.assertIn("fails R3", alert.text)
        self.assertIn("pid 900 ('psql'", alert.text)

    def test_the_trip_does_not_count_a_lock_wait_as_running(self):
        s = Scenario()
        s.warm_up()
        history = self.run_es(s, [200 + 15 * i for i in range(5)],
                              gate=Gate(True, backends=lock_wait_snapshot()))
        [alert] = held(history[4], ES)
        self.assertIn(
            "cmsEvaluationService 0 fails R3 (its locked section "
            "post_finish_lock:_write_results_sync has been held for 260 s "
            "with no statement for 70 s, under 1 s of CPU in the last "
            "minute and no statement of it running in the database, "
            "apart from a lock wait) but is held by H3: a database lock "
            "wait: 'cmsEvaluationService 0' (pid 500): its statement has "
            "been waiting 45 s on a lock", alert.text)

    def test_a_warning_says_when_the_section_trips(self):
        s = Scenario(mode=OBSERVE)
        s.warm_up()
        [warning] = [d for d in s.cycle(health={ES: es_busy(100.0, 40.0)})
                     if d.key == (ES, "R3 warning")]
        self.assertEqual(
            warning.text,
            "Watchdog: early warning: the locked section "
            "post_finish_lock:_write_results_sync of cmsEvaluationService "
            "0 has been held for 100 s with no statement for 40 s. Past "
            "180 s, with no statement for 60 s, no CPU used by its thread "
            "and no statement of it running in the database apart from a "
            "lock wait, the watchdog only logs the restart it would do "
            "(observe mode). Check ./logs.sh for its log; cmsWatchdog "
            "--snapshot cmsevaluationservice writes its stacks.")

    def test_the_warning_starts_past_90_s_with_30_s_idle(self):
        for age_s, idle_s, warned in ((90.0, 100.0, False),
                                      (91.0, 30.0, True),
                                      (91.0, 29.9, False)):
            with self.subTest(age_s=age_s, idle_s=idle_s):
                s = Scenario()
                s.warm_up()
                decisions = s.cycle(health={ES: es_busy(age_s, idle_s)})
                self.assertEqual([d.key for d in alerts(decisions)],
                                 [(ES, "R3 warning")] if warned else [])

    def test_it_trips_at_60_s_with_no_statement_exactly(self):
        for idle_s, tripped in ((59.9, False), (60.0, True)):
            with self.subTest(idle_s=idle_s):
                s = Scenario()
                s.warm_up()
                # The fifth cycle is the first with a CPU sample 60 s
                # old: the first one that can trip.
                history = self.run_es(s, [200 + 15 * i for i in range(5)],
                                      idle_s=idle_s)
                self.assertEqual([bool(actions(d)) for d in history],
                                 [False] * 4 + [tripped])

    def test_the_cpu_lookback_takes_the_newest_sample_60_s_old(self):
        s = Scenario()
        s.warm_up()
        # 2 s of CPU between the first two samples, none after: only
        # the newest sample that is 60 s old (the second) shows a holder
        # with no CPU. The first one is 59.9 s old at the fifth cycle.
        history = self.run_es(
            s, [200, 215, 230, 245, 260, 275],
            cpus=[5.0, 7.0, 7.0, 7.0, 7.0, 7.0],
            advances=[15.0, 14.9, 15.0, 15.0, 15.0, 15.2])
        self.assertEqual([bool(actions(d)) for d in history],
                         [False] * 5 + [True])

    def test_a_re_evaluation_only_alerts_every_5_minutes(self):
        s = Scenario()
        s.warm_up()
        lines = []
        for index in range(21):
            decisions = s.cycle(health={ES: es_busy(
                200 + 15 * index, 300.0, what=INVALIDATION_SECTION)},
                holder_cpu=(HOLDER, 5.0))
            self.assertEqual(actions(decisions), [])
            lines.append([d.text for d in alerts(decisions)
                          if d.key == (ES, "re-evaluation")])
        self.assertIn("re-evaluation still running for 3 min",
                      lines[0][0])
        self.assertEqual([bool(line) for line in lines],
                         [True] + [False] * 19 + [True])

    def test_the_invalidation_section_is_the_es_method(self):
        self.assertEqual(
            INVALIDATION_SECTION, "post_finish_lock:"
            + EvaluationService._invalidate_submission_sync.__name__)

    def test_an_r3_restart_ends_every_hold_episode_of_the_program(self):
        s = Scenario()
        s.warm_up()
        # ES stops answering; its R2 trip is held by H1.
        for index in range(5):
            s.cycle(fail=[ES], supervisor_ok=index < 4,
                    holder_cpu=(HOLDER, 5.0))
        self.assertEqual(s.state.held_headline[(ES, "R2")], "H1")
        # It answers again, stuck in its locked section: R3 restarts it,
        # and the restart ends its R2 hold episode too.
        [restart] = actions(s.cycle(health={ES: es_busy(400.0, 300.0)},
                                    holder_cpu=(HOLDER, 5.0)))
        self.assertEqual((restart.program, restart.rule), (ES, "R3"))
        self.assertEqual([key for key in s.state.held_headline
                          if key[0] == ES], [])


class TestScoringAndProxySections(unittest.TestCase):

    def test_a_section_warning_says_when_it_trips_and_what_to_do(self):
        s = Scenario(mode=OBSERVE)
        s.warm_up()
        [warning] = [d for d in s.cycle(health={
            SS: busy(103, "score", 100.0, 100.0)})
            if d.key == (SS, "R3 warning")]
        self.assertEqual(
            warning.text,
            "Watchdog: early warning: one scoring of cmsScoringService 0 "
            "has run for 100 s. Past 180 s, the watchdog only logs the "
            "restart it would do (observe mode). Check ./logs.sh for its "
            "log; cmsWatchdog --snapshot cmsscoringservice writes its "
            "stacks.")

    def test_a_send_warning_says_when_it_trips(self):
        s = Scenario(mode=OBSERVE)
        s.warm_up()
        [warning] = [d for d in s.cycle(
            health={PS: busy(107, "send", 500.0, 100.0)},
            rankings=RANKINGS_OK) if d.key == (PS, "R3 warning")]
        self.assertEqual(
            warning.text,
            "Watchdog: early warning: a send of cmsProxyService 0 to the "
            "rankings has started no request for 100 s. Past 180 s with no "
            "request, while every ranking answers, the watchdog only logs "
            "the restart it would do (observe mode). Check ./logs.sh for "
            "its log; cmsWatchdog --snapshot cmsproxyservice writes its "
            "stacks.")

    def test_ss_trips_past_180_s(self):
        s = Scenario()
        s.warm_up()
        warning = s.cycle(health={SS: busy(103, "score", 100.0, 100.0)})
        self.assertEqual([d.key for d in alerts(warning)],
                         [(SS, "R3 warning")])
        self.assertEqual(
            actions(s.cycle(health={SS: busy(103, "score", 180.0, 180)})),
            [])
        [restart] = actions(
            s.cycle(health={SS: busy(103, "score", 195.0, 195.0)}))
        self.assertEqual((restart.program, restart.rule), (SS, "R3"))

    def test_a_send_that_keeps_starting_requests_never_trips(self):
        s = Scenario()
        s.warm_up()
        for index in range(20):
            decisions = s.cycle(
                health={PS: busy(107, "send", 1000.0 + 15 * index, 5.0)},
                rankings=RANKINGS_OK)
            self.assertEqual(actions(decisions), [])

    def test_a_send_idle_past_180_s_trips_while_the_rankings_answer(self):
        s = Scenario()
        s.warm_up()
        self.assertEqual(actions(s.cycle(
            health={PS: busy(107, "send", 500.0, 180.0)},
            rankings=RANKINGS_OK)), [])
        self.assertEqual(actions(s.cycle(
            health={PS: busy(107, "send", 515.0, 195.0)},
            rankings=RANKINGS_DOWN)), [])
        [restart] = actions(s.cycle(
            health={PS: busy(107, "send", 530.0, 210.0)},
            rankings=RANKINGS_OK))
        self.assertEqual((restart.program, restart.rule), (PS, "R3"))

    def test_a_send_with_no_ranking_to_probe_never_trips(self):
        s = Scenario()
        s.warm_up()
        for index in range(5):
            decisions = s.cycle(health={PS: busy(
                107, "send", 500.0 + 15 * index, 195.0 + 15 * index)})
            self.assertEqual(actions(decisions), [])
            self.assertEqual(held(decisions, PS), [])

    def test_a_cycle_where_r3_cannot_judge_a_send_is_no_pass(self):
        for rankings in (RANKINGS_DOWN, {}):
            with self.subTest(rankings=rankings):
                s = Scenario()
                s.warm_up()
                [restart] = actions(s.cycle(
                    health={PS: busy(107, "send", 530.0, 210.0)},
                    rankings=RANKINGS_OK))
                self.assertEqual((restart.program, restart.rule),
                                 (PS, "R3"))
                # The send is still stuck, and with no ranking that
                # answers R3 cannot tell why: no pass of the incident.
                for index in range(PASSES_TO_RESOLVE):
                    decisions = s.cycle(health={PS: busy(
                        107, "send", 545.0 + 15 * index,
                        225.0 + 15 * index)}, rankings=rankings)
                    self.assertEqual(
                        [d.text for d in decisions
                         if d.text.startswith("Resolved:")], [])
                self.assertEqual(s.state.ledger.incidents[PS]["passes"],
                                 0)

    def test_h4_counts_a_section_past_half_its_trip_only(self):
        for idle_s, restarted in ((100.0, False), (5.0, True)):
            with self.subTest(idle_s=idle_s):
                s = Scenario()
                s.warm_up()
                for _ in range(4):
                    s.cycle(fail=[ES], rankings=RANKINGS_OK, health={
                        PS: busy(107, "send", 2000.0, idle_s)})
                decisions = s.cycle(fail=[ES], rankings=RANKINGS_OK,
                                    health={PS: busy(107, "send", 2000.0,
                                                     idle_s)})
                self.assertEqual(bool(actions(decisions)), restarted)
                if not restarted:
                    [alert] = held(decisions, ES)
                    self.assertEqual(alert.holds[0], "H4")

    def test_an_es_section_that_issues_statements_is_not_correlated(self):
        s = Scenario()
        s.warm_up()
        for _ in range(4):
            s.cycle(fail=[SS], health={ES: es_busy(1000.0, 5.0)})
        [restart] = actions(s.cycle(fail=[SS],
                                    health={ES: es_busy(1000.0, 5.0)}))
        self.assertEqual(restart.program, SS)

    def test_a_malformed_health_payload_is_no_section(self):
        s = Scenario()
        s.warm_up()
        for payload in ({}, {"busy": "x"}, {"busy": [3, None]},
                        {"busy": [{"what": 3, "age_s": 999,
                                   "idle_s": 999}]},
                        {"busy": [{"what": "post_finish_lock:x",
                                   "age_s": "999", "idle_s": 999}]},
                        {"pid": "104", "busy": [{"what": "score"}]}):
            with self.subTest(payload=payload):
                decisions = s.cycle(health={ES: payload, SS: payload,
                                            PS: payload})
                self.assertEqual(actions(decisions), [])
                self.assertEqual([d.key for d in alerts(decisions)
                                  if d.key[1] in ("pid", "R3 warning")],
                                 [])


class TestWorkerRule(unittest.TestCase):

    def test_disabled_for_180_s_trips(self):
        s = Scenario()
        s.warm_up()
        late = {"0": worker(False, s.wall + 15 - 179), "1": worker(True)}
        self.assertEqual(actions(s.cycle(workers=late)), [])
        [restart] = actions(s.cycle(workers={
            "0": worker(False, s.wall + 15 - 180), "1": worker(True)}))
        self.assertEqual((restart.program, restart.rule), (W0, "R4"))
        self.assertIn("disabled it by timeout 180 s ago", restart.text)

    def test_a_worker_that_restarted_after_it_was_disabled_is_spared(self):
        s = Scenario()
        s.warm_up()
        # It started 150 s ago, after ES disabled it 200 s ago: past its
        # own grace (H6), which would hold a trip, and short of the
        # 300 s of the unconnected rule.
        s.set(W0, start_wall=s.wall + 15 - 150, pid=905)
        decisions = s.cycle(workers={
            "0": worker(False, s.wall + 15 - 200), "1": worker(True)})
        self.assertEqual(actions(decisions), [])
        self.assertEqual(held(decisions, W0), [])
        self.assertNotIn(W0, s.state.ledger.incidents)

    def test_a_worker_that_never_connects_trips(self):
        s = Scenario()
        s.warm_up()
        [restart] = actions(s.cycle(workers={"0": worker(False),
                                             "1": worker(True)}))
        self.assertEqual((restart.program, restart.rule), (W0, "R4"))

    def test_the_unconnected_rule_needs_es_up_and_another_worker(self):
        cases = (
            ({"0": worker(False), "1": worker(False)}, 9_999.0),
            ({"0": worker(False), "1": worker(True)}, 100.0),
        )
        for workers, uptime in cases:
            with self.subTest(workers=workers, uptime=uptime):
                s = Scenario()
                s.warm_up()
                health = {ES: {"pid": 104, "uptime_s": uptime,
                               "busy": []}}
                decisions = s.cycle(workers=workers, health=health)
                # No trip at all: H4 would hold two Workers that trip
                # together, with no action either.
                self.assertEqual(actions(decisions), [])
                self.assertEqual(alerts(decisions), [])
                for name in (W0, W1):
                    self.assertNotIn(name, s.state.ledger.incidents)

    def test_the_unconnected_rule_needs_es_up_for_120_s(self):
        for uptime, expected in ((119.9, []), (120.0, [(W0, "R4")])):
            with self.subTest(uptime=uptime):
                s = Scenario()
                s.warm_up()
                decisions = actions(s.cycle(
                    workers={"0": worker(False), "1": worker(True)},
                    health={ES: {"pid": 104, "uptime_s": uptime,
                                 "busy": []}}))
                self.assertEqual(
                    [(d.program, d.rule) for d in decisions], expected)

    def test_the_unconnected_rule_trips_at_300_s_of_running(self):
        for running_s, expected in ((299, []), (300, [(W0, "R4")])):
            with self.subTest(running_s=running_s):
                s = Scenario()
                s.warm_up()
                s.set(W0, start_wall=s.wall + 15 - running_s, pid=905)
                decisions = actions(s.cycle(
                    workers={"0": worker(False), "1": worker(True)}))
                self.assertEqual(
                    [(d.program, d.rule) for d in decisions], expected)

    def test_an_unhealthy_es_suppresses_the_rule(self):
        s = Scenario()
        s.warm_up()
        decisions = s.cycle(fail=[ES], workers={
            "0": worker(False, s.wall + 15 - 600), "1": worker(True)})
        self.assertEqual([d for d in actions(decisions)
                          if d.program == W0], [])

    def test_two_workers_tripping_together_give_one_alert(self):
        s = Scenario()
        s.warm_up()
        decisions = s.cycle(workers={
            "0": worker(False, s.wall + 15 - 600),
            "1": worker(False, s.wall + 15 - 600)})
        self.assertEqual(actions(decisions), [])
        [alert] = held(decisions, "workers")
        self.assertIn("2 Workers trip R4 together (cmsWorker 0, "
                      "cmsWorker 1)", alert.text)

    def test_the_workers_pool_is_separate(self):
        s = Scenario()
        s.warm_up()
        for index, name in enumerate(("cmslogservice", "cmsscoringservice",
                                      "cmsproxyservice")):
            s.state.ledger.book(Booking(
                s.wall - 100 + index, name, name, SERVICES_POOL, RESTART,
                "R2", False, 1), False)
        [restart] = actions(s.cycle(workers={"0": worker(False),
                                             "1": worker(True)}))
        self.assertEqual(restart.program, W0)


class TestWorkersEpisode(unittest.TestCase):
    """The one line for the Workers that one hold holds together."""

    KEY = ("workers", "R4")

    @staticmethod
    def both(s: Scenario) -> dict:
        """Return ES's status of both Workers, disabled 600 s before."""
        disabled_at = s.wall + CYCLE_S - 600
        return {"0": worker(False, disabled_at),
                "1": worker(False, disabled_at)}

    @staticmethod
    def alone(s: Scenario) -> dict:
        """Return ES's status of Worker 0 disabled, Worker 1 fine."""
        return {"0": worker(False, s.wall + CYCLE_S - 600),
                "1": worker(True)}

    def grouped(self, decisions: list) -> bool:
        return any(d.key == self.KEY for d in decisions)

    @staticmethod
    def fourteen_workers() -> tuple[Scenario, list]:
        """Return a warmed-up Scenario with 14 Workers, and their facts."""
        workers = [program("cmsworker%d" % shard, "Worker", shard,
                           300 + shard) for shard in range(14)]
        s = Scenario(programs=[facts for facts in DEFAULT_PROGRAMS
                               if facts.kind != "Worker"] + workers)
        s.warm_up()
        return s, workers

    @staticmethod
    def use_up_budgets(s: Scenario) -> None:
        """Book 2 restarts of each Worker 100 s ago: H7, and no H4."""
        for name, label in ((W0, "cmsWorker 0"), (W1, "cmsWorker 1")):
            for _ in range(2):
                s.state.ledger.book(Booking(
                    s.wall - 100, name, label, WORKERS_POOL, RESTART, "R4",
                    False, 1), False)

    def test_a_second_episode_is_entering_a_hold_again(self):
        s = Scenario()
        s.warm_up()
        [line] = [d for d in s.cycle(workers=self.both(s))
                  if d.key == self.KEY]
        self.assertEqual(line.holds, ("H4",))
        # The same episode: the rate limit applies.
        self.assertFalse(self.grouped(s.cycle(workers=self.both(s))))
        # The Workers recover: the episode ends after 2 quiet cycles.
        s.cycle()
        self.assertIn(self.KEY, s.state.held_headline)
        s.cycle()
        for table in (s.state.held_headline, s.state.alert_sent):
            self.assertNotIn(self.KEY, table)
        # A new episode, 60 s after the first line, is a state change.
        [line] = [d for d in s.cycle(workers=self.both(s))
                  if d.key == self.KEY]
        self.assertEqual(line.holds, ("H4",))

    def test_another_hold_heading_the_group_does_not_end_the_episode(self):
        s = Scenario()
        s.warm_up()
        lock = Gate(True, backends=lock_wait_snapshot())
        # Both Workers trip in every cycle; a lock wait comes and goes,
        # so H3 heads their trips every other cycle and H4 the others.
        history = [s.cycle(workers=self.both(s),
                           gate=lock if cycle % 2 else GREEN)
                   for cycle in range(int(ALERT_REPEAT_S / CYCLE_S) + 2)]
        self.assertEqual(held(history[1], W0), [])
        self.assertEqual(
            [line.holds[0] for line in held(history[1], "workers")],
            ["H3"])
        # The first H3 headline goes out at once (an urgent hold); the
        # other changes of headline wait for the rate limit.
        self.assertEqual([self.grouped(d) for d in history],
                         [True, True] + [False] * 19 + [True])

    def test_a_trip_that_comes_and_goes_does_not_end_the_episode(self):
        s = Scenario()
        s.warm_up()
        history = [s.cycle(workers=self.both(s) if cycle % 2 == 0 else None)
                   for cycle in range(int(ALERT_REPEAT_S / CYCLE_S) + 1)]
        self.assertEqual([self.grouped(d) for d in history],
                         [True] + [False] * 19 + [True])

    def test_a_cycle_where_r4_cannot_judge_is_neither_quiet_nor_a_pass(
            self):
        def es_does_not_answer(s: Scenario) -> list:
            return s.cycle(fail=[ES])

        def es_is_stopped(s: Scenario) -> list:
            s.set(ES, state="STOPPED", pid=None)
            return s.cycle()

        def no_status_of_the_workers(s: Scenario) -> list:
            return s.cycle(workers={})

        for blind in (es_does_not_answer, es_is_stopped,
                      no_status_of_the_workers):
            with self.subTest(blind=blind.__name__):
                s = Scenario()
                s.warm_up()
                history = [s.cycle(workers=self.both(s))]
                history += [blind(s) for _ in range(PASSES_TO_RESOLVE)]
                s.set(ES, state="RUNNING", pid=104)
                # ES sees the Workers again, still disabled: the same
                # episode, and the same incidents.
                history.append(s.cycle(workers=self.both(s)))
                self.assertEqual([self.grouped(d) for d in history],
                                 [True, False, False, False])
                self.assertEqual(
                    [d.text for decisions in history for d in decisions
                     if d.text.startswith("Resolved:")], [])
                for name in (W0, W1):
                    self.assertIn(name, s.state.ledger.incidents)

    def test_a_young_es_cannot_judge_the_workers(self):
        s = Scenario()
        s.warm_up()
        self.assertTrue(self.grouped(s.cycle(workers=self.both(s))))
        # ES restarts: it has disabled no Worker yet, and R4 (b) needs
        # it up for 120 s, so for now R4 cannot judge them.
        s.set(ES, pid=777)
        history = [s.cycle(
            health={ES: {"pid": 777, "uptime_s": 15.0 + 15 * index,
                         "busy": []}},
            workers={"0": worker(False), "1": worker(False)})
            for index in range(3)]
        self.assertEqual([d.text for decisions in history for d in decisions
                          if d.text.startswith("Resolved:")], [])
        for name in (W0, W1):
            self.assertIn(name, s.state.ledger.incidents)
        self.assertIn(self.KEY, s.state.held_headline)

    def test_a_worker_that_trips_alone_does_not_keep_the_episode(self):
        s = Scenario()
        s.warm_up()
        self.assertTrue(self.grouped(s.cycle(workers=self.both(s))))
        # Worker 1 recovers; Worker 0 trips alone: H4 does not apply to
        # it, so it is restarted, and these cycles are quiet ones.
        [restart] = actions(s.cycle(workers=self.alone(s)))
        self.assertEqual(restart.program, W0)
        self.assertIn(self.KEY, s.state.held_headline)
        s.cycle(workers=self.alone(s))
        self.assertNotIn(self.KEY, s.state.held_headline)

    def test_workers_held_by_one_hold_share_one_line(self):
        s, workers = self.fourteen_workers()
        lock = Gate(True, backends=lock_wait_snapshot())
        names = {facts.name for facts in workers} | {"workers"}
        lines = []
        for _ in range(int(ALERT_REPEAT_S / CYCLE_S) + 1):
            disabled_at = s.wall + CYCLE_S - 600
            decisions = s.cycle(gate=lock, workers={
                str(shard): worker(False, disabled_at)
                for shard in range(14)})
            self.assertEqual(actions(decisions), [])
            lines.append([d for d in alerts(decisions)
                          if d.key[0] in names])
        self.assertEqual([len(cycle) for cycle in lines],
                         [1] + [0] * 19 + [1])
        [line] = lines[0]
        self.assertEqual((line.key, line.holds),
                         (("workers", "R4"), ("H3", "H4")))
        self.assertTrue(line.text.startswith(
            "Watchdog: 14 Workers trip R4 together (cmsWorker 0, "
            "cmsWorker 1, cmsWorker 2, and 11 more) and are held by H3: "
            "a database lock wait: 'cmsEvaluationService 0' (pid 500): "
            "its statement has been waiting 45 s on a lock held by pid "
            "900 ('psql'"), line.text)
        self.assertTrue(line.text.endswith(
            "Ending a blocker is a human decision. Also: H4. End the "
            "blocker by hand only if it is stuck (see \"When the watchdog "
            "alerts\")."), line.text)

    def test_the_group_line_stays_open_while_its_episode_lasts(self):
        lock = Gate(True, backends=lock_wait_snapshot())
        for headline, gate in (("H4", GREEN), ("H3", lock)):
            with self.subTest(headline=headline):
                s = Scenario()
                s.warm_up()
                s.cycle(workers=self.both(s), gate=gate)
                self.assertEqual(s.state.open_alerts[self.KEY].holds[0],
                                 headline)
                # The Workers recover: the episode ends after 2 cycles.
                s.cycle()
                self.assertIn(self.KEY, s.state.open_alerts)
                s.cycle()
                self.assertNotIn(self.KEY, s.state.open_alerts)

    def test_a_new_headline_waits_unless_it_is_a_first_urgent_one(self):
        s = Scenario()
        s.warm_up()
        lock = Gate(True, backends=lock_wait_snapshot())

        def lines(**kwargs) -> list[str]:
            """Return the headlines of the Workers' lines of a cycle."""
            return [d.holds[0] for d in held(
                s.cycle(workers=self.both(s), **kwargs), "workers")]

        self.assertEqual(lines(), ["H4"])
        # A one-cycle blip of supervisord: the same episode.
        self.assertEqual(lines(supervisor_ok=False), [])
        self.assertEqual(lines(), [])
        # The first lock wait: H3 is urgent, its first line goes out.
        self.assertEqual(lines(gate=lock), ["H3"])
        self.assertEqual(lines(), [])
        self.assertEqual(lines(gate=lock), [])
        # Both Workers use their budget up: so is H7's first line.
        self.use_up_budgets(s)
        self.assertEqual(lines(), ["H7"])
        self.assertEqual(lines(), [])
        self.assertEqual(list(s.state.held_headline), [self.KEY])

    def test_a_budget_group_line_is_about_several_workers(self):
        s = Scenario()
        s.warm_up()
        self.use_up_budgets(s)
        free = hhmm_utc(s.wall - 100 + BUDGET_WINDOW_S)
        why = ("at most 2 restarts of a program, and 3 of its pool, in 60 "
               "minutes")
        [line] = held(s.cycle(workers=self.both(s)), "workers")
        self.assertEqual(
            line.text,
            "Watchdog: 2 Workers trip R4 together (cmsWorker 0, cmsWorker "
            "1) and are held by H7: will not restart cmsWorker 0 before "
            "%s: %s; will not restart cmsWorker 1 before %s: %s. Restart "
            "them by hand if needed, after cmsWatchdog --snapshot."
            % (free, why, free, why))

    def test_a_grace_group_line_names_the_workers(self):
        s = Scenario()
        s.warm_up()
        self.use_up_budgets(s)
        s.state.programs[W0].last_action_mono = s.mono - CYCLE_S
        s.state.programs[W1].last_action_mono = s.mono
        [line] = held(s.cycle(workers=self.both(s)), "workers")
        self.assertEqual(
            line.text,
            "Watchdog: 2 Workers trip R4 together (cmsWorker 0, cmsWorker "
            "1) and are held by H6: the watchdog's last action or "
            "would-action on cmsWorker 0 was 30 s ago; the watchdog's last "
            "action or would-action on cmsWorker 1 was 15 s ago. Also: H7. "
            "The watchdog judges each again 120 s after its last action or "
            "would-action on it.")

    def test_a_grace_group_line_in_observe_mode_is_left_to_you(self):
        s = Scenario(mode=OBSERVE)
        s.warm_up()
        self.use_up_budgets(s)
        s.state.programs[W0].last_action_mono = s.mono - CYCLE_S
        s.state.programs[W1].last_action_mono = s.mono
        [line] = held(s.cycle(workers=self.both(s)), "workers")
        self.assertEqual(line.holds, ("H6", "H7"))
        self.assertTrue(line.text.endswith(
            "on it. It restarts nothing here: decide by hand, after "
            "cmsWatchdog --snapshot."), line.text)

    def test_workers_that_recover_together_share_one_resolved_line(self):
        s, workers = self.fourteen_workers()
        disabled_at = s.wall + CYCLE_S - 600
        s.cycle(workers={str(shard): worker(False, disabled_at)
                         for shard in range(14)})
        opened = s.wall
        s.cycle()
        lines = [d for d in s.cycle() if d.text.startswith("Resolved:")]
        self.assertEqual(
            [(line.key, line.text) for line in lines],
            [(("workers", "resolved"),
              "Resolved: 14 Workers pass their checks again: cmsWorker 0, "
              "cmsWorker 1, cmsWorker 2, and 11 more (incidents of R4, the "
              "first opened at %s)." % hhmm_utc(opened))])
        self.assertEqual(s.state.ledger.incidents, {})

    def test_a_resolved_line_of_workers_closes_each_one_s_alerts(self):
        s = Scenario()
        s.warm_up()
        # Worker 0 trips alone and is restarted, and goes on failing
        # alone (held by H6) into the next minute; then both trip, held
        # together; then both pass twice, and resolve in one cycle.
        [restart] = actions(s.cycle(workers=self.alone(s)))
        self.assertEqual(restart.program, W0)
        opened = s.wall
        for _ in range(3):
            s.cycle(workers=self.alone(s))
        self.assertEqual(s.state.held_headline[(W0, "R4")], "H6")
        self.assertTrue(self.grouped(s.cycle(workers=self.both(s))))
        self.assertNotEqual(hhmm_utc(opened), hhmm_utc(s.wall))
        s.cycle()
        [line] = [d for d in s.cycle() if d.text.startswith("Resolved:")]
        self.assertEqual(
            line.text,
            "Resolved: 2 Workers pass their checks again: cmsWorker 0, "
            "cmsWorker 1 (incidents of R4, the first opened at %s)."
            % hhmm_utc(opened))
        for table in (s.state.alert_sent, s.state.open_alerts,
                      s.state.held_headline):
            self.assertEqual([key for key in table if key[0] in (W0, W1)],
                             [])

    def test_a_worker_that_recovers_alone_keeps_its_own_line(self):
        s = Scenario()
        s.warm_up()
        self.assertEqual(len(actions(s.cycle(workers=self.alone(s)))), 1)
        opened = s.wall
        s.cycle()
        [line] = [d for d in s.cycle() if d.text.startswith("Resolved:")]
        self.assertEqual(
            (line.key, line.text),
            ((W0, "resolved"), "Resolved: cmsWorker 0 passes its checks "
             "again (incident of R4, opened at %s)." % hhmm_utc(opened)))

    def test_a_worker_held_alone_keeps_its_own_line(self):
        s = Scenario()
        s.warm_up()
        decisions = s.cycle(workers=self.alone(s),
                            gate=Gate(True, backends=lock_wait_snapshot()))
        self.assertEqual([line.holds[0] for line in held(decisions, W0)],
                         ["H3"])
        self.assertEqual(held(decisions, "workers"), [])


if __name__ == "__main__":
    unittest.main()
