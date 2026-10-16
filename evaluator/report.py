"""The evaluation report: one record per case, and how it reads.

Case records are produced by the engine; this module renders them as a
console table, serializes them to JSON, and writes per-case artifacts.
Nothing here talks to a model or a subprocess -- by the time a report
exists, every case is finished.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evaluator.case import Case
from evaluator.grader import Grade
from evaluator.runner import SuiteResult

# Outcomes, most to least severe.  ``pass`` is the only one that counts as a
# success; ``partial`` passed the oracle but lost grade points; ``fail``
# covers both an unfixed bug and a grader's failed verdict; ``error`` is an
# infrastructure failure, not a judgment about the code.
OUTCOMES = ("pass", "partial", "fail", "error")

_COLUMNS = ("#", "project", "oracle", "own-tests", "oracle@base", "verdict", "score", "time")


@dataclass
class CaseResult:
    """Everything one case's evaluation produced.

    ``outcome`` is one of :data:`OUTCOMES`; ``error`` carries the reason for
    an ``error``/``fail`` outcome that no verdict explains (a broken oracle,
    a crashed grader, a missing runtime).
    """

    case: Case
    oracle: SuiteResult
    own_tests: SuiteResult | None = None
    baseline_oracle: SuiteResult | None = None
    regression: bool = False
    candidate_diff: str | None = None
    grade: Grade | None = None
    duration: float = 0.0
    outcome: str = "fail"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == "pass"

    @property
    def score(self) -> int | None:
        return self.grade.score if self.grade is not None else None

    def _suite_fields(self, result: SuiteResult | None) -> dict[str, Any] | None:
        if result is None:
            return None
        return {
            "status": result.status,
            "passed": result.passed,
            "failed": result.failed,
            "total": result.total,
            "exit_code": result.exit_code,
            "duration": round(result.duration, 3),
            "command": list(result.command),
            "note": result.note,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.case.number,
            "project": self.case.name,
            "codebase": str(self.case.codebase),
            "outcome": self.outcome,
            "error": self.error,
            "duration": round(self.duration, 3),
            "oracle": self._suite_fields(self.oracle),
            "own_tests": self._suite_fields(self.own_tests),
            "baseline_oracle": self._suite_fields(self.baseline_oracle),
            "regression": self.regression,
            "candidate_diff_lines": (
                None if self.candidate_diff is None else len(self.candidate_diff.splitlines())
            ),
            "grade": self.grade.to_dict() if self.grade is not None else None,
        }


@dataclass
class Report:
    """Every case's result, plus the run's totals."""

    results: list[CaseResult] = field(default_factory=list)
    total_duration: float = 0.0

    @property
    def ok(self) -> bool:
        """True when every case passed (an empty report is not a success)."""
        return bool(self.results) and all(result.ok for result in self.results)

    @property
    def counts(self) -> dict[str, int]:
        tally = {outcome: 0 for outcome in OUTCOMES}
        for result in self.results:
            tally[result.outcome] += 1
        return tally

    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "total_duration": round(self.total_duration, 3),
            "counts": self.counts,
            "cases": [result.to_dict() for result in self.results],
        }

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def _row(self, result: CaseResult) -> tuple[str, ...]:
        own = result.own_tests
        own_text = "-" if own is None else own.status
        if result.regression:
            own_text = f"{own_text} (REGRESSION)"
        baseline = result.baseline_oracle
        baseline_text = "-" if baseline is None else baseline.status
        return (
            str(result.case.number),
            result.case.name,
            result.oracle.status,
            own_text,
            baseline_text,
            result.outcome,
            "-" if result.score is None else str(result.score),
            f"{result.duration:.1f}s",
        )

    def _notes(self, result: CaseResult) -> list[str]:
        notes: list[str] = []
        if result.error:
            notes.append(f"    ! {result.error}")
        if result.grade is not None and result.grade.issues:
            for issue in result.grade.issues:
                notes.append(f"    - {issue}")
        return notes

    def render(self) -> str:
        """The console view: a table over all cases, then a summary line."""
        rows = [self._row(result) for result in self.results]
        widths = [
            max(len(header), *(len(row[index]) for row in rows)) if rows else len(header)
            for index, header in enumerate(_COLUMNS)
        ]
        lines = ["  ".join(header.ljust(widths[index]) for index, header in enumerate(_COLUMNS))]
        lines.append("  ".join("-" * width for width in widths))
        for result, row in zip(self.results, rows):
            lines.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))
            lines.extend(self._notes(result))

        tally = self.counts
        summary = ", ".join(f"{count} {outcome}" for outcome, count in tally.items() if count)
        verdict = "all cases passed" if self.ok else "evaluation failed"
        summary = summary or "no cases"
        lines.append("")
        lines.append(f"{summary} · {verdict} · total {self.total_duration:.1f}s")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Artifacts
    # ------------------------------------------------------------------ #
    def write_artifacts(self, directory: Path) -> list[Path]:
        """Write per-case artifacts under ``directory/<n>/``; return the paths.

        The grader never writes files, so this is the only place the run
        touches the filesystem -- called from the main thread after every
        case has finished, never from a worker.
        """
        written: list[Path] = []
        for result in self.results:
            case_dir = directory / str(result.case.number)
            case_dir.mkdir(parents=True, exist_ok=True)
            for name, content in self._artifacts(result):
                path = case_dir / name
                path.write_text(content, encoding="utf-8")
                written.append(path)
        return written

    def _artifacts(self, result: CaseResult) -> list[tuple[str, str]]:
        artifacts: list[tuple[str, str]] = [
            ("result.json", json.dumps(result.to_dict(), indent=2)),
            ("oracle.txt", result.oracle.artifact_text()),
        ]
        if result.own_tests is not None:
            artifacts.append(("own-tests.txt", result.own_tests.artifact_text()))
        if result.baseline_oracle is not None:
            artifacts.append(("baseline-oracle.txt", result.baseline_oracle.artifact_text()))
        if result.candidate_diff:
            artifacts.append(("candidate.diff", result.candidate_diff))
        if result.grade is not None:
            artifacts.append(("grade.json", json.dumps(result.grade.to_dict(), indent=2)))
        return artifacts
