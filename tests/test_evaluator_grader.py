"""Tests for evaluator.grader: the agent grading phase."""

from __future__ import annotations

import json

import pytest

from agent import PiError
from evaluator.case import Case
from evaluator.grader import (
    GRADE_TOOLS,
    GraderFormatError,
    grade_case,
    normalize_grade,
)
from evaluator.runner import PASSED, SuiteResult
import evaluator.grader as grader_module


class StubSession:
    """A controllable stand-in for the PiSession the grader opens."""

    def __init__(self, outputs, **kwargs):
        self.kwargs = kwargs
        self.outputs = outputs
        self.run_calls: list[str] = []
        self.retry_errors: list = []
        self.closed = False

    def run(self, message):
        self.run_calls.append(message)
        return self.outputs.pop(0)

    def retry(self, *, error=None, timeout=None):
        self.retry_errors.append(error)
        return self.outputs.pop(0)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


@pytest.fixture
def stub(monkeypatch):
    sessions: list[StubSession] = []
    outputs: list[str] = []

    monkeypatch.setattr(grader_module, "load_prompt", lambda path: "GRADE PROMPT")

    def make_session(**kwargs):
        session = StubSession(outputs, **kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(grader_module, "PiSession", make_session)
    return sessions, outputs


def _case(tmp_path) -> Case:
    codebase = tmp_path / "codebase" / "1"
    codebase.mkdir(parents=True)
    return Case(
        number=1,
        codebase=codebase,
        oracle_test=tmp_path / "solution" / "1" / "test_oracle.py",
        reference_diffs=(),
        name="Alpha",
        issue="VALUE should be 2.",
    )


def _oracle(status=PASSED) -> SuiteResult:
    return SuiteResult(kind="oracle", command=("python", "t"), status=status, total=5, passed=5, failed=0)


def _grade_json(**overrides):
    payload = {
        "score": 85,
        "verdict": "pass",
        "criteria": {
            "correctness": {"score": 40, "max": 40, "notes": "ok"},
            "equivalence": {"score": 22, "max": 25, "notes": "ok"},
            "minimality": {"score": 18, "max": 20, "notes": "ok"},
            "quality": {"score": 5, "max": 15, "notes": "ok"},
        },
        "summary": "fine",
        "issues": ["a nit"],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_grade_case_returns_grade(stub, tmp_path):
    sessions, outputs = stub
    outputs.append(_grade_json())
    case = _case(tmp_path)

    grade = grade_case(case, oracle=_oracle(), candidate_diff="diff --git a/x b/x")

    assert grade.score == 85
    assert grade.verdict == "pass"
    assert grade.criteria["correctness"]["score"] == 40
    assert grade.issues == ("a nit",)
    assert sessions[0].closed


def test_grade_case_configures_session(stub, tmp_path):
    sessions, outputs = stub
    outputs.append(_grade_json())
    case = _case(tmp_path)

    grade_case(case, oracle=_oracle(), model="claude", thinking="high")

    assert sessions[0].kwargs["tools"] == GRADE_TOOLS
    assert sessions[0].kwargs["cwd"] == str(case.codebase)
    assert sessions[0].kwargs["model"] == "claude"
    assert sessions[0].kwargs["thinking"] == "high"


def test_grade_case_prompt_carries_everything(stub, tmp_path):
    sessions, outputs = stub
    outputs.append(_grade_json())
    case = _case(tmp_path)
    case = Case(
        number=case.number,
        codebase=case.codebase,
        oracle_test=case.oracle_test,
        reference_diffs=(tmp_path / "ref.diff",),
        name=case.name,
        issue="VALUE should be 2.",
    )
    (tmp_path / "ref.diff").write_text("reference diff text", encoding="utf-8")

    grade_case(case, oracle=_oracle(), candidate_diff="candidate diff text")

    message = sessions[0].run_calls[0]
    assert "GRADE PROMPT" in message
    envelope = json.loads(message.split("\n\n", 1)[1])
    assert envelope["issue"] == "VALUE should be 2."
    assert envelope["reference_diff"] == "reference diff text"
    assert envelope["candidate_diff"] == "candidate diff text"
    assert envelope["oracle_suite"]["status"] == PASSED
    assert envelope["oracle_suite"]["test"] == "test_oracle.py"


def test_grade_case_retries_once_then_gives_up(stub, monkeypatch, tmp_path):
    sessions, outputs = stub
    outputs.extend(["not json", "still not json"])
    monkeypatch.setattr(grader_module, "verify_json", lambda text: _bad_json())
    case = _case(tmp_path)

    with pytest.raises(PiError, match="not valid JSON after a retry"):
        grade_case(case, oracle=_oracle())

    assert sessions[0].retry_errors  # the retry carried the parser's error


def _bad_json():
    from utils import JsonCheck

    return JsonCheck(ok=False, error="Expecting value: line 1 column 1")


def test_grade_case_recovers_on_retry(stub, monkeypatch, tmp_path):
    sessions, outputs = stub
    outputs.extend(["not json", _grade_json()])
    checks = iter([_bad_json(), _good_json(_grade_json())])
    monkeypatch.setattr(grader_module, "verify_json", lambda text: next(checks))
    case = _case(tmp_path)

    grade = grade_case(case, oracle=_oracle())
    assert grade.verdict == "pass"


def _good_json(text):
    from utils import JsonCheck

    return JsonCheck(ok=True, value=json.loads(text))


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def test_normalize_grade_recomputes_score_from_criteria():
    grade = normalize_grade(
        {
            "score": 1,
            "verdict": "pass",
            "criteria": {
                "correctness": {"score": 40, "max": 40},
                "equivalence": {"score": 25, "max": 25},
                "minimality": {"score": 20, "max": 20},
                "quality": {"score": 15, "max": 15},
            },
        },
        oracle_passed=True,
    )

    assert grade.score == 100
    assert grade.verdict == "pass"


def test_normalize_grade_clamps_score():
    grade = normalize_grade({"score": 400, "criteria": {}}, oracle_passed=True)
    assert grade.score == 100


def test_normalize_grade_derives_missing_verdict_from_bands():
    assert normalize_grade({"score": 90}, oracle_passed=True).verdict == "pass"
    assert normalize_grade({"score": 70}, oracle_passed=True).verdict == "partial"
    assert normalize_grade({"score": 10}, oracle_passed=True).verdict == "fail"


def test_normalize_grade_rejects_unknown_verdict():
    grade = normalize_grade({"score": 90, "verdict": "shiny"}, oracle_passed=True)
    assert grade.verdict == "pass"


def test_normalize_grade_forces_fail_when_oracle_failed():
    grade = normalize_grade(
        {
            "score": 100,
            "verdict": "pass",
            "criteria": {"correctness": {"score": 40, "max": 40}},
        },
        oracle_passed=False,
    )

    assert grade.verdict == "fail"
    assert grade.criteria["correctness"]["score"] == 0
    assert grade.score == 0


def test_normalize_grade_requires_an_object():
    with pytest.raises(GraderFormatError):
        normalize_grade([1, 2, 3], oracle_passed=True)


def test_normalize_grade_drops_malformed_criteria():
    grade = normalize_grade(
        {"criteria": {"correctness": "fast", "quality": {"score": 3, "max": 15}}},
        oracle_passed=True,
    )
    assert list(grade.criteria) == ["quality"]
    assert grade.score == 3


def test_normalize_grade_issues_default_to_empty_tuple():
    grade = normalize_grade({}, oracle_passed=True)
    assert grade.issues == ()
    assert grade.summary == ""


def test_clamp_int_falls_back_on_garbage():
    grade = normalize_grade(
        {"criteria": {"correctness": {"score": "many", "max": "lots"}}}, oracle_passed=True
    )
    assert grade.criteria["correctness"]["score"] == 0
    assert grade.criteria["correctness"]["max"] == 0


def test_reference_diff_note_when_unreadable(tmp_path):
    case = _case(tmp_path)
    case = Case(
        number=case.number,
        codebase=case.codebase,
        oracle_test=case.oracle_test,
        reference_diffs=(tmp_path / "gone.diff",),
        name=case.name,
        issue=case.issue,
    )
    text = grader_module._reference_diff_text(case)
    assert "could not read gone.diff" in text
