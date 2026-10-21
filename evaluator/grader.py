"""Grading a candidate fix with a Pi agent session.

The deterministic phases answer *whether* the fix works; the grader answers
*whether it is a good fix*.  It receives everything the run learned -- the
issue, the reference diff, the candidate diff, the oracle result, the
project's own test result -- reads the working tree itself, and returns a
structured JSON grade.  Like ``diagnose`` and ``reproduce``, it retries once
when the model's answer is not valid JSON and then gives up.

The grader runs with write tools disabled and the prompt confines ``bash`` to
read-only use: a grade must never be able to change the codebase it judges.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from agent import PiError, PiSession, load_prompt
from evaluator.case import Case
from evaluator.runner import PASSED, SuiteResult
from utils import verify_json

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Grading inspects and runs tests; it never changes the tree.
GRADE_TOOLS = ("read", "grep", "find", "ls", "bash")

# The rubric: four weighted criteria, and the bands a total score lands in.
CRITERIA_MAX = {"correctness": 40, "equivalence": 25, "minimality": 20, "quality": 15}
PASS_BAND = 85
PARTIAL_BAND = 60

# A grading turn inspects the tree and may run test suites, so it needs more
# room than the deterministic suites' own timeout.
DEFAULT_GRADE_TIMEOUT = 600.0

VERDICTS = ("pass", "partial", "fail")


class GraderFormatError(PiError):
    """The grader's answer was valid JSON but not a usable grade."""


@dataclass(frozen=True)
class Grade:
    """A normalized grade for one case.

    ``score`` is the criterion sum (0-100), ``verdict`` the band it lands in,
    ``criteria`` the per-criterion detail the model returned, and ``issues``
    the concrete problems it found.
    """

    score: int
    verdict: str
    criteria: dict[str, dict[str, Any]]
    summary: str
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "verdict": self.verdict,
            "criteria": self.criteria,
            "summary": self.summary,
            "issues": list(self.issues),
        }


def _clamp_int(value: Any, *, low: int = 0, high: int = 100) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def _band(score: int) -> str:
    if score >= PASS_BAND:
        return "pass"
    if score >= PARTIAL_BAND:
        return "partial"
    return "fail"


def _clean_criteria(raw: Any) -> dict[str, dict[str, Any]]:
    """Keep only well-formed criteria entries, with clamped integer scores."""
    if not isinstance(raw, dict):
        return {}
    criteria: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        criteria[str(name)] = {
            "score": _clamp_int(spec.get("score", 0)),
            "max": _clamp_int(spec.get("max", 0), low=0),
            "notes": str(spec.get("notes", "")),
        }
    return criteria


def normalize_grade(value: Any, *, oracle_passed: bool) -> Grade:
    """Turn a parsed grader response into a :class:`Grade`.

    The score is recomputed as the criterion sum when the model supplied
    criteria -- a top-level ``score`` that disagrees with its own breakdown
    is the breakdown's sum, not the label.  A ``verdict`` outside the known
    bands is derived from the score.  When the oracle suite did not pass, the
    fix cannot be correct: correctness is forced to 0 and the verdict to
    ``fail``, whatever the model said.
    """
    if not isinstance(value, dict):
        raise GraderFormatError("grader response was not a JSON object")

    criteria = _clean_criteria(value.get("criteria"))
    if not oracle_passed and "correctness" in criteria:
        criteria["correctness"]["score"] = 0
    score = sum(spec["score"] for spec in criteria.values()) if criteria else _clamp_int(
        value.get("score", 0)
    )
    score = _clamp_int(score)

    verdict = value.get("verdict")
    verdict = verdict if verdict in VERDICTS else _band(score)
    if not oracle_passed:
        verdict = "fail"

    raw_issues = value.get("issues")
    issues = (
        tuple(str(issue) for issue in raw_issues)
        if isinstance(raw_issues, list)
        else ()
    )
    return Grade(
        score=score,
        verdict=verdict,
        criteria=criteria,
        summary=str(value.get("summary", "")),
        issues=issues,
    )


def _reference_diff_text(case: Case) -> str:
    """The reference diffs, joined; unreadable files are noted, not fatal."""
    sections: list[str] = []
    for path in case.reference_diffs:
        try:
            sections.append(path.read_text(encoding="utf-8").strip())
        except OSError as exc:
            sections.append(f"(could not read {path.name}: {exc})")
    return "\n\n".join(sections)


def _suite_report(result: SuiteResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "status": result.status,
        "command": list(result.command),
        "passed": result.passed,
        "failed": result.failed,
        "total": result.total,
        "output_tail": result.tail(),
    }


def grade_case(
    case: Case,
    *,
    oracle: SuiteResult,
    own_tests: SuiteResult | None = None,
    regression: bool = False,
    candidate_diff: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    observer: Callable[[dict[str, Any]], None] | None = None,
    timeout: float | None = None,
) -> Grade:
    """Grade one case with a Pi session and return the normalized grade.

    Raises :class:`PiError` when the model's answer is not valid JSON after
    one retry, and :class:`GraderFormatError` when it is JSON but carries no
    usable grade.  ``timeout`` caps inactivity during each model turn; it
    defaults to :data:`DEFAULT_GRADE_TIMEOUT`.
    """
    envelope = {
        "case": case.number,
        "project": case.name,
        "issue": case.issue,
        "oracle_suite": {
            "test": case.oracle_test.name,
            **_suite_report(oracle),
        },
        "project_tests": _suite_report(own_tests),
        "regression_detected": regression,
        "reference_diff": _reference_diff_text(case),
        "candidate_diff": candidate_diff,
    }
    prompt = load_prompt(PROMPTS_DIR / "grade.md")
    with PiSession(
        tools=GRADE_TOOLS,
        model=model,
        thinking=thinking,
        cwd=str(case.codebase),
        observer=observer,
        timeout=timeout if timeout is not None else DEFAULT_GRADE_TIMEOUT,
    ) as session:
        output = session.run(f"{prompt}\n\n{json.dumps(envelope, indent=2)}")
        check = verify_json(output)
        if not check.ok:
            output = session.retry(error=check.error)
            check = verify_json(output)
        if not check.ok:
            raise PiError("grader output was not valid JSON after a retry")
        return normalize_grade(check.value, oracle_passed=oracle.status == PASSED)
