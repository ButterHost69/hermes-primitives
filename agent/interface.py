"""Interactive terminal interface for Pi.

A small REPL that streams Pi's output, exposes controls for the session
(model, thinking, tools), and manages a library of custom prompts that can
be inserted into the input line.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from typing import Any

from .client import PiClient, PiError
from .prompts import PromptLibrary
from .tools import BUILTIN_TOOLS, TOOL_BY_NAME, default_tools

try:
    import readline  # noqa: F401  (line editing + prefill support)
    _HAVE_READLINE = True
except ImportError:  # pragma: no cover
    _HAVE_READLINE = False


class Interface:
    """Owns the Pi RPC client and drives the interactive loop."""

    def __init__(
        self,
        client: PiClient,
        prompts: PromptLibrary,
        *,
        color: bool | None = None,
    ) -> None:
        self.client = client
        self.prompts = prompts
        self._use_color = sys.stdout.isatty() if color is None else bool(color)

        self._print_lock = threading.Lock()
        self._settled = threading.Event()
        self._midline = False
        self._pending_prefill: str | None = None

        self.client.on_event(self._on_event)

    # ------------------------------------------------------------------ #
    # Output helpers (thread-safe against the streaming reader thread)
    # ------------------------------------------------------------------ #
    def _paint(self, code: str, text: str) -> str:
        if not self._use_color:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def _dim(self, t: str) -> str:
        return self._paint("2", t)

    def _bold(self, t: str) -> str:
        return self._paint("1", t)

    def _cyan(self, t: str) -> str:
        return self._paint("36", t)

    def _green(self, t: str) -> str:
        return self._paint("32", t)

    def _yellow(self, t: str) -> str:
        return self._paint("33", t)

    def _red(self, t: str) -> str:
        return self._paint("31", t)

    def _out(self, text: str = "", end: str = "\n") -> None:
        with self._print_lock:
            sys.stdout.write(text + end)
            sys.stdout.flush()
            self._midline = end == ""

    def _dim_out(self, text: str = "", end: str = "\n") -> None:
        self._out(self._dim(text), end=end)

    def _status_line(self, text: str) -> None:
        """Print a dim status marker on its own line, without disturbing
        in-flight text on the current line."""
        with self._print_lock:
            if self._midline:
                sys.stdout.write("\n")
            sys.stdout.write(self._dim(text) + "\n")
            sys.stdout.flush()
            self._midline = False

    def _err(self, text: str) -> None:
        self._out(self._red("error: ") + text)

    def _ensure_newline(self) -> None:
        with self._print_lock:
            if self._midline:
                sys.stdout.write("\n")
                sys.stdout.flush()
                self._midline = False

    def _clear_screen(self) -> None:
        if self._use_color:
            self._out("\x1b[2J\x1b[H", end="")
        else:
            self._out("\n" * 40, end="")

    # ------------------------------------------------------------------ #
    # Streaming event handler (runs on the client's reader thread)
    # ------------------------------------------------------------------ #
    def _on_event(self, record: dict[str, Any]) -> None:
        rtype = record.get("type")

        if rtype == "message_update":
            update = record.get("assistantMessageEvent") or {}
            kind = update.get("type")
            if kind == "text_delta":
                self._out(update.get("delta", ""), end="")
            # thinking deltas are intentionally suppressed to keep output clean
            return

        if rtype == "message_end":
            message = record.get("message") or {}
            if message.get("role") == "assistant":
                self._ensure_newline()
            return

        if rtype == "tool_execution_start":
            name = record.get("toolName") or "tool"
            args = record.get("args") or {}
            brief = self._brief_args(name, args)
            self._dim_out(f"  \u2699 {name}{brief} \u2026", end="")
            return

        if rtype == "tool_execution_end":
            suffix = " \u2717" if record.get("isError") else " \u2713"
            duration = record.get("durationMs")
            if duration is not None:
                suffix += f" ({duration} ms)"
            self._dim_out(suffix)
            self._ensure_newline()
            return

        if rtype == "agent_start":
            self._status_line("  \u2026 thinking \u2026")
            return

        if rtype == "agent_settled":
            self._ensure_newline()
            if record.get("aborted"):
                self._dim_out("[aborted]")
            self._settled.set()
            return

        if rtype == "auto_retry_start":
            attempt = record.get("attempt")
            maximum = record.get("maxAttempts")
            self._status_line(f"[retry {attempt}/{maximum} in {record.get('delayMs', 0)} ms]")
            return

        if rtype == "compaction_start":
            self._status_line("[compacting\u2026]")
            return

        if rtype == "queue_update":
            steering = record.get("steering") or []
            follow_up = record.get("followUp") or []
            if steering or follow_up:
                self._status_line(f"[queue: {len(steering)} steering, {len(follow_up)} follow-up]")

    @staticmethod
    def _brief_args(name: str, args: dict[str, Any]) -> str:
        if name in ("bash", "powershell"):
            command = args.get("command", "")
            if isinstance(command, str):
                command = command.strip().replace("\n", " \u23ce ")
                if len(command) > 70:
                    command = command[:67] + "\u2026"
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
    # Input helpers
    # ------------------------------------------------------------------ #
    def _prompt_str(self) -> str:
        if self._use_color and _HAVE_READLINE and sys.stdin.isatty():
            return "\x01\x1b[36m\x02pi> \x01\x1b[0m\x02"
        return "pi> "

    def _read_line(self) -> str:
        prefill = self._pending_prefill
        self._pending_prefill = None
        if prefill is not None and _HAVE_READLINE:
            try:
                readline.set_startup_hook(lambda: readline.insert_text(prefill))
                return input(self._prompt_str())
            finally:
                readline.set_startup_hook(None)
        if prefill is not None:
            # Non-readline fallback: echo the prompt and return it verbatim.
            self._out(self._prompt_str() + prefill)
            return prefill
        return input(self._prompt_str())

    def _read_multiline(self, terminator: str = ".") -> str:
        self._out(self._dim(f"enter text; a line containing only '{terminator}' ends input"))
        lines: list[str] = []
        while True:
            try:
                line = input("  \u2502 ")
            except (EOFError, KeyboardInterrupt):
                self._out()
                break
            if line.strip() == terminator:
                break
            lines.append(line)
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    def run(self) -> None:
        self._banner()
        try:
            self.client.get_state()
        except PiError as exc:
            self._err(str(exc))
            return

        while True:
            try:
                line = self._read_line()
            except EOFError:
                self._out()
                break
            except KeyboardInterrupt:
                self._out()
                self._dim_out("^C  (use /abort during a run, /quit to exit)")
                continue

            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not self._handle_command(line):
                    break
                continue
            self._send_prompt(line)

        self.client.close()

    def _send_prompt(self, message: str) -> None:
        self._settled.clear()
        try:
            response = self.client.prompt(message)
        except PiError as exc:
            self._err(str(exc))
            return
        if not response.get("success", False):
            self._err(response.get("error") or "prompt rejected")
            return
        data = response.get("data") or {}
        if data.get("disposition") == "handled":
            return  # an extension consumed the prompt; nothing to await
        self._wait_for_settle()

    def _wait_for_settle(self) -> None:
        while not self._settled.is_set():
            if not self.client.running:
                self._err("Pi process exited unexpectedly")
                return
            try:
                self._settled.wait(0.25)
            except KeyboardInterrupt:
                self._dim_out("\n[aborting run\u2026]")
                try:
                    self.client.abort()
                except PiError as exc:
                    self._err(str(exc))
                    return

    # ------------------------------------------------------------------ #
    # Command dispatch
    # ------------------------------------------------------------------ #
    def _handle_command(self, line: str) -> bool:
        body = line[1:]
        parts = body.split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1] if len(parts) > 1 else ""

        if command in ("quit", "exit", "q"):
            return False
        if command in ("help", "h", "?"):
            self._print_help()
        elif command == "tools":
            self._cmd_tools(argument)
        elif command == "models":
            self._cmd_models()
        elif command == "model":
            self._cmd_model(argument)
        elif command == "thinking":
            self._cmd_thinking(argument)
        elif command == "state":
            self._cmd_state()
        elif command == "new":
            self._cmd_new()
        elif command == "abort":
            self._cmd_abort()
        elif command in ("prompt", "prompts"):
            self._cmd_prompts(argument)
        elif command == "clear":
            self._clear_screen()
        elif command == "cwd":
            self._out(f"cwd: {self.client.cwd or os.getcwd()}")
        else:
            self._err(f"unknown command '/{command}'. Try /help.")
        return True

    def _cmd_tools(self, argument: str) -> None:
        if not argument:
            current = self.client.tools or []
            self._out("Selected tools: " + (", ".join(current) if current else "(none)"))
            self._out("Available: " + ", ".join(t.name for t in BUILTIN_TOOLS))
            self._out("Use /tools menu to toggle, or /tools read,bash,\u2026 to set.")
            return

        if argument == "menu":
            self._tools_menu()
            return

        if argument in ("off", "none"):
            names: list[str] = []
        elif argument == "default":
            names = default_tools()
        else:
            names = []
            unknown: list[str] = []
            for raw in argument.split(","):
                name = raw.strip().lower()
                if name in TOOL_BY_NAME:
                    if name not in names:
                        names.append(name)
                elif name:
                    unknown.append(raw.strip())
            if unknown:
                self._out(self._yellow("ignored unknown: " + ", ".join(unknown)))
        self._apply_tools(names)

    def _tools_menu(self) -> None:
        selected = set(self.client.tools or [])
        while True:
            self._out(self._bold("\nSelect tools \u2014 enter numbers to toggle, Enter to confirm:"))
            for index, tool in enumerate(BUILTIN_TOOLS, 1):
                mark = "\u2713" if tool.name in selected else " "
                line = f"  [{mark}] {index:>2}. {tool.name:<12} {tool.description}"
                self._out(self._green(line) if tool.name in selected else line)
            try:
                raw = input(self._prompt_str().replace("pi> ", "toggle> "))
            except (EOFError, KeyboardInterrupt):
                self._out()
                return
            raw = raw.strip()
            if raw == "":
                break
            if raw in ("q", "quit"):
                return
            for part in raw.replace(",", " ").split():
                if not part.lstrip("-").isdigit():
                    continue
                number = int(part)
                if 1 <= number <= len(BUILTIN_TOOLS):
                    name = BUILTIN_TOOLS[number - 1].name
                    if name in selected:
                        selected.discard(name)
                    else:
                        selected.add(name)
        names = [t.name for t in BUILTIN_TOOLS if t.name in selected]
        self._apply_tools(names)

    def _apply_tools(self, names: list[str]) -> None:
        before = list(self.client.tools or [])
        self.client.tools = names
        self._dim_out(f"restarting Pi with tools: {', '.join(names) if names else '(none)'} \u2026")
        self.client.restart()
        self._settled.clear()
        self._out(self._green("ready."))
        if names != before:
            self._out(self._yellow("note: changing tools restarts Pi, so the conversation is reset."))

    def _cmd_models(self) -> None:
        current_id = ""
        try:
            current_id = (self.client.get_state().get("model") or {}).get("id", "")
        except PiError:
            pass
        for model in self.client.get_models():
            mark = "\u2192" if model.get("id") == current_id else " "
            self._out(f"  {mark} {model.get('id',''):<28} {model.get('name','')}")

    def _cmd_model(self, argument: str) -> None:
        if not argument:
            model = self.client.get_state().get("model") or {}
            self._out(
                f"Model: {self._bold(model.get('name') or model.get('id') or '?')}"
                f"  ({model.get('id', '')})"
            )
            self._out("Use /model <pattern> to switch, or /models to list.")
            return
        model = self._match_model(self.client.get_models(), argument)
        if model is None:
            self._err(f"no model matches '{argument}'. Try /models.")
            return
        self.client.set_model(model)
        self.client.model = model.get("id")  # remembered for tool-restart re-application
        self._out(self._green(f"model \u2192 {model.get('name')} ({model.get('id')})"))

    @staticmethod
    def _match_model(models: list[dict[str, Any]], pattern: str) -> dict[str, Any] | None:
        needle = pattern.strip().lower()
        if not needle:
            return None

        def identifier(m: dict[str, Any]) -> str:
            return (m.get("id") or "").lower()

        for model in models:
            if identifier(model) == needle:
                return model
        for model in models:
            if identifier(model).startswith(needle):
                return model
        for model in models:
            if needle in identifier(model):
                return model
        for model in models:
            if needle in (model.get("name") or "").lower():
                return model
        return None

    def _cmd_thinking(self, argument: str) -> None:
        levels = self.client.get_thinking_levels()
        if not argument:
            state = self.client.get_state()
            self._out(
                f"Thinking: {self._bold(str(state.get('thinkingLevel', '?')))}"
                f"  (available: {', '.join(levels)})"
            )
            return
        level = argument.strip().lower()
        if level not in levels:
            self._err(f"invalid level '{argument}'. Available: {', '.join(levels)}")
            return
        self.client.set_thinking(level)
        self.client.thinking = level
        self._out(self._green(f"thinking \u2192 {level}"))

    def _cmd_state(self) -> None:
        state = self.client.get_state()
        model = state.get("model") or {}
        tools = self.client.tools or []
        self._out(f"  model      : {model.get('name') or model.get('id') or '?'}")
        self._out(f"  thinking   : {state.get('thinkingLevel', '?')}")
        self._out(f"  streaming  : {state.get('isStreaming', False)}")
        self._out(f"  tools      : {', '.join(tools) if tools else '(none)'}")
        self._out(f"  messages   : {state.get('messageCount', 0)}")
        self._out(f"  session    : {state.get('sessionId', '(in-memory)')}")
        if state.get("sessionName"):
            self._out(f"  name       : {state['sessionName']}")

    def _cmd_new(self) -> None:
        self.client.new_session()
        self._out(self._green("new session started"))

    def _cmd_abort(self) -> None:
        self.client.abort()
        self._out(self._dim("aborted"))

    def _cmd_prompts(self, argument: str) -> None:
        parts = argument.split()
        sub = parts[0] if parts else ""
        name = parts[1] if len(parts) > 1 else ""

        if sub in ("", "list"):
            names = self.prompts.list()
            if not names:
                self._out("No saved prompts. Use /prompt add <name> to create one.")
            else:
                self._out(self._bold("Saved prompts:"))
                for n in names:
                    text = self.prompts.get(n) or ""
                    first = text.splitlines()[0] if text.strip() else "(empty)"
                    self._out(f"  {self._cyan(n):<22} {first}")
            return

        if sub == "add":
            if not name:
                self._err("usage: /prompt add <name>")
                return
            text = self._read_multiline()
            if not text.strip():
                self._err("empty prompt, nothing saved")
                return
            path = self.prompts.save(name, text)
            self._out(self._green(f"saved prompt '{name}' \u2192 {path}"))
            return

        if sub == "del":
            if self.prompts.delete(name):
                self._out(self._green(f"deleted '{name}'"))
            else:
                self._err(f"no prompt named '{name}'")
            return

        if sub == "show":
            resolved = self.prompts.resolve(name)
            if resolved is None:
                self._err(f"no prompt named '{name}'")
                return
            self._out(self.prompts.get(resolved[0]) or "")
            return

        # Default action: insert the prompt into the input line for editing.
        resolved = self.prompts.resolve(sub)
        if resolved is None:
            self._err(f"no prompt named '{sub}'. Try /prompts.")
            return
        resolved_name, text = resolved
        text = text.strip()
        if "\n" not in text and _HAVE_READLINE:
            self._pending_prefill = text
            self._out(self._dim(f"inserted '{resolved_name}' \u2014 edit and press Enter to send"))
        else:
            self._out(self._dim(f"sending '{resolved_name}' (multi-line):"))
            self._out(text)
            self._send_prompt(text)

    # ------------------------------------------------------------------ #
    # Banner and help
    # ------------------------------------------------------------------ #
    def _banner(self) -> None:
        width = shutil.get_terminal_size((80, 24)).columns
        rule = "\u2500" * max(0, width - 1)
        self._out(self._cyan(rule))
        self._out(self._bold("Pi interface") + self._dim("  \u2014  type a prompt, or /help for controls"))
        self._out(self._cyan(rule))

    def _print_help(self) -> None:
        self._out(
            self._bold("Controls") + "\n"
            "  /tools [menu|<csv>|default|off]   select tools (menu = toggle, <csv> = set)\n"
            "  /models                           list available models\n"
            "  /model <pattern>                  switch model (id/name substring)\n"
            "  /thinking <level>                 set reasoning level\n"
            "  /state                            show session state\n"
            "  /new                              start a fresh session\n"
            "  /abort                            stop the running operation\n"
            "  /prompts                          list saved custom prompts\n"
            "  /prompt add <name>                save a new custom prompt (multi-line)\n"
            "  /prompt <name>                    insert a saved prompt into the input\n"
            "  /prompt show <name>               print a saved prompt\n"
            "  /prompt del <name>                delete a saved prompt\n"
            "  /cwd                              show the working directory\n"
            "  /clear                            clear the screen\n"
            "  /help                             this help\n"
            "  /quit                             exit\n"
            "\n"
            + self._dim(
                "Tip: end a line with '\\' to continue typing on the next line. "
                "Ctrl+C aborts a running prompt."
            )
        )
