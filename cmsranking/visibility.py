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

import dataclasses
import functools
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import time
from datetime import datetime

import gevent
from gevent.pywsgi import WSGIHandler
from gevent.threadpool import ThreadPool
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.wrappers import Request, Response

from cmscommon.crypto import parse_authentication, validate_password
from cmscommon.ranking_groups import check_window, window_is_open


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

# The environ key through which the guard tells the handlers to serve a
# frozen group's data as it was at this Unix time.
FREEZE_AT_ENVIRON = "cmsranking.freeze_at"

# What a public request to a frozen group gets, by first path segment.
# This is an allow-list: a segment that is not here is refused. Every route
# of the namespace app and every top-level entry of the static directory
# must be listed (a test checks it): "filter" is served as of the freeze
# time (the event stream without its score events), "forbid" is refused,
# "pass" carries nothing that changes after the freeze.
FROZEN_ROUTES = {
    "": "pass", "contests": "pass", "tasks": "pass", "teams": "pass",
    "users": "pass", "faces": "pass", "flags": "pass", "logo": "pass",
    "config": "pass",
    "scores": "filter", "history": "filter", "sublist": "filter",
    "events": "filter",
    "submissions": "forbid", "subchanges": "forbid",
    # The static files.
    "Ranking.html": "pass", "Ranking.css": "pass", "Ranking.js": "pass",
    "Chart.js": "pass", "Config.js": "pass", "DataStore.js": "pass",
    "HistoryStore.js": "pass", "Overview.js": "pass",
    "Scoreboard.js": "pass", "TeamSearch.js": "pass",
    "TimeView.js": "pass", "UserDetail.js": "pass",
    "img": "pass", "lib": "pass",
}

HIDDEN_TITLE = "Ranking oculto"
HIDDEN_MESSAGE = "Este ranking está oculto por ahora."
FROZEN_LOGIN_TITLE = "Acceso staff"
FROZEN_LOGIN_MESSAGE = "Acceso del staff al ranking en vivo."

NOTICE_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
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
<p>{message}</p>
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
def _banner(text_html: str) -> bytes:
    """Build a bottom bar with the given text (see STAFF_BANNER's CSS)."""
    return (
        '<style>'
        ':root{--rws-banner:2.25rem}'
        '@media(max-width:30em){:root{--rws-banner:3.5rem}}'
        '#InnerFrame,#UserDetail_bg{bottom:var(--rws-banner)}'
        '#SidePanel{bottom:calc(30px + var(--rws-banner))}'
        '</style>'
        '<div style="position:fixed;bottom:0;left:0;right:0;z-index:1000;'
        'box-sizing:border-box;height:var(--rws-banner);overflow:hidden;'
        'padding:0.5rem;font:0.85rem/1.25rem sans-serif;'
        'background:#b00020;color:#fff;text-align:center;">'
        + text_html + '</div>').encode("utf-8")


LINK = '<a style="color:#fff" href="%s">%s</a>'
STAFF_BANNER = _banner("Vista staff: este ranking está oculto al público "
                       "&middot; " + LINK % ("staff-logout", "Salir"))
STAFF_FROZEN_BANNER = _banner(
    "Vista staff: ranking congelado para el público &middot; "
    + LINK % ("staff-logout", "Salir"))


def public_frozen_banner(freeze_at: int) -> bytes:
    """Build the public bar of a frozen group.

    freeze_at: the freeze time, in Unix seconds.

    return: the bar, with the time in the server's local time zone.

    """
    when = datetime.fromtimestamp(freeze_at).astimezone()
    return _banner("Ranking congelado desde las %s (%s) &middot; %s" % (
        when.strftime("%H:%M"), when.strftime("%Z"),
        LINK % ("staff-login", "Acceso staff")))


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


TIME_FIELDS = ("hide_at", "show_at", "freeze_at", "unfreeze_at")


@dataclasses.dataclass(frozen=True)
class VisibilitySettings:
    """The visibility windows of a group and its staff password.

    The times are Unix seconds, or None: the group is hidden during
    [hide_at, show_at) and frozen during [freeze_at, unfreeze_at), and
    hidden wins over frozen.

    """
    hide_at: int | None = None
    show_at: int | None = None
    freeze_at: int | None = None
    unfreeze_at: int | None = None
    staff_password: str | None = None

    def hidden(self, now: float) -> bool:
        """Tell whether the group is hidden at a given time.

        now: the Unix time to check.

        return: True if now falls in [hide_at, show_at).

        """
        return window_is_open(self.hide_at, self.show_at, now)

    def frozen(self, now: float) -> bool:
        """Tell whether the group is frozen at a given time.

        now: the Unix time to check.

        return: True if now falls in [freeze_at, unfreeze_at) and the
            group is not hidden then.

        """
        return not self.hidden(now) and \
            window_is_open(self.freeze_at, self.unfreeze_at, now)

    def boundaries(self) -> list[int]:
        """Return the times at which the public view may change.

        return: the times that are not None.

        """
        return [t for t in (self.hide_at, self.show_at, self.freeze_at,
                            self.unfreeze_at) if t is not None]

    def to_json(self) -> dict:
        """Return the settings in the wire format.

        return: the four times and staff_password.

        """
        return dataclasses.asdict(self)


# What an unreadable state file and an old {"hidden": true} both mean.
HIDDEN_SINCE_ALWAYS = VisibilitySettings(hide_at=0)


def parse_settings(data: object) -> VisibilitySettings:
    """Validate the settings sent by ProxyService, in either format.

    data: the decoded JSON: the new format (the four times and
        staff_password) or MC-2 minimal's (hidden and staff_password).

    return: the settings.

    raise (ValueError): if data is neither format, a time is not an
        integer, a window ends before it starts, or the password is not
        a valid authentication string.

    """
    if not isinstance(data, dict):
        raise ValueError("The settings must be an object.")
    staff_password = data.get("staff_password", ())
    if staff_password == ():
        raise ValueError("staff_password is missing.")
    if staff_password is not None:
        if not isinstance(staff_password, str):
            raise ValueError("staff_password must be a string or null.")
        parse_authentication(staff_password)
    old = "hidden" in data
    new = any(field in data for field in TIME_FIELDS)
    if old == new:
        raise ValueError("Send either hidden or the four times.")
    if old:
        if not isinstance(data["hidden"], bool):
            raise ValueError("hidden must be a boolean.")
        base = HIDDEN_SINCE_ALWAYS if data["hidden"] else VisibilitySettings()
        return dataclasses.replace(base, staff_password=staff_password)
    times = dict()
    for field in TIME_FIELDS:
        if field not in data:
            raise ValueError("%s is missing." % field)
        value = data[field]
        # bool is a subclass of int, and True must not mean 1970.
        if value is not None and (isinstance(value, bool)
                                  or not isinstance(value, int)):
            raise ValueError("%s must be an integer or null." % field)
        times[field] = value
    check_window(times["hide_at"], times["show_at"], "hide")
    check_window(times["freeze_at"], times["unfreeze_at"], "freeze")
    return VisibilitySettings(staff_password=staff_password, **times)


class VisibilityState:
    """The visibility settings of one group, stored in its directory.

    """

    def __init__(self, group_dir: str):
        self.path = os.path.join(group_dir, VISIBILITY_FILE)
        self.settings = VisibilitySettings()
        self.secret = secrets.token_hex(32)
        # A restart counts as a change: streams opened before it reload.
        self.changed_at = time.time()
        self._load()

    @property
    def hidden(self) -> bool:
        return self.settings.hidden(time.time())

    @property
    def staff_password(self) -> str | None:
        return self.settings.staff_password

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            secret = data.pop("secret")
            if not isinstance(secret, str) or secret == "":
                raise ValueError("Wrong secret.")
            bytes.fromhex(secret)
            settings = parse_settings(data)
        except FileNotFoundError:
            # A group that was never configured is visible. Any other
            # failure to read the file must not make it public: checking
            # for the file first would take an unreadable one for none.
            return
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            logger.error("Cannot read %s: hiding the ranking until its "
                         "visibility is sent again.", self.path,
                         exc_info=True)
            self.settings = HIDDEN_SINCE_ALWAYS
            return
        self.settings = settings
        self.secret = secret

    def update_settings(self, settings: VisibilitySettings) -> bool:
        """Replace the settings, storing them atomically first.

        settings: the new settings.

        return: whether they differ from the previous ones.

        """
        data = dict(settings.to_json(), secret=self.secret)
        directory = os.path.dirname(self.path)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".vis-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp_path, self.path)
        except BaseException:
            os.unlink(tmp_path)
            raise
        changed = settings != self.settings
        # Only the windows change what the public sees: a new staff
        # password must not make every public page reload.
        view_changed = dataclasses.replace(settings, staff_password=None) \
            != dataclasses.replace(self.settings, staff_password=None)
        self.settings = settings
        if view_changed:
            self.changed_at = time.time()
        return changed

    def update(self, hidden: bool, staff_password: str | None):
        """Replace the settings from MC-2 minimal's format.

        hidden: whether the public scoreboard is hidden.
        staff_password: the staff authentication string, or None.

        """
        self.update_settings(parse_settings(
            {"hidden": hidden, "staff_password": staff_password}))

    def last_change(self, now: float) -> float:
        """Return when the public view last changed, as of now.

        now: the current Unix time.

        return: the latest of the last settings change and the scheduled
            times already passed.

        """
        passed = [t for t in self.settings.boundaries() if t <= now]
        return max([self.changed_at] + passed)


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

    def _login(self, request: Request, start_response,
               message: str = HIDDEN_MESSAGE,
               title: str = HIDDEN_TITLE) -> Response:
        """Check the staff password and start a staff session.

        request: the POST to staff-login, with the form field password.
        start_response: the WSGI start_response callable.
        message: the text of the notice shown after a failed attempt.
        title: the title of that notice.

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
            return self._notice(error=True, status=401, message=message,
                                title=title)
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

    def _with_banner(self, environ, start_response,
                     banner: bytes = STAFF_BANNER):
        """Serve the app's page with a banner after <body>.

        environ: the WSGI environ.
        start_response: the WSGI start_response callable.
        banner: the bar to insert.

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
        body = BODY_TAG.sub(lambda m: m.group(0) + banner, body, count=1)
        dropped = {"content-length", "last-modified", "etag",
                   "cache-control"}
        headers = [(k, v) for k, v in captured["headers"]
                   if k.lower() not in dropped]
        headers += [("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store")]
        start_response(captured["status"], headers)
        return [body]

    def _guard_writes(self, request: Request, start_response,
                      opened_at: float):
        """Wrap start_response so write() follows the public view.

        The /events handler sends its data through the write() callable
        instead of the returned iterable, so _CutWhenHidden never sees
        it. Its error handling ends the stream when write() raises, and
        closing the connection makes the browser reconnect: to get the
        403, or to be told to reload if the view changed meanwhile.

        The public view a request gets is decided when it comes (while
        frozen, the event stream leaves the score events out from the
        start): any change of that view cuts the stream at its next
        write.

        request: the request being served.
        start_response: the WSGI start_response callable.
        opened_at: the Unix time at which the request came, the one its
            view was decided at.

        return: a start_response whose write() callables refuse to send
            data to a public client once the group is hidden or its view
            changed since the request came.

        """
        handler = getattr(start_response, "__self__", None)

        def guarded_start_response(status, headers, exc_info=None):
            write = start_response(status, headers, exc_info)

            def guarded_write(data):
                if not self._is_staff(request):
                    now = time.time()
                    if self.state.settings.hidden(now) \
                            or self.state.last_change(now) > opened_at:
                        _close_connection(start_response)
                        raise ConnectionAbortedError(
                            "The ranking changed its visibility.")
                return write(data)

            return guarded_write

        if handler is not None:
            # The /events handler looks for the gevent handler here.
            guarded_start_response.__self__ = handler
        return guarded_start_response

    def __call__(self, environ, start_response):
        request = Request(environ)
        path = request.path
        now = time.time()
        hidden = self.state.settings.hidden(now)
        frozen = self.state.settings.frozen(now)
        start_response = self._guard_writes(request, start_response, now)
        if path == "/visibility" and request.method == "PUT":
            return self._update(request)(environ, start_response)
        if path == "/staff-logout" and request.method == "GET":
            return self._logout()(environ, start_response)
        staff = self._is_staff(request)
        if not hidden and not (staff and frozen) and \
                path in INDEX_PATHS and request.method in ("GET", "HEAD"):
            # The index page has a Last-Modified and nothing else (or, by
            # its file name, a max-age of 12 hours), so a browser would
            # keep it without asking, and show the scoreboard instead of
            # the notice once the group is hidden. (What the staff see of
            # a hidden or frozen group is private, whatever this says.)
            start_response = _cache_control(start_response, REVALIDATE)
        if staff:
            if hidden or frozen:
                # A cache shared with the public must not keep this, not
                # even the page that gets a banner.
                start_response = _cache_control(
                    start_response, PRIVATE_NO_STORE)
                if path == "/" and request.method == "GET":
                    return self._with_banner(
                        environ, start_response,
                        STAFF_BANNER if hidden else STAFF_FROZEN_BANNER)
            return self.app(environ, start_response)
        if path == "/events" and request.method == "GET" and not hidden \
                and self._missed_a_change(request, now):
            return Response(b"event:reload\ndata:\n\n", status=200,
                            mimetype="text/event-stream",
                            headers=NO_STORE)(environ, start_response)
        if not hidden and not frozen:
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
        if frozen:
            return self._frozen(request, environ, start_response)
        if path == "/" and request.method in ("GET", "HEAD"):
            return self._notice()(environ, start_response)
        if path == "/staff-login" and request.method == "POST":
            return self._login(request, start_response)(
                environ, start_response)
        return Response("Este ranking está oculto.", status=403,
                        mimetype="text/plain",
                        headers=NO_STORE)(environ, start_response)

    def _frozen(self, request: Request, environ, start_response):
        """Serve a public request to a frozen group.

        request: the request being served.
        environ: the WSGI environ.
        start_response: the WSGI start_response callable.

        return: the body of the response.

        """
        path = request.path
        if path == "/staff-login":
            if request.method == "POST":
                return self._login(request, start_response,
                                   FROZEN_LOGIN_MESSAGE,
                                   FROZEN_LOGIN_TITLE)(
                    environ, start_response)
            return self._notice(message=FROZEN_LOGIN_MESSAGE,
                                title=FROZEN_LOGIN_TITLE)(
                environ, start_response)
        if path == "/" and request.method == "GET":
            return self._with_banner(
                environ, start_response,
                public_frozen_banner(self.state.settings.freeze_at))
        kind = FROZEN_ROUTES.get(path.strip("/").split("/")[0], "forbid")
        if kind == "forbid":
            return Response("Este ranking está congelado.", status=403,
                            mimetype="text/plain",
                            headers=NO_STORE)(environ, start_response)
        if kind == "filter":
            environ[FREEZE_AT_ENVIRON] = self.state.settings.freeze_at
            start_response = _cache_control(start_response, "no-store")
        return _CutWhenHidden(self.app(environ, start_response),
                              self.state, start_response)

    def _missed_a_change(self, request: Request, now: float) -> bool:
        """Tell whether a reconnecting stream predates the last change.

        request: the /events request, with the ID of the last event the
            client got (the microseconds since the epoch, in hex).
        now: the current Unix time.

        return: True if that event is older than the last change of the
            public view, so replaying the events since then would be
            wrong.

        """
        last_id = request.headers.get("Last-Event-ID") \
            or request.args.get("last_event_id")
        if not last_id or not re.fullmatch(r"[0-9A-Fa-f]+", last_id):
            return False
        return int(last_id, 16) < self.state.last_change(now) * 1_000_000

    def _notice(self, error: bool = False, status: int = 200,
                message: str = HIDDEN_MESSAGE,
                title: str = HIDDEN_TITLE) -> Response:
        body = NOTICE_TEMPLATE.format(
            group=self._escaped_group(), message=message, title=title,
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
            settings = parse_settings(
                json.loads(request.get_data(as_text=True)))
        except (ValueError, TypeError) as error:
            logger.warning("Bad visibility update: %s.", error)
            return Response(str(error), status=400, mimetype="text/plain")
        # ProxyService sends the settings again at each sweep: only a
        # change is worth an INFO line.
        changed = self.state.update_settings(settings)
        logger.log(logging.INFO if changed else logging.DEBUG,
                   "Ranking group %s visibility is now %s.", self.group,
                   json.dumps({k: v for k, v in settings.to_json().items()
                               if k != "staff_password"}))
        return Response(status=204)
