"""Shared test fixtures: a fake `pi` RPC server and a fake client."""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest

# --------------------------------------------------------------------------- #
# Fake `pi` executable
#
# A self-contained Python program that speaks enough of the JSONL RPC protocol
# for the client tests.  It never touches the network or real credentials.
# --------------------------------------------------------------------------- #
_FAKE_PI_BODY = r'''
import json
import sys


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def respond(cmd, success=True, data=None, error=None):
    record = {"type": "response", "command": cmd.get("type"), "success": success}
    if cmd.get("id") is not None:
        record["id"] = cmd["id"]
    if data is not None:
        record["data"] = data
    if error is not None:
        record["error"] = error
    out(record)


for line in sys.stdin:
    try:
        cmd = json.loads(line)
    except json.JSONDecodeError:
        out({"type": "response", "command": "parse", "success": False, "error": "bad json"})
        continue

    t = cmd.get("type")

    if t == "die":
        respond(cmd, True, {"bye": True})
        break
    elif t == "noop":
        continue  # never respond -> exercises client timeout
    elif t == "fail":
        respond(cmd, False, error="fake failure")
    elif t == "get_state":
        respond(cmd, True, {
            "model": {"id": "fake-model", "provider": "fake", "name": "Fake Model"},
            "thinkingLevel": "high",
            "isStreaming": False,
            "isCompacting": False,
            "steeringMode": "one-at-a-time",
            "followUpMode": "one-at-a-time",
            "sessionId": "fake-session",
            "autoCompactionEnabled": True,
            "messageCount": 0,
            "pendingMessageCount": 0,
        })
    elif t == "get_available_models":
        respond(cmd, True, {"models": [
            {"id": "fake-model", "name": "Fake Model", "provider": "fake"},
            {"id": "other-model", "name": "Other Model", "provider": "fake"},
        ]})
    elif t == "get_available_thinking_levels":
        respond(cmd, True, {"levels": ["off", "low", "medium", "high"]})
    elif t == "set_model":
        respond(cmd, True, {"id": cmd.get("modelId"), "provider": cmd.get("provider")})
    elif t == "set_thinking":
        respond(cmd, True)
    elif t == "new_session":
        respond(cmd, True, {"cancelled": False})
    elif t == "abort":
        respond(cmd, True)
    elif t in ("prompt", "steer", "follow_up"):
        respond(cmd, True, {"disposition": "started"})
        message = cmd.get("message", "")
        out({"type": "agent_start"})
        out({"type": "message_start", "message": {"role": "user", "content": message}})
        out({"type": "message_end", "message": {"role": "user", "content": message}})
        out({"type": "message_start", "message": {"role": "assistant", "content": []}})
        for ch in "hello":
            out({"type": "message_update", "usage": {},
                 "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": ch}})
        out({"type": "message_end", "message": {"role": "assistant",
              "content": [{"type": "text", "text": "hello"}]}})
        out({"type": "turn_end", "message": {"role": "assistant"}, "toolResults": []})
        out({"type": "agent_end", "messages": []})
        out({"type": "agent_settled", "aborted": False})
    elif t == "bash":
        respond(cmd, True, {"output": "out", "exitCode": 0, "cancelled": False, "truncated": False})
    else:
        respond(cmd, True, {})
'''


@pytest.fixture
def fake_pi(tmp_path, monkeypatch):
    """Put a fake `pi` executable at the front of PATH and return its path."""
    script = tmp_path / "pi"
    script.write_text("#!" + sys.executable + "\n" + _FAKE_PI_BODY)
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    return str(script)


@pytest.fixture
def wait_for():
    """Poll a predicate until it is true or the deadline passes."""
    import time

    def _wait(predicate, timeout=5.0, interval=0.02):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return False

    return _wait


# --------------------------------------------------------------------------- #
# Fake client for Interface tests (no subprocess)
# --------------------------------------------------------------------------- #
class FakeClient:
    """Mimics the PiClient surface the Interface depends on."""

    def __init__(
        self,
        tools=("read", "bash", "edit", "write"),
        model="model-a",
        thinking="high",
        cwd="/tmp/fake",
        delta="hello",
    ):
        self.tools = list(tools)
        self.model = model
        self.thinking = thinking
        self.cwd = cwd
        self.running = True
        self.closed = False
        self.restarted = False

        self.delta = delta
        self.disposition = "started"
        self.fail_prompt = False

        self.prompt_calls: list[str] = []
        self.abort_calls = 0
        self.new_session_calls = 0
        self.set_model_calls: list[dict[str, Any]] = []
        self.set_thinking_calls: list[str] = []

        self.state = {
            "model": {"id": "model-a", "name": "Model A", "provider": "fake"},
            "thinkingLevel": "high",
            "isStreaming": False,
            "messageCount": 3,
            "sessionId": "session-1",
            "sessionName": "demo",
        }
        self.models = [
            {"id": "model-a", "name": "Model A", "provider": "fake"},
            {"id": "model-b", "name": "Model B", "provider": "fake"},
        ]
        self.thinking_levels = ["off", "low", "medium", "high"]

        self._handlers: list[Any] = []

    # -- event plumbing ----------------------------------------------------
    def on_event(self, handler):
        self._handlers.append(handler)

    def emit(self, record):
        for handler in list(self._handlers):
            handler(record)

    # -- commands ----------------------------------------------------------
    def prompt(self, message, steer=False, follow_up=False):
        self.prompt_calls.append(message)
        if self.fail_prompt:
            return {"type": "response", "command": "prompt", "success": False,
                    "error": "rejected"}
        if self.disposition == "started":
            self.emit({"type": "agent_start"})
            for ch in self.delta:
                self.emit({"type": "message_update",
                           "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0,
                                                     "delta": ch}})
            self.emit({"type": "message_end", "message": {"role": "assistant"}})
            self.emit({"type": "agent_settled", "aborted": False})
        return {"type": "response", "command": "prompt", "success": True,
                "data": {"disposition": self.disposition}}

    def abort(self):
        self.abort_calls += 1
        return {"type": "response", "command": "abort", "success": True}

    def get_state(self):
        return self.state

    def get_models(self):
        return self.models

    def set_model(self, model):
        self.set_model_calls.append(model)
        return {"id": model["id"]}

    def get_thinking_levels(self):
        return self.thinking_levels

    def set_thinking(self, level):
        self.set_thinking_calls.append(level)
        return {}

    def new_session(self):
        self.new_session_calls += 1
        return {"cancelled": False}

    def restart(self):
        self.restarted = True

    def close(self):
        self.closed = True


class FakeReadline:
    """Records startup-hook and insert_text calls for prefill tests."""

    def __init__(self):
        self.hooks = []
        self.inserted = []

    def set_startup_hook(self, fn):
        self.hooks.append(fn)

    def insert_text(self, text):
        self.inserted.append(text)


@pytest.fixture
def fake_client():
    return FakeClient()
