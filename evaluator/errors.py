"""Errors raised by the evaluator package."""

from __future__ import annotations


class EvaluatorError(Exception):
    """A setup or discovery problem that stops the evaluator before grading.

    Raised for a missing environment root, a case without its codebase, and
    other conditions the operator has to fix before a run can mean anything.
    The CLI turns it into exit code 2, distinct from a failed evaluation (1).
    """
