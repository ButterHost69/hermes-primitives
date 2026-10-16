"""Tests for evaluator.engine: phase order, gating, and parallelism."""

from __future__ import annotations

import time

import pytest

from agent import PiError
from evaluator.case import Case
from evaluator.engine import MAX_JOBS, evaluate, evaluate_case, evaluate_cases
from evaluator.grader import Grade
from evaluator.runner import ERROR, FAILED, PASSED, SuiteResult
import evaluator.engine as engine


def _case(tmp_path, number=1, *, path="") -> Case:
    return Case(
        number=number,
        codebase=tmp_path / f"{path}codebase{number}",
        oracle_test=tmp_path / f"solution{number}" / "test_oracle.py",
        reference_diffs=(),
        name=f"Proj{number}",
        issue="",
    )


def _suite(status=PASSED, kind="oracle") -> SuiteResult:
    return SuiteResult(kind=kind, command=("python", "t"), status=status)


def _grade(verdict="pass", score=90) -> Grade:
    return Grade(score=score, verdict=verdict, criteria={}, summary="", issues=())


class Recorder:
    """Stands in for the engine's phases and records the order they ran in.

    Statuses can differ per tree: the baseline case lives under a ``base``
    path, so the recorder can answer its suites differently from the live
    tree's.
    """

    def __init__(
        self,
        *,
        oracle=PASSED,
        own=PASSED,
        baseline_oracle=FAILED,
        baseline_own=PASSED,
        grade=None,
    ):
        self.calls: list[tuple[str, int | None]] = []
        self.oracle_status = oracle
        self.own_status = own
        self.baseline_oracle_status = baseline_oracle
        self.baseline_own_status = baseline_own
        self.grade = grade
        self.grade_kwargs: list[dict] = []
        self.delay = 0.0

    def _is_baseline(self, path) -> bool:
        return "baseline" in str(path)

    def run_oracle(self, test, *, cwd, timeout=None):
        self.calls.append(("oracle", None))
        if self.delay:
            time.sleep(self.delay)
        status = self.baseline_oracle_status if self._is_baseline(cwd) else self.oracle_status
        return _suite(status)

    def run_project_tests(self, codebase, *, timeout=None):
        self.calls.append(("own", None))
        if self.delay:
            time.sleep(self.delay)
        status = self.baseline_own_status if self._is_baseline(codebase) else self.own_status
        return _suite(status, kind="own")

    def candidate_diff(self, baseline, live):
        self.calls.append(("diff", None))
        return "diff --git a/x b/x"

    def grade_case(self, case, **kwargs):
        self.calls.append(("grade", case.number))
        self.grade_kwargs.append({"case": case, **kwargs})
        if self.delay:
            time.sleep(self.delay)
        if self.grade == "raise":
            raise PiError("grader output was not valid JSON after a retry")
        return self.grade or _grade()


@pytest.fixture
def install(monkeypatch):
    def _install(recorder: Recorder) -> Recorder:
        monkeypatch.setattr(engine, "run_oracle", recorder.run_oracle)
        monkeypatch.setattr(engine, "run_project_tests", recorder.run_project_tests)
        monkeypatch.setattr(engine, "candidate_diff", recorder.candidate_diff)
        monkeypatch.setattr(engine, "grade_case", recorder.grade_case)
        return recorder

    return _install


# --------------------------------------------------------------------------- #
# Phase order and gating
# --------------------------------------------------------------------------- #


def test_phase_order_own_then_oracle_then_diff_then_grade(install, tmp_path):
    rec = install(Recorder())
    result = evaluate_case(_case(tmp_path))

    kinds = [kind for kind, _ in rec.calls]
    assert kinds == ["own", "oracle", "diff", "grade"]
    assert result.outcome == "pass"
    assert result.grade.score == 90


def test_baseline_chain_runs_beside_live_chain(install, tmp_path):
    rec = install(Recorder())
    result = evaluate_case(_case(tmp_path), baseline_case=_case(tmp_path, path="baseline-"))

    oracle_calls = [call for call in rec.calls if call[0] == "oracle"]
    own_calls = [call for call in rec.calls if call[0] == "own"]
    assert len(oracle_calls) == 2
    assert len(own_calls) == 2
    assert result.baseline_oracle.status == FAILED
    assert result.regression is False


def test_baseline_oracle_passing_invalidates_the_case(install, tmp_path):
    rec = install(Recorder(baseline_oracle=PASSED))
    result = evaluate_case(_case(tmp_path), baseline_case=_case(tmp_path, path="baseline-"))

    assert result.outcome == "fail"
    assert "oracle invalid" in result.error
    assert not any(kind == "grade" for kind, _ in rec.calls)


def test_failing_oracle_is_not_graded_by_default(install, tmp_path):
    rec = install(Recorder(oracle=FAILED))
    result = evaluate_case(_case(tmp_path))

    assert result.outcome == "fail"
    assert result.error == "the oracle suite did not pass"
    assert not any(kind == "grade" for kind, _ in rec.calls)


def test_grade_all_grades_a_failing_oracle(install, tmp_path):
    rec = install(Recorder(oracle=FAILED, grade=_grade(verdict="fail", score=10)))
    result = evaluate_case(_case(tmp_path), grade_all=True)

    assert any(kind == "grade" for kind, _ in rec.calls)
    assert result.outcome == "fail"


def test_oracle_error_is_an_error_outcome(install, tmp_path):
    rec = install(Recorder(oracle=ERROR))
    result = evaluate_case(_case(tmp_path))

    assert result.outcome == "error"
    assert result.error == "the oracle suite could not run"
    assert not any(kind == "grade" for kind, _ in rec.calls)


def test_no_grade_skips_the_grader(install, tmp_path):
    rec = install(Recorder())
    result = evaluate_case(_case(tmp_path), no_grade=True)

    assert result.outcome == "pass"
    assert result.grade is None
    assert not any(kind == "grade" for kind, _ in rec.calls)


def test_grader_failure_becomes_an_error_outcome(install, tmp_path):
    rec = install(Recorder(grade="raise"))
    result = evaluate_case(_case(tmp_path))

    assert result.outcome == "error"
    assert "grader failed" in result.error


def test_regression_flag_requires_a_passing_baseline(install, tmp_path):
    rec = install(Recorder(own=FAILED))
    result = evaluate_case(_case(tmp_path), baseline_case=_case(tmp_path, path="baseline-"))

    assert result.regression is True
    assert rec.grade_kwargs[0]["regression"] is True


def test_no_regression_without_baseline_own_tests(install, tmp_path):
    rec = install(Recorder(own=FAILED))
    result = evaluate_case(_case(tmp_path))

    assert result.regression is False
    assert rec.grade_kwargs[0]["regression"] is False


def test_candidate_diff_reaches_the_grader(install, tmp_path):
    rec = install(Recorder())
    evaluate_case(_case(tmp_path))

    assert rec.grade_kwargs[0]["candidate_diff"] == "diff --git a/x b/x"


def test_skip_project_tests_skips_own_suites(install, tmp_path):
    rec = install(Recorder())
    evaluate_case(_case(tmp_path), skip_project_tests=True)

    assert not any(kind == "own" for kind, _ in rec.calls)


def test_subprocess_disaster_becomes_an_error_outcome(install, tmp_path, monkeypatch):
    def boom(test, *, cwd, timeout=None):
        raise RuntimeError("subprocess exploded")

    monkeypatch.setattr(engine, "run_oracle", boom)
    monkeypatch.setattr(engine, "run_project_tests", Recorder().run_project_tests)

    result = evaluate_case(_case(tmp_path))
    assert result.outcome == "error"


# --------------------------------------------------------------------------- #
# Fan-out
# --------------------------------------------------------------------------- #


def test_evaluate_cases_orders_results_by_number(install, tmp_path):
    install(Recorder())
    cases = [_case(tmp_path, 2), _case(tmp_path, 1)]

    report = evaluate_cases(cases, jobs=1)

    assert [result.case.number for result in report.results] == [1, 2]
    assert report.ok


def test_cases_run_concurrently(install, tmp_path):
    rec = install(Recorder())
    rec.delay = 0.15
    cases = [_case(tmp_path, 1), _case(tmp_path, 2)]

    sequential = evaluate_cases(cases, jobs=1).total_duration
    parallel = evaluate_cases(cases).total_duration

    # Each case sleeps twice (own tests and grade); two cases in parallel
    # cost about one case's delay, not two.
    assert parallel < sequential * 0.75


def test_one_case_failing_does_not_take_its_siblings_down(install, tmp_path, monkeypatch):
    install(Recorder())
    live_grade = engine.grade_case

    def grade_case(case, **kwargs):
        if case.number == 2:
            raise PiError("boom")
        return _grade()

    monkeypatch.setattr(engine, "grade_case", grade_case)
    try:
        report = evaluate_cases([_case(tmp_path, 1), _case(tmp_path, 2)])
    finally:
        monkeypatch.setattr(engine, "grade_case", live_grade)

    outcomes = {result.case.number: result.outcome for result in report.results}
    assert outcomes == {1: "pass", 2: "error"}
    assert report.exit_code() == 1


def test_on_result_fires_per_case(install, tmp_path):
    install(Recorder())
    seen: list[CaseResult] = []
    evaluate_cases(
        [_case(tmp_path, 1), _case(tmp_path, 2)], on_result=seen.append
    )

    assert sorted(result.case.number for result in seen) == [1, 2]


def test_evaluate_rejects_bad_jobs(install, tmp_path):
    install(Recorder())
    with pytest.raises(ValueError, match="at least one case"):
        evaluate_cases([])
    with pytest.raises(ValueError, match="jobs must be at least 1"):
        evaluate_cases([_case(tmp_path, 1)], jobs=0)


def test_evaluate_alias_caps_the_default_fan_out(install, tmp_path, monkeypatch):
    install(Recorder())
    calls = {}
    real = engine.evaluate_cases

    def spy(cases, **kwargs):
        calls.update(kwargs)
        return real(cases, **kwargs)

    monkeypatch.setattr(engine, "evaluate_cases", spy)
    cases = [_case(tmp_path, n) for n in range(1, 12)]

    report = evaluate(cases)
    assert calls["jobs"] == MAX_JOBS
    assert len(report.results) == 11
