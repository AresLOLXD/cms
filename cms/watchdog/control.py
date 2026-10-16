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
import shlex
import socket
import xmlrpc.client
from collections.abc import Sequence
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
