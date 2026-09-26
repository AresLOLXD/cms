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
        parameters.pop('auth_middleware', None)
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
        self._http_server = tornado.httpserver.HTTPServer(
            self.application, xheaders=num_proxies_used > 0)
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
            # Only stop accepting new connections: HTTPServer's
            # close_all_connections() force-closes the underlying
            # stream of every live connection (Tornado's
            # HTTP1ServerConnection.close() calls stream.close()
            # before waiting for its serving loop to finish), which
            # would cut off a response that is still being written.
            # Existing connections are left to finish and close on
            # their own (request completion or keep-alive timeout).
            self._http_server.stop()
