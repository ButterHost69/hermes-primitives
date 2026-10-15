"""Tests for the startup-failure gaps found while diagnosing the TUI timeout.

Running ``python -m tui --model step-5-preview-free ./environment/codebase/1``
ends in ``request timed out`` because ``pi`` itself refuses to start: the model
id is ambiguous across two authenticated providers, so Pi writes the reason to
stderr and exits 1 before speaking any JSONL.  Three defects turn that into a
silent hang, and each one is pinned here:

1. :meth:`agent.client.PiClient.send` only checks ``running`` *before* writing
   a command; while it waits for the response it never notices the subprocess
   dying, so a crashed Pi is indistinguishable from a slow one and every
   command costs its full timeout (30s in the ``prompt`` path).
2. Both clients spawn ``pi`` with ``stderr=None``, so Pi's fatal startup
   message is inherited by the shared terminal stderr instead of being
   captured: the client cannot report *why* Pi died, and the TUI's alternate
   screen repaints over the message before it can be read.
3. :func:`tui.__main__.main` returns normally when a role failed, so the
   command exits 0 with an empty result set -- scripts cannot detect it.

Each test below reproduces its defect and currently FAILS (red): together they
describe the behaviour the fixes have to deliver.
"""

from __future__ import annotations

import subprocess
import threading
import time

import pytest

import agent.client as client_module
import agent.gated_client as gated_module
import tui.__main__ as main_module
from tui.__main__ import main
from agent.client import PiClient, PiError
from agent.gated_client import GatedPiClient
from agent.session import PiSession


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class _FakeStdin:
    def __init__(self) -> None:
        self.closed = False
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _DeadStderr:
    """A drained stderr pipe: nothing left to read, ever."""

    def read(self, *args) -> bytes:
        return b""

    def readline(self, *args) -> bytes:
        return b""

    def __iter__(self):
        return iter(())

    def close(self) -> None:
        pass


class _DeadStdout:
    """A stdout pipe at EOF: the reader thread ends immediately."""

    def __iter__(self):
        return iter(())

    def close(self) -> None:
        pass


class _RecordingPopen:
    """Stands in for ``subprocess.Popen`` and records how it was called."""

    def __init__(self, argv, **kwargs) -> None:
        self.argv = argv
        self.kwargs = kwargs
        self.stdin = _FakeStdin()
        self.stdout = _DeadStdout()
        self.stderr = _DeadStderr()
        self.returncode = None

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


class _DyingProc:
    """A subprocess alive when ``send`` starts and dead while it waits.

    ``poll`` answers ``None`` for the first ``alive_polls`` calls -- the one
    the ``running`` check makes before writing -- and an exit code from then
    on: the crashed Pi, gone from the wire, never answering the command.
    """

    def __init__(self, exit_code: int = 17, alive_polls: int = 1) -> None:
        self.exit_code = exit_code
        self.alive_polls = alive_polls
        self.polls = 0
        self.returncode = None
        self.stdin = _FakeStdin()
        self.stdout = _DeadStdout()
        self.stderr = _DeadStderr()

    def poll(self):
        self.polls += 1
        if self.polls <= self.alive_polls:
            return None
        self.returncode = self.exit_code
        return self.exit_code


def command_outcome(client: PiClient, command: dict, timeout: float) -> dict:
    """Send one command and normalize raise/return into a result dict.

    A fix may report the failure either as an error response or as a raised
    :class:`PiError`; both are legitimate, so both are accepted here.
    """
    try:
        return client.send(command, timeout=timeout)
    except PiError as exc:
        return {"success": False, "error": str(exc)}


def run_with_deadline(fn, deadline: float) -> dict:
    """Run ``fn`` in a daemon thread and fail the test if it outlives it.

    A call that blocks on a dead Pi subprocess never returns on its own before
    its timeout, so the watchdog is what makes the defect visible quickly.
    """
    outcome: dict = {}

    def worker() -> None:
        start = time.monotonic()
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported through outcome
            outcome["error"] = exc
        outcome["elapsed"] = time.monotonic() - start

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=deadline)
    if thread.is_alive():
        pytest.fail(
            f"the call did not return within {deadline}s: it is blocked on a "
            "pi subprocess that is already gone"
        )
    return outcome


# --------------------------------------------------------------------------- #
# Issue 1 -- a dead `pi` must not be reported as a timeout
# --------------------------------------------------------------------------- #
def test_send_notices_a_subprocess_that_dies_mid_command():
    """``send`` must stop waiting once the subprocess is gone.

    Today the wait loop only watches for the correlated response, so a Pi that
    died while the command was in flight is held for the whole ``timeout`` and
    then reported as ``{"error": "request timed out"}`` -- the same answer a
    merely slow Pi would get.
    """
    client = PiClient()
    client._process = _DyingProc()

    start = time.monotonic()
    outcome = command_outcome(client, {"type": "prompt", "message": "hi"}, timeout=5.0)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, f"send ignored the dead subprocess and waited {elapsed:.1f}s"
    assert outcome["success"] is False
    assert "timed out" not in (outcome.get("error") or "")


def test_send_against_crashed_pi_fails_fast_with_the_reason(crash_pi):
    """End to end: a Pi that dies at startup fails fast, quoting Pi's stderr.

    This is the exact path the TUI's roles take.  The command must come back
    quickly, marked as a failure, carrying the message Pi wrote on stderr
    instead of a bare ``request timed out``.
    """
    client = PiClient()
    client.start()
    try:
        start = time.monotonic()
        outcome = command_outcome(
            client, {"type": "prompt", "message": "hi"}, timeout=5.0
        )
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"send waited {elapsed:.1f}s on a dead subprocess"
        assert outcome["success"] is False
        assert "ambiguous" in (outcome.get("error") or ""), (
            "the reason pi gave for dying is lost: "
            f"{outcome.get('error')!r}"
        )
    finally:
        client.close()


def test_session_run_fails_fast_when_pi_dies_at_startup(crash_pi):
    """``PiSession.run`` must surface the crash instead of timing out.

    The reproduce/diagnose roles drive a session whose prompt path waits 30s,
    so a Pi that never came up currently costs the operator half a minute of
    spinner per role before failing with a misleading timeout.
    """
    session = PiSession(timeout=2.0)
    try:
        outcome = run_with_deadline(lambda: session.run("hi"), deadline=6.0)
    finally:
        session.close()

    error = outcome.get("error")
    assert isinstance(error, PiError), f"expected a PiError, got {outcome!r}"
    assert "timed out" not in str(error)


def test_reproduce_fails_fast_and_says_why_pi_died(crash_pi, tmp_path):
    """The TUI's first role, on the ambiguous model, must fail with Pi's reason.

    ``reproduce`` is what the TUI runs first; with Pi dead on arrival the role
    should raise promptly and name the problem Pi reported (the ambiguous
    model), not a generic timeout.
    """
    from roles import reproduce as reproduce_role

    outcome = run_with_deadline(
        lambda: reproduce_role.reproduce(
            "order totals go negative when a discount is applied",
            model="step-5-preview-free",
            thinking="low",
            cwd=str(tmp_path),
        ),
        deadline=6.0,
    )

    error = outcome.get("error")
    assert isinstance(error, PiError), f"expected a PiError, got {outcome!r}"
    assert "ambiguous" in str(error)


# --------------------------------------------------------------------------- #
# Issue 2 -- Pi's stderr must be captured, not inherited
# --------------------------------------------------------------------------- #
def test_start_captures_the_child_stderr(monkeypatch):
    """``PiClient`` must spawn ``pi`` with a stderr pipe.

    ``stderr=None`` hands Pi's fatal startup message to the shared terminal,
    where the TUI's alternate screen repaints over it; capturing it is what
    lets the client report why Pi died.
    """
    monkeypatch.setattr(client_module, "which", lambda name: "/fake/pi")
    monkeypatch.setattr(client_module.subprocess, "Popen", _RecordingPopen)

    client = PiClient()
    client.start()
    try:
        assert client._process.kwargs["stderr"] is subprocess.PIPE
    finally:
        client.close()


def test_gated_start_captures_the_child_stderr(monkeypatch, tmp_path):
    """The gated client spawns its own subprocess; it must capture stderr too.

    The primitive_manager role runs through this path, so the same startup
    crash there must be reportable for the same reason.
    """
    extension = tmp_path / "gate.js"
    extension.write_text("export default function () {}\n")
    monkeypatch.setattr(client_module, "which", lambda name: "/fake/pi")
    monkeypatch.setattr(gated_module.subprocess, "Popen", _RecordingPopen)

    client = GatedPiClient(extension_path=extension)
    client.start()
    try:
        assert client._process.kwargs["stderr"] is subprocess.PIPE
        # the gate settings still have to reach the child
        assert client._process.kwargs["env"]["HMS_TOOL_GATE_TIMEOUT_MS"]
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# Issue 3 -- a failed role must make the command exit non-zero
# --------------------------------------------------------------------------- #
class _FakePipeline:
    def __init__(self, codebase, model, thinking, api_key):
        self.wired = (codebase, model, thinking, api_key)


class _ResultApp:
    """A PipelineApp stand-in whose ``run()`` completed."""

    error = None

    def __init__(self, pipeline, theme=None, color=None, show_thinking=True):
        self.pipeline = pipeline

    def run(self):
        return {"reproduce": {"summary": "reproduced"}}


class _FailedApp(_ResultApp):
    """A PipelineApp stand-in whose ``run()`` stopped on a failed role."""

    error = "reproduce failed: request timed out"


def test_main_exits_zero_when_every_role_succeeded(monkeypatch, capsys):
    monkeypatch.setattr(main_module, "Pipeline", _FakePipeline)
    monkeypatch.setattr(main_module, "PipelineApp", _ResultApp)

    exit_code = None
    try:
        main(["repo"])
    except SystemExit as exc:
        exit_code = exc.code
    assert exit_code in (None, 0)

    assert "reproduce" in capsys.readouterr().out


def test_main_exits_nonzero_when_a_role_failed(monkeypatch, capsys):
    """A failed role must not look like a successful, empty run.

    Today ``main`` prints whatever results came back -- none, when the role
    failed -- and returns normally, so ``python -m tui`` exits 0 and the caller
    cannot tell the pipeline apart from one that finished.
    """
    monkeypatch.setattr(main_module, "Pipeline", _FakePipeline)
    monkeypatch.setattr(main_module, "PipelineApp", _FailedApp)

    with pytest.raises(SystemExit) as excinfo:
        main(["repo"])
    assert excinfo.value.code not in (None, 0)

    captured = capsys.readouterr()
    assert "request timed out" in captured.out + captured.err
    assert "reproduce" in captured.out  # partial results are still reported
