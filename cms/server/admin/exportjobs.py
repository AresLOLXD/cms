# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
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

"""Contest dump exports of AWS: jobs that run cmsDumpExporter.

An export takes minutes and gigabytes, so the page starts it as a job and
asks for its state until it is over. cmscontrib.DumpExporter patches
threading and socket with gevent when it is imported, so AWS must never
import it: each job runs it in a child process, into a plain directory,
and then packs that directory in a zip with one root directory, which is
what cmsDumpImporter needs. The jobs live in the memory of this AWS
process, like those of importjobs.py: they are lost if it restarts (and
the files they left are removed at start), and they expire after JOB_TTL
counted from their end, deleting their file.

Everything here runs in the event loop of AWS, so no lock is needed.

"""

import asyncio
import dataclasses
import logging
import os
import secrets
import shutil
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Sequence
from datetime import date

from cms import config

logger = logging.getLogger(__name__)

JOB_TTL = 3600.0
# shortcut: a fixed floor stands in for twice the size of the files to
# export, as the database has no cheap size of its large objects (summing
# them reads all their pages); upgrade it to a real estimate if exports
# bigger than this start to fill the disk.
MIN_FREE_BYTES = 5 * 1024 ** 3
BUSY = "ya hay una exportación en curso; espera a que termine"
NO_SPACE = ("no hay espacio libre suficiente en el disco de AWS para "
            "exportar (se necesitan al menos %d GB libres, tanto en el "
            "directorio de exportaciones como en el temp_dir)")
EXPORT_FAILED = "la exportación falló (código de salida %d); no se generó nada"
UNEXPECTED_FAILURE = "la exportación falló; no se generó nada"

# Builds the command of the exporter: the contests, its flags and the
# directory to export into (which must not exist).
ArgvBuilder = Callable[[Sequence[int], Sequence[str], str], list[str]]


def dump_name(contest_ids: Sequence[int], today: date) -> str:
    """Name the dump of some contests, as its directory and its zip.

    contest_ids: the contests.
    today: the date of the export.

    return: the name, without extension; it does not grow with the number
        of contests, as a file name has a length limit.

    """
    ids = sorted(contest_ids)
    contests = ("-".join(map(str, ids)) if len(ids) <= 8
                else "%d-contests" % len(ids))
    return "dump_%s_%s" % (contests, today.isoformat())


def export_argv(contest_ids: Sequence[int], flags: Sequence[str],
                target: str) -> list[str]:
    """Build the command that exports some contests into a directory.

    contest_ids: the contests to export.
    flags: flags of cmsDumpExporter, chosen by the code (-f, -F, -G, -S,
        -U, -X), never taken from a request.
    target: the directory to export into; it must not exist yet.

    return: the command, at the lowest CPU priority so that it does not
        slow down the contest that shares the machine.

    """
    return ["nice", "-n", "19", sys.executable, "-m",
            "cmscontrib.DumpExporter", "-c", *map(str, contest_ids),
            *flags, target]


def pack_zip(directory: str, name: str) -> str:
    """Pack a dump in a zip with it as the only root, and remove it.

    directory: the directory that holds the dump.
    name: the name of the dump's directory inside it.

    return: the path of the zip, next to the dump.

    """
    archive = shutil.make_archive(os.path.join(directory, name), "zip",
                                  root_dir=directory, base_dir=name)
    shutil.rmtree(os.path.join(directory, name))
    return archive


@dataclasses.dataclass(eq=False)
class ExportJob:
    """One export running (or finished) in this AWS process.

    id: the unguessable identifier the page uses to ask for the state.
    owner_id: the id of the admin who started it.
    contest_ids: the contests it exports.
    flags: the flags it passes to the exporter.
    created_at: when the job started, in the clock of the store.
    directory: the directory of the job, inside the exports directory;
        it holds the dump while it is made and then the zip.
    name: the name of the dump, and of the root directory of the zip.
    status: "running", "done" or "error".
    phase: what a running job is doing: "exporting" or "packing".
    size: the size of the zip in bytes, once it is done.
    error: why the export failed, once it did; a message meant to be
        shown to the admin.
    finished_at: when the job ended, in the clock of the store.
    task: the task that runs the job.

    """
    id: str
    owner_id: int
    contest_ids: tuple[int, ...]
    flags: tuple[str, ...]
    created_at: float
    directory: str
    name: str
    status: str = "running"
    phase: str = "exporting"
    size: int | None = None
    error: str | None = None
    finished_at: float | None = None
    task: asyncio.Task | None = dataclasses.field(default=None, repr=False)

    @property
    def filename(self) -> str:
        """The name the zip has, and is downloaded with."""
        return self.name + ".zip"

    @property
    def path(self) -> str:
        """The path of the zip. It is built here, never from a request."""
        return os.path.join(self.directory, self.filename)

    def as_json(self) -> dict:
        """Describe the state of the job for the page.

        return: the state of the job, without any path.

        """
        return {"status": self.status, "phase": self.phase,
                "size": self.size, "error": self.error}


class ExportJobStore:
    """The export jobs of this process, one running at a time."""

    def __init__(self, directory: str,
                 clock: Callable[[], float] = time.monotonic,
                 build_argv: ArgvBuilder = export_argv):
        """Create an empty store.

        directory: where the jobs write; only this process uses it.
        clock: the source of time, replaceable for the tests.
        build_argv: builds the command of the exporter, replaceable for
            the tests.

        """
        self._directory = directory
        self._clock = clock
        self._build_argv = build_argv
        self._jobs: dict[str, ExportJob] = {}

    def wipe(self) -> None:
        """Remove what a previous AWS process left in the directory."""
        shutil.rmtree(self._directory, ignore_errors=True)

    def _evict(self) -> None:
        """Forget the finished jobs older than JOB_TTL, with their files."""
        cutoff = self._clock() - JOB_TTL
        for job_id in [i for i, j in self._jobs.items()
                       if j.finished_at is not None
                       and j.finished_at < cutoff]:
            shutil.rmtree(self._jobs.pop(job_id).directory,
                          ignore_errors=True)

    def running_job(self) -> ExportJob | None:
        """Return the job that is running, whoever started it, if any.

        return: the job, or None.

        """
        self._evict()
        return next((j for j in self._jobs.values()
                     if j.status == "running"), None)

    def start(self, owner_id: int, contest_ids: Sequence[int],
              flags: Sequence[str]) -> ExportJob:
        """Start exporting some contests; call it in the event loop.

        owner_id: the admin who starts it.
        contest_ids: the contests, already checked to exist.
        flags: the flags for the exporter, chosen by the code.

        return: the job.

        raise (ValueError): if an export is running, or the disk has too
            little free space; the message is meant for the admin.

        """
        if self.running_job() is not None:
            raise ValueError(BUSY)
        os.makedirs(self._directory, mode=0o700, exist_ok=True)
        # The exporter's FileCacher also keeps a copy of every file it reads
        # in the temp_dir of the configuration, until it exits.
        for path in (self._directory, config.global_.temp_dir):
            if shutil.disk_usage(path).free < MIN_FREE_BYTES:
                raise ValueError(NO_SPACE % (MIN_FREE_BYTES // 1024 ** 3))
        job = ExportJob(
            id=secrets.token_urlsafe(16), owner_id=owner_id,
            contest_ids=tuple(contest_ids), flags=tuple(flags),
            created_at=self._clock(),
            directory=tempfile.mkdtemp(dir=self._directory),
            name=dump_name(contest_ids, date.today()))
        self._jobs[job.id] = job
        job.task = asyncio.get_running_loop().create_task(self._run(job))
        return job

    def get(self, job_id: str, owner_id: int) -> ExportJob | None:
        """Return a job, if it exists, has not expired and is the owner's.

        job_id: the identifier of the job.
        owner_id: the admin who asks; the jobs of other admins are hidden.

        return: the job, or None.

        """
        self._evict()
        job = self._jobs.get(job_id)
        if job is None or job.owner_id != owner_id:
            return None
        return job

    def jobs_of(self, owner_id: int) -> list[ExportJob]:
        """Return the jobs of an admin, the newest first.

        owner_id: the admin.

        return: the jobs that have not expired.

        """
        self._evict()
        return sorted((j for j in self._jobs.values()
                       if j.owner_id == owner_id),
                      key=lambda j: j.created_at, reverse=True)

    async def _finish(self, job: ExportJob, error: str | None) -> None:
        """End a job: remove its files if it failed, and set its timer.

        job: the job.
        error: why it failed, or None if its zip is ready.

        """
        loop = asyncio.get_running_loop()
        if error is not None:
            await loop.run_in_executor(
                None, shutil.rmtree, job.directory, True)
        job.error = error
        job.status = "done" if error is None else "error"
        job.finished_at = self._clock()
        # So that the file goes away even if nobody asks for the job again.
        loop.call_later(JOB_TTL + 1, self._evict)

    async def _run(self, job: ExportJob) -> None:
        """Export, pack and report; the body of the task of a job.

        A failure is logged with the exit code of the exporter, or the
        class of the exception and its frames, and nothing else: what the
        exporter or the database say may carry the data of the contest.

        job: the job to run, updated as it goes.

        """
        loop = asyncio.get_running_loop()
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *self._build_argv(job.contest_ids, job.flags,
                                  os.path.join(job.directory, job.name)),
                env={**os.environ, "TMPDIR": job.directory},
                stdin=asyncio.subprocess.DEVNULL)
            code = await process.wait()
            if code != 0:
                logger.error("Dump export by admin %d failed: the exporter "
                             "exited with code %d.", job.owner_id, code)
                await self._finish(job, EXPORT_FAILED % code)
                return
            job.phase = "packing"
            await loop.run_in_executor(None, pack_zip, job.directory,
                                       job.name)
            job.size = os.path.getsize(job.path)
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.kill()
            shutil.rmtree(job.directory, ignore_errors=True)
            raise
        except Exception as exc:
            logger.error("Dump export by admin %d failed with %s.\n%s",
                         job.owner_id, type(exc).__name__,
                         "".join(traceback.format_tb(exc.__traceback__)))
            await self._finish(job, UNEXPECTED_FAILURE)
            return
        await self._finish(job, None)
        logger.info("Dump export %s by admin %d finished: %d bytes in "
                    "%.1f s.", job.name, job.owner_id, job.size,
                    job.finished_at - job.created_at)


EXPORT_JOBS = ExportJobStore(
    os.path.join(config.global_.data_dir, "aws-exports"))
