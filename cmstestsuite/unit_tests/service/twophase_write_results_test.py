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

"""Tests that exercise EvaluationService.write_results() directly (rather
than calling submission_enqueue_operations() or _advance_two_phase() in
isolation), to cover the two new two-phase call sites inside it: the
_advance_two_phase() skip-synthesis loop, and the phase-2 release
(submission_enqueue_operations()) in the "ending operations" loop.

"""

import gevent.monkey

gevent.monkey.patch_all()  # noqa

import unittest
from unittest.mock import patch

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms import config
from cms.db import Submission
from cms.grading.Job import EvaluationJob
from cms.service.esoperations import ESOperation
from cms.service.EvaluationService import EvaluationService, Result


class TestTwoPhaseWriteResults(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.task = self.add_task(contest=self.contest)
        self.dataset = self.add_dataset(task=self.task, autojudge=True)
        self.task.active_dataset = self.dataset
        self.testcases = {
            codename: self.add_testcase(self.dataset, codename=codename)
            for codename in ["s1-00-sample", "s1-01-scr-wa", "s1-02-normal"]
        }
        self.submission, self.results = self.add_submission_with_results(
            self.task, self.participation, True)
        self.result = self.results[0]
        # write_results() opens its own DB session (SessionGen()), so the
        # fixture needs to be actually committed, not just flushed, to be
        # visible to it.
        self.session.commit()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    @staticmethod
    def _make_evaluation_result(operation, outcome, text):
        job = EvaluationJob(
            operation=operation,
            outcome=outcome,
            text=text,
            success=True,
            shard=0,
            sandboxes=[],
            sandbox_digests={},
            plus={
                "execution_time": 0.0,
                "execution_wall_clock_time": 0.0,
                "execution_memory": 0,
            })
        return Result(job, True)

    @patch.object(config.global_, "two_phase_evaluation", True)
    def test_write_results_synthesizes_skips_and_completes_evaluation(self):
        sample_op = ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            "s1-00-sample")
        scr_op = ESOperation(
            ESOperation.EVALUATION, self.submission.id, self.dataset.id,
            "s1-01-scr-wa")

        items = [
            (sample_op, self._make_evaluation_result(
                sample_op, "1.0", ["Output is correct"])),
            (scr_op, self._make_evaluation_result(
                scr_op, "0.0", ["Output isn't correct"])),
        ]

        service = EvaluationService(0)
        service.write_results(items)

        self.session.expire_all()
        submission = Submission.get_from_id(self.submission.id, self.session)
        result = submission.get_result(self.dataset)

        codenames = {e.codename for e in result.evaluations}
        self.assertEqual(
            codenames, {"s1-00-sample", "s1-01-scr-wa", "s1-02-normal"})

        skipped = next(
            e for e in result.evaluations if e.codename == "s1-02-normal")
        self.assertEqual(skipped.outcome, "0.0")
        self.assertEqual(
            skipped.text, ["Skipped after screening phase failure"])

        # All testcases now have an evaluation (two real, one
        # synthesized), so the result should be fully (and correctly)
        # evaluated, not stuck waiting forever.
        self.assertTrue(result.evaluated())


if __name__ == "__main__":
    unittest.main()
