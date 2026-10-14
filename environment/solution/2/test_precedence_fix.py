"""Oracle tests for environment/codebase/2 (CalcLang).

Bug: ``Parser.parse_term`` consumes ``+`` and ``-`` as well as ``*`` and ``/``,
so every expression is evaluated strictly left-to-right and operator precedence
is lost.

These tests import the real ``calclang`` package from the codebase, so they fail
while precedence is broken and pass once ``parse_term`` only handles ``*``/``/``.
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


_add_codebase_to_path(os.path.join("calclang", "__init__.py"), 2)

from calclang import eval_expression  # noqa: E402


class TestOperatorPrecedence(unittest.TestCase):
    def test_multiplication_binds_tighter_than_addition(self):
        self.assertEqual(eval_expression("2 + 3 * 4"), 14)

    def test_multiplication_binds_tighter_than_subtraction(self):
        self.assertEqual(eval_expression("10 - 3 * 2"), 4)

    def test_division_binds_tighter_than_addition(self):
        self.assertEqual(eval_expression("4 + 8 / 2"), 8)

    def test_division_binds_tighter_than_subtraction(self):
        self.assertEqual(eval_expression("10 - 4 / 2"), 8)

    def test_multiplication_before_addition_on_the_left(self):
        self.assertEqual(eval_expression("3 * 4 + 2"), 14)

    def test_mixed_expression(self):
        self.assertEqual(eval_expression("2 + 3 * 4 - 6 / 3"), 12)

    def test_two_multiplications_around_an_addition(self):
        self.assertEqual(eval_expression("2 * 3 + 4 * 5"), 26)

    def test_two_multiplications_around_a_subtraction(self):
        self.assertEqual(eval_expression("2 * 3 - 4 * 5"), -14)

    def test_division_and_multiplication_are_left_associative(self):
        self.assertEqual(eval_expression("100 / 5 / 2"), 10)
        self.assertEqual(eval_expression("8 / 2 * 3"), 12)

    def test_addition_and_subtraction_are_left_associative(self):
        self.assertEqual(eval_expression("20 - 5 - 3"), 12)
        self.assertEqual(eval_expression("1 - 2 + 3"), 2)

    def test_parentheses_override_precedence(self):
        self.assertEqual(eval_expression("(2 + 3) * 4"), 20)
        self.assertEqual(eval_expression("2 * (3 + 4)"), 14)

    def test_unary_minus_with_precedence(self):
        self.assertEqual(eval_expression("-2 * 3 + 10"), 4)
        self.assertEqual(eval_expression("2 * -3 + 10"), 4)

    def test_floating_point_operands(self):
        self.assertEqual(eval_expression("1 + 2.5 * 2"), 6)

    def test_division_by_zero_still_raises(self):
        with self.assertRaises(ZeroDivisionError):
            eval_expression("5 / (3 - 3)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
