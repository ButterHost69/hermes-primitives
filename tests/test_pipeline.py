"""Tests for the pipeline orchestrator."""

from __future__ import annotations

import json

import pytest

import pipeline
import roles.primitive


class StubSession:
    """Minimal stand-in for the gated Pi session a primitive opens."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.runs: list[str] = []
        self.closed = False

    def run(self, prompt: str) -> str:
        self.runs.append(prompt)
        return "report"

    def close(self):
        self.closed = True


DIAGNOSIS = {
    "summary": "the parser drops the value",
    "findings": [
        {
            "file": "src/app.py",
            "explanation": "the parser returns None",
            "fixes": ["return the parsed value"],
            "confidence": "high",
        },
        {
            "file": "src/db.py",
            "explanation": "the cursor is never closed",
            "fixes": ["close the cursor", "use a context manager"],
        },
    ],
}


@pytest.fixture
def fake_roles(monkeypatch):
    """Replace reproduce and diagnose with recorders (no `pi` subprocess)."""
    reproduce_calls: list[dict] = []
    diagnose_calls: list[dict] = []
    sessions: list[StubSession] = []

    def reproduce(text, *, model=None, thinking=None, cwd=None, observer=None, context=None):
        reproduce_calls.append(
            {"text": text, "model": model, "thinking": thinking, "cwd": cwd,
             "observer": observer, "context": context}
        )
        return {"summary": "reproduced", "test_fails": True}

    diagnose_output = {"summary": "diagnosed"}

    def diagnose(text, *, model=None, thinking=None, cwd=None, observer=None, context=None):
        diagnose_calls.append(
            {"text": text, "model": model, "thinking": thinking, "cwd": cwd,
             "observer": observer, "context": context}
        )
        return json.loads(json.dumps(diagnose_output))

    def make_session(**kwargs):
        session = StubSession(**kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(pipeline.reproduce_role, "reproduce", reproduce)
    monkeypatch.setattr(pipeline.diagnose_role, "diagnose", diagnose)
    # The primitive role drives real primitives; only their session is faked.
    monkeypatch.setattr(roles.primitive, "GatedPiSession", make_session)

    result = {"reproduce": reproduce_calls, "diagnose": diagnose_calls,
              "sessions": sessions}

    def report_findings(findings_output):
        diagnose_output.clear()
        diagnose_output.update(findings_output)

    result["report"] = report_findings
    return result


@pytest.fixture
def diagnosis_roles(fake_roles):
    """The same fakes, with diagnose reporting two findings."""
    fake_roles["report"](DIAGNOSIS)
    return fake_roles


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

    assert list(results.keys()) == ["reproduce", "diagnose", "primitive_manager"]
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
    assert pipe.role_names == ["reproduce", "diagnose", "primitive_manager"]


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


# --------------------------------------------------------------------------- #
# The primitive_manager role
# --------------------------------------------------------------------------- #
def test_run_calls_every_primitive_the_diagnosis_found(diagnosis_roles, tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")

    results = pipe.run("x")

    assert results["primitive_manager"] == {
        "src/app.py": "report",
        "src/db.py": "report",
    }
    assert pipe.state["primitive_manager"] == results["primitive_manager"]
    # one primitive per diagnosed file, and each one was called exactly once
    assert len(diagnosis_roles["sessions"]) == 2
    assert [len(session.runs) for session in diagnosis_roles["sessions"]] == [1, 1]


def test_primitive_objective_carries_the_finding(diagnosis_roles, tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")

    pipe.run("x")

    objectives = [session.runs[0] for session in diagnosis_roles["sessions"]]
    assert "Why this file is implicated:\nthe parser returns None" in objectives[0]
    assert "Suggested fixes:\n- return the parsed value" in objectives[0]
    assert "- close the cursor\n- use a context manager" in objectives[1]


def test_primitives_run_under_the_pipeline_configuration(diagnosis_roles, tmp_path):
    observer = lambda record: None  # noqa: E731
    pipe = pipeline.Pipeline(
        tmp_path, "claude", "low", "secret", observer=observer
    )

    pipe.run("x")

    for session in diagnosis_roles["sessions"]:
        assert session.kwargs["model"] == "claude"
        assert session.kwargs["thinking"] == "low"
        assert session.kwargs["cwd"] == str(tmp_path.resolve())
        assert session.kwargs["observer"] is observer


def test_operator_context_is_appended_to_every_objective(diagnosis_roles, tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")

    pipe.run_step("primitive_manager", DIAGNOSIS, context="keep the public API")

    assert all(
        session.runs[0].endswith("\n\nkeep the public API")
        for session in diagnosis_roles["sessions"]
    )


def test_findings_without_work_are_skipped(fake_roles, tmp_path):
    fake_roles["report"](
        {
            "summary": "diagnosed",
            "findings": [
                {"file": "src/app.py", "explanation": "broken", "fixes": ["fix it"]},
                {"explanation": "no file path", "fixes": ["fix it"]},
                {"file": "src/db.py", "confidence": "high"},
                "not a finding",
            ],
        }
    )
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")

    results = pipe.run("x")

    # only the finding that names a file and asks for a change produced work
    assert list(results["primitive_manager"]) == ["src/app.py"]
    assert len(fake_roles["sessions"]) == 1


def test_diagnosis_without_findings_creates_no_primitive(fake_roles, tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")

    results = pipe.run("x")

    assert results["primitive_manager"] == {}
    assert fake_roles["sessions"] == []


def test_primitive_sessions_are_closed_after_the_role(diagnosis_roles, tmp_path):
    pipe = pipeline.Pipeline(tmp_path, "claude", "low", "secret")

    pipe.run("x")

    assert [session.closed for session in diagnosis_roles["sessions"]] == [True, True]
