"""The evaluator's command line: ``pi-evaluate``.

Resolves the environment root (and its baseline), discovers the cases, runs
the evaluation, and prints the report.  Exit codes: ``0`` every case passed,
``1`` the evaluation ran but something failed, ``2`` a setup problem (no
environment, no cases, bad arguments) -- distinct from a failed evaluation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evaluator.case import discover_cases
from evaluator.engine import evaluate_cases
from evaluator.errors import EvaluatorError
from evaluator.grader import DEFAULT_GRADE_TIMEOUT
from evaluator.report import Report
from evaluator.runner import DEFAULT_TIMEOUT

PROG = "pi-evaluate"

# The live environment is gitignored; the pristine, tracked copy is the
# baseline.  The fallback order matters: a pipeline run always lands in
# ``.environment``, so it wins when both exist.
LIVE_ENV = ".environment"
PRISTINE_ENV = "environment"


def resolve_environment(explicit: str | None) -> Path:
    """Resolve the environment root: ``--environment``, else the default.

    The default prefers the live ``.environment/`` and falls back to the
    pristine ``environment/``; neither existing is a setup error, not an
    empty run.
    """
    if explicit:
        root = Path(explicit).expanduser().resolve()
    else:
        root = next(
            (
                candidate.resolve()
                for candidate in (Path(LIVE_ENV), Path(PRISTINE_ENV))
                if candidate.is_dir()
            ),
            None,
        )
        if root is None:
            raise EvaluatorError(
                f"no environment found: expected {LIVE_ENV}/ or {PRISTINE_ENV}/ "
                "in the current directory, or pass --environment"
            )
    if not (root / "solution").is_dir():
        raise EvaluatorError(f"{root} has no solution/ directory")
    return root


def resolve_baseline(explicit: str | None, no_baseline: bool, env_root: Path) -> Path | None:
    """Resolve the baseline root, or ``None`` when the run has none.

    ``--no-baseline`` opts out entirely.  Otherwise an explicit
    ``--baseline`` wins; a live ``.environment`` otherwise auto-pairs with
    its pristine ``environment`` sibling, which is what makes the negative
    control and the candidate diff possible in a default run.
    """
    if no_baseline:
        return None
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not (root / "solution").is_dir():
            raise EvaluatorError(f"{root} has no solution/ directory")
        if root.resolve() == env_root.resolve():
            return None
        return root
    if env_root.name == LIVE_ENV:
        sibling = env_root.parent / PRISTINE_ENV
        if (sibling / "solution").is_dir() and sibling.resolve() != env_root.resolve():
            return sibling.resolve()
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Run each environment's oracle suite against its codebase, and grade "
            "every passing fix with an agent."
        ),
    )
    parser.add_argument(
        "--environment",
        metavar="DIR",
        help=(
            "environment root holding codebase/ and solution/ "
            f"(default: ./{LIVE_ENV}, else ./{PRISTINE_ENV})"
        ),
    )
    parser.add_argument(
        "--baseline",
        metavar="DIR",
        help="pristine environment root the candidate diff is taken against",
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="skip the baseline entirely (no candidate diff, no negative control)",
    )
    parser.add_argument(
        "--codebase",
        metavar="N",
        type=int,
        action="append",
        dest="only",
        help="evaluate only this case number (repeatable)",
    )
    parser.add_argument(
        "-m", "--model", metavar="PATTERN", help="model id or fuzzy match for the grader"
    )
    parser.add_argument(
        "--thinking",
        metavar="LEVEL",
        help="reasoning level for the grader (off, minimal, low, medium, high, ...)",
    )
    parser.add_argument(
        "--jobs",
        metavar="N",
        type=int,
        help="concurrent cases (default: one per case, capped at 8)",
    )
    parser.add_argument(
        "--timeout",
        metavar="SEC",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"per-suite timeout in seconds (default: {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument(
        "--grade-timeout",
        metavar="SEC",
        type=float,
        default=DEFAULT_GRADE_TIMEOUT,
        help=(
            "inactivity timeout per grader model turn in seconds "
            f"(default: {DEFAULT_GRADE_TIMEOUT:g})"
        ),
    )
    parser.add_argument(
        "--skip-project-tests",
        action="store_true",
        help="do not run the codebases' own test suites",
    )
    parser.add_argument(
        "--no-grade",
        action="store_true",
        help="run the tests only; never call the grader",
    )
    parser.add_argument(
        "--grade-all",
        action="store_true",
        help="grade every case, including ones whose oracle suite failed",
    )
    parser.add_argument(
        "--report-dir",
        metavar="DIR",
        help="write per-case artifacts (test output, diffs, grades) here",
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="write the machine-readable report here",
    )
    return parser


def run(args: argparse.Namespace) -> tuple[Report, Path, Path | None]:
    """Discover, evaluate, and write the run's outputs; return the report."""
    env_root = resolve_environment(args.environment)
    baseline_root = resolve_baseline(args.baseline, args.no_baseline, env_root)
    cases = discover_cases(env_root, only=args.only)
    baseline_cases = discover_cases(baseline_root) if baseline_root is not None else []

    report = evaluate_cases(
        cases,
        baseline_cases=baseline_cases,
        jobs=args.jobs,
        model=args.model,
        thinking=args.thinking,
        timeout=args.timeout,
        grade_timeout=args.grade_timeout,
        grade_all=args.grade_all,
        no_grade=args.no_grade,
        skip_project_tests=args.skip_project_tests,
    )

    if args.report_dir:
        written = report.write_artifacts(Path(args.report_dir))
        print(f"artifacts: {len(written)} files under {args.report_dir}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
        print(f"report: {args.json}")
    return report, env_root, baseline_root


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report, env_root, baseline_root = run(args)
    except EvaluatorError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 2

    print(f"environment: {env_root}")
    if baseline_root is not None:
        print(f"baseline:   {baseline_root}")
    print(report.render())
    return report.exit_code()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
