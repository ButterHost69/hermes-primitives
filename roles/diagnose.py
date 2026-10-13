"""Diagnose role: turn an error trace into a JSON diagnosis.

Runs Pi with write-capable tools disabled and prompts it through the
``agent`` package, retrying once if the model returns something that is not
valid JSON or that references file paths which do not resolve.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from agent import PiError, PiSession, load_prompt
from utils import missing_paths, verify_json

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Diagnose must only inspect, never modify. ``write``, ``edit`` and ``bash``
# are all capable of changing the working tree, so they are disabled here.
READ_ONLY_TOOLS = ("read", "grep", "find", "ls")


def _finding_files(value: Any) -> list[str]:
    """Extract the ``file`` paths from a parsed diagnosis."""
    if not isinstance(value, dict):
        return []
    findings = value.get("findings")
    if not isinstance(findings, list):
        return []
    files: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        file = finding.get("file")
        if isinstance(file, str):
            files.append(file)
    return files


def _missing_finding_files(value: Any, *, cwd: str | None) -> list[str]:
    """Return the finding paths that do not resolve under ``cwd``."""
    return missing_paths(_finding_files(value), cwd=cwd)


def _path_retry_message(missing: list[str]) -> str:
    """Build the corrective message for paths that do not resolve."""
    lines = "\n".join(f"- {path}" for path in missing)
    return (
        "Your previous response referenced files that do not exist. "
        "Respond again with only valid JSON and no other text.\n\n"
        f"These paths do not resolve to files in the working directory:\n{lines}"
    )


def _path_error(missing: list[str]) -> str:
    """Build the final error message for paths that still do not resolve."""
    return "diagnose referenced paths that do not resolve to files: " + ", ".join(
        missing
    )


def diagnose(
    text: str,
    *,
    model: str | None = None,
    thinking: str | None = None,
    cwd: str | None = None,
    observer: Callable[[dict[str, Any]], None] | None = None,
    context: str | None = None,
) -> dict:
    """Diagnose ``text`` (an error trace) and return the model's JSON verdict.

    ``observer``, when given, is registered as an extra event listener on the
    session's RPC client so callers can stream progress without changing the
    synchronous request/response contract.  ``context`` is appended to the
    role prompt as optional operator guidance.
    """
    prompt = load_prompt(PROMPTS_DIR / "diagnose.md")
    if context:
        prompt += "\n\n" + context
    with PiSession(
        tools=READ_ONLY_TOOLS,
        model=model,
        thinking=thinking,
        cwd=cwd,
        observer=observer,
    ) as session:
        output = session.run(prompt + "\n\n" + text)
        check = verify_json(output)
        missing = _missing_finding_files(check.value, cwd=cwd) if check.ok else []

        if not check.ok:
            output = session.retry(error=check.error)
            check = verify_json(output)
            missing = _missing_finding_files(check.value, cwd=cwd) if check.ok else []
        elif missing:
            output = session.retry(message=_path_retry_message(missing))
            check = verify_json(output)
            missing = _missing_finding_files(check.value, cwd=cwd) if check.ok else []

        if not check.ok:
            raise PiError("diagnose output was not valid JSON after a retry")
        if missing:
            raise PiError(_path_error(missing))

        return check.value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="diagnose",
        description="Diagnose an error trace into a JSON list of fixes.",
    )
    parser.add_argument(
        "text",
        nargs="?",
        help="error trace to diagnose (reads stdin when omitted)",
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
        result = diagnose(
            text,
            model=args.model,
            thinking=args.thinking,
            cwd=args.cwd,
        )
    except PiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
