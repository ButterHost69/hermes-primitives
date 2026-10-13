"""Helpers for checking whether file paths actually resolve."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def path_resolves(path: str | Path, *, cwd: str | Path | None = None) -> bool:
    """Return ``True`` when ``path`` names an existing file.

    Relative paths are resolved against ``cwd`` (or the current working
    directory when ``cwd`` is ``None``).  Absolute paths are used as-is.
    """
    p = Path(path)
    if cwd is not None and not p.is_absolute():
        p = Path(cwd) / p
    return p.is_file()


def missing_paths(
    paths: Iterable[str],
    *,
    cwd: str | Path | None = None,
) -> list[str]:
    """Return the paths from ``paths`` that do not resolve to existing files.

    Each missing path is returned unchanged so callers can report exactly what
    was provided.
    """
    return [path for path in paths if not path_resolves(path, cwd=cwd)]
