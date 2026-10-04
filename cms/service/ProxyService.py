#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2015 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2013-2018 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2013 Bernard Blackham <bernard@largestprime.net>
# Copyright © 2015 Luca Versari <veluca93@gmail.com>
# Copyright © 2015 William Di Luigi <williamdiluigi@gmail.com>
# Copyright © 2016 Amir Keivan Mohtashami <akmohtashami97@gmail.com>
# Copyright © 2019 Edoardo Morassutto <edoardo.morassutto@gmail.com>
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

"""The service that forwards data to RankingWebServer.

"""

from datetime import datetime
import asyncio
import enum
import json
import logging
import math
import string
from time import monotonic
from urllib.parse import urljoin, urlsplit

import requests
import requests.exceptions
from sqlalchemy import not_, select

from cms import config
from cms.db import SessionGen, Session, Contest, Participation, Task, \
    RankingGroup, Submission, get_submissions
from cms.io import QueueItem
from cms.io.async_triggeredservice import AsyncExecutor, AsyncTriggeredService
from cms.io.priorityqueue import QueueEntry
from cms.io.rpc import rpc_method
from cmscommon.datetime import make_timestamp
from cmscommon.ranking_groups import is_valid_group_name


logger = logging.getLogger(__name__)


# Seconds to wait for a ranking to accept our connection and, once
# connected, to answer. Without them a ranking that stops answering
# would block the thread sending to it forever.
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 120


class CannotSendError(Exception):
    """A request to a ranking failed.

    Unless it is a RejectedError, the ranking may take the same request
    if it is sent again later.

    """


class RejectedError(CannotSendError):
    """A request to a ranking cannot succeed.

    The ranking refused it, answering with a 4xx status, or its data
    cannot be encoded. The same request would fail again.

    """


class UnencodableError(RejectedError):
    """The data of a request to a ranking cannot be encoded as JSON.

    Building the same data again would fail the same way: the data has
    to be fixed first.

    """


class SendOutcome(enum.Enum):
    """What became of a request to a ranking."""

    # The ranking took it.
    SENT = enum.auto()
    # The ranking refused it (4xx), and would refuse it again.
    REJECTED = enum.auto()
    # It did not reach the ranking, or the ranking failed to handle
    # it: it has to be sent again.
    UNSENT = enum.auto()


def encode_id(entity_id: str) -> str:
    """Encode the id using only A-Za-z0-9_.

    entity_id: the entity id to encode.
    return: encoded entity id.

    """
    encoded_id = ""
    for char in entity_id:
        if char not in string.ascii_letters + string.digits:
            encoded_id += "_%x" % ord(char)
        else:
            encoded_id += char
    return encoded_id


def _unix_start(value: datetime | None) -> int | None:
    """Convert the start of a window to Unix seconds, rounding down.

    The ranking takes whole seconds and refuses a window whose end is
    not after its start, while the times set by the "now" buttons of
    the admin have microseconds: a start and an end that are less than
    a second apart would fall on the same second. Rounding the start
    down (and the end up, see _unix_end) keeps the end after the start
    and can only make the window wider by less than a second, which
    hides or freezes a little more, never less.

    value: a naive datetime in UTC, or None.
    return: Unix seconds (int), or None.

    """
    return None if value is None else math.floor(make_timestamp(value))


def _unix_end(value: datetime | None) -> int | None:
    """Convert the end of a window to Unix seconds, rounding up.

    The counterpart of _unix_start, which explains why: an end that
    is after its start in microseconds is still after it in whole
    seconds. An end that is already on a whole second is unchanged.

    value: a naive datetime in UTC, or None.
    return: Unix seconds (int), or None.

    """
    return None if value is None else math.ceil(make_timestamp(value))


def _check_status(status_code: int, operation: str):
    """Raise the right error if a ranking answered with a failure.

    status_code: the HTTP status of the answer.
    operation: a human-readable description of the operation
        we're performing (to produce log messages).

    raise (RejectedError): if the ranking refused the request (4xx).
    raise (CannotSendError): if the ranking failed to handle it (5xx).

    """
    if 400 <= status_code < 600:
        msg = "Status %s while %s." % (status_code, operation)
        logger.warning(msg)
        if status_code < 500:
            raise RejectedError(msg)
        raise CannotSendError(msg)


def safe_put_data(ranking: str, resource: str, data: dict, operation: str):
    """Send some data to ranking using a PUT request.

    ranking: the URL of ranking server.
    resource: the relative path of the entity.
    data: the data to JSON-encode and send.
    operation: a human-readable description of the operation
        we're performing (to produce log messages).

    raise (UnencodableError): if the data cannot be encoded as JSON.
    raise (RejectedError): if the ranking refuses the data.
    raise (CannotSendError): in case of communication or server errors.

    """
    try:
        body = json.dumps(data)
    except (TypeError, ValueError) as error:
        msg = "Cannot encode the data as JSON while %s: %s." % (
            operation, error)
        logger.warning(msg)
        raise UnencodableError(msg)
    try:
        url = urljoin(ranking, resource)
        # XXX With requests-1.2 auth is automatically extracted from
        # the URL: there is no need for this.
        auth = urlsplit(url)
        res = requests.put(url, body,
                           auth=(auth.username, auth.password),
                           headers={'content-type': 'application/json'},
                           verify=config.proxy_service.https_certfile,
                           timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    except requests.exceptions.RequestException as error:
        msg = "%s while %s: %s." % (type(error).__name__, operation, error)
        logger.warning(msg)
        raise CannotSendError(msg)
    _check_status(res.status_code, operation)


def safe_delete_data(ranking: str, resource: str, operation: str):
    """Delete a whole resource list from ranking using a DELETE request.

    ranking: the URL of ranking server.
    resource: the relative path of the entity list.
    operation: a human-readable description of the operation
        we're performing (to produce log messages).

    raise (RejectedError): if the ranking refuses the request.
    raise (CannotSendError): in case of communication or server errors.

    """
    try:
        url = urljoin(ranking, resource)
        auth = urlsplit(url)
        res = requests.delete(url,
                              auth=(auth.username, auth.password),
                              verify=config.proxy_service.https_certfile,
                              timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    except requests.exceptions.RequestException as error:
        msg = "%s while %s: %s." % (type(error).__name__, operation, error)
        logger.warning(msg)
        raise CannotSendError(msg)
    _check_status(res.status_code, operation)


def safe_url(url: str) -> str:
    """Return a sanitized URL without sensitive information.

    url: the URL to sanitize.
    return: sanitized URL.

    """
    parts = urlsplit(url)
    netloc = parts.hostname if parts.hostname is not None else ""
    netloc += ":%d" % parts.port if parts.port is not None else ""
    return parts._replace(netloc=netloc).geturl()


class ProxyOperation(QueueItem):

    def __init__(self, type_: int, data: dict, group: str | None = None):
        """Create an operation for the ranking namespace of group.

        type_: one of ProxyExecutor's *_TYPE constants.
        data: the entities to send, by id (empty for a reset, the
            settings for a visibility).
        group: the ranking group namespace, or None for the root.

        """
        self.type_ = type_
        self.data = data
        self.group = group

    def __str__(self):
        return "sending data of type %s to ranking %s" % (
            self.type_, self.group if self.group is not None else "(root)")

    def to_dict(self):
        return {"type": self.type_,
                "data": self.data,
                "group": self.group}


class ProxyExecutor(AsyncExecutor[ProxyOperation]):
    """An executor that sends data to one ranking.

    Its inherited run() method is spawned as an asyncio task and drives
    the object for as long as the service is alive.

    It maintains a queue of data to send. At each "round" the queue is
    emptied (i.e. all jobs are fetched) and the data is then "combined"
    to minimize the number of actual HTTP requests: they'll be at most
    one per entity type.

    What the ranking cannot take because it is unreachable or failing is
    put back in the queue for a later round; what it refuses is dropped.
    Each namespace waits on its own before it is tried again, so one
    that keeps failing doesn't slow down the others.

    Each entity type is identified by a integral class-level constant.

    """

    # We use a single queue for all the data we have to send to the
    # ranking so we need to distingush the type of each item.
    CONTEST_TYPE = 0
    TASK_TYPE = 1
    TEAM_TYPE = 2
    USER_TYPE = 3
    SUBMISSION_TYPE = 4
    SUBCHANGE_TYPE = 5

    # The resource paths for the different entity types, relative to
    # the self.ranking URL.
    RESOURCE_PATHS = [
        "contests",
        "tasks",
        "teams",
        "users",
        "submissions",
        "subchanges"]

    # How many different entity types we know about.
    TYPE_COUNT = len(RESOURCE_PATHS)

    # Pseudo-type of an operation that empties a ranking namespace.
    # Deleting contests and users is enough: RWS cascades to tasks,
    # submissions and subchanges. Teams are kept because RWS seeds them
    # at startup; leftover teams are harmless.
    RESET_TYPE = TYPE_COUNT
    RESET_RESOURCE_PATHS = ["contests", "users"]

    # Pseudo-type of an operation that sends the visibility settings of
    # a ranking group namespace (MC-2): {"hide_at": int | None,
    # "show_at": int | None, "freeze_at": int | None,
    # "unfreeze_at": int | None, "staff_password": str | None} where
    # the times are Unix seconds: the starts (hide_at, freeze_at) are
    # rounded down and the ends (show_at, unfreeze_at) up, so an end
    # is always after its start. Sent after resets and before any data,
    # so a hidden namespace never exposes data, even briefly.
    VISIBILITY_TYPE = TYPE_COUNT + 1

    # How long a namespace waits after its data could not be pushed to
    # the ranking before it is tried again: MIN_RETRY_WAIT seconds the
    # first time, then twice as long at each failure that follows, up
    # to MAX_RETRY_WAIT. Pushing its data successfully starts it over.
    MIN_RETRY_WAIT = 1.0
    MAX_RETRY_WAIT = 60.0

    # How many seconds the operations queued while some namespace waits
    # to be tried again gather before a round takes them. A round takes
    # the whole queue out and puts back what waits, which costs time in
    # proportion to that backlog: one round for each operation of a
    # burst would cost that much for each of them.
    BATCH_WINDOW = 0.2

    def __init__(self, ranking: str):
        """Create a proxy for the ranking at the given URL.

        ranking: a complete URL (containing protocol, username,
            password, hostname, port and prefix) where a ranking is
            supposed to listen.

        """
        super().__init__(batch_executions=True)

        self._ranking = ranking
        self._visible_ranking = safe_url(ranking)

        # For each namespace whose data the ranking could not take: how
        # long it waits if its next attempt fails too, and when (on the
        # monotonic clock) it may be tried again.
        self._retry_waits: dict[str | None, float] = dict()
        self._retry_after: dict[str | None, float] = dict()
        # Set when operations are queued for a namespace that may be
        # tried now, to end a wait for another namespace early: their
        # data must not wait for it.
        self._new_work = asyncio.Event()
        # The timer that sets _new_work at the end of a batch window.
        self._batch_window: asyncio.TimerHandle | None = None

        # The namespaces whose visibility settings the ranking refused
        # the last time they were sent. Their data is dropped until it
        # takes new ones, as it could show the data while they should
        # be hidden. Only _execute_sync uses it, one batch at a time.
        self._rejected_visibility: set[str | None] = set()

    @staticmethod
    def _prefix(group: str | None) -> str:
        """Return the resource path prefix of a ranking namespace."""
        return "" if group is None else "%s/" % group

    def _is_due(self, group: str | None, now: float) -> bool:
        """Return whether namespace group may be tried at time now.

        group: the namespace (None is the root).
        now: a time on the monotonic clock.

        """
        return self._retry_after.get(group, now) <= now

    def enqueue(
        self,
        item: ProxyOperation,
        priority: int | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        """Queue an operation, ending the wait of a round if it is due.

        An operation for a namespace that still waits to be tried again
        doesn't end it: a round takes the whole queue out and puts back
        what waits, so a round for each operation queued meanwhile would
        take time quadratic in the backlog. For the same reason, while
        some namespace waits, the wait ends BATCH_WINDOW seconds after
        the operation, so that a burst goes out in a few rounds.

        See AsyncExecutor.enqueue.

        """
        queued = super().enqueue(item, priority, timestamp)
        if queued and self._is_due(item.group, monotonic()):
            if not self._retry_after:
                self._new_work.set()
            elif self._batch_window is None:
                self._batch_window = asyncio.get_running_loop().call_later(
                    self.BATCH_WINDOW, self._end_batch_window)
        return queued

    def _end_batch_window(self):
        """End the wait of a round for the operations queued meanwhile."""
        self._batch_window = None
        self._new_work.set()

    async def execute(self, entries: list[QueueEntry[ProxyOperation]]):
        """Send one batch of operations already fetched from the queue.

        Combine the given entries and send them to the target ranking
        with a single synchronous batch call run in a worker thread
        (via run_in_executor), except the ones of the namespaces that
        still wait to be tried again after a failure. Those, and the
        entries whose data the ranking could not take, are put back in
        the queue as they were (same priority and timestamp, so they
        keep their place in the order).

        Each namespace waits on its own: MIN_RETRY_WAIT seconds after
        a failure, twice as long at each failure in a row up to
        MAX_RETRY_WAIT, and it starts over once the ranking takes all
        of its data. Before returning, we sleep until the first
        namespace is due, so the caller's next round doesn't retry
        immediately; but at most MIN_RETRY_WAIT seconds if another
        namespace got its data in this round, and new operations end
        the sleep early: a failing namespace must not hold back the
        data of the others.

        entries: entries containing the operations to perform.

        """
        # Operations queued from now on are new to this round: this
        # round takes those of a batch window already.
        self._new_work.clear()
        if self._batch_window is not None:
            self._batch_window.cancel()
            self._batch_window = None
        now = monotonic()
        ready = [entry for entry in entries
                 if self._is_due(entry.item.group, now)]
        unsent: list[QueueEntry[ProxyOperation]] = []
        if ready:
            loop = asyncio.get_running_loop()
            unsent = await loop.run_in_executor(
                None, self._execute_sync, ready)

        now = monotonic()
        failed = {entry.item.group for entry in unsent}
        for group in failed:
            wait = self._retry_waits.get(group, self.MIN_RETRY_WAIT)
            self._retry_waits[group] = min(2 * wait, self.MAX_RETRY_WAIT)
            self._retry_after[group] = now + wait
            logger.warning(
                "Could not send %d operation(s) of group %s to ranking %s, "
                "trying again in %g seconds.",
                sum(entry.item.group == group for entry in unsent),
                group if group is not None else "(root)",
                self._visible_ranking, wait)
        progressed = {entry.item.group for entry in ready} - failed
        for group in progressed:
            self._retry_waits.pop(group, None)
            self._retry_after.pop(group, None)

        ready_ids = {id(entry) for entry in ready}
        put_back = {id(entry) for entry in unsent}
        put_back.update(
            id(entry) for entry in entries if id(entry) not in ready_ids)
        waiting = [entry for entry in entries if id(entry) in put_back]
        if not waiting:
            return
        for entry in waiting:
            # Not self.enqueue: this is not new work.
            super().enqueue(entry.item, entry.priority, entry.timestamp)

        wait = min(self._retry_after[entry.item.group]
                   for entry in waiting) - now
        if progressed:
            wait = min(wait, self.MIN_RETRY_WAIT)
        await self._wait_for_retry(wait)

    async def _wait_for_retry(self, delay: float):
        """Sleep for delay seconds, or until operations are queued.

        delay: how many seconds to sleep at most.

        """
        if delay <= 0 or self._new_work.is_set():
            return
        sleeping = asyncio.ensure_future(asyncio.sleep(delay))
        queueing = asyncio.ensure_future(self._new_work.wait())
        try:
            await asyncio.wait((sleeping, queueing),
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeping.cancel()
            queueing.cancel()

    def _execute_sync(
        self, entries: list[QueueEntry[ProxyOperation]]
    ) -> list[QueueEntry[ProxyOperation]]:
        """Send the given batch of operations, synchronously.

        Runs inside loop.run_in_executor -- the actual HTTP requests
        (via the synchronous requests library) happen here; the caller
        is responsible for putting the unsent entries back in the
        queue and for the wait before trying again, which must happen
        on the event loop (asyncio.sleep), not in this thread.

        Namespaces don't depend on each other, so what goes wrong in
        one of them doesn't stop the others. Data refused by the
        ranking is dropped, as sending it again would get it refused
        again, and the following entity types are sent anyway. When
        data cannot be delivered instead (communication or server
        error), the entity types that follow it in the same namespace
        are held back too: the ranking would refuse them if they refer
        to what did not get there (a task, to its contest), and they
        would be lost.

        The visibility settings of a namespace go after its reset and
        before its data, so the ranking knows whether to hide the data
        before it gets it. If the ranking refuses the settings, the
        data of the namespace is dropped, in this batch and in the
        following ones, until the ranking takes new settings for it:
        it could show the data while the namespace should be hidden.
        Resets still go through, as they only delete.

        entries: entries containing the operations to perform.

        return: the entries whose data was not sent and has to be
            sent again, in their original order.

        """
        # The entries to send in each namespace (None is the root), by
        # entity type, in arrival order.
        pending: dict[str | None, list[list[QueueEntry]]] = dict()
        # The entry emptying each namespace before sending data (the
        # last one, if there is more than one).
        resets: dict[str | None, QueueEntry] = dict()
        # The entry with the visibility settings of each namespace (the
        # last one, if there is more than one).
        visibilities: dict[str | None, QueueEntry] = dict()

        for entry in entries:
            item = entry.item
            if item.type_ == self.RESET_TYPE:
                # Data queued before the reset is obsolete.
                pending.pop(item.group, None)
                resets[item.group] = entry
            elif item.type_ == self.VISIBILITY_TYPE:
                visibilities[item.group] = entry
            else:
                group_entries = pending.setdefault(
                    item.group, list(list() for i in range(self.TYPE_COUNT)))
                group_entries[item.type_].append(entry)

        # The entries to send again (by id, as they are not hashable).
        unsent: set[int] = set()
        # The namespaces where something could not be delivered.
        stalled: set[str | None] = set()

        for group, reset in resets.items():
            outcome = self._send(group, self.RESET_TYPE, dict())
            if outcome is SendOutcome.UNSENT:
                unsent.add(id(reset))
                stalled.add(group)

        for group, visibility in visibilities.items():
            if group not in stalled:
                outcome = self._send(
                    group, self.VISIBILITY_TYPE, visibility.item.data)
                self._track_visibility(group, outcome)
                if outcome is not SendOutcome.UNSENT:
                    continue
                stalled.add(group)
            unsent.add(id(visibility))

        for group, group_entries in pending.items():
            # Drop the data of a namespace whose settings were refused.
            # If it is stalled, the data waits instead: new settings may
            # be among what is sent again, and the data would follow them.
            if group in self._rejected_visibility and group not in stalled:
                logger.debug(
                    "Dropping %d operation(s) of group %s: ranking %s "
                    "rejected its visibility.",
                    sum(len(type_entries) for type_entries in group_entries),
                    group, self._visible_ranking)
                continue
            for type_, type_entries in enumerate(group_entries):
                data: dict = dict()
                for entry in type_entries:
                    data.update(entry.item.data)
                # Nothing to send, nor to send again.
                if len(data) == 0:
                    continue
                if group not in stalled:
                    outcome = self._send(group, type_, data)
                    if outcome is not SendOutcome.UNSENT:
                        continue
                    stalled.add(group)
                unsent.update(id(entry) for entry in type_entries)

        return [entry for entry in entries if id(entry) in unsent]

    def _track_visibility(
        self, group: str | None, outcome: SendOutcome
    ) -> None:
        """Remember whether the ranking refused the settings of group.

        Tell the operator when that changes. Regenerate is only
        suggested once the settings get there: before, it would empty
        the namespace and send nothing back.

        Runs inside loop.run_in_executor.

        group: the namespace whose visibility settings were sent.
        outcome: what became of them.

        """
        if outcome is SendOutcome.REJECTED \
                and group not in self._rejected_visibility:
            self._rejected_visibility.add(group)
            logger.warning(
                "Ranking %s rejected the visibility of group %s, so its "
                "data is held back. Check that RWS is up to date (it must "
                "support /%s/visibility), then save the group again in "
                "AWS.", self._visible_ranking, group, group)
        elif outcome is SendOutcome.SENT \
                and group in self._rejected_visibility:
            self._rejected_visibility.discard(group)
            logger.warning(
                "Ranking %s accepted the visibility of group %s: its data "
                "is sent again, but not the data held back meanwhile. Use "
                "Regenerate for this group in AWS (Ranking groups) to send "
                "that too.", self._visible_ranking, group)

    def _send(self, group: str | None, type_: int, data: dict) -> SendOutcome:
        """Send the entities of one type to a namespace of the ranking.

        Runs inside loop.run_in_executor.

        group: the namespace (None is the root).
        type_: one of the *_TYPE constants. For RESET_TYPE the
            namespace is emptied instead, and data is ignored. For
            VISIBILITY_TYPE data are the settings of the namespace.
        data: the entities to send, by id.

        return: SENT if the ranking took the data; REJECTED if it
            refused it (which is final); UNSENT if the data did not
            reach the ranking, or it failed to handle it, and it has
            to be sent again.

        """
        prefix = self._prefix(group)
        try:
            if type_ == self.RESET_TYPE:
                what = "the reset"
                for name in self.RESET_RESOURCE_PATHS:
                    operation = "deleting %s from ranking %s%s" % (
                        name, self._visible_ranking, prefix)
                    logger.debug(operation.capitalize())
                    safe_delete_data(
                        self._ranking, "%s%s/" % (prefix, name), operation)
            elif type_ == self.VISIBILITY_TYPE:
                operation = "sending visibility to ranking %s%s" % (
                    self._visible_ranking, prefix)
                logger.debug(operation.capitalize())
                safe_put_data(self._ranking, "%svisibility" % prefix,
                              data, operation)
            else:
                # We abuse the resource path as the English (plural)
                # name for the entity type.
                name = self.RESOURCE_PATHS[type_]
                what = "the " + name
                operation = "sending %s to ranking %s%s" % (
                    name, self._visible_ranking, prefix)
                logger.debug(operation.capitalize())
                safe_put_data(
                    self._ranking, "%s%s/" % (prefix, name), data, operation)
        except RejectedError as error:
            # The error has already been logged: say what to do about it
            # (for the visibility, _track_visibility does).
            if type_ == self.VISIBILITY_TYPE:
                return SendOutcome.REJECTED
            group_name = group if group is not None else "(root)"
            if isinstance(error, UnencodableError):
                # Regenerate alone would build the same data again.
                logger.warning(
                    "%s of group %s cannot be encoded for ranking %s, and "
                    "would fail the same way again. Fix the data, then use "
                    "Regenerate for this group in AWS (Ranking groups) to "
                    "send it.", what.capitalize(), group_name,
                    self._visible_ranking)
            else:
                logger.warning(
                    "Ranking %s rejected %s of group %s. It will not be sent "
                    "again: use Regenerate for this group in AWS (Ranking "
                    "groups) to send its data again.", self._visible_ranking,
                    what, group_name)
            return SendOutcome.REJECTED
        except CannotSendError:
            # A log message has already been produced.
            return SendOutcome.UNSENT
        except Exception:
            # Whoa! That's unexpected!
            logger.error("Unexpected error.", exc_info=True)
            return SendOutcome.UNSENT
        return SendOutcome.SENT


class ProxyService(AsyncTriggeredService[ProxyOperation, ProxyExecutor]):
    """Maintain the information held by rankings up-to-date.

    Discover (by receiving notifications and by periodically sweeping
    over the database) when relevant data changes happen and forward
    them to the rankings by putting them in the queues of the proxies.

    The "entry points" are submission_score, submission_tokened,
    dataset_updated (and search_operations_not_done, from triggered
    service, which is also periodically executed). These methods fetch
    objects from the database, check their validity (existence,
    non-hiddenness, etc.)  and status and, if needed, put call
    initialize, send_score and send_token that construct the data to
    send to rankings and put it in the queues of all proxies.

    """

    def __init__(self, shard: int, contest_id: int | None = None):
        """Start the service with the given parameters.

        Create an instance of the ProxyService and make it listen on
        the address corresponding to the given shard.

        shard: the shard of the service, i.e. this instance
            corresponds to the shard-th entry in the list of addresses
            (hostname/port pairs) for this kind of service in the
            configuration file.
        contest_id: the ID of the only contest to send to the root of
            the rankings (legacy mode), or None to send every contest
            that has a ranking group to its group's namespace (group
            mode).

        """
        super().__init__(shard)

        self.contest_id = contest_id

        # Store what data we already sent to rankings, to avoid
        # sending it twice.
        self.scores_sent_to_rankings: set[int] = set()
        self.tokens_sent_to_rankings: set[int] = set()

        # IDs of the contests whose data could not be built last time
        # we tried (e.g., a task with invalid score type parameters).
        # Their submissions are held back, as rankings would reject
        # them (and everything sent along with them) while they don't
        # know their tasks.
        self._broken_contests: set[int] = set()

        # Last known mapping from ranking group name to the IDs of its
        # contests (always empty in legacy mode), used by reinitialize
        # to find groups that lost contests and must be reset.
        self._group_contests: dict[str, set[int]] = dict()
        with SessionGen() as session:
            self._group_contests = self._compute_group_contests(session)

        # Create one executor for each ranking.
        self.rankings = list()
        for ranking in config.proxy_service.rankings:
            self.add_executor(ProxyExecutor(ranking))

        # Enqueue the dispatch of some initial data to rankings. Needs
        # to be done before the sweeper is started, as otherwise RWS
        # might receive submissions before the corresponding task, for
        # example.
        self.initialize()

        self.start_sweeper(347.0)

    def _threadsafe_enqueue(
        self,
        operation: ProxyOperation,
        priority: int | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Enqueue an operation, safely from any thread.

        self.enqueue() isn't safe to call from a run_in_executor worker
        thread -- it eventually touches asyncio.Event.set(), which (like
        every asyncio primitive) must only be touched from the event loop
        thread. Route the actual enqueue() call through
        loop.call_soon_threadsafe() when a loop is running; at __init__
        time (before run() starts the loop, self._loop is still None) no
        other coroutine can be waiting on the queue yet, so a direct call
        is safe -- this mirrors AsyncService._call_when_running's dual-mode
        dispatch and AsyncLogServiceHandler._send's "loop is None" guard.

        Note that an exception raised inside the scheduled enqueue()
        call is delivered to the event loop's exception handler, not
        back to the thread that called _threadsafe_enqueue.

        operation: the operation to enqueue.
        priority: the priority, or None to use default.
        timestamp: the timestamp of the first request for the
            operation, or None to use now.

        """
        if self._loop is None:
            self.enqueue(operation, priority, timestamp)
        else:
            self._loop.call_soon_threadsafe(
                self.enqueue, operation, priority, timestamp)

    def _is_sent(self, contest: Contest | None) -> bool:
        """Return whether the data of contest goes to a ranking.

        contest: a contest, or None for what is in no contest (a task
            that is not assigned to one, and the submissions to it).

        """
        if contest is None:
            return False
        if self.contest_id is not None:
            return contest.id == self.contest_id
        return contest.ranking_group is not None

    def _group_of(self, contest: Contest) -> str | None:
        """Return the namespace contest is sent to (None is the root).

        contest: a contest for which _is_sent is True.

        """
        if self.contest_id is not None:
            return None
        return contest.ranking_group.name

    def _contests_to_send(self, session: Session) -> list[Contest]:
        """Return the contests whose data goes to the rankings.

        raise (KeyError): in legacy mode, if the contest does not exist.

        """
        if self.contest_id is not None:
            contest = Contest.get_from_id(self.contest_id, session)
            if contest is None:
                logger.error("Received request for unexistent contest "
                             "id %s.", self.contest_id)
                raise KeyError("Contest not found.")
            return [contest]
        return session.execute(
            select(Contest)
            .filter(Contest.ranking_group_id.isnot(None))
            .order_by(Contest.id)
        ).scalars().all()

    def _compute_group_contests(self, session: Session) -> dict[str, set[int]]:
        """Return the current mapping from group name to contest IDs."""
        mapping: dict[str, set[int]] = dict()
        if self.contest_id is None:
            for contest in self._contests_to_send(session):
                mapping.setdefault(
                    contest.ranking_group.name, set()).add(contest.id)
        return mapping

    def _enqueue_submissions(
        self, session: Session, contest: Contest, only_missing: bool
    ) -> int:
        """Enqueue the scores and tokens of the submissions of contest.

        only_missing: if True, skip what was already sent.

        return: the number of operations enqueued.

        """
        counter = 0
        submissions = session.execute(
            get_submissions(session, contest_id=contest.id)
            .filter(not_(Participation.hidden))
            .filter(Submission.official)
        ).scalars().all()

        for submission in submissions:
            # The submission result can be None if the dataset has
            # been just made live.
            sr = submission.get_result()
            if sr is None:
                continue

            if sr.scored() and not (
                    only_missing and
                    submission.id in self.scores_sent_to_rankings):
                for operation in self.operations_for_score(submission):
                    self._threadsafe_enqueue(operation)
                    counter += 1

            if submission.tokened() and not (
                    only_missing and
                    submission.id in self.tokens_sent_to_rankings):
                for operation in self.operations_for_token(submission):
                    self._threadsafe_enqueue(operation)
                    counter += 1

        return counter

    async def _missing_operations(self) -> int:
        """Return the number of operations enqueued for missing data.

        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._missing_operations_sync)

    def _missing_operations_sync(self) -> int:
        """Return the number of operations enqueued.

        Runs inside loop.run_in_executor.

        Besides what was not sent yet, try again the contests whose
        data could not be built: whatever broke them may be over, and
        they should not wait for someone to reinitialize the rankings.

        In group mode, send again the visibility settings of every
        ranking group first (they are not counted). Nothing else would
        repair a ranking that lost them, or got a reinitialize that
        never arrived, until someone saves the group in AWS: a hidden
        group could stay public. It also lets a group whose settings
        the ranking refused be sent its data again once it takes them.

        """
        counter = 0
        with SessionGen() as session:
            self._enqueue_visibility(session)
            for contest in self._contests_to_send(session):
                only_missing = True
                if contest.id in self._broken_contests:
                    counter += self._enqueue_contest_data(contest)
                    if contest.id in self._broken_contests:
                        continue
                    # Its scores were held back, and the ranking may
                    # have been emptied since it broke: send them all.
                    only_missing = False
                counter += self._enqueue_submissions(
                    session, contest, only_missing=only_missing)
        return counter

    def initialize(self):
        """Send basic data to all the rankings.

        It's data that's supposed to be sent before the contest, that's
        needed to understand what we're talking about when we send
        submissions: contest, users, tasks.

        No support for teams, flags and faces.

        In group mode, the visibility settings of the ranking groups
        go first.

        """
        logger.info("Initializing rankings.")

        with SessionGen() as session:
            self._enqueue_visibility(session)
            for contest in self._contests_to_send(session):
                self._enqueue_contest_data(contest)

    def _enqueue_visibility(self, session: Session,
                            group: str | None = None) -> None:
        """Enqueue the visibility settings of the ranking groups.

        Only in group mode: legacy mode has no groups.

        session: the session to read the groups with.
        group: only this group, or None for every group.

        """
        if self.contest_id is not None:
            return
        query = select(RankingGroup)
        if group is not None:
            query = query.filter(RankingGroup.name == group)
        for ranking_group in session.execute(query).scalars().all():
            self._threadsafe_enqueue(ProxyOperation(
                ProxyExecutor.VISIBILITY_TYPE,
                {"hide_at": _unix_start(ranking_group.hide_at),
                 "show_at": _unix_end(ranking_group.show_at),
                 "freeze_at": _unix_start(ranking_group.freeze_at),
                 "unfreeze_at": _unix_end(ranking_group.unfreeze_at),
                 "staff_password": ranking_group.staff_password},
                ranking_group.name))

    def _enqueue_contest_data(self, contest: Contest) -> int:
        """Enqueue the contest, its users, teams and tasks.

        If the data cannot be built, log the error and enqueue nothing
        for the contest, without affecting the other contests. The
        traceback is only logged when the contest breaks: the sweeper
        tries it again every time, and it would fill the logs.

        return: the number of operations enqueued.

        """
        try:
            operations = self._operations_for_contest(contest)
        except Exception as error:
            if contest.id in self._broken_contests:
                logger.warning("Contest %d (%s) still cannot be sent to "
                               "the rankings: %s: %s", contest.id,
                               contest.name, type(error).__name__, error)
            else:
                logger.exception("Cannot build the ranking data of contest "
                                 "%d (%s), not sending it until fixed: the "
                                 "sweeper will try again.",
                                 contest.id, contest.name)
                self._broken_contests.add(contest.id)
            return 0
        for operation in operations:
            self._threadsafe_enqueue(operation)
        # Only now the scores of the contest may be sent: another thread
        # could otherwise queue them before the contest and its tasks,
        # and rankings refuse submissions of tasks they do not know.
        if contest.id in self._broken_contests:
            logger.info("Contest %d (%s) can be sent to the rankings again.",
                        contest.id, contest.name)
            self._broken_contests.discard(contest.id)
        return len(operations)

    def _operations_for_contest(
        self, contest: Contest
    ) -> list[ProxyOperation]:
        """Return the operations sending the contest, its users, teams
        and tasks.

        """
        group = self._group_of(contest)
        contest_id = encode_id(contest.name)
        contest_data = {
            "name": contest.description,
            "begin": int(make_timestamp(contest.main_group.start)),
            "end": int(make_timestamp(contest.main_group.stop)),
            "score_precision": contest.score_precision}

        users = dict()
        teams = dict()

        for participation in contest.participations:
            user = participation.user
            team = participation.team
            if not participation.hidden:
                users[encode_id(user.username)] = {
                    "f_name": user.first_name,
                    "l_name": user.last_name,
                    "team": encode_id(team.code)
                            if team is not None else None,
                }
                if team is not None:
                    teams[encode_id(team.code)] = {
                        "name": team.name
                    }

        tasks = dict()

        for task in contest.tasks:
            score_type = task.active_dataset.score_type_object
            tasks[encode_id(task.name)] = {
                "short_name": task.name,
                "name": task.title,
                "contest": encode_id(contest.name),
                "order": task.num,
                "max_score": score_type.max_score,
                "extra_headers": score_type.ranking_headers,
                "score_precision": task.score_precision,
                "score_mode": task.score_mode,
            }

        return [
            ProxyOperation(ProxyExecutor.CONTEST_TYPE,
                           {contest_id: contest_data}, group),
            ProxyOperation(ProxyExecutor.TEAM_TYPE, teams, group),
            ProxyOperation(ProxyExecutor.USER_TYPE, users, group),
            ProxyOperation(ProxyExecutor.TASK_TYPE, tasks, group)]

    def operations_for_score(self, submission: Submission):
        """Send the score for the given submission to all rankings.

        Put the submission and its score subchange in all the proxy
        queues for them to be sent to rankings.

        """
        if submission.task.contest_id in self._broken_contests:
            return []
        group = self._group_of(submission.task.contest)
        submission_result = submission.get_result()

        # Data to send to remote rankings.
        submission_id = "%d" % submission.id
        submission_data = {
            "user": encode_id(submission.participation.user.username),
            "task": encode_id(submission.task.name),
            "time": int(make_timestamp(submission.timestamp))}

        subchange_id = "%d%ss" % (make_timestamp(submission.timestamp),
                                  submission_id)
        subchange_data = {
            "submission": submission_id,
            "time": int(make_timestamp(submission.timestamp))}

        # This check is probably useless.
        if submission_result is not None and submission_result.scored():
            subchange_data["score"] = submission_result.score
            subchange_data["extra"] = submission_result.ranking_score_details

        self.scores_sent_to_rankings.add(submission.id)

        return [
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {submission_id: submission_data}, group),
            ProxyOperation(ProxyExecutor.SUBCHANGE_TYPE,
                           {subchange_id: subchange_data}, group)]

    def operations_for_token(self, submission: Submission):
        """Send the token for the given submission to all rankings.

        Put the submission and its token subchange in all the proxy
        queues for them to be sent to rankings.

        """
        if submission.task.contest_id in self._broken_contests:
            return []
        group = self._group_of(submission.task.contest)
        # Data to send to remote rankings.
        submission_id = "%d" % submission.id
        submission_data = {
            "user": encode_id(submission.participation.user.username),
            "task": encode_id(submission.task.name),
            "time": int(make_timestamp(submission.timestamp))}

        subchange_id = "%d%st" % (make_timestamp(submission.token.timestamp),
                                  submission_id)
        subchange_data = {
            "submission": submission_id,
            "time": int(make_timestamp(submission.token.timestamp)),
            "token": True}

        self.tokens_sent_to_rankings.add(submission.id)

        return [
            ProxyOperation(ProxyExecutor.SUBMISSION_TYPE,
                           {submission_id: submission_data}, group),
            ProxyOperation(ProxyExecutor.SUBCHANGE_TYPE,
                           {subchange_id: subchange_data}, group)]

    @rpc_method
    async def reinitialize(self):
        """Repeat the initialization procedure for all rankings.

        This method is usually called via RPC when someone knows that
        some basic data (i.e. contest, tasks or users) changed and
        rankings need to be updated. Ranking groups that lost a contest
        (or were deleted) are emptied first and then sent again in full,
        since rankings only merge the data they receive.

        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._reinitialize_sync)

    def _reinitialize_sync(self):
        """Do the work of reinitialize(), synchronously.

        Runs inside loop.run_in_executor. Calls self.initialize() (not
        the async reinitialize() -- that would try to await from inside
        this already-backgrounded thread) directly, since initialize()
        is itself a plain synchronous method safe to call from here.

        """
        logger.info("Reinitializing rankings.")
        with SessionGen() as session:
            new_mapping = self._compute_group_contests(session)
        lost = sorted(
            group for group, contest_ids in self._group_contests.items()
            if not contest_ids <= new_mapping.get(group, set()))
        # Contests new to their group: their submissions may have been
        # sent already, but to another namespace (or not at all).
        gained = {
            contest_id for group, contest_ids in new_mapping.items()
            for contest_id in
            contest_ids - self._group_contests.get(group, set())}
        previous_mapping = self._group_contests
        self._group_contests = new_mapping

        try:
            for group in lost:
                logger.info("Ranking group %s lost contests, resetting it.",
                            group)
                self._threadsafe_enqueue(
                    ProxyOperation(ProxyExecutor.RESET_TYPE, {}, group))

            broken_before = set(self._broken_contests)
            self.initialize()
            # Contests fixed since the last time: their submissions were
            # held back, so send them in full.
            gained |= broken_before - self._broken_contests

            if lost or gained:
                with SessionGen() as session:
                    for contest in self._contests_to_send(session):
                        if self._group_of(contest) in lost or \
                                contest.id in gained:
                            self._enqueue_submissions(
                                session, contest, only_missing=False)
        except Exception:
            # Nobody else logs it: the RPC server doesn't, and the
            # caller (AWS) stops waiting for the answer.
            if lost:
                logger.exception(
                    "Reinitializing the rankings failed. The ranking "
                    "groups that lost contests (%s) were being reset and "
                    "may be left empty: once the cause is fixed, use "
                    "Regenerate for them in AWS (Ranking groups).",
                    ", ".join(lost))
            else:
                logger.exception("Reinitializing the rankings failed.")
            # The groups that lost contests may be empty by now, and
            # nothing has filled them again. Go back to the mapping we
            # had, so that the next reinitialize sees them as lost and
            # does it all again (the sweeper would not: it only sends
            # what it thinks is missing).
            self._group_contests = previous_mapping
            raise

    @rpc_method
    async def regenerate_ranking(self, group: str | None = None):
        """Empty a ranking namespace and send all of its data again.

        Usually called by AdminWebServer when an admin asks to repair a
        ranking that got out of sync with the database.

        group: the ranking group whose namespace to regenerate, or None
            for the root namespace.

        raise (ValueError): if group is not a valid group name.

        """
        if group is not None and (
                not isinstance(group, str) or not is_valid_group_name(group)):
            logger.error("Received request to regenerate invalid ranking "
                         "group %r.", group)
            raise ValueError("Invalid ranking group name.")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._regenerate_ranking_sync, group)

    def _regenerate_ranking_sync(self, group: str | None):
        """Do the work of regenerate_ranking(), synchronously.

        Runs inside loop.run_in_executor. group has already been
        validated by the caller.

        """
        logger.info("Regenerating ranking %s.",
                    group if group is not None else "(root)")
        self._threadsafe_enqueue(
            ProxyOperation(ProxyExecutor.RESET_TYPE, {}, group))
        with SessionGen() as session:
            if group is not None:
                self._enqueue_visibility(session, group)
            for contest in self._contests_to_send(session):
                if self._group_of(contest) == group:
                    self._enqueue_contest_data(contest)
                    self._enqueue_submissions(
                        session, contest, only_missing=False)

    @rpc_method
    async def submission_scored(self, submission_id: int):
        """Notice that a submission has been scored.

        Usually called by ScoringService when it's done with scoring a
        submission result. Since we don't trust anyone we verify that,
        and then send data about the score to the rankings.

        submission_id: the id of the submission that changed.

        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._submission_scored_sync, submission_id)

    def _submission_scored_sync(self, submission_id: int):
        """Do the work of submission_scored(), synchronously.

        Runs inside loop.run_in_executor.

        """
        with SessionGen() as session:
            submission = Submission.get_from_id(submission_id, session)

            if submission is None:
                logger.error("[submission_scored] Received score request for "
                             "unexistent submission id %s.", submission_id)
                raise KeyError("Submission not found.")

            # The submission's contest is not sent to any ranking.
            if not self._is_sent(submission.task.contest):
                logger.debug("Ignoring submission %d of contest %s "
                             "(not sent to any ranking).",
                             submission.id, submission.task.contest_id)
                return

            if submission.participation.hidden:
                logger.info("[submission_scored] Score for submission %d "
                            "not sent because the participation is hidden.",
                            submission_id)
                return

            if not submission.official:
                logger.info("[submission_scored] Score for submission %d "
                            "not sent because the submission is not official.",
                            submission_id)
                return

            # Update RWS.
            for operation in self.operations_for_score(submission):
                self._threadsafe_enqueue(operation)

    @rpc_method
    async def submission_tokened(self, submission_id: int):
        """Notice that a submission has been tokened.

        Usually called by ContestWebServer when it's processing a token
        request of an user. Since we don't trust anyone we verify that,
        and then send data about the token to the rankings.

        submission_id: the id of the submission that changed.

        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._submission_tokened_sync, submission_id)

    def _submission_tokened_sync(self, submission_id: int):
        """Do the work of submission_tokened(), synchronously.

        Runs inside loop.run_in_executor.

        """
        with SessionGen() as session:
            submission = Submission.get_from_id(submission_id, session)

            if submission is None:
                logger.error("[submission_tokened] Received token request for "
                             "unexistent submission id %s.", submission_id)
                raise KeyError("Submission not found.")

            # The submission's contest is not sent to any ranking.
            if not self._is_sent(submission.task.contest):
                logger.debug("Ignoring submission %d of contest %s "
                             "(not sent to any ranking).",
                             submission.id, submission.task.contest_id)
                return

            if submission.participation.hidden:
                logger.info("[submission_tokened] Token for submission %d "
                            "not sent because participation is hidden.",
                            submission_id)
                return

            if not submission.official:
                logger.info("[submission_tokened] Token for submission %d "
                            "not sent because the submission is not official.",
                            submission_id)
                return

            # Update RWS.
            for operation in self.operations_for_token(submission):
                self._threadsafe_enqueue(operation)

    @rpc_method
    async def dataset_updated(self, task_id: int):
        """Notice that the active dataset of a task has been changed.

        Usually called by AdminWebServer when the contest administrator
        changed the active dataset of a task. This means that we should
        update all the scores for the task using the submission results
        on the new active dataset. If some of them are not available
        yet we keep the old scores (we don't delete them!) and wait for
        ScoringService to notify us that the new ones are available.

        task_id: the ID of the task whose dataset has changed.

        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._dataset_updated_sync, task_id)

    def _dataset_updated_sync(self, task_id: int):
        """Do the work of dataset_updated(), synchronously.

        Runs inside loop.run_in_executor. Calls self._reinitialize_sync()
        directly (not the async reinitialize() -- can't await from
        inside this already-backgrounded thread).

        """
        with SessionGen() as session:
            task: Task = Task.get_from_id(task_id, session)
            dataset = task.active_dataset

            # The task's contest is not sent to any ranking.
            if not self._is_sent(task.contest):
                logger.debug("Ignoring dataset change for task %d of "
                             "contest %s (not sent to any ranking).",
                             task_id, task.contest_id)
                return

            logger.info("Dataset update for task %d (dataset now is %d).",
                        task.id, dataset.id)

            # max_score and/or extra_headers might have changed.
            self._reinitialize_sync()

            for submission in task.submissions:
                # Update RWS.
                if not submission.participation.hidden and \
                        submission.official and \
                        submission.get_result() is not None and \
                        submission.get_result().scored():
                    for operation in self.operations_for_score(submission):
                        self._threadsafe_enqueue(operation)
