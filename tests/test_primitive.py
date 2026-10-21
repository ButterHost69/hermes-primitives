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

    def retry(self, *args, **kwargs):
        self.run_calls.append("RETRY")
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

    # The primitive opens its gated session when it is created; patch the
    # constructor it calls.
    monkeypatch.setattr(primitive_module, "GatedPiSession", make_session)
    return {"sessions": sessions, "outputs": outputs}


def test_primitive_fields(stubs):
    assert [f.name for f in fields(Primitive)] == [
        "source_file",
        "dep_files",
        "model",
        "thinking",
        "cwd",
        "observer",
        "session",
    ]
    target = Primitive("a.py")
    assert target.dep_files == {}
    assert target.session is not None


def test_primitive_grants_all_tools(stubs):
    stubs["outputs"].append('{"result":"report"}')
    Primitive("a.py").run("fix it")

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
        model="claude",
        thinking="low",
        cwd="/tmp/x",
    )
    stubs["outputs"].append('{"result":"fixed the loop"}')

    result = target.run("fix the crash")

    assert result == '{"result":"fixed the loop"}'
    # The session created with the primitive stays open: the primitive reuses
    # it for later objectives instead of spawning a Pi subprocess per run.
    session = target.session
    assert session is stubs["sessions"][0]
    assert session.closed is False
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
    stubs["outputs"].append('{"result":"no change needed"}')
    Primitive("solo.py").run("tidy it")

    message = stubs["sessions"][0].run_calls[0]
    assert "Component under change: solo.py" in message
    assert "- no related components are known" in message


def test_primitive_reuses_one_session_across_objectives(stubs):
    target = Primitive("a.py")
    stubs["outputs"].extend(['{"result":"first"}', '{"result":"second"}'])

    first = target.run("objective one")
    second = target.run("objective two")

    assert (first, second) == ('{"result":"first"}', '{"result":"second"}')
    # One session for the whole primitive: the second run reuses the one
    # opened at construction instead of spawning another Pi subprocess.
    assert len(stubs["sessions"]) == 1
    session = target.session
    assert session.closed is False
    assert len(session.run_calls) == 2
    assert "objective one" in session.run_calls[0]
    assert "objective two" in session.run_calls[1]


def test_close_closes_the_session_and_blocks_later_runs(stubs):
    target = Primitive("a.py")
    session = target.session

    target.close()

    assert session.closed is True
    assert target.session is None
    with pytest.raises(PiError):
        target.run("objective")


def test_primitive_forwards_observer(stubs):
    seen: list = []

    def observer(record):
        seen.append(record)

    stubs["outputs"].append('{"result":"report"}')
    Primitive("a.py", observer=observer).run("fix it")
    assert stubs["sessions"][0].kwargs["observer"] is observer


def test_main_error_exits_nonzero(monkeypatch, capsys, stubs):
    def boom(self, issue):
        raise PiError("nope")

    monkeypatch.setattr(primitive_module.Primitive, "run", boom)
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


def test_main_parses_target_and_prints_report(monkeypatch, capsys, stubs):
    seen: dict = {}

    def fake(self, issue):
        seen["issue"] = issue
        seen["target"] = self
        return "the report"

    monkeypatch.setattr(primitive_module.Primitive, "run", fake)
    primitive_module.main(
        ["fix the crash", "--file", "src/app.py", "--dep", "src/db.py=reads it"]
    )

    assert seen["issue"] == "fix the crash"
    assert seen["target"] == Primitive("src/app.py", {"src/db.py": "reads it"})
    assert "the report" in capsys.readouterr().out


def test_main_reads_stdin_when_text_omitted(monkeypatch, capsys, stubs):
    seen: dict = {}

    def fake(self, issue):
        seen["issue"] = issue
        return "done"

    monkeypatch.setattr(primitive_module.Primitive, "run", fake)
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
    Primitive("src/app.py", cwd="/repo")

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
    from utils.json_utils import JsonCheck
    monkeypatch.setattr(primitive_module, "verify_json", lambda text: JsonCheck(ok=True, value={}, error=None))

    target = Primitive(
        source_file="src/app.py",
        dep_files={"src/db.py": "reads its settings"},
    )
    # the real gated session, over the fake pi's gate protocol
    report = target.run("fix it")

    assert target.session is not None
    assert verdicts, "the policy was never consulted"
    assert verdicts[0]["action"] == "block"
    assert "may only write src/app.py" in verdicts[0]["reason"]
    assert report  # the turn still returns the assistant's text


def test_primitive_from_the_manager_is_gated(stubs):
    """A Primitive obtained through the registry is gated the same way."""
    from roles.primitive_manager import PrimitiveManager

    manager = PrimitiveManager()
    target = manager.create_primitive("src/app.py")
    stubs["outputs"].append('{"result":"report"}')

    target.run("fix it")

    session = stubs["sessions"][0]
    assert session.kwargs["policy"] is not None
    policy = session.kwargs["policy"]
    assert policy({"toolName": "write", "cwd": "/repo",
                   "input": {"path": "src/app.py"}}) == {"action": "allow"}
    assert policy({"toolName": "write", "cwd": "/repo",
                   "input": {"path": "src/db.py"}})["action"] == "block"
