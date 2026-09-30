#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2016 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2016 Luca Versari <veluca93@gmail.com>
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

from collections.abc import Awaitable, Callable
import asyncio
import logging
import threading
import time
import typing


logger = logging.getLogger(__name__)


KeyT = typing.TypeVar("KeyT")
ValueT = typing.TypeVar("ValueT")


class FlushingDict(typing.Generic[KeyT, ValueT]):
    """A dict that periodically flushes its content to a callback.

    The dict flushes after a specified time since the latest entry
    was added, or when it has reached its maximum size.

    This dict is thread safe. Keys must be hashable. New values for an
    existing keys will overwrite the previous values.

    """

    def __init__(
        self,
        size: int,
        flush_latency_seconds: float,
        callback: Callable[[list[tuple[KeyT, ValueT]]], Awaitable[typing.Any]],
    ):
        # Elements contained in the dict that force a flush.
        self.size = size

        # How much time we wait for other key-values before flushing.
        self.flush_latency_seconds = flush_latency_seconds

        # Function to flush the data to.
        self.callback = callback

        # This contains all the key-values received and not yet
        # flushed.
        self.d: dict[KeyT, ValueT] = dict()

        # The batches of key-values currently being flushed, in the
        # order the flushes took them. A list, since two flushes can
        # overlap (e.g. an explicit flush() while the background one is
        # still writing): each one must stay visible to __contains__,
        # fd and discard() until its own callback returns.
        self._flushing: list[dict[KeyT, ValueT]] = []

        # The task that checks if the dict should be flushed or not
        # TODO: do something if the FlushingDict is deleted
        self._flush_task: asyncio.Task | None = None

        # This lock ensures that if a key-value arrives while flush is
        # executing, it is not inserted in the dict until flush
        # terminates.
        self.d_lock = threading.RLock()

        # Time when an item was last inserted in the dict
        self.last_insert = time.monotonic()

    def start(self):
        """Start the background flush loop.

        Must be called once the event loop is running (e.g. via
        AsyncService._call_when_running from the owning service's
        __init__) -- constructing a FlushingDict itself never touches
        the event loop, since it may happen before one exists.

        """
        self._flush_task = asyncio.create_task(self._check_flush())

    def add(self, key: KeyT, value: ValueT):
        logger.debug("Adding item %s", key)
        with self.d_lock:
            self.d[key] = value
            self.last_insert = time.monotonic()

    async def flush(self):
        logger.debug("Flushing items")
        with self.d_lock:
            batch = self.d
            self.d = dict()
            self._flushing.append(batch)
            items = list(batch.items())
        try:
            await self.callback(items)
        except Exception:
            # Otherwise the background flush task would die silently,
            # leaving the batch in self._flushing forever.
            logger.error("Unexpected error while flushing.", exc_info=True)
        finally:
            with self.d_lock:
                # By identity: two batches may compare equal (e.g. both
                # emptied by discard()).
                self._flushing = [
                    flushing for flushing in self._flushing
                    if flushing is not batch]

    @property
    def fd(self) -> dict[KeyT, ValueT]:
        """Return the key-values currently being flushed.

        A merged copy of every batch in flight, for callers that only
        look at it (e.g. tests checking that nothing is left in flight).

        return: the key-values of every flush in progress.

        """
        with self.d_lock:
            merged: dict[KeyT, ValueT] = {}
            for batch in self._flushing:
                merged.update(batch)
            return merged

    def discard(self, predicate: Callable[[KeyT], bool]) -> list[ValueT]:
        """Remove the key-values whose key matches a predicate.

        Both the key-values not flushed yet and the ones in a batch
        being flushed are removed. The values removed from a batch
        being flushed were already handed to the callback (its list of
        items is built when the flush starts), so the callback may
        still process them: the caller must neutralize them, e.g. by
        marking them so that the callback skips them.

        predicate: called with each key; True means remove it.

        return: the values removed, pending ones first.

        """
        removed: list[ValueT] = []
        with self.d_lock:
            for mapping in [self.d, *self._flushing]:
                for key in [key for key in mapping if predicate(key)]:
                    removed.append(mapping.pop(key))
        return removed

    def __contains__(self, key):
        with self.d_lock:
            return key in self.d or any(
                key in batch for batch in self._flushing)

    async def _check_flush(self):
        while True:
            while True:
                with self.d_lock:
                    since_last_insert = time.monotonic() - self.last_insert
                    if len(self.d) != 0 and (
                            len(self.d) >= self.size or
                            since_last_insert > self.flush_latency_seconds):
                        break
                await asyncio.sleep(0.05)
            await self.flush()
