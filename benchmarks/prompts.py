"""Prompts shared by the SWE-bench runners."""

from __future__ import annotations

from pathlib import Path

from agent import load_prompt
from benchmarks.dataset import Instance

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def agent_prompt(instance: Instance) -> str:
    """The one-turn prompt used by the plain-agent runner."""
    template = load_prompt(PROMPTS_DIR / "swebench.md")
    template = (
        template.replace("__REPO__", instance.repo)
        .replace("__BASE_COMMIT__", instance.base_commit)
        .replace("__INSTANCE_ID__", instance.instance_id)
    )
    return f"{template}\n\nIssue:\n{instance.problem_statement}"


def benchmark_context(instance: Instance) -> str:
    """The constraints appended to every pipeline role's prompt."""
    return (
        f"SWE-bench task {instance.instance_id} ({instance.repo} at "
        f"{instance.base_commit}).\n"
        "This is a benchmark run: hidden oracle tests judge the final tree.\n"
        "Constraints: stay inside the repository (never search or read /, "
        "$HOME, or other paths outside the working tree; use relative paths), "
        "do not modify tests, do not install packages, do not access the "
        "network, and keep the change minimal. Do not use assert for input "
        "validation or a user-facing error: raise an explicit exception such "
        "as ValueError, and update related validation paths consistently. If "
        "the project's dependencies are not installed, reason from the source "
        "instead of installing them."
    )


__all__ = ["agent_prompt", "benchmark_context"]
