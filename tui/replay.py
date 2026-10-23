"""Replay a saved transcript in the live TUI.

The live view in :mod:`tui.app` is driven by Pi records as they arrive.  This
module feeds the same records back from a ``transcript.jsonl`` file, so a run
can be watched after the fact: the fan-out panel, per-primitive streams, tool
activity, and gate blocks are rebuilt by the same translation code the live app
uses.

The replay starts playing at a fixed records-per-second rate and can be
paused, stepped, rewound, or jumped to the end.  Only visually meaningful
records are kept by default; ``include_all`` preserves the raw event stream.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .app import _SPINNER, CTRL_C, PipelineApp
from .primitives import PrimitiveFeed
from .transcript import Transcript


@dataclass
class ReplaySource:
    """The small pipeline surface :class:`PipelineApp` needs from a source."""

    role_names: list[str] = field(default_factory=list)
    model_name: str = "?"
    reasoning: str = "?"


def is_text_delta(record: dict[str, Any]) -> bool:
    """Whether ``record`` is an assistant text or thinking delta."""
    if record.get("type") != "message_update":
        return False
    event = record.get("assistantMessageEvent") or {}
    return event.get("type") in ("text_delta", "thinking_delta")


class ReplayApp(PipelineApp):
    """A :class:`PipelineApp` that reads records from a saved transcript.

    The app inherits the live renderer, the primitive panel, the transcript,
    scrolling, and the focus controls.  It replaces the role driving loop with
    a record clock and adds replay-only keys.
    """

    def __init__(
        self,
        records: Sequence[dict[str, Any]],
        *,
        model: str = "?",
        thinking: str = "?",
        instance: str = "",
        theme: Any = None,
        color: bool | None = None,
        show_thinking: bool = True,
        start: str = "start",
        paused: bool = False,
        speed: float = 60.0,
    ) -> None:
        source = ReplaySource(model_name=model, reasoning=thinking)
        super().__init__(source, theme=theme, color=color, show_thinking=show_thinking)
        self.records = list(records)
        self.instance = instance
        self.speed = max(1.0, float(speed))
        self.phase = "running"
        self.current_role = None
        self.playing = not paused
        self._position = 0
        self._accumulator = 0.0
        self._last_time = time.monotonic()
        self._rebuild(0)
        if start == "end":
            self._jump_end()

    # ------------------------------------------------------------------ #
    # Playback state
    # ------------------------------------------------------------------ #
    def _rebuild(self, position: int) -> None:
        """Rebuild the view from the first ``position`` records."""
        position = max(0, min(position, len(self.records)))
        self.transcript = Transcript()
        self.feed = PrimitiveFeed()
        self.selected = None
        self.focus = None
        self._scroll_offset = 0
        self._auto_scroll = True
        self._position = 0
        for record in self.records[:position]:
            self._translate(record)
        self._position = position
        if position >= len(self.records):
            self.feed.finish()

    def _set_position(self, target: int) -> None:
        """Move to ``target``, replaying forward or rebuilding backward."""
        target = max(0, min(target, len(self.records)))
        if target < self._position:
            self._rebuild(target)
            return
        for record in self.records[self._position : target]:
            self._translate(record)
        self._position = target
        if target >= len(self.records):
            self.feed.finish()

    def _advance(self) -> None:
        """Feed the records due at the current playback rate."""
        if not self.playing or self._position >= len(self.records):
            self.playing = False
            return
        now = time.monotonic()
        elapsed = min(0.25, max(0.0, now - self._last_time))
        self._last_time = now
        self._accumulator += elapsed * self.speed
        count = int(self._accumulator)
        if count <= 0:
            return
        self._accumulator -= count
        self._set_position(self._position + count)
        if self._position >= len(self.records):
            self.playing = False

    def _step_forward(self, count: int = 1) -> None:
        self.playing = False
        self._set_position(self._position + count)

    def _step_back(self, count: int = 1) -> None:
        self.playing = False
        self._rebuild(self._position - count)

    def _next_event(self) -> None:
        """Step to the next non-text record (a tool call, report, fan-out)."""
        self.playing = False
        index = self._position
        while index < len(self.records):
            record = self.records[index]
            index += 1
            if not is_text_delta(record):
                break
        self._set_position(index)

    def _jump_start(self) -> None:
        self.playing = False
        self._rebuild(0)

    def _jump_end(self) -> None:
        self.playing = False
        self._scroll_offset = 0
        self._auto_scroll = True
        self._set_position(len(self.records))

    def _restart(self) -> None:
        self._rebuild(0)
        self._accumulator = 0.0
        self._last_time = time.monotonic()
        self.playing = True

    def _set_speed(self, speed: float) -> None:
        self.speed = max(5.0, min(1000.0, float(speed)))

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def _header_content(self) -> str:
        title = self.theme.style("hms replay", fg="accent", bold=True)
        meta = f" \u00b7 {self.model} \u00b7 {self.thinking}"
        if self.instance:
            meta += f" \u00b7 {self.instance}"
        return title + self.theme.style(meta, fg="muted")

    def _status_content(self) -> str:
        if self._position >= len(self.records) and not self.playing:
            state = "end"
        elif self.playing:
            state = "playing"
        else:
            state = "paused"
        spinner = _SPINNER[self._tick % len(_SPINNER)] if self.playing else "\u275a\u275a"
        base = (
            f"{spinner}  replay \u00b7 {self._position}/{len(self.records)} "
            f"\u00b7 {state} \u00b7 {self.speed:g}/s"
        )
        if self.feed:
            base += f"  \u00b7 {self.feed.summary()}"
        return self.theme.style(
            base, fg="warning" if self.playing else "accent"
        )

    def _phase_hint(self) -> str:
        if self.focus is not None:
            return "\u2191\u2193 scroll \u00b7 Esc or q back to every primitive"
        parts = ["Space play/pause", "\u2190/\u2192 step", "n next event"]
        if self.feed:
            parts += ["Tab switch", "Enter open"]
        parts += ["[ ] speed", "g/G start/end", "q quit"]
        return " \u00b7 ".join(parts)

    # ------------------------------------------------------------------ #
    # Input and loop
    # ------------------------------------------------------------------ #
    def _handle_key(self, key: str) -> None:
        if key in ("UP", "DOWN", "PAGE_UP", "PAGE_DOWN"):
            super()._handle_key(key)
            return
        if self._panel_key(key):
            return
        if key in ("q", "Q"):
            self.done = True
            return
        if key == CTRL_C:
            self.done = True
            return
        if key in (" ", "p", "P"):
            if self._position >= len(self.records):
                self._restart()
            else:
                self.playing = not self.playing
                self._accumulator = 0.0
                self._last_time = time.monotonic()
            return
        if key == "RIGHT":
            self._step_forward()
            return
        if key == "LEFT":
            self._step_back()
            return
        if key == "n":
            self._next_event()
            return
        if key == "g":
            self._jump_start()
            return
        if key == "G":
            self._jump_end()
            return
        if key == "r":
            self._restart()
            return
        if key == "]":
            self._set_speed(self.speed * 2)
            return
        if key == "[":
            self._set_speed(self.speed / 2)
            return
        super()._handle_key(key)

    def run(self) -> dict[str, Any]:
        """Play the transcript until the operator quits."""
        self.term.enter()
        self._last_time = time.monotonic()
        try:
            while not self.done:
                if self.playing:
                    self._advance()
                self._render()
                key = self.term.read_key(timeout=0.03)
                if key is not None:
                    self._handle_key(key)
                self._tick += 1
        finally:
            self.term.exit()
        return {}


__all__ = ["ReplayApp", "ReplaySource", "is_text_delta"]
