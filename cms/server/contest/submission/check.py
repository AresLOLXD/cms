#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2014 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2017 Stefano Maggiolo <s.maggiolo@gmail.com>
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

"""Functions to check whether submissions are allowed.

Provide functions to verify whether a given contestant is allowed to
send a submission or a user test on a given task and contest, based on
the current situation. Also include some support functions, which are
exported as they may be of general interest.

"""
import logging
import typing
from datetime import datetime, timedelta
from sqlalchemy import desc, func, select, text
from sqlalchemy.sql import Select

from cms.db import Task, Submission
from cms.db.contest import Contest
from cms.db.session import Session
from cms.db.user import Participation
from cms.db.usertest import UserTest


logger = logging.getLogger(__name__)


# Dummy function to mark translatable strings.
def N_(msgid):
    return msgid


# Key spaces of the advisory locks that serialize the limit checks of a
# participation: the first key of the two-key form of
# pg_try_advisory_xact_lock (an int32), the participation id being the
# second. There is one per kind of request, so that a submission never
# makes a token request of the same contestant fail.
#
# The fork's advisory lock namespaces are four ASCII letters read as a
# big-endian int32, all below 2^31. Never reuse one for something else,
# and keep this list in sync with the one in activity.py:
# - ACTV 0x41435456: the activity log's flush (activity.py);
# - SUBM 0x5355424D: the limit checks of accept_submission;
# - UTST 0x55545354: the limit checks of accept_user_test;
# - TOKN 0x544F4B4E: the checks of accept_token.
SUBMISSION_LOCK_NAMESPACE = 0x5355424D  # "SUBM" in ASCII.
USER_TEST_LOCK_NAMESPACE = 0x55545354  # "UTST" in ASCII.
TOKEN_LOCK_NAMESPACE = 0x544F4B4E  # "TOKN" in ASCII.

RequestKind = typing.Literal["submission", "user test", "token"]

LIMIT_LOCK_NAMESPACES: dict[RequestKind, int] = {
    "submission": SUBMISSION_LOCK_NAMESPACE,
    "user test": USER_TEST_LOCK_NAMESPACE,
    "token": TOKEN_LOCK_NAMESPACE,
}

TRY_LOCK_PARTICIPATION = text(
    "SELECT pg_try_advisory_xact_lock("
    "CAST(:ns AS integer), :participation_id)")

# The text of the refusal of a request whose lock was taken (the
# subject is the usual one of its kind of request).
CONCURRENT_REQUEST_TEXT = N_(
    "Another request from your account was being processed at the same "
    "moment, so this one was not accepted. Wait a few seconds and try "
    "again.")


def try_lock_limit_checks(
    sql_session: Session,
    participation: Participation,
    request_kind: RequestKind,
) -> bool:
    """Try to take the lock of a contestant's limits for this request.

    Without it, two requests of the same contestant handled at the same
    moment by two CWS shards would both pass a limit check (each one
    reads the past requests before the other one commits) and both be
    accepted, exceeding the limit. The lock is a transaction-level
    advisory lock on the participation, in the namespace of the kind of
    request, taken on the request's own session: the commit, the
    rollback or the close of the session releases it. It is never
    waited for: a shard's requests run on its event loop, so a wait
    would stall the whole shard. If another request holds it, this one
    is to be refused, and a retry a few seconds later is considered
    anew. That refusal is logged at INFO.

    The lock only protects a decision whose every input is a fresh SQL
    statement, issued after the lock, on the request's own connection:
    under PostgreSQL's default READ COMMITTED, each statement sees
    whatever the previous holder committed. Under REPEATABLE READ or
    SERIALIZABLE, the snapshot is taken by the transaction's first
    statement, so the lock would have to come before the first query
    of the handler's prepare().

    Call it only where a request is accepted (before the checks that
    read past requests), never from the checks themselves, as pages
    that only show the limits call those too.

    sql_session: the session of the request, which must end its
        transaction as soon as the request is accepted or refused.
    participation: the contestant sending the request.
    request_kind: what the request is, which chooses the namespace.

    return: whether the lock was taken; False means that another
        request of the contestant holds it.

    """
    locked = sql_session.execute(TRY_LOCK_PARTICIPATION, {
        "ns": LIMIT_LOCK_NAMESPACES[request_kind],
        "participation_id": participation.id,
    }).scalar_one()
    if not locked:
        logger.info("concurrent request rejected for %s (participation %d, "
                    "%s)", participation.user.username, participation.id,
                    request_kind)
    return locked


def _filter_submission_query(
    q: Select,
    participation: Participation,
    contest: Contest | None,
    task: Task | None,
    cls: type[Submission | UserTest],
) -> Select:
    """Filter a query for submissions by participation, contest, task.

    Apply to the given query some filters that narrow down the set of
    results to the submissions that were sent in by the given
    contestant on the given contest or task.

    q: a SQLAlchemy query, assumed to select from either
        submissions or user tests (as specified by cls).
    participation: the contestant to filter for.
    contest: the contest to filter for.
    task: the task to filter for.
    cls: either Submission or UserTest, specifies which class
        the query selects from.

    return: the original query with the filters applied.

    """
    if task is not None:
        if contest is not None and contest is not task.contest:
            raise ValueError("contest and task don't match")
        q = q.filter(cls.task == task)
    elif contest is not None:
        q = q.join(cls.task) \
            .filter(Task.contest == contest)
    else:
        raise ValueError("need at least one of contest and task")
    q = q.filter(cls.participation == participation)
    return q


def get_submission_count(
    sql_session: Session,
    participation: Participation,
    contest: Contest | None = None,
    task: Task | None = None,
    cls: type[Submission | UserTest] = Submission,
) -> int:
    """Return the number of submissions the contestant sent in.

    Count the submissions (or user tests) for the given participation
    on the given task or contest (that is, on all the contest's tasks).

    sql_session: the SQLAlchemy session to use.
    participation: the participation to fetch data for.
    contest: if given count on all the contest's tasks.
    task: if given count only on this task (trumps contest).
    cls: if the UserTest class is given, count user tests rather
        than submissions.

    return: the count.

    """
    q = select(func.count(cls.id))
    q = _filter_submission_query(q, participation, contest, task, cls)
    return sql_session.execute(q).scalar_one()


def check_max_number(
    sql_session: Session,
    max_number: int | None,
    participation: Participation,
    contest: Contest | None = None,
    task: Task | None = None,
    cls: type[Submission | UserTest] = Submission,
) -> bool:
    """Check whether user already sent in given number of submissions.

    Verify whether the given participation did already hit the given
    constraint on the maximum number of submissions (i.e., whether they
    submitted at least as many submissions as the limit) and return the
    *opposite*, that is, return whether they are allowed to send more.

    sql_session: the SQLAlchemy session to use.
    max_number: the constraint; None means no constraint has
        to be enforced and thus True is always returned.
    participation: the participation to fetch data for.
    contest: if given count on all the contest's tasks.
    task: if given count only on this task (trumps contest).
    cls: if the UserTest class is given, count user tests rather
        than submissions.

    return: whether the contestant can submit more.

    """
    if max_number is None or participation.unrestricted:
        return True
    count = get_submission_count(
        sql_session, participation, contest=contest, task=task, cls=cls)
    return count < max_number


def get_latest_submission(
    sql_session: Session,
    participation: Participation,
    contest: Contest | None = None,
    task: Task | None = None,
    cls: type[Submission | UserTest] = Submission,
) -> Submission | UserTest | None:
    """Return the most recent submission the contestant sent in.

    Retrieve the submission (or user test) with the latest timestamp
    among the ones for the given participation on the given task or
    contest (that is, on all the contest's tasks).

    sql_session: the SQLAlchemy session to use.
    participation: the participation to fetch data for.
    contest: if given look at all the contest's tasks.
    task: if given look only at this task (trumps contest).
    cls: if the UserTest class is given, fetch user tests rather
        than submissions.

    return: the latest submission/user test,
        if any.

    """
    q = select(cls)
    q = _filter_submission_query(q, participation, contest, task, cls)
    q = q.order_by(desc(cls.timestamp))
    return sql_session.execute(q).scalars().first()


def check_min_interval(
    sql_session: Session,
    min_interval: timedelta | None,
    timestamp: datetime,
    participation: Participation,
    contest: Contest | None = None,
    task: Task | None = None,
    cls: type[Submission | UserTest] = Submission,
) -> bool:
    """Check whether user sent in latest submission long enough ago.

    Verify whether at least the given amount of time has passed since
    the given participation last sent in a submission (or user test).

    sql_session: the SQLAlchemy session to use.
    min_interval: the constraint; None means no
        constraint has to be enforced and thus True is always returned.
    timestamp: the current timestamp.
    participation: the participation to fetch data for.
    contest: if given look at all the contest's tasks.
    task: if given look only at this task (trumps contest).
    cls: if the UserTest class is given, fetch user tests rather
        than submissions.

    return: whether the contestant's "cool down" period has
        expired and they can submit again.

    """
    if min_interval is None or participation.unrestricted:
        return True
    submission = get_latest_submission(
        sql_session, participation, contest=contest, task=task, cls=cls)
    return (submission is None
            or timestamp - submission.timestamp >= min_interval)


def is_last_minutes(timestamp: datetime, participation: Participation):
    """
    timestamp: the current timestamp.
    participation: the participation to be checked.

    return: whether the participation is in its last minutes of contest.
    """

    if participation.unrestricted \
            or participation.contest.min_submission_interval_grace_period is None:
        return False

    if participation.group.per_user_time is None:
        end_time = participation.group.stop
    else:
        end_time = participation.starting_time + participation.group.per_user_time

    end_time += participation.delay_time + participation.extra_time
    time_left = end_time - timestamp
    return time_left <= \
        participation.contest.min_submission_interval_grace_period
