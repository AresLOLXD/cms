#!/usr/bin/env python3

"""An HTTP interface to the internal RPC communications, native-Tornado
replacement for the old WSGI-based RPCMiddleware.

"""

import asyncio
import json
import logging
from collections.abc import Callable

import tornado.web

from cms import ServiceCoord
from cms.io.rpc import RPCError


logger = logging.getLogger(__name__)


RPC_TIMEOUT_SECONDS = 60.0


class RPCHandler(tornado.web.RequestHandler):
    """An HTTP-to-RPC proxy for the service this application runs for.

    This provides a synchronous-looking (from the client's point of
    view) and unfiltered access, over an HTTP transport, to all remote
    services and all their RPC methods. Each of them can be called by
    making a POST request to the URL "/<prefix>/<service>/<shard>/
    <method>" where "<service>" is the name of the remote service
    (i.e. the name of the class), "<shard>" is the shard of the
    instance and "<method>" is the name of the method.

    POST has been used because it's neither a safe nor an idempotent
    method (see HTTP spec.) and is therefore less restricted in what
    clients expect it to do than a GET.

    Arguments for the RPC should be given as a JSON-encoded object in
    the request body (should always be present, even if empty).

    A standard error code will be returned for all client-to-server
    errors (mostly communication errors: the client didn't declare it
    produces and consumes JSON or the JSON was invalid). A 404 will be
    returned if the requested remote service isn't found in the list
    of remote services that the service we're proxying for knows
    about. A 403 will be returned if the request is rejected by the
    optional authentication callback. A 503 status code means that, at
    the moment, there's no connection to the remote service.

    As soon as the RPC completes (or times out), the HTTP request is
    considered successful (i.e. status code 200). The response body
    will contain a JSON object with two fields: data and error
    (possibly null). The first contains the JSON-encoded result of the
    RPC, the second a string describing the error that occurred (if
    any).

    """

    def initialize(
        self, rpc_auth: Callable[[str, int, str], bool] | None = None
    ):
        self._rpc_auth = rpc_auth

    def write_error(self, status_code: int, **kwargs) -> None:
        # Keep the same {"data": ..., "error": ...} envelope on the
        # client-to-server error paths (404/403/415/406/400) as on
        # successful and RPC-failed calls, instead of Tornado's default
        # HTML error page.
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps({"data": None, "error": self._reason}))

    async def post(self, service_name: str, shard: str, method: str):
        coord = ServiceCoord(service_name, int(shard))

        if coord not in self.application.service.remote_services:
            raise tornado.web.HTTPError(404)

        if self._rpc_auth is not None and not self._rpc_auth(
                service_name, int(shard), method):
            raise tornado.web.HTTPError(403)

        content_type = self.request.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            raise tornado.web.HTTPError(415)

        accept = self.request.headers.get("Accept", "*/*")
        if "application/json" not in accept and "*/*" not in accept:
            raise tornado.web.HTTPError(406)

        try:
            data = json.loads(self.request.body)
        except ValueError:
            raise tornado.web.HTTPError(400)

        remote_service = self.application.service.remote_services[coord]
        if not remote_service.connected:
            raise tornado.web.HTTPError(503)

        try:
            result = await asyncio.wait_for(
                remote_service.execute_rpc(method, data),
                timeout=RPC_TIMEOUT_SECONDS)
            error = None
        except asyncio.TimeoutError:
            result = None
            error = "Timed out waiting for a reply."
        except RPCError as rpc_error:
            result = None
            error = str(rpc_error)

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"data": result, "error": error}))

    @classmethod
    def make_route(
        cls,
        url_pattern: str,
        rpc_auth: Callable[[str, int, str], bool] | None,
    ) -> tuple[str, type, dict]:
        """Build a Tornado route entry for this handler.

        url_pattern: the URL regex, with three capture groups for
            service name, shard, and method (e.g.
            r"/rpc/([^/]+)/([0-9]+)/([^/]+)").
        rpc_auth: a function taking (service_name, shard, method) and
            returning whether the request is allowed, or None to allow
            all requests.

        return: a (pattern, handler_class, kwargs) tuple usable
            directly in a tornado.web.Application's handlers list.

        """
        return (url_pattern, cls, {"rpc_auth": rpc_auth})
