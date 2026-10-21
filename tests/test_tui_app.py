"""Tests for the interactive pipeline TUI application."""

from __future__ import annotations

import time

import pytest

from tui.app import PipelineApp
from tui.screen import visible_width


class FakePipeline:
    """Minimal stand-in for ``pipeline.Pipeline``."""

    def __init__(self):
        self.role_names = ["reproduce", "diagnose"]
        self.model_name = "claude"
        self.reasoning = "low"
        self.observer = None
        self.run_step_calls: list[tuple] = []
        self.results = {
            "reproduce": {"summary": "reproduced", "test_fails": True},
            "diagnose": {"summary": "diagnosed"},
        }
        self.fail = False

    def run_step(self, name, previous, context=None):
        self.run_step_calls.append((name, previous, context))
        if self.fail:
            raise RuntimeError("boom")
        return self.results.get(name)


@pytest.fixture
def fake_pipeline():
    return FakePipeline()


@pytest.fixture
def app(fake_pipeline):
    return PipelineApp(fake_pipeline, color=False)


@pytest.fixture
def sized_app(app, monkeypatch):
    monkeypatch.setattr(app.term, "size", lambda: (80, 24))
    return app


# --------------------------------------------------------------------------- #
# Brief args
# --------------------------------------------------------------------------- #
def test_brief_args_bash(app):
    assert "ls" in app._brief_args("bash", {"command": "ls -la"})


def test_brief_args_truncates_long_command(app):
    assert len(app._brief_args("bash", {"command": "x" * 200})) <= 70


def test_brief_args_read_path(app):
    assert "file.txt" in app._brief_args("read", {"path": "file.txt"})


def test_brief_args_grep_pattern(app):
    assert "pat" in app._brief_args("grep", {"pattern": "pat"})


def test_brief_args_unknown_tool(app):
    assert app._brief_args("whatever", {}) == ""


# --------------------------------------------------------------------------- #
# Event translation
# --------------------------------------------------------------------------- #
def test_translate_text_delta(app):
    app._translate({"type": "message_update",
                    "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}})
    assert app.transcript.partial() == ("text", "hi")


def test_translate_thinking_shown(app):
    app._translate({"type": "message_update",
                    "assistantMessageEvent": {"type": "thinking_delta", "delta": "hmm"}})
    assert app.transcript.partial() == ("thinking", "hmm")


def test_translate_thinking_hidden(fake_pipeline):
    app = PipelineApp(fake_pipeline, color=False, show_thinking=False)
    app._translate({"type": "message_update",
                    "assistantMessageEvent": {"type": "thinking_delta", "delta": "hmm"}})
    assert app.transcript.partial() == ("text", "")


def test_translate_tool_start_and_end(app):
    app._translate({"type": "tool_execution_start", "toolName": "bash",
                    "args": {"command": "ls"}})
    app._translate({"type": "tool_execution_end", "toolName": "bash",
                    "isError": False, "durationMs": 12})
    assert app.transcript.lines[-1][0] == "tool"
    assert "✓" in app.transcript.lines[-1][1]
    assert "12 ms" in app.transcript.lines[-1][1]


def test_translate_tool_error_marker(app):
    app._translate({"type": "tool_execution_start", "toolName": "bash", "args": {}})
    app._translate({"type": "tool_execution_end", "toolName": "bash", "isError": True})
    assert "✗" in app.transcript.lines[-1][1]


def test_translate_settle_flushes(app):
    app._translate({"type": "message_update",
                    "assistantMessageEvent": {"type": "text_delta", "delta": "x"}})
    app._translate({"type": "agent_settled"})
    assert app.transcript.lines[-1] == ("text", "x")


def test_translate_auto_retry(app):
    app._translate({"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 10})
    assert "retry 1/3" in app.transcript.lines[-1][1]


# --------------------------------------------------------------------------- #
# Styling
# --------------------------------------------------------------------------- #
def test_style_line_kinds(sized_app):
    assert sized_app._style_line("role", "x").startswith("\x1b[1;")  # bold
    assert sized_app._style_line("thinking", "x").startswith("\x1b[3;")  # italic
    assert sized_app._style_line("text", "x").startswith("\x1b[38;2;")
    assert sized_app._style_line("tool", "x").startswith("\x1b[38;2;")
    assert sized_app._style_line("error", "x").startswith("\x1b[38;2;")


def test_summarize_result_json(sized_app):
    assert "✓ reproduce" in sized_app._summarize_result("reproduce", {"a": 1})


def test_summarize_result_truncates_long(sized_app):
    out = sized_app._summarize_result("diagnose", "x" * 500)
    assert len(out) <= 200


# --------------------------------------------------------------------------- #
# Input editing
# --------------------------------------------------------------------------- #
def test_edit_insert_and_backspace(app):
    app.phase = "input"
    app._edit_input("h")
    app._edit_input("i")
    assert app.input_line == "hi"
    app._edit_input("\x7f")
    assert app.input_line == "h"


def test_edit_left_right_and_home_end(app):
    app.phase = "input"
    for ch in "abc":
        app._edit_input(ch)
    app._edit_input("LEFT")
    assert app.input_cursor == 2
    app._edit_input("HOME")
    assert app.input_cursor == 0
    app._edit_input("END")
    assert app.input_cursor == 3
    app._edit_input("RIGHT")
    assert app.input_cursor == 3


def test_edit_delete_forward(app):
    app.phase = "input"
    for ch in "abc":
        app._edit_input(ch)
    app._edit_input("HOME")
    app._edit_input("DELETE")
    assert app.input_line == "bc"


def test_edit_ctrl_u_clears(app):
    app.phase = "input"
    for ch in "abc":
        app._edit_input(ch)
    app._edit_input("\x15")
    assert app.input_line == "" and app.input_cursor == 0


def test_edit_ctrl_d_empty_quits(app):
    app.phase = "input"
    app._edit_input("\x04")
    assert app.done is True


def test_submit_empty_input_ignored(app):
    app.phase = "input"
    app._submit()
    assert app.phase == "input"


def test_submit_quit_command(app):
    app.phase = "input"
    app.input_line = "/quit"
    app._submit()
    assert app.done is True


def test_submit_input_starts_first_role(fake_pipeline):
    app = PipelineApp(fake_pipeline, color=False)
    app.phase = "input"
    app.input_line = "the login button does nothing"
    app._submit()
    assert app.phase == "running"
    assert app.current_role == "reproduce"
    assert app.previous == "the login button does nothing"


# --------------------------------------------------------------------------- #
# Role execution
# --------------------------------------------------------------------------- #
def test_run_role_and_check_done(fake_pipeline):
    app = PipelineApp(fake_pipeline, color=False)
    app.previous = "prompt"
    app._start_role(0)
    assert app._role_done_event.wait(2.0)
    # Let the worker finish posting its result.
    for _ in range(100):
        if app._role_done is not None:
            break
        time.sleep(0.01)
    app._check_role_done()
    assert app.phase == "running"
    assert app.current_role == "diagnose"
    assert app.results["reproduce"] == {"summary": "reproduced", "test_fails": True}
    assert fake_pipeline.run_step_calls[0][0] == "reproduce"
    assert fake_pipeline.run_step_calls[0][1] == "prompt"


def test_run_last_role_reaches_done(fake_pipeline):
    app = PipelineApp(fake_pipeline, color=False)
    app.previous = {"summary": "reproduced"}
    app.role_index = 1
    app.current_role = "diagnose"
    app.phase = "running"
    app._role_done = ("diagnose", {"summary": "diagnosed"}, None)
    app._check_role_done()
    assert app.phase == "done"
    assert app.error is None


def test_run_role_error_marks_done(fake_pipeline):
    app = PipelineApp(fake_pipeline, color=False)
    app.phase = "running"
    app._role_done = ("reproduce", None, RuntimeError("boom"))
    app._check_role_done()
    assert app.phase == "done"
    assert app.error == "boom"


def test_handle_key_running_ctrl_c_sets_quit(app):
    app.phase = "running"
    app._handle_key("\x03")
    assert app._quit_requested is True


def test_handle_key_done_quits(app):
    app.phase = "done"
    app._handle_key("q")
    assert app.done is True


# --------------------------------------------------------------------------- #
# Frame building
# --------------------------------------------------------------------------- #
def test_build_frame_shape(sized_app):
    sized_app.phase = "input"
    sized_app.input_line = "hello"
    sized_app.input_cursor = 5
    lines, cursor = sized_app._build_frame()
    assert len(lines) == 24
    assert cursor == (21, 9)  # input row, after "› " + 5 chars


def test_build_frame_running_hint(sized_app):
    sized_app.phase = "running"
    sized_app.current_role = "reproduce"
    lines, cursor = sized_app._build_frame()
    assert cursor is None


def test_display_wrap_and_visible_tail(sized_app):
    sized_app.transcript.text("word " * 50)
    sized_app.transcript.settle()
    display = sized_app._display(20)
    chunks = sized_app._visible(display, 3)
    assert len(chunks) == 3
    assert all(len(text) <= 20 for _, _, text in chunks)


def test_rule_and_row_width(sized_app):
    assert visible_width(sized_app._rule("\u256d", "\u256e")) == 80
    assert visible_width(sized_app._row("x")) == 80


def test_header_content(sized_app):
    sized_app.current_role = "reproduce"
    assert "hms pipeline" in sized_app._header_content()
    assert "role 1/2" in sized_app._header_content()


def test_status_content_input(sized_app):
    sized_app.phase = "input"
    assert "bug report" in sized_app._status_content()


def test_status_content_running(sized_app):
    sized_app.phase = "running"
    sized_app.current_role = "reproduce"
    sized_app.elapsed_start = time.monotonic()
    assert "running reproduce" in sized_app._status_content()


def test_status_content_done_error(sized_app):
    sized_app.phase = "done"
    sized_app.error = "boom"
    assert "boom" in sized_app._status_content()


def test_prompt_content(sized_app):
    sized_app.input_line = "hi"
    assert "hi" in sized_app._prompt_content()
    assert "\u203a" in sized_app._prompt_content()


# --------------------------------------------------------------------------- #
# Full run loop (headless)
# --------------------------------------------------------------------------- #
def test_run_quits_on_ctrl_d(fake_pipeline, monkeypatch):
    app = PipelineApp(fake_pipeline, color=False)
    monkeypatch.setattr(app.term, "size", lambda: (80, 24))
    monkeypatch.setattr(app.term, "enter", lambda: None)
    monkeypatch.setattr(app.term, "exit", lambda: None)
    monkeypatch.setattr(app.term, "read_key", lambda timeout=None: "\x04")
    assert app.run() == {}
    assert app.done is True


def test_run_handles_none_key(fake_pipeline, monkeypatch):
    app = PipelineApp(fake_pipeline, color=False)
    monkeypatch.setattr(app.term, "size", lambda: (80, 24))
    monkeypatch.setattr(app.term, "enter", lambda: None)
    monkeypatch.setattr(app.term, "exit", lambda: None)
    calls = {"n": 0}

    def read_key(timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return "\x04"

    monkeypatch.setattr(app.term, "read_key", read_key)
    assert app.run() == {}


# --------------------------------------------------------------------------- #
# Observer / event draining
# --------------------------------------------------------------------------- #
def test_observer_and_drain(app):
    app._observer({"type": "message_update",
                   "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}})
    app._drain_events()
    assert app.transcript.partial() == ("text", "hi")


def test_translate_unknown_type(app):
    app._translate({"type": "bogus"})  # falls through without error


# --------------------------------------------------------------------------- #
# More brief-args / summarize branches
# --------------------------------------------------------------------------- #
def test_brief_args_non_string_values(app):
    assert app._brief_args("bash", {"command": 123}) == ""
    assert app._brief_args("read", {"path": 123}) == ""
    assert app._brief_args("grep", {"pattern": 123}) == ""


def test_summarize_result_non_serializable(sized_app):
    out = sized_app._summarize_result("x", object())
    assert "\u2713 x" in out


# --------------------------------------------------------------------------- #
# More handle-key branches
# --------------------------------------------------------------------------- #
def test_handle_key_running_other_key(app):
    app.phase = "running"
    app._handle_key("x")
    assert app._quit_requested is False


def test_handle_key_done_other_key(app):
    app.phase = "done"
    app._handle_key("x")
    assert app.done is False


def test_handle_key_unknown_phase(app):
    app.phase = "bogus"
    app._handle_key("x")  # no-op


# --------------------------------------------------------------------------- #
# More input-editing branches
# --------------------------------------------------------------------------- #
def test_edit_enter_submits(fake_pipeline):
    app = PipelineApp(fake_pipeline, color=False)
    app.phase = "input"
    app.input_line = "bug"
    app._edit_input("\r")
    assert app.phase == "running"
    assert app.current_role == "reproduce"


def test_edit_ctrl_d_deletes_forward(app):
    app.phase = "input"
    app.input_line = "abc"
    app.input_cursor = 0
    app._edit_input("\x04")
    assert app.input_line == "bc"


def test_edit_ctrl_d_at_end_is_noop(app):
    app.phase = "input"
    app.input_line = "a"
    app.input_cursor = 1
    app._edit_input("\x04")
    assert app.input_line == "a"


def test_edit_arrow_and_escape_ignored(app):
    app.phase = "input"
    app._edit_input("UP")
    app._edit_input("DOWN")
    app._edit_input("ESC")
    assert app.input_line == ""


def test_edit_nonprintable_ignored(app):
    app.phase = "input"
    app._edit_input("\x01")
    assert app.input_line == ""


def test_delete_backward_at_start(app):
    app.input_line = "a"
    app.input_cursor = 0
    app._delete_backward()
    assert app.input_line == "a"


def test_delete_forward_at_end(app):
    app.input_line = "a"
    app.input_cursor = 1
    app._delete_forward()
    assert app.input_line == "a"


def test_submit_unknown_phase(app):
    app.phase = "done"
    app.input_line = "x"
    app._submit()
    assert app.phase == "done"


# --------------------------------------------------------------------------- #
# More frame-building branches
# --------------------------------------------------------------------------- #
def test_display_multiple_short_lines(sized_app):
    sized_app.transcript.text("a\nb")
    sized_app.transcript.settle()
    assert sized_app._display(10) == [("text", "", "a"), ("text", "", "b")]


def test_display_includes_pending(sized_app):
    sized_app.transcript.text("pending")
    assert sized_app._display(10) == [("text", "", "pending")]


def test_visible_scroll_offset(sized_app):
    sized_app.transcript.text("a\nb\nc\nd\ne")
    sized_app.transcript.settle()
    display = sized_app._display(10)
    assert sized_app._visible(display, 2) == [("text", "", "d"), ("text", "", "e")]
    sized_app._scroll_offset = 2
    assert sized_app._visible(display, 2) == [("text", "", "b"), ("text", "", "c")]


def test_wrap_kind_indents_tools(sized_app):
    chunks = sized_app._wrap_kind("tool", "⚙ bash ls", 20)
    assert all(c.startswith("  ") for c in chunks)


def test_style_line_spacer(sized_app):
    assert sized_app._style_line("spacer", "") == ""


def test_scroll_up_and_down(sized_app):
    sized_app._scroll("UP")
    assert sized_app._scroll_offset == 1
    assert sized_app._auto_scroll is False
    sized_app._scroll("DOWN")
    assert sized_app._scroll_offset == 0
    assert sized_app._auto_scroll is True


def test_scroll_page_step(sized_app):
    sized_app._scroll("PAGE_UP")
    assert sized_app._scroll_offset == 17  # body height at 24 rows
    sized_app._scroll("PAGE_DOWN")
    assert sized_app._scroll_offset == 0


def test_scroll_home_jumps_to_top(sized_app):
    sized_app._total_lines = 50
    sized_app._scroll("HOME")
    assert sized_app._scroll_offset == 50 - 17
    assert sized_app._auto_scroll is False


def test_handle_key_up_scrolls(app):
    app.phase = "running"
    app._handle_key("UP")
    assert app._scroll_offset == 1


def test_handle_key_running_home_scrolls(sized_app):
    sized_app.phase = "running"
    sized_app._total_lines = 100
    sized_app._handle_key("HOME")
    assert sized_app._scroll_offset == 100 - 17


def test_handle_key_done_end_scrolls(sized_app):
    sized_app.phase = "done"
    sized_app._scroll_offset = 5
    sized_app._handle_key("END")
    assert sized_app._scroll_offset == 0
    assert sized_app._auto_scroll is True


def test_scroll_down_stays_above_bottom(sized_app):
    sized_app._scroll_offset = 5
    sized_app._auto_scroll = False
    sized_app._scroll("DOWN")
    assert sized_app._scroll_offset == 4
    assert sized_app._auto_scroll is False


def test_scroll_unknown_key_is_noop(sized_app):
    sized_app._scroll_offset = 3
    sized_app._auto_scroll = False
    sized_app._scroll("DELETE")
    assert sized_app._scroll_offset == 3
    assert sized_app._auto_scroll is False


def test_build_frame_clamps_scroll_offset(sized_app):
    sized_app.transcript.text("a\nb")
    sized_app.transcript.settle()
    sized_app._scroll_offset = 50
    sized_app._build_frame()
    assert sized_app._scroll_offset == 0


def test_status_content_scrolled_marker(sized_app):
    sized_app.phase = "done"
    sized_app._scroll_offset = 3
    assert "scrolled" in sized_app._status_content()


def test_phase_hint_scrolled(sized_app):
    sized_app.phase = "running"
    sized_app._scroll_offset = 1
    assert "scrolled" in sized_app._phase_hint()


def test_style_line_info_and_result(sized_app):
    assert sized_app._style_line("info", "x").startswith("\x1b[38;2;")
    assert sized_app._style_line("result", "x").startswith("\x1b[38;2;")


def test_phase_hint_done(sized_app):
    sized_app.phase = "done"
    assert "quit" in sized_app._phase_hint()


def test_phase_hint_other(sized_app):
    sized_app.phase = "input"
    assert sized_app._phase_hint() == ""


def test_status_content_done_success(sized_app):
    sized_app.phase = "done"
    assert "pipeline complete" in sized_app._status_content()


def test_status_content_running_no_elapsed(sized_app):
    sized_app.phase = "running"
    sized_app.current_role = "reproduce"
    sized_app.elapsed_start = None
    assert "running reproduce" in sized_app._status_content()


def test_build_frame_with_transcript(sized_app):
    sized_app.phase = "running"
    sized_app.current_role = "reproduce"
    sized_app.transcript.text("hello world")
    sized_app.transcript.settle()
    lines, _ = sized_app._build_frame()
    assert any("hello" in line for line in lines)


# --------------------------------------------------------------------------- #
# The fanned-out primitives
# --------------------------------------------------------------------------- #
def _fanned_out(app, files=("src/app.py", "src/db.py")):
    """Register a fan-out and stream one record from each of its primitives."""
    app._translate({"type": "primitives_fanned_out", "files": list(files)})
    for source_file in files:
        app._translate(
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "working"},
                "primitive": source_file,
            }
        )


def test_fanned_out_registers_every_primitive_and_announces_them(app):
    _fanned_out(app)

    assert app.feed.order == ["src/app.py", "src/db.py"]
    assert app.selected == "src/app.py"
    assert "2 primitive(s) fanned out" in app.transcript.lines[-1][1]


def test_fanned_out_ignores_entries_that_name_no_file(app):
    app._translate({"type": "primitives_fanned_out", "files": ["src/app.py", "", None]})

    assert app.feed.order == ["src/app.py"]
    assert app.selected == "src/app.py"


def test_fanned_out_keeps_the_selection_a_later_role_left_on_a_primitive(app):
    _fanned_out(app)
    app.selected = "src/db.py"

    _fanned_out(app, files=("src/other.py",))

    assert app.selected == "src/db.py"


def test_primitive_records_reach_their_own_stream(app):
    _fanned_out(app)

    assert app.transcript.lines == [] or all(
        line[0] == "info" for line in app.transcript.lines
    )
    app.feed.transcript_for("src/app.py").settle()
    app.feed.transcript_for("src/db.py").settle()
    assert app.feed.streams["src/app.py"].lines[-1] == ("text", "working")
    assert app.feed.streams["src/db.py"].lines[-1] == ("text", "working")


def test_a_primitive_the_fan_out_never_announced_is_still_streamed(app):
    app._translate(
        {
            "type": "tool_execution_start",
            "toolName": "read",
            "args": {"path": "src/late.py"},
            "primitive": "src/late.py",
        }
    )

    assert app.feed.order == ["src/late.py"]
    assert app.feed.streams["src/late.py"].lines[-1][1] == "\u2699 read  src/late.py \u2026"


def test_primitive_report_settles_its_stream(app):
    _fanned_out(app)

    app._translate(
        {"type": "primitive_reported", "primitive": "src/app.py", "report": "fixed it"}
    )

    assert app.feed.status["src/app.py"] == "done"
    assert app.feed.streams["src/app.py"].lines[-1] == ("result", "fixed it")


def test_primitive_failure_turns_its_file_name_red(app):
    _fanned_out(app)

    app._translate(
        {
            "type": "primitive_failed",
            "primitive": "src/db.py",
            "error": "pi exited before the turn settled",
        }
    )

    assert app.feed.status["src/db.py"] == "error"
    assert app.feed.marker("src/db.py") == "\u2717"
    assert app.feed.status_color("src/db.py") == "error"
    assert app.feed.status_color("src/app.py") == "accent"


def test_role_failure_marks_the_primitives_it_names(app):
    from roles.primitive_manager import PrimitiveRunError

    app.phase = "running"
    app.feed.register("src/app.py")
    app.feed.register("src/db.py")
    app._role_done = (
        "primitive_manager",
        None,
        PrimitiveRunError({"src/db.py": RuntimeError("boom")}, {"src/app.py": "fixed"}),
    )

    app._check_role_done()

    assert app.feed.status["src/db.py"] == "error"
    assert app.feed.status["src/app.py"] == "done"
    assert app.phase == "done"


def test_finished_fan_out_settles_the_primitives_that_reported_nothing(app):
    app.phase = "running"
    app.feed.register("src/app.py")
    app.role_index = 2
    app._role_done = ("primitive_manager", {"src/app.py": "fixed"}, None)

    app._check_role_done()

    assert app.feed.status["src/app.py"] == "done"


def test_tab_cycles_the_panel_and_enters_the_primitive(app):
    _fanned_out(app)
    app.phase = "running"

    app._handle_key("\t")

    assert app.selected == "src/db.py"
    assert app.focus is None

    app._handle_key("\t")

    assert app.selected == "src/app.py"


def test_enter_opens_the_selected_primitive(app):
    _fanned_out(app)
    app.phase = "running"

    app._handle_key("\r")

    assert app.focus == "src/app.py"


def test_tab_stepping_while_a_primitive_is_open_moves_to_the_next_one(app):
    _fanned_out(app)
    app.phase = "running"
    app.focus = "src/app.py"

    app._handle_key("\t")

    assert app.focus == "src/db.py"


def test_esc_or_q_leaves_an_open_primitive(app):
    _fanned_out(app)
    app.phase = "running"
    app.focus = "src/app.py"

    app._handle_key("\x7f")
    assert app.focus is None

    app.focus = "src/app.py"
    app._handle_key("q")
    assert app.focus is None


def test_a_key_the_open_primitive_does_not_use_falls_through(app):
    _fanned_out(app)
    app.phase = "running"
    app.focus = "src/app.py"

    app._handle_key("\r")

    assert app.focus == "src/app.py"
    assert app.done is False


def test_tab_from_a_stale_selection_returns_to_the_first_primitive(app):
    _fanned_out(app)
    app.selected = "src/vanished.py"

    app._handle_key("\t")

    assert app.selected == "src/app.py"


def test_enter_does_not_open_a_primitive_where_text_is_entered(app):
    _fanned_out(app)
    app.phase = "input"

    app._handle_key("\r")

    assert app.focus is None


def test_panel_keys_are_inert_before_a_fan_out(app):
    app.phase = "running"

    assert app._panel_key("\t") is False
    assert app._panel_key("\r") is False
    assert app.selected is None


def test_starting_a_role_drops_the_open_primitive(app):
    _fanned_out(app)
    app.focus = "src/app.py"

    app._start_role(0)

    assert app.focus is None


def test_panel_width_follows_the_terminal(sized_app):
    _fanned_out(sized_app)

    assert sized_app._panel_width(80) == 26
    assert sized_app._panel_width(90) == 30
    assert sized_app._panel_width(400) == 32
    assert sized_app._panel_width(60) == 0


def test_panel_width_is_zero_without_primitives(sized_app):
    assert sized_app._panel_width(120) == 0

    _fanned_out(sized_app)

    assert sized_app._panel_width(120) == 32


def test_display_appends_the_fan_out_after_the_main_transcript(sized_app):
    _fanned_out(sized_app)
    sized_app.feed.streams["src/app.py"].settle()
    sized_app.feed.streams["src/db.py"].settle()

    rows = sized_app._display(40)

    kinds = [kind for kind, _, _ in rows]
    assert "info" in kinds  # the fan-out announcement
    assert kinds.count("role") == 2  # one header per primitive
    assert ("text", "src/app.py", "working") in rows
    assert ("text", "src/db.py", "working") in rows


def test_display_labels_only_the_focused_stream_when_one_is_open(sized_app):
    _fanned_out(sized_app)
    sized_app.feed.streams["src/app.py"].settle()
    sized_app.feed.streams["src/db.py"].settle()
    sized_app.focus = "src/db.py"

    rows = sized_app._display(40)

    assert ("text", "src/app.py", "working") not in rows
    assert ("text", "src/db.py", "working") in rows


def test_display_labels_are_shortened_before_the_text_column(sized_app):
    sized_app._translate(
        {"type": "primitives_fanned_out", "files": ["environment/codebase/src/app.py"]}
    )
    sized_app.feed.streams["environment/codebase/src/app.py"].text("working")
    sized_app.feed.streams["environment/codebase/src/app.py"].settle()

    assert sized_app._label_width(40) == 13
    assert sized_app._text_width(40) == 26
    # a body too narrow for both columns still leaves the label its column
    assert sized_app._label_width(3) == 1
    assert sized_app._text_width(3) == 1


def test_style_row_paints_the_file_name_in_the_state_color(sized_app):
    _fanned_out(sized_app)
    sized_app.feed.note_failure("src/db.py", "boom")

    running = sized_app._style_row(("text", "src/app.py", "working"), 12)
    failed = sized_app._style_row(("text", "src/db.py", "working"), 12)

    assert "src/app.py" in running
    assert "src/db.py" in failed
    assert failed != running
    # a banner paints both columns in the same color
    banner = sized_app._style_row(("role", "src/db.py", "\u2717  failed"), 12)
    assert banner.count("38;2;229;103;108") == 2


def test_style_row_without_a_label_column_styles_the_text_alone(sized_app):
    assert sized_app._style_row(("text", "src/app.py", "working"), 0) == (
        sized_app._style_line("text", "working")
    )


def test_build_frame_splits_the_panel_and_the_body(sized_app):
    _fanned_out(sized_app)
    sized_app.feed.streams["src/app.py"].settle()
    sized_app.feed.streams["src/db.py"].settle()
    sized_app.phase = "running"
    sized_app.current_role = "primitive_manager"

    lines, _ = sized_app._build_frame()

    assert len(lines) == 24
    assert all(visible_width(line) == 80 for line in lines)
    assert any("primitives (2)" in line for line in lines)
    assert any("src/db.py" in line for line in lines)


def test_build_frame_keeps_a_narrow_terminal_single_column(sized_app, monkeypatch):
    _fanned_out(sized_app)
    monkeypatch.setattr(sized_app.term, "size", lambda: (60, 24))

    lines, _ = sized_app._build_frame()

    assert all(visible_width(line) == 60 for line in lines)
    assert not any("primitives (2)" in line for line in lines)


def test_panel_row_highlights_the_open_primitive(sized_app):
    _fanned_out(sized_app)
    sized_app.feed.note_failure("src/db.py", "boom")

    selected = sized_app._panel_row("src/app.py", 24)
    other = sized_app._panel_row("src/db.py", 24)

    assert visible_width(selected) == 24
    assert visible_width(other) < 24  # not padded to the selected background
    assert "src/app.py" in selected and "src/db.py" in other
    assert selected.startswith("\x1b[48;2;")


def test_panel_rows_are_headed_with_the_fan_out_size(sized_app):
    _fanned_out(sized_app)

    rows = sized_app._panel_rows(24)

    assert "primitives (2)" in rows[0]
    assert len(rows) == 3


def test_status_content_counts_the_fan_out(sized_app):
    _fanned_out(sized_app)
    sized_app.phase = "running"
    sized_app.current_role = "primitive_manager"
    sized_app.elapsed_start = time.monotonic()

    assert "2 primitives" in sized_app._status_content()


def test_status_content_has_no_fan_out_summary_for_another_role(sized_app):
    _fanned_out(sized_app)
    sized_app.phase = "running"
    sized_app.current_role = "reproduce"
    sized_app.elapsed_start = time.monotonic()

    assert "primitives" not in sized_app._status_content()


def test_phase_hint_lists_the_panel_keys(sized_app):
    _fanned_out(sized_app)
    sized_app.phase = "running"
    assert "Tab switch" in sized_app._phase_hint()
    assert "Enter open" in sized_app._phase_hint()

    sized_app.focus = "src/app.py"
    assert "Esc or q back" in sized_app._phase_hint()

    sized_app.focus = None
    sized_app.phase = "done"
    assert "q to quit" in sized_app._phase_hint()


def test_done_without_a_fan_out_still_quits_on_enter(sized_app):
    sized_app.phase = "done"

    sized_app._handle_key("\r")

    assert sized_app.done is True
