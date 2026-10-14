"""Tests for the primitive role."""

from __future__ import annotations

import io
import sys
from dataclasses import fields

import pytest

import roles.primitive as primitive_module
from agent import PiError
from agent.tools import BUILTIN_TOOLS
from roles.primitive import Primitive


class StubSession:
    """A controllable stand-in for the PiSession the primitive owns."""

    def __init__(self, outputs, **kwargs):
        self.kwargs = kwargs
        self.outputs = outputs
        self.run_calls: list[str] = []
        self.closed = False

    def run(self, message):
        self.run_calls.append(message)
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

    monkeypatch.setattr(primitive_module, "load_prompt", lambda path: "PROMPT")

    def make_session(**kwargs):
        session = StubSession(outputs, **kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(primitive_module, "PiSession", make_session)
    return {"sessions": sessions, "outputs": outputs}


def test_primitive_fields():
    assert [f.name for f in fields(Primitive)] == [
        "source_file",
        "dep_files",
        "session",
    ]
    assert Primitive("a.py").dep_files == {}
    assert Primitive("a.py").session is None


def test_primitive_grants_all_tools(stubs):
    stubs["outputs"].append("report")
    primitive_module.primitive("fix it", Primitive("a.py"))

    session = stubs["sessions"][0]
    assert session.kwargs["tools"] == primitive_module.ALL_TOOLS
    # The primitive modifies its own artifact, so write and edit are available
    # alongside the read/search tools and bash for running what it changed.
    for tool in ("read", "bash", "edit", "write", "grep", "find", "ls"):
        assert tool in session.kwargs["tools"]
    assert set(primitive_module.ALL_TOOLS) == {t.name for t in BUILTIN_TOOLS}


def test_primitive_sends_prompt_and_briefing(stubs):
    target = Primitive(
        source_file="src/app.py",
        dep_files={"src/db.py": "reads its settings"},
    )
    stubs["outputs"].append("fixed the loop")

    result = primitive_module.primitive(
        "fix the crash", target, model="claude", thinking="low", cwd="/tmp/x"
    )

    assert result == "fixed the loop"
    session = stubs["sessions"][0]
    # The session stays attached and open: the primitive reuses it for later
    # objectives instead of spawning a Pi subprocess per call.
    assert session.closed is False
    assert target.session is session
    assert session.kwargs["model"] == "claude"
    assert session.kwargs["thinking"] == "low"
    assert session.kwargs["cwd"] == "/tmp/x"
    assert session.kwargs["observer"] is None

    message = session.run_calls[0]
    assert "PROMPT" in message
    assert "fix the crash" in message
    assert "Component under change: src/app.py" in message
    assert "Dependency Context:" in message
    assert "- src/db.py: reads its settings" in message


def test_primitive_briefing_without_dependencies(stubs):
    stubs["outputs"].append("no change needed")
    primitive_module.primitive("tidy it", Primitive("solo.py"))

    message = stubs["sessions"][0].run_calls[0]
    assert "Component under change: solo.py" in message
    assert "- no related components are known" in message


def test_primitive_reuses_attached_session(stubs):
    target = Primitive("a.py")
    stubs["outputs"].extend(["first", "second"])

    first = primitive_module.primitive("objective one", target)
    second = primitive_module.primitive("objective two", target)

    assert (first, second) == ("first", "second")
    # One session for the whole primitive: re-run reuses the attached one
    # instead of spawning a second Pi subprocess.
    assert len(stubs["sessions"]) == 1
    session = stubs["sessions"][0]
    assert target.session is session
    assert session.closed is False
    assert len(session.run_calls) == 2
    assert "objective one" in session.run_calls[0]
    assert "objective two" in session.run_calls[1]


def test_primitive_reopens_session_after_close(stubs):
    target = Primitive("a.py")
    stubs["outputs"].extend(["first", "second"])

    primitive_module.primitive("objective one", target)
    first_session = target.session
    target.close()
    assert first_session.closed is True
    assert target.session is None

    primitive_module.primitive("objective two", target)
    assert len(stubs["sessions"]) == 2
    assert target.session is not first_session


def test_attach_session_replaces_and_closes_previous(stubs):
    target = Primitive("a.py")
    first = StubSession([])
    second = StubSession([])

    target.attach_session(first)
    target.attach_session(second)

    assert first.closed is True
    assert target.session is second
    # Re-attaching the same session is a no-op, not a close.
    target.attach_session(second)
    assert second.closed is False


def test_close_without_session_is_noop():
    Primitive("a.py").close()


def test_primitive_forwards_observer(stubs):
    seen: list = []

    def observer(record):
        seen.append(record)

    stubs["outputs"].append("report")
    primitive_module.primitive("fix it", Primitive("a.py"), observer=observer)
    assert stubs["sessions"][0].kwargs["observer"] is observer


def test_main_error_exits_nonzero(monkeypatch, capsys):
    def boom(issue, target, *, model=None, thinking=None, cwd=None, observer=None):
        raise PiError("nope")

    monkeypatch.setattr(primitive_module, "primitive", boom)
    with pytest.raises(SystemExit) as exc:
        primitive_module.main(["objective", "--file", "a.py"])
    assert exc.value.code == 1
    assert "nope" in capsys.readouterr().err


def test_parse_dep():
    assert primitive_module._parse_dep("src/db.py=reads its settings") == (
        "src/db.py",
        "reads its settings",
    )
    assert primitive_module._parse_dep("src/db.py") == ("src/db.py", "related component")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_build_parser_defaults():
    args = primitive_module.build_parser().parse_args(
        ["objective text", "--file", "a.py"]
    )
    assert args.text == "objective text"
    assert args.file == "a.py"
    assert args.dep == []
    assert args.model is None
    assert args.thinking is None
    assert args.cwd is None


def test_build_parser_full():
    args = primitive_module.build_parser().parse_args(
        [
            "fix the crash",
            "--file",
            "src/app.py",
            "--dep",
            "src/db.py=reads it",
            "-m",
            "claude",
            "--thinking",
            "low",
            "--cwd",
            "/x",
        ]
    )
    assert args.text == "fix the crash"
    assert args.file == "src/app.py"
    assert args.dep == ["src/db.py=reads it"]
    assert args.model == "claude"
    assert args.thinking == "low"
    assert args.cwd == "/x"


def test_build_parser_requires_file():
    with pytest.raises(SystemExit):
        primitive_module.build_parser().parse_args([])


def test_main_parses_target_and_prints_report(monkeypatch, capsys):
    seen: dict = {}

    def fake(issue, target, *, model=None, thinking=None, cwd=None, observer=None):
        seen["issue"] = issue
        seen["target"] = target
        return "the report"

    monkeypatch.setattr(primitive_module, "primitive", fake)
    primitive_module.main(
        ["fix the crash", "--file", "src/app.py", "--dep", "src/db.py=reads it"]
    )

    assert seen["issue"] == "fix the crash"
    assert seen["target"] == Primitive("src/app.py", {"src/db.py": "reads it"})
    assert "the report" in capsys.readouterr().out


def test_main_reads_stdin_when_text_omitted(monkeypatch, capsys):
    seen: dict = {}

    def fake(issue, target, *, model=None, thinking=None, cwd=None, observer=None):
        seen["issue"] = issue
        return "done"

    monkeypatch.setattr(primitive_module, "primitive", fake)
    monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin"))
    primitive_module.main(["--file", "a.py"])
    assert seen["issue"] == "from stdin"
    assert "done" in capsys.readouterr().out
