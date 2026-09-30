#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""A loopback RPC peer that stays connected but never answers.

It models a service whose process is hung (deadlocked, stopped, swapped
out) while the kernel keeps its TCP connection open: the caller's write
succeeds and no answer ever comes back. Tests use it to check that a
caller doesn't let its calls pile up against such a peer.

"""

import asyncio
import json
from unittest.mock import patch

from cms.conf import Address, ServiceCoord
from cms.io.async_rpc import AsyncRemoteServiceClient


class StuckPeer:
    """A loopback server that receives RPC requests and doesn't answer.

    Every request is recorded in `requests`. Nothing is answered until
    release() is called, which answers the requests received so far
    (and any later one) with an empty successful result.

    """

    def __init__(self):
        self.requests: list[dict] = []
        self.port: int | None = None
        self._server: asyncio.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []
        self._unanswered: list[tuple[asyncio.StreamWriter, dict]] = []
        self._released = False

    async def start(self):
        """Start listening on a free loopback port (see `port`)."""
        self._server = await asyncio.start_server(
            self._handle_connection, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self):
        """Close the listening socket and every connection."""
        self._server.close()
        for writer in self._writers:
            writer.close()
        await self._server.wait_closed()

    async def wait_for_requests(self, count: int, timeout: float = 2.0):
        """Wait until at least `count` requests have been received.

        count: how many requests to wait for.
        timeout: how many seconds to wait at most.

        raise (TimeoutError): if they didn't arrive in time.

        """
        async with asyncio.timeout(timeout):
            while len(self.requests) < count:
                await asyncio.sleep(0.005)

    def release(self):
        """Answer every request, the ones received so far included."""
        self._released = True
        for writer, request in self._unanswered:
            self._answer(writer, request)
        self._unanswered.clear()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        self._writers.append(writer)
        try:
            while line := await reader.readline():
                request = json.loads(line)
                self.requests.append(request)
                if self._released:
                    self._answer(writer, request)
                else:
                    self._unanswered.append((writer, request))
        finally:
            writer.close()

    @staticmethod
    def _answer(writer: asyncio.StreamWriter, request: dict):
        response = {"__id": request["__id"], "__data": None, "__error": None}
        if not writer.is_closing():
            writer.write(json.dumps(response).encode("utf-8") + b"\r\n")


async def connect_client(
    coord: ServiceCoord, peer: StuckPeer
) -> AsyncRemoteServiceClient:
    """Return a client of the given coord, connected to the peer.

    It connects through the client's own connect loop, so it behaves as
    the clients of an AsyncService do. The caller must disconnect it.

    coord: the coord the client is created for.
    peer: the (started) peer to connect to.

    return: the connected client.

    """
    with patch("cms.io.async_rpc.get_service_address",
               return_value=Address("127.0.0.1", peer.port)):
        client = AsyncRemoteServiceClient(coord)
    client.connect()
    if not await client.wait_for_connection(timeout=2):
        raise RuntimeError("Could not connect to the stuck peer.")
    return client
