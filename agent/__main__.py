"""Command-line entry point for the Pi interface.

Run with::

    python -m agent                 # interactive session
    python -m agent --select-tools  # pick tools before starting
    python -m agent -m claude -t read,bash,edit,write
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .client import PiClient, PiError
from .interface import Interface
from .prompts import PromptLibrary
from .tools import default_tools, normalize_tools

DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="A simple terminal interface for interacting with the Pi coding agent.",
    )
    parser.add_argument(
        "-t",
        "--tools",
        metavar="LIST",
        help="comma-separated tool names, or 'default'/'off' (default: read,bash,edit,write)",
    )
    parser.add_argument(
        "--select-tools",
        action="store_true",
        help="interactively choose tools before starting",
    )
    parser.add_argument(
        "-m",
        "--model",
        metavar="PATTERN",
        help="initial model (exact id or fuzzy match)",
    )
    parser.add_argument(
        "--thinking",
        metavar="LEVEL",
        help="initial reasoning level (off, minimal, low, medium, high, \u2026)",
    )
    parser.add_argument(
        "--cwd",
        metavar="DIR",
        help="working directory for the session (default: current directory)",
    )
    parser.add_argument(
        "--prompts-dir",
        metavar="DIR",
        default=str(DEFAULT_PROMPTS_DIR),
        help=f"custom prompt library directory (default: {DEFAULT_PROMPTS_DIR})",
    )
    parser.add_argument(
        "-s",
        "--session",
        action="store_true",
        help="persist the session to disk (default: in-memory only)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable colored output",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"agent {__version__}",
    )
    return parser


def build_client(args: argparse.Namespace) -> PiClient:
    """Construct and start a PiClient from parsed options."""
    if args.tools:
        if args.tools.strip().lower() in ("default",):
            tools = default_tools()
        elif args.tools.strip().lower() in ("off", "none"):
            tools = []
        else:
            tools = normalize_tools(args.tools.split(","))
    else:
        tools = default_tools()

    client = PiClient(
        tools=tools,
        model=args.model or None,
        thinking=args.thinking or None,
        cwd=args.cwd or os.getcwd(),
        persistent_session=bool(args.session),
    )
    try:
        client.start()
    except PiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    return client


def run_interface(client: PiClient, args: argparse.Namespace | None = None) -> None:
    """Attach an Interface to a started client and run the REPL."""
    prompts_dir = Path(args.prompts_dir) if args is not None else DEFAULT_PROMPTS_DIR
    interface = Interface(
        client,
        PromptLibrary(prompts_dir),
        color=None if args is None or not args.no_color else False,
    )
    if args is not None and args.select_tools:
        interface._cmd_tools("menu")
    interface.run()
    return interface


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    client = build_client(args)
    try:
        run_interface(client, args)
    finally:
        client.close()


if __name__ == "__main__":  # pragma: no cover - exercised by tests/test_main.py::test_module_run_as_main
    main()
