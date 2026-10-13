"""An ordered, bounded transcript of streamed role activity.

Text deltas stream in without newlines, and tool/role markers interleave with
them.  :class:`Transcript` buffers a partial text line, flushes it when a
marker or newline arrives, and keeps a bounded tail of finished ``(kind,
text)`` lines so the body of the TUI can render the most recent activity.
"""

from __future__ import annotations

KIND_TEXT = "text"
KIND_THINKING = "thinking"
KIND_TOOL = "tool"
KIND_ROLE = "role"
KIND_INFO = "info"
KIND_ERROR = "error"
KIND_RESULT = "result"


class Transcript:
    """Ordered stream of styled transcript lines."""

    def __init__(self, max_lines: int = 2000) -> None:
        self.max_lines = max_lines
        self.lines: list[tuple[str, str]] = []
        self._pending = ""
        self._pending_kind = KIND_TEXT

    def _append(self, kind: str, text: str) -> None:
        if text == "":
            return
        self.lines.append((kind, text))
        overflow = len(self.lines) - self.max_lines
        if overflow > 0:
            del self.lines[:overflow]

    def _flush(self) -> None:
        if self._pending:
            self._append(self._pending_kind, self._pending)
            self._pending = ""
            self._pending_kind = KIND_TEXT

    # -- streaming ------------------------------------------------------- #
    def text(self, delta: str) -> None:
        """Append an assistant text delta, splitting on newlines."""
        self._pending_kind = KIND_TEXT
        self._pending += delta
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._append(KIND_TEXT, line)

    def thinking(self, delta: str) -> None:
        """Append a thinking delta (rendered dim, unlike assistant text)."""
        self._pending_kind = KIND_THINKING
        self._pending += delta
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._append(KIND_THINKING, line)

    # -- markers --------------------------------------------------------- #
    def tool(self, text: str) -> None:
        """Add a tool-activity line, flushing any partial text first."""
        self._flush()
        self._append(KIND_TOOL, text)

    def role(self, text: str) -> None:
        """Add a role banner line, flushing any partial text first."""
        self._flush()
        self._append(KIND_ROLE, text)

    def extend_last(self, text: str) -> None:
        """Append ``text`` to the most recent line (for tool completion)."""
        if self.lines:
            kind, previous = self.lines[-1]
            self.lines[-1] = (kind, previous + text)

    def info(self, text: str) -> None:
        self._flush()
        self._append(KIND_INFO, text)

    def error(self, text: str) -> None:
        self._flush()
        self._append(KIND_ERROR, text)

    def result(self, text: str) -> None:
        self._flush()
        self._append(KIND_RESULT, text)

    def settle(self) -> None:
        """Flush any pending partial line (called when a turn settles)."""
        self._flush()

    def partial(self) -> tuple[str, str]:
        """Return the pending partial ``(kind, text)`` without flushing it."""
        return self._pending_kind, self._pending
