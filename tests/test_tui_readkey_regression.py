"""Regression tests for the read_key buffering bug in :mod:`tui.screen`.

These reproduce a real, user-reported bug: on the "done" screen, pressing ``q``
or ``Enter`` once sometimes does not close the TUI.

Root cause (tui/screen.py::Terminal.read_key)::

    ready, _, _ = select.select([self._fd], [], [], timeout)
    if not ready:
        return None                      # returns WITHOUT draining self._keybuf
    self._keybuf += os.read(self._fd, 64)  # slurps a whole burst of bytes...
    return self._parse_key()               # ...but parses only ONE key

When the terminal delivers more than one byte at once (a paste, fast typing, or
an escape sequence under WezTerm/WSL), ``os.read`` grabs them all but only one
key is returned.  On the next loop iteration the fd is *not* readable, so
``read_key`` returns ``None`` and the remaining bytes sit stranded in
``_keybuf``.  A subsequent single ``q``/``Enter`` then lands *behind* a stranded
byte and is swallowed -- the app processes the stranded byte (a no-op) and the
real key becomes the new stranded byte.

These tests are intentionally written to FAIL on the current code so they
document/validate the bug; they turn green once ``read_key`` drains already
buffered keys before blocking on ``select``.

They drive the real ``read_key`` over a genuine ``os.pipe()`` fd rather than a
mock, so they exercise the exact production input path.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from tui.app import PipelineApp
from tui.screen import Terminal


@pytest.fixture
def term_pipe():
    """A real Terminal whose read end is the read end of an OS pipe."""
    read_end, write_end = os.pipe()
    term = Terminal(color=False)
    term._fd = read_end  # bypass isatty(); read_key uses this fd for real
    try:
        yield term, write_end
    finally:
        os.close(read_end)
        os.close(write_end)


# --------------------------------------------------------------------------- #
# Terminal.read_key level: buffered bytes are abandoned when the fd goes idle.
# --------------------------------------------------------------------------- #
def test_read_key_drains_pending_bytes_when_fd_idle(term_pipe):
    # Leftover bytes from an earlier multi-byte read, fd NOT readable.
    term, _w = term_pipe
    term._keybuf = b"bc"
    assert term.read_key(timeout=0.01) == "b"  # buggy: None
    assert term.read_key(timeout=0.01) == "c"  # buggy: None
    assert term.read_key(timeout=0.01) is None


def test_read_key_returns_every_byte_of_a_burst(term_pipe):
    # A whole report arrives in one write (one os.read). Every byte must still
    # be handed to the caller; today only the first one is.
    term, w = term_pipe
    payload = b"the login button does nothing"
    os.write(w, payload)
    got = []
    for _ in range(len(payload) + 2):
        key = term.read_key(timeout=0.02)
        if key is None:
            break
        got.append(key)
    assert "".join(got) == payload.decode()


def test_read_key_single_key_after_burst_is_not_swallowed(term_pipe):
    # Two bytes at once -> one parsed, one stranded; the stranded one must be
    # returned even with no new input pending.
    term, w = term_pipe
    os.write(w, b"ab")
    assert term.read_key(timeout=0.05) == "a"
    assert term.read_key(timeout=0.01) == "b"  # buggy: None (stranded)


# --------------------------------------------------------------------------- #
# App level: the actual user-facing symptom.
# --------------------------------------------------------------------------- #
class _DummyPipeline:
    def __init__(self) -> None:
        self.role_names = ["reproduce"]
        self.model_name = "claude"
        self.reasoning = "low"
        self.observer = None


def test_pressing_q_once_closes_tui_even_with_stranded_input(monkeypatch):
    """The user-facing symptom: a stranded byte makes a single ``q`` no-op."""
    read_end, write_end = os.pipe()
    try:
        app = PipelineApp(_DummyPipeline(), color=False)
        app.term.size = lambda: (80, 24)  # stable geometry
        app.term._fd = read_end           # real input fd
        monkeypatch.setattr(app, "_render", lambda: None)  # keep it quiet/fast
        app.phase = "done"
        app.done = False

        out: dict[str, object] = {}

        def runner():
            out["done"] = app.run()

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        try:
            time.sleep(0.2)
            os.write(write_end, b"ZZ")  # burst strands one 'Z' in _keybuf
            time.sleep(0.3)
            os.write(write_end, b"q")   # user presses q EXACTLY ONCE
            thread.join(timeout=2.0)
            quitted_on_its_own = not thread.is_alive()
            if thread.is_alive():  # cleanup so a buggy hang can't leak the test
                app.done = True
                thread.join(timeout=1.0)
        finally:
            pass
    finally:
        os.close(read_end)
        os.close(write_end)

    assert quitted_on_its_own, (
        "pressing q once did not close the TUI: a stranded input byte "
        "was processed instead and the real key was swallowed."
    )
    assert app.done is True
