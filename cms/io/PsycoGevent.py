#!/usr/bin/env python3

# This file was taken from
# https://bitbucket.org/zzzeek/green_sqla/src/2732bb7ea9d06b9d4a61e8c \
# d587a95148ce2599b/green_sqla/psyco_gevent.py?at=default

"""A wait callback to allow psycopg2 cooperation with gevent.

Use `make_psycopg_green()` to enable gevent support in Psycopg.

"""

# Copyright (C) 2010 Daniele Varrazzo <daniele.varrazzo@gmail.com>
# Copyright (C) 2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# and licensed under the MIT license:
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

# The fork changes this vendored file: gevent_wait_callback enforces
# connect_timeout during a connect (see connect_deadline, #39).

import time
from contextlib import contextmanager

import psycopg2
from gevent.socket import wait_read, wait_write
from psycopg2 import extensions


# Libpq's floor for connect_timeout: a positive value under 2 s acts as
# 2 s.
MIN_CONNECT_TIMEOUT_S = 2


def make_psycopg_green():
    """Configure Psycopg to be used with gevent in non-blocking way."""
    if not hasattr(extensions, 'set_wait_callback'):
        raise ImportError(
            "support for coroutines not available in this Psycopg version (%s)"
            % psycopg2.__version__)

    extensions.set_wait_callback(gevent_wait_callback)


def unmake_psycopg_green():
    """Undo make_psycopg_green()."""
    if not hasattr(extensions, 'set_wait_callback'):
        raise ImportError(
            "support for coroutines not available in this Psycopg version (%s)"
            % psycopg2.__version__)

    extensions.set_wait_callback(None)


def is_psycopg_green():
    """Test whether gevent compatibility layer is installed in psycopg."""
    if not hasattr(extensions, 'set_wait_callback'):
        raise ImportError(
            "support for coroutines not available in this Psycopg version (%s)"
            % psycopg2.__version__)

    return extensions.get_wait_callback() == gevent_wait_callback


def connect_deadline(conn: extensions.connection) -> float | None:
    """Return when the connect a wait callback serves must give up.

    Libpq leaves connect_timeout to the application when it connects
    without blocking, as green psycopg2 does. The value is the
    connection's own connect_timeout, so that a URL's value (0
    included) applies: 0, none or a value that is not an integer means
    no deadline (blocking libpq rejects the last one; green libpq
    ignores it, so such a connect has no deadline, as before #39), and
    a positive value under MIN_CONNECT_TIMEOUT_S counts as that, as in
    libpq.
    Limits: with a multi-host URL the deadline covers the whole
    connect, not each host; a DNS lookup that blocks before the socket
    exists is not covered.

    conn: the connection the callback waits for, before its first
        poll(): its status leaves STATUS_SETUP as soon as the connect
        makes progress.

    return: the time.monotonic() deadline, or None when the call is not
        a connect or has no deadline.

    """
    if conn.status != extensions.STATUS_SETUP:
        return None
    try:
        seconds = int(extensions.parse_dsn(conn.dsn).get("connect_timeout"))
    except (TypeError, ValueError):
        # Absent, or not an integer: blocking libpq rejects the latter;
        # green libpq ignores it, so such a connect has no deadline, as
        # before #39.
        return None
    if seconds <= 0:
        return None
    return time.monotonic() + max(seconds, MIN_CONNECT_TIMEOUT_S)


def gevent_wait_callback(conn, timeout=None):
    """A wait callback useful to allow gevent to work with Psycopg.

    During a connect it also enforces connect_timeout (see
    connect_deadline): it closes the connection and raises
    psycopg2.OperationalError("timeout expired"), libpq's own message.
    It never bounds a query: a client-side cut would not cancel the
    server's work.

    raise (psycopg2.OperationalError): when the connect_timeout of a
        connect runs out.

    """
    deadline = connect_deadline(conn)
    while 1:
        state = conn.poll()
        if state == extensions.POLL_OK:
            break
        elif state == extensions.POLL_READ:
            wait = wait_read
        elif state == extensions.POLL_WRITE:
            wait = wait_write
        else:
            raise psycopg2.OperationalError(
                "Bad result from poll: %r" % state)
        if deadline is None:
            wait(conn.fileno(), timeout=timeout)
            continue
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                wait(conn.fileno(), timeout=remaining)
                continue
            except TimeoutError:
                # gevent's socket.timeout.
                pass
        # Close now: gevent keeps the last timeout error, and with it
        # this frame, so the socket would stay open until the next one.
        # Reachable only during a connect: psycopg2 holds the
        # connection's lock while a query waits, so close() there would
        # deadlock.
        conn.close()
        raise psycopg2.OperationalError("timeout expired")


@contextmanager
def ungreen_psycopg():
    """Temporarily disable gevent support in psycopg.

    Inside this context manager you can use psycopg's features that
    are not compatible with coroutine support, such as large
    objects. Of course, at the expense of being blocking, so please
    stay inside the context manager as short as possible.

    """
    is_green = is_psycopg_green()
    if is_green:
        unmake_psycopg_green()
    try:
        yield
    finally:
        if is_green:
            make_psycopg_green()
