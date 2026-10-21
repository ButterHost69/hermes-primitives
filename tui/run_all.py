"""Run the pipeline over every case in a fresh environment, then evaluate."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluator.__main__ import resolve_baseline, resolve_environment
from evaluator.case import discover_cases
from evaluator.engine import evaluate_cases
from pipeline import Pipeline

LIVE = REPO_ROOT / ".environment"
BASELINE = REPO_ROOT / "environment"


def _observer(case_name: str):
    def observer(record: dict) -> None:
        if record.get("type") == "primitives_fanned_out":
            files = record.get("files") or []
            print(
                f"  case {case_name}: fan-out {len(files)} primitive(s): "
                + ", ".join(files),
                flush=True,
            )

    return observer


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "claude"
    thinking = sys.argv[2] if len(sys.argv) > 2 else "low"
    api_key = os.environ.get("HMS_API_KEY", "")

    if not BASELINE.is_dir() or not (BASELINE / "solution").is_dir():
        print(f"error: pristine environment not found at {BASELINE}", file=sys.stderr)
        return 2

    # 1. Fresh live environment from the pristine baseline.
    if LIVE.exists():
        shutil.rmtree(LIVE)
    shutil.copytree(BASELINE, LIVE)
    print(f"created fresh environment at {LIVE}", flush=True)

    # 2. Run the pipeline once per case.
    codebase_root = LIVE / "codebase"
    case_dirs = sorted(codebase_root.iterdir(), key=lambda p: int(p.name))
    for case_dir in case_dirs:
        if not case_dir.is_dir():
            continue
        issue_file = case_dir / "Issues.md"
        prompt = issue_file.read_text(encoding="utf-8") if issue_file.is_file() else ""
        print(f"\n=== case {case_dir.name}: starting ===", flush=True)
        try:
            pipeline = Pipeline(
                str(case_dir), model, thinking, api_key, observer=_observer(case_dir.name)
            )
            previous = prompt
            for role_name in pipeline.role_names:
                print(f"  case {case_dir.name}: running {role_name}...", flush=True)
                previous = pipeline.run_step(role_name, previous)
                print(f"  case {case_dir.name}: {role_name} done", flush=True)
        except Exception as exc:
            print(f"  case {case_dir.name}: failed: {exc}", file=sys.stderr)

    # 3. Evaluate the resulting live tree against the pristine baseline.
    print("\n=== evaluating ===", flush=True)
    env_root = resolve_environment(None)
    baseline_root = resolve_baseline(None, False, env_root)
    cases = discover_cases(env_root)
    baseline_cases = discover_cases(baseline_root) if baseline_root is not None else []

    def on_result(result) -> None:
        score = "-" if result.score is None else result.score
        print(
            f"  case {result.case.number}: {result.outcome} (score={score})",
            flush=True,
        )

    report = evaluate_cases(
        cases,
        baseline_cases=baseline_cases,
        jobs=min(2, len(cases)),
        model=model,
        thinking=thinking,
        timeout=300.0,
        grade_timeout=600.0,
        on_result=on_result,
    )
    print()
    written = report.write_artifacts(REPO_ROOT / "evaluation-artifacts")
    print(f"artifacts: {len(written)} files under evaluation-artifacts/")
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
