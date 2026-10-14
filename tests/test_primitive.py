"""Tests for the primitive role."""

from __future__ import annotations

import io
import json
import sys
from dataclasses import fields
from pathlib import Path

import pytest

import roles.primitive as primitive_module
from agent import PiError
from agent.tools import BUILTIN_TOOLS
from roles.primitive import Primitive, writes_only_to


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

    # The primitive now opens a gated session; patch the constructor it calls.
    monkeypatch.setattr(primitive_module, "GatedPiSession", make_session)
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


# --------------------------------------------------------------------------- #
# The write gate
# --------------------------------------------------------------------------- #
def _request(tool: str, path=None, cwd: str = "/repo") -> dict:
    """A decision request as the tool-gate extension sends it."""
    arguments = {} if path is None else {"path": path}
    return {"v": 1, "toolName": tool, "toolCallId": "call_1", "cwd": cwd,
            "input": arguments}


def test_primitive_opens_a_gated_session(stubs):
    stubs["outputs"].append("report")
    primitive_module.primitive("fix it", Primitive("src/app.py"), cwd="/repo")

    session = stubs["sessions"][0]
    assert session.kwargs["tools"] == primitive_module.ALL_TOOLS
    assert callable(session.kwargs["policy"])


def test_gate_allows_every_tool_that_is_not_a_write():
    policy = writes_only_to("src/app.py", "/repo")
    for tool in ("read", "grep", "find", "ls", "bash", "codemode"):
        assert policy(_request(tool, "src/other.py")) == {"action": "allow"}


def test_gate_allows_write_to_the_source_file():
    policy = writes_only_to("src/app.py", "/repo")
    assert policy(_request("write", "src/app.py")) == {"action": "allow"}
    assert policy(_request("edit", "src/app.py")) == {"action": "allow"}


def test_gate_allows_write_to_the_source_file_by_absolute_path(tmp_path):
    source = tmp_path / "src" / "app.py"
    policy = writes_only_to(str(source), str(tmp_path))
    assert policy(_request("write", str(source), cwd=str(tmp_path))) == {"action": "allow"}
    # a relative spelling of the same file, and a traversal of it, both match
    assert policy(_request("write", "app.py", cwd=str(source.parent))) == {"action": "allow"}
    assert policy(_request("write", "src/../src/app.py", cwd=str(tmp_path))) == {
        "action": "allow"
    }


def test_gate_blocks_write_to_any_other_file():
    policy = writes_only_to("src/app.py", "/repo")
    verdict = policy(_request("write", "src/db.py"))
    assert verdict["action"] == "block"
    assert "may only write src/app.py" in verdict["reason"]
    assert "src/db.py" in verdict["reason"]
    assert policy(_request("edit", "README.md"))["action"] == "block"


def test_gate_blocks_absolute_paths_outside_the_source_file(tmp_path):
    source = tmp_path / "src" / "app.py"
    policy = writes_only_to(str(source), str(tmp_path))
    other = tmp_path / "src" / "db.py"
    assert policy(_request("edit", str(other), cwd=str(tmp_path)))["action"] == "block"


def test_gate_blocks_a_write_without_a_usable_path():
    policy = writes_only_to("src/app.py", "/repo")
    assert policy(_request("write", None))["action"] == "block"
    assert policy(_request("edit", 42))["action"] == "block"
    assert policy({"toolName": "write", "cwd": "/repo", "input": "not-a-dict"})[
        "action"
    ] == "block"


def test_gate_resolves_against_the_session_cwd_when_the_request_has_none():
    policy = writes_only_to("app.py", "/repo")
    # no cwd in the request: fall back to the session's cwd, same relative spelling
    request = {"toolName": "write", "toolCallId": "c", "input": {"path": "app.py"}}
    assert policy(request) == {"action": "allow"}
    # the request's own cwd names the same base, so the same file still matches
    assert policy(_request("write", "app.py", cwd="/repo")) == {"action": "allow"}
    # another file in that base is still blocked
    assert policy(_request("write", "other.py", cwd="/repo"))["action"] == "block"
    # an absolute path elsewhere is blocked too
    assert policy(_request("write", "/etc/hosts"))["action"] == "block"


def test_gate_follows_symlinks(tmp_path):
    real = tmp_path / "real" / "app.py"
    real.parent.mkdir(parents=True)
    real.write_text("x = 1\n")
    link = tmp_path / "link.py"
    link.symlink_to(real)
    policy = writes_only_to("link.py", str(tmp_path))
    # writing through the symlink still lands on the source file
    assert policy(_request("write", "link.py", cwd=str(tmp_path))) == {"action": "allow"}


def test_primitive_blocks_a_write_to_a_dependency(gate_pi, tmp_path, monkeypatch):
    """End to end: the gate answers a write aimed at a dependency with a block."""
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n")
    dependency = tmp_path / "src" / "db.py"
    dependency.write_text("y = 2\n")

    monkeypatch.setattr(primitive_module, "load_prompt", lambda path: "PROMPT")
    monkeypatch.setenv(
        "HMS_TEST_GATE_REQUEST",
        json.dumps({"v": 1, "toolName": "edit", "toolCallId": "call_dep",
                    "cwd": str(tmp_path), "input": {"path": "src/db.py"}}),
    )

    verdicts: list[dict] = []
    original = primitive_module.writes_only_to

    def spy(source_file, cwd=None):
        policy = original(source_file, cwd)

        def wrapped(request):
            verdict = policy(request)
            verdicts.append(verdict)
            return verdict

        return wrapped

    monkeypatch.setattr(primitive_module, "writes_only_to", spy)

    target = Primitive(
        source_file="src/app.py",
        dep_files={"src/db.py": "reads its settings"},
    )
    # the real gated session, over the fake pi's gate protocol
    report = primitive_module.primitive("fix it", target, cwd=str(tmp_path))

    assert target.session is not None
    assert verdicts, "the policy was never consulted"
    assert verdicts[0]["action"] == "block"
    assert "may only write src/app.py" in verdicts[0]["reason"]
    assert report  # the turn still returns the assistant's text


def test_primitive_from_the_manager_is_gated(stubs):
    """A Primitive obtained through the registry is gated the same way."""
    from primitives import PrimitiveManager

    manager = PrimitiveManager()
    target = manager.create("src/app.py", {"src/db.py": "reads its settings"})
    stubs["outputs"].append("report")

    primitive_module.primitive("fix it", target, cwd="/repo")

    session = stubs["sessions"][0]
    assert session.kwargs["policy"] is not None
    policy = session.kwargs["policy"]
    assert policy({"toolName": "write", "cwd": "/repo",
                   "input": {"path": "src/app.py"}}) == {"action": "allow"}
    assert policy({"toolName": "write", "cwd": "/repo",
                   "input": {"path": "src/db.py"}})["action"] == "block"
