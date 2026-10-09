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
- F3, at 600 s: docker pause of PostgreSQL for 30 s; 2 s into it, a
  probe in the cms container opens a fresh connection through CMS's own
  path, which must fail with "timeout expired" at about 10 s;
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
# The probe starts this long after the pause and is cut after
# F3_PROBE_TIMEOUT_S: it outlasts the 10 s connect deadline and ends
# before the unpause. The injector waits F3_PROBE_COLLECT_S for its answer.
F3_PROBE_AFTER_S = 2
F3_PROBE_TIMEOUT_S = 25
F3_PROBE_COLLECT_S = 10
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
# F3's probe: a fresh connection through CMS's own path (green psycopg2,
# the connection defaults of cms.db.connect_options), outside any pool.
# The elapsed time and connect_started_at (epoch seconds, to tell a
# connect that fell outside the pause) start after the imports. It prints
# one JSON line.
PROBE_SCRIPT = """\
import json
import time

import cms.io  # makes psycopg2 green, as in the services
from cms.db.session import custom_psycopg2_connection

connect_started_at = time.time()
start = time.monotonic()
try:
    custom_psycopg2_connection().close()
    result = {"outcome": "ok", "error": ""}
except Exception as error:
    result = {"outcome": "error", "error": "%s: %s" % (
        type(error).__name__, " ".join(str(error).split()))}
result["elapsed_s"] = round(time.monotonic() - start, 1)
result["connect_started_at"] = connect_started_at
print(json.dumps(result))
"""
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


def report(message: str) -> None:
    """Print a problem of the cleanup to stderr (it goes to faults.log)."""
    print(message, file=sys.stderr, flush=True)


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
        # pid of their namespace), the paused container, the holder, the
        # probe.
        self.rules: list[tuple[int, tuple[str, ...]]] = []
        self.paused: str | None = None
        self.holder: subprocess.Popen | None = None
        self.probe: subprocess.Popen | None = None

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
        """Pause PostgreSQL: its kernel still answers, it does not.

        Two seconds in, a probe tries a fresh connection from the cms
        container. A connect now reaches a kernel that completes the
        handshake and a PostgreSQL that never answers, so only the
        connect deadline ends it ("timeout expired", at about 10 s). The
        probe runs in the background: the unpause is not delayed by it.

        """
        container = self.db_container()
        start = self.clock()
        # Recorded before the pause, as the rules of f2.
        self.paused = container
        self.run(self.docker("pause", container))
        self.sleep(F3_PROBE_AFTER_S)
        self.probe = self.popen(
            self.compose("exec", "-T", "cms", "timeout",
                         str(F3_PROBE_TIMEOUT_S), "python3", "-c",
                         PROBE_SCRIPT),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        self.sleep(F3_PAUSE_S - F3_PROBE_AFTER_S)
        self.run(self.docker("unpause", container))
        self.paused = None
        self.records.append({"fault": "F3", "start": start,
                             "end": self.clock(),
                             "probe": self._probe_result()})

    def _probe_result(self) -> dict:
        """Collect the answer of the F3 probe, which ended before now.

        return: {"elapsed_s", "outcome", "error"}; the outcome is "ok",
            "error" or "hung" (cut by timeout(1), or not answering).

        """
        probe = self.probe
        try:
            out, err = probe.communicate(timeout=F3_PROBE_COLLECT_S)
        except subprocess.TimeoutExpired:
            probe.kill()
            self.probe = None
            return {"elapsed_s": None, "outcome": "hung", "error": ""}
        self.probe = None
        for line in reversed(out.splitlines()):
            try:
                result = json.loads(line)
            except ValueError:
                continue
            if isinstance(result, dict) and "outcome" in result:
                return result
        # timeout(1) exits with 124 when it had to cut the command.
        if probe.returncode == 124:
            return {"elapsed_s": None, "outcome": "hung", "error": ""}
        return {"elapsed_s": None, "outcome": "error",
                "error": "no answer (exit %s): %s"
                % (probe.returncode, err.strip()[-300:])}

    def f4(self) -> None:
        """Run cmsSetupDB while a psql session holds a read of contests."""
        self.holder = self.popen(
            self.compose("exec", "-T", "db", "psql", "-U", "cms",
                         "-d", "cmsdb", "-q"),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, text=True)
        self.holder.stdin.write(HOLDER_SCRIPT)
        self.holder.stdin.close()
        ready = False
        for _ in range(30):
            if self.psql(HOLDER_READY_SQL) != "0":
                ready = True
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
            "holder_ready": ready, "exit_status": setup.returncode,
            "duration": round(end - start, 1),
            "message": SETUP_DB_MESSAGE in setup.stdout + setup.stderr})

    def _delete_rules(self) -> None:
        """Delete the DROP rules in place, the last added first.

        A rule leaves the list only once its deletion was tried, so that
        a signal during it leaves the rule to cleanup. A deletion that
        fails or raises is reported, and the next one is tried.

        """
        while self.rules:
            pid, rule = self.rules[-1]
            try:
                deleted = self.run(iptables_argv(pid, "-D", rule),
                                   check=False)
            except Exception as error:
                report("iptables -D %s raised %r" % (" ".join(rule), error))
            else:
                if deleted.returncode != 0:
                    report("iptables -D %s failed: %s"
                           % (" ".join(rule), deleted.stderr.strip()))
            self.rules.pop()

    def _unpause(self) -> None:
        """Unpause PostgreSQL if it is paused; report a failure."""
        if self.paused is None:
            return
        unpaused = self.run(self.docker("unpause", self.paused), check=False)
        container, self.paused = self.paused, None
        if unpaused.returncode != 0:
            report("docker unpause %s failed: %s"
                   % (container, unpaused.stderr.strip()))

    def _end_holder(self) -> None:
        """Kill the holder's psql, if there is one."""
        if self.holder is not None:
            holder, self.holder = self.holder, None
            holder.kill()

    def _end_probe(self) -> None:
        """Kill the F3 probe's client, if it is still running.

        The probe in the container ends by itself: timeout(1) cuts it.

        """
        if self.probe is not None:
            probe, self.probe = self.probe, None
            probe.kill()

    def _guarded(self, what: str, step: Callable[[], None]) -> None:
        """Run a cleanup step; report an exception instead of raising."""
        try:
            step()
        except Exception as error:
            report("cleanup: %s raised %r" % (what, error))

    def cleanup(self) -> None:
        """Undo whatever fault is still in place, and save the records.

        Every step runs even if an earlier one failed: a problem is
        reported to stderr and the next step goes on.

        """
        self._delete_rules()
        self._guarded("docker unpause", self._unpause)
        self._guarded("ending the holder", self._end_holder)
        self._guarded("ending the probe", self._end_probe)
        self._guarded("saving faults.json", self.save)

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
        # A second SIGTERM must not cut the cleanup short.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        injector.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
