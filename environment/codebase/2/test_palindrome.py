import unittest

from palindrome import is_palindrome


class TestPalindrome(unittest.TestCase):
    def test_odd_palindrome(self):
        self.assertTrue(is_palindrome("racecar"))

    def test_even_palindrome(self):
        self.assertTrue(is_palindrome("abba"))

    def test_short_non_palindrome(self):
        self.assertFalse(is_palindrome("ab"))

    def test_longer_non_palindrome(self):
        self.assertFalse(is_palindrome("abca"))

    def test_ignores_case_and_punctuation(self):
        self.assertTrue(is_palindrome("A man, a plan, a canal: Panama"))


if __name__ == "__main__":
    unittest.main()
