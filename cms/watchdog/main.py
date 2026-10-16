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

"""cmsWatchdog: restart a CMS program that is alive but hung (#38).

    cmsWatchdog --mode observe|enforce --restart web,core,infra
        [--process-source supervisor|proc] [--status-history PATH]
    cmsWatchdog --status
    cmsWatchdog --snapshot PROGRAM

It runs as the supervisord program cmswatchdog (see
docker/generate_config.py). Every 15 seconds it probes the programs,
asks policy.decide() what to do, acts through an actuator, and writes
its status, its ledger and its journal. "When the watchdog alerts" in
docs/contest-day.md says what its alerts mean; the design is in
docs/superpowers/specs/2026-10-10-watchdog-design.md.

"""

import argparse
import asyncio
import contextlib
import faulthandler
import json
import os
import re
import secrets
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, \
    Sequence
from dataclasses import asdict
from typing import TextIO

from cms import ServiceCoord, config
from cms.watchdog import control, policy, probes


LEDGER_NAME = "watchdog-ledger.json"
STATUS_NAME = "watchdog.json"
EVIDENCE_DIR = "watchdog"
JOURNAL_NAME = "actions.jsonl"
LEVELS = {"INFO": 20, "WARNING": 30, "ERROR": 40}


def utc(wall: float) -> str:
    """Return an epoch time as "2027-01-15 08:00:00 UTC"."""
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(wall))


def write_atomic(path: str, text: str) -> None:
    """Write a file through a temporary file and a rename.

    The temporary file has a name of its own in the same directory, so
    that two writers of one path (the watchdog and --snapshot) never
    share it, and it is removed if anything fails. It is created as
    open(path, "w") creates a file, with the mode the umask allows
    (mkstemp would make it 0600).

    path: the file.
    text: its content.

    raise (OSError): if it cannot be written.

    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = "%s.%s.tmp" % (path, secrets.token_hex(8))
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                         0o666)
    try:
        with open(descriptor, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(partial, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(partial)
        raise


def _to_json(value: object, indent: int | None = None) -> str:
    """Return value as the text of a file of the watchdog.

    value: what to write; what JSON does not know is written as str().
    indent: as json.dumps's.

    return: the text.

    raise (OSError): if JSON cannot hold it (a key that is not a
        string or a number, a cycle): a failed write, which the
        callers handle as one.

    """
    try:
        return json.dumps(value, indent=indent, sort_keys=True,
                          default=str)
    except (TypeError, ValueError) as error:
        raise OSError("not JSON: %s" % error) from error


class Files:
    """Where the watchdog keeps its state, and how it writes it (3.10).

    The ledger and the status are in the run directory (a tmpfs in
    production: they survive a restart of the watchdog, not one of the
    container); the journal and the evidence are in log/watchdog/ (the
    cms-logs volume).

    """

    def __init__(self, run_dir: str, log_dir: str):
        """Name the files.

        run_dir: config.global_.run_dir.
        log_dir: config.global_.log_dir.

        """
        self.ledger = os.path.join(run_dir, LEDGER_NAME)
        self.status = os.path.join(run_dir, STATUS_NAME)
        self.evidence_dir = os.path.join(log_dir, EVIDENCE_DIR)
        self.journal = os.path.join(self.evidence_dir, JOURNAL_NAME)

    def load_ledger(self, wall: float) -> tuple[policy.Ledger, str]:
        """Read the ledger at startup.

        A missing ledger starts empty. One that exists but cannot be
        read (it does not parse, or is not UTF-8) counts as every budget
        exhausted for an hour, so that a crash that corrupts it cannot
        reset the budgets. Times ahead of now are brought back: at most
        an hour of exhausted budgets, and no booking, red interval or
        incident in the future.

        wall: now (epoch seconds).

        return: the ledger, and an alert line ("" if it was fine).

        """
        try:
            with open(self.ledger, encoding="utf-8") as f:
                ledger = policy.Ledger.from_json(f.read())
        except FileNotFoundError:
            return policy.Ledger(), ""
        except (OSError, ValueError) as error:
            # ValueError: a LedgerError, or a file that is not UTF-8.
            ledger = policy.Ledger.exhausted(wall)
            return ledger, (
                "Watchdog: its ledger %s did not parse (%s): every budget "
                "counts as used until %s." % (
                    self.ledger, " ".join(str(error).split()),
                    policy.hhmm_utc(ledger.exhausted_until)))
        # A time ahead of now (a hand edit, a clock that went back) must
        # not freeze the budgets past the hour of spec 3.10, nor make R5
        # see an outage after the later starts of a program. An open red
        # interval stays open: the gate was red when it was saved.
        limit = wall + policy.CORRUPT_LEDGER_S
        if ledger.exhausted_until is not None \
                and ledger.exhausted_until > limit:
            ledger.exhausted_until = limit
        for booking in ledger.real + ledger.shadow:
            booking.wall = min(booking.wall, wall)
        ledger.red = [[min(start, wall), None if end is None
                       else min(end, wall)] for start, end in ledger.red]
        for incident in ledger.incidents.values():
            incident["opened"] = min(incident["opened"], wall)
        return ledger, ""

    def save_ledger(self, ledger: policy.Ledger) -> None:
        """Write the ledger.

        raise (OSError): if it cannot be written, or if it holds a time
            that JSON cannot (NaN, an infinity): a failed write, which
            the callers handle as one.

        """
        try:
            text = ledger.to_json()
        except ValueError as error:
            raise OSError("the ledger holds a time that is not a number: "
                          "%s" % error) from error
        write_atomic(self.ledger, text)

    def save_status(self, status: Mapping) -> None:
        """Write the status file (raise OSError if it cannot be)."""
        write_atomic(self.status, _to_json(status))

    def append_journal(self, record: Mapping) -> None:
        """Append a line to the journal (raise OSError if it cannot)."""
        line = _to_json(record) + "\n"
        os.makedirs(self.evidence_dir, exist_ok=True)
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(line)

    def evidence_name(self, wall: float, program: str) -> str:
        """Return the name of an evidence file in evidence_dir.

        The program's name keeps its letters, digits, "-", "_" and ".";
        any other character becomes "_". So no name has a "/" to leave
        evidence_dir by, and none starts with a dot: it starts with the
        time.

        wall: when it happened (epoch seconds).
        program: the program's name.

        return: the name, "20270115T080000Z-cmsLogService_0.json".

        """
        return "%s-%s.json" % (
            time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(wall)),
            re.sub(r"[^A-Za-z0-9_.-]", "_", program))

    def write_evidence(self, name: str, evidence: Mapping) -> None:
        """Write an evidence file (raise OSError if it cannot be)."""
        write_atomic(os.path.join(self.evidence_dir, name),
                     _to_json(evidence, indent=1))


def log_record(level: str, text: str, wall: float) -> dict:
    """Return the keyword arguments of LogService.Log for an alert.

    LogService rebuilds a log record from them (logging.makeLogRecord),
    and the Overview's Logs table shows it as from "Watchdog,0".

    level: "WARNING" or "ERROR".
    text: the alert line.
    wall: when it happened (epoch seconds).

    return: the arguments.

    """
    return {"msg": text, "levelname": level, "levelno": LEVELS[level],
            "created": wall, "service_name": "Watchdog",
            "service_shard": 0, "operation": "", "exc_text": None}


class Alerter:
    """Write alerts to stdout and to LogService (3.9).

    stdout always (it reaches ./logs.sh). WARNING and ERROR lines also
    go to LogService, at the end of each cycle, best effort: on the
    first call that fails, or once the flush has taken its limit, the
    rest of that cycle's lines are left to stdout. So a stuck or slow
    LogService costs a cycle at most LOG_FLUSH_LIMIT_S (4 s).

    """

    def __init__(self, log_service: probes.RpcProbe | None,
                 out: TextIO | None = None,
                 flush_limit_s: float = policy.LOG_FLUSH_LIMIT_S):
        """Prepare the channels.

        log_service: an RpcProbe to LogService (one persistent
            connection of its own), or None.
        out: where to print (sys.stdout by default).
        flush_limit_s: the bound of one flush, in seconds.

        """
        self._log_service = log_service
        self._out = out if out is not None else sys.stdout
        self._flush_limit_s = flush_limit_s
        self._pending: list[dict] = []

    def emit(self, level: str, text: str, wall: float) -> None:
        """Print an alert, and queue it for LogService if WARNING+.

        A channel that fails does not take the other one with it.

        """
        if self._log_service is not None \
                and LEVELS[level] >= LEVELS["WARNING"]:
            self._pending.append(log_record(level, text, wall))
        try:
            print("%s %s %s" % (utc(wall), level, text), file=self._out,
                  flush=True)
        except (OSError, ValueError):
            # A closed or broken stdout: nowhere left to say it.
            pass

    def resend(self, alerts: Iterable[policy.Decision], wall: float) -> None:
        """Queue the open alerts again, for a restarted LogService.

        A restarted LogService starts with an empty Overview table.

        alerts: the open alerts.
        wall: now (epoch seconds).

        """
        if self._log_service is None:
            return
        for alert in alerts:
            if LEVELS[alert.level] >= LEVELS["WARNING"]:
                self._pending.append(log_record(alert.level, alert.text,
                                                wall))

    async def flush(self) -> bool:
        """Send the queued lines to LogService.

        It takes at most flush_limit_s (LOG_FLUSH_LIMIT_S, 4 s), however
        slowly LogService answers: the call in progress then is
        cancelled (its connection closed), and the lines not sent are
        left to stdout. A call that raises (an answer that RpcProbe does
        not expect) fails like one that does not answer.

        return: whether every queued line reached it (True when none
            was queued).

        """
        pending, self._pending = self._pending, []
        try:
            async with asyncio.timeout(self._flush_limit_s):
                for record in pending:
                    try:
                        result = await self._log_service.call("Log",
                                                              record)
                    except Exception:
                        return False
                    if not result.ok:
                        return False
        except TimeoutError:
            return False
        return True


def _budget_use(status: Mapping, now: float) -> str:
    """Return what --status says of the budgets (H7).

    program_status's counts count bookings only, and a ledger that did
    not parse exhausts every budget with none: its state comes first.
    The would-restarts are those of observe mode and those of H9; the
    FATAL starts count R5's would-starts too, as its budget does.

    status: the status file's content.
    now: the epoch time.

    return: "" when there is nothing to say, else the text to append.

    raise (TypeError, ValueError, AttributeError, KeyError,
        ArithmeticError): if the status file does not hold what the
        watchdog writes.

    """
    text = ""
    exhausted_until = status.get("exhausted_until")
    if exhausted_until is not None and float(exhausted_until) > now:
        text += (", every budget counts as used until %s (its ledger did "
                 "not parse)" % policy.hhmm_utc(float(exhausted_until)))
    counts = (
        ("restart", "restarts", ("restarts_last_hour",)),
        ("would-restart", "would-restarts",
         ("would_restarts_last_hour", "shadow_restarts_last_hour")),
        ("FATAL start or would-start", "FATAL starts or would-starts",
         ("starts_last_hour",)),
    )
    used = []
    for one, many, fields in counts:
        total, labels = 0, []
        for program in status.get("programs", []):
            count = sum(program.get(field, 0) for field in fields)
            if count:
                total += count
                labels.append(program.get("label") or program["name"])
        if total:
            used.append("%d %s (%s)" % (total, one if total == 1 else many,
                                        ", ".join(labels)))
    if used:
        text += ", in the last hour: " + ", ".join(used)
    return text


def status_line(path: str, environ: Mapping[str, str], now: float) -> str:
    """Return the line of cmsWatchdog --status (3.10).

    After the mode, the age of the status and the open alerts, it says
    whether supervisord answered, until when a ledger that did not
    parse exhausts every budget, and how much of the budgets each
    program used in the last hour.

    path: the status file.
    environ: the environment (CMS_WATCHDOG comes from .env).
    now: the epoch time.

    return: the line.

    """
    if environ.get("CMS_WATCHDOG", "").strip().lower() == "off":
        return "watchdog: off (not configured)"
    try:
        with open(path, encoding="utf-8") as f:
            status = json.load(f)
        since = max(0.0, now - float(status["time"]))
        # Formatted here: an infinite time (a hand edit) cannot be.
        line = "watchdog: %s, last cycle %d s ago, %d open alerts" % (
            status["mode"], since, len(status["open_alerts"]))
        budgets = _budget_use(status, now)
    except FileNotFoundError:
        return "no status file"
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            ArithmeticError) as error:
        return "watchdog: the status file %s does not parse (%s)" % (
            path, " ".join(str(error).split()))
    if status.get("supervisord") == "unreachable":
        line += ", supervisord unreachable"
    line += budgets
    if since > 3 * policy.CYCLE_S:
        line += " (stale: is cmswatchdog running? See ./status.sh)"
    return line


def http_address(kind: str, shard: int) -> tuple[str, int] | None:
    """Return where a CWS or AWS serves HTTP (3.2), or None.

    kind, shard: the program.

    return: (host, port); a wildcard address is probed on loopback.

    """
    if kind == "ContestWebServer":
        ports = config.contest_web_server.listen_port
        addresses = config.contest_web_server.listen_address
        if shard >= len(ports):
            return None
        address = addresses[shard] if shard < len(addresses) else ""
        return probes.probe_host(address), ports[shard]
    if kind == "AdminWebServer":
        return (probes.probe_host(config.admin_web_server.listen_address),
                config.admin_web_server.listen_port)
    return None


def rpc_address(kind: str, shard: int) -> tuple[str, int] | None:
    """Return a service's RPC address from cms.toml (3.2), or None."""
    address = config.services.get(ServiceCoord(kind, shard))
    if address is None:
        return None
    return probes.probe_host(address.ip), address.port


# Where a program listens: http_address or rpc_address (tests pass
# fakes).
AddressOf = Callable[[str, int], tuple[str, int] | None]


class Prober:
    """The probes of one watchdog, with their persistent connections."""

    def __init__(self, gate_env: Mapping[str, str], proc: str = "/proc"):
        """Prepare the probes.

        gate_env: the environment of the gate's psql.
        proc: where /proc is.

        """
        self._gate_env = dict(gate_env)
        self._proc = proc
        self._rpc: dict[tuple[str, int], probes.RpcProbe] = {}

    async def http(self, address: tuple[str, int]) -> probes.ProbeResult:
        """Probe a web server's favicon (R1)."""
        return await probes.http_probe(address[0], address[1],
                                       "/static/favicon.ico",
                                       policy.HTTP_TIMEOUT_S)

    async def rpc(self, address: tuple[str, int],
                  method: str) -> probes.ProbeResult:
        """Call method on the service's persistent connection."""
        probe = self._rpc.get(address)
        if probe is None:
            probe = self._rpc[address] = probes.RpcProbe(
                address[0], address[1], policy.RPC_TIMEOUT_S)
        return await probe.call(method)

    async def ranking(self,
                      target: probes.RankingTarget) -> probes.ProbeResult:
        """Probe a ranking: any status line passes, no credentials."""
        return await probes.http_probe(target.host, target.port, "/",
                                       policy.RANKING_TIMEOUT_S,
                                       any_status=True, tls=target.tls)

    async def gate(self) -> probes.GateResult:
        """Run the database gate."""
        return await probes.db_gate(self._gate_env, policy.GATE_KILL_S)

    def proc_facts(self, pid: int) -> dict | None:
        """Return the /proc facts of pid (see probes.proc_facts)."""
        return probes.proc_facts(pid, self._proc)

    def thread_cpu_s(self, pid: int, tid: int) -> float | None:
        """Return a thread's CPU time (see probes.thread_cpu_s)."""
        return probes.thread_cpu_s(pid, tid, self._proc)

    def close(self) -> None:
        """Close every persistent connection."""
        for probe in self._rpc.values():
            probe.close()


async def _probe_safely(
        job: Awaitable[probes.ProbeResult]) -> probes.ProbeResult:
    """Await a probe; an exception it raises is a miss with its text.

    The probes return a failure as a result, but an answer they do not
    expect can still raise (a deeply nested one: RecursionError), and
    it must not abort the other probes of the cycle.

    job: the probe.

    return: its result, or a failed one.

    """
    try:
        return await job
    except Exception as error:
        return probes.ProbeResult(False, error=probes.describe(error))


async def _gate_safely(
        job: Awaitable[probes.GateResult]) -> probes.GateResult:
    """Await the gate; an exception it raises makes it red ("other")."""
    try:
        return await job
    except Exception as error:
        return probes.GateResult(False, "other", probes.describe(error))


async def _gather(jobs: Mapping[str, Awaitable[probes.ProbeResult]]
                  ) -> dict[str, probes.ProbeResult]:
    """Await the probes concurrently; return their results by key."""
    results = await asyncio.gather(*map(_probe_safely, jobs.values()))
    return dict(zip(jobs, results))


def _probe(result: probes.ProbeResult) -> policy.Probe:
    return policy.Probe(result.ok, result.latency_s, result.error)


def _gate(result: probes.GateResult) -> policy.Gate:
    return policy.Gate(result.green, result.reason, result.detail,
                       tuple(policy.Backend(**row)
                             for row in result.backends))


# The outcome of an action that a stop of the watchdog (SIGTERM) cut.
INTERRUPTED = control.Outcome("interrupted", "the watchdog stopped during it")


def _may_have_signalled(outcome: control.Outcome) -> bool:
    """Tell whether a restart may have sent TERM or KILL (3.7).

    A "failed" restart sent TERM when its detail begins with "TERM
    sent" or "KILL failed"; one whose actuator raised ("error"), or
    that the watchdog's stop cut ("interrupted"), may have.

    """
    if outcome.result == "failed":
        return outcome.detail.startswith(("TERM sent", "KILL failed"))
    return outcome.result in ("restarted", "unconfirmed", "error",
                              "interrupted")


def restart_line(decision: policy.Decision, program: policy.ProgramFacts,
                 outcome: control.Outcome, evidence: str,
                 shadow_count: int) -> tuple[str, str]:
    """Return the level and the line that report a restart (3.9).

    Each line says what was signalled. Only a "refused" restart sent
    nothing at all; a "gone" or a "failed" one may have written the
    evidence and sent its USR1 first, and a "failed" one may have sent
    TERM.

    decision: the restart decision.
    program: the program, as the cycle saw it.
    outcome: what the actuator did (control.Outcome); "error" when the
        actuator raised, "interrupted" when the watchdog stopped during
        it.
    evidence: the evidence file's name.
    shadow_count: the program's restarts in the shadow ledger (H9).

    return: (level, line).

    """
    where = "evidence log/watchdog/%s" % evidence
    what = "%s (%s)" % (decision.text, decision.rule)
    # What the evidence step did, when the outcome came after it.
    evidence_step = ("; %s; %s" % (where, outcome.usr1) if outcome.usr1
                     else "")
    by_hand = ("Check ./status.sh, and restart it by hand if needed, "
               "after cmsWatchdog --snapshot.")
    result = outcome.result
    if result == "restarted":
        # The USR1 says where the stacks are, or why there are none.
        return "WARNING", (
            "Watchdog: restarted %s (pid %d→%d%s) after %s; DB OK; %s%s."
            % (program.label, program.pid, outcome.new_pid,
               ", KILL after %d s" % policy.TERM_WAIT_S
               if outcome.killed else "", what, where,
               "; " + outcome.usr1 if outcome.usr1 else ""))
    if result == "would":
        if decision.shadow:
            tail = ("class %s is not in CMS_WATCHDOG_RESTART (H9), so "
                    "nothing was done; shadow count %d of %d this hour."
                    % (program.klass, shadow_count, policy.PROGRAM_BUDGET))
        else:
            tail = "observe mode: nothing was restarted."
        return "WARNING", (
            "Watchdog: WOULD restart %s (pid %d) after %s; DB OK; %s; %s; "
            "%s" % (program.label, program.pid, what, where, outcome.usr1,
                    tail))
    if result == "unconfirmed":
        return "ERROR", (
            "Watchdog: sent TERM%s to %s (pid %d) after %s, but %s%s. "
            "Check ./status.sh: a program left FATAL is started again "
            "only after a database outage." % (
                " and KILL" if outcome.killed else "", program.label,
                program.pid, what, outcome.detail, evidence_step))
    if result == "unbooked":
        return "ERROR", (
            "Watchdog: did not restart %s: %s, and an unbooked restart "
            "would bypass the budget. Check the free space of the run "
            "directory." % (program.label, outcome.detail))
    if result == "refused":
        return "WARNING", ("Watchdog: did not restart %s: %s; nothing was "
                           "signalled." % (program.label, outcome.detail))
    if result == "gone":
        return "WARNING", (
            "Watchdog: did not restart %s after %s: %s, so no TERM or KILL "
            "was sent%s. Check ./status.sh." % (
                program.label, what, outcome.detail, evidence_step))
    if result == "failed":
        if outcome.detail.startswith("TERM sent"):
            sent = "TERM was sent, KILL was not"
        elif outcome.detail.startswith("KILL failed"):
            sent = "TERM was sent before it"
        else:
            sent = "no TERM or KILL was sent"
        return "ERROR", (
            "Watchdog: could not restart %s (pid %d) after %s: %s; %s%s. "
            "%s" % (program.label, program.pid, what, outcome.detail, sent,
                    evidence_step, by_hand))
    # "error" (the actuator raised) or "interrupted" (the watchdog
    # stopped during it). A dry run sends no TERM or KILL.
    return "ERROR", (
        "Watchdog: the %s of %s (pid %d) after %s %s: %s; %s. %s" % (
            "would-restart" if decision.would else "restart",
            program.label, program.pid, what, _how_it_ended(outcome),
            outcome.detail, "no TERM or KILL was sent" if decision.would
            else "TERM and KILL may have been sent", by_hand))


def _how_it_ended(outcome: control.Outcome) -> str:
    """Say how an action that did not finish ended, for its line."""
    if outcome.result == "interrupted":
        return "was interrupted"
    return "failed unexpectedly"


def start_line(decision: policy.Decision,
               outcome: control.Outcome) -> tuple[str, str]:
    """Return the level and the line that report an R5 batch.

    decision: the START decision.
    outcome: what the actuator did; "error" when it raised,
        "interrupted" when the watchdog stopped during it.

    return: (level, line).

    """
    names = ", ".join(decision.programs)
    if outcome.result == "started":
        return "WARNING", ("Watchdog: started %s, left FATAL by a database "
                           "outage (R5). Check ./status.sh." % names)
    if outcome.result == "would":
        tail = ("their class is not in CMS_WATCHDOG_RESTART (H9), so "
                "nothing was done." if decision.shadow
                else "observe mode: nothing was started.")
        return "WARNING", ("Watchdog: WOULD start %s, left FATAL by a "
                           "database outage (R5); %s" % (names, tail))
    if outcome.result == "unbooked":
        return "ERROR", "Watchdog: did not start %s: %s." % (
            names, outcome.detail)
    if outcome.result == "start failed":
        return "ERROR", ("Watchdog: could not start all of %s: %s. Read "
                         "./logs.sh, then start them with supervisorctl."
                         % (names, outcome.detail))
    # "error" (the actuator raised) or "interrupted" (the watchdog
    # stopped during it).
    if decision.would:
        return "ERROR", ("Watchdog: the would-start of %s %s: %s; nothing "
                         "was started." % (names, _how_it_ended(outcome),
                                           outcome.detail))
    return "ERROR", ("Watchdog: the start of %s %s: %s; some of them may "
                     "have been started. Read ./logs.sh and ./status.sh, "
                     "then start the FATAL ones with supervisorctl."
                     % (names, _how_it_ended(outcome), outcome.detail))


class Watchdog:
    """One cmsWatchdog: its cycle, its actions and its files (3.3)."""

    def __init__(self, *, state: policy.PolicyState,
                 source: control.ProcessSource,
                 actuator: control.DryRunActuator,
                 dry_run: control.DryRunActuator, prober: Prober,
                 files: Files, alerter: Alerter,
                 rankings: Sequence[probes.RankingTarget] = (),
                 rearm: Callable[[], None] = lambda: None,
                 status_history: str | None = None,
                 http_address: AddressOf = http_address,
                 rpc_address: AddressOf = rpc_address):
        """Wire the watchdog.

        state: the policy state (with the ledger read at startup).
        source: the process source.
        actuator: the actuator of real actions (a SignalActuator, or a
            DryRunActuator when the source cannot act).
        dry_run: the actuator of would-actions.
        prober: a Prober (or a fake with the same methods).
        files: where the state goes.
        alerter: where the alerts go.
        rankings: the rankings to probe.
        rearm: re-arms the self-guard; called at the end of each cycle.
        status_history: a file to append each cycle's status to, or
            None.
        http_address, rpc_address: where each program listens.

        """
        self.state = state
        self.source = source
        self.actuator = actuator
        self.dry_run = dry_run
        self.prober = prober
        self.files = files
        self.alerter = alerter
        self.rankings = list(rankings)
        self.rearm = rearm
        self.status_history = status_history
        self._http_address = http_address
        self._rpc_address = rpc_address
        self.cycles = 0
        self._programs: tuple[policy.ProgramFacts, ...] = ()
        self._action: asyncio.Task | None = None
        # LogService's pid, once a flush reached it (or at first sight).
        self._log_service_pid: int | None = None
        self._cpu: dict[int, float] = {}
        self._proc: dict[str, dict | None] = {}
        # By program name: the last health() answer received and its
        # time (epoch seconds), for the evidence (3.8). A trip of R1 or
        # R2 has no answer in its own cycle.
        self._last_health: dict[str, tuple[float, Mapping]] = {}
        self._file_alerts: dict[str, float] = {}

    async def run_forever(self, clock: Callable[[], float] = time.monotonic,
                          wall_clock: Callable[[], float] = time.time,
                          sleep: Callable[[float], Awaitable[None]] =
                          asyncio.sleep,
                          cycles: int | None = None) -> None:
        """Run a cycle every CYCLE_S seconds on a monotonic schedule.

        Each cycle gets its lag (how late it started) and the time since
        the previous one: a starved or paused watchdog tells the policy.
        The schedule keeps its grid: a cycle that ends after its next
        slot skips the slots it missed, so that a cycle of up to 30 s is
        followed 30 s after its start at most, which is no pause.

        clock, wall_clock, sleep: time.monotonic, time.time and
            asyncio.sleep (tests pass fakes).
        cycles: stop after that many cycles (tests); None runs forever.

        raise (Exception): what a cycle raised unexpectedly, once an
            ERROR line about it has been flushed to LogService:
            supervisord then starts the watchdog again.

        """
        scheduled = clock()
        previous = None
        count = 0
        while cycles is None or count < cycles:
            start = clock()
            try:
                await self.cycle(
                    start, wall_clock(), max(0.0, start - scheduled),
                    None if previous is None else start - previous)
            except Exception as error:
                self.alerter.emit(
                    "ERROR", "Watchdog: its cycle failed unexpectedly (%s: "
                    "%s); it exits, and supervisord starts it again. Its "
                    "traceback is in ./logs.sh."
                    % (type(error).__name__, probes.describe(error)),
                    wall_clock())
                await self.alerter.flush()
                raise
            count += 1
            previous = start
            scheduled += policy.CYCLE_S
            while scheduled <= clock():
                scheduled += policy.CYCLE_S
            await sleep(max(0.0, scheduled - clock()))

    async def settle(self) -> None:
        """Wait for the action in flight, if there is one."""
        if self._action is not None:
            await self._action

    async def stop(self) -> None:
        """Interrupt the action in flight, if there is one.

        It returns once the action has journaled and alerted that it
        was interrupted (as "interrupted").

        """
        if self._action is not None and not self._action.done():
            self._action.cancel()
            await asyncio.wait({self._action})

    async def cycle(self, mono: float, wall: float, lag_s: float,
                    since_previous_s: float | None) -> None:
        """Run one cycle (3.3): probe, decide, act, record.

        The action of a cycle runs as a task of its own, beside the
        next cycles: a restart may take 54 s (SignalActuator.restart),
        which with a cycle's own probes would not fit in one 60 s arm
        of the self-guard. Each cycle re-arms the self-guard at its
        end, whether or not an action is in flight.

        mono, wall: the monotonic and the epoch time of its start.
        lag_s: how late it started.
        since_previous_s: since the previous cycle, None for the first.

        """
        started = time.monotonic()
        # Taken before the source is read: an action that ends while
        # this cycle probes still holds it (H8), because the source's
        # answer may predate the action's end (a program of a start
        # batch still FATAL, a restarted program's old pid).
        in_flight = self._action is not None and not self._action.done()
        programs, supervisor_ok = await self._read_programs()
        running = [program for program in programs
                   if program.managed and program.state == "RUNNING"]
        http_jobs: dict[str, Awaitable[probes.ProbeResult]] = {}
        rpc_jobs: dict[str, Awaitable[probes.ProbeResult]] = {}
        for program in running:
            address = self._http_address(program.kind, program.shard)
            if program.kind in policy.HTTP_KINDS and address is not None:
                http_jobs[program.name] = self.prober.http(address)
            address = self._rpc_address(program.kind, program.shard)
            if program.kind in policy.HEALTH_KINDS and address is not None:
                rpc_jobs[program.name] = self.prober.rpc(address, "health")
        ranking_jobs = {target.visible: self.prober.ranking(target)
                        for target in self.rankings}
        gate, http, rpc, rankings = await asyncio.gather(
            _gate_safely(self.prober.gate()), _gather(http_jobs),
            _gather(rpc_jobs), _gather(ranking_jobs))
        workers, holder_cpu = await self._es_extras(running, rpc)
        observation = policy.Observation(
            lag_s=lag_s, since_previous_s=since_previous_s,
            supervisor_ok=supervisor_ok, programs=programs,
            gate=_gate(gate),
            http={name: _probe(result) for name, result in http.items()},
            rpc={name: _probe(result) for name, result in rpc.items()},
            health={name: result.data for name, result in rpc.items()
                    if result.ok and isinstance(result.data, Mapping)},
            workers=workers,
            rankings={address: _probe(result)
                      for address, result in rankings.items()},
            holder_cpu=holder_cpu, in_flight=in_flight)
        self.state, decisions = policy.decide(self.state, observation, mono,
                                              wall)
        names = {program.name for program in running}
        self._last_health = {name: entry for name, entry
                             in self._last_health.items() if name in names}
        for name, payload in observation.health.items():
            self._last_health[name] = (wall, payload)
        self._proc = {program.name: self._proc_facts(program)
                      for program in running}
        live = {program.pid for program in running}
        self._cpu = {pid: cpu for pid, cpu in self._cpu.items()
                     if pid in live}
        actions = []
        for decision in decisions:
            if decision.action == policy.ALERT:
                self.alerter.emit(decision.level, decision.text, wall)
            else:
                actions.append(decision)
        if actions:
            self._action = asyncio.create_task(
                self._act(actions, observation, wall))
        log_service_pid = self._check_log_service(programs, wall)
        self._record(wall, lag_s, observation, started)
        delivered = await self.alerter.flush()
        if log_service_pid is not None \
                and (delivered or self._log_service_pid is None):
            self._log_service_pid = log_service_pid
        self.cycles += 1
        self.rearm()

    async def _read_programs(
            self) -> tuple[tuple[policy.ProgramFacts, ...], bool]:
        """Return the programs, and whether the source answered (H1).

        When supervisord does not answer, the last programs it gave are
        probed and judged, and H1 holds every trip.

        """
        try:
            infos = await self.source.programs()
        except Exception:
            return self._programs, False
        self._programs = tuple(policy.ProgramFacts(**asdict(info))
                               for info in infos)
        return self._programs, True

    async def _es_extras(
            self, running: list[policy.ProgramFacts],
            rpc: Mapping[str, probes.ProbeResult],
    ) -> tuple[Mapping | None, tuple[int, float] | None]:
        """Ask ES for its Workers and read its holder's CPU (R3, R4).

        return: (workers_status() or None, (tid, cpu_s) or None).

        """
        es = next((program for program in running
                   if program.kind == "EvaluationService"), None)
        if es is None or es.name not in rpc or not rpc[es.name].ok:
            return None, None
        answer = await _probe_safely(self.prober.rpc(
            self._rpc_address(es.kind, es.shard), "workers_status"))
        workers = (answer.data if answer.ok
                   and isinstance(answer.data, Mapping) else None)
        sections = policy.busy_sections(rpc[es.name].data)
        if not sections or sections[0]["tid"] is None:
            return workers, None
        tid = sections[0]["tid"]
        cpu_s = self.prober.thread_cpu_s(es.pid, tid)
        return workers, None if cpu_s is None else (tid, cpu_s)

    def _proc_facts(self, program: policy.ProgramFacts) -> dict | None:
        """Return the /proc facts of program, with its CPU delta.

        cpu_delta_s is its CPU time since the previous cycle, None on
        its first.

        """
        facts = self.prober.proc_facts(program.pid)
        if facts is None:
            return None
        previous = self._cpu.get(program.pid)
        facts = dict(facts, cpu_delta_s=None if previous is None
                     else facts["cpu_s"] - previous)
        self._cpu[program.pid] = facts["cpu_s"]
        return facts

    async def _act(self, decisions: list[policy.Decision],
                   observation: policy.Observation, wall: float) -> None:
        """Carry out the actions of one cycle, in order (3.7).

        A cycle may decide two START batches (a real one and an H9
        shadow one): this task, and so H8, lasts until the last start
        of the last batch returns.

        """
        for decision in decisions:
            try:
                if decision.action == policy.RESTART:
                    await self._restart(decision, observation, wall)
                else:
                    await self._start(decision, wall)
            except Exception as error:
                self.alerter.emit(
                    "ERROR", "Watchdog: the %s of %s failed unexpectedly: "
                    "%s" % (decision.action,
                            decision.program or ", ".join(decision.programs),
                            probes.describe(error)), time.time())

    async def _restart(self, decision: policy.Decision,
                       observation: policy.Observation, wall: float) -> None:
        """Restart, or would-restart, one program (3.7).

        An actuator that raises fails the action ("error"): the booking
        stays, and the journal and an ERROR line say that TERM may have
        been sent. So does a stop of the watchdog during the action
        ("interrupted"), and then the cancellation goes on. A restart
        that sent no TERM or KILL (refused, gone, unbooked, failed
        before TERM) clears the policy's mark of it, so that "did it
        help" never reports a restart that did not happen.

        """
        program = next(item for item in observation.programs
                       if item.name == decision.program)
        name = self.files.evidence_name(wall, program.name)
        actuator = self.dry_run if decision.would else self.actuator

        def book() -> None:
            # An OSError here stops the action: an unbooked restart
            # would bypass the budget.
            self.files.save_ledger(self.state.ledger)
            self._journal({
                "time": utc(wall), "mode": self.state.mode,
                "program": program.name, "label": program.label,
                "rule": decision.rule, "action": decision.action,
                "would": decision.would, "shadow": decision.shadow,
                "pid": program.pid, "evidence": "log/watchdog/" + name,
                "evidence_planned": True}, wall)

        def write_evidence() -> None:
            self.files.write_evidence(
                name, self._evidence(decision, program, observation, wall))

        try:
            outcome = await actuator.restart(
                control.Target(program.name, program.kind, program.pid),
                book, write_evidence)
        except asyncio.CancelledError:
            self._report_restart(decision, program, name, INTERRUPTED,
                                 wall)
            raise
        except Exception as error:
            outcome = control.Outcome("error", probes.describe(error))
        self._report_restart(decision, program, name, outcome, wall)

    def _report_restart(self, decision: policy.Decision,
                        program: policy.ProgramFacts, name: str,
                        outcome: control.Outcome, wall: float) -> None:
        """Journal and alert the outcome of a restart (3.7, 3.9).

        decision: the restart decision.
        program: the program, as the cycle saw it.
        name: the evidence file's name.
        outcome: what the actuator did.
        wall: when the cycle that decided it started (epoch seconds).

        """
        now = time.time()
        self._journal({
            "time": utc(now), "program": program.name,
            "outcome": outcome.result, "detail": outcome.detail,
            "new_pid": outcome.new_pid, "killed": outcome.killed,
            "usr1": outcome.usr1, "evidence_error": outcome.evidence_error},
            now)
        if not decision.would and not _may_have_signalled(outcome):
            self._unmark_restart(program)
        level, text = restart_line(
            decision, program, outcome, name, self.state.ledger.count(
                program.name, policy.RESTART, True, wall))
        self.alerter.emit(level, text, now)
        if outcome.evidence_error:
            self.alerter.emit(
                "ERROR", "Watchdog: the evidence of %s could not be "
                "written (%s); the action went on. Check the free space of "
                "the log volume." % (program.label, outcome.evidence_error),
                now)

    def _unmark_restart(self, program: policy.ProgramFacts) -> None:
        """Forget the policy's mark of a restart that did not happen.

        program: the program, with the pid the restart was about.

        """
        program_state = self.state.programs.get(program.name)
        if program_state is not None \
                and program_state.restarted_pid == program.pid:
            program_state.restarted_mono = None
            program_state.restarted_pid = None

    async def _start(self, decision: policy.Decision, wall: float) -> None:
        """Start, or would-start, an R5 batch (3.7).

        An actuator that raises fails the batch ("error"), and a stop
        of the watchdog during it interrupts it ("interrupted"), as for
        a restart.

        """
        actuator = self.dry_run if decision.would else self.actuator

        def book() -> None:
            self.files.save_ledger(self.state.ledger)
            self._journal({
                "time": utc(wall), "mode": self.state.mode,
                "programs": list(decision.programs), "rule": decision.rule,
                "action": decision.action, "would": decision.would,
                "shadow": decision.shadow}, wall)

        try:
            outcome = await actuator.start(decision.programs, book)
        except asyncio.CancelledError:
            self._report_start(decision, INTERRUPTED)
            raise
        except Exception as error:
            outcome = control.Outcome("error", probes.describe(error))
        self._report_start(decision, outcome)

    def _report_start(self, decision: policy.Decision,
                      outcome: control.Outcome) -> None:
        """Journal and alert the outcome of an R5 batch (3.7, 3.9)."""
        now = time.time()
        self._journal({"time": utc(now), "programs": list(decision.programs),
                       "outcome": outcome.result, "detail": outcome.detail},
                      now)
        level, text = start_line(decision, outcome)
        self.alerter.emit(level, text, now)

    def _evidence(self, decision: policy.Decision,
                  program: policy.ProgramFacts,
                  observation: policy.Observation, wall: float) -> dict:
        """Return the evidence of an action (3.8).

        Its "health" is the last health() answer received from the
        program, at "health_time": an R1 or R2 trip has none in its own
        cycle.

        """
        program_state = self.state.programs.get(program.name)
        last_health = self._last_health.get(program.name)
        return {
            "time": utc(wall),
            "mode": self.state.mode,
            "decision": {
                "action": decision.action, "rule": decision.rule,
                "detail": decision.text, "would": decision.would,
                "shadow": decision.shadow, "holds_checked": list(policy.HOLDS),
                "holds_applied": ["H9"] if decision.shadow else []},
            "observations": list(program_state.history)
            if program_state is not None else [],
            "supervisor": asdict(program),
            "health": None if last_health is None else last_health[1],
            "health_time": None if last_health is None
            else utc(last_health[0]),
            "gate": {"green": observation.gate.green,
                     "reason": observation.gate.reason,
                     "detail": observation.gate.detail},
            "snapshot": [asdict(backend)
                         for backend in observation.gate.backends],
            "proc": self._proc.get(program.name),
        }

    def _journal(self, record: Mapping, wall: float) -> None:
        """Append to the journal; a failure alerts and goes on."""
        try:
            self.files.append_journal(record)
        except OSError as error:
            self._file_alert(
                "journal", "Watchdog: its journal %s could not be written "
                "(%s); actions go on. Check the free space of the log "
                "volume." % (self.files.journal, error), wall)

    def _file_alert(self, kind: str, text: str, wall: float) -> None:
        """Alert about a file, at most once per ALERT_REPEAT_S."""
        now = time.monotonic()
        last = self._file_alerts.get(kind)
        if last is None or now - last >= policy.ALERT_REPEAT_S:
            self._file_alerts[kind] = now
            self.alerter.emit("ERROR", text, wall)

    def _check_log_service(self, programs: Iterable[policy.ProgramFacts],
                           wall: float) -> int | None:
        """Queue the open alerts again when LogService has a new pid.

        A restarted LogService starts with an empty Overview. Its new
        pid counts as seen only once a flush reaches it (see cycle), so
        a LogService that does not listen yet just after RUNNING gets
        the open alerts again at the next cycle.

        programs: this cycle's programs.
        wall: now (epoch seconds).

        return: LogService's pid, or None when it is not RUNNING.

        """
        pid = next((program.pid for program in programs
                    if program.kind == "LogService"
                    and program.state == "RUNNING"), None)
        if pid is not None and self._log_service_pid is not None \
                and pid != self._log_service_pid:
            self.alerter.resend(self.state.open_alerts.values(), wall)
        return pid

    def _record(self, wall: float, lag_s: float,
                observation: policy.Observation, started: float) -> None:
        """Write the status (and its history) and the ledger (3.10)."""
        status = {
            "time": wall, "time_utc": utc(wall), "cycle": self.cycles,
            "mode": self.state.mode,
            "restart_classes": sorted(self.state.restart_classes),
            "cycle_duration_s": time.monotonic() - started,
            "lag_s": lag_s, "starved": lag_s > policy.STARVED_LAG_S,
            "supervisord": "ok" if observation.supervisor_ok
            else "unreachable",
            "gate": "green" if observation.gate.green else "red",
            "gate_reason": observation.gate.reason,
            "gate_detail": observation.gate.detail,
            "programs": policy.program_status(self.state, observation,
                                              wall),
            "rankings": {address: asdict(probe) for address, probe
                         in observation.rankings.items()},
            "open_alerts": [alert.text for alert
                            in self.state.open_alerts.values()],
            "incidents": self.state.ledger.incidents,
            # A ledger that did not parse exhausts every budget until
            # then (--status says so).
            "exhausted_until": self.state.ledger.exhausted_until,
            "action_in_flight": self._action is not None
            and not self._action.done(),
        }
        try:
            self.files.save_status(status)
        except OSError as error:
            self._file_alert(
                "status", "Watchdog: its status file %s could not be "
                "written (%s); it goes on, and ./status.sh shows a stale "
                "heartbeat meanwhile." % (self.files.status, error), wall)
        if self.status_history is not None:
            try:
                line = _to_json(status) + "\n"
                with open(self.status_history, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError as error:
                self._file_alert(
                    "history", "Watchdog: its status history %s could not "
                    "be written (%s)." % (self.status_history, error), wall)
        try:
            self.files.save_ledger(self.state.ledger)
        except OSError as error:
            self._file_alert(
                "ledger", "Watchdog: its ledger %s could not be written "
                "(%s); a restart stops at its booking until it can be."
                % (self.files.ledger, error), wall)


def _gate_environment() -> dict[str, str]:
    """Return the environment of the gate's psql, from cms.toml."""
    return probes.psql_environment(
        config.database.url, os.environ.get("PATH", os.defpath),
        policy.GATE_CONNECT_TIMEOUT_S, policy.GATE_STATEMENT_TIMEOUT_MS)


async def serve(mode: str, restart_classes: frozenset[str],
                process_source: str, files: Files,
                status_history: str | None) -> int:
    """Run the watchdog until SIGTERM or SIGINT.

    mode: policy.OBSERVE or policy.ENFORCE.
    restart_classes: the classes enforce mode may act on.
    process_source: "supervisor" or "proc".
    files: where the state goes.
    status_history: see Watchdog.

    return: 0; an unexpected error of the cycle propagates, so that
        supervisord restarts the watchdog.

    """
    wall = time.time()
    log_address = rpc_address("LogService", 0)
    alerter = Alerter(None if log_address is None else probes.RpcProbe(
        log_address[0], log_address[1], policy.LOG_ALERT_TIMEOUT_S))
    ledger, problem = files.load_ledger(wall)
    if problem:
        alerter.emit("ERROR", problem, wall)
    source = (control.SupervisorSource() if process_source == "supervisor"
              else control.ProcSource())
    dry_run = control.DryRunActuator(source, usr1_wait_s=policy.USR1_WAIT_S)
    actuator = control.SignalActuator(
        source, term_wait_s=policy.TERM_WAIT_S, confirm_s=policy.CONFIRM_S,
        usr1_wait_s=policy.USR1_WAIT_S) if source.can_act else dry_run
    prober = Prober(_gate_environment())
    rankings = [target for target in map(probes.ranking_target,
                                         config.proxy_service.rankings)
                if target is not None]

    def rearm() -> None:
        faulthandler.dump_traceback_later(policy.SELF_GUARD_S, exit=True)

    watchdog = Watchdog(
        state=policy.PolicyState(mode=mode, restart_classes=restart_classes,
                                 ledger=ledger),
        source=source, actuator=actuator, dry_run=dry_run, prober=prober,
        files=files, alerter=alerter, rankings=rankings, rearm=rearm,
        status_history=status_history)
    alerter.emit("INFO", "Watchdog: started in %s mode%s, with the %s "
                 "source; it acts on nothing in its first %d s." % (
                     mode, " (it may restart %s)" % ",".join(
                         sorted(restart_classes))
                     if mode == policy.ENFORCE else "", process_source,
                     policy.GRACE_S), wall)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    # The first cycle is guarded too; each cycle re-arms at its end.
    rearm()
    runner = asyncio.create_task(watchdog.run_forever())
    stopper = asyncio.create_task(stop.wait())
    await asyncio.wait({runner, stopper},
                       return_when=asyncio.FIRST_COMPLETED)
    faulthandler.cancel_dump_traceback_later()
    stopper.cancel()
    try:
        if runner.done():
            runner.result()
    finally:
        runner.cancel()
        # An action in flight journals that it was interrupted.
        await watchdog.stop()
        prober.close()
    return 0


async def take_snapshot(program: str, files: Files,
                        source: control.ProcessSource | None = None,
                        prober: Prober | None = None,
                        out: TextIO | None = None,
                        rpc_address: AddressOf = rpc_address) -> int:
    """Write the evidence of a RUNNING program, and USR1 it (3.8).

    It books nothing and restarts nothing: run it before a manual
    restart. The pidfd check of an action applies. A probe that raises
    is a failed one; an actuator that raises writes no evidence.

    program: its supervisord name (as in ./status.sh and the alerts),
        or its label.
    files: where the evidence goes.
    source, prober, out, rpc_address: tests pass fakes.

    return: 0, or 1 if no evidence was written.

    """
    out = out if out is not None else sys.stdout
    source = source if source is not None else control.SupervisorSource()
    try:
        facts = [policy.ProgramFacts(**asdict(info))
                 for info in await source.programs()]
    except Exception as error:
        print("cmsWatchdog: supervisord did not answer: %s"
              % probes.describe(error), file=out)
        return 1
    found = next((item for item in facts if item.name == program
                  or (item.managed and item.label == program)), None)
    if found is None or not found.managed or found.state != "RUNNING":
        why = ("it is unknown" if found is None
               else "it is not a CMS service" if not found.managed
               else "it is %s" % found.state)
        print("cmsWatchdog: no RUNNING CMS program named %r (%s). The CMS "
              "programs: %s." % (program, why, ", ".join(sorted(
                  item.name for item in facts if item.managed))), file=out)
        return 1
    own_prober = prober is None
    prober = prober if prober is not None else Prober(_gate_environment())
    try:
        gate = await _gate_safely(prober.gate())
        address = (rpc_address(found.kind, found.shard)
                   if found.kind in policy.HEALTH_KINDS else None)
        health = (await _probe_safely(prober.rpc(address, "health"))
                  if address is not None else None)
        wall = time.time()
        name = files.evidence_name(wall, found.name)
        evidence = {
            "time": utc(wall), "taken_by": "cmsWatchdog --snapshot",
            "supervisor": asdict(found),
            "health": health.data if health is not None and health.ok
            else None,
            "health_error": health.error if health is not None else "",
            "gate": {"green": gate.green, "reason": gate.reason,
                     "detail": gate.detail},
            "snapshot": list(gate.backends),
            "proc": prober.proc_facts(found.pid),
        }
        try:
            outcome = await control.DryRunActuator(
                source, usr1_wait_s=policy.USR1_WAIT_S).snapshot(
                control.Target(found.name, found.kind, found.pid),
                lambda: files.write_evidence(name, evidence))
        except Exception as error:
            outcome = control.Outcome(
                "error", "the snapshot failed unexpectedly: %s"
                % probes.describe(error))
    finally:
        if own_prober:
            prober.close()
    if outcome.result != "evidence":
        print("cmsWatchdog: no evidence: %s." % outcome.detail, file=out)
        return 1
    if outcome.evidence_error:
        print("cmsWatchdog: the evidence could not be written: %s."
              % outcome.evidence_error, file=out)
        return 1
    print("Evidence: %s (%s)." % (os.path.join(files.evidence_dir, name),
                                  outcome.usr1), file=out)
    return 0


def parse_classes(text: str) -> frozenset[str]:
    """Read --restart: a comma-separated subset of the classes.

    text: the classes, as CMS_WATCHDOG_RESTART gives them.

    return: the classes.

    raise (ValueError): for an unknown class.

    """
    classes = {item.strip().lower() for item in text.split(",")
               if item.strip()}
    unknown = classes - set(policy.CLASS_ORDER)
    if unknown:
        raise ValueError("unknown classes %s (choose from %s)" % (
            ",".join(sorted(unknown)), ",".join(policy.CLASS_ORDER)))
    return frozenset(classes)


def main(argv: Sequence[str] | None = None) -> int:
    """Run cmsWatchdog (see the module's docstring).

    argv: the arguments (sys.argv[1:] by default).

    return: the exit status.

    """
    parser = argparse.ArgumentParser(
        prog="cmsWatchdog",
        description="Restart a CMS program that is alive but hung (#38).")
    one_shot = parser.add_mutually_exclusive_group()
    one_shot.add_argument(
        "--status", action="store_true",
        help="print a summary of the status file, then exit")
    one_shot.add_argument(
        "--snapshot", metavar="PROGRAM",
        help="write the evidence of a RUNNING program (its name in "
             "./status.sh) and send it USR1 when that is safe, then exit; "
             "nothing is restarted")
    parser.add_argument(
        "--mode", choices=(policy.OBSERVE, policy.ENFORCE),
        default=policy.OBSERVE,
        help="observe logs what it would do; enforce does it")
    parser.add_argument(
        "--restart", default="web,core,infra", metavar="CLASSES",
        help="the classes enforce may restart, comma-separated, from web, "
             "core, infra and workers (default: web,core,infra)")
    parser.add_argument(
        "--process-source", choices=("supervisor", "proc"),
        default="supervisor",
        help="where the programs are found; proc (the load-test harness) "
             "cannot act and forces observe mode")
    parser.add_argument(
        "--status-history", metavar="PATH",
        help="also append each cycle's status to PATH as a JSON line")
    args = parser.parse_args(argv)
    files = Files(config.global_.run_dir, config.global_.log_dir)
    if args.status:
        print(status_line(files.status, os.environ, time.time()))
        return 0
    if args.snapshot is not None:
        return asyncio.run(take_snapshot(args.snapshot, files))
    try:
        restart_classes = parse_classes(args.restart)
    except ValueError as error:
        parser.error(str(error))
    mode = args.mode
    if args.process_source == "proc" and mode == policy.ENFORCE:
        print("cmsWatchdog: the proc source cannot act, so the mode is "
              "observe.", file=sys.stderr)
        mode = policy.OBSERVE
    return asyncio.run(serve(mode, restart_classes, args.process_source,
                             files, args.status_history))
