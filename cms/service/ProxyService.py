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

import json
import logging
import string
from urllib.parse import urljoin, urlsplit

import gevent
import gevent.queue
import requests
import requests.exceptions
from sqlalchemy import not_, select

from cms import config
from cms.db import SessionGen, Session, Contest, Participation, Task, \
    Submission, get_submissions
from cms.io import Executor, QueueItem, TriggeredService, rpc_method
from cms.io.priorityqueue import QueueEntry
from cmscommon.datetime import make_timestamp
from cmscommon.ranking_groups import is_valid_group_name


logger = logging.getLogger(__name__)


class CannotSendError(Exception):
    pass


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


def safe_put_data(ranking: str, resource: str, data: dict, operation: str):
    """Send some data to ranking using a PUT request.

    ranking: the URL of ranking server.
    resource: the relative path of the entity.
    data: the data to JSON-encode and send.
    operation: a human-readable description of the operation
        we're performing (to produce log messages).

    raise (CannotSendError): in case of communication errors.

    """
    try:
        url = urljoin(ranking, resource)
        # XXX With requests-1.2 auth is automatically extracted from
        # the URL: there is no need for this.
        auth = urlsplit(url)
        res = requests.put(url, json.dumps(data),
                           auth=(auth.username, auth.password),
                           headers={'content-type': 'application/json'},
                           verify=config.proxy_service.https_certfile)
    except requests.exceptions.RequestException as error:
        msg = "%s while %s: %s." % (type(error).__name__, operation, error)
        logger.warning(msg)
        raise CannotSendError(msg)
    if 400 <= res.status_code < 600:
        msg = "Status %s while %s." % (res.status_code, operation)
        logger.warning(msg)
        raise CannotSendError(msg)


def safe_delete_data(ranking: str, resource: str, operation: str):
    """Delete a whole resource list from ranking using a DELETE request.

    ranking: the URL of ranking server.
    resource: the relative path of the entity list.
    operation: a human-readable description of the operation
        we're performing (to produce log messages).

    raise (CannotSendError): in case of communication errors.

    """
    try:
        url = urljoin(ranking, resource)
        auth = urlsplit(url)
        res = requests.delete(url,
                              auth=(auth.username, auth.password),
                              verify=config.proxy_service.https_certfile)
    except requests.exceptions.RequestException as error:
        msg = "%s while %s: %s." % (type(error).__name__, operation, error)
        logger.warning(msg)
        raise CannotSendError(msg)
    if 400 <= res.status_code < 600:
        msg = "Status %s while %s." % (res.status_code, operation)
        logger.warning(msg)
        raise CannotSendError(msg)


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
        data: the entities to send, by id (empty for a reset).
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


class ProxyExecutor(Executor[ProxyOperation]):
    """A thread that sends data to one ranking.

    The object is used as a thread-local storage and its run method is
    the function that, started as a greenlet, uses it.

    It maintains a queue of data to send. At each "round" the queue is
    emptied (i.e. all jobs are fetched) and the data is then "combined"
    to minimize the number of actual HTTP requests: they'll be at most
    one per entity type.

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

    # How long we wait after having failed to push data to a ranking
    # before trying again.
    FAILURE_WAIT = 60.0

    def __init__(self, ranking: str):
        """Create a proxy for the ranking at the given URL.

        ranking: a complete URL (containing protocol, username,
            password, hostname, port and prefix) where a ranking is
            supposed to listen.

        """
        super().__init__(batch_executions=True)

        self._ranking = ranking
        self._visible_ranking = safe_url(ranking)

    @staticmethod
    def _prefix(group: str | None) -> str:
        """Return the resource path prefix of a ranking namespace."""
        return "" if group is None else "%s/" % group

    def execute(self, entries: list[QueueEntry[ProxyOperation]]):
        """Consume (i.e. send) the data put in the queue, forever.

        Pick all operations found in the queue (if there aren't any,
        block waiting until there are), combine them and send HTTP
        requests to the target ranking. Do it until something very bad
        happens (i.e. some exception is raised). If communication fails
        don't stop, just wait FAILURE_WAIT seconds before restarting
        the loop.

        Do all this cooperatively: yield at every blocking operation
        (queue fetch, request send, failure wait, etc.). Since the
        queue is joinable, also notify when the fetched jobs are done.

        entries: entries containing the operations to perform.

        """
        # The cumulative data that we will try to send to the ranking,
        # per namespace (None is the root), built by combining items in
        # the queue.
        data: dict[str | None, list[dict]] = dict()
        # Namespaces to empty before sending data, in arrival order.
        resets: list[str | None] = list()

        for entry in entries:
            item = entry.item
            if item.type_ == self.RESET_TYPE:
                # Data queued before the reset is obsolete.
                data.pop(item.group, None)
                if item.group not in resets:
                    resets.append(item.group)
            else:
                group_data = data.setdefault(
                    item.group, list(dict() for i in range(self.TYPE_COUNT)))
                group_data[item.type_].update(item.data)

        try:
            for group in resets:
                for name in self.RESET_RESOURCE_PATHS:
                    operation = "deleting %s from ranking %s%s" % (
                        name, self._visible_ranking, self._prefix(group))
                    logger.debug(operation.capitalize())
                    safe_delete_data(self._ranking, "%s%s/" % (
                        self._prefix(group), name), operation)

            for group, group_data in data.items():
                for i in range(self.TYPE_COUNT):
                    # Send entities of type i.
                    if len(group_data[i]) > 0:
                        # We abuse the resource path as the English
                        # (plural) name for the entity type.
                        name = self.RESOURCE_PATHS[i]
                        operation = "sending %s to ranking %s%s" % (
                            name, self._visible_ranking, self._prefix(group))

                        logger.debug(operation.capitalize())
                        safe_put_data(
                            self._ranking, "%s%s/" % (
                                self._prefix(group), name),
                            group_data[i], operation)

        except CannotSendError:
            # A log message has already been produced.
            gevent.sleep(self.FAILURE_WAIT)
        except:
            # Whoa! That's unexpected!
            logger.error("Unexpected error.", exc_info=True)
            gevent.sleep(self.FAILURE_WAIT)


class ProxyService(TriggeredService[ProxyOperation, ProxyExecutor]):
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

    def _is_sent(self, contest: Contest) -> bool:
        """Return whether the data of contest goes to a ranking."""
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
                    self.enqueue(operation)
                    counter += 1

            if submission.tokened() and not (
                    only_missing and
                    submission.id in self.tokens_sent_to_rankings):
                for operation in self.operations_for_token(submission):
                    self.enqueue(operation)
                    counter += 1

        return counter

    def _missing_operations(self):
        """Return a generator of data to be sent to the rankings..

        """
        counter = 0
        with SessionGen() as session:
            for contest in self._contests_to_send(session):
                counter += self._enqueue_submissions(
                    session, contest, only_missing=True)
        return counter

    def initialize(self):
        """Send basic data to all the rankings.

        It's data that's supposed to be sent before the contest, that's
        needed to understand what we're talking about when we send
        submissions: contest, users, tasks.

        No support for teams, flags and faces.

        """
        logger.info("Initializing rankings.")

        with SessionGen() as session:
            for contest in self._contests_to_send(session):
                self._enqueue_contest_data(contest)

    def _enqueue_contest_data(self, contest: Contest):
        """Enqueue the contest, its users, teams and tasks.

        If the data cannot be built, log the error and enqueue nothing
        for the contest, without affecting the other contests.

        """
        try:
            operations = self._operations_for_contest(contest)
        except Exception:
            logger.exception("Cannot build the ranking data of contest %d "
                             "(%s), not sending it until fixed.",
                             contest.id, contest.name)
            self._broken_contests.add(contest.id)
            return
        self._broken_contests.discard(contest.id)
        for operation in operations:
            self.enqueue(operation)

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
    def reinitialize(self):
        """Repeat the initialization procedure for all rankings.

        This method is usually called via RPC when someone knows that
        some basic data (i.e. contest, tasks or users) changed and
        rankings need to be updated. Ranking groups that lost a contest
        (or were deleted) are emptied first and then sent again in full,
        since rankings only merge the data they receive.

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
        self._group_contests = new_mapping

        for group in lost:
            logger.info("Ranking group %s lost contests, resetting it.",
                        group)
            self.enqueue(ProxyOperation(ProxyExecutor.RESET_TYPE, {}, group))

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

    @rpc_method
    def regenerate_ranking(self, group: str | None = None):
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
        logger.info("Regenerating ranking %s.",
                    group if group is not None else "(root)")
        self.enqueue(ProxyOperation(ProxyExecutor.RESET_TYPE, {}, group))
        with SessionGen() as session:
            for contest in self._contests_to_send(session):
                if self._group_of(contest) == group:
                    self._enqueue_contest_data(contest)
                    self._enqueue_submissions(
                        session, contest, only_missing=False)

    @rpc_method
    def submission_scored(self, submission_id: int):
        """Notice that a submission has been scored.

        Usually called by ScoringService when it's done with scoring a
        submission result. Since we don't trust anyone we verify that,
        and then send data about the score to the rankings.

        submission_id: the id of the submission that changed.

        """
        with SessionGen() as session:
            submission = Submission.get_from_id(submission_id, session)

            if submission is None:
                logger.error("[submission_scored] Received score request for "
                             "unexistent submission id %s.", submission_id)
                raise KeyError("Submission not found.")

            # The submission's contest is not sent to any ranking.
            if not self._is_sent(submission.task.contest):
                logger.debug("Ignoring submission %d of contest %d "
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
                self.enqueue(operation)

    @rpc_method
    def submission_tokened(self, submission_id: int):
        """Notice that a submission has been tokened.

        Usually called by ContestWebServer when it's processing a token
        request of an user. Since we don't trust anyone we verify that,
        and then send data about the token to the rankings.

        submission_id: the id of the submission that changed.

        """
        with SessionGen() as session:
            submission = Submission.get_from_id(submission_id, session)

            if submission is None:
                logger.error("[submission_tokened] Received token request for "
                             "unexistent submission id %s.", submission_id)
                raise KeyError("Submission not found.")

            # The submission's contest is not sent to any ranking.
            if not self._is_sent(submission.task.contest):
                logger.debug("Ignoring submission %d of contest %d "
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
                self.enqueue(operation)

    @rpc_method
    def dataset_updated(self, task_id: int):
        """Notice that the active dataset of a task has been changed.

        Usually called by AdminWebServer when the contest administrator
        changed the active dataset of a task. This means that we should
        update all the scores for the task using the submission results
        on the new active dataset. If some of them are not available
        yet we keep the old scores (we don't delete them!) and wait for
        ScoringService to notify us that the new ones are available.

        task_id: the ID of the task whose dataset has changed.

        """
        with SessionGen() as session:
            task: Task = Task.get_from_id(task_id, session)
            dataset = task.active_dataset

            # The task's contest is not sent to any ranking.
            if not self._is_sent(task.contest):
                logger.debug("Ignoring dataset change for task %d of "
                             "contest %d (not sent to any ranking).",
                             task_id, task.contest_id)
                return

            logger.info("Dataset update for task %d (dataset now is %d).",
                        task.id, dataset.id)

            # max_score and/or extra_headers might have changed.
            self.reinitialize()

            for submission in task.submissions:
                # Update RWS.
                if not submission.participation.hidden and \
                        submission.official and \
                        submission.get_result() is not None and \
                        submission.get_result().scored():
                    for operation in self.operations_for_score(submission):
                        self.enqueue(operation)
