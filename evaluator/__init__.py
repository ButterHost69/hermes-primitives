"""The final evaluator: run the oracle suites, then grade the fixes.

A standalone package that judges a finished pipeline run.  For every case in
an environment root it runs the codebase's own tests (regression guard), the
oracle suite (the pass/fail gate), diffs the live tree against the pristine
baseline, and -- for every fix that passes -- grades it with a Pi agent
session that reads the reference diff, the candidate diff, and the test
results and returns a structured verdict.

It imports only the public ``agent`` and ``utils`` surfaces; nothing in
``pipeline``, ``roles``, or ``tui`` is involved, so a pipeline run can be
evaluated long after it finished, by a different process, or on a copy.

    from evaluator import discover_cases, evaluate

    cases = discover_cases(Path(".environment"))
    report = evaluate(cases)
    print(report.render())
"""

from __future__ import annotations

from evaluator.case import Case, discover_cases
from evaluator.diffing import candidate_diff
from evaluator.engine import evaluate, evaluate_case, evaluate_cases
from evaluator.errors import EvaluatorError
from evaluator.grader import Grade, GraderFormatError, grade_case, normalize_grade
from evaluator.report import CaseResult, Report
from evaluator.runner import SuiteResult, run_oracle, run_project_tests

__version__ = "0.1.0"

__all__ = [
    "Case",
    "CaseResult",
    "EvaluatorError",
    "Grade",
    "GraderFormatError",
    "Report",
    "SuiteResult",
    "candidate_diff",
    "discover_cases",
    "evaluate",
    "evaluate_case",
    "evaluate_cases",
    "grade_case",
    "normalize_grade",
    "run_oracle",
    "run_project_tests",
]
