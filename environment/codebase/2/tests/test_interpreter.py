import unittest

from calclang import eval_expression


class TestInterpreter(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(eval_expression("2 + 3"), 5)

    def test_precedence_multiplication_over_addition(self):
        self.assertEqual(eval_expression("2 + 3 * 4"), 14)

    def test_precedence_division_over_subtraction(self):
        self.assertEqual(eval_expression("10 - 4 / 2"), 8)

    def test_parentheses_override_precedence(self):
        self.assertEqual(eval_expression("(2 + 3) * 4"), 20)

    def test_unary_minus(self):
        self.assertEqual(eval_expression("-5 + 10"), 5)


if __name__ == "__main__":
    unittest.main()
