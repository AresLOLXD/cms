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

import copy
import json
import math
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import TypeVar


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
# H3's line names at most this many waiting sessions, and this many
# blockers of each, so that it stays a line.
LOCK_WAIT_LISTED = 3
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

# The headline holds whose first line in a hold episode goes out at once,
# not after the rate limit: a budget used up (3.9 lists it among the
# state changes) and a lock wait only a human can end.
URGENT_HOLDS: tuple[str, ...] = ("H3", "H7")
# The reasons of a red gate that PostgreSQL itself answered. The others,
# a timeout and "other", can be the watchdog's own starvation.
GATE_ANSWERED_REASONS: tuple[str, ...] = ("refused", "too many clients",
                                          "authentication")

# The alert key of the Workers that H4 holds together.
WORKERS_H4_KEY = ("workers", "H4")

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
    """Return an epoch time as "HH:MM UTC", or "--:--" if it has none.

    A NaN, an infinity or a time the platform cannot represent has no
    hour: the line that quotes it must still be written.

    """
    try:
        return time.strftime("%H:%M UTC", time.gmtime(wall))
    except (ValueError, OverflowError, OSError):
        return "--:--"


def p99(values: Sequence[float]) -> float | None:
    """Return the 99th percentile of values (nearest rank), or None."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.99 * len(ordered)) - 1)]


def is_number(value: object) -> bool:
    """Tell whether value is a finite int or float (not a bool).

    An int too large for a float is not a number the policy can use.

    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


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


# What a ledger file may hold: anything else is not a ledger. The times
# are epoch seconds from 1970 to 2100, so that every one of them can be
# written in an alert and none of them is so far away that it would
# never age out.
LEDGER_MAX_EPOCH_S = 4_102_444_800.0
# Linux's PID_MAX_LIMIT.
LEDGER_MAX_PID = 1 << 22
LEDGER_MAX_PASSES = 1_000_000

_Shape = TypeVar("_Shape")


def _shape(value: object, kind: type[_Shape]) -> _Shape:
    """Return value if it is a kind (list or dict), else raise."""
    if not isinstance(value, kind):
        raise ValueError("expected a %s, got %.40r" % (kind.__name__, value))
    return value


def _epoch(value: object) -> float:
    """Return a time of a ledger file; raise if it is not a sane one."""
    if not is_number(value) or not 0 <= value <= LEDGER_MAX_EPOCH_S:
        raise ValueError("not a time: %.40r" % (value,))
    return float(value)


def _integer(value: object, low: int, high: int) -> int:
    """Return an int of a ledger file between low and high."""
    if isinstance(value, bool) or not isinstance(value, int) \
            or not low <= value <= high:
        raise ValueError("not an integer from %d to %d: %.40r"
                         % (low, high, value))
    return value


def _text(value: object, allowed: Sequence[str] | None = None) -> str:
    """Return a string of a ledger file, one of allowed if it is given."""
    if not isinstance(value, str) or \
            (allowed is not None and value not in allowed):
        raise ValueError("not a valid string: %.40r" % (value,))
    return value


def _flag(value: object) -> bool:
    """Return a bool of a ledger file: only true and false are one."""
    if not isinstance(value, bool):
        raise ValueError("not a boolean: %.40r" % (value,))
    return value


def _booking(item: object) -> Booking:
    """Read one booking of a ledger file."""
    item = _shape(item, dict)
    pid = item.get("pid")
    return Booking(
        wall=_epoch(item["wall"]), program=_text(item["program"]),
        label=_text(item["label"]),
        pool=_text(item["pool"], (WORKERS_POOL, SERVICES_POOL)),
        action=_text(item["action"], (RESTART, START)),
        rule=_text(item["rule"]), would=_flag(item["would"]),
        pid=None if pid is None else _integer(pid, 1, LEDGER_MAX_PID))


def _interval(item: object) -> list:
    """Read one red interval of a ledger file: [start, end or None]."""
    start, end = _shape(item, list)
    return [_epoch(start), None if end is None else _epoch(end)]


def _incident(item: object) -> dict:
    """Read one open incident of a ledger file."""
    item = _shape(item, dict)
    return {"rule": _text(item["rule"]),
            "opened": _epoch(item["opened"]),
            "passes": _integer(item["passes"], 0, LEDGER_MAX_PASSES)}


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
        """Return the ledger as the text of its file.

        raise (ValueError): if a time is NaN or infinite, which is not
            JSON and which from_json would not take back.

        """
        return json.dumps({
            "real": [asdict(b) for b in self.real],
            "shadow": [asdict(b) for b in self.shadow],
            "red": self.red,
            "incidents": self.incidents,
            "exhausted_until": self.exhausted_until,
        }, indent=1, sort_keys=True, allow_nan=False)

    @classmethod
    def from_json(cls, text: str) -> "Ledger":
        """Read the text of a ledger file.

        text: what to_json wrote.

        return: the ledger.

        raise (LedgerError): if text is not a ledger: it does not
            parse, or a field is missing, of the wrong type or out of
            its range. Nothing else leaves for a bad text.

        """
        try:
            data = _shape(json.loads(text), dict)
            exhausted_until = data["exhausted_until"]
            return cls(
                real=[_booking(item) for item in _shape(data["real"], list)],
                shadow=[_booking(item)
                        for item in _shape(data["shadow"], list)],
                red=[_interval(item) for item in _shape(data["red"], list)],
                incidents={
                    str(name): _incident(incident) for name, incident
                    in _shape(data["incidents"], dict).items()},
                exhausted_until=(None if exhausted_until is None
                                 else _epoch(exhausted_until)))
        except (ValueError, KeyError, TypeError, AttributeError,
                ArithmeticError, RecursionError) as error:
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
    green_since_mono: when the gate last turned green as the watchdog
        saw it, or at its first green cycle as if long enough ago for
        H2; None while red.
    green_cycles: how many cycles in a row the gate has been green.
    holder_samples: (mono, tid, cpu_s) samples of ES's holder thread.
    ranking_misses: failed cycles in a row, by ranking address.
    alert_sent: when each alert key was last emitted (monotonic).
    held_headline: the headline hold of each held trip's key.
    held_announced: for each held trip's key, the URGENT_HOLDS that have
        headed a line in this hold episode; it ends with held_headline.
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
    held_announced: dict[tuple[str, str], set[str]] = field(
        default_factory=dict)

    def shadow_for(self, klass: str) -> bool:
        """Tell whether an action on klass goes to the shadow (H9)."""
        return self.mode == ENFORCE and klass not in self.restart_classes


@dataclass(frozen=True)
class Trip:
    """A rule that tripped for a program in this cycle."""

    program: ProgramFacts
    rule: str
    detail: str


# The next step that an alert names for each headline hold (3.9).
HOLD_HINTS: dict[str, str] = {
    "H1": "Check ./status.sh.",
    "H2": ("Nothing to do: the watchdog waits for the database (see "
           "\"When the database misbehaves\")."),
    "H3": ("End the blocker by hand only if it is stuck (see \"When the "
           "watchdog alerts\")."),
    "H4": ("Find what the failing programs share before restarting any "
           "of them by hand."),
    "H5": "Check the load of the server and of the database.",
    "H6": "Nothing to do yet: it is still starting.",
    "H7": "Restart it by hand if needed, after cmsWatchdog --snapshot.",
    "H8": "Nothing to do: another action is running.",
}


def _other_holds(holds: Sequence[tuple[str, str]]) -> str:
    """Return the sentences that follow the headline hold of an alert.

    They list every other hold that applies and, when H3 is one of
    them, its blocker details: an alert where H3 applies always carries
    them (3.6).

    holds: (code, reason) of each hold that applies, headline first.

    return: the sentences, each after a space; "" for the headline
        alone.

    """
    others = holds[1:]
    if not others:
        return ""
    text = " Also: %s." % ", ".join(code for code, _ in others)
    lock_wait = dict(others).get("H3")
    if lock_wait:
        text += " H3: %s." % lock_wait
    return text


def busy_sections(payload: object) -> list[dict]:
    """Return the busy sections of a health() answer, oldest first.

    A malformed answer, or a malformed entry of it, counts as no
    section: no answer is trusted to be well formed.

    payload: a health() answer, or None.

    return: [{"what", "tid", "age_s", "idle_s", "statements"}]; tid and
        statements are None when the answer lacks them.

    """
    if not isinstance(payload, Mapping):
        return []
    busy = payload.get("busy")
    if not isinstance(busy, list):
        return []
    sections = []
    for entry in busy:
        if not isinstance(entry, Mapping):
            continue
        what = entry.get("what")
        age_s = entry.get("age_s")
        idle_s = entry.get("idle_s")
        if not isinstance(what, str) or not is_number(age_s) \
                or not is_number(idle_s):
            continue
        tid = entry.get("tid")
        statements = entry.get("statements")
        sections.append({
            "what": what,
            "tid": tid if isinstance(tid, int) and is_number(tid) else None,
            "age_s": float(age_s),
            "idle_s": float(idle_s),
            "statements": statements if is_number(statements) else None,
        })
    sections.sort(key=lambda section: -section["age_s"])
    return sections


def decide(state: PolicyState, observation: Observation, mono: float,
           wall: float) -> tuple[PolicyState, list[Decision]]:
    """Turn one cycle's observation into decisions (3.3, step 3).

    state: what the previous cycle left; it is not changed.
    observation: what the probes saw in this cycle.
    mono: the monotonic time of the cycle's start.
    wall: the epoch time of the cycle's start.

    return: the new state, and the decisions in order: the alerts to
        emit now (already rate-limited), and at most one action (a
        restart, or the R5 start batch with its H9 part).

    """
    cycle = _Cycle(copy.deepcopy(state), observation, mono, wall)
    cycle.run()
    return cycle.state, cycle.decisions


class _Cycle:
    """One call of decide(): the phases of a cycle, in order."""

    def __init__(self, state: PolicyState, observation: Observation,
                 mono: float, wall: float):
        self.state = state
        self.obs = observation
        self.mono = mono
        self.wall = wall
        self.decisions: list[Decision] = []
        # Whether this cycle decided an action (H8).
        self.acted = False
        self.starved = observation.lag_s > STARVED_LAG_S
        self.running = [program for program in observation.programs
                        if program.managed and program.state == "RUNNING"]
        self.trips: list[Trip] = []
        # The early warnings of this cycle, as Trips; _act_on_trips
        # emits them once every program's misses are known (H4).
        self.warnings: list[Trip] = []
        # Programs whose own probe failed in this cycle, or that a rule
        # without a probe tripped: they do not pass (incidents).
        self.failing: set[str] = set()
        # Programs failing for H4: MISSES_TO_WARN misses or more, or a
        # section past half its trip.
        self.half: set[str] = set()

    def run(self) -> None:
        """Run the phases of the cycle, in order."""
        state = self.state
        if state.started_mono is None:
            state.started_mono = self.mono
        if state.summary_mono is None:
            state.summary_mono = self.mono
        state.ledger.prune(self.wall)
        self._track_gate()
        self._track_pause()
        self._track_programs()
        if self.starved:
            state.near_miss.starved_cycles += 1
            return
        self._check_pids()
        self._liveness_trips()
        self._act_on_trips()
        self._update_incidents()

    # Alerts.

    def _alert(self, key: tuple[str, str], level: str, text: str,
               change: bool = False, holds: Sequence[str] = (),
               keep_open: bool = False) -> None:
        """Emit an alert, at most once per ALERT_REPEAT_S for its key.

        key: (program name or "-", topic).
        level: "INFO", "WARNING" or "ERROR".
        text: the line.
        change: whether it is a state change, emitted at once.
        holds: for a held trip, the holds that apply.
        keep_open: whether it stays open (and is sent again to a new
            LogService) until it is resolved.

        """
        decision = Decision(ALERT, text=text, level=level, key=key,
                            holds=tuple(holds))
        if keep_open:
            self.state.open_alerts[key] = decision
        last = self.state.alert_sent.get(key)
        if change or last is None or self.mono - last >= ALERT_REPEAT_S:
            self.state.alert_sent[key] = self.mono
            self.decisions.append(decision)

    def _resolve_key(self, key: tuple[str, str], text: str) -> None:
        """Close the alert key with a "Resolved:" line."""
        for table in (self.state.open_alerts, self.state.alert_sent,
                      self.state.held_headline, self.state.held_announced):
            table.pop(key, None)
        self.decisions.append(Decision(ALERT, text=text, key=key))

    def _resolve_program(self, name: str, text: str) -> None:
        """Close every alert of a program with a "Resolved:" line."""
        for table in (self.state.open_alerts, self.state.alert_sent,
                      self.state.held_headline, self.state.held_announced):
            for key in [key for key in table if key[0] == name]:
                del table[key]
        self.decisions.append(
            Decision(ALERT, text=text, key=(name, "resolved")))

    # Bookkeeping.

    def _track_gate(self) -> None:
        """Keep the red intervals, the green streak and their alerts.

        A starved cycle only counts a red gate, unless PostgreSQL itself
        answered it (GATE_ANSWERED_REASONS).

        """
        state, gate = self.state, self.obs.gate
        if self.starved and (gate.green
                             or gate.reason not in GATE_ANSWERED_REASONS):
            # A starved cycle's gate may have failed because of the
            # watchdog's own starvation (a timeout), and a green one
            # proves nothing in a cycle that decides nothing: neither
            # opens nor closes a red interval. A red that PostgreSQL
            # answered is real and is tracked below. The statistics
            # count every red.
            if not gate.green:
                state.near_miss.red_cycles += 1
            return
        ledger = state.ledger
        red_open = bool(ledger.red) and ledger.red[-1][1] is None
        key = ("-", "gate")
        if not gate.green:
            state.green_since_mono = None
            state.green_cycles = 0
            if not red_open:
                ledger.red.append([self.wall, None])
            state.near_miss.red_cycles += 1
            self._alert(
                key, "ERROR",
                "Watchdog: the database gate is red (%s: %s); the "
                "watchdog restarts nothing while PostgreSQL does not "
                "answer. See \"When the database misbehaves\"."
                % (gate.reason, gate.detail or "no detail"),
                change=not red_open, keep_open=True)
            return
        if red_open:
            started = ledger.red[-1][0]
            ledger.red[-1][1] = self.wall
            self._clear_windows()
            state.green_since_mono = self.mono
            state.green_cycles = 1
            self._resolve_key(
                key, "Resolved: the database gate is green again after "
                "%d s red; restarts wait %d s more."
                % (age(self.wall, started), GREEN_BEFORE_RESTART_S))
        elif state.green_since_mono is None:
            # The watchdog's first green cycle: it did not see the
            # database turn green, so H2 has nothing to wait for (H6
            # holds the watchdog's own startup).
            state.green_since_mono = self.mono - GREEN_BEFORE_RESTART_S
            state.green_cycles = 1
        else:
            state.green_cycles += 1

    def _track_pause(self) -> None:
        """Clear every window after a pause of the watchdog (3.3)."""
        gap = self.obs.since_previous_s
        if gap is not None and gap > PAUSE_S:
            self._clear_windows()
            self.decisions.append(Decision(
                ALERT, key=("-", "pause"),
                text="Watchdog: paused for %d s (a host, VM or container "
                     "pause); its miss windows were cleared." % gap))

    def _clear_windows(self) -> None:
        """Forget every known liveness result."""
        for program_state in self.state.programs.values():
            program_state.window.clear()

    def _track_programs(self) -> None:
        """Note each running program's pid, history and liveness."""
        for program in self.running:
            program_state = self.state.programs.setdefault(
                program.name, ProgramState())
            if program_state.pid is not None \
                    and program_state.pid != program.pid:
                program_state.window.clear()
                self._forget_episodes(program.name)
            program_state.pid = program.pid
            program_state.history.append(self._history_entry(program))
            probe = self._liveness_probe(program)
            # A starved cycle's result is unknown: no miss, no pass.
            if probe is not None and not self.starved:
                program_state.window.append(not probe.ok)

    def _forget_episodes(self, name: str) -> None:
        """End the hold episodes of a program that has a new process.

        They belong to the old process: the new one's first held line is
        entering a hold. The rate limits of the program's alerts stay,
        or a program that supervisord starts again every few seconds
        (another process holds its port) would give a line per cycle;
        so do its incident and its open alerts.

        name: the program's name.

        """
        for table in (self.state.held_headline, self.state.held_announced):
            for key in [key for key in table if key[0] == name]:
                del table[key]

    def _liveness_probe(self, program: ProgramFacts) -> Probe | None:
        """Return the probe that R1 or R2 judges program on, if any."""
        if program.kind in HTTP_KINDS:
            return self.obs.http.get(program.name)
        if program.kind in RPC_RULE_KINDS:
            return self.obs.rpc.get(program.name)
        return None

    def _history_entry(self, program: ProgramFacts) -> dict:
        """Describe what this cycle saw of program, for the evidence."""
        entry: dict = {"wall": self.wall, "pid": program.pid,
                       "starved": self.starved}
        for channel, probes in (("http", self.obs.http),
                                ("rpc", self.obs.rpc)):
            probe = probes.get(program.name)
            if probe is not None:
                entry[channel] = asdict(probe)
        sections = busy_sections(self.obs.health.get(program.name))
        if sections:
            entry["busy"] = sections
        return entry

    def _check_pids(self) -> None:
        """Alert when another process answers on a program's port."""
        for program in self.running:
            payload = self.obs.health.get(program.name)
            pid = (payload.get("pid") if isinstance(payload, Mapping)
                   else None)
            if isinstance(pid, int) and not isinstance(pid, bool) \
                    and program.pid is not None and pid != program.pid:
                self._alert(
                    (program.name, "pid"), "WARNING",
                    "Watchdog: pid %d answers on the RPC port of %s, "
                    "which supervisord started as pid %d; the watchdog "
                    "does nothing about it. Check ./status.sh and ps."
                    % (pid, program.label, program.pid))

    # R1 and R2.

    def _liveness_trips(self) -> None:
        """Apply R1 and R2, with their early warnings (3.5)."""
        for program in self.running:
            if program.kind in HTTP_KINDS:
                rule, what = "R1", "favicon probes"
            elif program.kind in RPC_RULE_KINDS:
                rule, what = "R2", "health() calls"
            else:
                continue
            probe = self._liveness_probe(program)
            missed_now = probe is not None and not probe.ok
            if missed_now:
                self.failing.add(program.name)
            window = self.state.programs[program.name].window
            misses = sum(window)
            if misses >= MISSES_TO_WARN:
                self.half.add(program.name)
            # A program that answers in this cycle is never restarted in
            # it, even when a hold has just lifted over 5 old misses.
            if misses >= MISSES_TO_TRIP and missed_now:
                self.trips.append(Trip(
                    program, rule, "%d of its last %d %s failed"
                    % (misses, len(window), what)))
            elif misses >= MISSES_TO_WARN:
                self.warnings.append(Trip(
                    program, rule, "%d of the last %d %s of %s failed"
                    % (misses, len(window), what, program.label)))

    # Holds and actions.

    def _act_on_trips(self) -> None:
        """Turn each trip into an action, or into an alert if held."""
        self._early_warnings()
        held_workers: list[Trip] = []
        incidents = self.state.ledger.incidents
        for trip in self.trips:
            program = trip.program
            opened = program.name not in incidents
            if opened:
                incidents[program.name] = {
                    "rule": trip.rule, "opened": self.wall, "passes": 0}
            shadow = self.state.shadow_for(program.klass)
            holds = self._holds(program, shadow)
            if holds and holds[0][0] == "H4" and program.kind == "Worker":
                held_workers.append(trip)
            elif holds:
                self._held(trip, holds, opened)
            else:
                self._restart(trip, shadow)
        if held_workers:
            self._held_workers(held_workers)
        elif not any(trip.program.kind == "Worker" for trip in self.trips):
            # No Worker trips: the Workers' H4 episode is over, and the
            # next one is entering a hold again. Another hold heading
            # their trips for a while does not end it.
            for table in (self.state.held_headline,
                          self.state.alert_sent):
                table.pop(WORKERS_H4_KEY, None)

    def _early_warnings(self) -> None:
        """Emit the early warnings of the cycle (3.5, 3.9)."""
        for warning in self.warnings:
            program = warning.program
            self._alert(
                (program.name, warning.rule + " warning"), "WARNING",
                "Watchdog: early warning: %s. %s"
                % (warning.detail, self._outlook(program)))

    def _outlook(self, program: ProgramFacts) -> str:
        """Say what happens if program goes on failing, and the next step.

        It does not promise a restart that observe mode, an H9 class or
        a hold that applies now would not give.

        program: the program with an early warning.

        return: the sentences to add to the warning.

        """
        shadow = self.state.shadow_for(program.klass)
        holds = self._holds(program, shadow)
        if self.state.mode == OBSERVE:
            what = "only logs the restart it would do (observe mode)"
        elif shadow:
            what = ("only logs the restart it would do (class %s is not "
                    "in CMS_WATCHDOG_RESTART)" % program.klass)
        elif holds:
            what = "would restart it"
        else:
            what = "restarts it"
        text = ("At %d misses, the latest included, the watchdog %s."
                % (MISSES_TO_TRIP, what))
        if holds:
            text += " But %s applies now: %s." % holds[0]
            text += _other_holds(holds)
        return text + (" Check ./logs.sh for its log; cmsWatchdog "
                       "--snapshot %s writes its stacks." % program.name)

    def _holds(self, program: ProgramFacts,
               shadow: bool) -> list[tuple[str, str]]:
        """Return the holds that apply to a trip of program, in order.

        program: the program that tripped.
        shadow: whether its action goes to the shadow ledger (H9).

        return: (code, reason) of each hold that applies. H9 is not in
            it: it turns the action into a would-action instead.

        """
        obs, state = self.obs, self.state
        holds = []
        if not obs.supervisor_ok:
            holds.append(("H1", "supervisord did not answer in this cycle"))
        if not obs.gate.green:
            holds.append(("H2", "the database gate is red (%s)"
                          % obs.gate.reason))
        elif self.mono - state.green_since_mono < GREEN_BEFORE_RESTART_S:
            back = self.mono - state.green_since_mono
            holds.append(("H2", "the database has been back for only %d s; "
                          "restarts wait %d s"
                          % (back, GREEN_BEFORE_RESTART_S)))
        for code, reason in (
                ("H3", self._lock_wait()),
                ("H4", self._correlation(program)),
                ("H5", self._global_failure()),
                ("H6", self._grace(program)),
                ("H7", self._budget(program, RESTART, shadow))):
            if reason:
                holds.append((code, reason))
        if self.acted or obs.in_flight:
            holds.append(("H8", "another action is in flight"))
        return holds

    def _lock_wait(self) -> str:
        """Describe the lock waits of CMS backends (H3), or ""."""
        gate = self.obs.gate
        if not gate.green:
            return ""
        by_pid = {backend.pid: backend for backend in gate.backends}
        # A statement age that is not a finite number is unknown: it
        # does not make a wait.
        waiters = sorted(
            (backend for backend in gate.backends
             if (backend.application_name or "").startswith("cms")
             and backend.wait_event_type == "Lock"
             and is_number(backend.query_age_s)
             and backend.query_age_s > LOCK_WAIT_S),
            key=lambda backend: -backend.query_age_s)
        if not waiters:
            return ""
        waits = []
        for backend in waiters[:LOCK_WAIT_LISTED]:
            blockers = "; ".join(
                self._blocker(by_pid.get(pid), pid)
                for pid in backend.blocked_by[:LOCK_WAIT_LISTED]) \
                or "a session the snapshot does not show"
            if len(backend.blocked_by) > LOCK_WAIT_LISTED:
                blockers += "; and %d more blockers" % (
                    len(backend.blocked_by) - LOCK_WAIT_LISTED)
            waits.append(
                "'%s' (pid %d): its statement has been waiting %d s on a "
                "lock held by %s"
                % (backend.application_name, backend.pid,
                   backend.query_age_s, blockers))
        if len(waiters) > LOCK_WAIT_LISTED:
            waits.append("and %d more sessions are waiting too"
                         % (len(waiters) - LOCK_WAIT_LISTED))
        return ("a database lock wait: %s. Ending a blocker is a human "
                "decision" % "; ".join(waits))

    def _blocker(self, backend: Backend | None, pid: int) -> str:
        """Describe one blocker of a lock wait (H3)."""
        if backend is None:
            return "pid %d (not in the snapshot)" % pid

        def shown(value: object, form: str = "%s") -> str:
            return "unknown" if value is None else form % value

        xact_age_s = backend.xact_age_s
        if not is_number(xact_age_s):
            xact_age_s = None

        text = "pid %d ('%s', %s, transaction open %s)" % (
            pid, shown(backend.application_name), shown(backend.state),
            shown(xact_age_s, "%d s"))
        orphan_of = self._orphan_of(backend)
        if orphan_of is not None:
            text += ", orphan of pid %d" % orphan_of
        return text

    def _orphan_of(self, backend: Backend) -> int | None:
        """Return the old pid if backend is an orphan of a restart (H3).

        It is one when its application_name is that of a program the
        watchdog restarted and it started more than GATE_KILL_S before
        that program's new process: the new process has the same
        application_name, so a younger backend may be its own.

        backend: a blocker.

        return: the pid the watchdog restarted, or None.

        """
        if backend.application_name is None \
                or not is_number(backend.backend_age_s):
            return None
        restart = self.state.ledger.last_restart_of(
            backend.application_name)
        program = next((p for p in self.obs.programs if p.managed
                        and p.label == backend.application_name), None)
        if restart is None or program is None \
                or program.start_wall is None:
            return None
        # The snapshot's ages are measured a little after the cycle
        # starts: a backend the new process opened as it started must
        # not look older than the process.
        if self.wall - backend.backend_age_s \
                < program.start_wall - GATE_KILL_S:
            return restart.pid
        return None

    def _correlation(self, program: ProgramFacts) -> str:
        """Describe a failure of program's whole class (H4), or ""."""
        if program.kind == "Worker":
            tripped = sorted({
                trip.program.label for trip in self.trips
                if trip.program.kind == "Worker"
                and not self._over_budget(trip.program)})
            if len(tripped) >= 2:
                return "%d Workers trip together (%s)" % (
                    len(tripped), ", ".join(tripped))
            return ""
        failing = sorted(
            other.label for other in self.running
            if other.kind != "Worker" and other.klass == program.klass
            and other.name in self.half and not self._over_budget(other))
        if len(failing) >= 2:
            return "%d programs of class %s fail together (%s)" % (
                len(failing), program.klass, ", ".join(failing))
        return ""

    def _over_budget(self, program: ProgramFacts) -> bool:
        """Tell whether program used its own restart budget (H4)."""
        return self.state.ledger.count(
            program.name, RESTART, self.state.shadow_for(program.klass),
            self.wall) >= PROGRAM_BUDGET

    def _global_failure(self) -> str:
        """Describe a failure of a third of the probed programs (H5)."""
        probed = [program for program in self.running
                  if program.kind in HEALTH_KINDS
                  and (program.name in self.obs.http
                       or program.name in self.obs.rpc)]
        failed = [program for program in probed
                  if any(not probes[program.name].ok
                         for probes in (self.obs.http, self.obs.rpc)
                         if program.name in probes)]
        if probed and len(failed) > GLOBAL_FAILING_FRACTION * len(probed):
            return ("%d of the %d programs probed failed a probe in this "
                    "cycle" % (len(failed), len(probed)))
        return ""

    def _grace(self, program: ProgramFacts) -> str:
        """Describe why program is still in its grace (H6), or ""."""
        since_start = self.mono - self.state.started_mono
        if since_start < GRACE_S:
            return "the watchdog itself started %d s ago" % since_start
        if program.start_wall is not None:
            running_for = age(self.wall, program.start_wall)
            if running_for < GRACE_S:
                return "%s started %d s ago" % (program.label, running_for)
        program_state = self.state.programs.get(program.name)
        if program_state is not None \
                and program_state.last_action_mono is not None:
            since_action = self.mono - program_state.last_action_mono
            if since_action < GRACE_S:
                return ("its last action or would-action was %d s ago"
                        % since_action)
        return ""

    def _budget(self, program: ProgramFacts, action: str,
                shadow: bool) -> str:
        """Describe why the budget holds action on program (H7), or ""."""
        ledger = self.state.ledger
        free = ledger.free_at(program.name, pool_of(program.klass), action,
                              shadow, self.wall)
        if free is None:
            return ""
        if ledger.exhausted_until is not None \
                and self.wall < ledger.exhausted_until:
            why = "its ledger did not parse, so every budget counts as used"
        elif action == RESTART:
            why = ("at most %d restarts of a program, and %d of its pool, "
                   "in 60 minutes" % (PROGRAM_BUDGET, POOL_BUDGET))
        else:
            why = ("at most %d starts of a FATAL program in 60 minutes"
                   % FATAL_PROGRAM_BUDGET)
        return "will not %s %s before %s: %s%s" % (
            "restart" if action == RESTART else "start", program.label,
            hhmm_utc(free), why,
            " (in the shadow ledger)" if shadow else "")

    def _held(self, trip: Trip, holds: list[tuple[str, str]],
              opened: bool) -> None:
        """Alert about a held trip, headed by its first hold."""
        program = trip.program
        key = (program.name, trip.rule)
        headline, reason = holds[0]
        codes = [code for code, _ in holds]
        previous = self.state.held_headline.get(key)
        self.state.held_headline[key] = headline
        text = "Watchdog: %s fails %s (%s) but is held by %s: %s." % (
            program.label, trip.rule, trip.detail, headline, reason)
        text += _other_holds(holds)
        text += " " + HOLD_HINTS[headline]
        # Entering a hold is a state change, and so is the first time an
        # urgent hold heads it; any other change of headline within one
        # is not, or a flapping hold would give a line per cycle.
        urgent = False
        if headline in URGENT_HOLDS:
            announced = self.state.held_announced.setdefault(key, set())
            urgent = headline not in announced
            announced.add(headline)
        self._alert(key, "WARNING", text,
                    change=opened or previous is None or urgent,
                    holds=codes, keep_open=True)

    def _held_workers(self, trips: list[Trip]) -> None:
        """Alert once about the Workers that H4 holds together."""
        key = WORKERS_H4_KEY
        previous = self.state.held_headline.get(key)
        self.state.held_headline[key] = "H4"
        labels = ", ".join(sorted(trip.program.label for trip in trips))
        self._alert(
            key, "WARNING",
            "Watchdog: %d Workers trip R4 together (%s) and are held by "
            "H4: a correlated failure is left to a human. %s"
            % (len(trips), labels, HOLD_HINTS["H4"]),
            change=previous != "H4", holds=("H4",))

    def _restart(self, trip: Trip, shadow: bool) -> None:
        """Decide a restart, or a would-restart, and book it."""
        program = trip.program
        would = self.state.mode == OBSERVE or shadow
        self.state.ledger.book(Booking(
            self.wall, program.name, program.label, pool_of(program.klass),
            RESTART, trip.rule, would, program.pid), shadow)
        self.acted = True
        program_state = self.state.programs[program.name]
        program_state.last_action_mono = self.mono
        if not would:
            program_state.restarted_mono = self.mono
            program_state.restarted_pid = program.pid
        key = (program.name, trip.rule)
        self.state.held_headline.pop(key, None)
        self.state.held_announced.pop(key, None)
        self.state.alert_sent[key] = self.mono
        self.decisions.append(Decision(
            RESTART, text=trip.detail, key=key, program=program.name,
            rule=trip.rule, would=would, shadow=shadow))

    # Incidents.

    def _update_incidents(self) -> None:
        """Count the passes of the open incidents; close them after 2."""
        tripped = {trip.program.name for trip in self.trips}
        by_name = {program.name: program for program in self.obs.programs}
        incidents = self.state.ledger.incidents
        for name, incident in list(incidents.items()):
            program = by_name.get(name)
            if program is None or program.state != "RUNNING":
                continue
            if name in tripped or name in self.failing:
                incident["passes"] = 0
                continue
            incident["passes"] += 1
            if incident["passes"] >= PASSES_TO_RESOLVE:
                del incidents[name]
                self._resolve_program(
                    name, "Resolved: %s passes its checks again "
                    "(incident of %s, opened at %s)." % (
                        program.label, incident["rule"],
                        hhmm_utc(incident["opened"])))
