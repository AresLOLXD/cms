#!/usr/bin/env python3

"""Tests proving that a raw, blocking psycopg2 connection (the same
kind LargeObject's custom_psycopg2_connection() opens, cms/db/fsobject.py)
behaves correctly when opened from inside an asyncio
loop.run_in_executor() worker thread, while the process-wide gevent
wait callback (cms.io.PsycoGevent.make_psycopg_green()) is active.

See docs/superpowers/specs/2026-09-25-async-db-access-design.md's Risks
section: this combination -- an AsyncService (sub-project 2.4) calling
FileCacher's default DBBackend, which uses LargeObject, via the
documented run_in_executor bridge (sub-project 2.2's spec) -- was left
as an untested assumption by sub-project 2.2. This test resolves it.
Outcome: SAFE -- queries complete, without hanging, on a separate OS
thread, because each executor worker thread lazily gets its own
private, thread-local gevent Hub for the wait callback to block on.

The query checks run in a fresh interpreter rather than in the pytest
process: several cmscontrib modules (and some unit-test modules) call
gevent.monkey.patch_all() at import time, and pytest imports them while
collecting the full suite. Once threading is monkey-patched,
ThreadPoolExecutor workers are greenlets on the main OS thread, which
is not the condition a real CMS service (which never monkey-patches)
runs under.

"""

import json
import os
import subprocess
import sys
import unittest

# Importing from cms.io triggers make_psycopg_green() unconditionally at
# import time (cms/io/__init__.py) -- exactly the condition every real
# CMS service process runs under today, and that any AsyncService in
# sub-project 2.4 (which also imports cms.io indirectly, via
# cms.io.async_service) would run under too.
import cms
from cms.io.PsycoGevent import is_psycopg_green


# Run as `python -c _CHILD_SCRIPT <query_count>`. Prints a single JSON
# line with what the parent test needs to assert on.
_CHILD_SCRIPT = """
import asyncio
import json
import sys
import threading

import gevent.monkey

from cms.db import custom_psycopg2_connection
from cms.io.PsycoGevent import is_psycopg_green


def run_query_in_thread(marker):
    conn = custom_psycopg2_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT %s", (marker,))
            return cur.fetchone()[0], threading.get_native_id()
    finally:
        conn.close()


async def main(query_count):
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        asyncio.gather(*(
            loop.run_in_executor(None, run_query_in_thread, marker)
            for marker in range(query_count)
        )),
        timeout=10)


query_count = int(sys.argv[1])
report = {
    "psycopg_green": is_psycopg_green(),
    "threading_patched": gevent.monkey.is_module_patched("threading"),
    "loop_thread_id": threading.get_native_id(),
    "results": asyncio.run(main(query_count)),
}
print(json.dumps(report))
"""


def _run_queries_in_clean_interpreter(query_count: int) -> dict:
    """Run query_count executor-thread queries in a fresh interpreter.

    query_count: how many queries to run concurrently, each in its own
        run_in_executor call.

    return: the child's report (see _CHILD_SCRIPT).

    """
    # Make sure the child imports the same cms package as this process,
    # whether it is installed or only importable from the source tree.
    project_root = os.path.dirname(os.path.dirname(cms.__file__))
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        path for path in (project_root, env.get("PYTHONPATH")) if path)
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT, str(query_count)],
        env=env, capture_output=True, text=True, timeout=60)
    if completed.returncode != 0:
        raise AssertionError(
            "Child interpreter failed with exit code %d.\n"
            "stdout:\n%s\nstderr:\n%s" % (
                completed.returncode, completed.stdout, completed.stderr))
    return json.loads(completed.stdout.strip().splitlines()[-1])


class TestRawConnectionInExecutorThread(unittest.TestCase):

    def test_precondition_gevent_wait_callback_is_active(self):
        # If this fails, the tests below wouldn't actually be exercising
        # the risk at all -- cms.io's own import-time side effect is
        # what's supposed to guarantee this.
        self.assertTrue(is_psycopg_green())

    def assert_ran_on_separate_os_threads(self, report: dict) -> None:
        # The conditions that make this a real test of the production
        # risk rather than a vacuous pass.
        self.assertTrue(report["psycopg_green"])
        self.assertFalse(report["threading_patched"])
        for _, worker_thread_id in report["results"]:
            self.assertNotEqual(worker_thread_id, report["loop_thread_id"])

    def test_query_completes_from_executor_thread(self):
        report = _run_queries_in_clean_interpreter(1)
        self.assert_ran_on_separate_os_threads(report)
        self.assertEqual([marker for marker, _ in report["results"]], [0])

    def test_concurrent_queries_from_multiple_executor_threads(self):
        # A single query completing could still hide a hang that only
        # shows up under real concurrency (e.g. the gevent wait callback
        # serializing on some global state it shouldn't). Run several at
        # once, in the default executor's thread pool.
        report = _run_queries_in_clean_interpreter(5)
        self.assert_ran_on_separate_os_threads(report)
        self.assertEqual(
            sorted(marker for marker, _ in report["results"]),
            list(range(5)))
