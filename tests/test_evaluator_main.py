"""Tests for evaluator.__main__: environment resolution and the CLI."""

from __future__ import annotations

import json

import pytest

import evaluator.__main__ as main_module
from evaluator.__main__ import build_parser, main, resolve_baseline, resolve_environment, run
from evaluator.errors import EvaluatorError
from evaluator.report import Report
from tests.conftest import make_environment


# --------------------------------------------------------------------------- #
# Environment resolution
# --------------------------------------------------------------------------- #


def test_resolve_environment_explicit(tmp_path):
    root = make_environment(tmp_path, name="env")
    assert resolve_environment(str(root)) == root


def test_resolve_environment_prefers_live(tmp_path, monkeypatch):
    make_environment(tmp_path, name=".environment")
    make_environment(tmp_path, name="environment")
    monkeypatch.chdir(tmp_path)

    assert resolve_environment(None) == (tmp_path / ".environment").resolve()


def test_resolve_environment_falls_back_to_pristine(tmp_path, monkeypatch):
    make_environment(tmp_path, name="environment")
    monkeypatch.chdir(tmp_path)

    assert resolve_environment(None) == (tmp_path / "environment").resolve()


def test_resolve_environment_without_either_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(EvaluatorError, match="no environment found"):
        resolve_environment(None)


def test_resolve_environment_requires_solution_dir(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(EvaluatorError, match="no solution/"):
        resolve_environment(str(bare))


# --------------------------------------------------------------------------- #
# Baseline resolution
# --------------------------------------------------------------------------- #


def test_resolve_baseline_auto_pairs_live_with_pristine(tmp_path):
    live = make_environment(tmp_path, name=".environment")
    pristine = make_environment(tmp_path, name="environment")

    assert resolve_baseline(None, False, live) == pristine


def test_resolve_baseline_none_for_pristine_env(tmp_path):
    pristine = make_environment(tmp_path, name="environment")

    assert resolve_baseline(None, False, pristine) is None


def test_resolve_baseline_no_baseline_opt_out(tmp_path):
    live = make_environment(tmp_path, name=".environment")
    make_environment(tmp_path, name="environment")

    assert resolve_baseline(None, True, live) is None


def test_resolve_baseline_explicit_wins(tmp_path):
    live = make_environment(tmp_path, name=".environment")
    other = make_environment(tmp_path, name="other")

    assert resolve_baseline(str(other), False, live) == other


def test_resolve_baseline_same_as_environment_is_none(tmp_path):
    root = make_environment(tmp_path, name=".environment")

    assert resolve_baseline(str(root), False, root) is None


def test_resolve_baseline_explicit_requires_solution_dir(tmp_path):
    live = make_environment(tmp_path, name=".environment")
    bare = tmp_path / "bare"
    bare.mkdir()

    with pytest.raises(EvaluatorError, match="no solution/"):
        resolve_baseline(str(bare), False, live)


# --------------------------------------------------------------------------- #
# The CLI
# --------------------------------------------------------------------------- #


def _parse(*argv: str):
    return build_parser().parse_args(list(argv))


def test_run_writes_json_and_artifacts(tmp_path, monkeypatch, capsys):
    live = make_environment(tmp_path, name=".environment")
    pristine = make_environment(tmp_path, name="environment")

    def fake_evaluate(cases, **kwargs):
        fake_evaluate.seen = (cases, kwargs)
        return Report(results=[], total_duration=0.0)

    fake_evaluate.seen = None
    monkeypatch.setattr(main_module, "evaluate_cases", fake_evaluate)

    run(
        _parse(
            "--environment", str(live),
            "--baseline", str(pristine),
            "--json", str(tmp_path / "report.json"),
            "--report-dir", str(tmp_path / "art"),
        )
    )

    cases, kwargs = fake_evaluate.seen
    assert [case.number for case in cases] == [1, 2]
    assert [case.number for case in kwargs["baseline_cases"]] == [1, 2]
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["ok"] is False  # an empty report is not a success
    out = capsys.readouterr().out
    assert "artifacts:" in out
    assert "report:" in out


def test_main_reports_setup_errors_as_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main([]) == 2
    assert "no environment found" in capsys.readouterr().err


def test_main_prints_the_report(tmp_path, monkeypatch, capsys):
    live = make_environment(tmp_path, name="env")
    monkeypatch.setattr(main_module, "evaluate_cases", lambda cases, **kw: Report())

    assert main(["--environment", str(live)]) == 1
    out = capsys.readouterr().out
    assert f"environment: {live}" in out
    assert "verdict" in out


def test_main_returns_zero_when_everything_passes(tmp_path, monkeypatch, capsys):
    live = make_environment(tmp_path, name="env")

    class Passing(Report):
        @property
        def ok(self):
            return True

    monkeypatch.setattr(main_module, "evaluate_cases", lambda cases, **kw: Passing())
    assert main(["--environment", str(live)]) == 0


def test_only_filter_with_no_matches_is_a_setup_error(tmp_path, monkeypatch):
    live = make_environment(tmp_path, name="env")
    monkeypatch.setattr(main_module, "evaluate_cases", lambda cases, **kw: Report())

    assert main(["--environment", str(live), "--codebase", "99"]) == 2


def test_main_reports_value_errors_as_exit_2(tmp_path, monkeypatch, capsys):
    live = make_environment(tmp_path, name="env")

    def bad_jobs(cases, **kwargs):
        raise ValueError("jobs must be at least 1: 0")

    monkeypatch.setattr(main_module, "evaluate_cases", bad_jobs)

    assert main(["--environment", str(live), "--jobs", "0"]) == 2
    assert "jobs must be at least 1" in capsys.readouterr().err


def test_run_pairs_a_live_root_with_its_sibling(tmp_path, monkeypatch):
    live = make_environment(tmp_path, name=".environment")
    make_environment(tmp_path, name="environment")
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_evaluate(cases, **kwargs):
        captured["baseline_cases"] = kwargs["baseline_cases"]
        return Report()

    monkeypatch.setattr(main_module, "evaluate_cases", fake_evaluate)

    run(_parse())
    assert [case.number for case in captured["baseline_cases"]] == [1, 2]


def test_resolve_baseline_without_sibling_is_none(tmp_path):
    live = make_environment(tmp_path, name=".environment")

    assert resolve_baseline(None, False, live) is None


def test_main_prints_the_baseline_when_auto_paired(tmp_path, monkeypatch, capsys):
    make_environment(tmp_path, name=".environment")
    make_environment(tmp_path, name="environment")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module, "evaluate_cases", lambda cases, **kw: Report())

    assert main([]) == 1
    out = capsys.readouterr().out
    assert "baseline:" in out
    assert str(tmp_path / "environment") in out
