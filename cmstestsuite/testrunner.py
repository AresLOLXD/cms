#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2015-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2016 Amir Keivan Mohtashami <akmohtashami97@gmail.com>
# Copyright © 2022 William Di Luigi <williamdiluigi@gmail.com>
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

"""Utility class to run functional-like tests."""

import datetime
import logging
import os
import re
import sys
import time
import types
from collections.abc import Callable

import requests

from cms import TOKEN_MODE_FINITE
from cms.service.ProxyService import encode_id
from cmscommon.datetime import get_system_timezone
from cmstestsuite import CONFIG, TestException
from cmstestsuite.Test import TestFailure
from cmstestsuite.Tests import ALL_LANGUAGES
from cmstestsuite.functionaltestframework import FunctionalTestFramework
from cmstestsuite.programstarter import ProgramStarter


logger = logging.getLogger(__name__)


# Seconds ProxyService and RWS get to show what was sent or changed through
# AWS. They take a moment, since ProxyService works in the background.
RANKING_TIMEOUT = 30.0

# What ProxyService logs when data does not get to RWS: RWS refuses or fails
# on it (a 4xx or 5xx status, a visibility that is rejected), the data cannot
# be encoded or built, a reinitialize fails, or something nobody expected
# happens. Nothing else reports it, so the tests would otherwise still pass.
PROXY_LOG_PROBLEMS = re.compile(
    r"Status [45]\d\d while|rejected|Unexpected error|Cannot encode the data"
    r"|cannot be encoded|Cannot build the ranking data"
    r"|Reinitializing the rankings failed")

# The staff password of the ranking group of check_ranking_visibility.
STAFF_PASSWORD = "staffpwd"

# In the page RWS shows to the public of a hidden group: its login form.
NOTICE_MARKER = 'action="staff-login"'

# In the page RWS shows to the staff of a hidden group: the banner.
STAFF_BANNER_MARKER = "Vista staff"

# In the page RWS shows to the public of a frozen group: the banner.
FROZEN_BANNER_MARKER = "Ranking congelado desde las"

# In the page RWS shows to the staff of a frozen group: the banner.
STAFF_FROZEN_BANNER_MARKER = "Vista staff: ranking congelado para el público"

# In the staff login page of a frozen group: its password field.
PASSWORD_FIELD_MARKER = 'type="password"'


def wait_until(description: str, unmet: Callable[[], str | None],
               timeout: float = RANKING_TIMEOUT, interval: float = 0.25,
               retry: Callable[[], None] | None = None,
               retry_interval: float = 5.0):
    """Wait for something that happens in the background.

    No call is started once timeout seconds have passed.

    description: what we wait for, for the error message.
    unmet: a function returning None once the wait is over, or else a
        short text on what is still missing. It is called again after
        interval seconds, and a failed request counts as missing.
    timeout: how many seconds to wait.
    interval: how many seconds to wait between two calls.
    retry: a function that does again what is meant to make it happen,
        or None. It is called every retry_interval seconds while the
        wait is not over. If it fails, that only counts as missing.
    retry_interval: how many seconds before it is called again.

    raise (TestFailure): if it is still not over after timeout seconds.

    """
    start = time.monotonic()
    deadline = start + timeout
    next_retry = start + retry_interval
    reason = "not even tried"
    while time.monotonic() < deadline:
        try:
            reason = unmet()
        except requests.RequestException as error:
            reason = "request failed: %s" % error
        if reason is None:
            return
        if retry is not None and time.monotonic() >= next_retry:
            next_retry = time.monotonic() + retry_interval
            try:
                retry()
            except (requests.RequestException, TestException) as error:
                reason = "%s (and doing it again failed: %s)" % (
                    reason, error)
        time.sleep(max(0.0, min(interval, deadline - time.monotonic())))
    raise TestFailure("Timed out after %.1f s waiting for %s: %s" %
                      (time.monotonic() - start, description, reason))


def expect_status(what: str, actual: int, expected: int):
    """Raise TestFailure if a request got another HTTP status."""
    if actual != expected:
        raise TestFailure("%s: expected HTTP %d, got %d" %
                          (what, expected, actual))


class TestRunner:
    # Tell pytest not to collect this class as test
    __test__ = False

    def __init__(self, test_list, contest_id=None, workers=1, cpu_limits=None):
        self.start_time = datetime.datetime.now()
        self.last_end_time = self.start_time

        self.framework = FunctionalTestFramework()
        self.load_cms_conf()

        self.ps = ProgramStarter(cpu_limits)

        # Map from task name to (task id, task_module).
        self.task_id_map = {}

        # String to append to objects' names to avoid collisions. Will be the
        # first positive integer i for which admin_<i> is not already
        # registered, and we will hope that if the admin name doesn't clash, no
        # other name will.
        self.suffix = None

        # Name of the contest we created, or None if we use an existing one.
        self.contest_name = None

        self.num_users = 0
        self.workers = workers

        self.start_generic_services()
        self.suffix = self.framework.initialize_aws()

        if contest_id is None:
            self.contest_id = self.create_contest()
        else:
            self.contest_id = int(contest_id)
        self.user_id = self.create_or_get_user()

        self.failures = []
        self.test_list = test_list
        self.n_tests = len(test_list)
        self.n_submissions = sum(len(test.languages) for test in test_list)
        self.n_user_tests = sum(len(test.languages) for test in test_list
                                if test.user_tests)
        logging.info("Have %s submissions and %s user_tests in %s tests...",
                     self.n_submissions, self.n_user_tests, self.n_tests)

    def load_cms_conf(self):
        CONFIG["CONFIG_PATH"] = os.path.join(sys.prefix, "etc/cms.toml")

        # Override CMS config path when environment variable is present
        if "CMS_CONFIG" in os.environ:
            CONFIG["CONFIG_PATH"] = os.environ["CMS_CONFIG"]

        return self.framework.get_cms_config()

    def log_elapsed_time(self):
        end_time = datetime.datetime.now()
        logger.info("Time elapsed: %s, since last: %s",
                    end_time - self.start_time,
                    end_time - self.last_end_time)
        self.last_end_time = end_time

    # Service management.

    def start_generic_services(self):
        self.ps.start("LogService")
        self.ps.start("ResourceService")
        self.ps.start("Checker")
        self.ps.start("ScoringService")
        self.ps.start("AdminWebServer")
        # Just to verify it starts successfully.
        self.ps.start("RankingWebServer", shard=None)
        self.ps.wait()

    def shutdown(self):
        self.ps.stop_all()

    # Data creation.

    def _add_contest(self, name: str, description: str,
                     **kwargs) -> tuple[int, int]:
        """Create a contest that lasts a day from now.

        name: the name of the contest.
        description: its description.
        kwargs: more fields of the contest's page to set.

        return: the id of the contest and the one of its main group.

        """
        start_time = datetime.datetime.utcnow()
        stop_time = start_time + datetime.timedelta(1, 0, 0)
        return self.framework.add_contest(
            name=name,
            description=description,
            languages=list(ALL_LANGUAGES),
            allow_password_authentication="checked",
            start=start_time.strftime("%Y-%m-%d %H:%M:%S.%f"),
            stop=stop_time.strftime("%Y-%m-%d %H:%M:%S.%f"),
            timezone=get_system_timezone(),
            allow_user_tests="checked",
            token_mode=TOKEN_MODE_FINITE,
            token_max_number="100",
            token_min_interval="0",
            token_gen_initial="100",
            token_gen_number="0",
            token_gen_interval="1",
            token_gen_max="100",
            **kwargs
        )

    def create_contest(self) -> int:
        """Create a new contest.

        return: contest id.

        """
        self.contest_name = "testcontest_%s" % self.suffix
        self.contest_id, self.group_id = self._add_contest(
            self.contest_name, "A test contest #%s." % self.suffix)
        logger.info("Created contest %s.", self.contest_id)
        return self.contest_id

    def create_or_get_user(self) -> int:
        """Create a new user if it doesn't exists already.

        return: user id.

        """
        self.num_users += 1

        def enumerify(x):
            if 11 <= x <= 13:
                return 'th'
            return {1: 'st', 2: 'nd', 3: 'rd'}.get(x % 10, 'th')

        username = "testrabbit_%s_%d" % (self.suffix, self.num_users)

        # Find a user that may already exist (from a previous contest).
        users = self.framework.get_users(self.contest_id)
        user_create_args = {
            "username": username,
            "password": "kamikaze",
            "method": "plaintext",
            "first_name": "Ms. Test",
            "last_name": "Wabbit the %d%s" % (self.num_users,
                                              enumerify(self.num_users)),
            "group_id": self.group_id
        }

        if username in users:
            self.user_id = users[username]['id']
            self.framework.add_existing_user(self.user_id, **user_create_args)
            logging.info("Using existing user with id %s.", self.user_id)
        else:
            self.user_id = self.framework.add_user(
                contest_id=str(self.contest_id), **user_create_args)
            logging.info("Created user with id %s.", self.user_id)
        return self.user_id

    def _task_name(self, task_module: types.ModuleType) -> str:
        """Return the name of a task of the tests in the contest."""
        return "%s_%s" % (task_module.task_info['name'], self.suffix)

    def create_or_get_task(self, task_module: types.ModuleType) -> int:
        """Create a new task if it does not exist.

        task_module: a task as in task/<name>.

        return: task id of the new (or existing) task.

        """
        name = self._task_name(task_module)

        # Have we done this before? Pull it out of our cache if so.
        if name in self.task_id_map:
            # Ensure we don't have multiple modules with the same task name.
            assert self.task_id_map[name][1] == task_module

            return self.task_id_map[name][0]

        task_create_args = {
            "token_mode": TOKEN_MODE_FINITE,
            "token_max_number": "100",
            "token_min_interval": "0",
            "token_gen_initial": "100",
            "token_gen_number": "0",
            "token_gen_interval": "1",
            "token_gen_max": "100",
            "max_submission_number": None,
            "max_user_test_number": None,
            "min_submission_interval": None,
            "min_user_test_interval": None,
        }
        task_create_args.update(task_module.task_info)

        # Update the name with the random bit to avoid conflicts.
        task_create_args["name"] = name

        # Find if the task already exists (the name make sure that if it
        # exists, it is already in out contest).
        tasks = self.framework.get_tasks()
        if name in tasks:
            # Then just use the existing one.
            task = tasks[name]
            task_id = task['id']
            self.task_id_map[name] = (task_id, task_module)
            self.framework.add_existing_task(
                task_id, contest_id=str(self.contest_id), **task_create_args)
            return task_id

        # Otherwise, we need to add the task ourselves.
        task_id = self.framework.add_task(
            contest_id=str(self.contest_id), **task_create_args)

        # add any managers
        code_path = os.path.join(
            os.path.dirname(task_module.__file__),
            "code")
        if hasattr(task_module, 'managers'):
            for manager in task_module.managers:
                mpath = os.path.join(code_path, manager)
                self.framework.add_manager(task_id, mpath)

        # add the task's test data.
        data_path = os.path.join(
            os.path.dirname(task_module.__file__),
            "data")
        for num, (input_file, output_file, public) \
                in enumerate(task_module.test_cases):
            ipath = os.path.join(data_path, input_file)
            opath = os.path.join(data_path, output_file)
            self.framework.add_testcase(task_id, num, ipath, opath, public)

        self.task_id_map[name] = (task_id, task_module)

        logging.info("Created task %s as id %s", name, task_id)

        return task_id

    # Test execution.

    def _all_submissions(self):
        """Yield all pairs (test, language)."""
        for test in self.test_list:
            for lang in test.languages:
                yield (test, lang)

    def _all_user_tests(self):
        """Yield all pairs (test, language)."""
        for test in self.test_list:
            if test.user_tests:
                for lang in test.languages:
                    yield (test, lang)

    def submit_tests(self, concurrent_submit_and_eval: bool = True):
        """Create the tasks, and submit for all languages in all tests.

        concurrent_submit_and_eval: if False, start ES only
            after CWS received all the submissions, with the goal of
            having a clearer view of the time each step takes.

        """
        # Pre-install all tasks in the contest. We start the other services
        # after this to ensure they pick up the new tasks before receiving
        # data for them.
        for test in self.test_list:
            self.create_or_get_task(test.task_module)

        # We start now only the services we need in order to submit and
        # we start the other ones while the submissions are being sent
        # out. A submission can arrive after ES's first sweep, but
        # before CWS connects to ES; if so, it will be ignored until
        # ES's second sweep, making the test flaky due to timeouts. By
        # waiting for ES to start before submitting, we ensure CWS can
        # send the notification for all submissions.
        self.ps.start("ContestWebServer", contest=self.contest_id)
        if concurrent_submit_and_eval:
            self.ps.start("EvaluationService", contest=self.contest_id)
        self.ps.wait()

        self.ps.start("ProxyService", contest=self.contest_id)
        for shard in range(self.workers):
            self.ps.start("Worker", shard)

        for i, (test, lang) in enumerate(self._all_submissions()):
            logging.info("Submitting submission %s/%s: %s (%s)",
                         i + 1, self.n_submissions, test.name, lang)
            task_id = self.create_or_get_task(test.task_module)
            try:
                test.submit(task_id, self.user_id, lang)
            except TestFailure as f:
                logging.error("(FAILED (while submitting): %s)", f)
                self.failures.append((test, lang, str(f)))

        for i, (test, lang) in enumerate(self._all_user_tests()):
            logging.info("Submitting user test  %s/%s: %s (%s)",
                         i + 1, self.n_user_tests, test.name, lang)
            task_id = self.create_or_get_task(test.task_module)
            try:
                test.submit_user_test(task_id, self.user_id, lang)
            except TestFailure as f:
                logging.error("(FAILED (while submitting): %s)", f)
                self.failures.append((test, lang, str(f)))

        if not concurrent_submit_and_eval:
            self.ps.start("EvaluationService", contest=self.contest_id)
        self.ps.wait()

    def wait_for_evaluation(self):
        """Wait for all submissions to evaluate.

        The first will wait longer as ES prioritizes compilations.

        """
        for i, (test, lang) in enumerate(self._all_submissions()):
            logging.info("Waiting for submission %s/%s: %s (%s)",
                         i + 1, self.n_submissions, test.name, lang)
            try:
                test.wait(self.contest_id, lang)
            except TestFailure as f:
                logging.error("(FAILED (while evaluating): %s)", f)
                self.failures.append((test, lang, str(f)))

        for i, (test, lang) in enumerate(self._all_user_tests()):
            logging.info("Waiting for user test %s/%s: %s (%s)",
                         i + 1, self.n_user_tests, test.name, lang)
            try:
                test.wait_user_test(self.contest_id, lang)
            except TestFailure as f:
                logging.error("(FAILED (while evaluating user test): %s)", f)
                self.failures.append((test, lang, str(f)))

        return self.failures

    # Checks on RankingWebServer. They run once all the tests are done.

    def check_rankings(self) -> list[str]:
        """Run the checks on RWS, whichever of them fail.

        return: one message for each check that failed.

        """
        problems = []
        for check in (self.check_ranking_delivery,
                      self.check_ranking_visibility):
            try:
                check()
            except Exception as error:
                # Whatever it is, the results of the tests must still be
                # reported, and so must those of the other check.
                logging.error("(FAILED %s: %s)", check.__name__, error,
                              exc_info=not isinstance(error, TestFailure))
                problems.append("%s: %s" % (check.__name__, error))
        return problems

    def _unmet_ranking_data(
        self, session: requests.Session, group_path: str,
        expected: dict[str, set[str]],
        submissions: dict[str, tuple[str, str]] | None = None
    ) -> str | None:
        """Tell what a ranking of RWS still lacks.

        session: the requests.Session to read RWS with.
        group_path: "" for the root ranking, else "/<group>".
        expected: for each store of RWS, the ids that must be there. Each
            store must have something anyway.
        submissions: if given, the ids of the submissions that must be in
            RWS, each with the keys of its user and of its task, and each
            must have a subchange that carries its score.

        return: what is missing, or None if nothing.

        """
        contents: dict[str, dict] = dict()
        for store, ids in expected.items():
            response = self.framework.rws_request(
                session, "GET", "%s/%s/" % (group_path, store))
            if response.status_code != 200:
                return "%s/ answered HTTP %d" % (store, response.status_code)
            try:
                contents[store] = response.json()
            except ValueError:
                return "%s/ did not answer JSON" % store
            if not contents[store]:
                return "RWS has no %s" % store
            missing = sorted(ids - set(contents[store]))
            if missing:
                return "RWS lacks %d of the %d %s, for example %s" % (
                    len(missing), len(ids), store, missing[:3])

        if submissions:
            wrong = sorted(
                id_ for id_, keys in submissions.items()
                if (contents["submissions"][id_].get("user"),
                    contents["submissions"][id_].get("task")) != keys)
            if wrong:
                return "RWS has %d submissions of another user or task, " \
                    "for example %s" % (len(wrong), wrong[:3])
            with_score = {
                subchange.get("submission")
                for subchange in contents["subchanges"].values()
                if "score" in subchange}
            unscored = sorted(set(submissions) - with_score)
            if unscored:
                return "RWS lacks the score of %d submissions, for " \
                    "example %s" % (len(unscored), unscored[:3])
        return None

    def _scored_submissions(self, user_key: str) -> dict[str, tuple[str, str]]:
        """Return the submissions of the tests that RWS must have.

        Those of the tests that got to be scored: the ones that failed
        (to be submitted, or evaluated) are reported as such already.

        user_key: the key of the user that submitted them in RWS.

        return: for each id of submission, the key of its user and of its
            task in RWS.

        """
        failed = {(test, lang) for test, lang, _ in self.failures}
        return {
            str(test.submission_id[lang]): (
                user_key, encode_id(self._task_name(test.task_module)))
            for test, lang in self._all_submissions()
            if test.submission_id.get(lang) is not None
            and (test, lang) not in failed}

    def check_proxy_service_log(self):
        """Check that ProxyService logged no failure to push to RWS.

        It reads the log of the last ProxyService started.

        raise (TestFailure): if there is one, or there is no log.

        """
        path = os.path.join(
            self.framework.get_log_dir(), "ProxyService-0", "last.log")
        try:
            with open(path, "rt", encoding="utf-8") as f:
                lines = [line.strip() for line in f
                         if PROXY_LOG_PROBLEMS.search(line)]
        except OSError as error:
            raise TestFailure("Cannot read the log of ProxyService: %s" %
                              error)
        if lines:
            raise TestFailure(
                "ProxyService reports failures sending to RWS (%d lines in "
                "%s), for example:\n%s" %
                (len(lines), path, "\n".join(lines[:5])))

    def check_ranking_delivery(self):
        """Check that RWS received what ProxyService sends for the tests.

        The tests would pass even if RWS refused everything: ProxyService
        only logs that. So look at what RWS holds (its root ranking, the
        one of the single contest ProxyService serves), and at that log.

        raise (TestFailure): if RWS lacks the contest, the user or the
            tasks of the tests, or a submission that was scored, or its
            score, or if ProxyService logged a failure.

        """
        started = time.monotonic()
        username = self.framework.created_users[self.user_id]["username"]
        user_key = encode_id(username)
        submissions = self._scored_submissions(user_key)
        expected: dict[str, set[str]] = {
            "contests": set(),
            "users": {user_key},
            "tasks": {encode_id(name) for name in self.task_id_map},
            "submissions": set(submissions),
            "subchanges": set(),
        }
        if self.contest_name is not None:
            expected["contests"].add(encode_id(self.contest_name))

        session = requests.Session()
        try:
            wait_until(
                "RWS to receive the data of contest %s" % self.contest_id,
                lambda: self._unmet_ranking_data(
                    session, "", expected, submissions))
        except TestFailure as error:
            # ProxyService says why, if RWS refused it.
            try:
                self.check_proxy_service_log()
            except TestFailure as log_error:
                raise TestFailure("%s\n%s" % (error, log_error))
            raise
        self.check_proxy_service_log()
        logger.info("RWS received the data, %d submissions with their "
                    "scores, and ProxyService reported no failure (%.1fs).",
                    len(submissions), time.monotonic() - started)

    def check_ranking_visibility(self):
        """Check that an admin can hide a ranking group, and freeze it.

        It goes through AWS, ProxyService and RWS, as the admins do. A
        group with a contest of its own is created (not the contest of
        the tests, which may be an existing one), hidden with a staff
        password and revealed again, then frozen and unfrozen.

        ProxyService serves either one contest, to the root ranking, or
        all the ones that have a ranking group, to the groups. So the
        one of the tests is stopped, and one for all contests started.
        This is done last, when nothing needs the first one anymore.

        raise (TestFailure): if RWS does not behave as expected at any
            step, or ProxyService logged a failure.

        """
        started = time.monotonic()
        fw = self.framework
        stamp = "%s_%d" % (self.suffix, time.time())
        group = "testvis-%s" % stamp
        group_path = "/" + group
        description = "Ranking visibility check"
        expected = {"contests": {encode_id("testvis_%s" % stamp)}}
        public = requests.Session()
        staff = requests.Session()

        def status(session: requests.Session, path: str) -> int:
            # Stream: /events of a visible group never ends.
            response = fw.rws_request(
                session, "GET", group_path + path, stream=True)
            response.close()
            return response.status_code

        def page(session: requests.Session) -> requests.Response:
            return fw.rws_request(session, "GET", group_path + "/")

        def notice_unmet() -> str | None:
            response = page(public)
            if response.status_code != 200:
                return "the group's page answered HTTP %d" % \
                    response.status_code
            if NOTICE_MARKER not in response.text:
                return "the group's page is not the notice"
            return None

        def banner_unmet(frozen: bool) -> str | None:
            response = page(public)
            if response.status_code != 200:
                return "the group's page answered HTTP %d" % \
                    response.status_code
            if (FROZEN_BANNER_MARKER in response.text) != frozen:
                return "the group's page %s the freeze banner" % (
                    "lacks" if frozen else "still has")
            return None

        def save(action: str | None = None,
                 staff_password: str | None = None):
            # Press a visibility action's button on the group's page in
            # AWS, or Update: saving the page as it is changes nothing,
            # but makes AWS send the group to RWS again.
            fw.edit_ranking_group(
                group_id, group, description, action=action,
                staff_password=staff_password)

        # The group is created visible, with a contest, before ProxyService
        # for all the contests starts: it sends them by itself when it
        # starts, and does not depend on AWS reaching it. AWS reconnects to
        # a new service within half a second, and drops what it sends
        # meanwhile, so what is saved later is saved again if RWS does not
        # show it (which changes nothing when it did arrive).
        group_id = fw.add_ranking_group(group, description)
        self._add_contest("testvis_%s" % stamp, description,
                          ranking_group_id=str(group_id))
        self.ps.stop("ProxyService", contest=self.contest_id)
        # Its log is still the last one, so this covers its whole life.
        self.check_proxy_service_log()
        self.ps.start("ProxyService", contest="ALL")
        self.ps.wait()

        wait_until(
            "the contest to reach the visible group %s" % group,
            lambda: self._unmet_ranking_data(public, group_path, expected))
        if NOTICE_MARKER in page(public).text:
            raise TestFailure("A visible group shows the notice.")

        # Hide it.
        save("hide_now", STAFF_PASSWORD)
        wait_until("the notice of the hidden group %s" % group, notice_unmet,
                   retry=save)
        # RWS applies it at once: no further waiting for these.
        for path in ("/contests/", "/scores", "/events"):
            expect_status("The public on the hidden group's %s" % path,
                          status(public, path), 403)
        response = fw.rws_request(
            staff, "POST", group_path + "/staff-login",
            data={"password": "not-" + STAFF_PASSWORD})
        expect_status("A staff login with a wrong password",
                      response.status_code, 401)
        if len(staff.cookies) > 0:
            raise TestFailure("A wrong staff password got a cookie.")
        response = fw.rws_request(
            staff, "POST", group_path + "/staff-login",
            data={"password": STAFF_PASSWORD})
        expect_status("A staff login", response.status_code, 303)
        if "rws_staff" not in staff.cookies:
            raise TestFailure("A staff login did not set the staff cookie.")
        expect_status("The staff on the hidden group's /contests/",
                      status(staff, "/contests/"), 200)
        if STAFF_BANNER_MARKER not in page(staff).text:
            raise TestFailure("The staff page of the hidden group has no "
                              "banner.")
        expect_status("The public on the hidden group's /contests/, after "
                      "a staff login", status(public, "/contests/"), 403)

        # Reveal it, keeping the staff password.
        save("show_now")
        wait_until(
            "the ranking of the revealed group %s" % group,
            lambda: self._unmet_ranking_data(public, group_path, expected),
            retry=save)
        if NOTICE_MARKER in page(public).text:
            raise TestFailure("A revealed group still shows the notice.")
        expect_status("The staff on the revealed group's /contests/",
                      status(staff, "/contests/"), 200)

        # Freeze it.
        save("freeze_now")
        # AWS froze it by now (this runs on the same clock).
        frozen_at = time.time()
        wait_until("the banner of the frozen group %s" % group,
                   lambda: banner_unmet(True), retry=save)
        # RWS applies it at once: no further waiting for these.
        expect_status("The public on the frozen group's /submissions/",
                      status(public, "/submissions/"), 403)
        response = fw.rws_request(public, "GET", group_path + "/scores")
        expect_status("The public on the frozen group's /scores",
                      response.status_code, 200)
        if "no-store" not in response.headers.get("Cache-Control", ""):
            raise TestFailure(
                "The frozen group's /scores may be stored: Cache-Control "
                "is %r." % response.headers.get("Cache-Control"))
        response = fw.rws_request(public, "GET", group_path + "/staff-login")
        expect_status("The public on the frozen group's /staff-login",
                      response.status_code, 200)
        if PASSWORD_FIELD_MARKER not in response.text:
            raise TestFailure("The staff login page of the frozen group has "
                              "no password field.")
        # A new visitor: the staff one got its cookie while hidden.
        frozen_staff = requests.Session()
        response = fw.rws_request(
            frozen_staff, "POST", group_path + "/staff-login",
            data={"password": STAFF_PASSWORD})
        expect_status("A staff login on the frozen group",
                      response.status_code, 303)
        if "rws_staff" not in frozen_staff.cookies:
            raise TestFailure("A staff login on the frozen group did not "
                              "set the staff cookie.")
        if STAFF_FROZEN_BANNER_MARKER not in page(frozen_staff).text:
            raise TestFailure("The staff page of the frozen group has no "
                              "banner.")

        # Unfreeze it, in a later second than the freeze. ProxyService
        # sends RWS whole seconds, and RWS refuses a window that ends in
        # the second it starts, though AWS accepts it: the group would
        # stay frozen. (Hiding and showing are over a second apart: a
        # wrong staff password alone takes that long.)
        time.sleep(max(0.0, int(frozen_at) + 1 - time.time()))
        save("unfreeze_now")
        wait_until("the unfrozen group %s to drop the banner" % group,
                   lambda: banner_unmet(False), retry=save)

        self.check_proxy_service_log()
        logger.info("Ranking group %s was hidden, revealed, frozen and "
                    "unfrozen (%.1fs).", group, time.monotonic() - started)
