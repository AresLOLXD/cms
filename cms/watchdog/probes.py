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

"""The probes of cmsWatchdog (#38): HTTP, RPC, the database gate, /proc.

They report what they see and decide nothing: the rules are in
policy.py, and main.py passes the timeouts. Each probe has its own
timeout, and none raises for a failure of what it probes: a failure is
a result. This module imports only the standard library and procfs.py.

"""

import asyncio
import json
import math
import os
import re
import socket
import ssl
import time
import uuid
from asyncio.subprocess import DEVNULL, PIPE
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from cms.watchdog.procfs import CLOCK_TICKS, stat_fields


# The largest RPC answer read, as CMS's own MAX_MESSAGE_SIZE.
MAX_MESSAGE_SIZE = 1024 * 1024
STATUS_LINE = re.compile(rb"HTTP/\d\.\d (\d{3})")


@dataclass(frozen=True)
class ProbeResult:
    """What one probe saw.

    ok: whether it passed.
    latency_s: how long it took, when it passed.
    error: why it failed, on one line.
    data: the answer of an RPC.

    """

    ok: bool
    latency_s: float | None = None
    error: str = ""
    data: Any = None


def probe_host(address: str) -> str:
    """Return where to probe a listen address: the wildcard is loopback."""
    return "127.0.0.1" if address in ("", "0.0.0.0") else address


def _errno_of(error: OSError) -> int | None:
    """Return the POSIX errno that error stands for, if it has one.

    error: an error of a connection.

    return: its errno; for the error that asyncio makes of a host with
        several addresses that all failed (no errno, the texts of the
        failures joined), the errno that all the failures share; else
        None.

    """
    if isinstance(error, (socket.gaierror, socket.herror, ssl.SSLError)):
        # Their numbers belong to getaddrinfo and OpenSSL, not to errno.h.
        return None
    if error.errno:
        return error.errno
    text = str(error)
    if error.errno is None and text.startswith("Multiple exceptions: "):
        shared = set(re.findall(r"\[Errno (\d+)\]", text))
        if len(shared) == 1:
            return int(shared.pop())
    return None


def describe(error: BaseException) -> str:
    """Return an error as one short line.

    An OSError gets its errno's name first: asyncio's "Connect call
    failed" does not say "Connection refused" by itself. The errors of
    name resolution and of TLS keep their own text.

    """
    if isinstance(error, TimeoutError):
        return "timed out"
    text = " ".join(str(error).split())
    code = _errno_of(error) if isinstance(error, OSError) else None
    if code:
        reason = os.strerror(code)
        if reason not in text:
            text = "%s: %s" % (reason, text)
    return text[:200] or type(error).__name__


def _unverified_tls() -> ssl.SSLContext:
    """Return a TLS context for a probe that sends nothing secret.

    A ranking's probe only checks that it answers, and it sends no
    credentials: a self-signed certificate must not fail it.

    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


async def http_probe(host: str, port: int, path: str, timeout: float, *,
                     any_status: bool = False,
                     tls: bool = False) -> ProbeResult:
    """GET path over HTTP/1.0 and read the status line (3.4).

    host, port: where to connect.
    path: what to request.
    timeout: the bound of the whole probe, in seconds.
    any_status: whether any status line passes (a ranking), or only
        2xx and 3xx (the favicon).
    tls: whether to speak TLS (an https ranking).

    return: the result.

    """
    start = time.monotonic()
    writer = None
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(
                host, port, ssl=_unverified_tls() if tls else None)
            writer.write(("GET %s HTTP/1.0\r\nHost: %s\r\n"
                          "User-Agent: cmsWatchdog\r\n\r\n"
                          % (path, host)).encode("ascii"))
            await writer.drain()
            line = await reader.readline()
    except (OSError, TimeoutError, ValueError) as error:
        return ProbeResult(False, error=describe(error))
    finally:
        if writer is not None:
            writer.close()
    match = STATUS_LINE.match(line)
    if match is None:
        return ProbeResult(False, error="no HTTP status line")
    status = int(match.group(1))
    if any_status or 200 <= status < 400:
        return ProbeResult(True, time.monotonic() - start)
    return ProbeResult(False, error="HTTP status %d" % status)


@dataclass(frozen=True)
class RankingTarget:
    """Where a ranking listens, without its credentials."""

    host: str
    port: int
    tls: bool

    @property
    def visible(self) -> str:
        """Its address as alerts show it: no user name, no password."""
        host = "[%s]" % self.host if ":" in self.host else self.host
        return "%s://%s:%d/" % ("https" if self.tls else "http", host,
                                self.port)


def ranking_target(url: str) -> RankingTarget | None:
    """Return where the ranking at url listens.

    url: an entry of config.proxy_service.rankings.

    return: its target, or None if url is not an http(s) URL with a
        host and a valid port.

    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    tls = parts.scheme == "https"
    return RankingTarget(parts.hostname, port or (443 if tls else 80), tls)


class RemoteError(ValueError):
    """The service answered the call with an error."""


class _StaleConnection(Exception):
    """A connection was closed before any byte of the answer."""


PROBE_ERRORS = (OSError, TimeoutError, ValueError, EOFError,
                asyncio.LimitOverrunError)


class RpcProbe:
    """One persistent connection to a CMS service's RPC port (3.4).

    It sends one request at a time, in CMS's wire format (JSON lines
    ended by \\r\\n): concurrent calls wait for each other, each within
    its own timeout. A timeout or an error of the call that holds the
    connection closes it, so that a late answer is never read, and the
    next call connects again.
    A reused connection that the service closed (it restarted) is
    retried once at once, within the same timeout: only a failure of
    the fresh connection is a miss. So the service logs "Established
    connection" only at the first call, after its own restart and after
    a failed call.

    """

    def __init__(self, host: str, port: int, timeout: float):
        """Prepare the probe; it connects at its first call.

        host, port: the service's RPC address.
        timeout: the bound of each call, in seconds.

        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        """Whether a connection is open."""
        return self._writer is not None

    def close(self) -> None:
        """Close the connection, if one is open."""
        if self._writer is not None:
            self._writer.close()
        self._reader = None
        self._writer = None

    async def call(self, method: str,
                   data: dict | None = None) -> ProbeResult:
        """Call an RPC method and return its answer in data.

        method: the method's name.
        data: its keyword arguments.

        return: the result. The timeout bounds the wait for a call in
            progress too; a call that times out while it waits is a
            miss, and leaves the connection to the call in progress.

        """
        holds_connection = False
        try:
            async with asyncio.timeout(self.timeout):
                async with self._lock:
                    holds_connection = True
                    start = time.monotonic()
                    answer = await self._call(method, data or {})
        except asyncio.CancelledError:
            if holds_connection:
                self.close()
            raise
        except PROBE_ERRORS as error:
            if holds_connection:
                self.close()
            return ProbeResult(False, error=describe(error))
        return ProbeResult(True, time.monotonic() - start, data=answer)

    async def _call(self, method: str, data: dict) -> Any:
        reused = self._writer is not None
        if not reused:
            await self._connect()
        try:
            return await self._exchange(method, data)
        except _StaleConnection as error:
            self.close()
            if not reused:
                raise ConnectionError("the service closed the connection "
                                      "before answering") from error
        await self._connect()
        try:
            return await self._exchange(method, data)
        except _StaleConnection as error:
            raise ConnectionError("the service closed the connection "
                                  "before answering") from error

    async def _connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self.host, self.port, limit=MAX_MESSAGE_SIZE)

    async def _exchange(self, method: str, data: dict) -> Any:
        request_id = uuid.uuid4().hex
        request = {"__id": request_id, "__method": method, "__data": data}
        try:
            self._writer.write(json.dumps(request).encode("utf-8")
                               + b"\r\n")
            await self._writer.drain()
            line = await self._reader.readuntil(b"\r\n")
        except asyncio.IncompleteReadError as error:
            if not error.partial:
                raise _StaleConnection() from error
            raise ConnectionError("the answer was cut") from error
        except (ConnectionResetError, BrokenPipeError) as error:
            raise _StaleConnection() from error
        response = json.loads(line)
        # As CMS's own client: an answer has all three fields.
        if not isinstance(response, dict) \
                or not {"__id", "__data", "__error"} <= response.keys():
            raise ValueError("a bad answer")
        if response["__id"] != request_id:
            raise ValueError("an answer to another request")
        if response["__error"]:
            raise RemoteError(describe(Exception(response["__error"])))
        return response["__data"]


# The gate's query (3.4.1): one row per backend of this database but
# its own, with the ages in seconds; pg_blocking_pids only for lock
# waiters, because each call takes the lock manager's partition locks.
GATE_SQL = (
    "SELECT coalesce(json_agg(row_to_json(t)), '[]'::json) FROM ("
    "SELECT pid, application_name, client_port, state, wait_event_type, "
    "wait_event, "
    "extract(epoch FROM now() - backend_start)::float8 AS backend_age_s, "
    "extract(epoch FROM now() - xact_start)::float8 AS xact_age_s, "
    "extract(epoch FROM now() - query_start)::float8 AS query_age_s, "
    "extract(epoch FROM now() - state_change)::float8 AS state_age_s, "
    "CASE WHEN wait_event_type = 'Lock' THEN pg_blocking_pids(pid) END "
    "AS blocked_by, "
    "left(query, 300) AS query "
    "FROM pg_stat_activity "
    "WHERE datname = current_database() AND pid <> pg_backend_pid()) t")
# The columns of a row, as the fields of policy.Backend.
BACKEND_FIELDS = ("pid", "application_name", "client_port", "state",
                  "wait_event_type", "wait_event", "backend_age_s",
                  "xact_age_s", "query_age_s", "state_age_s", "blocked_by",
                  "query")
# The columns that are ages in seconds.
AGE_FIELDS = ("backend_age_s", "xact_age_s", "query_age_s", "state_age_s")
# How long to wait for a psql that was killed to be reaped, in seconds.
KILL_WAIT_S = 1.0


@dataclass(frozen=True)
class GateResult:
    """What the database gate saw (3.4.1).

    green: whether the query returned valid JSON.
    reason: for a red gate: "refused", "timeout", "too many clients",
        "authentication" or "other".
    detail: the first line of psql's error, for the alert.
    backends: for a green gate, one dict per backend, BACKEND_FIELDS as
        keys.

    """

    green: bool
    reason: str = ""
    detail: str = ""
    backends: tuple[dict, ...] = ()


# The grammar of a database URL, copied from SQLAlchemy 2.0
# (sqlalchemy/engine/url.py, _parse_url), which is what CMS opens the
# database with. urlsplit differs: it ends the password at the first
# "/", "?" or "#", which SQLAlchemy accepts unencoded in a password, and
# the gate would then connect somewhere else than CMS does.
DATABASE_URL = re.compile(
    r"""
        (?P<name>[\w\+]+)://
        (?:
            (?P<username>[^:/]*)
            (?::(?P<password>[^@]*))?
        @)?
        (?:
            (?:
                \[(?P<ipv6host>[^/\?]+)\] |
                (?P<ipv4host>[^/:\?]+)
            )?
            (?::(?P<port>[^/\?]*))?
        )?
        (?:/(?P<database>[^\?]*))?
        (?:\?(?P<query>.*))?
    """, re.X)


def psql_environment(url: str, path: str, connect_timeout_s: int,
                     statement_timeout_ms: int) -> dict[str, str]:
    """Return the environment of the gate's psql (3.4.1).

    The password goes in the environment, never in argv. The URL is read
    as SQLAlchemy reads it (DATABASE_URL): the user and the password are
    percent-decoded, the host and the database are not. Of the URL's
    query only sslmode and host (a socket directory) are read: the gate
    sets its own timeouts. LC_ALL=C makes psql's and libpq's own
    messages English, which classify() reads; the server's messages
    (too many clients, authentication) follow its lc_messages.

    url: config.database.url, with or without its +driver suffix.
    path: the PATH in which to find psql.
    connect_timeout_s: PGCONNECT_TIMEOUT.
    statement_timeout_ms: the statement_timeout, through PGOPTIONS.

    return: the whole environment of the subprocess.

    """
    env = {"PATH": path, "LC_ALL": "C", "PGAPPNAME": "cmsWatchdog",
           "PGCONNECT_TIMEOUT": str(connect_timeout_s),
           "PGOPTIONS": "-c statement_timeout=%d" % statement_timeout_ms}
    match = DATABASE_URL.match(url)
    if match is None:
        # CMS itself would not start; leave libpq its defaults.
        return env
    parts = match.groupdict()
    host = parts["ipv4host"] or parts["ipv6host"]
    if host:
        env["PGHOST"] = host
    try:
        port = int(parts["port"] or 0)
    except ValueError:
        port = 0
    if port:
        env["PGPORT"] = str(port)
    if parts["username"]:
        env["PGUSER"] = unquote(parts["username"])
    if parts["password"]:
        env["PGPASSWORD"] = unquote(parts["password"])
    if parts["database"]:
        env["PGDATABASE"] = parts["database"]
    query = parse_qs(parts["query"] or "")
    if "sslmode" in query:
        env["PGSSLMODE"] = query["sslmode"][-1]
    if "host" in query:
        env["PGHOST"] = ",".join(query["host"])
    return env


def classify(stderr: str) -> str:
    """Return why psql failed, from its error text (in the C locale)."""
    text = stderr.lower()
    if "too many clients" in text or "remaining connection slots" in text:
        return "too many clients"
    if "authentication failed" in text or "no pg_hba.conf entry" in text \
            or "no password supplied" in text:
        return "authentication"
    if "timeout expired" in text or "statement timeout" in text:
        return "timeout"
    if "connection refused" in text:
        return "refused"
    return "other"


def _finite(value: object) -> bool:
    """Tell whether value is a finite int or float (not a bool)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _is_pid_list(value: object) -> bool:
    """Tell whether value is a list of ints (not bools)."""
    return isinstance(value, list) and all(
        isinstance(pid, int) and not isinstance(pid, bool) for pid in value)


# One token of an SQL text, as far as PostgreSQL's lexer (scan.l) tells
# a literal from the rest: a literal (an escape string, a string with
# no backslash, a dollar-quoted string), a quoted identifier, a word
# (which may hold a "$", so an E that ends one starts no escape
# string), a number (which holds no "$": "1e3$$" starts a literal), a
# parameter, a comment that nests none, or anything else. A string
# with a backslash is no token: how it reads depends on the session's
# standard_conforming_strings.
SQL_TOKEN = re.compile(r"""
      (?P<literal>
          [Ee]'(?:[^'\\]|\\.|'')*'
        | '(?:[^'\\]|'')*'
        | \$(?P<tag>(?:[A-Za-z_\x80-\U0010ffff][A-Za-z0-9_\x80-\U0010ffff]*)?)
          \$.*?\$(?P=tag)\$
      )
    | "(?:[^"]|"")*"
    | [A-Za-z_\x80-\U0010ffff][A-Za-z0-9_$\x80-\U0010ffff]*
    | [0-9][A-Za-z0-9_.\x80-\U0010ffff]*
    | \$[0-9]+
    | --[^\n]*
    | /\*(?:[^*/]|\*(?!/)|/(?!\*))*\*/
    | [^'"$A-Za-z0-9_\x80-\U0010ffff/-]+
    | /(?!\*)
    | -(?!-)
""", re.X | re.S)


def redact_literals(query: str) -> str:
    """Replace each literal of an SQL text with '…' (3.8).

    A backend's statement can hold a secret: psycopg2 writes the
    parameters into the statement it sends, an idle backend keeps its
    last one, and the AWS can store a password in plaintext. The
    statement keeps its shape, quoted identifiers included. A text that
    does not read with certainty (a literal cut at 300 characters, a
    backslash in a string, a nested comment) is cut at its first quote
    or dollar sign instead, where the first literal could start.

    query: the statement of a backend, as pg_stat_activity shows it.

    return: the statement with no literal.

    """
    parts = []
    position = 0
    while position < len(query):
        match = SQL_TOKEN.match(query, position)
        if match is None:
            first = re.search(r"['$]", query)
            return query if first is None else query[:first.start()] + "…"
        parts.append("'…'" if match.group("literal") is not None
                     else match.group())
        position = match.end()
    return "".join(parts)


def parse_backends(text: str) -> tuple[dict, ...]:
    """Read the gate's answer.

    A row with an age that is neither a finite number nor null (NaN,
    an infinity, text: json.loads takes them) is dropped: the policy
    formats the ages as whole seconds. So is a row whose blocked_by is
    neither null nor a list of pids. The literals of each query are
    replaced (redact_literals): the snapshot goes to the evidence
    files.

    text: psql's output, a JSON array of rows.

    return: one dict per row, BACKEND_FIELDS as keys (missing ones
        None), blocked_by a tuple of pids.

    raise (ValueError): if text is not such an array.

    """
    rows = json.loads(text)
    if not isinstance(rows, list):
        raise ValueError("not a JSON array")
    backends = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("pid"), int):
            raise ValueError("a row without a pid")
        if any(row.get(name) is not None and not _finite(row.get(name))
               for name in AGE_FIELDS):
            continue
        blocked_by = row.get("blocked_by")
        if blocked_by is not None and not _is_pid_list(blocked_by):
            continue
        backend = {name: row.get(name) for name in BACKEND_FIELDS}
        backend["blocked_by"] = tuple(blocked_by or ())
        if isinstance(backend["query"], str):
            backend["query"] = redact_literals(backend["query"])
        backends.append(backend)
    return tuple(backends)


def _kill(process: Any) -> None:
    """Kill a psql, unless it has just exited and is gone."""
    try:
        process.kill()
    except ProcessLookupError:
        pass


async def db_gate(env: dict[str, str], kill_s: float,
                  run: Callable[..., Awaitable[Any]] =
                  asyncio.create_subprocess_exec) -> GateResult:
    """Run the gate's query with psql, and read the snapshot (3.4.1).

    env: the environment from psql_environment.
    kill_s: when to kill a psql that has not finished.
    run: asyncio.create_subprocess_exec (tests pass a fake).

    return: the result; a green gate carries the snapshot.

    """
    try:
        process = await run(
            "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-w", "-c",
            GATE_SQL, stdin=DEVNULL, stdout=PIPE, stderr=PIPE, env=env)
    except OSError as error:
        return GateResult(False, "other",
                          "cannot run psql: %s" % describe(error))
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(),
                                                kill_s)
    except TimeoutError:
        _kill(process)
        try:
            await asyncio.wait_for(process.wait(), KILL_WAIT_S)
        except TimeoutError:
            pass
        return GateResult(False, "timeout",
                          "psql did not finish in %g s" % kill_s)
    except asyncio.CancelledError:
        _kill(process)
        raise
    if process.returncode != 0:
        text = stderr.decode("utf-8", "replace")
        lines = [line.strip() for line in text.splitlines()
                 if line.strip()]
        return GateResult(False, classify(text), lines[0] if lines
                          else "psql exited with %d" % process.returncode)
    try:
        backends = parse_backends(stdout.decode("utf-8", "replace"))
    except Exception as error:
        # ValueError for a bad snapshot, RecursionError for a too deep one.
        return GateResult(False, "other", "the snapshot did not parse: %s"
                          % describe(error))
    return GateResult(True, backends=backends)


def proc_facts(pid: int, proc: str = "/proc") -> dict | None:
    """Return the /proc facts of a process, for the evidence (3.4).

    pid: the process.
    proc: where /proc is (tests pass a fake tree).

    return: {"state", "wchan", "threads": {tid: state}, "cpu_s"
        (utime+stime in seconds)}, or None if the process is gone.

    """
    try:
        fields = stat_fields("%s/%d/stat" % (proc, pid))
        cpu_s = (int(fields[11]) + int(fields[12])) / CLOCK_TICKS
    except (OSError, ValueError, IndexError):
        return None
    try:
        with open("%s/%d/wchan" % (proc, pid)) as f:
            wchan = f.read().strip()
    except OSError:
        wchan = ""
    threads = {}
    try:
        tids = os.listdir("%s/%d/task" % (proc, pid))
    except OSError:
        tids = []
    for tid in tids:
        try:
            threads[int(tid)] = stat_fields(
                "%s/%d/task/%s/stat" % (proc, pid, tid))[0]
        except (OSError, ValueError, IndexError):
            continue
    return {"state": fields[0], "wchan": wchan, "threads": threads,
            "cpu_s": cpu_s}


def thread_cpu_s(pid: int, tid: int, proc: str = "/proc") -> float | None:
    """Return a thread's utime+stime in seconds, or None if gone (R3)."""
    try:
        fields = stat_fields("%s/%d/task/%d/stat" % (proc, pid, tid))
        return (int(fields[11]) + int(fields[12])) / CLOCK_TICKS
    except (OSError, ValueError, IndexError):
        return None
