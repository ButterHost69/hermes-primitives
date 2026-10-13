"""Tests for the interactive interface (using the FakeClient)."""

from __future__ import annotations

import io
import sys

import pytest

import agent.interface as interface_module
from agent.interface import Interface
from agent.prompts import PromptLibrary


@pytest.fixture
def iface(fake_client, tmp_path):
    return Interface(fake_client, PromptLibrary(tmp_path / "prompts"), color=False)


# --------------------------------------------------------------------------- #
# Paint helpers
# --------------------------------------------------------------------------- #
def test_paint_disabled_when_color_false(fake_client, tmp_path):
    interface = Interface(fake_client, PromptLibrary(tmp_path / "p"), color=False)
    assert interface._paint("31", "x") == "x"
    assert interface._dim("x") == "x"
    assert interface._red("x") == "x"


def test_paint_wraps_when_color_true(fake_client, tmp_path):
    interface = Interface(fake_client, PromptLibrary(tmp_path / "p"), color=True)
    assert interface._paint("31", "x") == "\x1b[31mx\x1b[0m"


# --------------------------------------------------------------------------- #
# Output primitives
# --------------------------------------------------------------------------- #
def test_out_and_midline_tracking(iface, capsys):
    iface._out("abc", end="")
    assert iface._midline is True
    iface._ensure_newline()
    assert iface._midline is False
    assert capsys.readouterr().out == "abc\n"


def test_status_line_breaks_midline(iface, capsys):
    iface._out("abc", end="")
    iface._status_line("[x]")
    assert capsys.readouterr().out == "abc\n[x]\n"


def test_clear_screen_no_color(iface, capsys):
    iface._clear_screen()
    assert capsys.readouterr().out == "\n" * 40


def test_clear_screen_color(fake_client, tmp_path, capsys):
    interface = Interface(fake_client, PromptLibrary(tmp_path / "p"), color=True)
    interface._clear_screen()
    assert capsys.readouterr().out == "\x1b[2J\x1b[H"


# --------------------------------------------------------------------------- #
# Event handler
# --------------------------------------------------------------------------- #
def test_on_event_text_delta(iface, capsys):
    iface._on_event({"type": "message_update",
                     "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}})
    assert capsys.readouterr().out == "hi"


def test_on_event_thinking_suppressed(iface, capsys):
    iface._on_event({"type": "message_update",
                     "assistantMessageEvent": {"type": "thinking_delta", "delta": "hmm"}})
    assert capsys.readouterr().out == ""


def test_on_event_message_end_newline(iface, capsys):
    iface._out("text", end="")
    iface._on_event({"type": "message_end", "message": {"role": "assistant"}})
    assert capsys.readouterr().out == "text\n"


def test_on_event_tool_execution(iface, capsys):
    iface._on_event({"type": "tool_execution_start", "toolName": "bash",
                     "args": {"command": "ls -la"}})
    iface._on_event({"type": "tool_execution_end", "toolName": "bash",
                     "isError": False, "durationMs": 12})
    out = capsys.readouterr().out
    assert "bash" in out and "ls -la" in out and "12 ms" in out


def test_on_event_agent_start_uses_status_line(iface, capsys):
    iface._on_event({"type": "agent_start"})
    assert "thinking" in capsys.readouterr().out


def test_on_event_agent_settled_sets_flag(iface, capsys):
    iface._on_event({"type": "agent_settled", "aborted": False})
    assert iface._settled.is_set()
    assert capsys.readouterr().out == ""


def test_on_event_agent_settled_aborted(iface, capsys):
    iface._on_event({"type": "agent_settled", "aborted": True})
    assert iface._settled.is_set()
    assert "aborted" in capsys.readouterr().out


def test_on_event_auto_retry_and_compaction(iface, capsys):
    iface._on_event({"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 10})
    iface._on_event({"type": "compaction_start", "reason": "threshold"})
    out = capsys.readouterr().out
    assert "retry 1/3" in out and "compacting" in out


def test_on_event_queue_update(iface, capsys):
    iface._on_event({"type": "queue_update", "steering": ["a"], "followUp": []})
    assert "1 steering" in capsys.readouterr().out
    iface._on_event({"type": "queue_update", "steering": [], "followUp": []})
    assert capsys.readouterr().out == ""  # nothing new printed


# --------------------------------------------------------------------------- #
# Brief args
# --------------------------------------------------------------------------- #
def test_brief_args_bash(iface):
    assert "ls" in iface._brief_args("bash", {"command": "ls"})


def test_brief_args_truncates_long_command(iface):
    brief = iface._brief_args("bash", {"command": "x" * 200})
    assert len(brief) <= 80


def test_brief_args_read(iface):
    assert "file.txt" in iface._brief_args("read", {"path": "file.txt"})


def test_brief_args_grep(iface):
    assert "pat" in iface._brief_args("grep", {"pattern": "pat"})


def test_brief_args_unknown(iface):
    assert iface._brief_args("whatever", {}) == ""


# --------------------------------------------------------------------------- #
# Prompt string
# --------------------------------------------------------------------------- #
def test_prompt_str_plain_when_no_color(iface):
    assert iface._prompt_str() == "pi> "


def test_prompt_str_ansi_when_ready(monkeypatch, fake_client, tmp_path):
    monkeypatch.setattr(interface_module, "_HAVE_READLINE", True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    interface = Interface(fake_client, PromptLibrary(tmp_path / "p"), color=True)
    assert "\x1b[36m" in interface._prompt_str()


# --------------------------------------------------------------------------- #
# Input helpers
# --------------------------------------------------------------------------- #
def test_read_line_prefill_uses_readline(monkeypatch, iface):
    fake_readline = FakeReadlineStub()
    monkeypatch.setattr(interface_module, "_HAVE_READLINE", True)
    monkeypatch.setattr(interface_module, "readline", fake_readline)
    monkeypatch.setattr("builtins.input", lambda prompt="": "edited")
    iface._pending_prefill = "do the thing"

    result = iface._read_line()
    assert result == "edited"
    assert len(fake_readline.hooks) == 2  # hook then reset(None)
    fake_readline.hooks[0]()
    assert fake_readline.inserted == ["do the thing"]
    assert fake_readline.hooks[1] is None


def test_read_line_prefill_fallback_without_readline(monkeypatch, iface, capsys):
    monkeypatch.setattr(interface_module, "_HAVE_READLINE", False)
    iface._pending_prefill = "do the thing"
    assert iface._read_line() == "do the thing"
    assert "do the thing" in capsys.readouterr().out


def test_read_multiline(monkeypatch, iface, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("one\ntwo\n.\nthree\n"))
    assert iface._read_multiline() == "one\ntwo"


class FakeReadlineStub:
    def __init__(self):
        self.hooks = []
        self.inserted = []

    def set_startup_hook(self, fn):
        self.hooks.append(fn)

    def insert_text(self, text):
        self.inserted.append(text)


# --------------------------------------------------------------------------- #
# Run loop
# --------------------------------------------------------------------------- #
def test_run_happy_path(monkeypatch, fake_client, tmp_path, capsys):
    interface = Interface(fake_client, PromptLibrary(tmp_path / "p"), color=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello there\n/state\n/quit\n"))
    interface.run()
    out = capsys.readouterr().out
    assert "Pi interface" in out
    assert "hello" in out              # streamed delta
    assert "Model A" in out            # /state output
    assert fake_client.prompt_calls == ["hello there"]
    assert fake_client.closed


def test_run_eof_closes(monkeypatch, fake_client, tmp_path):
    interface = Interface(fake_client, PromptLibrary(tmp_path / "p"), color=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    interface.run()
    assert fake_client.closed


def test_run_get_state_failure(monkeypatch, fake_client, tmp_path, capsys):
    class BrokenClient(fake_client.__class__):
        def get_state(self):
            raise interface_module.PiError("boom")

    client = BrokenClient()
    interface = Interface(client, PromptLibrary(tmp_path / "p"), color=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("hi\n/quit\n"))
    interface.run()
    assert "boom" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Send prompt / wait for settle
# --------------------------------------------------------------------------- #
def test_send_prompt_handled_disposition(iface, fake_client, capsys):
    fake_client.disposition = "handled"
    iface._send_prompt("go")
    assert fake_client.prompt_calls == ["go"]
    assert capsys.readouterr().out == ""


def test_send_prompt_rejected(iface, fake_client, capsys):
    fake_client.fail_prompt = True
    iface._send_prompt("go")
    assert "rejected" in capsys.readouterr().out


def test_send_prompt_streams_and_settles(iface, fake_client, capsys):
    iface._send_prompt("go")
    assert "hello" in capsys.readouterr().out
    assert iface._settled.is_set()


def test_wait_for_settle_already_set(iface, fake_client):
    iface._settled.set()
    iface._wait_for_settle()  # returns immediately


def test_wait_for_settle_not_running(iface, fake_client, capsys):
    fake_client.running = False
    iface._wait_for_settle()
    assert "exited unexpectedly" in capsys.readouterr().out


def test_wait_for_settle_keyboard_interrupt(iface, fake_client, capsys):
    class OneShotInterruptEvent:
        """First wait() simulates Ctrl+C; later ones simulate a settle."""

        def __init__(self):
            self.calls = 0

        def is_set(self):
            return self.calls > 1

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt
            return True

    iface._settled = OneShotInterruptEvent()
    iface._wait_for_settle()
    assert fake_client.abort_calls == 1
    assert "aborting" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Command dispatch
# --------------------------------------------------------------------------- #
def test_handle_quit_returns_false(iface):
    assert iface._handle_command("/quit") is False
    assert iface._handle_command("/exit") is False


def test_handle_unknown_command(iface, capsys):
    assert iface._handle_command("/bogus") is True
    assert "unknown command" in capsys.readouterr().out


def test_handle_help(iface, capsys):
    iface._handle_command("/help")
    assert "Controls" in capsys.readouterr().out


def test_handle_cwd(iface, fake_client, capsys):
    iface._handle_command("/cwd")
    assert fake_client.cwd in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
def test_cmd_tools_no_arg_lists(iface, capsys):
    iface._cmd_tools("")
    out = capsys.readouterr().out
    assert "read, bash, edit, write" in out
    assert "Available:" in out


def test_cmd_tools_off(iface, fake_client):
    iface._cmd_tools("off")
    assert fake_client.tools == []
    assert fake_client.restarted


def test_cmd_tools_none(iface, fake_client):
    iface._cmd_tools("none")
    assert fake_client.tools == []


def test_cmd_tools_default_no_note(iface, fake_client, capsys):
    iface._cmd_tools("default")
    assert fake_client.tools == ["read", "bash", "edit", "write"]
    assert "conversation is reset" not in capsys.readouterr().out


def test_cmd_tools_csv_ignores_unknown(iface, fake_client, capsys):
    iface._cmd_tools("read,bash,notatool")
    assert fake_client.tools == ["read", "bash"]
    assert "ignored unknown: notatool" in capsys.readouterr().out


def test_tools_menu_toggle_and_confirm(monkeypatch, iface, fake_client):
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n\n"))
    iface._cmd_tools("menu")
    assert fake_client.tools == ["bash", "edit", "write"]


def test_tools_menu_quit(monkeypatch, iface, fake_client):
    monkeypatch.setattr(sys, "stdin", io.StringIO("q\n"))
    iface._cmd_tools("menu")
    assert fake_client.restarted is False


def test_tools_menu_eof(monkeypatch, iface, fake_client):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    iface._cmd_tools("menu")
    assert fake_client.restarted is False


def test_apply_tools_changed_notes_reset(iface, fake_client, capsys):
    iface._apply_tools(["read"])
    assert fake_client.tools == ["read"]
    assert fake_client.restarted
    assert "conversation is reset" in capsys.readouterr().out


def test_apply_tools_unchanged_no_note(iface, fake_client, capsys):
    iface._apply_tools(["read", "bash", "edit", "write"])
    assert "conversation is reset" not in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Models and thinking
# --------------------------------------------------------------------------- #
def test_match_model_exact(iface):
    models = [{"id": "m1", "name": "One"}, {"id": "m2", "name": "Two"}]
    assert iface._match_model(models, "m1") == models[0]


def test_match_model_prefix(iface):
    models = [{"id": "claude-sonnet", "name": "Sonnet"}]
    assert iface._match_model(models, "claude") == models[0]


def test_match_model_contains(iface):
    models = [{"id": "my-claude-x", "name": "X"}]
    assert iface._match_model(models, "claude") == models[0]


def test_match_model_name(iface):
    models = [{"id": "abc", "name": "Claude Sonnet"}]
    assert iface._match_model(models, "sonnet") == models[0]


def test_match_model_no_match(iface):
    assert iface._match_model([{"id": "x", "name": "Y"}], "zzz") is None


def test_match_model_empty_pattern(iface):
    assert iface._match_model([{"id": "x", "name": "Y"}], "  ") is None


def test_cmd_models(iface, capsys):
    iface._cmd_models()
    out = capsys.readouterr().out
    assert "model-a" in out and "model-b" in out
    assert "\u2192" in out  # current model marker


def test_cmd_model_no_arg(iface, capsys):
    iface._cmd_model("")
    assert "Model A" in capsys.readouterr().out


def test_cmd_model_switch(iface, fake_client, capsys):
    iface._cmd_model("model-b")
    assert fake_client.set_model_calls == [
        {"id": "model-b", "name": "Model B", "provider": "fake"}
    ]
    assert fake_client.model == "model-b"
    assert "model-b" in capsys.readouterr().out


def test_cmd_model_no_match(iface, capsys):
    iface._cmd_model("does-not-exist")
    assert "no model matches" in capsys.readouterr().out


def test_cmd_thinking_no_arg(iface, capsys):
    iface._cmd_thinking("")
    assert "high" in capsys.readouterr().out


def test_cmd_thinking_valid(iface, fake_client, capsys):
    iface._cmd_thinking("low")
    assert fake_client.set_thinking_calls == ["low"]
    assert fake_client.thinking == "low"


def test_cmd_thinking_invalid(iface, capsys):
    iface._cmd_thinking("super-high")
    assert "invalid level" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# State / new / abort
# --------------------------------------------------------------------------- #
def test_cmd_state(iface, capsys):
    iface._cmd_state()
    out = capsys.readouterr().out
    assert "Model A" in out
    assert "high" in out
    assert "read, bash, edit, write" in out
    assert "session-1" in out
    assert "demo" in out


def test_cmd_new(iface, fake_client):
    iface._cmd_new()
    assert fake_client.new_session_calls == 1


def test_cmd_abort(iface, fake_client, capsys):
    iface._cmd_abort()
    assert fake_client.abort_calls == 1
    assert "aborted" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Custom prompts
# --------------------------------------------------------------------------- #
def test_cmd_prompts_empty(iface, capsys):
    iface._cmd_prompts("")
    assert "No saved prompts" in capsys.readouterr().out


def test_cmd_prompts_list(iface, capsys):
    iface.prompts.save("alpha", "one line")
    iface.prompts.save("beta", "two lines\nof text")
    iface._cmd_prompts("")
    out = capsys.readouterr().out
    assert "alpha" in out and "beta" in out
    assert "one line" in out


def test_cmd_prompt_add(monkeypatch, iface, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("line one\nline two\n.\n"))
    iface._cmd_prompts("add myprompt")
    assert "saved prompt 'myprompt'" in capsys.readouterr().out
    assert iface.prompts.get("myprompt") == "line one\nline two\n"


def test_cmd_prompt_add_empty(monkeypatch, iface, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(".\n"))
    iface._cmd_prompts("add empty")
    assert "nothing saved" in capsys.readouterr().out


def test_cmd_prompt_add_missing_name(iface, capsys):
    iface._cmd_prompts("add")
    assert "usage" in capsys.readouterr().out


def test_cmd_prompt_del(iface, capsys):
    iface.prompts.save("p", "text")
    iface._cmd_prompts("del p")
    assert "deleted" in capsys.readouterr().out
    assert iface.prompts.get("p") is None


def test_cmd_prompt_del_missing(iface, capsys):
    iface._cmd_prompts("del nope")
    assert "no prompt named" in capsys.readouterr().out


def test_cmd_prompt_show(iface, capsys):
    iface.prompts.save("p", "the body")
    iface._cmd_prompts("show p")
    assert "the body" in capsys.readouterr().out


def test_cmd_prompt_show_missing(iface, capsys):
    iface._cmd_prompts("show nope")
    assert "no prompt named" in capsys.readouterr().out


def test_cmd_prompt_insert_single_line(monkeypatch, iface, capsys):
    iface.prompts.save("oneline", "do the thing")
    monkeypatch.setattr(interface_module, "_HAVE_READLINE", True)
    iface._cmd_prompts("oneline")
    assert iface._pending_prefill == "do the thing"
    assert "inserted" in capsys.readouterr().out


def test_cmd_prompt_insert_multiline_sends(monkeypatch, iface, fake_client, capsys):
    iface.prompts.save("multi", "line a\nline b")
    monkeypatch.setattr(interface_module, "_HAVE_READLINE", True)
    iface._cmd_prompts("multi")
    assert fake_client.prompt_calls == ["line a\nline b"]
    assert "sending" in capsys.readouterr().out


def test_cmd_prompt_missing(iface, capsys):
    iface._cmd_prompts("nope")
    assert "no prompt named" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Banner and help
# --------------------------------------------------------------------------- #
def test_banner(iface, capsys):
    iface._banner()
    assert "Pi interface" in capsys.readouterr().out


def test_print_help(iface, capsys):
    iface._print_help()
    out = capsys.readouterr().out
    assert "/tools" in out and "/prompt add" in out and "/quit" in out


# --------------------------------------------------------------------------- #
# Coverage-gap tests: unhandled records, branch fall-throughs, dispatch
# --------------------------------------------------------------------------- #
def test_on_event_message_end_non_assistant(iface, capsys):
    iface._out("x", end="")
    iface._on_event({"type": "message_end", "message": {"role": "user"}})
    assert capsys.readouterr().out == "x"  # no newline appended for user msgs


def test_on_event_tool_end_without_duration(iface, capsys):
    iface._on_event({"type": "tool_execution_end", "toolName": "bash", "isError": False})
    out = capsys.readouterr().out
    assert "\u2713" in out and "ms" not in out


def test_on_event_unhandled_records_ignored(iface, capsys):
    iface._on_event({"type": "response", "command": "prompt"})
    iface._on_event({"type": "agent_end", "messages": []})
    iface._on_event({"type": "turn_end"})
    iface._on_event({"type": "message_start"})
    iface._on_event({"type": "entry_appended"})
    assert capsys.readouterr().out == ""


def test_brief_args_non_str_command(iface):
    assert iface._brief_args("bash", {"command": 123}) == ""


def test_brief_args_non_str_path(iface):
    assert iface._brief_args("read", {"path": 123}) == ""


def test_brief_args_non_str_value(iface):
    assert iface._brief_args("grep", {"pattern": 123}) == ""


def test_brief_args_write_edit_find_ls(iface):
    assert "f" in iface._brief_args("write", {"file": "f"})
    assert "f" in iface._brief_args("edit", {"path": "f"})
    assert "x" in iface._brief_args("find", {"path": "x"})
    assert "x" in iface._brief_args("ls", {"directory": "x"})


def test_brief_args_powershell_and_newlines(iface):
    assert "dir" in iface._brief_args("powershell", {"command": "dir"})
    assert "\u23ce" in iface._brief_args("bash", {"command": "a\nb"})


def test_read_multiline_eof(monkeypatch, iface):
    monkeypatch.setattr(sys, "stdin", io.StringIO("only one line\n"))
    assert iface._read_multiline() == "only one line"


def test_run_blank_line_and_ctrl_c(monkeypatch, fake_client, tmp_path, capsys):
    interface = Interface(fake_client, PromptLibrary(tmp_path / "p"), color=False)
    sequence = iter([KeyboardInterrupt, "", "/quit"])

    def fake_input(prompt=""):
        value = next(sequence)
        if value is KeyboardInterrupt:
            raise KeyboardInterrupt
        return value

    monkeypatch.setattr("builtins.input", fake_input)
    interface.run()
    out = capsys.readouterr().out
    assert "^C" in out
    assert fake_client.closed


def test_send_prompt_client_error(fake_client, tmp_path, capsys):
    class RaisingClient(type(fake_client)):
        def prompt(self, message, steer=False, follow_up=False):
            raise interface_module.PiError("client died")

    interface = Interface(RaisingClient(), PromptLibrary(tmp_path / "p"), color=False)
    interface._send_prompt("go")
    assert "client died" in capsys.readouterr().out


def test_wait_for_settle_abort_error(fake_client, tmp_path, capsys):
    class FailingAbort(type(fake_client)):
        def abort(self):
            raise interface_module.PiError("abort failed")

    interface = Interface(FailingAbort(), PromptLibrary(tmp_path / "p"), color=False)

    class OneShot:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            return False

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt
            return True

    interface._settled = OneShot()
    interface._wait_for_settle()
    assert "abort failed" in capsys.readouterr().out


def test_handle_command_routes_all_commands(iface, capsys):
    for command in ("/tools", "/models", "/model", "/thinking", "/new",
                    "/abort", "/prompts", "/clear"):
        assert iface._handle_command(command) is True
    out = capsys.readouterr().out
    assert "Selected tools" in out  # /tools with no arg lists selection


def test_cmd_tools_duplicate_and_empty(iface, fake_client, capsys):
    iface._cmd_tools("read,read,,bash")
    assert fake_client.tools == ["read", "bash"]
    assert "ignored unknown" not in capsys.readouterr().out


def test_tools_menu_ignores_bad_tokens(monkeypatch, iface, fake_client):
    monkeypatch.setattr(sys, "stdin", io.StringIO("abc 99 5\n\n"))
    iface._cmd_tools("menu")
    assert "grep" in fake_client.tools  # token 5 toggled grep on
    assert "read" in fake_client.tools


def test_cmd_models_state_error(fake_client, tmp_path, capsys):
    class StateErrorClient(type(fake_client)):
        def get_state(self):
            raise interface_module.PiError("state err")

    interface = Interface(StateErrorClient(), PromptLibrary(tmp_path / "p"), color=False)
    interface._cmd_models()
    out = capsys.readouterr().out
    assert "model-a" in out  # still lists models despite state failure


def test_cmd_state_without_session_name(iface, fake_client, capsys):
    fake_client.state = {
        "model": {"id": "m", "name": "M"},
        "thinkingLevel": "low",
        "isStreaming": False,
        "messageCount": 0,
        "sessionId": "s",
    }
    iface._cmd_state()
    assert "name       :" not in capsys.readouterr().out
