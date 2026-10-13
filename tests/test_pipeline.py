"""Tests for the pipeline orchestrator."""

from __future__ import annotations

import pytest

import pipeline


@pytest.fixture
def fake_triage(monkeypatch):
    """Replace triage with a recorder so no `pi` subprocess is needed."""
    calls: list[dict] = []

    def triage(text, *, model=None, thinking=None, cwd=None):
        calls.append({"text": text, "model": model, "thinking": thinking, "cwd": cwd})
        return {"category": "other"}

    monkeypatch.setattr(pipeline.triage_role, "triage", triage)
    return calls


def test_pipeline_stores_config(tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")

    assert pipe.codebase_path == tmp_path.resolve()
    assert pipe.model_name == "claude"
    assert pipe.reasoning == "low"
    assert pipe.api_key == "secret"
    assert pipe.state == {}


def test_run_runs_triage_first(fake_triage, tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")

    results = pipe.run()

    assert list(results.keys()) == ["triage"]
    assert results["triage"] == {"category": "other"}
    assert pipe.state["triage"] == {"category": "other"}

    call = fake_triage[0]
    assert call["model"] == "claude"
    assert call["thinking"] == "low"
    assert call["cwd"] == str(tmp_path.resolve())
    assert str(tmp_path.resolve()) in call["text"]


def test_api_key_not_forwarded_to_triage(fake_triage, tmp_path):
    """The API key is stored on the pipeline, not passed to triage."""
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")
    pipe.run()

    assert "api_key" not in fake_triage[0]
    assert pipe.api_key == "secret"
