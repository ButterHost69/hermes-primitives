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

    def reproduce(text, *, model=None, thinking=None, cwd=None, observer=None, context=None):
        reproduce_calls.append(
            {"text": text, "model": model, "thinking": thinking, "cwd": cwd,
             "observer": observer, "context": context}
        )
        return {"summary": "reproduced", "test_fails": True}

    def diagnose(text, *, model=None, thinking=None, cwd=None, observer=None, context=None):
        diagnose_calls.append(
            {"text": text, "model": model, "thinking": thinking, "cwd": cwd,
             "observer": observer, "context": context}
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


def test_role_names(fake_roles, tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")
    assert pipe.role_names == ["reproduce", "diagnose"]


def test_run_step_runs_one_role(fake_roles, tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")
    result = pipe.run_step("reproduce", "prompt")
    assert result == {"summary": "reproduced", "test_fails": True}
    assert pipe.state["reproduce"] == result


def test_run_step_unknown_role_raises(fake_roles, tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")
    with pytest.raises(KeyError):
        pipe.run_step("nope", "x")


def test_run_step_passes_context_and_observer(fake_roles, tmp_path):
    observer = lambda record: None  # noqa: E731
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret", observer=observer)
    pipe.run_step("reproduce", "prompt", context="extra")
    call = fake_roles["reproduce"][0]
    assert call["context"] == "extra"
    assert call["observer"] is observer


def test_run_does_not_pass_observer_when_none(fake_roles, tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")
    pipe.run("x")
    for calls in (fake_roles["reproduce"], fake_roles["diagnose"]):
        for call in calls:
            assert call["observer"] is None
            assert call["context"] is None
