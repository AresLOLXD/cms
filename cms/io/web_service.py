#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2015 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2013-2016 Luca Wehrstedt <luca.wehrstedt@gmail.com>
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

import asyncio
import hashlib
import logging
import importlib.resources

import tornado.httpserver
import tornado.netutil
import tornado.web

from cms.db.filecacher import FileCacher
from cms.server.util import Url
from .async_service import AsyncService


logger = logging.getLogger(__name__)


SECONDS_IN_A_YEAR = 365 * 24 * 60 * 60


def resolve_remote_ip(
    forwarded_for_header: str | None,
    socket_remote_ip: str,
    num_proxies_used: int,
) -> str:
    """Resolve the real client IP behind zero or more trusted proxies.

    Replicates werkzeug.middleware.proxy_fix.ProxyFix's semantics
    exactly (the mechanism this project used before this migration):
    take the num_proxies_used-th address from the *right* of a
    comma-separated X-Forwarded-For header, ignoring any X-Real-Ip
    header entirely (unlike Tornado's own xheaders handling, which
    this project deliberately does not use -- see this function's
    caller for why).

    forwarded_for_header: the raw X-Forwarded-For header value, or
        None if absent.
    socket_remote_ip: the actual TCP peer address (what
        request.remote_ip is when xheaders is disabled) -- used
        as-is when num_proxies_used is 0, or as a fallback if the
        header doesn't have enough entries.
    num_proxies_used: how many trusted proxies sit in front of this
        service; 0 means "trust the raw TCP peer address only."

    return: the resolved client IP.

    """
    if num_proxies_used <= 0 or not forwarded_for_header:
        return socket_remote_ip
    addresses = [addr.strip() for addr in forwarded_for_header.split(",")]
    if len(addresses) < num_proxies_used:
        return socket_remote_ip
    return addresses[-num_proxies_used]


class StaticFileHasher:
    """
    Constructs URLs to static files. The result of make() is similar to the
    url() function that's used in the templates, in that it constructs a
    relative URL, but it also adds a "?h=12345678" query parameter which forces
    browsers to reload the resource when it has changed.
    """
    def __init__(self, files: list[tuple[str, str]]):
        """
        Initialize.

        files: list of static file locations, each in the format that would be
            passed to SharedDataMiddleware.
        """
        # Cache of the hashes of files, to prevent re-hashing them on every request.
        self.cache: dict[tuple[str, ...], str] = {}
        # We reverse the order, because in WSGI later-added middlewares
        # override earlier ones, but here we iterate the locations and use the
        # first found match.
        self.static_locations = files[::-1]

    def make(self, base_url: Url):
        """
        Create a new url helper function (called once per request).

        The returned function takes arguments in the same format as `Url`, and
        returns a string in the same format as `Url` except with a hash
        appended as a query string.
        """
        def inner_func(*paths: str):
            # WebService always serves the static files under /static.
            assert paths[0] == "static"

            url_path_part = base_url(*paths)

            if paths in self.cache:
                return url_path_part + self.cache[paths]

            for module_name, dir in self.static_locations:
                resource = importlib.resources.files(module_name).joinpath(dir, *paths[1:])
                if resource.is_file():
                    with resource.open('rb') as file:
                        hash = hashlib.file_digest(file, hashlib.sha256).hexdigest()
                    result = "?h=" + hash[:24]
                    break
            else:
                logger.warning(f"Did not find path passed to static_url(): {paths}")
                result = ""

            self.cache[paths] = result
            return url_path_part + result
        return inner_func

class WebService(AsyncService):
    """RPC service with Web server capabilities.

    """

    def __init__(
        self,
        listen_port: int,
        handlers: list,
        parameters: dict,
        shard: int = 0,
        listen_address: str = "",
    ):
        super().__init__(shard)

        static_files = parameters.pop('static_files', [])
        parameters.pop('rpc_enabled', False)
        parameters.pop('rpc_auth', None)
        auth_middleware = parameters.pop('auth_middleware', None)
        self.auth_handler = auth_middleware() if auth_middleware is not None else None
        num_proxies_used = parameters.pop('num_proxies_used', None) or 0

        self.application = tornado.web.Application(handlers, **parameters)
        self.application.service = self

        self.static_file_hasher = StaticFileHasher(static_files)

        self.file_cacher = FileCacher(self)

        # Named distinctly from AsyncService's own self._listen_address
        # (the RPC server's Address namedtuple, set in super().__init__
        # and used by AsyncService._async_run) to avoid clobbering it:
        # these are the plain host/port of the HTTP server instead.
        self._http_listen_port = listen_port
        self._http_listen_address = listen_address
        # Never trust Tornado's own xheaders handling: it lets
        # X-Real-Ip take priority in some cases and doesn't implement
        # "skip N hops from the right" the way the old ProxyFix did,
        # which would let a contestant spoof request.remote_ip and
        # bypass the IP-lock feature. request.remote_ip is therefore
        # always the raw TCP peer address here; resolve_remote_ip()
        # below replicates ProxyFix's semantics explicitly. (Task 7's
        # CommonRequestHandler.prepare() is responsible for calling
        # resolve_remote_ip() with self.num_proxies_used and assigning
        # the result to self.request.remote_ip, before any handler
        # body or the auth hook runs.)
        self.num_proxies_used = num_proxies_used
        self._http_server = tornado.httpserver.HTTPServer(self.application)
        self._http_server_sockets: list = []

    def run(self) -> bool:
        """Start the WebService.

        Both the HTTP server and the RPC server are started, on the
        same event loop.

        """
        return asyncio.run(self._async_run())

    async def _async_run(self) -> bool:
        self._http_server_sockets = tornado.netutil.bind_sockets(
            self._http_listen_port, address=self._http_listen_address or None)
        self._http_server.add_sockets(self._http_server_sockets)
        try:
            return await super()._async_run()
        finally:
            # Stop accepting new connections, then give genuinely
            # in-flight requests a bounded grace period to finish on
            # their own before force-closing whatever's left. This
            # matters because asyncio.run() (how WebService.run() is
            # actually invoked in production) tears down the event
            # loop the moment this coroutine returns: any request
            # still awaiting something at that point would otherwise
            # have its task cancelled with no response sent, and any
            # idle keep-alive connection would leak its socket open
            # until process exit.
            self._http_server.stop()
            try:
                await asyncio.wait_for(
                    self._wait_for_requests_to_drain(), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "Some requests didn't finish within the shutdown "
                    "grace period; closing their connections.")
            await self._http_server.close_all_connections()

    async def _wait_for_requests_to_drain(self) -> None:
        """Poll until no HTTP connection is being served.

        Used by _async_run's shutdown to wait for in-flight requests
        to finish before force-closing anything left, mirroring the
        old gevent WSGIServer.stop(timeout=1)'s behavior this
        replaces.

        """
        while self._http_server._connections:
            await asyncio.sleep(0.02)
