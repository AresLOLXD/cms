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
# More than this since the previous cycle, plus the lag a cycle may have
# (STARVED_LAG_S), is a pause: every miss window is cleared.
PAUSE_S = 30.0

# The probes (3.4).
HTTP_TIMEOUT_S = 5.0
RPC_TIMEOUT_S = 3.0
RANKING_TIMEOUT_S = 5.0
GATE_CONNECT_TIMEOUT_S = 3
GATE_STATEMENT_TIMEOUT_MS = 3000
GATE_KILL_S = 7.0
LOG_ALERT_TIMEOUT_S = 2.0
# The bound of one cycle's flush of alerts to LogService, however slowly
# it answers: one call that times out, with room to spare.
LOG_FLUSH_LIMIT_S = 4.0

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

# The alert key of the line of the Workers that a hold holds together:
# one hold episode, whatever its headline. WORKERS_GROUP is its first
# element.
WORKERS_GROUP = "workers"
WORKERS_KEY = (WORKERS_GROUP, "R4")
# A group line (the Workers', R5's) names at most this many programs,
# and this many reasons of its headline hold, so that it stays a line.
WORKERS_LISTED = 3
# The alert key of the line of R5's held starts: the batch is one
# action, so its held starts share one hold episode, whatever its
# headline. A START decision has the same key.
FATAL_KEY = ("-", "R5")
# The alert key of the line of the FATAL programs that R5 leaves to a
# human.
FATAL_LEFT_KEY = ("-", "R5 left to you")
# What R5's lines say tripped.
FATAL_DETAIL = "FATAL after a database outage"

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
        invalidation), one entry per section: (what, its first start as
        epoch seconds, its largest age_s, its largest idle_s), by a key
        made of its holder thread and its first start; a start within
        CYCLE_S of it is the same section. A section seen on many
        cycles stays one entry, so the summary grows with the number of
        such sections only, and lists every one of them.
    ss_longest: (age_s, execution key) of the longest SS execution.
    ss_starts: the first start of each SS execution seen (epoch
        seconds), by its key, made like those of es_half.
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
    ss_starts: dict[str, float] = field(default_factory=dict)
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
    workers_quiet: while the Workers' hold episode (WORKERS_KEY) is
        open, the non-starved cycles in a row in which no Workers were
        held together; the episode ends at PASSES_TO_RESOLVE of them.
    open_alerts: the last alert of each open problem, by key (sent
        again to a new LogService).
    near_miss: this hour's statistics.
    summary_mono: when the last hourly summary was emitted.
    fatal_left: the FATAL programs left to a human in the last
        non-starved cycle; one that joins them is told at once.

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
    workers_quiet: int = 0
    fatal_left: set[str] = field(default_factory=set)

    def shadow_for(self, klass: str) -> bool:
        """Tell whether an action on klass goes to the shadow (H9)."""
        return self.mode == ENFORCE and klass not in self.restart_classes


@dataclass(frozen=True)
class Trip:
    """A rule that tripped for a program in this cycle."""

    program: ProgramFacts
    rule: str
    detail: str


# The next step that an alert names for each headline hold (3.9); H6's
# depends on the clause that applied (GRACE_HINTS).
HOLD_HINTS: dict[str, str] = {
    "H1": "Check ./status.sh.",
    "H2": ("Nothing to do: the watchdog waits for the database (see "
           "\"When the database misbehaves\")."),
    "H3": ("End the blocker by hand only if it is stuck (see \"When the "
           "watchdog alerts\")."),
    "H4": ("Find what the failing programs share before restarting any "
           "of them by hand."),
    "H5": "Check the load of the server and of the database.",
    "H7": "Restart it by hand if needed, after cmsWatchdog --snapshot.",
    "H8": "Nothing to do: another action is running.",
}
# The next step of the line about Workers held together: the hints
# about one program, said of several.
WORKERS_HOLD_HINTS: dict[str, str] = {
    **HOLD_HINTS,
    "H7": "Restart them by hand if needed, after cmsWatchdog --snapshot.",
}
# The next step of a line headed by H6, by the clause of _grace that
# applied: only in "started" is the program starting.
GRACE_HINTS: dict[str, str] = {
    "watchdog": "Nothing to do yet: the watchdog is still starting.",
    "started": "Nothing to do yet: it is still starting.",
    "action": ("The watchdog judges it again %d s after its last action "
               "or would-action." % GRACE_S),
}
# The same, said of several Workers.
WORKERS_GRACE_HINTS: dict[str, str] = {
    **GRACE_HINTS,
    "started": "Nothing to do yet: they are still starting.",
    "action": ("The watchdog judges each again %d s after its last action "
               "or would-action on it." % GRACE_S),
}
# What the "action" hint adds where the watchdog restarts nothing
# (observe mode, a class of H9): only a human can restart there.
BY_HAND_HINT = ("It restarts nothing here: decide by hand, after "
                "cmsWatchdog --snapshot.")
# The next step of a held R5 start: a FATAL program runs no process, so
# it is not starting (H6 holds R5 for the watchdog's own startup only),
# and cmsWatchdog --snapshot has no stacks to write; its log says why it
# failed.
FATAL_HOLD_HINTS: dict[str, str] = {
    **HOLD_HINTS,
    "H6": "Nothing to do yet: R5 decides once the watchdog's own grace ends.",
    "H7": ("Check ./logs.sh, then start it by hand with supervisorctl if "
           "needed."),
}
# The same, said of several programs.
FATAL_GROUP_HOLD_HINTS: dict[str, str] = {
    **FATAL_HOLD_HINTS,
    "H7": ("Check ./logs.sh, then start them by hand with supervisorctl "
           "if needed."),
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


def _listed(items: Sequence[str], separator: str) -> str:
    """Join the first WORKERS_LISTED items, then say how many are left.

    items: what to list, in order.
    separator: what goes between two of them.

    return: "a, b, c, and 2 more" for five items and ", ".

    """
    text = separator.join(items[:WORKERS_LISTED])
    if len(items) > WORKERS_LISTED:
        text += "%sand %d more" % (separator, len(items) - WORKERS_LISTED)
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
        # Programs that a rule could not judge in this cycle (R4 without
        # ES's view of them or with ES up for less than 120 s, R3 on a
        # send while a ranking does not answer or none is probed):
        # unknown, neither a pass nor a miss of their incident.
        self.unjudged: set[str] = set()
        # The Workers among them that ES reports not connected, or
        # disabled by timeout, while R4 cannot trip them yet: not a miss,
        # but no pass either ("did it help" does not say they answer).
        self.unconnected: set[str] = set()

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
        self._record_holder_cpu()
        self._near_miss()
        if self.starved:
            state.near_miss.starved_cycles += 1
            self._summary()
            return
        self._check_pids()
        self._liveness_trips()
        self._section_trips()
        self._worker_trips()
        self._ranking_alerts()
        self._absent_alerts()
        # Before the actions: a restart decided in this cycle would
        # replace the one whose outcome this line reports.
        self._did_it_help()
        # Before the restarts: after an outage, starting what it left
        # FATAL comes first, and a restart in the same cycle gets H8.
        self._fatal_starts()
        self._act_on_trips()
        self._update_incidents()
        self._summary()

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
        self._close_alerts(name)
        self.decisions.append(
            Decision(ALERT, text=text, key=(name, "resolved")))

    def _close_alerts(self, name: str) -> None:
        """Forget every alert of a program, with no line."""
        for table in (self.state.open_alerts, self.state.alert_sent,
                      self.state.held_headline, self.state.held_announced):
            for key in [key for key in table if key[0] == name]:
                del table[key]

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
        """Clear every window after a pause of the watchdog (3.3).

        By the schedule, a cycle that ends less than PAUSE_S seconds
        after its slot (its own lag plus its duration) is followed
        PAUSE_S seconds after its start at most, plus the next cycle's
        lag: a timer's overshoot, up to STARVED_LAG_S before that cycle
        is starved. Only a longer gap is a pause. A cycle that ends
        later skips one more slot, so the next one can look like a
        pause.

        """
        gap = self.obs.since_previous_s
        if gap is not None and gap > PAUSE_S + STARVED_LAG_S:
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
        """End the hold episodes of a program.

        It runs when the program has a new process, and when the
        watchdog restarts it or would restart it, whatever rule tripped:
        the episodes belong to the old process, or to the trip that was
        acted on, and the program's next held line is entering a hold.
        The rate limits of the program's alerts stay, or a program that
        supervisord starts again every few seconds (another process
        holds its port) would give a line per cycle; so do its incident
        and its open alerts.

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

    # R3.

    def _record_holder_cpu(self) -> None:
        """Keep the CPU samples of ES's holder thread (R3 (b))."""
        samples = [sample for sample in self.state.holder_samples
                   if self.mono - sample[0] <= ES_CPU_KEEP_S]
        if self.obs.holder_cpu is not None:
            tid, cpu_s = self.obs.holder_cpu
            samples.append((self.mono, tid, cpu_s))
        self.state.holder_samples = samples

    def _section_trips(self) -> None:
        """Apply R3 to the sections of ES, SS and PS (3.5).

        A section past half its trip adds its program to self.half (H4)
        and, if it does not trip, an early warning to self.warnings:
        _act_on_trips emits them with R1's and R2's.

        """
        for program in self.running:
            sections = busy_sections(self.obs.health.get(program.name))
            if not sections:
                continue
            if program.kind == "EvaluationService":
                self._es_section(program, sections[0])
            elif program.kind == "ScoringService":
                self._ss_section(program, sections)
            elif program.kind == "ProxyService":
                self._ps_section(program, sections)

    def _es_section(self, program: ProgramFacts, section: dict) -> None:
        """Apply R3 to ES's oldest section."""
        what, age_s = section["what"], section["age_s"]
        idle_s = section["idle_s"]
        if not what.startswith(ES_SECTION_PREFIX):
            return
        if what == INVALIDATION_SECTION:
            if age_s > SECTION_TRIP_S:
                self._alert(
                    (program.name, "re-evaluation"), "WARNING",
                    "Watchdog: re-evaluation still running for %d min in "
                    "%s; the watchdog never restarts it for that."
                    % (age_s // 60, program.label))
            return
        if age_s <= SECTION_WARN_S or idle_s < ES_IDLE_WARN_S:
            return
        self.half.add(program.name)
        if age_s > SECTION_TRIP_S and idle_s >= ES_IDLE_TRIP_S \
                and self._holder_idle(section["tid"]) \
                and not self._es_statement_running(program):
            self.trips.append(Trip(
                program, "R3", "its locked section %s has been held for "
                "%d s with no statement for %d s, under %d s of CPU in "
                "the last minute and no statement of it running in the "
                "database, apart from a lock wait"
                % (what, age_s, idle_s, ES_CPU_MIN_S)))
            return
        self.warnings.append(Trip(
            program, "R3", "the locked section %s of %s has been held for "
            "%d s with no statement for %d s" % (
                what, program.label, age_s, idle_s)))

    def _holder_idle(self, tid: int | None) -> bool:
        """R3 (b): the holder used under 1 s of CPU in the last minute.

        It compares with the newest sample of the same thread that is
        at least ES_CPU_LOOKBACK_S old; without one, (b) is not met.

        """
        current = self.obs.holder_cpu
        if tid is None or current is None or current[0] != tid:
            return False
        old = [sample for sample in self.state.holder_samples
               if sample[1] == tid
               and self.mono - sample[0] >= ES_CPU_LOOKBACK_S]
        if not old:
            return False
        newest = max(old, key=lambda sample: sample[0])
        return current[1] - newest[2] < ES_CPU_MIN_S

    def _es_statement_running(self, program: ProgramFacts) -> bool:
        """R3 (c): whether a backend of ES is running a statement.

        A lock wait (left to H3) and a write to a client that does not
        read (ClientWrite) do not count; a NULL field counts as "not
        Lock" and "not ClientWrite". With a red gate there is no
        snapshot, and it counts as running.

        """
        if not self.obs.gate.green:
            return True
        return any(backend.application_name == program.label
                   and backend.state == "active"
                   and backend.wait_event_type != "Lock"
                   and backend.wait_event != "ClientWrite"
                   for backend in self.obs.gate.backends)

    def _ss_section(self, program: ProgramFacts,
                    sections: list[dict]) -> None:
        """Apply R3 to SS's scoring."""
        ages = [section["age_s"] for section in sections
                if section["what"] == "score"]
        if not ages or max(ages) <= SECTION_WARN_S:
            return
        self.half.add(program.name)
        if max(ages) > SECTION_TRIP_S:
            self.trips.append(Trip(program, "R3", "one scoring has run "
                                   "for %d s" % max(ages)))
            return
        self.warnings.append(Trip(
            program, "R3", "one scoring of %s has run for %d s"
            % (program.label, max(ages))))

    def _ps_section(self, program: ProgramFacts,
                    sections: list[dict]) -> None:
        """Apply R3 to PS's sends to the rankings."""
        idles = [section["idle_s"] for section in sections
                 if section["what"] == "send"]
        if not idles or max(idles) <= SECTION_WARN_S:
            return
        self.half.add(program.name)
        if max(idles) > SECTION_TRIP_S:
            rankings = self.obs.rankings
            if rankings and all(probe.ok for probe in rankings.values()):
                self.trips.append(Trip(
                    program, "R3", "a send to the rankings has started no "
                    "request for %d s while every ranking answers"
                    % max(idles)))
                return
            # A ranking that does not answer may be why the send is
            # stuck, and with no ranking probed nothing says it is not.
            self.unjudged.add(program.name)
        self.warnings.append(Trip(
            program, "R3", "a send of %s to the rankings has started no "
            "request for %d s" % (program.label, max(idles))))

    # R4.

    def _worker_trips(self) -> None:
        """Apply R4 to the Workers, from ES's workers_status() (3.5)."""
        es = next((program for program in self.running
                   if program.kind == "EvaluationService"), None)
        workers = self.obs.workers
        # An unhealthy ES suppresses R4: its view of the Workers is old,
        # and R4 cannot judge any of them.
        if es is None or not isinstance(workers, Mapping) \
                or not self.obs.rpc.get(es.name, Probe(False)).ok:
            self.unjudged.update(program.name for program in self.running
                                 if program.kind == "Worker")
            return
        statuses = {str(shard): status for shard, status in workers.items()
                    if isinstance(status, Mapping)}
        es_health = self.obs.health.get(es.name)
        es_uptime = (es_health.get("uptime_s")
                     if isinstance(es_health, Mapping) else None)
        if is_number(es_uptime) and es_uptime < ES_UP_FOR_WORKERS_S:
            # A young ES has disabled no Worker yet, and (b) waits for
            # it: R4 cannot judge any Worker for now.
            self.unjudged.update(program.name for program in self.running
                                 if program.kind == "Worker")
            return
        for program in self.running:
            if program.kind != "Worker":
                continue
            status = statuses.get(str(program.shard))
            if status is None:
                self.unjudged.add(program.name)
                continue
            detail = self._disabled_too_long(program, status) \
                or self._never_connected(program, status, statuses,
                                         es_uptime)
            if detail:
                self.failing.add(program.name)
                self.trips.append(Trip(program, "R4", detail))
            elif status.get("connected") is not True \
                    or status.get("disabled_by_timeout") is True:
                # R4 cannot trip it yet ((a) needs a process that started
                # before disabled_at, (b) WORKER_UNCONNECTED_TRIP_S of
                # running), and ES does not use it: no pass.
                self.unjudged.add(program.name)
                self.unconnected.add(program.name)

    def _disabled_too_long(self, program: ProgramFacts,
                           status: Mapping) -> str:
        """R4 (a): ES disabled it 180 s ago, and it did not restart."""
        disabled_at = status.get("disabled_at")
        if status.get("disabled_by_timeout") is not True \
                or not is_number(disabled_at) \
                or program.start_wall is None \
                or program.start_wall >= disabled_at:
            return ""
        disabled_for = age(self.wall, disabled_at)
        if disabled_for < WORKER_DISABLED_TRIP_S:
            return ""
        return ("EvaluationService disabled it by timeout %d s ago, and "
                "it has not restarted since" % disabled_for)

    def _never_connected(self, program: ProgramFacts, status: Mapping,
                         statuses: Mapping[str, Mapping],
                         es_uptime: object) -> str:
        """R4 (b): not connected after 300 s, while others are.

        A Worker that ES disabled less than WORKER_DISABLED_TRIP_S ago
        is left to (a): it has just been told to quit, and it
        disconnects as it does.

        """
        disabled_at = status.get("disabled_at")
        if status.get("disabled_by_timeout") is True \
                and is_number(disabled_at) \
                and age(self.wall, disabled_at) < WORKER_DISABLED_TRIP_S:
            return ""
        others = any(other.get("connected") is True
                     for shard, other in statuses.items()
                     if shard != str(program.shard))
        # _worker_trips judges no Worker while ES is up for less than
        # ES_UP_FOR_WORKERS_S.
        if status.get("connected") is not False or not others \
                or program.start_wall is None \
                or not is_number(es_uptime):
            return ""
        running_for = age(self.wall, program.start_wall)
        if running_for < WORKER_UNCONNECTED_TRIP_S:
            return ""
        return ("it has not connected to EvaluationService in %d s of "
                "running, while other Workers have" % running_for)

    # Rankings.

    def _ranking_alerts(self) -> None:
        """Alert about a ranking that does not answer (alert only)."""
        misses = self.state.ranking_misses
        for address, probe in self.obs.rankings.items():
            previous = misses.get(address, 0)
            misses[address] = 0 if probe.ok else previous + 1
            key = ("ranking " + address, "ranking")
            if misses[address] >= RANKING_MISSES_TO_ALERT:
                self._alert(
                    key, "WARNING",
                    "Watchdog: ranking %s does not answer (%d cycles in a "
                    "row: %s). The watchdog cannot restart it: check the "
                    "ranking container with ./status.sh and ./logs.sh "
                    "ranking." % (address, misses[address],
                                  probe.error or "no detail"),
                    change=misses[address] == RANKING_MISSES_TO_ALERT,
                    keep_open=True)
            elif probe.ok and previous >= RANKING_MISSES_TO_ALERT:
                self._resolve_key(
                    key, "Resolved: ranking %s answers again." % address)

    def _absent_alerts(self) -> None:
        """Alert about a program the /proc source no longer sees (3.2).

        Only the harness's /proc source reports ABSENT; it cannot start
        anything, so this is an alert and nothing else.

        """
        for program in self.obs.programs:
            if program.managed and program.state == "ABSENT":
                self._alert(
                    (program.name, "absent"), "WARNING",
                    "Watchdog: %s is gone: no process runs it any more, "
                    "and the /proc source cannot start it." % program.label)

    # R5.

    def _fatal_starts(self) -> None:
        """Apply R5: start what a database outage left FATAL (3.5).

        R5 obeys H1, H2 (its own FATAL_GREEN_CYCLES of green, read from
        green_cycles), H6 (the watchdog's own startup only), H7 (the
        FATAL budget), H8 and H9. A held start is a held trip: it opens
        the program's incident, and _held_starts gives the held starts
        of the cycle one line, the batch's. _left_to_you gives one line
        to the FATAL programs R5 cannot start.

        """
        fatal = sorted((program for program in self.obs.programs
                        if program.managed and program.state == "FATAL"),
                       key=lambda p: (CLASS_ORDER.index(p.klass), p.name))
        eligible = [program for program in fatal
                    if program.start_wall is not None
                    and self.state.ledger.red_since(
                        program.start_wall - FATAL_RED_LOOKBACK_S)]
        self._left_to_you([program for program in fatal
                           if program not in eligible])
        if not eligible:
            self._end_episode(FATAL_KEY)
            return
        if not self.obs.gate.green \
                or self.state.green_cycles < FATAL_GREEN_CYCLES:
            # R5 judges no start in this cycle: its hold episode stays.
            return
        since_start = self.mono - self.state.started_mono
        incidents = self.state.ledger.incidents
        batches: dict[bool, list[ProgramFacts]] = {False: [], True: []}
        held: list[tuple[ProgramFacts, list[tuple[str, str]]]] = []
        for program in eligible:
            shadow = self.state.shadow_for(program.klass)
            holds = []
            if not self.obs.supervisor_ok:
                holds.append(("H1",
                              "supervisord did not answer in this cycle"))
            if since_start < GRACE_S:
                holds.append(("H6", "the watchdog itself started %d s ago"
                              % since_start))
            budget = self._budget(program, START, shadow)
            if budget:
                holds.append(("H7", budget))
            if self.acted or self.obs.in_flight:
                holds.append(("H8", "another action is in flight"))
            if program.name not in incidents:
                incidents[program.name] = {
                    "rule": "R5", "opened": self.wall, "passes": 0}
            if holds:
                held.append((program, holds))
            else:
                batches[shadow].append(program)
        self._held_starts(held)
        for shadow in (False, True):
            batch = batches[shadow]
            if not batch:
                continue
            would = self.state.mode == OBSERVE or shadow
            for program in batch:
                self.state.ledger.book(Booking(
                    self.wall, program.name, program.label,
                    pool_of(program.klass), START, "R5", would), shadow)
                # Like a restart, a start ends the program's hold
                # episodes.
                self._forget_episodes(program.name)
            self.acted = True
            self.decisions.append(Decision(
                START, text=FATAL_DETAIL, key=FATAL_KEY,
                programs=tuple(program.name for program in batch),
                rule="R5", would=would, shadow=shadow))

    def _held_starts(
            self, held: list[tuple[ProgramFacts, list[tuple[str, str]]]]
    ) -> None:
        """Alert about the held starts of R5, in one line: the batch's.

        The batch is one action, so its held starts share one hold
        episode, FATAL_KEY, however many programs it holds and whatever
        its headline: _held_alert rate-limits it, and it ends in a cycle
        in which R5 holds no start. The line names the programs, then
        the reasons of the headline (H7 gives one per program), both
        capped at WORKERS_LISTED, and every other hold that applies to
        any of them. The headline is the first hold, in HOLDS order,
        that holds every one of them: H1, H6 and H8 hold every start or
        none, and H7 holds each on its own budget. Starts with no hold
        in common, which cannot happen today, would be headed by the
        first hold of any of them.

        held: (program, holds) of each held start, in class order.

        """
        if not held:
            self._end_episode(FATAL_KEY)
            return
        reasons: dict[str, list[str]] = {}
        for _program, holds in held:
            for code, reason in holds:
                known = reasons.setdefault(code, [])
                if reason not in known:
                    known.append(reason)
        codes = [{code for code, _reason in holds} for _program, holds in held]
        headline = next((code for code in HOLDS
                         if all(code in mine for mine in codes)),
                        min(reasons, key=HOLDS.index))
        why = _listed(reasons[headline], "; ")
        others = [(code, reasons[code][0]) for code in HOLDS
                  if code in reasons and code != headline]
        if len(held) == 1:
            text = "Watchdog: %s fails R5 (%s) but is held by %s: %s." % (
                held[0][0].label, FATAL_DETAIL, headline, why)
            hints = FATAL_HOLD_HINTS
        else:
            labels = _listed([program.label for program, _ in held], ", ")
            text = ("Watchdog: %d programs fail R5 together (%s: %s) and "
                    "are held by %s: %s." % (len(held), FATAL_DETAIL, labels,
                                             headline, why))
            hints = FATAL_GROUP_HOLD_HINTS
        text += _other_holds([(headline, why)] + others)
        text += " " + hints[headline]
        # A program that joins the batch is no state change: entering a
        # hold, and its first urgent headline, are.
        self._held_alert(FATAL_KEY, text,
                         [headline] + [code for code, _ in others], False)

    def _left_to_you(self, left: list[ProgramFacts]) -> None:
        """Alert about the FATAL programs R5 cannot start, in one line.

        R5 cannot start a FATAL program when the watchdog saw no
        database outage since it last started, among the outages it
        remembers (BUDGET_WINDOW_S after their end). The line is an
        ERROR kept open while any such program is FATAL, and closed
        with no line when none is. A program that joins it is a state
        change, and the line goes out at once with its name: it needs
        the operator now, whether it has just gone FATAL or R5 could
        start it in the previous cycle (its outage was forgotten). A
        program that leaves it, or the same programs, are not.

        left: the FATAL programs R5 cannot start, in class order.

        """
        joined = any(program.name not in self.state.fatal_left
                     for program in left)
        self.state.fatal_left = {program.name for program in left}
        if not left:
            self.state.open_alerts.pop(FATAL_LEFT_KEY, None)
            self.state.alert_sent.pop(FATAL_LEFT_KEY, None)
            return
        minutes = BUDGET_WINDOW_S // 60
        if len(left) == 1:
            text = ("Watchdog: %s is FATAL, and in the last %d minutes the "
                    "watchdog saw no database outage since it last "
                    "started: it is left to you, and R5 acts on it only "
                    "after a later database outage. Read ./logs.sh, fix "
                    "the cause, then start it with supervisorctl."
                    % (left[0].label, minutes))
        else:
            text = ("Watchdog: %d programs are FATAL (%s), and in the last "
                    "%d minutes the watchdog saw no database outage since "
                    "they last started: they are left to you, and R5 acts "
                    "on them only after a later database outage. Read "
                    "./logs.sh, fix the cause, then start them with "
                    "supervisorctl." % (
                        len(left),
                        _listed([program.label for program in left], ", "),
                        minutes))
        self._alert(FATAL_LEFT_KEY, "ERROR", text, change=joined,
                    keep_open=True)

    # After a restart.

    def _did_it_help(self) -> None:
        """Say whether each restart helped (3.7, step 8).

        At the first pass of a new pid, "X answers again"; after
        DID_IT_HELP_S without one, "X is still failing". Would-actions
        get no line: nothing was restarted. A program that a rule could
        not judge in this cycle gets no line in it: whether it answers
        is unknown. A Worker that ES does not use (self.unconnected)
        has not passed: it can only be still failing.

        """
        tripped = {trip.program.name for trip in self.trips}
        by_name = {program.name: program for program in self.obs.programs}
        unknown = self.unjudged - self.unconnected
        for name, program_state in self.state.programs.items():
            if program_state.restarted_mono is None or name in unknown:
                continue
            program = by_name.get(name)
            # A program the source no longer lists keeps the label of
            # its restart's booking.
            label = program.label if program is not None else next(
                (booking.label for booking in reversed(self.state.ledger.real)
                 if booking.program == name), name)
            elapsed = self.mono - program_state.restarted_mono
            if program is not None and program.state == "RUNNING" \
                    and program.pid != program_state.restarted_pid \
                    and name not in self.failing and name not in tripped \
                    and name not in self.unconnected:
                text = "Watchdog: %s answers again %d s after the " \
                    "restart." % (label, elapsed)
            elif elapsed >= DID_IT_HELP_S:
                text = ("Watchdog: %s is still failing %d s after the "
                        "restart: look at its evidence file and "
                        "./logs.sh." % (label, elapsed))
            else:
                continue
            program_state.restarted_mono = None
            self.decisions.append(Decision(ALERT, text=text,
                                           key=(name, "help")))

    # The hourly summary.

    def _near_miss(self) -> None:
        """Feed this hour's near-miss statistics (starved cycles too)."""
        stats = self.state.near_miss
        invalidating = any(
            sections and sections[0]["what"] == INVALIDATION_SECTION
            for sections in (busy_sections(self.obs.health.get(program.name))
                             for program in self.running
                             if program.kind == "EvaluationService"))
        for program in self.running:
            window = self.state.programs[program.name].window
            probe = self._liveness_probe(program)
            if probe is not None:
                stats.misses[program.label] = max(
                    stats.misses.get(program.label, 0), sum(window))
                if probe.ok and probe.latency_s is not None:
                    stats.latencies.setdefault(program.label, []).append(
                        probe.latency_s)
            sections = busy_sections(self.obs.health.get(program.name))
            if program.kind == "EvaluationService" and sections:
                self._es_near_miss(sections[0])
            for section in sections:
                if program.kind == "ScoringService" \
                        and section["what"] == "score":
                    key, start = self._execution_key(stats.ss_starts,
                                                     section)
                    stats.ss_starts[key] = start
                    if invalidating:
                        stats.ss_overlapping.add(key)
                    if stats.ss_longest is None \
                            or section["age_s"] > stats.ss_longest[0]:
                        stats.ss_longest = (section["age_s"], key)
                if program.kind == "ProxyService" \
                        and section["what"] == "send":
                    stats.ps_idle_s = max(stats.ps_idle_s,
                                          section["idle_s"])

    def _es_near_miss(self, section: dict) -> None:
        """Feed the statistics of ES's oldest section.

        section: the section, as busy_sections gives it.

        """
        stats = self.state.near_miss
        what = section["what"]
        if not what.startswith(ES_SECTION_PREFIX):
            return
        if stats.es_longest is None or section["age_s"] > \
                stats.es_longest[0]:
            stats.es_longest = (section["age_s"], what)
        if what == INVALIDATION_SECTION:
            return
        stats.es_idle_s = max(stats.es_idle_s, section["idle_s"])
        if section["age_s"] > SECTION_WARN_S \
                and section["idle_s"] >= ES_IDLE_WARN_S:
            # One entry per section, with its largest values (as for the
            # SS executions).
            key, start = self._execution_key(
                {key: entry[1] for key, entry in stats.es_half.items()},
                section)
            _, start, age_s, idle_s = stats.es_half.get(
                key, (what, start, 0.0, 0.0))
            stats.es_half[key] = (what, start,
                                  max(age_s, section["age_s"]),
                                  max(idle_s, section["idle_s"]))

    def _execution_key(self, starts: Mapping[str, float],
                       section: dict) -> tuple[str, float]:
        """Return the key and the start of one execution of a section.

        A thread holds one section at a time, and the start computed
        from a section's age moves by a fraction of a second from cycle
        to cycle (health() measures the age a little before or after the
        cycle's time): a start within CYCLE_S of a known start of the
        same thread is the same execution, which keeps its key and its
        first start.

        starts: the first start of each known execution, by key.
        section: the section, as busy_sections gives it.

        return: its key, and its first start (epoch seconds).

        """
        start = self.wall - section["age_s"]
        prefix = "%s@" % section["tid"]
        for key, known in starts.items():
            if key.startswith(prefix) and abs(known - start) <= CYCLE_S:
                return key, known
        return "%s%d" % (prefix, round(start)), start

    def _summary(self) -> None:
        """Emit the hourly near-miss summary at INFO, and start over."""
        if self.mono - self.state.summary_mono < SUMMARY_PERIOD_S:
            return
        self.decisions.append(Decision(
            ALERT, level="INFO", key=("-", "summary"),
            text=summary_text(self.state.near_miss)))
        self.state.near_miss = NearMiss()
        self.state.summary_mono = self.mono

    # Holds and actions.

    def _act_on_trips(self) -> None:
        """Turn each trip into an action, or into an alert if held."""
        self._early_warnings()
        # The held trips of Workers: _held_workers groups them.
        held_workers: list[tuple[Trip, list[tuple[str, str]], bool]] = []
        incidents = self.state.ledger.incidents
        for trip in self.trips:
            program = trip.program
            opened = program.name not in incidents
            if opened:
                incidents[program.name] = {
                    "rule": trip.rule, "opened": self.wall, "passes": 0}
            shadow = self.state.shadow_for(program.klass)
            holds = self._holds(program, shadow)
            if holds and program.kind == "Worker":
                held_workers.append((trip, holds, opened))
            elif holds:
                self._held(trip, holds, opened)
            else:
                self._restart(trip, shadow)
        self._track_workers_episode(self._held_workers(held_workers))

    def _track_workers_episode(self, grouped: bool) -> None:
        """End the Workers' hold episode after PASSES_TO_RESOLVE quiet cycles.

        The episode is that of the group line (WORKERS_KEY). A quiet
        cycle is a non-starved one (this runs in those only) in which
        no hold heads the trips of two or more Workers, and in which R4
        judged at least one Worker. A Worker that trips alone, or over
        its budget, does not keep the episode open; a trip that comes
        and goes, or a change of headline, does not end it. The next
        episode is entering a hold again.

        grouped: whether Workers were held together in this cycle.

        """
        state = self.state
        if grouped or WORKERS_KEY not in state.held_headline:
            state.workers_quiet = 0
            return
        workers = {program.name for program in self.running
                   if program.kind == "Worker"}
        if workers and workers <= self.unjudged:
            # The cycle says nothing about the Workers: not a quiet one.
            return
        state.workers_quiet += 1
        if state.workers_quiet >= PASSES_TO_RESOLVE:
            state.workers_quiet = 0
            self._end_episode(WORKERS_KEY)

    def _end_episode(self, key: tuple[str, str]) -> None:
        """End the hold episode of a group line, and close the line.

        No "Resolved:" line closes a group line: the end of its episode
        does, and its next line is entering a hold again.

        key: WORKERS_KEY or FATAL_KEY.

        """
        for table in (self.state.held_headline, self.state.held_announced,
                      self.state.alert_sent, self.state.open_alerts):
            table.pop(key, None)

    def _early_warnings(self) -> None:
        """Emit the early warnings of the cycle (3.5, 3.9)."""
        for warning in self.warnings:
            program = warning.program
            self._alert(
                (program.name, warning.rule + " warning"), "WARNING",
                "Watchdog: early warning: %s. %s"
                % (warning.detail,
                   self._outlook(program, self._trip_point(warning))))

    @staticmethod
    def _trip_point(warning: Trip) -> str:
        """Say when the rule of an early warning trips (3.5).

        warning: the early warning.

        return: the start of the sentence that says what happens then.

        """
        if warning.rule != "R3":
            return "At %d misses, the latest included" % MISSES_TO_TRIP
        kind = warning.program.kind
        if kind == "EvaluationService":
            return ("Past %d s, with no statement for %d s, no CPU used by "
                    "its thread and no statement of it running in the "
                    "database apart from a lock wait"
                    % (SECTION_TRIP_S, ES_IDLE_TRIP_S))
        if kind == "ProxyService":
            return ("Past %d s with no request, while every ranking "
                    "answers" % SECTION_TRIP_S)
        return "Past %d s" % SECTION_TRIP_S

    def _outlook(self, program: ProgramFacts, trip_point: str) -> str:
        """Say what happens if program goes on failing, and the next step.

        It does not promise a restart that observe mode, an H9 class or
        a hold that applies now would not give.

        program: the program with an early warning.
        trip_point: when its rule trips, as _trip_point says.

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
        text = "%s, the watchdog %s." % (trip_point, what)
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
                ("H6", self._grace(program)[1]),
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

    def _grace(self, program: ProgramFacts,
               named: bool = False) -> tuple[str, str]:
        """Describe why program is still in its grace (H6).

        program: the program that tripped.
        named: whether the reason must name the program, for a line
            about several programs.

        return: the clause that applied ("watchdog", "started" or
            "action", the keys of GRACE_HINTS) and the reason, or
            ("", "") if H6 does not apply.

        """
        since_start = self.mono - self.state.started_mono
        if since_start < GRACE_S:
            return ("watchdog",
                    "the watchdog itself started %d s ago" % since_start)
        if program.start_wall is not None:
            running_for = age(self.wall, program.start_wall)
            if running_for < GRACE_S:
                return ("started", "%s started %d s ago"
                        % (program.label, running_for))
        program_state = self.state.programs.get(program.name)
        if program_state is not None \
                and program_state.last_action_mono is not None:
            since_action = self.mono - program_state.last_action_mono
            if since_action < GRACE_S:
                if named:
                    return ("action",
                            "the watchdog's last action or would-action "
                            "on %s was %d s ago"
                            % (program.label, since_action))
                return ("action",
                        "its last action or would-action was %d s ago"
                        % since_action)
        return "", ""

    def _grace_hint(self, program: ProgramFacts,
                    hints: Mapping[str, str]) -> str:
        """Return the next step of a line headed by H6 (3.9).

        program: a program of the line.
        hints: GRACE_HINTS, or WORKERS_GRACE_HINTS.

        return: the hint of the clause of _grace that applied to it,
            and, after an action where the watchdog restarts nothing
            (observe mode, a class of H9), that the next step is a
            human's.

        """
        clause = self._grace(program)[0]
        hint = hints[clause]
        if clause == "action" and (self.state.mode == OBSERVE
                                   or self.state.shadow_for(program.klass)):
            hint += " " + BY_HAND_HINT
        return hint

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
        headline, reason = holds[0]
        text = "Watchdog: %s fails %s (%s) but is held by %s: %s." % (
            program.label, trip.rule, trip.detail, headline, reason)
        text += _other_holds(holds)
        text += " " + (self._grace_hint(program, GRACE_HINTS)
                       if headline == "H6" else HOLD_HINTS[headline])
        self._held_alert((program.name, trip.rule), text,
                         [code for code, _ in holds], opened)

    def _held_alert(self, key: tuple[str, str], text: str,
                    codes: Sequence[str], opened: bool) -> None:
        """Emit the line of a hold episode, and keep it open.

        Entering a hold is a state change, and so is the first time an
        urgent hold heads it; any other change of headline within one
        is not, or a flapping hold would give a line per cycle.

        key: the episode's key: (program name, rule), or WORKERS_KEY.
        text: the line.
        codes: the holds that apply, the headline first.
        opened: whether the trip opened its incident in this cycle.

        """
        headline = codes[0]
        previous = self.state.held_headline.get(key)
        self.state.held_headline[key] = headline
        urgent = False
        if headline in URGENT_HOLDS:
            announced = self.state.held_announced.setdefault(key, set())
            urgent = headline not in announced
            announced.add(headline)
        self._alert(key, "WARNING", text,
                    change=opened or previous is None or urgent,
                    holds=codes, keep_open=True)

    def _held_workers(
            self, held: list[tuple[Trip, list[tuple[str, str]], bool]]
    ) -> bool:
        """Alert about the held trips of Workers, together if they can be.

        The first hold, in HOLDS order, that heads the trips of two or
        more Workers gets one line for them: it names the Workers, then
        the reasons of the headline (H6 and H7 give one per Worker),
        both capped at WORKERS_LISTED, and every other hold that applies
        to any of them. Every other held Worker gets its own line. The
        group line is one hold episode, WORKERS_KEY, whatever its
        headline: _held_alert rate-limits it, _track_workers_episode
        ends it.

        held: (trip, holds, opened) of each held Worker.

        return: whether Workers were held together in this cycle.

        """
        by_headline: dict[str, list] = {}
        for item in held:
            by_headline.setdefault(item[1][0][0], []).append(item)
        headline = next((code for code in HOLDS
                         if len(by_headline.get(code, ())) >= 2), None)
        for code, items in by_headline.items():
            if code != headline:
                for trip, holds, opened in items:
                    self._held(trip, holds, opened)
        if headline is None:
            return False
        group = sorted(by_headline[headline],
                       key=lambda item: item[0].program.shard)
        reasons: dict[str, list[str]] = {}
        for _trip, holds, _opened in group:
            for code, reason in holds:
                known = reasons.setdefault(code, [])
                if reason not in known:
                    known.append(reason)
        if headline == "H4":
            why = "a correlated failure is left to a human"
        elif headline == "H6":
            # Its reason about the last action does not name the Worker.
            why = _listed(list(dict.fromkeys(
                self._grace(item[0].program, named=True)[1]
                for item in group)), "; ")
        else:
            why = _listed(reasons[headline], "; ")
        others = [(code, reasons[code][0]) for code in HOLDS
                  if code in reasons and code != headline]
        labels = _listed([item[0].program.label for item in group], ", ")
        text = ("Watchdog: %d Workers trip R4 together (%s) and are held "
                "by %s: %s." % (len(group), labels, headline, why))
        text += _other_holds([(headline, why)] + others)
        # The Workers of the line share the clause of H6: the
        # watchdog's own start holds every one, and R4 trips no Worker
        # in its first GRACE_S seconds.
        text += " " + (self._grace_hint(group[0][0].program,
                                        WORKERS_GRACE_HINTS)
                       if headline == "H6"
                       else WORKERS_HOLD_HINTS[headline])
        # A Worker that joins the group is no state change: the line
        # would repeat as the Workers trip one after the other.
        self._held_alert(WORKERS_KEY, text,
                         [headline] + [code for code, _ in others], False)
        return True

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
        # A restart ends every hold episode of the program, whatever
        # rule tripped: an R3 restart of ES ends its R2 one too.
        self._forget_episodes(program.name)
        self.state.alert_sent[key] = self.mono
        self.decisions.append(Decision(
            RESTART, text=trip.detail, key=key, program=program.name,
            rule=trip.rule, would=would, shadow=shadow))

    # Incidents.

    def _update_incidents(self) -> None:
        """Count the passes of the open incidents; close them after 2.

        Two or more Workers that pass their second time in the same
        cycle get one "Resolved:" line together.

        """
        tripped = {trip.program.name for trip in self.trips}
        by_name = {program.name: program for program in self.obs.programs}
        incidents = self.state.ledger.incidents
        resolved: list[tuple[ProgramFacts, dict]] = []
        for name, incident in list(incidents.items()):
            program = by_name.get(name)
            if program is None or program.state != "RUNNING":
                continue
            if name in tripped or name in self.failing:
                incident["passes"] = 0
                continue
            if name in self.unjudged:
                # Unknown in this cycle: neither a pass nor a miss.
                continue
            incident["passes"] += 1
            if incident["passes"] >= PASSES_TO_RESOLVE:
                del incidents[name]
                resolved.append((program, incident))
        workers = [item for item in resolved if item[0].kind == "Worker"]
        together = len(workers) >= 2
        for program, incident in resolved:
            if together and program.kind == "Worker":
                continue
            self._resolve_program(
                program.name, "Resolved: %s passes its checks again "
                "(incident of %s, opened at %s)." % (
                    program.label, incident["rule"],
                    hhmm_utc(incident["opened"])))
        if together:
            self._resolve_workers(workers)

    def _resolve_workers(self,
                         workers: list[tuple[ProgramFacts, dict]]) -> None:
        """Close the incidents of Workers that pass together, in one line.

        workers: (program, incident) of each Worker, 2 or more.

        """
        workers = sorted(workers, key=lambda item: item[0].shard)
        for program, _incident in workers:
            self._close_alerts(program.name)
        labels = _listed([program.label for program, _ in workers], ", ")
        rules = ", ".join(sorted({incident["rule"]
                                  for _, incident in workers}))
        first = min(incident["opened"] for _, incident in workers)
        self.decisions.append(Decision(
            ALERT, key=(WORKERS_GROUP, "resolved"),
            text="Resolved: %d Workers pass their checks again: %s "
                 "(incidents of %s, the first opened at %s)."
                 % (len(workers), labels, rules, hhmm_utc(first))))


def summary_text(stats: NearMiss) -> str:
    """Return the line of the hourly near-miss summary (3.9).

    stats: this hour's statistics.

    return: the line.

    """
    parts = []
    for label in sorted(stats.misses):
        latency = p99(stats.latencies.get(label, []))
        parts.append("%s at most %d misses in 6, p99 %s" % (
            label, stats.misses[label],
            "n/a" if latency is None else "%.2f s" % latency))
    if stats.es_longest is not None:
        age_s, what = stats.es_longest
        parts.append("longest ES section %d s (%s%s)" % (
            age_s, what,
            ", a re-evaluation" if what == INVALIDATION_SECTION else ""))
    parts.append("longest ES idle_s in a section %d s" % stats.es_idle_s)
    if stats.es_half:
        parts.append("ES sections past half their trip: %s" % "; ".join(
            "%s held %d s from %s, up to %d s with no statement" % (
                what, age_s, hhmm_utc(start), idle_s)
            for what, start, age_s, idle_s in sorted(
                stats.es_half.values(), key=lambda entry: entry[1])))
    if stats.ss_longest is not None:
        age_s, key = stats.ss_longest
        parts.append("longest SS execution %d s%s" % (
            age_s, ", overlapping a re-evaluation"
            if key in stats.ss_overlapping else ""))
    parts.append("longest PS idle_s in a send %d s" % stats.ps_idle_s)
    parts.append("%d red-gate cycles, %d starved cycles"
                 % (stats.red_cycles, stats.starved_cycles))
    return "Watchdog: hourly near-miss summary: %s." % "; ".join(parts)


def program_status(state: PolicyState, observation: Observation,
                   wall: float) -> list[dict]:
    """Describe each program for the status file (3.10).

    misses and known describe the liveness window of a RUNNING program
    only (None otherwise: the window of its last process says nothing
    about it now). In the last hour, restarts_last_hour counts its real
    restarts, would_restarts_last_hour the would-restarts of observe
    mode, shadow_restarts_last_hour those of H9, and starts_last_hour
    what R5 used of its FATAL budget (starts and would-starts, in the
    ledger that H7 reads for it). incident is a copy.

    state: the state decide() returned for this cycle.
    observation: this cycle's observation.
    wall: now (epoch seconds).

    return: one dict per program of the process source.

    """
    def restarts(name: str, ledger: list[Booking], would: bool) -> int:
        return sum(1 for booking in ledger
                   if booking.program == name and booking.action == RESTART
                   and booking.would == would
                   and age(wall, booking.wall) < BUDGET_WINDOW_S)

    entries = []
    for program in observation.programs:
        program_state = state.programs.get(program.name)
        window = (program_state.window
                  if program_state is not None
                  and program.state == "RUNNING" else None)
        http = observation.http.get(program.name)
        rpc = observation.rpc.get(program.name)
        incident = state.ledger.incidents.get(program.name)
        entries.append({
            "name": program.name,
            "label": program.label if program.managed else None,
            "kind": program.kind,
            "state": program.state,
            "pid": program.pid,
            "misses": None if window is None else sum(window),
            "known": None if window is None else len(window),
            "http_ok": None if http is None else http.ok,
            "http_latency_s": None if http is None else http.latency_s,
            "rpc_ok": None if rpc is None else rpc.ok,
            "rpc_latency_s": None if rpc is None else rpc.latency_s,
            "sections": busy_sections(observation.health.get(program.name)),
            "incident": None if incident is None else dict(incident),
            "restarts_last_hour": restarts(
                program.name, state.ledger.real, False),
            "would_restarts_last_hour": restarts(
                program.name, state.ledger.real, True),
            "shadow_restarts_last_hour": state.ledger.count(
                program.name, RESTART, True, wall),
            "starts_last_hour": state.ledger.count(
                program.name, START,
                program.managed and state.shadow_for(program.klass), wall),
        })
    return entries
