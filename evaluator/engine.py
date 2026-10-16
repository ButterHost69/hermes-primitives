"""The evaluation engine: the phases of a run, and their parallelism.

One evaluation of one case runs its phases in order -- the codebase's own
tests (B), the oracle suite (A), the candidate diff (C), then the agent
grader (D) -- while the baseline tree's verification (the oracle on the
buggy code, which must fail, and its own tests, for regression comparison)
runs beside the live branch.  The baseline is a different directory tree, so
the two branches share nothing and run concurrently; within one tree the
suites stay sequential, because two test processes writing ``__pycache__``
and ``.pytest_cache`` in the same tree is a determinism risk for about a
second of savings.

Cases themselves are independent, and grading is a model turn that dominates
the wall clock, so :func:`evaluate_cases` fans the cases out over a thread
pool -- one worker per case by default, capped by ``jobs``.  A case never
raises into the pool: a crashed grader becomes that case's ``error``
outcome while its siblings finish.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from agent import PiError
from evaluator.case import Case
from evaluator.diffing import candidate_diff
from evaluator.grader import grade_case
from evaluator.report import CaseResult, Report
from evaluator.runner import (
    DEFAULT_TIMEOUT,
    ERROR,
    FAILED,
    PASSED,
    SuiteResult,
    run_oracle,
    run_project_tests,
)

# Grading drives one model turn per case; five concurrent sessions is a lot
# of provider traffic already, so the default fan-out is capped even when a
# bigger environment shows up.
MAX_JOBS = 8


def _regression(
    own: SuiteResult | None, baseline_own: SuiteResult | None
) -> bool:
    """Whether the candidate broke a test the baseline passed.

    Only a live failure against a *passing* baseline counts: a baseline that
    could not run says nothing about what the candidate changed.
    """
    return (
        own is not None
        and own.status == FAILED
        and baseline_own is not None
        and baseline_own.status == PASSED
    )


def _baseline_chain(
    case: Case,
    baseline_case: Case | None,
    *,
    timeout: float,
    skip_project_tests: bool,
) -> tuple[SuiteResult | None, SuiteResult | None]:
    """Run the baseline tree's verification: its own tests, then its oracle.

    The baseline oracle must FAIL -- a suite that passes on the buggy code
    detects nothing, and the case is invalid before grading is even
    considered.
    """
    if baseline_case is None:
        return None, None
    own = (
        run_project_tests(baseline_case.codebase, timeout=timeout)
        if not skip_project_tests
        else None
    )
    oracle = run_oracle(baseline_case.oracle_test, cwd=baseline_case.codebase, timeout=timeout)
    return own, oracle


def evaluate_case(
    case: Case,
    *,
    baseline_case: Case | None = None,
    model: str | None = None,
    thinking: str | None = None,
    observer: Callable[[dict[str, Any]], None] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    grade_all: bool = False,
    no_grade: bool = False,
    skip_project_tests: bool = False,
) -> CaseResult:
    """Evaluate one case end to end.  This never raises."""
    started = time.monotonic()
    own = baseline_own = baseline_oracle = None
    candidate = None
    grade = None
    outcome = "fail"
    error: str | None = None

    try:
        with ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"hms-eval-base-{case.number}"
        ) as baseline_pool:
            # The baseline branch runs beside the live branch, on its own
            # tree; the join happens even if the live branch raises.
            baseline_future = baseline_pool.submit(
                _baseline_chain, case, baseline_case, timeout=timeout,
                skip_project_tests=skip_project_tests,
            )
            try:
                own = (
                    None
                    if skip_project_tests
                    else run_project_tests(case.codebase, timeout=timeout)
                )
                oracle = run_oracle(case.oracle_test, cwd=case.codebase, timeout=timeout)
            finally:
                baseline_own, baseline_oracle = baseline_future.result()
            candidate = candidate_diff(
                baseline_case.codebase if baseline_case else None, case.codebase
            )
    except Exception as exc:  # a subprocess-level failure must not kill the run
        oracle = SuiteResult(kind="oracle", command=(), status=ERROR, note=str(exc))
        own = None

    regression = _regression(own, baseline_own)
    oracles_usable = oracle.status != ERROR

    try:
        if not oracles_usable:
            outcome, error = ERROR, oracle.note or "the oracle suite could not run"
        elif baseline_oracle is not None and baseline_oracle.status == PASSED:
            # The oracle cannot tell a fix from a bug, so there is nothing to
            # grade: the case fails on a broken environment, not on the code.
            outcome, error = (
                "fail",
                "oracle invalid: the suite also passes on the buggy baseline",
            )
        elif oracle.status != PASSED:
            # The oracle gates grading unless --grade-all asks for the
            # diagnostic grade anyway.
            if grade_all and not no_grade:
                grade = grade_case(
                    case,
                    oracle=oracle,
                    own_tests=own,
                    regression=regression,
                    candidate_diff=candidate,
                    model=model,
                    thinking=thinking,
                    observer=observer,
                )
                outcome = grade.verdict
            else:
                outcome, error = "fail", "the oracle suite did not pass"
        elif no_grade:
            outcome = "pass"
        else:
            grade = grade_case(
                case,
                oracle=oracle,
                own_tests=own,
                regression=regression,
                candidate_diff=candidate,
                model=model,
                thinking=thinking,
                observer=observer,
            )
            outcome = grade.verdict
    except PiError as exc:
        grade, outcome, error = None, ERROR, f"grader failed: {exc}"

    return CaseResult(
        case=case,
        oracle=oracle,
        own_tests=own,
        baseline_oracle=baseline_oracle,
        regression=regression,
        candidate_diff=candidate,
        grade=grade,
        duration=time.monotonic() - started,
        outcome=outcome,
        error=error,
    )


def evaluate_cases(
    cases: list[Case],
    *,
    baseline_cases: list[Case] | None = None,
    jobs: int | None = None,
    model: str | None = None,
    thinking: str | None = None,
    observer: Callable[[dict[str, Any]], None] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    grade_all: bool = False,
    no_grade: bool = False,
    skip_project_tests: bool = False,
    on_result: Callable[[CaseResult], None] | None = None,
) -> Report:
    """Evaluate every case and return their report, ordered by case number.

    The cases are fanned out over a thread pool -- one worker per case by
    default, ``jobs`` of them when given, so a large environment (or a rate
    limited provider) can be capped; ``1`` runs the cases one at a time.
    ``on_result`` is called from the calling thread as each case finishes,
    in completion order, so a caller can follow the fan-out without giving
    up the deterministic ordering of the report itself.
    """
    if not cases:
        raise ValueError("evaluate_cases needs at least one case")
    workers = len(cases) if jobs is None else min(jobs, len(cases))
    if workers < 1:
        raise ValueError(f"jobs must be at least 1: {jobs}")

    baseline_by_number = {case.number: case for case in (baseline_cases or [])}
    started = time.monotonic()
    reports: dict[int, CaseResult] = {}
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="hms-evaluator"
    ) as pool:
        futures = {
            pool.submit(
                evaluate_case,
                case,
                baseline_case=baseline_by_number.get(case.number),
                model=model,
                thinking=thinking,
                observer=observer,
                timeout=timeout,
                grade_all=grade_all,
                no_grade=no_grade,
                skip_project_tests=skip_project_tests,
            ): case
            for case in cases
        }
        for future in as_completed(futures):
            case = futures[future]
            result = future.result()
            reports[case.number] = result
            if on_result is not None:
                on_result(result)

    return Report(
        results=[reports[case.number] for case in sorted(cases, key=lambda c: c.number)],
        total_duration=time.monotonic() - started,
    )


def evaluate(
    cases: list[Case],
    *,
    baseline_cases: list[Case] | None = None,
    jobs: int | None = None,
    **kwargs: Any,
) -> Report:
    """Evaluate every case with the default fan-out.  See :func:`evaluate_cases`."""
    jobs = len(cases) if jobs is None else jobs
    return evaluate_cases(
        cases, baseline_cases=baseline_cases, jobs=min(jobs, MAX_JOBS), **kwargs
    )


__all__ = ["evaluate", "evaluate_case", "evaluate_cases"]
