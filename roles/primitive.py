"""Primitive role: reason about one code unit and modify only what is needed.

A :class:`Primitive` is a source file plus the dependency relationships that
bound it.  This is the first role that *changes* the codebase, so unlike the
read-only roles it receives every tool Pi offers — read, grep, find, and ls to
reason about the system, write and edit to apply the local modification, and
bash to run what it changed.  The model is instructed to touch nothing
outside the primitive's own artifact.  The role returns the assistant's final
report: the modification it applied, or the explicit statement that no local
change was necessary.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent import PiError, PiSession, load_prompt
from agent.tools import BUILTIN_TOOLS

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# The primitive applies the change itself, so it keeps the whole tool catalog:
# reproduction and diagnosis stay read-only, a modification is not.
ALL_TOOLS = tuple(tool.name for tool in BUILTIN_TOOLS)


@dataclass
class Primitive:
    """One unit of code.

    ``source_file`` is a filesystem path.  ``dep_files`` maps each related
    file to its relationship with this unit and is empty when no
    relationships are known.  ``session`` is the Pi session the primitive
    reuses across objectives: the role opens it on first use and it stays
    attached until :meth:`close` is called.
    """

    source_file: str
    dep_files: dict[str, str] = field(default_factory=dict)
    session: PiSession | None = None

    def attach_session(self, session: PiSession) -> None:
        """Attach the Pi session this primitive reuses for its objectives.

        A primitive outlives a single objective: it keeps one Pi subprocess
        attached and reuses it for every later modification instead of
        spawning a new one per role call.  Re-attaching a different session
        closes the previous one so no subprocess is leaked.
        """
        if self.session is not None and self.session is not session:
            self.session.close()
        self.session = session

    def close(self) -> None:
        """Close the attached Pi session; the next objective opens a new one."""
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


def _session_for(
    target: Primitive,
    *,
    model: str | None,
    thinking: str | None,
    cwd: str | None,
    observer: Callable[[dict[str, Any]], None] | None,
) -> PiSession:
    """Return the primitive's Pi session, opening it on first use."""
    if target.session is None:
        target.attach_session(
            PiSession(
                tools=ALL_TOOLS,
                model=model,
                thinking=thinking,
                cwd=cwd,
                observer=observer,
            )
        )
    return target.session


def primitive(
    issue: str,
    target: Primitive,
    *,
    model: str | None = None,
    thinking: str | None = None,
    cwd: str | None = None,
    observer: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """Apply ``issue`` to one code unit and return the assistant's report.

    ``issue`` is the objective the primitive must reason about; ``target`` is
    the unit itself, sent to the model together with its dependency
    relationships.  The Pi session stays attached to ``target`` so later
    objectives on the same primitive reuse it: ``model``, ``thinking``,
    ``cwd``, and ``observer`` only apply when that session is first opened.
    ``observer``, when given, is registered on the session's RPC client as an
    extra event listener so callers can stream progress without changing the
    synchronous request/response contract.
    """
    prompt = load_prompt(PROMPTS_DIR / "primitive.md")
    session = _session_for(
        target, model=model, thinking=thinking, cwd=cwd, observer=observer
    )
    return session.run(f"{prompt}\n\n{_briefing(issue, target)}")


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
        result = primitive(
            text,
            Primitive(
                source_file=args.file,
                dep_files=dict(_parse_dep(value) for value in args.dep),
            ),
            model=args.model,
            thinking=args.thinking,
            cwd=args.cwd,
        )
    except PiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(result)


if __name__ == "__main__":  # pragma: no cover
    main()
