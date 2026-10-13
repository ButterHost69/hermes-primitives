"""Reproduce role: turn a bug report into a failing regression test.

Runs Pi with write-capable tools *enabled* (the job is to create a test file
and run it) and prompts it through the ``agent`` package, retrying once if the
model returns something that is not valid JSON.

Unlike ``triage`` and ``diagnose``, this role must be able to write files and
execute commands, so ``write``, ``edit`` and ``bash`` are enabled.  The prompt
requires the model to observe the test *fail* before reporting a successful
reproduction, so a reproduction that silently passes is rejected.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from agent import PiError, PiSession, load_prompt
from utils import verify_json

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Reproduce must inspect, write a test, and run it.  It is the only role that
# needs the write-capable tools: ``write``/``edit`` to create the test and
# ``bash`` to execute it.
REPRO_TOOLS = ("read", "write", "edit", "bash", "grep", "find", "ls")


def reproduce(
    text: str,
    *,
    model: str | None = None,
    thinking: str | None = None,
    cwd: str | None = None,
    observer: Callable[[dict[str, Any]], None] | None = None,
    context: str | None = None,
) -> dict:
    """Reproduce ``text`` (a bug report) and return the model's JSON verdict.

    ``observer``, when given, is registered as an extra event listener on the
    session's RPC client so callers can stream progress without changing the
    synchronous request/response contract.  ``context`` is appended to the
    role prompt as optional operator guidance.
    """
    prompt = load_prompt(PROMPTS_DIR / "reproduce.md")
    if context:
        prompt += "\n\n" + context
    with PiSession(
        tools=REPRO_TOOLS,
        model=model,
        thinking=thinking,
        cwd=cwd,
        observer=observer,
    ) as session:
        output = session.run(prompt + "\n\n" + text)
        check = verify_json(output)
        if not check.ok:
            output = session.retry(error=check.error)
            check = verify_json(output)
        if not check.ok:
            raise PiError("reproduce output was not valid JSON after a retry")
        return check.value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reproduce",
        description="Reproduce a bug and write a failing regression test.",
    )
    parser.add_argument(
        "text",
        nargs="?",
        help="bug report to reproduce (reads stdin when omitted)",
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
        result = reproduce(
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
