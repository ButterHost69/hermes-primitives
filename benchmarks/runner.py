"""Run hms against SWE-bench tasks and produce a predictions file.

The official harness judges a patch, so this module's job is narrow: materialize
a task at its base commit, let hms edit the tree, capture the diff, and record
one ``model_patch`` row per task.  Two runners are available:

``pipeline``
    Run the project's own role chain -- reproduce, diagnose, then the gated
    primitive fan-out -- and capture whatever the tree looks like afterwards.
    This is the mode that exercises hms itself.

``agent``
    Give one Pi session a patch-oriented prompt and let it work directly.  It
    is a baseline and a fallback for tasks the role chain cannot parse.

Both modes run against a host checkout.  They do not install the project's
dependencies or run its tests; the SWE-bench harness provides the project's
environment when it grades the patch.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.gated_client import GatedPiSession
from agent.tools import BUILTIN_TOOLS
from benchmarks.dataset import Instance
from benchmarks.policy import SHELL_TOOLS, repo_policy
from benchmarks.prompts import agent_prompt, benchmark_context
from benchmarks.repo import capture_patch, check_patch, prepare_repo
from pipeline import Pipeline

#: Every built-in tool is available to the plain-agent runner when the shell
#: is enabled; host mode keeps the read/write tools only.  The pipeline role
#: decides its own subset, and the repo policy fences both modes.
BENCHMARK_TOOLS = tuple(tool.name for tool in BUILTIN_TOOLS)
AGENT_TOOLS = tuple(name for name in BENCHMARK_TOOLS if name not in SHELL_TOOLS)

MODES = ("pipeline", "agent")

DEFAULT_MODEL = "opencode-go/cat-jam-free"
DEFAULT_THINKING = "max"


class BenchmarkError(RuntimeError):
    """A benchmark run could not be set up or completed."""


@dataclass
class RunOptions:
    """Everything one benchmark invocation needs.

    ``session_timeout`` is an inactivity limit, not a wall-clock limit: Pi
    resets it whenever it streams an event.  The pipeline roles use the
    ``PiSession`` default, which is deliberately generous for a reasoning
    model; the plain-agent runner forwards this value.
    """

    model: str = DEFAULT_MODEL
    thinking: str = DEFAULT_THINKING
    mode: str = "pipeline"
    session_timeout: float = 900.0
    work_root: Path = Path(".swebench")
    refresh: bool = False
    keep_worktrees: bool = False
    skip_reproduce: bool = False
    allow_shell: bool = False

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise BenchmarkError(
                f"unknown mode {self.mode!r}; choose one of {', '.join(MODES)}"
            )
        if not self.model.strip():
            raise BenchmarkError("model must not be empty")
        if not self.thinking.strip():
            raise BenchmarkError("thinking level must not be empty")


def model_slug(options: RunOptions) -> str:
    """A filesystem- and harness-safe name for this configuration."""
    raw = f"hms-{options.model}-{options.thinking}-{options.mode}"
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-")
    return slug[:80] or "hms"


class TranscriptWriter:
    """Append Pi's streamed records to a JSONL file, thread-safely.

    Every role and every fanned-out primitive pushes records through the same
    observer, so writes are serialized here.  The file is optional: a run that
    is only interested in the patch can pass ``None``.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._handle = path.open("w", encoding="utf-8", buffering=1)

    def __call__(self, record: dict[str, Any]) -> None:
        try:
            line = json.dumps(record, ensure_ascii=False)
        except (TypeError, ValueError):
            line = json.dumps({"type": "unserializable-record", "record": repr(record)})
        with self._lock:
            if not self._handle.closed:
                self._handle.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.flush()
                self._handle.close()


def _write_json(path: Path, value: Any) -> None:
    """Write JSON atomically enough for a resumable run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def run_pipeline(
    instance: Instance,
    checkout: Path,
    options: RunOptions,
    observer: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    """Run reproduce -> diagnose -> primitive_manager over ``checkout``."""
    context = benchmark_context(instance)
    issue_context = f"{context}\n\nIssue:\n{instance.problem_statement}"
    pipeline = Pipeline(
        str(checkout),
        options.model,
        options.thinking,
        "",
        observer=observer,
        policy=repo_policy(checkout, allow_shell=options.allow_shell),
    )
    if options.skip_reproduce:
        # A host checkout has no project environment, so the reproduce role
        # would spend its turn looking for dependencies that are not installed.
        # The issue itself is enough for diagnose to locate the responsible
        # code; the oracle supplies the execution evidence later.
        reproduction: Any = {
            "summary": "Reproduction skipped: the host checkout has no project environment.",
            "reproduced": False,
            "reproduction": [],
            "evidence": "",
            "relevant_files": [],
            "notes_for_diagnose": "Diagnose from the issue and the repository source.",
        }
    else:
        reproduction = pipeline.run_step(
            "reproduce", instance.problem_statement, context=context
        )
    diagnosis = pipeline.run_step("diagnose", reproduction, context=issue_context)
    reports = pipeline.run_step(
        "primitive_manager", diagnosis, context=issue_context
    )
    return {
        "reproduce": reproduction,
        "diagnose": diagnosis,
        "primitive_manager": reports,
    }


def run_agent(
    instance: Instance,
    checkout: Path,
    options: RunOptions,
    observer: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    """Run one patch-oriented Pi session over ``checkout``.

    The session is gated by :func:`benchmarks.policy.repo_policy`, so a path
    outside the checkout is blocked before the tool runs.  In host mode the
    shell is disabled and ``AGENT_TOOLS`` omits it; ``allow_shell`` opts back
    in for a future containerized run.
    """
    tools = BENCHMARK_TOOLS if options.allow_shell else AGENT_TOOLS
    with GatedPiSession(
        tools=tools,
        model=options.model,
        thinking=options.thinking,
        cwd=str(checkout),
        persistent_session=False,
        observer=observer,
        timeout=options.session_timeout,
        policy=repo_policy(checkout, allow_shell=options.allow_shell),
    ) as session:
        text = session.run(agent_prompt(instance))
    return {"agent": text}


def run_instance(
    instance: Instance,
    options: RunOptions,
    run_dir: Path,
) -> dict[str, Any]:
    """Run one task and return its recorded outcome.

    A failure in the model run is recorded, not raised: a partial patch is
    still worth submitting, and one broken task must not stop a benchmark.
    """
    instance_dir = Path(run_dir) / "instances" / instance.instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    transcript = TranscriptWriter(instance_dir / "transcript.jsonl")
    started = time.monotonic()
    checkout: Path | None = None
    baseline = ""
    detail: dict[str, Any] = {}
    error: str | None = None
    try:
        checkout, baseline = prepare_repo(
            instance, options.work_root, refresh=options.refresh
        )
        if options.mode == "pipeline":
            detail = run_pipeline(instance, checkout, options, transcript)
        else:
            detail = run_agent(instance, checkout, options, transcript)
    except Exception as exc:  # noqa: BLE001 - a task failure is data, not a crash
        error = f"{type(exc).__name__}: {exc}"
    finally:
        transcript.close()

    patch = ""
    patch_error: str | None = None
    if checkout is not None:
        try:
            patch = capture_patch(checkout, baseline)
            patch_error = check_patch(checkout, baseline, patch)
        except Exception as exc:  # noqa: BLE001 - keep the task's partial outcome
            patch_error = f"{type(exc).__name__}: {exc}"
            patch = ""

    duration = time.monotonic() - started
    record = {
        "instance_id": instance.instance_id,
        "repo": instance.repo,
        "base_commit": instance.base_commit,
        "mode": options.mode,
        "model": options.model,
        "thinking": options.thinking,
        "duration": round(duration, 3),
        "error": error,
        "has_patch": bool(patch.strip()),
        "patch_lines": len(patch.splitlines()),
        "patch_bytes": len(patch.encode("utf-8")),
        "patch_check_error": patch_error,
        "result": detail,
    }
    if patch.strip():
        (instance_dir / "patch.diff").write_text(patch, encoding="utf-8")
    _write_json(instance_dir / "result.json", record)

    if not options.keep_worktrees and checkout is not None:
        # The patch and transcript are on disk; the checkout is reproducible
        # from the dataset row, so reclaim the space by default.
        shutil.rmtree(checkout, ignore_errors=True)

    return {**record, "patch": patch}


class PredictionStore:
    """A resumable ``predictions.jsonl`` keyed by instance id."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.records: dict[str, dict[str, Any]] = {}

    def load(self) -> None:
        if not self.path.is_file():
            return
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BenchmarkError(f"{self.path}:{number}: invalid JSON: {exc}") from exc
            instance_id = record.get("instance_id")
            if isinstance(instance_id, str):
                self.records[instance_id] = record

    def put(self, record: dict[str, Any]) -> None:
        self.records[str(record["instance_id"])] = record
        self.write()

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for instance_id in sorted(self.records):
                handle.write(json.dumps(self.records[instance_id], ensure_ascii=False) + "\n")
        temporary.replace(self.path)


def prediction_record(
    instance: Instance, patch: str, *, model_name: str
) -> dict[str, str]:
    """Build one official-harness prediction row."""
    return {
        "instance_id": instance.instance_id,
        "model_name_or_path": model_name,
        "model_patch": patch,
    }


def run_benchmark(
    instances: Sequence[Instance],
    options: RunOptions,
    run_dir: Path,
    *,
    predictions_path: Path | None = None,
    resume: bool = False,
    on_instance: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run every task, updating a predictions file as it goes.

    The predictions default to ``run_dir/predictions.jsonl``.  ``resume``
    keeps an existing non-empty prediction for a task instead of running it
    again; a failed or empty prediction is retried.  Returns a small summary
    and leaves the per-instance records under
    ``run_dir/instances/<instance_id>/``.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = Path(predictions_path) if predictions_path else run_dir / "predictions.jsonl"
    store = PredictionStore(predictions_path)
    if resume:
        store.load()

    model_name = model_slug(options)
    outcomes: list[dict[str, Any]] = []
    skipped = 0
    for index, instance in enumerate(instances, 1):
        existing = store.records.get(instance.instance_id)
        if (
            resume
            and existing is not None
            and isinstance(existing.get("model_patch"), str)
            and existing["model_patch"].strip()
        ):
            skipped += 1
            outcome = {
                "instance_id": instance.instance_id,
                "skipped": True,
                "has_patch": True,
                "patch_lines": len(existing["model_patch"].splitlines()),
                "error": None,
                "index": index,
                "total": len(instances),
            }
            outcomes.append(outcome)
            if on_instance is not None:
                on_instance(outcome)
            continue

        record = run_instance(instance, options, run_dir)
        prediction = prediction_record(
            instance, record["patch"], model_name=model_name
        )
        store.put(prediction)
        outcome = {
            "instance_id": instance.instance_id,
            "skipped": False,
            "has_patch": record["has_patch"],
            "patch_lines": record["patch_lines"],
            "patch_check_error": record["patch_check_error"],
            "error": record["error"],
            "duration": record["duration"],
            "index": index,
            "total": len(instances),
        }
        outcomes.append(outcome)
        if on_instance is not None:
            on_instance(outcome)

    summary = {
        "run_id": run_dir.name,
        "model": options.model,
        "thinking": options.thinking,
        "mode": options.mode,
        "model_name": model_name,
        "instances": len(instances),
        "skipped": skipped,
        "with_patch": sum(1 for outcome in outcomes if outcome["has_patch"]),
        "errors": sum(1 for outcome in outcomes if outcome["error"]),
        "predictions": str(predictions_path),
        "outcomes": outcomes,
    }
    _write_json(run_dir / "summary.json", summary)
    return summary


def evaluate_predictions(
    *,
    run_dir: Path,
    predictions: Path,
    dataset: str,
    instance_ids: Sequence[str],
    run_id: str,
    swebench_bin: str = "swebench",
    max_workers: int = 1,
    timeout: int = 1800,
) -> int:
    """Run the official SWE-bench evaluation harness on a predictions file.

    The harness lives in its own environment (it needs ``datasets`` and a
    Docker client), so it is invoked as an external command rather than
    imported.  The run happens in ``run_dir``, keeping ``logs/`` and the result
    report beside the predictions they describe.
    """
    run_dir = Path(run_dir).resolve()
    predictions = Path(predictions).resolve()
    if Path(dataset).is_file():
        dataset = str(Path(dataset).resolve())
    command = [
        *shlex.split(swebench_bin),
        "eval",
        dataset,
        "--predictions",
        str(predictions),
        "--run-id",
        run_id,
        "--workers",
        str(max_workers),
        "--timeout",
        str(timeout),
        "--report-dir",
        str(run_dir),
    ]
    for instance_id in instance_ids:
        command.extend(["--instance", instance_id])
    print("$ " + " ".join(shlex.quote(part) for part in command), flush=True)
    try:
        completed = subprocess.run(command, cwd=str(run_dir), check=False)
    except FileNotFoundError:
        print(
            "error: the SWE-bench harness was not found. Install it in a "
            "separate environment, e.g.\n"
            "  uv venv --python 3.13 .swebench/swebench-venv\n"
            "  uv pip install --python .swebench/swebench-venv/bin/python swebench==5.0.2\n"
            "then pass --swebench-bin .swebench/swebench-venv/bin/swebench",
        )
        return 2
    return completed.returncode


__all__ = [
    "BENCHMARK_TOOLS",
    "DEFAULT_MODEL",
    "DEFAULT_THINKING",
    "MODES",
    "BenchmarkError",
    "PredictionStore",
    "RunOptions",
    "TranscriptWriter",
    "evaluate_predictions",
    "model_slug",
    "prediction_record",
    "run_agent",
    "run_benchmark",
    "run_instance",
    "run_pipeline",
]
