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

"""

import asyncio
import unittest

# Importing cms.io triggers make_psycopg_green() unconditionally at
# import time (cms/io/__init__.py) -- exactly the condition every real
# CMS service process runs under today, and that any AsyncService in
# sub-project 2.4 (which also imports cms.io indirectly, via
# cms.io.async_service) would run under too.
import cms.io  # noqa: F401
from cms.db import custom_psycopg2_connection
from cms.io.PsycoGevent import is_psycopg_green


class TestRawConnectionInExecutorThread(unittest.IsolatedAsyncioTestCase):

    def test_precondition_gevent_wait_callback_is_active(self):
        # If this fails, the test below wouldn't actually be exercising
        # the risk at all -- cms.io's own import-time side effect is
        # what's supposed to guarantee this.
        self.assertTrue(is_psycopg_green())

    async def test_query_completes_from_executor_thread(self):
        def run_query_in_thread():
            conn = custom_psycopg2_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return cur.fetchone()
            finally:
                conn.close()

        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, run_query_in_thread), timeout=5)
        self.assertEqual(result, (1,))

    async def test_concurrent_queries_from_multiple_executor_threads(self):
        # A single query completing could still hide a hang that only
        # shows up under real concurrency (e.g. the gevent wait callback
        # serializing on some global state it shouldn't). Run several at
        # once, in the default executor's thread pool.
        def run_query_in_thread(marker: int):
            conn = custom_psycopg2_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT %s", (marker,))
                    return cur.fetchone()
            finally:
                conn.close()

        loop = asyncio.get_running_loop()
        results = await asyncio.wait_for(
            asyncio.gather(*(
                loop.run_in_executor(None, run_query_in_thread, i)
                for i in range(5)
            )),
            timeout=10)
        self.assertEqual(sorted(r[0] for r in results), list(range(5)))
