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

"""Visibility of RWS group rankings: hidden scoreboards and a
password-protected live view for the staff (MC-2).

"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import time

import gevent
from gevent.pywsgi import WSGIHandler
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.wrappers import Request, Response

from cmscommon.crypto import parse_authentication, validate_password


logger = logging.getLogger(__name__)


VISIBILITY_FILE = "visibility.json"
STAFF_COOKIE = "rws_staff"

NO_STORE = {"Cache-Control": "no-store"}

NOTICE_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ranking oculto</title>
<style>
body {{ font-family: sans-serif; background: #f4f4f4; color: #222;
       display: flex; min-height: 100vh; margin: 0;
       align-items: center; justify-content: center; }}
main {{ background: #fff; padding: 2em; border-radius: 8px;
       max-width: 26em; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15); }}
.error {{ color: #b00020; }}
</style>
</head>
<body>
<main>
<h1>{group}</h1>
<p>Este ranking está oculto por ahora.</p>
<form method="post" action="staff-login">
<label>Contraseña del staff
<input type="password" name="password"
       autocomplete="current-password" required>
</label>
<button type="submit">Entrar</button>
</form>
{error}
</main>
</body>
</html>
"""

STAFF_BANNER = (
    '<div style="background:#b00020;color:#fff;padding:0.5em;'
    'text-align:center;font-family:sans-serif;">Vista staff: este '
    'ranking está oculto al público &middot; '
    '<a style="color:#fff" href="staff-logout">Salir</a></div>'
).encode("utf-8")
BODY_TAG = re.compile(rb"<body[^>]*>", re.IGNORECASE)


def staff_cookie_value(secret: str, group: str, staff_password: str) -> str:
    """Return the value of a valid staff cookie for a group.

    secret: the group's secret, hex-encoded.
    group: the group name.
    staff_password: the stored authentication string.

    return: the hex-encoded HMAC-SHA256.

    """
    message = ("%s\n%s" % (group, staff_password)).encode("utf-8")
    return hmac.new(bytes.fromhex(secret), message,
                    hashlib.sha256).hexdigest()


class VisibilityState:
    """The visibility settings of one group, stored in its directory.

    """

    def __init__(self, group_dir: str):
        self.path = os.path.join(group_dir, VISIBILITY_FILE)
        self.hidden = False
        self.staff_password: str | None = None
        self.secret = secrets.token_hex(32)
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            hidden = data["hidden"]
            staff_password = data["staff_password"]
            secret = data["secret"]
            if not isinstance(hidden, bool) \
                    or not isinstance(secret, str) or secret == "" \
                    or not (staff_password is None
                            or isinstance(staff_password, str)):
                raise ValueError("Wrong types.")
            bytes.fromhex(secret)
        except (OSError, ValueError, KeyError, TypeError):
            logger.error("Cannot read %s: hiding the ranking until its "
                         "visibility is sent again.", self.path,
                         exc_info=True)
            self.hidden = True
            self.staff_password = None
            return
        self.hidden = hidden
        self.staff_password = staff_password
        self.secret = secret

    def update(self, hidden: bool, staff_password: str | None):
        """Replace the settings, storing them atomically first.

        hidden: whether the public scoreboard is hidden.
        staff_password: the staff authentication string, or None.

        """
        data = {"hidden": hidden, "staff_password": staff_password,
                "secret": self.secret}
        directory = os.path.dirname(self.path)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".vis-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp_path, self.path)
        except BaseException:
            os.unlink(tmp_path)
            raise
        self.hidden = hidden
        self.staff_password = staff_password


class _CutWhenHidden:
    """Wrap a response body so it stops once the group gets hidden."""

    def __init__(self, iterable, state: VisibilityState):
        self._iterable = iterable
        self._state = state

    def __iter__(self):
        for chunk in self._iterable:
            if self._state.hidden:
                return
            yield chunk

    def close(self):
        close = getattr(self._iterable, "close", None)
        if close is not None:
            close()


class VisibilityGuard:
    """WSGI middleware enforcing the visibility of one group namespace.

    """

    FAILED_LOGIN_DELAY = 1.0

    def __init__(self, app, group_dir: str, group: str, username: str,
                 password: str, realm_name: str):
        self.app = app
        self.group = group
        self.username = username
        self.password = password
        self.realm_name = realm_name
        self.state = VisibilityState(group_dir)

    def _writer_authorized(self, request: Request) -> bool:
        return request.authorization is not None and \
            request.authorization.type == "basic" and \
            request.authorization.username == self.username and \
            request.authorization.password == self.password

    def _is_staff(self, request: Request) -> bool:
        """Tell whether the request carries a valid staff cookie.

        request: the request being served.

        return: True if the cookie was issued for the current staff
            password of this group.

        """
        stored = self.state.staff_password
        cookie = request.cookies.get(STAFF_COOKIE)
        if stored is None or cookie is None:
            return False
        expected = staff_cookie_value(self.state.secret, self.group, stored)
        # The client chooses the cookie, and compare_digest refuses a str
        # that is not ASCII: compare bytes.
        return hmac.compare_digest(cookie.encode("utf-8"),
                                   expected.encode("ascii"))

    @staticmethod
    def _is_https(request: Request) -> bool:
        """Tell whether the client reached RWS over HTTPS.

        request: the request being served.

        return: True if the WSGI scheme or the X-Forwarded-Proto header
            of the reverse proxy that ends TLS says so.

        """
        return request.scheme == "https" or request.headers.get(
            "X-Forwarded-Proto", "").lower() == "https"

    def _login(self, request: Request) -> Response:
        """Check the staff password and start a staff session.

        request: the POST to staff-login, with the form field password.

        return: a redirect that sets the staff cookie, or the notice with
            an error after a failed attempt (or a 413 if the form is too
            big to be a password).

        """
        try:
            # As typed, without stripping: AWS stores it as it got it.
            password = request.form.get("password", "")
        except RequestEntityTooLarge as error:
            return error.get_response(request.environ)
        stored = self.state.staff_password
        valid = False
        if stored is not None and password != "":
            try:
                # bcrypt takes about 250 ms of CPU. On the hub it would
                # stop every public scoreboard during a burst of attempts,
                # so it runs in the threadpool.
                valid = gevent.get_hub().threadpool.apply(
                    validate_password, (stored, password))
            except ValueError:
                # An authentication method that is not known.
                valid = False
        if not valid:
            # cmsRankingWebServer monkey-patches time: only this greenlet
            # waits.
            time.sleep(self.FAILED_LOGIN_DELAY)
            return self._notice(error=True, status=401)
        response = Response(status=303, headers=dict(
            NO_STORE, Location="./"))
        # Sign the hash that was checked, not the current one: if the
        # password changed meanwhile, the cookie is worth nothing.
        response.set_cookie(
            STAFF_COOKIE,
            staff_cookie_value(self.state.secret, self.group, stored),
            path=None, httponly=True, samesite="Lax",
            secure=self._is_https(request))
        return response

    def _logout(self) -> Response:
        """End the staff session of the client.

        return: a redirect that clears the staff cookie.

        """
        response = Response(status=303, headers=dict(
            NO_STORE, Location="./"))
        response.delete_cookie(STAFF_COOKIE, path=None)
        return response

    def _with_banner(self, environ, start_response):
        """Serve the app's page with the staff banner after <body>.

        environ: the WSGI environ.
        start_response: the WSGI start_response callable.

        return: the body of the response.

        """
        captured = {}

        def capture(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = headers
            return lambda data: None

        body_iter = self.app(environ, capture)
        try:
            body = b"".join(body_iter)
        finally:
            close = getattr(body_iter, "close", None)
            if close is not None:
                close()
        body = BODY_TAG.sub(lambda m: m.group(0) + STAFF_BANNER, body,
                            count=1)
        dropped = {"content-length", "last-modified", "etag",
                   "cache-control"}
        headers = [(k, v) for k, v in captured["headers"]
                   if k.lower() not in dropped]
        headers += [("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store")]
        start_response(captured["status"], headers)
        return [body]

    def _guard_writes(self, request: Request, start_response):
        """Wrap start_response so write() stops once the group is hidden.

        The /events handler sends its data through the write() callable
        instead of the returned iterable, so _CutWhenHidden never sees
        it. Its error handling ends the stream when write() raises, and
        closing the connection makes the browser reconnect and get the
        403.

        request: the request being served.
        start_response: the WSGI start_response callable.

        return: a start_response whose write() callables refuse to send
            data to a client that may no longer see the ranking.

        """
        handler = getattr(start_response, "__self__", None)

        def guarded_start_response(status, headers, exc_info=None):
            write = start_response(status, headers, exc_info)

            def guarded_write(data):
                if self.state.hidden and not self._is_staff(request):
                    if isinstance(handler, WSGIHandler):
                        handler.close_connection = True
                    raise ConnectionAbortedError("The ranking is hidden.")
                return write(data)

            return guarded_write

        if handler is not None:
            # The /events handler looks for the gevent handler here.
            guarded_start_response.__self__ = handler
        return guarded_start_response

    def __call__(self, environ, start_response):
        request = Request(environ)
        path = request.path
        start_response = self._guard_writes(request, start_response)
        if path == "/visibility":
            return self._update(request)(environ, start_response)
        if path == "/staff-logout" and request.method == "GET":
            return self._logout()(environ, start_response)
        if self._is_staff(request):
            if self.state.hidden and path == "/" and \
                    request.method == "GET":
                return self._with_banner(environ, start_response)
            return self.app(environ, start_response)
        if not self.state.hidden:
            return _CutWhenHidden(self.app(environ, start_response),
                                  self.state)
        if request.method in ("PUT", "DELETE"):
            # The store handlers check the proxy's credentials.
            return self.app(environ, start_response)
        if path == "/" and request.method in ("GET", "HEAD"):
            return self._notice()(environ, start_response)
        if path == "/staff-login" and request.method == "POST":
            return self._login(request)(environ, start_response)
        return Response("Este ranking está oculto.", status=403,
                        mimetype="text/plain",
                        headers=NO_STORE)(environ, start_response)

    def _notice(self, error: bool = False, status: int = 200) -> Response:
        body = NOTICE_TEMPLATE.format(
            group=self._escaped_group(),
            error='<p class="error">Contraseña incorrecta.</p>'
                  if error else "")
        return Response(body, status=status, mimetype="text/html",
                        headers=NO_STORE)

    def _escaped_group(self) -> str:
        # Group names are validated to [a-z0-9_-] by RWS and AWS.
        return re.sub(r"[^a-z0-9_-]", "", self.group)

    def _update(self, request: Request) -> Response:
        if request.method != "PUT":
            return Response(status=405, headers={"Allow": "PUT"})
        if not self._writer_authorized(request):
            logger.warning("Unauthorized visibility update.",
                           extra={"location": request.url})
            return Response(
                "Unauthorized", status=401, headers={
                    "WWW-Authenticate":
                        'Basic realm="%s"' % self.realm_name})
        try:
            data = json.loads(request.get_data(as_text=True))
            hidden = data["hidden"]
            staff_password = data["staff_password"]
            if not isinstance(hidden, bool):
                raise ValueError("hidden must be a boolean.")
            if staff_password is not None:
                if not isinstance(staff_password, str):
                    raise ValueError("staff_password must be a string.")
                parse_authentication(staff_password)
        except (ValueError, KeyError, TypeError) as error:
            logger.warning("Bad visibility update: %s.", error)
            return Response(str(error), status=400, mimetype="text/plain")
        self.state.update(hidden, staff_password)
        logger.info("Ranking group %s is now %s.", self.group,
                    "hidden" if hidden else "visible")
        return Response(status=204)
