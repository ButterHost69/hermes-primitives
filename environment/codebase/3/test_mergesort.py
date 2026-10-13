import unittest

from mergesort import merge, merge_sort


class TestMerge(unittest.TestCase):
    def test_merge_interleaved(self):
        self.assertEqual(merge([1, 3, 5], [2, 4, 6]), [1, 2, 3, 4, 5, 6])

    def test_merge_with_empty(self):
        self.assertEqual(merge([], [1, 2, 3]), [1, 2, 3])
        self.assertEqual(merge([1, 2, 3], []), [1, 2, 3])

    def test_merge_sort(self):
        self.assertEqual(merge_sort([5, 2, 9, 1, 5, 6]), [1, 2, 5, 5, 6, 9])

    def test_merge_sort_edge_cases(self):
        self.assertEqual(merge_sort([]), [])
        self.assertEqual(merge_sort([42]), [42])


if __name__ == "__main__":
    unittest.main()
