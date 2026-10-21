"""Interactive terminal UI for the pipeline.

:class:`PipelineApp` drives a :class:`~pipeline.Pipeline` one role at a time
and paints a live, Pi-styled screen while it runs: streamed assistant text,
tool activity, role banners, and a status bar all update in place.  The only
interaction point is where a human enters text — the initial bug report.

When the ``primitive_manager`` role fans its registry out, the frame grows a
panel listing every primitive the fan-out spans — file name and state included
— and the operator can step into any one of them to watch that agent's own
stream, which is what the panel and the per-primitive streams are there to
undo: a fan-out streams all of its primitives at once, interleaved.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any

from .primitives import (
    PRIMITIVE_KEY,
    FILES_KEY,
    REPORT_KEY,
    ERROR_KEY,
    FANNED_OUT_TYPE,
    REPORTED_TYPE,
    FAILED_TYPE,
    PrimitiveFeed,
    abbreviate,
)
from .screen import Terminal, pad, wrap_text
from .theme import DARK_THEME, Theme, theme_by_name
from .transcript import (
    KIND_ERROR,
    KIND_INFO,
    KIND_RESULT,
    KIND_ROLE,
    KIND_SPACER,
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
TAB = "\t"
# Keys that take the operator out of the primitive they are looking at.
LEAVE_PRIMITIVE_KEYS = ("ESC",) + BACKSPACE + ("q", "Q")
# The primitive panel is only worth its columns in a terminal this wide.
PANEL_MIN_WIDTH = 22
PANEL_MAX_WIDTH = 32
PANEL_TERM_MIN_WIDTH = 64


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
        # The primitives a fan-out spans, each with its own live stream.
        self.feed = PrimitiveFeed()
        self.selected: str | None = None
        self.focus: str | None = None

        self.role_names: list[str] = list(pipeline.role_names)
        self.model = getattr(pipeline, "model_name", None) or "?"
        self.thinking = getattr(pipeline, "reasoning", None) or "?"

        self.phase = "input"  # input | running | done
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

        # Scrolling state: ``_scroll_offset`` is how many lines the view is
        # scrolled up from the bottom (0 = pinned to the latest output).
        self._scroll_offset = 0
        self._auto_scroll = True
        self._total_lines = 0

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
        """Route one streamed record to the stream it belongs to.

        A record the pipeline tagged with a primitive's source file belongs to
        that primitive's own stream; the records the pipeline emits about the
        primitives themselves update the panel.  Everything else is the role's
        own activity and lands in the main transcript.
        """
        rtype = record.get("type")

        if rtype == FANNED_OUT_TYPE:
            self._fanned_out(record)
            return

        if rtype == REPORTED_TYPE:
            self.feed.note_report(
                record.get(PRIMITIVE_KEY, ""), record.get(REPORT_KEY, "")
            )
            return

        if rtype == FAILED_TYPE:
            self.feed.note_failure(
                record.get(PRIMITIVE_KEY, ""), record.get(ERROR_KEY, "")
            )
            return

        source_file = record.get(PRIMITIVE_KEY)
        target = (
            self.feed.transcript_for(source_file) if source_file else self.transcript
        )
        self._apply(target, record)

    def _fanned_out(self, record: dict[str, Any]) -> None:
        """Register the primitives a fan-out just started."""
        files = [
            source_file
            for source_file in record.get(FILES_KEY, [])
            if isinstance(source_file, str) and source_file
        ]
        for source_file in files:
            self.feed.register(source_file)
        if self.selected not in self.feed.order:
            self.selected = files[0] if files else None
        self.transcript.info(
            f"\u27f3 {len(files)} primitive(s) fanned out: "
            + ", ".join(self.feed.order)
        )

    def _apply(self, target: Transcript, record: dict[str, Any]) -> None:
        """Translate one Pi record into ``target``'s transcript."""
        rtype = record.get("type")

        if rtype == "message_update":
            update = record.get("assistantMessageEvent") or {}
            kind = update.get("type")
            if kind == "text_delta":
                target.text(update.get("delta", ""))
            elif kind == "thinking_delta" and self.show_thinking:
                target.thinking(update.get("delta", ""))
            return

        if rtype == "tool_execution_start":
            name = record.get("toolName") or "tool"
            brief = self._brief_args(name, record.get("args") or {})
            target.tool(f"\u2699 {name}{brief} \u2026")
            return

        if rtype == "tool_execution_end":
            suffix = " \u2717" if record.get("isError") else " \u2713"
            duration = record.get("durationMs")
            if duration is not None:
                suffix += f" ({duration} ms)"
            target.extend_last(suffix)
            return

        if rtype == "agent_settled":
            target.settle()
            return

        if rtype == "auto_retry_start":
            attempt = record.get("attempt")
            maximum = record.get("maxAttempts")
            target.info(f"[retry {attempt}/{maximum} in {record.get('delayMs', 0)} ms]")
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
        self._scroll_offset = 0
        self._auto_scroll = True
        self.focus = None
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
            self._mark_failures(error)
            self.transcript.error(f"\u2717 {name} failed: {self.error}")
            self.phase = "done"
        else:
            self.results[name] = result
            self.transcript.result(self._summarize_result(name, result))
            self.previous = result
            if self._quit_requested or self.role_index >= len(self.role_names) - 1:
                self.phase = "done"
            else:
                self._start_role(self.role_index + 1)
        self.feed.finish()

    def _mark_failures(self, error: BaseException) -> None:
        """Mark the primitives a failed fan-out names, then settle the rest.

        A primitive that failed streams its failure as it happens; the ones a
        fanned-out run left unreported are settled here, from the failures the
        role's error carries, so the panel never keeps a finished agent
        spinning.
        """
        failures = getattr(error, "failures", None)
        if isinstance(failures, dict):
            for source_file, failure in failures.items():
                self.feed.note_failure(str(source_file), str(failure))
        self.feed.finish()

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
        if key in ("UP", "DOWN", "PAGE_UP", "PAGE_DOWN"):
            self._scroll(key)
            return
        if self._panel_key(key):
            return
        if self.phase == "input":
            self._edit_input(key)
        elif self.phase == "running":
            if key == CTRL_C:
                self._quit_requested = True
                self.transcript.info("[will quit after this role finishes]")
            elif key in ("HOME", "END"):
                self._scroll(key)
        elif self.phase == "done":
            if key in ("q", "Q", CTRL_C, CTRL_D):
                self.done = True
            elif key in ENTER_KEYS and not self.feed.order:
                # Enter opens a primitive while the fan-out left any to read.
                self.done = True
            elif key in ("HOME", "END"):
                self._scroll(key)

    # ------------------------------------------------------------------ #
    # Primitive panel
    # ------------------------------------------------------------------ #
    def _panel_key(self, key: str) -> bool:
        """Handle a primitive-panel key; report whether it was consumed.

        The panel is only live once a fan-out has registered primitives, and
        Enter only opens one where there is nothing to type, so a bug report
        is never swallowed by the panel.
        """
        if not self.feed.order:
            return False
        if key == TAB:
            self._cycle_primitive()
            return True
        if self.focus is not None:
            if key in LEAVE_PRIMITIVE_KEYS:
                self.focus = None
                return True
        elif key in ENTER_KEYS and self.phase in ("running", "done"):
            self.focus = self.selected
            return True
        return False

    def _cycle_primitive(self) -> None:
        """Move the panel one primitive along, entering it when one is open."""
        current = self.focus or self.selected
        if current in self.feed.order:
            index = self.feed.order.index(current) + 1
        else:
            index = 0
        self.selected = self.feed.order[index % len(self.feed.order)]
        if self.focus is not None:
            self.focus = self.selected

    def _panel_target(self) -> str | None:
        """Return the primitive the panel highlights: the open one, if any."""
        return self.focus or self.selected

    def _panel_rows(self, width: int) -> list[str]:
        """Build the primitive panel: a header and one row per primitive."""
        rows = [
            self.theme.style(
                f"primitives ({len(self.feed.order)})", fg="accent", bold=True
            )
        ]
        rows.extend(
            self._panel_row(source_file, width) for source_file in self.feed.order
        )
        return rows

    def _panel_row(self, source_file: str, width: int) -> str:
        """Build one primitive's row: its marker, its name, and selection."""
        selected = source_file == self._panel_target()
        pointer = "\u25b8 " if selected else "  "
        text = self.theme.style(
            abbreviate(f"{pointer}{source_file}", width),
            fg=self.feed.status_color(source_file),
            bold=selected,
        )
        return self.theme.style(pad(text, width), bg="selectedBg") if selected else text

    def _scroll(self, key: str) -> None:
        """Adjust the transcript scroll offset.

        UP/DOWN move one line; PAGE_UP/PAGE_DOWN move a full body; HOME jumps
        to the top and END re-pins to the latest output.
        """
        body_height = max(1, self.term.size()[1] - 7)
        step = body_height if key in ("PAGE_UP", "PAGE_DOWN") else 1

        if key in ("UP", "PAGE_UP"):
            self._scroll_offset += step
            self._auto_scroll = False
        elif key in ("DOWN", "PAGE_DOWN"):
            self._scroll_offset = max(0, self._scroll_offset - step)
            if self._scroll_offset == 0:
                self._auto_scroll = True
        elif key == "HOME":
            self._scroll_offset = max(0, self._total_lines - body_height)
            self._auto_scroll = False
        elif key == "END":
            self._scroll_offset = 0
            self._auto_scroll = True

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
        elif key == "ESC":
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

    def _wrap_kind(self, kind: str, text: str, width: int) -> list[str]:
        """Wrap one transcript line to ``width``, indenting tool lines."""
        if kind == KIND_TOOL:
            return ["  " + chunk for chunk in wrap_text(text, max(1, width - 2))]
        return wrap_text(text, width)

    def _display(self, width: int) -> list[tuple[str, str, str]]:
        """Build the full wrapped frame body, oldest first.

        The main transcript comes first — the role banners and the streaming of
        every role that ran before — and the fanned-out primitives follow it:
        either the one stream the operator is looking at, or every primitive's
        stream grouped under its own header.  Each row carries the source file
        it came from as its label, which the caller paints as a file-name
        column beside the text.
        """
        rows: list[tuple[str, str, str]] = [
            (kind, "", chunk)
            for kind, text in self.transcript.lines
            for chunk in self._wrap_kind(kind, text, width)
        ]
        kind, pending = self.transcript.partial()
        if pending:
            rows.extend(
                (kind, "", chunk) for chunk in self._wrap_kind(kind, pending, width)
            )
        if self.feed:
            rows.append((KIND_SPACER, "", ""))
            rows.extend(self.feed.rows(self._text_width(width), self.focus))
        return rows

    def _label_width(self, width: int) -> int:
        """Return the columns the file-name column of a fan-out row takes."""
        longest = max((len(path) for path in self.feed.order), default=0)
        return min(longest, width // 3)

    def _text_width(self, width: int) -> int:
        """Return the columns left for a fan-out row beside its file name."""
        label = self._label_width(width)
        return width - label - 1 if label else width

    def _layout(self, width: int) -> tuple[int, int]:
        """Return the ``(panel, body)`` column split for ``width`` columns."""
        panel = self._panel_width(width)
        return panel, (width - 5 - panel if panel else width - 4)

    def _panel_width(self, width: int) -> int:
        """Return the columns the primitive panel takes, or 0 without one."""
        if not self.feed or width < PANEL_TERM_MIN_WIDTH:
            return 0
        return min(PANEL_MAX_WIDTH, max(PANEL_MIN_WIDTH, width // 3))

    def _visible(
        self, display: list[tuple[str, str, str]], height: int
    ) -> list[tuple[str, str, str]]:
        """Slice the scrolled window of ``height`` lines out of ``display``."""
        offset = self._scroll_offset
        if offset <= 0:
            return display[-height:]
        end = max(0, len(display) - offset)
        start = max(0, end - height)
        return display[start:end]

    def _style_line(self, kind: str, text: str) -> str:
        if kind == KIND_SPACER:
            return ""
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

    def _style_row(self, row: tuple[str, str, str], label_width: int) -> str:
        """Style one body row, painting its file-name column when it has one.

        A primitive that failed paints its file name — and its banner — in the
        error color, so the operator sees which agent of a fan-out is out
        before reading a word it wrote.
        """
        kind, label, text = row
        if not label or label_width <= 0:
            return self._style_line(kind, text)
        color = self.feed.status_color(label)
        name = self.theme.style(
            abbreviate(label, label_width), fg=color, bold=kind == KIND_ROLE
        )
        if kind == KIND_ROLE:
            # a banner belongs to the primitive as a whole: both columns
            # carry its state
            body = self.theme.style(text, fg=color, bold=True)
        else:
            body = self._style_line(kind, text)
        return name + " " + body

    def _phase_hint(self) -> str:
        if self.phase in ("running", "done"):
            if self.focus is not None:
                return "\u2191\u2193 scroll \u00b7 Esc or q back to every primitive"
            if self.feed:
                if self.phase == "done":
                    return "Tab switch \u00b7 Enter open a primitive \u00b7 q to quit"
                return "Tab switch \u00b7 Enter open a primitive"
            if self.phase == "done":
                return "press q or Enter to quit"
            if self._scroll_offset > 0:
                return "\u2191 scrolled \u2014 press End to follow"
        return ""

    def _status_content(self) -> str:
        if self.phase == "input":
            base = self.theme.style(
                "\u25cf  enter the bug report, then press Enter", fg="accent"
            )
        elif self.phase == "running":
            spinner = _SPINNER[self._tick % len(_SPINNER)]
            elapsed = time.monotonic() - (self.elapsed_start or time.monotonic())
            base = self.theme.style(
                f"{spinner}  running {self.current_role} \u00b7 {elapsed:.1f}s",
                fg="warning",
            )
        elif self.error is not None:
            base = self.theme.style(f"\u2717  {self.error}", fg="error")
        else:
            base = self.theme.style("\u2713  pipeline complete", fg="success")

        if self.feed and self.current_role == "primitive_manager":
            base += self.theme.style(f"  \u00b7 {self.feed.summary()}", fg="dim")
        if self._scroll_offset > 0:
            base += self.theme.style("  \u00b7 \u2191 scrolled", fg="dim")
        return base

    def _prompt_content(self) -> str:
        prefix = self.theme.style("\u203a ", fg="accent", bold=True)
        text = self.theme.style(self.input_line, fg="text")
        return prefix + text

    def _split_rule(self, joiner: str, panel_width: int) -> str:
        """Build a frame rule that closes the primitive panel's column."""
        width, _ = self.term.size()
        head = panel_width
        tail = max(0, width - panel_width - 3)
        rule = "\u251c" + "\u2500" * head + joiner + "\u2500" * tail + "\u2524"
        return self.theme.style(rule, fg="borderMuted")

    def _split_row(self, panel: str, body: str, panel_width: int, body_width: int) -> str:
        """Build one frame row split into the panel and the body column."""
        border = self.theme.style("\u2502", fg="borderMuted")
        return (
            border
            + " "
            + self._pad(panel, panel_width - 2)
            + " "
            + border
            + " "
            + self._pad(body, body_width)
            + " "
            + border
        )

    def _body_lines(
        self,
        visible: list[tuple[str, str, str]],
        panel: list[str],
        height: int,
        panel_width: int,
        body_width: int,
        label_width: int,
    ) -> list[str]:
        """Build the body rows of a frame that carries the primitive panel."""
        rows: list[str] = []
        for index in range(height):
            panel_cell = panel[index] if index < len(panel) else ""
            row = visible[index] if index < len(visible) else None
            body_cell = self._style_row(row, label_width) if row is not None else ""
            rows.append(
                self._split_row(panel_cell, body_cell, panel_width, body_width)
            )
        return rows

    def _build_frame(self) -> tuple[list[str], tuple[int, int] | None]:
        width, height = self.term.size()
        body_height = max(1, height - 7)
        panel_width, body_width = self._layout(width)

        display = self._display(body_width)
        self._total_lines = len(display)
        max_offset = max(0, self._total_lines - body_height)
        if self._scroll_offset > max_offset:
            self._scroll_offset = max_offset
        visible = self._visible(display, body_height)
        label_width = self._label_width(body_width)

        lines: list[str] = []
        lines.append(self._rule("\u256d", "\u256e"))
        lines.append(self._row(self._header_content()))

        if panel_width:
            lines.append(self._split_rule("\u252c", panel_width))
            panel = self._panel_rows(panel_width - 2)
            lines.extend(
                self._body_lines(
                    visible, panel, body_height, panel_width, body_width, label_width
                )
            )
            lines.append(self._split_rule("\u2534", panel_width))
        else:
            lines.append(self._rule("\u251c", "\u2524"))
            for row in visible:
                lines.append(self._row(self._style_row(row, label_width)))
            while len(lines) < 3 + body_height:
                lines.append(self._row(""))
            lines.append(self._rule("\u251c", "\u2524"))
        cursor: tuple[int, int] | None = None
        if self.phase == "input":
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
