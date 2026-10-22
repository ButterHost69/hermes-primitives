"""Materialize a SWE-bench instance and turn edits into a patch.

SWE-bench publishes tasks as ``repo`` + ``base_commit``.  Evaluation only ever
needs the patch, so a run does not need the repository's history: the base
commit's GitHub tarball is extracted into a scratch directory, committed as a
local baseline, and every change the agent makes is diffed against that
baseline.  This keeps a task checkout to a few megabytes instead of the full
clone, which matters when one host runs many tasks.

The generated patch is checked against a detached worktree of the baseline
before it is submitted, so a malformed diff fails here rather than inside the
evaluation harness.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from benchmarks.dataset import Instance

#: Patterns an agent or its tooling leaves behind; they never belong in a
#: patch.  They are also written to ``.git/info/exclude`` before the run.
ARTIFACT_PATTERNS = (
    ".sessions/",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".venv/",
    ".tox/",
    ".nox/",
    "htmlcov/",
    "*.egg-info/",
    ".coverage",
)

#: Artifact directories removed from the tree before the diff is taken.
_ARTIFACT_DIRS = (
    ".sessions",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    ".tox",
    ".nox",
    "htmlcov",
)

_DOWNLOAD_TIMEOUT = 120.0


class RepoError(RuntimeError):
    """The instance checkout or its patch could not be produced."""


def tarball_url(instance: Instance) -> str:
    """The GitHub archive URL for an instance's base commit."""
    return (
        f"https://codeload.github.com/{instance.repo}"
        f"/tar.gz/{instance.base_commit}"
    )


def repo_path(work_root: Path, instance: Instance) -> Path:
    """The checkout path a run uses for ``instance``."""
    return Path(work_root) / "instances" / instance.instance_id / "repo"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one git command in ``repo``."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo),
            text=True,
            capture_output=True,
            check=check,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise RepoError("git is required for SWE-bench runs") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RepoError(f"git {' '.join(args)} failed: {detail}") from exc


def baseline_sha(repo: Path) -> str:
    """The commit of the pristine checkout."""
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _download(url: str, target: Path) -> None:
    """Download ``url`` to ``target`` through a temporary file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(
        url, headers={"User-Agent": "hms-swebench/0.1"}
    )
    try:
        with (
            urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT) as response,
            temporary.open("wb") as handle,
        ):
            shutil.copyfileobj(response, handle)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise RepoError(f"could not download {url}: {exc}") from exc
    if temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        raise RepoError(f"downloaded an empty archive from {url}")
    temporary.replace(target)


def _extract(archive: Path, destination: Path) -> Path:
    """Extract a GitHub tarball and return its single top-level directory."""
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            # "data" rejects absolute paths, parent traversal, devices, and
            # links outside the archive -- the archive is remote input.
            bundle.extractall(destination, filter="data")
    except (OSError, tarfile.TarError) as exc:
        raise RepoError(f"could not extract {archive}: {exc}") from exc
    entries = [entry for entry in destination.iterdir() if entry.is_dir()]
    if len(entries) != 1:
        raise RepoError(
            f"expected one top-level directory in {archive}, found {len(entries)}"
        )
    return entries[0]


def _init_baseline(checkout: Path) -> None:
    """Turn an extracted tree into a git repository with one baseline commit."""
    _git(checkout, "init", "-q")
    _git(checkout, "config", "core.fileMode", "false")
    _git(checkout, "config", "user.email", "hms@example.invalid")
    _git(checkout, "config", "user.name", "hms")
    _git(checkout, "add", "-A", "-f")
    _git(checkout, "commit", "-q", "-m", "hms: SWE-bench baseline")


def _materialize(instance: Instance, checkout: Path, work_root: Path) -> None:
    """Download, extract, and initialize ``checkout`` for ``instance``."""
    archive = (
        Path(work_root)
        / "cache"
        / "tarballs"
        / f"{instance.instance_id}@{instance.base_commit}.tar.gz"
    )
    if not archive.is_file():
        _download(tarball_url(instance), archive)

    staging = checkout.parent / f".extract-{instance.instance_id}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        top = _extract(archive, staging)
        checkout.parent.mkdir(parents=True, exist_ok=True)
        if checkout.exists():
            shutil.rmtree(checkout)
        shutil.move(str(top), str(checkout))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _init_baseline(checkout)


def _write_excludes(checkout: Path) -> None:
    """Teach the local repository to ignore agent-owned artifacts."""
    exclude = checkout / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    lines = existing.splitlines()
    for pattern in ARTIFACT_PATTERNS:
        if pattern not in lines:
            lines.append(pattern)
    exclude.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _remove_artifacts(checkout: Path) -> None:
    """Remove run artifacts that would otherwise leak into a patch."""
    for name in _ARTIFACT_DIRS:
        target = checkout / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    for root, directories, files in os.walk(checkout):
        if ".git" in directories:
            directories.remove(".git")
        if "__pycache__" in directories:
            shutil.rmtree(Path(root) / "__pycache__", ignore_errors=True)
            directories.remove("__pycache__")
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                Path(root, name).unlink(missing_ok=True)
    (checkout / ".coverage").unlink(missing_ok=True)


def prepare_repo(
    instance: Instance, work_root: Path, *, refresh: bool = False
) -> tuple[Path, str]:
    """Return ``(checkout, baseline_sha)`` for ``instance``, reset to pristine.

    A checkout that already exists is reset and cleaned, so re-running an
    instance starts from its base commit rather than from a previous agent's
    work.  ``refresh`` also re-materializes the checkout from the cached
    archive.
    """
    checkout = repo_path(work_root, instance)
    if refresh and checkout.exists():
        if checkout.is_dir():
            shutil.rmtree(checkout)
        else:
            checkout.unlink()

    if checkout.is_dir() and (checkout / ".git").is_dir():
        _git(checkout, "reset", "--hard", "-q")
        _git(checkout, "clean", "-fdx", "-q")
    else:
        if checkout.exists():
            if checkout.is_dir():
                shutil.rmtree(checkout)
            else:
                checkout.unlink()
        _materialize(instance, checkout, Path(work_root))

    baseline = baseline_sha(checkout)
    _write_excludes(checkout)
    return checkout, baseline


def capture_patch(checkout: Path, baseline: str) -> str:
    """Return the working tree's changes against ``baseline`` as a patch.

    Untracked source files are included (via git's intent-to-add), while the
    artifact patterns in :data:`ARTIFACT_PATTERNS` are not.
    """
    _remove_artifacts(checkout)
    _git(checkout, "add", "-A", "-N", "--", ".")
    return _git(checkout, "diff", "--binary", "--no-color", baseline).stdout


def check_patch(checkout: Path, baseline: str, patch: str) -> str | None:
    """Check that ``patch`` applies to the baseline; return an error or ``None``.

    The check runs in a detached worktree, so a failed application cannot
    touch the checkout the agent worked in.
    """
    if not patch.strip():
        return None
    holder = Path(tempfile.mkdtemp(prefix="hms-swe-check-"))
    worktree = holder / "tree"
    patch_file = holder / "candidate.diff"
    patch_file.write_text(patch, encoding="utf-8")
    try:
        _git(checkout, "worktree", "add", "--detach", "--quiet", str(worktree), baseline)
        result = subprocess.run(
            ["git", "apply", "--check", "--binary", "--whitespace=nowarn", str(patch_file)],
            cwd=str(worktree),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return None
        return (result.stderr or result.stdout or "git apply --check failed").strip()
    except RepoError as exc:
        return str(exc)
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=str(checkout),
            text=True,
            capture_output=True,
            check=False,
        )
        shutil.rmtree(holder, ignore_errors=True)


__all__ = [
    "ARTIFACT_PATTERNS",
    "RepoError",
    "baseline_sha",
    "capture_patch",
    "check_patch",
    "prepare_repo",
    "repo_path",
    "tarball_url",
]
