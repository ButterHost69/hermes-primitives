import unittest

from registry import record_visit


class TestRegistry(unittest.TestCase):
    def test_each_call_returns_fresh_list(self):
        self.assertEqual(record_visit(1), [1])
        self.assertEqual(record_visit(2), [2])
        self.assertEqual(record_visit(3), [3])


if __name__ == "__main__":
    unittest.main()
