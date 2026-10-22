"""Tests for the diagnose role."""

from __future__ import annotations

import io
import sys

import pytest

import roles.diagnose as diagnose_module
from agent import PiError
from utils.json_utils import JsonCheck


class StubSession:
    """A controllable stand-in for the PiSession diagnose owns."""

    def __init__(self, outputs, **kwargs):
        self.kwargs = kwargs
        self.outputs = outputs
        self.run_calls: list[str] = []
        self.retry_errors: list = []
        self.retry_messages: list = []
        self.closed = False

    def run(self, message):
        self.run_calls.append(message)
        return self.outputs.pop(0)

    def retry(self, *, error=None, message=None, timeout=None):
        self.retry_errors.append(error)
        self.retry_messages.append(message)
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

    monkeypatch.setattr(diagnose_module, "load_prompt", lambda path: "PROMPT")

    def make_session(**kwargs):
        session = StubSession(outputs, **kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(diagnose_module, "PiSession", make_session)

    def set_verify(results):
        calls: list[str] = []

        def verify(text):
            calls.append(text)
            return results[len(calls) - 1]

        monkeypatch.setattr(diagnose_module, "verify_json", verify)
        return calls

    def set_missing(results):
        calls: list[list] = []

        def missing(paths, *, cwd=None):
            calls.append(list(paths))
            return results[len(calls) - 1]

        monkeypatch.setattr(diagnose_module, "missing_paths", missing)
        return calls

    return {
        "sessions": sessions,
        "outputs": outputs,
        "set_verify": set_verify,
        "set_missing": set_missing,
    }


def test_diagnose_success_first_try(stubs):
    verdict = {
        "summary": "boom",
        "findings": [{"file": "a.py", "explanation": "x", "confidence": "high", "fixes": ["f"]}],
    }
    stubs["outputs"].append('{"summary": "boom"}')
    verify_calls = stubs["set_verify"]([JsonCheck(ok=True, value=verdict)])
    missing_calls = stubs["set_missing"]([[]])

    result = diagnose_module.diagnose(
        "traceback", model="claude", thinking="low", cwd="/tmp/x"
    )

    assert result == verdict
    session = stubs["sessions"][0]
    assert session.closed is True
    assert session.kwargs["tools"] == diagnose_module.READ_ONLY_TOOLS
    assert "write" not in session.kwargs["tools"]
    assert "edit" not in session.kwargs["tools"]
    assert "bash" not in session.kwargs["tools"]
    assert session.kwargs["model"] == "claude"
    assert session.kwargs["thinking"] == "low"
    assert session.kwargs["cwd"] == "/tmp/x"

    assert len(session.run_calls) == 1
    assert "PROMPT" in session.run_calls[0]
    assert "traceback" in session.run_calls[0]
    assert session.retry_errors == []
    assert session.retry_messages == []
    assert len(verify_calls) == 1
    assert missing_calls == [["a.py"]]


def test_diagnose_uses_a_gated_session_when_a_policy_is_given(stubs, monkeypatch):
    policy = lambda request: {"action": "allow"}  # noqa: E731
    gated: list[StubSession] = []

    def make_gated(**kwargs):
        session = StubSession(['{"findings": []}'], **kwargs)
        gated.append(session)
        return session

    monkeypatch.setattr(diagnose_module, "GatedPiSession", make_gated)
    monkeypatch.setattr(
        diagnose_module,
        "PiSession",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("ungated session")),
    )
    stubs["set_verify"]([JsonCheck(ok=True, value={"findings": []})])
    stubs["set_missing"]([[]])

    diagnose_module.diagnose("traceback", policy=policy)

    assert gated[0].kwargs["policy"] is policy
    assert gated[0].closed is True


def test_diagnose_disables_write_capable_tools(stubs):
    stubs["outputs"].append("{}")
    stubs["set_verify"]([JsonCheck(ok=True, value={})])
    diagnose_module.diagnose("hi")
    tools = stubs["sessions"][0].kwargs["tools"]
    assert "write" not in tools and "edit" not in tools and "bash" not in tools


def test_diagnose_retries_when_output_is_not_json(stubs):
    stubs["outputs"].extend(["not json", '{"ok": true}'])
    stubs["set_verify"]([
        JsonCheck(ok=False, error="boom"),
        JsonCheck(ok=True, value={"ok": True}),
    ])

    result = diagnose_module.diagnose("hi")

    assert result == {"ok": True}
    session = stubs["sessions"][0]
    assert len(session.run_calls) == 1
    assert session.retry_errors == ["boom"]


def test_diagnose_raises_when_retry_still_not_json(stubs):
    stubs["outputs"].extend(["bad", "still bad"])
    stubs["set_verify"]([
        JsonCheck(ok=False, error="boom"),
        JsonCheck(ok=False, error="boom2"),
    ])

    with pytest.raises(PiError, match="not valid JSON"):
        diagnose_module.diagnose("hi")
    assert stubs["sessions"][0].closed is True


def test_diagnose_retries_when_paths_do_not_resolve(stubs):
    first = {"findings": [{"file": "a.py"}]}
    second = {"findings": [{"file": "b.py"}]}
    stubs["outputs"].extend(['{"findings": [{"file": "a.py"}]}', '{"findings": [{"file": "b.py"}]}'])
    stubs["set_verify"]([
        JsonCheck(ok=True, value=first),
        JsonCheck(ok=True, value=second),
    ])
    missing_calls = stubs["set_missing"]([["a.py"], []])

    result = diagnose_module.diagnose("hi")

    assert result == second
    session = stubs["sessions"][0]
    assert len(session.run_calls) == 1
    assert session.retry_errors == [None]
    assert len(session.retry_messages) == 1
    assert "a.py" in session.retry_messages[0]
    assert "do not exist" in session.retry_messages[0]
    assert missing_calls == [["a.py"], ["b.py"]]


def test_diagnose_raises_when_paths_still_missing_after_retry(stubs):
    first = {"findings": [{"file": "a.py"}]}
    second = {"findings": [{"file": "a.py"}]}
    stubs["outputs"].extend(['{"findings": [{"file": "a.py"}]}', '{"findings": [{"file": "a.py"}]}'])
    stubs["set_verify"]([
        JsonCheck(ok=True, value=first),
        JsonCheck(ok=True, value=second),
    ])
    stubs["set_missing"]([["a.py"], ["a.py"]])

    with pytest.raises(PiError, match="do not resolve to files"):
        diagnose_module.diagnose("hi")
    assert stubs["sessions"][0].closed is True


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_build_parser_defaults():
    args = diagnose_module.build_parser().parse_args([])
    assert args.text is None
    assert args.model is None
    assert args.thinking is None
    assert args.cwd is None


def test_build_parser_full():
    args = diagnose_module.build_parser().parse_args(
        ["-m", "claude", "--thinking", "low", "--cwd", "/x", "some trace"]
    )
    assert args.text == "some trace"
    assert args.model == "claude"
    assert args.thinking == "low"
    assert args.cwd == "/x"


def test_main_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(
        diagnose_module,
        "diagnose",
        lambda text, model=None, thinking=None, cwd=None: {"a": 1},
    )
    diagnose_module.main(["hello"])
    assert '"a": 1' in capsys.readouterr().out


def test_main_reads_stdin_when_text_omitted(monkeypatch, capsys):
    monkeypatch.setattr(
        diagnose_module,
        "diagnose",
        lambda text, model=None, thinking=None, cwd=None: {"got": text},
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin"))
    diagnose_module.main([])
    assert "from stdin" in capsys.readouterr().out


def test_main_error_exits_nonzero(monkeypatch, capsys):
    def boom(text, model=None, thinking=None, cwd=None):
        raise PiError("nope")

    monkeypatch.setattr(diagnose_module, "diagnose", boom)
    with pytest.raises(SystemExit) as exc:
        diagnose_module.main(["x"])
    assert exc.value.code == 1
    assert "nope" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Integration against the fake `pi` subprocess (real PiClient)
# --------------------------------------------------------------------------- #
def test_diagnose_against_real_client_retries_then_fails(fake_pi):
    """Fake `pi` always answers "hello", so diagnose retries once then raises."""
    with pytest.raises(PiError, match="not valid JSON after a retry"):
        diagnose_module.diagnose("a traceback")
