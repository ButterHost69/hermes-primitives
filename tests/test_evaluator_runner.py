"""Tests for evaluator.runner: commands, output parsing, and classification."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from evaluator.errors import EvaluatorError
from evaluator.runner import (
    ERROR,
    FAILED,
    NONE,
    PASSED,
    _classify,
    oracle_command,
    parse_node_summary,
    parse_pytest_summary,
    parse_unittest_summary,
    project_test_commands,
    project_test_files,
    run_oracle,
    run_project_tests,
    run_suite,
    SuiteResult,
)
from tests.conftest import make_environment


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #


def test_parse_node_summary():
    text = "ℹ tests 11\nℹ pass 4\nℹ fail 7\n"
    assert parse_node_summary(text) == {"total": 11, "passed": 4, "failed": 7}


def test_parse_node_summary_tap_reporter():
    assert parse_node_summary("# tests 3\n# pass 3\n") == {"total": 3, "passed": 3, "failed": 0}


def test_parse_node_summary_without_counts():
    assert parse_node_summary("some crash output") is None


def test_parse_unittest_summary():
    assert parse_unittest_summary("....\nRan 4 tests in 0.01s\n\nOK\n") == {
        "total": 4,
        "passed": 4,
        "failed": 0,
    }


def test_parse_unittest_summary_failures_and_errors():
    text = "Ran 7 tests in 0.01s\n\nFAILED (failures=4, errors=1)\n"
    assert parse_unittest_summary(text) == {"total": 7, "passed": 2, "failed": 5}


def test_parse_unittest_summary_without_footer():
    assert parse_unittest_summary("Traceback... boom") is None


def test_parse_pytest_summary():
    assert parse_pytest_summary("2 failed, 5 passed in 0.03s") == {
        "total": 7,
        "passed": 5,
        "failed": 2,
    }


def test_parse_pytest_summary_no_tests():
    assert parse_pytest_summary("no tests ran in 0.01s") == {
        "total": 0,
        "passed": 0,
        "failed": 0,
    }


def test_parse_pytest_summary_errors():
    assert parse_pytest_summary("1 error in 0.01s") == {"total": 1, "passed": 0, "failed": 1}


def test_parse_pytest_summary_without_counts():
    assert parse_pytest_summary("INTERNALERROR> boom") is None


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("exit_code", "counts", "parser", "expected"),
    [
        (0, None, "node", PASSED),
        (0, None, "pytest", PASSED),
        (1, {"total": 2, "failed": 1}, "node", FAILED),
        (1, None, "node", ERROR),
        (1, None, "unittest", ERROR),
        (1, None, "pytest", FAILED),
        (5, {"total": 0}, "pytest", NONE),
        (2, None, "pytest", ERROR),
        (4, None, "unittest", ERROR),
    ],
)
def test_classify(exit_code, counts, parser, expected):
    assert _classify(exit_code, counts, parser) == expected


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def test_oracle_command_python():
    command, parser = oracle_command(Path("/env/solution/2/test_oracle.py"))
    assert command[0] != "python3"  # the running interpreter, not a name
    assert command[-1] == "/env/solution/2/test_oracle.py"
    assert parser == "unittest"


def test_oracle_command_node():
    command, parser = oracle_command(Path("/env/solution/1/test_oracle.js"))
    assert command == ["node", "--test", "/env/solution/1/test_oracle.js"]
    assert parser == "node"


def test_oracle_command_rejects_unknown_suffix():
    with pytest.raises(EvaluatorError, match="unsupported oracle test suffix"):
        oracle_command(Path("/env/solution/9/test_oracle.rb"))


def test_project_test_files_skips_artifacts(tmp_path):
    root = make_environment(tmp_path, name="env")
    codebase = root / "codebase" / "1"
    node_modules = codebase / "node_modules"
    node_modules.mkdir(exist_ok=True)
    (node_modules / "thing.test.js").write_text("", encoding="utf-8")
    pycache = codebase / "pkg" / "__pycache__"
    pycache.mkdir(exist_ok=True)
    (pycache / "test_cached.py").write_text("", encoding="utf-8")
    (codebase / "tests" / "mod.test.js").write_text("", encoding="utf-8")

    python_dirs, node_files = project_test_files(codebase)
    assert python_dirs == [codebase / "tests"]
    assert node_files == [codebase / "tests" / "mod.test.js"]


def test_project_test_commands_shape(tmp_path):
    root = make_environment(tmp_path, name="env", js_case=3)
    codebase = root / "codebase" / "1"
    commands = project_test_commands(codebase)
    assert len(commands) == 1
    command, parser = commands[0]
    assert "pytest" in command or "unittest" in command
    assert "addopts=" in command  # any enclosing config is neutralized
    assert parser == "pytest"


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["suite"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_run_suite_passed(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(0, "ℹ tests 3\nℹ pass 3\nℹ fail 0\n"))
    result = run_suite(["node", "--test", "t"], kind="oracle", parser="node")

    assert result.status == PASSED
    assert (result.passed, result.failed, result.total) == (3, 0, 3)
    assert result.exit_code == 0


def test_run_suite_failed(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _completed(1, stderr="Ran 2 tests in 0.01s\n\nFAILED (failures=2)\n"),
    )
    result = run_suite(["python", "t"], kind="own", parser="unittest")

    assert result.status == FAILED
    assert result.failed == 2


def test_run_suite_without_summary_is_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(1, "boom"))
    result = run_suite(["python", "t"], kind="oracle", parser="unittest")

    assert result.status == ERROR
    assert result.note == "no parseable test summary in the output"


def test_run_suite_missing_runtime_is_error(monkeypatch):
    def explode(*args, **kwargs):
        raise FileNotFoundError("node")

    monkeypatch.setattr(subprocess, "run", explode)
    result = run_suite(["node", "--test", "t"], kind="oracle", parser="node")

    assert result.status == ERROR
    assert "runtime not found" in result.note


def test_run_suite_timeout_is_error(monkeypatch):
    def explode(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["python", "t"], timeout=1.0, output="partial")

    monkeypatch.setattr(subprocess, "run", explode)
    result = run_suite(["python", "t"], kind="oracle", parser="unittest", timeout=1.0)

    assert result.status == ERROR
    assert "timed out" in result.note
    assert "partial" in result.stdout


def test_run_oracle_uses_case_suite_and_cwd(monkeypatch):
    seen = {}

    def fake_run(argv, *, cwd=None, **kwargs):
        seen["argv"] = argv
        seen["cwd"] = cwd
        return _completed(0, "Ran 1 test in 0s\n\nOK\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_oracle(Path("/env/solution/2/test_oracle.py"), cwd=Path("/env/codebase/2"))

    assert seen["argv"][-1] == "/env/solution/2/test_oracle.py"
    assert seen["cwd"] == "/env/codebase/2"
    assert result.status == PASSED


def test_run_project_tests_none_when_no_tests(tmp_path):
    root = make_environment(tmp_path, name="env")
    empty = root / "codebase" / "1" / "client"
    empty.mkdir(parents=True)

    result = run_project_tests(empty)
    assert result.status == NONE
    assert result.note == "no tests found"


def test_run_project_tests_aggregates_suites(monkeypatch, tmp_path):
    root = make_environment(tmp_path, name="env")
    codebase = root / "codebase" / "1"
    (codebase / "tests" / "mod.test.js").write_text("", encoding="utf-8")

    # The pytest suite runs first, then the node one.
    results = iter(
        [
            _completed(0, "1 passed in 0.01s"),
            _completed(1, "ℹ tests 1\nℹ pass 0\nℹ fail 1\n"),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: next(results))

    result = run_project_tests(codebase)
    assert result.status == FAILED
    assert result.total == 2
    assert result.failed == 1
    assert result.passed == 1
    assert "pytest" in result.command
    assert "node" in result.command


def test_run_project_tests_error_when_a_suite_errors(monkeypatch, tmp_path):
    root = make_environment(tmp_path, name="env")
    codebase = root / "codebase" / "1"
    (codebase / "tests" / "mod.test.js").write_text("", encoding="utf-8")

    results = iter(
        [
            _completed(0, "1 passed in 0.01s"),
            _completed(1, "cannot find module"),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: next(results))

    result = run_project_tests(codebase)
    assert result.status == ERROR
    assert result.note == "no parseable test summary in the output"


# --------------------------------------------------------------------------- #
# Result rendering and the remaining classification paths
# --------------------------------------------------------------------------- #


def test_suite_result_output_and_artifact_text():
    result = SuiteResult(
        kind="own",
        command=("node", "--test", "t.test.js"),
        status=ERROR,
        stdout="",
        stderr="boom",
        note="timed out after 1s",
    )

    assert result.output == "boom"
    assert result.tail(1) == "boom"
    text = result.artifact_text()
    assert "note:     timed out after 1s" in text
    assert "node --test t.test.js" in text  # shlex-quoted command
    assert "--- stderr ---" in text


def test_parse_pytest_summary_counts_skipped():
    assert parse_pytest_summary("2 passed, 1 skipped in 0.01s") == {
        "total": 3,
        "passed": 2,
        "failed": 0,
    }


def test_project_test_dedupes_python_dirs(tmp_path):
    root = make_environment(tmp_path, name="env")
    codebase = root / "codebase" / "1"
    (codebase / "tests" / "test_second.py").write_text("", encoding="utf-8")

    python_dirs, _ = project_test_files(codebase)
    assert python_dirs == [codebase / "tests"]


def test_project_tests_fall_back_to_unittest_without_pytest(tmp_path, monkeypatch):
    root = make_environment(tmp_path, name="env")
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)

    command, parser = project_test_commands(root / "codebase" / "1")[0]
    assert "unittest" in command
    assert parser == "unittest"


def test_run_suite_oserror_is_error(monkeypatch):
    def explode(*args, **kwargs):
        raise PermissionError("nope")

    monkeypatch.setattr(subprocess, "run", explode)
    result = run_suite(["python", "t"], kind="oracle", parser="unittest")

    assert result.status == ERROR
    assert "could not run python" in result.note


def test_timeout_with_bytes_output_is_error(monkeypatch):
    def explode(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["node", "t"], timeout=1.0, output=b"partial bytes"
        )

    monkeypatch.setattr(subprocess, "run", explode)
    result = run_suite(["node", "t"], kind="oracle", parser="node", timeout=1.0)

    assert result.status == ERROR
    assert result.stdout == "partial bytes"


def test_run_project_tests_empty_output_aggregate(monkeypatch, tmp_path):
    root = make_environment(tmp_path, name="env")
    codebase = root / "codebase" / "1"
    (codebase / "tests" / "mod.test.js").write_text("", encoding="utf-8")

    results = iter([_completed(0), _completed(0)])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: next(results))

    result = run_project_tests(codebase)
    assert result.status == PASSED
    assert result.stdout == ""
