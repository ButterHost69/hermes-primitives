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


# --------------------------------------------------------------------------- #
# Fake `pi` that dies mid-turn
#
# The prompt is accepted -- ``send`` gets its response -- and the process then
# exits without ever emitting ``agent_settled``.  A reader that only waits for
# the settled event can never be woken, which is the shape of a Pi crash the
# session has to turn into an immediate failure.
# --------------------------------------------------------------------------- #
_CRASH_MID_TURN_PI_BODY = r'''
import json
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


for line in sys.stdin:
    try:
        cmd = json.loads(line)
    except json.JSONDecodeError:
        continue
    if cmd.get("type") in ("prompt", "steer", "follow_up"):
        respond(cmd, data={"disposition": "started"})
        sys.exit(3)  # the agent died: no agent_settled will ever arrive
    respond(cmd, data={})
'''


@pytest.fixture
def crash_mid_turn_pi(tmp_path, monkeypatch):
    """Put a fake `pi` that accepts a prompt and dies before it settles."""
    script = tmp_path / "pi"
    script.write_text("#!" + sys.executable + "\n" + _CRASH_MID_TURN_PI_BODY)
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


# --------------------------------------------------------------------------- #
# Evaluator fixtures
#
# A fake environment root shaped like the real one: ``codebase/<n>/`` holds a
# project whose one-line bug the oracle suite detects, and ``solution/<n>/``
# holds that oracle suite plus a reference diff.  The oracle suites locate the
# codebase by walking up from their own location, the way the real ones do.
# --------------------------------------------------------------------------- #

BUGGY_MODULE = "VALUE = 1  # the bug: the oracle expects 2\n"
FIXED_MODULE = "VALUE = 2\n"

ORACLE_TEMPLATE = '''\
"""Oracle for codebase/{number}: VALUE must be 2."""

import os
import sys
import unittest


def _find_codebase(number):
    directory = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        candidate = os.path.join(directory, "codebase", str(number))
        if os.path.isfile(os.path.join(candidate, "pkg", "__init__.py")):
            sys.path.insert(0, candidate)
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    raise RuntimeError("codebase not found")


_find_codebase(__NUMBER__)
from pkg.mod import VALUE  # noqa: E402


class Oracle(unittest.TestCase):
    def test_value(self):
        self.assertEqual(VALUE, 2)


if __name__ == "__main__":
    unittest.main()
'''

PROJECT_TEMPLATE = '''\
"""The project's own tests: VALUE exists and is an int."""

import unittest

from pkg.mod import VALUE


class Project(unittest.TestCase):
    def test_value_is_int(self):
        self.assertIsInstance(VALUE, int)


if __name__ == "__main__":
    unittest.main()
'''

ORACLE_JS_TEMPLATE = """\
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

function findCodebase(number) {
  let dir = __dirname;
  for (let i = 0; i < 6; i += 1) {
    const candidate = path.join(dir, 'codebase', String(number));
    if (fs.existsSync(path.join(candidate, 'src', 'mod.js'))) return candidate;
    dir = path.dirname(dir);
  }
  throw new Error('codebase not found');
}

const { VALUE } = require(path.join(findCodebase(__NUMBER__), 'src', 'mod.js'));

test('oracle: VALUE is 2', () => {
  assert.strictEqual(VALUE, 2);
});
"""

PROJECT_JS_TEMPLATE = """\
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { VALUE } = require('../src/mod.js');

test('project: VALUE is an int', () => {
  assert.strictEqual(typeof VALUE, 'number');
});
"""

REFERENCE_DIFF = """\
diff --git a/pkg/mod.py b/pkg/mod.py
index 1111111..2222222 100644
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -1 +1 @@
-VALUE = 1  # the bug: the oracle expects 2
+VALUE = 2
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_codebase(root: Path, number: int, name: str, *, fixed: bool = False) -> Path:
    """Write a python project (``pkg/mod.py``, own tests, README, Issues.md)."""
    codebase = root / "codebase" / str(number)
    _write(codebase / "pkg" / "__init__.py", "")
    _write(codebase / "pkg" / "mod.py", FIXED_MODULE if fixed else BUGGY_MODULE)
    _write(codebase / "tests" / "__init__.py", "")
    _write(codebase / "tests" / "test_project.py", PROJECT_TEMPLATE)
    _write(codebase / "README.md", f"# {name}\n\nA fake project.\n")
    _write(codebase / "Issues.md", f"{name}: VALUE should be 2.\n")
    return codebase


def make_solution(root: Path, number: int, *, kind: str = "py") -> Path:
    """Write a case's oracle suite and reference diff under ``solution/<n>/``."""
    solution = root / "solution" / str(number)
    if kind == "js":
        _write(solution / "test_oracle.js", ORACLE_JS_TEMPLATE.replace("__NUMBER__", str(number)))
    else:
        _write(solution / "test_oracle.py", ORACLE_TEMPLATE.replace("__NUMBER__", str(number)))
    _write(solution / "pkg" / "mod.py.diff", REFERENCE_DIFF)
    return solution


def make_environment(
    tmp_path: Path,
    *,
    name: str = ".environment",
    fixed: tuple[int, ...] = (1,),
    js_case: int | None = None,
) -> Path:
    """Build an environment root with two (or three) cases.

    Case 1 is fixed when asked, case 2 always ships buggy, and ``js_case``
    adds one more case whose oracle is a node:test suite.
    """
    root = tmp_path / name
    make_codebase(root, 1, "Alpha", fixed=1 in fixed)
    make_solution(root, 1)
    make_codebase(root, 2, "Beta", fixed=2 in fixed)
    make_solution(root, 2)
    if js_case is not None:
        codebase = root / "codebase" / str(js_case)
        _write(codebase / "src" / "mod.js", "module.exports = { VALUE: 2 };\n")
        _write(codebase / "tests" / "mod.test.js", PROJECT_JS_TEMPLATE)
        _write(codebase / "README.md", "# Gamma\n")
        _write(codebase / "Issues.md", "Gamma: VALUE should be 2.\n")
        make_solution(root, js_case, kind="js")
    return root
