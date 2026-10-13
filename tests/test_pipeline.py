"""Tests for the pipeline orchestrator."""

from __future__ import annotations

import json

import pytest

import pipeline


@pytest.fixture
def fake_roles(monkeypatch):
    """Replace reproduce and diagnose with recorders (no `pi` subprocess)."""
    reproduce_calls: list[dict] = []
    diagnose_calls: list[dict] = []

    def reproduce(text, *, model=None, thinking=None, cwd=None):
        reproduce_calls.append(
            {"text": text, "model": model, "thinking": thinking, "cwd": cwd}
        )
        return {"summary": "reproduced", "test_fails": True}

    def diagnose(text, *, model=None, thinking=None, cwd=None):
        diagnose_calls.append(
            {"text": text, "model": model, "thinking": thinking, "cwd": cwd}
        )
        return {"summary": "diagnosed"}

    monkeypatch.setattr(pipeline.reproduce_role, "reproduce", reproduce)
    monkeypatch.setattr(pipeline.diagnose_role, "diagnose", diagnose)
    return {"reproduce": reproduce_calls, "diagnose": diagnose_calls}


def test_pipeline_stores_config(tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")

    assert pipe.codebase_path == tmp_path.resolve()
    assert pipe.model_name == "claude"
    assert pipe.reasoning == "low"
    assert pipe.api_key == "secret"
    assert pipe.state == {}


def test_run_reproduces_then_diagnoses(fake_roles, tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")

    results = pipe.run("the login button does nothing")

    assert list(results.keys()) == ["reproduce", "diagnose"]
    assert results["reproduce"] == {"summary": "reproduced", "test_fails": True}
    assert results["diagnose"] == {"summary": "diagnosed"}
    assert pipe.state["reproduce"] == results["reproduce"]
    assert pipe.state["diagnose"] == results["diagnose"]

    # The prompt is handed to reproduce first.
    call = fake_roles["reproduce"][0]
    assert call["text"] == "the login button does nothing"
    assert call["model"] == "claude"
    assert call["thinking"] == "low"
    assert call["cwd"] == str(tmp_path.resolve())

    # Reproduce's response is then passed to diagnose (serialized as JSON).
    dcall = fake_roles["diagnose"][0]
    assert json.loads(dcall["text"]) == {"summary": "reproduced", "test_fails": True}
    assert dcall["model"] == "claude"
    assert dcall["thinking"] == "low"
    assert dcall["cwd"] == str(tmp_path.resolve())


def test_api_key_not_forwarded(fake_roles, tmp_path):
    """The API key is stored on the pipeline, not passed to the roles."""
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")

    pipe.run("x")

    for calls in (fake_roles["reproduce"], fake_roles["diagnose"]):
        for call in calls:
            assert "api_key" not in call
