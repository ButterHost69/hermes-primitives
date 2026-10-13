"""Interactive terminal UI for the pipeline.

:class:`PipelineApp` drives a :class:`~pipeline.Pipeline` one role at a time
and paints a live, Pi-styled screen while it runs: streamed assistant text,
tool activity, role banners, and a status bar all update in place.  The only
interaction points are the places a human actually enters text — the initial
bug report and an optional context message between roles.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any

from .screen import Terminal, pad, wrap_text
from .theme import DARK_THEME, Theme, theme_by_name
from .transcript import (
    KIND_ERROR,
    KIND_INFO,
    KIND_RESULT,
    KIND_ROLE,
    KIND_THINKING,
    KIND_TOOL,
    Transcript,
)

_SPINNER = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
ENTER_KEYS = ("\r", "\n")
CTRL_C = "\x03"
CTRL_D = "\x04"
CTRL_U = "\x15"
BACKSPACE = ("\x7f", "\x08")


class PipelineApp:
    """Runs a :class:`Pipeline` interactively in the terminal."""

    def __init__(
        self,
        pipeline: Any,
        *,
        theme: Theme | str | None = None,
        color: bool | None = None,
        show_thinking: bool = True,
    ) -> None:
        self.pipeline = pipeline
        self.theme = theme_by_name(theme) if isinstance(theme, str) else (theme or DARK_THEME)
        self.show_thinking = show_thinking
        self.term = Terminal(color=color)

        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.transcript = Transcript()

        self.role_names: list[str] = list(pipeline.role_names)
        self.model = getattr(pipeline, "model_name", None) or "?"
        self.thinking = getattr(pipeline, "reasoning", None) or "?"

        self.phase = "input"  # input | running | between | done
        self.current_role: str | None = None
        self.role_index = 0
        self.previous: Any = None
        self.results: dict[str, Any] = {}
        self.error: str | None = None

        self.input_line = ""
        self.input_cursor = 0

        self.elapsed_start: float | None = None
        self._tick = 0
        self._role_done_event = threading.Event()
        self._role_done: tuple[str, Any, BaseException | None] | None = None
        self._worker: threading.Thread | None = None
        self._quit_requested = False
        self.done = False

        # Route streaming records from every role's Pi subprocess here.
        pipeline.observer = self._observer

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def run(self) -> dict[str, Any]:
        """Run the interactive loop and return the collected role results."""
        self.term.enter()
        try:
            while not self.done:
                self._drain_events()
                self._check_role_done()
                self._render()
                key = self.term.read_key(timeout=0.05)
                if key is not None:
                    self._handle_key(key)
                self._tick += 1
        finally:
            self.term.exit()
        return self.results

    # ------------------------------------------------------------------ #
    # Streaming observer (runs on each Pi reader thread)
    # ------------------------------------------------------------------ #
    def _observer(self, record: dict[str, Any]) -> None:
        self.events.put(record)

    def _drain_events(self) -> None:
        while True:
            try:
                record = self.events.get_nowait()
            except queue.Empty:
                return
            self._translate(record)

    def _translate(self, record: dict[str, Any]) -> None:
        rtype = record.get("type")

        if rtype == "message_update":
            update = record.get("assistantMessageEvent") or {}
            kind = update.get("type")
            if kind == "text_delta":
                self.transcript.text(update.get("delta", ""))
            elif kind == "thinking_delta" and self.show_thinking:
                self.transcript.thinking(update.get("delta", ""))
            return

        if rtype == "tool_execution_start":
            name = record.get("toolName") or "tool"
            brief = self._brief_args(name, record.get("args") or {})
            self.transcript.tool(f"\u2699 {name}{brief} \u2026")
            return

        if rtype == "tool_execution_end":
            suffix = " \u2717" if record.get("isError") else " \u2713"
            duration = record.get("durationMs")
            if duration is not None:
                suffix += f" ({duration} ms)"
            self.transcript.extend_last(suffix)
            return

        if rtype == "agent_settled":
            self.transcript.settle()
            return

        if rtype == "auto_retry_start":
            attempt = record.get("attempt")
            maximum = record.get("maxAttempts")
            self.transcript.info(
                f"[retry {attempt}/{maximum} in {record.get('delayMs', 0)} ms]"
            )
            return

    @staticmethod
    def _brief_args(name: str, args: dict[str, Any]) -> str:
        if name in ("bash", "powershell"):
            command = args.get("command", "")
            if isinstance(command, str):
                command = command.strip().replace("\n", " \u23ce ")
                if len(command) > 60:
                    command = command[:57] + "\u2026"
                return f"  {command}"
        if name in ("read", "write", "edit"):
            path = args.get("path") or args.get("file") or ""
            if isinstance(path, str):
                return f"  {path}"
        if name in ("grep", "find", "ls"):
            value = args.get("pattern") or args.get("path") or args.get("directory") or ""
            if isinstance(value, str):
                return f"  {value}"
        return ""

    # ------------------------------------------------------------------ #
    # Role execution
    # ------------------------------------------------------------------ #
    def _start_role(self, index: int, context: str | None = None) -> None:
        self.role_index = index
        self.current_role = self.role_names[index]
        self.phase = "running"
        self.elapsed_start = time.monotonic()
        self.transcript.role(f"\u25b6 {self.current_role}")
        self._role_done = None
        self._role_done_event = threading.Event()
        self._worker = threading.Thread(
            target=self._run_role_worker,
            args=(index, context),
            name=f"role-{self.current_role}",
            daemon=True,
        )
        self._worker.start()

    def _run_role_worker(self, index: int, context: str | None) -> None:
        name = self.role_names[index]
        try:
            result = self.pipeline.run_step(name, self.previous, context=context)
            self._role_done = (name, result, None)
        except BaseException as exc:  # pragma: no cover - depends on the role
            self._role_done = (name, None, exc)
        finally:
            self._role_done_event.set()

    def _check_role_done(self) -> None:
        if self.phase != "running" or self._role_done is None:
            return
        name, result, error = self._role_done
        self.transcript.settle()

        if error is not None:
            self.error = str(error)
            self.transcript.error(f"\u2717 {name} failed: {self.error}")
            self.phase = "done"
        else:
            self.results[name] = result
            self.transcript.result(self._summarize_result(name, result))
            if self._quit_requested or self.role_index >= len(self.role_names) - 1:
                self.phase = "done"
            else:
                self.phase = "between"
        self.previous = result

    def _summarize_result(self, name: str, result: Any) -> str:
        try:
            text = json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(result)
        if len(text) > 160:
            text = text[:157] + "\u2026"
        return f"\u2713 {name} \u2192 {text}"

    # ------------------------------------------------------------------ #
    # Input handling
    # ------------------------------------------------------------------ #
    def _handle_key(self, key: str) -> None:
        if self.phase in ("input", "between"):
            self._edit_input(key)
        elif self.phase == "running":
            if key == CTRL_C:
                self._quit_requested = True
                self.transcript.info("[will quit after this role finishes]")
        elif self.phase == "done":
            if key in ENTER_KEYS or key in ("q", "Q", CTRL_C, CTRL_D):
                self.done = True

    def _edit_input(self, key: str) -> None:
        if key in ENTER_KEYS:
            self._submit()
        elif key == CTRL_C or key == CTRL_U:
            self.input_line = ""
            self.input_cursor = 0
        elif key == CTRL_D:
            if not self.input_line:
                self.done = True
            elif self.input_cursor < len(self.input_line):
                self._delete_forward()
        elif key in BACKSPACE:
            self._delete_backward()
        elif key == "LEFT":
            self.input_cursor = max(0, self.input_cursor - 1)
        elif key == "RIGHT":
            self.input_cursor = min(len(self.input_line), self.input_cursor + 1)
        elif key == "HOME":
            self.input_cursor = 0
        elif key == "END":
            self.input_cursor = len(self.input_line)
        elif key == "DELETE":
            self._delete_forward()
        elif key in ("UP", "DOWN", "ESC"):
            return
        elif len(key) == 1 and key.isprintable():
            self.input_line = (
                self.input_line[: self.input_cursor]
                + key
                + self.input_line[self.input_cursor :]
            )
            self.input_cursor += 1

    def _delete_backward(self) -> None:
        if self.input_cursor > 0:
            self.input_line = (
                self.input_line[: self.input_cursor - 1]
                + self.input_line[self.input_cursor :]
            )
            self.input_cursor -= 1

    def _delete_forward(self) -> None:
        if self.input_cursor < len(self.input_line):
            self.input_line = (
                self.input_line[: self.input_cursor]
                + self.input_line[self.input_cursor + 1 :]
            )

    def _submit(self) -> None:
        text = self.input_line.strip()
        self.input_line = ""
        self.input_cursor = 0
        if text.lower() in ("/quit", "/q", "quit", "exit"):
            self.done = True
            return

        if self.phase == "input":
            if not text:
                return
            self.previous = text
            self._start_role(0)
        elif self.phase == "between":
            self._start_role(self.role_index + 1, context=text or None)

    # ------------------------------------------------------------------ #
    # Frame building
    # ------------------------------------------------------------------ #
    def _rule(self, left: str, right: str) -> str:
        width, _ = self.term.size()
        body = "\u2500" * max(0, width - 2)
        return self.theme.style(left + body + right, fg="borderMuted")

    def _row(self, content: str) -> str:
        width, _ = self.term.size()
        border = self.theme.style("\u2502", fg="borderMuted")
        return border + " " + self._pad(content, width - 4) + " " + border

    @staticmethod
    def _pad(content: str, width: int) -> str:
        return pad(content, width)

    def _header_content(self) -> str:
        title = self.theme.style("hms pipeline", fg="accent", bold=True)
        meta = f" \u00b7 {self.model} \u00b7 {self.thinking}"
        if self.current_role:
            meta += f" \u00b7 role {self.role_index + 1}/{len(self.role_names)}"
        return title + self.theme.style(meta, fg="muted")

    def _body_chunks(self, width: int, height: int) -> list[tuple[str, str]]:
        display: list[tuple[str, str]] = []
        for kind, text in reversed(self.transcript.lines):
            for chunk in reversed(wrap_text(text, width)):
                display.append((kind, chunk))
                if len(display) >= height:
                    break
            if len(display) >= height:
                break

        if len(display) < height:
            kind, pending = self.transcript.partial()
            if pending:
                for chunk in reversed(wrap_text(pending, width)):
                    display.append((kind, chunk))
                    if len(display) >= height:
                        break
        display.reverse()
        return display

    def _style_line(self, kind: str, text: str) -> str:
        if kind == KIND_ROLE:
            return self.theme.style(text, fg="accent", bold=True)
        if kind == KIND_THINKING:
            return self.theme.style(text, fg="thinkingText", italic=True)
        if kind == KIND_TOOL:
            return self.theme.style(text, fg="muted")
        if kind == KIND_INFO or kind == KIND_RESULT:
            return self.theme.style(text, fg="dim")
        if kind == KIND_ERROR:
            return self.theme.style(text, fg="error")
        return self.theme.style(text, fg="text")

    def _phase_hint(self) -> str:
        if self.phase == "running":
            return ""
        if self.phase == "done":
            return "press q or Enter to quit"
        return ""

    def _status_content(self) -> str:
        if self.phase == "input":
            return self.theme.style(
                "\u25cf  enter the bug report, then press Enter", fg="accent"
            )
        if self.phase == "running":
            spinner = _SPINNER[self._tick % len(_SPINNER)]
            elapsed = time.monotonic() - (self.elapsed_start or time.monotonic())
            return self.theme.style(
                f"{spinner}  running {self.current_role} \u00b7 {elapsed:.1f}s",
                fg="warning",
            )
        if self.phase == "between":
            return self.theme.style(
                "\u25cf  Enter to continue, type to add context, or /quit",
                fg="success",
            )
        if self.error is not None:
            return self.theme.style(f"\u2717  {self.error}", fg="error")
        return self.theme.style("\u2713  pipeline complete", fg="success")

    def _prompt_content(self) -> str:
        prefix = self.theme.style("\u203a ", fg="accent", bold=True)
        text = self.theme.style(self.input_line, fg="text")
        return prefix + text

    def _build_frame(self) -> tuple[list[str], tuple[int, int] | None]:
        width, height = self.term.size()
        body_height = max(1, height - 7)

        lines: list[str] = []
        lines.append(self._rule("\u256d", "\u256e"))
        lines.append(self._row(self._header_content()))
        lines.append(self._rule("\u251c", "\u2524"))

        for kind, chunk in self._body_chunks(width - 4, body_height):
            lines.append(self._row(self._style_line(kind, chunk)))
        while len(lines) < 3 + body_height:
            lines.append(self._row(""))

        lines.append(self._rule("\u251c", "\u2524"))
        cursor: tuple[int, int] | None = None
        if self.phase in ("input", "between"):
            lines.append(self._row(self._prompt_content()))
            cursor = (len(lines) - 1, 4 + self.input_cursor)
        else:
            lines.append(self._row(self.theme.style(self._phase_hint(), fg="dim")))
        lines.append(self._row(self._status_content()))
        lines.append(self._rule("\u2570", "\u256f"))
        return lines, cursor

    def _render(self) -> None:
        lines, cursor = self._build_frame()
        self.term.paint(lines, cursor)
