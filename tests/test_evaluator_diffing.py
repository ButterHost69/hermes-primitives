"""Tests for evaluator.diffing: the candidate diff."""

from __future__ import annotations

import subprocess
from pathlib import Path

from evaluator.diffing import candidate_diff, pruned_copy


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _tree(root: Path, name: str) -> Path:
    tree = root / name
    _write(tree / "pkg" / "mod.py", "VALUE = 2\n")
    _write(tree / "README.md", "# Proj\n")
    _write(tree / "__pycache__" / "mod.cpython-313.pyc", "junk")
    _write(tree / ".sessions" / "s.jsonl", "junk")
    _write(tree / "node_modules" / "left-pad" / "index.js", "junk")
    _write(tree / ".coverage", "junk")
    return tree


def test_pruned_copy_excludes_artifacts(tmp_path):
    tree = _tree(tmp_path, "live")
    copy = pruned_copy(tree, "live", tmp_path / "scratch")

    assert (copy / "pkg" / "mod.py").is_file()
    assert not (copy / "__pycache__").exists()
    assert not (copy / ".sessions").exists()
    assert not (copy / "node_modules").exists()
    assert not (copy / ".coverage").exists()


def test_candidate_diff_captures_changed_files(tmp_path):
    baseline = _tree(tmp_path, "baseline")
    live = _tree(tmp_path, "live")
    _write(live / "pkg" / "mod.py", "VALUE = 2  # the fix\n")

    diff = candidate_diff(baseline, live)

    assert diff is not None
    assert "pkg/mod.py" in diff
    assert "the fix" in diff
    assert "__pycache__" not in diff
    assert "a/baseline/pkg/mod.py" in diff  # plain tree names, not temp paths


def test_candidate_diff_empty_when_identical(tmp_path):
    tree = _tree(tmp_path, "baseline")

    diff = candidate_diff(tree, tree)

    assert diff == ""


def test_candidate_diff_none_without_baseline(tmp_path):
    live = _tree(tmp_path, "live")

    assert candidate_diff(None, live) is None
    assert candidate_diff(tmp_path / "gone", live) is None


def test_candidate_diff_falls_back_to_difflib_without_git(tmp_path, monkeypatch):
    baseline = _tree(tmp_path, "baseline")
    live = _tree(tmp_path, "live")
    _write(live / "pkg" / "mod.py", "VALUE = 3\n")

    def explode(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", explode)

    diff = candidate_diff(baseline, live)

    assert diff is not None
    assert "VALUE = 3" in diff
    assert "pkg/mod.py" in diff


def test_candidate_diff_falls_back_when_git_errors(tmp_path, monkeypatch):
    baseline = _tree(tmp_path, "baseline")
    live = _tree(tmp_path, "live")
    _write(live / "pkg" / "mod.py", "VALUE = 3\n")

    def bad_exit(*args, **kwargs):
        return subprocess.CompletedProcess(args=[], returncode=129, stdout="", stderr="fatal")

    monkeypatch.setattr(subprocess, "run", bad_exit)

    diff = candidate_diff(baseline, live)
    assert "VALUE = 3" in diff


def test_difflib_diff_reports_deleted_files(tmp_path, monkeypatch):
    baseline = _tree(tmp_path, "baseline")
    live = _tree(tmp_path, "live")
    (baseline / "pkg" / "only-here.py").write_text("VALUE = 9\n", encoding="utf-8")

    def explode(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", explode)

    diff = candidate_diff(baseline, live)
    assert "only-here.py" in diff
    assert "-VALUE = 9" in diff
