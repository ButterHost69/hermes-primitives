"""Tests for the streaming transcript buffer."""

from __future__ import annotations

from tui.transcript import (
    KIND_ERROR,
    KIND_ROLE,
    KIND_SPACER,
    KIND_TEXT,
    KIND_THINKING,
    KIND_TOOL,
    Transcript,
)


def test_text_delta_streams_and_splits_newlines():
    t = Transcript()
    t.text("hel")
    t.text("lo\nwor")
    t.text("ld")
    t.settle()
    assert t.lines == [(KIND_TEXT, "hello"), (KIND_TEXT, "world")]


def test_thinking_uses_thinking_kind():
    t = Transcript()
    t.thinking("hmm")
    t.settle()
    assert t.lines == [(KIND_THINKING, "hmm")]


def test_thinking_splits_newlines():
    t = Transcript()
    t.thinking("a\nb")
    t.settle()
    assert t.lines == [(KIND_THINKING, "a"), (KIND_THINKING, "b")]


def test_tool_flushes_pending_text():
    t = Transcript()
    t.text("before")
    t.tool("⚙ bash …")
    assert t.lines == [
        (KIND_TEXT, "before"),
        (KIND_SPACER, ""),
        (KIND_TOOL, "⚙ bash …"),
    ]


def test_extend_last_appends_to_last_line():
    t = Transcript()
    t.tool("⚙ bash …")
    t.extend_last(" ✓ (12 ms)")
    assert t.lines == [(KIND_TOOL, "⚙ bash … ✓ (12 ms)")]


def test_extend_last_empty_transcript_is_noop():
    t = Transcript()
    t.extend_last("x")
    assert t.lines == []


def test_role_banner_and_result():
    t = Transcript()
    t.role("▶ reproduce")
    t.result("✓ reproduce → {}")
    assert t.lines[0] == (KIND_ROLE, "▶ reproduce")


def test_role_adds_spacers_with_existing_content():
    t = Transcript()
    t.text("hello")
    t.settle()
    t.role("▶ reproduce")
    assert t.lines == [
        (KIND_TEXT, "hello"),
        (KIND_SPACER, ""),
        (KIND_ROLE, "▶ reproduce"),
        (KIND_SPACER, ""),
    ]


def test_info_and_error_kinds():
    t = Transcript()
    t.info("[retry 1/3]")
    t.error("boom")
    assert t.lines[0] == ("info", "[retry 1/3]")
    assert t.lines[1] == (KIND_SPACER, "")
    assert t.lines[2] == (KIND_ERROR, "boom")


def test_bounded_lines_drop_oldest():
    t = Transcript(max_lines=3)
    for i in range(5):
        t.info(str(i))
        t.settle()
    assert [text for _, text in t.lines] == ["2", "3", "4"]


def test_partial_returns_pending_without_flushing():
    t = Transcript()
    t.text("partial")
    assert t.partial() == (KIND_TEXT, "partial")
    assert t.lines == []


def test_append_ignores_empty():
    t = Transcript()
    t.info("")
    assert t.lines == []
