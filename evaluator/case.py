"""Discovery of evaluation cases inside an environment root.

An environment root holds ``codebase/<n>/`` (the projects a pipeline ran on)
and ``solution/<n>/`` (each project's oracle test plus the reference diff).
:func:`discover_cases` pairs them into :class:`Case` records; the evaluator
discovers the live root and the baseline root separately and pairs cases by
number, because the oracle suites locate the codebase they test by walking up
from their own location -- the baseline's suite must come from the baseline
root, not from a copy of the live one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from evaluator.errors import EvaluatorError

_H1 = re.compile(r"^#\s+(.+?)\s*$")

# The oracle suites ship as one test file per solution directory; the runner
# accepts Python (unittest) and Node (node:test) suites.
_TEST_SUFFIXES = (".py", ".js")


@dataclass(frozen=True)
class Case:
    """One bug-fix exercise: a codebase plus the oracle that judges its fix.

    ``number`` is the exercise number, ``codebase`` the project the pipeline
    worked on, ``oracle_test`` the suite that must pass once the fix lands,
    ``reference_diffs`` the reference solutions, ``name`` the project name
    from its README, and ``issue`` the bug report from ``Issues.md``.
    """

    number: int
    codebase: Path
    oracle_test: Path
    reference_diffs: tuple[Path, ...]
    name: str
    issue: str

    @property
    def solution_dir(self) -> Path:
        """The ``solution/<n>`` directory this case's oracle came from."""
        return self.oracle_test.parent


def _is_test_file(path: Path) -> bool:
    return path.name.startswith("test") and path.suffix in _TEST_SUFFIXES


def _project_name(codebase: Path, number: int) -> str:
    """Read the project name from its README's first heading."""
    try:
        first = (codebase / "README.md").read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return f"codebase/{number}"
    match = _H1.match(first.strip())
    return match.group(1) if match else f"codebase/{number}"


def _issue(codebase: Path) -> str:
    """Read the issue description, or ``""`` when the project ships none."""
    try:
        return (codebase / "Issues.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def discover_cases(root: Path, *, only: list[int] | None = None) -> list[Case]:
    """Find every case under ``root``, sorted by number.

    ``only`` restricts discovery to the listed case numbers; the returned
    list stays sorted.  A numeric ``solution/<n>`` directory whose
    ``codebase/<n>`` is missing, or that holds no oracle test, or more than
    one, is a broken environment and raises :class:`EvaluatorError` rather
    than being skipped: a silently dropped case would read as a passing run.
    """
    solution = root / "solution"
    if not solution.is_dir():
        raise EvaluatorError(f"{root} has no solution/ directory")

    cases: list[Case] = []
    for entry in sorted(solution.iterdir()):
        if not entry.is_dir() or not entry.name.isdigit():
            continue
        number = int(entry.name)
        if only is not None and number not in only:
            continue
        codebase = root / "codebase" / entry.name
        if not codebase.is_dir():
            raise EvaluatorError(f"case {number}: no codebase directory at {codebase}")
        tests = sorted(p for p in entry.iterdir() if p.is_file() and _is_test_file(p))
        if not tests:
            raise EvaluatorError(f"case {number}: no oracle test in {entry}")
        if len(tests) > 1:
            names = ", ".join(p.name for p in tests)
            raise EvaluatorError(f"case {number}: more than one oracle test: {names}")
        cases.append(
            Case(
                number=number,
                codebase=codebase,
                oracle_test=tests[0],
                reference_diffs=tuple(sorted(entry.rglob("*.diff"))),
                name=_project_name(codebase, number),
                issue=_issue(codebase),
            )
        )

    if not cases:
        raise EvaluatorError(f"no cases found under {solution}")
    return cases
