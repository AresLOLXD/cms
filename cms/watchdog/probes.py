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
a result. This module imports only the standard library.

"""

import asyncio
import json
import os
import re
import socket
import ssl
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


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
