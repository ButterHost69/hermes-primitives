"""Thin JSONL RPC client for the Pi coding agent.

Spawns ``pi --mode rpc`` as a long-lived subprocess and speaks its JSONL
protocol: commands in on stdin, responses and session events out on stdout.
See ``docs/rpc.md`` and ``docs/rpc-commands.md`` in the Pi package for the
wire reference.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import threading
import time
from shutil import which
from typing import Any, Callable, Sequence

EventCallback = Callable[[dict[str, Any]], None]


class PiError(RuntimeError):
    """Raised when Pi cannot be started or a request fails."""


class PiClient:
    """A running Pi RPC subprocess with command/response correlation."""

    def __init__(
        self,
        *,
        tools: Sequence[str] | None = None,
        model: str | None = None,
        thinking: str | None = None,
        cwd: str | None = None,
        persistent_session: bool = True,
        session_dir: str | None = None,
    ) -> None:
        self.tools: Sequence[str] | None = tools
        self.model = model
        self.thinking = thinking
        self.cwd = cwd
        self.persistent_session = persistent_session
        self.session_dir = session_dir

        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._handlers: list[EventCallback] = []
        self._pending: dict[str, dict[str, Any]] = {}
        self._cond = threading.Condition()
        self._write_lock = threading.Lock()
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------ #
    # Process lifecycle
    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _session_dir(self) -> str:
        """Directory where Pi persists this session.

        Defaults to ``.sessions/`` under the session's working directory.
        """
        if self.session_dir is not None:
            return self.session_dir
        return os.path.join(self.cwd or os.getcwd(), ".sessions")

    def _build_argv(self) -> list[str]:
        """Build the ``pi`` argument list from the current configuration."""
        exe = which("pi")
        if exe is None:
            raise PiError(
                "`pi` executable not found on PATH. Install Pi or set PATH."
            )

        argv = [exe, "--mode", "rpc"]
        if self.persistent_session:
            argv += ["--session-dir", self._session_dir()]
        else:
            argv.append("--no-session")
        if self.model:
            argv += ["--model", self.model]
        if self.thinking:
            argv += ["--thinking", self.thinking]
        if self.tools is not None:
            names = list(self.tools)
            if names:
                argv += ["--tools", ",".join(names)]
            else:
                argv.append("--no-tools")
        return argv

    def start(self) -> None:
        """Launch the Pi subprocess and start the stdout reader thread."""
        try:
            self._process = subprocess.Popen(
                self._build_argv(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,  # Pi diagnostics flow through to our stderr
                cwd=self.cwd,
            )
        except OSError as exc:  # pragma: no cover - environment dependent
            raise PiError(f"failed to start `pi`: {exc}") from exc

        self._reader = threading.Thread(
            target=self._read_loop, name="pi-rpc-reader", daemon=True
        )
        self._reader.start()

    def close(self) -> None:
        """Shut the subprocess down and reap the reader thread."""
        proc = self._process
        if proc is None:
            return
        if proc.stdin:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        reader = self._reader
        if reader and reader is not threading.current_thread():
            reader.join(timeout=2)
        self._process = None
        self._reader = None
        with self._cond:
            self._pending.clear()

    def restart(self) -> None:
        """Restart with the current configuration (used for tool changes)."""
        self.close()
        self.start()

    # ------------------------------------------------------------------ #
    # Command/response protocol
    # ------------------------------------------------------------------ #
    def on_event(self, handler: EventCallback) -> None:
        """Register a callback for session events and non-response records."""
        self._handlers.append(handler)

    def send(self, command: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        """Send one command and block until its correlated response arrives."""
        if not self.running:
            raise PiError("Pi is not running")
        assert self._process is not None and self._process.stdin is not None

        rid = command.get("id") or f"req-{next(self._ids)}"
        outgoing = {**command, "id": rid}
        with self._cond:
            self._pending.pop(rid, None)

        payload = json.dumps(outgoing, ensure_ascii=False).encode("utf-8") + b"\n"
        with self._write_lock:
            try:
                self._process.stdin.write(payload)
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise PiError(f"failed to write to Pi: {exc}") from exc

        deadline = time.monotonic() + timeout
        with self._cond:
            while rid not in self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._pending.pop(rid, None)
                    return {
                        "type": "response",
                        "command": outgoing.get("type"),
                        "success": False,
                        "error": "request timed out",
                        "id": rid,
                    }
                self._cond.wait(remaining)
            return self._pending.pop(rid)

    def command_ok(self, command: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        """Send a command and return its ``data``, raising on failure."""
        response = self.send(command, timeout=timeout)
        if not response.get("success", False):
            raise PiError(response.get("error") or f"{command.get('type')} failed")
        return response.get("data") or {}

    # ------------------------------------------------------------------ #
    # Convenience commands
    # ------------------------------------------------------------------ #
    def prompt(self, message: str, *, steer: bool = False, follow_up: bool = False) -> dict[str, Any]:
        """Send a user prompt. Blocks only until accepted (see docs)."""
        command: dict[str, Any] = {"type": "prompt", "message": message}
        if steer:
            command = {"type": "steer", "message": message}
        elif follow_up:
            command = {"type": "follow_up", "message": message}
        return self.send(command)

    def abort(self) -> dict[str, Any]:
        """Abort the current operation and wait for the session to go idle."""
        return self.send({"type": "abort"}, timeout=60.0)

    def get_state(self) -> dict[str, Any]:
        return self.command_ok({"type": "get_state"})

    def get_models(self) -> list[dict[str, Any]]:
        data = self.command_ok({"type": "get_available_models"})
        return data.get("models", [])

    def set_model(self, model: dict[str, Any]) -> dict[str, Any]:
        return self.command_ok(
            {"type": "set_model", "provider": model["provider"], "modelId": model["id"]}
        )

    def get_thinking_levels(self) -> list[str]:
        data = self.command_ok({"type": "get_available_thinking_levels"})
        return data.get("levels", [])

    def set_thinking(self, level: str) -> dict[str, Any]:
        return self.command_ok({"type": "set_thinking_level", "level": level})

    def new_session(self) -> dict[str, Any]:
        return self.command_ok({"type": "new_session"})

    # ------------------------------------------------------------------ #
    # Internal reader
    # ------------------------------------------------------------------ #
    def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        for raw in self._process.stdout:
            line = raw.rstrip(b"\r\n")
            if not line:
                continue
            try:
                record = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            self._dispatch(record)

    def _dispatch(self, record: dict[str, Any]) -> None:
        if record.get("type") == "response":
            rid = record.get("id")
            if rid is not None:
                with self._cond:
                    self._pending[rid] = record
                    self._cond.notify_all()
        for handler in list(self._handlers):
            try:
                handler(record)
            except Exception:
                # A display callback must never kill the reader thread.
                pass
