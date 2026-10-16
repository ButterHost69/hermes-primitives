"""Computing the candidate diff: what the pipeline actually changed.

The candidate diff is the grader's most important input -- the oracle suite
says *whether* a fix works, and the diff says *what the fix did*.  It is
produced as ``git diff --no-index`` between the pristine baseline tree and
the live tree, with build artifacts and session logs excluded.  When git is
unavailable or refuses the comparison, a :mod:`difflib` diff over the same
two trees is produced instead, so the grader always receives something to
read.
"""

from __future__ import annotations

import difflib
import shutil
import subprocess
import tempfile
from pathlib import Path

# Never part of a candidate diff: caches, compiled files, pipeline session
# logs, dependencies, and the environments' own bookkeeping.
IGNORE_PATTERNS = (
    "__pycache__",
    "*.py[co]",
    ".pytest_cache",
    ".sessions",
    "node_modules",
    ".coverage",
    ".git",
    ".venv",
)

# git's exit codes for --no-index: 0 means identical, 1 means differences,
# anything else is a real failure.
_GIT_IDENTICAL = 0
_GIT_DIFFERENT = 1


def pruned_copy(tree: Path, name: str, scratch: Path) -> Path:
    """Copy ``tree`` into ``scratch/name``, skipping :data:`IGNORE_PATTERNS`.

    Both trees are copied into one scratch directory and compared there, so
    the diff headers carry the plain ``baseline/`` and ``live/`` names rather
    than machine temp paths.
    """
    return Path(
        shutil.copytree(
            tree,
            scratch / name,
            ignore=shutil.ignore_patterns(*IGNORE_PATTERNS),
        )
    )


def _same_tree(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:  # pragma: no cover - unresolvable paths are not the same
        return False


def _git_diff(scratch: Path) -> str | None:
    """``git diff --no-index`` the two copies, or ``None`` if git cannot."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--no-index", "--", "baseline", "live"],
            cwd=str(scratch),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, ValueError):
        return None
    if proc.returncode not in (_GIT_IDENTICAL, _GIT_DIFFERENT):
        return None
    return proc.stdout


def _difflib_diff(baseline: Path, live: Path) -> str:
    """A unified diff of the two copies, built without git."""
    chunks: list[str] = []
    for path in sorted(live.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(live)
        old = baseline / relative
        old_lines = (
            old.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            if old.is_file()
            else []
        )
        new_lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(
                old_lines, new_lines, fromfile=f"a/{relative}", tofile=f"b/{relative}"
            )
        )
    for path in sorted(baseline.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(baseline)
        if not (live / relative).exists():
            old_lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            chunks.extend(
                difflib.unified_diff(
                    old_lines, [], fromfile=f"a/{relative}", tofile=f"b/{relative}"
                )
            )
    return "".join(chunks)


def candidate_diff(baseline: Path | None, live: Path) -> str | None:
    """Diff the live tree against the baseline, or ``None`` without one.

    ``None`` means "no baseline was available", which the grader is told
    explicitly; an empty string means "the candidate changed nothing".
    """
    if baseline is None or not baseline.is_dir():
        return None
    if _same_tree(baseline, live):
        return ""
    with tempfile.TemporaryDirectory(prefix="hms-eval-diff-") as scratch:
        base_copy = pruned_copy(baseline, "baseline", Path(scratch))
        live_copy = pruned_copy(live, "live", Path(scratch))
        diff = _git_diff(Path(scratch))
        if diff is not None:
            return diff
        return _difflib_diff(base_copy, live_copy)
