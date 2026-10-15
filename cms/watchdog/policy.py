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

"""The policy of cmsWatchdog: from observations to decisions (#38).

This module is pure. It imports only the standard library, knows
nothing about sockets, supervisord or PostgreSQL, and reads no clock:
every input is in an Observation and in the clocks given to decide(),
every output is a Decision, and what carries over from one cycle to the
next is in a PolicyState. Every threshold is a constant here, pinned
against the timeouts of the rest of CMS by
cmstestsuite/unit_tests/watchdog/ladder_test.py. The design is in
docs/superpowers/specs/2026-10-10-watchdog-design.md.

"""

import json
import math
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field


# The cycle (spec 3.3). Seconds, unless the name says otherwise.
CYCLE_S = 15.0
# A cycle that starts this late is starved: it decides nothing.
STARVED_LAG_S = 5.0
# More than this since the previous cycle is a pause: every miss window
# is cleared.
PAUSE_S = 30.0

# The probes (3.4).
HTTP_TIMEOUT_S = 5.0
RPC_TIMEOUT_S = 3.0
RANKING_TIMEOUT_S = 5.0
GATE_CONNECT_TIMEOUT_S = 3
GATE_STATEMENT_TIMEOUT_MS = 3000
GATE_KILL_S = 7.0
LOG_ALERT_TIMEOUT_S = 2.0

# Liveness, R1 and R2 (3.5): misses among the last WINDOW_SIZE known
# results.
WINDOW_SIZE = 6
MISSES_TO_TRIP = 5
MISSES_TO_WARN = 3

# Sections, R3.
SECTION_TRIP_S = 180.0
SECTION_WARN_S = 90.0
ES_IDLE_TRIP_S = 60.0
ES_IDLE_WARN_S = 30.0
ES_CPU_LOOKBACK_S = 60.0
ES_CPU_MIN_S = 1.0
ES_CPU_KEEP_S = 90.0
ES_SECTION_PREFIX = "post_finish_lock:"
# The outermost decorated method of an invalidation (re-evaluation):
# EvaluationService._invalidate_submission_sync.
INVALIDATION_SECTION = "post_finish_lock:_invalidate_submission_sync"

# Workers, R4.
WORKER_DISABLED_TRIP_S = 180.0
WORKER_UNCONNECTED_TRIP_S = 300.0
ES_UP_FOR_WORKERS_S = 120.0

# Rankings: alert only.
RANKING_MISSES_TO_ALERT = 3

# FATAL programs, R5.
FATAL_GREEN_CYCLES = 2
FATAL_RED_LOOKBACK_S = 15.0

# Holds (3.6).
GREEN_BEFORE_RESTART_S = 120.0
LOCK_WAIT_S = 30.0
GLOBAL_FAILING_FRACTION = 1 / 3
GRACE_S = 120.0
BUDGET_WINDOW_S = 3600.0
PROGRAM_BUDGET = 2
POOL_BUDGET = 3
FATAL_PROGRAM_BUDGET = 2
CORRUPT_LEDGER_S = 3600.0

# Actions (3.7).
TERM_WAIT_S = 10.0
CONFIRM_S = 30.0
DID_IT_HELP_S = 120.0
USR1_WAIT_S = 1.0

# Incidents and alerts (3.5, 3.9).
PASSES_TO_RESOLVE = 2
ALERT_REPEAT_S = 300.0
SUMMARY_PERIOD_S = 3600.0

# The self-guard (3.11).
SELF_GUARD_S = 60.0

# The class of each managed kind (3.2): the kind is what follows "cms"
# in a program's command. Any other kind is never acted on.
KIND_CLASS: dict[str, str] = {
    "LogService": "infra",
    "ResourceService": "infra",
    "EvaluationService": "core",
    "ScoringService": "core",
    "ProxyService": "core",
    "ContestWebServer": "web",
    "AdminWebServer": "web",
    "Worker": "workers",
}
# The classes, in the order an R5 batch starts them.
CLASS_ORDER: tuple[str, ...] = ("infra", "core", "web", "workers")
# Judged on the favicon probe (R1).
HTTP_KINDS = frozenset({"ContestWebServer", "AdminWebServer"})
# Judged on health() (R2).
RPC_RULE_KINDS = frozenset({"LogService", "ResourceService",
                            "EvaluationService", "ScoringService",
                            "ProxyService"})
# Asked for health() every cycle: every asyncio service.
HEALTH_KINDS = HTTP_KINDS | RPC_RULE_KINDS
# The holds, in the order they are checked (3.6).
HOLDS: tuple[str, ...] = ("H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8",
                          "H9")

# Decision.action and Booking.action.
ALERT = "alert"
RESTART = "restart"
START = "start"
# PolicyState.mode.
OBSERVE = "observe"
ENFORCE = "enforce"
# The budget pools of H7.
WORKERS_POOL = "workers"
SERVICES_POOL = "services"


def pool_of(klass: str) -> str:
    """Return the budget pool of a class: Workers have their own."""
    return WORKERS_POOL if klass == "workers" else SERVICES_POOL


def age(now: float, then: float) -> float:
    """Return now - then, or 0 if the clock went back (3.3)."""
    return max(0.0, now - then)


def hhmm_utc(wall: float) -> str:
    """Return an epoch time as "HH:MM UTC"."""
    return time.strftime("%H:%M UTC", time.gmtime(wall))


def p99(values: Sequence[float]) -> float | None:
    """Return the 99th percentile of values (nearest rank), or None."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.99 * len(ordered)) - 1)]


def is_number(value: object) -> bool:
    """Tell whether value is a finite int or float (not a bool)."""
    return (isinstance(value, (int, float))
            and not isinstance(value, bool) and math.isfinite(value))


@dataclass(frozen=True)
class ProgramFacts:
    """What the process source says about one program in one cycle.

    name: its name in the process source (supervisord's program name,
        or its label for the /proc source).
    kind: what follows "cms" in its command, or None for a command that
        is not a CMS program.
    shard: the argument after that, or None.
    state: supervisord's state name (RUNNING, FATAL, STOPPED...), or
        ABSENT for a program the /proc source does not see any more.
    pid: its process id, or None when it is not running.
    start_wall: when it last started (epoch seconds), or None.

    """

    name: str
    kind: str | None
    shard: int | None
    state: str
    pid: int | None
    start_wall: float | None

    @property
    def managed(self) -> bool:
        """Whether the watchdog may judge it and act on it."""
        return self.kind in KIND_CLASS and self.shard is not None

    @property
    def klass(self) -> str:
        """Its class; only for a managed program."""
        return KIND_CLASS[self.kind]

    @property
    def label(self) -> str:
        """Its CMS name, "cmsContestWebServer 2" (its application_name)."""
        return "cms%s %s" % (self.kind, self.shard)


@dataclass(frozen=True)
class Probe:
    """The result of one probe: ok, its latency, or why it failed."""

    ok: bool
    latency_s: float | None = None
    error: str = ""


@dataclass(frozen=True)
class Backend:
    """One row of the database snapshot (pg_stat_activity, 3.4.1).

    The ages are in seconds. A field PostgreSQL hides from another role
    (an operator's psql when the CMS role is not a superuser) is None.

    """

    pid: int
    application_name: str | None = None
    client_port: int | None = None
    state: str | None = None
    wait_event_type: str | None = None
    wait_event: str | None = None
    backend_age_s: float | None = None
    xact_age_s: float | None = None
    query_age_s: float | None = None
    state_age_s: float | None = None
    blocked_by: tuple[int, ...] = ()
    query: str | None = None


@dataclass(frozen=True)
class Gate:
    """The database gate of one cycle: green with a snapshot, or red.

    reason: for a red gate, "refused", "timeout", "too many clients",
        "authentication" or "other".
    detail: the first line of psql's error, for the alert.

    """

    green: bool
    reason: str = ""
    detail: str = ""
    backends: tuple[Backend, ...] = ()


@dataclass(frozen=True)
class Observation:
    """Everything the probes saw in one cycle (3.3, step 2).

    lag_s: how late the cycle started.
    since_previous_s: the time since the previous cycle started, or
        None for the first cycle.
    supervisor_ok: whether supervisord answered; if not, programs are
        the last ones it gave.
    programs: every program of the process source.
    gate: the database gate.
    http: the favicon probe of each CWS and AWS, by program name.
    rpc: the health() probe of each asyncio service, by program name.
    health: each health() answer, by program name.
    workers: ES's workers_status() answer (by shard, as a string), or
        None when ES was not asked or did not answer.
    rankings: the probe of each ranking, by its visible address.
    holder_cpu: (native thread id, utime+stime in seconds) of the
        thread that holds ES's oldest section, or None.
    in_flight: whether an action of an earlier cycle is still running.

    """

    lag_s: float
    since_previous_s: float | None
    supervisor_ok: bool
    programs: tuple[ProgramFacts, ...]
    gate: Gate
    http: Mapping[str, Probe] = field(default_factory=dict)
    rpc: Mapping[str, Probe] = field(default_factory=dict)
    health: Mapping[str, Mapping] = field(default_factory=dict)
    workers: Mapping[str, Mapping] | None = None
    rankings: Mapping[str, Probe] = field(default_factory=dict)
    holder_cpu: tuple[int, float] | None = None
    in_flight: bool = False


@dataclass(frozen=True)
class Decision:
    """What a cycle decided: an alert line, a restart or a start batch.

    action: ALERT, RESTART or START.
    text: an alert's line; for an action, what tripped (the action's
        lines quote it).
    level: an alert's level: "INFO", "WARNING" or "ERROR".
    key: (program name or "-", topic): the alert's rate-limit key.
    program: the program to restart.
    programs: the programs of a start batch, in class order.
    rule: "R1" to "R5".
    would: a dry run (observe mode, or H9).
    shadow: booked in the shadow ledger (H9).
    holds: for a held trip's alert, the holds that apply, the headline
        first.

    """

    action: str
    text: str = ""
    level: str = "WARNING"
    key: tuple[str, str] = ("-", "")
    program: str = ""
    programs: tuple[str, ...] = ()
    rule: str = ""
    would: bool = False
    shadow: bool = False
    holds: tuple[str, ...] = ()


@dataclass
class Booking:
    """One action or would-action, kept for the budgets (H7).

    wall: when it was decided (epoch seconds).
    program: the program's name in the process source.
    label: its CMS name (the orphan mark of H3 looks it up).
    pool: WORKERS_POOL or SERVICES_POOL.
    action: RESTART or START.
    rule: the rule that tripped.
    would: whether it was a dry run.
    pid: the pid a restart signals, None for a start.

    """

    wall: float
    program: str
    label: str
    pool: str
    action: str
    rule: str
    would: bool
    pid: int | None = None


class LedgerError(ValueError):
    """A ledger file that does not parse."""


def _booking(item: Mapping) -> Booking:
    """Read one booking of a ledger file."""
    return Booking(
        wall=float(item["wall"]), program=str(item["program"]),
        label=str(item["label"]), pool=str(item["pool"]),
        action=str(item["action"]), rule=str(item["rule"]),
        would=bool(item["would"]),
        pid=None if item.get("pid") is None else int(item["pid"]))


@dataclass
class Ledger:
    """What must survive a restart of the watchdog (3.10).

    real: the actions, and the would-actions of observe mode.
    shadow: the would-actions of a class that enforce mode may not act
        on (H9).
    red: the red intervals of the gate, [start, end] in epoch seconds,
        end None for the current one.
    incidents: the open incidents by program name: {"rule", "opened"
        (epoch seconds), "passes"}.
    exhausted_until: until when a ledger that did not parse counts as
        every budget exhausted (epoch seconds), or None.

    """

    real: list[Booking] = field(default_factory=list)
    shadow: list[Booking] = field(default_factory=list)
    red: list[list] = field(default_factory=list)
    incidents: dict[str, dict] = field(default_factory=dict)
    exhausted_until: float | None = None

    @classmethod
    def exhausted(cls, wall: float) -> "Ledger":
        """Return the ledger that replaces one that did not parse.

        wall: now (epoch seconds).

        return: an empty ledger whose budgets are all exhausted for
            CORRUPT_LEDGER_S seconds.

        """
        return cls(exhausted_until=wall + CORRUPT_LEDGER_S)

    def book(self, booking: Booking, shadow: bool) -> None:
        """Add an action to the real ledger, or to the shadow one."""
        (self.shadow if shadow else self.real).append(booking)

    def prune(self, wall: float) -> None:
        """Drop what no budget and no R5 question can need any more."""
        def recent(then: float) -> bool:
            return age(wall, then) < BUDGET_WINDOW_S

        self.real = [b for b in self.real if recent(b.wall)]
        self.shadow = [b for b in self.shadow if recent(b.wall)]
        self.red = [interval for interval in self.red
                    if interval[1] is None or recent(interval[1])]
        if self.exhausted_until is not None and \
                wall >= self.exhausted_until:
            self.exhausted_until = None

    def count(self, program: str, action: str, shadow: bool,
              wall: float) -> int:
        """Return how many times action hit program in the last hour.

        program: the program's name.
        action: RESTART or START.
        shadow: whether to count in the shadow ledger.
        wall: now (epoch seconds).

        return: the count.

        """
        return sum(1 for b in (self.shadow if shadow else self.real)
                   if b.program == program and b.action == action
                   and age(wall, b.wall) < BUDGET_WINDOW_S)

    def free_at(self, program: str, pool: str, action: str, shadow: bool,
                wall: float) -> float | None:
        """Tell when the budget allows action on program again (H7).

        program: the program's name.
        pool: its pool (pool_of its class).
        action: RESTART (PROGRAM_BUDGET per program, POOL_BUDGET per
            pool) or START (FATAL_PROGRAM_BUDGET per program).
        shadow: whether to count in the shadow ledger.
        wall: now (epoch seconds).

        return: None if the budget allows it now, otherwise the epoch
            time from which it does.

        """
        if self.exhausted_until is not None and wall < self.exhausted_until:
            return self.exhausted_until
        recent = [b for b in (self.shadow if shadow else self.real)
                  if b.action == action
                  and age(wall, b.wall) < BUDGET_WINDOW_S]
        if action == RESTART:
            limits = (([b for b in recent if b.program == program],
                       PROGRAM_BUDGET),
                      ([b for b in recent if b.pool == pool], POOL_BUDGET))
        else:
            limits = (([b for b in recent if b.program == program],
                       FATAL_PROGRAM_BUDGET),)
        free = None
        for bookings, limit in limits:
            if len(bookings) >= limit:
                walls = sorted(b.wall for b in bookings)
                at = walls[len(walls) - limit] + BUDGET_WINDOW_S
                free = at if free is None else max(free, at)
        return free

    def red_since(self, wall: float) -> bool:
        """Tell whether the gate was red at some time after wall."""
        return any(end is None or end > wall for _start, end in self.red)

    def last_restart_of(self, label: str) -> Booking | None:
        """Return the last real restart of the program called label."""
        restarts = [b for b in self.real if b.label == label
                    and b.action == RESTART and not b.would]
        return restarts[-1] if restarts else None

    def to_json(self) -> str:
        """Return the ledger as the text of its file."""
        return json.dumps({
            "real": [asdict(b) for b in self.real],
            "shadow": [asdict(b) for b in self.shadow],
            "red": self.red,
            "incidents": self.incidents,
            "exhausted_until": self.exhausted_until,
        }, indent=1, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "Ledger":
        """Read the text of a ledger file.

        text: what to_json wrote.

        return: the ledger.

        raise (LedgerError): if text is not a ledger.

        """
        try:
            data = json.loads(text)
            return cls(
                real=[_booking(item) for item in data["real"]],
                shadow=[_booking(item) for item in data["shadow"]],
                red=[[float(start), None if end is None else float(end)]
                     for start, end in data["red"]],
                incidents={
                    str(name): {"rule": str(incident["rule"]),
                                "opened": float(incident["opened"]),
                                "passes": int(incident["passes"])}
                    for name, incident in data["incidents"].items()},
                exhausted_until=(None if data["exhausted_until"] is None
                                 else float(data["exhausted_until"])))
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise LedgerError("not a watchdog ledger: %s" % error) \
                from error


@dataclass
class ProgramState:
    """What the policy remembers about one program.

    pid: its pid in the last cycle that saw it RUNNING.
    window: its last WINDOW_SIZE known liveness results, True for a
        miss.
    history: its last WINDOW_SIZE observations, for the evidence.
    last_action_mono: when its last action or would-action was decided
        (the grace of H6).
    restarted_mono, restarted_pid: when the watchdog last restarted it
        for real and the pid it had then, until the "did it help" line.

    """

    pid: int | None = None
    window: deque = field(
        default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    history: deque = field(
        default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    last_action_mono: float | None = None
    restarted_mono: float | None = None
    restarted_pid: int | None = None


@dataclass
class NearMiss:
    """How close each trip came in this hour (3.9).

    misses: the most misses in a window, by program label.
    latencies: the latencies of the passing liveness probes, by label.
    es_longest: (age_s, what) of the longest ES section.
    es_idle_s: the longest idle_s inside an ES section that is not an
        invalidation.
    es_half: every ES section that passed half its trip (not an
        invalidation), one entry per section: (what, its start as epoch
        seconds, its largest age_s, its largest idle_s), by a key made
        of its holder thread and its start. A section seen on many
        cycles stays one entry, so the summary grows with the number of
        such sections only, and lists every one of them.
    ss_longest: (age_s, execution key) of the longest SS execution.
    ss_overlapping: the keys of the SS executions that overlapped an
        invalidation.
    ps_idle_s: the longest idle_s inside a PS send.
    red_cycles, starved_cycles: how many cycles had a red gate, and
        how many were starved.

    """

    misses: dict[str, int] = field(default_factory=dict)
    latencies: dict[str, list[float]] = field(default_factory=dict)
    es_longest: tuple[float, str] | None = None
    es_idle_s: float = 0.0
    es_half: dict[str, tuple[str, float, float, float]] = field(
        default_factory=dict)
    ss_longest: tuple[float, str] | None = None
    ss_overlapping: set[str] = field(default_factory=set)
    ps_idle_s: float = 0.0
    red_cycles: int = 0
    starved_cycles: int = 0


@dataclass
class PolicyState:
    """What carries over from one cycle to the next.

    mode: OBSERVE or ENFORCE.
    restart_classes: the classes enforce mode may act on (H9).
    ledger: what survives a restart (main.py saves and loads it).
    programs: a ProgramState by program name.
    started_mono: when the watchdog's first cycle ran (H6).
    green_since_mono: when the gate last turned green; None while red.
    green_cycles: how many cycles in a row the gate has been green.
    holder_samples: (mono, tid, cpu_s) samples of ES's holder thread.
    ranking_misses: failed cycles in a row, by ranking address.
    alert_sent: when each alert key was last emitted (monotonic).
    held_headline: the headline hold of each held trip's key.
    open_alerts: the last alert of each open problem, by key (sent
        again to a new LogService).
    near_miss: this hour's statistics.
    summary_mono: when the last hourly summary was emitted.

    """

    mode: str
    restart_classes: frozenset[str]
    ledger: Ledger = field(default_factory=Ledger)
    programs: dict[str, ProgramState] = field(default_factory=dict)
    started_mono: float | None = None
    green_since_mono: float | None = None
    green_cycles: int = 0
    holder_samples: list[tuple[float, int, float]] = field(
        default_factory=list)
    ranking_misses: dict[str, int] = field(default_factory=dict)
    alert_sent: dict[tuple[str, str], float] = field(default_factory=dict)
    held_headline: dict[tuple[str, str], str] = field(default_factory=dict)
    open_alerts: dict[tuple[str, str], Decision] = field(
        default_factory=dict)
    near_miss: NearMiss = field(default_factory=NearMiss)
    summary_mono: float | None = None

    def shadow_for(self, klass: str) -> bool:
        """Tell whether an action on klass goes to the shadow (H9)."""
        return self.mode == ENFORCE and klass not in self.restart_classes
