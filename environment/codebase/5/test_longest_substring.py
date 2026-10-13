import unittest

from longest_substring import length_of_longest_substring


class TestLongestSubstring(unittest.TestCase):
    def test_no_repeats(self):
        self.assertEqual(length_of_longest_substring("abcdef"), 6)

    def test_repeat_not_adjacent(self):
        self.assertEqual(length_of_longest_substring("dvdf"), 3)

    def test_repeat_with_gap(self):
        self.assertEqual(length_of_longest_substring("abcade"), 5)

    def test_repeats(self):
        self.assertEqual(length_of_longest_substring("abcabcbb"), 3)

    def test_all_same(self):
        self.assertEqual(length_of_longest_substring("bbbbb"), 1)

    def test_empty(self):
        self.assertEqual(length_of_longest_substring(""), 0)


if __name__ == "__main__":
    unittest.main()
