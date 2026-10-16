"""Running and reading test suites: the evaluator's deterministic phases.

Two deterministic phases feed an evaluation:

- the **oracle** suite from ``solution/<n>``, run against a codebase -- the
  gate that decides whether the fix works, and
- the codebase's **own** tests, run as a regression guard -- their result is
  advisory input to the grader, never a gate.

Both are subprocesses whose output is parsed for per-suite counts.  A suite
that exits non-zero *with a recognizable summary* is ``failed`` (the code did
not pass); one that exits non-zero *without* one -- an import crash, a missing
runtime, a timeout -- is ``error``, because nothing about the fix was
actually judged.
"""

from __future__ import annotations

import importlib.util
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from evaluator.errors import EvaluatorError

DEFAULT_TIMEOUT = 300.0

# Statuses.  ``none`` means "there was nothing to run" (a codebase with no
# tests of its own); it never applies to an oracle suite.
PASSED = "passed"
FAILED = "failed"
ERROR = "error"
NONE = "none"

ORACLE = "oracle"
OWN = "own"

# Directories never walked when looking for a project's own tests: build
# artifacts, caches, session logs, and dependencies.
IGNORED_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", ".sessions", "node_modules", ".venv", ".git"}
)

# node --test's default reporter prints one "ℹ <field> <count>" line per
# counter ("ℹ tests 11", "ℹ pass 11", ...).  The marker is matched loosely so
# both the real reporter and the TAP reporter ("# tests 11") parse.
_NODE_COUNTS = re.compile(r"^(?:ℹ|#)\s*(tests|pass|fail|skipped|cancelled|todo)\s+(\d+)")
_RAN = re.compile(r"^Ran (\d+) tests?")
_FAILED = re.compile(r"^FAILED \(([^)]*)\)")
_PYTEST_COUNT = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)")
_PYTEST_NO_TESTS = re.compile(r"^no tests ran", re.IGNORECASE)


@dataclass(frozen=True)
class SuiteResult:
    """The outcome of running one suite.

    ``status`` is :data:`PASSED`, :data:`FAILED`, :data:`ERROR`, or
    :data:`NONE`; ``exit_code`` is the process's own exit code (``None`` when
    it never ran); ``passed``/``failed``/``total`` are the parsed counts
    (``None`` when the output carried no summary); ``note`` explains an
    ``ERROR`` status.
    """

    kind: str
    command: tuple[str, ...]
    status: str
    exit_code: int | None = None
    passed: int | None = None
    failed: int | None = None
    total: int | None = None
    duration: float = 0.0
    stdout: str = ""
    stderr: str = ""
    note: str | None = None

    @property
    def output(self) -> str:
        """Everything the suite wrote, stderr last."""
        if self.stderr:
            return f"{self.stdout}\n{self.stderr}" if self.stdout else self.stderr
        return self.stdout

    def tail(self, lines: int = 60) -> str:
        """The last ``lines`` lines of the combined output."""
        return "\n".join(self.output.splitlines()[-lines:])

    def artifact_text(self) -> str:
        """The full record written next to a case when ``--report-dir`` is set."""
        header = [
            f"kind:     {self.kind}",
            f"command:  {shlex.join(self.command)}",
            f"status:   {self.status}",
            f"exit:     {self.exit_code}",
            f"duration: {self.duration:.2f}s",
        ]
        if self.note:
            header.append(f"note:     {self.note}")
        return "\n".join([*header, "", "--- stdout ---", self.stdout, "--- stderr ---", self.stderr])


# --------------------------------------------------------------------------- #
# Output parsing
# --------------------------------------------------------------------------- #


def parse_node_summary(text: str) -> dict[str, int] | None:
    """Read ``total``/``passed``/``failed`` counts from node --test output."""
    counters: dict[str, int] = {}
    for line in text.splitlines():
        match = _NODE_COUNTS.match(line.strip())
        if match:
            counters[match.group(1)] = int(match.group(2))
    if not counters:
        return None
    failed = counters.get("fail", 0) + counters.get("cancelled", 0)
    return {
        "total": counters.get("tests", 0),
        "passed": counters.get("pass", 0),
        "failed": failed,
    }


def parse_unittest_summary(text: str) -> dict[str, int] | None:
    """Read counts from unittest's ``Ran N tests`` / ``FAILED (...)`` footer."""
    total = None
    failures = errors = 0
    for line in text.splitlines():
        stripped = line.strip()
        ran = _RAN.match(stripped)
        if ran:
            total = int(ran.group(1))
        failed = _FAILED.match(stripped)
        if failed:
            parts = dict(
                piece.strip().split("=", 1) for piece in failed.group(1).split(",")
            )
            failures = int(parts.get("failures", 0))
            errors = int(parts.get("errors", 0))
    if total is None:
        return None
    return {"total": total, "passed": total - failures - errors, "failed": failures + errors}


def parse_pytest_summary(text: str) -> dict[str, int] | None:
    """Read counts from pytest's short summary line."""
    total = passed = failed = 0
    matched = False
    for match in _PYTEST_COUNT.finditer(text):
        matched = True
        count = int(match.group(1))
        outcome = match.group(2)
        total += count
        if outcome == "passed":
            passed += count
        elif outcome in ("failed", "errors", "error"):
            failed += count
    if _PYTEST_NO_TESTS.search(text):
        return {"total": 0, "passed": 0, "failed": 0}
    return {"total": total, "passed": passed, "failed": failed} if matched else None


_PARSERS = {
    "node": parse_node_summary,
    "unittest": parse_unittest_summary,
    "pytest": parse_pytest_summary,
}


def _classify(exit_code: int, counts: dict[str, int] | None, parser: str) -> str:
    """Map a process's exit code (and parsed counts) onto a status.

    pytest's exit codes are the contract -- 0 passed, 1 tests failed,
    5 nothing collected -- so they decide the status directly; the counts
    are only for reporting.  node and unittest keep the parsed summary as
    the signal, because their failures and their inability to run share
    exit code 1.
    """
    if exit_code == 0:
        return PASSED
    if parser == "pytest":
        if exit_code == 5:
            # pytest's "no tests collected" is not a failure about the code.
            return NONE
        return FAILED if exit_code == 1 else ERROR
    if counts is not None:
        return FAILED
    return ERROR


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def oracle_command(test: Path) -> tuple[list[str], str]:
    """Return ``(command, parser)`` for an oracle suite, by its suffix."""
    if test.suffix == ".js":
        return ["node", "--test", str(test)], "node"
    if test.suffix == ".py":
        # sys.executable, not "python3": the suite must run under the same
        # interpreter the evaluator itself runs under.
        return [sys.executable, str(test)], "unittest"
    raise EvaluatorError(f"unsupported oracle test suffix: {test.name}")


def project_test_files(codebase: Path) -> tuple[list[Path], list[Path]]:
    """Find a codebase's own test files.

    Returns ``(python_test_dirs, node_test_files)``: directories holding
    ``test_*.py`` (each becomes one pytest/unittest run) and ``*.test.js``
    files.  Build artifacts, caches, and dependencies are never walked.
    """
    python_dirs: list[Path] = []
    node_files: list[Path] = []
    for path in sorted(codebase.rglob("*")):
        if not path.is_file() or IGNORED_DIRS.intersection(path.parts):
            continue
        if path.name.startswith("test_") and path.suffix == ".py":
            if path.parent not in python_dirs:
                python_dirs.append(path.parent)
        elif path.suffix == ".js" and ".test." in path.name:
            node_files.append(path)
    return python_dirs, node_files


def project_test_commands(codebase: Path) -> list[tuple[list[str], str]]:
    """Build ``(command, parser)`` pairs for a codebase's own tests."""
    python_dirs, node_files = project_test_files(codebase)
    commands: list[tuple[list[str], str]] = []
    for directory in python_dirs:
        if importlib.util.find_spec("pytest") is not None:
            commands.append(
                (
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        # Neutralize any pytest config found above the
                        # codebase (an enclosing project may inject its own
                        # addopts) and leave the codebase's cache untouched.
                        "-o",
                        "addopts=",
                        "-p",
                        "no:cacheprovider",
                        str(directory),
                    ],
                    "pytest",
                )
            )
        else:
            commands.append(
                ([sys.executable, "-m", "unittest", "discover", "-s", str(directory)], "unittest")
            )
    for path in node_files:
        commands.append((["node", "--test", str(path)], "node"))
    return commands


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #

def run_suite(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    kind: str,
    parser: str,
) -> SuiteResult:
    """Run one suite and classify its outcome.

    A suite that cannot be started (missing runtime), times out, or dies
    without a parseable summary comes back as :data:`ERROR` with a note --
    the run continues, but nothing was judged about the code.
    """
    argv = [str(part) for part in command]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return SuiteResult(
            kind=kind,
            command=tuple(argv),
            status=ERROR,
            duration=time.monotonic() - start,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr),
            note=f"timed out after {timeout:g}s",
        )
    except FileNotFoundError as exc:
        return SuiteResult(
            kind=kind,
            command=tuple(argv),
            status=ERROR,
            duration=time.monotonic() - start,
            note=f"runtime not found: {argv[0]} ({exc})",
        )
    except OSError as exc:
        return SuiteResult(
            kind=kind,
            command=tuple(argv),
            status=ERROR,
            duration=time.monotonic() - start,
            note=f"could not run {argv[0]}: {exc}",
        )

    duration = time.monotonic() - start
    counts = _PARSERS[parser](proc.stdout) or _PARSERS[parser](proc.stderr)
    status = _classify(proc.returncode, counts, parser)
    note = None
    if counts is None:
        note = (
            "no parseable test summary in the output" if status == ERROR else None
        )
    return SuiteResult(
        kind=kind,
        command=tuple(argv),
        status=status,
        exit_code=proc.returncode,
        passed=counts.get("passed") if counts else None,
        failed=counts.get("failed") if counts else None,
        total=counts.get("total") if counts else None,
        duration=duration,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        note=note,
    )


def _as_text(stream: bytes | str | None) -> str:
    """TimeoutExpired hands back bytes or str depending on ``text``."""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


def run_oracle(
    test: Path,
    *,
    cwd: Path,
    timeout: float = DEFAULT_TIMEOUT,
) -> SuiteResult:
    """Run a case's oracle suite with ``cwd`` as the working directory.

    ``cwd`` is the codebase under test; the suite itself locates that tree by
    walking up from its own location, so it must come from the same
    environment root as the codebase.
    """
    command, parser = oracle_command(test)
    return run_suite(command, cwd=cwd, timeout=timeout, kind=ORACLE, parser=parser)


def run_project_tests(codebase: Path, *, timeout: float = DEFAULT_TIMEOUT) -> SuiteResult:
    """Run a codebase's own tests and aggregate them.

    ``NONE`` when the codebase ships no tests of its own.  Suites run in
    sequence -- they share one tree -- and the aggregate is ``ERROR`` if any
    suite errored, else ``FAILED`` if any failed, else ``PASSED``.
    """
    commands = project_test_commands(codebase)
    if not commands:
        return SuiteResult(kind=OWN, command=(), status=NONE, note="no tests found")

    statuses: list[str] = []
    counts = {"passed": 0, "failed": 0, "total": 0}
    durations = 0.0
    outputs: list[str] = []
    exit_code: int | None = None
    note: str | None = None
    for command, parser in commands:
        result = run_suite(
            command, cwd=codebase, timeout=timeout, kind=OWN, parser=parser
        )
        statuses.append(result.status)
        durations += result.duration
        for key in counts:
            value = getattr(result, key)
            if value is not None:
                counts[key] += value
        if result.output:
            outputs.append(f"$ {shlex.join(result.command)}\n{result.output}")
        if result.status == ERROR and note is None:
            # Keep why the first suite could not run; the status alone says
            # nothing an operator can act on.
            note, exit_code = result.note, result.exit_code

    if ERROR in statuses:
        status = ERROR
    elif FAILED in statuses:
        status = FAILED
    else:
        status = PASSED if all(s in (PASSED, NONE) for s in statuses) else NONE
    return SuiteResult(
        kind=OWN,
        command=tuple(part for command, _ in commands for part in command),
        status=status,
        exit_code=exit_code,
        passed=counts["passed"],
        failed=counts["failed"],
        total=counts["total"],
        duration=durations,
        stdout="\n\n".join(outputs),
        note=note,
    )
