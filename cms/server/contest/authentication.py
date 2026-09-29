#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2014 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2016 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2018 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2013 Bernard Blackham <bernard@largestprime.net>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2015-2016 William Di Luigi <williamdiluigi@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
# Copyright © 2016 Amir Keivan Mohtashami <akmohtashami97@gmail.com>
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
import dataclasses
import ipaddress
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import typing

from sqlalchemy import select
from sqlalchemy.orm import contains_eager, joinedload

from cms import config
from cms.db import Participation, User
from cms.db.contest import Contest
from cms.db.session import Session
from cmscommon.crypto import validate_password
from cmscommon.datetime import make_datetime, make_timestamp


__all__ = ["validate_login", "validate_login_async", "authenticate_request"]


logger = logging.getLogger(__name__)


AnyIPAddress: typing.TypeAlias = ipaddress.IPv4Address | ipaddress.IPv6Address

LoginResult: typing.TypeAlias = tuple[Participation | None, bytes | None]


# The threads checking the passwords of logins. bcrypt releases the GIL, so
# they run in parallel with each other and with the event loop. It is
# dedicated to this so that a burst of logins cannot delay (or be delayed by)
# whatever else uses the event loop's default executor.
_PASSWORD_CHECK_POOL = ThreadPoolExecutor(
    max_workers=min(4, os.cpu_count() or 1),
    thread_name_prefix="cws-password-check")


def get_password(participation: Participation) -> str:
    """Return the password the participation can log in with.

    participation: a participation.

    return: the password that is on record for them.

    """
    if participation.password is None:
        return participation.user.password
    else:
        return participation.password


@dataclasses.dataclass(frozen=True)
class _PendingLogin:
    """A login that is only waiting for its password to be checked.

    It holds plain values only: everything the rest of the login needs
    is read from the database objects before the (slow) password check,
    so that the check can run without a database connection checked out
    and without touching any SQLAlchemy object.

    """

    # Handed back to the caller as the login's result; never read here.
    participation: Participation

    ip_address: AnyIPAddress
    timestamp: datetime
    username: str
    contest_name: str
    stored_username: str
    # Kept out of the repr, as it can be the plaintext password.
    correct_password: str = dataclasses.field(repr=False)
    ip_restriction: bool
    block_hidden_participations: bool
    participation_ip: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network,
                            ...] | None
    hidden: bool

    def finish_invalid_password(self, error: ValueError) -> LoginResult:
        """Conclude a login whose stored password could not be checked.

        error: the error the password check raised.

        return: the result of the login, i.e., a failure.

        """
        # This is either a programming or a configuration error.
        logger.warning(
            "Invalid password stored in database for user %s in contest %s: "
            "%s", self.stored_username, self.contest_name, error)
        return None, None

    def finish(self, password_valid: bool) -> LoginResult:
        """Conclude the login, given the outcome of the password check.

        password_valid: whether the password the user provided matched.

        return: the result of the login, as in validate_login.

        """
        def log_failed_attempt(msg):
            _log_failed_login_attempt(
                self.ip_address, self.username, self.contest_name,
                self.timestamp, msg)

        if not password_valid:
            log_failed_attempt("wrong password")
            return None, None

        if self.ip_restriction and self.participation_ip is not None \
                and not any(self.ip_address in network
                            for network in self.participation_ip):
            log_failed_attempt("unauthorized IP address")
            return None, None

        if self.block_hidden_participations and self.hidden:
            log_failed_attempt("participation is hidden and unauthorized")
            return None, None

        logger.info("Successful login attempt from IP address %s, as user %r, "
                    "on contest %s, at %s", self.ip_address, self.username,
                    self.contest_name, self.timestamp)

        # If hashing is used, the cookie stores the hashed password so that
        # the expensive bcrypt call doesn't need to be done at every request.
        return (self.participation,
                json.dumps([self.username, self.correct_password,
                            make_timestamp(self.timestamp), False])
                    .encode("utf-8"))


def _log_failed_login_attempt(
    ip_address: AnyIPAddress,
    username: str,
    contest_name: str,
    timestamp: datetime,
    msg: str,
):
    logger.info("Unsuccessful login attempt from IP address %s, as user "
                "%r, on contest %s, at %s: " + msg, ip_address,
                username, contest_name, timestamp)


def _begin_login(
    sql_session: Session,
    contest: Contest,
    timestamp: datetime,
    username: str,
    ip_address: AnyIPAddress,
    admin_token: str,
) -> LoginResult | _PendingLogin:
    """Do the part of a login that needs the database.

    See validate_login for the meaning of the arguments.

    return: either the final result of the login, if it could be
        decided without checking the password, or the login waiting for
        the password to be checked.

    """
    contest_name = contest.name

    if not contest.allow_password_authentication and admin_token == "":
        _log_failed_login_attempt(
            ip_address, username, contest_name, timestamp,
            "password authentication not allowed")
        return None, None

    participation: Participation | None = sql_session.execute(
        select(Participation)
        .join(Participation.user)
        .options(contains_eager(Participation.user))
        .filter(Participation.contest == contest)
        .filter(User.username == username)
    ).scalars().first()

    if participation is None:
        _log_failed_login_attempt(
            ip_address, username, contest_name, timestamp,
            "user not registered to contest")
        return None, None

    if admin_token != "":
        if config.contest_web_server.contest_admin_token is None:
            _log_failed_login_attempt(
                ip_address, username, contest_name, timestamp,
                "admin token not configured")
            return None, None

        if admin_token != config.contest_web_server.contest_admin_token:
            _log_failed_login_attempt(
                ip_address, username, contest_name, timestamp,
                "invalid admin token")
            return None, None

        logger.info("Successful impersonated login from IP address %s, as user %r, on "
                    "contest %s, at %s", ip_address, username, contest_name,
                    timestamp)

        return (participation,
                json.dumps([username, "", make_timestamp(timestamp), True])
                    .encode("utf-8"))

    return _PendingLogin(
        participation=participation,
        ip_address=ip_address,
        timestamp=timestamp,
        username=username,
        contest_name=contest_name,
        stored_username=participation.user.username,
        correct_password=get_password(participation),
        ip_restriction=contest.ip_restriction,
        block_hidden_participations=contest.block_hidden_participations,
        participation_ip=(None if participation.ip is None
                          else tuple(participation.ip)),
        hidden=participation.hidden,
    )


def validate_login(
    sql_session: Session,
    contest: Contest,
    timestamp: datetime,
    username: str,
    password: str,
    ip_address: AnyIPAddress,
    admin_token: str = ""
) -> LoginResult:
    """Authenticate a user logging in, with username and password.

    Given the information the user provided (the username and the
    password) and some context information (contest, to determine which
    users are allowed to log in, how and with which restrictions;
    timestamp for cookie creation; IP address to check against) try to
    authenticate the user and return its participation and the cookie
    to set to help authenticate future visits.

    After finding the participation, IP login and hidden users
    restrictions are checked.

    The password is checked on the calling thread. Code running on an
    event loop should use validate_login_async instead.

    sql_session: the SQLAlchemy database session used to
        execute queries.
    contest: the contest the user is trying to access.
    timestamp: the date and the time of the request.
    username: the username the user provided.
    password: the password the user provided.
    ip_address: the IP address the request came from.
    admin_token: administrator's token used to impersonate a user

    return: if the user couldn't
        be authenticated then return None, otherwise return the
        participation that they wanted to authenticate as; if a cookie
        has to be set return it as well, otherwise return None.

    """
    login = _begin_login(
        sql_session, contest, timestamp, username, ip_address, admin_token)
    if not isinstance(login, _PendingLogin):
        return login

    try:
        password_valid = validate_password(login.correct_password, password)
    except ValueError as e:
        return login.finish_invalid_password(e)

    return login.finish(password_valid)


async def validate_login_async(
    sql_session: Session,
    contest: Contest,
    timestamp: datetime,
    username: str,
    password: str,
    ip_address: AnyIPAddress,
    admin_token: str = ""
) -> LoginResult:
    """Authenticate a user logging in, without blocking the event loop.

    Behaves exactly as validate_login, but the password check (bcrypt
    takes around 200 ms of CPU) runs in a dedicated thread pool while
    the caller's coroutine waits for it.

    While waiting, the session must not keep a database connection
    checked out, since many logins can be waiting at the same time and
    would exhaust the connection pool. Therefore, once the participation
    has been read, its data is copied to plain values and the session's
    transaction is rolled back, giving the connection back (so the
    session must not have changes that need to be kept). As a
    consequence all the objects loaded in sql_session are expired when
    this function returns (unless the login is decided before the
    password has to be checked, as with an admin token): the caller
    must read what it needs from them beforehand, as accessing them
    later would start a new transaction.

    See validate_login for the arguments and the return value.

    """
    login = _begin_login(
        sql_session, contest, timestamp, username, ip_address, admin_token)
    if not isinstance(login, _PendingLogin):
        return login

    sql_session.rollback()

    loop = asyncio.get_running_loop()
    try:
        password_valid = await loop.run_in_executor(
            _PASSWORD_CHECK_POOL, validate_password,
            login.correct_password, password)
    except ValueError as e:
        return login.finish_invalid_password(e)

    return login.finish(password_valid)


class AmbiguousIPAddress(Exception):
    pass


def authenticate_request(
    sql_session: Session,
    contest: Contest,
    timestamp: datetime,
    cookie: bytes | None,
    authorization_header: bytes | None,
    ip_address: AnyIPAddress,
) -> tuple[Participation | None, bytes | None, bool]:
    """Authenticate a user returning to the site, with a cookie.

    Given the information the user's browser provided (the cookie) and
    some context information (contest, to determine which users are
    allowed to log in, how and with which restrictions; timestamp for
    cookie validation/creation, IP address to either do autologin or to
    check against) try to authenticate the user and return its
    participation and the cookie to refresh to help authenticate future
    visits.

    There are two way a user can authenticate:
    - if IP autologin is enabled, we look for a participation whose IP
      address matches the remote IP address; if a match is found, the
      user is authenticated as that participation;
    - if username/password authentication is enabled, and a
      "X-CMS-Authorization" header is present and valid, the
      corresponding participation is returned.
    - if username/password authentication is enabled, and the cookie
      is valid, the corresponding participation is returned, together
      with a refreshed cookie.

    After finding the participation, IP login and hidden users
    restrictions are checked.

    In case of any error, or of a login by other sources, no new cookie
    is returned and the old one, if any, should be cleared.

    sql_session: the SQLAlchemy database session used to
        execute queries.
    contest: the contest the user is trying to access.
    timestamp: the date and the time of the request.
    cookie: the cookie the user's browser provided in the
        request (if any).
    authorization_header: the value of X-CMS-Authorization header (if any).
    ip_address: the IP address the request
        came from.

    return: a tuple consisting of participation (None if authentication failed),
        a cookie that has to be set (or None), and a boolean flag indicating
        whether the admin token was used to impersonate a user.

    """
    participation: Participation | None = None
    impersonated = False

    if contest.ip_autologin:
        try:
            participation = _authenticate_request_by_ip_address(
                sql_session, contest, ip_address)
            # If the login is IP-based, the cookie should be cleared.
            if participation is not None:
                cookie = None
        except AmbiguousIPAddress:
            return None, None, False

    if participation is None:
        participation, cookie, impersonated = (
            _authenticate_request_from_cookie_or_authorization_header(
                sql_session, contest, timestamp,
                authorization_header if authorization_header is not None else cookie))

    if participation is None:
        return None, None, False

    # Check if user is using the right IP (or is on the right subnet).
    if (contest.ip_restriction and participation.ip is not None
            and not impersonated
            and not any(ip_address in network for network in participation.ip)):
        logger.info(
            "Unsuccessful authentication from IP address %s, on contest %s, "
            "as %s, at %s: unauthorized IP address",
            ip_address, contest.name, participation.user.username, timestamp)
        return None, None, False

    # Check that the user is not hidden if hidden users are blocked.
    if (contest.block_hidden_participations and participation.hidden
            and not impersonated):
        logger.info(
            "Unsuccessful authentication from IP address %s, on contest %s, "
            "as %s, at %s: participation is hidden and unauthorized",
            ip_address, contest.name, participation.user.username, timestamp)
        return None, None, False

    return participation, cookie, impersonated


def _authenticate_request_by_ip_address(
    sql_session: Session, contest: Contest, ip_address: AnyIPAddress
) -> Participation | None:
    """Return the current participation based on the IP address.

    sql_session: the SQLAlchemy database session used to
        execute queries.
    contest: the contest the user is trying to access.
    ip_address: the IP address the request
        came from.

    return: the only participation that is allowed
        to connect from the given IP address, or None if not found.

    raise (AmbiguousIPAddress): if there is more than one participation
        matching the remote IP address.

    """
    # We encode it as a network (i.e., we assign it a /32 or /128 mask)
    # since we're comparing it for equality with other networks.
    ip_network = ipaddress.ip_network((ip_address, ip_address.max_prefixlen))

    participations_query = (
        select(Participation)
        .options(joinedload(Participation.user))
        .filter(Participation.contest == contest)
        .filter(Participation.ip.any(ip_network))
    )

    # If hidden users are blocked we ignore them completely.
    if contest.block_hidden_participations:
        participations_query = participations_query.filter(
            Participation.hidden.is_(False)
        )

    participations: list[Participation] = list(
        sql_session.execute(participations_query).scalars()
    )

    if len(participations) == 0:
        logger.info(
            "Unsuccessful IP authentication from IP address %s, on contest "
            "%s: no user matches the IP address", ip_address, contest.name)
        return None

    # Having more than participation with the same IP, is a mistake and
    # should not happen. In such case, we disallow login for that IP
    # completely, in order to make sure the problem is noticed.
    if len(participations) > 1:
        # This is a configuration error.
        logger.warning(
            "Ambiguous IP address %s, assigned to %d participations.",
            ip_address, len(participations))
        raise AmbiguousIPAddress()

    participation = participations[0]
    logger.info(
        "Successful IP authentication from IP address %s, as user %s, on "
        "contest %s", ip_address, participation.user.username, contest.name)
    return participation


def _authenticate_request_from_cookie_or_authorization_header(
    sql_session: Session, contest: Contest, timestamp: datetime, cookie: bytes | None
) -> tuple[Participation | None, bytes | None, bool]:
    """Return the current participation based on the cookie.

    If a participation can be extracted, the cookie is refreshed.

    sql_session: the SQLAlchemy database session used to
        execute queries.
    contest: the contest the user is trying to access.
    timestamp: the date and the time of the request.
    cookie: the contents of the cookie (or authorization header)
        provided in the request (if any).

    return: a triple of the participation extracted from the cookie (or None),
        the cookie to set/refresh (or None), and a boolean flag indicating
        impersonation of the user by the administrator.

    """
    if cookie is None:
        logger.info("Unsuccessful cookie authentication: no cookie provided")
        return None, None, False

    # Parse cookie.
    try:
        cookie: typing.Any = json.loads(cookie.decode("utf-8"))
        username: str = cookie[0]
        password: str = cookie[1]
        last_update = make_datetime(cookie[2])
        impersonated: bool = cookie[3]
    except Exception as e:
        # Cookies are stored securely and thus cannot be tampered with:
        # this is either a programming or a configuration error.
        logger.warning("Invalid cookie (%s): %s", e, cookie)
        return None, None, False

    # Reject if password authentication is disabled and it's not an impersonation cookie/header.
    if not contest.allow_password_authentication and not impersonated:
        return None, None, False

    def log_failed_attempt(msg, *args):
        logger.info("Unsuccessful cookie authentication as %r, returning from "
                    "%s, at %s: " + msg, username, last_update, timestamp,
                    *args)

    # Check if the cookie is expired.
    if timestamp - last_update > timedelta(
        seconds=config.contest_web_server.cookie_duration
    ):
        log_failed_attempt("cookie expired (lasts %d seconds)",
                           config.contest_web_server.cookie_duration)
        return None, None, False

    # Load participation from DB and make sure it exists.
    participation: Participation | None = sql_session.execute(
        select(Participation)
        .join(Participation.user)
        .options(contains_eager(Participation.user))
        .filter(Participation.contest == contest)
        .filter(User.username == username)
    ).scalars().first()
    if participation is None:
        log_failed_attempt("user not registered to contest")
        return None, None, False

    if impersonated:
        correct_password = ""
        logger.info("Successful impersonation of user %r, on contest %s, "
                    "returning from %s, at %s", username, contest.name, last_update,
                    timestamp)
    else:
        # We compare hashed password because it would be too expensive to
        # re-hash the user-provided plaintext password at every request.
        correct_password = get_password(participation)
        if password != correct_password:
            log_failed_attempt("wrong password")
            return None, None, False

        logger.info("Successful cookie authentication as user %r, on contest %s, "
                    "returning from %s, at %s", username, contest.name, last_update,
                    timestamp)

    # We store the hashed password (if hashing is used) so that the
    # expensive bcrypt hashing doesn't need to be done at every request.
    return (participation,
            json.dumps([username, correct_password, make_timestamp(timestamp), impersonated])
                .encode("utf-8"),
            impersonated)
