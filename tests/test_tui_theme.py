"""Tests for the Pi-style theme module."""

from __future__ import annotations

from tui.theme import (
    DARK_PALETTE,
    DARK_THEME,
    LIGHT_PALETTE,
    LIGHT_THEME,
    Theme,
    _hex,
    okhsl,
    theme_by_name,
)


def test_okhsl_returns_8bit_triple():
    r, g, b = okhsl(234, 0.03, 0.89)
    assert all(0 <= c <= 255 for c in (r, g, b))


def test_okhsl_full_saturation_is_in_gamut():
    # Saturation 1 must still land inside the sRGB gamut.
    for h in range(0, 360, 30):
        r, g, b = okhsl(h, 1.0, 0.5)
        assert all(0 <= c <= 255 for c in (r, g, b))


def test_hex_is_well_formed():
    value = _hex(295, 0.5, 0.67)
    assert len(value) == 7 and value[0] == "#"


def test_dark_palette_has_semantic_tokens():
    for token in ("accent", "text", "muted", "dim", "success", "error", "warning", "border"):
        assert token in DARK_PALETTE


def test_light_palette_has_semantic_tokens():
    for token in ("accent", "text", "muted", "dim", "success", "error", "warning", "border"):
        assert token in LIGHT_PALETTE


def test_theme_hex_resolution():
    theme = Theme(DARK_PALETTE)
    assert theme.hex("accent") == DARK_PALETTE["accent"]
    assert theme.hex("missing") is None


def test_theme_style_fg_only():
    theme = Theme({"red": "#ff0000"})
    out = theme.style("x", fg="red")
    assert out == "\x1b[38;2;255;0;0mx\x1b[0m"


def test_theme_style_no_codes_returns_plain_text():
    theme = Theme({})
    assert theme.style("x") == "x"


def test_theme_style_attributes():
    theme = Theme({"text": "#ffffff"})
    out = theme.style("x", fg="text", bold=True, dim=True, italic=True, underline=True)
    assert out.startswith("\x1b[1;2;3;4;38;2;255;255;255m")


def test_theme_style_unknown_token_ignored():
    theme = Theme({})
    assert theme.style("x", fg="nope") == "x"


def test_theme_style_bg():
    theme = Theme({"blue": "#0000ff"})
    out = theme.style("x", bg="blue")
    assert "\x1b[48;2;0;0;255mx\x1b[0m" == out


def test_theme_style_unknown_bg_ignored():
    theme = Theme({})
    assert theme.style("x", bg="nope") == "x"


def test_theme_resolves_hex_literal_and_shorthand():
    theme = Theme({})
    assert theme._resolve("#abc") == (170, 187, 204)
    assert theme._resolve("#aabbcc") == (170, 187, 204)


def test_theme_by_name():
    assert theme_by_name("dark") is DARK_THEME
    assert theme_by_name("light") is LIGHT_THEME
    assert theme_by_name("LIGHT") is LIGHT_THEME
    assert theme_by_name(None) is DARK_THEME
    assert theme_by_name("bogus") is DARK_THEME
