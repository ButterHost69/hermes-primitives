"""Command-line entry point for the interactive pipeline TUI.

Run with::

    python -m tui /path/to/codebase
    python -m tui /path/to/codebase --model claude --thinking low --theme dark
"""

from __future__ import annotations

import argparse
import os
import sys

from pipeline import Pipeline

from .app import PipelineApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tui",
        description="Run the hms pipeline in an interactive, Pi-styled terminal UI.",
    )
    parser.add_argument(
        "codebase",
        help="path to the codebase the pipeline will work on",
    )
    parser.add_argument(
        "-m",
        "--model",
        metavar="PATTERN",
        default="claude",
        help="model id or fuzzy match (default: claude)",
    )
    parser.add_argument(
        "--thinking",
        metavar="LEVEL",
        default="low",
        help="reasoning level (off, minimal, low, medium, high, …; default: low)",
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        default=os.environ.get("HMS_API_KEY", ""),
        help="API key for roles that call a model API directly (default: $HMS_API_KEY)",
    )
    parser.add_argument(
        "--theme",
        metavar="NAME",
        choices=("dark", "light"),
        default="dark",
        help="color theme (default: dark, matching Pi's dark theme)",
    )
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="hide the model's thinking stream",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable colored output",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    pipeline = Pipeline(
        args.codebase,
        args.model,
        args.thinking,
        args.api_key,
    )
    app = PipelineApp(
        pipeline,
        theme=args.theme,
        color=False if args.no_color else None,
        show_thinking=not args.no_thinking,
    )
    results = app.run()
    sys.stdout.write("\n")
    for name, result in results.items():
        sys.stdout.write(f"{name}: {result}\n")


if __name__ == "__main__":  # pragma: no cover
    main()
