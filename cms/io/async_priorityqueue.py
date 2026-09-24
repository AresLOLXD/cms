#!/usr/bin/env python3

"""Asyncio priority queue, mirroring cms.io.priorityqueue.PriorityQueue.

NOT an in-place edit of priorityqueue.py -- see the design spec's Risks
section for why that would silently break EvaluationService/
ScoringService/ProxyService (still gevent-based TriggeredService
consumers, unmigrated until sub-project 2.4). This is a full parallel
implementation; top()/pop() are async def, since asyncio.Event has no
synchronous blocking .wait() the way gevent.event.Event does.

"""

import asyncio
from datetime import datetime
import typing

from cmscommon.datetime import make_datetime, make_timestamp

from cms.io.priorityqueue import QueueEntry, QueueEntryDict, QueueItemT


class AsyncPriorityQueue(typing.Generic[QueueItemT]):

    """A priority queue.

    It is asyncio-safe, and offers the ability of changing priorities
    and removing arbitrary items.

    The queue is implemented as a custom min-heap. The priority is a
    mix of a discrete priority level and of the timestamp. The
    elements of the queue are QueueItems.

    See cms.io.priorityqueue.PriorityQueue for the full contract this
    mirrors. The heap logic is identical; only the blocking-wait
    mechanism in top()/pop() differs.

    """

    PRIORITY_EXTRA_HIGH = 0
    PRIORITY_HIGH = 1
    PRIORITY_MEDIUM = 2
    PRIORITY_LOW = 3
    PRIORITY_EXTRA_LOW = 4

    def __init__(self):
        """Create a priority queue."""
        # The queue: a min-heap whose elements are of the form
        # (priority, timestamp, item), where item is the actual data.
        self._queue: list[QueueEntry[QueueItemT]] = []

        # Reverse lookup for the items in the queue: a dictionary
        # associating the index in the queue to each item.
        self._reverse: dict[QueueItemT, int] = {}

        # Event to signal that there are items in the queue.
        self._event = asyncio.Event()

        # Index of the next element that will be added to the queue.
        self._next_index = 0

    def __len__(self):
        return len(self._queue)

    def __contains__(self, item: QueueItemT) -> bool:
        """Implement the 'in' operator for an item in the queue.

        item: an item to search.

        return: True if item is in the queue.

        """
        return item in self._reverse

    def _swap(self, idx1: int, idx2: int):
        """Swap two elements in the queue, keeping their reverse
        indices up to date.

        idx1: the index of the first element.
        idx2: the index of the second element.

        """
        self._queue[idx1], self._queue[idx2] = \
            self._queue[idx2], self._queue[idx1]
        self._reverse[self._queue[idx1].item] = idx1
        self._reverse[self._queue[idx2].item] = idx2

    def _up_heap(self, idx: int) -> int:
        """Take the element in position idx up in the heap until its
        position is the right one.

        idx: the index of the element to lift.

        return: the new index of the element.

        """
        while idx > 0:
            parent = (idx - 1) // 2
            if self._queue[idx] < self._queue[parent]:
                self._swap(parent, idx)
                idx = parent
            else:
                break
        return idx

    def _down_heap(self, idx: int) -> int:
        """Take the element in position idx down in the heap until its
        position is the right one.

        idx: the index of the element to lower.

        return: the new index of the element.

        """
        last = len(self._queue) - 1
        while 2 * idx + 1 <= last:
            child = 2 * idx + 1
            if 2 * idx + 2 <= last and \
                    self._queue[2 * idx + 2] < self._queue[child]:
                child = 2 * idx + 2
            if self._queue[child] < self._queue[idx]:
                self._swap(child, idx)
                idx = child
            else:
                break
        return idx

    def _updown_heap(self, idx: int) -> int:
        """Perform both operations of up_heap and down_heap on an
        element.

        idx: the index of the element to lift.

        return: the new index of the element.

        """
        idx = self._up_heap(idx)
        return self._down_heap(idx)

    def push(
        self,
        item: QueueItemT,
        priority: int | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        """Push an item in the queue. If timestamp is not specified,
        uses the current time.

        item: the item to add to the queue.
        priority: the priority of the item, or None for
            medium priority.
        timestamp: the time of the submission, or None
            to use now.

        return: false if the element was already in the queue
            and was not pushed again, true otherwise.

        """
        if item in self._reverse:
            return False

        if priority is None:
            priority = AsyncPriorityQueue.PRIORITY_MEDIUM
        if timestamp is None:
            timestamp = make_datetime()

        index = self._next_index
        self._next_index += 1

        self._queue.append(QueueEntry(item, priority, timestamp, index))
        last = len(self._queue) - 1
        self._reverse[item] = last
        self._up_heap(last)

        # Signal to listener tasks that there might be something.
        self._event.set()

        return True

    async def top(self, wait: bool = False) -> QueueEntry[QueueItemT]:
        """Return the first element in the queue without extracting it.

        wait: if True, block until an element is present.

        return: first element in the queue.

        raise (LookupError): on empty queue if wait was false.

        """
        if not self.empty():
            return self._queue[0]
        else:
            if not wait:
                raise LookupError("Empty queue.")
            else:
                while True:
                    if self.empty():
                        await self._event.wait()
                        continue
                    return self._queue[0]

    async def pop(self, wait: bool = False) -> QueueEntry[QueueItemT]:
        """Extract (and return) the first element in the queue.

        wait: if True, block until an element is present.

        return: first element in the queue.

        raise (LookupError): on empty queue, if wait was false.

        """
        top = await self.top(wait)
        last = len(self._queue) - 1
        self._swap(0, last)

        del self._reverse[top.item]
        del self._queue[last]

        # last is 0 when the queue becomes empty.
        if last > 0:
            self._down_heap(0)
        else:
            # Signal that there is nothing left for listeners.
            self._event.clear()
        return top

    def remove(self, item: QueueItemT) -> QueueEntry[QueueItemT]:
        """Remove an item from the queue. Raise a KeyError if not present.

        item: the item to remove.

        return: the complete entry removed.

        raise (KeyError): if item not present.

        """
        pos = self._reverse[item]
        entry = self._queue[pos]

        last = len(self._queue) - 1
        self._swap(pos, last)

        del self._reverse[item]
        del self._queue[last]
        if pos != last:
            self._updown_heap(pos)

        if self.empty():
            self._event.clear()

        return entry

    def set_priority(self, item: QueueItemT, priority: int):
        """Change the priority of an item inside the queue. Raises an
        exception if the item is not in the queue.

        item: the item whose priority needs to change.
        priority: the new priority.

        raise (LookupError): if item not present.

        """
        pos = self._reverse[item]
        self._queue[pos].priority = priority
        self._updown_heap(pos)

    def length(self) -> int:
        """Return the number of elements in the queue.

        return: length of the queue

        """
        return len(self._queue)

    def empty(self) -> bool:
        """Return if the queue is empty.

        return: is the queue empty?

        """
        return self.length() == 0

    def get_status(self) -> list[QueueEntryDict]:
        """Return the content of the queue. Note that the order may be not
        correct, but the first element is the one at the top.

        return: a list of entries containing the
            representation of the item, the priority and the
            timestamp.

        """
        return [{'item': entry.item.to_dict(),
                 'priority': entry.priority,
                 'timestamp': make_timestamp(entry.timestamp)}
                for entry in self._queue]
