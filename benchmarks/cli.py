"""Command line for running hms on SWE-bench tasks.

Examples::

    python -m benchmarks.swebench --dry-run
    python -m benchmarks.swebench --instance-id pallets__flask-4045
    python -m benchmarks.swebench --instance-id pallets__flask-4045 --mode agent --evaluate

The defaults are the ones the experiment uses: SWE-bench Lite, the
``opencode-go/cat-jam-free`` model at ``max`` reasoning, and the project's role
chain (``--mode pipeline``).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from benchmarks.dataset import (
    DEFAULT_DATASET,
    DatasetError,
    load_instances,
    select_instances,
)
from benchmarks.runner import (
    DEFAULT_MODEL,
    DEFAULT_THINKING,
    BenchmarkError,
    RunOptions,
    evaluate_predictions,
    run_benchmark,
)

PROG = "pi-swebench"
DEFAULT_WORKDIR = Path(".swebench")


def _safe_run_id(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    if not slug:
        raise BenchmarkError("run id must contain at least one safe character")
    return slug


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Materialize SWE-bench tasks, let hms edit them, write a "
            "predictions file, and optionally grade it with the official harness."
        ),
    )
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        help=f"Hugging Face dataset id or local .json/.jsonl (default: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--dataset-file",
        metavar="PATH",
        help="local .json/.jsonl task file (overrides --dataset)",
    )
    parser.add_argument(
        "--split",
        default="test",
        help="dataset split (default: test)",
    )
    parser.add_argument(
        "--instance-id",
        action="append",
        dest="only",
        metavar="ID",
        help="run only this task (repeatable)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="run at most the first N selected tasks",
    )
    parser.add_argument(
        "--mode",
        choices=("pipeline", "agent"),
        default="pipeline",
        help="pipeline = reproduce/diagnose/primitive roles; agent = one Pi turn (default: pipeline)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Pi model pattern (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--thinking",
        default=DEFAULT_THINKING,
        help=f"Pi reasoning level (default: {DEFAULT_THINKING})",
    )
    parser.add_argument(
        "--session-timeout",
        type=float,
        default=900.0,
        metavar="SEC",
        help="plain-agent inactivity timeout per turn (default: 900)",
    )
    parser.add_argument(
        "--run-id",
        help="name for this run; defaults to a timestamp",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=DEFAULT_WORKDIR,
        help=f"checkouts, dataset cache, and run records (default: {DEFAULT_WORKDIR})",
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        metavar="PATH",
        help="predictions file (default: <workdir>/runs/<run-id>/predictions.jsonl)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="keep existing non-empty predictions and retry the rest",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-read the dataset and re-materialize task checkouts",
    )
    parser.add_argument(
        "--keep-worktrees",
        action="store_true",
        help="keep each task checkout after its patch is captured (uses disk)",
    )
    parser.add_argument(
        "--skip-reproduce",
        action="store_true",
        help="pipeline mode: skip reproduce and diagnose directly from the issue",
    )
    parser.add_argument(
        "--allow-shell",
        action="store_true",
        help="allow bash/codemode (only safe inside a project container)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="load and print the selected tasks without running a model",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="run the official SWE-bench harness after generating predictions",
    )
    parser.add_argument(
        "--swebench-bin",
        default="swebench",
        help="SWE-bench entry point, e.g. .swebench/swebench-venv/bin/swebench",
    )
    parser.add_argument(
        "--eval-run-id",
        help="harness run id (default: this run's id)",
    )
    parser.add_argument(
        "--eval-workers",
        type=int,
        default=1,
        help="concurrent harness workers (default: 1)",
    )
    parser.add_argument(
        "--eval-timeout",
        type=int,
        default=1800,
        help="per-instance harness timeout in seconds (default: 1800)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only print errors and the final locations",
    )
    return parser


def _print_task(instance, index: int, total: int) -> None:
    image = instance.image or "(no image field)"
    print(
        f"[{index}/{total}] {instance.instance_id}  {instance.repo}@{instance.base_commit[:10]}"
        f"  {image}"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workdir = Path(args.workdir)
    run_id = _safe_run_id(args.run_id or time.strftime("%Y%m%d-%H%M%S"))
    run_dir = workdir / "runs" / run_id
    predictions = Path(args.predictions) if args.predictions else run_dir / "predictions.jsonl"

    try:
        if args.dataset_file:
            instances = load_instances(
                Path(args.dataset_file), split=args.split, refresh=args.refresh
            )
            dataset_name = str(Path(args.dataset_file))
        else:
            instances = load_instances(
                args.dataset,
                split=args.split,
                cache_dir=workdir / "cache" / "datasets",
                refresh=args.refresh,
            )
            dataset_name = args.dataset
        selected = select_instances(instances, only=args.only, limit=args.limit)
        options = RunOptions(
            model=args.model,
            thinking=args.thinking,
            mode=args.mode,
            session_timeout=args.session_timeout,
            work_root=workdir,
            refresh=args.refresh,
            keep_worktrees=args.keep_worktrees,
            skip_reproduce=args.skip_reproduce,
            allow_shell=args.allow_shell,
        )
    except (DatasetError, BenchmarkError, ValueError) as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"dataset: {dataset_name} ({len(instances)} tasks, {len(selected)} selected)")
        for index, instance in enumerate(selected, 1):
            _print_task(instance, index, len(selected))
        return 0

    if not selected:
        print(f"{PROG}: error: no tasks selected", file=sys.stderr)
        return 2

    print(
        f"run {run_id}: {len(selected)} task(s), model={args.model}, "
        f"thinking={args.thinking}, mode={args.mode}",
        flush=True,
    )

    def report(outcome: dict) -> None:
        if args.quiet:
            return
        index = outcome.get("index", "?")
        total = outcome.get("total", "?")
        if outcome.get("skipped"):
            print(f"[{index}/{total}] {outcome['instance_id']}: skipped (existing patch)")
            return
        state = "patch" if outcome.get("has_patch") else "no patch"
        detail = f" ({outcome['patch_lines']} lines)" if outcome.get("has_patch") else ""
        error = f"  error: {outcome['error']}" if outcome.get("error") else ""
        check = (
            f"  patch check: {outcome['patch_check_error']}"
            if outcome.get("patch_check_error")
            else ""
        )
        print(f"[{index}/{total}] {outcome['instance_id']}: {state}{detail}{error}{check}")

    try:
        summary = run_benchmark(
            selected,
            options,
            run_dir,
            predictions_path=predictions,
            resume=args.resume,
            on_instance=report,
        )
    except (BenchmarkError, DatasetError, ValueError) as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 1

    print(
        f"\n{summary['with_patch']}/{summary['instances']} task(s) produced a patch"
        f" · {summary['errors']} error(s)"
    )
    print(f"predictions: {predictions}")
    print(f"records:     {run_dir}")

    if not args.evaluate:
        return 1 if summary["errors"] else 0

    instance_ids = [instance.instance_id for instance in selected]
    eval_run_id = _safe_run_id(args.eval_run_id or run_id)
    return evaluate_predictions(
        run_dir=run_dir,
        predictions=predictions,
        dataset=dataset_name,
        instance_ids=instance_ids,
        run_id=eval_run_id,
        swebench_bin=args.swebench_bin,
        max_workers=args.eval_workers,
        timeout=args.eval_timeout,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
