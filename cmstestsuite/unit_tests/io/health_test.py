#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""Tests for cms.io.health: busy sections and the USR1 stack dump."""

import signal
import subprocess
import sys
import textwrap
import threading
import time
import unittest

from sqlalchemy import create_engine, text

from cms.io.health import STACK_FRAMES, BusyTracker
from cmstestsuite.unit_tests.healthfakes import FakeClock


class TestBusyTracker(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        self.tracker = BusyTracker(clock=self.clock)
        self.release = threading.Event()
        self.addCleanup(self.release.set)

    def _hold(self, what: str) -> tuple[threading.Thread, list[int]]:
        """Start a thread that holds the section what until release.

        return: the thread, and a list that gets its native id.

        """
        entered = threading.Event()
        tids: list[int] = []

        def holder_section():
            tids.append(threading.get_native_id())
            with self.tracker.busy(what):
                entered.set()
                self.release.wait(10)

        thread = threading.Thread(target=holder_section, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(5))
        return thread, tids

    def test_a_section_belongs_to_its_thread(self):
        thread, tids = self._hold("score")

        # Another thread's stop() does not end it.
        self.tracker.stop()
        report = self.tracker.report()

        self.assertEqual([entry["what"] for entry in report], ["score"])
        self.assertEqual(report[0]["tid"], tids[0])
        self.assertNotEqual(report[0]["tid"], threading.get_native_id())
        self.release.set()
        thread.join(5)
        self.assertEqual(self.tracker.report(), [])

    def test_the_report_lists_the_oldest_first(self):
        older, _ = self._hold("older")
        self.clock.now += 5
        newer, _ = self._hold("newer")
        self.clock.now += 1

        report = self.tracker.report()

        self.assertEqual([entry["what"] for entry in report],
                         ["older", "newer"])
        self.assertEqual([entry["age_s"] for entry in report], [6.0, 1.0])
        self.release.set()
        older.join(5)
        newer.join(5)

    def test_the_stack_comes_only_after_its_threshold(self):
        thread, _ = self._hold("post_finish_lock:x")

        self.clock.now += 29
        self.assertIsNone(self.tracker.report(stack_after_s=30)[0]["stack"])
        self.clock.now += 1
        stack = self.tracker.report(stack_after_s=30)[0]["stack"]

        self.assertIn("holder_section", stack)
        self.assertLessEqual(stack.count('File "'), STACK_FRAMES)
        self.release.set()
        thread.join(5)

    def test_idle_follows_the_steps(self):
        self.tracker.start("send")
        self.clock.now += 10
        self.assertEqual(self.tracker.report()[0]["idle_s"], 10)

        self.tracker.step()
        self.clock.now += 4
        entry = self.tracker.report()[0]

        self.assertEqual(
            (entry["statements"], entry["idle_s"], entry["age_s"]),
            (1, 4, 14))
        self.tracker.stop()

    def test_a_step_outside_a_section_does_nothing(self):
        self.tracker.step()
        self.assertEqual(self.tracker.report(), [])

    def test_start_replaces_a_stale_record_of_its_thread(self):
        self.tracker.start("stale")
        self.clock.now += 50
        self.tracker.start("fresh")

        self.assertEqual(
            [(entry["what"], entry["age_s"])
             for entry in self.tracker.report()],
            [("fresh", 0)])
        self.tracker.stop()
        self.assertEqual(self.tracker.report(), [])

    def test_the_section_ends_when_its_body_raises(self):
        with self.assertRaises(ValueError):
            with self.tracker.busy("score"):
                raise ValueError("boom")
        self.assertEqual(self.tracker.report(), [])

    def test_concurrent_starts_and_stops(self):
        tracker = BusyTracker()
        errors: list[Exception] = []

        def churn(index: int):
            try:
                for _ in range(500):
                    with tracker.busy("section %d" % index):
                        tracker.step()
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=churn, args=(index,))
                   for index in range(8)]
        for thread in threads:
            thread.start()
        while any(thread.is_alive() for thread in threads):
            for entry in tracker.report(stack_after_s=0):
                self.assertTrue(entry["what"].startswith("section "))
        for thread in threads:
            thread.join(10)

        self.assertEqual(errors, [])
        self.assertEqual(tracker.report(), [])

    def test_the_statements_of_an_engine_are_steps_of_their_thread(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        self.tracker.count_statements_of(engine)

        with engine.connect() as connection:
            # Outside a section: not counted.
            connection.execute(text("SELECT 1"))
            self.tracker.start("post_finish_lock:x")
            self.clock.now += 3
            connection.execute(text("SELECT 1"))
            connection.execute(text("SELECT 2"))
            entry = self.tracker.report()[0]
            self.tracker.stop()

        self.assertEqual(entry["statements"], 2)
        self.assertEqual(entry["idle_s"], 0)


class TestRegisterStackDump(unittest.TestCase):

    def test_usr1_dumps_the_stacks_and_the_process_lives_on(self):
        script = textwrap.dedent("""\
            import time
            from cms.io.health import register_stack_dump
            register_stack_dump()
            print("ready", flush=True)
            time.sleep(60)
            """)
        process = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            process.send_signal(signal.SIGUSR1)
            time.sleep(0.5)
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            _, stderr = process.communicate(timeout=10)
        self.assertIn("Current thread", stderr)


if __name__ == "__main__":
    unittest.main()
