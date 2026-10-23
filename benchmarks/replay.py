"""Load and replay a saved ``pi-swebench`` transcript.

A run directory holds one transcript per task.  This module selects the task,
filters the raw Pi stream down to the records the TUI renders, and hands them
to :class:`tui.replay.ReplayApp`.

Usage::

    pi-swebench replay .swebench/runs/swe-pass-3 --instance psf__requests-2674
    pi-swebench replay .swebench/runs/swe-pass-4/instances/psf__requests-2148

The replay is read-only: it never starts a model, Docker, or a test suite.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tui.replay import ReplayApp


class ReplayError(RuntimeError):
    """A transcript or run directory could not be selected or read."""


# Record types the live TUI translates into something visible.  Everything
# else (deltas of tool-call arguments, response records, internal Pi entries)
# is skipped unless --all-events is given.
_VISIBLE_TYPES = frozenset(
    {
        "tool_execution_start",
        "tool_execution_end",
        "agent_settled",
        "auto_retry_start",
        "primitives_fanned_out",
        "primitive_reported",
        "primitive_failed",
    }
)


@dataclass(frozen=True)
class ReplaySelection:
    """The transcript a replay will show, with the run's configuration."""

    run_dir: Path
    instance_id: str
    transcript: Path
    model: str = "?"
    thinking: str = "?"
    mode: str = "pipeline"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _is_visible(record: dict[str, Any], *, show_thinking: bool) -> bool:
    """Whether a raw record contributes to the transcript/panel view."""
    rtype = record.get("type")
    if rtype == "message_update":
        event = record.get("assistantMessageEvent") or {}
        kind = event.get("type")
        if kind == "thinking_delta":
            return show_thinking
        return kind == "text_delta"
    return rtype in _VISIBLE_TYPES


def load_records(
    path: Path,
    *,
    include_all: bool = False,
    show_thinking: bool = True,
) -> list[dict[str, Any]]:
    """Read a transcript JSONL file.

    By default only records the TUI renders are kept; ``include_all`` keeps the
    whole raw stream.  A malformed line is an error rather than a silent gap.
    """
    records: list[dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReplayError(f"could not read {path}: {exc}") from exc
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ReplayError(f"{path}:{number}: record is not an object")
        if include_all or _is_visible(record, show_thinking=show_thinking):
            records.append(record)
    if not records:
        raise ReplayError(f"{path}: no replayable records")
    return records


def _instance_dirs(run_dir: Path) -> list[Path]:
    instances = run_dir / "instances"
    if not instances.is_dir():
        return []
    return sorted(
        entry
        for entry in instances.iterdir()
        if entry.is_dir() and (entry / "transcript.jsonl").is_file()
    )


def select_replay(
    run_dir: str | Path, *, instance: str | None = None
) -> ReplaySelection:
    """Resolve a run directory (or one transcript) to a replay selection.

    ``run_dir`` may be the run root, a single instance directory, or a
    ``transcript.jsonl`` path.  When the run holds several tasks, ``instance``
    is required.
    """
    root = Path(run_dir).expanduser()
    if not root.exists():
        raise ReplayError(f"no such run or transcript: {root}")
    if instance is not None and (
        not instance or "/" in instance or "\\" in instance or instance in (".", "..")
    ):
        raise ReplayError(f"invalid instance id: {instance!r}")

    if root.is_file():
        if root.name != "transcript.jsonl":
            raise ReplayError(f"not a transcript file: {root}")
        instance_dir = root.parent
        run = instance_dir.parent.parent if instance_dir.parent.name == "instances" else instance_dir
    elif (root / "transcript.jsonl").is_file():
        instance_dir = root
        run = instance_dir.parent.parent if instance_dir.parent.name == "instances" else instance_dir
    elif (root / "instances").is_dir():
        run = root
        candidates = _instance_dirs(run)
        if instance is None:
            if len(candidates) == 1:
                instance_dir = candidates[0]
            elif not candidates:
                raise ReplayError(f"{run} has no instance transcripts")
            else:
                names = ", ".join(entry.name for entry in candidates)
                raise ReplayError(
                    f"{run} has {len(candidates)} tasks; pass --instance with one of: {names}"
                )
        else:
            instance_dir = run / "instances" / instance
            if not (instance_dir / "transcript.jsonl").is_file():
                names = ", ".join(entry.name for entry in candidates) or "(none)"
                raise ReplayError(
                    f"no instance {instance!r} in {run}; available: {names}"
                )
    else:
        raise ReplayError(f"{root} is not a run directory or a transcript")

    if instance is not None and instance_dir.name != instance:
        raise ReplayError(
            f"selected {instance_dir.name} but --instance was {instance!r}"
        )

    result = _read_json(instance_dir / "result.json")
    summary = _read_json(run / "summary.json")
    return ReplaySelection(
        run_dir=run,
        instance_id=instance_dir.name,
        transcript=instance_dir / "transcript.jsonl",
        model=str(result.get("model") or summary.get("model") or "?"),
        thinking=str(result.get("thinking") or summary.get("thinking") or "?"),
        mode=str(result.get("mode") or summary.get("mode") or "pipeline"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pi-swebench replay",
        description="Replay a saved SWE-bench transcript in the terminal UI.",
    )
    parser.add_argument(
        "run_dir",
        help="run root, one instance directory, or a transcript.jsonl file",
    )
    parser.add_argument(
        "--instance",
        metavar="ID",
        help="task to replay when the run holds more than one",
    )
    parser.add_argument(
        "--theme",
        choices=("dark", "light"),
        default="dark",
        help="color theme (default: dark)",
    )
    parser.add_argument("--no-color", action="store_true", help="disable color")
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="drop thinking deltas from the replay",
    )
    parser.add_argument(
        "--start",
        choices=("start", "end"),
        default="start",
        help="begin at the first record or the final state (default: start)",
    )
    parser.add_argument(
        "--paused",
        action="store_true",
        help="start paused instead of playing",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=60.0,
        metavar="N",
        help="initial records per second (default: 60)",
    )
    parser.add_argument(
        "--all-events",
        action="store_true",
        help="replay the raw stream instead of the visually meaningful records",
    )
    return parser


def replay_main(argv: list[str] | None = None) -> int:
    """``pi-swebench replay`` entry point."""
    args = build_parser().parse_args(argv)
    try:
        selection = select_replay(args.run_dir, instance=args.instance)
        records = load_records(
            selection.transcript,
            include_all=args.all_events,
            show_thinking=not args.no_thinking,
        )
    except ReplayError as exc:
        print(f"pi-swebench replay: error: {exc}", file=sys.stderr)
        return 2

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "pi-swebench replay: error: the replay needs an interactive terminal",
            file=sys.stderr,
        )
        return 2

    print(
        f"replaying {selection.instance_id} ({len(records)} records, "
        f"{selection.model} / {selection.thinking})",
        flush=True,
    )
    app = ReplayApp(
        records,
        model=selection.model,
        thinking=selection.thinking,
        instance=selection.instance_id,
        theme=args.theme,
        color=False if args.no_color else None,
        show_thinking=not args.no_thinking,
        start=args.start,
        paused=args.paused,
        speed=args.speed,
    )
    try:
        app.run()
    except KeyboardInterrupt:  # pragma: no cover - terminal interrupt
        return 130
    return 0


__all__ = [
    "ReplayError",
    "ReplaySelection",
    "build_parser",
    "load_records",
    "replay_main",
    "select_replay",
]
