import heapq

from .tasks import Task


class Scheduler:
    """A priority-queue task scheduler backed by a min-heap.

    Tasks are ordered by priority; lower numbers run first.
    """

    def __init__(self):
        self._heap = []

    def submit(self, task):
        """Add a task to the queue."""
        heapq.heappush(self._heap, task)

    def next(self):
        """Pop and return the highest-priority task.

        Raises IndexError if the queue is empty.
        """
        if not self._heap:
            raise IndexError("next() called on an empty scheduler")
        return heapq.heappop(self._heap)

    def __len__(self):
        return len(self._heap)

    def is_empty(self):
        return not self._heap
