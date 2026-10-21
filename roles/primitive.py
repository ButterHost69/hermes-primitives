"""Primitive role: reason about one code unit and modify only what is needed.

A :class:`Primitive` is a source file plus the dependency relationships that
bound it.  This is the first role that *changes* the codebase, so unlike the
read-only roles it receives every tool Pi offers — read, grep, find, and ls to
reason about the system, write and edit to apply the local modification, and
bash to run what it changed.  The run goes through a gated Pi session whose
policy allows ``write`` and ``edit`` only against the primitive's own
artifact.  The role returns the assistant's final report: the modification it
applied, or the explicit statement that no local change was necessary.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent import PiError, load_prompt
from utils import verify_json
import json
from agent.gated_client import GatedPiSession, Policy
from agent.tools import BUILTIN_TOOLS

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# The primitive applies the change itself, so it keeps the whole tool catalog:
# reproduction and diagnosis stay read-only, a modification is not.
ALL_TOOLS = tuple(tool.name for tool in BUILTIN_TOOLS)

# The only tools that change the working tree directly.  Everything else —
# read, grep, find, ls, and the bash the primitive runs what it changed with —
# is allowed without a decision.
WRITE_TOOLS = ("write", "edit")


def _resolve_path(path: str, cwd: str | None) -> Path:
    """Resolve ``path`` the way Pi's file tools resolve their arguments."""
    candidate = Path(path)
    if cwd is not None and not candidate.is_absolute():
        candidate = Path(cwd) / candidate
    return candidate.resolve()


def writes_only_to(source_file: str, cwd: str | None = None) -> Policy:
    """Build a primitive's policy: write and edit only its own artifact.

    Requests for every other tool are allowed immediately.  A ``write`` or
    ``edit`` is compared against ``source_file`` after both sides are resolved
    against the session's working directory, so relative paths, ``..``
    segments, and symlinks cannot name another artifact.  A write without a
    usable path is blocked: an unverifiable target is not an allowed one.

    The gate fences the write and edit tools.  A bash command can still change
    another file, so confinement beyond that is the operating system's job.
    """

    def policy(request: dict[str, Any]) -> dict[str, Any]:
        if request.get("toolName") not in WRITE_TOOLS:
            return {"action": "allow"}
        arguments = request.get("input")
        path = arguments.get("path") if isinstance(arguments, dict) else None
        if not isinstance(path, str):
            return {
                "action": "block",
                "reason": "primitive gate: a write without a path cannot be verified",
            }
        base = request.get("cwd") or cwd
        if _resolve_path(path, base) == _resolve_path(source_file, base):
            return {"action": "allow"}
        return {
            "action": "block",
            "reason": (
                f"primitive gate: this primitive may only write {source_file}; "
                f"{path} is another artifact"
            ),
        }

    return policy


@dataclass
class Primitive:
    """One unit of code.

    ``source_file`` is a filesystem path.  ``dep_files`` maps each related
    file to its relationship with this unit and is empty when no
    relationships are known.  ``model``, ``thinking``, ``cwd``, and
    ``observer`` configure the gated Pi session the primitive drives, and
    ``session`` is that session: it is opened when the primitive is created
    and stays attached for every later objective until :meth:`close` is
    called.
    """

    source_file: str
    dep_files: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    thinking: str | None = None
    cwd: str | None = None
    observer: Callable[[dict[str, Any]], None] | None = field(
        default=None, repr=False
    )
    session: GatedPiSession | None = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        """Open the gated Pi session this primitive drives.

        The session is gated: the model is told to touch nothing outside its
        own artifact, and the gate makes that true -- a ``write`` or ``edit``
        anywhere else is blocked before it runs.  Every other tool, including
        bash, stays available because the primitive has to reason about the
        system and run what it changed.
        """
        self.session = GatedPiSession(
            tools=ALL_TOOLS,
            model=self.model,
            thinking=self.thinking,
            cwd=self.cwd,
            observer=self.observer,
            policy=writes_only_to(self.source_file, self.cwd),
        )

    def run(self, issue: str) -> str:
        """Apply ``issue`` to this code unit and return the assistant's report.

        ``issue`` is the objective the primitive must reason about; it is sent
        to the model together with the dependency relationships that bound
        this unit.  The session opened at construction is reused for every
        objective, so ``model``, ``thinking``, ``cwd``, and ``observer`` were
        fixed there and apply to this and every later run.  The session is
        gated by :func:`writes_only_to`, so only ``write`` and ``edit`` against
        ``source_file`` can run.
        """
        if self.session is None:
            raise PiError(f"this primitive is closed: {self.source_file}")
        prompt = load_prompt(PROMPTS_DIR / "primitive.md")
        output = self.session.run(f"{prompt}\n\n{_briefing(issue, self)}")
        check = verify_json(output)
        if not check.ok:
            retry_message = (
                "Your previous response was not valid JSON. "
                "Respond again with only valid JSON matching the required shape "
                "and no other text.\n\n"
                f"The parser reported: {check.error}"
            )
            output = self.session.retry(message=retry_message)
            check = verify_json(output)
        if not check.ok:
            raise PiError("primitive output was not valid JSON after a retry")
        return output

    def close(self) -> None:
        """Close the attached Pi session; the primitive cannot run afterwards."""
        if self.session is not None:
            self.session.close()
            self.session = None


def _dependency_context(target: Primitive) -> list[str]:
    """Render the dependency relationships as briefing lines."""
    if not target.dep_files:
        return ["- no related components are known"]
    return [
        f"- {path}: {relationship}" for path, relationship in target.dep_files.items()
    ]


def _briefing(issue: str, target: Primitive) -> str:
    """Build the per-primitive briefing: component, dependencies, objective."""
    lines = [
        f"Component under change: {target.source_file}",
        "",
        "Dependency Context:",
        *_dependency_context(target),
        "",
        "Objective:",
        issue,
    ]
    return "\n".join(lines)


def _parse_dep(value: str) -> tuple[str, str]:
    """Parse a ``--dep`` entry into a ``(path, relationship)`` pair."""
    path, _, relationship = value.partition("=")
    return path, relationship or "related component"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="primitive",
        description="Modify one code unit according to an objective.",
    )
    parser.add_argument(
        "text",
        nargs="?",
        help="objective to address (reads stdin when omitted)",
    )
    parser.add_argument(
        "--file",
        required=True,
        help="source file the primitive owns",
    )
    parser.add_argument(
        "--dep",
        action="append",
        default=[],
        metavar="PATH=RELATIONSHIP",
        help="declare a dependency relationship; repeatable",
    )
    parser.add_argument(
        "-m",
        "--model",
        metavar="PATTERN",
        help="model id or fuzzy match to use",
    )
    parser.add_argument(
        "--thinking",
        metavar="LEVEL",
        help="reasoning level (off, minimal, low, medium, high, \u2026)",
    )
    parser.add_argument(
        "--cwd",
        metavar="DIR",
        help="working directory for the session",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    text = args.text if args.text is not None else sys.stdin.read()
    try:
        result = Primitive(
            source_file=args.file,
            dep_files=dict(_parse_dep(value) for value in args.dep),
            model=args.model,
            thinking=args.thinking,
            cwd=args.cwd,
        ).run(text)
    except PiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(result)


if __name__ == "__main__":  # pragma: no cover
    main()
