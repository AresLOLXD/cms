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

"""Inject the database faults of a fault run (run.sh --faults, #39).

It runs on the host, next to run.sh, while the driver runs. At fixed
times after the contest start ("start" in users.json) it injects:

- F1, at 120 and 180 s: pg_terminate_backend of every CMS backend;
- F3, at 600 s: docker pause of PostgreSQL for 30 s;
- F4, at 1000 s: cmsSetupDB while a psql session holds a read of
  contests, idle in its transaction;
- F2, at 1080 s: a two-way DROP of PostgreSQL's port for 90 s, then,
  180 s after its end, the runbook's partition step: end the CMS
  backends left idle in a transaction since before that end.

It writes each fault's start and end (epoch seconds) to faults.json,
again after each fault, for faultstats.py. CMS backends are those whose
application_name starts with "cms", so the monitor and psql are spared.
The DROP rules go into PostgreSQL's network namespace (sudo nsenter):
they need only netfilter and leave the loopback inside the container
alone. On an error or on SIGTERM, the injector removes its rules and
unpauses PostgreSQL. Stdlib only.

"""

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable


# Seconds after the contest start.
F1_AT = (120, 180)
F3_AT = 600
F3_PAUSE_S = 30
F4_AT = 1000
F2_AT = 1080
F2_DROP_S = 90
F2_STEP_AFTER_S = 180
# The scores need about 6 minutes after the partition step, before the
# end burst: run.sh refuses a shorter --contest.
MIN_CONTEST_S = 2100

CMS_BACKENDS = "application_name LIKE 'cms%' AND pid <> pg_backend_pid()"
TERMINATE_SQL = ("SELECT count(pg_terminate_backend(pid)) "
                 "FROM pg_stat_activity WHERE " + CMS_BACKENDS)
PARTITION_STEP_SQL = (
    "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
    "WHERE " + CMS_BACKENDS + " AND state LIKE 'idle in transaction%' "
    "AND xact_start < to_timestamp({end})")
HOLDER_READY_SQL = (
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE application_name = 'psql' AND state = 'idle in transaction'")
# psql runs \! in its own shell while its transaction stays open, so the
# holder is idle in transaction for 40 s.
HOLDER_SCRIPT = ("BEGIN;\nSELECT 1 FROM contests LIMIT 1;\n"
                 "\\! sleep 40\nCOMMIT;\n")
SETUP_DB_MESSAGE = ("cmsSetupDB could not lock contests or ranking_groups "
                    "within 5 s")
# In PostgreSQL's namespace: what arrives at its port and what leaves
# from it, except on the loopback.
DROP_RULES = (
    ("INPUT", "!", "-i", "lo", "-p", "tcp", "--dport", "5432", "-j",
     "DROP"),
    ("OUTPUT", "!", "-o", "lo", "-p", "tcp", "--sport", "5432", "-j",
     "DROP"),
)

Runner = Callable[..., subprocess.CompletedProcess]


def run_command(argv: list[str],
                check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and capture its output as text.

    argv: the command.
    check: whether a non-zero exit status raises.

    return: the finished process.

    raise (subprocess.CalledProcessError): if check and it failed.

    """
    return subprocess.run(argv, check=check, capture_output=True,
                          text=True, timeout=120)


def iptables_argv(pid: int, action: str,
                  rule: tuple[str, ...]) -> list[str]:
    """Return the command that adds or deletes a rule in a namespace.

    pid: a process of the network namespace.
    action: "-I" to add the rule, "-D" to delete it.
    rule: the chain and the match of the rule.

    return: the command.

    """
    return ["sudo", "nsenter", "-t", str(pid), "-n", "iptables", action,
            *rule]


class Injector:
    """Inject the faults into one load-test stack, and record them."""

    def __init__(self, project: str, compose_file: str, env_file: str,
                 out: str, run: Runner = run_command,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time):
        """Prepare the injector.

        project, compose_file, env_file: how run.sh calls compose for
            the run.
        out: the path of faults.json.
        run, popen, sleep, clock: the tests replace them.

        """
        self.project = project
        self.compose_file = compose_file
        self.env_file = env_file
        self.out = out
        self.run = run
        self.popen = popen
        self.sleep = sleep
        self.clock = clock
        self.records: list[dict] = []
        # What is in place and must be undone: the DROP rules (with the
        # pid of their namespace), the paused container, the holder.
        self.rules: list[tuple[int, tuple[str, ...]]] = []
        self.paused: str | None = None
        self.holder: subprocess.Popen | None = None

    def docker(self, *args: str) -> list[str]:
        """Return a docker command for the rootful engine, as run.sh."""
        return ["sg", "docker", "-c", shlex.join(
            ["docker", "--context", "default", *args])]

    def compose(self, *args: str) -> list[str]:
        """Return a compose command for the run's stack."""
        return self.docker("compose", "-p", self.project,
                           "-f", self.compose_file,
                           "--env-file", self.env_file, *args)

    def psql(self, sql: str) -> str:
        """Run one statement in the db container; return its output."""
        return self.run(self.compose(
            "exec", "-T", "db", "psql", "-U", "cms", "-d", "cmsdb",
            "-At", "-c", sql)).stdout.strip()

    def db_container(self) -> str:
        """Return the id of the run's PostgreSQL container."""
        return self.run(self.compose("ps", "-q", "db")).stdout.strip()

    def db_pid(self) -> int:
        """Return the host pid of the PostgreSQL container."""
        return int(self.run(self.docker(
            "inspect", "-f", "{{.State.Pid}}",
            self.db_container())).stdout.strip())

    def save(self) -> None:
        """Write the records to faults.json, atomically."""
        partial = self.out + ".tmp"
        with open(partial, "w") as f:
            json.dump(self.records, f, indent=1)
        os.replace(partial, self.out)

    def sleep_until(self, moment: float) -> None:
        """Sleep until an epoch time, if it is still ahead."""
        delay = moment - self.clock()
        if delay > 0:
            self.sleep(delay)

    def f1(self) -> None:
        """End every CMS backend."""
        start = self.clock()
        terminated = int(self.psql(TERMINATE_SQL))
        self.records.append({"fault": "F1", "start": start,
                             "end": self.clock(), "terminated": terminated})

    def f2(self) -> None:
        """Drop PostgreSQL's traffic both ways, then do the runbook's
        partition step.

        """
        pid = self.db_pid()
        start = self.clock()
        for rule in DROP_RULES:
            # Recorded before it goes in: if SIGTERM arrives as it does,
            # cleanup still deletes it (deleting an absent rule is only
            # a warning).
            self.rules.append((pid, rule))
            self.run(iptables_argv(pid, "-I", rule))
        self.sleep(F2_DROP_S)
        self._delete_rules()
        end = self.clock()
        record = {"fault": "F2", "start": start, "end": end}
        self.records.append(record)
        self.save()
        self.sleep_until(end + F2_STEP_AFTER_S)
        record["partition_step"] = self.clock()
        record["partition_step_terminated"] = int(
            self.psql(PARTITION_STEP_SQL.format(end=end)))

    def f3(self) -> None:
        """Pause PostgreSQL: its kernel still answers, it does not."""
        container = self.db_container()
        start = self.clock()
        # Recorded before the pause, as the rules of f2.
        self.paused = container
        self.run(self.docker("pause", container))
        self.sleep(F3_PAUSE_S)
        self.run(self.docker("unpause", container))
        self.paused = None
        self.records.append({"fault": "F3", "start": start,
                             "end": self.clock()})

    def f4(self) -> None:
        """Run cmsSetupDB while a psql session holds a read of contests."""
        self.holder = self.popen(
            self.compose("exec", "-T", "db", "psql", "-U", "cms",
                         "-d", "cmsdb", "-q"),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, text=True)
        self.holder.stdin.write(HOLDER_SCRIPT)
        self.holder.stdin.close()
        for _ in range(30):
            if self.psql(HOLDER_READY_SQL) != "0":
                break
            self.sleep(1)
        start = self.clock()
        setup = self.run(self.compose("exec", "-T", "cms", "cmsSetupDB"),
                         check=False)
        end = self.clock()
        self.holder.wait(timeout=60)
        self.holder = None
        self.records.append({
            "fault": "F4", "start": start, "end": end,
            "exit_status": setup.returncode,
            "duration": round(end - start, 1),
            "message": SETUP_DB_MESSAGE in setup.stdout + setup.stderr})

    def _delete_rules(self) -> None:
        """Delete the DROP rules in place, the last added first.

        A rule leaves the list only once its deletion was tried, so that
        a signal during it leaves the rule to cleanup.

        """
        while self.rules:
            pid, rule = self.rules[-1]
            deleted = self.run(iptables_argv(pid, "-D", rule), check=False)
            self.rules.pop()
            if deleted.returncode != 0:
                print("iptables -D %s failed: %s"
                      % (" ".join(rule), deleted.stderr.strip()),
                      file=sys.stderr, flush=True)

    def cleanup(self) -> None:
        """Undo whatever fault is still in place."""
        self._delete_rules()
        if self.paused is not None:
            self.run(self.docker("unpause", self.paused), check=False)
            self.paused = None
        if self.holder is not None:
            self.holder.kill()
            self.holder = None

    def run_schedule(self, contest_start: float) -> None:
        """Inject every fault at its time, and record each one.

        contest_start: the contest start, in epoch seconds.

        """
        schedule = ((F1_AT[0], self.f1), (F1_AT[1], self.f1),
                    (F3_AT, self.f3), (F4_AT, self.f4), (F2_AT, self.f2))
        for offset, inject in schedule:
            self.sleep_until(contest_start + offset)
            inject()
            self.save()
            print(json.dumps(self.records[-1]), flush=True)


def main() -> int:
    """Parse the arguments and inject the faults."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--compose-file", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--users-file", required=True,
                        help="users.json, for the contest start")
    parser.add_argument("--out", required=True, help="faults.json")
    args = parser.parse_args()
    with open(args.users_file) as f:
        contest_start = json.load(f)["start"]
    injector = Injector(args.project, args.compose_file, args.env_file,
                        args.out)
    # run.sh ends the injector with SIGTERM if the run ends first.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(143))
    try:
        injector.run_schedule(contest_start)
    finally:
        injector.cleanup()
        injector.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
