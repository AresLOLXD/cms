#!/usr/bin/env python3
"""Export what the analysis needs from the CMS database (run in the cms
container after a run): every submission with its timestamps, status and
score, evaluation counts, and CMS's own task score per participation/task
(to compare with the ranking)."""

import json
import sys

from sqlalchemy import func

from cms.db import (Contest, Evaluation, Participation, SessionGen,
                    Submission, SubmissionResult)
from cms.grading.scoring import task_score
from cmscommon.datetime import make_timestamp


def main():
    out = {"submissions": [], "task_scores": []}
    with SessionGen() as session:
        eval_counts = dict(session.query(
            Evaluation.submission_id, func.count(Evaluation.id)).group_by(
                Evaluation.submission_id).all())
        for contest in session.query(Contest).all():
            for p in contest.participations:
                for task in contest.tasks:
                    score, partial = task_score(p, task)
                    out["task_scores"].append(
                        {"contest": contest.name, "user": p.user.username,
                         "task": task.name, "score": score,
                         "partial": partial})
        rows = session.query(Submission, SubmissionResult, Participation) \
            .outerjoin(SubmissionResult,
                       (SubmissionResult.submission_id == Submission.id)) \
            .join(Participation,
                  Participation.id == Submission.participation_id).all()
        for sub, sr, p in rows:
            if sr is not None and sr.dataset_id != sub.task.active_dataset_id:
                continue
            out["submissions"].append({
                "id": sub.id, "opaque_id": sub.opaque_id,
                "user": p.user.username, "task": sub.task.name,
                "timestamp": make_timestamp(sub.timestamp),
                "status": sr.get_status() if sr is not None else None,
                "compilation_outcome": sr.compilation_outcome if sr else None,
                "compilation_tries": sr.compilation_tries if sr else None,
                "evaluation_tries": sr.evaluation_tries if sr else None,
                "score": sr.score if sr else None,
                "scored_at": make_timestamp(sr.scored_at)
                if sr is not None and sr.scored_at is not None else None,
                "evaluations": eval_counts.get(sub.id, 0),
            })
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
