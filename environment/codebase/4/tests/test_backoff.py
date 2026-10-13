import unittest

from queuemaster.backoff import backoff_delay


class TestBackoff(unittest.TestCase):
    def test_first_retry_waits_base(self):
        self.assertEqual(backoff_delay(1), 1.0)

    def test_exponential_growth(self):
        self.assertEqual(backoff_delay(2), 2.0)
        self.assertEqual(backoff_delay(3), 4.0)
        self.assertEqual(backoff_delay(4), 8.0)

    def test_caps_at_max(self):
        self.assertEqual(backoff_delay(10, base=1.0, max_delay=30.0), 30.0)

    def test_rejects_invalid_attempt(self):
        with self.assertRaises(ValueError):
            backoff_delay(0)


if __name__ == "__main__":
    unittest.main()
