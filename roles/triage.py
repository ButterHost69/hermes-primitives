"""Triage role: classify an input message into a JSON verdict.

Runs Pi with write-capable tools disabled and prompts it through the
``agent`` package, retrying once if the model returns something that is not
valid JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent import PiError, PiSession, load_prompt
from utils import verify_json

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Triage must only inspect, never modify. ``write``, ``edit`` and ``bash``
# are all capable of changing the working tree, so they are disabled here.
READ_ONLY_TOOLS = ("read", "grep", "find", "ls")


def triage(
    text: str,
    *,
    model: str | None = None,
    thinking: str | None = None,
    cwd: str | None = None,
) -> dict:
    """Classify ``text`` and return the model's JSON verdict."""
    prompt = load_prompt(PROMPTS_DIR / "triage.md")
    with PiSession(
        tools=READ_ONLY_TOOLS,
        model=model,
        thinking=thinking,
        cwd=cwd,
    ) as session:
        output = session.run(prompt + "\n\n" + text)
        check = verify_json(output)
        if not check.ok:
            output = session.retry(error=check.error)
            check = verify_json(output)
        if not check.ok:
            raise PiError("triage output was not valid JSON after a retry")
        return check.value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="triage",
        description="Classify a message into a JSON triage verdict.",
    )
    parser.add_argument(
        "text",
        nargs="?",
        help="message to triage (reads stdin when omitted)",
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
        result = triage(
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
