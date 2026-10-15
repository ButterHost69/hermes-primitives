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


# --------------------------------------------------------------------------- #
# Fake `pi` that dies at startup
#
# The shape of the real failure behind the TUI timeout: Pi validates the
# ``--model`` argument, finds it ambiguous across two authenticated providers,
# writes the reason to stderr and exits 1 without ever speaking JSONL.  The
# short delay keeps the subprocess alive across the client's initial
# ``running`` check, so it dies *while* a command is being awaited -- which is
# the case the client has to notice.
# --------------------------------------------------------------------------- #
_CRASH_PI_BODY = r'''
import sys
import time

time.sleep(0.3)
sys.stderr.write(
    'Error: Model "step-5-preview-free" is ambiguous across providers: '
    "opencode-go/step-5-preview-free, opencode/step-5-preview-free. "
    "More than one matching provider is authenticated. "
    "Use --provider or provider/model.\n"
)
sys.stderr.flush()
sys.exit(1)
'''


@pytest.fixture
def crash_pi(tmp_path, monkeypatch):
    """Put a fake `pi` that dies at startup at the front of PATH."""
    script = tmp_path / "pi"
    script.write_text("#!" + sys.executable + "\n" + _CRASH_PI_BODY)
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


# --------------------------------------------------------------------------- #
# Fake `pi` that speaks the tool-gate protocol
#
# `gate_probe` stands in for the tool-gate extension: it raises an `input`
# dialog and waits for the client's answer, exactly as Pi does for
# `ctx.ui.input()` inside a `tool_call` handler.  `gate_probe_pending` records
# whatever arrives instead of answering, for the close() path.  On `prompt` the
# fake raises one gate dialog built from `HMS_TEST_GATE_REQUEST` (a JSON
# request) before streaming the turn, so role-level tests decide real requests.
# --------------------------------------------------------------------------- #
_GATE_FAKE_PI_BODY = r'''
import json
import os
import sys


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def respond(cmd, success=True, data=None):
    record = {"type": "response", "command": cmd.get("type"), "success": success}
    if cmd.get("id") is not None:
        record["id"] = cmd["id"]
    if data is not None:
        record["data"] = data
    out(record)


def dialog(cmd):
    out({"type": "extension_ui_request", "id": cmd["id"], "method": "input",
         "title": cmd.get("title", "hms-tool-gate"),
         "placeholder": cmd.get("placeholder", "{}")})


DEFAULT_GATE_REQUEST = json.dumps(
    {"v": 1, "toolName": "bash", "toolCallId": "t1", "cwd": os.getcwd(),
     "input": {"command": "ls"}}
)

while True:
    line = sys.stdin.readline()
    if not line:
        break
    try:
        cmd = json.loads(line)
    except json.JSONDecodeError:
        continue
    t = cmd.get("type")

    if t == "gate_probe":
        dialog(cmd)
        while True:
            reply = sys.stdin.readline()
            if not reply:
                break
            try:
                msg = json.loads(reply)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "extension_ui_response" and msg.get("id") == cmd["id"]:
                respond(cmd, True, {"verdict": msg.get("value"),
                                    "cancelled": msg.get("cancelled")})
                break
    elif t == "gate_probe_pending":
        dialog(cmd)
        reply = sys.stdin.readline()
        msg = json.loads(reply) if reply.strip() else {"eof": True}
        with open(cmd["record_path"], "w") as handle:
            json.dump(msg, handle)
    elif t == "prompt":
        respond(cmd, True, {"disposition": "started"})
        out({"type": "agent_start"})
        # One gated tool call inside the turn, then the turn's text.
        out({"type": "extension_ui_request", "id": cmd["id"] + "-gate",
             "method": "input", "title": "hms-tool-gate",
             "placeholder": os.environ.get("HMS_TEST_GATE_REQUEST",
                                           DEFAULT_GATE_REQUEST)})
        sys.stdin.readline()  # the verdict; the turn waits for it
        for ch in cmd.get("message", ""):
            out({"type": "message_update", "usage": {},
                 "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0,
                                           "delta": ch}})
        out({"type": "message_end", "message": {"role": "assistant",
              "content": [{"type": "text", "text": cmd.get("message", "")}]}})
        out({"type": "agent_settled", "aborted": False})
    elif t == "get_state":
        respond(cmd, True, {"sessionId": "gate-session"})
    elif t == "die":
        respond(cmd, True, {"bye": True})
        break
    else:
        respond(cmd, True, {})
'''


@pytest.fixture
def gate_pi(tmp_path, monkeypatch):
    """Put a gate-protocol fake `pi` at the front of PATH and return its path."""
    script = tmp_path / "pi"
    script.write_text("#!" + sys.executable + "\n" + _GATE_FAKE_PI_BODY)
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv("HMS_TEST_GATE_REQUEST", raising=False)
    return str(script)
