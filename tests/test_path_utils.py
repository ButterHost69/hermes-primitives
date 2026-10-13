"""Tests for the path-resolution helpers."""

from __future__ import annotations

from pathlib import Path

from utils.path_utils import missing_paths, path_resolves


def test_path_resolves_relative_to_cwd(tmp_path):
    (tmp_path / "a.py").write_text("x")
    assert path_resolves("a.py", cwd=tmp_path) is True
    assert path_resolves(Path("a.py"), cwd=tmp_path) is True


def test_path_resolves_missing_path(tmp_path):
    assert path_resolves("nope.py", cwd=tmp_path) is False


def test_path_resolves_directory_is_not_a_file(tmp_path):
    (tmp_path / "sub").mkdir()
    assert path_resolves("sub", cwd=tmp_path) is False


def test_path_resolves_absolute_path(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("x")
    assert path_resolves(str(target), cwd=tmp_path) is True


def test_path_resolves_absolute_path_ignores_cwd(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("x")
    assert path_resolves(str(target), cwd=Path("/elsewhere")) is True


def test_path_resolves_without_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x")
    assert path_resolves("a.py") is True


def test_missing_paths_returns_only_missing(tmp_path):
    (tmp_path / "a.py").write_text("x")
    missing = missing_paths(["a.py", "b.py", "c.py"], cwd=tmp_path)
    assert missing == ["b.py", "c.py"]


def test_missing_paths_all_resolve(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.py").write_text("x")
    assert missing_paths(["a.py", "b.py"], cwd=tmp_path) == []


def test_missing_paths_empty(tmp_path):
    assert missing_paths([], cwd=tmp_path) == []
