"""Repository-scoped tool policy for SWE-bench runs.

An agent that is told to "stay inside the repository" does not stay inside it.
On the first host run, diagnose searched ``/home`` for the fix and reproduce
ran ``find /`` for dependencies.  A prompt is not a sandbox, so the benchmark
uses the tool gate the project already has: every file tool's path is resolved
against the checkout, and a path outside it is blocked before execution.

The policy is deliberately strict in host mode: the shell and ``codemode`` are
disabled because this checkout has no project environment and a shell can
escape any path check.  A containerized run can opt back into the shell with
``allow_shell``; the file-tool checks still apply.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.gated_client import Policy

SHELL_TOOLS = ("bash", "powershell", "codemode")
READ_TOOLS = ("read", "grep", "find", "ls")
WRITE_TOOLS = ("write", "edit")
PATH_KEYS = ("path", "file", "directory", "dir")
ALLOWED_TOOLS = frozenset((*READ_TOOLS, *WRITE_TOOLS, "tool_search"))


def _within(root: Path, path: Path) -> bool:
    """Whether ``path`` is ``root`` or lives under it."""
    return path == root or root in path.parents


def _resolve(value: str, *, cwd: str | None, root: Path) -> Path | None:
    """Resolve a tool argument, treating relative paths as repository-relative."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path(cwd or root) / candidate
    try:
        return candidate.resolve()
    except OSError:  # pragma: no cover - unresolvable symlink loops
        return None


def repo_policy(root: str | Path, *, allow_shell: bool = False) -> Policy:
    """Build a policy that confines the file tools to ``root``.

    With ``allow_shell`` false, ``bash``, ``powershell`` and ``codemode`` are
    blocked outright: a shell command can leave the checkout by many routes,
    and in a host checkout it has no project environment to run anyway.  Every
    known file tool is checked against its path argument; a missing path means
    the tool's default working directory, which is the checkout.
    """
    root_path = Path(root).resolve()

    def policy(request: dict[str, Any]) -> dict[str, Any]:
        tool = str(request.get("toolName") or "")
        if tool in SHELL_TOOLS:
            if allow_shell:
                return {"action": "allow"}
            return {
                "action": "block",
                "reason": (
                    "benchmark: the shell is disabled in the host checkout "
                    "(no project environment); reason from the repository source"
                ),
            }
        if tool not in ALLOWED_TOOLS:
            return {
                "action": "block",
                "reason": f"benchmark: tool {tool!r} is not allowed in this run",
            }
        arguments = request.get("input")
        if not isinstance(arguments, dict):
            arguments = {}
        if tool in WRITE_TOOLS and not isinstance(arguments.get("path"), str):
            return {
                "action": "block",
                "reason": "benchmark: a write without a path cannot be verified",
            }
        cwd = request.get("cwd")
        for key in PATH_KEYS:
            value = arguments.get(key)
            if not isinstance(value, str) or not value:
                continue
            resolved = _resolve(value, cwd=cwd if isinstance(cwd, str) else None, root=root_path)
            if resolved is None or not _within(root_path, resolved):
                return {
                    "action": "block",
                    "reason": (
                        f"benchmark: {tool} may only use paths inside the "
                        f"repository; {value} is outside it"
                    ),
                }
        return {"action": "allow"}

    return policy


__all__ = [
    "ALLOWED_TOOLS",
    "PATH_KEYS",
    "READ_TOOLS",
    "SHELL_TOOLS",
    "WRITE_TOOLS",
    "repo_policy",
]
