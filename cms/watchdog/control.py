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

"""Process sources and actuators of cmsWatchdog (#38).

A process source tells which programs run, in which state and with
which pid: supervisord (SupervisorSource, which can also start them) or
/proc (ProcSource, for the load-test harness, which has no supervisord
and cannot act). An actuator restarts a program with signals to its
process group, or only says it would. This module knows nothing about
the rules (policy.py) and imports only the standard library (and
procfs.py, which does too).

"""

import asyncio
import concurrent.futures
import http.client
import os
import select
import shlex
import signal
import socket
import xmlrpc.client
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from cms.watchdog.procfs import CLOCK_TICKS, stat_fields


# supervisord's fault code for a program that is already running.
ALREADY_STARTED = 60
# Where supervisord listens when SUPERVISOR_SERVER_URL is not set
# (docker/generate_config.py).
DEFAULT_SERVER_URL = "unix:///tmp/supervisor.sock"
# The kinds that may get the evidence USR1 (3.8): CMS services whose
# base class (AsyncService, or the gevent Service) registers
# faulthandler on it. control_actuators_test.py pins it against them.
USR1_KINDS = frozenset({
    "LogService", "ResourceService", "EvaluationService",
    "ScoringService", "ProxyService", "ContestWebServer",
    "AdminWebServer", "Worker"})


@dataclass(frozen=True)
class ProcessInfo:
    """One program, as a process source sees it.

    It has the fields of policy.ProgramFacts (see there).

    """

    name: str
    kind: str | None
    shard: int | None
    state: str
    pid: int | None
    start_wall: float | None


def parse_command(argv: Sequence[str]) -> tuple[str, int | None] | None:
    """Find the CMS program in a command line (3.2).

    It is the first argument whose basename is "cms" followed by an
    upper-case letter, which skips an interpreter's argv[0]
    (".../bin/python3 .../bin/cmsWorker 0"); its shard is the next
    argument, when that is a decimal integer.

    argv: the command line.

    return: (kind, shard), the kind being what follows "cms" and the
        shard None without one; or None when no argument is a CMS
        program.

    """
    for index, argument in enumerate(argv):
        base = os.path.basename(argument)
        if base.startswith("cms") and base[3:4].isupper():
            shard = argv[index + 1] if index + 1 < len(argv) else ""
            return base[3:], int(shard) if shard.isdecimal() else None
    return None


def split_command(command: str) -> list[str]:
    """Split a supervisord command= line; [] if it does not parse."""
    try:
        return shlex.split(command)
    except ValueError:
        return []


class _UnixConnection(http.client.HTTPConnection):
    """An HTTP connection over a unix socket, with a timeout."""

    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


class _UnixTransport(xmlrpc.client.Transport):
    """An XML-RPC transport over a unix socket."""

    def __init__(self, path: str, timeout: float):
        super().__init__()
        self._path = path
        self._timeout = timeout

    def make_connection(self, host: object) -> _UnixConnection:
        """Return a connection to the socket; host is not used."""
        return _UnixConnection(self._path, self._timeout)


class SupervisorSource:
    """supervisord's XML-RPC interface on its unix socket (3.2).

    Every call has a socket timeout and runs in a one-thread executor
    under asyncio.wait_for, so a hung supervisord cannot block the
    watchdog's loop. It never runs supervisorctl.

    """

    can_act = True

    def __init__(self, url: str | None = None, timeout: float = 5.0):
        """Prepare the client; it connects at each call.

        url: supervisord's "unix://" URL; by default the
            SUPERVISOR_SERVER_URL that supervisord gives its programs,
            or DEFAULT_SERVER_URL.
        timeout: the socket timeout of each call, in seconds.

        raise (ValueError): if url is not a unix socket.

        """
        url = url or os.environ.get("SUPERVISOR_SERVER_URL") \
            or DEFAULT_SERVER_URL
        if not url.startswith("unix://"):
            raise ValueError("cmsWatchdog talks to supervisord on a unix "
                             "socket only, not %s" % url)
        self._proxy = xmlrpc.client.ServerProxy(
            "http://localhost/RPC2",
            transport=_UnixTransport(url[len("unix://"):], timeout),
            allow_none=True)
        self._timeout = timeout
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="supervisor")
        # By "group:name": its command (""), and the group of each name.
        self._commands: dict[str, str] = {}
        self._groups: dict[str, str] = {}

    async def _call(self, method: str, *args: Any) -> Any:
        """Call a supervisor.* method in the executor, with a timeout."""
        loop = asyncio.get_running_loop()
        function = getattr(self._proxy.supervisor, method)
        return await asyncio.wait_for(
            loop.run_in_executor(self._executor, function, *args),
            self._timeout + 1)

    async def programs(self) -> list[ProcessInfo]:
        """Return every program supervisord manages.

        Its command comes from getAllConfigInfo(), read again only when
        a new program appears. A program without one, or whose command
        does not parse, is unmanaged (kind None).

        return: the programs.

        raise (OSError, TimeoutError, xmlrpc.client.Error): if
            supervisord does not answer in time, or badly.

        """
        infos = await self._call("getAllProcessInfo")
        names = ["%s:%s" % (info["group"], info["name"]) for info in infos]
        if any(name not in self._commands for name in names):
            configs = await self._call("getAllConfigInfo")
            self._commands = {
                "%s:%s" % (item["group"], item["name"]):
                    str(item.get("command") or "")
                for item in configs}
            for name in names:
                self._commands.setdefault(name, "")
        programs = []
        for name, info in zip(names, infos):
            self._groups[info["name"]] = info["group"]
            parsed = parse_command(split_command(self._commands[name]))
            kind, shard = parsed if parsed is not None else (None, None)
            programs.append(ProcessInfo(
                name=info["name"], kind=kind, shard=shard,
                state=str(info["statename"]), pid=info["pid"] or None,
                start_wall=float(info["start"]) or None))
        return programs

    async def start(self, name: str) -> None:
        """Start a program that is not running (R5).

        name: the program's name.

        raise (xmlrpc.client.Fault): if supervisord refuses, except
            ALREADY_STARTED (an operator started it first), which is
            success.

        """
        group = self._groups.get(name, name)
        try:
            await self._call("startProcess", "%s:%s" % (group, name), False)
        except xmlrpc.client.Fault as fault:
            if fault.faultCode != ALREADY_STARTED:
                raise

    def close(self) -> None:
        """Let the executor's thread end."""
        self._executor.shutdown(wait=False)


class ProcSource:
    """The CMS programs /proc shows, for the harness (3.2).

    A program (named by its label, "cmsLogService 0") is RUNNING while a
    process runs it, and ABSENT once it has been seen and is gone. It
    cannot signal or start anything: main.py forces observe mode with
    it.

    """

    can_act = False

    def __init__(self, proc: str = "/proc"):
        """Prepare the source.

        proc: where /proc is (tests pass a fake tree).

        """
        self._proc = proc
        self._seen: dict[str, ProcessInfo] = {}

    async def programs(self) -> list[ProcessInfo]:
        """Return the CMS programs with a shard that /proc shows."""
        boot = self._boot_time()
        current: dict[str, ProcessInfo] = {}
        for entry in os.listdir(self._proc):
            if not entry.isdecimal():
                continue
            info = self._read(int(entry), boot)
            if info is None:
                continue
            known = current.get(info.name)
            if known is None or info.start_wall < known.start_wall:
                current[info.name] = info
        self._seen.update(current)
        programs = list(current.values())
        for name, info in self._seen.items():
            if name not in current:
                programs.append(ProcessInfo(name, info.kind, info.shard,
                                            "ABSENT", None,
                                            info.start_wall))
        return sorted(programs, key=lambda info: info.name)

    def _read(self, pid: int, boot: float) -> ProcessInfo | None:
        """Return the program pid runs, or None if it runs none."""
        try:
            with open("%s/%d/cmdline" % (self._proc, pid), "rb") as f:
                argv = [part.decode("utf-8", "replace")
                        for part in f.read().split(b"\0") if part]
            parsed = parse_command(argv)
            if parsed is None or parsed[1] is None:
                return None
            fields = stat_fields("%s/%d/stat" % (self._proc, pid))
            start = boot + int(fields[19]) / CLOCK_TICKS
        except (OSError, ValueError, IndexError):
            return None
        kind, shard = parsed
        return ProcessInfo("cms%s %d" % (kind, shard), kind, shard,
                           "RUNNING", pid, start)

    def _boot_time(self) -> float:
        """Return when the machine booted (btime of /proc/stat)."""
        with open("%s/stat" % self._proc) as f:
            for line in f:
                if line.startswith("btime "):
                    return float(line.split()[1])
        raise ValueError("no btime in %s/stat" % self._proc)


# The process sources: main.py picks one.
ProcessSource = SupervisorSource | ProcSource


@dataclass(frozen=True)
class Target:
    """The process an action is about: a program and its pid."""

    name: str
    kind: str
    pid: int


@dataclass(frozen=True)
class Outcome:
    """What an action did.

    result: "restarted"; "unconfirmed" (signalled, but no new pid was
        RUNNING in time); "would" (a dry run); "started" or "start
        failed" (a batch); "evidence" (a snapshot); "refused" (the pid
        changed or it is not RUNNING, nothing was signalled); "gone"
        (it exited on its own, before any TERM); "failed" (a system
        call failed: detail says which, and TERM was sent only when it
        is KILL); "unbooked" (the ledger could not be written, nothing
        was done).
    detail: a sentence for the alert.
    new_pid: the program's pid after a restart.
    killed: whether some process of the group was left for KILL after
        the TERM wait.
    usr1: what happened to the evidence USR1.
    evidence_error: why the evidence could not be written, if it
        could not.

    """

    result: str
    detail: str = ""
    new_pid: int | None = None
    killed: bool = False
    usr1: str = ""
    evidence_error: str = ""


def handles_signal(pid: int, signum: int, proc: str = "/proc") -> bool:
    """Tell whether a process catches signum (SigCgt of its status)."""
    try:
        with open("%s/%d/status" % (proc, pid)) as f:
            for line in f:
                if line.startswith("SigCgt:"):
                    return bool(int(line.split()[1], 16)
                                & (1 << (signum - 1)))
    except (OSError, ValueError, IndexError):
        return False
    return False


def process_state(pid: int, proc: str = "/proc") -> str | None:
    """Return a process's state letter, or None if it is gone."""
    try:
        return stat_fields("%s/%d/stat" % (proc, pid))[0]
    except (OSError, ValueError, IndexError):
        return None


def group_members(pgid: int, proc: str = "/proc") -> list[int]:
    """Return the processes of a process group that are not zombies."""
    members = []
    for entry in os.listdir(proc):
        if not entry.isdecimal():
            continue
        try:
            fields = stat_fields("%s/%s/stat" % (proc, entry))
        except (OSError, ValueError, IndexError):
            continue
        if len(fields) > 2 and fields[2] == str(pgid) \
                and fields[0] not in ("Z", "X"):
            members.append(int(entry))
    return members


async def wait_readable(fd: int, timeout: float) -> bool:
    """Wait until fd is readable: for a pidfd, until the process exits.

    fd: the file descriptor.
    timeout: how long to wait at most, in seconds.

    return: whether it became readable in time.

    """
    loop = asyncio.get_running_loop()
    ready = loop.create_future()

    def on_ready() -> None:
        if not ready.done():
            ready.set_result(None)

    loop.add_reader(fd, on_ready)
    try:
        await asyncio.wait_for(ready, timeout)
        return True
    except TimeoutError:
        return False
    finally:
        loop.remove_reader(fd)


def has_exited(pidfd: int) -> bool:
    """Tell, without waiting, whether a pidfd's process has exited.

    A pidfd is readable once its process has exited, a zombie included.
    wait_readable(pidfd, 0) cannot tell: asyncio.wait_for gives up
    before the reader can run.

    pidfd: the pidfd.

    return: whether its process has exited.

    """
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    return bool(poller.poll(0))


class DryRunActuator:
    """The actuator of would-actions (3.7).

    It books, checks that the pid is still the program's, writes the
    evidence and sends the evidence USR1 (only to a process that
    handles it), and then does nothing else: main.py logs "WOULD ...".

    """

    def __init__(self, source: ProcessSource, *, proc: str = "/proc",
                 usr1_wait_s: float = 1.0,
                 pidfd_open: Callable[[int], int] = os.pidfd_open,
                 send_signal: Callable[[int, int], None] =
                 signal.pidfd_send_signal):
        """Prepare the actuator.

        source: the process source (programs(), and start() for
            SignalActuator).
        proc: where /proc is.
        usr1_wait_s: how long faulthandler gets to write the stacks.
        pidfd_open, send_signal: os.pidfd_open and
            signal.pidfd_send_signal (tests may replace them).

        """
        self._source = source
        self._proc = proc
        self._usr1_wait_s = usr1_wait_s
        self._pidfd_open = pidfd_open
        self._send_signal = send_signal

    @staticmethod
    def _book(book: Callable[[], None]) -> Outcome | None:
        """Book an action, its first step (3.7, step 0).

        book: writes the ledger and the journal.

        return: None once booked, or the "unbooked" outcome when the
            ledger could not be written: the action stops there.

        """
        try:
            book()
        except OSError as error:
            return Outcome("unbooked", "its ledger could not be written: "
                           "%s" % error)
        return None

    async def _lookup(self, name: str) -> ProcessInfo | None:
        for info in await self._source.programs():
            if info.name == name:
                return info
        return None

    async def _open_verified(self,
                             target: Target) -> tuple[int, Outcome | None]:
        """Open a pidfd of target, still the program's (3.7, step 1).

        Opening it first, then re-reading the source, binds the pidfd to
        the process the source names: a pid reused later cannot be hit.

        return: (pidfd, None), or (-1, the refusal).

        """
        try:
            pidfd = self._pidfd_open(target.pid)
        except ProcessLookupError:
            return -1, Outcome("gone", "pid %d had already exited"
                               % target.pid)
        except OSError as error:
            return -1, Outcome("failed", "pidfd_open failed: %s" % error)
        try:
            current = await self._lookup(target.name)
        except Exception as error:
            os.close(pidfd)
            return -1, Outcome("refused", "the process source did not "
                               "answer: %s" % error)
        if current is None or current.state != "RUNNING" \
                or current.pid != target.pid:
            os.close(pidfd)
            return -1, Outcome("refused", "it is no longer RUNNING as pid "
                               "%d" % target.pid)
        return pidfd, None

    async def _evidence(self, pidfd: int, target: Target,
                        write_evidence: Callable[[], None]
                        ) -> tuple[str, str]:
        """Write the evidence, then send the USR1 if it is safe (3.8).

        return: (what happened to the USR1, the evidence's error or "").

        """
        evidence_error = ""
        try:
            write_evidence()
        except Exception as error:
            evidence_error = " ".join(str(error).split()) \
                or type(error).__name__
        if target.kind not in USR1_KINDS:
            return ("no USR1: %s is not a CMS service" % target.kind,
                    evidence_error)
        if process_state(target.pid, self._proc) == "T":
            return ("no USR1: the process is stopped (state T)",
                    evidence_error)
        if not handles_signal(target.pid, signal.SIGUSR1, self._proc):
            return "no USR1: the process does not handle it", evidence_error
        try:
            # The leader only, never the group: a Worker's isolate keeper
            # does not handle USR1, and it would die of it.
            self._send_signal(pidfd, signal.SIGUSR1)
        except ProcessLookupError:
            return "no USR1: the process exited", evidence_error
        except OSError as error:
            # Like a failed evidence write, it does not stop the action.
            return "no USR1: %s" % error, evidence_error
        await asyncio.sleep(self._usr1_wait_s)
        return "USR1 sent: its stacks are in ./logs.sh", evidence_error

    async def snapshot(self, target: Target,
                       write_evidence: Callable[[], None]) -> Outcome:
        """Write the evidence and send the USR1, booking nothing.

        For cmsWatchdog --snapshot, and steps 1 and 2 of an action.

        """
        pidfd, refusal = await self._open_verified(target)
        if refusal is not None:
            return refusal
        try:
            usr1, evidence_error = await self._evidence(pidfd, target,
                                                        write_evidence)
        finally:
            os.close(pidfd)
        return Outcome("evidence", usr1=usr1, evidence_error=evidence_error)

    async def restart(self, target: Target, book: Callable[[], None],
                      write_evidence: Callable[[], None]) -> Outcome:
        """Book a would-restart, then do steps 1 and 2 only."""
        unbooked = self._book(book)
        if unbooked is not None:
            return unbooked
        outcome = await self.snapshot(target, write_evidence)
        if outcome.result != "evidence":
            return outcome
        return Outcome("would", usr1=outcome.usr1,
                       evidence_error=outcome.evidence_error)

    async def start(self, names: Sequence[str],
                    book: Callable[[], None]) -> Outcome:
        """Book a would-start of a batch; start nothing."""
        unbooked = self._book(book)
        if unbooked is not None:
            return unbooked
        return Outcome("would")


class SignalActuator(DryRunActuator):
    """The actuator of enforce mode (3.7).

    A restart is a signal to the program's process group: TERM, a wait
    for the leader to exit, KILL to whatever is left, and supervisord's
    autorestart starts it again. It never runs supervisorctl.

    """

    def __init__(self, source: SupervisorSource, *,
                 term_wait_s: float = 10.0, confirm_s: float = 30.0,
                 poll_s: float = 1.0,
                 killpg: Callable[[int, int], None] = os.killpg,
                 **kwargs: Any):
        """Prepare the actuator.

        source: a process source that can act (SupervisorSource).
        term_wait_s: how long TERM gets before KILL.
        confirm_s: how long the new pid gets to be RUNNING.
        poll_s: how often the source is read meanwhile.
        killpg: os.killpg (tests may wrap it).
        kwargs: those of DryRunActuator.

        """
        super().__init__(source, **kwargs)
        self._term_wait_s = term_wait_s
        self._confirm_s = confirm_s
        self._poll_s = poll_s
        self._killpg = killpg

    async def restart(self, target: Target, book: Callable[[], None],
                      write_evidence: Callable[[], None]) -> Outcome:
        """Restart target (3.7, steps 0 to 7).

        It returns within one read of the source (step 1) + usr1_wait_s
        + term_wait_s + confirm_s + min(poll_s, 1 s), plus the booking
        and the evidence write. With the defaults and SupervisorSource's
        5 s timeout, at worst 12 s (two supervisord calls, when a new
        program appeared) + 1 + 10 + 30 + 1 = 54 s: close to the 60 s
        self-guard (3.11).

        target: the program and the pid the policy saw.
        book: writes the ledger and the journal; an OSError stops the
            action before anything else.
        write_evidence: writes the evidence file; its failure is
            reported, and the action goes on.

        return: the outcome: "unbooked", "gone", "refused", "failed",
            "restarted" or "unconfirmed" (see Outcome).

        """
        unbooked = self._book(book)
        if unbooked is not None:
            return unbooked
        pidfd, refusal = await self._open_verified(target)
        if refusal is not None:
            return refusal
        try:
            usr1, evidence_error = await self._evidence(pidfd, target,
                                                        write_evidence)
            # Exited meanwhile, it was not restarted by us; and while it
            # has not, its pid, so the group's, is still its own.
            if has_exited(pidfd):
                return Outcome("gone", "pid %d exited before its TERM"
                               % target.pid, usr1=usr1,
                               evidence_error=evidence_error)
            try:
                self._killpg(target.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError as error:
                return Outcome("failed", "TERM failed: %s" % error,
                               usr1=usr1, evidence_error=evidence_error)
            await wait_readable(pidfd, self._term_wait_s)
            killed = False
            if group_members(target.pid, self._proc):
                try:
                    self._killpg(target.pid, signal.SIGKILL)
                    killed = True
                except ProcessLookupError:
                    pass
                except OSError as error:
                    return Outcome("failed", "KILL failed: %s" % error,
                                   usr1=usr1, evidence_error=evidence_error)
        finally:
            os.close(pidfd)
        new_pid = await self._confirm(target)
        if new_pid is None:
            return Outcome("unconfirmed", "no new pid was RUNNING within "
                           "%g s" % self._confirm_s, killed=killed,
                           usr1=usr1, evidence_error=evidence_error)
        return Outcome("restarted", new_pid=new_pid, killed=killed,
                       usr1=usr1, evidence_error=evidence_error)

    async def _confirm(self, target: Target) -> int | None:
        """Return the new pid once RUNNING, or None after confirm_s.

        Each pause is cut at the time left, and so is each read of the
        source, but never below min(poll_s, 1 s), so that the read at
        the deadline runs. It returns within confirm_s + min(poll_s,
        1 s), however slow the source is.

        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._confirm_s
        while True:
            try:
                current = await asyncio.wait_for(
                    self._lookup(target.name),
                    max(deadline - loop.time(), min(self._poll_s, 1.0)))
            except Exception:
                current = None
            if current is not None and current.state == "RUNNING" \
                    and current.pid not in (None, target.pid):
                return current.pid
            left = deadline - loop.time()
            if left <= 0:
                return None
            await asyncio.sleep(min(self._poll_s, left))

    async def start(self, names: Sequence[str],
                    book: Callable[[], None]) -> Outcome:
        """Start a batch of FATAL programs, in order (R5)."""
        unbooked = self._book(book)
        if unbooked is not None:
            return unbooked
        failures = []
        for name in names:
            try:
                await self._source.start(name)
            except Exception as error:
                failures.append("%s: %s" % (name, error))
        if failures:
            return Outcome("start failed", "; ".join(failures))
        return Outcome("started")
