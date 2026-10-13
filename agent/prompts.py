"""A small library of reusable custom prompts.

Prompts are stored as UTF-8 text files in a directory (one file per prompt).
This keeps them editable outside the interface and diff-friendly.
"""

from __future__ import annotations

import re
from pathlib import Path


def load_prompt(path: Path | str) -> str:
    """Load a prompt from a Markdown file and return its text.

    Surrounding whitespace is stripped so the prompt can be sent to the
    model as-is.
    """
    return Path(path).read_text(encoding="utf-8").strip()


class PromptLibrary:
    """Read/write named prompt templates in a directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    # -- file naming -----------------------------------------------------
    @staticmethod
    def _filename(name: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-")
        return (safe or "prompt") + ".md"

    def _path(self, name: str) -> Path:
        return self.directory / self._filename(name)

    # -- operations ------------------------------------------------------
    def list(self) -> list[str]:
        """Return prompt names, sorted."""
        return sorted(p.stem for p in self.directory.glob("*.md"))

    def get(self, name: str) -> str | None:
        path = self._path(name)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def save(self, name: str, text: str) -> Path:
        path = self._path(name)
        path.write_text(text.strip() + "\n", encoding="utf-8")
        return path

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.exists():
            return False
        path.unlink()
        return True

    def resolve(self, name: str) -> tuple[str, str] | None:
        """Resolve a possibly-partial name to (name, text).

        Matches an exact name first, then a unique prefix/contains match.
        """
        names = self.list()
        if name in names:
            return name, self.get(name) or ""
        candidates = [n for n in names if n.startswith(name) or name in n]
        if len(candidates) == 1:
            return candidates[0], self.get(candidates[0]) or ""
        return None
