"""Oracle tests for environment/codebase/4 (QueueMaster).

Bug: ``backoff_delay`` computes ``base * 2 ** attempt`` instead of
``base * 2 ** (attempt - 1)``, so every retry waits one doubling step too long
(attempt 1 waits ``2 * base`` instead of ``base``).

These tests import the real ``queuemaster`` package from the codebase, so they
fail while the off-by-one is present and pass once it is fixed.
"""

import os
import sys
import unittest


def _add_codebase_to_path(marker, number):
    """Put the directory containing ``marker`` for codebase ``number`` on sys.path."""
    candidates = []
    directory = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        candidates.append(os.path.join(directory, "codebase", str(number)))
        candidates.append(os.path.join(directory, str(number)))
        candidates.append(directory)
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    directory = os.path.abspath(os.getcwd())
    for _ in range(6):
        candidates.append(os.path.join(directory, "codebase", str(number)))
        candidates.append(directory)
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, marker)):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return candidate
    raise RuntimeError(f"could not locate codebase {number} containing {marker}")


_add_codebase_to_path(os.path.join("queuemaster", "__init__.py"), 4)

from queuemaster.backoff import backoff_delay  # noqa: E402


class TestBackoffDelay(unittest.TestCase):
    def test_first_retry_waits_base(self):
        self.assertEqual(backoff_delay(1), 1.0)
        self.assertEqual(backoff_delay(1, base=0.5), 0.5)
        self.assertEqual(backoff_delay(1, base=2.0), 2.0)

    def test_delays_double_each_attempt(self):
        self.assertEqual(backoff_delay(1), 1.0)
        self.assertEqual(backoff_delay(2), 2.0)
        self.assertEqual(backoff_delay(3), 4.0)
        self.assertEqual(backoff_delay(4), 8.0)
        self.assertEqual(backoff_delay(5), 16.0)

    def test_scales_with_base(self):
        self.assertEqual(backoff_delay(1, base=0.5), 0.5)
        self.assertEqual(backoff_delay(2, base=0.5), 1.0)
        self.assertEqual(backoff_delay(3, base=0.5), 2.0)
        self.assertEqual(backoff_delay(2, base=2.0), 4.0)

    def test_caps_at_max_delay(self):
        self.assertEqual(backoff_delay(10, base=1.0, max_delay=30.0), 30.0)
        self.assertEqual(backoff_delay(1000, base=1.0, max_delay=60.0), 60.0)

    def test_cap_boundary(self):
        self.assertEqual(backoff_delay(1, base=1.0, max_delay=4.0), 1.0)
        self.assertEqual(backoff_delay(2, base=1.0, max_delay=4.0), 2.0)
        self.assertEqual(backoff_delay(3, base=1.0, max_delay=4.0), 4.0)
        self.assertEqual(backoff_delay(4, base=1.0, max_delay=4.0), 4.0)

    def test_each_uncapped_delay_is_twice_the_previous(self):
        previous = backoff_delay(1, base=1.0, max_delay=1000.0)
        for attempt in range(2, 7):
            current = backoff_delay(attempt, base=1.0, max_delay=1000.0)
            self.assertEqual(current, previous * 2)
            previous = current

    def test_rejects_attempts_below_one(self):
        with self.assertRaises(ValueError):
            backoff_delay(0)
        with self.assertRaises(ValueError):
            backoff_delay(-1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
