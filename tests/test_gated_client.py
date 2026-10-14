"""Tests for the gated Pi client and the tool-gate extension wire protocol."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

import agent.client as client_module
import agent.gated_client as gated_module
from agent.client import PiClient, PiError
from agent.gated_client import (
    DEFAULT_GATE_TIMEOUT_MS,
    GATE_TITLE,
    TOOL_GATE_JS,
    GatedPiClient,
    GatedPiSession,
    allow_all,
    block_all,
)


def gate_request(**overrides) -> dict:
    """A decision request as the tool-gate extension serializes it."""
    request = {"v": 1, "toolName": "bash", "toolCallId": "call_1",
               "cwd": "/repo", "input": {"command": "ls"}}
    request.update(overrides)
    return request


def make_client(policy=None, **kwargs) -> GatedPiClient:
    return GatedPiClient(policy=policy, **kwargs)


# --------------------------------------------------------------------------- #
# Argument building and the untouched original
# --------------------------------------------------------------------------- #
def test_build_argv_loads_the_extension(monkeypatch):
    monkeypatch.setattr(client_module, "which", lambda name: "/fake/pi")
    argv = make_client()._build_argv()
    assert "--extension" in argv
    assert argv[argv.index("--extension") + 1] == str(TOOL_GATE_JS.resolve())


def test_build_argv_honours_extension_override(monkeypatch, tmp_path):
    monkeypatch.setattr(client_module, "which", lambda name: "/fake/pi")
    other = tmp_path / "other.js"
    other.write_text("export default function () {}\n")
    argv = make_client(extension_path=other)._build_argv()
    assert argv[argv.index("--extension") + 1] == str(other)


def test_original_client_is_unchanged(monkeypatch):
    """PiClient stays exactly as it was: no extension, no environment."""
    monkeypatch.setattr(client_module, "which", lambda name: "/fake/pi")
    assert "--extension" not in PiClient()._build_argv()
    assert not hasattr(PiClient(), "respond_ui")


def test_child_env_carries_the_gate_timeout(monkeypatch):
    monkeypatch.delenv("HMS_TOOL_GATE_TIMEOUT_MS", raising=False)
    env = make_client(gate_timeout_ms=1234)._child_env()
    assert env["HMS_TOOL_GATE_TIMEOUT_MS"] == "1234"
    assert "PATH" in env


def test_child_env_default_timeout_is_the_module_default():
    assert make_client().gate_timeout_ms == DEFAULT_GATE_TIMEOUT_MS


# --------------------------------------------------------------------------- #
# start() guards
# --------------------------------------------------------------------------- #
def test_start_without_extension_file_raises(tmp_path):
    missing = tmp_path / "nope.js"
    with pytest.raises(PiError, match="tool gate extension not found"):
        make_client(extension_path=missing).start()


def test_start_popenerror_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(gated_module.subprocess, "Popen", _exploding_popen)
    with pytest.raises(PiError, match="failed to start"):
        make_client().start()


def _exploding_popen(*args, **kwargs):
    raise OSError("no fork for you")


# --------------------------------------------------------------------------- #
# The decision round-trip against the fake pi
# --------------------------------------------------------------------------- #
def test_policy_allow_reaches_pi(gate_pi):
    seen: list[dict] = []

    def policy(request):
        seen.append(request)
        return {"action": "allow"}

    client = make_client(policy=policy)
    client.start()
    try:
        data = client.command_ok({"type": "gate_probe", "placeholder": json.dumps(gate_request())})
        assert json.loads(data["verdict"]) == {"action": "allow"}
        assert data["cancelled"] is None
        assert seen == [gate_request()]
    finally:
        client.close()


def test_policy_block_reaches_pi(gate_pi):
    client = make_client(policy=lambda request: {"action": "block", "reason": "no rm"})
    client.start()
    try:
        data = client.command_ok({"type": "gate_probe", "placeholder": json.dumps(gate_request())})
        assert json.loads(data["verdict"]) == {"action": "block", "reason": "no rm"}
    finally:
        client.close()


def test_policy_patch_reaches_pi(gate_pi):
    def policy(request):
        return {"action": "allow", "patch": {"command": "ls /repo"}}

    client = make_client(policy=policy)
    client.start()
    try:
        data = client.command_ok({"type": "gate_probe", "placeholder": json.dumps(gate_request())})
        assert json.loads(data["verdict"])["patch"] == {"command": "ls /repo"}
    finally:
        client.close()


def test_default_policy_allows(gate_pi):
    client = make_client()
    client.start()
    try:
        data = client.command_ok({"type": "gate_probe", "placeholder": "{}"})
        assert json.loads(data["verdict"]) == allow_all({})
    finally:
        client.close()


def test_policy_exception_fails_closed(gate_pi):
    def policy(request):
        raise RuntimeError("boom")

    client = make_client(policy=policy)
    client.start()
    try:
        data = client.command_ok({"type": "gate_probe", "placeholder": json.dumps(gate_request())})
        verdict = json.loads(data["verdict"])
        assert verdict["action"] == "block"
        assert "exception" in verdict["reason"]
    finally:
        client.close()


def test_block_all_policy():
    assert block_all({}) == {"action": "block", "reason": "tool gate: nothing is allowed here"}


# --------------------------------------------------------------------------- #
# Verdict construction (no subprocess)
# --------------------------------------------------------------------------- #
def test_undecodable_request_blocks():
    client = make_client(policy=lambda request: {"action": "allow"})
    verdict = client._verdict_for({"placeholder": "not json"})
    assert verdict["action"] == "block"
    assert "undecodable" in verdict["reason"]


def test_missing_placeholder_blocks():
    client = make_client(policy=lambda request: {"action": "allow"})
    assert client._verdict_for({})["action"] == "block"


def test_non_string_placeholder_blocks():
    client = make_client(policy=lambda request: {"action": "allow"})
    assert client._verdict_for({"placeholder": {"nested": True}})["action"] == "block"


def test_non_object_request_blocks():
    client = make_client(policy=lambda request: {"action": "allow"})
    assert client._verdict_for({"placeholder": "[1, 2]"})["action"] == "block"


def test_policy_returning_non_object_blocks():
    client = make_client(policy=lambda request: "allow")
    verdict = client._verdict_for({"placeholder": json.dumps(gate_request())})
    assert verdict["action"] == "block"
    assert "no verdict" in verdict["reason"]


def test_policy_returning_unknown_action_blocks():
    client = make_client(policy=lambda request: {"action": "escalate"})
    verdict = client._verdict_for({"placeholder": json.dumps(gate_request())})
    assert verdict["action"] == "block"
    assert "no verdict" in verdict["reason"]


def test_valid_verdict_passes_through():
    client = make_client(policy=lambda request: {"action": "block", "reason": "x"})
    assert client._verdict_for({"placeholder": json.dumps(gate_request())}) == {
        "action": "block", "reason": "x",
    }


# --------------------------------------------------------------------------- #
# Routing: only our dialogs reach the policy
# --------------------------------------------------------------------------- #
def test_dispatch_routes_gate_dialogs(wait_for):
    seen: list[dict] = []
    client = make_client(policy=lambda request: seen.append(request) or {"action": "allow"})
    client._dispatch({
        "type": "extension_ui_request", "id": "gate-1", "method": "input",
        "title": GATE_TITLE, "placeholder": json.dumps(gate_request()),
    })
    assert wait_for(lambda: bool(seen) and client._gate_pending == set())
    assert seen == [gate_request()]


def test_dispatch_ignores_other_dialogs():
    observed: list[dict] = []
    asked: list[dict] = []
    client = make_client(policy=lambda request: asked.append(request) or {"action": "allow"})
    client.on_event(lambda record: observed.append(record))

    client._dispatch({
        "type": "extension_ui_request", "id": "gate-2", "method": "select",
        "title": GATE_TITLE, "options": ["Allow", "Block"],
    })
    client._dispatch({
        "type": "extension_ui_request", "id": "gate-3", "method": "input",
        "title": "some-other-extension", "placeholder": "{}",
    })
    client._dispatch({"type": "extension_ui_request", "method": "input",
                      "title": GATE_TITLE, "placeholder": "{}"})

    assert asked == []
    assert len(observed) == 3


def test_dispatch_passes_normal_records_through():
    observed: list[dict] = []
    client = make_client()
    client.on_event(lambda record: observed.append(record))
    client._dispatch({"type": "agent_start"})
    assert observed == [{"type": "agent_start"}]


# --------------------------------------------------------------------------- #
# Writing answers
# --------------------------------------------------------------------------- #
class _FakeStdin:
    def __init__(self, write_error=False, none=False):
        self.write_error = write_error
        self.none = none
        self.closed = False
        self.written: list[bytes] = []

    def write(self, data):
        if self.write_error:
            raise BrokenPipeError("broken")
        self.written.append(data)

    def flush(self):
        pass


class _FakeProc:
    def __init__(self, stdin):
        self.stdin = stdin


def test_respond_ui_writes_the_verdict():
    client = make_client()
    client._process = _FakeProc(_FakeStdin())
    client.respond_ui("gate-9", {"action": "block", "reason": "nope"})
    record = json.loads(client._process.stdin.written[0].decode())
    assert record == {
        "type": "extension_ui_response", "id": "gate-9",
        "value": json.dumps({"action": "block", "reason": "nope"}),
    }


def test_cancel_ui_writes_the_cancellation():
    client = make_client()
    client._process = _FakeProc(_FakeStdin())
    client.cancel_ui("gate-9")
    assert json.loads(client._process.stdin.written[0].decode()) == {
        "type": "extension_ui_response", "id": "gate-9", "cancelled": True,
    }


def test_write_record_is_silent_without_a_subprocess():
    client = make_client()
    client.respond_ui("gate-9", {"action": "allow"})  # never started
    client._process = _FakeProc(None)
    client.respond_ui("gate-9", {"action": "allow"})


def test_write_record_swallows_write_errors():
    client = make_client()
    client._process = _FakeProc(_FakeStdin(write_error=True))
    client.cancel_ui("gate-9")  # the extension's timeout blocks the call anyway


def test_close_cancels_outstanding_decisions(gate_pi, tmp_path):
    """A decision that never arrives is dismissed instead of hanging the run."""
    entered = threading.Event()
    release = threading.Event()

    def slow_policy(request):
        entered.set()
        release.wait(timeout=5)
        return {"action": "allow"}

    recorded = tmp_path / "recorded.json"
    client = make_client(policy=slow_policy)
    client.start()
    threading.Thread(
        target=lambda: client.send(
            {"type": "gate_probe_pending", "id": "probe-pending",
             "record_path": str(recorded)}
        ),
        daemon=True,
    ).start()
    assert entered.wait(timeout=5)

    client.close()
    assert json.loads(recorded.read_text()) == {
        "type": "extension_ui_response", "id": "probe-pending",
        "cancelled": True,
    }
    release.set()


def test_close_before_start_is_noop():
    make_client().close()


def test_restart_reconnects(gate_pi):
    client = make_client()
    client.start()
    try:
        assert client.get_state()["sessionId"] == "gate-session"
        client.restart()
        assert client.running
        assert client.get_state()["sessionId"] == "gate-session"
        # the fresh pool must still accept decisions
        data = client.command_ok({"type": "gate_probe", "placeholder": "{}"})
        assert json.loads(data["verdict"]) == {"action": "allow"}
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# GatedPiSession
# --------------------------------------------------------------------------- #
def test_gated_session_runs_and_gates_the_call(gate_pi, tmp_path):
    seen: list[dict] = []

    def policy(request):
        seen.append(request)
        return {"action": "allow"}

    observed: list[str] = []
    session = GatedPiSession(
        policy=policy, cwd=str(tmp_path),
        observer=lambda record: observed.append(record.get("type", "?")),
    )
    try:
        assert session.run("hi") == "hi"
        assert seen[0]["toolName"] == "bash"
        assert "agent_settled" in observed
    finally:
        session.close()


def test_gated_session_survives_a_blocked_call(gate_pi):
    session = GatedPiSession(policy=lambda request: {"action": "block", "reason": "nope"})
    try:
        assert session.run("hi") == "hi"  # the turn still finishes
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# The extension itself
# --------------------------------------------------------------------------- #
HARNESS = Path(__file__).with_name("tool_gate_harness.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_tool_gate_extension_behaviour():
    """The loaded extension must gate, patch, and fail closed."""
    result = subprocess.run(
        ["node", str(HARNESS)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
