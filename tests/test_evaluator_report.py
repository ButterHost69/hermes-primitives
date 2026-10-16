"""Tests for evaluator.report: rendering, serialization, artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from evaluator.case import Case
from evaluator.grader import Grade
from evaluator.report import CaseResult, Report
from evaluator.runner import PASSED, SuiteResult


def _case(number=1) -> Case:
    return Case(
        number=number,
        codebase=Path("/env/codebase") / str(number),
        oracle_test=Path("/env/solution") / str(number) / "test_oracle.py",
        reference_diffs=(),
        name="Alpha",
        issue="",
    )


def _suite(status=PASSED, *, kind="oracle", passed=5, failed=0, total=5) -> SuiteResult:
    return SuiteResult(
        kind=kind,
        command=("python", "-m", "pytest", "-q", "tests"),
        status=status,
        exit_code=0,
        passed=passed,
        failed=failed,
        total=total,
        duration=0.5,
        stdout="out",
        note=None,
    )


def _result(
    number=1,
    *,
    outcome="pass",
    score=None,
    issues=(),
    error=None,
    oracle=PASSED,
    own=PASSED,
    baseline=PASSED,
    regression=False,
) -> CaseResult:
    return CaseResult(
        case=_case(number),
        oracle=_suite(oracle),
        own_tests=None if own is None else _suite(own, kind="own"),
        baseline_oracle=None if baseline is None else _suite(baseline),
        regression=regression,
        candidate_diff="diff --git a/x b/x\n" if outcome != "fail" or score is not None else None,
        grade=(
            Grade(score=score, verdict=outcome, criteria={}, summary="s", issues=tuple(issues))
            if score is not None
            else None
        ),
        duration=1.25,
        outcome=outcome,
        error=error,
    )


def test_render_shows_every_case_and_notes():
    report = Report(
        results=[
            _result(1, outcome="pass", score=90),
            _result(
                2,
                outcome="fail",
                score=30,
                issues=["scope creep"],
                error="the oracle suite did not pass",
                own="failed",
                baseline="failed",
            ),
        ],
        total_duration=2.5,
    )

    text = report.render()
    assert "project" in text and "verdict" in text
    assert "Alpha" in text
    assert "passed (REGRESSION)" not in text  # no regression here
    assert "! the oracle suite did not pass" in text
    assert "- scope creep" in text
    assert "1 pass, 1 fail" in text
    assert "evaluation failed" in text


def test_render_marks_regressions():
    report = Report(
        results=[_result(1, outcome="partial", score=70, own="failed", regression=True)]
    )
    assert "failed (REGRESSION)" in report.render()


def test_counts_and_exit_code():
    passing = Report(results=[_result(1, outcome="pass", score=90)])
    assert passing.ok
    assert passing.exit_code() == 0
    assert passing.counts == {"pass": 1, "partial": 0, "fail": 0, "error": 0}

    failing = Report(results=[_result(2, outcome="partial", score=70)])
    assert not failing.ok
    assert failing.exit_code() == 1

    assert Report(results=[]).exit_code() == 1


def test_to_dict_shape():
    report = Report(results=[_result(1, outcome="pass", score=90)], total_duration=1.0)
    data = report.to_dict()

    assert data["ok"] is True
    assert data["counts"]["pass"] == 1
    case = data["cases"][0]
    assert case["number"] == 1
    assert case["project"] == "Alpha"
    assert case["outcome"] == "pass"
    assert case["oracle"]["status"] == PASSED
    assert case["grade"]["score"] == 90
    assert case["candidate_diff_lines"] == 1
    # The report is JSON-serializable for --json.
    json.dumps(data)


def test_write_artifacts(tmp_path):
    result = _result(1, outcome="pass", score=90)
    report = Report(results=[result])

    written = report.write_artifacts(tmp_path / "art")

    names = sorted(path.name for path in written)
    assert names == [
        "baseline-oracle.txt",
        "candidate.diff",
        "grade.json",
        "oracle.txt",
        "own-tests.txt",
        "result.json",
    ]
    oracle = (tmp_path / "art" / "1" / "oracle.txt").read_text(encoding="utf-8")
    assert "kind:     oracle" in oracle
    assert "--- stdout ---" in oracle
    grade = json.loads((tmp_path / "art" / "1" / "grade.json").read_text(encoding="utf-8"))
    assert grade["verdict"] == "pass"


def test_suite_fields_none_serializes_as_null():
    result = CaseResult(case=_case(1), oracle=_suite(), own_tests=None, baseline_oracle=None)
    data = result.to_dict()
    assert data["own_tests"] is None
    assert data["baseline_oracle"] is None
    assert data["candidate_diff_lines"] is None


def test_write_artifacts_with_a_bare_result(tmp_path):
    result = CaseResult(
        case=_case(1),
        oracle=_suite(),
        own_tests=None,
        baseline_oracle=None,
        candidate_diff=None,
        grade=None,
        outcome="fail",
        error="the oracle suite did not pass",
    )
    report = Report(results=[result])

    written = report.write_artifacts(tmp_path / "art")

    assert [path.name for path in written] == ["result.json", "oracle.txt"]
