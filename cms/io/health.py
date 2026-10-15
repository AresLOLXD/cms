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

"""Health reporting of a CMS service: busy sections and stack dumps.

A service marks the parts of its work that must never hang (a locked
section of EvaluationService, an execution of ScoringService or
ProxyService) with a BusyTracker, and its health() RPC reports them:
their age, how long ago they last made progress and, once old enough,
the stack of the thread that holds them. cmsWatchdog (#38) reads that
to tell a slow section from a stuck one.

This module imports only the standard library at module level:
BusyTracker.count_statements_of imports sqlalchemy.event when called.

"""

import contextlib
import faulthandler
import signal
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any


# How many frames of the holder's stack report() gives (the innermost).
STACK_FRAMES = 30


@dataclass
class _Section:
    """One thread's current section (see BusyTracker)."""

    what: str
    tid: int
    start: float
    statements: int
    last_statement: float


class BusyTracker:
    """Track the sections of a service that must not hang, per thread.

    A thread starts a section, may count steps of progress in it (a
    database statement, an HTTP request) and stops it. Each thread
    changes only its own record, and report() works on a copy of the
    records (list(dict.items()) is atomic under the GIL), so there is no
    lock: report() never waits for a section, whatever the section
    holds.

    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        """Create a tracker with no section.

        clock: the monotonic clock to measure with (tests pass a fake).

        """
        self._clock = clock
        # By threading.get_ident(), the key of sys._current_frames().
        self._sections: dict[int, _Section] = {}

    @contextlib.contextmanager
    def busy(self, what: str) -> Iterator[None]:
        """Run the body of a with statement as the section what.

        what: the name of the section, as report() gives it.

        """
        self.start(what)
        try:
            yield
        finally:
            self.stop()

    def start(self, what: str) -> None:
        """Start a section in the current thread.

        A record the thread left behind (a section never stopped) is
        replaced.

        what: the name of the section, as report() gives it.

        """
        now = self._clock()
        self._sections[threading.get_ident()] = _Section(
            what, threading.get_native_id(), now, 0, now)

    def stop(self) -> None:
        """End the current thread's section, if it has one."""
        self._sections.pop(threading.get_ident(), None)

    def step(self) -> None:
        """Count one step of progress in the current thread's section.

        Outside a section it does nothing.

        """
        section = self._sections.get(threading.get_ident())
        if section is not None:
            section.statements += 1
            section.last_statement = self._clock()

    def count_statements_of(self, engine: Any) -> None:
        """Count every statement that engine runs as a step.

        A statement counts for the thread that runs it, so only the
        statements of a thread inside a section count.

        engine: a SQLAlchemy Engine.

        """
        from sqlalchemy import event
        event.listen(engine, "before_cursor_execute", self._on_statement)

    def _on_statement(self, *args: Any) -> None:
        """Count a statement (SQLAlchemy's before_cursor_execute)."""
        self.step()

    def report(self, stack_after_s: float = 30.0) -> list[dict]:
        """Describe the sections in progress, the oldest first.

        stack_after_s: from which age an entry carries the stack of its
            thread.

        return: one dict per section: "what"; "tid", the holder's
            native thread id (as in /proc/<pid>/task/); "age_s";
            "statements", the steps counted; "idle_s", the seconds
            since the last step, or since the start without one; and
            "stack", the innermost STACK_FRAMES frames of the holder as
            text, or None while the section is younger than
            stack_after_s.

        """
        now = self._clock()
        sections = sorted(list(self._sections.items()),
                          key=lambda item: item[1].start)
        frames: dict[int, Any] | None = None
        result = []
        for ident, section in sections:
            age = now - section.start
            stack = None
            if age >= stack_after_s:
                if frames is None:
                    frames = sys._current_frames()
                frame = frames.get(ident)
                if frame is not None:
                    stack = "".join(
                        traceback.format_stack(frame)[-STACK_FRAMES:])
            result.append({
                "what": section.what,
                "tid": section.tid,
                "age_s": age,
                "statements": section.statements,
                "idle_s": now - section.last_statement,
                "stack": stack,
            })
        return result


def register_stack_dump() -> None:
    """Make SIGUSR1 write the stack of every thread to stderr.

    The process goes on running: faulthandler writes the stacks and
    returns. cmsWatchdog (#38) sends SIGUSR1 to a program before it
    restarts it, so that ./logs.sh shows where it was stuck. Call it
    from the method that runs a service, never from __init__: a service
    built in a test must not change the test process.

    """
    faulthandler.register(signal.SIGUSR1, file=sys.__stderr__,
                          all_threads=True)
