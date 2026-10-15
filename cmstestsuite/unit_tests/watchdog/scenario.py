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

"""Drive cmsWatchdog's policy cycle by cycle on a fake clock (#38).

A Scenario holds the programs and the policy state of a test. Each
cycle() builds the Observation the probes would give (every probe
passes unless its program is in fail), calls decide() and returns its
decisions.

"""

from collections.abc import Iterable, Mapping
from dataclasses import replace

from cms.watchdog.policy import ALERT, CYCLE_S, ENFORCE, HEALTH_KINDS, \
    HTTP_KINDS, Backend, Decision, Gate, Observation, PolicyState, \
    Probe, ProgramFacts, decide


WALL0 = 1_800_000_000.0
MONO0 = 10_000.0
LONG_AGO = WALL0 - 86_400.0
GREEN = Gate(True)
RED = Gate(False, "refused", "Connection refused")
ALL_CLASSES = ("web", "core", "infra", "workers")

LS = "cmslogservice"
RS = "cmsresourceservice"
SS = "cmsscoringservice"
ES = "cmsevaluationservice"
PS = "cmsproxyservice"
W0 = "cmsworker0"
W1 = "cmsworker1"
CWS0 = "cmscontestwebserver0"
CWS1 = "cmscontestwebserver1"
AWS = "cmsadminwebserver"


def program(name: str, kind: str | None, shard: int | None, pid: int,
            state: str = "RUNNING") -> ProgramFacts:
    """Return the facts of a program that started a day before WALL0."""
    return ProgramFacts(name, kind, shard, state,
                        pid if state == "RUNNING" else None, LONG_AGO)


DEFAULT_PROGRAMS: tuple[ProgramFacts, ...] = (
    program(LS, "LogService", 0, 101),
    program(RS, "ResourceService", 0, 102),
    program(SS, "ScoringService", 0, 103),
    program(ES, "EvaluationService", 0, 104),
    program(W0, "Worker", 0, 105),
    program(W1, "Worker", 1, 106),
    program(PS, "ProxyService", 0, 107),
    program(CWS0, "ContestWebServer", 0, 108),
    program(CWS1, "ContestWebServer", 1, 109),
    program(AWS, "AdminWebServer", 0, 110),
)


def lock_wait_snapshot(blocker_app: str = "psql",
                       blocker_age_s: float = 400.0
                       ) -> tuple[Backend, ...]:
    """Return a snapshot where ES has waited 45 s on another's lock."""
    return (
        Backend(pid=500, application_name="cmsEvaluationService 0",
                state="active", wait_event_type="Lock",
                wait_event="transactionid", query_age_s=45.0,
                backend_age_s=3_000.0, blocked_by=(900,)),
        Backend(pid=900, application_name=blocker_app,
                state="idle in transaction", xact_age_s=300.0,
                backend_age_s=blocker_age_s),
    )


def worker(connected: bool, disabled_at: float | None = None) -> dict:
    """Return one Worker's entry of ES's workers_status().

    disabled_at: when ES disabled it for not answering, or None.

    """
    return {"connected": connected,
            "disabled_by_timeout": disabled_at is not None,
            "disabled_at": disabled_at}


class Scenario:
    """The programs and the policy state of one test, cycle by cycle."""

    def __init__(self, mode: str = ENFORCE,
                 restart: Iterable[str] = ALL_CLASSES,
                 programs: Iterable[ProgramFacts] = DEFAULT_PROGRAMS):
        self.state = PolicyState(mode=mode,
                                 restart_classes=frozenset(restart))
        self.mono = MONO0
        self.wall = WALL0
        self.programs = {facts.name: facts for facts in programs}
        self.cycles = 0
        # The observation of the last cycle.
        self.observation: Observation | None = None

    def set(self, name: str, **changes) -> None:
        """Change the facts of a program from the next cycle on."""
        self.programs[name] = replace(self.programs[name], **changes)

    def cycle(self, fail: Iterable[str] = (), *, gate: Gate = GREEN,
              lag_s: float = 0.0, since: float | None = None,
              supervisor_ok: bool = True,
              health: Mapping[str, Mapping] | None = None,
              probes: Mapping[str, Probe] | None = None,
              workers: Mapping[str, Mapping] | None = None,
              rankings: Mapping[str, Probe] | None = None,
              holder_cpu: tuple[int, float] | None = None,
              in_flight: bool = False,
              advance: float = CYCLE_S) -> list[Decision]:
        """Run one cycle, advance seconds after the previous one.

        fail: the programs whose probes fail (HTTP and RPC).
        probes: probes to force, by program name (HTTP and RPC).
        health: health() answers to replace, by program name.
        workers: ES's workers_status(); by default every running Worker
            is connected, if ES's health() passes.
        since: the gap since the previous cycle; advance by default.
        The other arguments go to the Observation as they are.

        return: the decisions of the cycle.

        """
        if self.cycles:
            self.mono += advance
            self.wall += advance
        failing = set(fail)
        http: dict[str, Probe] = {}
        rpc: dict[str, Probe] = {}
        for facts in self.programs.values():
            if facts.state != "RUNNING" or facts.kind not in HEALTH_KINDS:
                continue
            probe = (Probe(False, None, "timed out")
                     if facts.name in failing else Probe(True, 0.01))
            if facts.kind in HTTP_KINDS:
                http[facts.name] = probe
            rpc[facts.name] = probe
        for name, probe in (probes or {}).items():
            http[name] = probe
            rpc[name] = probe
        answers: dict[str, Mapping] = {
            name: {"pid": self.programs[name].pid, "uptime_s": 9_999.0,
                   "busy": []}
            for name, probe in rpc.items() if probe.ok}
        answers.update(health or {})
        if workers is None and rpc.get(ES, Probe(False)).ok:
            workers = {
                str(facts.shard): worker(True)
                for facts in self.programs.values()
                if facts.kind == "Worker" and facts.state == "RUNNING"}
        if since is None and self.cycles:
            since = advance
        observation = Observation(
            lag_s=lag_s, since_previous_s=since,
            supervisor_ok=supervisor_ok,
            programs=tuple(self.programs.values()), gate=gate, http=http,
            rpc=rpc, health=answers, workers=workers,
            rankings=rankings or {}, holder_cpu=holder_cpu,
            in_flight=in_flight)
        self.observation = observation
        self.state, decisions = decide(self.state, observation, self.mono,
                                       self.wall)
        self.cycles += 1
        return decisions

    def warm_up(self) -> None:
        """Run 9 passing cycles: past the watchdog's and the gate's 120 s.

        The next cycle is 135 s after the first one.

        """
        for _ in range(9):
            self.cycle()


def actions(decisions: list[Decision]) -> list[Decision]:
    """Return the restarts and starts among decisions."""
    return [decision for decision in decisions if decision.action != ALERT]


def alerts(decisions: list[Decision]) -> list[Decision]:
    """Return the alerts among decisions."""
    return [decision for decision in decisions if decision.action == ALERT]


def held(decisions: list[Decision], name: str) -> list[Decision]:
    """Return the alerts about held trips of the program name."""
    return [decision for decision in alerts(decisions)
            if decision.key[0] == name and decision.holds]
