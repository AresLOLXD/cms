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

import functools
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile

import gevent
from gevent.pywsgi import WSGIHandler
from gevent.threadpool import ThreadPool
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.wrappers import Request, Response

from cmscommon.crypto import parse_authentication, validate_password


logger = logging.getLogger(__name__)


VISIBILITY_FILE = "visibility.json"
STAFF_COOKIE = "rws_staff"

NO_STORE = {"Cache-Control": "no-store"}
PRIVATE_NO_STORE = "private, no-store"
REVALIDATE = "no-cache"
# The index page, and the same file as the static files middleware serves.
INDEX_PATHS = ("/", "/Ranking.html")

# A login form is a few hundred bytes at most.
MAX_LOGIN_BODY = 4096
# Threads that check staff passwords, apart from the pool that the hub
# shares with everything else.
LOGIN_THREADS = 2

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

# A bar of its own at the bottom, above everything (the scoreboard goes up
# to a z-index of 500): the upper panel of the scoreboard is positioned at
# the top of the page and would cover a banner that is in the flow there.
# The scoreboard scrolls in areas that Ranking.css anchors to the bottom of
# the page, so they would sit under the bar: they stop above it instead
# (30px is what Ranking.css gives the side panel, whose "Powered by" line
# is under it). The height of the bar and that room are one property:
# 2.25rem is a line of 1.25rem and a padding of 0.5rem above and below,
# and 3.5rem holds the two lines that the text takes on a narrow screen.
STAFF_BANNER = (
    '<style>'
    ':root{--rws-banner:2.25rem}'
    '@media(max-width:30em){:root{--rws-banner:3.5rem}}'
    '#InnerFrame,#UserDetail_bg{bottom:var(--rws-banner)}'
    '#SidePanel{bottom:calc(30px + var(--rws-banner))}'
    '</style>'
    '<div style="position:fixed;bottom:0;left:0;right:0;z-index:1000;'
    'box-sizing:border-box;height:var(--rws-banner);overflow:hidden;'
    'padding:0.5rem;font:0.85rem/1.25rem sans-serif;'
    'background:#b00020;color:#fff;text-align:center;">Vista staff: este '
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
        except FileNotFoundError:
            # A group that was never configured is visible. Any other
            # failure to read the file must not make it public: checking
            # for the file first would take an unreadable one for none.
            return
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


@functools.cache
def _login_pool() -> ThreadPool:
    """Return the threads that check staff passwords, made on first use.

    return: a small pool of its own, so that a burst of anonymous logins
        can neither fill the pool that the hub shares with everything
        else, nor queue up in front of it.

    """
    return ThreadPool(LOGIN_THREADS)


def _close_connection(start_response):
    """Make the gevent server close the connection after this response.

    Used when the response stops short of its promised length, which
    would leave the browser waiting on a keep-alive connection for the
    rest, and when the app left part of the request body unread.

    start_response: the WSGI start_response callable of the request.

    """
    handler = getattr(start_response, "__self__", None)
    if isinstance(handler, WSGIHandler):
        handler.close_connection = True


def _cache_control(start_response, value: str):
    """Wrap start_response to set the Cache-Control of the response.

    start_response: the WSGI start_response callable.
    value: the Cache-Control header to send, whatever the app says.

    return: a start_response that replaces the Cache-Control header. The
        gevent handler stays reachable in its __self__, where the
        /events handler looks for it.

    """
    def wrapped(status, headers, exc_info=None):
        headers = [(key, header) for key, header in headers
                   if key.lower() != "cache-control"]
        headers.append(("Cache-Control", value))
        return start_response(status, headers, exc_info)

    handler = getattr(start_response, "__self__", None)
    if handler is not None:
        wrapped.__self__ = handler
    return wrapped


class _CutWhenHidden:
    """Wrap a response body so it stops once the group gets hidden.

    The connection is closed when that happens, because the body ends
    before its headers say it does.

    """

    def __init__(self, iterable, state: VisibilityState, start_response):
        self._iterable = iterable
        self._state = state
        self._start_response = start_response

    def __iter__(self):
        for chunk in self._iterable:
            if self._state.hidden:
                _close_connection(self._start_response)
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

    def _login(self, request: Request, start_response) -> Response:
        """Check the staff password and start a staff session.

        request: the POST to staff-login, with the form field password.
        start_response: the WSGI start_response callable.

        return: a redirect that sets the staff cookie, or the notice with
            an error after a failed attempt (or a 413 if the body is too
            big to be a login form).

        """
        # The gevent server hands the app a chunked body as a stream that
        # has no end but the client's. With a limit, werkzeug stops
        # reading at that many bytes and raises for a declared length or
        # a multipart body over it.
        request.max_content_length = MAX_LOGIN_BODY
        try:
            # As typed, without stripping: AWS stores it as it got it.
            password = request.form.get("password", "")
            # A urlencoded form that is read to its end is cut at the
            # limit without a word. Reading past the limit raises, and
            # finds nothing if the body was shorter.
            request.stream.read(1)
        except RequestEntityTooLarge as error:
            # The rest of the body stays unread: no keep-alive.
            _close_connection(start_response)
            response = error.get_response(request.environ)
            response.headers.update(NO_STORE)
            return response
        stored = self.state.staff_password
        valid = False
        if stored is not None and password != "":
            try:
                # bcrypt takes about 250 ms of CPU. On the hub it would
                # stop every public scoreboard during a burst of attempts,
                # so it runs in threads, apart from the shared pool.
                valid = _login_pool().apply(
                    validate_password, (stored, password))
            except ValueError:
                # An authentication method that is not known.
                valid = False
        if not valid:
            gevent.sleep(self.FAILED_LOGIN_DELAY)
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
                    _close_connection(start_response)
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
        if path == "/visibility" and request.method == "PUT":
            return self._update(request)(environ, start_response)
        if path == "/staff-logout" and request.method == "GET":
            return self._logout()(environ, start_response)
        if not self.state.hidden and path in INDEX_PATHS and \
                request.method in ("GET", "HEAD"):
            # The index page has a Last-Modified and nothing else (or, by
            # its file name, a max-age of 12 hours), so a browser would
            # keep it without asking, and show the scoreboard instead of
            # the notice once the group is hidden.
            start_response = _cache_control(start_response, REVALIDATE)
        if self._is_staff(request):
            if self.state.hidden:
                if path == "/" and request.method == "GET":
                    return self._with_banner(environ, start_response)
                # A cache shared with the public must not keep this.
                start_response = _cache_control(
                    start_response, PRIVATE_NO_STORE)
            return self.app(environ, start_response)
        if not self.state.hidden:
            return _CutWhenHidden(self.app(environ, start_response),
                                  self.state, start_response)
        if request.method in ("PUT", "DELETE"):
            # The store handlers check the proxy's credentials too, but
            # after they look the key up, so an anonymous DELETE would
            # tell which keys exist. Static files would take a PUT.
            if not self._writer_authorized(request):
                logger.warning("Unauthorized request.",
                               extra={"location": request.url})
                return self._unauthorized()(environ, start_response)
            return self.app(environ, start_response)
        if path == "/" and request.method in ("GET", "HEAD"):
            return self._notice()(environ, start_response)
        if path == "/staff-login" and request.method == "POST":
            return self._login(request, start_response)(
                environ, start_response)
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

    def _unauthorized(self) -> Response:
        """Build the answer to a write without the proxy's credentials.

        return: a 401 that asks for them and must not be cached.

        """
        return Response("Unauthorized", status=401, headers={
            **NO_STORE,
            "WWW-Authenticate": 'Basic realm="%s"' % self.realm_name})

    def _update(self, request: Request) -> Response:
        if not self._writer_authorized(request):
            logger.warning("Unauthorized visibility update.",
                           extra={"location": request.url})
            return self._unauthorized()
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
