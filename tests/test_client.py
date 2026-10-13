"""Tests for the Pi RPC client against the fake `pi` server."""

from __future__ import annotations

import json
import subprocess
import time

import pytest

import agent.client as client_module
from agent.client import PiClient, PiError


def make_client(**kwargs) -> PiClient:
    return PiClient(**kwargs)


# --------------------------------------------------------------------------- #
# Argument building (no subprocess)
# --------------------------------------------------------------------------- #
def test_build_argv_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(client_module, "which", lambda name: "/fake/pi")
    monkeypatch.chdir(tmp_path)
    client = make_client()
    argv = client._build_argv()
    assert argv[0] == "/fake/pi"
    assert argv[1] == "--mode"
    assert argv[2] == "rpc"
    assert "--no-session" not in argv
    assert argv[argv.index("--session-dir") + 1] == str(tmp_path / ".sessions")


def test_build_argv_persistent_session_uses_session_dir(monkeypatch):
    monkeypatch.setattr(client_module, "which", lambda name: "/fake/pi")
    client = make_client(persistent_session=True, session_dir="/tmp/sessions")
    argv = client._build_argv()
    assert "--no-session" not in argv
    assert argv[argv.index("--session-dir") + 1] == "/tmp/sessions"


def test_build_argv_no_session(monkeypatch):
    monkeypatch.setattr(client_module, "which", lambda name: "/fake/pi")
    client = make_client(persistent_session=False)
    argv = client._build_argv()
    assert "--no-session" in argv
    assert "--session-dir" not in argv


def test_session_dir_defaults_to_cwd_sessions():
    assert make_client(cwd="/work/proj")._session_dir() == "/work/proj/.sessions"


def test_session_dir_explicit_overrides_cwd():
    client = make_client(cwd="/work/proj", session_dir="/elsewhere")
    assert client._session_dir() == "/elsewhere"


def test_build_argv_model_thinking_tools(monkeypatch):
    monkeypatch.setattr(client_module, "which", lambda name: "/fake/pi")
    client = make_client(
        model="claude-sonnet",
        thinking="low",
        tools=["read", "bash"],
    )
    argv = client._build_argv()
    assert "--model" in argv and "claude-sonnet" in argv
    assert "--thinking" in argv and "low" in argv
    assert "--tools" in argv and "read,bash" in argv


def test_build_argv_empty_tools_uses_no_tools(monkeypatch):
    monkeypatch.setattr(client_module, "which", lambda name: "/fake/pi")
    client = make_client(tools=[])
    assert "--no-tools" in client._build_argv()
    assert "--tools" not in client._build_argv()


def test_build_argv_missing_pi_raises(monkeypatch):
    monkeypatch.setattr(client_module, "which", lambda name: None)
    with pytest.raises(PiError, match="not found"):
        make_client()._build_argv()


# --------------------------------------------------------------------------- #
# Process integration against the fake `pi`
# --------------------------------------------------------------------------- #
def test_start_and_get_state(fake_pi):
    client = make_client()
    client.start()
    try:
        state = client.get_state()
        assert state["sessionId"] == "fake-session"
        assert state["model"]["id"] == "fake-model"
        assert client.running
    finally:
        client.close()


def test_send_correlates_response(fake_pi):
    client = make_client()
    client.start()
    try:
        response = client.send({"type": "get_state"})
        assert response["success"] is True
        assert response["command"] == "get_state"
        assert response["id"] == "req-1"
    finally:
        client.close()


def test_command_ok_returns_data(fake_pi):
    client = make_client()
    client.start()
    try:
        data = client.command_ok({"type": "get_available_models"})
        assert [m["id"] for m in data["models"]] == ["fake-model", "other-model"]
    finally:
        client.close()


def test_command_ok_raises_on_failure(fake_pi):
    client = make_client()
    client.start()
    try:
        with pytest.raises(PiError, match="fake failure"):
            client.command_ok({"type": "fail"})
    finally:
        client.close()


def test_prompt_streams_events(fake_pi, wait_for):
    client = make_client()
    client.start()
    try:
        events = []
        client.on_event(lambda record: events.append(record))
        response = client.prompt("hello there")
        assert response["success"] is True
        assert response["data"]["disposition"] == "started"

        assert wait_for(
            lambda: any(e.get("type") == "agent_settled" for e in events)
        )
        kinds = {e.get("type") for e in events}
        assert {"agent_start", "agent_settled", "message_update"} <= kinds
        text = "".join(
            e["assistantMessageEvent"]["delta"]
            for e in events
            if e.get("type") == "message_update"
            and e.get("assistantMessageEvent", {}).get("type") == "text_delta"
        )
        assert text == "hello"
    finally:
        client.close()


def test_send_timeout(fake_pi):
    client = make_client()
    client.start()
    try:
        response = client.send({"type": "noop"}, timeout=0.3)
        assert response["success"] is False
        assert response["error"] == "request timed out"
    finally:
        client.close()


def test_send_after_process_death_raises(fake_pi, wait_for):
    client = make_client()
    client.start()
    try:
        assert client.send({"type": "die"})["success"] is True
        assert wait_for(lambda: not client.running)
        with pytest.raises(PiError, match="not running"):
            client.send({"type": "get_state"})
    finally:
        client.close()


def test_restart_reconnects(fake_pi):
    client = make_client()
    client.start()
    try:
        assert client.get_state()["sessionId"] == "fake-session"
        client.restart()
        assert client.running
        assert client.get_state()["sessionId"] == "fake-session"
    finally:
        client.close()


def test_close_stops_process(fake_pi):
    client = make_client()
    client.start()
    client.close()
    assert not client.running


# --------------------------------------------------------------------------- #
# Convenience commands
# --------------------------------------------------------------------------- #
def test_get_models(fake_pi):
    client = make_client()
    client.start()
    try:
        assert [m["id"] for m in client.get_models()] == ["fake-model", "other-model"]
    finally:
        client.close()


def test_set_model(fake_pi):
    client = make_client()
    client.start()
    try:
        data = client.set_model({"id": "other-model", "provider": "fake"})
        assert data["id"] == "other-model"
    finally:
        client.close()


def test_get_thinking_levels(fake_pi):
    client = make_client()
    client.start()
    try:
        assert client.get_thinking_levels() == ["off", "low", "medium", "high"]
    finally:
        client.close()


def test_set_thinking(fake_pi):
    client = make_client()
    client.start()
    try:
        client.set_thinking("low")
    finally:
        client.close()


def test_new_session(fake_pi):
    client = make_client()
    client.start()
    try:
        assert client.new_session()["cancelled"] is False
    finally:
        client.close()


def test_abort(fake_pi):
    client = make_client()
    client.start()
    try:
        assert client.abort()["success"] is True
    finally:
        client.close()


def test_steer_and_follow_up_convenience(fake_pi):
    client = make_client()
    client.start()
    try:
        assert client.prompt("x", steer=True)["command"] == "steer"
        assert client.prompt("x", follow_up=True)["command"] == "follow_up"
    finally:
        client.close()


def test_send_before_start_raises(fake_pi):
    with pytest.raises(PiError, match="not running"):
        make_client().send({"type": "get_state"})


# --------------------------------------------------------------------------- #
# close() edge cases (fake process object, no subprocess)
# --------------------------------------------------------------------------- #
class _FakeStdin:
    def __init__(self, close_error=False, write_error=False):
        self.close_error = close_error
        self.write_error = write_error
        self.closed = False
        self.written: list[bytes] = []

    def close(self):
        if self.close_error:
            raise OSError("close failed")
        self.closed = True

    def write(self, data):
        if self.write_error:
            raise BrokenPipeError("broken")
        self.written.append(data)

    def flush(self):
        pass


class _FakeProc:
    def __init__(self, stdin=None, stdout=None, fail_waits=0):
        self.stdin = stdin
        self.stdout = stdout
        self.fail_waits = fail_waits
        self.waits = 0
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.waits += 1
        if self.waits <= self.fail_waits:
            raise subprocess.TimeoutExpired("pi", timeout)
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_close_before_start_is_noop():
    client = make_client()
    client.close()  # proc is None -> early return, no exception


def test_close_swallows_stdin_close_error():
    client = make_client()
    client._process = _FakeProc(stdin=_FakeStdin(close_error=True))
    client.close()


def test_close_without_stdin():
    client = make_client()
    client._process = _FakeProc(stdin=None)
    client.close()


def test_close_terminates_then_kills():
    client = make_client()
    proc = _FakeProc(stdin=_FakeStdin(), fail_waits=2)
    client._process = proc
    client.close()
    assert proc.terminated is True
    assert proc.killed is True


def test_send_write_broken_pipe_raises():
    client = make_client()
    client._process = _FakeProc(stdin=_FakeStdin(write_error=True))
    with pytest.raises(PiError, match="failed to write"):
        client.send({"type": "get_state"})


# --------------------------------------------------------------------------- #
# Reader internals (direct, no subprocess)
# --------------------------------------------------------------------------- #
def test_read_loop_skips_blank_and_bad_json():
    client = make_client()
    client._process = _FakeProc(stdout=[
        b"\n",                                    # blank -> skipped
        b"not valid json\n",                      # decode error -> skipped
        json.dumps({"type": "agent_start"}).encode() + b"\n",
    ])
    seen: list[str] = []
    client.on_event(lambda record: seen.append(record["type"]))
    client._read_loop()
    assert seen == ["agent_start"]


def test_dispatch_response_without_id():
    client = make_client()
    seen: list[dict] = []
    client.on_event(lambda record: seen.append(record))
    client._dispatch({"type": "response"})  # no id -> not correlated
    assert client._pending == {}
    assert seen == [{"type": "response"}]


def test_dispatch_swallows_handler_exception():
    client = make_client()

    def bad_handler(record):
        raise RuntimeError("boom")

    seen: list[str] = []
    client.on_event(bad_handler)
    client.on_event(lambda record: seen.append(record["type"]))
    client._dispatch({"type": "agent_start"})
    assert seen == ["agent_start"]
