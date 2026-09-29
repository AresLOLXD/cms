#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""The functional-test harness must not guess a language it wasn't given.

Output-only tests have no language (their Test.languages is [None]) and
submit plain .txt files. SubmitRequest and SubmitUserTestRequest used to
read "no language" as "guess it from the filenames". That was harmless
until a language plugin registered .txt as a source extension: cms_rekarel
(installed in the Docker image) does it for both Karel languages, so the
outputs were sent as "Karel (rekarel.1.0.0)" sources and CWS rejected them
("a language ... is given when not needed"), aborting the functional run at
the first output-only test.

A fake plugin claims .txt the same way here, and we check which language
the requests carry:
- None means "no language": nothing is guessed and no field is sent;
- an explicit language is sent unchanged;
- leaving the argument out still guesses it from the filenames.

"""

import importlib.util
import types
import unittest
from unittest import mock

from cms.grading import languagemanager
from cmstestsuite.web.CWSRequests import SubmitRequest, SubmitUserTestRequest


KAREL = "Karel (rekarel.1.0.0)"
OUTPUT_FORMAT = ["output_000.txt", "output_001.txt"]
OUTPUT_FILES = ["correct-outputonly-000.txt", "correct-outputonly-001.txt"]

# The harness imports bs4 (through AWSRequests), a "devel" extra.
HAVE_BS4 = importlib.util.find_spec("bs4") is not None


class TxtLanguagePluginMixin:
    """Make a fake language plugin claim .txt, like cms_rekarel does."""

    def setUp(self):
        super().setUp()
        karel = types.SimpleNamespace(name=KAREL, source_extensions=[".txt"])
        self.patch(mock.patch.object(
            languagemanager, "LANGUAGES", languagemanager.LANGUAGES + [karel]))
        self.patch(mock.patch.dict(languagemanager._BY_NAME, {KAREL: karel}))
        # The scenario only makes sense if guessing now yields Karel.
        self.assertIs(
            languagemanager.filename_to_language(OUTPUT_FILES[0]), karel)

    def patch(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started


class LanguageFieldTests(TxtLanguagePluginMixin):
    """Checks shared by the two requests that send sources to CWS."""

    request_class = None

    def make_request(self, filenames, **kwargs):
        return self.request_class(
            mock.MagicMock(), (1, "outputonly"), OUTPUT_FORMAT, filenames,
            **kwargs)

    def test_none_means_no_language(self):
        request = self.make_request(OUTPUT_FILES, language=None)
        self.assertNotIn("language", request.data)

    def test_explicit_language_is_sent_unchanged(self):
        request = self.make_request(OUTPUT_FILES, language="C11 / gcc")
        self.assertEqual(request.data, {"language": "C11 / gcc"})

    def test_omitted_language_is_guessed_from_filenames(self):
        request = self.make_request(["solution.cpp"])
        guess = languagemanager.filename_to_language("solution.cpp")
        self.assertEqual(request.data, {"language": guess.name})

    def test_omitted_language_with_no_guess_sends_no_language(self):
        request = self.make_request(["output.unknown"])
        self.assertEqual(request.data, {})


class TestSubmitRequestLanguage(LanguageFieldTests, unittest.TestCase):
    request_class = SubmitRequest


class TestSubmitUserTestRequestLanguage(
        LanguageFieldTests, unittest.TestCase):
    request_class = SubmitUserTestRequest


@unittest.skipUnless(HAVE_BS4, "the functional test harness needs bs4")
class TestFunctionalTestsSubmitPath(TxtLanguagePluginMixin, unittest.TestCase):
    """The requests built by Test.submit() and Test.submit_user_test()."""

    TASK_ID = 7
    USER_ID = 8

    def setUp(self):
        super().setUp()
        # Imported here: they need bs4. The first import of Tests resets the
        # FunctionalTestFramework singleton (each Test() re-initializes it),
        # so it has to happen before the singleton is patched below.
        from cmstestsuite import Tests
        from cmstestsuite.functionaltestframework import \
            FunctionalTestFramework

        self.tests = Tests.ALL_TESTS
        self.sent = []

        def record(request):
            self.sent.append(request)

        # No network: fake the logged-in browser and record the requests
        # instead of executing them.
        self.patch(mock.patch.object(
            FunctionalTestFramework, "get_cws_browser"))
        self.patch(mock.patch.dict(
            FunctionalTestFramework().created_tasks,
            {self.TASK_ID: {"name": "outputonly"}}))
        self.patch(mock.patch.object(SubmitRequest, "execute", record))
        self.patch(mock.patch.object(
            SubmitRequest, "get_submission_id", return_value=1))
        self.patch(mock.patch.object(SubmitUserTestRequest, "execute", record))
        self.patch(mock.patch.object(
            SubmitUserTestRequest, "get_user_test_id", return_value=1))

    def language_less_tests(self):
        tests = [test for test in self.tests if None in test.languages]
        self.assertTrue(tests, "no output-only test is defined any more")
        return tests

    def test_language_less_submissions_send_no_language(self):
        # What TestRunner does for each (test, language) pair.
        for test in self.language_less_tests():
            with self.subTest(test=test.name):
                test.submit(self.TASK_ID, self.USER_ID, None)
                self.assertNotIn("language", self.sent[-1].data)

    def test_language_less_user_tests_send_no_language(self):
        # No output-only test asks for user tests today, but nothing
        # stops one from doing so.
        for test in self.language_less_tests():
            with self.subTest(test=test.name):
                test.submit_user_test(self.TASK_ID, self.USER_ID, None)
                self.assertNotIn("language", self.sent[-1].data)

    def test_explicit_language_is_sent_unchanged(self):
        test = next(test for test in self.tests if None not in test.languages)
        language = test.languages[0]
        test.submit(self.TASK_ID, self.USER_ID, language)
        self.assertEqual(self.sent[-1].data, {"language": language})
