"""Pi-style terminal theme.

Reproduces the color palette of the Pi coding agent's ``dark`` and ``light``
themes.  Pi's theme files express colors in OKHSL; this module implements the
OKHSL → sRGB conversion so the TUI can emit the same hues as Pi, then turns
the result into ANSI truecolor escapes.

The conversion follows Björn Ottosson's OKLab/OKHSL reference.  OKHSL keeps
lightness from OKLab and scales saturation relative to the maximum chroma the
sRGB gamut allows at that hue and lightness, so saturation is found by binary
search over chroma.
"""

from __future__ import annotations

import math
from typing import Mapping

# --------------------------------------------------------------------------- #
# OKHSL -> sRGB
# --------------------------------------------------------------------------- #
def _oklab_to_linear_srgb(L: float, a: float, b: float) -> tuple[float, float, float]:
    """Convert OKLab ``(L, a, b)`` to linear-light sRGB components."""
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return r, g, b


def _in_gamut(rgb: tuple[float, float, float]) -> bool:
    return all(-1e-6 <= c <= 1 + 1e-6 for c in rgb)


def _max_chroma(h_rad: float, L: float) -> float:
    """Largest chroma in the sRGB gamut at hue ``h_rad`` and lightness ``L``."""
    a_dir, b_dir = math.cos(h_rad), math.sin(h_rad)
    lo, hi = 0.0, 0.5
    for _ in range(60):
        mid = (lo + hi) / 2
        if _in_gamut(_oklab_to_linear_srgb(L, mid * a_dir, mid * b_dir)):
            lo = mid
        else:
            hi = mid
    return lo


def _linear_to_srgb(c: float) -> float:
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1 / 2.4)) - 0.055


def okhsl(h: float, s: float, l: float) -> tuple[int, int, int]:
    """Convert OKHSL ``(h degrees, s, l in 0..1)`` to an 8-bit sRGB triple."""
    h_rad = math.radians(h)
    chroma = _max_chroma(h_rad, l) * s
    a = chroma * math.cos(h_rad)
    b = chroma * math.sin(h_rad)
    r, g, b = _oklab_to_linear_srgb(l, a, b)

    def to_byte(c: float) -> int:
        c = min(1.0, max(0.0, _linear_to_srgb(c)))
        return round(c * 255)

    return to_byte(r), to_byte(g), to_byte(b)


def _hex(h: float, s: float, l: float) -> str:
    r, g, b = okhsl(h, s, l)
    return f"#{r:02x}{g:02x}{b:02x}"


# --------------------------------------------------------------------------- #
# Palettes (mirroring Pi's built-in themes)
# --------------------------------------------------------------------------- #
def _build_dark_palette() -> dict[str, str]:
    text = _hex(234, 0.03, 0.89)
    muted = _hex(229, 0.06, 0.67)
    violet = _hex(295, 0.50, 0.67)
    blue = _hex(232, 0.54, 0.67)
    green = _hex(159, 0.59, 0.67)
    red = _hex(20, 0.72, 0.67)
    yellow = _hex(83, 0.88, 0.67)
    return {
        "text": text,
        "muted": muted,
        "violet": violet,
        "blue": blue,
        "green": green,
        "red": red,
        "yellow": yellow,
        "accent": violet,
        "border": _hex(231, 0.57, 0.65),
        "borderAccent": _hex(295, 0.53, 0.64),
        "borderMuted": _hex(229, 0.08, 0.53),
        "success": green,
        "error": red,
        "warning": yellow,
        "dim": _hex(229, 0.08, 0.56),
        "thinkingText": _hex(226, 0.07, 0.65),
        "selectedBg": _hex(233, 0.41, 0.24),
        "toolPendingBg": _hex(229, 0.05, 0.24),
        "toolSuccessBg": _hex(158, 0.46, 0.25),
        "toolErrorBg": _hex(19, 0.54, 0.25),
        "userMessageBg": _hex(233, 0.41, 0.24),
        "mdCode": violet,
        "mdCodeBlock": green,
        "syntaxKeyword": blue,
        "syntaxFunction": yellow,
        "syntaxString": _hex(52, 0.67, 0.67),
        "syntaxNumber": green,
        "syntaxType": violet,
    }


def _build_light_palette() -> dict[str, str]:
    text = _hex(225, 0.05, 0.27)
    muted = _hex(229, 0.08, 0.47)
    violet = _hex(295, 0.60, 0.46)
    blue = _hex(231, 0.68, 0.47)
    green = _hex(159, 0.75, 0.46)
    red = _hex(20, 0.91, 0.47)
    yellow = _hex(83, 0.99, 0.47)
    return {
        "text": text,
        "muted": muted,
        "violet": violet,
        "blue": blue,
        "green": green,
        "red": red,
        "yellow": yellow,
        "accent": violet,
        "border": _hex(231, 0.67, 0.55),
        "borderAccent": _hex(295, 0.59, 0.55),
        "borderMuted": _hex(235, 0.07, 0.66),
        "success": green,
        "error": red,
        "warning": yellow,
        "dim": _hex(229, 0.07, 0.59),
        "thinkingText": _hex(234, 0.08, 0.55),
        "selectedBg": _hex(235, 0.19, 0.91),
        "toolPendingBg": _hex(248, 0.03, 0.91),
        "toolSuccessBg": _hex(156, 0.21, 0.91),
        "toolErrorBg": _hex(24, 0.23, 0.91),
        "userMessageBg": _hex(235, 0.19, 0.91),
        "mdCode": violet,
        "mdCodeBlock": green,
        "syntaxKeyword": blue,
        "syntaxFunction": yellow,
        "syntaxString": _hex(52, 0.84, 0.46),
        "syntaxNumber": green,
        "syntaxType": violet,
    }


DARK_PALETTE = _build_dark_palette()
LIGHT_PALETTE = _build_light_palette()


# --------------------------------------------------------------------------- #
# Theme
# --------------------------------------------------------------------------- #
class Theme:
    """Maps semantic color names to ANSI truecolor escapes.

    A palette is a mapping of token name to ``#rrggbb``.  :meth:`style` accepts
    a token (or any ``#rrggbb``) as ``fg``/``bg`` and combines it with text
    attributes, matching the role-based coloring Pi uses.
    """

    def __init__(self, palette: Mapping[str, str] | None = None) -> None:
        self.palette = dict(palette if palette is not None else DARK_PALETTE)

    def hex(self, token: str) -> str | None:
        """Return the ``#rrggbb`` for a palette token, or ``None`` if unknown."""
        return self.palette.get(token)

    @staticmethod
    def _rgb(hex_color: str) -> tuple[int, int, int]:
        value = hex_color.lstrip("#")
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]

    def _resolve(self, token: str) -> tuple[int, int, int] | None:
        if token.startswith("#"):
            return self._rgb(token)
        color = self.palette.get(token)
        if color is None:
            return None
        return self._rgb(color)

    def style(
        self,
        text: str,
        *,
        fg: str | None = None,
        bg: str | None = None,
        bold: bool = False,
        dim: bool = False,
        italic: bool = False,
        underline: bool = False,
    ) -> str:
        """Wrap ``text`` in ANSI escapes for the requested styling."""
        codes: list[str] = []
        if bold:
            codes.append("1")
        if dim:
            codes.append("2")
        if italic:
            codes.append("3")
        if underline:
            codes.append("4")
        if fg is not None:
            rgb = self._resolve(fg)
            if rgb is not None:
                codes.append(f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}")
        if bg is not None:
            rgb = self._resolve(bg)
            if rgb is not None:
                codes.append(f"48;2;{rgb[0]};{rgb[1]};{rgb[2]}")
        if not codes:
            return text
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


DARK_THEME = Theme(DARK_PALETTE)
LIGHT_THEME = Theme(LIGHT_PALETTE)


def theme_by_name(name: str | None) -> Theme:
    """Resolve a theme name (``dark``/``light``) to a :class:`Theme`."""
    if name is not None and name.lower() == "light":
        return LIGHT_THEME
    return DARK_THEME
