"""A Pi RPC client that decides every tool call in Python before it runs.

:class:`~agent.client.PiClient` is left exactly as it was.  This module adds a
second client type that loads ``agent/tool_gate.js`` into the spawned Pi process
and routes that extension's decision dialogs to a policy callable.  The policy
runs in Python, so the existing role rules, path helpers, and operator prompts
stay where the rest of HMS lives.

The extension asks its question through Pi's extension-UI subprotocol
(docs/rpc-extension-ui.md in the Pi package):

    pi  -> hms  {"type": "extension_ui_request", "id": ..., "method": "input",
                 "title": "hms-tool-gate", "placeholder": "<request JSON>"}
    hms -> pi   {"type": "extension_ui_response", "id": ..., "value": "<verdict JSON>"}

Pi blocks the tool call while the dialog is open, so the policy decision lands
*before* execution.  An ``extension_ui_response`` produces no normal response
record, so it is written straight to the subprocess' stdin instead of going
through :meth:`PiClient.send`.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Sequence

from .client import PiClient, PiError
from .session import PiSession

#: Dialog marker the tool-gate extension puts in the request title.
GATE_TITLE = "hms-tool-gate"

#: How long the extension waits for a verdict before it fails closed.
DEFAULT_GATE_TIMEOUT_MS = 30_000

#: The extension file this client loads into the Pi subprocess.
TOOL_GATE_JS = Path(__file__).with_name("tool_gate.js")

#: A policy maps a decision request to a verdict.  See agent/tool_gate.js.
Policy = Callable[[dict[str, Any]], dict[str, Any]]

_VERDICT_ACTIONS = ("allow", "block")


def allow_all(request: dict[str, Any]) -> dict[str, Any]:
    """Allow every call.  Only sensible when another control already applies."""
    return {"action": "allow"}


def block_all(request: dict[str, Any]) -> dict[str, Any]:
    """Block every call: the fail-closed default for a run nothing may escape."""
    return {"action": "block", "reason": "tool gate: nothing is allowed here"}


class GatedPiClient(PiClient):
    """A Pi RPC client whose tool calls are gated by a Python policy.

    The policy receives the request the extension sent and returns one of::

        {"action": "block", "reason": "why the model should not retry"}
        {"action": "allow"}
        {"action": "allow", "patch": {"path": "...", ...}}

    ``patch`` entries are assigned onto the tool's arguments before execution.
    Anything the policy cannot produce -- an exception, a non-object, an unknown
    action, or an undecodable request -- blocks the call.
    """

    def __init__(
        self,
        *,
        policy: Policy | None = None,
        gate_timeout_ms: int = DEFAULT_GATE_TIMEOUT_MS,
        max_workers: int = 8,
        extension_path: str | Path | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.policy: Policy = policy or allow_all
        self.gate_timeout_ms = gate_timeout_ms
        self._max_workers = max_workers
        self._extension_path = (
            Path(extension_path) if extension_path is not None else TOOL_GATE_JS
        )
        self._gate_pending: set[str] = set()
        self._gate_lock = threading.Lock()
        self._pool = self._new_pool()

    # ------------------------------------------------------------------ #
    # Subprocess lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Launch Pi with the gate extension loaded.

        ``PiClient.start`` spawns the subprocess without an environment, so the
        gate timeout cannot reach the extension through it; this override passes
        one.  Decisions run off the reader thread, so parallel tool calls do not
        serialize behind the policy.

        Pi's stderr is captured for the reason the base client captures it: a
        fatal startup error has to stay reportable, not be inherited by our
        terminal.
        """
        if not self._extension_path.is_file():
            raise PiError(f"tool gate extension not found: {self._extension_path}")
        try:
            self._process = subprocess.Popen(
                self._build_argv(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=self._child_env(),
            )
        except OSError as exc:  # pragma: no cover - environment dependent
            raise PiError(f"failed to start `pi`: {exc}") from exc
        self._drain_stderr(self._process)
        self._pool = self._new_pool()
        self._reader = threading.Thread(
            target=self._read_loop, name="pi-rpc-reader", daemon=True
        )
        self._reader.start()

    def _child_env(self) -> dict[str, str]:
        """Environment for the Pi subprocess: ours, plus the gate settings."""
        env = dict(os.environ)
        env["HMS_TOOL_GATE_TIMEOUT_MS"] = str(self.gate_timeout_ms)
        return env

    def _new_pool(self) -> ThreadPoolExecutor:
        """A pool for policy decisions; ``start`` replaces a shut-down one."""
        return ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="hms-tool-gate"
        )

    def close(self) -> None:
        """Dismiss outstanding decisions, then shut the subprocess down."""
        with self._gate_lock:
            outstanding = sorted(self._gate_pending)
            self._gate_pending.clear()
        for request_id in outstanding:
            self.cancel_ui(request_id)
        super().close()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def restart(self) -> None:
        """Restart with the current configuration (used for tool changes)."""
        self.close()
        self.start()

    # ------------------------------------------------------------------ #
    # Wire format
    # ------------------------------------------------------------------ #
    def _build_argv(self) -> list[str]:
        """Add the gate extension to the arguments ``PiClient`` builds."""
        return super()._build_argv() + ["--extension", str(self._extension_path)]

    def _dispatch(self, record: dict[str, Any]) -> None:
        """Route gate dialogs to the policy; pass every record to observers."""
        request_id = record.get("id")
        if (
            request_id is not None
            and record.get("type") == "extension_ui_request"
            and record.get("method") == "input"
            and record.get("title") == GATE_TITLE
        ):
            self._route_gate(str(request_id), record)
        super()._dispatch(record)

    def _route_gate(self, request_id: str, record: dict[str, Any]) -> None:
        """Send one decision request to the policy, off the reader thread."""
        with self._gate_lock:
            self._gate_pending.add(request_id)
        self._pool.submit(self._decide, request_id, record)

    def _decide(self, request_id: str, record: dict[str, Any]) -> None:
        try:
            self.respond_ui(request_id, self._verdict_for(record))
        finally:
            with self._gate_lock:
                self._gate_pending.discard(request_id)

    def _verdict_for(self, record: dict[str, Any]) -> dict[str, Any]:
        """Turn one dialog record into a verdict, failing closed throughout."""
        try:
            request = json.loads(record.get("placeholder") or "")
        except (json.JSONDecodeError, TypeError):
            return {"action": "block", "reason": "tool gate: undecodable request"}
        if not isinstance(request, dict):
            return {"action": "block", "reason": "tool gate: malformed request"}
        try:
            verdict = self.policy(request)
        except Exception:
            return {"action": "block", "reason": "tool gate: policy raised an exception"}
        if not isinstance(verdict, dict) or verdict.get("action") not in _VERDICT_ACTIONS:
            return {"action": "block", "reason": "tool gate: policy returned no verdict"}
        return verdict

    # ------------------------------------------------------------------ #
    # Answering dialogs
    # ------------------------------------------------------------------ #
    def respond_ui(self, request_id: str, verdict: dict[str, Any]) -> None:
        """Answer a gate dialog with a verdict.  Expects no response record."""
        self._write_record(
            {
                "type": "extension_ui_response",
                "id": request_id,
                "value": json.dumps(verdict, ensure_ascii=False),
            }
        )

    def cancel_ui(self, request_id: str) -> None:
        """Dismiss a dialog.  Pi resolves it to the fail-closed default."""
        self._write_record(
            {"type": "extension_ui_response", "id": request_id, "cancelled": True}
        )

    def _write_record(self, record: dict[str, Any]) -> None:
        """Write a record to Pi's stdin directly.

        Unlike :meth:`PiClient.send` this never raises: a subprocess that is
        gone cannot be answered, and the extension's timeout already blocks the
        call it was asking about.
        """
        proc = self._process
        if proc is None or proc.stdin is None:
            return
        payload = json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
        with self._write_lock:
            try:
                proc.stdin.write(payload)
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass


class GatedPiSession(PiSession):
    """A :class:`~agent.session.PiSession` that owns a :class:`GatedPiClient`.

    ``PiSession`` builds a plain ``PiClient`` itself, so the wiring is repeated
    here on purpose rather than changed there.
    """

    def __init__(
        self,
        *,
        tools: Sequence[str] | None = None,
        model: str | None = None,
        thinking: str | None = None,
        cwd: str | None = None,
        persistent_session: bool = True,
        session_dir: str | None = None,
        timeout: float = 120.0,
        observer: Callable[[dict[str, Any]], None] | None = None,
        policy: Policy | None = None,
        gate_timeout_ms: int = DEFAULT_GATE_TIMEOUT_MS,
        max_workers: int = 8,
        extension_path: str | Path | None = None,
    ) -> None:
        self.timeout = timeout
        self._parts: list[str] = []
        self._settled = threading.Event()
        self._last_activity = time.monotonic()
        self.client = GatedPiClient(
            tools=tools,
            model=model,
            thinking=thinking,
            cwd=cwd,
            persistent_session=persistent_session,
            session_dir=session_dir,
            policy=policy,
            gate_timeout_ms=gate_timeout_ms,
            max_workers=max_workers,
            extension_path=extension_path,
        )
        self.client.on_event(self._on_event)
        if observer is not None:
            self.client.on_event(observer)
        self.client.start()
