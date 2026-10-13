"""Tests for terminal width helpers and key parsing."""

from __future__ import annotations

import shutil
import sys

from tui.screen import Terminal, pad, truncate, visible_width, wrap_text


def test_visible_width_strips_ansi():
    assert visible_width("\x1b[31mhi\x1b[0m") == 2
    assert visible_width("plain") == 5


def test_truncate_keeps_ansi_and_limits_width():
    text = "\x1b[31mhello\x1b[0m world"
    out = truncate(text, 5)
    assert visible_width(out) == 5
    assert "\x1b[31m" in out


def test_truncate_empty_width():
    assert truncate("abc", 0) == ""


def test_truncate_bare_escape_not_matched():
    # A lone ESC (no following '[' or ']') is not a recognized ANSI sequence.
    assert truncate("\x1b", 5) == "\x1b"


def test_pad_pads_short_text():
    assert visible_width(pad("ab", 5)) == 5
    assert pad("ab", 5).startswith("ab")


def test_pad_truncates_long_text():
    assert visible_width(pad("abcdef", 3)) == 3


def test_wrap_text_word_wraps():
    assert wrap_text("one two three", 8) == ["one two", "three"]


def test_wrap_text_hard_breaks_long_word():
    assert wrap_text("abcdefghij", 4) == ["abcd", "efgh", "ij"]


def test_wrap_text_preserves_blank_lines():
    assert wrap_text("a\n\nb", 10) == ["a", "", "b"]


def test_wrap_text_empty():
    assert wrap_text("", 10) == [""]
    assert wrap_text("x", 0) == []


def test_map_csi_arrows_and_editing_keys():
    assert Terminal._map_csi(b"A") == "UP"
    assert Terminal._map_csi(b"B") == "DOWN"
    assert Terminal._map_csi(b"C") == "RIGHT"
    assert Terminal._map_csi(b"D") == "LEFT"
    assert Terminal._map_csi(b"H") == "HOME"
    assert Terminal._map_csi(b"F") == "END"
    assert Terminal._map_csi(b"3~") == "DELETE"
    assert Terminal._map_csi(b"Z") is None


def test_parse_key_plain_characters():
    term = Terminal(color=False)
    term._keybuf = b"abc"
    assert term._parse_key() == "a"
    assert term._parse_key() == "b"
    assert term._parse_key() == "c"
    assert term._parse_key() is None


def test_parse_key_csi_sequence():
    term = Terminal(color=False)
    term._keybuf = b"\x1b[A"
    assert term._parse_key() == "UP"


def test_parse_key_csi_with_parameter_bytes():
    term = Terminal(color=False)
    term._keybuf = b"\x1b[3~"
    assert term._parse_key() == "DELETE"


def test_parse_key_incomplete_csi_waits():
    term = Terminal(color=False)
    term._keybuf = b"\x1b["
    assert term._parse_key() is None
    term._keybuf += b"A"
    assert term._parse_key() == "UP"


def test_parse_key_alt_key():
    term = Terminal(color=False)
    term._keybuf = b"\x1bx"
    assert term._parse_key() == "x"


def test_parse_key_lone_escape():
    term = Terminal(color=False)
    term._keybuf = b"\x1b"
    assert term._parse_key() is None


def test_terminal_size_delegates(monkeypatch):
    term = Terminal(color=False)
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback=None: (100, 40))
    assert term.size() == (100, 40)


def test_terminal_init_color_none_uses_isatty(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    term = Terminal()
    assert term._use_color is False
    assert term._fd is None


def test_terminal_init_color_none_true(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    term = Terminal()
    assert term._use_color is True


def test_terminal_init_fd_present(monkeypatch):
    class FakeStdin:
        def isatty(self):
            return True

        def fileno(self):
            return 0

    monkeypatch.setattr(sys, "stdin", FakeStdin())
    term = Terminal(color=False)
    assert term._fd == 0


def test_enter_exit_color(monkeypatch, capsys):
    term = Terminal(color=True)
    term.enter()
    assert "\x1b[?1049h" in capsys.readouterr().out
    term.exit()
    assert "\x1b[?1049l" in capsys.readouterr().out


def test_enter_exit_no_color(monkeypatch, capsys):
    term = Terminal(color=False)
    term.enter()
    term.exit()
    assert capsys.readouterr().out == ""


def test_paint_color_with_cursor(monkeypatch, capsys):
    term = Terminal(color=True)
    monkeypatch.setattr(term, "size", lambda: (10, 3))
    term.paint(["ab"], cursor=(0, 2))
    out = capsys.readouterr().out
    assert "\x1b[1;3H" in out
    assert "\x1b[?25h" in out


def test_paint_color_without_cursor(monkeypatch, capsys):
    term = Terminal(color=True)
    monkeypatch.setattr(term, "size", lambda: (10, 3))
    term.paint(["ab"])
    assert "\x1b[?25l" in capsys.readouterr().out


def test_paint_no_color_multiple_lines(monkeypatch, capsys):
    term = Terminal(color=False)
    monkeypatch.setattr(term, "size", lambda: (10, 3))
    term.paint(["ab", "cd"])
    out = capsys.readouterr().out
    assert "\x1b[H" in out and "ab" in out and "\r\n" in out


def test_read_key_without_fd(monkeypatch):
    term = Terminal(color=False)
    assert term.read_key() is None


def test_parse_key_escape_sequence(monkeypatch):
    term = Terminal(color=False)
    term._keybuf = b"\x1b\x1b"
    assert term._parse_key() == "ESC"


def test_map_csi_home_end_delete_variants():
    assert Terminal._map_csi(b"1~") == "HOME"
    assert Terminal._map_csi(b"7~") == "HOME"
    assert Terminal._map_csi(b"4~") == "END"
    assert Terminal._map_csi(b"8~") == "END"
    assert Terminal._map_csi(b"3~") == "DELETE"
