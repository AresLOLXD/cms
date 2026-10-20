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

"""The invariants of cmsWatchdog's policy, over every scenario (#38).

- At most one action per cycle: one restart, or the R5 batch (with its
  H9 part); never both.
- No action in a cycle whose gate is red, or while another is in
  flight.
- Observe mode and enforce mode with every class enabled decide the
  same, apart from the actuator: the would flag, the "did it help"
  lines, which follow real restarts only, the early warnings, which
  say what the mode does at the trip (they are compared by key), and
  the next step of a line that H6 holds after an action, which observe
  mode leaves to a human (BY_HAND_HINT).

No script has a lock wait whose blocker is a session of a program the
watchdog restarted: H3's "orphan of pid N" mark follows real restarts
only (Ledger.last_restart_of leaves would-actions out), so the two
modes word that line differently by design.

"""

import unittest
from dataclasses import replace

from cms.watchdog.policy import BY_HAND_HINT, ENFORCE, OBSERVE, RESTART, \
    START
from cmstestsuite.unit_tests.watchdog.scenario import CWS0, CWS1, ES, \
    RED, SS, WALL0, Scenario, worker


ES_STUCK = {"pid": 104, "uptime_s": 9_999.0, "busy": [{
    "what": "post_finish_lock:_write_results_sync", "tid": 4321,
    "age_s": 400.0, "statements": 3, "idle_s": 300.0, "stack": None}]}


def fatal_ss(s: Scenario) -> None:
    s.set(SS, state="FATAL", pid=None, start_wall=s.wall + 20)


# Each scenario: a setup (or None) and the keyword arguments of each
# cycle after Scenario.warm_up().
SCRIPTS = {
    "r1": (None, [{"fail": [CWS1]}] * 45),
    "livelock": (None, ([{"fail": [CWS1]}] * 2 + [{}]
                        + [{"fail": [CWS1]}] * 3) * 6),
    "correlated": (None, [{"fail": [CWS0, CWS1]}] * 20),
    "outage": (None, [{"fail": [CWS1]}] * 6
               + [{"fail": [CWS1], "gate": RED}] * 5
               + [{"fail": [CWS1]}] * 25),
    "es stuck": (None, [{"health": {ES: ES_STUCK},
                         "holder_cpu": (4321, 5.0)}] * 30),
    "worker": (None, [{"workers": {"0": worker(False, WALL0),
                                   "1": worker(True)}}] * 30),
    "in flight": (None, [{"fail": [CWS1], "in_flight": True},
                         {"fail": [CWS1]}] * 15),
    "fatal": (fatal_ss, [{"gate": RED}] * 3 + [{}] * 12),
    "fatal and r1": (fatal_ss, [{"gate": RED, "fail": [CWS1]}] * 3
                     + [{"fail": [CWS1]}] * 20),
}


def play(mode: str, name: str) -> list[tuple[dict, list]]:
    """Play a script; return each cycle's arguments and decisions."""
    setup, cycles = SCRIPTS[name]
    s = Scenario(mode=mode)
    s.warm_up()
    if setup is not None:
        setup(s)
    return [(kwargs, s.cycle(**kwargs)) for kwargs in cycles]


def comparable(decisions: list) -> list:
    """Drop what only the mode explains (see the module docstring)."""
    return [replace(d, would=False,
                    text="" if d.key[1].endswith(" warning")
                    else d.text.replace(" " + BY_HAND_HINT, ""))
            for d in decisions if d.key[1] != "help"]


class TestInvariants(unittest.TestCase):

    def test_at_most_one_action_per_cycle(self):
        for name in SCRIPTS:
            for mode in (OBSERVE, ENFORCE):
                for kwargs, decisions in play(mode, name):
                    restarts = [d for d in decisions if d.action == RESTART]
                    starts = [d for d in decisions if d.action == START]
                    with self.subTest(script=name, mode=mode):
                        self.assertLessEqual(len(restarts), 1)
                        self.assertFalse(restarts and starts)
                        self.assertLessEqual(
                            len([d for d in starts if not d.shadow]), 1)
                        self.assertTrue(all(d.rule == "R5" for d in starts))

    def test_no_action_while_the_gate_is_red_or_one_is_in_flight(self):
        for name in SCRIPTS:
            for mode in (OBSERVE, ENFORCE):
                for kwargs, decisions in play(mode, name):
                    blocked = kwargs.get("gate") is RED \
                        or kwargs.get("in_flight", False)
                    acted = [d for d in decisions if d.action != "alert"]
                    with self.subTest(script=name, mode=mode):
                        self.assertFalse(blocked and acted)

    def test_observe_and_enforce_decide_the_same(self):
        for name in SCRIPTS:
            observe = play(OBSERVE, name)
            enforce = play(ENFORCE, name)
            for index, ((_, seen), (_, done)) in enumerate(
                    zip(observe, enforce)):
                with self.subTest(script=name, cycle=index):
                    self.assertEqual(comparable(seen), comparable(done))

    def test_a_real_restart_gives_the_grace_of_a_would_restart(self):
        # H6's grace after the last action follows real actions too: the
        # cycle after the restart is held the same way in both modes.
        for mode in (OBSERVE, ENFORCE):
            with self.subTest(mode=mode):
                history = [decisions for _, decisions in play(mode, "r1")]
                first = next(index for index, decisions
                             in enumerate(history)
                             if any(d.action == RESTART for d in decisions))
                self.assertEqual([d.would for d in history[first]
                                  if d.action == RESTART],
                                 [mode == OBSERVE])
                [line] = [d for d in history[first + 1] if d.holds]
                self.assertEqual(line.holds[0], "H6")
                self.assertIn("its last action or would-action was 15 s "
                              "ago", line.text)

    def test_every_script_acts_at_least_once(self):
        # Otherwise the invariants above would hold trivially.
        for name in SCRIPTS:
            if name == "correlated":
                continue
            with self.subTest(script=name):
                self.assertTrue(any(
                    d.action != "alert"
                    for _, decisions in play(ENFORCE, name)
                    for d in decisions))
