"""Interactive terminal UI for the hms pipeline.

Wraps :class:`~pipeline.Pipeline` in a live, Pi-styled screen that streams
each role's progress and pauses for input only where a human must type.
"""

from .app import PipelineApp
from .replay import ReplayApp
from .screen import Terminal
from .theme import DARK_THEME, LIGHT_THEME, Theme, theme_by_name
from .transcript import Transcript

__all__ = [
    "DARK_THEME",
    "LIGHT_THEME",
    "PipelineApp",
    "ReplayApp",
    "Terminal",
    "Theme",
    "Transcript",
    "theme_by_name",
]
