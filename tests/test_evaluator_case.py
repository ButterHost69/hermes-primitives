"""Tests for evaluator.case: discovering cases in an environment root."""

from __future__ import annotations

from pathlib import Path

import pytest

from evaluator.case import discover_cases
from evaluator.errors import EvaluatorError
from tests.conftest import make_codebase, make_environment, make_solution


def test_discovers_cases_sorted_with_metadata(tmp_path):
    root = make_environment(tmp_path, name="env")
    cases = discover_cases(root)

    assert [case.number for case in cases] == [1, 2]
    first = cases[0]
    assert first.name == "Alpha"
    assert first.issue == "Alpha: VALUE should be 2."
    assert first.codebase == root / "codebase" / "1"
    assert first.oracle_test == root / "solution" / "1" / "test_oracle.py"
    assert first.solution_dir == root / "solution" / "1"
    assert first.reference_diffs == (root / "solution" / "1" / "pkg" / "mod.py.diff",)


def test_discovers_js_case(tmp_path):
    root = make_environment(tmp_path, name="env", js_case=3)
    cases = discover_cases(root)

    assert [case.number for case in cases] == [1, 2, 3]
    assert cases[2].oracle_test.suffix == ".js"


def test_non_numeric_solution_dirs_are_ignored(tmp_path):
    root = make_environment(tmp_path, name="env")
    (root / "solution" / "not-a-number").mkdir()
    (root / "solution" / "notes.txt").write_text("hi", encoding="utf-8")

    assert [case.number for case in discover_cases(root)] == [1, 2]


def test_missing_codebase_raises(tmp_path):
    root = make_environment(tmp_path, name="env")
    (root / "solution" / "3").mkdir()
    (root / "solution" / "3" / "test_oracle.py").write_text("", encoding="utf-8")

    with pytest.raises(EvaluatorError, match="no codebase directory"):
        discover_cases(root)


def test_no_oracle_test_raises(tmp_path):
    root = make_environment(tmp_path, name="env")
    (root / "solution" / "3").mkdir()
    (root / "codebase" / "3").mkdir()

    with pytest.raises(EvaluatorError, match="no oracle test"):
        discover_cases(root)


def test_two_oracle_tests_raises(tmp_path):
    root = make_environment(tmp_path, name="env")
    solution = make_solution(root, 3)
    (root / "codebase" / "3").mkdir()
    (solution / "test_other.py").write_text("", encoding="utf-8")

    with pytest.raises(EvaluatorError, match="more than one oracle test"):
        discover_cases(root)


def test_only_filter_keeps_listed_numbers(tmp_path):
    root = make_environment(tmp_path, name="env")

    assert [case.number for case in discover_cases(root, only=[2])] == [2]


def test_missing_solution_dir_raises(tmp_path):
    with pytest.raises(EvaluatorError, match="no solution/"):
        discover_cases(tmp_path / "nowhere")


def test_name_falls_back_when_readme_missing(tmp_path):
    root = make_environment(tmp_path, name="env")
    (root / "codebase" / "2" / "README.md").unlink()

    cases = discover_cases(root)
    assert cases[1].name == "codebase/2"


def test_issue_is_empty_when_missing(tmp_path):
    root = make_environment(tmp_path, name="env")
    (root / "codebase" / "2" / "Issues.md").unlink()

    cases = discover_cases(root)
    assert cases[1].issue == ""


def test_no_cases_at_all_raises(tmp_path):
    root = tmp_path / "env"
    (root / "solution").mkdir(parents=True)

    with pytest.raises(EvaluatorError, match="no cases found"):
        discover_cases(root)


def test_make_codebase_helper_writes_own_tests(tmp_path):
    codebase = make_codebase(tmp_path, 7, "Seven")
    assert (codebase / "pkg" / "mod.py").exists()
    assert (codebase / "tests" / "test_project.py").exists()
    assert isinstance(codebase, Path)
