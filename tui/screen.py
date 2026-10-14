"""Terminal plumbing for the TUI.

Handles raw mode, alternate-screen entry/exit, key reading, and the retained
line buffer used to paint a frame without flicker.  Rendering is full-frame:
each frame moves the cursor home, redraws every line, and clears the rest, so
there is no state to reconcile when the terminal resizes.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import sys
from typing import Iterable

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def visible_width(text: str) -> int:
    """Return the display width of ``text`` with ANSI escapes removed."""
    return len(_ANSI_RE.sub("", text))


def truncate(text: str, width: int) -> str:
    """Truncate ``text`` to ``width`` visible columns, keeping ANSI intact.

    ``width`` must be non-negative.  The returned string never exceeds
    ``width`` visible columns.
    """
    if width <= 0:
        return ""
    out: list[str] = []
    columns = 0
    i = 0
    n = len(text)
    while i < n and columns < width:
        if text[i] == "\x1b":
            m = _ANSI_RE.match(text, i)
            if m is not None:
                out.append(m.group(0))
                i = m.end()
                continue
            out.append(text[i])
            i += 1
            continue
        out.append(text[i])
        columns += 1
        i += 1
    return "".join(out)


def pad(text: str, width: int) -> str:
    """Pad ``text`` with trailing spaces to ``width`` visible columns."""
    current = visible_width(text)
    if current >= width:
        return truncate(text, width)
    return text + " " * (width - current)


def wrap_text(text: str, width: int) -> list[str]:
    """Wrap plain ``text`` into lines no wider than ``width`` columns.

    Long unbroken words are hard-broken so no line exceeds ``width``.
    """
    if width <= 0:
        return []
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if paragraph == "":
            lines.append("")
            continue
        current = ""
        for word in paragraph.split(" "):
            if word == "":
                continue
            if current == "":
                current = word
            elif len(current) + 1 + len(word) <= width:
                current += " " + word
            else:
                lines.append(current)
                current = word
            while len(current) > width:
                lines.append(current[:width])
                current = current[width:]
        lines.append(current)
    return lines if lines else [""]


class Terminal:
    """Wraps stdin/stdout for raw-mode interaction and frame painting."""

    def __init__(self, *, color: bool | None = None) -> None:
        self._use_color = sys.stdout.isatty() if color is None else bool(color)
        self._fd = sys.stdin.fileno() if sys.stdin.isatty() else None
        self._saved: list[object] | None = None
        self._raw = False
        self._keybuf = b""

    # -- lifecycle ------------------------------------------------------- #
    def enter(self) -> None:
        """Enter the alternate screen and raw input mode (when supported)."""
        if self._use_color:
            sys.stdout.write("\x1b[?1049h\x1b[?25l")
            sys.stdout.flush()
        if self._fd is not None and os.isatty(self._fd):  # pragma: no cover - tty only
            try:  # pragma: no cover - tty only
                import termios  # pragma: no cover - tty only
                import tty  # pragma: no cover - tty only

                self._saved = termios.tcgetattr(self._fd)  # pragma: no cover - tty only
                tty.setraw(self._fd)  # pragma: no cover - tty only
                self._raw = True  # pragma: no cover - tty only
            except (ImportError, OSError):  # pragma: no cover - platform dependent
                self._raw = False

    def exit(self) -> None:
        """Restore the terminal and leave the alternate screen."""
        if self._raw and self._fd is not None and self._saved is not None:  # pragma: no cover - tty only
            try:  # pragma: no cover - tty only
                import termios  # pragma: no cover - tty only

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)  # type: ignore[arg-type]  # pragma: no cover - tty only
            except (ImportError, OSError):  # pragma: no cover - platform dependent
                pass
            self._raw = False
        if self._use_color:
            sys.stdout.write("\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()

    # -- sizing ---------------------------------------------------------- #
    def size(self) -> tuple[int, int]:
        """Return ``(columns, lines)`` for the terminal."""
        return shutil.get_terminal_size((80, 24))

    # -- output ---------------------------------------------------------- #
    def paint(self, lines: Iterable[str], cursor: tuple[int, int] | None = None) -> None:
        """Render ``lines`` as a full frame and place the cursor.

        ``lines`` are emitted in order, each padded/truncated to the current
        terminal width.  ``cursor`` is an optional ``(row, column)`` pair,
        both zero-based, where the hardware cursor is placed.
        """
        width, _ = self.size()
        out = ["\x1b[H"]
        row = 0
        for line in lines:
            if row > 0:
                out.append("\r\n")
            out.append(pad(line, width))
            row += 1
        out.append("\x1b[0J")
        if cursor is not None:
            crow, ccol = cursor
            out.append(f"\x1b[{crow + 1};{ccol + 1}H")
        if self._use_color and cursor is not None:
            out.append("\x1b[?25h")
        elif self._use_color:
            out.append("\x1b[?25l")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    # -- input ----------------------------------------------------------- #
    def read_key(self, timeout: float | None = 0.05) -> str | None:
        """Read one key press, or ``None`` when no input arrives in time.

        Returns single characters for printable text and control keys, or the
        tokens ``UP``/``DOWN``/``LEFT``/``RIGHT``/``HOME``/``END``/``DELETE``
        for the common escape sequences.
        """
        if self._fd is None:
            return None
        ready, _, _ = select.select([self._fd], [], [], timeout)  # pragma: no cover - real tty only
        if not ready:  # pragma: no cover - real tty only
            return None
        try:  # pragma: no cover - real tty only
            self._keybuf += os.read(self._fd, 64)  # pragma: no cover - real tty only
        except OSError:  # pragma: no cover - terminal gone
            return None
        return self._parse_key()  # pragma: no cover - real tty only

    def _parse_key(self) -> str | None:
        """Pop one complete key from the pending byte buffer."""
        buf = self._keybuf
        if not buf:
            return None

        first = buf[0]
        if first != 0x1B:
            self._keybuf = buf[1:]
            return bytes([first]).decode("utf-8", "replace")

        if len(buf) < 2:
            return None  # need at least the escape introducer

        if buf[1] == 0x5B:  # '[' -> CSI sequence
            for i in range(2, len(buf)):
                if 0x40 <= buf[i] <= 0x7E:
                    seq = bytes(buf[2 : i + 1])
                    self._keybuf = buf[i + 1 :]
                    return self._map_csi(seq)
            return None  # incomplete CSI sequence

        # Alt-key or lone escape: consume the escape and one following byte.
        self._keybuf = buf[1:]
        if buf[1] == 0x1B:
            return "ESC"
        return bytes(buf[1:2]).decode("utf-8", "replace")

    @staticmethod
    def _map_csi(seq: bytes) -> str | None:
        if seq in (b"A",):
            return "UP"
        if seq in (b"B",):
            return "DOWN"
        if seq in (b"C",):
            return "RIGHT"
        if seq in (b"D",):
            return "LEFT"
        if seq in (b"H",):
            return "HOME"
        if seq in (b"F",):
            return "END"
        if seq in (b"1~", b"7~"):
            return "HOME"
        if seq in (b"4~", b"8~"):
            return "END"
        if seq in (b"3~",):
            return "DELETE"
        if seq in (b"5~",):
            return "PAGE_UP"
        if seq in (b"6~",):
            return "PAGE_DOWN"
        return None
