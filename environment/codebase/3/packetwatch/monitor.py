"""A small throughput calculator.

Tracks the number of bytes observed over time and reports the current
bytes-per-second rate.
"""

import time


class ThroughputMonitor:
    def __init__(self):
        self._bytes = 0
        self._start = time.monotonic()

    def add(self, nbytes: int) -> None:
        self._bytes += nbytes

    @property
    def bytes_seen(self) -> int:
        return self._bytes

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def rate(self) -> float:
        elapsed = self.elapsed
        if elapsed <= 0:
            return 0.0
        return self._bytes / elapsed

    def reset(self) -> None:
        self._bytes = 0
        self._start = time.monotonic()
