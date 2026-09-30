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

"""Log handlers must be usable from several OS threads at once.

The handlers of cms.log (StreamHandler, FileHandler, LogServiceHandler)
used a gevent RLock as their lock. A gevent lock only works across OS
threads if the thread that "owns" its hub keeps running its gevent loop,
which the thread of an asyncio event loop never does. Once such a
handler is attached to the root logger, an executor thread logging
concurrently with the event loop thread gets stuck forever (or the event
loop thread's logging call raises LoopExit).

"""

import logging
import os
import tempfile
import threading
import unittest

import gevent

from cms.log import FileHandler


class TestLogHandlerAcrossThreads(unittest.TestCase):

    def setUp(self):
        # Like the gevent-based tests that ran earlier in the same
        # process: the main thread owns a gevent hub which nobody runs.
        gevent.get_hub()
        fd, self.path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        self.handler = FileHandler(self.path, mode="w", encoding="utf-8")
        self.logger = logging.getLogger(self.id())
        self.logger.propagate = False
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(self.handler)

    def tearDown(self):
        self.logger.removeHandler(self.handler)
        self.handler.close()
        os.unlink(self.path)

    def _release_while_worker_waits(self, worker_creates_hub):
        # The event loop thread is inside emit() (holding the handler
        # lock) when an executor thread starts logging ...
        self.handler.acquire()
        self.handler.release()  # first use: the lock is now tied to our hub
        self.handler.acquire()

        started = threading.Event()
        finished = threading.Event()

        def worker():
            if worker_creates_hub:
                gevent.get_hub()  # what psycopg's gevent wait callback does
            started.set()
            self.logger.error("logged from an executor thread")
            finished.set()

        threading.Thread(target=worker, daemon=True).start()
        self.assertTrue(started.wait(5))
        # ... so the executor thread has to wait for the lock.
        self.assertFalse(finished.wait(0.3), "the worker did not have to wait")
        # The event loop thread finishes emitting and releases the lock.
        self.handler.release()
        return finished

    def test_waiting_executor_thread_is_woken_up(self):
        finished = self._release_while_worker_waits(worker_creates_hub=True)
        self.assertTrue(finished.wait(3),
                        "the executor thread was never woken")

    def test_waiting_executor_thread_without_hub_is_woken_up(self):
        finished = self._release_while_worker_waits(worker_creates_hub=False)
        self.assertTrue(finished.wait(3),
                        "the executor thread was never woken")

    def test_concurrent_logging_from_several_threads(self):
        records = 2000
        errors = []
        finished = []

        def worker():
            try:
                for i in range(records):
                    self.logger.info("executor thread record %d", i)
                finished.append(True)
            except BaseException as e:
                errors.append(repr(e))

        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(2)]
        for t in threads:
            t.start()
        try:
            # The main thread plays the event loop thread.
            for i in range(records):
                self.logger.info("main thread record %d", i)
        except BaseException as e:
            errors.append(repr(e))
        for t in threads:
            t.join(5)

        self.assertEqual(errors, [])
        self.assertEqual(len(finished), len(threads), "a worker thread hung")


if __name__ == "__main__":
    unittest.main()
