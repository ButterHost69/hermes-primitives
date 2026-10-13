"""A simple terminal interface for interacting with the Pi coding agent."""

from .client import PiClient, PiError
from .prompts import PromptLibrary, load_prompt
from .session import PiSession

__version__ = "0.1.0"

__all__ = ["PiClient", "PiError", "PiSession", "PromptLibrary", "load_prompt"]
