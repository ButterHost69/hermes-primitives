"""Built-in tool catalog and selection helpers.

The tool names and descriptions mirror Pi's built-in tools.  Tools are
configured on the RPC subprocess at startup via ``--tools``, since the RPC
protocol has no mid-session tool-selection command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Tool:
    """A selectable built-in tool."""

    name: str
    description: str
    default: bool = False


BUILTIN_TOOLS: tuple[Tool, ...] = (
    Tool("read", "Read text files and supported images", default=True),
    Tool("bash", "Run shell commands", default=True),
    Tool("edit", "Apply exact text replacements to an existing file", default=True),
    Tool("write", "Create or overwrite a file", default=True),
    Tool("grep", "Search file contents"),
    Tool("find", "Find paths using glob patterns"),
    Tool("ls", "List directory contents"),
    Tool("powershell", "Run PowerShell commands (Windows)"),
    Tool("codemode", "Run JavaScript that calls the other tools"),
    Tool("tool_search", "Search tools not declared to the model"),
)

TOOL_BY_NAME = {t.name: t for t in BUILTIN_TOOLS}


def default_tools() -> list[str]:
    """Return the default enabled tool names."""
    return [t.name for t in BUILTIN_TOOLS if t.default]


def normalize_tools(names: Iterable[str]) -> list[str]:
    """Validate and de-duplicate a tool-name sequence, preserving order.

    Unknown names are ignored so a stray name never breaks startup; callers
    can diff the result against their input to report the unknown ones.
    """
    seen: set[str] = set()
    result: list[str] = []
    for raw in names:
        name = raw.strip().lower()
        if name and name in TOOL_BY_NAME and name not in seen:
            seen.add(name)
            result.append(name)
    return result
