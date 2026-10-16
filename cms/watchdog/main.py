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

import json
import os
import sys
import time
from collections.abc import Iterable, Mapping
from typing import TextIO

from cms.watchdog import policy, probes


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

    raise (OSError): if it cannot be written.

    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = path + ".tmp"
    with open(partial, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(partial, path)


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
        an hour of exhausted budgets, no booking in the future.

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
                "Watchdog: its ledger %s did not parse (%s): every restart "
                "budget counts as used until %s." % (
                    self.ledger, " ".join(str(error).split()),
                    policy.hhmm_utc(ledger.exhausted_until)))
        # A time ahead of now (a hand edit, a clock that went back) must
        # not freeze the budgets past the hour of spec 3.10.
        limit = wall + policy.CORRUPT_LEDGER_S
        if ledger.exhausted_until is not None \
                and ledger.exhausted_until > limit:
            ledger.exhausted_until = limit
        for booking in ledger.real + ledger.shadow:
            booking.wall = min(booking.wall, wall)
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
        write_atomic(self.status,
                     json.dumps(status, sort_keys=True, default=str))

    def append_journal(self, record: Mapping) -> None:
        """Append a line to the journal (raise OSError if it cannot)."""
        os.makedirs(self.evidence_dir, exist_ok=True)
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True, default=str) + "\n")

    def evidence_name(self, wall: float, program: str) -> str:
        """Return the name of an evidence file in evidence_dir."""
        return "%s-%s.json" % (
            time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(wall)),
            program.replace(" ", "_"))

    def write_evidence(self, name: str, evidence: Mapping) -> None:
        """Write an evidence file (raise OSError if it cannot be)."""
        write_atomic(os.path.join(self.evidence_dir, name),
                     json.dumps(evidence, indent=1, sort_keys=True,
                                default=str))


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
    first call that fails, the rest of that cycle's lines are left to
    stdout, so a stuck LogService costs one timeout per cycle.

    """

    def __init__(self, log_service: probes.RpcProbe | None,
                 out: TextIO | None = None):
        """Prepare the channels.

        log_service: an RpcProbe to LogService (one persistent
            connection of its own), or None.
        out: where to print (sys.stdout by default).

        """
        self._log_service = log_service
        self._out = out if out is not None else sys.stdout
        self._pending: list[dict] = []

    def emit(self, level: str, text: str, wall: float) -> None:
        """Print an alert, and queue it for LogService if WARNING+."""
        print("%s %s %s" % (utc(wall), level, text), file=self._out,
              flush=True)
        if self._log_service is not None \
                and LEVELS[level] >= LEVELS["WARNING"]:
            self._pending.append(log_record(level, text, wall))

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

    async def flush(self) -> None:
        """Send the queued lines to LogService."""
        pending, self._pending = self._pending, []
        for record in pending:
            result = await self._log_service.call("Log", record)
            if not result.ok:
                break


def _budget_use(status: Mapping, now: float) -> str:
    """Return what --status says of the budgets (H7).

    program_status's counts count bookings only, and a ledger that did
    not parse exhausts every budget with none: its state comes first.
    The would-restarts are those of observe mode and those of H9; the
    FATAL starts count R5's would-starts too, as its budget does.

    status: the status file's content.
    now: the epoch time.

    return: "" when there is nothing to say, else the text to append.

    raise (TypeError, ValueError, AttributeError, KeyError): if the
        status file does not hold what the watchdog writes.

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
        mode = status["mode"]
        since = max(0.0, now - float(status["time"]))
        open_alerts = len(status["open_alerts"])
        budgets = _budget_use(status, now)
    except FileNotFoundError:
        return "no status file"
    except (OSError, ValueError, KeyError, TypeError,
            AttributeError) as error:
        return "watchdog: the status file %s does not parse (%s)" % (
            path, " ".join(str(error).split()))
    line = "watchdog: %s, last cycle %d s ago, %d open alerts" % (
        mode, since, open_alerts)
    if status.get("supervisord") == "unreachable":
        line += ", supervisord unreachable"
    line += budgets
    if since > 3 * policy.CYCLE_S:
        line += " (stale: is cmswatchdog running? See ./status.sh)"
    return line
