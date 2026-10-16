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

"""Tests for cmsWatchdog's policy: FATAL starts, rankings, the lines
that follow a restart, the hourly summary and the status."""

import unittest

from cms.watchdog.policy import BUDGET_WINDOW_S, CYCLE_S, \
    INVALIDATION_SECTION, OBSERVE, RESTART, START, Gate, NearMiss, Probe, \
    program_status
from cmstestsuite.unit_tests.watchdog.scenario import AWS, CWS0, CWS1, \
    ES, LS, PS, RED, RS, SS, W0, W1, Scenario, actions, alerts, busy, \
    lock_wait_snapshot, worker


# The key of the line of R5's held starts: the batch's.
HELD_STARTS = ("-", "R5")
# The key of the line of the FATAL programs left to a human.
LEFT_TO_YOU = ("-", "R5 left to you")


def fatal(s: Scenario, *names: str) -> None:
    """Make names FATAL, as if they last started 5 s from now."""
    for name in names:
        s.set(name, state="FATAL", pid=None, start_wall=s.wall + 5)


def outage(s: Scenario, red_cycles: int = 2) -> None:
    """Run red cycles: a database outage."""
    for _ in range(red_cycles):
        s.cycle(gate=RED)


def held_starts(decisions: list) -> list:
    """Return the line of R5's held starts among decisions, if any."""
    return [d for d in alerts(decisions) if d.key == HELD_STARTS]


def left_to_you(decisions: list) -> list:
    """Return the line of the programs left to a human, if any."""
    return [d for d in alerts(decisions) if d.key == LEFT_TO_YOU]


class TestFatalStarts(unittest.TestCase):

    def test_a_start_after_two_green_cycles(self):
        s = Scenario()
        s.warm_up()
        fatal(s, SS)
        outage(s)
        self.assertEqual(actions(s.cycle()), [])
        [start] = actions(s.cycle())
        self.assertEqual(
            (start.action, start.programs, start.rule, start.would,
             start.shadow),
            (START, (SS,), "R5", False, False))
        self.assertIn(SS, s.state.ledger.incidents)

    def test_ten_fatal_programs_start_in_one_batch_infra_first(self):
        s = Scenario()
        s.warm_up()
        fatal(s, LS, RS, SS, ES, W0, W1, PS, CWS0, CWS1, AWS)
        outage(s)
        self.assertEqual(actions(s.cycle()), [])
        [start] = actions(s.cycle())
        self.assertEqual(start.programs,
                         (LS, RS, ES, PS, SS, AWS, CWS0, CWS1, W0, W1))

    def test_a_program_fatal_with_the_database_up_is_left_to_a_human(
            self):
        s = Scenario()
        s.warm_up()
        fatal(s, SS)
        decisions = s.cycle()
        self.assertEqual(actions(decisions), [])
        [alert] = left_to_you(decisions)
        self.assertEqual(alert.level, "ERROR")
        self.assertIn("left to you", alert.text)
        # It says what the watchdog can know, and promises no start.
        self.assertEqual(
            alert.text,
            "Watchdog: cmsScoringService 0 is FATAL, and in the last 60 "
            "minutes the watchdog saw no database outage since it last "
            "started: it is left to you, and R5 acts on it only after a "
            "later database outage. Read ./logs.sh, fix the cause, then "
            "start it with supervisorctl.")
        self.assertEqual(s.state.open_alerts[LEFT_TO_YOU].text, alert.text)

    def test_programs_left_to_you_share_one_line(self):
        s = Scenario()
        s.warm_up()
        fatal(s, W1, W0, CWS1, CWS0, AWS)
        lines = [[d for d in alerts(s.cycle()) if "R5" in d.key[1]]
                 for _ in range(21)]
        # One line at once, then one per ALERT_REPEAT_S (20 cycles).
        self.assertEqual([index for index, line in enumerate(lines)
                          if line], [0, 20])
        self.assertEqual([[d.key for d in line] for line in lines if line],
                         [[LEFT_TO_YOU], [LEFT_TO_YOU]])
        [line] = lines[0]
        self.assertEqual(line.level, "ERROR")
        self.assertEqual(
            line.text,
            "Watchdog: 5 programs are FATAL (cmsAdminWebServer 0, "
            "cmsContestWebServer 0, cmsContestWebServer 1, and 2 more), "
            "and in the last 60 minutes the watchdog saw no database "
            "outage since they last started: they are left to you, and R5 "
            "acts on them only after a later database outage. Read "
            "./logs.sh, fix the cause, then start them with supervisorctl.")
        # Once a human starts them, the line is closed.
        for name, pid in ((W0, 105), (W1, 106), (CWS0, 108), (CWS1, 109),
                          (AWS, 110)):
            s.set(name, state="RUNNING", pid=pid, start_wall=s.wall + 5)
        self.assertEqual(left_to_you(s.cycle()), [])
        self.assertNotIn(LEFT_TO_YOU, s.state.open_alerts)

    def test_a_start_is_left_to_you_once_its_outage_is_forgotten(self):
        # The ledger forgets an outage BUDGET_WINDOW_S after its end: R5
        # no longer starts SS, and the line says so at once, even while
        # the line of the programs left to you is open for another one.
        s = Scenario(mode=OBSERVE)
        s.warm_up()
        fatal(s, SS)
        outage(s)
        # The first green cycle ends the outage.
        end = s.wall + CYCLE_S
        while s.wall + CYCLE_S < end + BUDGET_WINDOW_S - 60:
            s.cycle()
        self.assertIn(HELD_STARTS, s.state.open_alerts)
        fatal(s, CWS0)
        [left] = left_to_you(s.cycle())
        self.assertIn("cmsContestWebServer 0 is FATAL", left.text)
        while s.wall + CYCLE_S < end + BUDGET_WINDOW_S:
            self.assertEqual(left_to_you(s.cycle()), [])
        decisions = s.cycle()
        [left] = left_to_you(decisions)
        self.assertTrue(left.text.startswith(
            "Watchdog: 2 programs are FATAL (cmsScoringService 0, "
            "cmsContestWebServer 0), and in the last 60 minutes"), left.text)
        self.assertEqual(held_starts(decisions), [])
        self.assertNotIn(HELD_STARTS, s.state.open_alerts)
        self.assertEqual(s.state.open_alerts[LEFT_TO_YOU].text, left.text)
        # Its FATAL budget frees 15 s later; it is no longer R5's.
        for _ in range(4):
            self.assertEqual(actions(s.cycle()), [])

    def test_a_red_interval_before_its_last_start_does_not_count(self):
        s = Scenario()
        s.warm_up()
        outage(s)
        for _ in range(5):
            s.cycle()
        fatal(s, SS)
        for _ in range(3):
            self.assertEqual(actions(s.cycle()), [])

    def test_it_ignores_h3_h4_h5_and_the_program_grace(self):
        s = Scenario()
        s.warm_up()
        fatal(s, SS)
        outage(s)
        noisy = {
            "gate": Gate(True, backends=lock_wait_snapshot()),
            "fail": [CWS0, CWS1, LS],
            "health": {ES: busy(104, "post_finish_lock:_x_sync", 100.0,
                                40.0),
                       PS: busy(107, "send", 500.0, 100.0)},
        }
        self.assertEqual(actions(s.cycle(**noisy)), [])
        [start] = actions(s.cycle(**noisy))
        self.assertEqual(start.programs, (SS,))

    def test_it_waits_for_the_watchdog_grace(self):
        s = Scenario()
        fatal(s, SS)
        s.cycle(gate=RED)
        s.cycle()
        history = [s.cycle() for _ in range(7)]
        self.assertEqual([bool(actions(d)) for d in history],
                         [False] * 6 + [True])
        [alert] = held_starts(history[0])
        self.assertIn("held by H6: the watchdog itself started 30 s ago",
                      alert.text)
        # A FATAL program is not starting; whether the watchdog starts
        # it, or only logs that it would, depends on its mode.
        self.assertTrue(alert.text.endswith(
            ". Nothing to do yet: R5 decides once the watchdog's own grace "
            "ends."), alert.text)

    def test_a_held_start_is_a_held_trip(self):
        s = Scenario()
        fatal(s, SS)
        s.cycle(gate=RED)
        s.cycle()
        [alert] = held_starts(s.cycle(supervisor_ok=False))
        self.assertEqual(alert.holds, ("H1", "H6"))
        self.assertEqual(
            alert.text,
            "Watchdog: cmsScoringService 0 fails R5 (FATAL after a "
            "database outage) but is held by H1: supervisord did not "
            "answer in this cycle. Also: H6. Check ./status.sh.")
        self.assertEqual(s.state.ledger.incidents[SS]["rule"], "R5")

    def test_it_has_its_own_budget(self):
        s = Scenario(mode=OBSERVE)
        s.warm_up()
        fatal(s, SS)
        outage(s)
        s.cycle()
        self.assertTrue(actions(s.cycle())[0].would)
        self.assertTrue(actions(s.cycle())[0].would)
        decisions = s.cycle()
        self.assertEqual(actions(decisions), [])
        [alert] = held_starts(decisions)
        self.assertIn("held by H7: will not start cmsScoringService 0",
                      alert.text)
        self.assertEqual(s.state.ledger.count(SS, START, False, s.wall), 2)
        self.assertEqual(s.state.ledger.count(SS, RESTART, False, s.wall),
                         0)

    def test_a_start_held_by_its_budget_points_at_the_logs(self):
        # A FATAL program runs no process: cmsWatchdog --snapshot would
        # have no stacks to write, and its log says why it failed.
        s = Scenario()
        s.warm_up()
        fatal(s, SS)
        outage(s)
        s.cycle()
        self.assertEqual(actions(s.cycle())[0].programs, (SS,))
        self.assertEqual(actions(s.cycle())[0].programs, (SS,))
        [alert] = held_starts(s.cycle())
        self.assertEqual(
            alert.text,
            "Watchdog: cmsScoringService 0 fails R5 (FATAL after a "
            "database outage) but is held by H7: will not start "
            "cmsScoringService 0 before 09:03 UTC: at most 2 starts of a "
            "FATAL program in 60 minutes. Check ./logs.sh, then start it "
            "by hand with supervisorctl if needed.")
        self.assertNotIn("--snapshot", alert.text)

    def test_h9_splits_the_batch(self):
        s = Scenario(restart=("web",))
        s.warm_up()
        fatal(s, LS, CWS0)
        outage(s)
        s.cycle()
        real, shadow = actions(s.cycle())
        self.assertEqual((real.programs, real.would, real.shadow),
                         ((CWS0,), False, False))
        self.assertEqual((shadow.programs, shadow.would, shadow.shadow),
                         ((LS,), True, True))

    def test_it_waits_for_an_action_in_flight(self):
        s = Scenario()
        s.warm_up()
        fatal(s, SS)
        outage(s)
        s.cycle()
        decisions = s.cycle(in_flight=True)
        self.assertEqual(actions(decisions), [])
        self.assertIn("held by H8", held_starts(decisions)[0].text)
        self.assertIn(HELD_STARTS, s.state.open_alerts)
        self.assertEqual(actions(s.cycle())[0].programs, (SS,))
        # No start is held any more: the hold episode is over.
        self.assertNotIn(HELD_STARTS, s.state.open_alerts)

    def test_held_starts_share_one_line(self):
        # Observe mode, the rollout default: after its 2 would-starts,
        # every program left FATAL enters H7 in the same cycle.
        s = Scenario(mode=OBSERVE)
        s.warm_up()
        fatal(s, LS, RS, SS, ES, W0, W1, PS, CWS0, CWS1, AWS)
        outage(s)
        lines = [[d for d in alerts(s.cycle()) if d.key[1] == "R5"]
                 for _ in range(25)]
        # One line at once, then one per ALERT_REPEAT_S (20 cycles).
        self.assertEqual([index for index, line in enumerate(lines)
                          if line], [3, 23])
        self.assertEqual([len(line) for line in lines if line], [1, 1])
        [line] = lines[3]
        self.assertEqual((line.key, line.level, line.holds),
                         (HELD_STARTS, "WARNING", ("H7",)))
        self.assertEqual(
            line.text,
            "Watchdog: 10 programs fail R5 together (FATAL after a "
            "database outage: cmsLogService 0, cmsResourceService 0, "
            "cmsEvaluationService 0, and 7 more) and are held by H7: will "
            "not start cmsLogService 0 before 09:03 UTC: at most 2 starts "
            "of a FATAL program in 60 minutes; will not start "
            "cmsResourceService 0 before 09:03 UTC: at most 2 starts of a "
            "FATAL program in 60 minutes; will not start "
            "cmsEvaluationService 0 before 09:03 UTC: at most 2 starts of "
            "a FATAL program in 60 minutes; and 7 more. Check ./logs.sh, "
            "then start them by hand with supervisorctl if needed.")
        self.assertEqual(s.state.open_alerts[HELD_STARTS].text, line.text)
        # Each program still has its own incident.
        self.assertEqual(
            {name: incident["rule"]
             for name, incident in s.state.ledger.incidents.items()},
            dict.fromkeys((LS, RS, SS, ES, W0, W1, PS, CWS0, CWS1, AWS),
                          "R5"))

    def test_the_headline_of_held_starts_holds_every_one_of_them(self):
        s = Scenario(mode=OBSERVE)
        s.warm_up()
        fatal(s, SS)
        outage(s)
        s.cycle()
        s.cycle()
        s.cycle()
        # CWS0 went FATAL during the outage too, but later: SS has used
        # its budget, CWS0 has not.
        s.set(CWS0, state="FATAL", pid=None, start_wall=s.wall - 40)
        [alert] = held_starts(s.cycle(in_flight=True))
        self.assertEqual(alert.holds, ("H8", "H7"))
        self.assertEqual(
            alert.text,
            "Watchdog: 2 programs fail R5 together (FATAL after a database "
            "outage: cmsScoringService 0, cmsContestWebServer 0) and are "
            "held by H8: another action is in flight. Also: H7. Nothing to "
            "do: another action is running.")


class TestRankings(unittest.TestCase):

    def test_a_ranking_that_does_not_answer_is_only_alerted(self):
        s = Scenario()
        s.warm_up()
        down = {"http://ranking:8890/": Probe(False, None,
                                              "Connection refused")}
        lines = []
        for _ in range(4):
            decisions = s.cycle(rankings=down)
            self.assertEqual(actions(decisions), [])
            lines.append([d for d in alerts(decisions)
                          if d.key[1] == "ranking"])
        self.assertEqual([len(line) for line in lines], [0, 0, 1, 0])
        self.assertIn("ranking http://ranking:8890/ does not answer (3 "
                      "cycles in a row: Connection refused)",
                      lines[2][0].text)
        up = {"http://ranking:8890/": Probe(True, 0.02)}
        [resolved] = [d for d in s.cycle(rankings=up)
                      if d.text.startswith("Resolved:")]
        self.assertIn("ranking http://ranking:8890/ answers again",
                      resolved.text)


class TestAbsentPrograms(unittest.TestCase):

    def test_a_program_the_proc_source_lost_is_only_alerted(self):
        s = Scenario(mode=OBSERVE)
        s.warm_up()
        s.set(W0, state="ABSENT", pid=None)
        decisions = s.cycle()
        self.assertEqual(actions(decisions), [])
        [alert] = [d for d in alerts(decisions) if d.key == (W0, "absent")]
        self.assertIn("cmsWorker 0 is gone", alert.text)


class TestDidItHelp(unittest.TestCase):

    def restarted(self) -> Scenario:
        s = Scenario()
        s.warm_up()
        for _ in range(4):
            s.cycle(fail=[CWS1])
        self.assertEqual(actions(s.cycle(fail=[CWS1]))[0].program, CWS1)
        return s

    @staticmethod
    def help_lines(decisions: list) -> list[str]:
        return [d.text for d in decisions if d.key[1] == "help"]

    def test_the_first_pass_after_the_restart(self):
        s = self.restarted()
        s.set(CWS1, pid=999, start_wall=s.wall + 5)
        self.assertEqual(
            self.help_lines(s.cycle()),
            ["Watchdog: cmsContestWebServer 1 answers again 15 s after "
             "the restart."])
        self.assertEqual(self.help_lines(s.cycle()), [])

    def test_still_failing_120_s_after_the_restart(self):
        s = self.restarted()
        history = [self.help_lines(s.cycle(fail=[CWS1]))
                   for _ in range(8)]
        self.assertEqual(history[:7], [[]] * 7)
        self.assertEqual(len(history[7]), 1)
        self.assertIn("cmsContestWebServer 1 is still failing 120 s after "
                      "the restart", history[7][0])

    def test_no_line_follows_a_would_action(self):
        s = Scenario(mode=OBSERVE)
        s.warm_up()
        for _ in range(4):
            s.cycle(fail=[CWS1])
        s.cycle(fail=[CWS1])
        for _ in range(10):
            self.assertEqual(self.help_lines(s.cycle(fail=[CWS1])), [])

    def test_no_line_while_no_rule_can_judge_the_program(self):
        # A young ES cannot judge the Workers: whether the new Worker
        # answers is unknown until it can.
        s = Scenario()
        s.warm_up()
        disabled = {"0": worker(False, s.wall - 200), "1": worker(True)}
        [restart] = actions(s.cycle(workers=disabled))
        self.assertEqual((restart.program, restart.rule), (W0, "R4"))
        s.set(W0, pid=999, start_wall=s.wall + 5)
        young_es = {ES: {"pid": 104, "uptime_s": 30.0, "busy": []}}
        self.assertEqual(self.help_lines(s.cycle(health=young_es)), [])
        self.assertEqual(
            self.help_lines(s.cycle()),
            ["Watchdog: cmsWorker 0 answers again 30 s after the "
             "restart."])

    def test_a_worker_that_es_does_not_use_yet_has_not_passed(self):
        # R4 cannot trip the new process yet: (a) needs a start before
        # disabled_at, (b) 300 s of running. ES still reports the Worker
        # disabled and not connected, so nothing has passed.
        s = Scenario()
        s.warm_up()
        still = {"0": worker(False, s.wall - 200), "1": worker(True)}
        [restart] = actions(s.cycle(workers=still))
        self.assertEqual((restart.program, restart.rule), (W0, "R4"))
        s.set(W0, pid=999, start_wall=s.wall + 5)
        history = [s.cycle(workers=still) for _ in range(8)]
        self.assertEqual([self.help_lines(d) for d in history[:7]],
                         [[]] * 7)
        self.assertEqual(
            self.help_lines(history[7]),
            ["Watchdog: cmsWorker 0 is still failing 120 s after the "
             "restart: look at its evidence file and ./logs.sh."])
        self.assertEqual([d.text for decisions in history for d in decisions
                          if d.text.startswith("Resolved:")], [])
        self.assertIn(W0, s.state.ledger.incidents)
        # Once ES reports it connected and enabled, it passes.
        s.cycle()
        [resolved] = [d for d in s.cycle() if d.key == (W0, "resolved")]
        self.assertIn("Resolved: cmsWorker 0 passes its checks again",
                      resolved.text)


class TestSummaryAndStatus(unittest.TestCase):

    def test_the_hourly_summary(self):
        s = Scenario()
        s.warm_up()
        s.cycle(fail=[CWS1])
        s.cycle(health={
            ES: busy(104, "post_finish_lock:_write_results_sync", 100.0,
                     40.0),
            SS: busy(103, "score", 50.0, 50.0),
            PS: busy(107, "send", 60.0, 20.0)})
        s.cycle(lag_s=6.0)
        s.cycle(gate=RED)
        s.cycle()
        decisions = s.cycle(advance=3600.0, since=CYCLE_S)
        [summary] = [d for d in decisions if d.key == ("-", "summary")]
        self.assertEqual(summary.level, "INFO")
        for part in (
                "cmsContestWebServer 1 at most 1 misses in 6, p99 0.01 s",
                "longest ES section 100 s "
                "(post_finish_lock:_write_results_sync)",
                "longest ES idle_s in a section 40 s",
                "ES sections past half their trip: "
                "post_finish_lock:_write_results_sync held 100 s from "
                "08:00 UTC, up to 40 s with no statement",
                "longest SS execution 50 s;",
                "longest PS idle_s in a send 20 s",
                "1 red-gate cycles, 1 starved cycles"):
            self.assertIn(part, summary.text)
        self.assertEqual(s.state.near_miss, NearMiss())

    def test_the_summary_lists_every_es_section_once(self):
        s = Scenario()
        s.warm_up()
        for index in range(3):
            # The same section, 15 s older each cycle.
            s.cycle(health={ES: busy(
                104, "post_finish_lock:_write_results_sync",
                100.0 + 15 * index, 40.0 + 15 * index)})
        for index in range(24):
            # A new section each cycle: the same age, a later start. A
            # thread holds one section at a time, so each new one is
            # another thread's.
            answer = busy(104, "post_finish_lock:_x_sync", 95.0, 35.0)
            answer["busy"][0]["tid"] = 5000 + index
            s.cycle(health={ES: answer})
        [summary] = [d for d in s.cycle(advance=3600.0, since=CYCLE_S)
                     if d.key == ("-", "summary")]
        self.assertEqual(summary.text.count("_write_results_sync held"), 1)
        self.assertIn("_write_results_sync held 130 s from 08:00 UTC, up "
                      "to 70 s with no statement", summary.text)
        self.assertEqual(summary.text.count("_x_sync held 95 s"), 24)

    def test_the_summary_marks_a_scoring_during_a_re_evaluation(self):
        s = Scenario()
        s.warm_up()
        s.cycle(health={
            ES: busy(104, INVALIDATION_SECTION, 400.0, 400.0),
            SS: busy(103, "score", 70.0, 70.0)})
        [summary] = [d for d in s.cycle(advance=3600.0, since=CYCLE_S)
                     if d.key == ("-", "summary")]
        self.assertIn("longest ES section 400 s "
                      "(post_finish_lock:_invalidate_submission_sync, a "
                      "re-evaluation)", summary.text)
        self.assertIn("longest SS execution 70 s, overlapping a "
                      "re-evaluation", summary.text)
        self.assertIn("longest ES idle_s in a section 0 s", summary.text)

    def test_a_section_seen_with_jitter_is_one_entry(self):
        # health() measures age_s a little before or after the cycle's
        # time: the start computed from it moves by a fraction of a
        # second, here across a 10 s boundary.
        s = Scenario()
        s.warm_up()
        start = (s.wall + CYCLE_S - 100.0) // 10 * 10 + 5.0
        for index, jitter in enumerate((0.3, -0.3, 0.3, -0.3)):
            s.cycle(health={ES: busy(
                104, "post_finish_lock:_write_results_sync",
                s.wall + CYCLE_S - start + jitter, 40.0 + 15 * index)})
        [summary] = [d for d in s.cycle(advance=3600.0, since=CYCLE_S)
                     if d.key == ("-", "summary")]
        self.assertEqual(summary.text.count("_write_results_sync held"), 1)
        self.assertIn("_write_results_sync held 144 s from 08:00 UTC, up "
                      "to 85 s with no statement", summary.text)

    def test_a_scoring_seen_with_jitter_keeps_its_overlap(self):
        s = Scenario()
        s.warm_up()
        start = (s.wall + CYCLE_S - 70.0) // 10 * 10 + 5.0
        s.cycle(health={
            ES: busy(104, INVALIDATION_SECTION, 400.0, 400.0),
            SS: busy(103, "score", s.wall + CYCLE_S - start + 0.3, 70.0)})
        # The same execution, longer, once the re-evaluation is over.
        s.cycle(health={
            SS: busy(103, "score", s.wall + CYCLE_S - start - 0.3, 85.0)})
        [summary] = [d for d in s.cycle(advance=3600.0, since=CYCLE_S)
                     if d.key == ("-", "summary")]
        self.assertIn("longest SS execution 84 s, overlapping a "
                      "re-evaluation", summary.text)

    def test_program_status(self):
        s = Scenario()
        s.warm_up()
        s.cycle(fail=[CWS1], health={SS: busy(103, "score", 50.0, 50.0)})
        entries = {entry["name"]: entry for entry in
                   program_status(s.state, s.observation, s.wall)}
        cws1 = entries[CWS1]
        self.assertEqual(
            (cws1["label"], cws1["state"], cws1["misses"], cws1["known"],
             cws1["http_ok"], cws1["rpc_ok"], cws1["http_latency_s"]),
            ("cmsContestWebServer 1", "RUNNING", 1, 6, False, False,
             None))
        self.assertEqual(entries[CWS0]["http_latency_s"], 0.01)
        self.assertEqual([section["what"]
                          for section in entries[SS]["sections"]],
                         ["score"])
        self.assertEqual(entries[W0]["misses"], 0)
        self.assertIsNone(entries[W0]["http_ok"])
        self.assertEqual(entries[CWS1]["restarts_last_hour"], 0)


if __name__ == "__main__":
    unittest.main()
