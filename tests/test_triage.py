"""Tests for the triage role."""

from __future__ import annotations

import io
import sys

import pytest

import roles.triage as triage_module
from agent import PiError
from utils.json_utils import JsonCheck


class StubSession:
    """A controllable stand-in for the PiSession triage owns."""

    def __init__(self, outputs, **kwargs):
        self.kwargs = kwargs
        self.outputs = outputs
        self.run_calls: list[str] = []
        self.retry_errors: list = []
        self.closed = False

    def run(self, message):
        self.run_calls.append(message)
        return self.outputs.pop(0)

    def retry(self, *, error=None, timeout=None):
        self.retry_errors.append(error)
        return self.outputs.pop(0)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


@pytest.fixture
def stubs(monkeypatch):
    sessions: list[StubSession] = []
    outputs: list = []

    monkeypatch.setattr(triage_module, "load_prompt", lambda path: "PROMPT")

    def make_session(**kwargs):
        session = StubSession(outputs, **kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(triage_module, "PiSession", make_session)

    def set_verify(results):
        calls: list[str] = []

        def verify(text):
            calls.append(text)
            return results[len(calls) - 1]

        monkeypatch.setattr(triage_module, "verify_json", verify)
        return calls

    return {
        "sessions": sessions,
        "outputs": outputs,
        "set_verify": set_verify,
    }


def test_triage_success_first_try(stubs):
    stubs["outputs"].append('{"category": "bug"}')
    verify_calls = stubs["set_verify"]([JsonCheck(ok=True, value={"category": "bug"})])

    result = triage_module.triage(
        "it crashed", model="claude", thinking="low", cwd="/tmp/x"
    )

    assert result == {"category": "bug"}
    session = stubs["sessions"][0]
    assert session.closed is True
    assert session.kwargs["tools"] == triage_module.READ_ONLY_TOOLS
    assert "write" not in session.kwargs["tools"]
    assert "edit" not in session.kwargs["tools"]
    assert "bash" not in session.kwargs["tools"]
    assert session.kwargs["model"] == "claude"
    assert session.kwargs["thinking"] == "low"
    assert session.kwargs["cwd"] == "/tmp/x"

    assert len(session.run_calls) == 1
    assert "PROMPT" in session.run_calls[0]
    assert "it crashed" in session.run_calls[0]
    assert session.retry_errors == []
    assert len(verify_calls) == 1


def test_triage_disables_write_capable_tools(stubs):
    stubs["outputs"].append("{}")
    stubs["set_verify"]([JsonCheck(ok=True, value={})])
    triage_module.triage("hi")
    tools = stubs["sessions"][0].kwargs["tools"]
    assert "write" not in tools and "edit" not in tools and "bash" not in tools


def test_triage_retries_when_output_is_not_json(stubs):
    stubs["outputs"].extend(["not json", '{"ok": true}'])
    stubs["set_verify"]([
        JsonCheck(ok=False, error="boom"),
        JsonCheck(ok=True, value={"ok": True}),
    ])

    result = triage_module.triage("hi")

    assert result == {"ok": True}
    session = stubs["sessions"][0]
    assert len(session.run_calls) == 1
    assert session.retry_errors == ["boom"]


def test_triage_raises_when_retry_still_not_json(stubs):
    stubs["outputs"].extend(["bad", "still bad"])
    stubs["set_verify"]([
        JsonCheck(ok=False, error="boom"),
        JsonCheck(ok=False, error="boom2"),
    ])

    with pytest.raises(PiError, match="not valid JSON"):
        triage_module.triage("hi")
    assert stubs["sessions"][0].closed is True


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_build_parser_defaults():
    args = triage_module.build_parser().parse_args([])
    assert args.text is None
    assert args.model is None
    assert args.thinking is None
    assert args.cwd is None


def test_build_parser_full():
    args = triage_module.build_parser().parse_args(
        ["-m", "claude", "--thinking", "low", "--cwd", "/x", "some text"]
    )
    assert args.text == "some text"
    assert args.model == "claude"
    assert args.thinking == "low"
    assert args.cwd == "/x"


def test_main_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(
        triage_module,
        "triage",
        lambda text, model=None, thinking=None, cwd=None: {"a": 1},
    )
    triage_module.main(["hello"])
    assert '"a": 1' in capsys.readouterr().out


def test_main_reads_stdin_when_text_omitted(monkeypatch, capsys):
    monkeypatch.setattr(
        triage_module,
        "triage",
        lambda text, model=None, thinking=None, cwd=None: {"got": text},
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin"))
    triage_module.main([])
    assert "from stdin" in capsys.readouterr().out


def test_main_error_exits_nonzero(monkeypatch, capsys):
    def boom(text, model=None, thinking=None, cwd=None):
        raise PiError("nope")

    monkeypatch.setattr(triage_module, "triage", boom)
    with pytest.raises(SystemExit) as exc:
        triage_module.main(["x"])
    assert exc.value.code == 1
    assert "nope" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Integration against the fake `pi` subprocess (real PiClient)
# --------------------------------------------------------------------------- #
def test_triage_against_real_client_retries_then_fails(fake_pi):
    """Fake `pi` always answers "hello", so triage retries once then raises."""
    with pytest.raises(PiError, match="not valid JSON after a retry"):
        triage_module.triage("the login button does nothing")
