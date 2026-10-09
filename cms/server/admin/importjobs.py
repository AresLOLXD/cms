#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <www.luisrodolfo@gmail.com>
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

"""In-memory bulk import jobs of AWS, with their progress.

An import takes seconds (bcrypt hashes every password), longer than a
request should last, so AWS starts it as a job in its own thread and the
page asks for the progress until the job is over. The jobs live only in
the memory of this AWS process: they are lost if it restarts, and they
expire after JOB_TTL.

"""

import dataclasses
import logging
import secrets
import threading
import time
import traceback
from collections.abc import Callable

from cms.db import SessionGen
from cms.server.admin.bulkimport import ImportRow, apply_import, \
    apply_user_import, hash_account_passwords, hash_passwords, \
    plan_import, plan_user_import

logger = logging.getLogger(__name__)

JOB_TTL = 3600.0
APPLY_FAILED = "no se pudo aplicar la importación; no se guardó nada"

# Held around the writing and the commit of every job, whatever its
# contest: the files of two contests may share new users, and the second
# job must read them after the first one commits, instead of inserting
# them again and failing.
_APPLY_LOCK = threading.Lock()


def _describe(job: "ImportJob") -> str:
    """Say what a job imports, for the log lines.

    job: the job.

    return: the words that follow "Bulk import" in a log line.

    """
    if job.contest_id is None:
        return "of global users"
    return "into contest %d" % job.contest_id


@dataclasses.dataclass
class ImportJob:
    """One import running (or finished) in this AWS process.

    id: the unguessable identifier the page uses to ask for the progress.
    owner_id: the id of the admin who started it.
    contest_id: the contest the import goes to, or None for a global
        users import.
    total: the number of rows to import.
    created_at: when the job started, in the clock of the store.
    status: "running", "done" or "error".
    processed: the number of rows with their password already hashed.
    summary: what the import did, once it is done.
    error: why the import did not happen, once it failed; a message
        meant to be shown to the admin.

    """
    id: str
    owner_id: int
    contest_id: int | None
    total: int
    created_at: float
    status: str = "running"
    processed: int = 0
    summary: dict[str, int] | None = None
    error: str | None = None

    def as_json(self) -> dict:
        """Describe the progress of the job for the page.

        return: the state of the job, without the rows or the passwords.

        """
        return {"status": self.status, "processed": self.processed,
                "total": self.total, "summary": self.summary,
                "error": self.error}


class ImportJobStore:
    """The import jobs of this process, each run in its own thread."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        """Create an empty store.

        clock: the source of time, replaceable for the tests.

        """
        self._clock = clock
        self._jobs: dict[str, ImportJob] = {}
        self._lock = threading.Lock()

    def _evict(self) -> None:
        """Forget the jobs older than JOB_TTL; the caller holds the lock."""
        cutoff = self._clock() - JOB_TTL
        for job_id in [i for i, j in self._jobs.items()
                       if j.created_at < cutoff]:
            del self._jobs[job_id]

    def _find_running(self, contest_id: int | None) -> ImportJob | None:
        """Find the job running for a contest; the caller holds the lock.

        contest_id: the contest, or None for the global users import.

        return: the job, or None if there is none.

        """
        return next((j for j in self._jobs.values()
                     if j.contest_id == contest_id
                     and j.status == "running"), None)

    def start(self, owner_id: int, contest_id: int | None,
              rows: list[ImportRow],
              on_done: Callable[[], None]) -> ImportJob:
        """Start importing rows into a contest, in a thread.

        owner_id: the admin who starts it.
        contest_id: the contest, or None for a global users import.
        rows: the rows, already validated by read_rows and plan_import.
        on_done: called after the commit (to notify ProxyService).

        return: the job.

        raise (ValueError): if a job is already running for the contest
            (or, for None, a global users import).

        """
        with self._lock:
            self._evict()
            if self._find_running(contest_id) is not None:
                raise ValueError(
                    "ya hay una importación de usuarios en curso"
                    if contest_id is None
                    else "ya hay una importación en curso para este concurso")
            job = ImportJob(id=secrets.token_urlsafe(16), owner_id=owner_id,
                            contest_id=contest_id, total=len(rows),
                            created_at=self._clock())
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, rows, on_done),
                         name="aws-import-%s" % job.id[:8],
                         daemon=True).start()
        return job

    def _run(self, job: ImportJob, rows: list[ImportRow],
             on_done: Callable[[], None]) -> None:
        """Import the rows of a job; the body of its thread.

        A failure is logged with what the job imports (the contest or the
        global users), the class of the exception and the frames of its
        traceback, and nothing else: an exception from the database
        carries the values of the rows (usernames, names, password
        hashes) in its message and its parameters, and those must not
        reach the AWS log.

        job: the job to run, updated as it goes.
        rows: the rows to import.
        on_done: called after the commit.

        """
        global_import = job.contest_id is None
        try:
            with SessionGen() as session:
                if global_import:
                    plan, errors = plan_user_import(session, rows)
                else:
                    plan, errors = plan_import(session, job.contest_id, rows)
                if plan is None:
                    job.error = "; ".join(errors)
                    job.status = "error"
                    return
                # The plan holds only plain values and the apply steps read
                # again what they write, so no transaction stays open,
                # idle, during the hashing.
                session.rollback()

                def progress() -> None:
                    job.processed += 1

                if global_import:
                    hashes = hash_account_passwords(
                        rows, plan.stored_passwords, progress)
                else:
                    hashes = hash_passwords(rows, set(plan.new_users),
                                            plan.stored_passwords, progress)
                with _APPLY_LOCK:
                    if global_import:
                        apply_user_import(session, rows, hashes)
                    else:
                        apply_import(session, job.contest_id, rows, plan,
                                     hashes)
                    session.commit()
            job.summary = plan.summary()
            job.status = "done"
            logger.info("Bulk import %s by admin %d: %s.",
                        _describe(job), job.owner_id, job.summary)
        except Exception as exc:
            logger.error("Bulk import %s failed with %s.\n%s",
                         _describe(job), type(exc).__name__,
                         "".join(traceback.format_tb(exc.__traceback__)))
            job.error = APPLY_FAILED
            job.status = "error"
            return
        # The import is saved: if the notification fails, the job stays
        # done, since telling the admin that nothing was saved would be
        # false.
        try:
            on_done()
        except Exception as exc:
            logger.warning("Bulk import %s was saved, but ProxyService was "
                           "not notified (%s).",
                           _describe(job), type(exc).__name__)

    def running_job(self, contest_id: int | None) -> ImportJob | None:
        """Return the job that is running for a contest, if any.

        It is the job that makes start() refuse another one, whoever
        started it.

        contest_id: the contest, or None for the global users import.

        return: the job, or None.

        """
        with self._lock:
            self._evict()
            return self._find_running(contest_id)

    def get(self, job_id: str, owner_id: int) -> ImportJob | None:
        """Return a job, if it exists, has not expired and is the owner's.

        job_id: the identifier of the job.
        owner_id: the admin who asks; the jobs of other admins are hidden.

        return: the job, or None.

        """
        with self._lock:
            self._evict()
            job = self._jobs.get(job_id)
        if job is None or job.owner_id != owner_id:
            return None
        return job


IMPORT_JOBS = ImportJobStore()
